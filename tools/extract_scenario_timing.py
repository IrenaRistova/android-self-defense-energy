#!/usr/bin/env python3
"""How long each Appium scenario run took, in every series cell with a run.

During a cell, the Appium workload appends one JSON line per scenario run to the run's
appium_workload_coverage.jsonl (Android Runner output, data/<phone folder>/<APK slug>/). The line holds
the scenario's name, its wall-clock duration as the workload timed it (scenario_wall_seconds, laptop
clock, millisecond resolution) and its start and end stamps (laptop clock, whole seconds). The run-table
builder (tools/build_run_table_extended.py) takes the active phase from the same file: from the first
scenario's start to the last scenario's end.

This writes one row per line, for every series cell whose run table row names a run and whose run holds
exactly one coverage file for the cell's build. Every analysed cell must have as many rows as the run
table's scenarios_completed; tools/check_data_complete.py checks that.

Usage (from the repository root):  python3 tools/extract_scenario_timing.py
Output: 6_results_data/inputs/scenario_timing_series.csv (provenance: scenario_timing.PROVENANCE.md)
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
RUN_TABLES = {
    "series1": "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": "run_table_extended_final_ykush_series3_2026-08-17.csv",
}
PHONE_DIR = {"pixel3": "Pixel 3-W", "pixel6": "Pixel 6-W", "pixel9": "Pixel 9-W"}
COLUMNS = ["batch", "phone", "app", "variant", "rep", "seq", "scenario_name", "wall_s", "ts"]


def coverage_file(r):
    """The cell's coverage file: the one under the phone's folder whose APK slug names app_variant."""
    d = AR_OUT / r["run_id"] / "data" / PHONE_DIR[r["phone"]]
    hits = [p for p in d.glob("*/appium_workload_coverage.jsonl") if f"{r['app']}_{r['variant']}" in p.parent.name]
    return hits[0] if len(hits) == 1 else None, len(hits)


def main():
    rows, cells, no_file, ambiguous = [], 0, [], []
    for batch, rt in RUN_TABLES.items():
        for r in csv.DictReader(open(ROOT / "6_results_data" / "run_tables" / rt, newline="")):
            if not r.get("run_id"):
                continue
            f, n = coverage_file(r)
            if f is None:
                (ambiguous if n > 1 else no_file).append((batch, r["phone"], r["app"], r["variant"], r["rep"]))
                continue
            cells += 1
            seq = 0
            for line in f.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                seq += 1
                rows.append([batch, r["phone"], r["app"], r["variant"], r["rep"], seq, e.get("scenario_name", ""),
                             e.get("scenario_wall_seconds", ""), e.get("ts", "")])
    out = ROOT / "6_results_data" / "inputs" / "scenario_timing_series.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} scenario runs in {cells} cells; "
          f"{len(no_file)} runs without a coverage file for the cell's build; {len(ambiguous)} with more than one")
    for k in (no_file + ambiguous)[:20]:
        print("  ", k)


if __name__ == "__main__":
    main()
