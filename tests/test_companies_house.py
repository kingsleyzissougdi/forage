"""UK_COMPANY_COUNTERPARTY_PREFLIGHT — acceptance tests (mocked CH upstream)."""

from __future__ import annotations

import json

import pytest

from forage.gen1.companies_house import (
    CompaniesHouseUpstreamError,
    uk_company_counterparty_preflight,
)

ACTIVE_PROFILE = {
    "company_name": "TESCO PLC",
    "company_number": "00445790",
    "type": "plc",
    "company_status": "active",
    "date_of_creation": "1947-11-27",
    "registered_office_address": {
        "address_line_1": "TESCO HOUSE",
        "locality": "WELWYN GARDEN CITY",
        "postal_code": "AL7 1GA",
        "country": "United Kingdom",
    },
    "accounts": {
        "next_accounts": {"due_on": "2027-02-28", "overdue": False},
        "overdue": False,
    },
    "confirmation_statement": {
        "next_due": "2026-12-01",
        "overdue": False,
    },
    "has_charges": True,
    "has_insolvency_history": True,
    "registered_office_is_in_dispute": False,
    "undeliverable_registered_office_address": False,
}


class FakeCH:
    def __init__(self, *, profile=None, search_items=None, fail_status=None):
        self.profile = profile
        self.search_items = search_items if search_items is not None else []
        self.fail_status = fail_status
        self.calls: list[str] = []

    def get_company(self, number: str) -> dict:
        self.calls.append(f"company:{number}")
        if self.fail_status:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"companies_house_http_{self.fail_status}",
                http_status=503,
                retryable=True,
            )
        assert self.profile is not None
        return dict(self.profile)

    def search_companies(self, q: str) -> list[dict]:
        self.calls.append(f"search:{q}")
        if self.fail_status:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"companies_house_http_{self.fail_status}",
                http_status=503,
                retryable=True,
            )
        return list(self.search_items)


def test_exact_company_number_lookup():
    client = FakeCH(profile=ACTIVE_PROFILE)
    out = uk_company_counterparty_preflight(company_number="00445790", client=client)
    assert out["verdict"] == "NORMAL"
    assert out["matched"]["company_number"] == "00445790"
    assert out["matched"]["company_name"] == "TESCO PLC"
    assert out["accounts"]["overdue"] is False
    assert "HAS_CHARGES" in out["informational_flags"]
    assert "HAS_INSOLVENCY_HISTORY" in out["informational_flags"]
    assert "HAS_CHARGES" not in out["reason_codes"]
    assert client.calls == ["company:00445790"]
    assert out["upstream_calls"] == 1
    assert "officers" not in out and "psc" not in out
    assert out["sources"][0]["name"] == "Companies House Public Data API"
    assert "disclaimer" in out and out["registry_as_of"]


def test_unique_name_match():
    client = FakeCH(
        profile=ACTIVE_PROFILE,
        search_items=[
            {
                "company_number": "00445790",
                "title": "TESCO PLC",
                "company_status": "active",
            }
        ],
    )
    out = uk_company_counterparty_preflight(company_name="Tesco", client=client)
    assert out["verdict"] == "NORMAL"
    assert out["matched"]["company_number"] == "00445790"
    assert client.calls[0].startswith("search:")
    assert client.calls[1] == "company:00445790"
    assert out["upstream_calls"] == 2


def test_ambiguous_name_review():
    client = FakeCH(
        search_items=[
            {"company_number": "1", "title": "FOO LTD", "company_status": "active"},
            {"company_number": "2", "title": "FOO HOLDINGS LTD", "company_status": "active"},
        ]
    )
    out = uk_company_counterparty_preflight(company_name="Foo", client=client)
    assert out["verdict"] == "REVIEW"
    assert "NAME_AMBIGUOUS" in out["reason_codes"]
    assert out["matched"] is None
    assert out["upstream_calls"] == 1


def test_not_found():
    client = FakeCH(search_items=[])
    out = uk_company_counterparty_preflight(company_name="ZZZZNOMATCH", client=client)
    assert out["verdict"] == "NOT_FOUND"
    assert "NOT_FOUND" in out["reason_codes"]


def test_inactive_company_review():
    profile = {
        **ACTIVE_PROFILE,
        "company_status": "dissolved",
        "has_charges": False,
        "has_insolvency_history": False,
    }
    client = FakeCH(profile=profile)
    out = uk_company_counterparty_preflight(company_number="00445790", client=client)
    assert out["verdict"] == "REVIEW"
    assert "STATUS_NOT_ACTIVE" in out["reason_codes"]


def test_accounts_overdue_review():
    profile = {
        **ACTIVE_PROFILE,
        "has_charges": False,
        "has_insolvency_history": False,
        "accounts": {"next_accounts": {"due_on": "2020-01-01", "overdue": True}, "overdue": True},
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "REVIEW"
    assert "ACCOUNTS_OVERDUE" in out["reason_codes"]


def test_confirmation_overdue_review():
    profile = {
        **ACTIVE_PROFILE,
        "has_charges": False,
        "has_insolvency_history": False,
        "confirmation_statement": {"next_due": "2020-01-01", "overdue": True},
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "REVIEW"
    assert "CONFIRMATION_OVERDUE" in out["reason_codes"]


def test_charges_alone_do_not_trigger_review():
    profile = {
        **ACTIVE_PROFILE,
        "has_charges": True,
        "has_insolvency_history": False,
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "NORMAL"
    assert "HAS_CHARGES" in out["informational_flags"]
    assert "HAS_CHARGES" not in out["reason_codes"]


def test_historical_insolvency_flag_informational_only():
    """has_insolvency_history alone must not imply current insolvency or force REVIEW."""
    profile = {
        **ACTIVE_PROFILE,
        "company_status": "active",
        "has_charges": False,
        "has_insolvency_history": True,
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "NORMAL"
    assert "HAS_INSOLVENCY_HISTORY" in out["informational_flags"]
    assert out["facts"]["has_insolvency_history"] is True
    assert out["facts"]["current_status_implies_insolvency"] is False
    assert "HAS_INSOLVENCY_HISTORY" not in out["reason_codes"]


def test_current_insolvency_status_triggers_review():
    profile = {
        **ACTIVE_PROFILE,
        "company_status": "liquidation",
        "has_charges": False,
        "has_insolvency_history": True,
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "REVIEW"
    assert "STATUS_NOT_ACTIVE" in out["reason_codes"]
    assert out["facts"]["current_status_implies_insolvency"] is True


def test_upstream_429_is_transport_error_not_review():
    client = FakeCH(fail_status=429)
    with pytest.raises(CompaniesHouseUpstreamError) as ei:
        uk_company_counterparty_preflight(company_number="00445790", client=client)
    assert ei.value.code == "UPSTREAM_UNAVAILABLE"
    assert ei.value.retryable is True


def test_ro_disputed_review():
    profile = {
        **ACTIVE_PROFILE,
        "has_charges": False,
        "has_insolvency_history": False,
        "registered_office_is_in_dispute": True,
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "REVIEW"
    assert "RO_DISPUTED" in out["reason_codes"]


def test_rejects_both_or_neither_input():
    with pytest.raises(ValueError):
        uk_company_counterparty_preflight()
    with pytest.raises(ValueError):
        uk_company_counterparty_preflight(company_number="1", company_name="x")


def test_no_officers_psc_leak():
    profile = {
        **ACTIVE_PROFILE,
        "links": {"officers": "/company/1/officers", "persons_with_significant_control": "/x"},
        "officer_name": "SHOULD_NOT_LEAK",
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    blob = str(out).lower()
    assert "officer" not in blob
    assert "psc" not in blob
    assert "should_not_leak" not in blob


def test_ch_app_unpaid_402_and_fulfill(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from forage.gen1 import ch_app
    from forage.gen1.ch_app import build_ch_app

    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(ch_app, "EVENTS_PATH", events)
    client_fake = FakeCH(profile={**ACTIVE_PROFILE, "has_charges": False, "has_insolvency_history": False})
    monkeypatch.setenv("FORAGE_CH_API_KEY", "test-key")
    monkeypatch.setattr("forage.gen1.ch_app._default_client", lambda: client_fake)
    app = build_ch_app(enable_payments=False)
    tc = TestClient(app)
    r = tc.get("/v1/uk_company_counterparty_preflight", params={"company_number": "00445790"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "NORMAL"
    assert body["meta"]["evidence_class"] in ("SELF_TEST", "UNKNOWN", "EXTERNAL_CANDIDATE")
    assert body["meta"]["price"] == "$0.01"

    app_pay = build_ch_app(enable_payments=True)
    tc2 = TestClient(app_pay)
    r2 = tc2.get(
        "/v1/uk_company_counterparty_preflight",
        params={"company_number": "00445790"},
        headers={"User-Agent": "curl/8.0", "X-Forwarded-For": "203.0.113.9"},
    )
    assert r2.status_code == 402
    access = [json.loads(line) for line in events.read_text().splitlines() if '"ACCESS"' in line]
    unpaid = [e for e in access if e.get("unpaid_402") and e.get("status") == 402]
    assert unpaid, access
    assert unpaid[-1]["client_ip"] == "203.0.113.9"
    assert unpaid[-1]["payment_attempt"] is False


def test_http_client_maps_429_to_upstream_error(monkeypatch):
    import httpx

    from forage.gen1.companies_house import HttpCompaniesHouseClient

    class Resp:
        status_code = 429

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return Resp()

    monkeypatch.setenv("FORAGE_CH_API_KEY", "k")
    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = HttpCompaniesHouseClient(api_key="k")
    with pytest.raises(CompaniesHouseUpstreamError) as ei:
        client.get_company("00445790")
    assert ei.value.code == "UPSTREAM_UNAVAILABLE"
    assert ei.value.retryable is True


def test_ro_undeliverable_review():
    profile = {
        **ACTIVE_PROFILE,
        "has_charges": False,
        "has_insolvency_history": False,
        "undeliverable_registered_office_address": True,
    }
    out = uk_company_counterparty_preflight(company_number="00445790", client=FakeCH(profile=profile))
    assert out["verdict"] == "REVIEW"
    assert "RO_UNDELIVERABLE" in out["reason_codes"]


def test_company_number_not_found():
    class EmptyCH(FakeCH):
        def get_company(self, number: str) -> dict:
            self.calls.append(f"company:{number}")
            return {}

    out = uk_company_counterparty_preflight(company_number="00000000", client=EmptyCH())
    assert out["verdict"] == "NOT_FOUND"


def test_upstream_error_http_shape(monkeypatch):
    from fastapi.testclient import TestClient

    from forage.gen1.ch_app import build_ch_app

    monkeypatch.setenv("FORAGE_CH_API_KEY", "test-key")
    monkeypatch.setattr("forage.gen1.ch_app._default_client", lambda: FakeCH(fail_status=429))
    app = build_ch_app(enable_payments=False)
    tc = TestClient(app)
    r = tc.get("/v1/uk_company_counterparty_preflight", params={"company_number": "00445790"})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "UPSTREAM_UNAVAILABLE"
    assert body["retryable"] is True
    assert "verdict" not in body
