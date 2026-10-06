"""Minimal public Ethereum JSON-RPC helpers for Gen-1 organisms."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_RPC = os.environ.get("FORAGE_GEN1_RPC_URL", "https://ethereum.publicnode.com")

# Data-plane RPCs (payment network is separate; default pay rail is Base Sepolia).
RPC_BY_CHAIN: dict[str, str] = {
    "eip155:1": os.environ.get("FORAGE_GEN1_RPC_URL", "https://ethereum.publicnode.com"),
    "eip155:8453": os.environ.get("FORAGE_GEN1_BASE_RPC_URL", "https://base.publicnode.com"),
    "eip155:84532": os.environ.get("FORAGE_GEN1_BASE_SEPOLIA_RPC_URL", "https://base-sepolia.publicnode.com"),
}

# Common tokens (Ethereum mainnet) for readiness defaults
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


def rpc_for_chain(chain: str) -> str:
    return RPC_BY_CHAIN.get(chain, DEFAULT_RPC)


BALANCE_OF_SELECTOR = "0x70a08231"
DECIMALS_SELECTOR = "0x313ce567"
SYMBOL_SELECTOR = "0x95d89b41"


def _pad_addr(addr: str) -> str:
    a = addr.lower().replace("0x", "")
    return a.rjust(64, "0")


def rpc_call(method: str, params: list[Any], rpc_url: str = DEFAULT_RPC) -> Any:
    with httpx.Client(timeout=25.0) as client:
        r = client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result")


def eth_get_balance(address: str, rpc_url: str = DEFAULT_RPC) -> int:
    result = rpc_call("eth_getBalance", [address, "latest"], rpc_url)
    return int(result, 16)


def eth_get_code(address: str, rpc_url: str = DEFAULT_RPC) -> str:
    return str(rpc_call("eth_getCode", [address, "latest"], rpc_url) or "0x")


def eth_gas_price(rpc_url: str = DEFAULT_RPC) -> int:
    return int(rpc_call("eth_gasPrice", [], rpc_url), 16)


def eth_block_number(rpc_url: str = DEFAULT_RPC) -> int:
    return int(rpc_call("eth_blockNumber", [], rpc_url), 16)


def eth_call(to: str, data: str, rpc_url: str = DEFAULT_RPC) -> str:
    return str(rpc_call("eth_call", [{"to": to, "data": data}, "latest"], rpc_url) or "0x")


def erc20_balance(token: str, holder: str, rpc_url: str = DEFAULT_RPC) -> int | None:
    try:
        data = BALANCE_OF_SELECTOR + _pad_addr(holder)
        raw = eth_call(token, data, rpc_url)
        if raw in ("0x", "0x0", ""):
            return 0
        return int(raw, 16)
    except Exception:
        return None


def erc20_decimals(token: str, rpc_url: str = DEFAULT_RPC) -> int | None:
    try:
        raw = eth_call(token, DECIMALS_SELECTOR, rpc_url)
        return int(raw, 16) if raw and raw != "0x" else None
    except Exception:
        return None


def erc20_symbol(token: str, rpc_url: str = DEFAULT_RPC) -> str | None:
    try:
        raw = eth_call(token, SYMBOL_SELECTOR, rpc_url)
        if not raw or raw == "0x":
            return None
        b = bytes.fromhex(raw[2:])
        # ABI string or bytes32
        if len(b) >= 64 and int.from_bytes(b[:32], "big") == 32:
            length = int.from_bytes(b[32:64], "big")
            return b[64 : 64 + length].decode("utf-8", errors="replace").strip("\x00")
        return b.rstrip(b"\x00").decode("utf-8", errors="replace") or None
    except Exception:
        return None
