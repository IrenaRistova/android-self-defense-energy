#!/usr/bin/env python3
"""Canonical parser for sweep-runner cell logs, per-scenario coverage data,
and hub/AR failure signatures.

This module is the SINGLE SOURCE OF TRUTH for:

  - Parsing scenario pass/fail from cell logs (Format A JSON coverage + Format B
    single-line, including the ``round=N`` variant used by androidskk).
  - Classifying a cell's failure root-cause into a small, defensible set of
    categories (HUB_ABORTED / HUB_RACE_CONTAMINATED / SCENARIO_FAILED /
    AR_INTERNAL / OK).
  - Computing per-cell ``all_scenarios_pass`` boolean for straightforward
    filtering in analysis.

Any new tool that reads a cell log or a state CSV MUST import from here
instead of copy-pasting regexes. Historical loss from copy-pasted-but-
divergent parsers cost the thesis ~600 cells on 2026-07-31 (parser missed
androidskk's ``round=N`` format and misclassified the entire app as broken).
See ``docs/SCENARIO_LOG_FORMATS.md`` for the format reference.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path("/home/irena/Documents/Master Experiment")
LOGS_DIR = REPO_ROOT / "tools" / "sweep_logs"
AR_OUTPUT_DIR = REPO_ROOT / "android-runner" / "examples" / "batterymanager" / "output"

# ---------------------------------------------------------------------------
# Scenario log formats: see docs/SCENARIO_LOG_FORMATS.md
# ---------------------------------------------------------------------------

# Format A: JSON coverage blob emitted by 8 of 11 cohort apps
# (calculator, tipuous, poetskingdom, linkhub, itsok, another_notes,
#  documenter, pdfviewer). Also emitted by build_run_table_extended.py to
# appium_workload_coverage.jsonl.
FMT_A_LOG = re.compile(r"coverage:\s*(\{[^\n]+\})")

# Format B: single-line "scenario NAME [round=N] -> success=X strict=Y wall=..."
# Used by androidskk (with round=N), medtimer, keep_recipe.
# The (?:\s+round=\d+)? is CRITICAL: missing it drops all androidskk cells.
FMT_B_LOG = re.compile(
    r"scenario\s+([A-Za-z0-9_]+)(?:\s+round=\d+)?\s+->\s+success=(True|False)\s+strict=(True|False)"
)


@dataclass
class ScenarioResult:
    name: str
    strict: Optional[bool]
    success: Optional[bool] = None
    source: str = "unknown"      # "A_log" | "B_log" | "A_jsonl"
    suite_round: Optional[int] = None


def parse_scenarios_from_log(path: Path) -> list[ScenarioResult]:
    """Parse a sweep-runner per-cell log for scenario results.

    Handles both Format A (JSON coverage) and Format B (single-line).
    Dedups by scenario_name: first occurrence wins so that Appium's
    duplicated 'appium_android_tests:' prefix lines don't double-count.
    Returns [] on missing/unreadable file or when no scenario data found.
    """
    if path is None or not Path(path).exists():
        return []
    seen: set[str] = set()
    results: list[ScenarioResult] = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if line.startswith("appium_android_tests:"):
                    # Skip Appium's duplicated-with-prefix lines
                    continue
                m = FMT_A_LOG.search(line)
                if m:
                    try:
                        d = json.loads(m.group(1))
                        name = d.get("scenario_name")
                        strict = d.get("scenario_success_strict_ui",
                                       d.get("scenario_success"))
                        if name and name not in seen:
                            seen.add(name)
                            results.append(ScenarioResult(
                                name=name,
                                strict=bool(strict) if strict is not None else None,
                                success=bool(d.get("scenario_success"))
                                    if d.get("scenario_success") is not None else None,
                                source="A_log",
                                suite_round=int(d.get("suite_round"))
                                    if d.get("suite_round") is not None else None,
                            ))
                        continue
                    except Exception:
                        pass
                m = FMT_B_LOG.search(line)
                if m:
                    name, success, strict = m.group(1), m.group(2), m.group(3)
                    if name not in seen:
                        seen.add(name)
                        results.append(ScenarioResult(
                            name=name,
                            strict=(strict == "True"),
                            success=(success == "True"),
                            source="B_log",
                        ))
    except Exception:
        return []
    return results


def parse_scenarios_from_jsonl(path: Path) -> list[ScenarioResult]:
    """Parse AR's appium_workload_coverage.jsonl file (one JSON per line)."""
    if path is None or not Path(path).exists():
        return []
    seen: set[str] = set()
    results: list[ScenarioResult] = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                name = d.get("scenario_name")
                if not name or name in seen:
                    continue
                seen.add(name)
                strict = d.get("scenario_success_strict_ui", d.get("scenario_success"))
                results.append(ScenarioResult(
                    name=name,
                    strict=bool(strict) if strict is not None else None,
                    success=bool(d.get("scenario_success"))
                        if d.get("scenario_success") is not None else None,
                    source="A_jsonl",
                    suite_round=int(d.get("suite_round"))
                        if d.get("suite_round") is not None else None,
                ))
    except Exception:
        return []
    return results


def find_cell_log(phone: str, app: str, variant: str, rep: str,
                  end_ts: Optional[str] = None) -> Optional[Path]:
    """Locate the cell log matching (phone, app, variant, rep).

    When multiple attempts of the same (phone, app, variant, rep) exist
    (common after a retry), the newest-by-mtime lookup would silently return
    the RETRY's log for a canonical row that represents an EARLIER attempt.
    This misclassifies clean PASS cells as HUB_ABORTED because the retry's
    log contains USBHandlerException / hub-race signatures (found 2026-07-31).

    Args:
        end_ts: canonical row's ``ts`` field (ISO8601 UTC, e.g.
            ``2026-07-28T11:46:22Z``). When provided, returns the log whose
            mtime is closest to this timestamp, that is, the log that
            corresponds to the same attempt the canonical row records.
            When None, falls back to newest-by-mtime (legacy behavior).

    Fallback: newest-by-mtime is retained when ``end_ts`` is unparseable
    so we never silently return None on malformed input.
    """
    pattern = f"parallel_*_{phone}_{app}_{variant}_r{rep}.log"
    candidates = list(LOGS_DIR.glob(pattern))
    if not candidates:
        return None
    if end_ts:
        try:
            end_dt = datetime.strptime(end_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc,
            )
            end_epoch = end_dt.timestamp()
            return min(candidates,
                       key=lambda p: abs(p.stat().st_mtime - end_epoch))
        except (ValueError, TypeError):
            pass
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Cell-level scenario verdict
# ---------------------------------------------------------------------------

def all_scenarios_pass(scenarios: Iterable[ScenarioResult]) -> Optional[bool]:
    """True iff every scenario in the iterable reported strict=True.

    Returns None if there are no scenarios (no data to judge).
    """
    scenarios = list(scenarios)
    if not scenarios:
        return None
    return all(s.strict is True for s in scenarios)


def scenario_verdict(scenarios: Iterable[ScenarioResult],
                     planned_count: Optional[int] = None) -> str:
    """Rich verdict for a cell's scenarios.

    - "all_pass"        : every planned scenario strict-passed
    - "some_failed"     : at least one strict=False
    - "count_mismatch"  : fewer scenarios ran than planned_count
    - "no_data"         : no scenarios at all
    """
    scenarios = list(scenarios)
    if not scenarios:
        return "no_data"
    if planned_count is not None and len(scenarios) < planned_count:
        return "count_mismatch"
    if all(s.strict is True for s in scenarios):
        return "all_pass"
    return "some_failed"


# ---------------------------------------------------------------------------
# Cell failure class: 4-bucket root cause
# ---------------------------------------------------------------------------

# Signature regexes for every distinct HUB / ENVIRONMENT / SCENARIO / AR
# failure mode observed in the sweep.
_HUB_SIGNATURES = [
    # HUB_ABORTED (nothing was measured)
    ("HUB_PREFLIGHT_ABORT",   re.compile(r"3/3 discharge attempts all suspected_supplying|hub leak confirmed", re.I)),
    ("HUB_UHUBCTL_TIMEOUT",   re.compile(r"USBHandlerException.*TimeOutError", re.I)),
    ("HUB_LOST_FROM_USB",     re.compile(r"USBHandlerException.*No compatible devices detected", re.I)),
    ("HUB_UHUBCTL_BUSY",      re.compile(r"cannot read port \d+ status.*Resource temporarily unavailable", re.I)),
    ("HUB_AR_ZOMBIE",         re.compile(r"FAIL\(ar_empty_run_id\)", re.I)),
    ("HUB_AR_NO_OUTPUT_DIR",  re.compile(r"FAIL\(ar_no_output_dir\)", re.I)),
    ("HUB_AR_LAUNCH_ERROR",   re.compile(r"FAIL\(ar_launch_error\)", re.I)),
    ("SAFETY_MIN_SOC",        re.compile(r"FAIL\(safety-min-level\)|below SAFETY_MIN_LEVEL", re.I)),
    # HUB_RACE_CONTAMINATED (measured but dirty energy signal)
    ("HUB_RACE_MIDWORKLOAD",  re.compile(r"CONTAM\(float_mode_suspect\)", re.I)),
    ("FUEL_GAUGE_MAINTENANCE",re.compile(r"CONTAM\(fuel_gauge_maintenance\)", re.I)),
    ("AR_DATA_MISSING",       re.compile(r"CONTAM\((no_csv|few_samples)\)", re.I)),
    # SCENARIO_FAILED (energy signal fine, scenario/Appium bug)
    # 2026-07-31: regex extended to catch Format A JSON "scenario_success_strict_ui": false
    # (used by 8/11 cohort apps) in addition to Format B shorthand "strict=False".
    ("SCENARIO_STRICT_FAIL",  re.compile(
        r"STRICT MODE ABORT|scenario.*strict=False|scenario_success_strict_ui\"\s*:\s*false",
        re.I)),
    ("APPIUM_SESSION_DEAD",   re.compile(r"SESSION_CONNECT_FAILED|socket hang up", re.I)),
]

# Roll each specific signature into one of 4 broad buckets
_BUCKET = {
    "HUB_PREFLIGHT_ABORT":    "HUB_ABORTED",
    "HUB_UHUBCTL_TIMEOUT":    "HUB_ABORTED",
    "HUB_LOST_FROM_USB":      "HUB_ABORTED",
    "HUB_UHUBCTL_BUSY":       "HUB_ABORTED",
    "HUB_AR_ZOMBIE":          "HUB_ABORTED",
    "HUB_AR_NO_OUTPUT_DIR":   "HUB_ABORTED",
    "HUB_AR_LAUNCH_ERROR":    "HUB_ABORTED",
    "SAFETY_MIN_SOC":         "HUB_ABORTED",  # env cause but same effect: nothing measured
    "HUB_RACE_MIDWORKLOAD":   "HUB_RACE_CONTAMINATED",
    "FUEL_GAUGE_MAINTENANCE": "HUB_RACE_CONTAMINATED",
    "AR_DATA_MISSING":        "HUB_ABORTED",
    "SCENARIO_STRICT_FAIL":   "SCENARIO_FAILED",
    "APPIUM_SESSION_DEAD":    "SCENARIO_FAILED",
}


# Maximum fraction of a cell's samples allowed to be anything other than
# status=3 (Discharging). A discharge measurement needs the pack discharging for
# essentially the whole window; 0.5 % tolerates a sample or two at a boundary
# without tolerating a phone that was fed mid-cell.
NONDISCHARGE_MAX_FRAC = 0.005

# A cell is discarded at this many app crashes or more.
#
# 2 rather than 1, because the 2026-08-04 sweep contains two cleanly separated
# populations and only one of them is a broken measurement:
#
#   documenter/allatori   5-6 crashes/cell   AbstractMethodError        27 cells
#   keep_recipe (ALL 4)   1-3 crashes/cell   ConcurrentModificationEx   29 cells
#
# The documenter cells are a crash loop: the app dies and relaunches repeatedly,
# so the cell measures restarts rather than the treatment, and every other
# documenter variant crashes exactly 0.00 times: the crashing is specific to the
# obfuscated arm and would enter dE as if it were an obfuscation cost.
#
# The keep_recipe CME is a single non-fatal exception per cell and appears in ALL
# FOUR arms at similar rates (baseline 8, bangcle 9, allatori 6, freerasp 6). It
# is a property of the app, balanced across arms, so it cannot bias dE, and six
# of those cells belong to a null control declared before the series, which we
# need intact.
# Dropping them would cost data symmetrically and buy nothing.
#
# Sensitivity: threshold 1 excludes 56 cells, threshold 2 excludes 31.
APP_CRASH_MAX = 2


def classify_cell_failure_class(cell_log: Optional[Path],
                                 stream_log_fail_tag: Optional[str] = None,
                                 battery_status_mean: Optional[float] = None,
                                 result: str = "PASS",
                                 scenarios: Optional[list["ScenarioResult"]] = None,
                                 run_id: Optional[str] = None,
                                 battery_status_nondischarge_frac: Optional[float] = None,
                                 app_crash_count: Optional[int] = None) -> tuple[str, str]:
    """Classify a cell into one of 5 buckets + return the specific signature.

    Returns (bucket, specific_signature).

    Buckets:
      - "OK"                        cell PASSed with clean data
      - "HUB_ABORTED"               cell never actually measured (hub failed,
                                    safety-min, uhubctl timeout, AR crashed)
      - "HUB_RACE_CONTAMINATED"     cell measured but the fuel gauge caught
                                    charge current mid-workload (dirty energy)
      - "SCENARIO_FAILED"           cell measured, energy clean, but Appium
                                    scenarios strict-failed
      - "AR_INTERNAL"               none of the above matched but cell FAILed
                                    (framework crash without a known signature)
      - "ZOMBIE_NO_RUN_ID"          cell state row has empty run_id, meaning
                                    AR wrote no output dir. Distinct bucket
                                    added 2026-07-31 so consumers can
                                    distinguish this hub-related class from
                                    ordinary hub aborts.

    Args:
      cell_log: path to parallel_..._<phone>_<app>_<variant>_rN.log (or None)
      stream_log_fail_tag: the FAIL(<tag>) string from the master log (or None)
      battery_status_mean: BATTERY_PROPERTY_STATUS mean from Aggregated_Results
                           (or None); < 2.98 = HUB_RACE_MIDWORKLOAD post-hoc
      result: cell-level PASS/FAIL/CONTAM/TIMEOUT from state CSV
      scenarios: optional pre-parsed list of ScenarioResult. When provided,
        overrides regex-based SCENARIO_STRICT_FAIL detection. This fixes the
        Format-A blind spot where cells that strict-failed via JSON coverage
        (8/11 cohort apps) were silently labeled OK because the regex only
        matched Format-B shorthand (found 2026-07-31).
      run_id: canonical row's AR run_id. When "" (empty), promotes to
        ZOMBIE_NO_RUN_ID unconditionally after the fast paths. When None
        (parameter not supplied) this promotion is skipped for backward
        compatibility with callers that don't have run_id in scope.

    CALLER CONTRACT (2026-07-31): callers MUST pre-filter
    ``stream_log_fail_tag`` when ``result == "PASS"`` AND ``run_id`` is a
    non-empty string. Because ``stream_fail_map`` has no per-attempt
    disambiguation, a canonical PASS row could otherwise inherit a stale
    FAIL(<tag>) from an EARLIER attempt of the same (phone,app,variant,rep)
    tuple, and this classifier would then early-return HUB_ABORTED for a
    cell that actually succeeded. See the precedence guard
    (effective_stream_tag) in build_run_table_extended.py for the pattern:
        effective_tag = tag if (result != "PASS" or not run_id) else None
    """
    # Fast path: post-hoc hub-race detection from the aggregated CSV.
    #
    # COUNT-based, not mean-based. BATTERY_PROPERTY_STATUS is categorical
    # (2=Charging, 3=Discharging, 4=Not charging), so a mean lets status=4
    # samples cancel status=2 samples: a cell that is 5 % charging and 6 % not
    # charging averages to ~3.0 and passes a `< 2.98` gate while a tenth of its
    # window is not measuring a discharge at all. Four cells escaped that way in
    # the 2026-08-04 sweep, the worst with 10.6 % non-discharging samples and
    # +336 mA flowing INTO the battery.
    #
    # A discharge measurement requires the pack to be discharging for
    # essentially the whole window, so gate on the fraction that is not.
    if battery_status_nondischarge_frac is not None:
        if battery_status_nondischarge_frac > NONDISCHARGE_MAX_FRAC:
            return ("HUB_RACE_CONTAMINATED", "HUB_RACE_MIDWORKLOAD_RETRO")
    elif battery_status_mean is not None and battery_status_mean < 2.98:
        # Legacy fallback for callers that cannot supply the fraction. Kept so
        # older tools keep working, but it is the weaker test (see above).
        return ("HUB_RACE_CONTAMINATED", "HUB_RACE_MIDWORKLOAD_RETRO")

    # Master-log FAIL tag (fastest signal: sweep_runner already tagged the reason).
    # We run this BEFORE the ZOMBIE promotion below so zombies preserve their
    # specific hub-failure signature (HUB_UHUBCTL_TIMEOUT / HUB_AR_ZOMBIE /
    # etc.) as ``sig`` while still landing in the ZOMBIE_NO_RUN_ID bucket
    # (2026-07-31).
    matched_sig = None
    if stream_log_fail_tag:
        for sig, pattern in _HUB_SIGNATURES:
            if pattern.search(f"FAIL({stream_log_fail_tag})"):
                matched_sig = sig
                if run_id != "":
                    # Not a zombie: return the full (bucket, sig) as before.
                    return (_BUCKET[sig], sig)
                # Zombie with a known tag: fall through to the ZOMBIE branch
                # but keep matched_sig so we return the specific signature.
                break

    # Zombie: state row has empty run_id → AR wrote no output dir. Distinct
    # from HUB_ABORTED (which measured nothing due to a known hub failure).
    # Zombies imply the sweep_runner recorded PASS/FAIL but the AR run
    # itself vanished, usually a pre-fix zombie left over from before the
    # ar_empty_run_id post-cell check landed 2026-07-31.
    if run_id == "":
        return ("ZOMBIE_NO_RUN_ID", matched_sig or "ZOMBIE_NO_RUN_ID")

    # Look inside the cell log for the specific signature (HUB signatures first:
    # a cell that hit a hub-abort AND had partial scenarios should be HUB_ABORTED)
    if cell_log is not None and Path(cell_log).exists():
        try:
            content = Path(cell_log).read_text(errors="replace")
        except Exception:
            content = ""
        for sig, pattern in _HUB_SIGNATURES:
            if pattern.search(content):
                return (_BUCKET[sig], sig)

    # Parser-driven SCENARIO_FAILED detection (works for both Format A and B).
    # Only fires if no hub signature matched: hub issues take precedence.
    if scenarios:
        strict_fails = [s for s in scenarios if s.strict is False]
        if strict_fails:
            return ("SCENARIO_FAILED", "SCENARIO_STRICT_FAIL_PARSED")

    # An app that crashed repeatedly did not run the workload the cell claims to
    # have measured. Checked LAST so a more specific diagnosis wins.
    #
    # This is not hypothetical. Every documenter/allatori cell in the 2026-08-04
    # sweep (27 of them) crashes 5-6 times per cell with AbstractMethodError,
    # while every other documenter variant crashes exactly 0.00 times, and all
    # 27 still reported 8/8 scenarios strict-passed and scientifically_valid=True,
    # because the scorers for those scenarios are relaxed by design and the app
    # relaunches. Those cells measure a crash loop, not obfuscation, and they
    # were silently entering the Allatori arm (removing them moves pooled
    # allatori +1.19 -> +0.69).
    if app_crash_count is not None and app_crash_count >= APP_CRASH_MAX:
        return ("APP_CRASHED", f"APP_CRASH_x{int(app_crash_count)}")

    if result == "PASS":
        return ("OK", "OK")
    return ("AR_INTERNAL", "UNKNOWN")


# ---------------------------------------------------------------------------
# State CSV helpers
# ---------------------------------------------------------------------------

def state_dir(sweep_tag: str) -> Path:
    return LOGS_DIR / f"state_{sweep_tag}"


def load_state_rows(sweep_tag: str) -> list[dict]:
    """Load every row from every phone's state CSV. Adds a 'phone' field."""
    rows: list[dict] = []
    for csv_path in sorted(state_dir(sweep_tag).glob("pixel*.csv")):
        phone = csv_path.stem
        with open(csv_path) as f:
            for r in csv.DictReader(f):
                r["phone"] = phone
                rows.append(r)
    return rows


def canonical_rows(rows: list[dict]) -> dict[tuple, dict]:
    """Return {(phone, app, variant, rep): most-recent PASS row (else newest)}.

    Mirrors build_run_table_extended.py's canonicalization: prefer a PASS row
    over any other, then break ties by latest ts.
    """
    grouped: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["phone"], r["app"], r["variant"], r["rep"])
        grouped.setdefault(key, []).append(r)
    out = {}
    for key, group in grouped.items():
        passes = [r for r in group if r.get("result") == "PASS"]
        pool = passes if passes else group
        out[key] = max(pool, key=lambda r: r.get("ts", ""))
    return out


# ---------------------------------------------------------------------------
# Aggregated_Results_Batterymanager.csv reader
# ---------------------------------------------------------------------------

@dataclass
class AggregatedBM:
    energy_j_trapz: Optional[float] = None
    avg_power_w: Optional[float] = None
    avg_voltage_v: Optional[float] = None
    avg_current_ma: Optional[float] = None
    battery_status_mean: Optional[float] = None


def load_aggregated_bm(run_id: str) -> Optional[AggregatedBM]:
    """Load the Aggregated_Results_Batterymanager.csv for a given AR run_id."""
    if not run_id:
        return None
    p = AR_OUTPUT_DIR / run_id / "Aggregated_Results_Batterymanager.csv"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            for row in csv.DictReader(f):
                def flt(k):
                    try: return float(row[k])
                    except (KeyError, ValueError, TypeError): return None
                return AggregatedBM(
                    energy_j_trapz=flt("Energy trapz (J)"),
                    avg_power_w=flt("Avg power (W)"),
                    avg_voltage_v=flt("EXTRA_VOLTAGE"),
                    avg_current_ma=flt("BATTERY_PROPERTY_CURRENT_NOW"),
                    battery_status_mean=flt("BATTERY_PROPERTY_STATUS"),
                )
    except Exception:
        return None
    return None
