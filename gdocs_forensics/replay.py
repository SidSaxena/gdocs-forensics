"""Replay mutations to reconstruct each tab with per-character authorship.

Google Docs models a multi-tab document as multiple SEGMENTS. Each tab's text
edits are scoped to that segment, and every insert/delete index is relative to
its own segment. So we keep one buffer of Cells per segment, apply each mutation
to the buffer named by its `segment`, and after replaying, each segment's
surviving cells ARE that tab's final text — every cell stamped with its author.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .parse import Mutation

INDEX_BASE = 1  # Google Docs indices are 1-based; map to 0-based list indices


@dataclass
class Cell:
    char: str
    user_id: Optional[str]
    rev: int
    timestamp_ms: Optional[int]
    segment: str


class Replayer:
    def __init__(self) -> None:
        self.segments: dict[str, list[Cell]] = {}
        self.warnings: list[str] = []
        self.inserted_chars: dict[str, int] = {}
        self.deleted_chars: dict[str, int] = {}
        # Tombstones: every deletion event, retaining the removed text AND the
        # original author of each removed character. This is what powers
        # edit-war analysis ("who deleted whose words") and recovery of text
        # that was written and later removed.
        self.deletions: list[dict] = []

    def _buf(self, segment: str) -> list[Cell]:
        return self.segments.setdefault(segment, [])

    def apply_all(self, mutations: list[Mutation]) -> None:
        for m in mutations:
            if m.op == "insert":
                self._insert(m)
            elif m.op == "delete":
                self._delete(m)
            # style / unknown: no effect on the character stream

    def _insert(self, m: Mutation) -> None:
        if m.index is None or m.text is None:
            return
        buf = self._buf(m.segment)
        pos = max(0, min(m.index - INDEX_BASE, len(buf)))
        new = [Cell(ch, m.user_id, m.revision, m.timestamp_ms, m.segment)
               for ch in m.text]
        buf[pos:pos] = new
        if m.user_id is not None:
            self.inserted_chars[m.user_id] = (
                self.inserted_chars.get(m.user_id, 0) + len(new))

    def _delete(self, m: Mutation) -> None:
        if m.start is None or m.end is None:
            return
        buf = self._buf(m.segment)
        lo = m.start - INDEX_BASE
        hi = m.end - INDEX_BASE  # inclusive
        if lo < 0 or hi >= len(buf) or lo > hi:
            self.warnings.append(
                f"rev {m.revision} seg {m.segment}: delete [{m.start},{m.end}] "
                f"out of range (len {len(buf)})")
            lo = max(0, lo)
            hi = min(len(buf) - 1, hi)
            if lo > hi:
                return
        removed = buf[lo:hi + 1]
        if m.user_id is not None:
            self.deleted_chars[m.user_id] = (
                self.deleted_chars.get(m.user_id, 0) + len(removed))
        if removed:
            self.deletions.append({
                "segment": m.segment,
                "del_user": m.user_id,
                "del_rev": m.revision,
                "del_ts": m.timestamp_ms,
                "text": "".join(c.char for c in removed),
                "origins": [c.user_id for c in removed],
                "orig_revs": [c.rev for c in removed],
            })
        del buf[lo:hi + 1]

    # -- outputs -------------------------------------------------------------
    def text(self, segment: Optional[str] = None) -> str:
        if segment is not None:
            return "".join(c.char for c in self.segments.get(segment, []))
        return "".join(c.char for buf in self.segments.values() for c in buf)

    @property
    def cells(self) -> list[Cell]:
        """All surviving cells across every segment (for document-wide stats)."""
        return [c for buf in self.segments.values() for c in buf]

    def surviving_char_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.cells:
            if c.user_id is not None:
                counts[c.user_id] = counts.get(c.user_id, 0) + 1
        return counts
