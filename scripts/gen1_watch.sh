#!/usr/bin/env bash
# Gen-1 external-demand watch — SELF_TEST never counts.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"
set -a; [[ -f .env ]] && . ./.env; set +a

GATES="$ROOT/data/gen1/gates.json"
OUT="$ROOT/data/gen1/watch_log.jsonl"
REPORT24="$ROOT/data/gen1/read_24h.json"

python3 - <<'PY'
import json, time
from datetime import datetime, timezone
from pathlib import Path

root = Path(".")
gates = json.loads((root / "data/gen1/gates.json").read_text())
first_read = datetime.fromisoformat(gates["first_read_at"])
hard_stop = datetime.fromisoformat(gates["hard_stop_at"])
events_path = root / "data/gen1/events.jsonl"
deny = set()
dw = root / "data/gen1/controlled_wallets.json"
if dw.exists():
    deny = {w.lower() for w in json.loads(dw.read_text()).get("wallets", [])}

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

def metrics():
    fulfills = [e for e in load_events() if e.get("kind") == "FULFILL"]
    # Only EXTERNAL_CANDIDATE with non-controlled payer
    ext = []
    for e in fulfills:
        payer = (e.get("payer") or "").lower()
        if e.get("evidence_class") == "EXTERNAL_CANDIDATE" and payer and payer not in deny:
            ext.append(e)
        elif e.get("evidence_class") == "SELF_TEST":
            continue
    buyers = sorted({e["payer"].lower() for e in ext if e.get("payer")})
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "fulfill_events": len(fulfills),
        "self_test_fulfills": sum(1 for e in fulfills if e.get("evidence_class") == "SELF_TEST"),
        "external_candidate_events": len(ext),
        "unique_external_buyers": len(buyers),
        "external_buyers": buyers,
        "verified_external_revenue_usd": "0",  # settlements need payment-response audit; keep 0 until confirmed external settle
        "actual_spend_usd": "0",
    }

# Poll until first read or hard stop
while True:
    now = datetime.now(timezone.utc)
    m = metrics()
    with (root / "data/gen1/watch_log.jsonl").open("a") as f:
        f.write(json.dumps(m) + "\n")
    (root / "data/gen1/live_metrics.json").write_text(json.dumps(m, indent=2))
    if m["unique_external_buyers"] > 0:
        (root / "data/gen1/E5_SIGNAL.json").write_text(json.dumps(m, indent=2))
        print("E5_SIGNAL", m)
        break
    if now >= first_read:
        report = {
            **m,
            "kind": "READ_24H",
            "listed_at": gates["listed_at"],
            "public_base_url": gates["public_base_url"],
            "gates": gates,
            "verdict": "no_external_buyer" if m["unique_external_buyers"] == 0 else "external_buyer_seen",
        }
        (root / "data/gen1/read_24h.json").write_text(json.dumps(report, indent=2))
        print("READ_24H", report["verdict"])
        break
    if now >= hard_stop:
        print("HARD_STOP")
        break
    time.sleep(300)
PY
