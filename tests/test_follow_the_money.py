from decimal import Decimal

from forage.follow_the_money import (
    AdjacentOrganism,
    DemandCategory,
    DemandEvidenceLevel,
    PaidServiceObservation,
    build_demand_map,
    demand_shape,
    evidence_for_existing_loops,
    justify_bootstrap,
    propose_adjacent_organisms,
)
from forage.models import AgentEconomicRole, SetupClass


def test_demand_shape_flags_high_frequency_few_buyers():
    assert demand_shape(72, 9_000_000, Decimal("90000")) == "concentrated_hf"
    assert demand_shape(2000, 16000, Decimal("100")) == "broad"


def test_evidence_ladder_change_monitors_not_e3_without_category_spend():
    demand_map = [
        DemandCategory(
            name="monitoring",
            evidence_level=DemandEvidenceLevel.E0,
            sellers_observed=0,
            buyers_observed=0,
            tx_activity=0,
            volume_usd=Decimal("0"),
        ),
        DemandCategory(
            name="blockchain_rpc_data",
            evidence_level=DemandEvidenceLevel.E4,
            sellers_observed=5,
            buyers_observed=2000,
            tx_activity=100000,
            volume_usd=Decimal("50000"),
        ),
    ]
    ladder = evidence_for_existing_loops(demand_map)
    by = {x["name"]: x for x in ladder}
    assert by["public_change_diff_refinery"]["evidence_level"] in {"E0", "E1"}
    assert by["material_change_alert_api"]["evidence_level"] in {"E0", "E1"}
    assert "related_to" in by["public_change_diff_refinery"]


def test_build_demand_map_promotes_observed_buyers_to_e3():
    obs = [
        PaidServiceObservation(
            source="x402scan",
            product_or_origin="https://api.onesource.io",
            category="blockchain_rpc_data",
            unique_buyers=2005,
            tx_count=16285,
            volume_usd=Decimal("101"),
            demand_shape="broad",
        )
    ]
    cats = build_demand_map(obs, [])
    chain = next(c for c in cats if c.name == "blockchain_rpc_data")
    assert chain.evidence_level in {DemandEvidenceLevel.E3, DemandEvidenceLevel.E4}


def test_bootstrap_skips_offline_and_shopify():
    orgs = [
        AdjacentOrganism(
            name="oneshot_wallet_token_snapshot",
            parent_category="blockchain_rpc_data",
            evidence_level=DemandEvidenceLevel.E4,
            agent_role=AgentEconomicRole.MERCHANT,
            summary="batch balances",
            input_desc="wallet",
            value_creation="rpc batch",
            buyer_distribution="x402",
            transaction="x402",
            fulfillment="json",
            verified_payment="settlement",
            observed_comparable_pricing="$0.003",
            estimated_marginal_cost_usd=Decimal("0.002"),
            proposed_price_usd=Decimal("0.01"),
            cheapest_test="Offline: fetch balances; no listing yet",
            setup_class=SetupClass.A_NONE,
            setup_required="none",
        )
    ]
    msg = justify_bootstrap(orgs)
    assert msg.startswith("No owner bootstrap")
    assert not msg.startswith("ONE bootstrap")


def test_propose_organisms_require_e3_parent():
    demand_map = [
        DemandCategory(
            name="monitoring",
            evidence_level=DemandEvidenceLevel.E1,
            volume_usd=Decimal("0"),
        ),
        DemandCategory(
            name="blockchain_rpc_data",
            evidence_level=DemandEvidenceLevel.E4,
            sellers_observed=3,
            buyers_observed=2000,
            tx_activity=50000,
            volume_usd=Decimal("10000"),
            typical_price_usd=[Decimal("0.003")],
            notable_products=["https://api.onesource.io"],
            evidence_urls=["https://www.x402scan.com/"],
        ),
        DemandCategory(
            name="retrieval_search",
            evidence_level=DemandEvidenceLevel.E3,
            sellers_observed=2,
            buyers_observed=160,
            tx_activity=8000,
            volume_usd=Decimal("55"),
            typical_price_usd=[Decimal("0.01")],
            evidence_urls=["https://www.x402scan.com/"],
        ),
    ]
    orgs = propose_adjacent_organisms(demand_map)
    assert len(orgs) >= 1
    assert all(o.parent_category != "monitoring" or o.evidence_level.value >= "E3" for o in orgs)
    assert any(o.parent_category == "blockchain_rpc_data" for o in orgs)
