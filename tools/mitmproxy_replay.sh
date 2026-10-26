#!/usr/bin/env bash
# mitmproxy_replay.sh: serve a previously-recorded flow file as deterministic
# responses to the phone, so the app sees the same network state on every cell.
#
# WHY: the matched-pair (baseline vs bangcle) energy comparison is only valid
# if both runs see the same network responses with the same timing. Replay
# guarantees that.
#
# USAGE:
#   tools/mitmproxy_replay.sh <app-slug>             # latest flow for that app
#   tools/mitmproxy_replay.sh <app-slug> --flow FILE # specific flow
#   tools/mitmproxy_replay.sh <app-slug> --port 8888
#   tools/mitmproxy_replay.sh <app-slug> --phone pixel3
#
# Replay behaviour:
#   --server-replay <flow>           # serve responses from the flow
#   --server-replay-extra forward    # if app asks for something not in flow,
#                                    # forward upstream (fallback to live)
#                                    # (or `kill` to reject: strict)
#   --set server_replay_reuse=true   # responses can be served multiple times
#                                    # (matters for re-runs within one cell)

set -euo pipefail

ROOT="/home/irena/Documents/Master Experiment"
APP=""
FLOW=""
PORT=8080
PHONE=""
STRICT=0   # if 1, reject requests not in the flow file

while [ $# -gt 0 ]; do
    case "$1" in
        --flow)   FLOW="$2"; shift 2 ;;
        --port)   PORT="$2"; shift 2 ;;
        --phone)  PHONE="$2"; shift 2 ;;
        --strict) STRICT=1; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *)        APP="$1"; shift ;;
    esac
done

if [ -z "$APP" ]; then
    echo "Usage: $0 <app-slug> [--flow FILE] [--port N] [--phone pixel3|pixel6|pixel9] [--strict]" >&2
    exit 2
fi

FLOWS_DIR="$ROOT/_external/mitm_flows/$APP"

# Pick latest if not specified
if [ -z "$FLOW" ]; then
    FLOW=$(ls -t "$FLOWS_DIR"/*.flow 2>/dev/null | head -1)
fi
if [ -z "$FLOW" ] || [ ! -f "$FLOW" ]; then
    echo "ERROR: no flow file for app=$APP under $FLOWS_DIR" >&2
    exit 3
fi

HOST_IP=$(hostname -I | awk '{print $1}')

if [ -n "$PHONE" ]; then
    # Replicators put their own phones' USB serials here.
    case "$PHONE" in
        pixel3) SERIAL="<pixel3-serial>" ;;
        pixel6) SERIAL="<pixel6-serial>" ;;
        pixel9) SERIAL="<pixel9-serial>" ;;
        *) echo "Unknown phone: $PHONE" >&2; exit 2 ;;
    esac
    timeout 5 adb -s "$SERIAL" shell "settings put global http_proxy ${HOST_IP}:${PORT}" 2>&1
    trap "timeout 5 adb -s $SERIAL shell 'settings put global http_proxy :0' 2>&1; true" EXIT INT TERM
fi

EXTRA_BEHAVIOUR="forward"
[ "$STRICT" -eq 1 ] && EXTRA_BEHAVIOUR="kill"

echo "============ mitmproxy REPLAY ============"
echo "  app:        $APP"
echo "  flow:       $FLOW"
echo "  listen:     0.0.0.0:$PORT"
echo "  host IP:    $HOST_IP"
echo "  on-miss:    $EXTRA_BEHAVIOUR ($([ $STRICT -eq 1 ] && echo strict || echo lenient))"
echo

# 2026-07-27: mitmdump lives in the appium_android_tests venv (not on system PATH).
# Prefer the venv binary; fall back to PATH lookup for other environments.
MITMDUMP="${ROOT}/appium_android_tests/.venv/bin/mitmdump"
if [ ! -x "$MITMDUMP" ]; then
    MITMDUMP="$(command -v mitmdump || true)"
fi
if [ -z "$MITMDUMP" ]; then
    echo "ERROR: mitmdump not found. Install mitmproxy in the appium venv:" >&2
    echo "  source appium_android_tests/.venv/bin/activate && pip install mitmproxy" >&2
    exit 4
fi
"$MITMDUMP" \
  --listen-host 0.0.0.0 --listen-port "$PORT" \
  --server-replay "$FLOW" \
  --set "server_replay_extra=$EXTRA_BEHAVIOUR" \
  --set server_replay_reuse=true \
  --set ssl_insecure=true
