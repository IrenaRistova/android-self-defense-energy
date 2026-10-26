#!/usr/bin/env bash
# sweep_runner_parallel_ykush.sh: cohort sweep across all 3 phones, on YKUSH3 power.
#
# Written 2026-08-04 for the Yepkit YKUSH3. It takes the place of the runner
# used with the earlier Plugable USB3-HUB7BC-EU hub ("the old runner" or
# "the uhubctl runner" below), which switched phone power with uhubctl and
# could not cut VBUS on this board. The sections below list what differs from
# that runner, and why.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT IS DIFFERENT FROM THE OLD (uhubctl) RUNNER, AND WHY
# ─────────────────────────────────────────────────────────────────────────────
# 1. POWER. Every `sudo -n /usr/sbin/uhubctl -l <hub> -p <port> -a 0|1` is gone.
#    uhubctl does NOT cut VBUS on a YKUSH3: it drives the board's internal
#    Microchip USB2744 hub, which advertises ppps and accepts the command (the
#    phone even drops off USB), while the real VBUS switch is a MOSFET driven by
#    the board's own MCU, reachable only via ykushcmd. Measured 2026-08-03: a
#    Pixel 3 read BatteryManager status=2 at ~500 mA a full THIRTY SECONDS after
#    uhubctl reported "Port 1: 0000 off". Had the sweep run that way, every cell
#    would have measured a CHARGING phone while the logs looked clean and the
#    strict-pass rate stayed at 100%.
#
# 2. NO CONFIG SYNC. The old runner's sync_ar_configs_to_live_hub() is not ported. On
#    this board it is the thing that would ARM the defect above: the uhubctl
#    configs currently carry a STALE hub address, so AR raises USBHandlerException
#    and the cell fails LOUDLY. Rewriting them to the live address converts that
#    loud failure into a silent one. It would also write the WRONG PORT: the
#    board's downstream numbering is REVERSED relative to Linux sysfs indices
#    (board port 1 = pixel3, but pixel3 enumerates at sysfs 3-6.3), so pixel3's
#    cut would land on pixel9.
#
# 3. CONFIG SUFFIX. Loads *_wifi_ykush.json (written by ykush_sync_ar_configs.py),
#    whose usb_handler commands point at tools/ykush_ar_power.sh. The 120 old
#    *_wifi_hubctl.json files are untouched. Only the two usb_handler commands
#    differ between the two sets: device list, profilers, scripts, duration and
#    APK paths are copied verbatim, so the measurement configuration is provably
#    identical to the audited original.
#
# 4. THE CUT IS VERIFIED, EVERY CELL. ykush_port_off proves the SWITCH says off;
#    ykush_verify_cut proves the PHONE agrees, by reading raw sysfs
#    /sys/class/power_supply/usb/online over WiFi-ADB. A cell whose cut cannot be
#    verified is FAILED, not measured. This check runs AROUND the AR invocation,
#    never inside it: AR's USB_COMMAND_TIMEOUT_SECONDS is 5 and it raises
#    USBHandlerException on ANY stderr from the handler command.
#
#    It reads sysfs and NOT `dumpsys battery` on purpose. AR calls device.unplug()
#    at every experiment start, and AndroidRunner/USBHandler.py says in its own
#    docstring that this "only mocks the battery status ... but it does NOT
#    actually stop charging the device through USB". The mock sits ABOVE sysfs, so
#    a dumpsys-based cut check would return PASS unconditionally for the whole of
#    every AR cell, reproducing the exact silent failure being fixed here.
#
# 5. THE PORT IS CUT AGAIN AFTER EVERY CELL. AR calls enable_command at the end
#    of each experiment, so the phone is left CHARGING between cells. In the old
#    runner that gap is what drove all three phones from 60 % to 100 % overnight
#    on 2026-07-27. Here the port is re-cut the moment AR returns, on every exit
#    path, and the next cell's top-up is the only thing that ever powers it up.
#
# 6. TOP-UPS ARE SERIALISED ACROSS STREAMS. On laptop bus power the board cannot
#    feed three Pixels: measured 2026-08-03, with all three powered P3 charged at
#    +670 mA while P6 (-220 mA) and P9 (-405 mA) went status=4 and actively
#    DISCHARGED. A flock means one phone charges at a time; the other two keep
#    running cells, because a measuring phone's port is cut and draws nothing.
#    Lift this only when the external 5.1-5.25 V supply is fitted (jumper on VEXT).
#
# Each phone gets its own Appium server on a distinct port (4723/4724/4725) and
# its own AR process. Phone-level isolation is enforced: no AR process touches
# another phone's serial, board port, or output dir.
#
# Usage:
#   tools/sweep_runner_parallel_ykush.sh                    # default cohort, 1 rep each
#   tools/sweep_runner_parallel_ykush.sh --apps calculator,poetskingdom --variants baseline_debug
#   tools/sweep_runner_parallel_ykush.sh --reps 10 --cell-timeout 900
#   tools/sweep_runner_parallel_ykush.sh --phones pixel3,pixel6   # subset of phones
#   tools/sweep_runner_parallel_ykush.sh --dry-run
#
# Normally invoked through tools/run_final_sweep_ykush.sh, which adds the
# pre-flight gates and the mitmproxy replay server.
#
# Per-cell watchdog: 480 s default (see the WATCHDOG block in the stream loop).
# If a cell times out, recovery (force-stop over WiFi-ADB, battery reset; the
# port stays cut) runs ONLY for that phone, and other phones keep going.
#
# Concurrency model:
#   - 3 background streams, one per phone.
#   - Each stream walks the SAME (app × variant × rep) sequence in the same order.
#   - No cross-stream synchronisation per cell: phones progress independently.
#   - Master script waits for all 3 streams to finish, then prints per-phone summary.
#
# Why parallel + same-order: we want N=3 reps on the same phone × variant
# combination to be temporally adjacent (minimise drift in battery level, ambient
# temp, etc.). Three streams running the same sequence in parallel achieve that.

set -uo pipefail

# ---------- args ----------
# Final 10-app / 4-variant cohort (2026-08-04).
APPS="calculator,tipuous,poetskingdom,linkhub,medtimer,androidskk,itsok,keep_recipe,documenter,pdfviewer"
VARIANTS="baseline_debug,bangcle_debug,allatori_debug,freerasp_debug"
PHONES="pixel3,pixel6,pixel9"
REPS=1
CELL_TIMEOUT_S=480
TOP_UP_S=60               # DEPRECATED: kept for backwards-compat. Band logic replaces this.
SETTLE_S=60               # 2026-07-27: the protocol sets a 60 s SETTLE between cells (was 5)
# SoC band. 2026-08-04: 40-70 (was 30-60 on the Plugable, where a narrow high band
# existed to minimise how often top-up ran, because every top-up risked PPPS
# backfeed contamination). The YKUSH switches VBUS properly, so the band can now
# be chosen for MEASUREMENT VALIDITY instead: energy is integrated as ∫V·I·dt and
# pack voltage tracks SoC, so cells run at widely different SoC carry a systematic
# voltage difference unrelated to the protection variant. Below ~30 % you also get
# voltage sag and rising internal resistance.
BAND_LOW=40               # top-up fires whenever level < BAND_LOW
BAND_HIGH=70              # top-up exits when level >= BAND_HIGH
BAND_MAX=75               # HARD ceiling: cut immediately at/above this, no exceptions
# 2026-08-04: 300 -> 2400. On bus power a Pixel 3 charges at ~1.5 A, so 40->70 %
# is ~875 mAh ≈ 35 min. A 300 s cap could never reach the band; it would silently
# "proceed under-band" every single time and the band would be fiction. This is a
# STALL DETECTOR, not the mechanism: the loop exits on LEVEL, not on time.
MAX_TOPUP_S=2400
POLL_INTERVAL_S=5         # How often to re-read SoC during top-up loop
# Only one phone may charge at a time (see header §6). Lift ONLY with the
# external 5.1-5.25 V supply fitted and the jumper moved from VBUS to VEXT.
TOPUP_PARALLEL="${TOPUP_PARALLEL:-0}"
TOPUP_LOCK="${TOPUP_LOCK:-/tmp/ykush_topup.lock}"
# ---- charge-refusal backoff (added 2026-08-04, after the pixel3 incident) ----
# The charge lock is global, so a phone that CANNOT charge used to hold it for the
# full MAX_TOPUP_S every time it dropped below the band, blocking the two phones
# that could. Measured that day: pixel3's fuel gauge wedged and refused charge,
# while pixel9 sat with its port off at a perfectly chargeable 41.8 % / 41.8 C
# waiting for a lock that was gaining nobody anything. Roughly half the rig's
# charging throughput went to a phone that charged 0 %.
#
# Two guards, in order:
#   1. FAST REFUSAL: with VBUS confirmed present, if the phone reports
#      status != 2 (not charging) AND has not gained a single level point after
#      CHARGE_REFUSAL_S, stop and release the lock. Gated on the phone's own
#      status so a slow-but-real charge (status=2, one point every few minutes)
#      still gets the full MAX_TOPUP_S stall cap. A 40->70 % top-up on bus power
#      legitimately takes ~2 h and must not be cut short.
#   2. BACKOFF: after a refusal or a stall, that phone skips top-ups entirely
#      for CHARGE_BACKOFF_S and never takes the lock. It keeps running cells on
#      the charge it has and, if it never recovers, its cells FAIL loudly at
#      SAFETY_MIN_LEVEL. Under-band-and-honest beats stalling the whole rig, and
#      a phone that cannot charge cannot overcharge, so this is safe by
#      construction. Any level rise clears the marker.
CHARGE_REFUSAL_S="${CHARGE_REFUSAL_S:-300}"
CHARGE_BACKOFF_S="${CHARGE_BACKOFF_S:-1800}"
# AR configs to load. The old *_wifi_hubctl.json set is left in place, untouched.
CFG_SUFFIX="${SWEEP_CFG_SUFFIX:-_wifi_ykush}"
DRY_RUN=0
SWEEP_TAG=""             # for resume: tag identifies the sweep family
RESUME=0                  # if set, skip cells already in the state file
RANDOM_PICK=0             # if set, randomize per-phone (app × variant × rep) queue
RANDOM_SEED=""            # deterministic shuffle seed (auto-generated if empty)
INTER_CELL_MIN_S=0        # floor between AR cell END and next AR cell START

while [ $# -gt 0 ]; do
    case "$1" in
        --apps)             APPS="$2"; shift 2 ;;
        --variants)         VARIANTS="$2"; shift 2 ;;
        --phones)           PHONES="$2"; shift 2 ;;
        --reps)             REPS="$2"; shift 2 ;;
        --cell-timeout)     CELL_TIMEOUT_S="$2"; shift 2 ;;
        --top-up)           TOP_UP_S="$2"; shift 2 ;;
        --settle)           SETTLE_S="$2"; shift 2 ;;
        --band-low)         BAND_LOW="$2"; shift 2 ;;
        --band-high)        BAND_HIGH="$2"; shift 2 ;;
        --band-max)         BAND_MAX="$2"; shift 2 ;;
        --max-topup)        MAX_TOPUP_S="$2"; shift 2 ;;
        --poll-interval)    POLL_INTERVAL_S="$2"; shift 2 ;;
        --inter-cell-min)   INTER_CELL_MIN_S="$2"; shift 2 ;;
        --dry-run)          DRY_RUN=1; shift ;;
        --sweep-tag)        SWEEP_TAG="$2"; shift 2 ;;
        --resume)           RESUME=1; shift ;;
        --random)           RANDOM_PICK=1; shift ;;
        --random-seed)      RANDOM_SEED="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,88p' "$0"
            exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

ROOT="/home/irena/Documents/Master Experiment"
AR_DIR="$ROOT/android-runner"
VENV_ACTIVATE="$AR_DIR/.venv/bin/activate"
APPIUM_BIN="/home/irena/.nvm/versions/node/v22.17.1/bin/appium"
LOG_DIR="$ROOT/tools/sweep_logs"
mkdir -p "$LOG_DIR"
SWEEP_TS=$(date +%Y%m%d_%H%M%S)
SWEEP_LOG="$LOG_DIR/parallel_${SWEEP_TS}.log"
> "$SWEEP_LOG"

# State-file directory for resume. If a tag is provided, use it; otherwise use
# the sweep timestamp. With --resume, the state dir is reused; new completed
# cells append to existing per-phone state files.
[ -z "$SWEEP_TAG" ] && SWEEP_TAG="$SWEEP_TS"
STATE_DIR="$LOG_DIR/state_${SWEEP_TAG}"
mkdir -p "$STATE_DIR"

# Random seed: if not provided, derive from sweep tag for reproducibility.
# Save it so --resume can re-shuffle identically.
if [ -z "$RANDOM_SEED" ]; then
    RANDOM_SEED=$(echo -n "$SWEEP_TAG" | md5sum | awk '{print $1}' | cut -c1-10)
fi
echo "$RANDOM_SEED" > "$STATE_DIR/.random_seed"

# ---------- power library ----------
# shellcheck source=/dev/null
source "$ROOT/tools/ykush_power.sh"

# ---------- device tables ----------
# WIFI_ADDR / WIFI_ALIAS / APPIUM_PORT are stable (assigned by us, not the bus).
#
# There is deliberately NO hub/port discovery here. On the Plugable, HUB/PORT had
# to be re-derived from /sys/bus/usb/devices/ at every startup because the bus
# address drifted on every replug (the same hub appeared as 3-6.1, 5-1.3.2, 3-3
# and 5-1.3 across four sessions in two days). The YKUSH is addressed by BOARD
# SERIAL instead (<ykush-serial>), which does not drift, and its downstream ports are
# named by the board, not by Linux.
#
# Discovery would in fact be actively WRONG here: cutting a YKUSH port removes
# VBUS *and* both data pairs, so the phone vanishes from sysfs entirely. You
# cannot resolve a phone whose port is already off, which is precisely when you
# most need to know its port. And the board's numbering is REVERSED relative to
# sysfs (board 1 = pixel3, which enumerates at sysfs 3-6.3), so inferring one
# from the other cuts the wrong phone. Phone -> port comes from the calibrated
# map (tools/ykush_calibrate.py, determined by observation) and nothing else.
declare -A WIFI_ADDR WIFI_ALIAS APPIUM_PORT YKPORT
# Replicators put their own phones' Wi-Fi ADB addresses here.
WIFI_ADDR[pixel3]="<pixel3-ip>:5555"; WIFI_ALIAS[pixel3]="Pixel 3-W"; APPIUM_PORT[pixel3]=4723
WIFI_ADDR[pixel6]="<pixel6-ip>:5555"; WIFI_ALIAS[pixel6]="Pixel 6-W"; APPIUM_PORT[pixel6]=4724
WIFI_ADDR[pixel9]="<pixel9-ip>:5555";  WIFI_ALIAS[pixel9]="Pixel 9-W"; APPIUM_PORT[pixel9]=4725

# Resolve every requested phone through the calibrated map, once, at startup.
load_ykush_portmap() {
    local phones_csv="$1" short p
    if ! ykush_board_present; then
        log "  YKUSH board $YKUSH_SERIAL NOT reachable."
        log "  Check: lsusb | grep 04d8:f11b   and   ls -l /dev/bus/usb/*/*"
        log "  Never run ykushcmd under sudo — sudoers grants NOPASSWD to uhubctl only,"
        log "  so it would block on a password prompt and hang the sweep."
        return 1
    fi
    log "  YKUSH board $YKUSH_SERIAL present"
    for short in $(echo "$phones_csv" | tr ',' ' '); do
        p=$(ykush_resolve_port "$short" 2>/dev/null) || {
            log "  $short: NOT in the calibrated port map — run tools/ykush_calibrate.py"
            return 1
        }
        YKPORT[$short]="$p"
        log "  $short: YKUSH board port $p (calibrated map)"
    done
    return 0
}

# ---------- helpers ----------
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$SWEEP_LOG"; }

# charge_backoff_ok <phone> <stream_log>
#   0 -> this phone may attempt a top-up
#   1 -> it recently refused charge; skip the top-up so it does not take the
#        global lock. Logs the reason to the phone's own stream log.
# The marker lives in STATE_DIR so it is scoped to the sweep tag and survives a
# --resume, and it self-expires on age, so a stale one can never wedge a phone
# out of charging permanently.
charge_backoff_ok() {
    local ph="$1" slog="$2" marker ts now age left
    marker="$STATE_DIR/.charge_refused_${ph}"
    [ -f "$marker" ] || return 0
    ts=$(cat "$marker" 2>/dev/null)
    [[ "$ts" =~ ^[0-9]+$ ]] || { rm -f "$marker"; return 0; }
    now=$(date +%s); age=$(( now - ts ))
    if [ "$age" -ge "$CHARGE_BACKOFF_S" ]; then
        rm -f "$marker"
        echo "  charge backoff for $ph EXPIRED after $(( age / 60 ))min — will try to top up again" >> "$slog"
        return 0
    fi
    left=$(( (CHARGE_BACKOFF_S - age + 59) / 60 ))
    echo "  top-up SKIPPED: $ph refused charge $(( age / 60 ))min ago — backing off ${left}min more instead of holding the global charge lock while gaining nothing." >> "$slog"
    echo "                  Running under-band; cells FAIL at level < ${SAFETY_MIN_LEVEL:-25} if it never recovers. Check the cable, then reboot the phone (wedged fuel gauge, 2026-08-04)." >> "$slog"
    return 1
}

ensure_appium() {
    local port=$1
    local logfile=$2
    if curl -sS --max-time 3 "http://127.0.0.1:${port}/status" >/dev/null 2>&1; then
        log "  appium :${port} already up"
        return 0
    fi
    log "  starting appium on :${port} (log: $logfile)"
    # `9>&-` CLOSES THE SWEEP LOCK IN THE CHILD. Do not remove it.
    #
    # run_final_sweep_ykush.sh holds the single-instance lock as `exec 9>...`
    # plus `flock -n 9`. A child started without closing fd 9 inherits both the
    # descriptor AND the lock, and Appium is nohup'd and disowned, so it
    # deliberately outlives the sweep. The result: the wrapper exits, its EXIT
    # trap runs, every process is gone, and the lock is still held by three
    # Appium servers nobody thinks of as part of the sweep.
    #
    # That happened on 2026-08-14. Series batch 1 finished cleanly at 02:08; the
    # chain waiting to launch series batch 2 polled the lock every two minutes
    # and saw it held until 09:29: seven hours idle on a rig that was ready.
    # `fuser` on the lock file named the three `node` processes.
    #
    # Any other daemon spawned here that outlives the sweep needs the same.
    nohup "$APPIUM_BIN" --port "$port" --log-level warn > "$logfile" 2>&1 9>&- &
    disown
    # Wait up to 15 s for readiness
    for _ in $(seq 1 15); do
        sleep 1
        if curl -sS --max-time 3 "http://127.0.0.1:${port}/status" >/dev/null 2>&1; then
            log "  appium :${port} ready"
            return 0
        fi
    done
    log "  ERROR: appium :${port} failed to start"
    return 1
}

resolve_cfg() {
    # Translate (app, variant, phone) -> AR config filename.
    # Tries: <app>_<variant>_<phone><CFG_SUFFIX>.json, then stub_<variant>.
    #
    # CFG_SUFFIX defaults to _wifi_ykush. It is a variable and not a literal so a
    # calibration run can point at the old set with SWEEP_CFG_SUFFIX=_wifi_hubctl,
    # but note that the hubctl configs do NOT cut VBUS on this board, so such a
    # run measures a charging phone. Do not use it to collect thesis data.
    local app=$1 variant=$2 phone=$3
    local primary="${app}_${variant}_${phone}${CFG_SUFFIX}.json"
    local stub="${app}_stub_${variant}_${phone}${CFG_SUFFIX}.json"
    if [ -f "$AR_DIR/examples/batterymanager/$primary" ]; then
        echo "$primary"
    elif [ -f "$AR_DIR/examples/batterymanager/$stub" ]; then
        echo "$stub"
    else
        echo ""
    fi
}

# Per-phone stream: runs cohort sequentially for one phone.
run_phone_stream() {
    local phone=$1
    local stream_log="$LOG_DIR/parallel_${SWEEP_TS}_${phone}.log"
    > "$stream_log"
    local appium_url="http://127.0.0.1:${APPIUM_PORT[$phone]}"

    local total=0 pass=0 fail=0 timeout=0 skipped=0
    local safety_stop=0
    # Per-phone state file: CSV `app,variant,rep,result,run_id,wall_s,ts`
    # On --resume, completed (PASS or TIMEOUT) cells are skipped. Existing FAIL cells
    # are NOT auto-skipped: they typically warrant a retry. To force a retry of a
    # PASS/TIMEOUT cell, delete its line from the state file.
    local state_file="$STATE_DIR/${phone}.csv"
    if [ ! -f "$state_file" ]; then
        echo "app,variant,rep,result,run_id,wall_s,ts" > "$state_file"
    fi
    # Explicit empty initialization: without `=()` the `set -u` shell setting
    # would treat ${#COMPLETED[@]} as unbound when the array has zero entries
    # (bash 5.2 quirk, confirmed on this machine 2026-06-29). That used to
    # crash P6/P9 streams during --resume when neither phone had any PASS
    # cells yet (all prior cells were CONTAM, which doesn't add to COMPLETED).
    declare -A COMPLETED=()
    if [ "$RESUME" -eq 1 ]; then
        # Only PASS counts as "completed". TIMEOUT, FAIL, and CONTAM are re-run.
        # CONTAM is a successful AR run that produced contaminated energy data:
        # the workload was fine, but the fuel gauge / charge IC misreported.
        # Re-running the cell gives the phone (which has by now had more cells
        # between then and now) a chance to be in a cleaner battery state.
        while IFS=',' read -r app_ variant_ rep_ result_ rest_; do
            [ "$app_" = "app" ] && continue
            if [ "$result_" = "PASS" ]; then
                COMPLETED["${app_}|${variant_}|${rep_}"]=1
            fi
        done < "$state_file"
        log "  $phone: resume mode — ${#COMPLETED[@]} cells already PASS in state file (CONTAM/FAIL/TIMEOUT will be re-run)"
    fi

    IFS=',' read -ra APP_ARR <<< "$APPS"
    IFS=',' read -ra VAR_ARR <<< "$VARIANTS"

    # Build the per-phone task queue: array of "app|variant|rep" tuples.
    # In sequential mode, the order matches the historical for-rep / for-app / for-variant
    # iteration. In random mode, the queue is shuffled deterministically using
    # `${RANDOM_SEED}_${phone}` as the seed (so each phone gets a different but
    # reproducible order, and --resume re-shuffles identically).
    local queue=()
    for rep in $(seq 1 "$REPS"); do
        for app in "${APP_ARR[@]}"; do
            for variant in "${VAR_ARR[@]}"; do
                queue+=("${app}|${variant}|${rep}")
            done
        done
    done

    if [ "$RANDOM_PICK" -eq 1 ]; then
        local seed="${RANDOM_SEED}_${phone}"
        log "  $phone: shuffling ${#queue[@]} tasks with seed=$seed"
        # Use Python's seeded PRNG (Mersenne Twister) for true per-seed determinism.
        # Previous `shuf --random-source=<(yes "$seed" | head -c 4096)` did NOT
        # actually vary by seed text: `yes` emits a highly regular byte stream so
        # shuf produced the same shuffle regardless of seed (in the 2026-06-30
        # mini-validation run all 3 phones got identical queues). Also log the
        # per-phone seed for reproducibility.
        echo "$seed" > "$STATE_DIR/.random_seed_${phone}"
        mapfile -t queue < <(printf '%s\n' "${queue[@]}" | python3 -c '
import random, sys
random.seed(sys.argv[1])
lines = [l for l in sys.stdin.read().splitlines() if l]
random.shuffle(lines)
print("\n".join(lines))
' "$seed")
    fi

    for task in "${queue[@]}"; do
        IFS='|' read -r app variant rep <<< "$task"
        cfg=$(resolve_cfg "$app" "$variant" "$phone")
                if [ -z "$cfg" ]; then
                    echo "[$(date +%H:%M:%S)] $phone $app/$variant rep$rep — no config, SKIP" >> "$stream_log"
                    continue
                fi
                # Resume: skip already-completed cells
                if [ "$RESUME" -eq 1 ] && [ -n "${COMPLETED["${app}|${variant}|${rep}"]:-}" ]; then
                    echo "[$(date +%H:%M:%S)] $phone $app/$variant rep$rep — already completed, SKIP" >> "$stream_log"
                    skipped=$((skipped + 1))
                    continue
                fi
                total=$((total + 1))
                cell_log="$LOG_DIR/parallel_${SWEEP_TS}_${phone}_${app}_${variant}_r${rep}.log"
                t_start=$(date +%s)
                echo "[$(date +%H:%M:%S)] START $phone $app/$variant rep$rep cfg=$cfg" >> "$stream_log"

                # ----- TIER-A NETWORK GUARD (2026-07-31) -----
                # An app with recorded .flow files REQUIRES the mitmproxy replay
                # server, otherwise its network responses come from the live
                # internet and are non-deterministic across reps and variants.
                #
                # WHY THIS EXISTS: in batch1, every pdfviewer cell ran with
                # MITMPROXY_REPLAY_HOST unset. 27 runs of S10_show_inline_pdf
                # produced only 3 css4.pub requests through mitmproxy, and the rest
                # hit the live network or a cache. That both (a) made S10 look
                # like a 40%-flaky scenario when it was really uncontrolled
                # network, and (b) contaminated pdfviewer's energy with variable
                # radio traffic, unevenly across variants. The old code checked
                # `if [ -n "$MITMPROXY_REPLAY_HOST" ]` FIRST, so when the var was
                # unset the whole block (including the .flow check) was skipped
                # and NOTHING was logged. Silent contamination.
                #
                # Now: determine whether the app needs a proxy from the .flow
                # files alone, then refuse the cell if the proxy isn't available.
                # Loud failure beats silent bad data.
                #
                # Escape hatch: SWEEP_ALLOW_UNPROXIED_TIER_A=1 downgrades to a
                # warning (use only for deliberate no-network calibration runs).
                app_needs_mitm=0
                flow_dir_guard="$ROOT/_external/mitm_flows/$app"
                if compgen -G "$flow_dir_guard/*.flow" > /dev/null 2>&1; then
                    for _fg in "$flow_dir_guard"/*.flow; do
                        if [ -s "$_fg" ]; then app_needs_mitm=1; break; fi
                    done
                fi
                if [ "$app_needs_mitm" -eq 1 ]; then
                    mitm_problem=""
                    if [ -z "${MITMPROXY_REPLAY_HOST:-}" ]; then
                        mitm_problem="MITMPROXY_REPLAY_HOST unset (use tools/run_final_sweep_ykush.sh, not sweep_runner_parallel_ykush.sh directly)"
                    elif ! timeout 3 bash -c \
                            "exec 3<>/dev/tcp/${MITMPROXY_REPLAY_HOST}/${MITMPROXY_REPLAY_PORT:-8080}" 2>/dev/null; then
                        mitm_problem="mitmdump not listening on ${MITMPROXY_REPLAY_HOST}:${MITMPROXY_REPLAY_PORT:-8080}"
                    fi
                    if [ -n "$mitm_problem" ]; then
                        if [ "${SWEEP_ALLOW_UNPROXIED_TIER_A:-0}" = "1" ]; then
                            echo "  WARNING: $app needs mitmproxy but $mitm_problem — proceeding UNPROXIED (SWEEP_ALLOW_UNPROXIED_TIER_A=1). Network is uncontrolled; energy + scenario results for this cell are NOT comparable." >> "$stream_log"
                        else
                            echo "  ABORT: $app has recorded .flow files and REQUIRES mitmproxy, but $mitm_problem" >> "$stream_log"
                            echo "         Running un-proxied would silently contaminate this cell (see batch1 pdfviewer)." >> "$stream_log"
                            echo "         Set SWEEP_ALLOW_UNPROXIED_TIER_A=1 to override deliberately." >> "$stream_log"
                            echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep FAIL(mitm-required-but-unavailable) wall=0s" >> "$stream_log"
                            fail=$((fail + 1))
                            continue
                        fi
                    fi
                fi

                if [ "$DRY_RUN" -eq 1 ]; then
                    echo "  (dry-run) would invoke AR with timeout=${CELL_TIMEOUT_S}s" >> "$stream_log"
                    continue
                fi

                # ---- pre-cell: SoC-band top-up + VERIFIED cut + settle + maintenance guard ----
                # Level-driven, not time-driven: charge rate varies by phone, by SoC, by
                # temperature and by how many phones are plugged in at once, so "charge
                # for N seconds" either stops short or wastes rig time. The loop exits on
                # LEVEL; MAX_TOPUP_S is only a stall detector.
                #
                # Order of operations:
                #   - Read battery level + status from phone.
                #   - SAFETY LOWER BOUND: if level < SAFETY_MIN_LEVEL (25 %), abort cell + alert.
                #   - HARD CEILING: if level >= BAND_MAX, cut immediately, never top up.
                #   - BAND TOP-UP: if level < BAND_LOW, take the global charge lock, power
                #     the port, poll until level >= BAND_HIGH (or stall), release.
                #   - Cut the port AND PROVE VBUS IS GONE. A cell whose cut cannot be
                #     verified is FAILED, not measured: that is the whole point of this
                #     runner. See header §4.
                #   - Wait SETTLE_S.
                #   - MAINTENANCE GUARD: verify `current_now` responds to a brief CPU load.
                #     If maintenance suspected, run a 30 s drain via WiFi-ADB.
                #   - Only THEN invoke AR.
                # 2026-08-08: 25 -> 20. The floor is not "when to charge" (that is
                # BAND_LOW); it is the runway a phone has left when a top-up is
                # REFUSED. Refusals became real on 2026-08-08: pixel6 hit 45.5 C and
                # its charger stopped accepting current (VBUS present at 4.875 V,
                # dumpsys status=2, raw sysfs "Not charging", ~0 mA). Each refusal
                # costs a 30 min backoff spent measuring, ~6 cells, ~6 points of
                # drain. From 40 the old floor absorbed two refusals; pixel6 died on
                # its second. At 20 it absorbs three.
                SAFETY_MIN_LEVEL="${SAFETY_MIN_LEVEL:-20}"

                # Tighten grep to only match the top-level `level:` and
                # `status:` lines. Android 13+ adds `Capacity level:` and `Charging policy:`
                # which would otherwise produce multi-line `pre_lvl` / `pre_st`, breaking
                # the `-ge` integer test.
                pre_lvl=$(timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell "dumpsys battery | grep '^  level:'" 2>/dev/null | awk '{print $2}' | tr -d '\r')
                pre_st=$(timeout 5  adb -s "${WIFI_ADDR[$phone]}" shell "dumpsys battery | grep '^  status:'" 2>/dev/null | awk '{print $2}' | tr -d '\r')
                echo "  pre-cell: level=$pre_lvl status=$pre_st (band=$BAND_LOW-$BAND_HIGH)" >> "$stream_log"

                # An UNREADABLE level is not an in-band level. Every branch below is
                # guarded by `[ -n "$pre_lvl" ]`, so an empty read fell through all of
                # them and the cell proceeded as if the phone were fine, logged
                # verbatim on 2026-08-07 as:
                #     pre-cell: level= status= (band=40-70)
                #     top-up SKIPPED (level= >= 40)
                # If the phone were genuinely at 20 % and unreadable, that measures it
                # far out of band. Refuse instead: UNPROVEN is not "in band".
                if [ -z "$pre_lvl" ] || ! [[ "$pre_lvl" =~ ^[0-9]+$ ]]; then
                    echo "  UNPROVEN: could not read level (got '$pre_lvl'). Refusing to measure a phone whose SoC is unknown." >> "$stream_log"
                    echo "           Reconnecting WiFi-ADB before the next cell." >> "$stream_log"
                    timeout 10 adb disconnect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
                    sleep 1
                    timeout 15 adb connect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
                    echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep FAIL(level-unproven) wall=0s" >> "$stream_log"
                    fail=$((fail + 1))
                    unproven_streak=$(( ${unproven_streak:-0} + 1 ))
                    if [ "$unproven_streak" -ge 5 ]; then
                        echo "  STOPPING this phone's stream — level unreadable for $unproven_streak consecutive cells." >> "$stream_log"
                        echo "  The phone is off the network, not merely slow. Walking the queue would only write FAIL rows." >> "$stream_log"
                        safety_stop=1
                        break
                    fi
                    continue
                fi
                unproven_streak=0

                if [ -n "$pre_lvl" ] && [ "$pre_lvl" -lt "$SAFETY_MIN_LEVEL" ] 2>/dev/null; then
                    # 2026-08-05: STOP this phone's stream. It used to `continue`,
                    # which walked the ENTIRE remaining queue marking every cell
                    # FAIL(safety-min-level) in a few minutes: 977 cells across the
                    # three phones on the night of 2026-08-04, because bus-powered
                    # top-up cannot charge these phones while a workload runs (every
                    # attempt reported status=4, NOT_CHARGING, with VBUS present).
                    #
                    # Nothing was measured by those rows and --resume re-runs them,
                    # so no data was lost, but it churned the state file, buried the
                    # real signal, and left the phones draining to 3-20 % overnight.
                    # A phone below the safety floor cannot produce a valid cell, so
                    # the only correct action is to stop and let it charge.
                    echo "  ABORT: level=$pre_lvl below SAFETY_MIN_LEVEL ($SAFETY_MIN_LEVEL)." >> "$stream_log"
                    echo "  STOPPING this phone's stream — remaining cells are NOT attempted." >> "$stream_log"
                    echo "  Charge this phone back into the band, then rerun with --resume." >> "$stream_log"
                    echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep FAIL(safety-min-level) wall=0s" >> "$stream_log"
                    fail=$((fail + 1))
                    safety_stop=1
                    break
                fi

                if [ -n "$pre_lvl" ] && [ "$pre_lvl" -ge "$BAND_MAX" ] 2>/dev/null; then
                    # Hard ceiling. Never charge here; just make sure the port is off.
                    echo "  HARD CEILING: level=$pre_lvl >= BAND_MAX=$BAND_MAX — no top-up, cutting" >> "$stream_log"
                    ykush_port_off "$phone" >/dev/null 2>&1 || true
                elif [ -n "$pre_lvl" ] && [ "$pre_lvl" -lt "$BAND_LOW" ] 2>/dev/null \
                     && charge_backoff_ok "$phone" "$stream_log"; then
                    # ---- level-based top-up, serialised across streams ----
                    # The flock is load-bearing, not hygiene. On laptop bus power the
                    # board cannot feed three Pixels at once: with all three powered,
                    # P3 charged at +670 mA while P6 (-220 mA) and P9 (-405 mA) went
                    # status=4 and actively DISCHARGED (measured 2026-08-03). Without
                    # this lock, three concurrent top-ups would leave two phones
                    # draining for the full MAX_TOPUP_S while believing they charged.
                    #
                    # Waiting here is cheap: the other two streams keep running cells,
                    # because a measuring phone's port is cut and draws nothing.
                    echo "  top-up: level=$pre_lvl < $BAND_LOW -> charging to >= $BAND_HIGH (stall cap ${MAX_TOPUP_S}s)" >> "$stream_log"
                    topup_start_ts=$(date +%s)
                    (
                        if [ "$TOPUP_PARALLEL" != "1" ]; then
                            flock -w $(( MAX_TOPUP_S * 3 )) 8 || {
                                echo "    could not acquire the charge lock — another phone has been charging for a very long time" >> "$stream_log"
                                exit 1
                            }
                            waited=$(( $(date +%s) - topup_start_ts ))
                            [ "$waited" -gt 5 ] && echo "    waited ${waited}s for the charge lock (another phone was topping up)" >> "$stream_log"
                        fi
                        if ! ykush_port_on "$phone" >/dev/null 2>&1; then
                            echo "    FAILED to power on $phone (board port ${YKPORT[$phone]})" >> "$stream_log"
                            exit 1
                        fi
                        # Confirm VBUS actually arrived before polling for 40 minutes.
                        sleep 8
                        vb=$(timeout 8 adb -s "${WIFI_ADDR[$phone]}" shell 'cat /sys/class/power_supply/usb/online' 2>/dev/null | tr -d '\r\n')
                        if [ "$vb" != "1" ]; then
                            echo "    powered the port but usb/online='$vb' — check the cable; aborting top-up" >> "$stream_log"
                            ykush_port_off "$phone" >/dev/null 2>&1 || true
                            # Same class of failure as a refusal: the lock buys this phone
                            # nothing, so stop competing for it until the backoff expires.
                            date +%s > "$STATE_DIR/.charge_refused_${phone}"
                            exit 1
                        fi
                        charge_t0=$(date +%s); t_last_rise=$charge_t0; last_lvl="$pre_lvl"
                        # Separate clock from t_last_rise: the refusal check may be
                        # re-armed (phone says status=2, just slow) WITHOUT resetting
                        # the stall cap, which must keep counting from the last real gain.
                        refusal_t0=$charge_t0
                        while :; do
                            sleep "$POLL_INTERVAL_S"
                            cur_lvl=$(timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell "dumpsys battery | grep '^  level:'" 2>/dev/null | awk '{print $2}' | tr -d '\r')
                            now_ts=$(date +%s)
                            if [ -n "$cur_lvl" ] && [ "$cur_lvl" -ge "$BAND_HIGH" ] 2>/dev/null; then
                                echo "    band reached: level=$cur_lvl >= $BAND_HIGH after $(( now_ts - charge_t0 ))s" >> "$stream_log"
                                rm -f "$STATE_DIR/.charge_refused_${phone}"
                                break
                            fi
                            if [ -n "$cur_lvl" ] && [ "$cur_lvl" -gt "$last_lvl" ] 2>/dev/null; then
                                # Demonstrated a real gain: this phone charges. Clear any
                                # backoff marker left by an earlier refusal.
                                last_lvl="$cur_lvl"; t_last_rise="$now_ts"
                                rm -f "$STATE_DIR/.charge_refused_${phone}"
                            fi
                            # FAST REFUSAL: VBUS is present (verified above) but the phone
                            # says it is not charging and has not moved a single point.
                            # Gated on the phone's OWN status so a slow-but-real charge
                            # (status=2) is never cut short: it falls through to the
                            # stall cap below. Without this, a phone that cannot charge
                            # holds the global lock for the full MAX_TOPUP_S.
                            if [ $(( now_ts - refusal_t0 )) -ge "$CHARGE_REFUSAL_S" ] \
                               && [ "$t_last_rise" -eq "$charge_t0" ]; then
                                cur_st=$(timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell "dumpsys battery | grep '^  status:'" 2>/dev/null | awk '{print $2}' | tr -d '\r')
                                if [ "$cur_st" != "2" ]; then
                                    echo "    REFUSED: VBUS is present but $phone reports status=${cur_st:-?} (not charging) and level has not moved off $pre_lvl in $(( CHARGE_REFUSAL_S / 60 ))min." >> "$stream_log"
                                    echo "             Releasing the charge lock now instead of holding it for $(( MAX_TOPUP_S / 60 ))min. Backing off ${CHARGE_BACKOFF_S}s before retrying." >> "$stream_log"
                                    echo "             If this repeats: check the cable, then REBOOT the phone — a wedged fuel gauge looks exactly like this (2026-08-04)." >> "$stream_log"
                                    date +%s > "$STATE_DIR/.charge_refused_${phone}"
                                    break
                                fi
                                # status=2 -> genuinely charging, just slowly. Re-arm the
                                # refusal clock ONLY, so this does not re-fire every poll
                                # interval. t_last_rise is deliberately untouched: the
                                # stall cap must keep counting from the last real gain.
                                refusal_t0="$now_ts"
                            fi
                            # STALL, not TIMEOUT: fires only when the phone genuinely
                            # stopped gaining charge. On bus power that means the supply
                            # cannot feed it, so more waiting will not help.
                            if [ $(( now_ts - t_last_rise )) -ge "$MAX_TOPUP_S" ]; then
                                echo "    STALLED at level=${cur_lvl:-?} — no gain in $(( MAX_TOPUP_S / 60 ))min (target $BAND_HIGH). Proceeding under-band, and backing off $(( CHARGE_BACKOFF_S / 60 ))min before this phone takes the charge lock again." >> "$stream_log"
                                date +%s > "$STATE_DIR/.charge_refused_${phone}"
                                break
                            fi
                        done
                        ykush_port_off "$phone" >/dev/null 2>&1 || true
                    ) 8>"$TOPUP_LOCK"
                elif [ -n "$pre_lvl" ] && [ "$pre_lvl" -lt "$BAND_LOW" ] 2>/dev/null; then
                    : # below band but in charge-refusal backoff: charge_backoff_ok
                      # already logged why, including how much longer it will hold off.
                else
                    echo "  top-up SKIPPED (level=$pre_lvl >= $BAND_LOW)" >> "$stream_log"
                fi

                # ---- CUT, AND PROVE IT ----
                # ykush_port_off proves the SWITCH says off. ykush_verify_cut proves the
                # PHONE agrees, by reading raw sysfs /sys/class/power_supply/usb/online
                # over WiFi-ADB. This is the check that would have caught the uhubctl
                # defect the moment it appeared instead of after a whole sweep, and it is
                # the reason this runner exists.
                #
                # A cell that cannot prove its cut is FAILED, never measured. Measuring a
                # charging phone produces data that looks perfect and means nothing.
                cut_ok=0
                for cut_try in 1 2; do
                    ykush_port_off "$phone" >/dev/null 2>&1 || {
                        echo "  CUT: ykush_port_off failed for $phone (attempt $cut_try)" >> "$stream_log"
                        sleep 3; continue
                    }
                    if ykush_verify_cut "${WIFI_ADDR[$phone]}" 20 2>>"$stream_log"; then
                        cut_ok=1
                        echo "  CUT: verified — usb/online=0 on $phone (board port ${YKPORT[$phone]}, attempt $cut_try)" >> "$stream_log"
                        break
                    fi
                    echo "  CUT: VBUS still live after attempt $cut_try — retrying" >> "$stream_log"
                    sleep 3
                done
                if [ "$cut_ok" -ne 1 ]; then
                    # Two very different faults reach this point and they need opposite
                    # responses, so name them apart instead of blaming the cable for both.
                    # The BOARD's own port state tells them apart, host-side:
                    #
                    #   port ON  -> the cut genuinely failed. Hardware. Stop.
                    #   port OFF -> VBUS is gone; ykush_verify_cut simply could not READ
                    #               the phone, and failed closed (correct). This is a
                    #               stale WiFi-ADB socket, not a charging phone.
                    #
                    # On 2026-08-07 at 19:00 pixel3 hit the second case. The old message
                    # sent the operator to inspect cabling for a network fault, and
                    # `continue` walked on to the next cell: the same queue-burn shape
                    # that wrote 977 FAIL rows on 2026-08-06. Unattended, pixel3 would
                    # have burned its remaining ~150 cells at ~60 s each.
                    _board_state=$(ykush_port_state "$phone" 2>/dev/null)
                    if [ "$_board_state" = "ON" ]; then
                        echo "  ABORT: the cut FAILED — board port ${YKPORT[$phone]} still reads ON for $phone." >> "$stream_log"
                        echo "         This is hardware. Check the YKUSH cable and re-run tools/ykush_calibrate.py --verify." >> "$stream_log"
                        echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep FAIL(cut-unverified) wall=0s" >> "$stream_log"
                        fail=$((fail + 1))
                        echo "  STOPPING this phone's stream — a port that will not cut cannot be measured safely." >> "$stream_log"
                        safety_stop=1
                        break
                    fi

                    # Board says OFF: recoverable. Bounce the socket and retry the cell
                    # once before giving up on it.
                    echo "  CUT UNVERIFIED, but board port ${YKPORT[$phone]} reads OFF — VBUS is gone; the PHONE is unreachable." >> "$stream_log"
                    echo "         Reconnecting WiFi-ADB and retrying this cell." >> "$stream_log"
                    timeout 10 adb disconnect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
                    sleep 1
                    timeout 15 adb connect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
                    sleep 2
                    if ykush_verify_cut "${WIFI_ADDR[$phone]}" 20 2>>"$stream_log"; then
                        echo "  CUT: verified after reconnect — usb/online=0 on $phone" >> "$stream_log"
                        cut_unverified_streak=0
                    else
                        echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep FAIL(cut-unverified) wall=0s" >> "$stream_log"
                        fail=$((fail + 1))
                        cut_unverified_streak=$(( ${cut_unverified_streak:-0} + 1 ))
                        if [ "$cut_unverified_streak" -ge 5 ]; then
                            echo "  STOPPING this phone's stream — unreachable for $cut_unverified_streak consecutive cells even after reconnects." >> "$stream_log"
                            echo "  Walking the rest of the queue would only write FAIL rows. Fix the phone, then --resume." >> "$stream_log"
                            safety_stop=1
                            break
                        fi
                        continue
                    fi
                fi
                cut_unverified_streak=0

                if [ "$SETTLE_S" -gt 0 ]; then
                    sleep "${SETTLE_S}"
                fi

                # MAINTENANCE GUARD:
                # Brief CPU stress (3 s) via WiFi-ADB; read current_now.
                # If |current_now| < 150 mA = fuel gauge silently bypassing workload current
                # (= maintenance mode). Drain via 30 s sustained load before AR.
                guard_current=$(timeout 8 adb -s "${WIFI_ADDR[$phone]}" shell '
                    for i in 1 2 3 4 5 6 7 8; do ( while true; do : ; done ) & done
                    sleep 3
                    cat /sys/class/power_supply/battery/current_now
                    pkill -9 -f "while true" 2>/dev/null
                ' 2>/dev/null | grep -E "^-?[0-9]+$" | tail -1 | tr -d '\r')
                guard_abs=${guard_current#-}
                # Belt-and-suspenders kill any orphan loops
                timeout 3 adb -s "${WIFI_ADDR[$phone]}" shell 'pkill -9 -f "while true" 2>/dev/null; true' >/dev/null 2>&1
                if [ -z "$guard_abs" ] || [ "$guard_abs" -lt 150000 ] 2>/dev/null; then
                    echo "  MAINTENANCE GUARD: weak active-load response (${guard_current}µA) — applying 30s drain" >> "$stream_log"
                    timeout 40 adb -s "${WIFI_ADDR[$phone]}" shell '
                        for i in 1 2 3 4 5 6 7 8; do ( while true; do : ; done ) & done
                        sleep 30
                        pkill -9 -f "while true" 2>/dev/null
                    ' >/dev/null 2>&1
                    timeout 3 adb -s "${WIFI_ADDR[$phone]}" shell 'pkill -9 -f "while true" 2>/dev/null; true' >/dev/null 2>&1
                    sleep 2
                else
                    echo "  MAINTENANCE GUARD: active-load OK (${guard_current}µA = ≥150mA)" >> "$stream_log"
                fi

                # ---- THERMAL GATE (2026-08-09, opt-out via SWEEP_THERMAL_GATE=0) ----
                # Rest a hot phone before measuring it, rather than measuring a
                # throttled one and discovering it afterwards.
                #
                # Two failures on 2026-08-08 motivate this. pixel6 reached ~45.5 C
                # and its CHARGER stopped accepting current entirely, so it refused
                # two top-ups, drained through the backoff and stopped its stream at
                # the safety floor, about 6 h of rig time. And every cell in the
                # sweep was measured with no record of whether the SoC was
                # throttling, leaving an uncontrolled term in every ΔE.
                #
                # Gate on `dumpsys thermalservice` Thermal Status: the framework's
                # OWN throttling state, not a proxy for it. NOT on battery
                # temperature: that is a sticky broadcast frozen for the whole cell,
                # and the pack runs ~10 C cooler than the CPU anyway.
                #
                # Placed after the port cut, the settle and the load check, just
                # before the measurement starts, so cooling time is spent with the
                # port already cut.
                #
                # Non-fatal by construction: a phone that will not cool is measured
                # anyway and flagged, because a stalled stream is worse than a
                # thermally-degraded cell that is labelled as one.
                if [ "${SWEEP_THERMAL_GATE:-1}" = "1" ] && [ -x "$ROOT/tools/thermal_gate.sh" ]; then
                    thermal_before=$("$ROOT/tools/thermal_gate.sh" json "$phone" 2>/dev/null)
                    echo "  THERMAL pre-cell: ${thermal_before:-unreadable}" >> "$stream_log"
                    # STATUS_MAX=1: rest only at MODERATE throttling (2) or worse.
                    # These defaults used to read 0 / 38 / 1800, which were picked
                    # from IDLE readings and are unreachable under load: all three
                    # phones sit at status 1 and 38-42 C skin while working, so the
                    # gate held every cell for the full timeout and halved throughput.
                    # thermal_gate.sh clamps them to these values anyway; stating
                    # them here means the file no longer claims a policy it does not
                    # apply. The clamp stays as a backstop for other callers.
                    #
                    # Measured over batch 2's 619 cells, arrival state is 7% status 0,
                    # 72% status 1, 21% status 2, so this threshold rests roughly one
                    # cell in five, and zero of those rests failed to cool in time.
                    SKIN_MAX_C="${THERMAL_SKIN_MAX_C:-0}" \
                    STATUS_MAX="${THERMAL_STATUS_MAX:-1}" \
                    COOL_MAX_S="${THERMAL_COOL_MAX_S:-600}" \
                    POLL_S=60 \
                        "$ROOT/tools/thermal_gate.sh" wait "$phone" >> "$stream_log" 2>&1
                    case $? in
                        0) : ;;
                        2) echo "  THERMAL: gave up waiting — cell is thermally degraded, flag it in analysis" >> "$stream_log" ;;
                        3) echo "  THERMAL: UNPROVEN — could not read thermalservice; proceeding without a thermal guarantee" >> "$stream_log" ;;
                    esac
                fi

                # ----- PER-CELL FULL LOGCAT CAPTURE (2026-07-31) -----
                # Passive kernel-ring-buffer reader (~0% CPU on device, ~1-5 MB/cell
                # unfiltered). Answers "what does the app log during this cell"
                # WITHOUT touching the matched-pair ΔE: logcat is a pull operation
                # against a lock-free ring buffer; the app doesn't know it's being
                # observed. Distinct from AR's batterymanager profiler, which also
                # runs `adb logcat` but tag-filters to just its own battery events.
                # Concurrent logcat readers are supported by Android's log driver.
                #
                # 2026-07-31 upgrade: capture -b all
                # (main + system + events + crash) instead of just main. The events
                # buffer contains stable structured entries (am_activity_launch_time,
                # am_proc_start, dex2oat events, ART GC events) that are regex-tolerant
                # for cross-Android-version parsing. -v epoch gives seconds-since-epoch
                # timestamps to bypass year-less local-time ambiguity in the default
                # format. System buffer picks up SELinux avc denials (opportunistic
                # root-probe shadow). Crash buffer picks up java stacktraces.
                #
                # Opt-out: export SWEEP_CAPTURE_LOGCAT=0 before running the wrapper.
                logcat_out=""
                logcat_pid=""
                if [ "${SWEEP_CAPTURE_LOGCAT:-1}" = "1" ]; then
                    logcat_out="$LOG_DIR/logcat/logcat_${SWEEP_TS}_${phone}_${app}_${variant}_r${rep}.log"
                    mkdir -p "$LOG_DIR/logcat"
                    # -c clears the buffer so we start fresh from this moment.
                    # -b all pulls main+system+events+crash concurrently.
                    # -v epoch = seconds-since-epoch (parseable, unambiguous).
                    timeout 3 adb -s "${WIFI_ADDR[$phone]}" logcat -b all -c >/dev/null 2>&1 || true
                    adb -s "${WIFI_ADDR[$phone]}" logcat -b all -v epoch > "$logcat_out" 2>&1 &
                    logcat_pid=$!
                    # PHASE_BEGIN marker via `adb shell log`: device-clock timestamped,
                    # in-band with the log stream, so downstream parsers slice cleanly
                    # without needing host-vs-device clock reconciliation. The parser
                    # also uses its presence as a buffer-wrap-detection sentinel.
                    timeout 3 adb -s "${WIFI_ADDR[$phone]}" shell "log -p i -t HARNESS \
                        'PHASE_BEGIN preworkload cell=${phone}/${app}/${variant}/r${rep} \
                        sweep_ts=${SWEEP_TS}'" >/dev/null 2>&1 || true
                fi

                # ----- MITMPROXY REPLAY HOOK -----
                # When MITMPROXY_REPLAY_HOST is set (typically by
                # tools/run_final_sweep_ykush.sh), route this phone's HTTPS
                # through the laptop's running mitmdump replay server so the
                # app sees identical recorded responses across baseline and
                # Bangcle variants.
                #
                # Per-app gate: proxy is set ONLY if this app has at least one
                # non-empty .flow file under _external/mitm_flows/<app>/. Apps
                # with no recorded flows (calculator, metronome, androidskk on
                # P3, etc.) run without a proxy: no overhead, no risk of
                # strict-replay killing incidental system traffic.
                cell_replay_proxy_set=0
                if [ -n "${MITMPROXY_REPLAY_HOST:-}" ]; then
                    flow_dir="$ROOT/_external/mitm_flows/$app"
                    if compgen -G "$flow_dir/*.flow" > /dev/null; then
                        # any non-empty flow file?
                        for f in "$flow_dir"/*.flow; do
                            if [ -s "$f" ]; then
                                cell_replay_proxy_set=1
                                break
                            fi
                        done
                    fi
                    if [ "$cell_replay_proxy_set" -eq 1 ]; then
                        want_proxy="${MITMPROXY_REPLAY_HOST}:${MITMPROXY_REPLAY_PORT:-8080}"
                        timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell \
                            "settings put global http_proxy ${want_proxy}" \
                            >/dev/null 2>&1 || true
                        # READBACK: `settings put` can silently fail (permissions,
                        # adb hiccup, device policy). Never trust fire-and-forget
                        # on something that determines data validity. 2026-07-31.
                        got_proxy=$(timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell \
                            'settings get global http_proxy' 2>/dev/null | tr -d '\r')
                        if [ "$got_proxy" = "$want_proxy" ]; then
                            echo "  mitmproxy replay: proxy ON for $app → ${want_proxy} (readback OK)" >> "$stream_log"
                        else
                            echo "  mitmproxy replay: PROXY READBACK MISMATCH for $app — wanted '${want_proxy}', got '${got_proxy:-<empty>}'" >> "$stream_log"
                            if [ "${SWEEP_ALLOW_UNPROXIED_TIER_A:-0}" != "1" ]; then
                                echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep FAIL(mitm-proxy-set-failed) wall=0s" >> "$stream_log"
                                fail=$((fail + 1))
                                timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell \
                                    "settings put global http_proxy :0" >/dev/null 2>&1 || true
                                continue
                            fi
                        fi
                    else
                        echo "  mitmproxy replay: SKIP for $app (no .flow file)" >> "$stream_log"
                    fi
                fi

                # Snapshot the mitmproxy replay-hit count BEFORE the cell so we
                # can prove after the cell that the flow was ACTUALLY SERVED,
                # not merely that a proxy was configured. mitmdump prints a
                # "[replay] << HTTP/..." line for every response served from the
                # recorded flow; a pass-through to the live internet does not.
                # Counting the delta avoids parsing mitmproxy's timestamps.
                MITM_LOG_PATH="${MITMPROXY_LOG_FILE:-$LOG_DIR/mitmproxy_${SWEEP_TAG}.log}"
                # NOTE: `grep -c` prints "0" AND exits 1 when there are no
                # matches, so `|| echo 0` appends a SECOND zero and the variable
                # becomes "0\n0", which then blows up the $(( )) arithmetic with
                # "syntax error in expression". Use `|| true` plus a default.
                # 2026-08-04: count replays served TO THIS PHONE, not globally.
                # The mitmdump log is shared by all three phones, so a global
                # delta includes a concurrent phone's traffic. Measured that day:
                # every pdfviewer cell truly served 5 responses, but the one
                # overlapping pair reported 8 and 10. The dangerous direction is
                # that a phone serving ZERO borrows its neighbour's count and
                # PASSES the very guard meant to catch an un-proxied cell.
                phone_ip="${WIFI_ADDR[$phone]%%:*}"
                replay_hits_before=0
                if [ "$app_needs_mitm" -eq 1 ] && [ -f "$MITM_LOG_PATH" ]; then
                    replay_hits_before=$("$ROOT/tools/mitm_replay_count.sh" "$MITM_LOG_PATH" "$phone_ip" 2>/dev/null || true)
                    replay_hits_before=${replay_hits_before:-0}
                fi

                # ----- WATCHDOG -----
                # setsid + bash wrapper records the session-leader PID so we can
                # `kill -KILL -<pid>` (negative PID = kill the whole process group)
                # after `timeout` fires. Without this, `subprocess.Popen` grandchildren
                # of the AR python (adb, JVM) survive `timeout`'s SIGTERM and the cell
                # hangs indefinitely. On 2026-06-04 Calculator P6 rep5 ran 8 h before
                # finally exiting because the JVM held the parent's pipe open.
                # Empirically verified: this pattern kills `subprocess.Popen` orphans.
                # ---- AR LAUNCH SPACING (2026-08-04) ----
                # AndroidRunner names its output directory `YYYY.MM.DD_HHMMSS`:
                # SECOND resolution, no PID, no phone. Two AR processes that start
                # in the same second therefore SHARE one output directory: the
                # second to write config.json overwrites the first's, and both
                # phones' data lands under one run_id.
                #
                # This is not theoretical and it is not rare. The three streams are
                # launched 2 s apart and their cells take near-identical time, so
                # they PHASE-LOCK: it happened on the very first parallel cells of
                # this sweep (output/2026.08.04_023641 held Pixel 6-W and Pixel 9-W).
                # The original runner's only defence was a 2 s stagger at stream
                # launch, which drifts away within a cell or two.
                #
                # Space the launches instead. The lock is held only long enough to
                # stamp the time, so the common case costs nothing; a stream only
                # waits when another really did launch within AR_LAUNCH_GAP_S. The
                # gap must exceed the jitter in AR's start-up-to-mkdir latency
                # (venv activate + imports, ~2-3 s here), hence 5 s.
                AR_LAUNCH_GAP_S="${AR_LAUNCH_GAP_S:-5}"
                (
                    flock -w 60 6 || exit 0
                    _last=$(cat "$LOG_DIR/.ar_launch_stamp" 2>/dev/null || echo 0)
                    [[ "$_last" =~ ^[0-9]+$ ]] || _last=0
                    _now=$(date +%s)
                    _gap=$(( _now - _last ))
                    if [ "$_gap" -lt "$AR_LAUNCH_GAP_S" ]; then
                        sleep $(( AR_LAUNCH_GAP_S - _gap ))
                    fi
                    date +%s > "$LOG_DIR/.ar_launch_stamp"
                ) 6>"$LOG_DIR/.ar_launch.lock"

                local sid_file="$LOG_DIR/.cell_sid_${phone}_$$.pid"
                rm -f "$sid_file"
                # ---- PORT WATCH (2026-08-06, opt-out via SWEEP_PORT_WATCH=0) ----
                # Records this phone's BOARD port state for the whole cell, so a
                # CONTAM(float_mode_suspect) verdict can be attributed. On the
                # 2026-08-06 run, 42 % of pixel9 cells and 22 % of pixel6 cells
                # were discarded because the kernel reported STATUS=2 (Charging)
                # MID-workload, not in teardown (checked on 12 cells; 9 showed it
                # at 0-89 % through). AR cannot be the cause (disable_usb ->
                # profile -> enable_usb, repetitions=1), nor a ykushcmd race
                # (ykush_power.sh serialises every access behind an flock), nor
                # another phone's top-up (the first contaminated cell preceded any
                # top-up). That leaves real VBUS vs a spurious charger-driver
                # status, which need OPPOSITE fixes, so measure instead of guess.
                #
                # Polls the BOARD over USB, never the phone, so it adds no radio or
                # CPU load to the device under measurement. Entirely non-fatal: a
                # failure here must never fail a cell.
                watch_pid=""
                if [ "${SWEEP_PORT_WATCH:-1}" = "1" ]; then
                    port_watch_log="$LOG_DIR/portwatch_${SWEEP_TS}_${phone}_${app}_${variant}_r${rep}.log"
                    "$ROOT/tools/ykush_port_watch.sh" "${YKPORT[$phone]}" "$port_watch_log" 5 \
                        >/dev/null 2>&1 &
                    watch_pid=$!
                fi

                (
                    cd "$AR_DIR"
                    # shellcheck disable=SC1090
                    [ -f "$VENV_ACTIVATE" ] && . "$VENV_ACTIVATE"
                    setsid bash -c '
                        echo $$ > "$1"
                        shift
                        exec "$@"
                    ' bash "$sid_file" \
                       timeout --signal=TERM --kill-after=10s "${CELL_TIMEOUT_S}" \
                       env APPIUM_BUILD_LABEL="$variant" APPIUM_SERVER_URL="$appium_url" \
                           MASTEREXP_RECUT_CMD="$ROOT/tools/ykush_ar_power.sh $phone off" \
                       python3 -u . "examples/batterymanager/$cfg" \
                       > "$cell_log" 2>&1
                )
                rc=$?
                # Stop the port watch and report its verdict. Non-fatal throughout.
                if [ -n "$watch_pid" ]; then
                    kill "$watch_pid" 2>/dev/null || true
                    wait "$watch_pid" 2>/dev/null || true
                    if [ -f "${port_watch_log:-/nonexistent}" ]; then
                        # EXCLUDE THE TEARDOWN WINDOW. AR calls enable_usb() at
                        # Experiment.py:207 -- BEFORE after_run -- and the watcher
                        # (MAX_S=900) is still sampling when it does. So every HEALTHY
                        # cell ends with a short run of ON samples, and asking "was the
                        # port ever ON?" reported "real VBUS reached the phone" on
                        # essentially every cell. Measured across the first 44 cells of
                        # the VEXT run: 42 ended with a trailing ON run, 2 never went
                        # ON, and ZERO had an ON sample anywhere else.
                        #
                        # A STRICTLY trailing run is teardown. Anything else is real:
                        # power that arrives and then goes away mid-cell still reports,
                        # because that is exactly case (a).
                        _pw=$(awk '
                            /^20/ { st[++n] = $2 }
                            END {
                                trail = 0
                                for (i = n; i >= 1 && st[i] == "ON"; i--) trail++
                                mid = 0; unk = 0
                                for (i = 1; i <= n - trail; i++) {
                                    if (st[i] == "ON")      mid++
                                    if (st[i] == "UNKNOWN") unk++
                                }
                                for (i = n - trail + 1; i <= n; i++) if (st[i] == "UNKNOWN") unk++
                                printf "%d %d %d %d", n, mid, trail, unk
                            }' "$port_watch_log" 2>/dev/null)
                        _tot=$(echo "$_pw" | awk '{print $1}'); _mid=$(echo "$_pw" | awk '{print $2}')
                        _trail=$(echo "$_pw" | awk '{print $3}'); _unk=$(echo "$_pw" | awk '{print $4}')
                        if [ "${_mid:-0}" -gt 0 ] 2>/dev/null; then
                            echo "  PORT WATCH: REAL VBUS — port ${YKPORT[$phone]} ON for $_mid of $_tot samples MID-CELL (trailing teardown run was $_trail). $port_watch_log" >> "$stream_log"
                        elif [ "${_unk:-0}" -gt 0 ] 2>/dev/null; then
                            echo "  PORT WATCH: no mid-cell VBUS ($_tot samples, $_trail teardown, $_unk UNREADABLE — not proof of OFF). Any STATUS=2 is a charger-driver artifact." >> "$stream_log"
                        else
                            echo "  PORT WATCH: no mid-cell VBUS ($_tot samples, $_trail teardown). Any STATUS=2 this cell is a charger-driver artifact, not VBUS." >> "$stream_log"
                        fi
                    fi
                fi

                # Belt-and-suspenders: always SIGKILL the whole process group, regardless
                # of rc. Safe on a healthy exit (group is already empty); critical on a hang.
                local sid=$(cat "$sid_file" 2>/dev/null)
                rm -f "$sid_file"
                if [ -n "$sid" ]; then
                    kill -KILL -"$sid" 2>/dev/null
                fi

                # ---- POST-CELL RE-CUT (unconditional, first thing after AR) ----
                # AR calls usb_handler.enable_command at the END of every experiment, so
                # the phone is CHARGING the moment AR returns. In the uhubctl runner that
                # gap is what drove all three phones from 60 % to 100 % overnight on
                # 2026-07-27: nothing cut the port again until the NEXT cell's top-up
                # phase, and on a timeout/crash path there might not be a next cell.
                #
                # Cut here, before the logcat and mitmproxy teardowns (each of which is
                # several seconds of adb), on every exit path (PASS, FAIL, TIMEOUT and
                # crash alike). The next cell's top-up is the only thing that ever powers
                # a port back up.
                if ykush_port_off "$phone" >/dev/null 2>&1; then
                    echo "  post-cell: port cut (AR re-enabled it at experiment end)" >> "$stream_log"
                else
                    echo "  post-cell: WARNING — ykush_port_off failed for $phone" >> "$stream_log"
                fi

                # ----- LOGCAT CAPTURE STOP -----
                # PHASE_END marker via `adb shell log` before killing the reader
                # so downstream parsers see the workload window boundary.
                if [ -n "$logcat_pid" ]; then
                    timeout 3 adb -s "${WIFI_ADDR[$phone]}" shell "log -p i -t HARNESS \
                        'PHASE_END cell=${phone}/${app}/${variant}/r${rep} \
                        result=${result:-UNKNOWN}'" >/dev/null 2>&1 || true
                fi
                # Kill the background logcat reader started before the mitmproxy
                # hook. Safe on all exit paths (PASS/FAIL/TIMEOUT/CRASH); if the
                # PID is stale (already reaped), kill is a no-op.
                # 2026-07-31: bounded kill/wait so adb
                # logcat stuck in a blocked read against an unreachable phone
                # cannot hang the next cell: SIGTERM first, wait 3s, then SIGKILL
                # + one more short wait so we always progress.
                if [ -n "$logcat_pid" ]; then
                    kill "$logcat_pid" 2>/dev/null
                    for _ in 1 2 3; do
                        kill -0 "$logcat_pid" 2>/dev/null || break
                        sleep 1
                    done
                    kill -KILL "$logcat_pid" 2>/dev/null
                    wait "$logcat_pid" 2>/dev/null || true
                fi
                t_end=$(date +%s)
                wall=$((t_end - t_start))

                # ----- MITMPROXY REPLAY HOOK: tear-down -----
                # Symmetric with the ON hook above the watchdog. Only fires if
                # the ON hook set the proxy (avoids spurious adb calls on
                # non-network cells). Runs unconditionally if proxy WAS set so
                # the phone doesn't get stuck pointing at a dead proxy if the
                # cell timed out or crashed.
                if [ "${cell_replay_proxy_set:-0}" -eq 1 ]; then
                    timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell \
                        "settings put global http_proxy :0" >/dev/null 2>&1 || true
                    echo "  mitmproxy replay: proxy OFF for $app" >> "$stream_log"
                fi

                # Compute the replay-hit delta now (cheap), but DEFER the verdict
                # until after `result` is finalised further below: at this point
                # in the loop `result` has not been assigned yet.
                replay_delta=""
                if [ "$app_needs_mitm" -eq 1 ] && [ "${cell_replay_proxy_set:-0}" -eq 1 ]; then
                    replay_hits_after=0
                    if [ -f "$MITM_LOG_PATH" ]; then
                        # See the note on replay_hits_before: `grep -c || echo 0`
                        # yields "0\n0" on no-match. Use `|| true` + default.
                        replay_hits_after=$("$ROOT/tools/mitm_replay_count.sh" "$MITM_LOG_PATH" "$phone_ip" 2>/dev/null || true)
                        replay_hits_after=${replay_hits_after:-0}
                    fi
                    replay_delta=$(( replay_hits_after - replay_hits_before ))
                    echo "  mitmproxy replay: ${replay_delta} response(s) served from flow during this cell" >> "$stream_log"
                fi

                # Recovery on timeout.
                # Unlike the uhubctl runner, this does NOT power the port back on. That
                # restore existed because the Plugable's data path and its power were the
                # same switch, so a cut phone was unreachable for the force-stop. Here the
                # force-stop goes over WiFi-ADB, which needs no USB power at all, so the
                # restore was pure charging risk for no benefit.
                if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
                    recovery_lvl=$(timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell "dumpsys battery | grep '^  level:'" 2>/dev/null | awk '{print $2}' | tr -d '\r')
                    echo "  recovery: cell timed out at level=${recovery_lvl:-unknown}; force-stop over WiFi-ADB, port stays cut" >> "$stream_log"
                    app_id=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['application_id'])" "$AR_DIR/examples/batterymanager/$cfg" 2>/dev/null || true)
                    if [ -n "${app_id:-}" ]; then
                        timeout 5 adb -s "${WIFI_ADDR[$phone]}" shell "am force-stop $app_id; dumpsys battery reset" >/dev/null 2>&1 || true
                    fi
                    timeout=$((timeout + 1))
                    result="TIMEOUT"
                elif [ "$rc" -eq 0 ]; then
                    pass=$((pass + 1))
                    result="PASS"
                else
                    fail=$((fail + 1))
                    result="FAIL(rc=$rc)"
                fi

                # 2026-06-30: AR can exit rc=0 even after throwing an
                # internal AdbError (e.g. INSTALL_FAILED_NO_MATCHING_ABIS surfaces
                # as "Could not launch <pkg>", observed on Pixel 9 with the broken
                # NSC+Bangcle APKs lacking arm64-v8a). When this happens AR:
                #   1. creates a fresh output/<ts>/ dir with experiment.log + config.json
                #      but NO data/ subdir, and
                #   2. exits 0 to the shell.
                # Without explicit detection we end up classifying these zombie cells
                # as PASS and the state CSV records run_id pointing at a STALE
                # (previous-cell) output dir whose batterymanager CSV looks healthy.
                # Defense: scan THIS cell's output dir (newest, must be >= t_start)
                # for AdbError / Could not launch / INSTALL_FAILED markers.
                #
                # 2026-07-01: in parallel mode, `ls -td | head -1`
                # can pick a sibling phone's dir instead of THIS phone's dir. The
                # broken-AR case writes NO data/<phone>/ subdir so the run_id
                # resolver's filter below doesn't help here. Instead we
                # match config.json's `devices` key against WIFI_ALIAS[$phone]
                # (verified 2026-07-01 that AR's config.json carries this).
                #
                # 2026-08-04: config.json ALONE is not sufficient. AR names its
                # output dir by SECOND-resolution timestamp, so two AR processes
                # that start in the same second SHARE one directory, and the
                # second one to write config.json overwrites the first's. Observed
                # on the very first parallel cells of this sweep:
                #   output/2026.08.04_023641/config.json -> devices: [Pixel 6-W]
                #   output/2026.08.04_023641/data/       -> Pixel 6-W AND Pixel 9-W
                # pixel9's cell had run fine, scenarios and all, but was marked
                # FAIL(ar_no_output_dir) because config.json named pixel6. Worse,
                # anything globbing data/*/* in that dir could read the OTHER
                # phone's energy CSV. Prefer the presence of data/<alias>/, which
                # survives a shared dir; fall back to config.json for the
                # crashed-before-data case that this check exists to catch.
                if [ "$result" = "PASS" ]; then
                    phone_alias="${WIFI_ALIAS[$phone]}"
                    cell_output_dir=$(ls -td "$AR_DIR/examples/batterymanager/output/"*/ 2>/dev/null | while read d; do
                        d_mtime=$(stat -c %Y "$d" 2>/dev/null || echo 0)
                        [ "$d_mtime" -lt "$t_start" ] && continue
                        if [ -d "${d}data/${phone_alias}" ]; then
                            echo "$d"; break
                        fi
                        if [ -f "${d}config.json" ] && \
                           grep -qE "\"${phone_alias}\"" "${d}config.json" 2>/dev/null; then
                            echo "$d"; break
                        fi
                    done | head -1)
                    if [ -n "$cell_output_dir" ]; then
                        # 2026-07-31 fix: regex was "NativeExperiment:.*Error" and
                        # missed USBHandlerException (thrown when uhubctl -a 0 fails
                        # because hub silicon disappeared mid-sweep). That AR path
                        # exits rc=0 despite the crash, so PASS was recorded with
                        # empty run_id: 611 cells silently lost overnight of 07-30.
                        # Added: USBHandlerException, generic .*Exception under
                        # NativeExperiment prefix, and the specific uhubctl-error
                        # marker text ("Could not execute USB command").
                        if [ -f "${cell_output_dir}experiment.log" ] && \
                             grep -qE "AdbError|Could not launch|INSTALL_FAILED_[A-Z_]+|NativeExperiment:.*(Error|Exception)|USBHandlerException|Could not execute USB command" "${cell_output_dir}experiment.log" 2>/dev/null; then
                            err_kind=$(grep -oE "INSTALL_FAILED_[A-Z_]+|AdbError|USBHandlerException|Could not launch \"[^\"]+\"" "${cell_output_dir}experiment.log" | head -1 | tr -d '"' | tr ' ' '_')
                            result="FAIL(ar_${err_kind:-launch_error})"
                            pass=$((pass - 1))
                            fail=$((fail + 1))
                            echo "  POST-CELL: AR experiment.log contains launch error: $err_kind — marked FAIL" >> "$stream_log"
                        fi
                    else
                        # No dir matching this phone's config.json AND mtime >= t_start
                        # → AR either didn't run, wrote to a stale dir, or crashed
                        # before writing config.json.
                        result="FAIL(ar_no_output_dir)"
                        pass=$((pass - 1))
                        fail=$((fail + 1))
                        echo "  POST-CELL: no AR output dir >= cell start matching phone_alias=${phone_alias} — marked FAIL" >> "$stream_log"
                    fi
                fi

                # Resolve the AR run_id (latest under output dir) for the state file.
                # Fix 2026-06-21: in parallel mode with
                # 3 streams, taking the literally newest output dir can pick another
                # phone's just-created dir. Filter by this phone's alias subdir so we
                # only see output dirs belonging to this phone.
                # 2026-06-30: also constrain to dirs created
                # after t_start, so we never resolve to a stale (previous-cell) dir
                # when AR's current attempt didn't produce a data subdir.
                phone_alias="${WIFI_ALIAS[$phone]}"
                run_id=$(ls -td "$AR_DIR/examples/batterymanager/output/"*/ 2>/dev/null | while read d; do
                    d_mtime=$(stat -c %Y "$d" 2>/dev/null || echo 0)
                    if [ "$d_mtime" -ge "$t_start" ] && [ -d "${d}data/${phone_alias}" ]; then echo "$d"; break; fi
                done | head -1 | xargs -I{} basename {})

                # 2026-07-31 defense-in-depth: if AR exited rc=0 but produced no
                # data/${phone_alias} subdir, the "PASS" is a zombie: AR crashed
                # mid-workload but exited cleanly (e.g. USBHandlerException when
                # the hub silicon disappeared). Empty run_id + PASS = 611 silent
                # data losses observed 2026-07-31. Force this to FAIL.
                if [ "$result" = "PASS" ] && [ -z "$run_id" ]; then
                    result="FAIL(ar_empty_run_id)"
                    pass=$((pass - 1))
                    fail=$((fail + 1))
                    echo "  POST-CELL: PASS but empty run_id — AR crashed without writing data — marked FAIL" >> "$stream_log"
                fi

                # POST-CELL CONTAMINATION CHECK
                # If the cell produced clean energy data, mark PASS. If it produced
                # PASS-rc but the raw CSV shows contamination, mark CONTAM
                # instead. CONTAM cells are re-run by --resume.
                #
                # 2026-06-21: Pixel 9 maxfg fuel gauge reports POSITIVE
                # current_now (sign-flipped) while the charge IC is NOT actually
                # engaged. Voltage stays flat (e.g. 4085 mV stdev 0 over 100 s),
                # confirming no real charging. Per Linux power_supply_class docs,
                # positive = current INTO cell, so a sign-flip artifact under
                # status=Discharging is a known maxfg behavior after charger-
                # presence transitions. AR's power calc already uses abs(),
                # so energy magnitudes are correct: we only need to discriminate
                # sign-artifact (PASS) from real charging (CONTAM).
                #
                # Discriminator: BATTERY_PROPERTY_STATUS from the kernel.
                #   - Status 3 = Discharging during positive samples → sign artifact, PASS
                #   - Status 2 = Charging during positive samples → real CONTAM
                # Fallback when STATUS column absent (legacy CSV): old heuristic.
                if [ "$result" = "PASS" ] && [ -n "$run_id" ]; then
                    contam=$(python3 -c "
import csv, glob, statistics, sys
from pathlib import Path
# 2026-06-21: was 'Pixel 3' in '$phone' which never matched
# the lowercase 'pixel3' passed in, so P3 sign inversion never fired and the
# CONTAM heuristic mis-flagged every P3 cell as float_mode_suspect. Match the
# actual phone key.
P3_SIGN = -1 if '$phone' == 'pixel3' else 1
# Find the per-sample CSV for this run.
#
# SCOPED TO THIS PHONE (2026-08-04). This used to glob 'data/*/*/…' and take
# csvs[0]. AR names its output dir by SECOND-resolution timestamp, so two AR
# processes starting in the same second share one directory, as observed on the
# first parallel cells of this sweep, where output/2026.08.04_023641/data/ held
# BOTH 'Pixel 6-W' and 'Pixel 9-W'. With the wildcard, this phone's
# contamination verdict could be computed from the OTHER phone's samples, and
# the P3 sign correction above would be applied to the wrong device.
csvs = list(Path('$AR_DIR/examples/batterymanager/output/$run_id')
            .glob('data/$phone_alias/*/batterymanager/*.csv'))
if not csvs:
    print('no_csv'); sys.exit(0)
try:
    with open(csvs[0]) as f:
        rows = list(csv.DictReader(f))
except Exception:
    print('csv_err'); sys.exit(0)
if len(rows) < 50:
    print('few_samples'); sys.exit(0)
currents = [int(r['BATTERY_PROPERTY_CURRENT_NOW'])/1000 * (-1 if P3_SIGN < 0 else 1) for r in rows]
# convention now: negative = discharge
pos_pct = sum(1 for c in currents if c > 0) / len(currents) * 100
abs_mean = statistics.mean(abs(c) for c in currents)
# maxfg sign-artifact discriminator using BATTERY_PROPERTY_STATUS.
# Android BatteryManager status codes: 1=UNKNOWN 2=CHARGING 3=DISCHARGING
# 4=NOT_CHARGING 5=FULL.
have_status = 'BATTERY_PROPERTY_STATUS' in rows[0]
status_pos_charging = 0
status_pos_discharging = 0
if have_status:
    for i, c in enumerate(currents):
        if c <= 0:
            continue
        try:
            s = int(rows[i]['BATTERY_PROPERTY_STATUS'])
        except (ValueError, TypeError):
            continue
        if s == 2:
            status_pos_charging += 1
        elif s in (3, 4, 5):
            status_pos_discharging += 1
if abs_mean < 30:
    print('fuel_gauge_maintenance')  # fuel gauge in maintenance mode
elif pos_pct >= 5:
    # Discriminate using STATUS if available
    if have_status and (status_pos_charging + status_pos_discharging) > 0:
        if status_pos_discharging >= 0.9 * (status_pos_charging + status_pos_discharging):
            # >=90% of pos samples report Discharging status → maxfg sign artifact, accept
            print('ok_sign_artifact_issue21')
        else:
            # Material fraction of pos samples report Charging status → real CONTAM
            print('float_mode_suspect')
    else:
        # Legacy CSV (no STATUS): use the conservative old heuristic
        print('float_mode_suspect')
else:
    print('ok')
" 2>/dev/null)
                    # Treat "ok" and any "ok_*" suffix (e.g. ok_sign_artifact_issue21) as PASS.
                    if [ -n "$contam" ] && [ "${contam#ok}" = "$contam" ]; then
                        # contam value does not start with "ok" → real contamination
                        result="CONTAM($contam)"
                        pass=$((pass - 1))
                        fail=$((fail + 1))
                        echo "  POST-CELL: contamination detected ($contam) — marked CONTAM, will re-run on --resume" >> "$stream_log"
                    elif [ "$contam" != "ok" ] && [ -n "$contam" ]; then
                        # contam starts with "ok_" → noted artifact but PASS the cell
                        echo "  POST-CELL: accepted ($contam) — known fuel-gauge artifact, PASS" >> "$stream_log"
                    fi
                fi

                # ----- REPLAY-SERVED VERIFICATION (2026-07-31) -----
                # Prove the recorded flow was ACTUALLY USED, not merely that a
                # proxy was pointed at it. Proxy-set + mitmdump-listening are
                # each necessary but neither is sufficient: the app can still
                # reach the live internet via cert-pinning bypass, cached
                # content, or a host absent from the flow being forwarded
                # upstream. mitmdump prints "[replay] << HTTP/..." ONLY for
                # responses served from the recorded flow, so a zero delta over
                # the cell proves the recording was never used.
                #
                # Placed here, after `result` is final, so we never clobber a
                # genuine FAIL/TIMEOUT verdict: we only downgrade a PASS.
                if [ -n "$replay_delta" ] && [ "$replay_delta" -le 0 ] 2>/dev/null; then
                    echo "  WARNING: $app is a Tier-A app but ZERO responses were served from the recorded flow." >> "$stream_log"
                    echo "           Its network came from the live internet or a cache — this cell's energy" >> "$stream_log"
                    echo "           and scenario outcome are NOT comparable across variants." >> "$stream_log"
                    if [ "$result" = "PASS" ]; then
                        result="CONTAM(no_replay_served)"
                        pass=$((pass - 1))
                        fail=$((fail + 1))
                        echo "  POST-CELL: marked CONTAM(no_replay_served) — will re-run on --resume" >> "$stream_log"
                    fi
                fi

                # Append to per-phone state file for resume capability
                echo "${app},${variant},${rep},${result%%(*},${run_id},${wall},$(date -u +%FT%TZ)" >> "$state_file"
                echo "[$(date +%H:%M:%S)] DONE  $phone $app/$variant rep$rep $result wall=${wall}s log=$cell_log" >> "$stream_log"

                # Inter-cell minimum wait. Phone stays cut (no top-up restore): the
                # next cell's top-up phase (if level < 90) will restore power.
                if [ "$INTER_CELL_MIN_S" -gt 0 ] 2>/dev/null; then
                    elapsed_post=$(( $(date +%s) - t_end ))
                    remaining=$(( INTER_CELL_MIN_S - elapsed_post ))
                    if [ "$remaining" -gt 0 ]; then
                        sleep "$remaining"
                    fi
                fi

                # PAUSE FILE CHECK: if the operator `touch`'d the pause file, exit cleanly
                # after the current cell. Resume with --resume picks up where we left off.
                if [ -f "$STATE_DIR/.pause" ]; then
                    log "  $phone: pause-file detected; exiting stream cleanly after this cell"
                    break
                fi
    done   # task in queue

    # Stream summary
    if [ "${safety_stop:-0}" = "1" ]; then
        log "  $phone STOPPED EARLY: battery fell below SAFETY_MIN_LEVEL. Charge it, then --resume."
    fi
    log "  $phone stream finished: total=$total pass=$pass fail=$fail timeout=$timeout skipped=$skipped"
    echo "STREAM_SUMMARY $phone total=$total pass=$pass fail=$fail timeout=$timeout skipped=$skipped" >> "$stream_log"
}

# ---------- main ----------
log "============ sweep_runner_parallel_ykush.sh started ============"
log "  apps:           $APPS"
log "  variants:       $VARIANTS"
log "  phones:         $PHONES"
log "  reps:           $REPS"
log "  cell-timeout:   ${CELL_TIMEOUT_S}s"
log "  SoC band:       ${BAND_LOW}-${BAND_HIGH}% (hard ceiling ${BAND_MAX}%, stall cap ${MAX_TOPUP_S}s)"
log "  settle:         ${SETTLE_S}s"
log "  AR configs:     *${CFG_SUFFIX}.json"
log "  power:          YKUSH3 ${YKUSH_SERIAL} via tools/ykush_power.sh (topup-parallel=${TOPUP_PARALLEL})"
log "  master log:     $SWEEP_LOG"
log "  sweep tag:      $SWEEP_TAG"
log "  resume mode:    $RESUME"
log "  state dir:      $STATE_DIR"
log "  dry-run:        $DRY_RUN"
log ""

# Pre-sweep health checks. Fail fast: we don't want a 3-day sweep to die at
# hour 18 because of a preventable infra issue.
log "Pre-sweep health checks..."

# 1. Disk free. Each AR cell produces ~30-100 MB. A 1000-cell sweep needs at
#    minimum ~30 GB; require 50 GB headroom by default.
disk_free_gb=$(df -BG "$AR_DIR" | tail -1 | awk '{print $4}' | tr -d 'G')
MIN_DISK_GB="${MIN_DISK_GB:-50}"
if [ -n "$disk_free_gb" ] && [ "$disk_free_gb" -lt "$MIN_DISK_GB" ] 2>/dev/null; then
    log "FATAL: only ${disk_free_gb} GB free on $AR_DIR; need >= ${MIN_DISK_GB} GB for sweep"
    log "       Delete or archive old runs under android-runner/examples/batterymanager/output/"
    exit 4
fi
log "  disk free: ${disk_free_gb} GB ≥ ${MIN_DISK_GB} GB OK"

# 2. WiFi-ADB reachable on every requested phone. Without this, AR can't talk
#    to the phone after USB-power-cut.
#    Bounce a stale socket before condemning the phone. WiFi-ADB regularly keeps
#    reporting `device` while every shell returns nothing, and this gate is the
#    single point where that costs the most: it aborts the whole sweep. An
#    unattended launch that follows hours of charging would find nothing running
#    in the morning because one TCP connection went stale overnight (pixel3 did
#    exactly this at 00:38 on 2026-08-07; one reconnect fixed it).
#    This weakens nothing: a phone that is genuinely gone still fails all three
#    attempts and still aborts.
IFS=',' read -ra PHONE_ARR <<< "$PHONES"
adb_unreachable=()
for phone in "${PHONE_ARR[@]}"; do
    reachable=0
    for attempt in 1 2 3; do
        if timeout 8 adb -s "${WIFI_ADDR[$phone]}" shell echo ok 2>/dev/null | grep -q "^ok$"; then
            reachable=1; break
        fi
        if [ "$attempt" -lt 3 ]; then
            log "  ${phone}: WiFi-ADB silent (${attempt}/3) — reconnecting ${WIFI_ADDR[$phone]}"
            timeout 10 adb disconnect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
            sleep 1
            timeout 15 adb connect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
            sleep 2
        fi
    done
    [ "$reachable" -eq 1 ] || adb_unreachable+=("$phone")
done
if [ "${#adb_unreachable[@]}" -gt 0 ]; then
    log "FATAL: WiFi-ADB not reachable for: ${adb_unreachable[*]} (after 3 attempts with reconnects)"
    log "       Run tools/setup_wifi_adb.sh <usb-serial> for each unreachable phone first."
    exit 4
fi
log "  WiFi-ADB: all ${#PHONE_ARR[@]} phones reachable OK"

# 3. The YKUSH board itself, and the calibrated phone -> port map.
#    NOT a sudoers check: ykushcmd reaches the board through libusb
#    (/dev/bus/usb/<bus>/<dev>, NOT /dev/hidraw*) and access comes from the udev
#    rule at /etc/udev/rules.d/99-ykush.rules, which is ours (a copy is
#    scratchpad/99-ykush.rules), as Yepkit ships none.
#    Running it under sudo would be actively harmful: the sudoers entry here
#    grants NOPASSWD to /usr/sbin/uhubctl only, so `sudo ykushcmd` would block on
#    a password prompt and hang an unattended 30-hour sweep at 3 a.m.
log "Checking YKUSH board + calibrated port map..."
if ! load_ykush_portmap "$PHONES"; then
    log "FATAL: YKUSH board or port map unavailable; aborting"
    exit 3
fi

# EXIT trap. Any exit path (Ctrl-C, laptop sleep, a stream dying mid-topup, an
# `exit 3/4` in a preflight branch) that happened while a port was ON would leave
# a phone charging indefinitely. The 2026-07-29 audit added this to the uhubctl
# runner after exactly that; here it uses the board's single atomic all-ports
# report, and the trap-safe variant deliberately does no python, no adb and no
# subshell that could itself fail and mask the original exit code.
cleanup_ykush_ports_on_exit() {
    local rc=$?
    echo "[$(date '+%H:%M:%S')] EXIT trap: cutting ALL YKUSH ports (rc=$rc)"
    ykush_emergency_cut_all
    return $rc
}
trap cleanup_ykush_ports_on_exit EXIT INT TERM

# Make the cut survive a board reset, a replug or a host reboot. The board
# re-derives live port state from a stored per-port default at every power-on
# (datasheet §6.6); without this an overnight reset lands all three ports ON,
# which means a phone charged to 100 %, the exact outcome the rig exists to
# avoid. The datasheet does not state the factory default, so set it explicitly.
ykush_set_safe_defaults >/dev/null 2>&1 \
    && log "  power-on default: OFF on all 3 ports (survives reset/replug)" \
    || log "  WARNING: could not set the power-on default — a board reset would land ports ON"

# NOTE: there is deliberately no AR-config sync step here. See header §2: on this
# board that sync is what would ARM the silent-charge defect, and it would write
# the wrong port numbers as well.

# ---------- CUT-EFFECTIVENESS PROBE ----------
# This is the check the whole rig turns on, so it is worth stating what it must
# be able to do: FAIL. The uhubctl version of this probe passed on this hardware
# while the phones were charging, because it concluded "PPPS works" from
# uhubctl's own output, the tool reporting on itself. It reads the PHONE here,
# over WiFi-ADB, from raw sysfs.
#
# Two independent readings per phone:
#   usb/online:      1 means the phone sees a charger attached.
#   usb/voltage_now: µV on the USB input; a properly cut port reads ~0.
# Either one alone is enough to abort. `dumpsys battery` is NOT used: AR's
# device.unplug() mocks it above sysfs, so it would pass unconditionally.
#
# All three ports are cut TOGETHER and sampled with no intervening board traffic.
# That was forced on the Plugable, whose shared inner controller re-powered a
# held-cut port within ~3 s of a restore/cut cycle on any other port (6/6
# reproducible), and the old per-phone cut/sample/restore probe manufactured the
# very leak it was testing for. The YKUSH switches each port with its own MOSFET
# so it should not do this, but sampling in the steady state is also simply the
# TRUE operating condition: in a parallel sweep all three phones hold their own
# port cut for the whole workload.
#
# Ports are left CUT afterwards. The uhubctl version restored power here, which
# meant the sweep began with three charging phones.
log "Cut-effectiveness probe (all ports cut together, read from the phones)..."
cut_probe_fail=0
declare -A PROBE_UV=() PROBE_ONLINE=()

probe_sample_all() {
    for phone in "${PHONE_ARR[@]}"; do
        local waddr="${WIFI_ADDR[$phone]}"
        [ -n "$waddr" ] || { PROBE_UV[$phone]=""; PROBE_ONLINE[$phone]=""; continue; }
        PROBE_ONLINE[$phone]=$(timeout 8 adb -s "$waddr" shell \
            "cat /sys/class/power_supply/usb/online 2>/dev/null" | tr -d '\r ' | head -1)
        PROBE_UV[$phone]=$(timeout 8 adb -s "$waddr" shell \
            "cat /sys/class/power_supply/usb/voltage_now 2>/dev/null" | tr -d '\r ' | head -1)
    done
}

if ! ykush_cut_all; then
    log "FATAL: could not cut all YKUSH ports — the board is not switching."
    exit 3
fi
sleep 5                       # let the charge ICs notice and settle
probe_sample_all

# Re-sample anything that looks live, still with every port held cut and still
# with no board traffic. A transient settles; a real leak does not.
recheck=0
for phone in "${PHONE_ARR[@]}"; do
    v="${PROBE_UV[$phone]:-}"; o="${PROBE_ONLINE[$phone]:-}"
    { [ "$o" = "1" ] || { [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -gt 1000000 ] 2>/dev/null; }; } && recheck=1
done
if [ "$recheck" = "1" ]; then
    log "  residual VBUS seen on first pass — re-sampling after a longer settle"
    for phone in "${PHONE_ARR[@]}"; do
        log "    first pass: $phone online=${PROBE_ONLINE[$phone]:-?} voltage_now=${PROBE_UV[$phone]:-?} µV"
    done
    sleep 10
    probe_sample_all
fi

for phone in "${PHONE_ARR[@]}"; do
    usb_v_uv="${PROBE_UV[$phone]:-}"
    usb_online="${PROBE_ONLINE[$phone]:-}"
    ykp="${YKPORT[$phone]:-?}"
    if [ -z "${WIFI_ADDR[$phone]:-}" ]; then
        log "  $phone: no WiFi-ADB address — cannot probe; skipping"
        continue
    fi
    if [ "$usb_online" = "1" ]; then
        log "  FATAL: $phone on board port $ykp — usb/online=1 with ALL ports held cut."
        log "         The phone still sees a charger. Every cell would measure a charging phone."
        log "         Check the cable is in a YKUSH DOWNSTREAM socket (not the upstream port),"
        log "         then re-run tools/ykush_calibrate.py --verify."
        cut_probe_fail=1
        continue
    fi
    if ! [[ "$usb_v_uv" =~ ^[0-9]+$ ]]; then
        log "  $phone on board port $ykp — online=0, voltage_now unreadable ('$usb_v_uv'); accepting on online alone"
        continue
    fi
    if [ "$usb_v_uv" -gt 1000000 ]; then
        log "  FATAL: $phone on board port $ykp — usb voltage_now=${usb_v_uv} µV (>1 V) with ALL ports held cut."
        log "         This survived a re-sample, so it is real residual VBUS, not a transient."
        cut_probe_fail=1
    else
        log "  $phone on board port $ykp — online=0, voltage_now=${usb_v_uv} µV (clean cut)"
    fi
done
if [ "$cut_probe_fail" = "1" ]; then
    exit 3
fi
log "  all ports verified cut; leaving them cut for the sweep"
log ""

# Start one Appium server per phone
log "Starting Appium servers..."
for phone in "${PHONE_ARR[@]}"; do
    port="${APPIUM_PORT[$phone]}"
    appium_log="$LOG_DIR/parallel_${SWEEP_TS}_appium_${phone}.log"
    ensure_appium "$port" "$appium_log" || {
        log "FATAL: cannot start appium for $phone"
        exit 3
    }
done
log ""

# Launch per-phone streams in parallel
log "Launching per-phone streams in parallel..."
declare -A STREAM_PID
for phone in "${PHONE_ARR[@]}"; do
    run_phone_stream "$phone" &
    STREAM_PID[$phone]=$!
    log "  $phone stream PID ${STREAM_PID[$phone]}"
    # Stagger by 2 s so AR's output-dir timestamps don't collide
    sleep 2
done
log ""

# Wait for all
log "Waiting for all streams..."
for phone in "${PHONE_ARR[@]}"; do
    wait "${STREAM_PID[$phone]}" 2>/dev/null
done

# End-of-sweep: LEAVE EVERY PORT CUT.
#
# The uhubctl runner restored power to all three phones here. That was needed
# because its next startup ran a hub-port discovery script, which resolves a phone by
# walking /sys/bus/usb/devices/: a cut phone is invisible there, so a sweep that
# ended cut could not be restarted. This runner resolves ports from the
# calibrated map by BOARD SERIAL, so it does not need the phones enumerated, and
# the restore becomes pure charging risk: three phones left on a live port for
# however many hours pass before anyone looks at the rig.
#
# Cut is also the correct resting state per the standing rule: the hub stays off
# whenever a sweep is not running. Bring phones back into band deliberately, with
# tools/ykush_band_keeper.sh once.
log "End-of-sweep: cutting all ports (they stay cut until a top-up or the band keeper)..."
ykush_cut_all && log "  all 3 ports OFF, verified" || log "  WARNING: cut-all reported a problem — check tools/ykush_power.sh status"
sleep 2

log ""
log "============ sweep_runner_parallel_ykush.sh finished ============"
log "Per-stream summaries:"
for phone in "${PHONE_ARR[@]}"; do
    grep "^STREAM_SUMMARY" "$LOG_DIR/parallel_${SWEEP_TS}_${phone}.log" 2>/dev/null || log "  $phone: no summary written"
done
