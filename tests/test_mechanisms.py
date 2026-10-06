from decimal import Decimal

from forage.adapters.gateway import MockResearchAdapter, ResearchHit
from forage.mechanisms import (
    discover_mechanisms,
    eligibility_check,
    score_mechanism,
    select_finalists,
)
from forage.models import MechanismSpec, SetupClass


def test_eligibility_rejects_forbidden():
    m = MechanismSpec(
        mechanism_type="bad",
        description="fake review farm for spam blast",
        payer="x",
        paid_event="y",
        evidence_urls=["https://example.com"],
        required_agent_action="spam",
        acceptance_method="none",
        time_to_feedback_hours=1,
        startup_cost_usd=Decimal("1"),
        expected_txn_value_usd=Decimal("10"),
        repeatability=0.5,
        automation_feasibility=0.5,
        subjective_quality_risk=0.2,
        liability_reputation_risk=0.2,
        platform_account_risk=0.2,
        fulfillment_complexity=0.2,
        cheapest_validation="n/a",
    )
    m = eligibility_check(m)
    assert m.eligible is False
    assert any("forbidden" in r for r in m.reject_reasons)


def test_score_is_inspectable():
    from forage.models import MechanismRole

    m = MechanismSpec(
        mechanism_type="public_challenge_inventory_signal",
        role=MechanismRole.RESEARCH_INFRASTRUCTURE,
        description="public prizes",
        payer="sponsor",
        paid_event="observe listing",
        evidence_urls=["https://www.challenge.gov/"],
        required_agent_action="fetch",
        acceptance_method="http",
        time_to_feedback_hours=1,
        startup_cost_usd=Decimal("0"),
        expected_txn_value_usd=Decimal("0"),
        repeatability=0.3,
        automation_feasibility=0.95,
        ongoing_human_minutes_per_txn=0,
        one_time_bootstrap_minutes=0,
        setup_class=SetupClass.A_NONE,
        subjective_quality_risk=0.05,
        liability_reputation_risk=0.05,
        platform_account_risk=0.05,
        fulfillment_complexity=0.1,
        cheapest_validation="GET listings",
    )
    m = score_mechanism(eligibility_check(m))
    assert m.score == 0.0
    assert m.score_components.get("capital_eligible") == 0.0
    assert m.eligible is True

    earning = MechanismSpec(
        mechanism_type="objective_security_bounty",
        description="paid bounty",
        payer="sponsor",
        paid_event="triage accept",
        evidence_urls=["https://immunefi.com/bug-bounty/"],
        required_agent_action="submit report",
        acceptance_method="triage",
        time_to_feedback_hours=72,
        startup_cost_usd=Decimal("0"),
        expected_txn_value_usd=Decimal("100"),
        repeatability=0.6,
        automation_feasibility=0.4,
        setup_class=SetupClass.D_ONE_TIME_OWNER,
        subjective_quality_risk=0.2,
        liability_reputation_risk=0.3,
        platform_account_risk=0.2,
        fulfillment_complexity=0.5,
        cheapest_validation="confirm live program",
    )
    earning = score_mechanism(eligibility_check(earning))
    assert "expected_upside_0_5" in earning.score_components
    assert 0 <= earning.score <= 5


def test_select_finalists_excludes_research_infrastructure():
    hits = [
        ResearchHit("Immunefi bounty paid", "https://immunefi.com/bug-bounty/", "bounty reward $", "mock"),
        ResearchHit("Challenge.gov prize", "https://www.challenge.gov/", "prize awarded", "mock"),
        ResearchHit("affiliate commission", "https://ex.com/aff", "affiliate commission conversion", "mock"),
        ResearchHit("dataset marketplace sold", "https://ex.com/data", "dataset sold download", "mock"),
        ResearchHit("gitcoin bounty", "https://ex.com/gitcoin", "gitcoin open source bounty", "mock"),
        ResearchHit("uptime monitoring pricing", "https://ex.com/up", "uptime monitor subscription pricing", "mock"),
        ResearchHit("lead referral", "https://ex.com/lead", "lead referral cash", "mock"),
        ResearchHit("mechanical turk HIT payment", "https://ex.com/mt", "mechanical turk HIT payment", "mock"),
        ResearchHit("chrome extension paid", "https://ex.com/ext", "chrome extension paid users", "mock"),
        ResearchHit("API metered usage", "https://ex.com/api", "api metered usage billing", "mock"),
        ResearchHit("kaggle prize", "https://ex.com/kaggle", "kaggle competition prize awarded", "mock"),
        ResearchHit("RSS alert paid", "https://ex.com/rss", "RSS keyword alert paid newsletter", "mock"),
    ]
    research = MockResearchAdapter(hits)
    mechs = discover_mechanisms(research)
    assert len(mechs) >= 10
    finalists = select_finalists(mechs, k=3)
    assert len(finalists) == 3
    types = [f.mechanism_type for f in finalists]
    assert len(set(types)) == 3
    assert all(not t.endswith("_inventory_signal") for t in types)
    assert all(f.role.value == "EARNING_MECHANISM" for f in finalists)
    assert all(f.score > 0 for f in finalists)
