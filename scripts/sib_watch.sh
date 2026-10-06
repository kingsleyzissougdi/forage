#!/usr/bin/env bash
# Sibling SKU clocks — independent of CH. SELF_TEST never E5/E6.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"
set -a; [[ -f .env ]] && . ./.env; set +a
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif [[ -x /opt/forage/.venv/bin/python ]]; then
  PYTHON="/opt/forage/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" - <<'PY'
import json
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from forage.gen1.sib_discovery import check_sib_bazaar_visibility
from forage.gen1.sib_intent import classify_sib_access

root = Path(".")
meta = root / "data/gen1_sib"
gates_all = json.loads((meta / "gates.json").read_text())
public = (gates_all.get("public_base_url") or "").rstrip("/")
pay_to = gates_all.get("pay_to") or ""
events_path = meta / "events.jsonl"
deny = set()
for dw in (meta / "controlled_wallets.json", root / "data/gen1/controlled_wallets.json", root / "data/gen1_ch/controlled_wallets.json"):
    if dw.exists():
        deny |= {w.lower() for w in json.loads(dw.read_text()).get("wallets", [])}

health_ticks = 0
health_ok = 0
PRICE_D = {
    "oneshot_wallet_token_snapshot": Decimal("0.008"),
    "wallet_payment_readiness": Decimal("0.005"),
    "x402_resource_preflight": Decimal("0.009"),
    "call_before_paying_x402": Decimal("0.009"),
    "capability_resolver": Decimal("0.01"),
    "x402_failover_pick": Decimal("0.008"),
    "x402_budget_check": Decimal("0.006"),
    "x402_option_compat": Decimal("0.006"),
    "onchain_entity_brief": Decimal("0.01"),
}
MC_D = {
    "oneshot_wallet_token_snapshot": Decimal("0.0004"),
    "wallet_payment_readiness": Decimal("0.0005"),
    "x402_resource_preflight": Decimal("0.0008"),
    "call_before_paying_x402": Decimal("0.0008"),
    "capability_resolver": Decimal("0.0002"),
    "x402_failover_pick": Decimal("0.0002"),
    "x402_budget_check": Decimal("0.0002"),
    "x402_option_compat": Decimal("0.0004"),
    "onchain_entity_brief": Decimal("0.0006"),
}


def load_events():
    if not events_path.exists():
        return []
    out = []
    for line in events_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def since(events, listed_at):
    out = []
    for e in events:
        ts = e.get("ts")
        if not ts:
            continue
        try:
            if datetime.fromisoformat(ts) >= listed_at:
                out.append(e)
        except Exception:
            pass
    return out


def probe_health() -> None:
    import urllib.request

    global health_ticks, health_ok
    health_ticks += 1
    try:
        with urllib.request.urlopen("http://127.0.0.1:4023/sib/health", timeout=5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    if ok:
        health_ok += 1


def metrics_for(org: str, listed_at: datetime):
    events = since(load_events(), listed_at)
    org_e = [e for e in events if e.get("organism") == org]
    fulfills = [e for e in org_e if e.get("kind") == "FULFILL"]
    access = [e for e in org_e if e.get("kind") == "ACCESS"]
    tagged = [{**e, **classify_sib_access(e)} for e in access]
    buckets = Counter(e["bucket"] for e in tagged)
    unpaid = [e for e in tagged if e.get("unpaid_402") or e.get("status") == 402]
    intent = [e for e in tagged if e.get("intentful")]
    indexer = [e for e in tagged if e["bucket"] == "indexer_or_crawler"]
    schema = [e for e in tagged if e["bucket"] == "schema_example"]
    pay_attempts = [e for e in tagged if e.get("payment_attempt")]
    pay_fails = [e for e in tagged if e.get("payment_attempt_failed")]
    ips = []
    for e in intent:
        ip = e.get("client_ip")
        if ip:
            ips.append(ip)
    ip_c = Counter(ips)
    unique_req = len(ip_c)
    repeat_req = sum(1 for n in ip_c.values() if n > 1)
    ext = []
    for e in fulfills:
        payer = (e.get("payer") or "").lower()
        if e.get("evidence_class") == "EXTERNAL_CANDIDATE" and payer and payer not in deny:
            ext.append(e)
    buyers = sorted({e["payer"].lower() for e in ext if e.get("payer")})
    paid_calls = len(ext)
    hours = max((datetime.now(timezone.utc) - listed_at).total_seconds() / 3600.0, 1e-6)
    intent_per_h = round(len(intent) / hours, 4)
    if paid_calls > 0:
        dclass = "E6_CANDIDATE"
    elif pay_attempts:
        dclass = "PAYMENT_DQ" if not ext else "pay_attempt"
    elif intent:
        dclass = "intentful_no_pay"
    elif unpaid:
        dclass = "indexer_or_schema_only"
    elif access:
        dclass = "discovered_no_pay"
    else:
        dclass = "no_external_discovery"
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "organism": org,
        "listed_at": listed_at.isoformat(),
        "public_base_url": public,
        "exposure_hours": round(hours, 4),
        "intentful_requests": len(intent),
        "intentful_requests_per_exposure_hour": intent_per_h,
        "indexer_or_crawler_probes": len(indexer),
        "schema_example_probes": len(schema),
        "empty_query": buckets.get("empty_query", 0),
        "health_checks_local": health_ticks,
        "unique_external_requesters": unique_req,
        "repeat_external_requesters": repeat_req,
        "fulfill_events": len(fulfills),
        "self_test_fulfills": sum(1 for e in fulfills if e.get("evidence_class") == "SELF_TEST"),
        "external_candidate_events": len(ext),
        "unique_external_buyers": len(buyers),
        "paid_calls_external": paid_calls,
        "verified_external_revenue_usd": str(PRICE_D[org] * paid_calls),
        "marginal_cost_usd": str(MC_D[org]),
        "contribution_profit_usd": str(PRICE_D[org] * paid_calls - MC_D[org] * paid_calls),
        "external_unpaid_402": len(unpaid),
        "payment_attempts": len(pay_attempts),
        "payment_attempts_failed": len(pay_fails),
        "buckets": dict(buckets),
        "uptime_probe_ok_pct": round(100.0 * health_ok / health_ticks, 2) if health_ticks else None,
        "demand_class": dclass,
        "actual_spend_usd": "0",
        "note": "SELF_TEST excluded from E5/E6. Intentful = external non-crawler valid non-example input.",
    }


def bazaar():
    try:
        return check_sib_bazaar_visibility(public, pay_to)
    except Exception as exc:  # noqa: BLE001
        return {"all_visible": False, "error": f"{type(exc).__name__}: {exc}"}


def maybe_write(odir: Path, name: str, now: datetime, when: datetime, kind: str, m: dict, g: dict) -> None:
    if now >= when and not (odir / name).exists():
        b = bazaar()
        appearing = (b.get("intent_queries_appearing") or {}).get(m["organism"], 0)
        report = {
            **m,
            "kind": kind,
            "gates": g,
            "bazaar": b,
            "indexed_discoverable": bool((b.get("by_organism") or {}).get(m["organism"])),
            "intent_queries_appearing": appearing,
            "verdict": m["demand_class"],
        }
        (odir / name).write_text(json.dumps(report, indent=2, default=str))
        print(kind, m["organism"], m["demand_class"], "intent_per_h", m["intentful_requests_per_exposure_hour"])


while True:
    now = datetime.now(timezone.utc)
    probe_health()
    organisms = gates_all["organisms"]
    live = {}
    e5 = False
    for org, g in organisms.items():
        listed = datetime.fromisoformat(g["listed_at"])
        m = metrics_for(org, listed)
        live[org] = m
        odir = meta / org
        odir.mkdir(parents=True, exist_ok=True)
        (odir / "live_metrics.json").write_text(json.dumps(m, indent=2))
        with (odir / "watch_log.jsonl").open("a") as f:
            f.write(json.dumps(m) + "\n")
        if m["unique_external_buyers"] > 0 and not (odir / "E5_SIGNAL.json").exists():
            (odir / "E5_SIGNAL.json").write_text(json.dumps(m, indent=2))
            (odir / "E6_SIGNAL.json").write_text(json.dumps({**m, "kind": "E6_CANDIDATE"}, indent=2))
            print("E5_SIGNAL", org)
            e5 = True
        maybe_write(odir, "read_1h.json", now, datetime.fromisoformat(g["read_1h_at"]), "READ_1H", m, g)
        maybe_write(odir, "read_3h.json", now, datetime.fromisoformat(g["read_3h_at"]), "READ_3H", m, g)
        maybe_write(odir, "read_6h.json", now, datetime.fromisoformat(g["read_6h_at"]), "READ_6H", m, g)
        maybe_write(odir, "read_24h.json", now, datetime.fromisoformat(g["read_24h_at"]), "READ_24H", m, g)
        if "read_72h_at" in g:
            maybe_write(odir, "read_72h.json", now, datetime.fromisoformat(g["read_72h_at"]), "READ_72H", m, g)
    (meta / "live_metrics.json").write_text(
        json.dumps(
            {
                "ts": now.isoformat(),
                "uptime_probe_ok_pct": round(100.0 * health_ok / health_ticks, 2) if health_ticks else None,
                "organisms": live,
            },
            indent=2,
        )
    )
    if e5 or any((meta / org / "E5_SIGNAL.json").exists() for org in organisms):
        break
    time.sleep(180)
PY
