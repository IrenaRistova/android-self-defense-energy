#!/usr/bin/env python3
"""revalidate_state.py: make `--resume` re-run cells that PASSED but are not
scientifically valid.

Written 2026-08-09.

WHY THIS EXISTS
---------------
`sweep_runner --resume` decides what to skip from ONE field: `result` in
`tools/sweep_logs/state_<tag>/<phone>.csv`. Anything marked PASS is skipped
forever.

But PASS and *valid* are different tests, and validity is much stricter. A cell
can pass the runner's checks and still be unusable: contaminated by a hub race,
measured while the app was crash-looping, or run against a since-replaced APK.
On the 2026-08-04 sweep that gap is 116 cells: PASS in the state file, excluded
from every analysis, and invisible to resume. They would never be re-measured no
matter how many times the sweep was restarted.

This flips the `result` field for the recoverable ones so resume picks them up.
It writes ONLY that field; run_id/wall_s/ts are left intact so the history of
what originally ran stays readable.

WHICH CLASSES ARE WORTH RE-RUNNING
----------------------------------
Re-running is not free (~5 min of rig time per cell) and for some classes it is
guaranteed to reproduce the same defect. Default selection:

  RERUN
    HUB_RACE_CONTAMINATED  all 28 ran on laptop bus power before the external
                           VEXT supply was fitted on 2026-08-06/07. That regime
                           is gone; these will measure clean now.
    SCENARIO_FAILED        a strict-fail is often flaky UI timing, not a
                           permanent break.
    HUB_ABORTED            aborted by the rig, not by the app.
    APP_CRASHED, EXCEPT documenter/allatori (see below).

  DO NOT RERUN (use --include-futile to override)
    STALE_BUILD            itsok/allatori (the rule as of 2026-08-09; the
                           published run tables mark none of these cells
                           STALE_BUILD).
    documenter/allatori    27 cells, every one crashing 5-6x with
                           AbstractMethodError from androidx.databinding. It is
                           deterministic: the crash is in the build, not the
                           run. A rerun yields another crash-loop cell.

Usage:
  revalidate_state.py --sweep-tag final_ykush_2026-08-04            # dry run
  revalidate_state.py --sweep-tag final_ykush_2026-08-04 --apply
  revalidate_state.py --sweep-tag ... --apply --include-futile
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/irena/Documents/Master Experiment")

RERUN_CLASSES = {
    "HUB_RACE_CONTAMINATED",
    "SCENARIO_FAILED",
    "HUB_ABORTED",
    "APP_CRASHED",
}
FUTILE_CLASSES = {"STALE_BUILD"}
# (app, variant) pairs whose defect is in the BUILD, so a rerun cannot fix it.
FUTILE_PAIRS = {("documenter", "allatori_debug"), ("itsok", "allatori_debug")}

# What the flipped rows are set to. Not a bare "FAIL": the tag records WHY the
# cell is being re-measured, so a later reader can tell a revalidation re-run
# from a genuine rig failure.
NEW_RESULT = "FAIL(revalidate)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep-tag", required=True)
    ap.add_argument("--run-table", default=None,
                    help="default: 6_results_data/run_tables/run_table_extended_<tag>.csv")
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the state CSVs (default: dry run)")
    ap.add_argument("--include-futile", action="store_true",
                    help="also flip classes whose defect a rerun cannot fix")
    args = ap.parse_args()

    rt_path = Path(args.run_table) if args.run_table else \
        REPO / "6_results_data" / "run_tables" / f"run_table_extended_{args.sweep_tag}.csv"
    state_dir = REPO / "tools" / "sweep_logs" / f"state_{args.sweep_tag}"

    if not rt_path.exists():
        print(f"FATAL: run table not found: {rt_path}", file=sys.stderr)
        return 2
    if not state_dir.is_dir():
        print(f"FATAL: state dir not found: {state_dir}", file=sys.stderr)
        return 2

    rows = list(csv.DictReader(open(rt_path)))

    # Target set: PASS in state, but not scientifically valid.
    targets: dict[tuple[str, str, str, str], str] = {}
    skipped = Counter()
    for r in rows:
        if r.get("scientifically_valid") == "True":
            continue
        if r.get("result") != "PASS":
            continue          # resume already re-runs anything not PASS
        cls = r.get("cell_failure_class", "")
        pair = (r.get("app", ""), r.get("variant", ""))
        futile = cls in FUTILE_CLASSES or pair in FUTILE_PAIRS
        if futile and not args.include_futile:
            skipped[f"{cls} {pair[0]}/{pair[1]}"] += 1
            continue
        if cls not in RERUN_CLASSES and not args.include_futile:
            skipped[f"{cls} (not in rerun set)"] += 1
            continue
        targets[(r["phone"], r["app"], r["variant"], str(r["rep"]))] = cls

    print(f"run table : {rt_path.name}")
    print(f"state dir : {state_dir}")
    print(f"mode      : {'APPLY' if args.apply else 'DRY RUN'}\n")

    print(f"cells to flip to {NEW_RESULT}: {len(targets)}")
    for cls, n in Counter(targets.values()).most_common():
        print(f"    {cls:24} {n}")
    if skipped:
        print("\nnot flipped (a rerun cannot fix these):")
        for k, n in skipped.most_common():
            print(f"    {k:44} {n}")

    if not targets:
        print("\nnothing to do")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    total = 0
    for phone in ("pixel3", "pixel6", "pixel9"):
        csv_path = state_dir / f"{phone}.csv"
        if not csv_path.exists():
            continue
        with open(csv_path) as f:
            state_rows = list(csv.DictReader(f))
            fields = list(state_rows[0].keys()) if state_rows else []
        hits = 0
        for sr in state_rows:
            key = (phone, sr.get("app", ""), sr.get("variant", ""), str(sr.get("rep", "")))
            if key in targets and sr.get("result") == "PASS":
                sr["result"] = NEW_RESULT
                hits += 1
        print(f"  {phone}: {hits} row(s)")
        total += hits
        if args.apply and hits:
            backup = csv_path.with_suffix(f".csv.bak_revalidate_{stamp}")
            shutil.copy2(csv_path, backup)
            tmp = csv_path.with_suffix(".csv.tmp")
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(state_rows)
            tmp.replace(csv_path)

    print(f"\ntotal rows {'flipped' if args.apply else 'that WOULD flip'}: {total}")
    if args.apply:
        print(f"backups: {state_dir}/<phone>.csv.bak_revalidate_{stamp}")
        print("\nNow resume the sweep — these cells will be re-measured:")
        print(f"  tools/run_final_sweep_ykush.sh --resume --sweep-tag {args.sweep_tag} ...")
    else:
        print("\nre-run with --apply to write the changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
