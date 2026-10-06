"""Thin sibling jobs sharing existing RPC/Bazaar/preflight backends."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from forage.gen1.capability_resolver import resolve_capability
from forage.gen1.preflight import USDC_BY_NETWORK, _wallet_asset_balance


def x402_failover_pick(need: str, *, max_price: str | None = None) -> dict[str, Any]:
    inner = resolve_capability(need, max_price_usd=max_price)
    out = {
        "organism": "x402_failover_pick",
        "verdict": inner.get("verdict"),
        "reason_codes": list(inner.get("reason_codes") or []) + ["failover_card"],
        "primary": inner.get("recommended"),
        "backup": inner.get("fallback"),
        "candidates_considered": inner.get("candidates_considered"),
    }
    if inner.get("recommended") and inner.get("fallback"):
        out["verdict"] = "FAILOVER_READY"
    elif inner.get("recommended"):
        out["verdict"] = "PRIMARY_ONLY"
        out["reason_codes"].append("no_backup")
    return out


def x402_budget_check(need: str, *, max_price: str, n_calls: str = "1") -> dict[str, Any]:
    try:
        budget = Decimal(str(max_price))
        n = int(n_calls)
    except (InvalidOperation, ValueError):
        return {
            "organism": "x402_budget_check",
            "verdict": "INCOMPATIBLE",
            "reason_codes": ["budget_or_n_invalid"],
            "affordable": False,
        }
    if n < 1 or n > 100:
        return {
            "organism": "x402_budget_check",
            "verdict": "INCOMPATIBLE",
            "reason_codes": ["n_calls_out_of_range"],
            "affordable": False,
        }
    inner = resolve_capability(need, max_price_usd=str(budget))
    rec = inner.get("recommended") or {}
    unit = None
    try:
        unit = Decimal(str(rec.get("price_usd"))) if rec.get("price_usd") is not None else None
    except InvalidOperation:
        unit = None
    total = (unit * n) if unit is not None else None
    affordable = total is not None and total <= budget
    return {
        "organism": "x402_budget_check",
        "verdict": "AFFORDABLE" if affordable else "OVER_BUDGET" if unit is not None else inner.get("verdict") or "NONE",
        "reason_codes": list(inner.get("reason_codes") or []),
        "recommended": rec or None,
        "unit_price_usd": str(unit) if unit is not None else None,
        "n_calls": n,
        "total_usd": str(total) if total is not None else None,
        "budget_usd": str(budget),
        "affordable": affordable,
    }


def x402_option_compat(
    wallet: str,
    *,
    network: str,
    asset: str,
    amount: str,
) -> dict[str, Any]:
    if network not in USDC_BY_NETWORK and not network.startswith("eip155:"):
        return {
            "organism": "x402_option_compat",
            "verdict": "INCOMPATIBLE",
            "reason_codes": [f"unsupported_network:{network}"],
        }
    try:
        need = int(amount)
    except ValueError:
        return {
            "organism": "x402_option_compat",
            "verdict": "INCOMPATIBLE",
            "reason_codes": ["amount_invalid"],
        }
    bal, sym = _wallet_asset_balance(wallet, network, asset)
    if bal is None:
        return {
            "organism": "x402_option_compat",
            "verdict": "UNAVAILABLE",
            "reason_codes": ["payment_balance_unavailable"],
            "symbol": sym,
        }
    ok = int(bal) >= need
    return {
        "organism": "x402_option_compat",
        "verdict": "COMPATIBLE" if ok else "SHORT",
        "reason_codes": [] if ok else ["insufficient_payment_token"],
        "symbol": sym,
        "available_raw": str(bal),
        "required_raw": str(need),
        "network": network,
        "asset": asset,
    }
