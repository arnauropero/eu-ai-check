"""Every file the generation code writes or reads is explicitly UTF-8 (a smoke-test
answer showed "16(a)–(l)" mangled by the Windows cp1252 console)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from eu_ai_check.generation import evaluation as ev
from eu_ai_check.generation.spend import SpendTracker

PACKAGE = Path("src/eu_ai_check/generation")
FILE_CALLS = {"open", "write_text", "read_text"}


def test_every_text_file_call_names_utf8() -> None:
    missing = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if name not in FILE_CALLS:
                continue
            enc = [k for k in node.keywords if k.arg == "encoding"]
            if not enc or getattr(enc[0].value, "value", None) != "utf-8":
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, missing


def test_en_dash_survives_the_runs_file_and_ledger(tmp_path) -> None:
    text = "Article 16(a)–(l)"
    runs = tmp_path / "runs.jsonl"
    ev._write_jsonl(runs, [{"shipped_text": text}])
    raw = runs.read_bytes()
    assert "–".encode("utf-8") in raw and b"\x96" not in raw
    assert json.loads(raw.decode("utf-8"))["shipped_text"] == text
    t = SpendTracker(ledger=tmp_path / "ledger.jsonl")
    t.record("dev", "X#0", "m", 1, 1, error=text)
    assert SpendTracker(ledger=tmp_path / "ledger.jsonl").entries[0]["error"] == text
