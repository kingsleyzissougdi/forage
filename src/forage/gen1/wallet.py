"""Isolated Gen-1 receiving wallet (never commit private key)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from eth_account import Account

from forage.gen1.denylist import register_controlled

WALLET_PATH = Path("data/gen1/receiver_wallet.json")


def ensure_receiver_wallet() -> dict[str, str]:
    """Return {address, ...}; create if missing. Private key only on disk under data/gen1/."""
    env_addr = os.environ.get("FORAGE_GEN1_PAY_TO", "").strip()
    if env_addr:
        register_controlled(env_addr)
        return {"address": env_addr, "source": "env"}

    if WALLET_PATH.exists():
        data = json.loads(WALLET_PATH.read_text())
        register_controlled(data["address"])
        return {"address": data["address"], "source": "file"}

    Account.enable_unaudited_hdwallet_features()
    acct = Account.create()
    WALLET_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "address": acct.address,
        "private_key": acct.key.hex(),
        "warning": "NEVER commit this file; NEVER log private_key",
    }
    WALLET_PATH.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(WALLET_PATH, 0o600)
    except Exception:
        pass
    register_controlled(acct.address)
    return {"address": acct.address, "source": "generated"}
