#!/usr/bin/env bash
# ykush_port_watch.sh: record a YKUSH port's ON/OFF state for the duration of
# one measurement cell.
#
# Written 2026-08-06.
#
# WHY THIS EXISTS
# ---------------
# The run of 2026-08-06 discarded 25+ cells as CONTAM(float_mode_suspect):
# pixel9 42 % of cells, pixel6 22 %, pixel3 0 %. Those are cells where the
# kernel reported BATTERY_PROPERTY_STATUS=2 (Charging) *during measurement*.
# Checking twelve of them, 9 showed charging MID-WORKLOAD (at 0-3 %, 8-18 %,
# 38-46 %, 61-72 % through the cell), not in the teardown window. So it is not
# an artifact of AR re-enabling USB at the end.
#
# Three candidate causes were eliminated by inspection:
#   - AR re-plugging mid-run: its order is disable_usb -> start_profiling ->
#     workload -> stop_profiling -> enable_usb, with repetitions=1.
#   - A ykushcmd race between the three parallel streams: ykush_power.sh
#     already serialises every board access behind an flock.
#   - Another phone's top-up: pixel9's first contaminated cell was at 00:13,
#     before any phone had dropped below the 40 % band floor.
#
# That leaves two possibilities with OPPOSITE fixes:
#
#   (a) VBUS really does reach the phone mid-cell: a board or wiring fault.
#       Fix: hardware.
#   (b) VBUS never arrives and the charger driver reports STATUS=2 spuriously
#       after charger-presence transitions. pixel9 has shown this kind of
#       artifact before (the maxfg fuel-gauge sign flip).
#       Fix: teach the CONTAM classifier to ignore it, and recover the cells.
#
# This script discriminates them. It samples the BOARD's own port state, which
# is host-side over USB and touches the phone not at all, so it cannot perturb
# the energy measurement it is auditing. Sampling `usb/online` on the phone over
# WiFi-ADB would have added radio and CPU wakeups to every cell, which is
# exactly the kind of instrument that changes what it measures.
#
# Reading the log afterwards:
#   port never ON during the cell + phone said STATUS=2  -> driver artifact (b)
#   port ON at any point during the cell                 -> real VBUS (a)
#
# Usage:
#   ykush_port_watch.sh <board_port> <outfile> [interval_s]
#   kill "$!" to stop it; it also stops on its own after MAX_S.
set -uo pipefail

port="${1:-}"
out="${2:-}"
interval="${3:-5}"
MAX_S="${YKUSH_WATCH_MAX_S:-900}"

if [ -z "$port" ] || [ -z "$out" ]; then
    echo "usage: ykush_port_watch.sh <board_port> <outfile> [interval_s]" >&2
    exit 64
fi

ROOT="/home/irena/Documents/Master Experiment"
# shellcheck source=/dev/null
source "$ROOT/tools/ykush_power.sh"

# LOCK BEHAVIOUR. ykush_power.sh hardcodes `flock -w 15` and does not read any
# override, so this observer queues behind real cuts and power-ons like every
# other board access. That is the right priority order, and the cost is small:
# one `ykushcmd -g` holds the board for ~100 ms, so at a 5 s interval a watcher
# occupies the lock about 2 % of the time.
#
# A sample that fails for any reason is written as UNKNOWN rather than skipped.
# A gap in this log must never be readable as "the port was off": that is the
# reading the whole file exists to make trustworthy.

started=$(date +%s)
printf '# ykush_port_watch port=%s interval=%ss started=%s\n' \
       "$port" "$interval" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$out"

while :; do
    now=$(date +%s)
    [ $(( now - started )) -ge "$MAX_S" ] && break
    # _raw: takes a BOARD PORT number. `ykush_port_state` takes a phone name and
    # resolves it through the calibrated map, the wrong entry point here.
    state=$(ykush_port_state_raw "$port" 2>/dev/null) || state="UNKNOWN"
    [ -z "$state" ] && state="UNKNOWN"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$state" >> "$out"
    sleep "$interval"
done

printf '# ended=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$out"
