#!/usr/bin/env python3
"""The compilation state of the app under test before each cell's launch (series batches).

Before every launch, the before_run hook (android-runner/Scripts/before_run.py)
asks Android to compile the app with `cmd package compile -f -m speed <package>`, then saves
`dumpsys package dexopt` for it to the run's dexopt_state.txt. This reads, per cell, the filter that was
requested and the status Android reports for the app's base.apk ("speed" = compiled ahead of time;
"verify" = only verified). Android forces debuggable apps to verification, whatever is requested
(PackageDexOptimizer / ART Service: "We force vmSafeMode on debuggable apps as well").

Usage (from the repository root):  python3 tools/extract_dexopt_status.py
Output: 6_results_data/inputs/dexopt_status_series.csv (provenance: dexopt_status.PROVENANCE.md)
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
RUN_TABLES = {
    "series1": "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": "run_table_extended_final_ykush_series3_2026-08-17.csv",
}


def status(path):
    """(requested filter, status of the app's own entry) from one dexopt_state.txt."""
    txt = path.read_text(errors="replace")
    req = re.search(r"^requested_filter=(.*)$", txt, re.M)
    pkg = re.search(r"^package=(.*)$", txt, re.M)
    if not (req and pkg) or f"[{pkg.group(1)}]" not in txt:
        return (req.group(1) if req else ""), ""
    own = txt.split(f"[{pkg.group(1)}]", 1)[1].split("\n  [", 1)[0]       # the app's block, not the next package's
    st = re.search(r"\[status=([^\]]+)\]", own)
    return req.group(1), (st.group(1) if st else "")


def main():
    rows, missing = [], 0
    for batch, rt in RUN_TABLES.items():
        for r in csv.DictReader(open(ROOT / "6_results_data" / "run_tables" / rt)):
            if not r.get("run_id"):
                continue
            f = next((AR_OUT / r["run_id"]).rglob("dexopt_state.txt"), None)
            if f is None:
                missing += 1
                continue
            req, st = status(f)
            rows.append([batch, r["phone"], r["app"], r["variant"], r["rep"], req, st])
    out = ROOT / "6_results_data" / "inputs" / "dexopt_status_series.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["batch", "phone", "app", "variant", "rep", "requested_filter", "status"])
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} cells; {missing} runs without dexopt_state.txt")


if __name__ == "__main__":
    main()
