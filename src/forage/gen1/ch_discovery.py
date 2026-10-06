"""CH-only Bazaar visibility check (independent of Gen-1 discovery needles)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from forage.gen1.discovery import CDP_DISCOVERY, CDP_MERCHANT, CDP_SEARCH

REPORT_PATH = Path("data/gen1_ch/bazaar_discovery.json")
NEEDLE = "uk_company_counterparty_preflight"


def check_ch_bazaar_visibility(public_base_url: str, pay_to: str) -> dict[str, Any]:
    base = public_base_url.rstrip("/")
    host = base.replace("https://", "").replace("http://", "")
    found: list[str] = []
    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        r = client.get(CDP_MERCHANT, params={"payTo": pay_to, "limit": 100})
        if r.status_code == 200:
            for it in r.json().get("resources") or []:
                res = str(it.get("resource") or "")
                if host in res and NEEDLE in res:
                    found.append(res)

        if not found:
            for offset in range(0, 500, 100):
                r = client.get(CDP_DISCOVERY, params={"type": "http", "limit": 100, "offset": offset})
                if r.status_code != 200:
                    break
                items = r.json().get("items") or []
                if not items:
                    break
                for it in items:
                    res = str(it.get("resource") or "")
                    if host in res and NEEDLE in res:
                        found.append(res)
                if len(items) < 100:
                    break

        for q in (NEEDLE, host, "uk company counterparty"):
            r = client.get(CDP_SEARCH, params={"query": q, "limit": 20})
            if r.status_code != 200:
                continue
            data = r.json()
            for it in data.get("resources") or data.get("items") or []:
                res = str(it.get("resource") or "")
                if host in res and NEEDLE in res:
                    found.append(res)

    unique = sorted(set(found))
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "CH_BAZAAR_DISCOVERY_CHECK",
        "evidence_class": "SELF_TEST",
        "public_base_url": base,
        "pay_to": pay_to,
        "visible_resources": unique,
        "route_visible": any(NEEDLE in u for u in unique),
        "note": "Independent of Gen-1 A/B/C needles.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    return report
