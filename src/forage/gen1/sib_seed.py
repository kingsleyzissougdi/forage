"""SELF_TEST seed for sibling SKUs. Never E5/E6. Does not touch CH listed_at."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from forage.gen1.ch_seed_discovery import paid_get
from forage.gen1.denylist import register_controlled
from forage.gen1.seed_discovery import ensure_seed_buyer
from forage.gen1.wallet import ensure_receiver_wallet

SEED_REPORT = Path("data/gen1_sib/self_test_discovery_seed.json")
VITALIK = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _load_dotenv() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _seed_urls(base: str) -> dict[str, str]:
    b = base.rstrip("/")

    def u(path: str, **params: str) -> str:
        return f"{b}{path}?{urlencode(params)}"

    return {
        "oneshot_wallet_token_snapshot": u(
            "/v1/oneshot_wallet_token_snapshot",
            address=VITALIK,
            tokens=USDC,
            chain="eip155:8453",
        ),
        "wallet_payment_readiness": u("/v1/wallet_payment_readiness", address=VITALIK, chain="eip155:8453"),
        "x402_resource_preflight": u(
            "/v1/x402_resource_preflight",
            wallet=VITALIK,
            resource_url="https://kenoodl.com/preflight",
            max_spend_usd="0.05",
        ),
        "call_before_paying_x402": u(
            "/v1/call_before_paying_x402",
            wallet=VITALIK,
            resource_url="https://kenoodl.com/preflight",
            max_spend_usd="0.05",
        ),
        "capability_resolver": u("/v1/capability_resolver", need="wallet usdc balance", max_price="0.05"),
        "x402_failover_pick": u("/v1/x402_failover_pick", need="wallet usdc balance", max_price="0.05"),
        "x402_budget_check": u(
            "/v1/x402_budget_check",
            need="wallet usdc balance",
            max_price="0.05",
            n_calls="3",
        ),
        "x402_option_compat": u(
            "/v1/x402_option_compat",
            wallet=VITALIK,
            network="eip155:8453",
            asset=USDC_BASE,
            amount="10000",
        ),
        "onchain_entity_brief": u("/v1/onchain_entity_brief", address=VITALIK, chain="eip155:8453"),
    }


async def run_sib_seed(public_base_url: str, *, network: str | None = None) -> dict[str, Any]:
    _load_dotenv()
    net = network or os.environ.get("FORAGE_GEN1_NETWORK", "eip155:8453")
    receiver = ensure_receiver_wallet()
    buyer = ensure_seed_buyer()
    register_controlled(receiver["address"])
    register_controlled(buyer["address"])
    urls = _seed_urls(public_base_url)
    payments: dict[str, Any] = {}
    for org, url in urls.items():
        payments[org] = await paid_get(url, buyer["private_key"], net)
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "SELF_TEST_DISCOVERY_SEED",
        "evidence_class": "SELF_TEST",
        "rule": "SELF_TEST never promotes to E5/E6 or revenue",
        "network": net,
        "buyer": buyer["address"],
        "buyer_denylisted": True,
        "payments": payments,
        "public_base_url": public_base_url.rstrip("/"),
    }
    SEED_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SEED_REPORT.write_text(json.dumps(report, indent=2, default=str))
    return report
