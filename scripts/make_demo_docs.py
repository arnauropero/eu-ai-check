"""Render docs/demo.md, the readable demo page, from data/demo/recording.json.

No API call. The model output is copied unedited: each example gets a summary table
(one row per "## " heading of the shipped answer) and the full answer in a
collapsible block. Titles and notes come from the recording and are annotations.

    uv run python scripts/make_demo_docs.py
"""

from __future__ import annotations

import json
from pathlib import Path

RECORDING = Path("data/demo/recording.json")
OUT = Path("docs/demo.md")

TITLES = {  # short reader-facing titles; the recording's `covers` stays the source label
    "X01": "Language-school placement tool",
    "X02": "Vague HR tool: it asks what it needs to know",
    "X03": "Bakery forecast before the Act was in force",
    "X04": "Inferring voters' political opinions from face photos",
    "X05": "Recruitment document completeness check",
}


def summary_rows(answer: str) -> list[tuple[str, str]]:
    rows = []
    for line in answer.splitlines():
        if line.startswith("## ") and ": " in line and not line.startswith("## What we would"):
            head, _, verdict = line[3:].partition(": ")
            rows.append((head, verdict))
    return rows


def render(rec: dict) -> str:
    out = [
        "# Demo: five recorded examples",
        "",
        "Five new descriptions, run once on 2026-11-04 through the frozen pipeline "
        f"(`{rec['model']}` for every stage). The answers are shown **exactly as produced, "
        "unedited**. This is a demonstration, not an evaluation: the examples have no "
        "labels and are not scored. Titles and notes are our annotations.",
        "",
        f"Total demo spend: ${rec['spend_usd']:.4f}. The same output in raw form is in "
        "[`data/demo/recording.md`](../data/demo/recording.md); the full record is "
        "[`recording.json`](../data/demo/recording.json). Known weaknesses visible here "
        "are listed in [limitations.md](limitations.md).",
        "",
    ]
    for e in rec["examples"]:
        title = TITLES.get(e["id"], e["covers"])
        out += [f"## {e['id']}: {title}", "", f"> {e['input']}", ""]
        rows = summary_rows(e["shipped_answer"] or "")
        if rows:
            out += ["| Determination | Answer |", "|---|---|"]
            out += [f"| {h} | {v.replace('|', '/')} |" for h, v in rows]
            out.append("")
        out.append(f"Cost: ${e['spend_usd']:.4f} ({len(e['calls'])} API calls). "
                   f"Template fallback: {'yes' if e['fallback'] else 'no'}.")
        out.append("")
        if e.get("note"):
            out += [f"**Note:** {e['note']}", ""]
        out += ["<details>", "<summary>Full answer, unedited</summary>", "", "```text",
                (e["shipped_answer"] or f"No answer: {e['stage_failed']}: {e['error']}").rstrip(),
                "```", "", "</details>", ""]
    return "\n".join(out)


def main() -> None:
    rec = json.loads(RECORDING.read_text(encoding="utf-8"))
    OUT.write_text(render(rec), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
