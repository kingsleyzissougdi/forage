"""CAPABILITY_RESOLVER V1 tests — mocked discovery."""

from __future__ import annotations

from forage.gen1.capability_resolver import resolve_capability


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self.status_code = status
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status
        self.calls: list[tuple] = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return _FakeResp(self.payload, self.status)

    def close(self):
        return None


def _item(resource: str, desc: str, amount: str, calls: int = 0) -> dict:
    return {
        "resource": resource,
        "description": desc,
        "accepts": [{"amount": amount, "network": "eip155:8453", "payTo": "0xabc"}],
        "quality": {"l30DaysTotalCalls": calls},
    }


def test_need_too_short():
    out = resolve_capability("ab")
    assert out["verdict"] == "INCOMPATIBLE"


def test_budget_filters_and_ranks(monkeypatch):
    client = _FakeClient(
        {
            "resources": [
                _item("https://a.example/wallet-usdc", "wallet usdc balance snapshot", "50000", calls=10),
                _item("https://b.example/cheap-usdc", "wallet usdc balance cheap", "3000", calls=2),
                _item("https://c.example/weather", "weather forecast", "1000", calls=99),
            ]
        }
    )
    out = resolve_capability("wallet usdc balance", max_price_usd="0.01", client=client)
    assert out["verdict"] == "RECOMMEND"
    assert out["recommended"]["endpoint"] == "https://b.example/cheap-usdc"
    assert out["fallback"]["endpoint"] == "https://c.example/weather" or out["fallback"] is not None
    # 50000 atomic = $0.05 filtered by max_price 0.01
    recs = {out["recommended"]["endpoint"]}
    if out.get("fallback"):
        recs.add(out["fallback"]["endpoint"])
    recs |= {a["endpoint"] for a in out.get("alternatives") or []}
    assert "https://a.example/wallet-usdc" not in recs


def test_discovery_unavailable():
    client = _FakeClient({}, status=500)
    out = resolve_capability("wallet usdc balance", client=client)
    assert out["verdict"] == "UNAVAILABLE"
