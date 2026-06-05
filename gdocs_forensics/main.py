"""CLI entrypoint.

Usage:
    python -m gdocs_forensics.main --url "<google doc url or id>" [--browser chrome]
                                   [--cookies cookies.txt] [--out ./output]

You must be signed into the target Google account in the chosen browser. The tool
reads that browser's own session cookies locally and queries Google's internal
revision endpoints as you. It only works on documents your account can open.

A single fetch of the document's global revision stream contains EVERY tab's
history: each tab's edits are scoped to a segment (an `nm` wrapper naming the tab
id). We reconstruct each segment independently, so the output is per-tab,
per-character authorship.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import auth, fetch, parse, analyze, report, insights, dashboard, pdf_report
from .replay import Replayer

# A fixed reference time so reruns are deterministic; the harness forbids
# Date.now()-style calls in some contexts, but here we stamp generation time.
import time as _time


def extract_doc_id(url_or_id: str) -> str:
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", url_or_id):
        return url_or_id
    raise SystemExit(f"Could not extract a document id from {url_or_id!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Google Docs revision history analysis")
    ap.add_argument("--url", required=True, help="Doc URL or id")
    ap.add_argument("--browser", default="chrome",
                    help=f"Browser to read cookies from {auth.SUPPORTED_BROWSERS}")
    ap.add_argument("--cookies", help="Exported cookies.txt (overrides --browser)")
    ap.add_argument("--out", default="./output", help="Output directory")
    ap.add_argument("--chunk", type=int, default=100000, help="Revisions per request")
    ap.add_argument("--no-pdf", action="store_true", help="Skip the PDF report")
    args = ap.parse_args(argv)

    doc_id = extract_doc_id(args.url)
    out = os.path.abspath(args.out)
    raw_dir = os.path.join(out, "raw")
    os.makedirs(out, exist_ok=True)

    print(f"[*] Document id: {doc_id}")
    print(f"[*] Loading session cookies from "
          f"{'cookies file' if args.cookies else args.browser} …")
    jar = auth.load_cookiejar(browser=args.browser, cookie_file=args.cookies)
    if not auth.has_auth_cookies(jar):
        print(f"[!] No Google session cookies found. Sign in in {args.browser} "
              "first (or pass --cookies).", file=sys.stderr)
        return 2

    f = fetch.RevisionFetcher(doc_id, jar, raw_dir)
    print("[*] Handshake + finding true revision count …")
    di = f.bootstrap()
    last_rev = f.find_last_revision(tab=None, hint=di or 1)
    print(f"[*] Document has {last_rev} revisions. Fetching full changelog …")
    tiles = f.tiles(last_rev)
    user_map = parse.build_user_map(tiles)
    rev_authors = parse.build_revision_author_index(tiles)
    changelog = f.load_all(last_rev, chunk=args.chunk)
    f.write_manifest()

    tab_titles = parse.extract_tab_titles(changelog)
    structure = parse.extract_structure_events(changelog)
    mutations, prep = parse.parse_changelog(changelog, 1, rev_authors)

    r = Replayer()
    r.apply_all(mutations)

    segments = sorted(r.segments, key=lambda s: -len(r.segments[s]))
    print(f"[*] Reconstructed {len(segments)} segment(s)/tab(s), "
          f"{len(r.cells)} total surviving chars.")

    # Document-wide author stats.
    stats = analyze.author_stats(r.cells, mutations, r.inserted_chars,
                                 r.deleted_chars, user_map)
    sessions = analyze.editing_sessions(mutations)

    # Per-tab outputs + collect paragraph attribution labeled by tab.
    paragraphs = []
    for seg in segments:
        cells = r.segments[seg]
        if not cells:
            continue
        title = tab_titles.get(seg, seg)
        seg_muts = [m for m in mutations if m.segment == seg]
        seg_stats = analyze.author_stats(cells, seg_muts, {}, {}, user_map)
        tdir = os.path.join(out, "tabs", re.sub(r"[^A-Za-z0-9.]+", "_", seg))
        os.makedirs(tdir, exist_ok=True)
        with open(os.path.join(tdir, "reconstructed.txt"), "w") as fh:
            fh.write(f"# {title} ({seg})\n\n{r.text(seg)}")
        report.write_attributed_html(cells, user_map, seg_stats,
                                     os.path.join(tdir, "attributed_text.html"))
        report.write_timeline_csv(seg_muts, user_map,
                                  os.path.join(tdir, "timeline.csv"))
        for p in analyze.paragraph_attribution(cells, user_map):
            p["tab"] = title
            paragraphs.append(p)

    report.write_report_md(
        doc_id=doc_id, doc_title=f"{len(segments)} tabs", stats=stats,
        sessions=sessions, paragraphs=paragraphs, parse_report=prep,
        replay_warnings=r.warnings, total_revs=last_rev,
        reconstructed_len=len(r.cells), path=os.path.join(out, "report.md"),
        tab_titles=tab_titles, segments=segments, replayer=r, user_map=user_map)

    # ---- full analytics bundle -> dashboard + PDF -------------------------
    print("[*] Building analytics bundle (deletion, structure, playback) …")
    bundle = insights.build_bundle(
        doc_id=doc_id, last_rev=last_rev, user_map=user_map, mutations=mutations,
        replayer=r, structure=structure, tab_titles=tab_titles, segments=segments,
        generated_ms=int(_time.time() * 1000))
    import json as _json
    with open(os.path.join(out, "insights.json"), "w") as fh:
        _json.dump(bundle, fh, ensure_ascii=False)
    dashboard.render(bundle, os.path.join(out, "dashboard.html"))
    dashboard.render_combined(bundle, os.path.join(out, "all_tabs_colored.html"))
    print("[*] Wrote dashboard.html + all_tabs_colored.html")
    if not args.no_pdf:
        try:
            pdf_report.render(bundle, os.path.join(out, "report.pdf"))
            print("[*] Wrote report.pdf")
        except Exception as e:
            print(f"    [!] PDF generation failed: {e}", file=sys.stderr)

    print("\n[✓] Done. Combined report:", os.path.join(out, "report.md"))
    print("    Interactive dashboard:", os.path.join(out, "dashboard.html"))
    print("    PDF report:", os.path.join(out, "report.pdf"))
    print(f"    Per-tab outputs under: {os.path.join(out, 'tabs')}")
    print(f"    Raw API responses + manifest: {raw_dir}")
    print("\nPer-tab summary:")
    for seg in segments:
        n = len(r.segments[seg])
        if n:
            print(f"   {tab_titles.get(seg, seg):<28} {n:>6} chars  ({seg})")
    print("\nDocument-wide authors:")
    for s in stats:
        print(f"   {s.name:<28} surviving={s.surviving_chars:<7} "
              f"inserted={s.inserted_chars:<7} deleted={s.deleted_chars:<7} "
              f"edits={s.edits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
