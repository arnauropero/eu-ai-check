"""Phase 0 acceptance tests (PROJECT_PLAN.md §6, Phase 0).

These assert the acceptance criteria as written, against the real Official
Journal text rather than a fixture, because the point of Phase 0 is that the
corpus assumptions survive contact with the actual document. Where the spec names
a specific verified case (Article 5(1)(h), Annex III Area 2, Article 16's depth),
there is a test named after it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from eu_ai_check.corpus.ingest import (
    ENTRY_INTO_FORCE,
    V1_ANNEXES,
    V1_ARTICLES,
    V1_PARTIAL_ARTICLES,
    ingest_corpus,
)
from eu_ai_check.corpus.models import (
    ApplicabilityRule,
    Corpus,
    Provision,
    ProvisionType,
    SegmentType,
    StructuralSegment,
)
from eu_ai_check.corpus.normalize import content_id, normalize_text
from eu_ai_check.corpus.parser import parse_document
from eu_ai_check.corpus.rules import (
    ARTICLE_6_1_APPLICATION,
    CHAPTER_I_II_APPLICATION,
    GENERAL_APPLICATION,
    build_rules,
)
from eu_ai_check.corpus.validation import Severity, validate_corpus

SOURCE = Path(__file__).resolve().parents[1] / "data/raw/ai_act_32024R1689.xhtml"


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    provisions, issues = ingest_corpus(parse_document(SOURCE))
    assert issues == [], f"ingestion reported issues: {issues}"
    return Corpus(
        source_reference="test",
        provisions=provisions,
        applicability_rules=build_rules(provisions),
    )


def find(corpus: Corpus, logical_id: str) -> Provision:
    matches = [p for p in corpus.provisions if p.logical_provision_id == logical_id]
    assert len(matches) == 1, f"expected exactly one {logical_id}, got {len(matches)}"
    return matches[0]


# --------------------------------------------------------------------------
# Acceptance: every provision citable at its correct granularity
# --------------------------------------------------------------------------

def test_v1_corpus_scope_is_fully_ingested(corpus: Corpus) -> None:
    articles = {
        seg.value
        for p in corpus.provisions
        for seg in p.structural_location
        if seg.type == SegmentType.ARTICLE
    }
    annexes = {
        seg.value
        for p in corpus.provisions
        for seg in p.structural_location
        if seg.type == SegmentType.ANNEX
    }
    assert articles == {"3", "5", "6", "16", "25", "26"}
    assert annexes == {"I", "III"}


def test_citation_renders_at_correct_granularity(corpus: Corpus) -> None:
    assert find(corpus, "aiact:art_5:para_1:point_h").citation() == (
        "Chapter II -> Article 5(1)(h)"
    )
    assert find(corpus, "aiact:art_16:point_a").citation() == (
        "Chapter III -> Section 3 -> Article 16(a)"
    )
    assert find(corpus, "aiact:anx_III:area_2").citation() == "Annex III(2)"


def test_structural_depth_is_variable_not_fixed(corpus: Corpus) -> None:
    """SYSTEM_SPEC.md §6: the fixed-depth assumption was falsified by the corpus.

    Measured below the article/annex level, so chapter and section context does
    not mask the difference.
    """

    def legal_depth(p: Provision) -> int:
        below = [
            s for s in p.structural_location
            if s.type not in (SegmentType.CHAPTER, SegmentType.SECTION,
                              SegmentType.ARTICLE, SegmentType.ANNEX)
        ]
        return len(below)

    # Article 16: Article -> Point, no paragraph layer.
    assert legal_depth(find(corpus, "aiact:art_16:point_a")) == 1
    # Article 26: Article -> Paragraph, no lettered points.
    assert legal_depth(find(corpus, "aiact:art_26:para_2")) == 1
    # Article 5: Article -> Paragraph -> Point.
    assert legal_depth(find(corpus, "aiact:art_5:para_1:point_h")) == 2

    depths = {legal_depth(p) for p in corpus.provisions}
    assert len(depths) > 1, "a single depth would mean fixed fields were adequate"


def test_article_16_has_no_paragraph_layer(corpus: Corpus) -> None:
    points = [
        p for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:art_16:")
    ]
    assert len(points) == 12, "Article 16 has lettered points (a)-(l)"
    assert not any(
        s.type == SegmentType.PARAGRAPH
        for p in points for s in p.structural_location
    )


def test_article_16_point_i_is_a_letter_not_roman_one(corpus: Corpus) -> None:
    """The marker "(i)" is ambiguous between a lettered point and a roman
    sub-point; level must come from tree position, not marker text."""
    point_i = find(corpus, "aiact:art_16:point_i")
    assert point_i.structural_location[-1].type == SegmentType.POINT
    # It must sort as the 9th lettered point, i.e. between (h) and (j).
    ids = sorted(
        p.logical_provision_id for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:art_16:point_")
    )
    assert ids.index("aiact:art_16:point_i") == 8


# --------------------------------------------------------------------------
# Acceptance: Annex III Area 2 terminal case handled correctly
# --------------------------------------------------------------------------

def test_annex_iii_area_2_is_terminal(corpus: Corpus) -> None:
    """SYSTEM_SPEC.md §9: Area 2 has no lettered sub-items, so the area itself is
    the atomic provision -- not a synthetic wrapper chunk."""
    area_2 = find(corpus, "aiact:anx_III:area_2")
    assert area_2.structural_location[-1].type == SegmentType.ANNEX_AREA
    assert "Critical infrastructure" in area_2.text
    assert not [
        p for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:anx_III:area_2:")
    ], "Area 2 must not have been given sub-items it does not have"


def test_annex_iii_area_structure_matches_the_verified_corpus(corpus: Corpus) -> None:
    """The per-area item counts recorded in SYSTEM_SPEC.md §9, re-derived from the
    source so a drifting assumption fails here rather than at retrieval time."""
    expected = {"1": 3, "2": 0, "3": 4, "4": 2, "5": 4, "6": 5, "7": 4, "8": 2}
    for area, count in expected.items():
        items = [
            p for p in corpus.provisions
            if p.logical_provision_id.startswith(f"aiact:anx_III:area_{area}:item_")
        ]
        assert len(items) == count, f"Annex III area {area}"
    # Only the terminal area is itself a provision.
    areas_as_provisions = [
        p for p in corpus.provisions
        if p.structural_location[-1].type == SegmentType.ANNEX_AREA
    ]
    assert [p.logical_provision_id for p in areas_as_provisions] == [
        "aiact:anx_III:area_2"
    ]


# --------------------------------------------------------------------------
# Acceptance: Article 5(1)(h) nested structure preserved as one provision
# --------------------------------------------------------------------------

def test_article_5_1_h_is_one_canonical_provision(corpus: Corpus) -> None:
    """SYSTEM_SPEC.md §7.1: the exceptions must not be separately citable, or the
    system could state an exception without the prohibition it excepts from."""
    h = find(corpus, "aiact:art_5:para_1:point_h")

    # All three exceptions live inside the one provision...
    for fragment in (
        "targeted search for specific victims",
        "imminent threat to the life",
        "localisation or identification of a person suspected",
    ):
        assert fragment in h.text, fragment

    # ...and are not provisions in their own right.
    assert not [
        p for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:art_5:para_1:point_h:")
    ]


def test_article_5_1_h_keeps_its_prohibition_with_its_exceptions(corpus: Corpus) -> None:
    h = find(corpus, "aiact:art_5:para_1:point_h")
    prohibition = h.text.index("real-time")
    first_exception = h.text.index("targeted search for specific victims")
    assert prohibition < first_exception, (
        "the prohibition must precede its exceptions in the canonical text"
    )


def test_canonical_text_carries_the_operative_stem(corpus: Corpus) -> None:
    """A lettered point read alone is a bare noun phrase; the operative verb lives
    in the parent's lead-in. §5 requires the canonical unit to be interpretable
    without it being lost."""
    a = find(corpus, "aiact:art_5:para_1:point_a")
    assert a.text.startswith("The following AI practices shall be prohibited:")

    sixteen_a = find(corpus, "aiact:art_16:point_a")
    assert sixteen_a.text.startswith("Providers of high-risk AI systems shall:")


def test_scoped_savings_clause_attaches_only_to_the_point_it_scopes(
    corpus: Corpus,
) -> None:
    """Article 5(1) closes with "Point (h) ... is without prejudice to Article 9 of
    Regulation (EU) 2016/679". It qualifies (h) alone, so attaching it to every
    point of 5(1) would misstate the law for (a)-(g)."""
    h = find(corpus, "aiact:art_5:para_1:point_h")
    assert "without prejudice to Article 9" in h.text

    for marker in "abcdefg":
        other = find(corpus, f"aiact:art_5:para_1:point_{marker}")
        assert "without prejudice to Article 9" not in other.text, marker


def test_every_v1_source_fragment_is_in_some_canonical_provision(
    corpus: Corpus,
) -> None:
    """No source text may be silently dropped. Phase 0 lost two subparagraphs this
    way (Article 6(3) third, Article 5(2) second) because trailing text that named
    no point was attached nowhere. Compared ignoring punctuation and spacing, so
    the check is about lost words, not normalisation. Points an article
    deliberately leaves out (`V1_PARTIAL_ARTICLES`) are skipped."""
    def key(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    in_corpus = key(" ".join(p.text for p in corpus.provisions))

    def walk(node, kept=None):
        yield node
        for child in node.children:
            if kept is None or child.marker in kept:
                yield from walk(child)

    missing = [
        (article.number, fragment[:80])
        for article in parse_document(SOURCE)
        if article.number in (V1_ANNEXES if article.is_annex else V1_ARTICLES)
        for node in walk(
            article.root,
            None if article.is_annex else V1_PARTIAL_ARTICLES.get(article.number),
        )
        for fragment in node.own_text
        if key(fragment) not in in_corpus
    ]
    assert missing == []


def test_article_3_keeps_only_the_three_actor_role_definitions(corpus: Corpus) -> None:
    """Article 3 is ingested in part, on purpose: provider (3), deployer (4) and
    substantial modification (23) ground the actor-role determination; the other
    65 definitions are left out explicitly, not lost."""
    ids = {
        p.logical_provision_id for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:art_3:")
    }
    assert ids == {"aiact:art_3:point_3", "aiact:art_3:point_4", "aiact:art_3:point_23"}
    provider = find(corpus, "aiact:art_3:point_3")
    assert provider.provision_type == ProvisionType.DEFINITION
    assert provider.citation() == "Chapter I -> Article 3(3)"
    assert provider.text.startswith(
        "For the purposes of this Regulation, the following definitions apply: ‘provider’"
    )
    assert "‘deployer’ means" in find(corpus, "aiact:art_3:point_4").text
    assert "‘substantial modification’ means" in find(corpus, "aiact:art_3:point_23").text


def test_article_6_3_profiling_override_is_its_own_provision(corpus: Corpus) -> None:
    """"Notwithstanding the first subparagraph, an AI system referred to in Annex III
    shall always be considered to be high-risk where the AI system performs
    profiling of natural persons." It names no point, so it is a provision of its
    own, cited as the Act cites it, and carries the subparagraph it overrides."""
    override = find(corpus, "aiact:art_6:para_3:subpara_3")
    assert override.citation().endswith("Article 6(3), third subparagraph")
    assert override.provision_type == ProvisionType.EXCEPTION
    assert override.text.startswith("By derogation from paragraph 2")
    assert override.text.endswith("performs profiling of natural persons.")
    # The list lead-in introduces (a)-(d), which this provision does not carry.
    assert "following conditions is fulfilled" not in override.text
    for marker in "abcd":
        point = find(corpus, f"aiact:art_6:para_3:point_{marker}")
        assert "profiling" not in point.text, marker


def test_article_5_2_second_subparagraph_is_its_own_provision(corpus: Corpus) -> None:
    """The safeguards / impact-assessment / registration rule on real-time remote
    biometric identification is a separate rule, not a condition of 5(2)(a)-(b)."""
    second = find(corpus, "aiact:art_5:para_2:subpara_2")
    assert second.citation().endswith("Article 5(2), second subparagraph")
    assert second.text.startswith("In addition,")
    assert "fundamental rights impact assessment" in second.text
    for marker in "ab":
        assert "In addition," not in find(
            corpus, f"aiact:art_5:para_2:point_{marker}"
        ).text


def test_annex_i_is_ingested_with_its_sections(corpus: Corpus) -> None:
    """Annex I (needed for Article 6(1)): 20 entries numbered continuously across
    Section A (1-12) and Section B (13-20). Each entry carries its own section's
    heading and not the other's."""
    items = [
        p for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:anx_I:")
    ]
    assert len(items) == 20
    assert all(p.provision_type == ProvisionType.SCOPE for p in items)

    medical = find(corpus, "aiact:anx_I:item_11")
    assert medical.citation() == "Annex I, Section A, point 11"
    assert "medical devices" in medical.text
    assert medical.text.startswith("Section A.")

    aviation = find(corpus, "aiact:anx_I:item_13")
    assert aviation.citation() == "Annex I, Section B, point 13"
    assert aviation.text.startswith("Section B.")
    assert "Section A." not in aviation.text


# --------------------------------------------------------------------------
# §10 identity model and normalization
# --------------------------------------------------------------------------

def test_three_identities_are_distinct(corpus: Corpus) -> None:
    p = find(corpus, "aiact:art_26:para_1")
    assert p.logical_provision_id != p.versioned_provision_id != p.content_id
    assert p.versioned_provision_id.startswith(p.logical_provision_id)
    assert p.text_valid_from == ENTRY_INTO_FORCE


def test_content_id_is_the_hash_of_the_stored_text(corpus: Corpus) -> None:
    for p in corpus.provisions:
        assert p.content_id == content_id(p.text), p.logical_provision_id


def test_content_ids_are_unique_for_distinct_text(corpus: Corpus) -> None:
    by_content: dict[str, list[str]] = {}
    for p in corpus.provisions:
        by_content.setdefault(p.content_id, []).append(p.text)
    for texts in by_content.values():
        assert len(set(texts)) == 1, "one content_id must mean one canonical text"


def test_normalization_folds_nbsp_but_keeps_legal_punctuation() -> None:
    # The OJ uses NBSP as a typographic space: "Article 5".
    assert normalize_text("Article 5") == "Article 5"
    assert content_id("a b") == content_id("a b")
    # Curly quotes are legally meaningful -- the Act quotes its own defined terms,
    # and it uses single curly quotes (U+2018/U+2019), not double.
    assert normalize_text("‘real-time’") == "‘real-time’"
    assert content_id("‘real-time’") != content_id("'real-time'")
    # Soft hyphens are invisible and must not fork identity.
    assert content_id("bio­metric") == content_id("biometric")


def test_source_text_is_preserved_verbatim(corpus: Corpus) -> None:
    """Normalization must not have stripped numbering or punctuation (§10)."""
    h = find(corpus, "aiact:art_5:para_1:point_h")
    assert "(i)" in h.text and "(ii)" in h.text and "(iii)" in h.text
    assert "‘real-time’" in h.text, "defined-term quoting preserved"


# --------------------------------------------------------------------------
# §8 cross-references
# --------------------------------------------------------------------------

def test_cross_references_are_edges_not_inlined_text(corpus: Corpus) -> None:
    annex_items = [
        p for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:anx_III:")
    ]
    assert any("aiact:art_6" in p.cross_references for p in annex_items)


def test_external_instrument_references_are_not_treated_as_internal(
    corpus: Corpus,
) -> None:
    """"Article 9 of Regulation (EU) 2016/679" is outside this corpus and must not
    be recorded as an edge to this Regulation's own Article 9."""
    h = find(corpus, "aiact:art_5:para_1:point_h")
    assert "without prejudice to Article 9 of Regulation" in h.text
    assert "aiact:art_9" not in h.cross_references


def test_external_references_cannot_backtrack_into_internal_ones(
    corpus: Corpus,
) -> None:
    """Before the fix, "Article 35 of Regulation (EU) 2016/679" failed the
    exclusion as "Article 35" but matched as "Article 3", and "Article 3(4) of
    Directive (EU) 2016/680" as "Article 3" without its paragraph. Annex I point 20
    cites "Article 2(1), points (a) and (b) thereof" of another Regulation."""
    assert find(corpus, "aiact:art_26:para_9").cross_references == ["aiact:art_13"]
    assert find(corpus, "aiact:art_26:para_10").cross_references == []
    assert "aiact:art_3" not in find(
        corpus, "aiact:anx_III:area_6:item_d"
    ).cross_references
    assert find(corpus, "aiact:anx_I:item_20").cross_references == []


# --------------------------------------------------------------------------
# §12 applicability rules -- the staggered schedule
# --------------------------------------------------------------------------

def test_every_obligation_and_prohibition_has_a_rule_or_a_declared_gap(
    corpus: Corpus,
) -> None:
    """§12.1 as written: an operative provision carries at least one rule, *or* its
    gap is declared. What is forbidden is a silent zero -- that is what becomes a
    false `unresolved` at determination time."""
    for p in corpus.provisions:
        if not p.requires_applicability_rule:
            continue
        has_rule = bool(corpus.rules_for(p.logical_provision_id))
        assert has_rule or p.applicability_gap_reason, p.logical_provision_id


def test_provider_and_deployer_obligations_are_all_scheduled(corpus: Corpus) -> None:
    """The declared-gap escape hatch must not have quietly swallowed an obligation
    that V1 is actually responsible for. Every operative provision of Articles
    5/16/25/26 carries a real rule."""
    for p in corpus.provisions:
        if not p.requires_applicability_rule:
            continue
        article = next(
            s.value for s in p.structural_location if s.type == SegmentType.ARTICLE
        )
        if article in ("5", "16", "25", "26"):
            assert corpus.rules_for(p.logical_provision_id), p.logical_provision_id
            assert not p.applicability_gap_reason, p.logical_provision_id


def test_non_operative_provisions_carry_no_rules(corpus: Corpus) -> None:
    """Annex III categories and Article 5's procedural paragraphs create no
    standalone duty, so scheduling them would invent obligations."""
    for p in corpus.provisions:
        if p.provision_type in (
            ProvisionType.ANNEX_CATEGORY, ProvisionType.EXCEPTION
        ):
            assert not corpus.rules_for(p.logical_provision_id), p.logical_provision_id


def test_article_5_prohibitions_apply_from_february_2025(corpus: Corpus) -> None:
    """Article 113(a): Chapters I and II apply from 2 February 2025."""
    rules = corpus.rules_for("aiact:art_5:para_1:point_a")
    assert len(rules) == 1
    rule = rules[0]
    assert rule.applicable_from == CHAPTER_I_II_APPLICATION == date(2025, 2, 2)
    assert rule.source_reference == "Article 113, point (a)"
    assert not rule.in_force_on(date(2025, 2, 1))
    assert rule.in_force_on(date(2025, 2, 2))


def test_high_risk_obligations_split_by_system_category(corpus: Corpus) -> None:
    """Article 113(c) is why one date per provision is not enough: the same
    Article 16 text starts applying a year apart depending on whether the system
    is high-risk under Article 6(2) (Annex III) or Article 6(1)."""
    rules = {
        r.conditions["system_category"]: r
        for r in corpus.rules_for("aiact:art_16:point_a")
    }
    assert rules["annex_iii_high_risk"].applicable_from == GENERAL_APPLICATION
    assert rules["article_6_1_high_risk"].applicable_from == ARTICLE_6_1_APPLICATION
    assert rules["annex_iii_high_risk"].applicable_from < (
        rules["article_6_1_high_risk"].applicable_from
    )


def test_same_provision_two_dates_two_outcomes(corpus: Corpus) -> None:
    """The temporal case the plan requires an eval for (PROJECT_PLAN.md §8): one
    provision, two dates, two different applicability outcomes."""
    annex3 = next(
        r for r in corpus.rules_for("aiact:art_16:point_a")
        if r.conditions["system_category"] == "annex_iii_high_risk"
    )
    assert not annex3.in_force_on(date(2026, 8, 1))
    assert annex3.in_force_on(date(2026, 8, 2))


def test_rules_carry_actor_role_conditions(corpus: Corpus) -> None:
    for rule in corpus.rules_for("aiact:art_16:point_a"):
        assert rule.conditions["actor_role"] == "provider"
    for rule in corpus.rules_for("aiact:art_26:para_1"):
        assert rule.conditions["actor_role"] == "deployer"


def test_applicability_is_never_inferred_from_text_validity(corpus: Corpus) -> None:
    """§13.1: the two temporal questions are independent. Every rule's start date
    must differ from the provision's text_valid_from -- if any rule had silently
    defaulted to it, this would catch it."""
    for p in corpus.provisions:
        for rule in corpus.rules_for(p.logical_provision_id):
            assert rule.applicable_from != p.text_valid_from, rule.rule_id


# --------------------------------------------------------------------------
# Article 6 -- added to V1 scope on Phase 0 corpus evidence
# --------------------------------------------------------------------------

def test_article_6_is_ingested_with_heterogeneous_types(corpus: Corpus) -> None:
    """Article 6 mixes classification rules, a derogation and duties, so typing it
    uniformly would misdescribe it."""
    types = {
        p.logical_provision_id: p.provision_type
        for p in corpus.provisions
        if ":art_6:" in p.logical_provision_id
    }
    # The two classification routes.
    assert types["aiact:art_6:para_1:point_a"] == ProvisionType.SCOPE
    assert types["aiact:art_6:para_2"] == ProvisionType.SCOPE
    # The derogation that lets an Annex III system be found not high-risk.
    assert types["aiact:art_6:para_3:point_a"] == ProvisionType.EXCEPTION
    # A genuine provider duty.
    assert types["aiact:art_6:para_4"] == ProvisionType.OBLIGATION


def test_article_6_3_derogation_is_ingested(corpus: Corpus) -> None:
    """Article 6(3) is a real determination branch: an Annex III system that does
    not pose a significant risk of harm is not high-risk."""
    points = [
        p for p in corpus.provisions
        if p.logical_provision_id.startswith("aiact:art_6:para_3:point_")
    ]
    assert len(points) == 4, "Article 6(3) has conditions (a)-(d)"
    assert all("By derogation from paragraph 2" in p.text for p in points)


def test_article_6_classification_routes_have_different_dates(corpus: Corpus) -> None:
    """Article 113(c) dates Article 6(1) to 2027-08-02 while Article 6(2) falls
    under the general 2026-08-02 date. Both routes must be represented on Article 6
    itself, or a determination cannot tell "not high-risk" from "this route was not
    yet in force" (§14)."""
    route_6_1 = corpus.rules_for("aiact:art_6:para_1:point_a")
    route_6_2 = corpus.rules_for("aiact:art_6:para_2")

    assert len(route_6_1) == 1
    assert route_6_1[0].applicable_from == ARTICLE_6_1_APPLICATION
    assert route_6_1[0].source_reference == "Article 113, point (c)"

    assert len(route_6_2) == 1
    assert route_6_2[0].applicable_from == GENERAL_APPLICATION

    # The same date that separates the Article 16 obligation rules.
    assert route_6_2[0].applicable_from < route_6_1[0].applicable_from


def test_article_6_1_route_not_in_force_before_2027(corpus: Corpus) -> None:
    """The `not_in_force` case, on the classification rule itself."""
    rule = corpus.rules_for("aiact:art_6:para_1:point_b")[0]
    assert not rule.in_force_on(date(2026, 8, 2)), (
        "the Annex III route is live here, but the Article 6(1) route is not"
    )
    assert rule.in_force_on(date(2027, 8, 2))


def test_article_6_1_rule_applies_to_its_points_not_just_the_paragraph(
    corpus: Corpus,
) -> None:
    """Article 6(1) has lettered points, so the canonical units are the points and
    the paragraph itself is never a provision. A rule declared for Article 6(1) has
    to reach them, or Article 113(c) would be recorded nowhere."""
    assert not [
        p for p in corpus.provisions
        if p.logical_provision_id == "aiact:art_6:para_1"
    ], "6(1) is not itself canonical -- it has points"
    for marker in ("a", "b"):
        rules = corpus.rules_for(f"aiact:art_6:para_1:point_{marker}")
        assert len(rules) == 1, f"6(1)({marker}) must inherit the Article 6(1) rule"


def test_article_6_4_is_scoped_to_annex_iii_only(corpus: Corpus) -> None:
    """6(4)'s own text limits it to "an AI system referred to in Annex III", so it
    takes one rule -- not the two-date split used for obligations covering both
    high-risk routes."""
    rules = corpus.rules_for("aiact:art_6:para_4")
    assert len(rules) == 1
    assert rules[0].conditions["system_category"] == "annex_iii_high_risk"
    assert rules[0].conditions["actor_role"] == "provider"


def test_commission_duties_are_declared_gaps_not_silent_ones(corpus: Corpus) -> None:
    """Article 6(5)-(7) bind the Commission, an actor V1 does not model. They must
    be recorded as declared gaps, so the omission is reviewable rather than
    invisible (§12.1)."""
    report = validate_corpus(corpus)
    gap_subjects = {
        f.subject for f in report.declared_gaps
        if f.check == "no_orphan_obligations"
    }
    for para in ("5", "6", "7"):
        logical_id = f"aiact:art_6:para_{para}"
        provision = find(corpus, logical_id)
        assert provision.provision_type == ProvisionType.OBLIGATION
        assert provision.applicability_gap_reason, logical_id
        assert logical_id in gap_subjects, logical_id
    assert report.ok, "declared gaps must not fail ingestion"


def test_adding_article_6_resolved_its_cross_references(corpus: Corpus) -> None:
    """The dependency that motivated the scope change: Article 6 was the largest
    unresolved cross-reference target before it was ingested."""
    citing = [p for p in corpus.provisions if "aiact:art_6" in p.cross_references]
    assert len(citing) > 20, "Article 6 is referenced across the corpus"

    report = validate_corpus(corpus)
    unresolved = {
        f.subject for f in report.declared_gaps
        if f.check == "cross_reference_integrity"
    }
    assert "aiact:art_6" not in unresolved, (
        "Article 6 must now resolve inside the V1 corpus"
    )


def test_declared_rule_prefix_matches_only_at_segment_boundaries() -> None:
    """`para_1` must not match `para_10`."""
    from eu_ai_check.corpus.rules import _declared_templates

    assert _declared_templates("aiact:art_6:para_1") is not None
    assert _declared_templates("aiact:art_6:para_1:point_a") is not None
    assert _declared_templates("aiact:art_6:para_10") is None
    assert _declared_templates("aiact:art_16:point_a") is None


# --------------------------------------------------------------------------
# The validators themselves must actually fail on bad data
# --------------------------------------------------------------------------

def test_clean_corpus_passes_validation(corpus: Corpus) -> None:
    report = validate_corpus(corpus)
    assert report.ok, [str(f) for f in report.errors]
    assert len(report.checks_run) == 8


def _provision(**overrides) -> Provision:
    text = overrides.pop("text", "Providers shall do the thing.")
    defaults = dict(
        logical_provision_id="aiact:art_16:point_a",
        versioned_provision_id="aiact:art_16:point_a@2024-08-01",
        content_id=content_id(text),
        provision_type=ProvisionType.OBLIGATION,
        structural_location=[
            StructuralSegment(type=SegmentType.ARTICLE, value="16"),
            StructuralSegment(type=SegmentType.POINT, value="a"),
        ],
        text=text,
        text_valid_from=date(2024, 8, 1),
    )
    return Provision(**(defaults | overrides))


def _rule(**overrides) -> ApplicabilityRule:
    defaults = dict(
        rule_id="r1",
        legal_provision_id="aiact:art_16:point_a",
        applicable_from=date(2026, 8, 2),
        conditions={"actor_role": "provider"},
        scope={"regulation": "Regulation (EU) 2024/1689"},
        source_reference="Article 113",
    )
    return ApplicabilityRule(**(defaults | overrides))


def _errors(corpus: Corpus) -> list[str]:
    return [f.check for f in validate_corpus(corpus).errors]


def test_validator_catches_overlapping_text_versions() -> None:
    """§11 version-integrity invariant."""
    bad = Corpus(
        source_reference="t",
        provisions=[
            _provision(
                versioned_provision_id="v1",
                text_valid_from=date(2024, 8, 1),
                text_valid_to=date(2026, 1, 1),
            ),
            _provision(
                versioned_provision_id="v2",
                text_valid_from=date(2025, 1, 1),  # starts before v1 ends
                text="Amended text.",
                content_id=content_id("Amended text."),
            ),
        ],
        applicability_rules=[_rule()],
    )
    assert "text_version_non_overlap" in _errors(bad)


def test_validator_catches_open_ended_version_followed_by_another() -> None:
    bad = Corpus(
        source_reference="t",
        provisions=[
            _provision(versioned_provision_id="v1", text_valid_to=None),
            _provision(
                versioned_provision_id="v2",
                text_valid_from=date(2026, 1, 1),
                text="Amended text.",
                content_id=content_id("Amended text."),
            ),
        ],
        applicability_rules=[_rule()],
    )
    assert "text_version_non_overlap" in _errors(bad)


def test_validator_catches_dangling_rule_reference() -> None:
    """§12.1: a rule pointing at nothing can never fire, so the obligation it was
    written to schedule would silently never apply."""
    bad = Corpus(
        source_reference="t",
        provisions=[_provision()],
        applicability_rules=[_rule(legal_provision_id="aiact:art_99:point_z")],
    )
    errors = _errors(bad)
    assert "rule_provision_reference" in errors


def test_validator_catches_stale_content_id() -> None:
    bad = Corpus(
        source_reference="t",
        provisions=[_provision(content_id="0" * 64)],
        applicability_rules=[_rule()],
    )
    assert "identity_model" in _errors(bad)


def test_validator_catches_empty_and_unrecognised_conditions() -> None:
    empty = Corpus(
        source_reference="t",
        provisions=[_provision()],
        applicability_rules=[_rule(conditions={})],
    )
    assert "rule_conditions_wellformed" in _errors(empty)

    unknown = Corpus(
        source_reference="t",
        provisions=[_provision()],
        applicability_rules=[_rule(conditions={"phase_of_moon": "waxing"})],
    )
    assert "rule_conditions_wellformed" in _errors(unknown)


def test_validator_catches_orphan_obligation() -> None:
    """§12.1: an obligation with zero rules is the source of a false `unresolved`
    later; Phase 0 is where it must surface."""
    bad = Corpus(
        source_reference="t",
        provisions=[_provision()],
        applicability_rules=[],
    )
    assert "no_orphan_obligations" in _errors(bad)


def test_declared_gap_is_permitted_but_still_reported() -> None:
    """A gap may be intentional, but it must be stated, not silent."""
    declared = Corpus(
        source_reference="t",
        provisions=[_provision(applicability_gap_reason="deferred to Phase 3")],
        applicability_rules=[],
    )
    report = validate_corpus(declared)
    assert report.ok, "a declared gap must not fail ingestion"
    assert any(
        f.check == "no_orphan_obligations" and f.severity is Severity.DECLARED_GAP
        for f in report.findings
    ), "a declared gap must still be reported"


def test_validator_catches_ambiguous_overlapping_rule_windows() -> None:
    bad = Corpus(
        source_reference="t",
        provisions=[_provision()],
        applicability_rules=[
            _rule(rule_id="r1", applicable_from=date(2026, 8, 2), applicable_to=None),
            _rule(rule_id="r2", applicable_from=date(2027, 8, 2)),
        ],
    )
    assert "rule_temporal_fields" in _errors(bad)


def test_model_rejects_inverted_rule_window() -> None:
    """Field-level ordering is enforced by the model, so a malformed rule cannot be
    constructed at all."""
    with pytest.raises(ValueError, match="strictly after"):
        _rule(applicable_from=date(2027, 1, 1), applicable_to=date(2026, 1, 1))


def test_model_rejects_inverted_text_validity() -> None:
    with pytest.raises(ValueError, match="strictly after"):
        _provision(
            text_valid_from=date(2026, 1, 1), text_valid_to=date(2025, 1, 1)
        )


def test_validator_catches_colliding_structural_paths() -> None:
    bad = Corpus(
        source_reference="t",
        provisions=[
            _provision(
                logical_provision_id="aiact:art_16:point_a",
                versioned_provision_id="v1",
            ),
            _provision(
                logical_provision_id="aiact:art_16:point_a_dup",
                versioned_provision_id="v2",
            ),
        ],
        applicability_rules=[_rule()],
    )
    assert "structural_path" in _errors(bad)


def test_validator_catches_illegal_path_transition() -> None:
    bad = Corpus(
        source_reference="t",
        provisions=[
            _provision(
                structural_location=[
                    StructuralSegment(type=SegmentType.ARTICLE, value="16"),
                    # An article cannot contain an annex area.
                    StructuralSegment(type=SegmentType.ANNEX_AREA, value="2"),
                ]
            )
        ],
        applicability_rules=[_rule()],
    )
    assert "structural_path" in _errors(bad)
