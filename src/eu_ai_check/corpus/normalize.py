"""Text normalization and the `content_id` contract (SYSTEM_SPEC.md §10).

Ingestion invariant: `content_id = SHA-256(normalized_canonical_text)`.

Normalization is deliberately **conservative**. Legally meaningful formatting,
numbering and punctuation are never stripped purely to raise the deduplication
rate -- a higher dedup rate bought by discarding legal signal is a bad trade. The
source text stays authoritative; normalization exists only to stop encoding and
whitespace accidents from forking the identity of byte-equivalent text.

What is normalized, and why each is safe:

  * Unicode NFC            -- composed/decomposed forms of the same character are
                              the same character; the distinction is invisible and
                              carries no legal meaning.
  * NBSP (U+00A0) -> space -- the OJ XHTML uses NBSP as a typographic space inside
                              "Article 5" and "a person". It is a
                              rendering choice, not legal content.
  * Other Unicode spaces   -- same reasoning (thin space, narrow NBSP, etc).
  * Soft hyphen (U+00AD)   -- an invisible line-break hint.
  * Collapse runs of
    whitespace to one
    space; strip ends      -- the source wraps and indents text for layout only.

What is explicitly NOT normalized:

  * Quotation marks and apostrophes stay curly. The Act's own quoting of defined
    terms ("real-time" remote biometric identification) is meaningful.
  * Case is preserved.
  * Numbering, enumerators and punctuation are preserved verbatim.
  * Dashes are preserved; an en dash in a date range is not a hyphen.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Unicode space characters that are typographic variants of a plain space.
# Written as code points rather than literal glyphs so the table stays readable
# and cannot be corrupted by an editor silently folding them into ASCII spaces.
_UNICODE_SPACES = (
    0x00A0,  # no-break space -- pervasive in the OJ XHTML, incl. inside "Article 5"
    0x1680,  # ogham space mark
    *range(0x2000, 0x200B),  # quads, en/em space, thin, hair, zero-width space
    0x202F,  # narrow no-break space
    0x205F,  # medium mathematical space
    0x3000,  # ideographic space
)
_SPACE_TRANSLATION: dict[int, str | None] = {cp: " " for cp in _UNICODE_SPACES}
_SPACE_TRANSLATION[0x00AD] = None  # soft hyphen: invisible line-break hint
_SPACE_TRANSLATION[0xFEFF] = None  # zero-width no-break space / BOM

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Return the canonical form of `text` used for hashing and embedding.

    Conservative by design -- see module docstring for what is deliberately left
    untouched.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_SPACE_TRANSLATION)
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def content_id(text: str) -> str:
    """SHA-256 of the normalized canonical text (§10 ingestion invariant).

    Two provision versions with byte-identical canonical text share a
    `content_id` and therefore a single embedding, while keeping distinct
    `versioned_provision_id`s. That is the whole point of separating the two:
    index-time dedup and legal version identity are different concerns.
    """
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
