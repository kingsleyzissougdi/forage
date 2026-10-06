from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from forage.models import ExperimentSpec


def test_clone_changes_exactly_one_field():
    spec = ExperimentSpec(
        customer="A",
        problem="P",
        offer="O",
        price_usd=Decimal("10"),
        acquisition_channel="web",
        fulfillment_method="digital",
        permitted_actions=["public_research"],
        budget_usd=Decimal("5"),
        deadline=datetime.now(timezone.utc) + timedelta(days=1),
        evidence_target="reply",
        continue_condition="pay",
        pause_condition="wait",
        stop_condition="dead",
    )
    cloned = spec.clone_with("price_usd", Decimal("12"))
    assert cloned.parent_id == spec.id
    assert cloned.version == spec.version + 1
    assert cloned.mutation_field == "price_usd"
    assert cloned.price_usd == Decimal("12")
    assert cloned.offer == spec.offer
    assert cloned.id != spec.id


def test_clone_rejects_unknown_field():
    spec = ExperimentSpec(
        customer="A",
        problem="P",
        offer="O",
        price_usd=1,
        acquisition_channel="web",
        fulfillment_method="digital",
        permitted_actions=["public_research"],
        budget_usd=5,
        deadline=datetime.now(timezone.utc) + timedelta(days=1),
        evidence_target="reply",
        continue_condition="pay",
        pause_condition="wait",
        stop_condition="dead",
    )
    with pytest.raises(ValueError):
        spec.clone_with("not_a_field", "x")
