"""Offline validation of parse -> replay -> analyze with a synthetic changelog.

Simulates two authors editing one document so we can confirm per-character
attribution, deletion handling, and stats without touching the network.
"""
from gdocs_forensics import parse, analyze
from gdocs_forensics.replay import Replayer

# Fake tiles response: user map + per-revision author ranges.
TILES = {
    "lastRev": 5,
    "userMap": {
        "uA": {"name": "Alice", "color": "#1b6ca8"},
        "uB": {"name": "Bob", "color": "#a8331b"},
    },
    "tileInfo": [
        {"firstRev": 1, "lastRev": 2, "userId": "uA"},  # Alice writes
        {"firstRev": 3, "lastRev": 3, "userId": "uB"},  # Bob inserts
        {"firstRev": 4, "lastRev": 4, "userId": "uB"},  # Bob deletes
        {"firstRev": 5, "lastRev": 5, "userId": "uA"},  # Alice appends
    ],
}

# Changelog: entry i -> revision i+1. Format [mutation, sid, timestamp_ms].
T0 = 1_700_000_000_000
CHANGELOG = [
    [{"ty": "is", "ibi": 1, "s": "Hello world"}, "s1", T0],          # rev1 Alice
    [{"ty": "is", "ibi": 12, "s": "!"}, "s1", T0 + 1000],            # rev2 Alice -> "Hello world!"
    [{"ty": "is", "ibi": 6, "s": "BIG "}, "s2", T0 + 2_000_000],     # rev3 Bob -> "Hello BIG world!"
    [{"ty": "ds", "si": 6, "ei": 9}, "s2", T0 + 2_001_000],         # rev4 Bob deletes "BIG " -> "Hello world!"
    [{"ty": "is", "ibi": 13, "s": "\nBy Alice"}, "s1", T0 + 5_000_000],  # rev5 Alice
]

user_map = parse.build_user_map(TILES)
rev_authors = parse.build_revision_author_index(TILES)
mutations, report = parse.parse_changelog(CHANGELOG, 1, rev_authors)

r = Replayer()
r.apply_all(mutations)
text = r.text()

print("Reconstructed:", repr(text))
assert text == "Hello world!\nBy Alice", text

surv = r.surviving_char_counts()
print("Surviving by uid:", surv)
# "Hello world!" (12) + "\nBy Alice" (9) = 21 chars all by Alice; Bob's survived 0.
assert surv.get("uA") == 21, surv
assert surv.get("uB", 0) == 0, surv

assert r.inserted_chars["uB"] == 4   # "BIG "
assert r.deleted_chars["uB"] == 4    # deleted her own "BIG "

stats = analyze.author_stats(r.cells, mutations, r.inserted_chars, r.deleted_chars, user_map)
for s in stats:
    print(f"  {s.name}: surviving={s.surviving_chars} inserted={s.inserted_chars} "
          f"deleted={s.deleted_chars} edits={s.edits} days={len(s.active_days)}")

paras = analyze.paragraph_attribution(r.cells, user_map)
print("Paragraphs:", [(p["dominant"], p["text"]) for p in paras])
assert report["ops"]["insert"] == 4 and report["ops"]["delete"] == 1

# Insights bundle smoke test (edit-war, pastes, playback, serialization).
import json as _json
from gdocs_forensics import insights
bundle = insights.build_bundle(
    doc_id="dummy", last_rev=5, user_map=user_map, mutations=mutations, replayer=r,
    structure={"links": [], "images": [], "lists": [], "tables": [],
               "comment_anchors": [], "headings": []},
    tab_titles={"t.0": "Main"}, segments=["t.0"], generated_ms=1_700_000_000_000)
_json.dumps(bundle)  # must be JSON-serializable
assert bundle["total_chars"] == len("Hello world!\nBy Alice")
assert any(a["name"] == "Alice" for a in bundle["authors"])
# Bob inserted "BIG " then deleted it -> appears in the edit-war deletions.
assert r.deletions, "tombstones should record the deletion"
print("insights bundle OK:", len(bundle["authors"]), "authors,",
      len(bundle["playback"]), "playback segment(s)")

print("\nALL OFFLINE ASSERTIONS PASSED")
