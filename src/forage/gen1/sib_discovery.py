"""Sibling Bazaar check — independent of CH needles."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from forage.gen1.discovery import CDP_DISCOVERY, CDP_MERCHANT, CDP_SEARCH
from forage.gen1.sib_intent import INTENT_QUERIES

REPORT_PATH = Path("data/gen1_sib/bazaar_discovery.json")
NEEDLES = tuple(INTENT_QUERIES.keys())


def check_sib_bazaar_visibility(public_base_url: str, pay_to: str) -> dict[str, Any]:
    base = public_base_url.rstrip("/")
    host = base.replace("https://", "").replace("http://", "")
    found: dict[str, list[str]] = {n: [] for n in NEEDLES}
    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        r = client.get(CDP_MERCHANT, params={"payTo": pay_to, "limit": 100})
        if r.status_code == 200:
            for it in r.json().get("resources") or []:
                res = str(it.get("resource") or "")
                if host not in res:
                    continue
                for n in NEEDLES:
                    if n in res:
                        found[n].append(res)
        for n in NEEDLES:
            if found[n]:
                continue
            r = client.get(CDP_SEARCH, params={"query": n, "limit": 20})
            if r.status_code != 200:
                continue
            data = r.json()
            for it in data.get("resources") or data.get("items") or []:
                res = str(it.get("resource") or "")
                if host in res and n in res:
                    found[n].append(res)
        if not any(found.values()):
            r = client.get(CDP_DISCOVERY, params={"type": "http", "limit": 50, "offset": 0})
            if r.status_code == 200:
                for it in r.json().get("items") or []:
                    res = str(it.get("resource") or "")
                    if host not in res:
                        continue
                    for n in NEEDLES:
                        if n in res:
                            found[n].append(res)
        intent_hits: dict[str, dict[str, int]] = {}
        for n, queries in INTENT_QUERIES.items():
            intent_hits[n] = {}
            for q in queries:
                r = client.get(CDP_SEARCH, params={"query": q, "limit": 20})
                if r.status_code != 200:
                    intent_hits[n][q] = -1
                    continue
                data = r.json()
                hits = 0
                for it in data.get("resources") or data.get("items") or []:
                    res = str(it.get("resource") or "")
                    if host in res and n in res:
                        hits += 1
                intent_hits[n][q] = hits
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "SIB_BAZAAR_DISCOVERY_CHECK",
        "evidence_class": "SELF_TEST",
        "public_base_url": base,
        "pay_to": pay_to,
        "by_organism": {k: sorted(set(v)) for k, v in found.items()},
        "intent_query_hits": intent_hits,
        "intent_queries_appearing": {n: sum(1 for v in intent_hits.get(n, {}).values() if v and v > 0) for n in NEEDLES},
        "all_visible": all(found[n] for n in NEEDLES),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    return report


GATES_PATH = Path("data/gen1_sib/gates.json")


def stamp_sib_listed(public_base_url: str, pay_to: str, *, force: bool = False) -> dict[str, Any]:
    """Write listed_at only after Bazaar visibility (unless force)."""
    vis = check_sib_bazaar_visibility(public_base_url, pay_to)
    if not vis.get("all_visible") and not force:
        return {"stamped": False, "reason": "not_all_visible_on_bazaar", "bazaar": vis}
    now = datetime.now(timezone.utc)

    organisms: dict[str, Any] = {}
    for n in NEEDLES:
        organisms[n] = {
            "listed_at": now.isoformat(),
            "read_1h_at": (now + timedelta(hours=1)).isoformat(),
            "read_3h_at": (now + timedelta(hours=3)).isoformat(),
            "read_6h_at": (now + timedelta(hours=6)).isoformat(),
            "read_24h_at": (now + timedelta(hours=24)).isoformat(),
            "read_72h_at": (now + timedelta(hours=72)).isoformat(),
        }
    payload = {
        "public_base_url": public_base_url.rstrip("/"),
        "pay_to": pay_to,
        "listed_at": now.isoformat(),
        "bazaar": vis,
        "organisms": organisms,
        "portfolio": "fast_parallel_discovery",
        "note": "CH listed_at is independent. SELF_TEST never E5/E6.",
    }
    GATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    GATES_PATH.write_text(json.dumps(payload, indent=2, default=str))
    return {"stamped": True, "gates": payload}
