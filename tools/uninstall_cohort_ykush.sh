#!/usr/bin/env bash
# uninstall_cohort_ykush.sh: remove every cohort package from every phone.
#
# Written 2026-08-04.
#
# WHY THIS MATTERS FOR THE MEASUREMENT, not just for tidiness
# ───────────────────────────────────────────────────────────
# AR installs the APK at experiment start and the per-app before_experiment
# uninstall hooks remove it at the end. A leftover package means a previous run
# died mid-cell, and the next cell's install then becomes an UPGRADE over a
# DIFFERENT variant's package rather than a fresh install. App data, dexopt
# state, ART profiles and first-run flags all survive an upgrade, so that cell
# measures a warm app carrying another variant's leftovers instead of the cold
# first launch the protocol assumes. The cell still PASSes.
#
# It also breaks signature-mismatched installs outright: the four variants are
# signed with the same debug keystore, so upgrades usually succeed silently,
# which is precisely why this never announces itself.
#
# Package list is read from the AR configs, not hardcoded, so it cannot drift
# from what the sweep will actually install.
#
# Usage:
#   tools/uninstall_cohort_ykush.sh                 # all cohort apps, all phones
#   tools/uninstall_cohort_ykush.sh --dry-run
#   tools/uninstall_cohort_ykush.sh --phones pixel3
set -uo pipefail

ROOT="/home/irena/Documents/Master Experiment"
AR_CFG="$ROOT/android-runner/examples/batterymanager"
PHONES="pixel3,pixel6,pixel9"
APPS="calculator,tipuous,poetskingdom,linkhub,medtimer,androidskk,itsok,keep_recipe,documenter,pdfviewer"
VARIANTS="baseline_debug,bangcle_debug,allatori_debug,freerasp_debug"
CFG_SUFFIX="${SWEEP_CFG_SUFFIX:-_wifi_ykush}"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --phones)   PHONES="$2"; shift 2 ;;
        --apps)     APPS="$2"; shift 2 ;;
        --variants) VARIANTS="$2"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        -h|--help)  sed -n '2,28p' "$0"; exit 0 ;;
        *)          echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Replicators put their own phones' Wi-Fi ADB addresses here.
declare -A WIFI_ADDR
WIFI_ADDR[pixel3]="<pixel3-ip>:5555"
WIFI_ADDR[pixel6]="<pixel6-ip>:5555"
WIFI_ADDR[pixel9]="<pixel9-ip>:5555"

APPIDS=$(python3 - "$APPS" "$VARIANTS" "$PHONES" "$CFG_SUFFIX" "$AR_CFG" <<'PYEOF'
import json, sys
from pathlib import Path
apps, variants, phones, suffix, cfgdir = (sys.argv[1].split(","), sys.argv[2].split(","),
                                          sys.argv[3].split(","), sys.argv[4], Path(sys.argv[5]))
ids = set()
for app in apps:
    for variant in variants:
        for phone in phones:
            for c in (cfgdir / f"{app}_{variant}_{phone}{suffix}.json",
                      cfgdir / f"{app}_stub_{variant}_{phone}{suffix}.json"):
                if c.exists():
                    aid = json.loads(c.read_text()).get("application_id")
                    if aid:
                        ids.add(aid)
                    break
print("\n".join(sorted(ids)))
PYEOF
)

if [ -z "$APPIDS" ]; then
    echo "no application_ids resolved from ${AR_CFG}/*${CFG_SUFFIX}.json — nothing to do" >&2
    exit 2
fi

echo "cohort packages ($(echo "$APPIDS" | wc -l)):"
echo "$APPIDS" | sed 's/^/  /'
echo ""

rc=0
for phone in $(echo "$PHONES" | tr ',' ' '); do
    addr="${WIFI_ADDR[$phone]:-}"
    [ -n "$addr" ] || { echo "$phone: unknown phone"; rc=1; continue; }
    if ! timeout 8 adb -s "$addr" shell echo ok 2>/dev/null | grep -q ok; then
        echo "$phone: NOT reachable at $addr — skipping"
        rc=1
        continue
    fi
    installed=$(timeout 20 adb -s "$addr" shell 'pm list packages' 2>/dev/null \
                | tr -d '\r' | sed 's/^package://' | grep -Fxf <(echo "$APPIDS") || true)
    if [ -z "$installed" ]; then
        echo "$phone: clean (no cohort package installed)"
        continue
    fi
    for pkg in $installed; do
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "$phone: (dry-run) would uninstall $pkg"
            continue
        fi
        # `pm uninstall --user 0` rather than `adb uninstall`: it also clears the
        # per-user data directory, which is what carries first-run flags between
        # variants. Fall back to plain uninstall if the package is a system app
        # or the per-user form is rejected.
        out=$(timeout 30 adb -s "$addr" shell "pm uninstall --user 0 $pkg" 2>&1 | tr -d '\r')
        if [ "$out" != "Success" ]; then
            out=$(timeout 30 adb -s "$addr" uninstall "$pkg" 2>&1 | tr -d '\r' | tail -1)
        fi
        if [ "$out" = "Success" ]; then
            echo "$phone: uninstalled $pkg"
        else
            echo "$phone: FAILED to uninstall $pkg — $out"
            rc=1
        fi
    done
done

exit $rc
