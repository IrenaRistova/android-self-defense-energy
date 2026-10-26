#!/usr/bin/env python3
"""Index and separate sweep logs by sweep tag.

Written 2026-08-13.

THE PROBLEM
-----------
Every sweep writes into one flat pair of directories::

    tools/sweep_logs/parallel_<SWEEP_TS>_<phone>[...].log     stream + per-cell
    tools/sweep_logs/logcat/logcat_<SWEEP_TS>_<phone>_...log  per-cell logcat

keyed on the *run* timestamp, never on the sweep tag. So there is no way to
select one batch's captures without already knowing which run timestamps belong
to it, and a batch that was paused and resumed has several. Series batch 1 spans
three runs; the pilot spans more.

That is not a cosmetic problem. On 2026-08-13 a chained analysis step globbed
``logcat_*_pixel*.log`` intending "this batch" and matched **2282 files when only
928 belonged to it**, folding the pilot's captures in beside the series' and
making the ``(phone, app, variant, rep)`` join key ambiguous: two different
cells, same key, silently.

THE MAPPING IS RECOVERABLE, EXACTLY
-----------------------------------
Each wrapper log states its tag on line 2::

    ============ run_final_sweep_ykush.sh ============
      sweep tag:  final_ykush_batch2_2026-08-10

and contains the stream-log paths, which carry the run timestamp. That gives an
authoritative ``run_ts -> sweep_tag`` mapping with no guessing from mtimes.

WHAT THIS TOOL DOES
-------------------
``index``  build specs/sweep_log_index.csv mapping every log file to its tag.
``check``  report which files cannot be attributed, and any run_ts claimed by
           more than one tag (which would mean the mapping is unsound).
``move``   reorganise into tools/sweep_logs/by_tag/<tag>/{logcat,streams}/.
           Refuses to touch a run that is still being written.

Readers should go through ``resolve_logs_for_tag()`` rather than globbing.

Usage:
    organise_sweep_logs.py index
    organise_sweep_logs.py check
    organise_sweep_logs.py move --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "tools" / "sweep_logs"
LOGCAT_DIR = LOG_DIR / "logcat"
BY_TAG = LOG_DIR / "by_tag"
INDEX_CSV = REPO_ROOT / "specs" / "sweep_log_index.csv"

RUN_TS_RE = re.compile(r"(?P<ts>\d{8}_\d{6})")
TAG_RE = re.compile(r"sweep tag:\s*(?P<tag>\S+)")
# Files that are sweep OUTPUT, not orchestration logs.
STREAM_RE = re.compile(r"^parallel_(?P<ts>\d{8}_\d{6})_(?P<rest>.+)\.log$")
LOGCAT_RE = re.compile(r"^logcat_(?P<ts>\d{8}_\d{6})_(?P<rest>.+)\.log$")
# Orchestration logs we scan for the tag, rather than attribute.
NOT_A_WRAPPER = ("parallel_", "logcat", "guard_", "charge_", "chain_",
                 "bandkeeper", "hub_", "soc_")

# A run whose newest file was touched within this many seconds is treated as
# LIVE and never moved: the sweep is still writing into it.
LIVE_WINDOW_S = 1800


def wrapper_logs() -> List[Path]:
    out = []
    for p in sorted(LOG_DIR.glob("*.log")):
        if any(p.name.startswith(x) for x in NOT_A_WRAPPER):
            continue
        out.append(p)
    return out


SESSION_LOG_RE = re.compile(r"^parallel_(?P<ts>\d{8}_\d{6})\.log$")


def build_run_map() -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """run_ts -> sweep_tag, plus tag -> {run_ts}, from wrapper logs and from each session's own log.

    ADDED 2026-10-05. Each session's master log (``parallel_<run_ts>.log``) states its own
    tag ("sweep tag: ..." in its header). Before, only wrapper logs were read, and a session
    whose wrapper log was not kept stayed an orphan. The pilot's first session,
    20260804_025319, with 81 captures of measured pilot runs, was missed that way. A session
    log is authoritative for its own run_ts. If it disagrees with a wrapper, the conflict is
    reported like any other.
    """
    run_to_tag: Dict[str, str] = {}
    conflicts: Dict[str, Set[str]] = defaultdict(set)
    tag_runs: Dict[str, Set[str]] = defaultdict(set)
    for sl in sorted(LOG_DIR.glob("parallel_*.log")):
        sm = SESSION_LOG_RE.match(sl.name)
        if not sm:
            continue
        try:
            with open(sl, errors="replace") as fh:
                head = "".join(fh.readline() for _ in range(40))
        except OSError:
            continue
        m = TAG_RE.search(head)
        if m:
            run_to_tag[sm.group("ts")] = m.group("tag")
            tag_runs[m.group("tag")].add(sm.group("ts"))
    for wl in wrapper_logs():
        try:
            text = wl.read_text(errors="replace")
        except OSError:
            continue
        m = TAG_RE.search(text)
        if not m:
            continue
        tag = m.group("tag")
        runs = set(re.findall(r"parallel_(\d{8}_\d{6})", text))
        if not runs:
            # A wrapper that never reached the runner (preflight abort). Its own
            # filename timestamp is NOT a run timestamp: do not invent one.
            continue
        for ts in runs:
            if ts in run_to_tag and run_to_tag[ts] != tag:
                conflicts[ts].update({run_to_tag[ts], tag})
            run_to_tag[ts] = tag
            tag_runs[tag].add(ts)
    if conflicts:
        for ts, tags in conflicts.items():
            print(f"CONFLICT: run {ts} claimed by {sorted(tags)}", file=sys.stderr)
    return run_to_tag, tag_runs


def classify() -> Tuple[List[dict], List[Path]]:
    """Attribute every stream/cell/logcat file to a tag. Returns (rows, orphans)."""
    run_to_tag, _ = build_run_map()
    rows: List[dict] = []
    orphans: List[Path] = []
    for d, kind in ((LOG_DIR, "stream"), (LOGCAT_DIR, "logcat")):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix != ".log":
                continue
            m = (LOGCAT_RE if kind == "logcat" else STREAM_RE).match(p.name)
            if not m:
                continue
            ts = m.group("ts")
            tag = run_to_tag.get(ts)
            if not tag:
                orphans.append(p)
                continue
            rows.append({
                "sweep_tag": tag, "run_ts": ts, "kind": kind,
                "file": p.name,
                "rel_path": str(p.relative_to(REPO_ROOT)),
                "bytes": p.stat().st_size,
                "mtime": int(p.stat().st_mtime),
            })
    return rows, orphans


def resolve_logs_for_tag(tag: str, kind: str = "logcat") -> List[Path]:
    """THE supported way to get one batch's logs. Never glob by hand.

    Prefers the per-tag directory; falls back to the index over the flat layout,
    so this keeps working before, during and after a migration.
    """
    tagged = BY_TAG / tag / ("logcat" if kind == "logcat" else "streams")
    if tagged.is_dir():
        found = sorted(tagged.glob("*.log"))
        if found:
            return found
    if INDEX_CSV.is_file():
        out = []
        for r in csv.DictReader(open(INDEX_CSV)):
            if r["sweep_tag"] == tag and r["kind"] == kind:
                p = REPO_ROOT / r["rel_path"]
                if p.is_file():
                    out.append(p)
        if out:
            return sorted(out)
    # Last resort: rebuild the mapping live.
    run_to_tag, tag_runs = build_run_map()
    runs = tag_runs.get(tag, set())
    d = LOGCAT_DIR if kind == "logcat" else LOG_DIR
    pref = "logcat_" if kind == "logcat" else "parallel_"
    return sorted(p for ts in runs for p in d.glob(f"{pref}{ts}_*.log"))


def cmd_index() -> int:
    rows, orphans = classify()
    INDEX_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["sweep_tag", "run_ts", "kind", "file",
                                           "rel_path", "bytes", "mtime"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    per = defaultdict(lambda: defaultdict(int))
    for r in rows:
        per[r["sweep_tag"]][r["kind"]] += 1
    print(f"wrote {INDEX_CSV}  ({len(rows)} files attributed, {len(orphans)} orphans)\n")
    print(f"{'sweep tag':<42}{'logcat':>9}{'stream':>9}")
    for tag in sorted(per, key=lambda t: -per[t]["logcat"]):
        print(f"{tag:<42}{per[tag]['logcat']:>9}{per[tag]['stream']:>9}")
    if orphans:
        print(f"\n{len(orphans)} unattributed (no wrapper log names their run):")
        for p in orphans[:8]:
            print(f"   {p.name}")
        if len(orphans) > 8:
            print(f"   ... and {len(orphans)-8} more")
    return 0


def cmd_check() -> int:
    run_to_tag, tag_runs = build_run_map()
    print(f"{len(run_to_tag)} run timestamps mapped to {len(tag_runs)} sweep tags\n")
    for tag in sorted(tag_runs):
        print(f"  {tag:<42}{len(tag_runs[tag])} run(s): {' '.join(sorted(tag_runs[tag]))}")
    rows, orphans = classify()
    print(f"\n{len(rows)} files attributed, {len(orphans)} orphans")
    now = time.time()
    live = {r["run_ts"] for r in rows if now - r["mtime"] < LIVE_WINDOW_S}
    if live:
        print(f"LIVE runs (written within {LIVE_WINDOW_S}s, will not be moved): {sorted(live)}")
    return 0 if not orphans else 1


def cmd_move(apply: bool) -> int:
    rows, orphans = classify()
    now = time.time()
    live = {r["run_ts"] for r in rows if now - r["mtime"] < LIVE_WINDOW_S}
    if live:
        print(f"skipping LIVE run(s) still being written: {sorted(live)}")
    moved = skipped = 0
    for r in rows:
        if r["run_ts"] in live:
            skipped += 1
            continue
        src = REPO_ROOT / r["rel_path"]
        dst_dir = BY_TAG / r["sweep_tag"] / ("logcat" if r["kind"] == "logcat" else "streams")
        dst = dst_dir / r["file"]
        if dst.exists():
            continue
        if apply:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        moved += 1
    print(f"{'moved' if apply else 'would move'} {moved} files, skipped {skipped} live")
    if apply:
        print("re-indexing...")
        cmd_index()
    else:
        print("dry run — pass --apply to write")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["index", "check", "move"])
    ap.add_argument("--apply", action="store_true", help="move: actually move files")
    a = ap.parse_args()
    if a.command == "index":
        return cmd_index()
    if a.command == "check":
        return cmd_check()
    return cmd_move(a.apply)


if __name__ == "__main__":
    sys.exit(main())
