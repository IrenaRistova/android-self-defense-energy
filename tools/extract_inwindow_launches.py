#!/usr/bin/env python3
"""Every cold launch that happened while energy was being measured (series batches), with its energy.

The first launch of each cell precedes the energy measurement (Android Runner launches the app,
then starts the profilers). Some cells relaunch the app inside the measured window: poetskingdom's
workload terminates and relaunches it by design, and other apps occasionally restart. For each such
launch this joins the full logcat (process start; "Displayed ... +Xms", the time to initial display)
with the cell's battery samples, and integrates power over:

  e_launch_j          launch request (display time minus TTID) to first display
  e_launch_plus1s_j   the same, extended 1 s past the first display (the fuel gauge's reading lags:
                      it updates every ~220-330 ms)
  p_before_w          mean power over the 2 s before the launch request
  e_launch_early_j,   the launch window shifted 0.33 s earlier / later: one fuel-gauge update, to test
  e_launch_late_j     whether the result depends on where the coarse readings fall
  samples_in_launch   battery samples strictly inside the launch window
  gauge_updates_in_launch   changes of the current reading inside the launch window
  gauge_update_ms     median interval between changes of the current reading over the whole cell

Power is |current_now| x voltage, integrated with the trapezoid rule as in the run-table builder, but
over exactly the window: the power at each edge is interpolated linearly between the two samples
around it. (A launch spans only a few samples, about 0.1 s apart; integrating only the samples inside
would leave out up to one sample interval of a window that is itself 0.3-0.8 s long.)
Only launches fully inside the samples are kept (request >= 2 s after the first sample, display + 1 s
before the last).

Usage (from the repository root):  python3 tools/extract_inwindow_launches.py
Output: 6_results_data/inputs/inwindow_launches_series.csv (provenance: inwindow_launches.PROVENANCE.md)
"""
import bisect
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "6_results_data" / "inputs"
LOGCAT = ROOT / "tools" / "sweep_logs" / "logcat"
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
LINE = re.compile(r"^\s*(\d+\.\d+)\s+\d+\s+\d+\s+\w\s+(.*)$")
DUR = re.compile(r"(?:(\d+)s)?(\d+)ms$")


def cold_launches(path, pkg):
    """(process start, display time, TTID ms) for every process start followed by a display within 5 s."""
    ev = []
    for ln in path.read_text(errors="replace").splitlines():
        m = LINE.match(ln)
        if not m:
            continue
        t, msg = float(m.group(1)), m.group(2)
        if msg.startswith("am_proc_start:"):
            f = msg.split("[", 1)[1].split(",")
            if len(f) > 3 and f[3] == pkg:
                ev.append(("proc", t, None))
        elif msg.startswith(f"ActivityTaskManager: Displayed {pkg}/"):
            d = DUR.search(msg.rsplit("+", 1)[-1].strip())
            if d:
                ev.append(("disp", t, int(d.group(1) or 0) * 1000 + int(d.group(2))))
    out = []
    for i, (k, t, _) in enumerate(ev):
        if k == "proc":
            nxt = next(((t2, ms) for k2, t2, ms in ev[i + 1:] if k2 == "disp" and 0 <= t2 - t < 5), None)
            if nxt:
                out.append((t, nxt[0], nxt[1]))
    return out


def battery(run_id):
    """[(t, power W, raw current reading)] for one run, in time order."""
    f = next((AR_OUT / run_id / "data").glob("*/*/batterymanager/logcat_*.csv"), None)
    if f is None:
        return []
    out = []
    for r in csv.DictReader(open(f)):
        try:
            cur = int(r["BATTERY_PROPERTY_CURRENT_NOW"])
            out.append((int(r["Timestamp"]) / 1000.0, abs(cur) / 1e6 * int(r["EXTRA_VOLTAGE"]) / 1e3, cur))
        except (KeyError, ValueError):
            continue
    return sorted(out)


def changes(samples, lo=float("-inf"), hi=float("inf")):
    """Times at which the current reading changed (a new fuel-gauge value), within (lo, hi]."""
    return [samples[i][0] for i in range(1, len(samples))
            if samples[i][2] != samples[i - 1][2] and lo < samples[i][0] <= hi]


def gauge_update_ms(samples):
    c = changes(samples)
    gaps = sorted(1000 * (b - a) for a, b in zip(c, c[1:]))
    return gaps[len(gaps) // 2] if gaps else None


def power_at(samples, ts, x):
    k = bisect.bisect_left(ts, x)
    if ts[k] == x:
        return samples[k][1]
    (t1, p1, _), (t2, p2, _) = samples[k - 1], samples[k]
    return p1 + (p2 - p1) * (x - t1) / (t2 - t1)


def energy(samples, ts, lo, hi):
    """Trapezoid integral of power over exactly [lo, hi], the power interpolated linearly at both edges."""
    if not ts[0] <= lo < hi <= ts[-1]:
        return None
    inner = [(t, p) for t, p, _ in samples[bisect.bisect_right(ts, lo):bisect.bisect_left(ts, hi)]]
    pts = [(lo, power_at(samples, ts, lo))] + inner + [(hi, power_at(samples, ts, hi))]
    return sum((t2 - t1) * (p1 + p2) / 2 for (t1, p1), (t2, p2) in zip(pts, pts[1:]))


def main():
    pkg = {r["app"]: r["package"] for r in csv.DictReader(open(INPUTS / "app_characteristics.csv"))}
    run_tables = {
        "series1": "run_table_extended_final_ykush_batch2_2026-08-10.csv",
        "series2": "run_table_extended_final_ykush_series2_2026-08-14.csv",
        "series3": "run_table_extended_final_ykush_series3_2026-08-17.csv",
    }
    rows = []
    for batch, rt in run_tables.items():
        run_id = {(r["phone"], r["app"], r["variant"], r["rep"]): r["run_id"]
                  for r in csv.DictReader(open(ROOT / "6_results_data" / "run_tables" / rt))}
        for r in csv.DictReader(open(INPUTS / f"logcat_activity_{batch}.csv")):
            k = (r["phone"], r["app"], r["variant"], r["rep"])
            path = LOGCAT / r["logfile"]
            if not path.exists() or r["app"] not in pkg or not run_id.get(k):
                continue
            launches = cold_launches(path, pkg[r["app"]])
            if len(launches) < 2:                     # the first launch always precedes the measurement
                continue
            samples = battery(run_id[k])
            if len(samples) < 2:
                continue
            ts = [s[0] for s in samples]
            gauge = gauge_update_ms(samples)
            t0, t1 = ts[0], ts[-1]
            for n, (tp, td, ms) in enumerate(launches, 1):
                lo = td - ms / 1000.0
                if lo < t0 + 2 or td + 1 > t1:
                    continue
                e, e1, pre = energy(samples, ts, lo, td), energy(samples, ts, lo, td + 1), energy(samples, ts, lo - 2, lo)
                ee, el = energy(samples, ts, lo - 0.33, td - 0.33), energy(samples, ts, lo + 0.33, td + 0.33)
                if None in (e, e1, pre, ee, el):
                    continue
                inside = bisect.bisect_left(ts, td) - bisect.bisect_right(ts, lo)
                rows.append([batch, *k[:2], r["variant"], r["rep"], n, ms, f"{e:.4f}", f"{e1:.4f}", f"{pre / 2:.4f}",
                             f"{ee:.4f}", f"{el:.4f}", inside, len(changes(samples, lo, td)),
                             "" if gauge is None else f"{gauge:.0f}"])
    out = INPUTS / "inwindow_launches_series.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["batch", "phone", "app", "variant", "rep", "launch_number", "ttid_ms",
                    "e_launch_j", "e_launch_plus1s_j", "p_before_w", "e_launch_early_j", "e_launch_late_j",
                    "samples_in_launch", "gauge_updates_in_launch", "gauge_update_ms"])
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} in-window cold launches")


if __name__ == "__main__":
    main()
