"""Opportunity discovery and deterministic selection (no ML / LLM scores as evidence)."""

from __future__ import annotations

from decimal import Decimal

from forage.adapters.gateway import (
    OpportunityCandidate,
    PublicWebResearchAdapter,
    ResearchHit,
    ResearchPort,
    sanitize_untrusted,
)

# Seed queries targeting agent-friendly, digital, fast-feedback mechanisms
SEED_QUERIES = [
    "csv cleaning freelance",
    "data cleaning spreadsheet",
    "competitor pricing research",
    "lead generation freelance",
    "research brief hiring",
]


def _hits_to_candidates(hits: list[ResearchHit]) -> list[OpportunityCandidate]:
    """Map raw public hits into structured candidates with concrete fields."""
    candidates: list[OpportunityCandidate] = []
    for hit in hits:
        text = f"{hit.title} {hit.snippet}".lower()
        if any(
            k in text
            for k in (
                "csv",
                "spreadsheet",
                "data clean",
                "excel",
                "data cleansing",
                "data entry",
            )
        ):
            candidates.append(
                OpportunityCandidate(
                    buyer="Ops / ecommerce teams needing recurring CSV cleanup",
                    demand_where=hit.url or hit.source,
                    what_people_pay_or_do="Post freelance gigs / pay per cleanup job",
                    sellable_outcome="Cleaned CSV delivered within 24h + one-page change log",
                    shortest_validation=("Post a fixed-price offer on a public freelance board; measure replies"),
                    expected_time_hours=6.0,
                    expected_cost_usd=Decimal("5.00"),
                    automation_feasibility=0.85,
                    economic_upside_usd=Decimal("200.00"),
                    evidence_urls=[hit.url] if hit.url else [],
                    category="productized_digital_service",
                    raw_hits=[hit],
                )
            )
        elif any(k in text for k in ("pricing", "competitor", "market monitor", "scrape", "market analysis")):
            candidates.append(
                OpportunityCandidate(
                    buyer="Indie SaaS founders tracking competitor pricing",
                    demand_where=hit.url or hit.source,
                    what_people_pay_or_do="Manual checks or hire freelancers for snapshots",
                    sellable_outcome="Weekly PDF of N competitor prices with source links",
                    shortest_validation=("Offer one free sample snapshot to 10 prospects; ask for paid weekly"),
                    expected_time_hours=8.0,
                    expected_cost_usd=Decimal("8.00"),
                    automation_feasibility=0.8,
                    economic_upside_usd=Decimal("300.00"),
                    evidence_urls=[hit.url] if hit.url else [],
                    category="information_data_product",
                    raw_hits=[hit],
                )
            )
        elif any(k in text for k in ("lead", "enrichment", "email list", "prospect", "hiring")):
            candidates.append(
                OpportunityCandidate(
                    buyer="B2B sellers who need niche prospect lists",
                    demand_where=hit.url or hit.source,
                    what_people_pay_or_do="Buy lead lists or hire VA research",
                    sellable_outcome="Verified list of 50 niche prospects with public contact path",
                    shortest_validation=("Sell one niche list at fixed price via public marketplace listing"),
                    expected_time_hours=10.0,
                    expected_cost_usd=Decimal("10.00"),
                    automation_feasibility=0.7,
                    economic_upside_usd=Decimal("400.00"),
                    evidence_urls=[hit.url] if hit.url else [],
                    category="lead_opportunity_matching",
                    raw_hits=[hit],
                )
            )
        elif any(k in text for k in ("research", "brief", "summary", "report", "freelance")):
            candidates.append(
                OpportunityCandidate(
                    buyer="Operators who need one-off research briefs",
                    demand_where=hit.url or hit.source,
                    what_people_pay_or_do="Pay freelancers for short research memos",
                    sellable_outcome="2-page sourced brief on a scoped question within 48h",
                    shortest_validation="List a fixed-scope brief offer; require prepaid or escrow",
                    expected_time_hours=7.0,
                    expected_cost_usd=Decimal("6.00"),
                    automation_feasibility=0.75,
                    economic_upside_usd=Decimal("250.00"),
                    evidence_urls=[hit.url] if hit.url else [],
                    category="information_data_product",
                    raw_hits=[hit],
                )
            )
    # Deduplicate by category keeping richest evidence
    by_cat: dict[str, OpportunityCandidate] = {}
    for c in candidates:
        prev = by_cat.get(c.category)
        if prev is None or len(c.evidence_urls) > len(prev.evidence_urls):
            by_cat[c.category] = c
        elif prev is not None:
            prev.evidence_urls = list(dict.fromkeys(prev.evidence_urls + c.evidence_urls))
            prev.raw_hits.extend(c.raw_hits)
    return list(by_cat.values())


def eligibility(c: OpportunityCandidate) -> bool:
    return (
        bool(c.buyer)
        and bool(c.demand_where)
        and bool(c.sellable_outcome)
        and c.expected_cost_usd <= Decimal("15")
        and c.expected_time_hours <= 24
        and c.automation_feasibility >= 0.6
        and len(c.evidence_urls) + len(c.raw_hits) > 0
    )


def compare_key(c: OpportunityCandidate) -> tuple:
    """Deterministic ranking: demand evidence, speed, cost, automation, upside."""
    evidence_n = len(c.evidence_urls) + len(c.raw_hits)
    # Higher better for evidence, automation, upside; lower better for time/cost
    return (
        -evidence_n,
        c.expected_time_hours,
        float(c.expected_cost_usd),
        -c.automation_feasibility,
        -float(c.economic_upside_usd),
        c.category,
    )


def discover_opportunities(
    research: ResearchPort | None = None,
    *,
    limit: int = 3,
    use_public_web: bool = True,
) -> list[OpportunityCandidate]:
    research = research or (PublicWebResearchAdapter() if use_public_web else None)
    assert research is not None
    all_hits: list[ResearchHit] = []
    for q in SEED_QUERIES:
        all_hits.extend(research.search(q, limit=5))
    # Ensure snippets sanitized
    for h in all_hits:
        h.title = sanitize_untrusted(h.title)
        h.snippet = sanitize_untrusted(h.snippet)

    candidates = _hits_to_candidates(all_hits)
    if not candidates:
        # Fallback structured candidates from whatever hits we got (still require URLs/snippets)
        if all_hits:
            candidates = [
                OpportunityCandidate(
                    buyer="Unknown buyer — weak evidence; needs better channel",
                    demand_where=all_hits[0].url or all_hits[0].source,
                    what_people_pay_or_do=sanitize_untrusted(all_hits[0].snippet or "unclear"),
                    sellable_outcome="Scoped digital deliverable TBD from demand snippet",
                    shortest_validation="Confirm buyer class with 5 outbound messages (policy permitting)",
                    expected_time_hours=12.0,
                    expected_cost_usd=Decimal("5.00"),
                    automation_feasibility=0.65,
                    economic_upside_usd=Decimal("100.00"),
                    evidence_urls=[h.url for h in all_hits if h.url][:5],
                    category="productized_digital_service",
                    raw_hits=all_hits[:5],
                )
            ]
    eligible = [c for c in candidates if eligibility(c)]
    eligible.sort(key=compare_key)
    # Prefer diverse categories
    selected: list[OpportunityCandidate] = []
    seen_cats: set[str] = set()
    for c in eligible:
        if c.category in seen_cats:
            continue
        selected.append(c)
        seen_cats.add(c.category)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for c in eligible:
            if c in selected:
                continue
            selected.append(c)
            if len(selected) >= limit:
                break
    return selected[:limit]
