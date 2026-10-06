from decimal import Decimal

from forage.economic_loops import (
    apply_loop_gate,
    hard_loop_gate,
    seal_loop,
    select_top_loops,
)
from forage.models import AgentEconomicRole, EconomicLoopSpec, LoopArrow, SetupClass


def _good_loop(**overrides) -> EconomicLoopSpec:
    base = dict(
        name="test_merchant",
        scout="a2a_services",
        agent_role=AgentEconomicRole.MERCHANT,
        summary="Sell schema JSON via payable catalog",
        input_arrow=LoopArrow(description="public data", evidence_urls=["https://example.com/in"]),
        value_creation=LoopArrow(description="normalize to schema", evidence_urls=["https://example.com/vc"]),
        buyer_discovery=LoopArrow(description="agent bazaar catalog", evidence_urls=["https://example.com/buy"]),
        transaction=LoopArrow(description="x402 USDC settlement per call", evidence_urls=["https://example.com/tx"]),
        fulfillment=LoopArrow(description="return validated JSON", evidence_urls=["https://example.com/out"]),
        verified_payment=LoopArrow(description="x402 settlement receipt", evidence_urls=["https://example.com/pay"]),
        marginal_cost=LoopArrow(description="fetch+model ~0.008", evidence_notes="hypothesis"),
        sell_price_usd=Decimal("0.025"),
        marginal_cost_usd=Decimal("0.008"),
        ongoing_human_minutes=0,
        setup_class=SetupClass.D_ONE_TIME_OWNER,
        setup_required="wallet",
        afternoon_experiment="stub endpoint + 1 paid test call",
        afternoon_budget_usd=Decimal("15"),
        automation_pct=0.9,
        repeatability=0.8,
        time_to_first_payment_hours=8,
        key_risk="cold start",
    )
    base.update(overrides)
    return EconomicLoopSpec(**base)


def test_hard_loop_gate_rejects_incomplete_evidence():
    loop = _good_loop(
        input_arrow=LoopArrow(description="public data", evidence_urls=[], evidence_notes=""),
    )
    fails = hard_loop_gate(apply_loop_gate(loop))
    assert any(r.startswith("missing_arrow_evidence:input_arrow") for r in fails)


def test_hard_loop_gate_rejects_nonpositive_margin():
    loop = apply_loop_gate(_good_loop(sell_price_usd=Decimal("0.01"), marginal_cost_usd=Decimal("0.02")))
    assert "nonpositive_contribution_margin" in loop.reject_reasons
    assert loop.eligible is False


def test_hard_loop_gate_rejects_ongoing_human():
    loop = apply_loop_gate(_good_loop(ongoing_human_minutes=15))
    assert "ongoing_human_over_10_min" in loop.reject_reasons


def test_hard_loop_gate_rejects_worker_role():
    loop = apply_loop_gate(_good_loop(agent_role=AgentEconomicRole.WORKER))
    assert "worker_listing_out_of_scope" in loop.reject_reasons


def test_good_loop_passes_and_scores_0_to_5():
    loop = seal_loop(_good_loop())
    assert loop.eligible is True
    assert loop.contribution_margin_usd > 0
    assert loop.scores["contribution_margin"] > 0
    for v in loop.scores.values():
        assert 0 <= v <= 5
    assert 0 <= loop.rank_score <= 5


def test_seal_loop_finalizes_margin_before_score():
    """Regression: scoring before gate left contribution_margin component at 0."""
    loop = seal_loop(_good_loop(sell_price_usd=Decimal("0.025"), marginal_cost_usd=Decimal("0.008")))
    assert loop.contribution_margin_usd == Decimal("0.017")
    assert loop.scores["contribution_margin"] > 0


def test_select_top_loops_diverse_scouts():
    loops = [
        seal_loop(_good_loop(name="a", scout="a2a_services", sell_price_usd=Decimal("0.05"))),
        seal_loop(
            _good_loop(
                name="b",
                scout="data_refinery",
                agent_role=AgentEconomicRole.REFINERY,
                sell_price_usd=Decimal("0.04"),
            )
        ),
        seal_loop(
            _good_loop(
                name="c",
                scout="referral_broker",
                agent_role=AgentEconomicRole.BROKER,
                sell_price_usd=Decimal("0.03"),
            )
        ),
        seal_loop(_good_loop(name="a2", scout="a2a_services", sell_price_usd=Decimal("0.049"))),
    ]
    top = select_top_loops(loops, k=3)
    assert len(top) == 3
    assert len({L.scout for L in top}) >= 2
