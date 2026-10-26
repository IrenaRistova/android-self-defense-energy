#!/usr/bin/env bash
# adb_reconnect_watchdog.sh: revive a phone's WiFi-ADB the moment the sweep
# starts failing cells because it cannot reach it.
#
# Written 2026-08-07 as a stopgap. tools/sweep_runner_parallel_ykush.sh now
# reconnects and retries such cells itself.
#
# WHY THIS EXISTS
# ---------------
# At 19:00 on 2026-08-07 pixel3 dropped off WiFi-ADB mid-sweep. The phone was
# fine: it pinged, it sat at 55%, and a single `adb connect` brought it back.
# But while it was gone the sweep did this, once per cell, at wall=0s:
#
#   pre-cell: level= status= (band=40-70)
#   [ykush_power] VBUS still live on <pixel3-ip>:5555 after 20s
#                 (usb/online= battery/status=)
#   ABORT: could not prove VBUS is off for pixel3.
#   DONE  pixel3 tipuous/freerasp_debug rep9 FAIL(cut-unverified) wall=0s
#
# `ykush_verify_cut` fails closed when it cannot read the phone, which is the
# right call: refusing to measure a possibly-charging phone is exactly what it
# is for. The problem is what happens next: the stream CONTINUES to the next
# cell. That is the same queue-burn shape that wrote 977 FAIL rows in one night
# on 2026-08-06, and the fix applied then only covered the safety-min-level
# path. Two cells were lost here before a human noticed; unattended, pixel3
# would have walked its remaining ~150 cells at ~60 s each.
#
# The runner cannot be fixed while it is running (bash re-reads a script by byte
# offset, so editing a live one corrupts it). This watchdog closes the gap from
# outside until the runner is restarted with its own retry.
#
# WHY RECONNECTING HERE IS SAFE
# -----------------------------
# It only ever fires on a FAIL(cut-unverified) line, which means that cell has
# already aborted: the phone is NOT being measured. `adb connect` on a phone
# mid-cell would add a wakeup to a live measurement; on an aborted one it costs
# nothing. That is why this triggers on the failure and never on a timer.
#
# Usage: adb_reconnect_watchdog.sh [poll_seconds]
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"
POLL="${1:-20}"

# Replicators put their own phones' Wi-Fi ADB addresses here.
declare -A ADDR=(
    [pixel3]="<pixel3-ip>:5555"
    [pixel6]="<pixel6-ip>:5555"
    [pixel9]="<pixel9-ip>:5555"
)

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

# The per-phone STREAM log for the newest sweep.
#
# `parallel_*_pixel3.log` is NOT specific enough: it also matches
# `parallel_<ts>_appium_pixel3.log`, and `ls -t` returns whichever of the two was
# written most recently. The Appium log never contains a verdict line, so the
# watchdog would sit silent forever. Worse, it worked on its first launch
# purely because the stream log happened to be the newer file at that instant.
# An intermittently-correct watchdog is more dangerous than none, so exclude the
# companion logs explicitly.
stream_log_for() {
    ls -t "$ROOT"/tools/sweep_logs/parallel_*_"${1}".log 2>/dev/null \
        | grep -v '_appium_' | head -1
}

# Baseline from what is ALREADY in the logs, rather than starting at 0.
# Starting at 0 makes every historical failure look new, so the watchdog fires a
# reconnect the moment it launches, at which point the phone may well be back
# and mid-cell, and an adb round-trip into a live measurement is precisely what
# this script exists to avoid. Only NEW failures should trigger anything.
declare -A LAST_COUNT
for p in pixel3 pixel6 pixel9; do
    f=$(stream_log_for "$p")
    c=0
    if [ -n "$f" ]; then
        c=$(grep -c "FAIL(cut-unverified)" "$f" 2>/dev/null)
        [[ "$c" =~ ^[0-9]+$ ]] || c=0
    fi
    LAST_COUNT[$p]=$c
    log "baseline $p: $c existing cut-unverified failure(s) — will act only on new ones"
done

log "watching for FAIL(cut-unverified); poll=${POLL}s"

while :; do
    for phone in pixel3 pixel6 pixel9; do
        f=$(stream_log_for "$phone")
        [ -n "$f" ] || continue

        # NO `|| echo 0` here. `grep -c` already prints 0 when it matches
        # nothing, but it also EXITS 1, so the fallback fires as well and the
        # variable becomes "0\n0", which then blows up the integer test below.
        # The net effect is exactly backwards: phones that have never failed are
        # the ones that stop being watched.
        n=$(grep -c "FAIL(cut-unverified)" "$f" 2>/dev/null)
        [[ "$n" =~ ^[0-9]+$ ]] || n=0

        if [ "$n" -gt "${LAST_COUNT[$phone]}" ]; then
            LAST_COUNT[$phone]=$n
            addr="${ADDR[$phone]}"

            # Distinguish the two causes before acting, because they need
            # opposite responses and the runner's message conflates them.
            port_on=""
            case "$phone" in
                pixel9) port_on=$(bash "$ROOT/tools/ykush_power.sh" state 1 2>/dev/null) ;;
                pixel6) port_on=$(bash "$ROOT/tools/ykush_power.sh" state 2 2>/dev/null) ;;
                pixel3) port_on=$(bash "$ROOT/tools/ykush_power.sh" state 3 2>/dev/null) ;;
            esac

            if [ "$port_on" = "ON" ]; then
                log "$phone: cut-unverified AND board port is genuinely ON — this is HARDWARE, not adb. Not touching it."
                continue
            fi

            log "$phone: cut-unverified #${n} with the board port OFF — unreachable phone, reconnecting $addr"
            timeout 10 adb disconnect "$addr" >/dev/null 2>&1
            sleep 1
            timeout 15 adb connect "$addr" >/dev/null 2>&1
            sleep 2
            if [ "$(timeout 10 adb -s "$addr" shell echo ok 2>/dev/null | tr -d '\r')" = "ok" ]; then
                log "$phone: RECOVERED — adb responds again"
            else
                log "$phone: still unreachable after reconnect. Phone may be off the network entirely."
            fi
        fi
    done
    sleep "$POLL"
done
