"""Gen-1 serve + report helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forage.gen1.app import MARGINAL_COST_USD, PRICES
from forage.gen1.metrics import build_organism_metrics
from forage.gen1.wallet import ensure_receiver_wallet

REPORT_PATH = Path("data/gen1_status.json")
EVENTS_PATH = Path("data/gen1/events.jsonl")
LISTED_PATH = Path("data/gen1/listed.json")
SELFTEST_PATH = Path("data/gen1_selftest.json")
SPEC_PATH = Path("data/gen1_specificity.json")


def load_events() -> list[dict[str, Any]]:
    if not EVENTS_PATH.exists():
        return []
    out = []
    for line in EVENTS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def build_status_report() -> dict[str, Any]:
    receiver = ensure_receiver_wallet()
    listed = json.loads(LISTED_PATH.read_text()) if LISTED_PATH.exists() else {}
    selftest = json.loads(SELFTEST_PATH.read_text()) if SELFTEST_PATH.exists() else {}
    specificity = json.loads(SPEC_PATH.read_text()) if SPEC_PATH.exists() else {}
    events = load_events()

    fulfills = [e for e in events if e.get("kind") == "FULFILL"]
    external_candidates = [e for e in fulfills if e.get("evidence_class") == "EXTERNAL_CANDIDATE"]
    # Never count SELF_TEST as revenue/demand
    external_buyers = sorted({e["payer"].lower() for e in external_candidates if e.get("payer")})

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "campaign": "GEN1_ONCHAIN_ORGANISMS",
        "receiver_address": receiver["address"],
        "network": os.environ.get("FORAGE_GEN1_NETWORK", "eip155:84532"),
        "prices": PRICES,
        "marginal_cost_usd": MARGINAL_COST_USD,
        "listed_at": listed.get("listed_at"),
        "public_base_url": listed.get("public_base_url"),
        "endpoints": [
            "/v1/wallet_payment_readiness",
            "/v1/oneshot_wallet_token_snapshot",
            "/v1/onchain_entity_brief",
            "/v1/x402_payment_preflight",
            "/v1/x402_resource_preflight",
        ],
        "self_test": {
            "report_path": str(SELFTEST_PATH) if SELFTEST_PATH.exists() else None,
            "payment_probe": selftest.get("payment_probe"),
            "rule": "SELF_TEST never promotes to E5/E6",
        },
        "specificity_path": str(SPEC_PATH) if SPEC_PATH.exists() else None,
        "specificity_status": {k: v.get("status") for k, v in (specificity.get("organisms") or {}).items()},
        "metrics": {
            "incoming_fulfill_events": len(fulfills),
            "external_candidate_events": len(external_candidates),
            "unique_external_buyers": len(external_buyers),
            "external_buyers": external_buyers,
            "verified_external_revenue_usd": "0",
            "actual_spend_usd": "0",
        },
        "per_organism": build_organism_metrics().get("organisms"),
        "gates": {
            "first_read_after_listed": "24h",
            "second_read": "72h",
            "hard_stop": "7d without external buyer",
        },
        "bootstrap_required": not bool(listed.get("public_base_url")),
        "note": "Sibling arms are parallel experiments, not Gen-2.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    return report
