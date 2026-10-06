#!/usr/bin/env bash
# Supervise Cloudflare Quick Tunnel → Gen-1 (:4021). No Cloudflare account.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

META_DIR="${ROOT}/data/gen1"
mkdir -p "$META_DIR"
URL_FILE="${META_DIR}/public_url.txt"
LOG_FILE="${META_DIR}/cloudflared.log"
PID_FILE="${META_DIR}/cloudflared.pid"
STATE_FILE="${META_DIR}/tunnel_state.json"
TARGET_URL="${FORAGE_GEN1_LOCAL_URL:-http://127.0.0.1:4021}"

write_state() {
  local status="$1"
  local url="$2"
  local pid="${3:-}"
  python3 -c "
import json
from datetime import datetime, timezone
from pathlib import Path
Path(r'''${STATE_FILE}''').write_text(json.dumps({
  'status': '''${status}''',
  'public_base_url': ('''${url}''' or None),
  'target': '''${TARGET_URL}''',
  'updated_at': datetime.now(timezone.utc).isoformat(),
  'pid': int('''${pid}''') if '''${pid}'''.isdigit() else None,
}, indent=2))
"
}

extract_url_from_log() {
  python3 -c "
import re, pathlib
p = pathlib.Path(r'''${LOG_FILE}''')
if not p.exists():
    raise SystemExit(0)
m = re.findall(r'https://[a-zA-Z0-9.-]+\.trycloudflare\.com', p.read_text(errors='ignore'))
print(m[-1] if m else '', end='')
"
}

while true; do
  write_state "starting" "" ""
  : >"$LOG_FILE"
  cloudflared tunnel --url "$TARGET_URL" --no-autoupdate >>"$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" >"$PID_FILE"
  url=""
  for _ in $(seq 1 60); do
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    url="$(extract_url_from_log)"
    if [[ -n "$url" ]]; then
      echo "$url" >"$URL_FILE"
      write_state "up" "$url" "$pid"
      echo "tunnel_url=$url pid=$pid"
      break
    fi
  done
  if [[ -z "$url" ]]; then
    write_state "failed" "" "$pid"
    kill "$pid" 2>/dev/null || true
    sleep 3
    continue
  fi
  wait "$pid" || true
  write_state "restarting" "$url" ""
  sleep 2
done
