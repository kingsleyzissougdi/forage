from forage.adapters.gateway import MockResearchAdapter, ResearchHit
from forage.opportunity import discover_opportunities, eligibility


def test_selection_prefers_diverse_categories():
    hits = [
        ResearchHit("csv cleanup needed", "https://ex.com/1", "will pay for csv clean", "mock"),
        ResearchHit("competitor pricing", "https://ex.com/2", "pricing scrape weekly", "mock"),
        ResearchHit("lead list", "https://ex.com/3", "email list enrichment leads", "mock"),
        ResearchHit("research brief", "https://ex.com/4", "pay for research brief", "mock"),
    ]
    research = MockResearchAdapter(hits)
    selected = discover_opportunities(research, limit=3, use_public_web=False)
    assert 1 <= len(selected) <= 3
    assert all(eligibility(c) for c in selected)
    cats = [c.category for c in selected]
    assert len(cats) == len(set(cats))
