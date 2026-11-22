#!/usr/bin/env bash
set -eu
cd -- "$(dirname -- "$0")"

port="${PORT:-8780}"
base_url="http://127.0.0.1:$port"
if command -v chromium >/dev/null 2>&1; then
  browser=chromium
else
  browser=chromium-browser
fi
if ! command -v "$browser" >/dev/null 2>&1; then
  echo "Chromium is required in the Raspberry Pi desktop session." >&2
  exit 1
fi

python3 server.py --port "$port" &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

python3 - "$base_url" "$server_pid" <<'PY'
import os
import sys
import time
import urllib.request

for _ in range(50):
    try:
        os.kill(int(sys.argv[2]), 0)
    except ProcessLookupError:
        sys.exit("Guangzai server exited during startup.")
    try:
        with urllib.request.urlopen(sys.argv[1] + "/api/health", timeout=0.5) as reply:
            if reply.status == 200:
                sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit("Guangzai server did not become ready.")
PY

if [ "${GUANGZAI_VIEW:-projection}" = "console" ]; then
  display_url="$base_url/"
else
  display_url="$base_url/?projection=1"
fi
# Use a persistent profile so projection calibration and the pet save survive reboot.
"$browser" --kiosk --no-first-run --noerrdialogs \
  --user-data-dir="${XDG_DATA_HOME:-$HOME/.local/share}/guangzai-chromium" "$display_url"
