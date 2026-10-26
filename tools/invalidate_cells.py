#!/usr/bin/env python3
"""Flip named cells from PASS to FAIL so `--resume` re-measures exactly those.

Written 2026-08-11.

WHY A SECOND TOOL AND NOT revalidate_state.py
---------------------------------------------
``revalidate_state.py`` selects cells by *validity class*: it reads the run
table and flips everything that passed but is scientifically invalid. That is
the right instrument when the pipeline can see the defect.

Sometimes it cannot. A cell can be contaminated by something that leaves no
trace in any column: a stray ``adb shell`` from an operator or a leftover
monitoring loop wakes the CPU mid-workload, and the cell records a perfectly
ordinary PASS. The contamination is known to a human and invisible to the
pipeline. This tool exists for that case: name the cells, say why, flip them.

First use, 2026-08-11: a polling loop left over from the charging phase fired
two ``adb shell`` calls at each phone at 20:03:14, sixteen minutes after batch 2
had resumed, catching three cells mid-workload. Around 0.03 % of cell energy
(far below the 4.84 % within-group CV), but known contamination should be
labelled, not silently kept.

WHY IT REFUSES TO RUN DURING A SWEEP
------------------------------------
The per-phone state CSVs are append-only logs written by the live streams. A
rewrite while a stream is mid-append races the writer and can lose or corrupt
the row it is writing. So this checks the sweep lock first and refuses if it is
held. That is also the general lesson from the incident that prompted it:
nothing should touch the rig, or its state, while a sweep owns it.

Usage:
    invalidate_cells.py --sweep-tag final_ykush_batch2_2026-08-10 \\
        --reason perturbed \\
        --cell pixel3:itsok:freerasp_debug:1 \\
        --cell pixel6:itsok:freerasp_debug:5 \\
        --cell pixel9:tipuous:bangcle_debug:2
    # add --apply to write; default is a dry run
"""
from __future__ import annotations

import argparse
import fcntl
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "tools" / "sweep_logs" / ".sweep.lock"


def sweep_is_running() -> bool:
    """True if a sweep holds the single-instance lock.

    Tries to take the lock non-blockingly. Success means nothing holds it, so we
    release immediately and report idle. An unreadable/absent lock file is
    reported as NOT running: the file only exists once a sweep has started.
    """
    try:
        fh = open(LOCKFILE, "w")
    except OSError:
        return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep-tag", required=True)
    ap.add_argument("--cell", action="append", required=True,
                    metavar="PHONE:APP:VARIANT:REP",
                    help="repeatable; e.g. pixel3:itsok:freerasp_debug:1")
    ap.add_argument("--reason", default="perturbed",
                    help="tag written into the result field, e.g. FAIL(perturbed)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; omit for a dry run")
    ap.add_argument("--force", action="store_true",
                    help="write even while a sweep holds the lock (races the writer)")
    args = ap.parse_args()

    if sweep_is_running() and not args.force:
        print("REFUSING: a sweep holds " + str(LOCKFILE) + ".", file=sys.stderr)
        print("The per-phone state CSVs are being appended to right now; rewriting",
              file=sys.stderr)
        print("them would race the writer. Wait for the sweep to exit, then re-run.",
              file=sys.stderr)
        return 3

    state_dir = REPO_ROOT / "tools" / "sweep_logs" / f"state_{args.sweep_tag}"
    if not state_dir.is_dir():
        print(f"no state dir: {state_dir}", file=sys.stderr)
        return 2

    wanted = {}
    for spec in args.cell:
        parts = spec.split(":")
        if len(parts) != 4:
            print(f"bad --cell {spec!r}; want PHONE:APP:VARIANT:REP", file=sys.stderr)
            return 64
        phone, app, variant, rep = parts
        wanted.setdefault(phone, set()).add((app, variant, rep))

    total = 0
    for phone, keys in sorted(wanted.items()):
        csv_path = state_dir / f"{phone}.csv"
        if not csv_path.is_file():
            print(f"  {phone}: no state file, skipping", file=sys.stderr)
            continue
        lines = csv_path.read_text().splitlines(keepends=True)
        out, hits = [], 0
        for ln in lines:
            f = ln.rstrip("\n").split(",")
            # app,variant,rep,result,run_id,wall_s,ts
            if len(f) >= 4 and (f[0], f[1], f[2]) in keys:
                if f[3] == "PASS":
                    f[3] = f"FAIL({args.reason})"
                    ln = ",".join(f) + "\n"
                    hits += 1
                    print(f"  {phone}: {f[0]}/{f[1]} rep{f[2]}  PASS -> {f[3]}")
                else:
                    print(f"  {phone}: {f[0]}/{f[1]} rep{f[2]}  already {f[3]}, left alone")
            out.append(ln)
        for app, variant, rep in sorted(keys):
            if not any(l.split(",")[:3] == [app, variant, rep] for l in lines):
                print(f"  {phone}: {app}/{variant} rep{rep} NOT FOUND in state file",
                      file=sys.stderr)
        total += hits
        if hits and args.apply:
            shutil.copy2(csv_path, csv_path.with_suffix(".csv.bak"))
            csv_path.write_text("".join(out))

    if not args.apply:
        print(f"\nDRY RUN — {total} row(s) would change. Re-run with --apply to write.")
    else:
        print(f"\nwrote {total} row(s); .csv.bak kept alongside each state file")
        print("Now resume the sweep; it will re-measure exactly these cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
