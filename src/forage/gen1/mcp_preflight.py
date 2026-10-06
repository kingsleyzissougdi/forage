"""MCP_INSTALL_FUNNEL_V1: one tool wrapping x402_resource_preflight.

Distribution layer only. Does not change backend price or verdicts.
Never logs private keys.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forage.gen1.preflight import x402_resource_preflight

TOOL_NAME = "check_x402_payment_readiness"
SERVER_TITLE = "Check x402 Payment Readiness"
SERVER_INSTRUCTIONS = (
    "Call immediately before paying an x402 resource. Checks payment requirements, "
    "network, wallet balance/readiness, and budget; returns PAY/BLOCKED with reason "
    "codes. Does not move funds."
)
EXAMPLE_WALLET = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
EXAMPLE_RESOURCE_URL = "https://kenoodl.com/preflight"
EVENTS_PATH = Path("data/gen1_mcp/events.jsonl")
SESSION_ID = str(uuid.uuid4())

_SECRET_KEYS = frozenset(
    {
        "private_key",
        "x402_private_key",
        "authorization",
        "payment-signature",
        "payment_signature",
        "x-payment",
        "secret",
        "api_key",
        "apikey",
    }
)
_HEX_KEY = re.compile(r"0x[a-fA-F0-9]{64}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_event(event: dict[str, Any]) -> dict[str, Any]:
    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                if k.lower().replace("-", "_") in _SECRET_KEYS:
                    out[k] = "[redacted]"
                else:
                    out[k] = walk(v)
            return out
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, str):
            return _HEX_KEY.sub("[redacted]", obj)
        return obj

    return walk(event)


def log_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_event({**event, "ts": event.get("ts") or _now(), "session_id": SESSION_ID})
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def is_example_input(resource_url: str, wallet: str) -> bool:
    url = (resource_url or "").rstrip("/").lower()
    return url == EXAMPLE_RESOURCE_URL.rstrip("/").lower() or ((wallet or "").lower() == EXAMPLE_WALLET.lower() and "kenoodl.com/preflight" in url)


def is_intentful_input(resource_url: str, wallet: str) -> bool:
    url = (resource_url or "").strip()
    w = (wallet or "").strip()
    if not url.startswith("http"):
        return False
    if not (w.startswith("0x") and len(w) == 42):
        return False
    return not is_example_input(url, w)


def invoke_check_x402_payment_readiness(
    resource_url: str,
    wallet: str,
    max_spend_usd: str | None = None,
) -> dict[str, Any]:
    """Thin wrap of existing preflight. Does not execute payment."""
    out = x402_resource_preflight(wallet, resource_url, max_spend_usd=max_spend_usd)
    out["mcp_tool"] = TOOL_NAME
    out["does_not_move_funds"] = True
    log_event(
        {
            "kind": "TOOL_INVOKE",
            "tool": TOOL_NAME,
            "intentful": is_intentful_input(resource_url, wallet),
            "example_input": is_example_input(resource_url, wallet),
            "valid_input": is_intentful_input(resource_url, wallet) or is_example_input(resource_url, wallet),
            "backend_x402_402": out.get("http_status") == 402,
            "verdict": out.get("verdict"),
            "friction": None,
            "resource_host": resource_url.split("/")[2] if "://" in resource_url else None,
        }
    )
    return out


def build_mcp():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        SERVER_TITLE,
        instructions=SERVER_INSTRUCTIONS,
        json_response=True,
        host=os.environ.get("FORAGE_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("FORAGE_MCP_PORT", "4024")),
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1",
                "127.0.0.1:*",
                "localhost",
                "localhost:*",
                "138-197-9-7.sslip.io",
            ],
            allowed_origins=[
                "https://138-197-9-7.sslip.io",
                "http://127.0.0.1",
                "http://localhost",
            ],
        ),
    )

    @mcp.tool(
        name=TOOL_NAME,
        description=(
            "Call immediately before paying an x402 resource. Given a resource URL, wallet, "
            "and optional max spend, checks payment requirements, network, wallet "
            "balance/readiness, and budget; returns PAY/BLOCKED with reason codes. "
            "Does not move funds."
        ),
    )
    def check_x402_payment_readiness(
        resource_url: str,
        wallet: str,
        max_spend_usd: str | None = None,
    ) -> dict[str, Any]:
        return invoke_check_x402_payment_readiness(resource_url, wallet, max_spend_usd)

    return mcp


def main() -> None:
    transport = os.environ.get("FORAGE_MCP_TRANSPORT", "streamable-http")
    log_event({"kind": "MCP_INITIALIZE", "transport": transport})
    mcp = build_mcp()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    from starlette.middleware.base import BaseHTTPMiddleware

    class _Access(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            mcp_method = None
            if request.method == "POST":
                raw = await request.body()
                try:
                    data = json.loads(raw.decode() or "{}")
                    if isinstance(data, dict):
                        mcp_method = data.get("method")
                except Exception:
                    mcp_method = None
            response = await call_next(request)
            xff = request.headers.get("x-forwarded-for") or ""
            ip = xff.split(",")[0].strip() or (request.client.host if request.client else None)
            ua = (request.headers.get("user-agent") or "")[:160]
            crawler = any(tok in ua.lower() for tok in ("bot", "crawler", "spider", "health", "monitor"))
            log_event(
                {
                    "kind": "MCP_HTTP",
                    "path": str(request.url.path),
                    "status": int(response.status_code),
                    "mcp_method": mcp_method,
                    "client_ip": ip,
                    "user_agent": ua,
                    "crawler_ua": crawler,
                }
            )
            return response

    app = mcp.streamable_http_app()
    app.add_middleware(_Access)
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("FORAGE_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("FORAGE_MCP_PORT", "4024")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
