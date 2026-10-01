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
        if not provision.requires_applicability_rule:
            continue
        article = _article_of(provision)
        for template in ARTICLE_RULE_TEMPLATES.get(article or "", ()):
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
