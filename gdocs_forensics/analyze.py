"""Derive human-meaningful findings from replayed cells and mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .replay import Cell
from .parse import Mutation


def _fmt(ts_ms: Optional[int]) -> Optional[str]:
    if not ts_ms:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


@dataclass
class AuthorStat:
    user_id: str
    name: str
    surviving_chars: int
    inserted_chars: int
    deleted_chars: int
    edits: int
    first_ts: Optional[int]
    last_ts: Optional[int]
    active_days: set


def author_stats(
    cells: list[Cell],
    mutations: list[Mutation],
    inserted: dict[str, int],
    deleted: dict[str, int],
    user_map: dict[str, dict],
) -> list[AuthorStat]:
    surviving: dict[str, int] = {}
    for c in cells:
        if c.user_id:
            surviving[c.user_id] = surviving.get(c.user_id, 0) + 1

    edits: dict[str, int] = {}
    first_ts: dict[str, int] = {}
    last_ts: dict[str, int] = {}
    days: dict[str, set] = {}

    for m in mutations:
        if m.op in ("insert", "delete") and m.user_id:
            uid = m.user_id
            edits[uid] = edits.get(uid, 0) + 1
            if m.timestamp_ms:
                first_ts[uid] = min(first_ts.get(uid, m.timestamp_ms), m.timestamp_ms)
                last_ts[uid] = max(last_ts.get(uid, m.timestamp_ms), m.timestamp_ms)
                day = datetime.fromtimestamp(
                    m.timestamp_ms / 1000, tz=timezone.utc
                ).date().isoformat()
                days.setdefault(uid, set()).add(day)

    all_uids = set(surviving) | set(inserted) | set(deleted) | set(edits)
    stats = []
    for uid in all_uids:
        info = user_map.get(uid, {})
        stats.append(
            AuthorStat(
                user_id=uid,
                name=info.get("name", uid),
                surviving_chars=surviving.get(uid, 0),
                inserted_chars=inserted.get(uid, 0),
                deleted_chars=deleted.get(uid, 0),
                edits=edits.get(uid, 0),
                first_ts=first_ts.get(uid),
                last_ts=last_ts.get(uid),
                active_days=days.get(uid, set()),
            )
        )
    stats.sort(key=lambda s: s.surviving_chars, reverse=True)
    return stats


def editing_sessions(mutations: list[Mutation], gap_minutes: int = 30) -> list[dict]:
    """Cluster edits into sessions separated by >gap_minutes of inactivity."""
    events = []
    for m in mutations:
        if m.op in ("insert", "delete") and m.timestamp_ms:
            events.append((m.timestamp_ms, m.user_id))
    events.sort()

    sessions: list[dict] = []
    gap_ms = gap_minutes * 60_000
    for ts, uid in events:
        if sessions and ts - sessions[-1]["end_ms"] <= gap_ms:
            s = sessions[-1]
            s["end_ms"] = ts
            s["edits"] += 1
            s["users"].add(uid)
        else:
            sessions.append(
                {"start_ms": ts, "end_ms": ts, "edits": 1, "users": {uid}}
            )
    return sessions


def paragraph_attribution(cells: list[Cell], user_map: dict[str, dict]) -> list[dict]:
    """Split the final text on newlines; report author breakdown per paragraph."""
    paras: list[dict] = []
    current: list[Cell] = []

    def flush():
        if not current:
            paras.append({"text": "", "by_author": {}, "dominant": None})
            return
        counts: dict[str, int] = {}
        for c in current:
            counts[c.user_id] = counts.get(c.user_id, 0) + 1
        dominant = max(counts, key=counts.get)
        paras.append(
            {
                "text": "".join(c.char for c in current),
                "by_author": {
                    user_map.get(uid, {}).get("name", uid): n
                    for uid, n in counts.items()
                },
                "dominant": user_map.get(dominant, {}).get("name", dominant),
                "first_ts": _fmt(min((c.timestamp_ms or 0) for c in current) or None),
            }
        )

    for c in cells:
        if c.char == "\n":
            flush()
            current = []
        else:
            current.append(c)
    flush()
    return [p for p in paras if p["text"].strip()]
