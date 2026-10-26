#!/usr/bin/env python3
"""ykush_calibrate.py: determine which YKUSH3 port feeds which phone, by observation.

Written 2026-08-03. Does not touch the uhubctl path.

WHY THIS IS NOT OPTIONAL
------------------------
1. The YKUSH3 datasheet documents NO mapping between the board's silkscreen
   "Downstream 1/2/3" and Linux sysfs port indices. Assuming sysfs port N ==
   ykushcmd port N is an unverified guess whose failure mode is cutting the
   WRONG phone -- i.e. measuring one phone while charging another, silently.

2. It is not stable across replugs. Measured 2026-08-03 within a single session:

       before:  P3 -> 3-6.1   P6 -> 3-6.2   P9 -> 3-6.3
       after:   P9 -> 3-6.1   P6 -> 3-6.2   P3 -> 3-6.3

   Same cables, same board, phones moved between physical connectors. Anything
   hardcoded would now be wrong.

3. A cut port removes VBUS *and* both data pairs, so the phone disappears from
   sysfs completely. You cannot resolve a phone whose port is already off --
   which is exactly when you most need to know its port. A persisted map is the
   only thing that answers "which port do I power back on for pixel6?".

METHOD
------
For each port p in 1,2,3: cut p, wait, and see which phone reports
usb/online == 0 over WiFi ADB. Exactly one phone must flip. Then restore.

Reading usb/online over WiFi (not USB) is deliberate: the phone stays reachable
while its USB port is dead. And it reads RAW SYSFS rather than `dumpsys battery`,
because Android Runner calls device.unplug() at every experiment start, which
mocks the dumpsys battery status above sysfs -- AndroidRunner/USBHandler.py says
so in its own docstring. A dumpsys-based probe would report "charging" for a
phone whose VBUS is genuinely gone, and vice versa.

The "exactly one phone flips" assertion doubles as a regression test for the
defect that killed the previous Plugable hub, where cutting one port re-powered
its siblings within ~3s.

Usage:
    tools/ykush_calibrate.py                 # calibrate and write the map
    tools/ykush_calibrate.py --dry-run       # show what it would do
    tools/ykush_calibrate.py --verify        # check the existing map still holds
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
# This script writes the map here; on a new set-up its first run creates the file
# (the map is not part of this package).
MAP_PATH = REPO / "tools" / "ykush_portmap.json"
YKUSH_BIN = os.environ.get("YKUSH_BIN", "/usr/bin/ykushcmd")
# Replicators set YKUSH_SERIAL to their own board's serial.
YKUSH_SERIAL = os.environ.get("YKUSH_SERIAL", "<ykush-serial>")

# WiFi ADB addresses: the phone must stay reachable while its USB port is dead.
# Replicators put their own phones' addresses here.
PHONES = {
    "pixel3": "<pixel3-ip>:5555",
    "pixel6": "<pixel6-ip>:5555",
    "pixel9": "<pixel9-ip>:5555",
}

SETTLE_AFTER_CUT_S = 6.0
SETTLE_AFTER_RESTORE_S = 10.0


def yk(*args: str) -> tuple[int, str]:
    """Every board call goes through here. `-s <serial>` MUST precede the action
    flag -- ykushcmd silently ignores it otherwise, so a command aimed at one
    board can land on another. The literal 'ykush3' is likewise required:
    omitting it addresses a different board class entirely."""
    cmd = [YKUSH_BIN, "ykush3", "-s", YKUSH_SERIAL, *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return p.returncode, (p.stdout or "").strip()


def usb_online(addr: str) -> str | None:
    """Raw sysfs, over WiFi. Returns '0', '1', or None if unreachable."""
    try:
        p = subprocess.run(
            ["adb", "-s", addr, "shell", "cat /sys/class/power_supply/usb/online"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None
    v = (p.stdout or "").strip()
    return v if v in ("0", "1") else None


def port_state(port: int) -> str:
    """ykushcmd -g echoes the port number the DEVICE reports, not the one asked
    for, and the send is unchecked -- a failed transfer decodes uninitialised
    stack memory. So the echoed port is asserted rather than trusted."""
    rc, out = yk("-g", str(port))
    if rc != 0:
        return "UNKNOWN"
    if out == f"Port {port} ON":
        return "ON"
    if out == f"Port {port} OFF":
        return "OFF"
    print(f"  !! unparseable -g reply for port {port}: {out!r}", file=sys.stderr)
    return "UNKNOWN"


def snapshot() -> dict[str, str | None]:
    return {name: usb_online(addr) for name, addr in PHONES.items()}


def calibrate(dry_run: bool = False) -> dict:
    print(f"YKUSH3 {YKUSH_SERIAL} — calibrating port map by observation\n")

    # All ports on, so every phone is visible before we start.
    for p in (1, 2, 3):
        yk("-u", str(p))
    time.sleep(SETTLE_AFTER_RESTORE_S)

    base = snapshot()
    print(f"  baseline (all ports on): {base}")
    missing = [n for n, v in base.items() if v != "1"]
    if missing:
        print(f"\n  ABORT: these phones do not see VBUS with all ports on: {missing}", file=sys.stderr)
        print("  Check the cables and that each phone is on a YKUSH downstream port.", file=sys.stderr)
        return {}

    if dry_run:
        print("\n  --dry-run: would now cut each port in turn and observe.")
        return {}

    mapping: dict[str, str] = {}
    for p in (1, 2, 3):
        print(f"\n  cutting port {p} ...")
        rc, _ = yk("-d", str(p))
        if rc != 0 or port_state(p) != "OFF":
            print(f"    ABORT: port {p} did not report OFF", file=sys.stderr)
            for q in (1, 2, 3):
                yk("-u", str(q))
            return {}
        time.sleep(SETTLE_AFTER_CUT_S)

        after = snapshot()
        dropped = [n for n in PHONES if base[n] == "1" and after[n] != "1"]
        print(f"    online after cut: {after}")

        if len(dropped) != 1:
            print(f"\n    ABORT: expected exactly ONE phone to lose VBUS, got {dropped}.", file=sys.stderr)
            if len(dropped) > 1:
                print("    More than one dropped => cross-port interference, the exact defect", file=sys.stderr)
                print("    that made the previous Plugable hub unusable. Do NOT sweep on this board.", file=sys.stderr)
            else:
                print("    None dropped => the cut is not reaching VBUS. This is the uhubctl", file=sys.stderr)
                print("    failure mode; check you are driving ykushcmd and not the USB2744 hub.", file=sys.stderr)
            for q in (1, 2, 3):
                yk("-u", str(q))
            return {}

        phone = dropped[0]
        mapping[str(p)] = phone
        print(f"    => port {p} feeds {phone}")

        yk("-u", str(p))
        time.sleep(SETTLE_AFTER_RESTORE_S)

    if sorted(mapping.values()) != sorted(PHONES):
        print(f"\n  ABORT: map does not cover every phone exactly once: {mapping}", file=sys.stderr)
        return {}

    return {
        "board_serial": YKUSH_SERIAL,
        "calibrated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "observed: cut each port, see which phone lost /sys/class/power_supply/usb/online",
        "ports": mapping,
    }


def verify() -> int:
    if not MAP_PATH.exists():
        print(f"no map at {MAP_PATH}", file=sys.stderr)
        return 3
    m = json.loads(MAP_PATH.read_text())
    if m.get("board_serial") != YKUSH_SERIAL:
        print(f"map is for board {m.get('board_serial')!r}, live board is {YKUSH_SERIAL!r}", file=sys.stderr)
        return 4
    fresh = calibrate()
    if not fresh:
        return 5
    if fresh["ports"] != m["ports"]:
        print(f"\nMAP CHANGED\n  stored: {m['ports']}\n  live:   {fresh['ports']}", file=sys.stderr)
        return 6
    print(f"\nmap still correct: {m['ports']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()

    rc, out = yk("-l")
    if f"Board found with serial number: {YKUSH_SERIAL}" not in out:
        # -l exits 0 even when it finds nothing, so stdout is the only signal.
        print(f"board {YKUSH_SERIAL} not found; ykushcmd said: {out!r}", file=sys.stderr)
        return 6

    if a.verify:
        return verify()

    m = calibrate(dry_run=a.dry_run)
    if not m:
        return 1 if not a.dry_run else 0

    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(m, indent=2) + "\n")
    print(f"\nwrote {MAP_PATH}")
    print(json.dumps(m["ports"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
