#!/usr/bin/env python3
"""verify_variant_parity.py -- do the four variants of each app differ ONLY by the protection?

Written 2026-10-02. Produces 6_results_data/inputs/variant_parity.csv, the input
to the variant-parity table in the results notebooks.

A matched pair is only a pair if the treated APK is the control APK plus the
protection and nothing else. This asks, of the exact APK every AR config swept:

  * is it the APK the run tables recorded?   (sha256 vs the run tables' apk_sha256)
  * same upstream version?                    (versionCode, versionName)
  * same platform targets?                    (minSdk, targetSdk)
  * same network-security-config state?       (NSC attribute present or not)
  * same INTERNET permission?                 (freeRASP's SDK adds it)
  * debuggable?                               (every thesis build is a debug build)
  * does it carry its protection's marker?    (structural detector, see
                                               scratchpad/apk_structural_fingerprint.py)

Every APK is read from disk; nothing is taken from a build log. Allatori leaves
no structural marker (it is compile-time renaming only), so it -- like the control --
must read NO_STRUCTURAL_MARKER, and its effect is measured separately (classes renamed, see
6_results_data/inputs/allatori_static_code_effects.csv).

Usage:
    tools/verify_variant_parity.py --csv 6_results_data/inputs/variant_parity.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CFG_DIR = REPO / "android-runner" / "examples" / "batterymanager"
AAPT2 = sorted(glob.glob(str(pathlib.Path.home() / "Android/Sdk/build-tools/*/aapt2")))
VARIANTS = ["baseline_debug", "allatori_debug", "bangcle_debug", "freerasp_debug"]
COHORT = ["androidskk", "calculator", "documenter", "itsok", "keep_recipe",
          "linkhub", "medtimer", "pdfviewer", "poetskingdom", "tipuous"]
PHONES = ["pixel3", "pixel6", "pixel9"]

sys.path.insert(0, str(REPO / "scratchpad"))
import apk_structural_fingerprint as FP  # noqa: E402


def swept_apks(app: str, variant: str) -> set[pathlib.Path]:
    """Every APK path any phone's AR config for this (app, variant) points at."""
    out = set()
    for phone in PHONES:
        cfg = CFG_DIR / f"{app}_{variant}_{phone}_wifi_ykush.json"
        if not cfg.exists():
            continue
        for p in json.loads(cfg.read_text()).get("paths") or []:
            if p.endswith(".apk"):
                out.add(pathlib.Path(p))
    return out


def recorded_hashes() -> dict:
    hashes: dict = {}
    for f in sorted((REPO / "6_results_data" / "run_tables").glob("*.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("apk_sha256"):
                    hashes.setdefault((r["app"], r["variant"]), set()).add(r["apk_sha256"])
    return hashes


def manifest(apk: pathlib.Path) -> dict:
    if not AAPT2:
        raise SystemExit("aapt2 not found under ~/Android/Sdk/build-tools")
    b = subprocess.run([AAPT2[-1], "dump", "badging", str(apk)],
                       capture_output=True, text=True, check=True).stdout
    x = subprocess.run([AAPT2[-1], "dump", "xmltree", "--file", "AndroidManifest.xml",
                        str(apk)], capture_output=True, text=True, check=True).stdout
    perms = subprocess.run([AAPT2[-1], "dump", "permissions", str(apk)],
                           capture_output=True, text=True, check=True).stdout
    g = lambda rx: (re.search(rx, b) or [None, ""])[1]  # noqa: E731
    return {"version_code": g(r"versionCode='(\d+)'"),
            "version_name": g(r"versionName='([^']*)'"),
            "min_sdk": g(r"minSdkVersion:'(\d+)'"),
            "target_sdk": g(r"targetSdkVersion:'(\d+)'"),
            "network_security_config": "networkSecurityConfig" in x,
            "debuggable": "android:debuggable(0x0101000f)=true" in x,
            "declares_internet": "android.permission.INTERNET'" in perms
                                 or "android.permission.INTERNET\n" in perms}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="", help="write one row per (app, variant)")
    a = ap.parse_args()
    rec = recorded_hashes()
    rows = []
    for app in COHORT:
        base_fp = None
        for variant in VARIANTS:
            apks = swept_apks(app, variant)
            if not apks:
                continue
            if len(apks) > 1:
                print(f"{app}/{variant}: phones point at {len(apks)} different APKs",
                      file=sys.stderr)
            apk = sorted(apks)[0]
            if not apk.exists():
                rows.append({"app": app, "variant": variant, "apk": apk.name,
                             "note": "APK missing on disk"})
                continue
            sha = hashlib.sha256(apk.read_bytes()).hexdigest()
            fp = FP.fingerprint_apk(apk)
            if variant == "baseline_debug":
                base_fp = fp
            # Classified for every variant: the control and the Allatori build must
            # read NO_STRUCTURAL_MARKER, or a protection leaked into them.
            marker = FP.classify(fp, base_fp or fp)["verdict"]
            seen = rec.get((app, variant), set())
            rows.append({"app": app, "variant": variant, "apk": apk.name,
                         "phones_share_one_apk": len(apks) == 1,
                         "sha_matches_run_tables": seen == {sha},
                         **manifest(apk), "structural_marker": marker,
                         "apk_sha256": sha})
    for r in rows:
        print(" ".join(f"{k}={v}" for k, v in r.items() if k != "apk_sha256"))
    if a.csv:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
