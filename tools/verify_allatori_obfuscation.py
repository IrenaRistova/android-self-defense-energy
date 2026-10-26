#!/usr/bin/env python3
"""verify_allatori_obfuscation.py: prove each allatori APK is actually obfuscated.

Written 2026-08-03.

WHY THIS EXISTS
---------------
apk_structural_fingerprint.py identifies bangcle (stub DEX + payload) and
freerasp (Talsec classes) by structural markers, and its GATE 1 proves every
variant is a DISTINCT build via the DEX-set hash. But baseline and allatori both
come out as NO_STRUCTURAL_MARKER, and its renaming metric
(`short_class_per_mb`) is TOO INSENSITIVE to tell them apart:

    poetskingdom   baseline 0.3 short/MB   allatori 0.4 short/MB   delta +0.1

That reads as "not obfuscated", yet a direct class-set diff of the same two
APKs shows 470 classes present only in the allatori build, named `a`, `b`, `B`,
`com/wendorochena/poetskingdom/a`, `aA`, and 439 original names gone.

The metric fails because it normalises by total DEX megabytes, and the DEX is
dominated by ~24k unobfuscated library classes. A few hundred renamed app
classes vanish into that denominator. So a silently no-op'd Allatori build
would sail through the gate, for example one whose obfuscator runs BEFORE the
ASM transform, so that its output is discarded.

This checks the thing that actually matters: are the APP's OWN classes renamed?

METHOD
------
For each app, dump class descriptors from the baseline and allatori APKs and
compare the sets. Obfuscation is asserted by two independent signals:
  1. classes that exist ONLY in allatori and have short (1-3 char) leaf names;
  2. classes that exist ONLY in baseline (their original names are gone).
Both must be non-trivial. A build where the class sets are near-identical is
reported as NOT OBFUSCATED regardless of what its DEX-set hash says, because a
distinct hash only proves recompilation, not renaming.

Usage:
    tools/verify_allatori_obfuscation.py            # whole cohort
    tools/verify_allatori_obfuscation.py --app linkhub
"""
from __future__ import annotations

import argparse
import csv
import glob
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

APK_ROOT = pathlib.Path("/home/irena/Documents/Master Thesis/APKs")
COHORT = ["calculator", "tipuous", "poetskingdom", "linkhub", "medtimer",
          "androidskk", "keep_recipe", "pdfviewer", "documenter", "itsok"]

# Minimum renamed classes to call a build obfuscated. Deliberately low: some
# apps legitimately obfuscate very little because Hilt/Dagger/databinding
# scaffolding is keep-exempt (measured range this cohort: a few dozen to 80%+).
# The point is to catch ZERO, i.e. a no-op build.
MIN_RENAMED = 15


def find_dexdump() -> str:
    for p in sorted(glob.glob(str(pathlib.Path.home() / "Android/Sdk/build-tools/*/dexdump")), reverse=True):
        return p
    print("dexdump not found under ~/Android/Sdk/build-tools/*/", file=sys.stderr)
    sys.exit(2)


def apk_for(app: str, variant: str) -> pathlib.Path | None:
    hits = sorted((APK_ROOT / f"{variant}_canonical").glob(f"{app}_{variant}*.apk"))
    return hits[0] if hits else None


def classes_of(dexdump: str, apk: pathlib.Path) -> set[str]:
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["unzip", "-qo", str(apk), "*.dex", "-d", td],
                       check=False, capture_output=True)
        dexes = sorted(glob.glob(f"{td}/*.dex"))
        if not dexes:
            return set()
        # binary mode + errors="replace": dexdump emits raw bytes from string
        # constants that are not valid UTF-8 (hit on real cohort APKs).
        p = subprocess.run([dexdump, *dexes], capture_output=True, check=False)
    text = p.stdout.decode("utf-8", errors="replace")
    out = set()
    for m in re.finditer(r"Class descriptor\s*:\s*'L([^;]+);'", text):
        out.add(m.group(1))
    return out


def leaf(c: str) -> str:
    return c.rsplit("/", 1)[-1]


def check(app: str, dexdump: str) -> tuple[str, dict]:
    base, allat = apk_for(app, "baseline_debug"), apk_for(app, "allatori_debug")
    if not base or not allat:
        return "MISSING_APK", {}
    b, a = classes_of(dexdump, base), classes_of(dexdump, allat)
    if not b or not a:
        return "DEX_READ_FAILED", {}

    only_allatori = a - b
    only_baseline = b - a
    short_new = {c for c in only_allatori if len(re.sub(r"\$.*", "", leaf(c))) <= 3}

    info = {
        "baseline_classes": len(b),
        "allatori_classes": len(a),
        "only_allatori": len(only_allatori),
        "only_baseline": len(only_baseline),
        "short_named_new": len(short_new),
        "sample": sorted(short_new)[:4],
    }
    if len(short_new) >= MIN_RENAMED and len(only_baseline) >= MIN_RENAMED:
        return "OBFUSCATED", info
    if len(only_allatori) == 0 and len(only_baseline) == 0:
        return "IDENTICAL_CLASS_SET", info
    return "WEAK_OR_NONE", info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app")
    ap.add_argument("--swept", action="store_true",
                    help="read the APKs the AR configs actually swept, not *_canonical/ "
                         "(the two differ for medtimer: 400 renamed swept, 430 canonical)")
    ap.add_argument("--csv", default="", help="also write one row per app to this CSV")
    a = ap.parse_args()
    if a.swept:
        global apk_for
        from verify_baseline_not_obfuscated import apk_and_package
        apk_for = lambda app, variant: apk_and_package(app, variant)[0]  # noqa: E731
    rows = []
    dexdump = find_dexdump()
    apps = [a.app] if a.app else COHORT

    print(f"{'app':14} {'verdict':20} {'renamed-in':>10} {'gone-from':>10} {'short-new':>10}  sample")
    print(f"{'':14} {'':20} {'allatori':>10} {'baseline':>10} {'names':>10}")
    bad = []
    for app in apps:
        verdict, info = check(app, dexdump)
        if verdict != "OBFUSCATED":
            bad.append(app)
        rows.append({"app": app, "verdict": verdict,
                     "classes_only_in_allatori": info.get("only_allatori", ""),
                     "classes_only_in_baseline": info.get("only_baseline", ""),
                     "short_named_new": info.get("short_named_new", ""),
                     "apks": "swept" if a.swept else "canonical"})
        if info:
            print(f"{app:14} {verdict:20} {info['only_allatori']:10d} {info['only_baseline']:10d} "
                  f"{info['short_named_new']:10d}  {','.join(info['sample'][:3])}")
        else:
            print(f"{app:14} {verdict:20}")

    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print()
    if bad:
        print(f"NOT PROVEN OBFUSCATED: {bad}", file=sys.stderr)
        print("A distinct DEX-set hash only proves recompilation. If a build lands here,", file=sys.stderr)
        print("check that the Allatori build ran the obfuscator AFTER transform<V>ClassesWithAsm", file=sys.stderr)
        print("(its output, not javac's, is what the dexer consumes).", file=sys.stderr)
        return 1
    print("ALL ALLATORI BUILDS CONFIRMED OBFUSCATED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
