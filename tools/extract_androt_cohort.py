#!/usr/bin/env python3
"""The AndroT dataset's record of each measured application's repository.

AndroT (Muradyan and Fazzini, MSR 2026) lists 1,337 Android projects on GitHub that are published on
Google Play or F-Droid and have tests; app_repositories_newest/androT.json is its dataset file. This copies,
for each application in 6_results_data/inputs/app_characteristics.csv, the AndroT record whose
application_id equals the measured package id without the ".debug" suffix that a debug build type adds
(documenter, tipuous). Exactly one record must match each application.

Usage (from the repository root):  python3 tools/extract_androt_cohort.py
Output: 6_results_data/inputs/androt_cohort.csv (provenance: androt_cohort.PROVENANCE.md)
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROT = ROOT / "app_repositories_newest" / "androT.json"
CHARS = ROOT / "6_results_data" / "inputs" / "app_characteristics.csv"
OUT = ROOT / "6_results_data" / "inputs" / "androt_cohort.csv"
COLUMNS = ["app", "application_id", "repository", "latest_commit_id", "latest_commit_date"]


def main():
    repos = json.loads(ANDROT.read_text())["repositories"]
    rows = []
    for c in csv.DictReader(open(CHARS, newline="")):
        pid = c["package"].removesuffix(".debug")
        hits = [r for r in repos if r.get("application_id") == pid]
        assert len(hits) == 1, f"{c['app']}: {len(hits)} AndroT records with application_id {pid}"
        r = hits[0]
        rows.append([c["app"], pid, r["url"], r["latest_commit_id"], r["latest_commit_date"]])
    with open(OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} applications, from {len(repos)} AndroT records")


if __name__ == "__main__":
    main()
