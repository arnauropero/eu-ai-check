"""Okapi BM25 over sub-chunks, with a legal-citation-aware tokenizer.

Implemented here rather than taken from a library for one reason: the temporal
filter is a *pre-retrieval* hard constraint (SYSTEM_SPEC.md §13.2). The index is
built over exactly the date-eligible chunks, so document frequencies, average
length and scores all describe the corpus as it stood on `relevant_date` -- an
ineligible version cannot even influence IDF, let alone a ranking.

**Tokenizer.** Plain word tokens, plus compound citation tokens, so "Article 6(2)"
in a query can match "Article 6(2)" in the text as a unit rather than as the
unordered bag {article, 6, 2} -- which every Article 6, 16 and 26 provision would
partly satisfy:

    "Article 26(7)"        -> art_26, art_26_7
    "Article 5(1)(h)"      -> art_5, art_5_1, art_5_1_h
    "Annex III point 4(a)" -> annex_iii, annex_iii_4, annex_iii_4_a

This is where BM25 is expected to beat a dense embedding (PROJECT_PLAN.md §6,
Phase 1 acceptance): a vector model encodes "26(7)" as a vague numeric texture.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from ..corpus.normalize import normalize_text

_WORD = re.compile(r"[a-z0-9]+")
_ARTICLE = re.compile(r"\barticles?\s+(\d+)((?:\s*\(\w{1,4}\))*)")
_ANNEX = re.compile(r"\bannex\s+([ivx]+)(?:\s*(?:point|area|section)?\s*(\d+))?((?:\s*\(\w{1,4}\))*)")
_PAREN = re.compile(r"\((\w{1,4})\)")

_STOPWORDS = frozenset(
    "a an and are as at be been by for from has have in into is it its of on or "
    "such that the their them they this those to was were where which who will "
    "with shall any all other".split()
)


def _compound(prefix: str, parts: list[str]) -> list[str]:
    tokens = []
    for i in range(1, len(parts) + 1):
        tokens.append(prefix + "_" + "_".join(parts[:i]))
    return tokens


def tokenize(text: str) -> list[str]:
    text = normalize_text(text).lower()
    tokens = [t for t in _WORD.findall(text) if t not in _STOPWORDS]
    for m in _ARTICLE.finditer(text):
        tokens += _compound("art", [m.group(1), *_PAREN.findall(m.group(2))])
    for m in _ANNEX.finditer(text):
        parts = [m.group(1)]
        if m.group(2):
            parts.append(m.group(2))
            parts += _PAREN.findall(m.group(3))
        tokens += _compound("annex", parts)
    return tokens


class BM25Index:
    """Okapi BM25 (k1=1.5, b=0.75) over a fixed document list."""

    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs = [Counter(tokenize(d)) for d in documents]
        self._lengths = [sum(d.values()) for d in self._docs]
        self._avg_len = (sum(self._lengths) / len(self._lengths)) if self._docs else 0.0
        df: Counter[str] = Counter()
        for d in self._docs:
            df.update(d.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for doc, length in zip(self._docs, self._lengths, strict=True):
            norm = self.k1 * (1 - self.b + self.b * length / self._avg_len)
            s = 0.0
            for t in terms:
                f = doc.get(t)
                if f:
                    s += self._idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out

    def top(self, query: str, n: int) -> list[tuple[int, float]]:
        """(document index, score) for the top `n` documents with a non-zero
        score. A zero score means no query term matched at all, so the document
        is not a candidate -- padding the list with it would only feed noise into
        rank fusion."""
        ranked = sorted(enumerate(self.scores(query)), key=lambda x: -x[1])
        return [(i, s) for i, s in ranked[:n] if s > 0]
