#!/usr/bin/env python3
"""ykush_sync_ar_configs.py: emit AR configs whose USB handler drives the YKUSH3.

Written 2026-08-03. Writes `*_wifi_ykush.json` configs next to the existing
`*_wifi_hubctl.json` ones and never modifies the originals.

WHY NOT JUST REWRITE THE EXISTING CONFIGS
-----------------------------------------
The uhubctl-based sweep runner rewrote every `*_wifi_hubctl.json` so its
`uhubctl -l <hub> -p <port>` matched the live hub. On this hardware that is
actively dangerous, for two independent reasons:

1. uhubctl does not cut VBUS on a YKUSH3. It drives the board's internal
   Microchip USB2744 hub, which advertises ppps and accepts the command -- the
   phone even drops off USB -- but power keeps flowing. Measured 2026-08-03: a
   Pixel 3 read BatteryManager status=2 (charging) at ~500 mA thirty seconds
   after uhubctl reported "Port 1: 0000 off". Every cell would have measured a
   charging phone while the logs looked clean.

   Those configs hold a STALE hub address, so uhubctl errors out and
   AR raises USBHandlerException -- loud and safe. Running that sync is precisely
   what would convert that loud failure into a silent one.

2. The port numbers would be wrong anyway. The board's downstream numbering is
   REVERSED relative to the Linux sysfs indices (calibrated by observation
   2026-08-03: board port 1 = pixel3, but pixel3 enumerates at sysfs 3-6.3).
   The sync writes sysfs port numbers, so it would aim pixel3's cut at pixel9.

Hence: new files, new naming, old path untouched and quarantined.

WHAT CHANGES IN EACH CONFIG
---------------------------
Only `usb_handler.enable_command` / `disable_command`. Everything else -- device
list, profilers, scripts, duration, repetitions, paths -- is copied verbatim, so
the measurement configuration is provably identical to the audited original.

Usage:
    tools/ykush_sync_ar_configs.py --check     # report, write nothing
    tools/ykush_sync_ar_configs.py             # write the new configs
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
AR_DIR = REPO / "android-runner/examples/batterymanager"
WRAPPER = REPO / "tools/ykush_ar_power.sh"
# Created by tools/ykush_calibrate.py on a new set-up (the map is not part of this package).
PORTMAP = REPO / "tools" / "ykush_portmap.json"

# The final sweep cohort: 10 apps, each in the four variants below.
COHORT = [
    "calculator", "tipuous", "poetskingdom", "linkhub", "medtimer",
    "androidskk", "keep_recipe", "pdfviewer", "documenter", "itsok",
]
VARIANTS = ["baseline_debug", "bangcle_debug", "allatori_debug", "freerasp_debug"]
PHONES = ["pixel3", "pixel6", "pixel9"]

NAME_RE = re.compile(r"^(?P<app>.+)_(?P<variant>[a-z0-9]+_debug)_(?P<phone>pixel[369])_wifi_hubctl\.json$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    a = ap.parse_args()

    if not WRAPPER.exists():
        print(f"missing wrapper {WRAPPER}", file=sys.stderr)
        return 2
    if not PORTMAP.exists():
        print(f"missing port map {PORTMAP} — run tools/ykush_calibrate.py first", file=sys.stderr)
        return 2

    pm = json.loads(PORTMAP.read_text())
    mapped = set(pm.get("ports", {}).values())
    missing_phones = [p for p in PHONES if p not in mapped]
    if missing_phones:
        print(f"port map does not cover {missing_phones}; re-run tools/ykush_calibrate.py", file=sys.stderr)
        return 2
    print(f"port map (board {pm['board_serial']}, calibrated {pm['calibrated_utc']}): {pm['ports']}\n")

    written, skipped, missing = 0, 0, []
    for src in sorted(AR_DIR.glob("*_wifi_hubctl.json")):
        m = NAME_RE.match(src.name)
        if not m:
            continue
        app, variant, phone = m["app"], m["variant"], m["phone"]
        if app not in COHORT or variant not in VARIANTS:
            skipped += 1
            continue

        cfg = json.loads(src.read_text())
        uh = cfg.get("usb_handler")
        if not isinstance(uh, dict):
            missing.append(f"{src.name}: no usb_handler block")
            continue

        # Only the two commands change; everything else is copied verbatim.
        #
        # THE PATH MUST BE QUOTED. AndroidRunner/USBHandler.py runs the command
        # through `subprocess.Popen(shlex.split(command))`, and this repo lives at
        # ".../Master Experiment/", with a space. Unquoted, shlex.split yields
        # argv[0] = "/home/irena/Documents/Master" and AR dies with
        # FileNotFoundError the moment it tries to cut USB, i.e. immediately
        # before the workload. Verified 2026-08-04: 3/3 smoke cells failed exactly
        # there. The uhubctl commands this replaces were "sudo -n /usr/sbin/uhubctl
        # ..." (no spaces anywhere), so the whole config set had never exercised
        # this path.
        cfg["usb_handler"] = dict(uh)
        cfg["usb_handler"]["enable_command"] = f'"{WRAPPER}" {phone} on'
        cfg["usb_handler"]["disable_command"] = f'"{WRAPPER}" {phone} off'
        cfg["_comment"] = (
            (cfg.get("_comment", "") + " | ").lstrip(" |")
            + "2026-08-03: USB power switched via YKUSH3 (tools/ykush_ar_power.sh). "
              "Do NOT use uhubctl on this board — it disables the data port but leaves "
              "VBUS live, so the phone charges through the measurement."
        )

        dst = src.with_name(src.name.replace("_wifi_hubctl.json", "_wifi_ykush.json"))
        if a.check:
            print(f"  would write {dst.name}")
        else:
            dst.write_text(json.dumps(cfg, indent=2) + "\n")
        written += 1

    print(f"\n{'would write' if a.check else 'wrote'} {written} configs; skipped {skipped} non-cohort")
    if missing:
        print("\nPROBLEMS:", file=sys.stderr)
        for x in missing:
            print("  " + x, file=sys.stderr)
        return 1

    expected = len(COHORT) * len(VARIANTS) * len(PHONES)
    if written != expected:
        print(f"\nWARNING: expected {expected} configs (9 apps x 4 variants x 3 phones), got {written}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
