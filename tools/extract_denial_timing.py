#!/usr/bin/env python3
"""When the SELinux denials attributed to the app happen, relative to the measured windows (series batches).

The mechanism tables count, per cell, the `auditd` lines written by the app's own processes
(`tools/logcat_app_parser.py`: the processes `am_proc_start` reports under the app's own package; corrected
2026-10-05, when Google Play Services' processes stopped being counted as the app). This
splits the same lines by time, using the cell's first battery sample as zero and the run table's window
lengths:

  before        before the first energy sample (the launch and the post-launch hook)
  post_launch   [0, duration_pre_workload_s): the post-launch window
  workload      the next duration_workload_s seconds: the workload window
  after         anything later (teardown)

Every cell's total must equal the parser's count in logcat_activity_<batch>.csv (`auditd_lines`); a
mismatch stops the script, so the split can never silently drift from the counts the thesis reports.
Only cells with at least one such line are written (all other cells have none in every window).

Usage (from the repository root):  python3 tools/extract_denial_timing.py
Output: 6_results_data/inputs/denial_timing_series.csv (provenance: denial_timing.PROVENANCE.md)
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from logcat_app_parser import LINE_RE, app_package, resolve_app_pids  # noqa: E402  the same attribution as the counts

INPUTS = ROOT / "6_results_data" / "inputs"
LOGCAT = ROOT / "tools" / "sweep_logs" / "logcat"
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
RUN_TABLES = {
    "series1": "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": "run_table_extended_final_ykush_series3_2026-08-17.csv",
}


def first_sample_s(run_id):
    f = next((AR_OUT / run_id / "data").glob("*/*/batterymanager/logcat_*.csv"), None)
    if f is None:
        return None
    with open(f) as fh:
        row = next(csv.DictReader(fh), None)
    return int(row["Timestamp"]) / 1000.0 if row else None


def main():
    rows, mismatches = [], []
    for batch, rt in RUN_TABLES.items():
        cells = {(r["phone"], r["app"], r["variant"], r["rep"]): r
                 for r in csv.DictReader(open(ROOT / "6_results_data" / "run_tables" / rt))}
        for r in csv.DictReader(open(INPUTS / f"logcat_activity_{batch}.csv")):
            expected = int(float(r.get("auditd_lines") or 0))
            if expected == 0:
                continue
            k = (r["phone"], r["app"], r["variant"], r["rep"])
            cell = cells.get(k)
            path = LOGCAT / r["logfile"]
            if not cell or not cell.get("run_id") or not path.exists():
                continue
            t0 = first_sample_s(cell["run_id"])
            try:
                pre = float(cell["duration_pre_workload_s"])
                wl = float(cell["duration_workload_s"])
            except (TypeError, ValueError):
                continue
            if t0 is None:
                continue
            lines = path.read_text(errors="replace").splitlines()
            pids, _ = resolve_app_pids(lines, app_package(r["app"]))
            n = {"before": 0, "post_launch": 0, "workload": 0, "after": 0}
            for ln in lines:
                m = LINE_RE.match(ln)
                if not m or m.group("pid") not in pids or m.group("tag").strip() != "auditd":
                    continue
                t = float(m.group("ts")) - t0
                n["before" if t < 0 else "post_launch" if t < pre else "workload" if t < pre + wl else "after"] += 1
            total = sum(n.values())
            if total != expected:
                mismatches.append((batch, *k, total, expected))
            rows.append([batch, *k, total, n["before"], n["post_launch"], n["workload"], n["after"], f"{pre:.1f}", f"{wl:.1f}"])
    if mismatches:
        sys.exit(f"{len(mismatches)} cells differ from the parser's auditd_lines, e.g. {mismatches[:3]}")
    out = INPUTS / "denial_timing_series.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["batch", "phone", "app", "variant", "rep", "auditd_lines", "before", "post_launch", "workload", "after",
                    "post_launch_s", "workload_s"])
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} cells with denials; every total equals the parser's count")


if __name__ == "__main__":
    main()
