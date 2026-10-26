#!/usr/bin/env python3
"""Checksum manifest of the raw data behind every result: one row per file, with its size and
SHA-256, so that any archive of the raw data can be checked against what produced the results.

Covered (the four batches of the final experiment: pilot, series 1-3):
  ar_output   android-runner/examples/batterymanager/output/<run_id>/**, for every run_id in the
              four run tables (battery samples, Android Runner's timestamped log, scenario timings,
              CPU/memory samples, frame times, crash and session status)
  logcat      tools/sweep_logs/logcat/<logfile>, for every capture the sweep-log index
              (6_results_data/sweep_log_index.csv, tools/organise_sweep_logs.py) attributes to the batch's
              sweep tag: all of them, including superseded attempts and truncated captures, so an
              archive built from this list is complete. Corrected 2026-10-05: this used to list only
              the files named in logcat_activity_<batch>.csv, and the series-1 table was stale (built
              mid-batch on 2026-08-12), so 289 series-1 captures were missing from the manifest.
  sweep_logs  tools/sweep_logs/* (not the logcat folder): sweep, per-cell stream, Appium and build logs
  apks        every APK path in the four run tables (outside the repository)

Usage (from the repository root):  python3 tools/raw_data_manifest.py
Output: 6_results_data/raw_manifest.csv.gz  (columns: source, batch, path, bytes, sha256, used_by_results)
used_by_results: 1 for every Android Runner file of a run in the run tables, every capture that
logcat_activity_<batch>.csv uses (the capture of the measured run) and every APK; 0 for a capture of
the batch that no result uses (a superseded attempt, or one that stopped before measuring); blank for
the sweep logs.
Paths are relative to the repository root, except APKs (absolute, outside the repository).
"""
import csv
import gzip
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from organise_sweep_logs import resolve_logs_for_tag  # noqa: E402  the batch's captures, from the log index
DATA = ROOT / "6_results_data"
AR = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
LOGCAT = ROOT / "tools" / "sweep_logs" / "logcat"
SWEEP = ROOT / "tools" / "sweep_logs"
TAGS = {
    "pilot": "final_ykush_2026-08-04",
    "series1": "final_ykush_batch2_2026-08-10",
    "series2": "final_ykush_series2_2026-08-14",
    "series3": "final_ykush_series3_2026-08-17",
}
RUN_TABLES = {
    "pilot": "run_table_extended_final_ykush_2026-08-04.csv",
    "series1": "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": "run_table_extended_final_ykush_series3_2026-08-17.csv",
}


def sha256(p, buf=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while chunk := fh.read(buf):
            h.update(chunk)
    return h.hexdigest()


def main():
    rows, missing = [], []
    apks = {}
    for batch, f in RUN_TABLES.items():
        for r in csv.DictReader(open(DATA / "run_tables" / f)):
            if r.get("apk_path"):
                apks.setdefault(r["apk_path"], batch)
            rid = r.get("run_id")
            if not rid:
                continue
            d = AR / rid
            if not d.is_dir():
                missing.append(f"ar_output {rid}")
                continue
            for p in sorted(x for x in d.rglob("*") if x.is_file()):
                rows.append(("ar_output", batch, str(p.relative_to(ROOT)), p.stat().st_size, sha256(p), 1))
        used = {r["logfile"] for r in csv.DictReader(open(DATA / "inputs" / f"logcat_activity_{batch}.csv"))}
        captures = {p.name: p for p in resolve_logs_for_tag(TAGS[batch], kind="logcat")}
        for name in sorted(used - set(captures)):
            missing.append(f"logcat {name} (used, but not attributed to {TAGS[batch]} by the index)")
        for name in sorted(set(captures) | used):
            p = LOGCAT / name
            if p.is_file():
                rows.append(("logcat", batch, str(p.relative_to(ROOT)), p.stat().st_size, sha256(p),
                             1 if name in used else 0))
            else:
                missing.append(f"logcat {name}")
    for p in sorted(x for x in SWEEP.iterdir() if x.is_file()):
        rows.append(("sweep_logs", "", str(p.relative_to(ROOT)), p.stat().st_size, sha256(p), ""))
    for path, batch in sorted(apks.items()):
        p = Path(path)
        if p.is_file():
            rows.append(("apks", "", str(p), p.stat().st_size, sha256(p), 1))
        else:
            missing.append(f"apk {path}")
    rows = sorted(set(rows), key=lambda r: (r[0], r[2]))      # a file listed by two cells appears once
    out = DATA / "raw_manifest.csv.gz"
    with gzip.open(out, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "batch", "path", "bytes", "sha256", "used_by_results"])
        w.writerows(rows)
    by = {}
    for s, *_rest in rows:
        by[s] = by.get(s, 0) + 1
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} files {by}; "
          f"{sum(r[3] for r in rows) / 1e9:.1f} GB; {len(missing)} listed items missing")
    for m in missing[:10]:
        print("  missing:", m)
    sys.exit(0)


if __name__ == "__main__":
    main()
