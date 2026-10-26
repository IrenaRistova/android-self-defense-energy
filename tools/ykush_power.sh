#!/usr/bin/env bash
# ykush_power.sh: per-port USB power control for the Yepkit YKUSH3 board.
#
# Written 2026-08-03. Source it, or run it as a CLI.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THIS EXISTS: the defect it fixes
# ─────────────────────────────────────────────────────────────────────────────
# The YKUSH3 enumerates an internal Microchip USB2744 hub at 3-6, and that hub
# advertises `ppps`, so uhubctl happily drives it. It appears to work: the phone
# drops off USB and uhubctl prints "Port 1: 0000 off".
#
# It does not cut power. Measured 2026-08-03 on Pixel 3: BatteryManager status=2
# (charging) drawing ~500 mA a full THIRTY SECONDS after uhubctl reported the
# port off. uhubctl sends the USB hub-class PORT_POWER feature request to the
# hub controller; the YKUSH's actual VBUS switch is a MOSFET driven by the
# board's own MCU, reachable only over the board's HID control interface.
# Different chip, different command path.
#
# Had the sweep run that way, every cell would have measured a CHARGING phone
# while the logs showed a clean cut. The whole ΔE result would have been void.
#
# The datasheet confirms the hardware does what we need (§3, verbatim):
#   "The hub supports full downstream port switching (enable/disable). By full
#    switching we mean that all components of the downstream ports can be
#    switched by the user, namely the VBUS lines (power), SuperSpeed data lines
#    and USB 2.0 data lines."
#
# ─────────────────────────────────────────────────────────────────────────────
# TRAPS IN ykushcmd 1.2.5 THAT THIS FILE DEFUSES
# ─────────────────────────────────────────────────────────────────────────────
# Every one of these was verified against the installed binary and its source
# (Debian ykush-control 1.3.0+ds-1, reports "YKUSHCMD Version 1.2.5"):
#
#  1. Omitting the board name does NOT default to ykush3: it addresses a
#     DIFFERENT board class and reports "No YKUSH boards found." while the
#     YKUSH3 sits there working. Always pass the literal `ykush3`.
#  2. `-s <serial>` placed AFTER the action flag is silently ignored. It must
#     come first, or a command aimed at one board can hit another.
#  3. `-d 9` and `-d z` EXIT 0 having sent no HID report at all. Trusting the
#     exit code means believing you cut power when nothing was transmitted.
#     `-d` with an empty argument segfaults (rc 139). Hence ykush_port_valid.
#  4. `-d`/`-u` print NOTHING on success: the exit code is the only signal, so
#     every switch here is followed by a `-g` readback.
#  5. `-l` returns 0 unconditionally, even when it prints "No YKUSH3 boards
#     found." Board presence must be decided by parsing stdout, not by rc.
#  6. `-g`'s reply byte carries the port number the DEVICE reports, not the one
#     you asked for, and the send is unchecked: a failed transfer decodes
#     uninitialised stack memory. So we assert the echoed port matches.
#
# ─────────────────────────────────────────────────────────────────────────────
# PERMISSIONS
# ─────────────────────────────────────────────────────────────────────────────
# ykushcmd reaches the board through libusb, i.e. /dev/bus/usb/<bus>/<dev>,
# NOT /dev/hidraw*. Yepkit ships no udev rule and their guide just says to run
# everything as root; /etc/udev/rules.d/99-ykush.rules is ours (a copy is
# scratchpad/99-ykush.rules). NEVER call this
# under sudo: the sudoers entry here grants NOPASSWD to /usr/sbin/uhubctl only,
# so `sudo ykushcmd` would block on a password prompt and hang an unattended
# 33-hour sweep at 3 a.m.
set -uo pipefail

YKUSH_BIN="${YKUSH_BIN:-/usr/bin/ykushcmd}"
# Replicators set YKUSH_SERIAL to their own board's serial.
YKUSH_SERIAL="${YKUSH_SERIAL:-<ykush-serial>}"
YKUSH_LOCK="${YKUSH_LOCK:-/tmp/ykush_${YKUSH_SERIAL}.lock}"
YKUSH_CALL_TIMEOUT="${YKUSH_CALL_TIMEOUT:-10}"
# The port map is created by tools/ykush_calibrate.py on a new set-up (it is not
# part of this package).
YKUSH_PORTMAP="${YKUSH_PORTMAP:-$(dirname "${BASH_SOURCE[0]}")/../specs/todo-roadmap/master-experiment-scale/ykush_portmap.json}"

_yk_err() { printf '[ykush_power] %s\n' "$*" >&2; }

# Single chokepoint for every board call.
#   flock:    ykushcmd claims the board's HID interface and detaches the kernel
#             driver without reattaching it. Three tmux panes plus a watchdog
#             plus exit traps WILL collide. Serialise.
#   timeout:  a wedged board must fail one cell, not stall the whole sweep.
#   -s first: see trap 2 above.
_yk() {
    flock -w 15 9 || { _yk_err "board lock timeout after 15s"; return 70; }
    timeout -k 2 "$YKUSH_CALL_TIMEOUT" "$YKUSH_BIN" ykush3 -s "$YKUSH_SERIAL" "$@"
} 9>"$YKUSH_LOCK"

# ── port sanity ──────────────────────────────────────────────────────────────
# Guards trap 3. Without this, a malformed port silently "succeeds".
ykush_port_valid() { [[ "${1:-}" =~ ^[123]$ ]]; }

# ── board presence ───────────────────────────────────────────────────────────
# Parses stdout because -l always exits 0 (trap 5).
ykush_board_present() {
    local out
    out=$(_yk -l 2>/dev/null) || return 6
    grep -qF "Board found with serial number: $YKUSH_SERIAL" <<<"$out" || {
        _yk_err "board $YKUSH_SERIAL not found; ykushcmd said: $(tr '\n' ' ' <<<"$out")"
        return 6
    }
}

# ── raw port state ───────────────────────────────────────────────────────────
# Prints ON|OFF. Asserts the echoed port number matches what we asked (trap 6),
# so a garbled reply becomes a loud UNKNOWN rather than a confident lie.
ykush_port_state_raw() {
    local port="$1" out
    ykush_port_valid "$port" || { _yk_err "invalid port '$port'"; return 4; }
    out=$(_yk -g "$port" 2>/dev/null) || return 5
    case "$out" in
        "Port $port ON")  echo ON  ;;
        "Port $port OFF") echo OFF ;;
        *) _yk_err "unparseable -g reply for port $port: '$out'"; echo UNKNOWN; return 5 ;;
    esac
}

# ── switching by raw port ────────────────────────────────────────────────────
# Always reads back: -d/-u are silent on success (trap 4).
ykush_port_off_raw() {
    local port="$1" st
    ykush_port_valid "$port" || { _yk_err "refusing to switch invalid port '$port'"; return 4; }
    _yk -d "$port" >/dev/null 2>&1 || { _yk_err "ykushcmd -d $port failed"; return 5; }
    st=$(ykush_port_state_raw "$port") || return 5
    [ "$st" = OFF ] || { _yk_err "port $port readback says $st after -d"; return 5; }
}

ykush_port_on_raw() {
    local port="$1" st
    ykush_port_valid "$port" || { _yk_err "refusing to switch invalid port '$port'"; return 4; }
    _yk -u "$port" >/dev/null 2>&1 || { _yk_err "ykushcmd -u $port failed"; return 5; }
    st=$(ykush_port_state_raw "$port") || return 5
    [ "$st" = ON ] || { _yk_err "port $port readback says $st after -u"; return 5; }
}

# ── cut everything ───────────────────────────────────────────────────────────
# Uses the board's single atomic all-ports report (0x0a) rather than looping, and
# deliberately does NOT resolve phones first: this is the path taken when
# downstream enumeration has already collapsed, and the board's own HID
# interface stays reachable in exactly that condition.
ykush_cut_all() {
    local p st rc=0
    _yk -d a >/dev/null 2>&1 || { _yk_err "ykushcmd -d a failed"; rc=5; }
    for p in 1 2 3; do
        st=$(ykush_port_state_raw "$p") || { rc=5; continue; }
        [ "$st" = OFF ] || { _yk_err "port $p still $st after -d a"; rc=5; }
    done
    return $rc
}

# Trap-safe variant for EXIT/INT/TERM. No python, no adb, no network, no
# subshell that could itself fail; must never mask the original exit code.
ykush_emergency_cut_all() {
    timeout -k 2 "$YKUSH_CALL_TIMEOUT" "$YKUSH_BIN" ykush3 -s "$YKUSH_SERIAL" -d a >/dev/null 2>&1 || true
    return 0
}

# ── power-on default ─────────────────────────────────────────────────────────
# The board re-derives live port state from a stored per-port default at every
# power-on and reset (datasheet §6.6). Without setting this, a board reset, a
# replug, or a host reboot lands all three ports ON. Overnight that means a
# phone charged to 100%, the exact Li-ion damage the rig exists to avoid.
# The datasheet does NOT state the factory default, so this is set explicitly.
ykush_set_safe_defaults() {
    local p rc=0
    for p in 1 2 3; do
        _yk -c "$p" 0 >/dev/null 2>&1 || { _yk_err "failed to set power-on default OFF for port $p"; rc=5; }
    done
    return $rc
}

# ── phone -> port ────────────────────────────────────────────────────────────
# Reads the calibrated map. A map is REQUIRED rather than optional because:
#   (a) a cut port removes VBUS *and* both data pairs, so the phone vanishes
#       from sysfs entirely: you cannot resolve a phone whose port is already
#       off, which is precisely when you most need to know its port; and
#   (b) the datasheet documents NO mapping between the board's silkscreen
#       "Downstream 1/2/3" and Linux sysfs port indices, so assuming
#       sysfs port N == ykushcmd port N risks cutting the WRONG phone.
# Build it with ykush_calibrate.py, which determines the map by observation.
ykush_resolve_port() {
    local phone="$1"
    [ -f "$YKUSH_PORTMAP" ] || { _yk_err "no port map at $YKUSH_PORTMAP — run tools/ykush_calibrate.py"; return 3; }
    python3 - "$YKUSH_PORTMAP" "$phone" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
if m.get("board_serial") != __import__("os").environ.get("YKUSH_SERIAL", "<ykush-serial>"):
    sys.stderr.write("[ykush_power] port map was calibrated for board %r, not the live board\n"
                     % m.get("board_serial"))
    sys.exit(4)
for port, phone in m.get("ports", {}).items():
    if phone == sys.argv[2]:
        print(port); sys.exit(0)
sys.stderr.write("[ykush_power] %r is not in the port map\n" % sys.argv[2])
sys.exit(3)
PY
}

ykush_port_off() { local p; p=$(ykush_resolve_port "$1") || return $?; ykush_port_off_raw "$p"; }
ykush_port_on()  { local p; p=$(ykush_resolve_port "$1") || return $?; ykush_port_on_raw  "$p"; }
ykush_port_state() { local p; p=$(ykush_resolve_port "$1") || return $?; ykush_port_state_raw "$p"; }

# ── THE IMPORTANT ONE: prove VBUS is actually gone ───────────────────────────
# ykush_port_off_raw only proves the SWITCH says off. This proves the PHONE
# agrees, which is the check that would have caught the uhubctl defect the
# moment it appeared instead of after a full sweep.
#
# Reads raw sysfs, NOT `dumpsys battery`. Android Runner calls device.unplug()
# at every experiment start, and AndroidRunner/USBHandler.py states in its own
# docstring that this "only mocks the battery status ... but it does NOT
# actually stop charging the device through USB." The mock sits ABOVE sysfs:
# after `dumpsys battery set status 2`, dumpsys reports charging while
# /sys/class/power_supply/usb/online reads 0. A dumpsys-based check would
# therefore return PASS unconditionally for the entire duration of every AR
# cell, reproducing the exact silent failure being fixed here.
#
# Usage: ykush_verify_cut <wifi_adb_addr> [timeout_s]
ykush_verify_cut() {
    local addr="$1" budget="${2:-20}" deadline online status
    deadline=$(( $(date +%s) + budget ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        online=$(timeout 8 adb -s "$addr" shell 'cat /sys/class/power_supply/usb/online' 2>/dev/null | tr -d '\r\n')
        status=$(timeout 8 adb -s "$addr" shell 'cat /sys/class/power_supply/battery/status' 2>/dev/null | tr -d '\r\n')
        if [ "$online" = "0" ] && [ "$status" != "Charging" ] && [ "$status" != "Full" ]; then
            return 0
        fi
        sleep 2
    done
    _yk_err "VBUS still live on $addr after ${budget}s (usb/online=$online battery/status=$status)"
    return 8
}

# Cut a phone's port AND prove the phone stopped charging.
# Usage: ykush_port_off_verified <phone> <wifi_adb_addr> [timeout_s]
ykush_port_off_verified() {
    ykush_port_off "$1" || return $?
    ykush_verify_cut "$2" "${3:-20}"
}

# ── CLI ──────────────────────────────────────────────────────────────────────
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    cmd="${1:-}"; shift || true
    case "$cmd" in
        present)       ykush_board_present && echo "board $YKUSH_SERIAL present" ;;
        state)         ykush_port_state_raw "$1" ;;
        off)           ykush_port_off_raw "$1" ;;
        on)            ykush_port_on_raw "$1" ;;
        cut-all)       ykush_cut_all ;;
        safe-defaults) ykush_set_safe_defaults && echo "power-on default set to OFF on ports 1-3" ;;
        verify-cut)    ykush_verify_cut "$1" "${2:-20}" && echo "VBUS confirmed off on $1" ;;
        status)
            ykush_board_present || exit 6
            for p in 1 2 3; do printf '  port %s: %s\n' "$p" "$(ykush_port_state_raw "$p")"; done ;;
        *)
            cat >&2 <<'USAGE'
usage: ykush_power.sh <command>
  present                    board reachable? (parses stdout; -l always exits 0)
  status                     print all three port states
  state <1|2|3>              print ON|OFF for one port
  off <1|2|3>                power port down, with readback
  on  <1|2|3>                power port up, with readback
  cut-all                    atomic all-ports off, verified
  safe-defaults              set power-on default OFF (survives reset/replug)
  verify-cut <adb_addr> [s]  prove VBUS is gone, via raw sysfs not dumpsys
USAGE
            exit 64 ;;
    esac
fi
