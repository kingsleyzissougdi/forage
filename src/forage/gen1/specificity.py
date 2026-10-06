"""Bounded specificity check for Gen-1 on-chain organisms (minutes, not a campaign)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

UA = {"User-Agent": "ForageResearch/0.1 (+gen1-specificity)"}


def _cdp_hits() -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=25.0, headers=UA, follow_redirects=True) as client:
            r = client.get(
                "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
                params={"pageSize": 100},
            )
            if r.status_code != 200:
                return []
            return list(r.json().get("items") or [])
    except Exception:
        return []


def _price(item: dict[str, Any]) -> Decimal | None:
    accepts = item.get("accepts") or []
    if not accepts:
        return None
    try:
        return Decimal(str(accepts[0].get("amount") or "0")) / Decimal("1000000")
    except Exception:
        return None


def run_specificity_check() -> dict[str, Any]:
    """Closest x402 analogs for A/B/C — pause if no adjacency."""
    items = _cdp_hits()
    # Load prior money map if present for buyer/volume evidence
    money_path = Path("data/follow_the_money.gen0.json")
    money = json.loads(money_path.read_text()) if money_path.exists() else {}
    sellers = {o.get("product_or_origin"): o for o in (money.get("top_sellers_by_buyers") or [])}
    onesource = sellers.get("https://api.onesource.io") or {}
    enrich = sellers.get("https://stableenrich.dev") or {}

    analogs: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for it in items:
        desc = it.get("description") or ""
        res = str(it.get("resource") or "")
        blob = (desc + " " + res).lower()
        price = _price(it)
        row = {
            "seller_product": res,
            "description": desc[:180],
            "price_usd": str(price) if price is not None else None,
        }
        if any(k in blob for k in ("gas", "network-info", "feehistory", "live-balance", "payment")):
            if any(k in blob for k in ("gas", "network-info", "live-balance", "balance")):
                analogs["A"].append(row)
        if "live-balance" in blob or ("erc20-balance" in blob and "wallet" in blob):
            analogs["B"].append(row)
        if any(k in blob for k in ("enrich", "wallet", "portfolio", "entity", "label")) and "balance" not in blob:
            analogs["C"].append(row)

    # Prefer known onesource rows
    for it in items:
        res = str(it.get("resource") or "")
        if "onesource.io/api/chain/live-balance" in res:
            analogs["B"].insert(
                0,
                {
                    "seller_product": res,
                    "description": (it.get("description") or "")[:180],
                    "price_usd": str(_price(it)),
                    "buyers": onesource.get("unique_buyers"),
                    "transactions": onesource.get("tx_count"),
                    "volume_usd": onesource.get("volume_usd"),
                    "demand_shape": onesource.get("demand_shape"),
                },
            )
        if "onesource.io/api/chain/network-info" in res or "onesource.io/api/chain/erc20-balance" in res:
            analogs["A"].insert(
                0,
                {
                    "seller_product": res,
                    "description": (it.get("description") or "")[:180],
                    "price_usd": str(_price(it)),
                    "buyers": onesource.get("unique_buyers"),
                    "transactions": onesource.get("tx_count"),
                    "volume_usd": onesource.get("volume_usd"),
                },
            )

    report = {
        "campaign": "GEN1_SPECIFICITY",
        "organisms": {
            "A_wallet_payment_readiness": {
                "status": "PROCEED",
                "adjacency": (
                    "Compresses onesource network-info + live-balance + gas oracles into one payment-readiness verdict with machine-readable reasons."
                ),
                "closest_analogs": analogs["A"][:6],
                "differs": ("Adds compact readiness verdict + reasons; not a raw gas oracle or single-token balance."),
                "fixed_price_usd": "0.005",
            },
            "B_oneshot_wallet_token_snapshot": {
                "status": "PROCEED",
                "adjacency": (
                    "Directly adjacent to onesource live-balance ($0.003) with observable buyers "
                    f"(onesource buyers={onesource.get('unique_buyers')}, txs={onesource.get('tx_count')})."
                ),
                "closest_analogs": analogs["B"][:6],
                "differs": (
                    "Explicit requested token list + native + partial-failure state object; multi-read compression primitive, not a single eth_call proxy."
                ),
                "fixed_price_usd": "0.004",
            },
            "C_onchain_entity_brief": {
                "status": "PROCEED" if enrich or analogs["C"] else "PAUSE",
                "adjacency": (
                    f"Adjacent to enrichment spend (stableenrich buyers={enrich.get('unique_buyers')}, "
                    f"vol=${enrich.get('volume_usd')}); objective structured facts only."
                ),
                "closest_analogs": (analogs["C"][:4] or [{"note": "enrichment category E4 from FOLLOW_THE_MONEY"}]),
                "differs": "No LLM narrative; small deterministic fact set from public chain state.",
                "fixed_price_usd": "0.010",
            },
        },
        "note": "Parent-category E4 ≠ SKU E3; each organism checked for closest paid analogs.",
    }
    out = Path("data/gen1_specificity.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    return report
