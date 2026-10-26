#!/usr/bin/env bash
# run_final_sweep_ykush.sh: orchestrate the final thesis sweep on the YKUSH3.
#
# Written 2026-08-04 for the YKUSH3 board. It takes the place of the wrapper used
# with the earlier Plugable hub (the "old wrapper" below).
#
# THIS IS THE ONLY SUPPORTED ENTRY POINT. Do not call
# sweep_runner_parallel_ykush.sh directly: the mitmproxy replay server is started
# here, and an app with recorded .flow files that runs un-proxied takes its
# network responses from the live internet. That both makes scenarios look flaky
# and contaminates the energy with variable radio traffic, unevenly across
# variants. (In batch1 every pdfviewer cell ran that way: 27 runs of
# S10_show_inline_pdf produced 3 css4.pub requests through mitmproxy.)
#
# Sequence:
#   1. tools/uninstall_cohort_ykush.sh:      no leftover package from a dead run
#   2. tools/ykush_band_keeper.sh once:      bring every phone into [BAND_LOW, BAND_HIGH]
#   3. tools/preflight_final_sweep_ykush.sh: gates; leaves every port CUT
#   4. mitmdump --server-replay:             for the Tier-A apps
#   5. tools/sweep_runner_parallel_ykush.sh
#
# WHY BAND BEFORE PREFLIGHT (the old wrapper had it the other way round)
# ─────────────────────────────────────────────────────────────────────
# The old wrapper ran preflight first and passed it
# `--require-soc-min $BAND_LOW --require-soc-max $BAND_HIGH`, then ran the band
# stage. So if a phone was out of band the preflight failed and the stage that
# would have fixed it never ran, and the operator's only move was --skip-preflight,
# which skips every other gate too. Charging first makes the SoC gate meaningful.
#
# Usage:
#   tools/run_final_sweep_ykush.sh --reps 10 --sweep-tag final_ykush_2026-08-04
#   tools/run_final_sweep_ykush.sh --reps 1 --apps calculator --sweep-tag smoke_x
#   tools/run_final_sweep_ykush.sh --resume --sweep-tag final_ykush_2026-08-04
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"

# ---- SINGLE-INSTANCE LOCK -----------------------------------------------------
# Two sweeps cannot share three phones and one YKUSH board. Stage 1 below
# uninstalls every cohort app, so a second sweep starting on top of a live one
# pulls the app out from under whatever cell is currently measuring.
#
# That is not hypothetical. On 2026-08-10 a chaining script misread a running
# sweep as finished (a `grep -q` inside a pipeline under `set -o pipefail`
# reported SIGPIPE as failure) and launched batch 2 over run 1. Three cells were
# contaminated before preflight's port-8080 check happened to catch the
# collision, and it only caught it because mitmproxy holds that port. Nothing
# structural was stopping it.
#
# The lock is held on a dedicated file for the life of the script; the fd stays
# open, so it releases automatically on exit, crash or kill. Non-blocking: a
# second invocation fails immediately and loudly rather than queueing behind a
# 40-hour run.
_LOCKFILE="$ROOT/tools/sweep_logs/.sweep.lock"
mkdir -p "$(dirname "$_LOCKFILE")"
exec 9>"$_LOCKFILE" || { echo "FATAL: cannot open lock file $_LOCKFILE" >&2; exit 1; }
if ! flock -n 9; then
    echo "FATAL: another sweep already holds $_LOCKFILE — refusing to start a second one." >&2
    echo "       Running sweep:" >&2
    ps -ef | grep -E "[r]un_final_sweep_ykush|[s]weep_runner_parallel_ykush" >&2 || true
    echo "       If you are certain nothing is running, remove the file and retry." >&2
    exit 9
fi

# Strict-mode gate for AR's discharge verification hook. Without this export,
# is_strict_mode_enabled() returns False and supply-masked cells slip through as
# PASS instead of aborting.
export MASTEREXP_STRICT_DISCHARGE_CHECK=1

# ---------- defaults ----------
# Final 10-app / 4-variant cohort (2026-08-04).
APPS="calculator,tipuous,poetskingdom,linkhub,medtimer,androidskk,itsok,keep_recipe,documenter,pdfviewer"
VARIANTS="baseline_debug,bangcle_debug,allatori_debug,freerasp_debug"
REPS=10
PHONES="pixel3,pixel6,pixel9"
CELL_TIMEOUT_S=480
SETTLE_S=60
BAND_LOW=40
BAND_HIGH=70
BAND_MAX=75
MAX_TOPUP_S=2400
SWEEP_TAG="final_ykush_$(date +%Y%m%d_%H%M%S)"
RANDOM_SEED=""
SKIP_PREFLIGHT=0
SKIP_BAND=0
SKIP_UNINSTALL=0
DRY_RUN=0
RESUME=0

while [ $# -gt 0 ]; do
    case "$1" in
        --apps)              APPS="$2"; shift 2 ;;
        --variants)          VARIANTS="$2"; shift 2 ;;
        --phones)            PHONES="$2"; shift 2 ;;
        --reps)              REPS="$2"; shift 2 ;;
        --cell-timeout)      CELL_TIMEOUT_S="$2"; shift 2 ;;
        --settle)            SETTLE_S="$2"; shift 2 ;;
        --band-low)          BAND_LOW="$2"; shift 2 ;;
        --band-high)         BAND_HIGH="$2"; shift 2 ;;
        --band-max)          BAND_MAX="$2"; shift 2 ;;
        --max-topup)         MAX_TOPUP_S="$2"; shift 2 ;;
        --sweep-tag)         SWEEP_TAG="$2"; shift 2 ;;
        --random-seed)       RANDOM_SEED="$2"; shift 2 ;;
        --skip-preflight)    SKIP_PREFLIGHT=1; shift ;;
        --skip-band)         SKIP_BAND=1; shift ;;
        --skip-uninstall)    SKIP_UNINSTALL=1; shift ;;
        --dry-run)           DRY_RUN=1; shift ;;
        --resume)            RESUME=1; shift ;;
        -h|--help)           sed -n '2,40p' "$0"; exit 0 ;;
        *)                   echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

# The random seed. A FIXED-ORDER sweep confounds ΔE with time of day: thermal
# drift, ambient temperature and battery aging all move monotonically through a
# 30-hour run, so whichever variant happens to sit late in the fixed order carries
# that drift as if it were a treatment effect. sweep_runner derives a seed from
# the sweep tag when none is given, which is deterministic and resumable; passing
# one explicitly is preferred so it is recorded here, in this wrapper's own log,
# rather than only in the state dir.
if [ -z "$RANDOM_SEED" ]; then
    RANDOM_SEED=$(echo -n "$SWEEP_TAG" | md5sum | awk '{print $1}' | cut -c1-10)
fi

N_APPS=$(echo "$APPS" | tr ',' '\n' | grep -c .)
N_VARS=$(echo "$VARIANTS" | tr ',' '\n' | grep -c .)
N_PHON=$(echo "$PHONES" | tr ',' '\n' | grep -c .)

echo "============ run_final_sweep_ykush.sh ============"
echo "  apps:       $APPS ($N_APPS)"
echo "  variants:   $VARIANTS ($N_VARS)"
echo "  phones:     $PHONES ($N_PHON)"
echo "  reps:       $REPS"
echo "  cells:      $(( N_APPS * N_VARS * N_PHON * REPS ))"
echo "  band:       ${BAND_LOW}-${BAND_HIGH}% (hard ceiling ${BAND_MAX}%, stall cap ${MAX_TOPUP_S}s)"
echo "  settle:     ${SETTLE_S}s between cells"
echo "  cell wall:  <= ${CELL_TIMEOUT_S}s"
echo "  sweep tag:  $SWEEP_TAG"
echo "  RANDOM SEED: $RANDOM_SEED"
echo "  power:      YKUSH3 via tools/ykush_power.sh (AR configs *_wifi_ykush.json)"
echo "  resume:     $RESUME"
echo ""

# ---------- 1. uninstall leftovers ----------
if [ "$SKIP_UNINSTALL" -ne 1 ] && [ "$RESUME" -ne 1 ]; then
    echo "============ Stage 1: uninstall cohort packages ============"
    "${ROOT}/tools/uninstall_cohort_ykush.sh" --phones "$PHONES" --apps "$APPS" --variants "$VARIANTS" \
        || echo "  WARNING: uninstall reported problems (preflight Y.7 will re-check)"
    echo ""
fi

# ---------- 2. bring phones into band ----------
if [ "$SKIP_BAND" -ne 1 ]; then
    echo "============ Stage 2: ykush_band_keeper.sh once ============"
    BAND_LOW="$BAND_LOW" BAND_HIGH="$BAND_HIGH" BAND_MAX="$BAND_MAX" \
        "${ROOT}/tools/ykush_band_keeper.sh" once \
        || echo "  WARNING: band keeper reported problems (preflight Y.9 will re-check)"
    echo ""
fi

# ---------- 3. preflight ----------
if [ "$SKIP_PREFLIGHT" -ne 1 ]; then
    echo "============ Stage 3: preflight_final_sweep_ykush.sh ============"
    if ! "${ROOT}/tools/preflight_final_sweep_ykush.sh" --phones "$PHONES" \
                --apps "$APPS" --variants "$VARIANTS" \
                --require-soc-min "$BAND_LOW" --require-soc-max "$BAND_MAX"; then
        echo ""
        echo "FATAL: preflight failed — the sweep is NOT safe to launch."
        echo "       --skip-preflight ONLY after manually verifying each failed check."
        exit 1
    fi
    echo ""
fi

# ---------- 4. mitmproxy replay ----------
# Per-app gate: only apps with a non-empty .flow file need the proxy. Kill any
# orphan first: an orphan holding :8080 makes mitmdump fail to bind and the
# wrapper exit ~3 s later with rc=0, silently burning the run (2026-08-01).
MITM_APPS=""
IFS=',' read -ra _APP_LIST <<< "$APPS"
for app in "${_APP_LIST[@]}"; do
    flow_dir="${ROOT}/_external/mitm_flows/${app}"
    [ -d "$flow_dir" ] || continue
    for f in "$flow_dir"/*.flow; do
        if [ -s "$f" ]; then MITM_APPS="${MITM_APPS}${app},"; break; fi
    done
done
MITM_APPS="${MITM_APPS%,}"

MITM_PID=""
if [ -n "$MITM_APPS" ]; then
    N_MITM=$(echo "$MITM_APPS" | tr ',' '\n' | grep -c .)
    echo "============ Stage 4: mitmproxy replay ============"
    echo "  apps needing mitm: $MITM_APPS ($N_MITM)"
    pkill -f 'mitmdump.*server-replay' 2>/dev/null && { echo "  killed an orphan mitmdump"; sleep 2; }
    FIRST_MITM_APP="${MITM_APPS%%,*}"
    MITM_LOG="${ROOT}/tools/sweep_logs/mitmproxy_${SWEEP_TAG}.log"
    mkdir -p "${ROOT}/tools/sweep_logs"
    # setsid so the whole replay tree gets its own process group. mitmproxy_replay.sh
    # runs mitmdump as a CHILD (it does not exec it), so killing only the script's
    # PID leaves mitmdump alive holding :8080 (verified 2026-08-04, when exactly
    # that orphan made the next run's [Y.12] fail and abort the sweep). Killing the
    # group takes the child with it.
    # `9>&-` closes the single-instance lock in the child, for the same reason as
    # the Appium spawn in sweep_runner_parallel_ykush.sh::ensure_appium. mitmproxy is
    # killed by the EXIT trap so it does not currently outlive the sweep, but it
    # is setsid'd into its own session and one orphan has already survived a PID
    # kill before. A daemon that escapes must not take the lock with it.
    setsid "${ROOT}/tools/mitmproxy_replay.sh" "$FIRST_MITM_APP" > "$MITM_LOG" 2>&1 9>&- &
    MITM_PID=$!
    sleep 5
    if ! kill -0 "$MITM_PID" 2>/dev/null; then
        echo "  FATAL: mitmproxy died within 5s. See $MITM_LOG"
        tail -20 "$MITM_LOG"
        exit 1
    fi
    # Bind check, not just liveness: the wrapper process can survive while
    # mitmdump itself failed to take the port.
    if ! ss -lnt 2>/dev/null | grep -q ':8080 '; then
        echo "  FATAL: mitmproxy is running but nothing is listening on :8080. See $MITM_LOG"
        tail -20 "$MITM_LOG"
        kill -TERM "$MITM_PID" 2>/dev/null
        exit 1
    fi
    export MITMPROXY_REPLAY_HOST=$(hostname -I | awk '{print $1}')
    export MITMPROXY_REPLAY_PORT=8080
    # Exported so the runner can count "[replay]" lines and PROVE the recorded
    # flow was actually served per cell, rather than re-deriving this path from a
    # naming convention.
    export MITMPROXY_LOG_FILE="$MITM_LOG"
    echo "  mitmproxy PID: $MITM_PID"
    echo "  replay for:    $FIRST_MITM_APP"
    echo "  listen:        ${MITMPROXY_REPLAY_HOST}:${MITMPROXY_REPLAY_PORT}"
    echo "  log:           $MITM_LOG"
    echo ""
else
    echo "============ Stage 4: mitmproxy SKIPPED — no cohort app has .flow files ============"
    echo ""
fi

# Clean up mitmproxy AND cut every port when this wrapper exits, however it exits.
# The runner has its own EXIT trap, but a failure between here and there (or a
# Ctrl-C at exactly the wrong moment) must not leave a phone on a live port.
cleanup_on_exit() {
    local rc=$?
    if [ -n "${MITM_PID:-}" ]; then
        echo ""
        echo "stopping mitmproxy (process group $MITM_PID)"
        # Negative PID = the whole group, which is why it was started under setsid.
        kill -TERM -"$MITM_PID" 2>/dev/null || kill -TERM "$MITM_PID" 2>/dev/null
        for _ in 1 2 3 4 5; do
            ss -lnt 2>/dev/null | grep -q ':8080 ' || break
            sleep 1
        done
        # Belt and braces: if anything still holds the port, the next run's [Y.12]
        # would abort the sweep before a single cell ran.
        if ss -lnt 2>/dev/null | grep -q ':8080 '; then
            echo "  :8080 still held — pkill'ing mitmdump"
            pkill -f 'mitmdump.*server-replay' 2>/dev/null
            sleep 2
        fi
    fi
    # The board serial comes from YKUSH_SERIAL, as in tools/ykush_power.sh.
    timeout -k 2 10 /usr/bin/ykushcmd ykush3 -s "${YKUSH_SERIAL:-<ykush-serial>}" -d a >/dev/null 2>&1 || true
    echo "all YKUSH ports cut"
    return $rc
}
trap cleanup_on_exit EXIT INT TERM

# ---------- 5. the sweep ----------
echo "============ Stage 5: sweep_runner_parallel_ykush.sh ============"
SWEEP_ARGS=(
    --apps "$APPS"
    --variants "$VARIANTS"
    --phones "$PHONES"
    --reps "$REPS"
    --cell-timeout "$CELL_TIMEOUT_S"
    --settle "$SETTLE_S"
    --band-low "$BAND_LOW"
    --band-high "$BAND_HIGH"
    --band-max "$BAND_MAX"
    --max-topup "$MAX_TOPUP_S"
    --sweep-tag "$SWEEP_TAG"
    --random
    --random-seed "$RANDOM_SEED"
)
[ "$RESUME" -eq 1 ]  && SWEEP_ARGS+=(--resume)
[ "$DRY_RUN" -eq 1 ] && SWEEP_ARGS+=(--dry-run)

echo "  invoking: tools/sweep_runner_parallel_ykush.sh ${SWEEP_ARGS[*]}"
echo ""
# No `exec`: the trap must fire so mitmproxy is killed and the ports are cut.
"${ROOT}/tools/sweep_runner_parallel_ykush.sh" "${SWEEP_ARGS[@]}"
exit $?
