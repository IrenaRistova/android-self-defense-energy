#!/usr/bin/env bash
# record_via_scenario.sh: start mitmdump in record mode, set HTTP proxy on a
# phone, drive that app's Appium scenario via run_workload(), then stop and
# verify the captured flow file.
#
# Output:
#   _external/mitm_flows/<slug>/<timestamp>.flow
#   tools/log/record_<slug>_<phone>_<timestamp>.log
#
# Usage:
#   tools/record_via_scenario.sh <module> <phone-addr> [<duration-s>]
#
# Examples:
#   tools/record_via_scenario.sh keepitup <phone-ip>:5555 120
#
# Prerequisites:
#   - Appium server running at http://127.0.0.1:4723 (default)
#   - Phone reachable via ADB
#   - mitmproxy CA cert installed and trusted (already done for cohort apps
#     via NSC patch + cert-on-phone)
#   - Patched APK already installed
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"
cd "$ROOT"
source appium_android_tests/.venv/bin/activate

MODULE="${1:-}"
PHONE="${2:-}"
DURATION="${3:-120}"
HOST_IP=$(hostname -I | awk '{print $1}')
PORT=8080

[[ -z "$MODULE" || -z "$PHONE" ]] && { echo "usage: $0 <module> <phone-addr> [duration]"; exit 1; }

TS=$(date -u +%Y.%m.%d_%H%M%SZ)
FLOWS_DIR="_external/mitm_flows/${MODULE}"
FLOW="${FLOWS_DIR}/${TS}.flow"
LOG_DIR="tools/log"
LOG="${LOG_DIR}/record_${MODULE}_${PHONE//[:.\/]/_}_${TS}.log"
MITM_LOG="${LOG_DIR}/record_${MODULE}_${PHONE//[:.\/]/_}_${TS}.mitm.log"
mkdir -p "$FLOWS_DIR" "$LOG_DIR"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

cleanup() {
  log "cleanup: unsetting proxy on $PHONE"
  adb -s "$PHONE" shell "settings put global http_proxy :0" 2>/dev/null || true
  if [[ -n "${MITM_PID:-}" ]] && kill -0 "$MITM_PID" 2>/dev/null; then
    log "cleanup: stopping mitmdump pid=$MITM_PID"
    kill -INT "$MITM_PID" 2>/dev/null || true
    sleep 1
    kill -KILL "$MITM_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

log "module=$MODULE phone=$PHONE duration=${DURATION}s"
log "host_ip=$HOST_IP mitm_port=$PORT flow=$FLOW"

# 1. preflight
if ! adb -s "$PHONE" shell echo ok 2>/dev/null | grep -q ok; then
  log "ERROR: phone $PHONE unreachable"
  exit 2
fi
if ! curl -sS --max-time 3 http://127.0.0.1:4723/status >/dev/null 2>&1; then
  log "ERROR: Appium server not reachable at http://127.0.0.1:4723"
  exit 3
fi

# 2. start mitmdump
log "starting mitmdump..."
mitmdump --listen-host 0.0.0.0 --listen-port "$PORT" \
         --set ssl_insecure=true -w "$FLOW" \
         >"$MITM_LOG" 2>&1 &
MITM_PID=$!
sleep 2
if ! kill -0 "$MITM_PID" 2>/dev/null; then
  log "ERROR: mitmdump failed to start; see $MITM_LOG"; exit 4
fi
log "mitmdump pid=$MITM_PID"

# 3. set proxy
log "setting proxy on $PHONE → ${HOST_IP}:${PORT}"
adb -s "$PHONE" shell "settings put global http_proxy ${HOST_IP}:${PORT}"

# 4. invoke the scenario
log "invoking run_workload() for module=$MODULE..."
APPIUM_BUILD_LABEL="record_via_scenario_${MODULE}_${TS}" \
APPIUM_SERVER_URL="http://127.0.0.1:4723" \
python3 - "$MODULE" "$PHONE" "$DURATION" <<'PY' 2>&1 | tee -a "$LOG"
import sys, importlib, time
module, phone, duration = sys.argv[1], sys.argv[2], float(sys.argv[3])

class Exp:
    def __init__(self, duration):
        self.duration = duration
        self.package = None  # let scenarios.py default to its PACKAGE constant

class Dev:
    def __init__(self, phone):
        self.id = phone

mod = importlib.import_module(f"appium_android_tests.{module}.scenarios")
exp = Exp(duration)
dev = Dev(phone)
print(f"[runner] calling {module}.run_workload(duration={duration}, device={phone})")
t0 = time.time()
try:
    mod.run_workload(exp, dev)
    print(f"[runner] run_workload returned in {time.time()-t0:.1f}s")
except Exception as ex:
    print(f"[runner] FAIL: {type(ex).__name__}: {ex}")
    raise
PY

WORKLOAD_RC=${PIPESTATUS[0]:-1}
log "workload exit code: $WORKLOAD_RC"

# 5. stop mitmdump cleanly so flow file is fully flushed
log "stopping mitmdump..."
kill -INT "$MITM_PID" 2>/dev/null || true
wait "$MITM_PID" 2>/dev/null || true
MITM_PID=""
sleep 1

# 6. verify
log "verifying flow file..."
if [[ -f "$FLOW" && -s "$FLOW" ]]; then
  FLOW_COUNT=$(python3 -c "
from mitmproxy.io import FlowReader
with open('$FLOW', 'rb') as f:
    flows = list(FlowReader(f).stream())
print(len(flows))
" 2>/dev/null || echo 0)
  FLOW_SIZE=$(stat -c%s "$FLOW")
  log "✓ flow file: $FLOW (size=$FLOW_SIZE bytes, $FLOW_COUNT flows)"
else
  log "⚠ flow file empty or missing: $FLOW"
  FLOW_COUNT=0
fi

# 7. show host breakdown if any flows
if (( FLOW_COUNT > 0 )); then
  log "host breakdown:"
  python3 -c "
from mitmproxy.io import FlowReader
from collections import Counter
with open('$FLOW', 'rb') as f:
    flows = list(FlowReader(f).stream())
counts = Counter((f.request.host, f.response.status_code if f.response else 'no_resp') for f in flows)
for (host, status), n in sorted(counts.items(), key=lambda x: -x[1]):
    print(f'  {n:3d}× [{status}] {host}')
" 2>&1 | tee -a "$LOG"
fi

log "DONE — exit code $WORKLOAD_RC"
exit "$WORKLOAD_RC"
