#!/usr/bin/env bash
# ykush_ar_power.sh: the command Android Runner calls to switch a phone's USB power.
#
# Written 2026-08-03. It takes the place of the `sudo -n /usr/sbin/uhubctl ...`
# commands in the AR *_wifi_hubctl.json configs: tools/ykush_sync_ar_configs.py
# writes separate *_wifi_ykush.json configs that point here and leaves the uhubctl
# configs untouched.
#
# Usage:  ykush_ar_power.sh <pixel3|pixel6|pixel9> <on|off>
#
# ─────────────────────────────────────────────────────────────────────────────
# THREE HARD CONSTRAINTS IMPOSED BY AndroidRunner/USBHandler.py (read before editing)
# ─────────────────────────────────────────────────────────────────────────────
# 1. USB_COMMAND_TIMEOUT_SECONDS = 5. This must finish in well under 5 seconds.
#    A switch plus readback is ~60 ms, so there is room, but it is why there is
#    NO WiFi-ADB verification loop in here. Verifying that VBUS actually died
#    takes seconds and belongs in the sweep runner, around the AR invocation.
#
# 2. AR raises USBHandlerException if the command writes ANYTHING to stderr,
#    regardless of exit code. So: zero stderr on success. All diagnostics go to
#    the logfile. On genuine failure we write one line to stderr AND exit
#    nonzero, so the cell fails loudly instead of silently measuring a charging
#    phone.
#
# 3. Never `sudo`. Sudoers here grants NOPASSWD to /usr/sbin/uhubctl only, so
#    `sudo ykushcmd` would block on a password prompt and hang an unattended
#    33-hour sweep. Access comes from the udev rule at
#    /etc/udev/rules.d/99-ykush.rules (our own, copied from scratchpad/99-ykush.rules,
#    because Yepkit ships none).
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY NOT uhubctl (the whole reason this file exists)
# ─────────────────────────────────────────────────────────────────────────────
# uhubctl drives the YKUSH3's internal Microchip USB2744 hub, which advertises
# ppps and therefore accepts the command. The phone even drops off USB. But VBUS
# STAYS LIVE: measured 2026-08-03, a Pixel 3 read BatteryManager status=2
# (charging) at ~500 mA thirty seconds after uhubctl reported "Port 1: 0000 off".
# Every AR cell would have measured a charging phone while the logs looked clean.
#
# Compounding it: the board's port numbers are REVERSED relative to the Linux
# sysfs indices (calibrated by observation 2026-08-03: board port 1 = pixel3,
# but pixel3 enumerates at sysfs 3-6.3). So syncing the uhubctl configs to the
# live hub would have written not just an ineffective command but the WRONG PORT.
# Phone -> port comes from the calibrated map only; nothing is inferred.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${YKUSH_AR_LOG:-$HERE/sweep_logs/ykush_ar_power.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

phone="${1:-}"
action="${2:-}"

_log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true; }
_fail() { _log "FAIL: $*"; printf 'ykush_ar_power: %s\n' "$*" >&2; exit 1; }

case "$phone" in pixel3|pixel6|pixel9) ;; *) _fail "bad phone '$phone'" ;; esac
case "$action" in on|off) ;; *) _fail "bad action '$action' (want on|off)" ;; esac

# Source the library with stdout/stderr suppressed: it must not leak a byte.
# shellcheck source=/dev/null
source "$HERE/ykush_power.sh" >/dev/null 2>&1 || _fail "cannot source ykush_power.sh"

port="$(ykush_resolve_port "$phone" 2>/dev/null)" \
    || _fail "$phone is not in the calibrated port map (run tools/ykush_calibrate.py)"

if [ "$action" = off ]; then
    ykush_port_off_raw "$port" >/dev/null 2>&1 \
        || _fail "failed to power OFF $phone (board port $port)"
    _log "$phone (board port $port) OFF"
else
    ykush_port_on_raw "$port" >/dev/null 2>&1 \
        || _fail "failed to power ON $phone (board port $port)"
    _log "$phone (board port $port) ON"
fi
exit 0
