#!/usr/bin/env python3
"""Does any measured APK carry JaCoCo coverage instrumentation?

The analysis sets medtimer's packed build aside because it carries JaCoCo coverage probes that its control lacks. This
check makes that verifiable. For every APK the three series batches measured (column `apk_path` of the run tables,
identified by `apk_sha256`) it counts the JaCoCo files inside the APK (`jacoco-agent.properties` and the JaCoCo
runtime's `jacoco.properties`) and the `jacocoInit` references in its DEX files. Of a packed build, only the part the
packer leaves unencrypted can be read.

Needs the measured APKs, which are not in this package (available on request).
Usage (from the repository root):  python3 tools/check_coverage_instrumentation.py [-o PATH]
"""
import argparse
import csv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_TABLES = ROOT / "6_results_data" / "run_tables"
SERIES = ("run_table_extended_final_ykush_batch2_2026-08-10.csv", "run_table_extended_final_ykush_series2_2026-08-14.csv",
          "run_table_extended_final_ykush_series3_2026-08-17.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "6_results_data" / "inputs" / "coverage_screen.csv")
    a = ap.parse_args()
    apks = {}
    for name in SERIES:
        with open(RUN_TABLES / name, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["apk_path"]:
                    apks[r["apk_sha256"]] = (r["app"], r["variant"], r["apk_path"])
    rows = []
    for sha, (app, variant, path) in sorted(apks.items(), key=lambda kv: kv[1]):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            files = [n for n in names if "jacoco" in n.lower()]
            refs = sum(z.read(n).count(b"jacocoInit") for n in names if n.endswith(".dex"))
        rows.append({"app": app, "variant": variant, "apk_sha256": sha, "jacoco_files": len(files), "jacocoInit_refs": refs})
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["app", "variant", "apk_sha256", "jacoco_files", "jacocoInit_refs"])
        w.writeheader()
        w.writerows(rows)
    marked = [f"{r['app']}/{r['variant']}" for r in rows if r["jacoco_files"] or r["jacocoInit_refs"]]
    print(f"{len(rows)} APKs; with JaCoCo files or references: {', '.join(marked) or 'none'}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
