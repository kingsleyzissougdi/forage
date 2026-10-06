"""Sibling app unpaid 402 (payments on)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from forage.gen1.sib_app import build_sib_app


def test_sib_health_and_unpaid_402(monkeypatch, tmp_path):
    from forage.gen1 import sib_app

    monkeypatch.setattr(sib_app, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setenv("FORAGE_GEN1_PAY_TO", "0xbf7bdA097C600Cc09749CB1D70874F11DFBFF00A")
    app = build_sib_app(enable_payments=True)
    tc = TestClient(app)
    h = tc.get("/sib/health")
    assert h.status_code == 200
    assert h.json()["ok"] is True
    r = tc.get(
        "/v1/capability_resolver",
        params={"need": "wallet usdc balance"},
    )
    assert r.status_code == 402
    r2 = tc.get(
        "/v1/x402_resource_preflight",
        params={
            "wallet": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
            "resource_url": "https://example.com/x",
        },
    )
    assert r2.status_code == 402
    w = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    for path, params in (
        ("/v1/call_before_paying_x402", {"wallet": w, "resource_url": "https://example.com/x"}),
        ("/v1/oneshot_wallet_token_snapshot", {"address": w, "tokens": usdc}),
        ("/v1/wallet_payment_readiness", {"address": w}),
        ("/v1/onchain_entity_brief", {"address": w}),
        ("/v1/x402_failover_pick", {"need": "wallet usdc balance"}),
        ("/v1/x402_budget_check", {"need": "wallet usdc balance", "max_price": "0.05"}),
        ("/v1/x402_option_compat", {"wallet": w, "amount": "10000"}),
    ):
        rr = tc.get(path, params=params)
        assert rr.status_code == 402, path
    assert set(h.json()["organisms"]) == {
        "oneshot_wallet_token_snapshot",
        "wallet_payment_readiness",
        "x402_resource_preflight",
        "call_before_paying_x402",
        "capability_resolver",
        "x402_failover_pick",
        "x402_budget_check",
        "x402_option_compat",
        "onchain_entity_brief",
    }
