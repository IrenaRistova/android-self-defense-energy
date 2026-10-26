#!/usr/bin/env python3
"""Every series build's declared permissions, read from the measured APK itself.

WHY IT EXISTS (2026-10-05)
--------------------------
`variant_parity_full.csv` records only whether a build declares INTERNET. The freeRASP integration
adds more than that (Wi-Fi and network state, Google services, biometrics, start at boot). This
input lists every permission of every measured build, so the notebooks can emit what each
protection adds to its control.

WHAT IT READS
-------------
The three series run tables. For each (app, variant) it takes the APK the cells ran (`apk_path`), checks
that every series cell of that build names the same SHA-256 (`apk_sha256`) and that the file on disk has
that hash, and runs `aapt2 dump permissions` on it.

OUTPUT
------
`6_results_data/inputs/variant_permissions.csv`: one row per (app, variant, permission), with the APK's
SHA-256. Provenance: `6_results_data/inputs/variant_permissions.PROVENANCE.md`.

Usage (from the repository root):
    python3 tools/extract_variant_permissions.py [--aapt2 PATH]
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_TABLES = [ROOT / "6_results_data" / "run_tables" / f for f in ("run_table_extended_final_ykush_batch2_2026-08-10.csv",
                                                                  "run_table_extended_final_ykush_series2_2026-08-14.csv",
                                                                  "run_table_extended_final_ykush_series3_2026-08-17.csv")]
OUT = ROOT / "6_results_data" / "inputs" / "variant_permissions.csv"


def find_aapt2(given: str | None) -> str:
    if given:
        return given
    cands = sorted(glob.glob(os.path.expanduser("~/Android/Sdk/build-tools/*/aapt2")))
    if not cands:
        sys.exit("aapt2 not found: pass --aapt2 PATH")
    return cands[-1]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aapt2")
    args = ap.parse_args()
    aapt2 = find_aapt2(args.aapt2)

    builds: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for t in RUN_TABLES:
        for r in csv.DictReader(open(t, encoding="utf-8")):
            if r.get("apk_path"):
                builds.setdefault((r["app"], r["variant"]), set()).add((r["apk_path"], r["apk_sha256"]))

    rows = []
    for (app, variant), apks in sorted(builds.items()):
        shas = {s for _, s in apks}
        if len(shas) != 1:
            sys.exit(f"{app} {variant}: the series cells name {len(shas)} different APKs: {sorted(shas)}")
        path, sha = sorted(apks)[0]
        if sha256(path) != sha:
            sys.exit(f"{app} {variant}: {path} on disk does not have the run tables' SHA-256 {sha}")
        out = subprocess.run([aapt2, "dump", "permissions", path], capture_output=True, text=True, check=True).stdout
        perms = sorted({line.split("name='")[1].split("'")[0] for line in out.splitlines()
                        if line.strip().startswith("uses-permission") and "name='" in line})
        if not perms:
            sys.exit(f"{app} {variant}: aapt2 listed no permissions; refusing to write an empty build")
        rows += [{"app": app, "variant": variant, "permission": p, "apk_sha256": sha} for p in perms]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["app", "variant", "permission", "apk_sha256"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(builds)} builds, {len(rows)} permission rows -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
