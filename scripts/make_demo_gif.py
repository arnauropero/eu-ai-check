"""Render docs/assets/demo.gif: a terminal-style replay of recorded demo examples.

No API call. Text comes from data/demo/recording.json, unedited but trimmed: each
"## " determination line is shown with its cited provisions (ids dropped), the
explanations and verbatim obligation lists are left out, and the GIF says so on
screen. The command shown is the `assess` call that runs the same frozen pipeline.

    uv run --with pillow python scripts/make_demo_gif.py [--font PATH --bold-font PATH]

Defaults to Consolas (Windows). Any monospaced TTF works.
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RECORDING = Path("data/demo/recording.json")
OUT = Path("docs/assets/demo.gif")
SCENES = ["X01", "X02"]

COLS, ROWS = 96, 30
FONT_SIZE, LINE_H, PAD, BAR_H = 15, 20, 18, 30
BG, BAR = (24, 26, 33), (38, 41, 51)
FG, DIM, WHITE = (205, 210, 220), (120, 127, 142), (240, 242, 246)
GREEN, AMBER, BLUE, PROMPT = (126, 201, 140), (236, 178, 92), (120, 170, 240), (150, 200, 255)

TYPE_MS, LINE_MS, CHARS_PER_FRAME = 35, 110, 5


def verdict_colour(verdict: str) -> tuple[int, int, int]:
    v = verdict.lower()
    if "could not be determined" in v:
        return BLUE
    if v.startswith(("no ", "not ", "does not", "you are not")):
        return GREEN
    return AMBER


def short_sources(line: str) -> str:
    names = re.findall(r"\s*([^;]+?) \(aiact:[^)]*\)", line.removeprefix("Sources:"))
    return "Sources: " + "; ".join(names)


def fit(text: str, width: int = COLS) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def scene_lines(ex: dict) -> list[list[tuple[str, tuple]]]:
    """Output lines of one scene; each line is a list of (text, colour) runs."""
    out: list[list[tuple[str, tuple]]] = []
    lines = ex["shipped_answer"].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## What we would need to know"):
            out.append([(line, WHITE)])
            items = [l for l in lines[i + 1:] if re.match(r"^\d+\. ", l)]
            for it in items[:3]:
                for j, w in enumerate(textwrap.wrap(it, COLS - 3)):
                    out.append([(("   " if j else "") + w, FG)])
            if len(items) > 3:
                out.append([(f"   … {len(items) - 3} more questions", DIM)])
            break
        if line.startswith("## "):
            head, _, verdict = line[3:].partition(": ")
            out.append([("## " + head + ": ", WHITE), (fit(verdict, COLS - len(head) - 5),
                                                        verdict_colour(verdict))])
        elif line.startswith("Sources:"):
            out.append([(fit(short_sources(line)), DIM)])
        i += 1
    out.append([("", FG)])
    out.append([("[trimmed: explanations and quoted obligations; full answer in docs/demo.md]",
                 DIM)])
    out.append([("---", DIM)])
    out.append([(f"Assessed for {ex['relevant_date']} with the frozen pipeline. Cost of this "
                 f"run: ${ex['spend_usd']:.4f} ({len(ex['calls'])} API calls).", FG)])
    return out


class Screen:
    def __init__(self, font: ImageFont.FreeTypeFont, bold: ImageFont.FreeTypeFont):
        self.font, self.bold = font, bold
        self.cw = font.getlength("M")
        self.w = int(PAD * 2 + COLS * self.cw)
        self.h = BAR_H + PAD * 2 + ROWS * LINE_H
        self.buf: list[list[tuple[str, tuple]]] = []
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []

    def snap(self, ms: int, cursor: bool = False) -> None:
        im = Image.new("RGB", (self.w, self.h), BG)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, self.w, BAR_H], fill=BAR)
        for k, c in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
            d.ellipse([14 + k * 20, 10, 26 + k * 20, 22], fill=c)
        title = "eu-ai-check — replay of a recorded run (no API call)"
        d.text(((self.w - self.font.getlength(title)) / 2, 7), title, font=self.font, fill=DIM)
        view = self.buf[-ROWS:]
        for r, runs in enumerate(view):
            x, y = PAD, BAR_H + PAD + r * LINE_H
            for text, colour in runs:
                f = self.bold if colour == WHITE else self.font
                d.text((x, y), text, font=f, fill=colour)
                x += len(text) * self.cw
            if cursor and r == len(view) - 1:
                d.rectangle([x + 1, y + 2, x + self.cw - 1, y + LINE_H - 3], fill=FG)
        self.frames.append(im)
        self.durations.append(ms)

    def type_command(self, cmd: str) -> None:
        wrapped = textwrap.wrap(cmd, COLS - 2, break_on_hyphens=False)
        self.buf.append([("$ ", PROMPT)])
        for n, part in enumerate(wrapped):
            if n:
                self.buf.append([("  ", PROMPT)])
            base = self.buf[-1][0]
            for k in range(CHARS_PER_FRAME, len(part) + CHARS_PER_FRAME, CHARS_PER_FRAME):
                self.buf[-1] = [base, (part[:k], FG)]
                self.snap(TYPE_MS, cursor=True)
        self.snap(600, cursor=True)

    def print_lines(self, lines) -> None:
        for runs in lines:
            self.buf.append(runs)
            self.snap(LINE_MS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", default="C:/Windows/Fonts/consola.ttf")
    ap.add_argument("--bold-font", default="C:/Windows/Fonts/consolab.ttf")
    args = ap.parse_args()
    rec = json.loads(RECORDING.read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in rec["examples"]}
    sc = Screen(ImageFont.truetype(args.font, FONT_SIZE),
                ImageFont.truetype(args.bold_font, FONT_SIZE))
    for n, ex_id in enumerate(SCENES):
        ex = by_id[ex_id]
        sc.buf = [[(f"# Recorded demo example {ex_id} (2026-11-04), replayed. "
                    "Output trimmed, wording unedited.", DIM)], [("", FG)]]
        sc.snap(500, cursor=False)
        sc.type_command(f'eu-ai-check assess "{ex["input"]}"')
        sc.buf.append([("", FG)])
        sc.print_lines(scene_lines(ex))
        sc.durations[-1] = 6500 if n == len(SCENES) - 1 else 5000
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sc.frames[0].save(OUT, save_all=True, append_images=sc.frames[1:], duration=sc.durations,
                      loop=0, optimize=True)
    print(f"wrote {OUT}: {len(sc.frames)} frames, {sum(sc.durations) / 1000:.1f}s, "
          f"{OUT.stat().st_size / 1024:.0f} KiB, {sc.w}x{sc.h}")


if __name__ == "__main__":
    main()
