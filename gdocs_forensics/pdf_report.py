"""Static multi-page PDF report from the insights bundle, for formal submission.
Uses matplotlib (Agg) so it needs no display and no other dependency.
"""

from __future__ import annotations

import re
import matplotlib
matplotlib.use("Agg")
# Document text can contain '$' (e.g. LaTeX-like math); disable mathtext parsing
# so those are rendered literally instead of raising.
matplotlib.rcParams["text.parse_math"] = False
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np


# The bundled matplotlib font (DejaVu) has no emoji glyphs; strip emoji/symbols
# and control characters so PDF labels don't render as tofu boxes.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "︀-️←-⇿⬀-⯿⌀-⏿]")


def _clean(s, fallback=""):
    s = _EMOJI.sub("", str(s))
    s = "".join(ch if ch == "\n" or ord(ch) >= 32 else " " for ch in s)
    return s.strip() or fallback


def _fig():
    return plt.figure(figsize=(11, 8.5))


def _title(fig, t, sub=""):
    fig.text(0.06, 0.95, t, fontsize=18, fontweight="bold")
    if sub:
        fig.text(0.06, 0.915, sub, fontsize=10, color="#555")


def render(bundle: dict, path: str) -> None:
    A = bundle["authors"]
    names = [a["name"] for a in A]
    colors = [a["color"] for a in A]

    with PdfPages(path) as pdf:
        # ---- executive summary (page 1) ----
        X = bundle.get("executive")
        if X:
            fig = _fig()
            _title(fig, "Executive summary",
                   "  ·  ".join(X.get("facts", [])))
            y = 0.86
            fig.text(0.06, y, X.get("headline", ""), fontsize=12.5,
                     fontweight="bold", wrap=True)
            y = 0.78
            def block(label, items):
                nonlocal y
                fig.text(0.06, y, label, fontsize=11, fontweight="bold",
                         color="#1a3a6b"); y -= 0.028
                for it in items:
                    for ln in _wrap(it, 105):
                        fig.text(0.08, y, "• " + ln if ln == _wrap(it, 105)[0] else "  " + ln,
                                 fontsize=9.5); y -= 0.02
                y -= 0.012
            block("Final-text ownership",
                  [f"{o['name']}: {o['pct']}%  ({o['surviving']:,} chars)"
                   for o in X.get("ownership", [])])
            block("Authorship style", X.get("authorship_style", []))
            block("Deletions between authors",
                  X.get("deletions", []) or ["No cross-author deletions"])
            block("Tab ownership",
                  [f"{t['tab']} → {t['owner']} ({t['pct']}%)"
                   for t in X.get("tab_owners", [])])
            # ownership bar
            ax = fig.add_axes([0.62, 0.10, 0.32, 0.30])
            ow = X.get("ownership", [])
            ax.barh(range(len(ow)), [o["pct"] for o in ow],
                    color=[o["color"] for o in ow])
            ax.set_yticks(range(len(ow))); ax.set_yticklabels([o["name"] for o in ow], fontsize=8)
            ax.invert_yaxis(); ax.set_xlabel("% of surviving text", fontsize=8)
            fig.text(0.06, 0.04, "AI was not used to generate this summary; all "
                     "figures are computed directly from the revision history.",
                     fontsize=7.5, color="#777")
            pdf.savefig(fig); plt.close(fig)

        # ---- author detail table ----
        fig = _fig()
        _title(fig, "Document Revision Report",
               f"Document {bundle['doc_id']}  ·  {bundle['total_revs']} revisions  ·  "
               f"{bundle['total_chars']:,} surviving chars  ·  generated {bundle['generated']}")
        tot = sum(a["surviving"] for a in A) or 1
        rows = [[a["name"], f"{a['surviving']:,}", f"{100*a['surviving']/tot:.1f}%",
                 f"{a['inserted']:,}", f"{a['deleted']:,}",
                 f"{a['survival_rate']*100:.0f}%", f"{a['typed']:,}", f"{a['pasted']:,}",
                 f"{a['edits']:,}", str(a["active_days"])] for a in A]
        ax = fig.add_axes([0.06, 0.45, 0.88, 0.4]); ax.axis("off")
        tbl = ax.table(cellText=rows,
                       colLabels=["Author", "Surviving", "Share", "Inserted", "Deleted",
                                  "Survival", "Typed", "Pasted", "Edits", "Days"],
                       loc="upper center", cellLoc="center")
        tbl.auto_set_font_size(False); tbl.set_fontsize(9); tbl.scale(1, 1.6)
        for j, a in enumerate(A):
            tbl[(j + 1, 0)].set_facecolor(a["color"]); tbl[(j + 1, 0)].set_text_props(color="white")
        # ownership donut
        ax2 = fig.add_axes([0.30, 0.06, 0.4, 0.34])
        ax2.pie([a["surviving"] for a in A], colors=colors, labels=names,
                autopct="%1.0f%%", wedgeprops=dict(width=0.42), textprops={"fontsize": 9})
        ax2.set_title("Final-text ownership", fontsize=11)
        pdf.savefig(fig); plt.close(fig)

        # ---- per-tab stacked bars ----
        fig = _fig(); _title(fig, "Per-tab authorship")
        ax = fig.add_axes([0.28, 0.12, 0.66, 0.78])
        tabs = bundle["tabs"]; y = np.arange(len(tabs))
        left = np.zeros(len(tabs))
        for i, a in enumerate(A):
            vals = np.array([t["by_author"].get(str(i), t["by_author"].get(i, 0)) for t in tabs])
            ax.barh(y, vals, left=left, color=a["color"], label=a["name"])
            left += vals
        ax.set_yticks(y); ax.set_yticklabels([_clean(t["title"], "(untitled)")[:28] for t in tabs])
        ax.invert_yaxis(); ax.set_xlabel("characters"); ax.legend(loc="lower right", fontsize=8)
        pdf.savefig(fig); plt.close(fig)

        # ---- cumulative timeline ----
        C = bundle["timeline"]["cumulative"]
        if C:
            fig = _fig(); _title(fig, "Who built the document, and when",
                                 "cumulative characters inserted over time")
            ax = fig.add_axes([0.09, 0.12, 0.85, 0.74])
            x = np.arange(len(C))
            for i, a in enumerate(A):
                ax.plot(x, [c["by"].get(str(i), c["by"].get(i, 0)) for c in C],
                        color=a["color"], lw=2, label=a["name"])
            nt = min(9, len(C))
            idxs = sorted(set(int(round(k * (len(C) - 1) / (nt - 1)))
                              for k in range(nt))) if len(C) > 1 else [0]
            ax.set_xticks(idxs)
            ax.set_xticklabels([C[i]["ts"][:16].replace("T", " ") for i in idxs],
                               rotation=40, ha="right", fontsize=8)
            ax.grid(axis="x", color="#eee", lw=0.6)
            ax.set_ylabel("cumulative chars"); ax.legend(fontsize=8)
            pdf.savefig(fig); plt.close(fig)

        # ---- activity heatmap ----
        hh = bundle["timeline"]["hour"]
        fig = _fig(); _title(fig, "Activity by hour of day (UTC)")
        ax = fig.add_axes([0.18, 0.3, 0.76, 0.5])
        mat = np.array([hh.get(str(i), hh.get(i, [0]*24)) for i in range(len(A))])
        im = ax.imshow(mat, aspect="auto", cmap="Blues")
        ax.set_yticks(range(len(A))); ax.set_yticklabels(names)
        ax.set_xticks(range(0, 24, 2)); ax.set_xlabel("hour (UTC)")
        fig.colorbar(im, ax=ax, fraction=0.025, label="edits")
        pdf.savefig(fig); plt.close(fig)

        # ---- deletion: cross-author deletions ----
        M = bundle["deletions"]["matrix"]; n = len(A)

        def mget(o, d):  # bundle keys may be int (in-memory) or str (from JSON)
            row = M.get(o, M.get(str(o), {})) or {}
            return row.get(d, row.get(str(d), 0)) or 0

        full = np.array([[mget(o, d) for d in range(n)] for o in range(n)], dtype=float)
        # Color only by CROSS-author deletions; the diagonal (self-revision) is
        # huge and uninteresting here, so mask it and label it separately.
        off = full.copy()
        for i in range(n):
            off[i, i] = np.nan
        vmax = np.nanmax(off) if n > 1 and np.isfinite(np.nanmax(off)) else 1
        cmap = plt.cm.Reds.copy(); cmap.set_bad("#f0f0f0")

        fig = _fig(); _title(fig, "Deletions between authors",
                             "characters of each author's text removed by each other author "
                             "(diagonal = self-revision, greyed)")
        ax = fig.add_axes([0.26, 0.26, 0.52, 0.56])
        im = ax.imshow(np.ma.masked_invalid(off), cmap=cmap, vmin=0, vmax=max(vmax, 1))
        ax.set_xticks(range(n)); ax.set_xticklabels(names, rotation=25, ha="right")
        ax.set_yticks(range(n)); ax.set_yticklabels(names)
        ax.set_xlabel("deleted by →"); ax.set_ylabel("← original author")
        for o in range(n):
            for d in range(n):
                v = int(full[o, d])
                if o == d:
                    ax.text(d, o, f"{v:,}\n(self)", ha="center", va="center",
                            fontsize=7.5, color="#999")
                else:
                    shade = "white" if (vmax and full[o, d] > 0.6 * vmax) else "black"
                    ax.text(d, o, f"{v:,}", ha="center", va="center",
                            fontsize=11, color=shade, fontweight="bold")
        fig.colorbar(im, ax=ax, fraction=0.045, label="cross-author chars deleted")
        # a plain-language caption of the top cross-author deletions
        dels = bundle.get("executive", {}).get("deletions", [])
        fig.text(0.06, 0.16, "Highlights:", fontsize=10, fontweight="bold")
        for k, line in enumerate(dels[:5]):
            fig.text(0.08, 0.13 - k * 0.022, "• " + line, fontsize=9)
        pdf.savefig(fig); plt.close(fig)

        # ---- text appendix: deleted passages ----
        _text_page(pdf, "Deleted text (top passages)",
                   [f"[{AN(A,p['orig_author'])} → deleted by {AN(A,p['del_author'])}, "
                    f"{(p['del_ts'] or '')[:16]}, {p['len']} chars]\n{p['text'][:500]}"
                    for p in bundle["deletions"]["passages"][:18]])

        # ---- text appendix: pastes ----
        _text_page(pdf, "Large inserts / likely pastes",
                   [f"[{AN(A,p['author'])}, {(p['ts'] or '')[:16]}, {p['size']:,} chars] "
                    f"{p['preview']}" for p in bundle["pastes"][:22]])


def AN(A, i):
    return A[i]["name"] if isinstance(i, int) and 0 <= i < len(A) else "?"


def _text_page(pdf, title, blocks):
    fig = _fig(); _title(fig, title)
    y = 0.88
    for b in blocks:
        for line in _wrap(b, 110):
            if y < 0.06:
                pdf.savefig(fig); plt.close(fig); fig = _fig(); _title(fig, title + " (cont.)"); y = 0.88
            fig.text(0.06, y, line, fontsize=8, family="monospace")
            y -= 0.018
        y -= 0.012
    pdf.savefig(fig); plt.close(fig)


def _wrap(text, width):
    out = []
    text = _clean(str(text).replace("\t", "    "))  # strip tabs/emoji/control
    for para in text.split("\n"):
        while len(para) > width:
            out.append(para[:width]); para = para[width:]
        out.append(para)
    return out
