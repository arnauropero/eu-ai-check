"""The `ApplicabilityRule` layer for the V1 corpus (SYSTEM_SPEC.md §12, §12.1).

Every date here is transcribed from **Article 113** of the Regulation, which sets
out the staggered application schedule, and each rule carries the point of Article
113 it came from in `source_reference`. No date is derived from a provision's
`text_valid_from` -- text validity and applicability are independent facts
(§13.1), and inferring one from the other is the exact conflation the two-layer
model exists to prevent.

Article 113, verbatim in substance:

    It shall apply from 2 August 2026. However:
    (a) Chapters I and II shall apply from 2 February 2025;
    (b) Chapter III Section 4, Chapter V, Chapter VII and Chapter XII and
        Article 78 shall apply from 2 August 2025, with the exception of
        Article 101;
    (c) Article 6(1) and the corresponding obligations in this Regulation shall
        apply from 2 August 2027.

How that lands on the V1 corpus:

* **Article 5** sits in Chapter II, so the prohibitions apply from **2025-02-02**
  under point (a). They bind regardless of actor role.

* **Articles 16, 25 and 26** sit in Chapter III Section 3, which is not carved out
  by points (a) or (b), so they fall under the general date of **2026-08-02**.

* Point (c) is why these articles need *two* rules each rather than one date.
  Article 6(1) covers high-risk systems that are safety components of products
  already regulated under the Annex I legislation; Article 6(2) covers the Annex
  III use-case list. The provider and deployer obligations are the same text in
  both cases, but they start applying a year apart: **2026-08-02** for Annex III
  systems, **2027-08-02** for Article 6(1) systems. Applicability therefore
  genuinely depends on `system_category` and cannot be collapsed to a single date
  per provision -- which is the concrete justification for modelling
  applicability as a conditioned rule layer instead of a second date pair on the
  provision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import ApplicabilityRule, Provision, ProvisionType

#: Article 113, first subparagraph -- the general application date.
GENERAL_APPLICATION = date(2026, 8, 2)
#: Article 113, point (a) -- Chapters I and II.
CHAPTER_I_II_APPLICATION = date(2025, 2, 2)
#: Article 113, point (c) -- Article 6(1) and its corresponding obligations.
ARTICLE_6_1_APPLICATION = date(2027, 8, 2)

REGULATION = "Regulation (EU) 2024/1689"

#: Condition keys recognised in V1 (§12.1). An unrecognised key is a validation
#: failure, not a silently accepted extra.
RECOGNISED_CONDITION_KEYS = frozenset({"actor_role", "system_category"})
RECOGNISED_SCOPE_KEYS = frozenset({"regulation", "structural_basis", "territorial_scope"})


@dataclass(frozen=True)
class RuleTemplate:
    """A hand-authored rule shape, expanded once per matching provision.

    The template is the unit of *legal* authorship (a date plus the conditions it
    applies under, traceable to Article 113); expanding it per provision is
    mechanical. Writing 70-odd records by hand would invite transcription errors
    in exactly the fields that matter most.
    """

    suffix: str
    applicable_from: date
    conditions: dict[str, str]
    scope: dict[str, str]
    source_reference: str
    applicable_to: date | None = None


#: Article 5 -- prohibitions, Chapter II, from 2 February 2025. A prohibition binds
#: any actor and is not conditioned on the Article 6 high-risk classification.
_ARTICLE_5_TEMPLATES = (
    RuleTemplate(
        suffix="ch2",
        applicable_from=CHAPTER_I_II_APPLICATION,
        conditions={"actor_role": "any", "system_category": "any"},
        scope={
            "regulation": REGULATION,
            "structural_basis": "Chapter II",
            "territorial_scope": "union",
        },
        source_reference="Article 113, point (a)",
    ),
)


def _high_risk_templates(actor_role: str) -> tuple[RuleTemplate, ...]:
    """The two-date split that Article 113(c) forces on high-risk obligations."""
    return (
        RuleTemplate(
            suffix="annex3",
            applicable_from=GENERAL_APPLICATION,
            conditions={
                "actor_role": actor_role,
                "system_category": "annex_iii_high_risk",
            },
            scope={
                "regulation": REGULATION,
                "structural_basis": "Chapter III Section 3",
                "territorial_scope": "union",
            },
            source_reference="Article 113, first subparagraph",
        ),
        RuleTemplate(
            suffix="art6_1",
            applicable_from=ARTICLE_6_1_APPLICATION,
            conditions={
                "actor_role": actor_role,
                "system_category": "article_6_1_high_risk",
            },
            scope={
                "regulation": REGULATION,
                "structural_basis": "Chapter III Section 3",
                "territorial_scope": "union",
            },
            source_reference="Article 113, point (c)",
        ),
    )


#: Which templates apply to which article. Keyed by the article segment value, so
#: the mapping stays readable next to Article 113 itself.
ARTICLE_RULE_TEMPLATES: dict[str, tuple[RuleTemplate, ...]] = {
    "5": _ARTICLE_5_TEMPLATES,
    "16": _high_risk_templates("provider"),
    # Article 25 governs when a distributor, importer or deployer is *treated as*
    # a provider. The resulting duties are provider duties, so it is scoped to the
    # provider role on the same schedule as Article 16.
    "25": _high_risk_templates("provider"),
    "26": _high_risk_templates("deployer"),
}


#: Rules attached to one specific part of an article rather than to every operative
#: provision of it. Keyed by a `logical_provision_id` **prefix**, which is matched
#: at segment boundaries and resolved longest-first.
#:
#: Prefix rather than exact match, because the keyed unit is often not itself a
#: canonical provision: Article 6(1) has lettered points, so the canonical units are
#: `art_6:para_1:point_a` and `:point_b`, and Article 113(c) dates the whole of
#: Article 6(1) -- points included. An exact-match lookup silently emits nothing
#: here.
#:
#: Article 6 needs this because Article 113(c) names **Article 6(1)** itself, not
#: just the obligations that depend on it: the Article 6(1) high-risk
#: classification route does not start applying until 2027-08-02, while the Annex
#: III route in Article 6(2) applies from the general date. Representing that on
#: Article 6 itself is what lets a determination distinguish "this system is not
#: high-risk" from "this classification route was not yet in force on the relevant
#: date" -- the `not_applicable` vs `not_in_force` distinction the four-state
#: contract requires (§14).
#:
#: These are declared per-provision because they are *not* uniform across the
#: article: typing them at article level would wrongly date Article 6(2) to 2027.
PROVISION_RULE_TEMPLATES: dict[str, tuple[RuleTemplate, ...]] = {
    "aiact:art_6:para_1": (
        RuleTemplate(
            suffix="art6_1",
            applicable_from=ARTICLE_6_1_APPLICATION,
            conditions={
                "actor_role": "any",
                "system_category": "article_6_1_high_risk",
            },
            scope={
                "regulation": REGULATION,
                "structural_basis": "Chapter III Section 1",
                "territorial_scope": "union",
            },
            source_reference="Article 113, point (c)",
        ),
    ),
    "aiact:art_6:para_2": (
        RuleTemplate(
            suffix="annex3",
            applicable_from=GENERAL_APPLICATION,
            conditions={
                "actor_role": "any",
                "system_category": "annex_iii_high_risk",
            },
            scope={
                "regulation": REGULATION,
                "structural_basis": "Chapter III Section 1",
                "territorial_scope": "union",
            },
            source_reference="Article 113, first subparagraph",
        ),
    ),
    # Article 6(4) is a provider duty, and it is scoped to Annex III systems by its
    # own text ("an AI system referred to in Annex III"), so it takes one rule --
    # not the two-date split that applies to obligations covering both high-risk
    # routes.
    "aiact:art_6:para_4": (
        RuleTemplate(
            suffix="annex3",
            applicable_from=GENERAL_APPLICATION,
            conditions={
                "actor_role": "provider",
                "system_category": "annex_iii_high_risk",
            },
            scope={
                "regulation": REGULATION,
                "structural_basis": "Chapter III Section 1",
                "territorial_scope": "union",
            },
            source_reference="Article 113, first subparagraph",
        ),
    ),
}


def _declared_templates(logical_id: str) -> tuple[RuleTemplate, ...] | None:
    """Longest-prefix lookup in `PROVISION_RULE_TEMPLATES`, matched only at segment
    boundaries so `para_1` cannot match `para_10`."""
    best: tuple[RuleTemplate, ...] | None = None
    best_len = -1
    for prefix, templates in PROVISION_RULE_TEMPLATES.items():
        if logical_id == prefix or logical_id.startswith(prefix + ":"):
            if len(prefix) > best_len:
                best, best_len = templates, len(prefix)
    return best


def _article_of(provision: Provision) -> str | None:
    from .models import SegmentType

    return next(
        (s.value for s in provision.structural_location if s.type == SegmentType.ARTICLE),
        None,
    )


def build_rules(provisions: list[Provision]) -> list[ApplicabilityRule]:
    """Expand the hand-authored templates across the ingested provisions.

    Only `obligation` and `prohibition` provisions carry rules -- those are the
    types whose legal question is "does this bind, and when" (§12.1). Definitions,
    exceptions and Annex III categories are retrieved and reasoned over directly;
    they create no standalone duty to schedule.
    """
    rules: list[ApplicabilityRule] = []
    for provision in provisions:
        # A provision-specific declaration wins over the article-level default, and
        # applies whatever the provision's type: Article 6(1)/(2) are `scope`
        # provisions that nonetheless carry their own application dates.
        templates = _declared_templates(provision.logical_provision_id)
        if templates is None:
            if not provision.requires_applicability_rule:
                continue
            templates = ARTICLE_RULE_TEMPLATES.get(_article_of(provision) or "", ())
        for template in templates:
            rules.append(
                ApplicabilityRule(
                    rule_id=f"{provision.logical_provision_id}#{template.suffix}",
                    legal_provision_id=provision.logical_provision_id,
                    applicable_from=template.applicable_from,
                    applicable_to=template.applicable_to,
                    conditions=dict(template.conditions),
                    scope=dict(template.scope),
                    source_reference=template.source_reference,
                )
            )
    return rules
