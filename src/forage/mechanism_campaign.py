"""Gen-0 mechanism campaign: analyze → select 3 → parallel cheap tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from forage.flow import build_services, default_db_path
from forage.ledger import Ledger
from forage.mechanisms import (
    consolidated_bootstrap_request,
    mechanisms_report_payload,
    run_finalist_tests_parallel,
    select_finalists,
)
from forage.models import (
    EvidenceKind,
    ExperimentSpec,
    ExperimentStatus,
    FlowPhase,
    MechanismSpec,
)
from forage.roles import Strategist


def _ensure_campaign(ledger: Ledger) -> None:
    if any(e["id"] == "campaign" for e in ledger.list_experiments()):
        return
    ledger.upsert_experiment(
        ExperimentSpec(
            id="campaign",
            customer="n/a",
            problem="campaign_meta",
            offer="n/a",
            price_usd=Decimal("0"),
            acquisition_channel="n/a",
            fulfillment_method="n/a",
            permitted_actions=["public_research", "web_fetch", "prepare_artifact"],
            budget_usd=Decimal("0"),
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            evidence_target="n/a",
            continue_condition="n/a",
            pause_condition="n/a",
            stop_condition="n/a",
        ),
        ExperimentStatus.DRAFT,
        FlowPhase.OPPORTUNITY,
    )


def _spec_from_mechanism(m: MechanismSpec) -> ExperimentSpec:
    return ExperimentSpec(
        customer=m.payer,
        problem=m.description,
        offer=m.required_agent_action,
        price_usd=m.expected_txn_value_usd,
        acquisition_channel=m.evidence_urls[0] if m.evidence_urls else m.mechanism_type,
        fulfillment_method=m.acceptance_method,
        permitted_actions=[
            "public_research",
            "web_fetch",
            "prepare_artifact",
            "post_offer",
        ],
        budget_usd=min(m.startup_cost_usd * Decimal("2") + Decimal("5"), Decimal("20")),
        deadline=datetime.now(timezone.utc) + timedelta(hours=max(24, min(m.time_to_feedback_hours, 72 * 3))),
        evidence_target=m.cheapest_validation,
        continue_condition=f"observe:{m.paid_event}",
        pause_condition="needs_bootstrap_or_waiting",
        stop_condition="falsified_or_budget_or_deadline",
        mutation_field=m.mechanism_type,
    )


def run_mechanism_generation(
    db_path: Path | None = None,
    *,
    demo: bool = False,
) -> dict[str, Any]:
    services = build_services(db_path=db_path or default_db_path(), demo=demo)
    ledger: Ledger = services["ledger"]
    strategist: Strategist = services["strategist"]
    _ensure_campaign(ledger)

    raw = strategist.analyze_mechanisms("campaign")
    # Gateway may return JSON-safe dicts if replayed; prefer live discover when needed
    mechanisms: list[MechanismSpec] = []
    for item in raw:
        if isinstance(item, MechanismSpec):
            mechanisms.append(item)
        elif isinstance(item, dict) and "mechanism_type" in item:
            mechanisms.append(MechanismSpec.model_validate(item))

    if len(mechanisms) < 10 and not demo:
        from forage.mechanisms import discover_mechanisms

        mechanisms = discover_mechanisms(services["research"])

    finalists = select_finalists(mechanisms, k=3)
    ledger.set_meta(
        "gen0_mechanisms",
        json.dumps(
            [
                {
                    "id": m.mechanism_id,
                    "type": m.mechanism_type,
                    "score": m.score,
                    "eligible": m.eligible,
                    "evidence": m.evidence_urls[:4],
                }
                for m in mechanisms
            ]
        ),
    )
    ledger.set_meta(
        "gen0_finalists",
        json.dumps([json.loads(m.model_dump_json()) for m in finalists]),
    )

    # Persist finalists as experiments
    for m in finalists:
        spec = _spec_from_mechanism(m)
        # Stable-ish id from mechanism type for readability
        spec.id = f"mech-{m.mechanism_type[:40]}-{uuid4().hex[:6]}"
        status = ExperimentStatus.RUNNING
        phase = FlowPhase.CHEAP_TEST
        # Do not mark NEEDS_BOOTSTRAP from abstract mechanism class alone.
        ledger.upsert_experiment(spec, status, phase)
        for url in m.evidence_urls[:3]:
            ledger.add_evidence(
                experiment_id=spec.id,
                kind=EvidenceKind.DEMAND,
                source="public_web",
                summary=f"mechanism={m.mechanism_type}; event={m.paid_event}",
                externally_observed=True,
                reference=url,
            )

    # Parallel cheap tests
    results = run_finalist_tests_parallel(finalists)
    by_type = {m.mechanism_type: m for m in finalists}
    for r in results:
        exp_rows = [e for e in ledger.list_experiments() if e.get("mutation_field") == r.mechanism_type or r.mechanism_type in (e.get("id") or "")]
        eid = exp_rows[0]["id"] if exp_rows else "campaign"
        ledger.add_evidence(
            experiment_id=eid,
            kind=EvidenceKind.RESPONSE if r.launched else EvidenceKind.OTHER,
            source="cheap_test",
            summary=sanitize_summary(r),
            externally_observed=r.launched and r.status in ("EXTERNAL_SIGNAL", "NEEDS_BOOTSTRAP"),
            payload={
                "status": r.status,
                "detail": r.detail,
                "needs_bootstrap": r.needs_bootstrap,
                "mechanism_id": r.mechanism_id,
            },
        )
        if r.needs_bootstrap:
            ledger.set_experiment_state(eid, status=ExperimentStatus.NEEDS_BOOTSTRAP, phase=FlowPhase.NEEDS_BOOTSTRAP)
        elif r.status == "EXTERNAL_SIGNAL":
            ledger.set_experiment_state(eid, status=ExperimentStatus.WAITING_EXTERNAL, phase=FlowPhase.EXTERNAL_EVIDENCE)
        _ = by_type

    # Abstract mechanism ranking never issues owner KYC; concrete hunt may.
    bootstrap = consolidated_bootstrap_request(results, finalists)
    if bootstrap:
        ledger.set_meta("setup_request", "")
        ledger.set_meta("bootstrap_request", bootstrap)

    report = mechanisms_report_payload(mechanisms, finalists, results)
    report["totals"] = {k: str(v) for k, v in ledger.totals().items()}
    report["db_path"] = str(db_path or default_db_path())
    ledger.set_meta("gen0_report", json.dumps(report, default=str)[:100000])
    return report


def sanitize_summary(r: Any) -> str:
    from forage.adapters.gateway import sanitize_untrusted

    return sanitize_untrusted(f"{r.mechanism_type}:{r.status}:launched={r.launched}")
