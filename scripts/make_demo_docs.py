"""Render docs/demo.md, the readable demo page, from data/demo/recording.json.

No API call. Each answer is rebuilt at $0 from the recording (recorded facts,
matcher verdicts and model text) with the current renderer:

- if the rebuilt text equals the recorded answer, it is shown as recorded;
- if it differs, the change can only be in code-written lines (the model's text is
  replayed as recorded and must still pass every check), and the page says so.

Each example gets a summary table (one row per "## " heading) and the full answer
in a collapsible block. Titles and notes are our annotations.

    uv run python scripts/make_demo_docs.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eu_ai_check.e2e import runner
from eu_ai_check.generation.checks import run_checks
from eu_ai_check.generation.render import REFERENCE_ONLY
from eu_ai_check.generation.render import render as render_answer
from eu_ai_check.generation.schema import parse_answer

RECORDING = Path("data/demo/recording.json")
OUT = Path("docs/demo.md")

TITLES = {  # short reader-facing titles; the recording's `covers` stays the source label
    "X01": "Language-school placement tool",
    "X02": "Vague HR tool: it asks what it needs to know",
    "X03": "Bakery forecast before the Act was in force",
    "X04": "Inferring voters' political opinions from face photos",
    "X05": "Where it goes wrong: recruitment document check",
}

NOTES = {  # our annotations; replace the recording's note where given
    "X01": "**Note:** this answer follows the 2024 text the tool uses. Regulation (EU) "
           "2026/1744, in force since 27 July 2026, moved the high-risk rules for Annex III "
           "systems, including Articles 16 and 26, to 2 December 2027, so under the "
           "amended Act they do not apply yet on 1 November 2026.",
    "X04": "**Note:** the prohibition (Article 5(1)(g), biometric categorisation inferring "
           "political opinions) is the finding that matters. The high-risk classification "
           "and Articles 16 and 26 are still determined and shown, marked as for reference "
           "only. Some quoted Article 26 paragraphs do not fit this case, such as 26(10) on "
           "criminal investigations (see [limitations.md](limitations.md)). The prohibition "
           "is not affected by the 2026 amendment (Regulation (EU) 2026/1744).",
    "X05": "**Shown because it goes wrong.** The answer is \"not high-risk\" through the "
           "Article 6(3) derogation. That is debatable: returning incomplete applications "
           "before any recruiter reads them is close to \"filtering applications\", which "
           "Annex III(4)(a) lists as high-risk, and a lawyer could go either way. The "
           "matcher also cited Article 6(3)(b) (improving a previously completed human "
           "activity), which nothing in the description supports. Every automatic check "
           "passed, because the checks verify that citations exist and quote the Act, not "
           "that applying them is correct. Also, \"Article 26 does not apply\" does not mean "
           "no obligations at all: provisions outside this tool's scope, such as Article 4 "
           "(AI literacy), can still apply. See [limitations.md](limitations.md).",
}

RERENDER_NOTE = ("**Re-rendered on 2026-11-11** with the current renderer, at $0 and with no "
                 "API call, from this example's recorded facts, matcher verdicts and model "
                 "text. The model's words are unchanged. Only code-written lines differ "
                 "from the recording: the prohibited-practice notice at the top and the "
                 "\"for reference only\" marks, added after the evaluation so that the "
                 "high-risk sections no longer read as a way to comply. The answer as "
                 "originally recorded is in [`data/demo/recording.md`]"
                 "(../data/demo/recording.md#x04-a-prohibited-practice).")


def rebuild(examples: list[dict]) -> dict[str, str]:
    """Answer text per example from the current renderer; raises if a rebuilt answer
    fails a check or if its determinations no longer replay."""
    env = runner._Env()
    out = {}
    for x in examples:
        run = runner.restore(x["run"], {"description": x["input"]}, env)
        if run.gen_input is None or x["shipped_answer"] is None or x["fallback"]:
            out[x["id"]] = x["shipped_answer"]
            continue
        answer, _ = parse_answer(x["raw_model_output"])
        rendered = render_answer(run.gen_input, answer)
        if not run_checks(run.gen_input, rendered, answer, corpus_texts=env.texts).passed:
            raise SystemExit(f"{x['id']}: rebuilt answer fails a check")
        out[x["id"]] = rendered.to_text()
    return out


def summary_rows(answer: str) -> list[tuple[str, str]]:
    rows = []
    lines = answer.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## ") and ": " in line and not line.startswith("## What we would"):
            head, _, verdict = line[3:].partition(": ")
            if i + 1 < len(lines) and lines[i + 1] == REFERENCE_ONLY:
                verdict += " *(for reference only)*"
            rows.append((head, verdict))
    return rows


def render(rec: dict) -> str:
    out = [
        "# Demo: five recorded examples",
        "",
        "Five new descriptions, run once on 2026-11-04 through the frozen pipeline "
        f"(`{rec['model']}` for every stage). The answers are shown **exactly as produced, "
        "unedited** (X04 is re-rendered by code; see its note). This is a demonstration, not an evaluation: the examples have no "
        "labels and are not scored. Titles and notes are our annotations.",
        "",
        f"Total demo spend: ${rec['spend_usd']:.4f}. The same output in raw form is in "
        "[`data/demo/recording.md`](../data/demo/recording.md); the full record is "
        "[`recording.json`](../data/demo/recording.json). Known weaknesses visible here "
        "are listed in [limitations.md](limitations.md).",
        "",
    ]
    rebuilt = rebuild(rec["examples"])
    for e in rec["examples"]:
        title = TITLES.get(e["id"], e["covers"])
        answer = rebuilt[e["id"]]
        changed = answer != e["shipped_answer"]
        out += [f"## {e['id']}: {title}", "", f"> {e['input']}", ""]
        if answer and answer.startswith("Important:"):
            out += [f"**{answer.splitlines()[0]}**", ""]
        rows = summary_rows(answer or "")
        if rows:
            out += ["| Determination | Answer |", "|---|---|"]
            out += [f"| {h} | {v.replace('|', '/')} |" for h, v in rows]
            out.append("")
        out.append(f"Cost: ${e['spend_usd']:.4f} ({len(e['calls'])} API calls). "
                   f"Template fallback: {'yes' if e['fallback'] else 'no'}.")
        out.append("")
        if changed:
            out += [RERENDER_NOTE, ""]
        note = NOTES.get(e["id"], e.get("note"))
        if note:
            out += [note if note.startswith("**") else f"**Note:** {note}", ""]
        summary = "Full answer (model text unedited)" if changed else "Full answer, unedited"
        out += ["<details>", f"<summary>{summary}</summary>", "", "```text",
                (answer or f"No answer: {e['stage_failed']}: {e['error']}").rstrip(),
                "```", "", "</details>", ""]
    return "\n".join(out)


def main() -> None:
    rec = json.loads(RECORDING.read_text(encoding="utf-8"))
    OUT.write_text(render(rec), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
