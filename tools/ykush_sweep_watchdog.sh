#!/usr/bin/env bash
# ykush_sweep_watchdog.sh: cut every port if the sweep dies, or if any phone
# climbs above the ceiling.
#
# Written 2026-08-04.
#
# WHAT THIS COVERS THAT THE SWEEP ALREADY DOESN'T
# ───────────────────────────────────────────────
# The runner and the wrapper both trap EXIT/INT/TERM and cut all ports, the port
# is re-cut after every cell, top-up stops at BAND_HIGH, and the board's power-on
# default is OFF. Between them, every ORDERLY death is covered.
#
# What is NOT covered is a death with no exit path: SIGKILL, an OOM kill, a VS
# Code crash taking the terminal with it, a kernel hang. If that happens while a
# top-up is in progress, the port stays live and the phone charges until someone
# notices. That is exactly what happened overnight on 2026-07-28 (71 % -> 100 %)
# and it is the failure the standing "never above ~70 %" rule exists to prevent.
#
# So the PRIMARY check here is host-side and costs the phones nothing: is the
# sweep process still alive? If it is gone, cut everything. Polling that is free.
#
# The SECONDARY check reads SoC, and is deliberately infrequent (default 15 min)
# because every `dumpsys battery` is a small wakeup on a phone that is in the
# middle of an energy measurement. 15 min is ample: charging from the 70 % band
# ceiling to 100 % takes ~40 min on bus power, so a runaway is caught with a wide
# margin, while the perturbation is well under one wakeup per cell.
#
# Never powers anything ON. Only ever cuts.
#
# Usage:
#   tools/ykush_sweep_watchdog.sh                 # watch the ykush sweep runner
#   BAND_MAX=75 SOC_POLL_S=900 tools/ykush_sweep_watchdog.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/ykush_power.sh"

WATCH_PATTERN="${WATCH_PATTERN:-sweep_runner_parallel_ykush.sh}"
PROC_POLL_S="${PROC_POLL_S:-60}"
SOC_POLL_S="${SOC_POLL_S:-900}"
BAND_MAX="${BAND_MAX:-75}"
# How long to wait for the sweep to appear before giving up. Must exceed the
# pre-flight, which runs a DEX-set gate and a class-set diff over 40 APKs and
# takes several minutes, so 120 s is too short.
GRACE_S="${GRACE_S:-1800}"

# Replicators put their own phones' Wi-Fi ADB addresses here.
declare -A ADDR=(
    [pixel3]="<pixel3-ip>:5555"
    [pixel6]="<pixel6-ip>:5555"
    [pixel9]="<pixel9-ip>:5555"
)

log() { printf '[%s] watchdog: %s\n' "$(date '+%F %H:%M:%S')" "$*"; }

soc() {
    timeout 10 adb -s "${ADDR[$1]}" shell 'dumpsys battery | grep "^  level:"' 2>/dev/null \
        | awk '{print $2}' | tr -d '\r'
}

log "armed. watching '$WATCH_PATTERN' every ${PROC_POLL_S}s; SoC ceiling ${BAND_MAX}% every ${SOC_POLL_S}s"

# Wait for the sweep to show up, so launching the watchdog first is not a trap.
waited=0
until pgrep -f "$WATCH_PATTERN" >/dev/null 2>&1; do
    sleep 5
    waited=$((waited + 5))
    if [ "$waited" -ge "$GRACE_S" ]; then
        log "sweep never appeared within ${GRACE_S}s — cutting all ports and exiting"
        ykush_emergency_cut_all
        exit 1
    fi
done
log "sweep is running; watching"

last_soc_check=$(date +%s)
while :; do
    sleep "$PROC_POLL_S"

    if ! pgrep -f "$WATCH_PATTERN" >/dev/null 2>&1; then
        # Confirm rather than react to a single miss: pgrep can race a fork.
        sleep 5
        if ! pgrep -f "$WATCH_PATTERN" >/dev/null 2>&1; then
            log "SWEEP IS GONE — cutting all ports"
            ykush_emergency_cut_all
            for p in pixel3 pixel6 pixel9; do
                log "  final SoC $p = $(soc "$p")%"
            done
            log "all ports cut; exiting"
            exit 0
        fi
    fi

    now=$(date +%s)
    if [ $(( now - last_soc_check )) -ge "$SOC_POLL_S" ]; then
        last_soc_check=$now
        for p in pixel3 pixel6 pixel9; do
            s=$(soc "$p")
            [[ "$s" =~ ^[0-9]+$ ]] || continue
            if [ "$s" -ge "$BAND_MAX" ]; then
                log "CEILING BREACH: $p at ${s}% >= ${BAND_MAX}% — cutting its port NOW"
                ykush_port_off "$p" || log "  CUT FAILED for $p"
            fi
        done
    fi
done
