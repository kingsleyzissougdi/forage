"""UK_COMPANY_COUNTERPARTY_PREFLIGHT — Companies House company-level preflight.

Company-level public-register facts only. No officers/PSC. No credit/fraud scores.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

CH_BASE = "https://api.company-information.service.gov.uk"
DISCLAIMER = (
    "Registry preflight only. Facts are copied/normalized from the Companies House "
    "public register. Not credit advice, legal advice, KYC, AML screening, or a "
    "clearance decision. Historical insolvency evidence is not a statement of "
    "current insolvency unless company_status indicates otherwise."
)

# Status values that are themselves current insolvency-related on the register
_CURRENT_INSOLVENCY_STATUSES = frozenset(
    {
        "liquidation",
        "receivership",
        "administration",
        "insolvency-proceedings",
        "voluntary-arrangement",
    }
)


class CompaniesHouseUpstreamError(Exception):
    """Transport/upstream failure — never a counterparty verdict."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 503,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable


class CHClient(Protocol):
    def get_company(self, number: str) -> dict[str, Any]: ...

    def search_companies(self, q: str) -> list[dict[str, Any]]: ...


class HttpCompaniesHouseClient:
    """Live Companies House Public Data API client."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 12.0) -> None:
        key = (api_key or os.environ.get("FORAGE_CH_API_KEY", "")).strip()
        if not key:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                "FORAGE_CH_API_KEY_missing",
                http_status=503,
                retryable=False,
            )
        self._auth = (key, "")
        self._timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{CH_BASE}{path}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(url, params=params, auth=self._auth)
        except httpx.TimeoutException as exc:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                "companies_house_timeout",
                http_status=503,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"companies_house_http_error:{type(exc).__name__}",
                http_status=503,
                retryable=True,
            ) from exc

        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"companies_house_http_{r.status_code}",
                http_status=503,
                retryable=True,
            )
        if r.status_code >= 400:
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"companies_house_http_{r.status_code}",
                http_status=503,
                retryable=r.status_code in (408, 429) or r.status_code >= 500,
            )
        return r.json()

    def get_company(self, number: str) -> dict[str, Any]:
        data = self._get(f"/company/{number}")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                "companies_house_bad_payload",
                http_status=503,
                retryable=True,
            )
        return data

    def search_companies(self, q: str) -> list[dict[str, Any]]:
        data = self._get("/search/companies", params={"q": q, "items_per_page": 5})
        if data is None:
            return []
        if not isinstance(data, dict):
            raise CompaniesHouseUpstreamError(
                "UPSTREAM_UNAVAILABLE",
                "companies_house_bad_payload",
                http_status=503,
                retryable=True,
            )
        items = data.get("items") or []
        return [i for i in items if isinstance(i, dict)]


def _addr_summary(addr: dict[str, Any] | None) -> str | None:
    if not addr or not isinstance(addr, dict):
        return None
    parts = [
        addr.get("premises"),
        addr.get("address_line_1"),
        addr.get("address_line_2"),
        addr.get("locality"),
        addr.get("region"),
        addr.get("postal_code"),
        addr.get("country"),
    ]
    return ", ".join(str(p) for p in parts if p)


def _accounts_slice(profile: dict[str, Any]) -> dict[str, Any]:
    accounts = profile.get("accounts") or {}
    next_acc = accounts.get("next_accounts") or {}
    overdue = next_acc.get("overdue")
    if overdue is None:
        overdue = accounts.get("overdue")
    due = next_acc.get("due_on") or accounts.get("next_due")
    return {"next_due": due, "overdue": bool(overdue) if overdue is not None else False}


def _confirmation_slice(profile: dict[str, Any]) -> dict[str, Any]:
    cs = profile.get("confirmation_statement") or {}
    overdue = cs.get("overdue")
    return {
        "next_due": cs.get("next_due"),
        "overdue": bool(overdue) if overdue is not None else False,
    }


def _status_implies_insolvency(status: str | None) -> bool:
    return (status or "").lower() in _CURRENT_INSOLVENCY_STATUSES


def _build_from_profile(profile: dict[str, Any], *, upstream_calls: int, t0: float) -> dict[str, Any]:
    status = (profile.get("company_status") or "").lower() or None
    accounts = _accounts_slice(profile)
    confirmation = _confirmation_slice(profile)
    ro_dispute = bool(profile.get("registered_office_is_in_dispute"))
    ro_undeliverable = bool(profile.get("undeliverable_registered_office_address"))
    has_charges = bool(profile.get("has_charges"))
    has_insolvency_history = bool(profile.get("has_insolvency_history"))
    current_insolvency = _status_implies_insolvency(status)

    reason_codes: list[str] = []
    informational_flags: list[str] = []

    if has_charges:
        informational_flags.append("HAS_CHARGES")
    if has_insolvency_history:
        informational_flags.append("HAS_INSOLVENCY_HISTORY")

    if status and status != "active":
        reason_codes.append("STATUS_NOT_ACTIVE")
    if accounts.get("overdue"):
        reason_codes.append("ACCOUNTS_OVERDUE")
    if confirmation.get("overdue"):
        reason_codes.append("CONFIRMATION_OVERDUE")
    if ro_dispute:
        reason_codes.append("RO_DISPUTED")
    if ro_undeliverable:
        reason_codes.append("RO_UNDELIVERABLE")

    verdict = "REVIEW" if reason_codes else "NORMAL"
    as_of = datetime.now(timezone.utc).isoformat()
    number = str(profile.get("company_number") or "")

    return {
        "organism": "uk_company_counterparty_preflight",
        "verdict": verdict,
        "reason_codes": reason_codes,
        "informational_flags": informational_flags,
        "matched": {
            "company_number": number,
            "company_name": profile.get("company_name"),
            "company_type": profile.get("type"),
            "company_status": status,
            "company_status_detail": profile.get("company_status_detail"),
            "date_of_creation": profile.get("date_of_creation"),
        },
        "registered_office_summary": _addr_summary(profile.get("registered_office_address")),
        "registered_office_flags": {
            "in_dispute": ro_dispute,
            "undeliverable": ro_undeliverable,
        },
        "accounts": accounts,
        "confirmation_statement": confirmation,
        "facts": {
            "has_charges": has_charges,
            "has_insolvency_history": has_insolvency_history,
            "current_status_implies_insolvency": current_insolvency,
            "has_insolvency_history_note": (
                "Historical register flag only; not a claim of current insolvency unless company_status is an insolvency-related status."
            ),
        },
        "registry_as_of": as_of,
        "sources": [
            {
                "name": "Companies House Public Data API",
                "url": f"{CH_BASE}/company/{number}" if number else CH_BASE,
                "retrieved_at": as_of,
            }
        ],
        "disclaimer": DISCLAIMER,
        "upstream_calls": upstream_calls,
        "elapsed_s": round(time.time() - t0, 4),
    }


def uk_company_counterparty_preflight(
    *,
    company_number: str | None = None,
    company_name: str | None = None,
    client: CHClient | None = None,
) -> dict[str, Any]:
    """Run scoped counterparty preflight. Raises CompaniesHouseUpstreamError on transport failure."""
    num = (company_number or "").strip() or None
    name = (company_name or "").strip() or None
    if bool(num) == bool(name):
        raise ValueError("provide_exactly_one_of_company_number_or_company_name")

    ch = client or HttpCompaniesHouseClient()
    t0 = time.time()
    calls = 0

    if name:
        calls += 1
        items = ch.search_companies(name)
        if not items:
            as_of = datetime.now(timezone.utc).isoformat()
            return {
                "organism": "uk_company_counterparty_preflight",
                "verdict": "NOT_FOUND",
                "reason_codes": ["NOT_FOUND"],
                "informational_flags": [],
                "matched": None,
                "registered_office_summary": None,
                "registered_office_flags": None,
                "accounts": None,
                "confirmation_statement": None,
                "facts": None,
                "registry_as_of": as_of,
                "sources": [
                    {
                        "name": "Companies House Public Data API",
                        "url": f"{CH_BASE}/search/companies",
                        "retrieved_at": as_of,
                    }
                ],
                "disclaimer": DISCLAIMER,
                "upstream_calls": calls,
                "elapsed_s": round(time.time() - t0, 4),
            }
        if len(items) > 1:
            # Exact title match (casefold) can uniquify
            exact = [i for i in items if str(i.get("title") or i.get("company_name") or "").casefold() == name.casefold()]
            if len(exact) == 1:
                items = exact
            else:
                as_of = datetime.now(timezone.utc).isoformat()
                return {
                    "organism": "uk_company_counterparty_preflight",
                    "verdict": "REVIEW",
                    "reason_codes": ["NAME_AMBIGUOUS"],
                    "informational_flags": [],
                    "matched": None,
                    "candidates": [
                        {
                            "company_number": i.get("company_number"),
                            "company_name": i.get("title") or i.get("company_name"),
                            "company_status": i.get("company_status"),
                        }
                        for i in items[:5]
                    ],
                    "registered_office_summary": None,
                    "registered_office_flags": None,
                    "accounts": None,
                    "confirmation_statement": None,
                    "facts": None,
                    "registry_as_of": as_of,
                    "sources": [
                        {
                            "name": "Companies House Public Data API",
                            "url": f"{CH_BASE}/search/companies",
                            "retrieved_at": as_of,
                        }
                    ],
                    "disclaimer": DISCLAIMER,
                    "upstream_calls": calls,
                    "elapsed_s": round(time.time() - t0, 4),
                }
        num = str(items[0].get("company_number") or "").strip()
        if not num:
            as_of = datetime.now(timezone.utc).isoformat()
            return {
                "organism": "uk_company_counterparty_preflight",
                "verdict": "NOT_FOUND",
                "reason_codes": ["NOT_FOUND"],
                "informational_flags": [],
                "matched": None,
                "registered_office_summary": None,
                "registered_office_flags": None,
                "accounts": None,
                "confirmation_statement": None,
                "facts": None,
                "registry_as_of": as_of,
                "sources": [
                    {
                        "name": "Companies House Public Data API",
                        "url": f"{CH_BASE}/search/companies",
                        "retrieved_at": as_of,
                    }
                ],
                "disclaimer": DISCLAIMER,
                "upstream_calls": calls,
                "elapsed_s": round(time.time() - t0, 4),
            }

    assert num is not None
    calls += 1
    profile = ch.get_company(num)
    if not profile or not profile.get("company_number"):
        as_of = datetime.now(timezone.utc).isoformat()
        return {
            "organism": "uk_company_counterparty_preflight",
            "verdict": "NOT_FOUND",
            "reason_codes": ["NOT_FOUND"],
            "informational_flags": [],
            "matched": None,
            "registered_office_summary": None,
            "registered_office_flags": None,
            "accounts": None,
            "confirmation_statement": None,
            "facts": None,
            "registry_as_of": as_of,
            "sources": [
                {
                    "name": "Companies House Public Data API",
                    "url": f"{CH_BASE}/company/{num}",
                    "retrieved_at": as_of,
                }
            ],
            "disclaimer": DISCLAIMER,
            "upstream_calls": calls,
            "elapsed_s": round(time.time() - t0, 4),
        }

    return _build_from_profile(profile, upstream_calls=calls, t0=t0)
