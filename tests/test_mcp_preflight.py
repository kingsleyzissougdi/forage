"""MCP wrapper around existing x402_resource_preflight — no new SKU."""

from __future__ import annotations

import json
from pathlib import Path

from forage.gen1.mcp_preflight import (
    EXAMPLE_RESOURCE_URL,
    EXAMPLE_WALLET,
    TOOL_NAME,
    invoke_check_x402_payment_readiness,
    is_example_input,
    is_intentful_input,
    redact_event,
)


def test_tool_name_is_job_to_be_done():
    assert TOOL_NAME == "check_x402_payment_readiness"


def test_invoke_calls_existing_backend(monkeypatch, tmp_path):
    from forage.gen1 import mcp_preflight as m

    monkeypatch.setattr(m, "EVENTS_PATH", tmp_path / "events.jsonl")

    called = {}

    def fake(wallet: str, resource_url: str, *, max_spend_usd: str | None = None) -> dict:
        called["wallet"] = wallet
        called["resource_url"] = resource_url
        called["max_spend_usd"] = max_spend_usd
        return {"organism": "x402_resource_preflight", "verdict": "PAY", "reason_codes": []}

    monkeypatch.setattr(m, "x402_resource_preflight", fake)
    out = invoke_check_x402_payment_readiness(
        resource_url="https://example.org/pay",
        wallet="0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        max_spend_usd="0.05",
    )
    assert called["resource_url"] == "https://example.org/pay"
    assert out["verdict"] == "PAY"
    assert out["organism"] == "x402_resource_preflight"
    assert out["mcp_tool"] == TOOL_NAME
    assert "private_key" not in json.dumps(out).lower()


def test_example_vs_intentful():
    assert is_example_input(EXAMPLE_RESOURCE_URL, EXAMPLE_WALLET) is True
    assert is_intentful_input("https://other.example/pay", EXAMPLE_WALLET) is True
    assert is_intentful_input(EXAMPLE_RESOURCE_URL, EXAMPLE_WALLET) is False
    assert is_intentful_input("", EXAMPLE_WALLET) is False


def test_redact_never_keeps_private_keys():
    ev = redact_event(
        {
            "kind": "TOOL",
            "X402_PRIVATE_KEY": "0xdeadbeef",
            "private_key": "secret",
            "nested": {"authorization": "0xabc"},
        }
    )
    blob = json.dumps(ev).lower()
    assert "deadbeef" not in blob
    assert "secret" not in blob
    assert "0xabc" not in blob


def test_log_event_redacts(tmp_path: Path, monkeypatch):
    from forage.gen1 import mcp_preflight as m

    monkeypatch.setattr(m, "EVENTS_PATH", tmp_path / "events.jsonl")
    m.log_event({"kind": "TEST", "private_key": "nope", "ok": True})
    line = (tmp_path / "events.jsonl").read_text()
    assert "nope" not in line
    assert '"ok": true' in line
