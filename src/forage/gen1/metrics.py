"""Per-organism economic metrics — SELF_TEST never counts as demand/revenue."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forage.gen1.app import MARGINAL_COST_USD, ORGANISM_AXIS, PRICES
from forage.gen1.denylist import is_controlled

META_PATH = Path("data/gen1/organism_meta.json")
REPORT_PATH = Path("data/gen1/organism_metrics.json")
EVENTS_PATH = Path("data/gen1/events.jsonl")

ORGANISM_NAMES = list(PRICES.keys())


def _load_events() -> list[dict[str, Any]]:
    if not EVENTS_PATH.exists():
        return []
    out = []
    for line in EVENTS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _hours_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - t).total_seconds() / 3600.0, 4)
    except Exception:
        return None


def build_organism_metrics() -> dict[str, Any]:
    meta = {}
    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text()).get("organisms") or {}
        except Exception:
            meta = {}
    events = _load_events()
    unpaid = [e for e in events if e.get("kind") == "UNPAID_402"]
    fulfills = [e for e in events if e.get("kind") == "FULFILL"]

    by: dict[str, Any] = {}
    for name in ORGANISM_NAMES:
        om = meta.get(name) or {}
        listed_at = om.get("listed_at")
        org_fulfills = [e for e in fulfills if e.get("organism") == name]
        org_unpaid = [e for e in unpaid if e.get("organism") == name]
        ext = [e for e in org_fulfills if e.get("evidence_class") == "EXTERNAL_CANDIDATE" and e.get("payer") and not is_controlled(e["payer"])]
        self_tests = [e for e in org_fulfills if e.get("evidence_class") == "SELF_TEST"]
        buyers = sorted({e["payer"].lower() for e in ext if e.get("payer")})
        paid_calls = len(ext)  # external paid fulfills only
        price = PRICES.get(name, "$0")
        try:
            price_f = float(str(price).replace("$", ""))
        except ValueError:
            price_f = 0.0
        try:
            mcost = float(MARGINAL_COST_USD.get(name, "0"))
        except ValueError:
            mcost = 0.0
        revenue = round(price_f * paid_calls, 6)
        contrib = round(revenue - mcost * paid_calls, 6)
        by[name] = {
            "axis": ORGANISM_AXIS.get(name),
            "listed_at": listed_at,
            "hours_exposed": _hours_since(listed_at),
            "price": price,
            "marginal_cost_usd": MARGINAL_COST_USD.get(name),
            "unpaid_external_requests_observed": len(org_unpaid),  # only if we log them
            "402_responses_logged": len(org_unpaid),
            "unique_external_paying_wallets": len(buyers),
            "external_paying_wallets": buyers,
            "paid_calls_external": paid_calls,
            "self_test_fulfills": len(self_tests),
            "revenue_usd_external": str(revenue),
            "repeat_buyers": 0,  # need >1 call/buyer; computed below
            "contribution_profit_usd_external": str(contrib),
        }
        # repeat buyers
        from collections import Counter

        counts = Counter(e["payer"].lower() for e in ext if e.get("payer"))
        by[name]["repeat_buyers"] = sum(1 for _, n in counts.items() if n > 1)

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "ORGANISM_METRICS",
        "rule": "SELF_TEST excluded from demand/revenue",
        "organisms": by,
        "total_exposed": sum(1 for v in by.values() if v.get("listed_at")),
        "cap": 6,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    return report
