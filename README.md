# gdocs-forensics

Reconstructs a Google Doc's full edit history with **per-character, per-tab
authorship**, by replaying Google Docs' internal revision changelog. Built for
authorship disputes where you need "who wrote this exact text," not just "who
edited when."

## Quick start (step by step)

> **Privacy:** this tool reads **only your own local browser cookies** to talk to
> Google *as you*. Nothing is uploaded anywhere; no passwords or cookies are ever
> stored or committed. Run it on the computer where **you** are signed in to the
> Google account that can open the document.

### 0. Prerequisites
- **[uv](https://docs.astral.sh/uv/)** — the only thing you need to install; it
  manages Python and dependencies for you.
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows (PowerShell): `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **git**
- The Google account that has access to the document, **signed in to Chrome**
  (or Firefox / Edge / Safari / Brave) on this computer.

### 1. Clone the repository
```bash
git clone https://github.com/SidSaxena/gdocs-forensics.git
cd gdocs-forensics
```

### 2. Install dependencies
```bash
uv sync
```
That's it — `uv` creates the environment and installs everything (it even fetches
a suitable Python if you don't have one). Same command on macOS, Linux, Windows.

### 3. Sign in
Open the target Google Doc in **Chrome** (or your chosen browser) and confirm you
can see it. Leave that browser signed in. (On the newest Chrome, if step 4 can't
read cookies, use `--browser firefox` or `--browser safari` instead — see
Troubleshooting.)

### 4. Run it
```bash
uv run gdocs-forensics \
  --url "https://docs.google.com/document/d/<YOUR_DOC_ID>/edit" \
  --browser chrome \
  --out ./output
```
- Paste the full document URL (or just the id) after `--url`.
- One run captures **all tabs** automatically — you don't list tab ids.
- It takes ~30–60s. Add `--no-pdf` to skip the PDF.
- `uv run` activates the environment automatically — no `source .venv/...` needed.
  (Equivalent: `uv run python -m gdocs_forensics.main --url ...`.)

### 5. Open the results
Everything is written to `./output/`:
- **`output/dashboard.html`** — double-click to open the full interactive report
  in your browser (offline, no server).
- **`output/report.pdf`** — the static report (executive summary first) for sharing.
- **`output/all_tabs_colored.html`** — the whole document colored by author.

### Troubleshooting
- **"No Google session cookies found"** — you're not signed in in that browser, or
  (newest Chrome on macOS/Windows) cookie encryption is blocking the read. Try
  `--browser firefox` or `--browser safari`, or export a `cookies.txt` for
  docs.google.com and pass `--cookies cookies.txt`.
- **403 / 404 from the endpoint** — the signed-in account can't open that document.
  Sign in as an account that has access.
- **"Could not determine revision count"** — inspect `output/evidence/raw/` and
  open an issue; Google may have changed the response shape.

## How it works
1. Reads your **own** signed-in browser session cookies locally (never anyone
   else's; nothing is transmitted except to Google, as you). Each person runs it
   authenticated as themselves, on documents their account can open.
2. Handshake: the first call to `revisions/tiles` returns an XSRF token that must
   be echoed on later calls.
3. Finds the **true** last revision. Google's `["di", N]` hint badly
   under-reports (it returned 30 for a doc with 5,515 revisions), so we
   binary-search the largest revision range the `load` endpoint still serves.
4. Fetches the whole `revisions/load` changelog in one pass. This single global
   stream contains **every tab's** history.
5. Replays the mutations **per segment**. Multi-tab docs scope each tab's edits
   inside an `nm` wrapper (`nmr: ["ksm", "t.<id>"]`, `nmc: <the insert/delete>`).
   We keep one text buffer per tab, so each tab reconstructs independently with
   every character stamped by its author.
6. Writes a report, per-tab color-coded HTML and CSV, and preserves the raw API
   responses + a SHA-256 manifest under `evidence/`.

## Command reference
```
uv run gdocs-forensics --url URL [options]
  --url       Google Doc URL or document id (required)
  --browser   chrome | chromium | firefox | safari | edge | brave   (default: chrome)
  --cookies   path to an exported cookies.txt (overrides --browser)
  --out       output directory (default: ./output)
  --no-pdf    skip the PDF report
```

## Outputs
- **`dashboard.html`** — a single self-contained, offline interactive report:
  document-wide ownership, per-tab authorship, "who built it when" timeline,
  activity-by-hour heatmap, the **edit-war matrix** (who deleted whose words),
  **recovered deleted text**, likely **pastes**, **links/structure** by author,
  per-character **colored text**, and a Draftback-style **playback** scrubber.
  No server or network needed; can be hashed for chain-of-custody.
- **`report.pdf`** — a static multi-page version, opening with a one-page
  **executive summary** (ownership, who-typed-vs-pasted, who-deleted-whose-work,
  per-tab owners), for formal submission.
- **`all_tabs_colored.html`** — a standalone, printable page: the entire document,
  all tabs in creation order, every character colored by its author.
- `insights.json` — the full computed analytics bundle (machine-readable).
- `report.md` — per-tab authorship breakdown, document-wide author table,
  editing sessions, and paragraph-level attribution grouped by tab.
- `tabs/<tabid>/attributed_text.html` — that tab, each character colored by author.
- `tabs/<tabid>/reconstructed.txt` — the rebuilt tab text (sanity-check vs. the
  live tab's File → Download → plain text).
- `tabs/<tabid>/timeline.csv` — every insert/delete in that tab, with author,
  segment, timestamp, and text.
- `evidence/raw/*.json` + `evidence/manifest.json` — untouched API responses with
  SHA-256 hashes, for chain-of-custody.

### What gets analyzed
Per-character authorship and survival, typed-vs-pasted split, per-tab ownership,
who created each tab, cumulative contribution over time, activity by hour,
editing sessions, the author×author deletion matrix, recovered deleted passages,
hyperlinks/images/lists/tables/headings/comment-anchors by author, and
paragraph-level attribution. Comment *thread text* needs the Drive API (OAuth);
only comment-anchor authorship is taken from the revision stream.

## Validation
Reconstructed tab lengths match the live document's per-tab text export within a
few percent (the small delta is ongoing edits between runs). `uv run python
test_offline.py` validates parse → replay → analyze on a synthetic two-author
changelog, including
insert/delete attribution and edit-war accounting (text inserted then deleted
survives as 0 chars).

## Important caveats (read before relying on this for a dispute)
- These endpoints are **undocumented and unofficial**; Google can change the
  response shape at any time. The parser is defensive and always keeps raw data.
  Check `report.md`'s "Parse / replay diagnostics" — unrecognized mutations or
  replay warnings mean a reconstruction may be incomplete.
- For a **legal** proceeding, a self-run extract is the weakest form of evidence.
  Pair it with (a) Google's native File → Version history (screen-recorded; it
  shows per-author colored changes per tab) and ideally (b) a formal request /
  subpoena to Google or Google Vault, which produce the authoritative record.
  This tool is for investigation and corroboration.
- Attribution is only as honest as the data: pasted text is attributed to the
  person who pasted it, not its original source. Large single inserts in
  `timeline.csv` flag likely pastes worth scrutinizing.
