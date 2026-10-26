#!/usr/bin/env bash
# verify_parallel_charging.sh: PROVE the external supply can charge every phone
# at once, before anything is allowed to assume it.
#
# Written 2026-08-06.
#
# WHY THIS EXISTS
# ---------------
# `TOPUP_PARALLEL=1` (sweep_runner) and `PARALLEL=1` (band keeper) are manual
# flags. Nothing in the codebase can see whether the board's jumper is on VBUS or
# VEXT, or whether an external supply is connected at all, so those flags are a
# claim with no check behind them. The principle here: a check that cannot fail
# is worse than no check.
#
# The failure they invite is silent and expensive. Measured on laptop bus power,
# 2026-08-03, with all three ports powered:
#
#     pixel3  +670 mA  charging
#     pixel6  -220 mA  status=4, DISCHARGING while "topping up"
#     pixel9  -405 mA  status=4, DISCHARGING while "topping up"
#
# Two phones drain during a top-up that reports success, then hit the safety
# floor mid-sweep. On the night of 2026-08-05 this emptied all three phones to
# 0-3 % and cost the whole run.
#
# So: prove it from the phones. A phone is charging only if the KERNEL says
# status=2 AND current is flowing INTO the pack. Both, on all three, at once.
#
# SIGN CONVENTION: the trap here. pixel3's fuel gauge reports charge current
# NEGATIVE; pixel6 and pixel9 report it POSITIVE. Measured directly 2026-08-06
# while all three charged from the external supply:
#     pixel3  -1259375 µA      pixel6  +658125 µA      pixel9  +700000 µA
# A naive `current > 0` test would therefore mark pixel3 as failing on a rig
# that is working perfectly. This is the same inversion `P3_SIGN = -1` corrects
# in the sweep runner's contamination classifier. We compare MAGNITUDE and lean
# on `status` for direction, which is sign-convention-independent.
#
# Usage:
#   verify_parallel_charging.sh [--min-ma 300] [--settle 45] [--keep-on]
# Exit 0 = proven; all three charge simultaneously. Non-zero = do NOT set
# PARALLEL=1. Ports are left CUT unless --keep-on.
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"
# shellcheck source=/dev/null
source "$ROOT/tools/ykush_power.sh"

MIN_MA=300          # below this the phone is trickling, not charging
SETTLE=45           # deeply-discharged packs need time to leave pre-charge
KEEP_ON=0

while [ $# -gt 0 ]; do
    case "$1" in
        --min-ma) MIN_MA="$2"; shift 2 ;;
        --settle) SETTLE="$2"; shift 2 ;;
        --keep-on) KEEP_ON=1; shift ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Replicators put their own phones' Wi-Fi ADB addresses and USB serials here.
declare -A WIFI_ADDR SERIAL_OF
WIFI_ADDR[pixel3]="<pixel3-ip>:5555"
WIFI_ADDR[pixel6]="<pixel6-ip>:5555"
WIFI_ADDR[pixel9]="<pixel9-ip>:5555"
# USB serials. Needed because this check runs in exactly the situation where
# WiFi-ADB is most likely to be gone: a phone that drained flat and rebooted
# loses it (persist.adb.tcp.port is SELinux-denied on these non-rooted Pixels),
# and the ports are powered here anyway, so USB is available.
SERIAL_OF[pixel3]="<pixel3-serial>"
SERIAL_OF[pixel6]="<pixel6-serial>"
SERIAL_OF[pixel9]="<pixel9-serial>"

# Prefer WiFi-ADB; fall back to the USB serial. Echoes the usable adb target, or
# nothing if neither answers.
resolve_target() {
    local phone="$1" t
    for t in "${WIFI_ADDR[$phone]}" "${SERIAL_OF[$phone]}"; do
        [ -n "$t" ] || continue
        if [ "$(timeout 12 adb -s "$t" shell echo ok 2>/dev/null | tr -d '\r')" = "ok" ]; then
            printf '%s' "$t"; return 0
        fi
    done
    return 1
}

fail=0
echo "=== verify_parallel_charging: can all three charge AT ONCE? ==="

if ! ykush_board_present; then
    echo "  FAIL  YKUSH board not reachable"
    exit 1
fi

echo "  powering all three ports..."
for p in 1 2 3; do ykush_port_on_raw "$p" >/dev/null 2>&1 || true; done
echo "  settling ${SETTLE}s (a pack under ~10 % sits in pre-charge and draws little)"
sleep "$SETTLE"

total_ma=0
for phone in pixel3 pixel6 pixel9; do
    addr=$(resolve_target "$phone") || {
        echo "  FAIL  $phone — not reachable over WiFi-ADB or USB. Cannot prove charging."
        fail=$((fail + 1)); continue
    }
    out=$(timeout 25 adb -s "$addr" shell dumpsys battery 2>/dev/null | tr -d '\r')
    lvl=$(printf '%s\n' "$out" | grep -E '^  level:'  | awk '{print $2}')
    st=$(printf  '%s\n' "$out" | grep -E '^  status:' | awk '{print $2}')
    # Three samples, keep the largest magnitude. A single instantaneous reading is
    # far too noisy to gate on: pixel9 was measured at 700 mA and then 38 mA
    # minutes apart while genuinely charging, because a pack in pre-charge pulses.
    # Taking the max does NOT weaken the check: `status=2` is still required, and
    # a phone that is not charging never shows a high sample.
    cur=""; best=0
    for _s in 1 2 3; do
        c=$(timeout 20 adb -s "$addr" shell \
              'cat /sys/class/power_supply/maxfg/current_now 2>/dev/null || cat /sys/class/power_supply/battery/current_now' \
              2>/dev/null | tr -d '\r')
        [ -z "$c" ] && continue
        cur="$c"
        m=$(( c / 1000 )); m=${m#-}
        [ "$m" -gt "$best" ] 2>/dev/null && best="$m"
        [ "$_s" -lt 3 ] && sleep 3
    done

    if [ -z "$st" ] || [ -z "$cur" ]; then
        echo "  FAIL  $phone — unreadable (adb said nothing). Cannot prove charging."
        fail=$((fail + 1)); continue
    fi

    ma="$best"                                # magnitude; sign differs per phone
    total_ma=$(( total_ma + ma ))

    if [ "$st" != "2" ]; then
        echo "  FAIL  $phone level=${lvl}% status=$st (not Charging), ${ma} mA — the supply cannot feed all three"
        fail=$((fail + 1))
    elif [ "$ma" -lt "$MIN_MA" ]; then
        echo "  FAIL  $phone level=${lvl}% status=2 but only ${ma} mA (< ${MIN_MA}) — trickling, not charging"
        fail=$((fail + 1))
    else
        echo "  OK    $phone level=${lvl}% status=2, ${ma} mA"
    fi
done

echo "  total draw: ~${total_ma} mA"

if [ "$KEEP_ON" -ne 1 ]; then
    ykush_cut_all >/dev/null 2>&1 || true
    echo "  ports cut"
else
    echo "  ports left ON (--keep-on)"
fi

echo ""
if [ "$fail" -eq 0 ]; then
    echo "  PROVEN — all three charge simultaneously. TOPUP_PARALLEL=1 / PARALLEL=1 are safe."
    exit 0
fi
echo "  NOT PROVEN ($fail phone(s) failed) — leave TOPUP_PARALLEL=0."
echo "  On bus power this is expected: the board leaves USB spec around 2.6 A and"
echo "  three Pixels want ~4.5 A, so only one phone charges and the others drain."
exit 1
