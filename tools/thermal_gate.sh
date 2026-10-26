#!/usr/bin/env bash
# thermal_gate.sh: give a phone a break when it is too hot to measure.
#
# Written 2026-08-09.
#
# WHY THIS EXISTS
# ---------------
# Two separate failures on 2026-08-08, same root cause:
#
#  1. pixel6 hit ~45.5 C and its charger simply stopped accepting current
#     (VBUS present at 4.875 V, dumpsys status=2, raw sysfs "Not charging",
#     ~0 mA). It refused two top-ups, kept measuring through the backoff, and
#     stopped its stream at the safety floor. Roughly 6 h of rig time lost.
#  2. Cells were measured across a 28-53 C spread with no record of whether the
#     SoC was throttling at the time, which put an uncontrolled term into every
#     dE the sweep produced.
#
# WHICH TEMPERATURE, AND WHY NOT THE OBVIOUS ONE
# ----------------------------------------------
# NOT `dumpsys battery` EXTRA_TEMPERATURE. That is a sticky broadcast: measured
# 2026-08-09 across 1113 cells it holds EXACTLY ONE value per cell over ~1400
# samples and ~160 s. It is a frozen pre-cell snapshot and can never show a
# phone heating up during a cell. The 2026-08-04 sweep's `battery_temp_avg_c`
# and `battery_temp_max_c` are bit-identical for this reason.
#
# NOT the battery at all, for a throttling question. Measured on pixel6 idle:
# battery 29.6 C while BIG sat at 37 C, and under load the CPU clusters run tens
# of degrees above the pack. A battery reading cannot tell you the SoC is
# throttling.
#
# NOT `/sys/class/thermal/`: 0 zones are readable on these devices.
#
# USE `dumpsys thermalservice`, and specifically:
#   * `Thermal Status:`  Android's OWN throttling state. 0=NONE, 1=LIGHT,
#     2=MODERATE, 3=SEVERE, 4=CRITICAL, 5=EMERGENCY, 6=SHUTDOWN. This is not a
#     proxy for throttling, it IS the throttling signal, straight from the
#     framework that does the throttling.
#   * The block after `Current temperatures from HAL:`.
#
# THE PARSING TRAP: that dumpsys prints TWO temperature blocks. The first,
# `Cached temperatures:`, is stale: on pixel6 it reported BIG=61.0 C, unchanged
# across three samples 8 s apart, while the live block read BIG=33/34/33 C. A
# parser that takes the first match is wrong by ~28 C. Always read after
# `Current temperatures from HAL:`.
#
# Sensors are selected by mType (0=CPU 1=GPU 2=BATTERY 3=SKIN), never by name:
# the two SoC families here share no sensor names at all. SKIN is what Android's
# own throttling policy keys on, so it is the gate; the hottest CPU cluster is
# the leading indicator.
#
# Measured 2026-08-09 with pixel3 playing video and the others idle:
#   pixel3   status=2  skin 43.2 C  cpu 51.7 C  battery 41.1 C
#   pixel6   status=0  skin 32.0 C  cpu 36.0 C  battery 29.7 C
#   pixel9   status=0  skin 31.7 C  cpu 35.0 C  battery 31.5 C
# Note pixel3's CPU running 10 C above its own battery.
#
# Usage:
#   thermal_gate.sh read <phone>              one-shot, human readable
#   thermal_gate.sh json <phone>              one-shot, machine readable
#   thermal_gate.sh wait <phone>              block until cool enough
#
# Env:
#   SKIN_MAX_C=0       cool-enough threshold on VIRTUAL-SKIN in C (0 = off; 0 < x <= 45 is set to 0)
#   STATUS_MAX=1       highest acceptable Thermal Status (1 = light; lower values are raised to 1)
#   COOL_MAX_S=1800    give up waiting after this and proceed, loudly (at most 600)
#   POLL_S=60
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"

# Replicators put their own phones' Wi-Fi ADB addresses here.
declare -A ADDR=(
    [pixel3]="<pixel3-ip>:5555"
    [pixel6]="<pixel6-ip>:5555"
    [pixel9]="<pixel9-ip>:5555"
)

# SKIN_MAX_C is OFF by default (0 = disabled). An absolute skin threshold cannot
# be set from idle readings, and setting one from a guess is worse than not
# setting one. Measured on the 2026-08-04 sweep, PRE-CELL battery temperature was
# median 40.5 C (p10 36.6, p90 45.6, max 53.1) and skin runs ~2 C above battery,
# so a 38 C skin gate would have rested the rig on ~79 % of its own cells. Any
# absolute number here must come from a calibration run that samples skin DURING
# real cells, which has never been done.
#
# Thermal Status is the default gate instead, and it is the better one: it is
# self-calibrating. Android decides when THIS device on THIS day is throttling,
# so it needs no threshold from us and travels across the two SoC families.
SKIN_MAX_C="${SKIN_MAX_C:-0}"
STATUS_MAX="${STATUS_MAX:-1}"
COOL_MAX_S="${COOL_MAX_S:-1800}"
POLL_S="${POLL_S:-60}"

# ── CALIBRATION, measured 2026-08-10 45 min into a live sweep ────────────────
# All three phones sit at Thermal Status=1 (LIGHT) under a sustained Appium
# workload:
#     pixel3  status=1  skin 40.6 C  cpu 47.4 C
#     pixel6  status=1  skin 38.3 C  cpu 43.0 C
#     pixel9  status=1  skin 38.6 C  cpu 44.0 C
# Status 1 is therefore the NORMAL operating state of this rig, not a fault. A
# gate at STATUS_MAX=0 holds EVERY cell for the full COOL_MAX_S and then measures
# it anyway: pure lost rig time for no change in what gets recorded. Within 45
# minutes it had already logged 53 wait-polls and roughly halved throughput.
#
# Status 2 (MODERATE) is the level worth stopping for: pixel3 reached it under
# video playback, and that is where throttling actually bites.
#
# CLAMP, not a default: a STATUS_MAX below 1 is raised to 1. Overriding a caller
# is normally wrong, so this is deliberately narrow: it only ever RAISES a value
# that experience shows is unusable. sweep_runner_parallel_ykush.sh passes
# STATUS_MAX=1 itself.
if [ "${STATUS_MAX:-1}" -lt 1 ] 2>/dev/null; then
    STATUS_MAX=1
fi
# Same clamp for the skin gate, same reason. SKIN_MAX_C=38 is a number taken from
# IDLE readings before any calibration existed. Under load skin sits at
# 38.3-40.6 C on these phones, so 38 fires on every cell forever. Any threshold
# at or below 45 C is below normal operating temperature and is therefore not a
# gate, it is a stall. Disable it and let Thermal Status decide, since that one IS
# calibrated against this rig.
if [ -n "${SKIN_MAX_C:-}" ] && awk -v v="${SKIN_MAX_C:-0}" 'BEGIN{exit !(v>0 && v<=45)}'; then
    SKIN_MAX_C=0
fi
# Likewise cap the stall. A phone that has not dropped a thermal level in 10 min
# of an active sweep is not going to in 30: the workload is what is heating it.
if [ "${COOL_MAX_S:-600}" -gt 600 ] 2>/dev/null; then
    COOL_MAX_S=600
fi

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

# Read thermalservice once. Echoes "status skin big little" or nothing.
#
# Writes to a temp file rather than using $(...) on a pipe: command substitution
# waits for the PIPE to close, not for the process to exit, so a wedged adb that
# ignores TERM hangs the caller forever despite `timeout`. That exact bug held a
# poll loop for 5.5 h on 2026-08-09 while a port stayed powered. -k escalates to
# KILL; the temp file means we only ever wait on the process.
thermal_read() {
    local phone="$1" tmp status skin big little
    [ -n "${ADDR[$phone]:-}" ] || return 1
    tmp=$(mktemp)
    timeout -k 2 15 adb -s "${ADDR[$phone]}" shell "dumpsys thermalservice" > "$tmp" 2>/dev/null
    if [ ! -s "$tmp" ]; then rm -f "$tmp"; return 1; fi

    status=$(tr -d '\r' < "$tmp" | sed -n 's/^Thermal Status: *\([0-9]*\).*/\1/p' | head -1)
    # ONLY the live block. See the parsing trap in the header.
    local live; live=$(tr -d '\r' < "$tmp" | sed -n '/Current temperatures from HAL/,$p')

    # Key on mType, NOT mName. The sensor names are SoC-specific and share
    # nothing between the two families in this rig:
    #   Tensor  (pixel6/pixel9): VIRTUAL-SKIN, BIG, MID, LITTLE, G3D
    #   Snapdragon 845 (pixel3): fps-therm-monitor, cpu0-gold-usr, gpu0-usr, maxfg
    # mType is the framework's TemperatureType enum and is identical on both:
    #   0=CPU  1=GPU  2=BATTERY  3=SKIN  4=USB_PORT
    # A name-based parser silently returned nothing at all for pixel3.
    #
    # CPU is the MAX across clusters: a phone throttles on its hottest core, and
    # pixel3 exposes eight of them.
    _max_of_type() {
        printf '%s\n' "$live" \
            | sed -n "s/.*mValue=\([0-9.]*\), mType=$1,.*/\1/p" \
            | sort -rn | head -1
    }
    skin=$(_max_of_type 3)
    big=$(_max_of_type 0)
    little=$(_max_of_type 2)   # battery, kept for continuity with the old column
    rm -f "$tmp"

    [ -n "$status" ] || return 1
    printf '%s %s %s %s' "$status" "${skin:-}" "${big:-}" "${little:-}"
}

cmd_read() {
    local phone="$1" out
    if ! out=$(thermal_read "$phone"); then
        log "$phone: UNPROVEN — thermalservice unreadable. Not asserting it is cool."
        return 3
    fi
    read -r st skin big little <<< "$out"
    printf '  %-8s Thermal Status=%s  skin=%s C  cpu_max=%s C  battery=%s C\n' \
        "$phone" "$st" "${skin:-?}" "${big:-?}" "${little:-?}"
    [ "${st:-9}" -le "$STATUS_MAX" ] 2>/dev/null || return 1
    return 0
}

cmd_json() {
    local phone="$1" out
    out=$(thermal_read "$phone") || { printf '{"phone":"%s","ok":false}\n' "$phone"; return 3; }
    read -r st skin big little <<< "$out"
    printf '{"phone":"%s","ok":true,"thermal_status":%s,"skin_c":%s,"cpu_max_c":%s,"battery_c":%s,"ts":"%s"}\n' \
        "$phone" "${st:-null}" "${skin:-null}" "${big:-null}" "${little:-null}" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# Block until the phone is cool enough to measure.
#
# Returns 0 cool, 2 gave up after COOL_MAX_S, 3 unreadable.
#
# It does NOT return 0 on an unreadable phone. A gate that passes when it cannot
# see is a check that cannot fail, so unreadable is reported as UNPROVEN and the
# caller decides.
cmd_wait() {
    local phone="$1" t0 now out st skin waited slept=0
    t0=$(date +%s)

    # Put the display out WHILE resting, and only while resting.
    #
    # Without this the "rest" is nearly useless: the phone waits with its screen
    # lit at brightness 255, which is a large fraction of its total draw, so it
    # sheds almost nothing. Observed 2026-08-10: pixel3 held twice at status=2
    # for the full 10 min and came out at the same temperature both times.
    # Compare 2026-08-08, when sleeping pixel6's screen took it 45.2 -> 42.7 C in
    # four minutes and unblocked its charger.
    #
    # NOT a measurement confound. This sits AFTER the port cut and BEFORE the
    # 60 s settle, and the screen is woken again before this function returns,
    # so the settle and the cell itself run with the display on, exactly as they
    # do for a cell that never waited. The only thing that changes is the state
    # of a phone during an idle period it would otherwise have spent heating
    # itself for no reason.
    _screen_off() {
        [ "$slept" -eq 1 ] && return 0
        timeout -k 2 8 adb -s "${ADDR[$phone]}" shell \
            "settings put global stay_on_while_plugged_in 0; input keyevent KEYCODE_SLEEP" \
            >/dev/null 2>&1
        slept=1
    }
    _screen_on() {
        [ "$slept" -eq 0 ] && return 0
        timeout -k 2 8 adb -s "${ADDR[$phone]}" shell \
            "input keyevent KEYCODE_WAKEUP" >/dev/null 2>&1
        slept=0
    }
    # Restore on ANY exit path, including a kill: a phone left dark would fail
    # the next cell's launch.
    trap '_screen_on' RETURN

    while :; do
        if ! out=$(thermal_read "$phone"); then
            _screen_on
            log "$phone: UNPROVEN — thermalservice unreadable; cannot confirm it is cool"
            return 3
        fi
        read -r st skin big little <<< "$out"
        now=$(date +%s); waited=$(( now - t0 ))

        local hot=0
        [ "${st:-9}" -gt "$STATUS_MAX" ] 2>/dev/null && hot=1
        # SKIN_MAX_C=0 means DISABLED, and that has to be checked explicitly:
        # `skin > 0` is true for every real temperature, so without this guard a
        # "disabled" threshold marks every phone hot forever. That is exactly
        # what happened on 2026-08-10: the gate stalled every cell for the full
        # timeout while claiming the skin limit was off.
        if [ -n "$skin" ] && awk -v b="${SKIN_MAX_C:-0}" 'BEGIN{exit !(b>0)}'; then
            awk -v a="$skin" -v b="$SKIN_MAX_C" 'BEGIN{exit !(a>b)}' && hot=1
        fi

        if [ "$hot" -eq 0 ]; then
            _screen_on
            [ "$waited" -gt 0 ] && log "$phone: cool after ${waited}s (status=$st skin=${skin}C)"
            return 0
        fi
        if [ "$waited" -ge "$COOL_MAX_S" ]; then
            _screen_on
            log "$phone: STILL HOT after $((waited/60))min (status=$st skin=${skin}C BIG=${big}C) — proceeding anyway, cell is thermally degraded"
            return 2
        fi
        # Screen out for the rest itself (see the note at the top of this function).
        _screen_off
        log "$phone: hot (status=$st skin=${skin}C BIG=${big}C) — resting ${POLL_S}s screen-off, ${waited}s so far"
        sleep "$POLL_S"
    done
}

case "${1:-}" in
    read) cmd_read "${2:?usage: read <phone>}" ;;
    json) cmd_json "${2:?usage: json <phone>}" ;;
    wait) cmd_wait "${2:?usage: wait <phone>}" ;;
    all)  rc=0; for p in pixel3 pixel6 pixel9; do cmd_read "$p" || rc=1; done; exit $rc ;;
    *)
        cat >&2 <<'USAGE'
usage: thermal_gate.sh <read|json|wait> <phone>
       thermal_gate.sh all

  read   one-shot, human readable; exit 1 if throttling
  json   one-shot, machine readable (for per-cell logging)
  wait   block until cool; 0=cool 2=gave up 3=unreadable

env: SKIN_MAX_C=0 STATUS_MAX=1 COOL_MAX_S=1800 (at most 600) POLL_S=60
USAGE
        exit 64 ;;
esac
