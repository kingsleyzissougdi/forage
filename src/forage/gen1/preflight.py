"""Payment-decision siblings: preflight only — never execute payment."""

from __future__ import annotations

import base64
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from forage.gen1 import rpc as R

# Known USDC (6 decimals) by CAIP-2 network for balance checks
USDC_BY_NETWORK: dict[str, str] = {
    "eip155:1": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}


def _parse_payment_required(raw: Any) -> dict[str, Any]:
    if raw is None:
        raise ValueError("payment_required_missing")
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            return json.loads(s)
        # base64 PAYMENT-REQUIRED header value
        return json.loads(base64.b64decode(s))
    if isinstance(raw, dict):
        return raw
    raise ValueError("payment_required_invalid_type")


def _accepts(pr: dict[str, Any]) -> list[dict[str, Any]]:
    acc = pr.get("accepts") or []
    return [a for a in acc if isinstance(a, dict)]


def _amount_usd(amount_raw: str | None, decimals: int = 6) -> Decimal | None:
    if amount_raw is None:
        return None
    try:
        return Decimal(str(amount_raw)) / (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError):
        return None


def _select_option(
    accepts: list[dict[str, Any]],
    *,
    max_spend_usd: Decimal | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    if not accepts:
        return None, ["no_accepts"]
    # Prefer exact + known USDC networks first
    ranked = sorted(
        accepts,
        key=lambda a: (
            0 if a.get("scheme") == "exact" else 1,
            0 if a.get("network") in USDC_BY_NETWORK else 1,
            int(a.get("amount") or "0"),
        ),
    )
    for opt in ranked:
        amt = _amount_usd(str(opt.get("amount")) if opt.get("amount") is not None else None)
        if max_spend_usd is not None and amt is not None and amt > max_spend_usd:
            reasons.append("option_exceeds_max_spend")
            continue
        if opt.get("network") not in R.RPC_BY_CHAIN and opt.get("network") not in USDC_BY_NETWORK:
            reasons.append(f"unsupported_network:{opt.get('network')}")
            continue
        return opt, reasons
    return None, reasons or ["no_compatible_option"]


def _wallet_asset_balance(wallet: str, network: str, asset: str) -> tuple[int | None, str | None]:
    rpc = R.rpc_for_chain(network)
    bal = R.erc20_balance(asset, wallet, rpc)
    sym = R.erc20_symbol(asset, rpc)
    return bal, sym


def x402_payment_preflight(
    wallet: str,
    payment_required: Any,
    *,
    max_spend_usd: str | None = None,
) -> dict[str, Any]:
    """Decide READY/BLOCKED from wallet + PAYMENT-REQUIRED — no execution."""
    reasons: list[str] = []
    max_spend: Decimal | None = None
    if max_spend_usd is not None and str(max_spend_usd).strip() != "":
        try:
            max_spend = Decimal(str(max_spend_usd))
        except InvalidOperation:
            return {
                "organism": "x402_payment_preflight",
                "verdict": "BLOCKED",
                "reason_codes": ["max_spend_invalid"],
                "next_action": {"type": "FIX_INPUT", "field": "max_spend_usd"},
            }

    try:
        pr = _parse_payment_required(payment_required)
    except Exception as exc:  # noqa: BLE001
        return {
            "organism": "x402_payment_preflight",
            "verdict": "BLOCKED",
            "reason_codes": ["payment_required_parse_failed", str(exc)[:80]],
            "next_action": {"type": "FIX_INPUT", "field": "payment_required"},
        }

    accepts = _accepts(pr)
    opt, select_reasons = _select_option(accepts, max_spend_usd=max_spend)
    reasons.extend(select_reasons)
    if opt is None:
        return {
            "organism": "x402_payment_preflight",
            "verdict": "BLOCKED",
            "reason_codes": reasons or ["no_option"],
            "compatible_network": None,
            "required": None,
            "available_payment_balance": None,
            "next_action": {"type": "ABORT", "detail": "no_compatible_payment_option"},
            "accepts_count": len(accepts),
        }

    network = str(opt.get("network"))
    asset = str(opt.get("asset") or "")
    amount_raw = str(opt.get("amount") or "0")
    amount_usd = _amount_usd(amount_raw)
    bal, sym = _wallet_asset_balance(wallet, network, asset)
    native = None
    try:
        native = R.eth_get_balance(wallet, R.rpc_for_chain(network))
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"native_balance_unavailable:{exc}")

    ready = True
    if bal is None:
        ready = False
        reasons.append("payment_balance_unavailable")
    elif int(bal) < int(amount_raw):
        ready = False
        reasons.append("insufficient_payment_token")
    if native is not None and native == 0:
        # soft: many facilitators sponsor gas; still surface
        reasons.append("native_gas_balance_zero")

    if max_spend is not None and amount_usd is not None and amount_usd > max_spend:
        ready = False
        reasons.append("exceeds_max_spend")

    verdict = "READY" if ready else "BLOCKED"
    next_action: dict[str, Any]
    if verdict == "READY":
        next_action = {
            "type": "PAY",
            "scheme": opt.get("scheme"),
            "network": network,
            "asset": asset,
            "amount": amount_raw,
            "pay_to": opt.get("payTo") or opt.get("pay_to"),
        }
    else:
        next_action = {
            "type": "FUND_OR_ABORT",
            "required_amount": amount_raw,
            "asset": asset,
            "network": network,
        }

    return {
        "organism": "x402_payment_preflight",
        "verdict": verdict,
        "compatible_network": network,
        "required": {
            "scheme": opt.get("scheme"),
            "asset": asset,
            "symbol": sym,
            "amount": amount_raw,
            "amount_usd": str(amount_usd) if amount_usd is not None else None,
            "pay_to": opt.get("payTo") or opt.get("pay_to"),
        },
        "available_payment_balance": {
            "raw": str(bal) if bal is not None else None,
            "symbol": sym,
            "native_wei": str(native) if native is not None else None,
        },
        "reason_codes": reasons,
        "next_action": next_action,
        "accepts_count": len(accepts),
    }


def x402_resource_preflight(
    wallet: str,
    resource_url: str,
    *,
    max_spend_usd: str | None = None,
    method: str = "GET",
) -> dict[str, Any]:
    """Fetch resource, parse 402, check wallet — return PAY/BLOCKED. No payment."""
    if not resource_url.startswith("http://") and not resource_url.startswith("https://"):
        return {
            "organism": "x402_resource_preflight",
            "verdict": "INCOMPATIBLE",
            "reason_codes": ["resource_url_invalid"],
            "next_action": {"type": "FIX_INPUT", "field": "resource_url"},
        }

    try:
        with httpx.Client(timeout=40.0, follow_redirects=True) as client:
            r = client.request(method.upper(), resource_url)
    except Exception as exc:  # noqa: BLE001
        return {
            "organism": "x402_resource_preflight",
            "verdict": "UNAVAILABLE",
            "reason_codes": ["resource_fetch_failed", str(exc)[:100]],
            "next_action": {"type": "RETRY_OR_ABORT"},
            "http_status": None,
        }

    if r.status_code != 402:
        return {
            "organism": "x402_resource_preflight",
            "verdict": "UNAVAILABLE",
            "reason_codes": ["expected_402", f"got_{r.status_code}"],
            "http_status": r.status_code,
            "next_action": {"type": "ABORT", "detail": "resource_not_payment_gated_or_already_open"},
        }

    pr_hdr = r.headers.get("payment-required") or r.headers.get("PAYMENT-REQUIRED")
    body_pr = None
    if not pr_hdr:
        try:
            body_pr = r.json()
        except Exception:
            body_pr = None
        if not isinstance(body_pr, dict) or not body_pr.get("accepts"):
            return {
                "organism": "x402_resource_preflight",
                "verdict": "UNAVAILABLE",
                "reason_codes": ["payment_required_missing"],
                "http_status": 402,
                "next_action": {"type": "ABORT"},
            }
        payment_required: Any = body_pr
    else:
        payment_required = pr_hdr

    inner = x402_payment_preflight(wallet, payment_required, max_spend_usd=max_spend_usd)
    out = {**inner, "organism": "x402_resource_preflight", "http_status": 402, "resource_url": resource_url}
    codes = [str(c) for c in (inner.get("reason_codes") or [])]
    if inner.get("verdict") == "READY":
        out["verdict"] = "PAY"
        na = dict(inner.get("next_action") or {})
        na["type"] = "PAY"
        na["resource_url"] = resource_url
        out["next_action"] = na
        return out
    incompatible = any(
        c in ("no_accepts", "no_compatible_option", "no_option", "payment_required_parse_failed", "max_spend_invalid") or c.startswith("unsupported_network:")
        for c in codes
    )
    out["verdict"] = "INCOMPATIBLE" if incompatible else "BLOCKED"
    out["reason_codes"] = codes
    return out
