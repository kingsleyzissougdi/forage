"""ACCESS classification for CH 24h/72h demand reads."""

from __future__ import annotations

from forage.gen1.ch_traffic import classify_access, summarize_access


def _ev(**kwargs):
    base = {
        "kind": "ACCESS",
        "path": "/v1/uk_company_counterparty_preflight",
        "status": 402,
        "unpaid_402": True,
        "client_ip": "203.0.113.1",
        "user_agent": "curl/8.5.0",
        "query": "",
    }
    base.update(kwargs)
    return base


def test_empty_query_and_indexer_ua():
    e = _ev(user_agent="x402-census-probe/2.1 (independent index research)")
    c = classify_access(e)
    assert c["empty_query"] is True
    assert c["indexer_ua"] is True
    assert c["plausible_lookup"] is False
    assert c["bucket"] == "empty_query"


def test_bazaar_example_number_is_not_plausible_lookup():
    c = classify_access(_ev(query="company_number=00445790", user_agent="CoinbaseBazaarDiscovery/1.0"))
    assert c["schema_example"] is True
    assert c["plausible_lookup"] is False
    assert c["bucket"] == "schema_example"
    assert c["indexer_ua"] is True


def test_other_company_number_is_plausible():
    c = classify_access(_ev(query="company_number=12345678", user_agent="httpx/0.27"))
    assert c["plausible_lookup"] is True
    assert c["bucket"] == "plausible_lookup"
    assert c["indexer_ua"] is False


def test_summarize_separates_indexer_from_plausible():
    events = [
        _ev(client_ip="1.1.1.1", user_agent="CarbonMonitor/0.1 healthcheck"),
        _ev(client_ip="1.1.1.1", user_agent="CarbonMonitor/0.1 healthcheck"),
        _ev(client_ip="2.2.2.2", query="company_number=00445790", user_agent="node"),
        _ev(client_ip="3.3.3.3", query="company_number=00999999", user_agent="httpx/0.27"),
        _ev(path="/", query="", user_agent="scanner", unpaid_402=False, status=404),
    ]
    s = summarize_access(events)
    assert s["unpaid_402"] == 4
    assert s["indexer_ua_402"] == 2
    assert s["empty_query_402"] == 2
    assert s["schema_example_402"] == 1
    assert s["plausible_lookup_402"] == 1
    assert s["plausible_company_numbers"] == ["00999999"]
    assert s["repeat_non_indexer_ips"] == 0
    assert s["traffic_read"] == "has_plausible_lookups"
