#!/usr/bin/env bash
# pause_sweep.sh: politely ask a running sweep to stop after its current cells.
#
# WHAT IT DOES:
#   1. Touches `$STATE_DIR/.pause`.
#   2. Each phone stream checks for that file BEFORE each cell. If present,
#      the stream finishes its current cell and exits cleanly.
#   3. State file is preserved. Re-launch the same command + `--resume` to
#      pick up exactly where you left off.
#
# USAGE:
#   tools/pause_sweep.sh                            # auto-detect newest tag
#   tools/pause_sweep.sh <sweep-tag>               # specific tag
#
# After pausing, wait until each stream's master log reports
# "exiting stream cleanly" before killing the parent process.

set -u

ROOT="/home/irena/Documents/Master Experiment"
LOG_DIR="$ROOT/tools/sweep_logs"

TAG="${1:-}"
if [ -z "$TAG" ]; then
    # Pick the newest state dir
    newest=$(ls -td "$LOG_DIR"/state_*/ 2>/dev/null | head -1)
    if [ -z "$newest" ]; then
        echo "ERROR: no state_*/ directories found under $LOG_DIR" >&2
        exit 2
    fi
    TAG=$(basename "$newest" | sed 's/^state_//')
    echo "Using newest state dir → tag=$TAG"
fi

STATE_DIR="$LOG_DIR/state_${TAG}"
if [ ! -d "$STATE_DIR" ]; then
    echo "ERROR: state dir not found: $STATE_DIR" >&2
    exit 2
fi

PAUSE_FILE="$STATE_DIR/.pause"
touch "$PAUSE_FILE"
echo "Pause requested for sweep tag=$TAG (state: $STATE_DIR)"
echo
echo "Each phone stream will exit after its current cell completes."
echo "Watch the master log for 'exiting stream cleanly':"
echo "    tail -F $LOG_DIR/parallel_*.log | grep 'exiting stream cleanly'"
echo
echo "To RESUME later:"
echo "    rm $PAUSE_FILE"
echo "    tools/run_final_sweep_ykush.sh --sweep-tag $TAG --resume ..."
