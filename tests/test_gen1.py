from __future__ import annotations

import json
from pathlib import Path

from forage.gen1.denylist import evidence_class, is_controlled, register_controlled
from forage.gen1.organisms import (
    onchain_entity_brief,
    oneshot_wallet_token_snapshot,
    wallet_payment_readiness,
)
from forage.gen1.rpc import USDC


def test_denylist_marks_self_test_never_e5(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forage.gen1.denylist.DENY_PATH", tmp_path / "controlled_wallets.json")
    register_controlled("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert is_controlled("0xAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAa")
    sib = tmp_path / "data/gen1_sib/controlled_wallets.json"
    assert sib.exists()
    assert "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in json.loads(sib.read_text())["wallets"]
    assert not is_controlled(None)
    assert evidence_class("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == "SELF_TEST"
    assert evidence_class("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb") == "EXTERNAL_CANDIDATE"
    assert evidence_class(None, forced_self_test=True) == "SELF_TEST"
    assert evidence_class(None) == "UNKNOWN"


def test_payer_from_verified_payment_ignores_headers():
    from types import SimpleNamespace

    from forage.gen1.app import _payer_from_verified_payment

    req = SimpleNamespace(
        state=SimpleNamespace(
            payment_payload={
                "payload": {"authorization": {"from": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}},
            }
        ),
        headers={"x-payment-payer": "0xcccccccccccccccccccccccccccccccccccccccc"},
    )
    assert _payer_from_verified_payment(req) == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    req2 = SimpleNamespace(state=SimpleNamespace(), headers={"x-payment-payer": "0xcc"})
    assert _payer_from_verified_payment(req2) is None


def test_readiness_schema_keys(monkeypatch):
    from forage.gen1 import rpc as R

    monkeypatch.setattr(R, "eth_block_number", lambda url=None: 123)
    monkeypatch.setattr(R, "eth_gas_price", lambda url=None: 1_000_000_000)
    monkeypatch.setattr(R, "eth_get_balance", lambda a, url=None: 10**18)
    monkeypatch.setattr(R, "eth_get_code", lambda a, url=None: "0x")
    monkeypatch.setattr(R, "erc20_balance", lambda t, h, url=None: 0)
    monkeypatch.setattr(R, "erc20_decimals", lambda t, url=None: 6)
    monkeypatch.setattr(R, "erc20_symbol", lambda t, url=None: "USDC")

    out = wallet_payment_readiness("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert out["organism"] == "wallet_payment_readiness"
    assert "ready" in out and "reasons" in out and out["as_of_block"] == 123
    assert out["account_type"] == "eoa"


def test_snapshot_partial_failure(monkeypatch):
    from forage.gen1 import rpc as R

    monkeypatch.setattr(R, "eth_block_number", lambda url=None: 1)
    monkeypatch.setattr(R, "eth_get_balance", lambda a, url=None: 1)
    monkeypatch.setattr(R, "erc20_balance", lambda t, h, url=None: None if t == "bad" else 5)
    monkeypatch.setattr(R, "erc20_decimals", lambda t, url=None: 18)
    monkeypatch.setattr(R, "erc20_symbol", lambda t, url=None: "T")

    out = oneshot_wallet_token_snapshot(
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        tokens=[USDC, "bad"],
    )
    assert out["partial_failure"]["any"] is True
    assert out["partial_failure"]["failed_token_reads"] == 1
    assert len(out["tokens"]) == 2


def test_entity_brief_no_prose(monkeypatch):
    from forage.gen1 import rpc as R

    monkeypatch.setattr(R, "eth_block_number", lambda url=None: 9)
    monkeypatch.setattr(R, "eth_get_balance", lambda a, url=None: 0)
    monkeypatch.setattr(R, "eth_get_code", lambda a, url=None: "0x")
    monkeypatch.setattr(R, "erc20_balance", lambda t, h, url=None: 0)

    out = onchain_entity_brief("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert "facts" in out
    assert "narrative" not in out
    assert out["facts"]["account_type"] == "eoa"


def test_app_health_without_payments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORAGE_GEN1_PAYMENTS", "0")
    # Avoid leaking real data/gen1 into test cwd
    from forage.gen1.app import build_app

    app = build_app(enable_payments=False)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "wallet_payment_readiness" in body["prices"]


def test_payment_preflight_ready(monkeypatch):
    from forage.gen1 import preflight as P

    monkeypatch.setattr(P.R, "erc20_balance", lambda t, h, url=None: 10_000)
    monkeypatch.setattr(P.R, "erc20_symbol", lambda t, url=None: "USDC")
    monkeypatch.setattr(P.R, "eth_get_balance", lambda a, url=None: 10**15)
    pr = {
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",
                "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "amount": "8000",
                "payTo": "0xbf7bdA097C600Cc09749CB1D70874F11DFBFF00A",
            }
        ]
    }
    out = P.x402_payment_preflight("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", pr)
    assert out["verdict"] == "READY"
    assert out["next_action"]["type"] == "PAY"
    assert out["required"]["amount"] == "8000"


def test_payment_preflight_blocked_insufficient(monkeypatch):
    from forage.gen1 import preflight as P

    monkeypatch.setattr(P.R, "erc20_balance", lambda t, h, url=None: 100)
    monkeypatch.setattr(P.R, "erc20_symbol", lambda t, url=None: "USDC")
    monkeypatch.setattr(P.R, "eth_get_balance", lambda a, url=None: 10**15)
    pr = {
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",
                "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "amount": "8000",
                "payTo": "0xbf7bdA097C600Cc09749CB1D70874F11DFBFF00A",
            }
        ]
    }
    out = P.x402_payment_preflight("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", pr)
    assert out["verdict"] == "BLOCKED"
    assert "insufficient_payment_token" in out["reason_codes"]


def test_resource_preflight_not_402(monkeypatch):
    from forage.gen1 import preflight as P

    class FakeResp:
        status_code = 200
        headers = {}

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(P.httpx, "Client", FakeClient)
    out = P.x402_resource_preflight(
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        "https://example.com/x",
    )
    assert out["verdict"] == "UNAVAILABLE"
    assert "expected_402" in out["reason_codes"]


def test_specificity_writes_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(
        "forage.gen1.specificity._cdp_hits",
        lambda: [
            {
                "resource": "https://api.onesource.io/api/chain/live-balance",
                "description": "live-balance",
                "accepts": [{"amount": "3000"}],
            }
        ],
    )
    from forage.gen1.specificity import run_specificity_check

    report = run_specificity_check()
    assert report["organisms"]["B_oneshot_wallet_token_snapshot"]["status"] == "PROCEED"
    assert Path("data/gen1_specificity.json").exists()
    assert json.loads(Path("data/gen1_specificity.json").read_text())["campaign"] == "GEN1_SPECIFICITY"
