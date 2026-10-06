#!/usr/bin/env bash
# Independent CH preflight market clock — SELF_TEST never counts as E5/E6.
# Formal window starts at gates.listed_at (droplet durable exposure).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"
set -a; [[ -f .env ]] && . ./.env; set +a
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" - <<'PY'
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

root = Path(".")
meta = root / "data/gen1_ch"
gates = json.loads((meta / "gates.json").read_text())
listed_at = datetime.fromisoformat(gates["listed_at"])
first_read = datetime.fromisoformat(gates["first_read_at"])
second_read = datetime.fromisoformat(gates["second_read_at"])
hard_stop = datetime.fromisoformat(gates["hard_stop_at"])
public = (gates.get("public_base_url") or "").rstrip("/")
pay_to = gates.get("pay_to") or ""
events_path = meta / "events.jsonl"
deny = set()
for dw in (meta / "controlled_wallets.json", root / "data/gen1/controlled_wallets.json"):
    if dw.exists():
        deny |= {w.lower() for w in json.loads(dw.read_text()).get("wallets", [])}

PRICE = Decimal("0.01")
MC = Decimal("0.0001")
health_ticks = 0
health_ok = 0


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


def since_listed(events):
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


def probe_health() -> bool:
    global health_ticks, health_ok
    health_ticks += 1
    try:
        with urllib.request.urlopen("http://127.0.0.1:4022/health", timeout=5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    if ok:
        health_ok += 1
    return ok


def bazaar_visible() -> dict:
    try:
        from forage.gen1.ch_discovery import check_ch_bazaar_visibility

        if not public or not pay_to:
            return {"route_visible": None, "error": "missing public/pay_to"}
        return check_ch_bazaar_visibility(public, pay_to)
    except Exception as exc:  # noqa: BLE001
        return {"route_visible": None, "error": f"{type(exc).__name__}: {exc}"}


def demand_class(m: dict) -> str:
    if m["unique_external_buyers"] > 0:
        return "demand_paid"
    if m["payment_attempts_failed"] > 0:
        return "pay_attempt_failed"
    if m["external_unpaid_402"] > 0 or m["external_paid_route_hits"] > 0:
        return "discovered_no_pay"
    return "no_external_discovery"


def metrics():
    events = since_listed(load_events())
    fulfills = [e for e in events if e.get("kind") == "FULFILL"]
    upstream_err = [e for e in events if e.get("kind") == "UPSTREAM_ERROR"]
    access = [e for e in events if e.get("kind") == "ACCESS"]
    paid_route = [e for e in access if str(e.get("path") or "").startswith("/v1/uk_company_counterparty_preflight")]
    unpaid = [e for e in paid_route if e.get("unpaid_402")]
    pay_attempts = [e for e in paid_route if e.get("payment_attempt")]
    pay_fails = [e for e in paid_route if e.get("payment_attempt_failed")]
    crawler_hits = [e for e in paid_route if e.get("crawler_ua")]
    from forage.gen1.ch_traffic import summarize_access

    traffic = summarize_access(access)
    ext = []
    for e in fulfills:
        payer = (e.get("payer") or "").lower()
        if e.get("evidence_class") == "EXTERNAL_CANDIDATE" and payer and payer not in deny:
            ext.append(e)
    buyers = sorted({e["payer"].lower() for e in ext if e.get("payer")})
    ips = sorted({e["client_ip"] for e in paid_route if e.get("client_ip")})
    paid_calls = len(ext)
    revenue = PRICE * paid_calls
    ch_calls = sum(int(e.get("upstream_calls") or 0) for e in fulfills)
    latencies = [float(e["elapsed_s"]) for e in fulfills if e.get("elapsed_s") is not None]
    avg_lat = round(sum(latencies) / len(latencies), 4) if latencies else None
    contrib = revenue - (MC * paid_calls)
    uptime_pct = round(100.0 * health_ok / health_ticks, 2) if health_ticks else None
    m = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "organism": "uk_company_counterparty_preflight",
        "listed_at": gates["listed_at"],
        "public_base_url": public,
        "window": "formal_droplet_listed_at",
        "fulfill_events": len(fulfills),
        "self_test_fulfills": sum(1 for e in fulfills if e.get("evidence_class") == "SELF_TEST"),
        "external_candidate_events": len(ext),
        "unique_external_buyers": len(buyers),
        "external_buyers": buyers,
        "paid_calls_external": paid_calls,
        "verified_external_revenue_usd": str(revenue),
        "contribution_profit_usd": str(contrib),
        "ch_upstream_calls": ch_calls,
        "avg_fulfill_latency_s": avg_lat,
        "upstream_errors": len(upstream_err),
        "external_paid_route_hits": len(paid_route),
        "external_unpaid_402": len(unpaid),
        "payment_attempts": len(pay_attempts),
        "payment_attempts_failed": len(pay_fails),
        "unique_external_ips": len(ips),
        "external_ips": ips[:50],
        "crawler_ua_hits": len(crawler_hits),
        "traffic": traffic,
        "uptime_probe_ok_pct": uptime_pct,
        "uptime_probe_ticks": health_ticks,
        "actual_spend_usd": "0",
        "note": (
            "Formal window = gates.listed_at onward. "
            "E5=unique uncontrolled external FULFILL; E6=settled external payment. "
            "demand_class separates no_discovery / discovered_no_pay / pay_attempt_failed / demand_paid."
        ),
    }
    m["demand_class"] = demand_class(m)
    return m


def write_gate(kind: str, m: dict, bazaar: dict) -> dict:
    report = {
        **m,
        "kind": kind,
        "listed_at": gates["listed_at"],
        "public_base_url": public,
        "gates": gates,
        "bazaar_still_visible": bazaar.get("route_visible"),
        "bazaar_check": {
            "route_visible": bazaar.get("route_visible"),
            "visible_resources": bazaar.get("visible_resources"),
            "error": bazaar.get("error"),
            "ts": bazaar.get("ts"),
        },
        "verdict": m["demand_class"],
        "formal_window_note": "Ignore pre-listed_at / interrupted WSL tunnel exposure for this call.",
    }
    return report


while True:
    now = datetime.now(timezone.utc)
    probe_health()
    m = metrics()
    with (meta / "watch_log.jsonl").open("a") as f:
        f.write(json.dumps(m) + "\n")
    (meta / "live_metrics.json").write_text(json.dumps(m, indent=2))
    if m["unique_external_buyers"] > 0:
        (meta / "E5_SIGNAL.json").write_text(json.dumps(m, indent=2))
        e6 = {
            **m,
            "kind": "E6_CANDIDATE",
            "listed_at": gates["listed_at"],
            "public_base_url": public,
        }
        (meta / "E6_SIGNAL.json").write_text(json.dumps(e6, indent=2))
        print("E5_SIGNAL", m)
        break
    if now >= first_read and not (meta / "read_24h.json").exists():
        report = write_gate("READ_24H", m, bazaar_visible())
        (meta / "read_24h.json").write_text(json.dumps(report, indent=2))
        print("READ_24H", report["verdict"])
    if now >= second_read and not (meta / "read_72h.json").exists():
        report = write_gate("READ_72H", m, bazaar_visible())
        (meta / "read_72h.json").write_text(json.dumps(report, indent=2))
        print("READ_72H", report["verdict"])
    if now >= hard_stop:
        report = write_gate("HARD_STOP_7D", m, bazaar_visible())
        (meta / "hard_stop.json").write_text(json.dumps(report, indent=2))
        print("HARD_STOP", report["verdict"])
        break
    time.sleep(300)
PY
