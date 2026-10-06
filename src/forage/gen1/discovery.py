"""Gen-1 Bazaar discovery checks — never counts as E5/E6."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

CDP_DISCOVERY = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
CDP_SEARCH = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search"
CDP_MERCHANT = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant"
CDP_VALIDATE = "https://api.cdp.coinbase.com/platform/v2/x402/validate"
REPORT_PATH = Path("data/gen1/bazaar_discovery.json")


def check_bazaar_visibility(public_base_url: str, pay_to: str) -> dict[str, Any]:
    base = public_base_url.rstrip("/")
    host = base.replace("https://", "").replace("http://", "")
    path_needles = [
        "wallet_payment_readiness",
        "oneshot_wallet_token_snapshot",
        "onchain_entity_brief",
    ]
    found: list[str] = []
    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        r = client.get(CDP_MERCHANT, params={"payTo": pay_to, "limit": 50})
        if r.status_code == 200:
            for it in r.json().get("resources") or []:
                res = str(it.get("resource") or "")
                if host in res:
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
                    if host in res:
                        found.append(res)
                if len(items) < 100:
                    break

        search_hits: list[str] = []
        for q in path_needles + [host]:
            r = client.get(CDP_SEARCH, params={"query": q, "limit": 20})
            if r.status_code != 200:
                continue
            data = r.json()
            for it in data.get("resources") or data.get("items") or []:
                res = str(it.get("resource") or "")
                if host in res:
                    search_hits.append(res)

    unique = sorted(set(found + search_hits))
    by_route = {p: [u for u in unique if p in u] for p in path_needles}
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "BAZAAR_DISCOVERY_CHECK",
        "evidence_class": "SELF_TEST",
        "public_base_url": base,
        "pay_to": pay_to,
        "visible_resources": unique,
        "by_route": by_route,
        "all_routes_visible": all(by_route[p] for p in path_needles),
        "note": ("Merchant lookup /v2/x402/discovery/merchant?payTo= is authoritative for indexing."),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    return report
