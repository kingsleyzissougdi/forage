"""Gen-1 organism handlers: readiness, snapshot, entity brief."""

from __future__ import annotations

from typing import Any

from forage.gen1 import rpc as R


def wallet_payment_readiness(
    address: str,
    *,
    chain: str = "eip155:1",
    tokens: list[str] | None = None,
    rpc_url: str | None = None,
) -> dict[str, Any]:
    """A — compress gas + native + payment-token checks into a readiness verdict."""
    url = rpc_url or R.rpc_for_chain(chain)
    tokens = tokens or [R.USDC, R.USDT]
    errors: list[str] = []
    block = None
    gas = None
    native = None
    code = None
    try:
        block = R.eth_block_number(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"block:{exc}")
    try:
        gas = R.eth_gas_price(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"gas:{exc}")
    try:
        native = R.eth_get_balance(address, url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"native:{exc}")
    try:
        code = R.eth_get_code(address, url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"code:{exc}")

    token_rows: list[dict[str, Any]] = []
    for t in tokens[:8]:
        bal = R.erc20_balance(t, address, url)
        dec = R.erc20_decimals(t, url)
        sym = R.erc20_symbol(t, url)
        if bal is None:
            token_rows.append({"token": t, "ok": False, "error": "balance_failed"})
        else:
            token_rows.append(
                {
                    "token": t,
                    "ok": True,
                    "balance_raw": str(bal),
                    "decimals": dec,
                    "symbol": sym,
                }
            )

    reasons: list[str] = []
    ready = True
    if native is None:
        ready = False
        reasons.append("native_balance_unavailable")
    elif native == 0:
        ready = False
        reasons.append("native_gas_balance_zero")
    usdc = next((r for r in token_rows if r.get("token", "").lower() == R.USDC.lower()), None)
    if usdc and usdc.get("ok") and int(usdc.get("balance_raw") or 0) == 0:
        reasons.append("usdc_balance_zero")
        # still "ready" for gas-only actions; mark soft
    if code and code not in ("0x", "0x0"):
        reasons.append("address_is_contract")
    if errors:
        ready = False
        reasons.extend(errors)

    return {
        "organism": "wallet_payment_readiness",
        "chain": chain,
        "address": address,
        "account_type": "contract" if (code and code not in ("0x", "0x0")) else "eoa",
        "native_balance_wei": str(native) if native is not None else None,
        "gas_price_wei": str(gas) if gas is not None else None,
        "tokens": token_rows,
        "ready": ready and native is not None and native > 0,
        "reasons": reasons,
        "as_of_block": block,
        "partial_errors": errors,
    }


def oneshot_wallet_token_snapshot(
    address: str,
    *,
    chain: str = "eip155:1",
    tokens: list[str],
    rpc_url: str | None = None,
) -> dict[str, Any]:
    """B — multi-read compression: native + requested token list + partial failures."""
    url = rpc_url or R.rpc_for_chain(chain)
    if not tokens:
        raise ValueError("tokens required")
    tokens = tokens[:20]
    block = None
    try:
        block = R.eth_block_number(url)
    except Exception:
        block = None

    native_err = None
    native = None
    try:
        native = R.eth_get_balance(address, url)
    except Exception as exc:  # noqa: BLE001
        native_err = str(exc)

    rows: list[dict[str, Any]] = []
    failures = 0
    for t in tokens:
        bal = R.erc20_balance(t, address, url)
        if bal is None:
            failures += 1
            rows.append({"token": t, "ok": False, "error": "rpc_failed"})
            continue
        rows.append(
            {
                "token": t,
                "ok": True,
                "balance_raw": str(bal),
                "decimals": R.erc20_decimals(t, url),
                "symbol": R.erc20_symbol(t, url),
            }
        )

    return {
        "organism": "oneshot_wallet_token_snapshot",
        "chain": chain,
        "address": address,
        "native": {
            "ok": native_err is None,
            "balance_wei": str(native) if native is not None else None,
            "error": native_err,
        },
        "tokens": rows,
        "partial_failure": {
            "any": failures > 0 or native_err is not None,
            "failed_token_reads": failures,
            "native_failed": native_err is not None,
        },
        "as_of_block": block,
    }


def onchain_entity_brief(
    address: str,
    *,
    chain: str = "eip155:1",
    rpc_url: str | None = None,
) -> dict[str, Any]:
    """C — narrow deterministic facts; no LLM prose."""
    url = rpc_url or R.rpc_for_chain(chain)
    block = R.eth_block_number(url)
    native = R.eth_get_balance(address, url)
    code = R.eth_get_code(address, url)
    is_contract = code not in ("0x", "0x0", "")
    # Sample a few well-known tokens as objective holdings signal
    holdings = []
    for t, label in ((R.USDC, "USDC"), (R.USDT, "USDT"), (R.WETH, "WETH")):
        bal = R.erc20_balance(t, address, url)
        if bal is None:
            continue
        holdings.append({"token": t, "symbol": label, "balance_raw": str(bal), "nonzero": bal > 0})

    return {
        "organism": "onchain_entity_brief",
        "chain": chain,
        "address": address,
        "facts": {
            "account_type": "contract" if is_contract else "eoa",
            "native_balance_wei": str(native),
            "native_nonzero": native > 0,
            "sampled_holdings": holdings,
            "code_size_bytes": max(0, (len(code) - 2) // 2) if code.startswith("0x") else 0,
        },
        "as_of_block": block,
    }
