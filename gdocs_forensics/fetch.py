"""Fetch raw revision data from Google Docs' internal endpoints.

Two endpoints are used, both authenticated by the caller's own session cookies:

  - revisions/tiles : metadata. Gives the true revision range (firstRev/lastRev),
                      a userMap (id -> display name / color / anonymous flag), and
                      a per-revision-range author breakdown.
  - revisions/load  : the atomic change log. Every insert/delete mutation, in
                      order, one entry per revision.

Everything fetched is written verbatim to a raw/ directory with a SHA-256
manifest before any parsing happens, so results can be verified or reproduced.

NOTE: these endpoints are undocumented and unofficial. Response shapes drift over
time, so the parser (parse.py) is written defensively and we always keep the raw
bytes. This tool reads the signed-in user's *own* documents only.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from typing import Any, Optional

import requests

XSSI_PREFIX = ")]}'"

BASE = "https://docs.google.com/document/d/{doc_id}/revisions/{kind}"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _strip_xssi(text: str) -> Any:
    text = text.lstrip()
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX):]
    return json.loads(text)


class RevisionFetcher:
    def __init__(self, doc_id: str, cookiejar, raw_dir: str,
                 authuser: Optional[int] = None):
        self.doc_id = doc_id
        self.raw_dir = raw_dir
        # When several accounts share one browser session, `authuser` selects
        # which signed-in account the request acts as (0 = first/default).
        self.authuser = authuser
        os.makedirs(raw_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.cookies = requests.cookies.merge_cookies(
            self.session.cookies, cookiejar
        )
        self.session.headers.update({"User-Agent": UA, "X-Same-Domain": "1"})
        self._manifest: list[dict] = []
        self.token: Optional[str] = None

    # -- low level -----------------------------------------------------------
    def _get(self, kind: str, params: dict, tag: str, tab: Optional[str] = None,
             save: bool = True) -> Any:
        url = BASE.format(doc_id=self.doc_id, kind=kind)
        params = {"id": self.doc_id, **params}
        if tab:
            params["tab"] = tab
        if self.authuser is not None:
            params["authuser"] = self.authuser
        # Retry with backoff on rate limiting (the export/revision endpoints 429).
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=60)
            if resp.status_code != 429:
                break
            time.sleep(1.5 * (attempt + 1))
        if not save:
            return _strip_xssi(resp.text)
        raw_path = os.path.join(self.raw_dir, f"{tag}.json")
        with open(raw_path, "wb") as fh:
            fh.write(resp.content)
        digest = hashlib.sha256(resp.content).hexdigest()
        self._manifest.append(
            {
                "tag": tag,
                "url": resp.url,
                "status": resp.status_code,
                "bytes": len(resp.content),
                "sha256": digest,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
        if resp.status_code == 401 or resp.status_code == 403:
            raise PermissionError(
                f"{resp.status_code} from {kind}. The signed-in account cannot "
                f"read this document's history, or you are not signed in."
            )
        if resp.status_code == 404:
            raise FileNotFoundError(
                f"404 from {kind}. Bad document id, or no access."
            )
        resp.raise_for_status()
        return _strip_xssi(resp.text)

    # -- public --------------------------------------------------------------
    def bootstrap(self, tab: Optional[str] = None) -> int:
        """First call: the endpoint returns a control list carrying an XSRF token
        we must echo on subsequent requests, plus ``["di", N]`` = current last
        revision. Returns the last revision number for the given tab.
        """
        tag = f"bootstrap_{tab}" if tab else "bootstrap"
        data = self._get(
            "tiles",
            {"start": 1, "end": 1, "showDetailedRevisions": "true",
             "filterNamed": "false"},
            tag=tag, tab=tab,
        )
        last_rev = 0
        if isinstance(data, list):
            for cmd in data:
                if isinstance(cmd, list) and cmd:
                    if cmd[0] == "er" and len(cmd) > 4 and isinstance(cmd[4], str):
                        self.token = cmd[4]
                    elif cmd[0] == "di" and len(cmd) > 1 and isinstance(cmd[1], int):
                        last_rev = cmd[1]
        if not self.token:
            raise RuntimeError(
                "Could not obtain an XSRF token from the bootstrap response. "
                f"Inspect raw/{tag}.json."
            )
        return last_rev

    def _has_revisions(self, end: int, tab: Optional[str]) -> bool:
        """True if a load request for [1, end] returns a non-empty changelog.
        Used only for probing — responses are not saved to disk."""
        data = self._get("load", {"start": 1, "end": end, "token": self.token},
                          tag="probe", tab=tab, save=False)
        if isinstance(data, dict):
            return bool(data.get("changelog"))
        return False

    def find_last_revision(self, tab: Optional[str], hint: int = 1) -> int:
        """Find the TRUE last revision. ``di`` from bootstrap badly under-reports
        (it returned 30 for a tab with 5,515 revisions), and an over-large ``end``
        yields an empty response rather than clamping — so we exponentially probe
        upward from a hint until the response goes empty, then binary-search the
        boundary. The largest ``end`` that still returns data is the last revision.
        """
        if not self._has_revisions(max(hint, 1), tab):
            # Even the hint is too high (or no data); search down from the hint.
            lo, hi = 1, max(hint, 1)
        else:
            lo = max(hint, 1)
            hi = max(lo * 2, 2)
            while self._has_revisions(hi, tab):
                lo = hi
                hi *= 2
                if hi > 5_000_000:
                    break
        # Invariant: lo returns data, hi does not. Binary-search the edge.
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._has_revisions(mid, tab):
                lo = mid
            else:
                hi = mid
        return lo

    def tiles(self, end: int, tab: Optional[str] = None) -> dict:
        """Metadata + author map (requires a token from bootstrap())."""
        tag = f"tiles_{tab}" if tab else "tiles"
        return self._get(
            "tiles",
            {"start": 1, "end": end, "showDetailedRevisions": "true",
             "filterNamed": "false", "token": self.token},
            tag=tag, tab=tab,
        )

    def load_chunk(self, start: int, end: int, tab: Optional[str] = None) -> dict:
        tag = f"load_{tab + '_' if tab else ''}{start}_{end}"
        return self._get(
            "load",
            {"start": start, "end": end, "token": self.token},
            tag=tag, tab=tab,
        )

    def load_all(self, last_rev: int, chunk: int = 1000,
                 tab: Optional[str] = None) -> list[Any]:
        """Pull the full changelog across [1, last_rev] in chunks."""
        changelog: list[Any] = []
        start = 1
        while start <= last_rev:
            end = min(start + chunk - 1, last_rev)
            data = self.load_chunk(start, end, tab=tab)
            entries = data.get("changelog") if isinstance(data, dict) else data
            if entries:
                changelog.extend(entries)
            start = end + 1
        return changelog

    def _stream_fingerprint(self, tab: Optional[str]) -> Optional[str]:
        """A stable id for the revision stream a (possibly bogus) tab maps to.

        Google ignores an unknown ``tab`` param and serves the default stream, so
        we can't trust that a tab "exists" just because it returns data. Instead
        we fingerprint the stream by revision 1's content MAC: every real tab has
        a distinct first revision; bogus tabs all collapse onto the default's.
        """
        try:
            last_rev = self.bootstrap(tab=tab)
            if not last_rev:
                return None
            tiles = self.tiles(last_rev, tab=tab)
        except Exception:
            return None
        if not isinstance(tiles, dict):
            return None
        for tile in tiles.get("tileInfo", []):
            if isinstance(tile, dict) and tile.get("start") == 1:
                mac = tile.get("revisionMac")
                if mac:
                    return f"{mac}"
        return None

    def enumerate_tabs(self) -> list[str]:
        """Discover the document's *distinct* tab streams from its edit page.

        We scrape candidate ``t.xxxx`` ids, then keep only one id per distinct
        stream fingerprint — collapsing JS-noise ids (which all resolve to the
        default stream) and de-duplicating, so the result is the real tab set.
        """
        import re
        from collections import Counter

        url = f"https://docs.google.com/document/d/{self.doc_id}/edit"
        resp = self.session.get(url, timeout=60)
        with open(os.path.join(self.raw_dir, "edit_page.html"), "wb") as fh:
            fh.write(resp.content)
        # Prefer real-looking ids (long, containing a digit) and higher frequency.
        counts = Counter(re.findall(r"t\.[a-z0-9]{6,}", resp.text))
        candidates = sorted(
            counts,
            key=lambda t: (bool(re.search(r"\d", t)) and len(t) >= 12, counts[t]),
            reverse=True,
        )
        seen_streams: dict[str, str] = {}
        for tid in candidates:
            fp = self._stream_fingerprint(tid)
            if fp and fp not in seen_streams:
                seen_streams[fp] = tid
        return list(seen_streams.values())

    def write_manifest(self) -> str:
        path = os.path.join(self.raw_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"doc_id": self.doc_id, "artifacts": self._manifest},
                fh,
                indent=2,
            )
        return path


def detect_last_rev(tiles_data: dict) -> Optional[int]:
    """Find the latest revision number from a tiles response, tolerating shape drift."""
    for key in ("lastRev", "last_rev", "endRev"):
        if isinstance(tiles_data.get(key), int):
            return tiles_data[key]
    # Fall back to scanning tileInfo / revisions arrays for the max revision.
    best = 0
    for container_key in ("tileInfo", "revisions", "tiles"):
        for item in tiles_data.get(container_key, []) or []:
            if isinstance(item, dict):
                for k in ("lastRev", "endRev", "end", "revision", "rev"):
                    v = item.get(k)
                    if isinstance(v, int):
                        best = max(best, v)
    return best or None
