"""Technical retrieval units: sub-chunks of canonical provisions (SYSTEM_SPEC.md §7).

The canonical provision is the legal and citation unit; the sub-chunk is only a
retrieval unit. Every sub-chunk carries its parent's `versioned_provision_id`, so
parent resolution (§7.2) is a lookup, never a reconstruction.

**When a provision is split.** A provision whose text fits in `max_chars` is one
chunk. A longer one is split at legal boundaries -- sentence ends and enumerator
starts such as "(ii)" -- and the pieces are packed greedily up to `max_chars`. The
budget defaults to 800 characters, roughly 200 tokens. Two things fix that number:

  * bge-base truncates at 512 tokens. Article 26(10), the longest provision at
    2,726 characters, runs past that, so embedding it whole silently drops its
    closing sentences -- including the annual-reporting duty on deployers.
  * Below the hard limit, a shorter chunk gives a sharper embedding. Article
    5(1)(h) is 1,191 characters: its prohibition and its three exceptions would
    otherwise be averaged into one vector. Split, a query about searching for
    missing persons matches exception (i) directly -- and parent resolution
    still hands back the whole of 5(1)(h), prohibition included.

**Contextual header.** Each chunk is indexed as `"<citation> - <heading>\\n<body>"`.
The citation is the provision's own structural identity, so both the vector and
BM25 sides can match "Article 26(7)" or "Annex III point 4(a)" even when the body
text never names itself. Both retrievers see the same header, which keeps the
vector-only vs hybrid comparison fair.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from ..corpus.models import Provision, SegmentType
from ..corpus.normalize import normalize_text

DEFAULT_MAX_CHARS = 800

# A boundary is whitespace after sentence-final or list punctuation, followed by
# either a capitalised word or an enumerator like "(ii)" / "(b)". Splitting only
# here keeps every piece a whole sentence or a whole enumerated item.
_BOUNDARY = re.compile(r"(?<=[.;:])\s+(?=[A-Z‘(]|\([a-z]{1,4}\)\s)")


@dataclass(frozen=True)
class SubChunk:
    """One retrieval unit. Never passed to generation on its own (§7.2)."""

    chunk_id: str
    versioned_provision_id: str
    logical_provision_id: str
    ordinal: int
    body: str
    index_text: str
    text_valid_from: date
    text_valid_to: date | None

    @property
    def content_hash(self) -> str:
        """Embedding dedup key: identical indexed text is embedded once (§15)."""
        return hashlib.sha256(self.index_text.encode("utf-8")).hexdigest()

    def valid_on(self, on: date) -> bool:
        if on < self.text_valid_from:
            return False
        return self.text_valid_to is None or on < self.text_valid_to


def short_citation(provision: Provision) -> str:
    """'Article 26(10)' / 'Annex III point 4(a)' -- the structural path without
    its chapter/section context, which adds tokens but no discrimination."""
    article = annex = None
    markers: list[str] = []
    for seg in provision.structural_location:
        if seg.type == SegmentType.ARTICLE:
            article = seg.value
        elif seg.type == SegmentType.ANNEX:
            annex = seg.value
        elif seg.type in (SegmentType.CHAPTER, SegmentType.SECTION):
            continue
        else:
            markers.append(seg.value)
    if annex is not None:
        if not markers:
            return f"Annex {annex}"
        return f"Annex {annex} point {markers[0]}" + "".join(f"({m})" for m in markers[1:])
    return f"Article {article}" + "".join(f"({m})" for m in markers)


def split_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Split at legal boundaries, packing pieces up to `max_chars`.

    A single sentence longer than the budget is kept whole rather than cut
    mid-sentence: an over-long chunk loses some embedding sharpness, a sentence
    cut in half loses its meaning.
    """
    if len(text) <= max_chars:
        return [text]
    pieces = _BOUNDARY.split(text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}" if current else piece
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_provision(provision: Provision, max_chars: int = DEFAULT_MAX_CHARS) -> list[SubChunk]:
    header = short_citation(provision)
    if provision.heading:
        header = f"{header} - {normalize_text(provision.heading)}"
    return [
        SubChunk(
            chunk_id=f"{provision.versioned_provision_id}#c{i}",
            versioned_provision_id=provision.versioned_provision_id,
            logical_provision_id=provision.logical_provision_id,
            ordinal=i,
            body=body,
            index_text=f"{header}\n{body}",
            text_valid_from=provision.text_valid_from,
            text_valid_to=provision.text_valid_to,
        )
        for i, body in enumerate(split_text(provision.text, max_chars))
    ]


def chunk_corpus(
    provisions: list[Provision], max_chars: int = DEFAULT_MAX_CHARS
) -> list[SubChunk]:
    return [c for p in provisions for c in chunk_provision(p, max_chars)]
