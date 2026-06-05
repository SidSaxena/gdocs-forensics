"""Render outputs: colored HTML, per-mutation CSV, and a Markdown report."""

from __future__ import annotations

import csv
import html
import os
from datetime import datetime, timezone
from typing import Optional

from .replay import Cell
from .parse import Mutation
from .analyze import AuthorStat


PALETTE = [
    "#1b6ca8", "#a8331b", "#2e8b57", "#8b5cf6", "#d97706",
    "#be185d", "#0f766e", "#525252",
]


def _fmt(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def color_for(idx: int) -> str:
    return PALETTE[idx % len(PALETTE)]


def write_attributed_html(
    cells: list[Cell], user_map: dict, stats: list[AuthorStat], path: str
) -> None:
    color_by_uid = {s.user_id: color_for(i) for i, s in enumerate(stats)}
    legend = "".join(
        f'<span style="background:{color_by_uid[s.user_id]};color:#fff;'
        f'padding:2px 6px;margin:2px;border-radius:3px">'
        f"{html.escape(s.name)} — {s.surviving_chars} chars</span> "
        for s in stats
    )
    spans = []
    for c in cells:
        col = color_by_uid.get(c.user_id, "#999")
        ch = "<br>\n" if c.char == "\n" else html.escape(c.char)
        title = f"{user_map.get(c.user_id, {}).get('name', c.user_id)} · {_fmt(c.timestamp_ms)}"
        spans.append(f'<span style="color:{col}" title="{html.escape(title)}">{ch}</span>')
    doc = f"""<!doctype html><meta charset="utf-8">
<title>Authorship attribution</title>
<body style="font-family:Georgia,serif;max-width:820px;margin:40px auto;line-height:1.6">
<h2>Per-character authorship</h2>
<p>Each character is colored by the author who typed it. Hover for author + timestamp.</p>
<div style="margin:12px 0">{legend}</div>
<hr>
<div>{''.join(spans)}</div>
</body>"""
    with open(path, "w") as fh:
        fh.write(doc)


def write_timeline_csv(mutations: list[Mutation], user_map: dict, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["revision", "timestamp_utc", "author", "segment", "op",
                    "index", "range", "text"])
        for m in mutations:
            if m.op not in ("insert", "delete"):
                continue
            name = user_map.get(m.user_id, {}).get("name", m.user_id or "")
            rng = f"{m.start}-{m.end}" if m.op == "delete" else ""
            text = (m.text or "").replace("\n", "\\n") if m.op == "insert" else ""
            w.writerow([m.revision, _fmt(m.timestamp_ms), name, m.segment, m.op,
                        m.index or "", rng, text])


def write_report_md(
    *, doc_id: str, doc_title: str, stats: list[AuthorStat], sessions: list[dict],
    paragraphs: list[dict], parse_report: dict, replay_warnings: list[str],
    total_revs: int, reconstructed_len: int, path: str,
    tab_titles: dict = None, segments: list = None, replayer=None, user_map: dict = None,
) -> None:
    lines = [
        f"# Authorship forensic report — {doc_title or doc_id}",
        "",
        f"- Document id: `{doc_id}`",
        f"- Revisions analyzed: **{total_revs}**",
        f"- Reconstructed length (all tabs): **{reconstructed_len}** characters",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
    ]

    # Per-tab authorship breakdown.
    if segments and replayer is not None and user_map is not None:
        tab_titles = tab_titles or {}
        lines += ["## Per-tab authorship", "",
                  "| Tab | Chars | Author breakdown (surviving chars) |",
                  "|---|--:|---|"]
        for seg in segments:
            cells = replayer.segments.get(seg, [])
            if not cells:
                continue
            counts: dict = {}
            for c in cells:
                counts[c.user_id] = counts.get(c.user_id, 0) + 1
            total = sum(counts.values()) or 1
            breakdown = ", ".join(
                f"{user_map.get(uid, {}).get('name', uid)} {100*n/total:.0f}%"
                for uid, n in sorted(counts.items(), key=lambda kv: -kv[1]))
            lines.append(f"| {tab_titles.get(seg, seg)} | {len(cells)} | {breakdown} |")
        lines.append("")

    lines += [
        "## Authors (document-wide)",
        "",
        "| Author | Surviving chars | Total inserted | Total deleted | Edits | First edit | Last edit | Active days |",
        "|---|--:|--:|--:|--:|---|---|--:|",
    ]
    total_surv = sum(s.surviving_chars for s in stats) or 1
    for s in stats:
        pct = 100 * s.surviving_chars / total_surv
        lines.append(
            f"| {s.name} ({pct:.1f}%) | {s.surviving_chars} | {s.inserted_chars} | "
            f"{s.deleted_chars} | {s.edits} | {_fmt(s.first_ts)} | {_fmt(s.last_ts)} | "
            f"{len(s.active_days)} |"
        )

    lines += ["", "## Editing sessions", "",
              "| Start | End | Duration (min) | Edits | Participants |",
              "|---|---|--:|--:|---|"]
    for s in sessions:
        dur = (s["end_ms"] - s["start_ms"]) / 60000
        users = ", ".join(sorted(str(u) for u in s["users"] if u)) or "—"
        lines.append(f"| {_fmt(s['start_ms'])} | {_fmt(s['end_ms'])} | {dur:.0f} | "
                     f"{s['edits']} | {users} |")

    lines += ["", "## Paragraph-level attribution", ""]
    last_tab = None
    for i, p in enumerate(paragraphs, 1):
        if p.get("tab") != last_tab:
            last_tab = p.get("tab")
            lines += ["", f"### Tab: {last_tab}", ""]
        breakdown = ", ".join(f"{a}: {n}" for a, n in sorted(
            p["by_author"].items(), key=lambda kv: -kv[1]))
        snippet = p["text"][:120].replace("\n", " ")
        lines += [f"**¶{i}** — dominant: **{p['dominant']}** ({breakdown})",
                  f"> {snippet}{'…' if len(p['text']) > 120 else ''}", ""]

    lines += ["## Parse / replay diagnostics", "",
              f"- Changelog entries: {parse_report.get('entries')}",
              f"- Mutation op counts: `{parse_report.get('ops')}`"]
    if parse_report.get("unknown_samples"):
        lines.append(f"- ⚠️ Unrecognized mutation samples present "
                     f"({len(parse_report['unknown_samples'])}); see raw dump.")
    if replay_warnings:
        lines.append(f"- ⚠️ {len(replay_warnings)} replay range warnings "
                     f"(first: {replay_warnings[0]}).")
    lines += ["",
              "---",
              "_Method: per-character replay of Google Docs' internal revision "
              "changelog, authored via the signed-in account's own session. Raw "
              "API responses and a SHA-256 manifest are preserved under `evidence/` "
              "for verification._"]

    with open(path, "w") as fh:
        fh.write("\n".join(lines))
