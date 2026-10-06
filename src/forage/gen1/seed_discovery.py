"""SELF_TEST_DISCOVERY_SEED — controlled buyer pays once via CDP facilitator.

Never counts as E5/E6/revenue.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eth_account import Account

from forage.gen1.denylist import register_controlled
from forage.gen1.wallet import ensure_receiver_wallet

SEED_REPORT = Path("data/gen1/self_test_discovery_seed.json")
BUYER_PATH = Path("data/gen1/seed_buyer_wallet.json")
NETWORK = "eip155:84532"  # Base Sepolia


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


def ensure_seed_buyer() -> dict[str, str]:
    if BUYER_PATH.exists():
        data = json.loads(BUYER_PATH.read_text())
        register_controlled(data["address"])
        return data
    Account.enable_unaudited_hdwallet_features()
    acct = Account.create()
    data = {
        "address": acct.address,
        "private_key": acct.key.hex(),
        "warning": "SELF_TEST buyer only; NEVER count as E5/E6; NEVER commit",
    }
    BUYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUYER_PATH.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(BUYER_PATH, 0o600)
    except Exception:
        pass
    register_controlled(acct.address)
    return data


async def faucet_usdc(address: str) -> dict[str, Any]:
    """Request Base Sepolia USDC via CDP faucet (test funds only)."""
    from cdp import CdpClient

    out: dict[str, Any] = {"address": address, "network": "base-sepolia", "token": "usdc"}
    async with CdpClient() as cdp:
        # eth for gas if needed by some paths; USDC for payment
        try:
            eth_tx = await cdp.evm.request_faucet(address=address, network="base-sepolia", token="eth")
            out["eth_tx"] = eth_tx
        except Exception as exc:  # noqa: BLE001
            out["eth_error"] = str(exc)
        try:
            usdc_tx = await cdp.evm.request_faucet(address=address, network="base-sepolia", token="usdc")
            out["usdc_tx"] = usdc_tx
        except Exception as exc:  # noqa: BLE001
            out["usdc_error"] = str(exc)
    return out


async def paid_get(url: str, private_key: str) -> dict[str, Any]:
    from eth_account import Account as EthAccount
    from x402 import x402Client
    from x402.http.clients import wrapHttpxWithPayment
    from x402.mechanisms.evm.exact import register_exact_evm_client

    acct = EthAccount.from_key(private_key)
    client = x402Client()
    register_exact_evm_client(client, acct, networks=NETWORK)

    async with wrapHttpxWithPayment(client, timeout=90.0) as http:
        r = await http.get(url)
        headers = {k.lower(): v for k, v in r.headers.items()}
        return {
            "url": url,
            "status": r.status_code,
            "ok": r.status_code == 200,
            "payment_response_present": "payment-response" in headers or "x-payment-response" in headers,
            "body_organism": (r.json().get("organism") if r.headers.get("content-type", "").startswith("application/json") else None),
            "evidence_class": "SELF_TEST",
        }


async def run_seed(public_base_url: str) -> dict[str, Any]:
    _load_dotenv()
    if not os.environ.get("CDP_API_KEY_ID") or not os.environ.get("CDP_API_KEY_SECRET"):
        raise RuntimeError("CDP_API_KEY_ID/SECRET required")

    receiver = ensure_receiver_wallet()
    buyer = ensure_seed_buyer()
    register_controlled(receiver["address"])
    register_controlled(buyer["address"])

    faucet = await faucet_usdc(buyer["address"])
    # brief wait for faucet settle
    await asyncio.sleep(8)

    # ONE minimum seed: cheapest route B ($0.004). If Bazaar only indexes the paid
    # resource URL, follow-up seeds for A/C are still SELF_TEST and recorded separately.
    vitalik = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    seed_url = f"{public_base_url.rstrip('/')}/v1/oneshot_wallet_token_snapshot?address={vitalik}&tokens={usdc}"
    payment = await paid_get(seed_url, buyer["private_key"])

    report: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "SELF_TEST_DISCOVERY_SEED",
        "evidence_class": "SELF_TEST",
        "rule": "SELF_TEST never promotes to E5/E6 or revenue",
        "network": NETWORK,
        "receiver": receiver["address"],
        "buyer": buyer["address"],
        "buyer_denylisted": True,
        "faucet": faucet,
        "payment": payment,
        "public_base_url": public_base_url.rstrip("/"),
    }
    SEED_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SEED_REPORT.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    public = Path("data/gen1/public_url.txt").read_text().strip()
    report = asyncio.run(run_seed(public))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
