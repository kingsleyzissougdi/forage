"""Intent vs indexer/schema classification for sibling ACCESS logs."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from forage.gen1.ch_traffic import ua_is_generic_crawler, ua_is_indexer

EXAMPLES: dict[str, dict[str, str]] = {
    "oneshot_wallet_token_snapshot": {
        "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        "tokens": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    },
    "wallet_payment_readiness": {"address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"},
    "onchain_entity_brief": {"address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"},
    "x402_resource_preflight": {"resource_url": "https://kenoodl.com/preflight"},
    "call_before_paying_x402": {"resource_url": "https://kenoodl.com/preflight"},
    "capability_resolver": {"need": "wallet usdc balance"},
    "x402_failover_pick": {"need": "wallet usdc balance"},
    "x402_budget_check": {"need": "wallet usdc balance"},
    "x402_option_compat": {
        "wallet": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        "amount": "10000",
    },
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "oneshot_wallet_token_snapshot": ("address", "tokens"),
    "wallet_payment_readiness": ("address",),
    "onchain_entity_brief": ("address",),
    "x402_resource_preflight": ("wallet", "resource_url"),
    "call_before_paying_x402": ("wallet", "resource_url"),
    "capability_resolver": ("need",),
    "x402_failover_pick": ("need",),
    "x402_budget_check": ("need", "max_price"),
    "x402_option_compat": ("wallet", "amount"),
}

INTENT_QUERIES: dict[str, list[str]] = {
    "oneshot_wallet_token_snapshot": [
        "wallet token snapshot",
        "compressed erc20 balances",
        "oneshot wallet token snapshot",
        "wallet holdings one call",
        "token balances for an address",
    ],
    "wallet_payment_readiness": [
        "can this wallet pay gas and usdc",
        "wallet payment readiness",
        "ready to send usdc on base",
        "gas and usdc check before action",
        "wallet can pay x402",
    ],
    "x402_resource_preflight": [
        "x402 resource preflight",
        "parse 402 payment required",
        "wallet can pay this x402 url",
        "preflight x402 resource",
        "PAYMENT-REQUIRED check wallet",
    ],
    "call_before_paying_x402": [
        "call before paying x402",
        "check before I pay an x402 url",
        "should I pay this 402",
        "x402 pay or blocked for wallet",
        "preflight before sending usdc",
    ],
    "capability_resolver": [
        "find paid x402 tool",
        "capability resolver",
        "recommend x402 endpoint for a need",
        "which bazaar tool should I call",
        "procure a paid agent tool",
    ],
    "x402_failover_pick": [
        "x402 failover",
        "backup paid tool if seller down",
        "primary and fallback x402",
        "failover pick bazaar",
        "alternative x402 seller",
    ],
    "x402_budget_check": [
        "x402 budget check",
        "can I afford this bazaar tool",
        "usdc budget for n calls",
        "cheapest x402 under budget",
        "cost planning paid tools",
    ],
    "x402_option_compat": [
        "x402 option compatibility",
        "wallet funded for this network asset amount",
        "compatible with payment option",
        "schema compat x402 wallet",
        "enough usdc on base for amount",
    ],
    "onchain_entity_brief": [
        "onchain entity brief",
        "is this address a contract",
        "eoa vs contract plus holdings",
        "short on-chain facts for address",
        "entity brief usdc usdt weth",
    ],
}


def _qs(query: str | None) -> dict[str, str]:
    return {k: (v[0] if v else "") for k, v in parse_qs(query or "", keep_blank_values=True).items()}


def _norm(v: str) -> str:
    return v.replace("+", " ").strip().rstrip("/").lower()


def schema_example(org: str, query: str | None) -> bool:
    q = _qs(query)
    ex = EXAMPLES.get(org) or {}
    if not ex:
        return False
    for k, ev in ex.items():
        got = _norm(q.get(k, ""))
        if got and got == _norm(ev):
            return True
    return False


def valid_nonempty(org: str, query: str | None) -> bool:
    q = _qs(query)
    keys = REQUIRED.get(org) or ()
    if not keys:
        return False
    for k in keys:
        v = (q.get(k) or "").strip()
        if not v:
            return False
        if k in ("wallet", "address") and not (v.startswith("0x") and len(v) == 42):
            return False
    return True


def classify_sib_access(event: dict[str, Any]) -> dict[str, Any]:
    org = str(event.get("organism") or "")
    ua = event.get("user_agent") or ""
    indexer = bool(event.get("crawler_ua")) or ua_is_indexer(ua) or ua_is_generic_crawler(ua)
    query = event.get("query")
    empty = not _qs(query)
    example = schema_example(org, query)
    valid = valid_nonempty(org, query)
    intentful = bool(valid and not example and not indexer and not empty)
    if indexer:
        bucket = "indexer_or_crawler"
    elif empty:
        bucket = "empty_query"
    elif example:
        bucket = "schema_example"
    elif intentful:
        bucket = "intentful"
    elif valid:
        bucket = "valid_but_crawler_or_other"
    else:
        bucket = "other_query"
    return {
        "bucket": bucket,
        "indexer_ua": indexer,
        "empty_query": empty,
        "schema_example": example,
        "valid_input": valid,
        "intentful": intentful,
    }
