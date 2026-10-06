"""CAPABILITY_RESOLVER V1 — Bazaar procurement card. No execution, no local index."""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from forage.gen1.discovery import CDP_SEARCH

ORGANISM = "capability_resolver"
EXAMPLE_NEED = "wallet usdc balance"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in {"the", "and", "for", "with"}}


def _amount_usd(accepts: list[dict[str, Any]]) -> Decimal | None:
    if not accepts:
        return None
    a = accepts[0]
    raw = a.get("amount")
    if raw is None:
        return None
    try:
        return Decimal(str(raw)) / Decimal(10**6)
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _score(need_tokens: set[str], item: dict[str, Any], usd: Decimal | None) -> float:
    blob = f"{item.get('resource') or ''} {item.get('description') or ''}"
    hit = _tokens(blob)
    overlap = len(need_tokens & hit) / max(len(need_tokens), 1)
    price_term = 1.0 / (1.0 + float(usd or 1))
    q = item.get("quality") or {}
    calls = 0.0
    try:
        calls = float(q.get("l30DaysTotalCalls") or 0)
    except (TypeError, ValueError):
        calls = 0.0
    trust = math.log1p(calls)
    return overlap * 4.0 + price_term + min(trust, 3.0) * 0.15


def _card(item: dict[str, Any], usd: Decimal | None, reason: str) -> dict[str, Any]:
    acc = (item.get("accepts") or [{}])[0] if isinstance(item.get("accepts"), list) else {}
    return {
        "endpoint": item.get("resource"),
        "price_usd": str(usd) if usd is not None else None,
        "amount": acc.get("amount"),
        "network": acc.get("network"),
        "pay_to": acc.get("payTo") or acc.get("pay_to"),
        "description": (item.get("description") or "")[:240],
        "reason": reason,
    }


def resolve_capability(
    need: str,
    *,
    max_price_usd: str | None = None,
    max_latency_ms: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    n = (need or "").strip()
    if len(n) < 3:
        return {
            "organism": ORGANISM,
            "verdict": "INCOMPATIBLE",
            "reason_codes": ["need_too_short"],
            "recommended": None,
            "fallback": None,
            "alternatives": [],
        }
    max_p: Decimal | None = None
    if max_price_usd is not None and str(max_price_usd).strip() != "":
        try:
            max_p = Decimal(str(max_price_usd))
        except InvalidOperation:
            return {
                "organism": ORGANISM,
                "verdict": "INCOMPATIBLE",
                "reason_codes": ["max_price_invalid"],
                "recommended": None,
                "fallback": None,
                "alternatives": [],
            }
    latency_note = "latency_unobserved"
    if max_latency_ms is not None and str(max_latency_ms).strip() != "":
        try:
            Decimal(str(max_latency_ms))
        except InvalidOperation:
            return {
                "organism": ORGANISM,
                "verdict": "INCOMPATIBLE",
                "reason_codes": ["max_latency_invalid"],
                "recommended": None,
                "fallback": None,
                "alternatives": [],
            }

    own = client or httpx.Client(timeout=45.0, follow_redirects=True)
    close = own is not client
    try:
        r = own.get(CDP_SEARCH, params={"query": n[:120], "limit": 20})
    except Exception as exc:  # noqa: BLE001
        if close:
            own.close()
        return {
            "organism": ORGANISM,
            "verdict": "UNAVAILABLE",
            "reason_codes": ["discovery_fetch_failed", str(exc)[:80]],
            "recommended": None,
            "fallback": None,
            "alternatives": [],
        }
    if close:
        own.close()
    if r.status_code != 200:
        return {
            "organism": ORGANISM,
            "verdict": "UNAVAILABLE",
            "reason_codes": ["discovery_http", f"got_{r.status_code}"],
            "recommended": None,
            "fallback": None,
            "alternatives": [],
        }
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    items = [it for it in (data.get("resources") or data.get("items") or []) if isinstance(it, dict)]
    need_tokens = _tokens(n)
    ranked: list[tuple[float, Decimal | None, dict[str, Any]]] = []
    for it in items:
        acc = it.get("accepts") or []
        accepts = [a for a in acc if isinstance(a, dict)] if isinstance(acc, list) else []
        usd = _amount_usd(accepts)
        if max_p is not None and usd is not None and usd > max_p:
            continue
        ranked.append((_score(need_tokens, it, usd), usd, it))
    ranked.sort(key=lambda t: (-t[0], t[1] if t[1] is not None else Decimal("999")))
    if not ranked:
        return {
            "organism": ORGANISM,
            "verdict": "NONE",
            "reason_codes": ["no_compatible_under_constraints", latency_note],
            "recommended": None,
            "fallback": None,
            "alternatives": [],
            "candidates_considered": len(items),
        }
    rec = _card(ranked[0][2], ranked[0][1], "highest_schema_price_trust_score")
    fb = _card(ranked[1][2], ranked[1][1], "next_best_score") if len(ranked) > 1 else None
    alts = [_card(it, usd, "alternative") for _, usd, it in ranked[2:5]]
    return {
        "organism": ORGANISM,
        "verdict": "RECOMMEND",
        "reason_codes": [latency_note],
        "recommended": rec,
        "fallback": fb,
        "alternatives": alts,
        "candidates_considered": len(items),
        "candidates_after_price_filter": len(ranked),
    }
