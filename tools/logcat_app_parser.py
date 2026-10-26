#!/usr/bin/env python3
"""Extract the APP's own logcat activity from a cell capture, discarding system noise.

Written 2026-08-11.

WHY THIS EXISTS
---------------
``tools/sweep_logs/logcat/`` holds an unfiltered kernel-ring-buffer dump per
cell: every process on the phone, not just the app under test. A representative
cell (pixel9 / tipuous / freerasp, batch 1) is 23 291 lines of which **529 belong
to the app**: 2.3 %. The rest is ``pixel-thermal`` sampling, ``system_server``
window transitions, ``BackgroundInstallControlService``, ``am_cpu`` accounting.

Reading such a file by eye, or by grepping it whole, produces answers about the
platform rather than the app. Grepping for a string like "talsec" across the raw
file also silently mixes in the *system's* mentions of the app (activity manager
lines quote package names), so a hit count is not evidence the app did anything.

ATTRIBUTION IS BY PID, NOT BY STRING MATCH
------------------------------------------
This mirrors the rule already used by ``build_run_table_extended.py`` for crash
counting: attribute by the process, never by grepping the text. Android's event
log gives us the mapping directly::

    am_proc_start: [0,17503,10744,com.tips.tipuous.debug,next-top-activity,{...}]
                      ^PID  ^UID  ^package

The app under test is every PID activity-manager started under the app's own
package: its main process, named after the package, and its own sub-processes,
named ``<package>:<name>`` (documenter's ``:pdf2htmlEXWorker``, for one). One cell
commonly has several, and an app that crashed and relaunched has one PID per
launch. All of them count, and nothing else does. The package comes from
``6_results_data/inputs/app_characteristics.csv``, keyed by the app named in the
cell's filename.

CORRECTED 2026-10-05. Until then the rule was "every process that is not
infrastructure", with a denylist of prefixes (``com.google.android.``,
``com.android.``, ``android.``). That rule had two faults:

- Google Play Services' shared processes are named ``com.google.process.gapps``
  and ``com.google.process.gservices``, outside every prefix. They were counted as
  the app in about 7 % of cells, in every arm, as was one telephony service,
  ``com.google.RilConfigService``.
- The process pattern could not match a name containing a colon, so the app's own
  sub-processes were never counted.

WHAT IT REPORTS
---------------
Per cell: how many lines the app actually emitted, how many threads it ran, its
tag histogram, GC events, native-library loads, and hidden-API reflection
(which is how RASP libraries reach root/debugger detection). ``--compare`` diffs
two cells so a variant's extra work is visible directly.

A NOTE ON WHAT THIS CANNOT TELL YOU
-----------------------------------
Log volume is not energy. A library doing heavy work silently emits nothing; one
doing trivial work verbosely fills the log. Use this to find out *what* a variant
does, then attribute cost with the run-table's CPU / memory / energy columns.

Usage:
    logcat_app_parser.py <logcat.log> [--json]
    logcat_app_parser.py --compare <baseline.log> <variant.log>
    logcat_app_parser.py --sweep-diff <app> <phone> --variant freerasp_debug
    logcat_app_parser.py --sweep-tag <tag> --run-table <run table> --out <csv>   (the analysis inputs)
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGCAT_DIR = REPO_ROOT / "tools" / "sweep_logs" / "logcat"

# Packages activity-manager starts during a cell that are measurement
# infrastructure, not the app under test. Same principle (and mostly the same
# list) as NON_APP_CRASH_PROCESSES in build_run_table_extended.py.
INFRA_PACKAGES = frozenset({
    "com.example.batterymanager_utility",
    "io.appium.settings",
    "io.appium.uiautomator2.server",
    "io.appium.uiautomator2.server.test",
    "com.android.settings",
})
INFRA_PREFIXES = ("com.google.android.", "com.android.", "android.")

# `TIMESTAMP PID TID LEVEL Tag: message`, e.g.
#   1785805621.845 17503 17503 I FreeRasp: Talsec started
LINE_RE = re.compile(
    r"^\s*(?P<ts>\d+\.\d+)\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<lvl>[VDIWEF])\s+(?P<tag>[^:]*?)\s*:\s?(?P<msg>.*)$"
)
PROC_START_RE = re.compile(
    r"am_proc_start:\s*\[\d+,(?P<pid>\d+),(?P<uid>\d+),(?P<proc>[^,\]]+),"
)
APP_CHARACTERISTICS = REPO_ROOT / "6_results_data" / "inputs" / "app_characteristics.csv"
_PACKAGES: Optional[Dict[str, str]] = None

# Activity we specifically want to surface, because it is how integrity /
# anti-tamper libraries do their work.
HIDDENAPI_RE = re.compile(r"hiddenapi:\s*Accessing hidden (?:method|field)\s+(?P<sym>\S+)")
HIDDENAPI_FROM_RE = re.compile(r"from\s+L(?P<cls>[\w/$]+);")
NATIVE_LIB_RE = re.compile(r"\b(lib[\w.-]+\.so)\b")
GC_RE = re.compile(r"\b(?:Background concurrent|Explicit|Alloc|NativeAlloc)\b.*\bGC\b|"
                   r"^(?:Background )?(?:concurrent )?(?:copying|mark sweep) GC")


def app_package(app: str) -> Optional[str]:
    """The app's package, which is also its main process's name."""
    global _PACKAGES
    if _PACKAGES is None:
        import csv as _csv
        with open(APP_CHARACTERISTICS) as fh:
            _PACKAGES = {r["app"]: r["package"] for r in _csv.DictReader(fh)}
    return _PACKAGES.get(app)


def resolve_app_pids(lines: List[str], package: Optional[str] = None,
                     subprocesses: bool = True) -> Tuple[Set[str], Dict[str, str]]:
    """Return (app PIDs, pid -> process name) from am_proc_start events.

    With ``package``, which every analysis path passes, the result is the processes
    of that package: its main process and its ``<package>:<name>`` sub-processes,
    and nothing else. Without it (interactive use on a file whose app is unknown),
    it is every process outside infrastructure, the platform and Google: a
    fallback, not an attribution. Every PID the app occupied is returned, so a cell
    where the app died and relaunched is fully covered. ``subprocesses=False`` keeps
    the main process only (the analysis inputs count both).
    """
    pids: Set[str] = set()
    owner: Dict[str, str] = {}
    for ln in lines:
        m = PROC_START_RE.search(ln)
        if not m:
            continue
        proc = m.group("proc")
        if package is not None:
            if proc != package and not (subprocesses and proc.startswith(package + ":")):
                continue
        else:
            base = proc.split(":", 1)[0]
            if (base in INFRA_PACKAGES or base.startswith(INFRA_PREFIXES)
                    or base.startswith("com.google.")):
                continue
        pids.add(m.group("pid"))
        owner[m.group("pid")] = proc
    return pids, owner


def parse_cell(path: Path, package: Optional[str] = None) -> Dict:
    """Parse one cell capture down to the app's own activity.

    The package defaults to that of the app named in the cell's filename.
    """
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}

    if package is None:
        key = cell_key(path)
        package = app_package(key["app"]) if key else None
    pids, owner = resolve_app_pids(lines, package)
    out: Dict = {
        "path": path.name,
        "total_lines": len(lines),
        "app_pids": sorted(pids),
        "packages": sorted(set(owner.values())),
        "app_lines": 0,
        "threads": set(),
        "tags": collections.Counter(),
        "levels": collections.Counter(),
        "hiddenapi": collections.Counter(),
        "hiddenapi_callers": collections.Counter(),
        "native_libs": collections.Counter(),
        "gc_events": 0,
        "auditd_main": 0,
        "exceptions": collections.Counter(),
        "first_ts": None,
        "last_ts": None,
    }
    if not pids:
        # No am_proc_start for a non-infrastructure package. Report it as
        # UNRESOLVED rather than returning zeros that read like "the app was
        # silent": those are different facts.
        out["unresolved"] = True
        return out

    main_pids = {p for p, name in owner.items() if name == package}
    for ln in lines:
        m = LINE_RE.match(ln)
        if not m or m.group("pid") not in pids:
            continue
        out["app_lines"] += 1
        ts = float(m.group("ts"))
        out["first_ts"] = ts if out["first_ts"] is None else min(out["first_ts"], ts)
        out["last_ts"] = ts if out["last_ts"] is None else max(out["last_ts"], ts)
        out["threads"].add((m.group("pid"), m.group("tid")))
        tag, msg = m.group("tag").strip(), m.group("msg")
        out["tags"][tag] += 1
        if tag == "auditd" and m.group("pid") in main_pids:
            out["auditd_main"] += 1
        out["levels"][m.group("lvl")] += 1

        hm = HIDDENAPI_RE.search(msg)
        if hm:
            out["hiddenapi"][hm.group("sym")] += 1
            fm = HIDDENAPI_FROM_RE.search(msg)
            if fm:
                out["hiddenapi_callers"][fm.group("cls").replace("/", ".")] += 1
        for lib in NATIVE_LIB_RE.findall(msg):
            out["native_libs"][lib] += 1
        if GC_RE.search(msg):
            out["gc_events"] += 1
        em = re.search(r"\b([A-Za-z][\w.$]*(?:Exception|Error))\b", msg)
        if em and m.group("lvl") in ("E", "F"):
            out["exceptions"][em.group(1)] += 1

    out["threads"] = len(out["threads"])
    out["noise_ratio"] = (
        1.0 - out["app_lines"] / out["total_lines"] if out["total_lines"] else float("nan")
    )
    out["span_s"] = (
        out["last_ts"] - out["first_ts"] if out["first_ts"] is not None else None
    )
    return out


def find_cells(app: str, phone: str, variant: str, limit: int = 0) -> List[Path]:
    hits = sorted(LOGCAT_DIR.glob(f"logcat_*_{phone}_{app}_{variant}_r*.log"))
    return hits[:limit] if limit else hits


def aggregate(paths: List[Path]) -> Dict:
    """Sum several cells of the same (app, phone, variant) into one profile."""
    agg = {
        "cells": 0, "unresolved": 0, "app_lines": [], "threads": [], "gc": [],
        "tags": collections.Counter(), "hiddenapi": collections.Counter(),
        "hiddenapi_callers": collections.Counter(),
        "native_libs": collections.Counter(), "exceptions": collections.Counter(),
        "noise": [],
    }
    for p in paths:
        c = parse_cell(p)
        if c.get("error") or c.get("unresolved"):
            agg["unresolved"] += 1
            continue
        agg["cells"] += 1
        agg["app_lines"].append(c["app_lines"])
        agg["threads"].append(c["threads"])
        agg["gc"].append(c["gc_events"])
        agg["noise"].append(c["noise_ratio"])
        for k in ("tags", "hiddenapi", "hiddenapi_callers", "native_libs", "exceptions"):
            agg[k].update(c[k])
    return agg


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def print_profile(name: str, agg: Dict) -> None:
    print(f"\n  {name}")
    if not agg["cells"]:
        print(f"    no resolvable cells ({agg['unresolved']} unresolved)")
        return
    print(f"    cells {agg['cells']}"
          + (f"  (+{agg['unresolved']} unresolved)" if agg["unresolved"] else ""))
    print(f"    app log lines / cell   {_mean(agg['app_lines']):>8.1f}"
          f"   (system noise discarded: {100*_mean(agg['noise']):.1f}% of the file)")
    print(f"    threads / cell         {_mean(agg['threads']):>8.1f}")
    print(f"    GC events / cell       {_mean(agg['gc']):>8.1f}")
    if agg["native_libs"]:
        libs = ", ".join(f"{k}" for k, _ in agg["native_libs"].most_common(6))
        print(f"    native libs            {libs}")
    if agg["hiddenapi"]:
        print(f"    hidden-API reflection  {sum(agg['hiddenapi'].values())} calls, "
              f"{len(agg['hiddenapi'])} distinct")
        for sym, n in agg["hiddenapi"].most_common(5):
            print(f"        {n:>4}x {sym}")
        for cls, n in agg["hiddenapi_callers"].most_common(5):
            print(f"        caller: {cls} ({n})")
    if agg["exceptions"]:
        print(f"    exceptions             "
              + ", ".join(f"{k} x{v}" for k, v in agg["exceptions"].most_common(4)))


def cmd_compare(base_paths: List[Path], var_paths: List[Path],
                base_label: str, var_label: str) -> None:
    a, b = aggregate(base_paths), aggregate(var_paths)
    print_profile(base_label, a)
    print_profile(var_label, b)

    print(f"\n  WHAT {var_label} DOES THAT {base_label} DOES NOT")
    only = {t: n for t, n in b["tags"].items() if t not in a["tags"]}
    if only:
        for t, n in sorted(only.items(), key=lambda kv: -kv[1])[:12]:
            per = n / b["cells"] if b["cells"] else 0
            print(f"    tag {t!r:<34} {per:>6.1f} lines/cell   (absent from {base_label})")
    else:
        print("    no tags unique to the variant")

    lib_only = {k for k in b["native_libs"] if k not in a["native_libs"]}
    if lib_only:
        print(f"    native libs only in {var_label}: {', '.join(sorted(lib_only))}")
    ha_only = {k: v for k, v in b["hiddenapi_callers"].items()
               if k not in a["hiddenapi_callers"]}
    if ha_only:
        print(f"    hidden-API callers only in {var_label}:")
        for cls, n in sorted(ha_only.items(), key=lambda kv: -kv[1])[:6]:
            print(f"        {cls}  ({n} calls over {b['cells']} cells)")


# Cell filenames are the join key back to the run table:
#   logcat_<sweepstamp>_<phone>_<app>_<variant>_r<rep>.log
CELL_NAME_RE = re.compile(
    r"^logcat_\d+_\d+_(?P<phone>pixel\d)_(?P<app>[a-z_0-9]+)_"
    r"(?P<variant>\w+_debug)_r(?P<rep>\d+)\.log$"
)

# Per-variant runtime markers, discovered 2026-08-11 by diffing app-attributed
# logcat across all four arms on 8 app/phone combinations.
#
# `auditd` is the strongest of these by a distance: over 192 batch-2 cells it
# fired in 50/50 freeRasp cells and 0/142 of every other arm. It is the kernel
# logging SELinux denials as Talsec probes selinuxfs for root, so it is a direct
# trace of the security work rather than a library announcing itself.
VARIANT_MARKERS = {
    "freerasp_debug": {"tags": ("auditd", "FreeRasp", "DefaultDispatch"),
                       "libs": ("libts.so",),
                       "callers": ("com.aheaditec.talsec.security",)},
    "bangcle_debug":  {"tags": ("System.err",),
                       "libs": ("libSecShell.so",),
                       "callers": ("com.SecShell.SecShell",)},
    "allatori_debug": {"tags": (), "libs": (), "callers": ()},
}


def cell_key(path: Path) -> Optional[Dict[str, str]]:
    m = CELL_NAME_RE.match(path.name)
    return m.groupdict() if m else None


AR_OUTPUT = REPO_ROOT / "android-runner" / "examples" / "batterymanager" / "output"
TS_RE = re.compile(r"^\s*(\d+\.\d+)\s")


def _capture_span(path: Path) -> Tuple[Optional[float], Optional[float]]:
    """First and last timestamp of a capture (device clock)."""
    first = last = None
    with open(path, errors="replace") as fh:
        for ln in fh:
            m = TS_RE.match(ln)
            if m:
                t = float(m.group(1))
                first = t if first is None else first
                last = t
    return first, last


def _run_span(run_id: str) -> Tuple[Optional[float], Optional[float]]:
    """First and last battery sample of a measured run (device clock, the same clock as the log)."""
    import csv as _csv
    f = next((AR_OUTPUT / run_id / "data").glob("*/*/batterymanager/logcat_*.csv"), None) if run_id else None
    if f is None:
        return None, None
    ts = []
    with open(f) as fh:
        for r in _csv.DictReader(fh):
            try:
                ts.append(int(r["Timestamp"]) / 1000.0)
            except (KeyError, ValueError, TypeError):
                continue
    return (min(ts), max(ts)) if ts else (None, None)


def select_measured_captures(files: List[Path], run_table: Path) -> Tuple[List[Path], Dict[str, str], List[str]]:
    """Keep, for each cell, the capture of the run the run table measured.

    A cell can have more than one capture: a run that failed and was re-run in a later
    session leaves the failed attempt's capture behind. A capture belongs to the measured
    run when its time span overlaps that run's battery samples, both on the device clock.
    A capture that ended before the first battery sample (logcat stopped early) overlaps
    nothing measured and is dropped too: it cannot describe the measured windows.
    Returns the kept files, a per-file flag ("1" overlaps, "" no run to check against) and
    the dropped files.
    """
    import csv as _csv
    with open(run_table) as fh:
        run_id = {(r["phone"], r["app"], r["variant"], r["rep"]): r.get("run_id", "")
                  for r in _csv.DictReader(fh)}
    by_key: Dict[tuple, List[Path]] = collections.defaultdict(list)
    for f in files:
        k = cell_key(f)
        if k:
            by_key[(k["phone"], k["app"], k["variant"], k["rep"])].append(f)
    kept, flag, dropped = [], {}, []
    for key, caps in by_key.items():
        r0, r1 = _run_span(run_id.get(key, ""))
        if r0 is None:
            for f in caps:
                kept.append(f)
                flag[f.name] = ""
            continue
        overlap = []
        for f in caps:
            c0, c1 = _capture_span(f)
            ov = (min(c1, r1) - max(c0, r0)) if c0 is not None else -1.0
            overlap.append((ov, f))
        best = max(overlap, key=lambda x: x[0])
        for ov, f in overlap:
            if f is best[1] and ov > 0:
                kept.append(f)
                flag[f.name] = "1"
            else:
                dropped.append(f.name)
    return sorted(kept), flag, dropped


def cmd_batch(pattern: Optional[str], out_csv: Optional[Path],
              sweep_tag: Optional[str] = None, run_table: Optional[Path] = None) -> int:
    """Emit one row per cell so logcat activity can be joined to the run table.

    This is the form the analysis actually needs: prose summaries cannot be
    correlated with energy, a CSV keyed by (phone, app, variant, rep) can.
    Cells whose PIDs cannot be resolved are emitted with `resolved=0` and empty
    metrics, never as zeros, which would read as "the app was silent".

    With ``run_table`` (added 2026-10-05), each cell keeps only the capture of the
    run that table measured (``select_measured_captures``). Without it, a cell that
    was re-run in a later session has two rows, one of them from the failed attempt.
    """
    import csv as _csv
    if sweep_tag:
        # Exact selection: a batch's run timestamps come from its wrapper logs,
        # not from a filename pattern. This is the only way to get one batch's
        # captures and nothing else.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from organise_sweep_logs import resolve_logs_for_tag
        files = resolve_logs_for_tag(sweep_tag, kind="logcat")
        if not files:
            print(f"no logcat files resolve to sweep tag {sweep_tag!r}. "
                  f"Run: python3 tools/organise_sweep_logs.py index", file=sys.stderr)
            return 1
        print(f"resolved {len(files)} captures for sweep tag {sweep_tag}")
    else:
        files = sorted(LOGCAT_DIR.glob(pattern or ""))
        if not files:
            print(f"no logcat files match {pattern!r} under {LOGCAT_DIR}", file=sys.stderr)
            return 1
    flag: Dict[str, str] = {}
    if run_table:
        files, flag, dropped = select_measured_captures(files, run_table)
        print(f"kept {len(files)} captures of measured runs; dropped {len(dropped)} that do not "
              f"overlap the measured run (another attempt's, or one that ended before measuring began)"
              + (f": {', '.join(dropped)}" if dropped else ""))
    cols = ["phone", "app", "variant", "rep", "resolved", "total_lines", "app_lines",
            "noise_pct", "threads", "gc_events", "span_s", "auditd_lines", "auditd_lines_main",
            "hiddenapi_calls", "talsec_calls", "secshell_calls", "native_libs",
            "exceptions", "logfile", "capture_matches_run"]
    rows = []
    for f in files:
        k = cell_key(f)
        if not k:
            continue
        c = parse_cell(f)
        base = {**k, "logfile": f.name, "capture_matches_run": flag.get(f.name, "")}
        if c.get("error") or c.get("unresolved"):
            rows.append({**base, "resolved": 0,
                         "total_lines": c.get("total_lines", ""),
                         **{x: "" for x in cols if x not in base and x != "resolved"
                            and x != "total_lines"}})
            continue
        callers = c["hiddenapi_callers"]
        rows.append({
            **base, "resolved": 1,
            "total_lines": c["total_lines"], "app_lines": c["app_lines"],
            "noise_pct": f"{100*c['noise_ratio']:.2f}",
            "threads": c["threads"], "gc_events": c["gc_events"],
            "span_s": f"{c['span_s']:.1f}" if c["span_s"] is not None else "",
            "auditd_lines": c["tags"].get("auditd", 0),
            "auditd_lines_main": c["auditd_main"],
            "hiddenapi_calls": sum(c["hiddenapi"].values()),
            "talsec_calls": sum(v for k2, v in callers.items()
                                if k2.startswith("com.aheaditec.talsec")),
            "secshell_calls": sum(v for k2, v in callers.items()
                                  if k2.startswith("com.SecShell")),
            "native_libs": ";".join(sorted(c["native_libs"])),
            "exceptions": ";".join(f"{k2}x{v}" for k2, v in c["exceptions"].most_common(3)),
        })
    dest = out_csv or (REPO_ROOT / "6_results_data" / "inputs" / "logcat_app_activity.csv")
    with open(dest, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    ok = sum(1 for r in rows if r.get("resolved") == 1)
    print(f"wrote {dest}  ({len(rows)} cells, {ok} resolved, {len(rows)-ok} UNRESOLVED)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile", nargs="?", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BASELINE", "VARIANT"))
    ap.add_argument("--sweep-diff", nargs=2, metavar=("APP", "PHONE"))
    ap.add_argument("--batch", metavar="GLOB",
                    help="parse many cells into a joinable CSV, e.g. 'logcat_20260810_063438_*.log'")
    ap.add_argument("--sweep-tag", metavar="TAG",
                    help="parse exactly one batch's captures, resolved via "
                         "tools/organise_sweep_logs.py. PREFER THIS OVER --batch: a "
                         "batch spans several run timestamps once it has been paused "
                         "and resumed, so no single glob selects it, and a broad glob "
                         "silently mixes batches (2282 files matched where 931 belong).")
    ap.add_argument("--out", type=Path, help="destination CSV for --batch/--sweep-tag")
    ap.add_argument("--run-table", type=Path,
                    help="keep, per cell, only the capture of the run this run table measured "
                         "(a cell re-run in a later session also has the failed attempt's capture)")
    ap.add_argument("--variant", default="freerasp_debug")
    ap.add_argument("--baseline", default="baseline_debug")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap cells per arm (0 = all)")
    args = ap.parse_args()

    if args.sweep_tag:
        return cmd_batch(None, args.out, sweep_tag=args.sweep_tag, run_table=args.run_table)
    if args.batch:
        return cmd_batch(args.batch, args.out, run_table=args.run_table)

    if args.sweep_diff:
        app, phone = args.sweep_diff
        bp = find_cells(app, phone, args.baseline, args.limit)
        vp = find_cells(app, phone, args.variant, args.limit)
        if not bp or not vp:
            print(f"no captures for {app}/{phone} "
                  f"({len(bp)} baseline, {len(vp)} variant)", file=sys.stderr)
            return 1
        print(f"=== {app} on {phone}: {args.baseline} vs {args.variant} ===")
        cmd_compare(bp, vp, args.baseline, args.variant)
        return 0

    if args.compare:
        cmd_compare([args.compare[0]], [args.compare[1]], "baseline", "variant")
        return 0

    if not args.logfile:
        ap.print_help()
        return 2
    c = parse_cell(args.logfile)
    if args.json:
        c["tags"] = dict(c["tags"]); c["levels"] = dict(c["levels"])
        c["hiddenapi"] = dict(c["hiddenapi"]); c["native_libs"] = dict(c["native_libs"])
        c["hiddenapi_callers"] = dict(c["hiddenapi_callers"])
        c["exceptions"] = dict(c["exceptions"])
        print(json.dumps(c, indent=2))
        return 0
    if c.get("unresolved"):
        print(f"{c['path']}: UNRESOLVED — no am_proc_start for a non-infrastructure "
              f"package. Cannot attribute lines to the app.")
        return 1
    print(f"{c['path']}")
    print(f"  packages     {', '.join(c['packages'])}  pids={','.join(c['app_pids'])}")
    print(f"  total lines  {c['total_lines']}")
    print(f"  app lines    {c['app_lines']}  ({100*(1-c['noise_ratio']):.1f}% of file; "
          f"{100*c['noise_ratio']:.1f}% was system noise)")
    print(f"  threads      {c['threads']}   span {c['span_s']:.1f}s   GC {c['gc_events']}")
    print("  top tags     " + ", ".join(f"{t}({n})" for t, n in c["tags"].most_common(8)))
    if c["native_libs"]:
        print("  native libs  " + ", ".join(f"{k}" for k, _ in c["native_libs"].most_common(8)))
    if c["hiddenapi"]:
        print(f"  hidden-API   {sum(c['hiddenapi'].values())} calls")
        for sym, n in c["hiddenapi"].most_common(6):
            print(f"      {n:>3}x {sym}")
        for cls, n in c["hiddenapi_callers"].most_common(4):
            print(f"      caller: {cls} ({n})")
    if c["exceptions"]:
        print("  exceptions   " + ", ".join(f"{k} x{v}" for k, v in c["exceptions"].most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
