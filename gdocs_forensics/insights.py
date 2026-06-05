"""Compute the full analytics bundle from mutations, the tombstone replay, and
structure events. Output is a single JSON-serializable dict consumed by both the
HTML dashboard and the PDF report.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

PASTE_THRESHOLD = 80      # single inserts >= this many chars are treated as pastes
SESSION_GAP_MIN = 30      # minutes of inactivity that separate editing sessions
PALETTE = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706",
           "#db2777", "#0891b2", "#65a30d", "#525252"]


def _iso(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _clock(ms: Optional[int]) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_bundle(*, doc_id, last_rev, user_map, mutations, replayer,
                 structure, tab_titles, segments, generated_ms) -> dict:
    seg_cells = replayer.segments

    # ---- author ordering / index ----------------------------------------
    surviving = defaultdict(int)
    for c in replayer.cells:
        if c.user_id:
            surviving[c.user_id] += 1
    all_uids = (set(surviving) | set(replayer.inserted_chars)
                | set(replayer.deleted_chars))
    order = sorted(all_uids, key=lambda u: -surviving.get(u, 0))
    uidx = {u: i for i, u in enumerate(order)}

    def name(u):
        return (user_map.get(u) or {}).get("name", u or "unknown")

    # ---- typed vs pasted + edit times -----------------------------------
    typed = defaultdict(int)
    pasted = defaultdict(int)
    edits = defaultdict(int)
    first_ts, last_ts = {}, {}
    days = defaultdict(set)
    pastes = []
    for m in mutations:
        if m.op == "insert" and m.user_id:
            n = len(m.text or "")
            if n >= PASTE_THRESHOLD:
                pasted[m.user_id] += n
                pastes.append({"author": uidx.get(m.user_id), "ts": _iso(m.timestamp_ms),
                               "segment": m.segment, "size": n,
                               "preview": (m.text or "")[:160]})
            else:
                typed[m.user_id] += n
        if m.op in ("insert", "delete") and m.user_id:
            edits[m.user_id] += 1
            if m.timestamp_ms:
                first_ts[m.user_id] = min(first_ts.get(m.user_id, m.timestamp_ms),
                                          m.timestamp_ms)
                last_ts[m.user_id] = max(last_ts.get(m.user_id, m.timestamp_ms),
                                         m.timestamp_ms)
                days[m.user_id].add(_day(m.timestamp_ms))

    authors = []
    for u in order:
        ins = replayer.inserted_chars.get(u, 0)
        authors.append({
            "id": u, "name": name(u), "color": PALETTE[uidx[u] % len(PALETTE)],
            "surviving": surviving.get(u, 0),
            "inserted": ins, "deleted": replayer.deleted_chars.get(u, 0),
            "typed": typed.get(u, 0), "pasted": pasted.get(u, 0),
            "edits": edits.get(u, 0),
            "survival_rate": round(surviving.get(u, 0) / ins, 3) if ins else 0.0,
            "first_ts": _iso(first_ts.get(u)), "last_ts": _iso(last_ts.get(u)),
            "active_days": len(days.get(u, set())),
        })

    # ---- tabs ------------------------------------------------------------
    seg_first_author, seg_first_ts = {}, {}
    for m in mutations:
        if m.op == "insert" and m.user_id and m.timestamp_ms:
            if m.segment not in seg_first_ts or m.timestamp_ms < seg_first_ts[m.segment]:
                seg_first_ts[m.segment] = m.timestamp_ms
                seg_first_author[m.segment] = m.user_id
    tabs = []
    for seg in segments:
        cells = seg_cells.get(seg, [])
        if not cells:
            continue
        by = defaultdict(int)
        for c in cells:
            by[c.user_id] += 1
        tabs.append({
            "id": seg, "title": tab_titles.get(seg, seg), "chars": len(cells),
            "by_author": {uidx.get(u, -1): n for u, n in by.items()},
            "created_by": uidx.get(seg_first_author.get(seg)),
            "created_ts": _iso(seg_first_ts.get(seg)),
        })

    # ---- temporal: cumulative inserted per author (bucketed) ------------
    timed = sorted([(m.timestamp_ms, m.user_id, len(m.text or ""))
                    for m in mutations
                    if m.op == "insert" and m.user_id and m.timestamp_ms])
    cumulative = []
    running = defaultdict(int)
    if timed:
        t0, t1 = timed[0][0], timed[-1][0]
        span = max(t1 - t0, 1)
        nb = 120
        bucket_edges = [t0 + span * k // nb for k in range(nb + 1)]
        bi = 0
        for ts, u, n in timed:
            running[u] += n
            while bi < nb and ts > bucket_edges[bi + 1]:
                cumulative.append({"ts": _iso(bucket_edges[bi + 1]),
                                   "by": {uidx[a]: running[a] for a in running}})
                bi += 1
        cumulative.append({"ts": _iso(t1), "by": {uidx[a]: running[a] for a in running}})

    # ---- activity heatmaps ----------------------------------------------
    hour = {uidx[u]: [0] * 24 for u in order}
    weekday = {uidx[u]: [0] * 7 for u in order}
    for m in mutations:
        if m.op in ("insert", "delete") and m.user_id and m.timestamp_ms:
            dt = datetime.fromtimestamp(m.timestamp_ms / 1000, tz=timezone.utc)
            hour[uidx[m.user_id]][dt.hour] += 1
            weekday[uidx[m.user_id]][dt.weekday()] += 1

    # ---- sessions --------------------------------------------------------
    evts = sorted((m.timestamp_ms, m.user_id) for m in mutations
                  if m.op in ("insert", "delete") and m.timestamp_ms)
    sessions = []
    gap = SESSION_GAP_MIN * 60_000
    for ts, u in evts:
        if sessions and ts - sessions[-1]["end"] <= gap:
            s = sessions[-1]
            s["end"] = ts
            s["edits"] += 1
            s["participants"].add(u)
        else:
            sessions.append({"start": ts, "end": ts, "edits": 1,
                             "participants": {u}})
    sessions_out = [{"start": _iso(s["start"]), "end": _iso(s["end"]),
                     "dur_min": round((s["end"] - s["start"]) / 60000, 1),
                     "edits": s["edits"],
                     "participants": sorted(uidx.get(p) for p in s["participants"])}
                    for s in sessions]

    # ---- deletion: deletion matrix + recovered passages -----------------
    matrix = defaultdict(lambda: defaultdict(int))   # orig -> deleter -> chars
    passages = []
    for d in replayer.deletions:
        for ou in d["origins"]:
            matrix[ou][d["del_user"]] += 1
        txt = d["text"].strip()
        if len(txt) >= 40:  # recover substantial deleted passages
            ocount = defaultdict(int)
            for ou in d["origins"]:
                ocount[ou] += 1
            dom = max(ocount, key=ocount.get)
            passages.append({
                "orig_author": uidx.get(dom), "del_author": uidx.get(d["del_user"]),
                "del_ts": _iso(d["del_ts"]), "segment": d["segment"],
                "len": len(d["text"]), "text": d["text"][:600],
            })
    deletion_matrix = {uidx.get(o, -1): {uidx.get(k, -1): v for k, v in row.items()}
                       for o, row in matrix.items()}
    passages.sort(key=lambda p: -p["len"])

    # ---- structure summary ----------------------------------------------
    def by_author_count(items):
        c = defaultdict(int)
        for it in items:
            c[uidx.get(it.get("author"), -1)] += 1
        return dict(c)
    links = [{"author": uidx.get(l["author"]), "url": l.get("url"),
              "ts": _iso(l.get("ts")), "segment": l.get("segment")}
             for l in structure.get("links", []) if l.get("url")]
    structure_summary = {
        "links": links,
        "links_by_author": by_author_count(structure.get("links", [])),
        "images_by_author": by_author_count(structure.get("images", [])),
        "lists_by_author": by_author_count(structure.get("lists", [])),
        "tables_by_author": by_author_count(structure.get("tables", [])),
        "headings_by_author": by_author_count(structure.get("headings", [])),
        "comments_by_author": by_author_count(structure.get("comment_anchors", [])),
    }

    # ---- per-tab colored cells (for the viewer) -------------------------
    colored = {}
    for seg in segments:
        cells = seg_cells.get(seg, [])
        if cells:
            colored[seg] = [[c.char, uidx.get(c.user_id, -1), c.timestamp_ms, c.rev]
                            for c in cells]

    # ---- per-tab playback streams ---------------------------------------
    playback = defaultdict(list)
    for m in mutations:
        if m.op == "insert":
            playback[m.segment].append([1, m.index, m.text or "",
                                        uidx.get(m.user_id, -1), m.timestamp_ms])
        elif m.op == "delete":
            playback[m.segment].append([0, m.start, m.end,
                                        uidx.get(m.user_id, -1), m.timestamp_ms])

    # ---- executive summary (computed, deterministic) --------------------
    tot_surv = sum(a["surviving"] for a in authors) or 1
    span_lo = min((first_ts[u] for u in first_ts), default=None)
    span_hi = max((last_ts[u] for u in last_ts), default=None)
    by_typed = sorted(authors, key=lambda a: -a["typed"])
    by_pasted = sorted(authors, key=lambda a: -a["pasted"])
    cross = []
    for oi, row in deletion_matrix.items():
        for di, v in row.items():
            if oi != di and oi >= 0 and di >= 0:
                cross.append((v, di, oi))
    cross.sort(reverse=True)
    tab_owncount = 0
    tab_owners = []
    for t in tabs:
        if not t["by_author"]:
            continue
        oi, n = max(t["by_author"].items(), key=lambda kv: kv[1])
        tab_owners.append({"tab": t["title"], "owner": authors[int(oi)]["name"]
                           if int(oi) < len(authors) else "?",
                           "pct": round(100 * n / (t["chars"] or 1))})
    top = authors[0] if authors else None
    headline = ""
    if top:
        style = ("largely pasted" if top["pasted"] > top["typed"] else "mostly typed")
        typed_leader = by_typed[0]["name"] if by_typed else ""
        headline = (f"{len(authors)} authors. {top['name']} owns "
                    f"{round(100*top['surviving']/tot_surv)}% of surviving text "
                    f"({style}); {typed_leader} typed the most original characters.")
    executive = {
        "headline": headline,
        "facts": [
            f"{last_rev:,} revisions, {_clock(span_lo)} → {_clock(span_hi)}",
            f"{len(replayer.cells):,} surviving characters across {len(tabs)} tabs",
            f"{len(authors)} contributing authors",
        ],
        "ownership": [{"name": a["name"], "pct": round(100 * a["surviving"] / tot_surv, 1),
                       "surviving": a["surviving"], "color": a["color"]} for a in authors],
        "authorship_style": [
            f"{a['name']}: {a['typed']:,} typed vs {a['pasted']:,} pasted "
            f"(survival {round(a['survival_rate']*100)}%)" for a in authors],
        "tab_owners": tab_owners,
        "deletions": [
            f"{authors[di]['name']} deleted {v:,} characters of "
            f"{authors[oi]['name']}'s text" for v, di, oi in cross[:6]],
    }

    return {
        "doc_id": doc_id,
        "generated": _iso(generated_ms),
        "executive": executive,
        "total_revs": last_rev,
        "total_chars": len(replayer.cells),
        "authors": authors,
        "tabs": tabs,
        "timeline": {"cumulative": cumulative, "hour": hour, "weekday": weekday,
                     "sessions": sessions_out},
        "deletions": {"matrix": deletion_matrix, "passages": passages[:60]},
        "pastes": sorted(pastes, key=lambda p: -p["size"])[:60],
        "structure": structure_summary,
        "colored": colored,
        "playback": playback,
        "warnings": replayer.warnings[:50],
    }
