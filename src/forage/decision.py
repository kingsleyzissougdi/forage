"""Deterministic CONTINUE / PAUSE / STOP — LLMs do not own this."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from forage.ledger import Ledger
from forage.models import ExperimentStatus, FlowPhase


@dataclass
class Decision:
    phase: FlowPhase
    status: ExperimentStatus
    reason: str


def evaluate_experiment(ledger: Ledger, experiment_id: str) -> Decision:
    exp = ledger.get_experiment(experiment_id)
    deadline = datetime.fromisoformat(exp["deadline"])
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    if now > deadline:
        return Decision(FlowPhase.STOP, ExperimentStatus.STOPPED, "deadline_passed")

    if exp["status"] == ExperimentStatus.NEEDS_CHANNEL_SETUP.value:
        return Decision(
            FlowPhase.NEEDS_CHANNEL_SETUP,
            ExperimentStatus.NEEDS_CHANNEL_SETUP,
            "needs_channel_setup",
        )

    if exp["status"] == ExperimentStatus.FAILED.value:
        return Decision(FlowPhase.FAILED, ExperimentStatus.FAILED, "failed")

    if exp["phase"] == FlowPhase.BLOCKED.value:
        return Decision(FlowPhase.BLOCKED, ExperimentStatus.PAUSED, "blocked_capability")

    # Evidence of external demand response?
    with ledger._connect() as conn:
        responses = conn.execute(
            "SELECT COUNT(*) AS c FROM evidence WHERE experiment_id=? AND kind='RESPONSE'",
            (experiment_id,),
        ).fetchone()["c"]
        demand = conn.execute(
            "SELECT COUNT(*) AS c FROM evidence WHERE experiment_id=? AND kind='DEMAND' AND externally_observed=1",
            (experiment_id,),
        ).fetchone()["c"]
        payments = conn.execute(
            "SELECT COUNT(*) AS c FROM economics WHERE experiment_id=? AND kind='payment_received' AND is_demo=0",
            (experiment_id,),
        ).fetchone()["c"]

    profit = ledger.contribution_profit(experiment_id, include_demo=False)
    budget = Decimal(exp["budget_usd"])

    if payments > 0 and profit >= 0:
        return Decision(FlowPhase.CONTINUE, ExperimentStatus.CONTINUED, "verified_payment")

    if responses > 0:
        return Decision(
            FlowPhase.WAITING_EXTERNAL,
            ExperimentStatus.WAITING_EXTERNAL,
            "external_response_recorded",
        )

    if demand == 0 and exp["phase"] in (
        FlowPhase.CHEAP_TEST.value,
        FlowPhase.EXTERNAL_EVIDENCE.value,
    ):
        return Decision(FlowPhase.PAUSE, ExperimentStatus.PAUSED, "no_external_demand_evidence")

    spent = ledger.totals()["spent"]
    if spent >= budget:
        return Decision(FlowPhase.STOP, ExperimentStatus.STOPPED, "budget_exhausted")

    # Still in pipeline
    phase = FlowPhase(exp["phase"])
    return Decision(phase, ExperimentStatus.RUNNING, "in_progress")
