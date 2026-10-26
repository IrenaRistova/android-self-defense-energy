#!/usr/bin/env bash
# preflight_final_sweep_ykush.sh: pre-launch gates for the final sweep, on YKUSH3.
#
# Written 2026-08-04 for the YKUSH3 board. It takes the place of the preflight
# used with the earlier Plugable hub ("the old preflight" below, whose checks are
# cited by number).
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THE OLD PREFLIGHT COULD NOT BE REUSED
# ─────────────────────────────────────────────────────────────────────────────
# Its single most important check (12.3, "does the power cut actually work?")
# was structurally incapable of failing on this hardware. It cut the port with
# uhubctl and then read the phone's usb voltage_now. On the YKUSH3, uhubctl
# drives the board's internal Microchip USB2744 hub (which advertises ppps and
# accepts the command, so the phone drops off USB) while the real VBUS switch is
# an MCU-driven MOSFET reached only through ykushcmd. The port reads "off", the
# phone keeps charging at ~500 mA, and the check reports a clean PPPS cut.
#
# Two further checks were wrong rather than merely useless:
#   * 12.7 asserted a NOPASSWD sudoers entry for uhubctl. Not only is uhubctl not
#     the tool here: ykushcmd must NEVER run under sudo, because the sudoers
#     entry covers /usr/sbin/uhubctl only, so `sudo ykushcmd` would block on a
#     password prompt and hang an unattended 30-hour sweep at 3 a.m.
#   * 12.14 required every phone to enumerate on USB. A phone whose port is
#     correctly cut is INVISIBLE on the YKUSH (VBUS and both data pairs are
#     switched together), so the safe resting state would have failed the gate.
#     Here the ports are powered up for that check and cut again afterwards.
#
# New checks, each closing a hole that produced bad data at least once:
#   [Y.3]  the cut is proven from the PHONE's raw sysfs, not from the switch
#   [Y.5]  every cohort config resolves AND its APK matches the variant it claims
#   [Y.7]  no cohort app is left installed on any phone
#   [Y.16] allatori builds are proven obfuscated by class-set diff
#
# Exit code is the number of FAIL checks (0 = ready to launch).
# Leaves every port CUT, whatever happens.
#
# Usage:
#   tools/preflight_final_sweep_ykush.sh [--phones pixel3,pixel6,pixel9]
#                                        [--require-soc-min 40 --require-soc-max 70]
#                                        [--apps a,b,c] [--variants v1,v2]
#                                        [--min-disk-gb 20] [--skip-slow]
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"
# shellcheck source=/dev/null
source "$ROOT/tools/ykush_power.sh"

PHONES="pixel3,pixel6,pixel9"
APPS="calculator,tipuous,poetskingdom,linkhub,medtimer,androidskk,itsok,keep_recipe,documenter,pdfviewer"
VARIANTS="baseline_debug,bangcle_debug,allatori_debug,freerasp_debug"
CFG_SUFFIX="${SWEEP_CFG_SUFFIX:-_wifi_ykush}"
REQUIRE_SOC_MIN=40
REQUIRE_SOC_MAX=70
SKIP_SLOW=0
# Measured 2026-08-01 against 1438 real cells: AR output averages 2.3 MB/cell and
# per-cell logcat 2.6 MB/cell, so a cell costs ~5 MB and a full 1200-cell sweep is
# ~6 GB. 20 GB is ~3x that, with room for reruns and mitm dumps. The original
# 100 GB was a guess an order of magnitude too high, and a check that cries wolf
# is a check people start passing --skip-preflight around.
MIN_DISK_GB=20

while [ $# -gt 0 ]; do
    case "$1" in
        --phones)          PHONES="$2"; shift 2 ;;
        --apps)            APPS="$2"; shift 2 ;;
        --variants)        VARIANTS="$2"; shift 2 ;;
        --require-soc-min) REQUIRE_SOC_MIN="$2"; shift 2 ;;
        --require-soc-max) REQUIRE_SOC_MAX="$2"; shift 2 ;;
        --min-disk-gb)     MIN_DISK_GB="$2"; shift 2 ;;
        --skip-slow)       SKIP_SLOW=1; shift ;;
        -h|--help)         sed -n '2,42p' "$0"; exit 0 ;;
        *)                 echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

declare -A WIFI_ADDR SERIAL_OF YKPORT
# Replicators put their own phones' Wi-Fi ADB addresses and USB serials here.
WIFI_ADDR[pixel3]="<pixel3-ip>:5555"
WIFI_ADDR[pixel6]="<pixel6-ip>:5555"
WIFI_ADDR[pixel9]="<pixel9-ip>:5555"
# USB serials, used by [Y.14] to prove the board's DATA path is alive,
# independently of whether WiFi-ADB answers.
SERIAL_OF[pixel3]="<pixel3-serial>"
SERIAL_OF[pixel6]="<pixel6-serial>"
SERIAL_OF[pixel9]="<pixel9-serial>"

IFS=',' read -ra phone_arr <<< "$PHONES"

FAIL_COUNT=0
UNPROVEN_COUNT=0
ok()   { printf '  OK     %s\n' "$1"; }
fail() { printf '  FAIL   %s\n' "$1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
info() { printf '  INFO   %s\n' "$1"; }
# UNPROVEN is not INFO. INFO means "did not apply"; UNPROVEN means "this gate was
# asked and could not answer". It never prints OK, and it blocks the launch-ready
# verdict without inflating FAIL_COUNT (the exit code stays a count of real
# failures). Added 2026-08-04: --skip-slow used to suppress [Y.14], which in turn
# let [Y.3] print a green "clean cut" for phones that were not attached at all,
# a gate that cannot fail.
unproven() { printf '  UNPROVEN %s\n' "$1"; UNPROVEN_COUNT=$((UNPROVEN_COUNT+1)); }

# ---------- Y.0: YKUSH board present + calibrated map ----------
# First, because every power check below depends on it, and because a board that
# has silently fallen off the bus is exactly the failure that cost 2026-08-01.
echo "[Y.0] YKUSH board + calibrated port map"
BOARD_OK=0
if ykush_board_present 2>/dev/null; then
    ok "board $YKUSH_SERIAL reachable via ykushcmd (no sudo)"
    BOARD_OK=1
else
    fail "board $YKUSH_SERIAL NOT reachable — check 'lsusb | grep 04d8:f11b' and the udev rule /etc/udev/rules.d/99-ykush.rules"
fi
if [ "$BOARD_OK" -eq 1 ]; then
    for phone in "${phone_arr[@]}"; do
        p=$(ykush_resolve_port "$phone" 2>/dev/null) || p=""
        if [ -n "$p" ]; then YKPORT[$phone]="$p"; ok "$phone -> board port $p"
        else fail "$phone not in the calibrated map — run tools/ykush_calibrate.py"; fi
    done
fi

# ---------- Y.1: phones reachable via WiFi-ADB ----------
# WiFi-ADB is the ONLY channel to a measuring phone: its USB port is cut for the
# whole cell, so USB-ADB is gone by construction.
# A stale socket is bounced before it is called a failure: adb keeps reporting
# `device` while every shell returns nothing, and this check gates an entire
# unattended run. Three attempts, reconnecting between them: a phone that is
# really gone still fails all three.
echo "[Y.1] WiFi-ADB reachability"
for phone in "${phone_arr[@]}"; do
    out=""
    for attempt in 1 2 3; do
        out=$(timeout 8 adb -s "${WIFI_ADDR[$phone]}" shell echo OK 2>&1 | tr -d '\r' | head -1)
        [ "$out" = "OK" ] && break
        if [ "$attempt" -lt 3 ]; then
            timeout 10 adb disconnect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
            sleep 1
            timeout 15 adb connect "${WIFI_ADDR[$phone]}" >/dev/null 2>&1 || true
            sleep 2
        fi
    done
    if [ "$out" = "OK" ]; then ok "$phone (${WIFI_ADDR[$phone]}) reachable"
    else fail "$phone (${WIFI_ADDR[$phone]}) NOT reachable after 3 attempts: $out"; fi
done

# ---------- Y.14: phones enumerate on USB with ports POWERED ----------
# Powers every port up, waits for enumeration, checks each phone's USB serial
# appears in sysfs, then cuts again in [Y.3]. This proves the data path and the
# cable are alive. It is done with power ON deliberately: on the YKUSH, VBUS and
# both data pairs switch together, so a correctly-cut phone is invisible here and
# an always-on-hub version of this check would fail the safe resting state.
# The ports are up for ~15 s, which adds well under 0.1 % SoC.
echo "[Y.14] Phones enumerate on USB (data path + cable alive)"
if [ "$BOARD_OK" -eq 1 ] && [ "$SKIP_SLOW" -ne 1 ]; then
    for phone in "${phone_arr[@]}"; do
        [ -n "${YKPORT[$phone]:-}" ] && ykush_port_on_raw "${YKPORT[$phone]}" >/dev/null 2>&1
    done
    sleep 12
    _usb_missing=""
    for phone in "${phone_arr[@]}"; do
        _ser="${SERIAL_OF[$phone]:-}"
        [ -z "$_ser" ] && continue
        grep -qs "^${_ser}$" /sys/bus/usb/devices/*/serial 2>/dev/null || _usb_missing="$_usb_missing $phone"
    done
    ENUMERATED="$(for phone in "${phone_arr[@]}"; do
        _s="${SERIAL_OF[$phone]:-}"
        [ -n "$_s" ] && grep -qs "^${_s}$" /sys/bus/usb/devices/*/serial 2>/dev/null && printf '%s ' "$phone"
    done)"
    if [ -n "$_usb_missing" ]; then
        fail "not enumerated on USB with the port POWERED:${_usb_missing} — not connected to the YKUSH, bad cable, or in a different socket than the map says (run tools/ykush_calibrate.py --verify)"
    else
        ok "all requested phones enumerate when powered"
    fi
else
    ENUMERATED=""
    info "skipped (--skip-slow, or board unreachable)"
fi

# ---------- Y.3: THE CUT IS PROVEN FROM THE PHONE ----------
# The check the whole rig turns on. Cut every port together, let the charge ICs
# settle, then ask each PHONE two independent questions over WiFi-ADB:
#   /sys/class/power_supply/usb/online       -> 1 means it still sees a charger
#   /sys/class/power_supply/usb/voltage_now  -> µV on the USB input
#
# NOT `dumpsys battery`. Android Runner calls device.unplug() at every experiment
# start and AndroidRunner/USBHandler.py says in its own docstring that this "only
# mocks the battery status ... but it does NOT actually stop charging the device
# through USB". The mock sits ABOVE sysfs, so a dumpsys-based check passes
# unconditionally for the entire duration of every AR cell.
#
# All ports are cut TOGETHER and sampled with no intervening board traffic. On
# the Plugable, a restore/cut cycle on one port re-powered a held-cut port within
# ~3 s (6/6 reproducible), so the old per-phone cut/sample/restore probe
# manufactured the very leak it was testing for. It is also simply the true
# operating condition: in a parallel sweep all three phones hold their port cut
# for the whole workload.
echo "[Y.3] Cut effectiveness — proven from the phone's raw sysfs"
if [ "$BOARD_OK" -eq 1 ]; then
    if ykush_cut_all 2>/dev/null; then
        sleep 6
        for phone in "${phone_arr[@]}"; do
            waddr="${WIFI_ADDR[$phone]}"
            online=$(timeout 8 adb -s "$waddr" shell 'cat /sys/class/power_supply/usb/online' 2>/dev/null | tr -d '\r ' | head -1)
            uv=$(timeout 8 adb -s "$waddr" shell 'cat /sys/class/power_supply/usb/voltage_now' 2>/dev/null | tr -d '\r ' | head -1)
            # 2026-08-04: a phone that is NOT PLUGGED IN reads usb/online=0
            # trivially, so this check used to report OK for a rig with nothing
            # attached: the same "gate that cannot fail" defect as the old
            # [12.3]. Require that [Y.14] saw the phone enumerate first.
            if [ "$online" = "1" ]; then
                # Attachment is not in question here: a phone that reports a live
                # charger is plainly attached, so this failure stands on its own
                # regardless of whether [Y.14] ran.
                fail "$phone usb/online=1 with ALL ports cut — the phone still sees a charger. Every cell would measure a charging phone."
            elif ! printf '%s' "$ENUMERATED" | grep -qw "$phone"; then
                # online=0 only evidences a good cut if the phone is ATTACHED.
                # An unplugged phone reads 0 identically, and [Y.14] is the only
                # thing that establishes attachment, so without it this gate
                # cannot answer, and must not answer OK.
                if [ "${SKIP_SLOW}" -eq 1 ]; then
                    unproven "$phone online=0, but [Y.14] was skipped (--skip-slow) so attachment is unknown — an unplugged phone reads 0 identically. Re-run WITHOUT --skip-slow before launching."
                else
                    fail "$phone did not enumerate on USB in [Y.14], so a cut cannot be verified — usb/online=0 here only means 'not connected'. Attach it to the YKUSH and re-run."
                fi
            elif ! [[ "$uv" =~ ^[0-9]+$ ]]; then
                ok "$phone online=0 (voltage_now unreadable: '${uv:-empty}')"
            elif [ "$uv" -gt 1000000 ] 2>/dev/null; then
                fail "$phone residual VBUS ${uv} µV (> 1 V) with ALL ports cut"
            else
                ok "$phone online=0, voltage_now=${uv} µV — clean cut"
            fi
        done
    else
        fail "ykush_cut_all failed — the board is not switching"
    fi
else
    info "skipped (board unreachable)"
fi

# ---------- Y.4: mitmproxy CA (operator visual step) ----------
echo "[Y.4] Mitmproxy CA on every phone"
info "Must be visually confirmed via Settings -> Trusted credentials -> User on each phone."
info "adb shell ls /data/misc/user/0/cacerts-added/ returns empty because of UID"
info "permissions, NOT because the cert is absent. The visual check is the truth."

# ---------- Y.5: every cohort config resolves, and points at the RIGHT APK ----------
# Two distinct failures, both real:
#   (a) a config missing entirely -> sweep_runner silently SKIPs the cell, and a
#       skipped cell looks identical to a cell that was never scheduled;
#   (b) a config whose paths[0] points at ANOTHER variant's APK. Six configs did
#       exactly this on 2026-07-31 (calculator + linkhub freerasp -> baseline).
#       The sweep runs, every cell PASSes, and the freerasp arm is silently a
#       duplicate of baseline.
echo "[Y.5] Cohort configs resolve + APK matches the claimed variant"
python3 - "$APPS" "$VARIANTS" "$PHONES" "$CFG_SUFFIX" <<'PYEOF'
import json, os, shlex, sys
from pathlib import Path

apps     = sys.argv[1].split(",")
variants = sys.argv[2].split(",")
phones   = sys.argv[3].split(",")
suffix   = sys.argv[4]
cfg_dir = Path("/home/irena/Documents/Master Experiment/android-runner/examples/batterymanager")

missing_cfg, missing_apk, wrong_variant, handler_bad, total = [], [], [], [], 0
# path -> set of (app, variant) that reference it. Two different arms sharing one
# APK is the collision that actually destroys a comparison.
owners = {}

for app in apps:
    for variant in variants:
        for phone in phones:
            cands = [cfg_dir / f"{app}_{variant}_{phone}{suffix}.json",
                     cfg_dir / f"{app}_stub_{variant}_{phone}{suffix}.json"]
            cfg = next((c for c in cands if c.exists()), None)
            if not cfg:
                missing_cfg.append(f"{app}/{variant}/{phone}")
                continue
            total += 1
            data = json.loads(cfg.read_text())
            for p in data.get("paths", []):
                if not Path(p).exists():
                    missing_apk.append(f"{cfg.name} -> {p}")
                    continue
                owners.setdefault(p, set()).add((app, variant))
                # The invariant is the BASENAME, not the directory. Baseline APKs
                # and a couple of freerasp ones sit directly in APKs/ rather than
                # in a <variant>_canonical/ subdir, so a directory-based rule would
                # flag 36 perfectly correct configs. The filename is what the build
                # pipeline stamps, and it is what caught the 2026-07-31 defect where
                # calculator/linkhub freerasp configs pointed at *_baseline_debug.apk.
                base = Path(p).name
                if not (base.startswith(f"{app}_{variant}") or
                        base.startswith(f"{app}_stub_{variant}")):
                    wrong_variant.append(f"{cfg.name} -> {base}")

            # The usb_handler commands must survive shlex.split, because that is
            # exactly what AndroidRunner/USBHandler.py does with them:
            #   subprocess.Popen(shlex.split(command), ...)
            # This repo lives at ".../Master Experiment/", WITH A SPACE. Unquoted,
            # argv[0] becomes "/home/irena/Documents/Master" and AR dies with
            # FileNotFoundError the instant it tries to cut USB, which is
            # immediately before the workload. 3/3 smoke cells failed exactly there
            # on 2026-08-04. The uhubctl commands this replaced were
            # "sudo -n /usr/sbin/uhubctl ..." (no spaces), so no config had ever
            # exercised this path. Resolve it here rather than discovering it a
            # cell at a time.
            uh = data.get("usb_handler") or {}
            for key in ("enable_command", "disable_command"):
                cmd = uh.get(key)
                if not cmd:
                    handler_bad.append(f"{cfg.name}: usb_handler.{key} missing")
                    continue
                argv = shlex.split(cmd)
                if not argv or not Path(argv[0]).is_file() or not os.access(argv[0], os.X_OK):
                    handler_bad.append(
                        f"{cfg.name}: usb_handler.{key} argv[0]={argv[0] if argv else '<empty>'!r} "
                        f"is not an executable file (quote paths containing spaces)")
                elif key == "disable_command" and argv[-1] != "off":
                    handler_bad.append(f"{cfg.name}: disable_command does not end in 'off': {argv}")
                elif key == "enable_command" and argv[-1] != "on":
                    handler_bad.append(f"{cfg.name}: enable_command does not end in 'on': {argv}")
                elif len(argv) >= 2 and argv[-2] != phone:
                    # A config that cuts a DIFFERENT phone's port is the worst
                    # possible failure: this phone measures while charging AND the
                    # other phone loses power mid-cell.
                    handler_bad.append(f"{cfg.name}: usb_handler.{key} targets '{argv[-2]}', not '{phone}'")

# One APK claimed by two different arms => the arms are not independent.
collisions = [f"{p} claimed by {sorted(o)}" for p, o in owners.items() if len(o) > 1]

for label, items in (("config MISSING", missing_cfg),
                     ("APK MISSING", missing_apk),
                     ("APK filename does not match its arm", wrong_variant),
                     ("SHARED APK across arms", collisions),
                     ("usb_handler command unusable", handler_bad)):
    for x in items[:8]:
        print(f"         {label}: {x}")
    if len(items) > 8:
        print(f"         ... and {len(items)-8} more: {label}")

bad = len(missing_cfg) + len(missing_apk) + len(wrong_variant) + len(collisions) + len(handler_bad)
print(f"         {total} configs resolved, {len(owners)} distinct APKs, {bad} problem(s)")
sys.exit(1 if bad else 0)
PYEOF
if [ $? -eq 0 ]; then ok "every cohort config resolves and points at its own variant's APK"
else fail "config/APK mapping problems (listed above)"; fi

# ---------- Y.6: Appium IMEs enabled ----------
# Without all three, Android pops "Available virtual keyboards" mid-cell, the
# Settings activity steals focus, and the cell CONTAMs.
echo "[Y.6] Appium IMEs enabled (3 per phone)"
for phone in "${phone_arr[@]}"; do
    enabled=$(timeout 8 adb -s "${WIFI_ADDR[$phone]}" shell 'ime list -s 2>/dev/null | grep io.appium.settings | wc -l' 2>/dev/null | tr -d '\r' | head -1)
    if [ -z "$enabled" ]; then fail "$phone IME list unreadable"
    elif [ "$enabled" -ge 3 ] 2>/dev/null; then ok "$phone has $enabled Appium IME(s) enabled"
    else fail "$phone has $enabled/3 Appium IMEs (before_experiment_apply_device_state.py enables them; run one cell or enable manually)"; fi
done

# ---------- Y.7: no cohort app left installed ----------
# AR installs the APK at experiment start and the per-app before_experiment
# uninstall hooks remove it at the end, so a leftover means a previous run died
# mid-cell. It matters because AR's install would then be an UPGRADE over a
# different variant's package: app data, dexopt state and first-run flags all
# survive, so the cell measures a warm app with another variant's leftovers
# instead of the cold first-launch the protocol assumes.
echo "[Y.7] No cohort app left installed on any phone"
_appids=$(python3 - "$APPS" "$VARIANTS" "$PHONES" "$CFG_SUFFIX" <<'PYEOF'
import json, sys
from pathlib import Path
apps, variants, phones, suffix = sys.argv[1].split(","), sys.argv[2].split(","), sys.argv[3].split(","), sys.argv[4]
cfg_dir = Path("/home/irena/Documents/Master Experiment/android-runner/examples/batterymanager")
ids = set()
for app in apps:
    for variant in variants:
        for phone in phones:
            for c in (cfg_dir / f"{app}_{variant}_{phone}{suffix}.json",
                      cfg_dir / f"{app}_stub_{variant}_{phone}{suffix}.json"):
                if c.exists():
                    aid = json.loads(c.read_text()).get("application_id")
                    if aid:
                        ids.add(aid)
                    break
print("\n".join(sorted(ids)))
PYEOF
)
if [ -z "$_appids" ]; then
    info "no application_ids resolved — skipping (see Y.5)"
else
    for phone in "${phone_arr[@]}"; do
        _installed=$(timeout 20 adb -s "${WIFI_ADDR[$phone]}" shell 'pm list packages' 2>/dev/null \
                     | tr -d '\r' | sed 's/^package://' | grep -Fxf <(echo "$_appids") || true)
        if [ -z "$_installed" ]; then
            ok "$phone: none of the $(echo "$_appids" | wc -l) cohort packages installed"
        else
            fail "$phone still has: $(echo "$_installed" | tr '\n' ' ')— run tools/uninstall_cohort_ykush.sh"
        fi
    done
fi

# ---------- Y.8: free disk ----------
echo "[Y.8] Free disk space"
free_gb=$(df -BG "${ROOT}" | awk 'NR==2 {gsub("G","",$4); print $4}')
if [ -z "$free_gb" ]; then fail "disk space unreadable"
elif [ "$free_gb" -ge "$MIN_DISK_GB" ] 2>/dev/null; then ok "${free_gb} GB free (>= ${MIN_DISK_GB} GB)"
else fail "${free_gb} GB free (< ${MIN_DISK_GB} GB)"; fi

# ---------- Y.9: SoC in band ----------
# Read from dumpsys, NOT sysfs battery/capacity: on Pixel 3 that node reads a
# constant 100 while the phone is actually at 50 % (verified: capacity=100,
# charge_counter/charge_full=47.6 %, dumpsys level=50).
echo "[Y.9] Per-phone SoC in [${REQUIRE_SOC_MIN}, ${REQUIRE_SOC_MAX}]"
for phone in "${phone_arr[@]}"; do
    lvl=$(timeout 8 adb -s "${WIFI_ADDR[$phone]}" shell "dumpsys battery | grep '^  level:'" 2>/dev/null | awk '{print $2}' | tr -d '\r')
    if [ -z "$lvl" ]; then fail "$phone level unreadable"
    elif [ "$lvl" -lt "$REQUIRE_SOC_MIN" ] 2>/dev/null; then fail "$phone level=$lvl < $REQUIRE_SOC_MIN (run 'tools/ykush_band_keeper.sh once')"
    elif [ "$lvl" -gt "$REQUIRE_SOC_MAX" ] 2>/dev/null; then fail "$phone level=$lvl > $REQUIRE_SOC_MAX (discharge it before launching)"
    else ok "$phone level=$lvl in band"; fi
done

# ---------- Y.10: adb daemon healthy ----------
echo "[Y.10] adb daemon healthy"
n_devices=$(adb devices 2>/dev/null | awk 'NR>1 && /device$/{n++} END{print n+0}')
if [ "$n_devices" -ge 3 ]; then ok "adb sees $n_devices device(s)"
else fail "adb sees only $n_devices device(s) (expected >= 3 over WiFi)"; fi

# ---------- Y.11: stale .pause file ----------
# THREE separate sweeps have been killed by this. sweep_runner checks for
# $STATE_DIR/.pause at the top of every cell and exits the stream cleanly when it
# exists. A watchdog that paused a previous run leaves the file behind; the next
# sweep then runs exactly ONE cell per phone and stops, reporting rc=0 and
# "finished" so it looks like a successful short run. Most recent: 2026-08-01,
# an overnight run produced 3 cells instead of ~250.
echo "[Y.11] No stale .pause files"
_pause_found=0
for _pf in "$ROOT"/tools/sweep_logs/state_*/.pause; do
    [ -e "$_pf" ] || continue
    _pause_found=1
    fail "stale pause file: $_pf (age $(( ( $(date +%s) - $(stat -c %Y "$_pf") ) / 60 )) min) — rm it or the sweep stops after one cell per phone"
done
[ "$_pause_found" -eq 0 ] && ok "no .pause files under tools/sweep_logs/state_*/"

# ---------- Y.12: mitmproxy port free ----------
# run_final_sweep starts its own mitmdump on :8080. An orphan from a previous run
# holds the port, mitmproxy fails to bind with EADDRINUSE, and the wrapper exits
# ~3 s later with rc=0, silently burning the whole run. Observed 2026-08-01.
echo "[Y.12] mitmproxy port ${MITM_PORT:-8080} free"
if ss -lnt 2>/dev/null | grep -q ":${MITM_PORT:-8080} "; then
    _holder=$(ss -lntp 2>/dev/null | grep ":${MITM_PORT:-8080} " | grep -oE 'pid=[0-9]+' | head -1)
    fail "port ${MITM_PORT:-8080} already in use (${_holder:-unknown}) — pkill -f 'mitmdump.*server-replay' first"
else
    ok "port ${MITM_PORT:-8080} is free"
fi

# ---------- Y.13: stale watcher / watchdog processes ----------
# These play audible alerts. Orphans from earlier sweeps keep running for days and
# fire alarms during unrelated runs, at night. On 2026-08-01 there were TWELVE
# watcher processes alive from a sweep 33 hours earlier.
echo "[Y.13] No stale watcher/watchdog processes"
_stale_watch=$(ps -eo pid,cmd --no-headers 2>/dev/null \
    | grep -E "soc_watchdog\.sh" | grep -cv grep || true)
_stale_watch=${_stale_watch:-0}
if [ "$_stale_watch" -gt 0 ]; then
    fail "$_stale_watch watcher/watchdog process(es) running — they fire audible alerts; kill before launching"
else
    ok "no stale watcher processes"
fi

# ---------- Y.15: APK build integrity (DEX-set gate) ----------
# Guards the three ways a variant APK can silently be the wrong code:
#   (a) an AR config's paths[0] points at another variant's APK (also caught by Y.5)
#   (b) a protection step no-ops and the pipeline re-signs the unmodified input,
#       so the FILE hash differs while the DEX is byte-identical. Hash the DEX
#       SET, never the APK file: re-signing masks identical code.
#   (c) a "baseline" carries another variant's runtime hook (documenter baseline
#       contained FreeRaspApplication, 2026-07-31).
echo "[Y.15] APK build integrity (DEX-set gate)"
if [ -f "$ROOT/scratchpad/apk_structural_fingerprint.py" ]; then
    if python3 "$ROOT/scratchpad/apk_structural_fingerprint.py" --gate >/tmp/preflight_apk_gate.txt 2>&1; then
        ok "build-integrity gate passed"
    else
        fail "build-integrity gate FAILED — see /tmp/preflight_apk_gate.txt"
        grep -E "^  COLLISION |^  [a-z_]+ +-> |missing its marker" \
            /tmp/preflight_apk_gate.txt 2>/dev/null | head -10 | sed 's/^/         /' || true
    fi
else
    info "apk_structural_fingerprint.py not found — skipping"
fi

# ---------- Y.16: allatori builds are actually obfuscated ----------
# Y.15's DEX-set hash proves RECOMPILATION, not RENAMING, and its short_class_per_mb
# metric reported +0.0 for poetskingdom (an app with 490 renamed classes) because
# it normalises by total DEX size, which ~24k unobfuscated library classes dominate.
# So a silently no-op'd Allatori build sails straight through it. This diffs the
# class sets directly.
#
# linkhub and keep_recipe are EXPECTED to report zero renames. They are
# negative controls declared before the series: both are Hilt + Compose apps in
# which essentially every class the app owns is a ViewModel, Fragment or
# AppCompatActivity, all of which the conventional Android keep set preserves.
# That is an architectural outcome, not a build mistake: the same config renames
# 490 classes in poetskingdom. Their ΔE is the measurement noise floor, so
# failing them here would be wrong.
echo "[Y.16] Allatori builds proven obfuscated (class-set diff)"
if [ -f "$ROOT/tools/verify_allatori_obfuscation.py" ] && [ "$SKIP_SLOW" -ne 1 ]; then
    _vo=/tmp/preflight_allatori_verify.txt
    python3 "$ROOT/tools/verify_allatori_obfuscation.py" >"$_vo" 2>&1
    # Rows look like: "<app> <VERDICT> <n> <n> <n> <sample>". Only take lines whose
    # first field is a known cohort app, so header/footer prose cannot be mistaken
    # for a failing app name.
    _unexpected=$(awk -v apps="$APPS" '
        BEGIN { n=split(apps, a, ","); for (i=1;i<=n;i++) want[a[i]]=1 }
        ($1 in want) && $2 != "OBFUSCATED" { print $1 }' "$_vo" \
        | grep -vE '^(linkhub|keep_recipe)$' || true)
    if [ -z "$_unexpected" ]; then
        ok "all allatori builds obfuscated except the 2 negative controls declared before the series"
        grep -E "^(linkhub|keep_recipe) " "$_vo" | sed 's/^/         /' || true
    else
        fail "allatori build(s) NOT proven obfuscated: $(echo "$_unexpected" | tr '\n' ' ')— see $_vo"
    fi
else
    info "skipped (--skip-slow, or verify_allatori_obfuscation.py not found)"
fi

# ---------- leave the rig safe ----------
# Whatever happened above, the ports end CUT. A preflight that fails halfway must
# not leave three phones on a live port.
if [ "$BOARD_OK" -eq 1 ]; then
    ykush_cut_all >/dev/null 2>&1 || true
fi

# ---------- summary ----------
echo ""
echo "============ preflight_final_sweep_ykush.sh summary ============"
if [ "$FAIL_COUNT" -ne 0 ]; then
    echo "  ✗ $FAIL_COUNT check(s) failed — sweep is NOT safe to launch (all ports left CUT)"
elif [ "$UNPROVEN_COUNT" -ne 0 ]; then
    # Deliberately NOT a pass. Nothing failed, but $UNPROVEN_COUNT gate(s) were
    # asked a question they could not answer, and every one of them is a gate
    # whose whole job is to catch a silently-invalid sweep.
    echo "  ! no failures, but $UNPROVEN_COUNT check(s) are UNPROVEN — NOT launch-qualified (all ports left CUT)"
    echo "    Re-run without --skip-slow to turn these into real verdicts."
else
    echo "  ✓ all checks passed — ready to launch (all ports left CUT)"
fi
exit "$FAIL_COUNT"
