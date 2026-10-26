#!/usr/bin/env python3
"""Per-cell offset between the phone's clock and the laptop's, and the phase energies re-integrated with it.

Why. The run-table builder (tools/build_run_table_extended.py) places the passive/active boundary at the first
scenario's start and the active phase's end at the last scenario's end, both stamped by the workload script on the
LAPTOP's clock, and compares them directly with the battery samples, which carry the PHONE's clock. Nothing
reconciles the two, and the Pixel 3's clock drifted about 2 s behind the laptop's over the series.

How the offset is measured, per cell. Android Runner logs, with millisecond laptop timestamps, the moment it starts
the battery profiler: the line `AndroidRunner.Adb:<device>: "am start-foreground-service -n
"com.example.batterymanager_utility/...DataCollectionService" ... returned` in the run's experiment.log (local time,
Europe/Amsterdam). The battery service's first sample, on the phone's clock, is the cell's t = 0. So

    theta_s = first battery sample (phone clock) - that log line (laptop clock, converted to UTC)

is the phone-minus-laptop offset. The log line is written when the command returns, and on the phone the first
sample follows the service start by about 20 ms (checked in the cell logcats), so the two delays are small. Checked
independently (2026-10-25): linkhub's first scenario sends a share intent that the phone logs; that log time minus the
laptop's scenario stamp correlates with theta_s at r = 0.96 / 0.94 / 0.93 (Pixel 3 / 6 / 9), and the remainder is
0.06-1.1 s (median 0.5-0.7 s): the whole-second truncation of the stamp. So theta_s is the clock offset to about 0.1 s.

The corrected phases. The builder's boundaries ws (first scenario start) and we (last scenario end) are recovered
exactly from the run table (ws = t0 + duration_pre_workload_s, we = ws + duration_workload_s) and moved onto the
phone's clock: ws' = ws + theta, we' = we + theta. The energies are then re-integrated from the cell's raw battery
samples with the builder's own loader and trapezoid rule (samples inside each window), so that with theta = 0 they
reproduce the run table: the *_check columns, which the notebook compares.

Usage (from the repository root):  python3 tools/extract_clock_offset.py
Output: 6_results_data/inputs/clock_offset_series.csv (one row per series cell with a run and battery samples)
"""
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_run_table_extended as B  # noqa: E402  (the builder's own loader and integration)

INPUTS = ROOT / "6_results_data" / "inputs"
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
RUN_TABLES = {  # thesis batch -> run table (the file tag is not the thesis name)
    "series1": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_series3_2026-08-17.csv",
}
LOCAL = ZoneInfo("Europe/Amsterdam")       # the laptop's zone; experiment.log stamps local time
START_PROF = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}):DEBUG:PluginHandler:Batterymanager: (.+?) \(([\d.]+:\d+),.*\): Start profiling")
FGS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}):DEBUG:AndroidRunner\.Adb:([\d.]+:\d+): "am start-foreground-service -n '
                 r'"com\.example\.batterymanager_utility')


def host_ms(stamp):
    return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=LOCAL).timestamp() * 1000)


def profiler_start(log_path, alias):
    """(start-profiling line, start-foreground-service returned) in UTC epoch ms, for this phone's alias."""
    t_sp, addr = None, None
    with open(log_path, errors="replace") as fh:
        for line in fh:
            if t_sp is None:
                m = START_PROF.match(line)
                if m and m.group(2) == alias:
                    t_sp, addr = host_ms(m.group(1)), m.group(3)
            else:
                m = FGS.match(line)
                if m and m.group(2) == addr:
                    return t_sp, host_ms(m.group(1))
    return t_sp, None


def main():
    cols = ["batch", "phone", "app", "variant", "rep", "cell_start_ts_utc", "run_id", "t0_ms", "t_last_ms",
            "host_profiling_start_ms", "host_fgs_returned_ms", "theta_s", "ws_ms", "we_ms",
            "pre_j_check", "wl_j_check", "pre_j_corr", "wl_j_corr", "td_j_corr",
            "active_reaches_last_sample", "active_reaches_last_sample_corr",
            "e_active_after_true_end_j", "samples_active_after_true_end", "nondischarging_samples_after_true_end",
            "nondischarging_j_after_true_end"]
    rows, skipped = [], 0
    for batch, rt in RUN_TABLES.items():
        for r in csv.DictReader(open(rt)):
            rid = r.get("run_id", "")
            if not rid or not r.get("duration_pre_workload_s") or not r.get("duration_workload_s"):
                skipped += 1
                continue
            run_dir = AR_OUT / rid
            subject_dir, _, _ = B.find_subject_dir(run_dir, B.WIFI_ALIAS.get(r["phone"]))
            bm_csv = B.find_bm_csv(subject_dir)[0] if subject_dir else None
            bm = B.load_bm_samples(bm_csv, r["phone"]) if bm_csv else None
            log = run_dir / "experiment.log"
            if bm is None or len(bm) < 2 or not log.is_file():
                skipped += 1
                continue
            t_sp, t_fgs = profiler_start(log, B.WIFI_ALIAS[r["phone"]])
            t0, t_last = int(bm["ts_ms"].iloc[0]), int(bm["ts_ms"].iloc[-1])
            ws = t0 + round(float(r["duration_pre_workload_s"]) * 1000)
            we = ws + round(float(r["duration_workload_s"]) * 1000)
            theta = None if t_fgs is None else (t0 - t_fgs) / 1000.0
            tz = lambda lo, hi: B.trapz_over_window(bm, lo, hi) if hi > lo else 0.0
            out = dict(batch=batch, phone=r["phone"], app=r["app"], variant=r["variant"], rep=r["rep"],
                       cell_start_ts_utc=r["cell_start_ts_utc"], run_id=rid, t0_ms=t0, t_last_ms=t_last,
                       host_profiling_start_ms=t_sp if t_sp else "", host_fgs_returned_ms=t_fgs if t_fgs else "",
                       theta_s="" if theta is None else f"{theta:.3f}", ws_ms=ws, we_ms=we,
                       pre_j_check=tz(t0, max(ws, t0)), wl_j_check=tz(max(ws, t0), min(we, t_last)),
                       active_reaches_last_sample=int(we >= t_last))
            if theta is not None:
                ws2 = min(max(ws + round(theta * 1000), t0), t_last)
                we2 = min(max(we + round(theta * 1000), t0), t_last)
                seg = bm[(bm["ts_ms"] >= we2) & (bm["ts_ms"] <= min(we, t_last))]
                nd = seg[seg["status"] != 3] if "status" in seg else seg.iloc[0:0]
                out.update(pre_j_corr=tz(t0, ws2), wl_j_corr=tz(ws2, we2), td_j_corr=tz(we2, t_last),
                           active_reaches_last_sample_corr=int(we + round(theta * 1000) >= t_last),
                           e_active_after_true_end_j=tz(we2, min(we, t_last)) if min(we, t_last) > we2 else 0.0,
                           samples_active_after_true_end=len(seg), nondischarging_samples_after_true_end=len(nd),
                           nondischarging_j_after_true_end=float((nd["power_W"] * 0.1).sum()))   # ~100 ms per sample
            rows.append({c: (f"{v:.6f}" if isinstance(v, float) else v) for c, v in out.items()})
    out_path = INPUTS / "clock_offset_series.csv"
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_path.relative_to(ROOT)}: {len(rows)} cells; {skipped} run-table rows skipped (no run, window or samples)")


if __name__ == "__main__":
    main()
