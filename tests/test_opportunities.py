from decimal import Decimal

from forage.mechanisms import score_mechanism
from forage.models import MechanismRole, MechanismSpec, SetupClass
from forage.opportunities import cash_path_complete, eligibility_opportunity, score_opportunity


def test_research_infrastructure_cannot_win_capital_score():
    m = MechanismSpec(
        mechanism_type="public_bounty_inventory_signal",
        role=MechanismRole.RESEARCH_INFRASTRUCTURE,
        description="inventory only",
        payer="n/a",
        paid_event="observe",
        evidence_urls=["https://example.com"],
        required_agent_action="fetch",
        acceptance_method="http",
        time_to_feedback_hours=1,
        startup_cost_usd=Decimal("0"),
        expected_txn_value_usd=Decimal("50"),
        repeatability=0.9,
        automation_feasibility=0.95,
        setup_class=SetupClass.A_NONE,
        subjective_quality_risk=0.05,
        liability_reputation_risk=0.05,
        platform_account_risk=0.05,
        fulfillment_complexity=0.1,
        cheapest_validation="get",
    )
    m = score_mechanism(m)
    assert m.score == 0.0
    assert m.score_components.get("capital_eligible") == 0.0


def test_cash_path_gate_blocks_incomplete():
    from forage.models import PaidOpportunity

    o = PaidOpportunity(
        platform="X",
        source_url="https://example.com/p",
        title="Test",
        payout_usd_low=0,
        payout_usd_high=0,
        acceptance_rule="",
        required_deliverable="report",
        currently_open=True,
        autonomy_confidence=0.5,
        key_risk="risk",
    )
    missing = cash_path_complete(o)
    assert "payout" in missing
    assert "acceptance_rule" in missing


def test_opportunity_scores_are_0_to_5():
    from forage.models import PaidOpportunity

    o = PaidOpportunity(
        platform="YesWeHack",
        source_url="https://yeswehack.com/programs/example",
        title="Example Bug Bounty",
        payout_usd_low=Decimal("100"),
        payout_usd_high=Decimal("1000"),
        acceptance_rule="triage accepts in-scope report per severity table",
        required_deliverable="Valid vulnerability report",
        currently_open=True,
        agent_exec_cost_usd=Decimal("2"),
        agent_exec_hours=10,
        ongoing_human_minutes=5,
        setup_class=SetupClass.D_ONE_TIME_OWNER,
        setup_required="account",
        autonomy_confidence=0.4,
        key_risk="skill gap",
        mechanism_type="objective_security_bounty",
    )
    o = score_opportunity(eligibility_opportunity(o))
    assert o.eligible is True
    for k, v in o.scores.items():
        assert 0 <= v <= 5, (k, v)
    assert 0 <= o.rank_score <= 5


def test_hard_viability_gate_requires_80pct_autonomy():
    from forage.models import PaidOpportunity
    from forage.viability_sprint import apply_viability, hard_viability_gate

    weak = PaidOpportunity(
        platform="GitHub OSS bounty",
        source_url="https://github.com/ex/repo/issues/1",
        title="Fix flaky unit test",
        payout_usd_low=Decimal("50"),
        payout_usd_high=Decimal("50"),
        acceptance_rule="CI green and maintainer merges PR satisfying issue checklist",
        required_deliverable="PR fixing flaky test with new regression coverage",
        currently_open=True,
        agent_exec_cost_usd=Decimal("2"),
        agent_exec_hours=8,
        ongoing_human_minutes=5,
        setup_class=SetupClass.C_AGENT_CREATABLE,
        setup_required="GitHub account",
        autonomy_confidence=0.72,
        key_risk="review delays",
        mechanism_type="oss_objective_pr_bounty",
    )
    fails = hard_viability_gate(weak)
    assert "autonomy_below_80pct" in fails
    weak = apply_viability(weak)
    assert weak.eligible is False

    strong = weak.model_copy(update={"autonomy_confidence": 0.85})
    strong = apply_viability(strong)
    assert strong.eligible is True
