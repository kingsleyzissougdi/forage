"""Gen-1 self-test: fulfillment + 402 plumbing. SELF_TEST ≠ E5/E6."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from forage.gen1.denylist import evidence_class, register_controlled
from forage.gen1.organisms import (
    onchain_entity_brief,
    oneshot_wallet_token_snapshot,
    wallet_payment_readiness,
)
from forage.gen1.rpc import USDC, USDT, WETH
from forage.gen1.wallet import ensure_receiver_wallet

VITALIK = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
REPORT_PATH = Path("data/gen1_selftest.json")


def run_selftest(base_url: str | None = None) -> dict[str, Any]:
    receiver = ensure_receiver_wallet()
    register_controlled(receiver["address"])

    t_ready = wallet_payment_readiness(VITALIK, tokens=[USDC, USDT])
    t_snap = oneshot_wallet_token_snapshot(VITALIK, tokens=[USDC, USDT, WETH])
    t_brief = onchain_entity_brief(VITALIK)

    fulfillment = {
        "wallet_payment_readiness": {
            "ok": bool(t_ready.get("as_of_block")),
            "ready": t_ready.get("ready"),
            "evidence_class": evidence_class(receiver["address"], forced_self_test=True),
        },
        "oneshot_wallet_token_snapshot": {
            "ok": bool(t_snap.get("as_of_block")),
            "partial_failure": t_snap.get("partial_failure"),
            "evidence_class": "SELF_TEST",
        },
        "onchain_entity_brief": {
            "ok": bool(t_brief.get("as_of_block")),
            "account_type": (t_brief.get("facts") or {}).get("account_type"),
            "evidence_class": "SELF_TEST",
        },
    }

    payment_probe: dict[str, Any] = {"skipped": True}
    if base_url:
        import base64

        payment_probe = {"skipped": False, "checks": []}
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            for path in (
                f"/v1/wallet_payment_readiness?address={VITALIK}",
                f"/v1/oneshot_wallet_token_snapshot?address={VITALIK}&tokens={USDC},{USDT}",
                f"/v1/onchain_entity_brief?address={VITALIK}",
            ):
                r = client.get(base_url.rstrip("/") + path)
                pr_hdr = r.headers.get("payment-required")
                decoded: dict[str, Any] = {}
                if pr_hdr:
                    try:
                        decoded = json.loads(base64.b64decode(pr_hdr))
                    except Exception as exc:  # noqa: BLE001
                        decoded = {"decode_error": str(exc)}
                accepts = decoded.get("accepts") or []
                payment_probe["checks"].append(
                    {
                        "path": path.split("?")[0],
                        "status": r.status_code,
                        "expect_402_without_payment": r.status_code == 402,
                        "has_payment_required_header": bool(pr_hdr),
                        "network": (accepts[0] or {}).get("network") if accepts else None,
                        "amount": (accepts[0] or {}).get("amount") if accepts else None,
                        "pay_to": (accepts[0] or {}).get("payTo") if accepts else None,
                        "bazaar": "bazaar" in (decoded.get("extensions") or {}),
                    }
                )

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "receiver_address": receiver["address"],
        "receiver_source": receiver.get("source"),
        "fulfillment_self_test": fulfillment,
        "payment_probe": payment_probe,
        "rule": "SELF_TEST never promotes organism to E5/E6",
        "external_buyers": 0,
        "verified_external_revenue_usd": "0",
        "actual_spend_usd": "0",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    return report
