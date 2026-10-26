#!/usr/bin/env python3
"""Is every derived input complete, and built from the right raw files?

Run it before rebuilding the notebooks, and before explaining any gap in the data.

WHY IT EXISTS (2026-10-05)
--------------------------
The series-1 log table (`logcat_activity_series1.csv`) was generated on 12 August while series 1 was
still running, and was never regenerated. 283 of its cells looked unlogged. The raw-data manifest, built
from the same table, left 289 captures out. Nothing compared the derived files with the raw data. This
script does, and it fails loudly. See `6_results_data/DATA_MAP.md`.

WHAT IT CHECKS
--------------
Every check prints PASS, FAIL or SKIPPED, and every check can fail. [raw] means it needs the raw data.

A  run tables. 1,200 cells per batch, one row per cell. Every valid run's Android Runner folder exists
   and has battery samples [raw]. Invalid runs without samples are listed, not failed: no result uses
   them.
B  log captures [raw]. For every cell, B looks at every capture on disk with its key, in any session:
   it does not trust the sweep-log index. The capture whose time span overlaps the measured run's
   battery samples (both on the phone's clock) must be the one `logcat_activity_<batch>.csv` lists.
   B fails when:
   - a capture exists but the table lacks the cell;
   - the table lists a different capture;
   - the table lists a capture that overlaps nothing measured;
   - the table lists a cell twice.
   B also checks the index itself: every capture of a measured run must be attributed to that batch's
   tag in `6_results_data/sweep_log_index.csv`. Until 2026-10-05 the pilot's first session (20260804_025319,
   81 captures) was missing from the index. Its session log names its tag, but only wrapper logs were
   read, so 76 pilot cells looked unlogged.
C  the per-cell inputs the notebooks join, against the analysed cells:
   - `dexopt_status_series`, `post_charge_k_window`, `mechanism_cells_series`: every analysed cell;
   - `launch_timing_series`: every analysed cell whose capture resolves the application's process;
   - `denial_timing_series`: every analysed cell with denials, with the same totals as the log tables.
D  the raw manifest (`raw_manifest.csv.gz`) lists [raw for the file checks]:
   - every capture the index attributes to each batch;
   - every capture the log tables use, marked used;
   - every run's battery samples.

Section C loads `thesis_lib`, which needs pandas and scipy. A Python without them re-runs the script
under `6_results_analysis/.venv/bin/python`; without that environment, C fails and says so.

Usage (from the repository root):
    python3 tools/check_data_complete.py                 # everything
    python3 tools/check_data_complete.py --no-raw        # only the checks that need no raw data
    python3 tools/check_data_complete.py --inputs DIR    # check another copy of 6_results_data/inputs
    python3 tools/check_data_complete.py --index FILE    # check another copy of the sweep-log index

Exit status: 0 if every check that ran passed; 1 if any failed.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "6_results_analysis" / ".venv" / "bin" / "python"
try:  # section C needs the analysis environment; re-run under it once, never in a loop
    import pandas, scipy  # noqa: E401, F401
except ImportError:
    if VENV_PY.exists() and not os.environ.get("CHECK_DATA_COMPLETE_REEXEC"):
        os.environ["CHECK_DATA_COMPLETE_REEXEC"] = "1"
        print(f"(this Python lacks pandas or scipy; re-running under {VENV_PY.relative_to(ROOT)})", flush=True)
        os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "6_results_analysis" / "notebooks"))
from logcat_app_parser import cell_key  # noqa: E402  capture filename -> phone, app, variant, rep

DATA = ROOT / "6_results_data"
LOGCAT = ROOT / "tools" / "sweep_logs" / "logcat"
AR = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
INDEX = ROOT / "6_results_data" / "sweep_log_index.csv"
MANIFEST = DATA / "raw_manifest.csv.gz"
BATCHES = {  # thesis name: (sweep tag, run table)
    "pilot": ("final_ykush_2026-08-04", "run_table_extended_final_ykush_2026-08-04.csv"),
    "series1": ("final_ykush_batch2_2026-08-10", "run_table_extended_final_ykush_batch2_2026-08-10.csv"),
    "series2": ("final_ykush_series2_2026-08-14", "run_table_extended_final_ykush_series2_2026-08-14.csv"),
    "series3": ("final_ykush_series3_2026-08-17", "run_table_extended_final_ykush_series3_2026-08-17.csv"),
}
SERIES = ("series1", "series2", "series3")
KEY = ("phone", "app", "variant", "rep")
TS = re.compile(rb"^\s*(\d{10}\.\d+)\s")

results = []  # (check, status, detail)


def report(check, ok, detail, skipped=False):
    results.append((check, "SKIPPED" if skipped else "PASS" if ok else "FAIL", detail))


def key_of(row):
    return tuple(str(row[k]) for k in KEY)


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def battery_csv(run_id):
    return next((AR / run_id / "data").glob("*/*/batterymanager/logcat_*.csv"), None) if run_id else None


def battery_span(run_id):
    """First and last battery-sample time of a run (epoch s, the phone's clock)."""
    f = battery_csv(run_id)
    if f is None:
        return None
    with open(f, "rb") as fh:
        head = fh.read(65536).decode(errors="replace").splitlines()
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 65536))
        tail = fh.read().decode(errors="replace").splitlines()
    rows = list(csv.reader(head))
    if not rows or "Timestamp" not in rows[0]:
        return None
    i = rows[0].index("Timestamp")
    first = next((int(r[i]) / 1000 for r in rows[1:] if len(r) > i and r[i].isdigit()), None)
    last = next((int(r[i]) / 1000 for r in reversed(list(csv.reader(tail))) if len(r) > i and r[i].isdigit()), None)
    return (first, last) if first and last else None


def capture_span(path):
    """First and last timestamp of a logcat capture (epoch s, the phone's clock)."""
    first = last = None
    with open(path, "rb") as fh:
        for _ in range(2000):
            ln = fh.readline()
            if not ln:
                break
            m = TS.match(ln)
            if m:
                first = float(m.group(1))
                break
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 65536))
        for ln in reversed(fh.read().splitlines()):
            m = TS.match(ln)
            if m:
                last = float(m.group(1))
                break
    return (first, last) if first is not None and last is not None else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", type=Path, default=DATA / "inputs")
    ap.add_argument("--no-raw", action="store_true")
    ap.add_argument("--index", type=Path, default=INDEX)
    args = ap.parse_args()
    inputs = args.inputs
    index_path = args.index
    raw = not args.no_raw and LOGCAT.is_dir() and AR.is_dir()
    if not args.no_raw and not raw:
        print("raw data not found: running only the checks that need none")

    # ---- A: run tables
    runs, valid, bad = {}, set(), []
    for batch, (_, rt) in BATCHES.items():
        rows = read_csv(DATA / "run_tables" / rt)
        keys = [key_of(r) for r in rows]
        if len(rows) != 1200 or len(set(keys)) != len(keys):
            bad.append(f"{batch}: {len(rows)} rows, {len(keys) - len(set(keys))} duplicate keys")
        for r in rows:
            runs[(batch, *key_of(r))] = r.get("run_id", "")
            if r.get("scientifically_valid") == "True":
                valid.add((batch, *key_of(r)))
    report("A run tables: 1,200 cells per batch, one row each", not bad, "; ".join(bad) or "4 x 1,200")
    if raw:
        no_battery = [k for k, rid in runs.items() if rid and battery_csv(rid) is None]
        fatal = [k for k in no_battery if k in valid]
        report("A every valid run has its Android Runner battery samples", not fatal,
               (f"{len(fatal)} valid runs without battery samples, e.g. {fatal[:3]}" if fatal
                else f"{sum(1 for k in valid if runs.get(k))} valid runs")
               + (f" (invalid runs without samples, not used by any result: {len(no_battery) - len(fatal)}: "
                  f"{[k for k in no_battery if k not in valid]})" if len(no_battery) > len(fatal) else ""))
    else:
        report("A every valid run has its Android Runner battery samples", True, "needs the raw data", skipped=True)

    # ---- B: each log table against the raw captures
    index = defaultdict(list)
    if index_path.is_file():
        for r in read_csv(index_path):
            if r["kind"] == "logcat":
                index[r["sweep_tag"]].append(r["file"])
    on_disk = defaultdict(list)                       # every capture on disk, by cell key, any session
    if raw:
        for p in LOGCAT.glob("logcat_*.log"):
            k = cell_key(p)
            if k:
                on_disk[(k["phone"], k["app"], k["variant"], k["rep"])].append(p.name)
    tables = {}
    for batch in BATCHES:
        p = inputs / f"logcat_activity_{batch}.csv"
        tables[batch] = read_csv(p) if p.is_file() else []
    for batch, (tag, _) in BATCHES.items():
        rows = tables[batch]
        keys = [key_of(r) for r in rows]
        dup = len(keys) - len(set(keys))
        if not raw:
            report(f"B {batch}: one log row per cell", dup == 0, f"{dup} cells listed twice" if dup else f"{len(rows)} rows")
            continue
        in_index = set(index.get(tag, []))
        listed = {key_of(r): r.get("logfile", "") for r in rows}
        not_indexed = []
        missing, wrong, stray, none = [], [], [], []
        for (b, *k), rid in runs.items():
            if b != batch:
                continue
            k = tuple(k)
            span = battery_span(rid)
            if span is None:
                continue
            best = None
            for name in on_disk.get(k, []):
                c = capture_span(LOGCAT / name)
                if c:
                    ov = min(c[1], span[1]) - max(c[0], span[0])
                    if ov > 0 and (best is None or ov > best[0]):
                        best = (ov, name)
            have = listed.get(k)
            if best and best[1] not in in_index:
                not_indexed.append(best[1])
            if best and not have:
                missing.append(k)
            elif best and have != best[1]:
                wrong.append((k, have, best[1]))
            elif not best and have:
                stray.append((k, have))
            elif not best:
                none.append(k)
        ok = not (missing or wrong or stray or dup)
        detail = (f"{len(rows)} rows; {len(missing)} cells have a capture of the measured run but no row"
                  f"{', e.g. ' + str(missing[:2]) if missing else ''}; {len(wrong)} list another capture"
                  f"{', e.g. ' + str(wrong[:1]) if wrong else ''}; {len(stray)} list a capture that overlaps "
                  f"nothing measured; {dup} listed twice. (Cells with no capture of the measured run: {len(none)}.)")
        report(f"B {batch}: the log table lists, for every cell, the capture of its measured run", ok, detail)
        report(f"B {batch}: the sweep-log index attributes every capture of a measured run to {tag}", not not_indexed,
               f"{len(not_indexed)} captures of measured runs missing from the index"
               + (f", e.g. {not_indexed[:2]}" if not_indexed else ""))

    # ---- C: the per-cell inputs the notebooks join, against the analysed cells
    try:
        import thesis_lib as T
        df, _ = T.apply_filters(T.load_raw())
        analysed = {(r.batch, r.phone, r.app, r.variant, str(r.rep)) for r in df.itertuples() if r.batch in SERIES}
    except Exception as exc:  # noqa: BLE001
        analysed = None
        report("C analysed cells (thesis_lib filter chain)", False,
               f"could not load: {exc} (run it with {VENV_PY.relative_to(ROOT)})")
    if analysed is not None:
        def cover(name, file, need, batch_col=True):
            p = inputs / file
            if not p.is_file():
                report(f"C {name}", False, f"{file} not found")
                return
            have = {(r["batch"], *key_of(r)) if batch_col else key_of(r) for r in read_csv(p)}
            lack = sorted(need - have)
            report(f"C {name}", not lack, f"{len(need) - len(lack)} of {len(need)} required cells present"
                   + (f"; missing e.g. {lack[:3]}" if lack else ""))
        cover("dexopt_status_series covers every analysed cell", "dexopt_status_series.csv", analysed)
        cover("post_charge_k_window covers every analysed cell", "post_charge_k_window.csv", analysed)
        cover("mechanism_cells_series covers every analysed cell", "mechanism_cells_series.csv", analysed)
        cover("clock_offset_series covers every analysed cell", "clock_offset_series.csv", analysed)
        resolved = {(b, *key_of(r)) for b in SERIES for r in tables[b] if r.get("resolved") == "1"} & analysed
        cover("launch_timing_series covers every analysed cell with a resolved capture", "launch_timing_series.csv", resolved)
        audit = {(b, *key_of(r)): int(float(r.get("auditd_lines") or 0)) for b in SERIES for r in tables[b]}
        need = {k for k, v in audit.items() if v > 0} & analysed
        p = inputs / "denial_timing_series.csv"
        dt = {(r["batch"], *key_of(r)): int(r["auditd_lines"]) for r in read_csv(p)} if p.is_file() else {}
        lack = sorted(k for k in need if k not in dt)
        differ = sorted(k for k in need if k in dt and dt[k] != audit[k])
        report("C denial_timing_series covers every analysed cell with denials, same totals", not (lack or differ),
               f"{len(need) - len(lack)} of {len(need)} present; {len(differ)} totals differ"
               + (f"; missing e.g. {lack[:3]}" if lack else ""))
        # variant_permissions is per build, not per cell: every analysed build, read from the APK its cells ran
        ran = defaultdict(set)
        for b in SERIES:
            for r in read_csv(DATA / "run_tables" / BATCHES[b][1]):
                if (b, *key_of(r)) in analysed:
                    ran[(r["app"], r["variant"])].add(r["apk_sha256"])
        p = inputs / "variant_permissions.csv"
        perm = defaultdict(set)
        for r in (read_csv(p) if p.is_file() else []):
            perm[(r["app"], r["variant"])].add(r["apk_sha256"])
        lack = sorted(k for k in ran if k not in perm)
        other = sorted(k for k in ran if k in perm and perm[k] != ran[k])
        report("C variant_permissions covers every analysed build, read from the APK its cells ran", not (lack or other),
               f"{len(ran) - len(lack)} of {len(ran)} builds present; {len(other)} read from another APK"
               + (f"; missing e.g. {lack[:3]}" if lack else "") + (f"; other APK e.g. {other[:3]}" if other else ""))

    # ---- D: the raw manifest
    if not MANIFEST.is_file():
        report("D raw manifest", False, f"{MANIFEST.name} not found")
    else:
        with gzip.open(MANIFEST, "rt", newline="") as fh:
            man = list(csv.DictReader(fh))
        used_col = "used_by_results" in (man[0] if man else {})
        listed = {r["path"]: r.get("used_by_results", "") for r in man}
        need_used = {f"tools/sweep_logs/logcat/{r['logfile']}" for b in BATCHES for r in tables[b] if r.get("logfile")}
        lack_used = sorted(p for p in need_used if p not in listed or (used_col and listed[p] != "1"))
        need_all = {f"tools/sweep_logs/logcat/{n}" for b, (tag, _) in BATCHES.items() for n in index.get(tag, [])}
        lack_all = sorted(p for p in need_all if p not in listed)
        report("D manifest lists every capture the log tables use, marked used", used_col and not lack_used,
               ("no used_by_results column; " if not used_col else "") + f"{len(need_used) - len(lack_used)} of {len(need_used)}"
               + (f"; missing e.g. {lack_used[:2]}" if lack_used else ""))
        report("D manifest lists every capture the index attributes to the four batches", not lack_all,
               f"{len(need_all) - len(lack_all)} of {len(need_all)}" + (f"; missing e.g. {lack_all[:2]}" if lack_all else ""))
        if raw:
            need_bat = {str(battery_csv(rid).relative_to(ROOT)) for rid in set(runs.values()) if rid and battery_csv(rid)}
            lack_bat = sorted(p for p in need_bat if p not in listed)
            report("D manifest lists every run's battery samples", not lack_bat,
                   f"{len(need_bat) - len(lack_bat)} of {len(need_bat)}")

    width = max(len(c) for c, _, _ in results)
    for c, s, d in results:
        print(f"{s:7s}  {c:{width}s}  {d}")
    failed = [c for c, s, _ in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed or skipped; {len(failed)} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
