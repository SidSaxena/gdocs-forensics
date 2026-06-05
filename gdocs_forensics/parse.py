"""Parse the raw revision data into normalized structures.

Two jobs:
  1. Build an author map: userId -> {name, color, anonymous} and a function that
     maps a revision number to the userId that authored it (from tiles detail).
  2. Normalize the changelog into a flat list of atomic Mutation records.

The internal format is undocumented; entries and mutation objects vary. We parse
defensively: recognized fields are used, unknown shapes are recorded as 'unknown'
mutations rather than crashing, and a parse report counts what we saw so we can
verify coverage against the raw dump on the dummy doc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Label for the default segment: text not wrapped in an `nm` segment context.
# In these documents that is the first tab's body ("t.0").
DEFAULT_SEGMENT = "t.0"


@dataclass
class Mutation:
    revision: int
    user_id: Optional[str]
    timestamp_ms: Optional[int]
    op: str                      # 'insert' | 'delete' | 'style' | 'unknown'
    index: Optional[int] = None  # insert position (1-based)
    start: Optional[int] = None  # delete range start (1-based, inclusive)
    end: Optional[int] = None    # delete range end (1-based, inclusive)
    text: Optional[str] = None   # inserted text
    segment: str = DEFAULT_SEGMENT  # which tab/segment this op targets
    raw: Any = None


# --------------------------------------------------------------------------
# Author map from the tiles response
# --------------------------------------------------------------------------
def build_user_map(tiles_data: dict) -> dict[str, dict]:
    """userId -> {name, color, anonymous}. Tolerates a few known shapes."""
    users: dict[str, dict] = {}
    raw_map = (
        tiles_data.get("userMap")
        or tiles_data.get("users")
        or {}
    )
    if isinstance(raw_map, dict):
        for uid, info in raw_map.items():
            if not isinstance(info, dict):
                users[uid] = {"name": str(info), "color": None, "anonymous": False}
                continue
            users[uid] = {
                "name": info.get("name") or info.get("displayName") or uid,
                "color": (info.get("color") or {}).get("color")
                if isinstance(info.get("color"), dict)
                else info.get("color"),
                "anonymous": bool(info.get("anonymous", False)),
                "photo": info.get("photo") or info.get("photoUrl"),
            }
    return users


def build_revision_author_index(tiles_data: dict) -> dict[int, str]:
    """Map each revision number -> userId, from the detailed tile breakdown.

    Detailed tiles describe contiguous revision ranges [firstRev, lastRev] each
    attributed to a user (or set of users). We expand ranges to per-revision.
    When a range lists multiple users we cannot split it further from tiles
    alone, so we record the first/primary user and flag it (see ambiguous()).
    """
    index: dict[int, str] = {}
    containers = (
        tiles_data.get("tileInfo")
        or tiles_data.get("revisions")
        or tiles_data.get("tiles")
        or []
    )
    for tile in containers:
        if not isinstance(tile, dict):
            continue
        first = _first_int(tile, ("firstRev", "startRev", "start", "first"))
        last = _first_int(tile, ("lastRev", "endRev", "end", "last"))
        uid = _tile_user(tile)
        if first is None or last is None or uid is None:
            continue
        for rev in range(first, last + 1):
            index.setdefault(rev, uid)
    return index


def _first_int(d: dict, keys) -> Optional[int]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, int):
            return v
    return None


def _tile_user(tile: dict) -> Optional[str]:
    for k in ("userId", "user", "uid", "author"):
        v = tile.get(k)
        if isinstance(v, str):
            return v
    for k in ("userIds", "users", "authors"):
        v = tile.get(k)
        if isinstance(v, list) and v:
            return str(v[0])
    return None


# --------------------------------------------------------------------------
# Changelog -> mutations
# --------------------------------------------------------------------------
def parse_changelog(
    changelog: list[Any],
    start_rev: int,
    rev_author_index: dict[int, str],
) -> tuple[list[Mutation], dict]:
    """Normalize changelog entries. Entry i corresponds to revision start_rev+i.

    Each top-level entry is typically [mutation, sid, timestamp_ms, ...]. We pull
    the mutation object (first list/dict element), the timestamp (first int that
    looks like epoch-ms), and attribute the author via rev_author_index, falling
    back to any user id embedded in the entry.
    """
    mutations: list[Mutation] = []
    report = {"entries": len(changelog), "ops": {}, "unknown_samples": [],
              "segments": {}}

    for i, entry in enumerate(changelog):
        rev = start_rev + i
        mut_obj, ts, embedded_uid = _dissect_entry(entry)
        # The changelog carries a per-mutation author; prefer it. Fall back to the
        # tiles-derived revision->author map only when the entry lacks it.
        uid = embedded_uid or rev_author_index.get(rev)
        _flatten(mut_obj, rev, uid, ts, DEFAULT_SEGMENT, mutations, report)
    return mutations, report


def _seg_from_nmr(nmr: Any, current: str) -> str:
    """A segment-scoped (`nm`) mutation names its target in nmr, e.g.
    ["ksm", "t.j88a1ox66b9d"] for a tab body, or a kix.* entity for
    headers/footers/lists. Return the tab id when present, else keep current."""
    if isinstance(nmr, list):
        for el in reversed(nmr):
            if isinstance(el, str) and (el.startswith("t.") or el.startswith("kix.")):
                return el
    return current


def _flatten(obj: Any, rev: int, uid: Optional[str], ts: Optional[int],
             segment: str, out: list[Mutation], report: dict) -> None:
    """Recursively expand a mutation tree into atomic insert/delete/style ops,
    threading the active segment through `nm` (segment-scoped) and `mlti`
    (multi) wrappers. THIS is what was missing before: tab text lives inside
    `nm`/`nmc`, so without descending here it was silently dropped."""
    if isinstance(obj, list):
        # Some encodings wrap a single dict in a list.
        for el in obj:
            _flatten(el, rev, uid, ts, segment, out, report)
        return
    if not isinstance(obj, dict):
        return
    ty = obj.get("ty") or obj.get("type")

    if ty == "nm":  # segment-scoped wrapper: nmr names the segment, nmc is the op
        seg = _seg_from_nmr(obj.get("nmr"), segment)
        _flatten(obj.get("nmc"), rev, uid, ts, seg, out, report)
        return
    if ty == "mlti":  # ordered multiple sub-mutations, same segment
        for sub in obj.get("mts", []):
            _flatten(sub, rev, uid, ts, segment, out, report)
        return

    if ty == "is":      # insert string
        m = Mutation(rev, uid, ts, "insert", index=obj.get("ibi") or obj.get("ib"),
                     text=obj.get("s", ""), segment=segment, raw=obj)
    elif ty == "ds":    # delete string (inclusive range)
        m = Mutation(rev, uid, ts, "delete", start=obj.get("si"), end=obj.get("ei"),
                     segment=segment, raw=obj)
    elif ty in ("as", "us", "rs", "iss", "ue", "de", "ae", "te", "ac", "ucp",
                "mch", "mkch", "dc", "nmc"):
        m = Mutation(rev, uid, ts, "style", segment=segment, raw=obj)
    else:
        m = Mutation(rev, uid, ts, "unknown", segment=segment, raw=obj)
        if len(report["unknown_samples"]) < 5:
            report["unknown_samples"].append(obj)

    out.append(m)
    report["ops"][m.op] = report["ops"].get(m.op, 0) + 1
    if m.op in ("insert", "delete"):
        report["segments"][m.segment] = report["segments"].get(m.segment, 0) + 1


def extract_tab_titles(changelog: list[Any]) -> dict[str, str]:
    """Walk the changelog for tab create/rename ops and map tab id -> title.
    `ac` (add child) and `ucp` (update child props) carry ["t.xxxx", ..., title]."""
    titles: dict[str, str] = {}

    def find_title(d: Any) -> Optional[str]:
        # title appears as the last string inside a [int, "title"] pair
        if isinstance(d, list):
            if len(d) == 2 and isinstance(d[0], int) and isinstance(d[1], str):
                return d[1]
            for el in d:
                t = find_title(el)
                if t:
                    return t
        return None

    def walk(m: Any):
        if isinstance(m, dict):
            ty = m.get("ty")
            if ty in ("ac", "ucp", "mkch") and isinstance(m.get("d"), list):
                d = m["d"]
                tab = next((x for x in _iter_strings(d) if x.startswith("t.")), None)
                title = find_title(d)
                if tab and title:
                    titles[tab] = title
            if ty == "mlti":
                for x in m.get("mts", []):
                    walk(x)
            if ty == "nm":
                walk(m.get("nmc"))
        elif isinstance(m, list):
            for x in m:
                walk(x)

    for entry in changelog:
        if isinstance(entry, list) and entry:
            walk(entry[0])
    return titles


def extract_structure_events(changelog: list[Any], start_rev: int = 1) -> dict:
    """Pull non-text authorship signal from the changelog: who added hyperlinks,
    images/inline objects, lists, tables, and comment anchors. Each event keeps
    the author, segment, and timestamp so it can be attributed."""
    events = {"links": [], "images": [], "lists": [], "tables": [],
              "comment_anchors": [], "headings": []}

    def walk(m, uid, ts, seg):
        if isinstance(m, list):
            for x in m:
                walk(x, uid, ts, seg)
            return
        if not isinstance(m, dict):
            return
        ty = m.get("ty")
        if ty == "nm":
            seg = _seg_from_nmr(m.get("nmr"), seg)
            walk(m.get("nmc"), uid, ts, seg)
            return
        if ty == "mlti":
            for x in m.get("mts", []):
                walk(x, uid, ts, seg)
            return
        if ty == "as":  # apply style
            st = m.get("st")
            sm = m.get("sm") if isinstance(m.get("sm"), dict) else {}
            if st == "link":
                url = sm.get("lnks_link") or sm.get("lnk_url") or _find_url(sm)
                events["links"].append({"author": uid, "ts": ts, "segment": seg,
                                        "url": url})
            elif st == "comment" or st == "doco_anchor":
                events["comment_anchors"].append({"author": uid, "ts": ts,
                                                  "segment": seg})
            elif st == "paragraph" and ("ps_hd" in sm):
                events["headings"].append({"author": uid, "ts": ts, "segment": seg,
                                           "level": sm.get("ps_hd")})
        elif ty == "ae":  # add entity
            et = m.get("et")
            if et == "inline":
                events["images"].append({"author": uid, "ts": ts, "segment": seg})
            elif et == "list":
                events["lists"].append({"author": uid, "ts": ts, "segment": seg})
            elif et == "table":
                events["tables"].append({"author": uid, "ts": ts, "segment": seg})

    for i, entry in enumerate(changelog):
        if not (isinstance(entry, list) and entry):
            continue
        mut, ts, uid = _dissect_entry(entry)
        walk(mut, uid, ts, DEFAULT_SEGMENT)
    return events


def _find_url(sm: dict):
    for v in sm.values():
        if isinstance(v, str) and v.startswith(("http://", "https://", "mailto:")):
            return v
        if isinstance(v, dict):
            u = _find_url(v)
            if u:
                return u
    return None


def _iter_strings(x: Any):
    if isinstance(x, str):
        yield x
    elif isinstance(x, list):
        for el in x:
            yield from _iter_strings(el)


def _dissect_entry(entry: Any):
    """Return (mutation_obj, timestamp_ms, embedded_user_id) from one entry."""
    if isinstance(entry, dict):
        return entry.get("mutation", entry), entry.get("timestamp") or entry.get("ts"), \
            entry.get("userId") or entry.get("uid")
    if not isinstance(entry, list):
        return entry, None, None
    # Confirmed canonical shape: [mutation, timestamp_ms, user_id, revision, ...]
    if (
        len(entry) >= 4
        and isinstance(entry[0], (dict, list))
        and isinstance(entry[1], int)
        and isinstance(entry[2], str)
    ):
        return entry[0], entry[1], entry[2]
    # Fallback heuristic for any shape drift.
    mut_obj = None
    ts = None
    uid = None
    for el in entry:
        if mut_obj is None and isinstance(el, (dict, list)):
            mut_obj = el
        elif isinstance(el, int) and el > 1_000_000_000_000:  # epoch-ms
            ts = el
        elif isinstance(el, str) and uid is None and len(el) > 6:
            uid = el
    return mut_obj if mut_obj is not None else entry, ts, uid
