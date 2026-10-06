"""Bounded sibling specificity — payment-decision arms only."""

from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("data/gen1_sibling_specificity.json")


def run_sibling_specificity() -> dict:
    report = {
        "campaign": "GEN1_SIBLING_ARMS",
        "note": "Parallel arms, not Gen-2. Third slot unused — no distinct high-info hypothesis beyond payment_preflight vs resource_preflight.",
        "organisms": {
            "D_x402_payment_preflight": {
                "status": "PROCEED",
                "axis": "PAYMENT_DECISION",
                "adjacency": ("Adjacent to kenoodl x402-preflight ($0.01), agent402 wallet-readiness ($0.008), aura x402-preflight ($0.01)."),
                "differs": ("Takes normalized PAYMENT-REQUIRED + wallet; returns READY/BLOCKED + reason codes + next_action. No execution."),
                "fixed_price_usd": "0.008",
            },
            "E_x402_resource_preflight": {
                "status": "PROCEED",
                "axis": "PAYMENT_DECISION",
                "adjacency": ("Adjacent to agent402 x402-quote ($0.002) and seller-payability ($0.10); compresses fetch+parse+wallet check."),
                "differs": ("Fetches URL, parses 402, selects option, checks wallet → PAY/BLOCKED. No payment."),
                "fixed_price_usd": "0.009",
            },
            "F_third_slot": {
                "status": "PAUSE",
                "reason": "No genuinely distinct high-information hypothesis beyond D/E on this seam.",
            },
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    return report
