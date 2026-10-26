#!/usr/bin/env python3
"""Per-cell launch timing for the three series batches, from the raw per-cell logcat.

For every captured cell listed in 6_results_data/inputs/logcat_activity_<batch>.csv this reads
the cell's full logcat (tools/sweep_logs/logcat/<logfile>) and the first battery sample of its
Android Runner run (android-runner/examples/batterymanager/output/<run_id>/.../batterymanager/
logcat_*.csv; run_id from the batch's run table) and writes one row:

  ttid_ms                 "ActivityTaskManager: Displayed <pkg>/<activity> ...: +XsYms" for the
                          first display of the subject app: the cold launch, time to initial display
  app_process_starts      am_proc_start events whose process name is exactly the package
  launch_start_rel_s      the first "START u0 ... cmp=<pkg>/" (Android Runner's monkey launch),
  proc_start_rel_s        the first am_proc_start of the package,
  first_display_rel_s     the first Displayed line,
  uia2_start_rel_s        am_proc_start of io.appium.uiautomator2.server,
  appium_start_rel_s      the second START of the package (Appium's activity start),
                          each in seconds relative to the first battery sample, i.e. the start of
                          the energy measurement. Negative = before measurement began.
  appium_start_action     the intent action of that second START (MAIN for Appium's session start)
  appium_start_outcome    what Android did with it, from the window-manager events of the package
                          within 0.05 s of the START line (logged just before or after it):
                          "delivered to the running activity" (wm_new_intent: no new activity, no
                          new process), "new activity instance" (wm_create_activity, in the running
                          process), or "no activity event" (neither: the task was brought to the front)

Logcat timestamps (logcat -v epoch) and battery-sample timestamps (BatteryManager, epoch ms) are
both taken on the phone, so they share one clock.

Usage (from the repository root):  python3 tools/extract_launch_timing.py
Output: 6_results_data/inputs/launch_timing_series.csv
"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "6_results_data" / "inputs"
LOGCAT = ROOT / "tools" / "sweep_logs" / "logcat"
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
RUN_TABLES = {  # thesis batch -> run table (the file tag is not the thesis name)
    "series1": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_series3_2026-08-17.csv",
}
LINE = re.compile(r"^\s*(\d+\.\d+)\s+\d+\s+\d+\s+\w\s+(.*)$")
DUR = re.compile(r"(?:(\d+)s)?(\d+)ms$")


def parse_logcat(path, pkg):
    out = {"t_launch": None, "t_proc": None, "t_disp": None, "ttid_ms": None, "t_uia2": None,
           "t_start2": None, "n_proc": 0, "n_start": 0, "act2": ""}
    wm = []                     # (time, event) of wm_new_intent / wm_create_activity for the package
    for ln in path.read_text(errors="replace").splitlines():
        m = LINE.match(ln)
        if not m:
            continue
        t, msg = float(m.group(1)), m.group(2)
        if "ActivityTaskManager: START u0" in msg and f"cmp={pkg}/" in msg:
            out["n_start"] += 1
            if out["n_start"] == 1:
                out["t_launch"] = t
            elif out["n_start"] == 2:
                out["t_start2"] = t
                a = re.search(r"act=([\w.]+)", msg)
                out["act2"] = a.group(1).rsplit(".", 1)[-1] if a else ""
        elif (msg.startswith("wm_new_intent:") or msg.startswith("wm_create_activity:")) and f",{pkg}/" in msg:
            wm.append((t, msg.split(":", 1)[0]))
        elif msg.startswith("am_proc_start:"):
            fields = msg.split("[", 1)[1].split(",")
            name = fields[3] if len(fields) > 3 else ""
            if name == pkg:
                out["n_proc"] += 1
                if out["t_proc"] is None:
                    out["t_proc"] = t
            elif name == "io.appium.uiautomator2.server" and out["t_uia2"] is None:
                out["t_uia2"] = t
        elif out["ttid_ms"] is None and msg.startswith(f"ActivityTaskManager: Displayed {pkg}/"):
            d = DUR.search(msg.rsplit("+", 1)[-1].strip())
            if d:
                out["ttid_ms"] = int(d.group(1) or 0) * 1000 + int(d.group(2))
                out["t_disp"] = t
    out["outcome2"] = ""
    if out["t_start2"] is not None:
        near = {e for tt, e in wm if abs(tt - out["t_start2"]) <= 0.05}
        out["outcome2"] = ("new activity instance" if "wm_create_activity" in near else
                           "delivered to the running activity" if "wm_new_intent" in near else "no activity event")
    return out


def first_battery_sample(run_id):
    for f in (AR_OUT / run_id / "data").glob("*/*/batterymanager/logcat_*.csv"):
        with open(f) as fh:
            rows = csv.DictReader(fh)
            first = next(rows, None)
            if first and first.get("Timestamp"):
                return int(first["Timestamp"]) / 1000.0
    return None


def main():
    pkg = {r["app"]: r["package"] for r in csv.DictReader(open(INPUTS / "app_characteristics.csv"))}
    cols = ["batch", "phone", "app", "variant", "rep", "logfile", "run_id", "ttid_ms", "app_process_starts",
            "launch_start_rel_s", "proc_start_rel_s", "first_display_rel_s", "uia2_start_rel_s", "appium_start_rel_s",
            "appium_start_action", "appium_start_outcome"]
    rows, missing = [], 0
    for batch, rt in RUN_TABLES.items():
        run_id = {(r["phone"], r["app"], r["variant"], r["rep"]): r["run_id"] for r in csv.DictReader(open(rt))}
        for r in csv.DictReader(open(INPUTS / f"logcat_activity_{batch}.csv")):
            k = (r["phone"], r["app"], r["variant"], r["rep"])
            path = LOGCAT / r["logfile"]
            rid = run_id.get(k, "")
            if not path.exists() or not rid or r["app"] not in pkg:
                missing += 1
                continue
            o = parse_logcat(path, pkg[r["app"]])
            bm0 = first_battery_sample(rid)
            rel = lambda t: "" if t is None or bm0 is None else f"{t - bm0:.3f}"
            rows.append([batch, *k[:2], r["variant"], r["rep"], r["logfile"], rid,
                         "" if o["ttid_ms"] is None else o["ttid_ms"], o["n_proc"],
                         rel(o["t_launch"]), rel(o["t_proc"]), rel(o["t_disp"]), rel(o["t_uia2"]), rel(o["t_start2"]),
                         o["act2"], o["outcome2"]])
    out = INPUTS / "launch_timing_series.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} cells; {missing} listed cells skipped (no logcat, run or package)")
    sys.exit(0)


if __name__ == "__main__":
    main()
