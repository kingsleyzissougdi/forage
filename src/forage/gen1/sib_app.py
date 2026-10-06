"""Isolated sibling SKUs: x402_resource_preflight + capability_resolver (:4023).

Does not modify the CH process on :4022.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request

from forage.gen1.capability_resolver import EXAMPLE_NEED, resolve_capability
from forage.gen1.denylist import evidence_class
from forage.gen1.organisms import onchain_entity_brief, oneshot_wallet_token_snapshot, wallet_payment_readiness
from forage.gen1.preflight import x402_resource_preflight
from forage.gen1.sib_jobs import x402_budget_check, x402_failover_pick, x402_option_compat
from forage.gen1.wallet import ensure_receiver_wallet

EVENTS_PATH = Path("data/gen1_sib/events.jsonl")
PRICES = {
    "oneshot_wallet_token_snapshot": "$0.008",
    "wallet_payment_readiness": "$0.005",
    "x402_resource_preflight": "$0.009",
    "call_before_paying_x402": "$0.009",
    "capability_resolver": "$0.01",
    "x402_failover_pick": "$0.008",
    "x402_budget_check": "$0.006",
    "x402_option_compat": "$0.006",
    "onchain_entity_brief": "$0.01",
}
MARGINAL = {
    "oneshot_wallet_token_snapshot": "0.0004",
    "wallet_payment_readiness": "0.0005",
    "x402_resource_preflight": "0.0008",
    "call_before_paying_x402": "0.0008",
    "capability_resolver": "0.0002",
    "x402_failover_pick": "0.0002",
    "x402_budget_check": "0.0002",
    "x402_option_compat": "0.0004",
    "onchain_entity_brief": "0.0006",
}
PATH_ORG = {
    "/v1/oneshot_wallet_token_snapshot": "oneshot_wallet_token_snapshot",
    "/v1/wallet_payment_readiness": "wallet_payment_readiness",
    "/v1/x402_resource_preflight": "x402_resource_preflight",
    "/v1/call_before_paying_x402": "call_before_paying_x402",
    "/v1/capability_resolver": "capability_resolver",
    "/v1/x402_failover_pick": "x402_failover_pick",
    "/v1/x402_budget_check": "x402_budget_check",
    "/v1/x402_option_compat": "x402_option_compat",
    "/v1/onchain_entity_brief": "onchain_entity_brief",
}
VITALIK = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def _log_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {**event, "ts": datetime.now(timezone.utc).isoformat()}
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _payer_from_verified_payment(request: Request) -> str | None:
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


def _bazaar_ext(example_input: dict[str, Any], required: list[str]) -> dict[str, Any]:
    from x402.extensions.bazaar import declare_discovery_extension

    props = {k: {"type": "string"} for k in example_input}
    return declare_discovery_extension(
        input=example_input,
        input_schema={"properties": props, "required": required},
    )


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip() or None
    if request.client:
        return request.client.host
    return None


def _has_payment_header(request: Request) -> bool:
    for k in request.headers:
        lk = k.lower()
        if lk in ("payment-signature", "payment", "x-payment", "x-payment-response"):
            return True
    return False


def _ua_looks_crawler(ua: str) -> bool:
    u = ua.lower()
    return any(tok in u for tok in ("bot", "crawler", "spider", "slurp", "bingpreview", "facebookexternalhit"))


def build_sib_app(*, enable_payments: bool = True) -> FastAPI:
    pay_to = ensure_receiver_wallet()["address"]
    network = os.environ.get("FORAGE_GEN1_NETWORK", "eip155:84532")
    facilitator_url = os.environ.get("FORAGE_GEN1_FACILITATOR", "https://x402.org/facilitator")
    app = FastAPI(title="Forage sibling SKUs", version="0.1.0")
    app.state.pay_to = pay_to
    app.state.network = network

    @app.get("/sib/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "organisms": list(PRICES),
            "pay_to": pay_to,
            "network": network,
            "prices": PRICES,
            "note": "Isolated from CH :4022. Parallel sibling hypotheses, not Gen-2.",
        }

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
            "marginal_cost_usd": MARGINAL["x402_resource_preflight"],
            "pay_to": pay_to,
            "evidence_class": cls,
            "axis": "DECISION_WORKFLOW_COMPRESSION",
        }
        return out

    @app.get("/v1/capability_resolver")
    def capability_resolver(
        request: Request,
        need: str = Query(..., min_length=3),
        max_price: str | None = Query(None),
        max_latency_ms: str | None = Query(None),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = resolve_capability(need, max_price_usd=max_price, max_latency_ms=max_latency_ms)
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": "capability_resolver",
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "verdict": out.get("verdict"),
                "need_len": len(need),
            }
        )
        out["meta"] = {
            "price": PRICES["capability_resolver"],
            "marginal_cost_usd": MARGINAL["capability_resolver"],
            "pay_to": pay_to,
            "evidence_class": cls,
            "axis": "TOOL_PROCUREMENT",
        }
        return out

    def _finish(request: Request, organism: str, t0: float, out: dict[str, Any], axis: str) -> dict[str, Any]:
        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": organism,
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "verdict": out.get("verdict") or out.get("ready"),
            }
        )
        out["meta"] = {
            "price": PRICES[organism],
            "marginal_cost_usd": MARGINAL[organism],
            "pay_to": pay_to,
            "evidence_class": cls,
            "axis": axis,
        }
        return out

    @app.get("/v1/call_before_paying_x402")
    def call_before_paying(
        request: Request,
        wallet: str = Query(..., min_length=42, max_length=42),
        resource_url: str = Query(..., min_length=8),
        max_spend_usd: str | None = Query(None),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = x402_resource_preflight(wallet, resource_url, max_spend_usd=max_spend_usd)
        out["organism"] = "call_before_paying_x402"
        return _finish(request, "call_before_paying_x402", t0, out, "DECISION_WORKFLOW_COMPRESSION")

    @app.get("/v1/oneshot_wallet_token_snapshot")
    def snapshot(
        request: Request,
        address: str = Query(..., min_length=42, max_length=42),
        tokens: str = Query(..., min_length=8),
        chain: str = Query("eip155:8453"),
    ) -> dict[str, Any]:
        t0 = time.time()
        tok = [t.strip() for t in tokens.split(",") if t.strip()]
        out = oneshot_wallet_token_snapshot(address, chain=chain, tokens=tok)
        return _finish(request, "oneshot_wallet_token_snapshot", t0, out, "RAW_COMPRESSED_DATA")

    @app.get("/v1/wallet_payment_readiness")
    def readiness(
        request: Request,
        address: str = Query(..., min_length=42, max_length=42),
        chain: str = Query("eip155:8453"),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = wallet_payment_readiness(address, chain=chain)
        return _finish(request, "wallet_payment_readiness", t0, out, "REAL_WORLD_ACTION_READINESS")

    @app.get("/v1/onchain_entity_brief")
    def brief(
        request: Request,
        address: str = Query(..., min_length=42, max_length=42),
        chain: str = Query("eip155:8453"),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = onchain_entity_brief(address, chain=chain)
        return _finish(request, "onchain_entity_brief", t0, out, "SCOUT_WILDCARD")

    @app.get("/v1/x402_failover_pick")
    def failover(
        request: Request,
        need: str = Query(..., min_length=3),
        max_price: str | None = Query(None),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = x402_failover_pick(need, max_price=max_price)
        return _finish(request, "x402_failover_pick", t0, out, "FAILOVER_ROUTING")

    @app.get("/v1/x402_budget_check")
    def budget(
        request: Request,
        need: str = Query(..., min_length=3),
        max_price: str = Query(...),
        n_calls: str = Query("1"),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = x402_budget_check(need, max_price=max_price, n_calls=n_calls)
        return _finish(request, "x402_budget_check", t0, out, "COST_BUDGET_PLANNING")

    @app.get("/v1/x402_option_compat")
    def option_compat(
        request: Request,
        wallet: str = Query(..., min_length=42, max_length=42),
        network: str = Query("eip155:8453"),
        asset: str = Query(USDC),
        amount: str = Query("10000"),
    ) -> dict[str, Any]:
        t0 = time.time()
        out = x402_option_compat(wallet, network=network, asset=asset, amount=amount)
        return _finish(request, "x402_option_compat", t0, out, "SCHEMA_COMPATIBILITY")

    if enable_payments:
        from x402.extensions.bazaar import bazaar_resource_server_extension
        from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.schemas import Network
        from x402.server import x402ResourceServer

        cdp_id = os.environ.get("CDP_API_KEY_ID", "").strip()
        cdp_secret = os.environ.get("CDP_API_KEY_SECRET", "").strip()
        if cdp_id and cdp_secret:
            try:
                from cdp.x402 import create_facilitator_config

                facilitator_client: Any = HTTPFacilitatorClient(create_facilitator_config())
            except Exception:
                facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402"))
        else:
            facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url=facilitator_url))

        evm_network: Network = network  # type: ignore[assignment]
        server = x402ResourceServer(facilitator_client)
        server.register(evm_network, ExactEvmServerScheme())
        server.register_extension(bazaar_resource_server_extension)

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
            "GET /v1/oneshot_wallet_token_snapshot": opt(
                PRICES["oneshot_wallet_token_snapshot"],
                "When an agent needs compressed wallet token balances (native+ERC20) in one read. Job: raw/compressed on-chain data.",
                _bazaar_ext({"address": VITALIK, "tokens": USDC, "chain": "eip155:8453"}, ["address", "tokens"]),
            ),
            "GET /v1/wallet_payment_readiness": opt(
                PRICES["wallet_payment_readiness"],
                "When an agent must know if a wallet can pay gas+USDC right now before attempting a Base action. Job: action readiness.",
                _bazaar_ext({"address": VITALIK, "chain": "eip155:8453"}, ["address"]),
            ),
            "GET /v1/x402_resource_preflight": opt(
                PRICES["x402_resource_preflight"],
                "x402_resource_preflight: fetch URL, parse PAYMENT-REQUIRED, check wallet vs max_spend → PAY|BLOCKED|INCOMPATIBLE|UNAVAILABLE. No payment.",
                _bazaar_ext({"wallet": VITALIK, "resource_url": "https://kenoodl.com/preflight", "max_spend_usd": "0.05"}, ["wallet", "resource_url"]),
            ),
            "GET /v1/call_before_paying_x402": opt(
                PRICES["call_before_paying_x402"],
                "Call before paying any x402 URL: fetch 402, normalize terms, PAY or BLOCKED. No money moved.",
                _bazaar_ext(
                    {
                        "wallet": VITALIK,
                        "resource_url": "https://kenoodl.com/preflight",
                        "max_spend_usd": "0.05",
                    },
                    ["wallet", "resource_url"],
                ),
            ),
            "GET /v1/capability_resolver": opt(
                PRICES["capability_resolver"],
                "Find a paid x402 tool for a capability: recommended endpoint + price + fallback. No execute.",
                _bazaar_ext({"need": EXAMPLE_NEED, "max_price": "0.05"}, ["need"]),
            ),
            "GET /v1/x402_failover_pick": opt(
                PRICES["x402_failover_pick"],
                "When an agent needs a primary paid tool plus an explicit backup if the first 402 seller is down. Procurement failover, no execution.",
                _bazaar_ext({"need": EXAMPLE_NEED, "max_price": "0.05"}, ["need"]),
            ),
            "GET /v1/x402_budget_check": opt(
                PRICES["x402_budget_check"],
                "When an agent has a USDC budget and N planned calls: can they afford the cheapest matching Bazaar tool? Cost planning, no execution.",
                _bazaar_ext({"need": EXAMPLE_NEED, "max_price": "0.05", "n_calls": "3"}, ["need", "max_price"]),
            ),
            "GET /v1/x402_option_compat": opt(
                PRICES["x402_option_compat"],
                "Known network/asset/amount: is this wallet compatible and funded? No third-party fetch.",
                _bazaar_ext(
                    {
                        "wallet": VITALIK,
                        "network": "eip155:8453",
                        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                        "amount": "10000",
                    },
                    ["wallet", "amount"],
                ),
            ),
            "GET /v1/onchain_entity_brief": opt(
                PRICES["onchain_entity_brief"],
                "When an agent needs a short deterministic on-chain entity brief (EOA vs contract, native, sampled USDC/USDT/WETH). No LLM prose.",
                _bazaar_ext({"address": VITALIK, "chain": "eip155:8453"}, ["address"]),
            ),
        }
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
        _log_event({"kind": "PAYMENTS_ENABLED", "network": network, "pay_to": pay_to, "prices": PRICES})

    @app.middleware("http")
    async def access_log(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path == "/sib/health":
            return await call_next(request)
        t0 = time.time()
        response = await call_next(request)
        if path.startswith("/v1/"):
            ua = (request.headers.get("user-agent") or "")[:160]
            payment_attempt = _has_payment_header(request)
            status = int(response.status_code)
            org = PATH_ORG.get(path, "unknown")
            _log_event(
                {
                    "organism": org,
                    "kind": "ACCESS",
                    "path": path,
                    "status": status,
                    "client_ip": _client_ip(request),
                    "payment_attempt": payment_attempt,
                    "unpaid_402": status == 402 and not payment_attempt,
                    "payment_attempt_failed": payment_attempt and status != 200,
                    "user_agent": ua,
                    "crawler_ua": _ua_looks_crawler(ua),
                    "query": str(request.url.query)[:300],
                    "elapsed_s": round(time.time() - t0, 4),
                }
            )
        return response

    return app


app = build_sib_app(enable_payments=os.environ.get("FORAGE_GEN1_PAYMENTS", "1") != "0")
