#!/usr/bin/env bash
# ykush_band_keeper.sh: hold every phone inside the SoC band, on YKUSH3 power.
#
# Written 2026-08-03. Does not touch the uhubctl path or the old top-up logic.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY LEVEL-DRIVEN AND NOT TIME-DRIVEN
# ─────────────────────────────────────────────────────────────────────────────
# The old sweep top-up was `--max-topup 900`, i.e. "charge for up to 15 minutes".
# That is the wrong primitive: charge rate varies by phone, by SoC, by
# temperature, and (critically, see below) by how many phones are plugged in at
# once. A fixed window either stops short (leaving a phone below the floor for
# the next cell) or wastes rig time. This charges until the phone REACHES the
# target, polling as it goes, with a timeout only as a stall detector.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY ONE PHONE AT A TIME
# ─────────────────────────────────────────────────────────────────────────────
# Measured 2026-08-03 with the board BUS-POWERED from the laptop:
#
#   P3 alone .................. status=2 (charging), 1.5 A
#   all three powered ......... P3 status=2  +670 mA -> +530 mA
#                               P6 status=4  -220 mA   (net DISCHARGING)
#                               P9 status=4  -405 mA   (net DISCHARGING)
#
# The host port cannot feed three Pixels, so two of them refuse to charge and
# run down instead. The datasheet predicts exactly this: at a 5.00 V supply the
# downstream Vbus falls below the 4.75 V USB minimum at roughly 2.6 A aggregate,
# and three charging Pixels want ~4.5 A.
#
# WHEN THE EXTERNAL SUPPLY IS FITTED (jumper moved VBUS -> VEXT, 5.1-5.25 V,
# >=5 A) this constraint lifts and PARALLEL=1 may be set. Until then, serial.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHICH SoC SOURCE, AND WHY IT IS NOT THE OBVIOUS ONE
# ─────────────────────────────────────────────────────────────────────────────
# SoC comes from `dumpsys battery` level.
# Charge-state comes from raw sysfs /sys/class/power_supply/usb/online.
# Two different questions, two different sources, and BOTH choices are forced:
#
#  * NOT sysfs battery/capacity for SoC: on Pixel 3 that node reads a constant
#    100 while the phone is actually at 50% (verified: capacity=100,
#    charge_counter/charge_full=47.6%, dumpsys level=50). A keeper polling it
#    would believe P3 is permanently full and never charge it.
#
#  * NOT dumpsys for charge-state: Android Runner calls device.unplug() at
#    every experiment start, which MOCKS the dumpsys battery status above sysfs.
#    AndroidRunner/USBHandler.py says so in its own docstring: it "does NOT
#    actually stop charging the device through USB". A dumpsys-based cut check
#    passes unconditionally for the whole of every AR cell.
#
# ─────────────────────────────────────────────────────────────────────────────
# BAND CHOICE
# ─────────────────────────────────────────────────────────────────────────────
# Default 40-70%. The ceiling is not really about Li-ion wear here (measured
# cycle counts are 80/35/27 and all three packs still report >=100% of design
# capacity). It is about MEASUREMENT VALIDITY. Energy is integrated as
# ∫V·I·dt and pack voltage tracks SoC, so cells run at widely different SoC
# carry a systematic voltage difference unrelated to the protection variant.
# Below ~30% you additionally get voltage sag and rising internal resistance.
# A narrow band keeps that confound small; widen it only for throughput, and
# only knowingly.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/ykush_power.sh"

BAND_LOW="${BAND_LOW:-40}"        # charge a phone that falls below this
BAND_HIGH="${BAND_HIGH:-70}"      # ... up to this, then cut
BAND_MAX="${BAND_MAX:-75}"        # hard ceiling: cut immediately, no exceptions
POLL_S="${POLL_S:-30}"            # SoC poll interval while charging
STALL_S="${STALL_S:-900}"         # abort a top-up if SoC has not risen in this long
CHARGE_CAP_S="${CHARGE_CAP_S:-7200}"  # absolute per-phone safety cap
PARALLEL="${PARALLEL:-0}"         # set 1 ONLY with the external supply fitted
ADB_TRIES="${ADB_TRIES:-3}"       # adb reads to attempt, reviving between them
BAND_ROUNDS="${BAND_ROUNDS:-4}"   # waves of top-up before giving up on a phone

# Replicators put their own phones' Wi-Fi ADB addresses here.
declare -A ADDR=(
    [pixel3]="<pixel3-ip>:5555"
    [pixel6]="<pixel6-ip>:5555"
    [pixel9]="<pixel9-ip>:5555"
)

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

# ─────────────────────────────────────────────────────────────────────────────
# WHY EVERY PHONE READ RETRIES
# ─────────────────────────────────────────────────────────────────────────────
# WiFi-ADB goes stale without going down. The socket keeps reporting `device`
# in `adb devices` while every `adb shell` returns nothing at all. A single
# bare read therefore cannot distinguish "this phone is gone" from "this TCP
# connection needs bouncing", and the two want opposite responses.
#
# Treating the second as the first is not cheap. On 2026-08-07 at 00:38 pixel3
# was cut and abandoned at 25 % on one empty read, six minutes into a three-hour
# charge; it never rejoined the run. `adb disconnect && adb connect` brought it
# back on the first try, still at 25 %, perfectly healthy.
#
# So: revive and retry before concluding anything. A phone is declared
# unreachable only after ADB_TRIES reads across ADB_TRIES-1 reconnects, which is
# a real fault rather than a flapped socket. Cutting on genuine unreachability
# stays: that direction is fail-safe.
adb_revive() {
    local addr="$1"
    # -k so a wedged adb is KILLed rather than left alive holding descriptors,
    # for the same reason phone_read avoids pipes. See the note there.
    timeout -k 2 10 adb disconnect "$addr" >/dev/null 2>&1
    sleep 1
    timeout -k 2 15 adb connect "$addr" >/dev/null 2>&1
    sleep 2
}

# Read from a phone, reviving the connection between attempts.
# Echoes the output; returns non-zero (and echoes nothing) only if the phone is
# genuinely unreachable.
phone_read() {
    local phone="$1" cmd="$2" try v tmp
    tmp=$(mktemp)
    for (( try = 1; try <= ADB_TRIES; try++ )); do
        # WHY A TEMP FILE AND NOT $(timeout ... adb ...):
        #
        # Command substitution reads until the PIPE CLOSES, not until the child
        # exits. `timeout` returning does not close the pipe if adb ignored the
        # TERM and is still alive holding the write end, so `v=$(timeout 10 adb
        # ...)` can block FOREVER despite the timeout. That is not theoretical:
        # on 2026-08-09 this hung the top-up poll loop for 5.5 hours with
        # pixel3's port ON, and it charged from 57 % to 100 %, straight through
        # a 70 % target and a 75 % hard ceiling, because the loop never got to
        # evaluate either. `-k 2` then escalates to KILL, and writing to a file
        # means this function waits on the PROCESS, never on a pipe.
        timeout -k 2 10 adb -s "${ADDR[$phone]}" shell "$cmd" > "$tmp" 2>/dev/null
        v=$(tr -d '\r' < "$tmp")
        if [ -n "$v" ]; then rm -f "$tmp"; printf '%s' "$v"; return 0; fi
        if [ "$try" -lt "$ADB_TRIES" ]; then
            # STDERR, not stdout. Callers read this function through $(...), so
            # anything logged on stdout is captured as part of the value and
            # silently corrupts the SoC parse.
            log "$phone: empty adb read (${try}/${ADB_TRIES}) — reviving connection" >&2
            adb_revive "${ADDR[$phone]}"
        fi
    done
    rm -f "$tmp"
    return 1
}

# SoC via dumpsys (see the header for why not sysfs capacity).
soc() {
    local v
    v=$(phone_read "$1" 'dumpsys battery | grep "^  level:"' | awk '{print $2}')
    [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo ""
}

# Charge-state via raw sysfs (see the header for why not dumpsys).
vbus_online() {
    phone_read "$1" 'cat /sys/class/power_supply/usb/online' | tr -d '\n'
}

phones() { printf '%s\n' pixel3 pixel6 pixel9; }

report() {
    local p s o
    for p in $(phones); do
        s=$(soc "$p"); o=$(vbus_online "$p")
        printf '  %-8s soc=%-4s vbus=%-4s port=%s\n' \
            "$p" "${s:-?}" "${o:-?}" "$(ykush_port_state "$p" 2>/dev/null || echo '?')"
    done
}

# Charge ONE phone to BAND_HIGH. Level-driven; the timeouts are stall detectors,
# not the mechanism.
topup() {
    local phone="$1" target="${2:-$BAND_HIGH}"
    local start now last_soc cur t0 t_last_rise

    cur=$(soc "$phone")
    [ -n "$cur" ] || { log "$phone: unreachable, skipping"; return 3; }
    if [ "$cur" -ge "$target" ]; then
        log "$phone: already ${cur}% >= ${target}%, nothing to do"
        return 0
    fi

    log "$phone: ${cur}% -> ${target}%, powering port on"
    ykush_port_on "$phone" || { log "$phone: FAILED to power on"; return 5; }

    # Confirm VBUS actually arrived. If the phone never sees power there is no
    # point polling for 2 hours.
    sleep 8
    if [ "$(vbus_online "$phone")" != "1" ]; then
        log "$phone: powered the port but usb/online is not 1 — check the cable"
        ykush_port_off "$phone" || true
        return 8
    fi

    t0=$(date +%s); t_last_rise=$t0; last_soc=$cur
    while :; do
        sleep "$POLL_S"
        cur=$(soc "$phone")
        now=$(date +%s)

        if [ -z "$cur" ]; then
            log "$phone: went unreachable mid-charge — cutting for safety"
            ykush_port_off "$phone" || true
            return 3
        fi

        if [ "$cur" -ge "$target" ]; then
            log "$phone: reached ${cur}%, cutting"
            ykush_port_off "$phone" || { log "$phone: CUT FAILED"; return 5; }
            ykush_verify_cut "${ADDR[$phone]}" 20 || { log "$phone: cut did NOT remove VBUS"; return 8; }
            log "$phone: done at ${cur}%, VBUS confirmed off"
            return 0
        fi

        if [ "$cur" -gt "$last_soc" ]; then
            t_last_rise=$now; last_soc=$cur
            log "$phone: ${cur}%"
        elif [ $((now - t_last_rise)) -ge "$STALL_S" ]; then
            # Not a timer-based stop: this fires only when the phone genuinely
            # stopped gaining charge, which on bus power means the supply cannot
            # feed it (see the header measurements).
            log "$phone: STALLED at ${cur}% — no gain in $((STALL_S/60))min, cutting"
            ykush_port_off "$phone" || true
            return 9
        fi

        if [ $((now - t0)) -ge "$CHARGE_CAP_S" ]; then
            log "$phone: hit the ${CHARGE_CAP_S}s safety cap at ${cur}%, cutting"
            ykush_port_off "$phone" || true
            return 9
        fi
    done
}

# Bring every out-of-band phone back in, LOWEST SoC FIRST so the most urgent
# phone charges while the others sit safely cut.
#
# RETRIES IN WAVES. A top-up can end early for reasons that are not permanent:
# a phone that briefly stops answering, a stall that was really the fuel gauge
# relearning after a deep discharge, a cut that could not be verified. One wave
# per phone meant any of those dropped it from the run for good, and because
# the sweep's preflight then fails on an out-of-band phone, one transient blip
# at 00:38 could cost an entire unattended night. Each wave re-reads the truth
# from the phones, so a phone that has genuinely arrived is simply not pending.
bring_into_band() {
    local p s pending=() unreachable=() round entry ordered
    log "band ${BAND_LOW}-${BAND_HIGH}% (hard ceiling ${BAND_MAX}%)"

    # One extra pass beyond BAND_ROUNDS: the last wave's RESULT still has to be
    # assessed, or a run whose final wave succeeded would report failure.
    for (( round = 1; round <= BAND_ROUNDS + 1; round++ )); do
        # Ceiling first, always, before anything else gets powered.
        enforce_ceiling

        pending=(); unreachable=()
        for p in $(phones); do
            s=$(soc "$p")
            if [ -z "$s" ]; then
                # Unreadable is NOT "fine". Cut it: an unattended port we cannot
                # see the SoC behind is the one way this rig reaches 100 %.
                log "$p: unreachable — cutting its port (cannot enforce a ceiling blind)"
                ykush_port_off "$p" || log "$p: CUT FAILED while unreachable"
                unreachable+=("$p")
                continue
            fi
            [ "$s" -lt "$BAND_LOW" ] && pending+=("$s:$p")
        done

        if [ ${#pending[@]} -eq 0 ]; then
            if [ ${#unreachable[@]} -gt 0 ]; then
                # The defect this replaces: with every phone unreachable the old
                # code printed "all phones already in band" and returned 0,
                # having proved nothing about any of them.
                log "no phone is chargeable, but ${#unreachable[@]} are UNREACHABLE: ${unreachable[*]}"
                log "their ports are cut; band state is UNPROVEN for them"
                report
                return 4
            fi
            log "all phones verified in band"
            report
            return 0
        fi

        if [ "$round" -gt "$BAND_ROUNDS" ]; then
            log "gave up after ${BAND_ROUNDS} waves; still out of band: ${pending[*]#*:}"
            log "final state:"; report
            return 9
        fi

        [ "$round" -gt 1 ] && log "--- wave ${round}/${BAND_ROUNDS}: still below ${BAND_LOW}%: ${pending[*]#*:} ---"

        # Sort ascending by SoC -> the flattest phone is charged first.
        mapfile -t ordered < <(printf '%s\n' "${pending[@]}" | sort -t: -k1 -n)

        if [ "$PARALLEL" = "1" ]; then
            log "PARALLEL=1 — external 5.1-5.25V supply on VEXT (prove it with tools/verify_parallel_charging.sh)"
            for entry in "${ordered[@]}"; do topup "${entry#*:}" & done
            wait
        else
            for entry in "${ordered[@]}"; do
                log "--- topping up ${entry#*:} (was ${entry%%:*}%) ---"
                topup "${entry#*:}" || log "${entry#*:}: top-up returned $?"
            done
        fi
    done

}

# The safety net. Runs before any charging and can be run standalone.
enforce_ceiling() {
    local p s hit=0
    for p in $(phones); do
        s=$(soc "$p")
        if [ -z "$s" ]; then
            # A phone we cannot read is a phone whose ceiling we cannot enforce.
            # If its port is live, that is an unbounded charge: cut it. Skipping
            # here is what let three powered ports sit with no ceiling at all.
            if [ "$(ykush_port_state "$p" 2>/dev/null)" = "ON" ]; then
                log "$p: unreachable with its port ON — cutting (ceiling unenforceable)"
                ykush_port_off "$p" || log "$p: CUT FAILED while unreachable"
                hit=1
            fi
            continue
        fi
        if [ "$s" -ge "$BAND_MAX" ]; then
            log "$p: ${s}% >= hard ceiling ${BAND_MAX}% — cutting NOW"
            ykush_port_off "$p" || log "$p: CUT FAILED at the ceiling"
            hit=1
        fi
    done
    return $hit
}

# Absolute ceiling on how long a port may stay powered, enforced WITHOUT talking
# to the phone. No Pixel here needs more than ~2.5 h to cross the band; anything
# still drawing after this has escaped whatever was supposed to stop it.
MAX_PORT_ON_S="${MAX_PORT_ON_S:-10800}"   # 3 h

# Long-running safety daemon: never charges, only cuts. Safe to leave running
# alongside a sweep.
# ── FLOOR GUARD SUPPORT ──────────────────────────────────────────────────────
# Every guard layer before 2026-08-16 could only CUT. Nothing anywhere charged a
# phone that was merely being neglected, so a phone whose sweep arm finished had
# no floor at all: it simply drained until someone noticed.
#
# It cost a pack twice. 2026-08-08: pixel9 finished its arm at 17:28, sat
# unmanaged 8 h, reached 6 %. 2026-08-16: pixel3 finished at 14:44, sat unmanaged
# 8 h, reached 6 % again: same failure, same number, the second time in a batch
# where all three phones then sat below 40 % for hours after the sweep exited.
#
# The reason this is delicate rather than obvious: charging a phone that is being
# MEASURED destroys the cell. So the floor guard may only act on a phone it can
# PROVE is idle. Two proofs are accepted, in order:
#
#   1. No sweep holds tools/sweep_logs/.sweep.lock -> nothing is measuring
#      anything anywhere.
#   2. A sweep is running, but this phone's own stream log carries its
#      STREAM_SUMMARY line -> that phone's arm is finished, and the runner will
#      never schedule another cell on it.
#
# Anything else, including "I cannot tell", means DO NOT CHARGE. An unnecessary
# 20 % is cheap; a contaminated cell is silent and permanent.
FLOOR_MIN="${FLOOR_MIN:-25}"       # below this, intervene
FLOOR_TARGET="${FLOOR_TARGET:-50}" # ... and charge to here, not to the band top
# $HERE is tools/: this script has no $ROOT. An undefined $ROOT here would make
# the lock path "/tools/sweep_logs/.sweep.lock", which does not exist, and reading
# that as "no sweep is running" is the one mistake that would let the floor guard
# power a phone mid-measurement. Both helpers below therefore FAIL CLOSED:
# anything they cannot positively establish counts as "a sweep is running, leave
# it alone".
SWEEP_LOCK="${SWEEP_LOCK:-$HERE/sweep_logs/.sweep.lock}"

sweep_lock_held() {
    # 0 = a sweep holds it (or we could not tell). 1 = definitely free.
    exec 7>"$SWEEP_LOCK" 2>/dev/null || return 0   # cannot open -> assume held
    if flock -n 7 2>/dev/null; then
        flock -u 7 2>/dev/null; exec 7>&-; return 1
    fi
    exec 7>&-; return 0
}

phone_is_provably_idle() {
    local p="$1" f
    # Proof 1: the lock file exists AND is free -> nothing is measuring anywhere.
    if [ -e "$SWEEP_LOCK" ] && ! sweep_lock_held; then
        return 0
    fi
    # Proof 2: this phone's own stream log carries its STREAM_SUMMARY line, so
    # the runner has finished that arm and will never schedule another cell on it.
    f=$(ls -t "$HERE"/sweep_logs/parallel_*_"$p".log 2>/dev/null \
        | grep -v appium | head -1)
    [ -n "$f" ] || return 1              # no log at all -> cannot prove -> refuse
    grep -q "^STREAM_SUMMARY $p " "$f" 2>/dev/null && return 0
    return 1
}

guard() {
    log "guard: polling every ${POLL_S}s, cutting anything at or above ${BAND_MAX}%"
    log "guard: also cutting any port powered for more than $((MAX_PORT_ON_S/60))min, phone-independent"
    log "guard: FLOOR ${FLOOR_MIN}% -> charges to ${FLOOR_TARGET}%, but only a phone PROVEN idle"

    local -A on_since=()
    local -A floor_charging=()
    local p port st now s

    while :; do
        # ── Layer 1: SoC ceiling. Needs the phone. ────────────────────────────
        enforce_ceiling || true

        # ── Layer 2: port-on duration. Needs NOTHING from the phone. ─────────
        #
        # This exists because on 2026-08-09 layer 1 was not merely wrong, it was
        # never evaluated: a wedged adb read blocked the top-up poll loop for
        # 5.5 h with pixel3's port ON, and it charged 57 % -> 100 % straight
        # through both the 70 % target and the 75 % hard ceiling. Every guard we
        # had asked the phone a question first, so a phone that stopped answering
        # disabled all of them at once.
        #
        # The board's port state is read host-side over USB. A phone cannot
        # block it, hang it, or lie about it. If a port has been continuously
        # powered longer than any legitimate charge, cut it and ask questions
        # afterwards: an over-cut costs one top-up, an under-cut costs a pack.
        now=$(date +%s)
        for p in pixel3 pixel6 pixel9; do
            port=$(ykush_resolve_port "$p" 2>/dev/null) || continue
            st=$(ykush_port_state_raw "$port" 2>/dev/null)
            if [ "$st" != "ON" ]; then
                unset "on_since[$p]"
                continue
            fi
            if [ -z "${on_since[$p]:-}" ]; then
                on_since[$p]=$now
                continue
            fi
            if [ $(( now - on_since[$p] )) -ge "$MAX_PORT_ON_S" ]; then
                log "$p: port has been ON for $(( (now - on_since[$p]) / 60 ))min (cap $((MAX_PORT_ON_S/60))min) — CUTTING, phone not consulted"
                ykush_port_off "$p" || log "$p: CUT FAILED on the duration cap"
                unset "on_since[$p]"
            fi
        done

        # ── Layer 3: SoC FLOOR. The only layer that CHARGES. ─────────────────
        for p in $(phones); do
            s=$(soc "$p")
            [ -n "$s" ] && [[ "$s" =~ ^[0-9]+$ ]] || continue

            # Already floor-charging: stop at the target and hand back.
            if [ -n "${floor_charging[$p]:-}" ]; then
                if [ "$s" -ge "$FLOOR_TARGET" ]; then
                    log "$p: reached ${s}% (floor target ${FLOOR_TARGET}%) — cutting"
                    ykush_port_off "$p" || log "$p: CUT FAILED ending floor charge"
                    unset "floor_charging[$p]"
                fi
                continue
            fi

            [ "$s" -lt "$FLOOR_MIN" ] || continue

            if ! phone_is_provably_idle "$p"; then
                # Mid-arm. Its own next cell will top it up; charging now would
                # contaminate whatever is being measured.
                log "$p: ${s}% is under the ${FLOOR_MIN}% floor, but it is MID-SWEEP — leaving it alone"
                continue
            fi

            log "$p: ${s}% under floor ${FLOOR_MIN}% and its arm is finished — charging to ${FLOOR_TARGET}%"
            if ykush_port_on "$p"; then
                floor_charging[$p]=1
                unset "on_since[$p]"   # do not let layer 2 count this as a stuck port
            else
                log "$p: FAILED to power on for floor rescue"
            fi
        done

        sleep "$POLL_S"
    done
}

case "${1:-}" in
    once)     bring_into_band ;;
    topup)    topup "${2:?usage: topup <phone> [target]}" "${3:-$BAND_HIGH}" ;;
    ceiling)  enforce_ceiling; report ;;
    guard)    guard ;;
    status)   report ;;
    cut-all)  ykush_cut_all && log "all ports off" ;;
    *)
        cat >&2 <<'USAGE'
usage: ykush_band_keeper.sh <command>
  once              bring every out-of-band phone into band, lowest SoC first
  topup <phone> [%] charge one phone to the target (default BAND_HIGH)
  ceiling           cut anything at/above BAND_MAX, then report
  guard             daemon: only ever cuts, never charges
  status            print SoC / VBUS / port state for all three
  cut-all           power every port off

env: BAND_LOW=40 BAND_HIGH=70 BAND_MAX=75 POLL_S=30 STALL_S=900 PARALLEL=0
     PARALLEL=1 is ONLY safe with the external 5.1-5.25V supply and the
     jumper moved from VBUS to VEXT — on laptop bus power only one phone
     charges and the other two actively discharge.
USAGE
        exit 64 ;;
esac
