"""Thin role wrappers. Deterministic Python owns permissions/budgets/decisions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from forage.adapters.gateway import ActionGateway, ResearchPort, sanitize_untrusted
from forage.ledger import Ledger
from forage.mechanisms import discover_mechanisms
from forage.models import EvidenceKind, ExperimentSpec, MechanismSpec
from forage.opportunity import OpportunityCandidate, discover_opportunities


class Strategist:
    """Find shortest credible path to external economic evidence.

    Gen-0 Opportunity Analyst phase: discover monetization MECHANISMS (not ideas).
    """

    def __init__(self, ledger: Ledger, gateway: ActionGateway, research: ResearchPort):
        self.ledger = ledger
        self.gateway = gateway
        self.research = research

    def analyze_mechanisms(self, experiment_id: str | None = None) -> list[MechanismSpec]:
        """Opportunity Analyst: public evidence → MechanismSpecs (≥10 target)."""
        out = self.gateway.execute(
            experiment_id=experiment_id or "campaign",
            action_type="public_research",
            actor="strategist",
            tool="public_web",
            estimate_usd=Decimal("0"),
            idempotency_key=f"mechanisms:{experiment_id or 'campaign'}:gen0",
            provider="public_web",
            fn=lambda: discover_mechanisms(self.research),
            detail={"phase": "OPPORTUNITY_ANALYST"},
        )
        result = out.get("result") or []
        if experiment_id and isinstance(result, list):
            for m in result:
                if not isinstance(m, MechanismSpec):
                    continue
                self.ledger.add_evidence(
                    experiment_id=experiment_id,
                    kind=EvidenceKind.DEMAND,
                    source="public_web",
                    summary=sanitize_untrusted(f"mechanism={m.mechanism_type}; payer={m.payer}; event={m.paid_event}"),
                    externally_observed=True,
                    reference=(m.evidence_urls[0] if m.evidence_urls else None),
                    payload={
                        "mechanism_id": m.mechanism_id,
                        "score": m.score,
                        "eligible": m.eligible,
                        "urls": m.evidence_urls[:5],
                    },
                )
        return result if isinstance(result, list) else []

    def find_opportunities(self, experiment_id: str | None = None) -> list[OpportunityCandidate]:
        out = self.gateway.execute(
            experiment_id=experiment_id or "campaign",
            action_type="public_research",
            actor="strategist",
            tool="public_web",
            estimate_usd=Decimal("0"),
            idempotency_key=f"research:{experiment_id or 'campaign'}:gen0",
            provider="public_web",
            fn=lambda: discover_opportunities(self.research, limit=3, use_public_web=True),
            detail={"phase": "OPPORTUNITY"},
        )
        result = out.get("result") or []
        if experiment_id and result:
            for c in result:
                self.ledger.add_evidence(
                    experiment_id=experiment_id,
                    kind=EvidenceKind.DEMAND,
                    source="public_web",
                    summary=sanitize_untrusted(f"buyer={c.buyer}; where={c.demand_where}; offer={c.sellable_outcome}"),
                    externally_observed=True,
                    reference=(c.evidence_urls[0] if c.evidence_urls else None),
                    payload={
                        "category": c.category,
                        "urls": c.evidence_urls,
                        "validation": c.shortest_validation,
                    },
                )
        return result if isinstance(result, list) else []


class Operator:
    """Uses permitted tools; creates/delivers work."""

    def __init__(self, ledger: Ledger, gateway: ActionGateway):
        self.ledger = ledger
        self.gateway = gateway

    def prepare_artifact(self, spec: ExperimentSpec) -> dict[str, Any]:
        def _make():
            return {
                "artifact": {
                    "title": spec.offer,
                    "customer": spec.customer,
                    "price_usd": str(spec.price_usd),
                    "channel": spec.acquisition_channel,
                    "fulfillment": spec.fulfillment_method,
                    "body": (f"Offer: {spec.offer}\nFor: {spec.customer}\nProblem: {spec.problem}\nPrice: ${spec.price_usd}\n"),
                }
            }

        return self.gateway.execute(
            experiment_id=spec.id,
            action_type="prepare_artifact",
            actor="operator",
            tool="local",
            estimate_usd=Decimal("0"),
            idempotency_key=f"artifact:{spec.id}:v{spec.version}",
            provider="mock",
            fn=_make,
        )

    def attempt_external_test(self, spec: ExperimentSpec, artifact: dict[str, Any]) -> dict[str, Any]:
        return self.gateway.execute(
            experiment_id=spec.id,
            action_type="post_offer",
            actor="operator",
            tool="commercial_channel",
            estimate_usd=Decimal("1.00"),
            idempotency_key=f"post_offer:{spec.id}:v{spec.version}",
            provider="manual_channel",
            fn=lambda: {"posted": True, "artifact": artifact},
            detail={"channel": spec.acquisition_channel},
        )


class Critic:
    """One falsification/quality pass before external test. No debate loops."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._used: set[str] = set()

    def review_once(self, spec: ExperimentSpec, artifact: dict[str, Any]) -> dict[str, Any]:
        if spec.id in self._used:
            return {"skipped": True, "reason": "critic_already_used"}
        self._used.add(spec.id)
        issues: list[str] = []
        body = str(artifact.get("artifact", artifact))
        if len(body) < 40:
            issues.append("artifact_too_thin")
        if not spec.evidence_target:
            issues.append("missing_evidence_target")
        if "http" not in (spec.acquisition_channel + body).lower() and "board" not in spec.acquisition_channel.lower():
            issues.append("channel_may_be_vague")
        ok = len(issues) == 0 or issues == ["channel_may_be_vague"]
        self.ledger.add_evidence(
            experiment_id=spec.id,
            kind=EvidenceKind.QUALITY,
            source="critic",
            summary=sanitize_untrusted("pass" if ok else f"issues={issues}"),
            externally_observed=False,
            payload={"issues": issues, "ok": ok},
        )
        return {"ok": ok, "issues": issues}
