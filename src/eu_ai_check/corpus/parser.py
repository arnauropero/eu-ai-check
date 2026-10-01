"""Parse the Official Journal XHTML of Regulation (EU) 2024/1689 into a
structure-preserving tree.

This module knows about *markup*, not about law. It produces a generic
`RawNode` tree that mirrors the source's own hierarchy; mapping that tree onto
`Provision` records (and deciding where the canonical citable unit sits) is
`ingest.py`'s job. Keeping the two apart means the legal chunking rule can be
revised without touching XHTML handling.

Observed source structure (verified against the real document):

    <div class="eli-subdivision" id="art_5">
      <p class="oj-ti-art">Article 5</p>          <- article number
      <p class="oj-sti-art">Prohibited AI practices</p>  <- heading
      <div id="005.001">                          <- numbered paragraph
        <p class="oj-normal">1.   The following ...</p>
        <table>...<td>(a)</td><td><p>text</p>     <- lettered point
                                  <table>...</table>  <- nested sub-point
        </td>...</table>
      </div>
    </div>

Two structural facts drive the design:

* **Depth varies.** Article 16 has lettered points directly under the article
  with no paragraph layer; Article 26 has paragraphs and no points; Article 5
  reaches four levels. The parser therefore recurses rather than reading a fixed
  set of levels.

* **Marker text is ambiguous; nesting is not.** Article 16 has a lettered point
  "(i)" while Article 5(1)(h) has a roman sub-point "(i)". These are different
  levels with identical marker text, so the level of a node is decided by *where
  it sits in the tree*, never by parsing its marker string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .models import SegmentType
from .normalize import normalize_text

_XHTML_NS = "{http://www.w3.org/1999/xhtml}"

# "CHAPTER III" / "SECTION 3" in heading paragraphs. NBSP-tolerant.
_CHAPTER_RE = re.compile(r"^CHAPTER\s+([IVXLC]+)$")
_SECTION_RE = re.compile(r"^SECTION\s+(\d+)$")
_ARTICLE_RE = re.compile(r"^Article\s+(\d+)$")
_ANNEX_RE = re.compile(r"^ANNEX\s+([IVXLC]+)$")

# Enumerator markers as they appear in the first cell of an enumerated row.
_MARKER_RE = re.compile(r"^\(?([0-9]+|[a-z]+|[ivxlc]+)\)?\.?$")


@dataclass(eq=False)  # identity semantics: nodes are located in the tree by `is`
class RawNode:
    """One node of the source hierarchy.

    `content` is an **ordered** sequence interleaving this node's own text
    fragments with its enumerated children, in source order. The ordering is not
    cosmetic: Article 5(1) ends with a subparagraph that comes *after* its
    (a)-(h) list and qualifies point (h) ("Point (h) of the first subparagraph is
    without prejudice to Article 9 of Regulation (EU) 2016/679"). Storing text
    and children in separate buckets would re-emit that clause before the list it
    qualifies, silently corrupting the legal text.
    """

    level: SegmentType
    marker: str
    content: list[str | RawNode] = field(default_factory=list)
    heading: str | None = None

    @property
    def children(self) -> list[RawNode]:
        return [c for c in self.content if isinstance(c, RawNode)]

    @property
    def own_text(self) -> list[str]:
        return [c for c in self.content if isinstance(c, str)]

    def full_text(self) -> str:
        """This node's complete text including all descendants, in source order,
        with enumerators re-inserted so the canonical text reads as the source
        does.

        Article 5(1)(h) depends on this: its three exceptions must stay inside
        the one canonical provision, or an exception could be retrieved without
        the prohibition it excepts from (SYSTEM_SPEC.md §7.1).
        """
        parts: list[str] = []
        for item in self.content:
            if isinstance(item, str):
                parts.append(item)
            else:
                parts.append(f"({item.marker}) {item.full_text()}")
        return " ".join(p for p in parts if p).strip()

    def iter_descendants(self):
        for child in self.children:
            yield child
            yield from child.iter_descendants()


@dataclass
class ArticleNode:
    """A top-level article or annex, with its position in the Act's hierarchy."""

    number: str
    heading: str | None
    chapter: str | None
    section: str | None
    is_annex: bool
    root: RawNode


def _text_of(el: etree._Element) -> str:
    """Flattened text of an element, markup removed, whitespace left for the
    normalizer to handle."""
    return "".join(el.itertext())


def _tag(el: etree._Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and tag.startswith(_XHTML_NS):
        return tag[len(_XHTML_NS):]
    return tag if isinstance(tag, str) else ""


def _direct_rows(table: etree._Element):
    """Rows belonging to `table` itself, not to any table nested inside it."""
    for child in table:
        tag = _tag(child)
        if tag == "tr":
            yield child
        elif tag in ("tbody", "thead", "tfoot"):
            for tr in child:
                if _tag(tr) == "tr":
                    yield tr


def _enumerated_rows(table: etree._Element) -> list[tuple[str, etree._Element]]:
    """Return (marker, content-cell) pairs for an enumerated-list table.

    The OJ renders every enumerated item as a two-column table: a narrow cell
    holding the marker, and a wide cell holding the content (which may itself
    contain further tables).
    """
    rows: list[tuple[str, etree._Element]] = []
    # Only this table's own rows. Using .iter() here would also pick up the rows
    # of nested tables and flatten the hierarchy -- which is exactly the failure
    # that would break Article 5(1)(h) apart from its sub-points.
    for tr in _direct_rows(table):
        cells = [td for td in tr if _tag(td) == "td"]
        if len(cells) != 2:
            continue
        marker_text = normalize_text(_text_of(cells[0]))
        match = _MARKER_RE.match(marker_text)
        if not match:
            continue
        rows.append((match.group(1), cells[1]))
    return rows


def _child_level(parent_level: SegmentType) -> SegmentType:
    """The level of an enumerated child, decided structurally.

    This is the ambiguity guard: "(i)" is a lettered point under an article but a
    roman sub-point under a point, and only position in the tree tells them
    apart.
    """
    match parent_level:
        case SegmentType.ARTICLE:
            return SegmentType.POINT
        case SegmentType.PARAGRAPH:
            return SegmentType.POINT
        case SegmentType.POINT:
            return SegmentType.SUB_POINT
        case SegmentType.SUB_POINT:
            return SegmentType.SUB_POINT
        case SegmentType.ANNEX:
            return SegmentType.ANNEX_AREA
        case SegmentType.ANNEX_AREA:
            return SegmentType.ANNEX_ITEM
        case _:
            return SegmentType.ANNEX_ITEM


def _parse_container(container: etree._Element, node: RawNode, *, skip: set) -> None:
    """Populate `node` from `container`'s direct children.

    Text paragraphs become the node's own text; tables become enumerated
    children, recursed into. Nested tables inside a content cell are handled by
    the recursion, which is what preserves Article 5(1)(h) intact.
    """
    for el in container:
        tag = _tag(el)
        if el in skip:
            continue
        if tag == "p":
            text = normalize_text(_text_of(el))
            if text:
                node.content.append(text)
        elif tag == "table":
            for marker, cell in _enumerated_rows(el):
                child = RawNode(level=_child_level(node.level), marker=marker)
                _parse_container(cell, child, skip=skip)
                node.content.append(child)
        elif tag == "div":
            # A numbered paragraph container (id like "005.001"), or a wrapper.
            div_id = el.get("id") or ""
            if re.fullmatch(r"\d{3}\.\d{3}", div_id):
                child = RawNode(
                    level=SegmentType.PARAGRAPH,
                    marker=str(int(div_id.split(".")[1])),
                )
                _parse_container(el, child, skip=skip)
                _strip_leading_enumerator(child)
                node.content.append(child)
            else:
                _parse_container(el, node, skip=skip)


_LEADING_ENUM_RE = re.compile(r"^\d+\.\s*")


def _strip_leading_enumerator(node: RawNode) -> None:
    """Numbered paragraphs repeat their number inside the text ("1.   The
    following..."). The number is captured as the marker, so drop the inline
    copy to avoid citing it twice."""
    for i, item in enumerate(node.content):
        if isinstance(item, str):
            node.content[i] = _LEADING_ENUM_RE.sub("", item, count=1)
            return


def parse_document(path: Path) -> list[ArticleNode]:
    """Parse the whole OJ XHTML into article and annex nodes, in document order,
    tracking the enclosing chapter and section as it goes."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()

    articles: list[ArticleNode] = []
    chapter: str | None = None
    section: str | None = None

    for el in root.iter():
        tag = _tag(el)
        if tag == "p":
            text = normalize_text(_text_of(el))
            if m := _CHAPTER_RE.match(text):
                chapter, section = m.group(1), None
                continue
            if m := _SECTION_RE.match(text):
                section = m.group(1)
                continue
        if tag != "div":
            continue
        el_id = el.get("id") or ""
        if re.fullmatch(r"art_\d+", el_id):
            articles.append(_build_article(el, chapter, section))
        elif re.fullmatch(r"anx_[IVXLC]+", el_id):
            articles.append(_build_annex(el, el_id.removeprefix("anx_")))

    return articles


def _headings(container: etree._Element) -> tuple[str | None, str | None, set]:
    """Extract (number-text, heading-text, elements-to-skip) from an article or
    annex container, so title markup is not mistaken for provision text."""
    number_text: str | None = None
    heading_text: str | None = None
    skip: set = set()
    for el in container.iter():
        if _tag(el) != "p":
            continue
        cls = el.get("class") or ""
        if cls in ("oj-ti-art", "oj-doc-ti") and number_text is None:
            number_text = normalize_text(_text_of(el))
            skip.add(el)
            _skip_ancestors(el, container, skip)
        elif cls in ("oj-sti-art", "oj-doc-ti") and heading_text is None:
            heading_text = _text_of(el).strip()
            skip.add(el)
            _skip_ancestors(el, container, skip)
    return number_text, heading_text, skip


def _skip_ancestors(el: etree._Element, stop: etree._Element, skip: set) -> None:
    """Mark the title-wrapper div (e.g. <div class="eli-title">) as skippable so
    the recursive walk does not pick the heading up as body text."""
    parent = el.getparent()
    while parent is not None and parent is not stop:
        if (parent.get("class") or "") == "eli-title":
            skip.add(parent)
        parent = parent.getparent()


def _build_article(container: etree._Element, chapter: str | None,
                   section: str | None) -> ArticleNode:
    number_text, heading_text, skip = _headings(container)
    number = m.group(1) if (m := _ARTICLE_RE.match(number_text or "")) else "?"
    root = RawNode(level=SegmentType.ARTICLE, marker=number, heading=heading_text)
    _parse_container(container, root, skip=skip)
    return ArticleNode(
        number=number,
        heading=heading_text,
        chapter=chapter,
        section=section,
        is_annex=False,
        root=root,
    )


def _build_annex(container: etree._Element, numeral: str) -> ArticleNode:
    number_text, heading_text, skip = _headings(container)
    if number_text and _ANNEX_RE.match(number_text):
        pass
    root = RawNode(level=SegmentType.ANNEX, marker=numeral, heading=heading_text)
    _parse_container(container, root, skip=skip)
    return ArticleNode(
        number=numeral,
        heading=heading_text,
        chapter=None,
        section=None,
        is_annex=True,
        root=root,
    )
