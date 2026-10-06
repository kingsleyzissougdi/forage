"""Sibling job verdicts — no live Bazaar required."""

from __future__ import annotations

from forage.gen1.sib_intent import classify_sib_access
from forage.gen1.sib_jobs import x402_budget_check, x402_option_compat


def test_budget_rejects_bad_n():
    out = x402_budget_check("wallet usdc", max_price="0.05", n_calls="0")
    assert out["verdict"] == "INCOMPATIBLE"
    assert out["affordable"] is False


def test_option_compat_bad_network():
    out = x402_option_compat(
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        network="solana:mainnet",
        asset="x",
        amount="1",
    )
    assert out["verdict"] == "INCOMPATIBLE"


def test_intentful_excludes_schema_example():
    e = classify_sib_access(
        {
            "organism": "call_before_paying_x402",
            "query": "wallet=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045&resource_url=https://kenoodl.com/preflight",
            "user_agent": "Mozilla/5.0",
        }
    )
    assert e["schema_example"] is True
    assert e["intentful"] is False
    e2 = classify_sib_access(
        {
            "organism": "call_before_paying_x402",
            "query": "wallet=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045&resource_url=https://other.example/pay",
            "user_agent": "Mozilla/5.0",
        }
    )
    assert e2["intentful"] is True
    e3 = classify_sib_access(
        {
            "organism": "call_before_paying_x402",
            "query": "wallet=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045&resource_url=https://other.example/pay",
            "user_agent": "x402-census/1.0",
        }
    )
    assert e3["intentful"] is False
    assert e3["bucket"] == "indexer_or_crawler"
