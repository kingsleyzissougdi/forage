#!/usr/bin/env bash
# MCP_INSTALL_FUNNEL_V1 clocks. Does not touch sibling Bazaar listed_at or forage-sib-watch.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
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
from pathlib import Path

root = Path(".")
meta = root / "data/gen1_mcp"
gates = json.loads((meta / "gates.json").read_text())
listed = datetime.fromisoformat(gates["listed_at"])
events_path = meta / "events.jsonl"
deny = set()
for dw in (
    root / "data/gen1/controlled_wallets.json",
    root / "data/gen1_sib/controlled_wallets.json",
    root / "data/gen1_ch/controlled_wallets.json",
):
    if dw.exists():
        deny |= {w.lower() for w in json.loads(dw.read_text()).get("wallets", [])}


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
    return [e for e in out if e.get("ts") and datetime.fromisoformat(e["ts"]) >= listed]


def metrics():
    ev = load_events()
    hours = max((datetime.now(timezone.utc) - listed).total_seconds() / 3600.0, 1e-6)
    inits = [e for e in ev if e.get("kind") in ("MCP_INITIALIZE", "MCP_HTTP") and (e.get("mcp_method") in (None, "initialize") or e.get("kind") == "MCP_INITIALIZE")]
    http = [e for e in ev if e.get("kind") == "MCP_HTTP"]
    ext_http = [
        e
        for e in http
        if e.get("client_ip") not in (None, "", "127.0.0.1", "::1") and not e.get("crawler_ua")
    ]
    tools_list = [e for e in ev if e.get("mcp_method") == "tools/list" or e.get("path", "").endswith("/mcp") and e.get("kind") == "MCP_HTTP"]
    invokes = [e for e in ev if e.get("kind") == "TOOL_INVOKE"]
    intent = [e for e in invokes if e.get("intentful")]
    sessions = sorted({e.get("session_id") for e in ev if e.get("session_id")})
    ips = Counter(e.get("client_ip") for e in http if e.get("client_ip"))
    friction = sum(1 for e in invokes if e.get("friction") == "PAYMENT_BOOTSTRAP_FRICTION")
    x402s = sum(1 for e in invokes if e.get("backend_x402_402"))
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "listed_at": listed.isoformat(),
        "exposure_hours": round(hours, 4),
        "mcp_initialize_events": len([e for e in ev if e.get("kind") == "MCP_INITIALIZE"]) + sum(1 for e in http if e.get("mcp_method") == "initialize"),
        "mcp_http_events": len(http),
        "mcp_http_external": len(ext_http),
        "tools_list_events": sum(1 for e in ext_http if e.get("mcp_method") == "tools/list"),
        "unique_sessions": len(sessions),
        "unique_client_ips": len({e.get("client_ip") for e in ext_http if e.get("client_ip")}),
        "tool_invocations": len(invokes),
        "intentful_invocations": len(intent),
        "example_invocations": sum(1 for e in invokes if e.get("example_input")),
        "backend_x402_402s": x402s,
        "payment_attempts": sum(1 for e in ev if e.get("payment_attempt")),
        "paid_calls_external": 0,
        "payment_bootstrap_friction_flags": friction,
        "demand_class": (
            "intentful_mcp"
            if intent
            else             "mcp_session_no_intent"
            if ext_http
            else "no_external_discovery"
        ),
    }


def maybe(name, when_iso, kind, m):
    when = datetime.fromisoformat(when_iso)
    dest = meta / name
    if datetime.now(timezone.utc) >= when and not dest.exists():
        dest.write_text(json.dumps({**m, "kind": kind, "gates": gates}, indent=2))
        print(kind, m.get("demand_class"), "intent", m.get("intentful_invocations"))


while True:
    m = metrics()
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "live_metrics.json").write_text(json.dumps(m, indent=2))
    maybe("read_1h.json", gates["read_1h_at"], "READ_1H", m)
    maybe("read_3h.json", gates["read_3h_at"], "READ_3H", m)
    maybe("read_6h.json", gates["read_6h_at"], "READ_6H", m)
    maybe("read_24h.json", gates["read_24h_at"], "READ_24H", m)
    if m["intentful_invocations"] > 0 and not (meta / "EXTERNAL_MCP_INVOKE.json").exists():
        (meta / "EXTERNAL_MCP_INVOKE.json").write_text(json.dumps(m, indent=2))
        print("EXTERNAL_MCP_INVOKE")
    time.sleep(60)
PY
