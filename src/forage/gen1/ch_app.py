"""Isolated x402 app for UK_COMPANY_COUNTERPARTY_PREFLIGHT (port 4022 by default).

Does not modify the Gen-1 listed experiment process or its routes.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from forage.gen1.companies_house import (
    CompaniesHouseUpstreamError,
    HttpCompaniesHouseClient,
    uk_company_counterparty_preflight,
)
from forage.gen1.denylist import evidence_class
from forage.gen1.wallet import ensure_receiver_wallet

EVENTS_PATH = Path("data/gen1_ch/events.jsonl")
PRICE = "$0.01"
MARGINAL_COST_USD = "0.0001"
ORGANISM = "uk_company_counterparty_preflight"


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


def _default_client() -> HttpCompaniesHouseClient:
    return HttpCompaniesHouseClient()


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


def build_ch_app(*, enable_payments: bool = True) -> FastAPI:
    pay_to = ensure_receiver_wallet()["address"]
    network = os.environ.get("FORAGE_GEN1_NETWORK", "eip155:84532")
    facilitator_url = os.environ.get("FORAGE_GEN1_FACILITATOR", "https://x402.org/facilitator")

    app = FastAPI(title="Forage UK Company Counterparty Preflight", version="0.1.0")
    app.state.pay_to = pay_to
    app.state.network = network

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "organism": ORGANISM,
            "pay_to": pay_to,
            "network": network,
            "price": PRICE,
            "ch_key_configured": bool(os.environ.get("FORAGE_CH_API_KEY", "").strip()),
            "note": "Isolated from Gen-1 listed routes; company-level CH facts only.",
        }

    @app.get("/v1/uk_company_counterparty_preflight")
    def preflight(
        request: Request,
        company_number: str | None = Query(None),
        company_name: str | None = Query(None),
    ) -> Any:
        t0 = time.time()
        try:
            out = uk_company_counterparty_preflight(
                company_number=company_number,
                company_name=company_name,
                client=_default_client(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CompaniesHouseUpstreamError as exc:
            _log_event(
                {
                    "organism": ORGANISM,
                    "kind": "UPSTREAM_ERROR",
                    "error": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "elapsed_s": round(time.time() - t0, 4),
                }
            )
            return JSONResponse(
                status_code=exc.http_status,
                content={
                    "error": exc.code,
                    "retryable": exc.retryable,
                    "message": exc.message,
                    "organism": ORGANISM,
                },
            )

        payer = _payer_from_verified_payment(request)
        cls = evidence_class(payer)
        _log_event(
            {
                "organism": ORGANISM,
                "kind": "FULFILL",
                "payer": payer,
                "evidence_class": cls,
                "elapsed_s": round(time.time() - t0, 4),
                "verdict": out.get("verdict"),
                "upstream_calls": out.get("upstream_calls"),
                "company_number": (out.get("matched") or {}).get("company_number") if isinstance(out.get("matched"), dict) else None,
            }
        )
        out["meta"] = {
            "price": PRICE,
            "marginal_cost_usd": MARGINAL_COST_USD,
            "pay_to": pay_to,
            "evidence_class": cls,
            "axis": "COUNTERPARTY_PREFLIGHT",
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

        routes: dict[str, RouteConfig] = {
            "GET /v1/uk_company_counterparty_preflight": RouteConfig(
                accepts=[
                    PaymentOption(
                        scheme="exact",
                        pay_to=pay_to,
                        price=PRICE,
                        network=evm_network,
                    )
                ],
                mime_type="application/json",
                description=("UK Companies House company-level counterparty preflight: status, RO, accounts/confirmation overdue → NORMAL|REVIEW|NOT_FOUND"),
                extensions=_bazaar_ext(
                    {"company_number": "00445790"},
                    ["company_number"],
                ),
            ),
        }
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
        _log_event(
            {
                "kind": "PAYMENTS_ENABLED",
                "organism": ORGANISM,
                "network": network,
                "pay_to": pay_to,
                "price": PRICE,
            }
        )

    # Outer middleware (added last) so ACCESS sees final 402/200 including payment middleware.
    @app.middleware("http")
    async def access_log(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path == "/health":
            return await call_next(request)
        t0 = time.time()
        response = await call_next(request)
        if path.startswith("/v1/uk_company_counterparty_preflight") or path == "/":
            ua = (request.headers.get("user-agent") or "")[:160]
            payment_attempt = _has_payment_header(request)
            status = int(response.status_code)
            _log_event(
                {
                    "organism": ORGANISM,
                    "kind": "ACCESS",
                    "path": path,
                    "status": status,
                    "client_ip": _client_ip(request),
                    "payment_attempt": payment_attempt,
                    "unpaid_402": status == 402 and not payment_attempt,
                    "payment_attempt_failed": payment_attempt and status != 200,
                    "user_agent": ua,
                    "crawler_ua": _ua_looks_crawler(ua),
                    "query": str(request.url.query)[:200],
                    "elapsed_s": round(time.time() - t0, 4),
                }
            )
        return response

    return app


app = build_ch_app(enable_payments=os.environ.get("FORAGE_GEN1_PAYMENTS", "1") != "0")
