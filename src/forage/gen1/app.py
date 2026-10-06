"""Gen-1 FastAPI app: three x402-priced on-chain routes on one process."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, Field

from forage.gen1.denylist import evidence_class, is_controlled
from forage.gen1.organisms import (
    onchain_entity_brief,
    oneshot_wallet_token_snapshot,
    wallet_payment_readiness,
)
from forage.gen1.preflight import x402_payment_preflight, x402_resource_preflight
from forage.gen1.rpc import USDC, USDT
from forage.gen1.wallet import ensure_receiver_wallet

EVENTS_PATH = Path("data/gen1/events.jsonl")
LISTED_PATH = Path("data/gen1/listed.json")
ORGANISM_META_PATH = Path("data/gen1/organism_meta.json")
PRICES = {
    "wallet_payment_readiness": "$0.005",
    "oneshot_wallet_token_snapshot": "$0.004",
    "onchain_entity_brief": "$0.010",
    # Sibling arms — frozen market-comparable anchors (kenoodl/agent402 ~$0.002–$0.01)
    "x402_payment_preflight": "$0.008",
    "x402_resource_preflight": "$0.009",
}
# Approximate measured marginal cost (public RPC + process overhead)
MARGINAL_COST_USD = {
    "wallet_payment_readiness": "0.0005",
    "oneshot_wallet_token_snapshot": "0.0004",
    "onchain_entity_brief": "0.0006",
    "x402_payment_preflight": "0.0005",
    "x402_resource_preflight": "0.0008",
}

ORGANISM_AXIS = {
    "wallet_payment_readiness": "WALLET_INTELLIGENCE",
    "oneshot_wallet_token_snapshot": "RAW_DATA",
    "onchain_entity_brief": "RAW_DATA",
    "x402_payment_preflight": "PAYMENT_DECISION",
    "x402_resource_preflight": "PAYMENT_DECISION",
}


class PaymentPreflightBody(BaseModel):
    wallet: str = Field(..., min_length=42, max_length=42)
    payment_required: Any
    max_spend_usd: str | None = None


def _log_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {**event, "ts": datetime.now(timezone.utc).isoformat()}
    event.pop("private_key", None)
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _payer_from_verified_payment(request: Request) -> str | None:
    """Extract payer only from middleware-verified payment payload (not spoofable headers)."""
    payload = getattr(request.state, "payment_payload", None)
    if payload is None:
        return None
    raw = payload
    if hasattr(payload, "model_dump"):
        raw = payload.model_dump(by_alias=True)
    elif hasattr(payload, "dict"):
        raw = payload.dict()
    if not isinstance(raw, dict):
        return None
    body = raw.get("payload") or {}
    if not isinstance(body, dict):
        return None
    auth = body.get("authorization") or body.get("permit2") or {}
    if isinstance(auth, dict):
        payer = auth.get("from") or auth.get("from_address")
        if isinstance(payer, str) and payer.startswith("0x"):
            return payer
    return None


def _bazaar_ext(
    example_input: dict[str, Any],
    required: list[str],
    *,
    body_type: str | None = None,
) -> dict[str, Any]:
    from x402.extensions.bazaar import declare_discovery_extension

    props = {k: {"type": "string"} for k in example_input}
    kwargs: dict[str, Any] = {
        "input": example_input,
        "input_schema": {"properties": props, "required": required},
    }
    if body_type:
        kwargs["body_type"] = body_type
    return declare_discovery_extension(**kwargs)


def _record_organism_listed(name: str, public_base_url: str | None = None) -> None:
    ORGANISM_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {}
    if ORGANISM_META_PATH.exists():
        try:
            meta = json.loads(ORGANISM_META_PATH.read_text())
        except Exception:
            meta = {}
    organisms = meta.setdefault("organisms", {})
    if name not in organisms or not organisms[name].get("listed_at"):
        organisms[name] = {
            "listed_at": datetime.now(timezone.utc).isoformat(),
            "price": PRICES.get(name),
            "marginal_cost_usd": MARGINAL_COST_USD.get(name),
            "axis": ORGANISM_AXIS.get(name),
            "public_base_url": public_base_url,
        }
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    ORGANISM_META_PATH.write_text(json.dumps(meta, indent=2))


def build_app(*, enable_payments: bool = True) -> FastAPI:
    pay_to = ensure_receiver_wallet()["address"]
    network = os.environ.get("FORAGE_GEN1_NETWORK", "eip155:84532")  # Base Sepolia
    facilitator_url = os.environ.get("FORAGE_GEN1_FACILITATOR", "https://x402.org/facilitator")

    app = FastAPI(title="Forage Gen1 Onchain Organisms", version="0.1.0")
    app.state.pay_to = pay_to
    app.state.network = network
    listed_at = None
    if LISTED_PATH.exists():
        try:
            listed_at = json.loads(LISTED_PATH.read_text()).get("listed_at")
        except Exception:
            listed_at = None
    app.state.listed_at = listed_at

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "pay_to": pay_to,
            "network": network,
            "prices": PRICES,
            "marginal_cost_usd": MARGINAL_COST_USD,
            "listed_at": app.state.listed_at,
            "rule": "SELF_TEST never promotes to E5/E6",
            "note": "Payment rail is FORAGE_GEN1_NETWORK (Base mainnet eip155:8453 for the live experiment). Data plane uses chain= query (default eip155:1).",
        }

    @app.get("/v1/wallet_payment_readiness")
    def readiness(
        request: Request,
        address: str = Query(..., min_length=42, max_length=42),
        chain: str = Query("eip155:1"),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = wallet_payment_readiness(address, chain=chain, tokens=[USDC, USDT])
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": "wallet_payment_readiness",
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "controlled_payer": is_controlled(payer) if payer else None,
                "elapsed_s": round(time.time() - t0, 4),
                "address": address,
                "chain": chain,
            }
        )
        out["meta"] = {
            "price": PRICES["wallet_payment_readiness"],
            "marginal_cost_usd": MARGINAL_COST_USD["wallet_payment_readiness"],
            "pay_to": pay_to,
            "evidence_class": cls,
        }
        return out

    @app.get("/v1/oneshot_wallet_token_snapshot")
    def snapshot(
        request: Request,
        address: str = Query(..., min_length=42, max_length=42),
        tokens: str = Query(..., description="Comma-separated ERC20 addresses"),
        chain: str = Query("eip155:1"),
    ) -> dict[str, Any]:
        t0 = time.time()
        token_list = [t.strip() for t in tokens.split(",") if t.strip()]
        out = oneshot_wallet_token_snapshot(address, chain=chain, tokens=token_list)
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": "oneshot_wallet_token_snapshot",
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "address": address,
                "token_count": len(token_list),
                "chain": chain,
            }
        )
        out["meta"] = {
            "price": PRICES["oneshot_wallet_token_snapshot"],
            "marginal_cost_usd": MARGINAL_COST_USD["oneshot_wallet_token_snapshot"],
            "pay_to": pay_to,
            "evidence_class": cls,
        }
        return out

    @app.get("/v1/onchain_entity_brief")
    def brief(
        request: Request,
        address: str = Query(..., min_length=42, max_length=42),
        chain: str = Query("eip155:1"),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = onchain_entity_brief(address, chain=chain)
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": "onchain_entity_brief",
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "address": address,
                "chain": chain,
            }
        )
        out["meta"] = {
            "price": PRICES["onchain_entity_brief"],
            "marginal_cost_usd": MARGINAL_COST_USD["onchain_entity_brief"],
            "pay_to": pay_to,
            "evidence_class": cls,
        }
        return out

    @app.post("/v1/x402_payment_preflight")
    def payment_preflight(request: Request, body: PaymentPreflightBody) -> dict[str, Any]:
        t0 = time.time()
        out = x402_payment_preflight(
            body.wallet,
            body.payment_required,
            max_spend_usd=body.max_spend_usd,
        )
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": "x402_payment_preflight",
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "verdict": out.get("verdict"),
            }
        )
        out["meta"] = {
            "price": PRICES["x402_payment_preflight"],
            "marginal_cost_usd": MARGINAL_COST_USD["x402_payment_preflight"],
            "pay_to": pay_to,
            "evidence_class": cls,
            "axis": ORGANISM_AXIS["x402_payment_preflight"],
        }
        return out

    @app.get("/v1/x402_resource_preflight")
    def resource_preflight(
        request: Request,
        wallet: str = Query(..., min_length=42, max_length=42),
        resource_url: str = Query(..., min_length=8),
        max_spend_usd: str | None = Query(None),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = x402_resource_preflight(wallet, resource_url, max_spend_usd=max_spend_usd)
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": "x402_resource_preflight",
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "verdict": out.get("verdict"),
            }
        )
        out["meta"] = {
            "price": PRICES["x402_resource_preflight"],
            "marginal_cost_usd": MARGINAL_COST_USD["x402_resource_preflight"],
            "pay_to": pay_to,
            "evidence_class": cls,
            "axis": ORGANISM_AXIS["x402_resource_preflight"],
        }
        return out

    if enable_payments:
        from x402.extensions.bazaar import bazaar_resource_server_extension
        from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.schemas import Network
        from x402.server import x402ResourceServer

        # Prefer CDP Facilitator when API keys exist — required for Bazaar indexing.
        cdp_id = os.environ.get("CDP_API_KEY_ID", "").strip()
        cdp_secret = os.environ.get("CDP_API_KEY_SECRET", "").strip()
        facilitator_client: Any
        facilitator_label = facilitator_url
        if cdp_id and cdp_secret:
            try:
                from cdp.x402 import create_facilitator_config

                facilitator_client = HTTPFacilitatorClient(create_facilitator_config())
                facilitator_label = "cdp:" + getattr(facilitator_client, "url", "https://api.cdp.coinbase.com/platform/v2/x402")
            except Exception:
                # Fall back to explicit CDP URL + raw config if cdp package missing
                facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402"))
                facilitator_label = "cdp:https://api.cdp.coinbase.com/platform/v2/x402"
        else:
            facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url=facilitator_url))

        evm_network: Network = network  # type: ignore[assignment]
        server = x402ResourceServer(facilitator_client)
        server.register(evm_network, ExactEvmServerScheme())
        server.register_extension(bazaar_resource_server_extension)

        vitalik = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"

        def opt(price: str, description: str, bazaar: dict[str, Any]) -> RouteConfig:
            return RouteConfig(
                accepts=[
                    PaymentOption(
                        scheme="exact",
                        pay_to=pay_to,
                        price=price,
                        network=evm_network,
                    )
                ],
                mime_type="application/json",
                description=description,
                extensions=bazaar,
            )

        routes: dict[str, RouteConfig] = {
            "GET /v1/wallet_payment_readiness": opt(
                PRICES["wallet_payment_readiness"],
                "Payment readiness: gas + native + USDC/USDT + verdict",
                _bazaar_ext({"address": vitalik, "chain": "eip155:1"}, ["address"]),
            ),
            "GET /v1/oneshot_wallet_token_snapshot": opt(
                PRICES["oneshot_wallet_token_snapshot"],
                "One-shot native + ERC20 token snapshot with partial-failure state",
                _bazaar_ext(
                    {
                        "address": vitalik,
                        "tokens": f"{USDC},{USDT}",
                        "chain": "eip155:1",
                    },
                    ["address", "tokens"],
                ),
            ),
            "GET /v1/onchain_entity_brief": opt(
                PRICES["onchain_entity_brief"],
                "Deterministic on-chain entity brief (no LLM prose)",
                _bazaar_ext({"address": vitalik, "chain": "eip155:1"}, ["address"]),
            ),
            "POST /v1/x402_payment_preflight": opt(
                PRICES["x402_payment_preflight"],
                "READY/BLOCKED payment decision from wallet + PAYMENT-REQUIRED (no execution)",
                _bazaar_ext(
                    {
                        "wallet": vitalik,
                        "payment_required": "{}",
                        "max_spend_usd": "0.05",
                    },
                    ["wallet", "payment_required"],
                    body_type="json",
                ),
            ),
            "GET /v1/x402_resource_preflight": opt(
                PRICES["x402_resource_preflight"],
                "Fetch x402 resource, parse 402, wallet check → PAY/BLOCKED (no execution)",
                _bazaar_ext(
                    {
                        "wallet": vitalik,
                        "resource_url": "https://api.onesource.io/api/chain/live-balance",
                        "max_spend_usd": "0.05",
                    },
                    ["wallet", "resource_url"],
                ),
            ),
        }
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
        _log_event(
            {
                "kind": "PAYMENTS_ENABLED",
                "network": network,
                "pay_to": pay_to,
                "facilitator": facilitator_label,
                "cdp_keys_present": bool(cdp_id and cdp_secret),
            }
        )

    return app


app = build_app(enable_payments=os.environ.get("FORAGE_GEN1_PAYMENTS", "1") != "0")
