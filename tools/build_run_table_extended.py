#!/usr/bin/env python3
"""build_run_table_extended.py: extended per-cell aggregator for the N=3 sweep.

Pure-function aggregator that consumes:
  * ``tools/sweep_logs/state_<TAG>/<phone>.csv`` (one per phone stream)
  * ``android-runner/examples/batterymanager/output/<run_id>/`` (per-cell AR outputs)
  * ``appium_android_tests/<app>/scenarios.py`` (planned scenario counts)

and produces:
  * ``specs/run_table_extended_<TAG>.csv``: one wide row per canonical
    (sweep_tag, app, phone, variant, rep) cell, sorted so matched-pair
    siblings sit adjacent. The published run tables in
    ``6_results_data/run_tables/`` are copies of these files.
  * ``specs/per_scenario_long_<TAG>.csv``: long-format sibling with
    one row per scenario execution within a canonical cell.
  * ``tools/sweep_logs/state_<TAG>/ingest_report.json``: diagnostic sidecar
    (row counts by verdict, missing run_ids, output sha256s).

Contracts:
  * Idempotent: every invocation rebuilds both CSVs from scratch and atomically
    swaps them into place (write to ``<out>.tmp``, ``fsync``, ``os.replace``).
  * Canonical row per cell = the most recent PASS row if any exists, else the
    newest row overall. All older rows are recorded in the ingest report.
  * ``cell_valid`` is a strict AND across:
      result == 'PASS', crash_anr_status == 'none',
      scenarios_completed == scenarios_planned,
      scenarios_strict_pass_count == scenarios_planned,
      contam_flag empty, energy_source != 'none'.
  * ``rerun_reason`` is a semicolon-joined enumeration of the specific failures
    that flipped ``cell_valid`` to False; empty string otherwise.
  * Exit 0 if every state row is accounted for in the output; exit 2 if any
    canonical row could not be mapped to an AR output directory.

Column set (67 design cols + 2 aggregator-added = 69 total):
  identity:       sweep_tag, app, phone, variant, rep, run_id,
                  cell_start_ts_utc, cell_end_ts_utc
  config:         ar_config_name, apk_path, apk_sha256, apk_storage,
                  android_release, cpu_abilist, appium_workload_mode,
                  scenarios_planned
  verdict:        result, contam_flag, ar_exit_rc, installed,
                  appium_session_created, workload_started, crash_anr_status,
                  scenarios_completed, scenarios_strict_pass_count,
                  scenarios_action_pass_count, scenario_pass_rate_strict,
                  scenario_strict_pass, scenario_action_pass
  durations:      wall_s, duration_whole_window_s, duration_pre_workload_s,
                  duration_workload_s, seconds_per_scenario
  energy:         energy_source, total_cell_joules, workload_joules,
                  pre_workload_joules, teardown_joules, workload_avg_watts,
                  whole_window_avg_watts, aggregated_energy_mwh,
                  energy_workload_only_mwh, energy_per_scenario_joules,
                  energy_per_scenario_mwh
  device_state:   battery_level_pre_pct, battery_level_post_pct,
                  battery_temp_avg_c, battery_temp_max_c, voltage_pre_v,
                    ^^ BOTH NAMES ARE MISLEADING. Read them as
                       `battery_temp_PRE_c`: the ACTION_BATTERY_CHANGED
                       temperature is a sticky broadcast that holds ONE value
                       for the whole cell, so the "avg" is a mean over identical
                       samples and the "max" is that same value. Verified
                       2026-08-30: the two columns are equal to floating-point
                       noise in all 4800 rows of all four run tables. Neither
                       can show a phone heating up DURING a cell -- that is what
                       thermal_status_pre exists for. The columns are NOT renamed
                       because the analysed CSVs are closed and every analysis
                       script reads these names; renaming would invalidate
                       reproduction of published figures for no gain.
                       As a PRE-cell snapshot the column is exactly the right
                       input to a pre-cell covariate, and the name
                       understates it rather than overstating it.
                  current_now_abs_avg_ma, screen_brightness_value,
                  screen_brightness_mode, airplane_mode_on, discharge_verdict
  performance:    cpu_avg_pct, cpu_p95_pct, mem_pss_avg_mb, mem_pss_max_mb,
                  frame_time_median_ms, frame_time_p95_ms,
                  delayed_frames_count, gc_count
  per_scenario:   scenarios_json, scenario_pass_flags
  notes:          issues_flags, notes
  aggregator:     cell_valid, rerun_reason

Usage:
  python3 tools/build_run_table_extended.py \\
      --sweep-tag final_ykush_2026-08-04 \\
      --ar-output-dir android-runner/examples/batterymanager/output \\
      --out specs/run_table_extended_final_ykush_2026-08-04.csv \\
      --per-scenario-out specs/per_scenario_long_final_ykush_2026-08-04.csv

Notes:
  * EXTRA_VOLTAGE in the BatteryManager CSV is millivolts (not microvolts),
    confirmed against real cells; we divide by 1e3.
  * BATTERY_PROPERTY_CURRENT_NOW is signed microamperes; we abs() before the
    trapz energy integral so the Pixel 3 sign-flip artifact (see
    docs/PIXEL3_CURRENT_NOW_SIGN_INVERSION.md) is a display-only concern.
    For the ``current_now_abs_avg_ma`` column we still apply the P3
    sign-mult per the design so downstream signed-current inspection
    matches the modern convention.
  * When multiple ``logcat_*.csv`` files are found under a batterymanager/
    directory we pick the largest by size and flag ``multi_logcat_csvs_picked_largest``.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import importlib
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Canonical parser: single source of truth for cell-log + hub/AR signature
# classification. Wired 2026-07-31.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scenario_log_parser import (
    parse_scenarios_from_log as _parse_scenarios_from_log,
    parse_scenarios_from_jsonl as _parse_scenarios_from_jsonl,
    find_cell_log as _find_cell_log,
    all_scenarios_pass as _all_scenarios_pass,
    classify_cell_failure_class as _classify_cell_failure_class,
)


# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_AR_OUTPUT = REPO_ROOT / "android-runner" / "examples" / "batterymanager" / "output"
DEFAULT_STATE_DIR_TMPL = REPO_ROOT / "tools" / "sweep_logs" / "state_{tag}"
# Default outputs. The published run tables (6_results_data/run_tables/) are
# copies of the run_table_extended_<tag>.csv files written here.
DEFAULT_OUT_TMPL = REPO_ROOT / "specs" / "run_table_extended_{tag}.csv"
DEFAULT_PER_SCENARIO_OUT_TMPL = REPO_ROOT / "specs" / "per_scenario_long_{tag}.csv"
DEFAULT_INGEST_REPORT_TMPL = REPO_ROOT / "tools" / "sweep_logs" / "state_{tag}" / "ingest_report.json"

APPIUM_TESTS_ROOT = REPO_ROOT / "appium_android_tests"

# Phones we recognise as state-CSV stems.
KNOWN_PHONES = ("pixel3", "pixel6", "pixel9")

# 2026-07-27: mirror of the sweep runner's WIFI_ALIAS. Used by
# find_subject_dir to disambiguate when two parallel AR runs collide on
# the same 1-second output-dir name and land in the same run_dir/data/.
# Without this, the aggregator picks aliases[0] alphabetically and both
# state rows sharing the run_id read Pixel 3-W's data (see smoke pilot
# validation_pdfviewer_v4_20260727_1440 workload_j byte-match bug).
WIFI_ALIAS: Dict[str, str] = {
    "pixel3": "Pixel 3-W",
    "pixel6": "Pixel 6-W",
    "pixel9": "Pixel 9-W",
}

# Special-case scenario list variable names that don't match the default
# <APP_UPPER>_SCENARIO_LIST convention.
SCENARIO_LIST_NAME_OVERRIDES: Dict[str, str] = {
    # (all apps in the 11-app cohort resolve via the default rule; keep this
    # dict as an override hook if a future app needs it).
}

# Apps whose planned count multiplies SCENARIO_LIST length by SUITE_ROUNDS.
SCENARIO_ROUNDS_MULTIPLIER: Dict[str, str] = {
    "androidskk": "ANDROIDSKK_SUITE_ROUNDS",
}


# ---------------------------------------------------------------------------
# Canonical column order (67 design cols + 2 aggregator-added).
# ---------------------------------------------------------------------------

RUN_TABLE_COLUMNS: Tuple[str, ...] = (
    # identity
    "sweep_tag", "app", "phone", "variant", "rep", "run_id",
    "cell_start_ts_utc", "cell_end_ts_utc",
    # config
    "ar_config_name", "apk_path", "apk_sha256", "apk_storage",
    "android_release", "cpu_abilist", "appium_workload_mode",
    "scenarios_planned",
    # verdict
    "result", "contam_flag", "ar_exit_rc", "installed",
    "appium_session_created", "workload_started", "crash_anr_status",
    "scenarios_completed", "scenarios_strict_pass_count",
    "scenarios_action_pass_count", "scenario_pass_rate_strict",
    "scenario_strict_pass", "scenario_action_pass",
    # durations
    "wall_s", "duration_whole_window_s", "duration_pre_workload_s",
    "duration_workload_s", "seconds_per_scenario",
    # energy
    "energy_source", "total_cell_joules", "workload_joules",
    "pre_workload_joules", "teardown_joules", "workload_avg_watts",
    "whole_window_avg_watts", "aggregated_energy_mwh",
    "energy_workload_only_mwh", "energy_per_scenario_joules",
    "energy_per_scenario_mwh",
    # device_state
    "battery_level_pre_pct", "battery_level_post_pct",
    "battery_temp_avg_c", "battery_temp_max_c", "voltage_pre_v",
    "current_now_abs_avg_ma", "screen_brightness_value",
    "screen_brightness_mode", "airplane_mode_on", "discharge_verdict",
    # performance
    "cpu_avg_pct", "cpu_p95_pct", "mem_pss_avg_mb", "mem_pss_max_mb",
    "frame_time_median_ms", "frame_time_p95_ms",
    "delayed_frames_count", "gc_count",
    # per-scenario
    "scenarios_json", "scenario_pass_flags",
    # notes
    "issues_flags", "notes",
    # aggregator-added
    "cell_valid", "rerun_reason",
    # aggregator-added 2026-07-31 (parser-derived; safe extension)
    "all_scenarios_pass", "cell_failure_class", "cell_failure_signature",
    # aggregator-added 2026-07-31 (composite convenience column)
    "scientifically_valid",
    # aggregator-added 2026-08-01 (informational; see SOC_BAND_OK)
    "soc_pre_band_ok",
    # aggregator-added 2026-08-04 (see NEGATIVE_CONTROLS)
    "negative_control",
    # aggregator-added 2026-08-05 (see count_app_crashes)
    "app_crash_count", "app_crash_signature",
    # aggregator-added 2026-08-10 (see portwatch_midcell_on). Board-side count of
    # ON samples that are NOT the trailing teardown run. >0 invalidates the cell.
    # Empty means no port-watch log existed for that cell, which is NOT evidence
    # that the port stayed off.
    "portwatch_midcell_on",
    # aggregator-added 2026-08-10 (see parse_thermal_pre_cell). Android's OWN
    # throttling state at cell start, plus the live skin/CPU temperatures.
    # 0=NONE 1=LIGHT 2=MODERATE 3=SEVERE. Unlike battery_temp_avg_c (a sticky
    # broadcast frozen for the whole cell), these are sampled at cell start from
    # `dumpsys thermalservice`. This is the thermal covariate batch 1 never had.
    "thermal_status_pre", "thermal_skin_pre_c", "thermal_cpu_pre_c",
)

# Processes that legitimately appear in a cell's logcat but are NOT the app
# under test. `svc` (bluetooth disable) crashes on every phone in every cell,
# and counting it would make every cell look broken.
NON_APP_CRASH_PROCESSES = frozenset({
    "com.example.batterymanager_utility",
    "io.appium.settings",
    "io.appium.uiautomator2.server",
    "io.appium.uiautomator2.server.test",
})

# Negative controls declared before the series (2026-08-04). Keyed by
# (app, variant). These are build properties, not run properties, so they are a
# fixed lookup rather than something measured per cell.
#
# TWO DIFFERENT KINDS OF NULL, and the distinction matters for the write-up:
#   exact_null        0 classes / 0 methods / 0 INSTRUCTIONS changed. The
#                     executed bytecode is byte-identical; only the DEX
#                     container was re-emitted (keep_recipe: +4.55 % dex bytes
#                     across all 15 dex files, each ~4-5 % larger).
#   shrink_only_null  0 identifiers renamed, but unused code DELETED: linkhub
#                     is 52 classes gone / 0 new, i.e. deletion, not renaming
#                     (renaming shows up as N gone + N new). -0.17 %
#                     instructions, and the removed code was unreachable, so it
#                     never executed in the first place.
#
# NEITHER tests obfuscation. Both measure the pipeline's NOISE FLOOR, and both
# must be excluded from any cohort-wide allatori mean. batch1 had no way to
# express this, so the two were silently pooled in with the apps that really are
# obfuscated (poetskingdom renames 490 classes, medtimer 430), which drags the
# allatori arm's mean toward zero for a reason that has nothing to do with
# obfuscation cost.
NEGATIVE_CONTROLS: Dict[Tuple[str, str], str] = {
    ("keep_recipe", "allatori_debug"): "exact_null",
    ("linkhub", "allatori_debug"): "shrink_only_null",
}

# Reference band for `soc_pre_band_ok`. Chosen to contain the batch1 valid-cell
# distribution (26–82%, median 46%) rather than to impose a new constraint on
# it. Cells run on 2026-08-01 start near 100% because the phones were
# wall-charged; they drain into this band over the first ~40 cells per phone.
SOC_BAND_OK: Tuple[int, int] = (25, 75)

PER_SCENARIO_COLUMNS: Tuple[str, ...] = (
    "sweep_tag", "app", "phone", "variant", "rep",
    "scenario_name", "suite_round", "ts", "ts_end",
    "scenario_wall_seconds", "scenario_success",
    "scenario_success_strict_ui", "scenario_success_action_only",
)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_iso8601_utc(s: str) -> Optional[datetime]:
    """Parse ``2026-07-01T09:08:16Z`` or similar into a tz-aware UTC datetime."""
    if not s:
        return None
    s = s.strip()
    # Normalise trailing Z -> +00:00 for fromisoformat.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso8601_utc(dt: datetime) -> str:
    """Round-trip friendly ``2026-07-01T09:08:16Z``."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(value: Any, default: Any = "") -> Any:
    if value is None or value == "":
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: Any = "") -> Any:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _atomic_write_csv(df: pd.DataFrame, out_path: Path) -> str:
    """Write ``df`` to ``out_path`` atomically; return sha256 hex."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    with open(tmp, "rb+") as f:
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return _sha256_file(out_path)


# ---------------------------------------------------------------------------
# State-CSV loading + canonical row selection
# ---------------------------------------------------------------------------

def load_state_rows(state_dir: Path) -> List[Dict[str, Any]]:
    """Return raw state rows across all per-phone CSVs in ``state_dir``.

    Each row is annotated with ``phone`` (from the CSV filename stem) and
    ``ts_parsed`` (datetime | None). Missing files are silently skipped.
    """
    rows: List[Dict[str, Any]] = []
    if not state_dir.is_dir():
        return rows
    for phone in KNOWN_PHONES:
        csv_path = state_dir / f"{phone}.csv"
        if not csv_path.is_file():
            continue
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                r = dict(r)
                r["phone"] = phone
                r["ts_parsed"] = _parse_iso8601_utc(r.get("ts", ""))
                try:
                    r["rep"] = int(r.get("rep", "") or 0)
                except (TypeError, ValueError):
                    r["rep"] = 0
                try:
                    r["wall_s"] = float(r.get("wall_s", "") or 0.0)
                except (TypeError, ValueError):
                    r["wall_s"] = 0.0
                rows.append(r)
    return rows


def pick_canonical_rows(state_rows: List[Dict[str, Any]]) -> Tuple[
    List[Dict[str, Any]], Dict[Tuple[str, str, str, int], List[Dict[str, Any]]]
]:
    """Reduce state rows to one canonical row per (app, phone, variant, rep).

    Returns ``(canonical_rows, siblings_by_key)``, where ``siblings_by_key`` maps
    each key to the full list of state rows (canonical + duplicates) so the
    ingest report can enumerate re-runs.
    """
    groups: Dict[Tuple[str, str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for r in state_rows:
        key = (r.get("app", ""), r.get("phone", ""), r.get("variant", ""), int(r.get("rep", 0)))
        groups[key].append(r)

    canonical: List[Dict[str, Any]] = []
    for key, members in groups.items():
        # PASS rows: newest-ts wins; else newest overall.
        passes = [m for m in members if (m.get("result") or "").strip() == "PASS"]
        if passes:
            pick = max(passes, key=lambda m: m.get("ts_parsed") or datetime.min.replace(tzinfo=timezone.utc))
        else:
            pick = max(members, key=lambda m: m.get("ts_parsed") or datetime.min.replace(tzinfo=timezone.utc))
        canonical.append(pick)
    return canonical, groups


# ---------------------------------------------------------------------------
# Scenario-list resolution (planned N per app)
# ---------------------------------------------------------------------------

_SCENARIO_LIST_CACHE: Dict[str, Tuple[Tuple[str, ...], int]] = {}


def _app_scenario_var_names(app: str) -> Tuple[str, Optional[str]]:
    """Return (list_var_name, rounds_var_name_or_None) for ``app``."""
    if app in SCENARIO_LIST_NAME_OVERRIDES:
        list_name = SCENARIO_LIST_NAME_OVERRIDES[app]
    else:
        list_name = f"{app.upper()}_SCENARIO_LIST"
    rounds_name = SCENARIO_ROUNDS_MULTIPLIER.get(app)
    return list_name, rounds_name


def resolve_planned_scenarios(app: str) -> Tuple[Tuple[str, ...], int]:
    """Return ``(scenario_list, planned_total)`` for ``app``.

    ``planned_total`` = ``len(scenario_list) * rounds`` where ``rounds`` is
    read from the app's ``<APP>_SUITE_ROUNDS`` variable when applicable
    (currently only androidskk), else 1.

    Returns ``((), 0)`` if the app module can't be imported or the variable
    is missing.
    """
    if app in _SCENARIO_LIST_CACHE:
        return _SCENARIO_LIST_CACHE[app]

    list_name, rounds_name = _app_scenario_var_names(app)
    scenario_list: Tuple[str, ...] = ()
    planned_total = 0

    module_path = f"appium_android_tests.{app}.scenarios"
    repo_root_str = str(REPO_ROOT)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    try:
        mod = importlib.import_module(module_path)
        raw_list = getattr(mod, list_name, None)
        if raw_list is None:
            _SCENARIO_LIST_CACHE[app] = ((), 0)
            return _SCENARIO_LIST_CACHE[app]
        scenario_list = tuple(str(x) for x in raw_list)
        rounds = 1
        if rounds_name:
            rounds = int(getattr(mod, rounds_name, 1) or 1)
        planned_total = len(scenario_list) * rounds
    except Exception:
        scenario_list = ()
        planned_total = 0

    _SCENARIO_LIST_CACHE[app] = (scenario_list, planned_total)
    return _SCENARIO_LIST_CACHE[app]


# ---------------------------------------------------------------------------
# AR per-cell artifact discovery
# ---------------------------------------------------------------------------

def find_run_dir(ar_output_dir: Path, run_id: str) -> Optional[Path]:
    if not run_id:
        return None
    p = ar_output_dir / run_id
    if p.is_dir():
        return p
    return None


def find_subject_dir(
    run_dir: Path,
    phone_alias: Optional[str] = None,
) -> Tuple[Optional[Path], Optional[str], List[str]]:
    """Return ``(subject_dir, wifi_alias, extra_flags)`` for a run dir.

    2026-07-27: when ``phone_alias`` is given, ONLY match that alias in
    ``run_dir/data/*/``. This defends against AR's 1-second output-dir
    collision where two parallel sweep_runner streams share the same
    ``run_dir``; the per-phone data is intact under distinct ``data/<alias>/``
    subdirs but the old ``aliases[0]`` picker silently returned Pixel 3-W's
    subject to every cell. Emits ``run_dir_collision`` and
    ``phone_alias_absent`` issues_flags for visibility.
    """
    extra: List[str] = []
    data = run_dir / "data"
    if not data.is_dir():
        return None, None, extra
    aliases = sorted(p for p in data.iterdir() if p.is_dir())
    if not aliases:
        return None, None, extra
    if len(aliases) > 1:
        extra.append(f"run_dir_collision:{len(aliases)}aliases")
    if phone_alias:
        matched = [p for p in aliases if p.name == phone_alias]
        if not matched:
            extra.append(f"phone_alias_absent:{phone_alias}")
            return None, None, extra
        alias_dir = matched[0]
    else:
        alias_dir = aliases[0]
    subjects = sorted(p for p in alias_dir.iterdir() if p.is_dir())
    if not subjects:
        return None, alias_dir.name, extra
    return subjects[0], alias_dir.name, extra


def load_json_safe(path: Path) -> Optional[Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pick_largest(paths: Sequence[Path]) -> Optional[Path]:
    valid = [p for p in paths if p.is_file()]
    if not valid:
        return None
    return max(valid, key=lambda p: p.stat().st_size)


# ---------------------------------------------------------------------------
# Stream-log contam_flag extraction (2026-07-27 audit fix)
# ---------------------------------------------------------------------------
_CONTAM_RE = re.compile(
    r"DONE\s+(\w+)\s+([^/\s]+)/([^\s]+)\s+rep(\d+)\s+CONTAM\(([^)]+)\)"
)
# 2026-07-31: FAIL(<tag>) tags live in phone-level stream logs
# (parallel_*_pixel*.log without the _app_variant_rep suffix); per-cell logs
# never carry them. Without this map the classifier's SAFETY_MIN_SOC / hub
# signatures were unreachable for CONTAM/FAIL rows.
_FAIL_RE = re.compile(
    r"DONE\s+(\w+)\s+([^/\s]+)/([^\s]+)\s+rep(\d+)\s+FAIL\(([^)]+)\)"
)
_STREAM_CONTAM_CACHE: Dict[str, Dict[Tuple[str, str, str, int], str]] = {}
_STREAM_FAIL_CACHE: Dict[str, Dict[Tuple[str, str, str, int], str]] = {}


def load_stream_contam_map(log_dir: Path) -> Dict[Tuple[str, str, str, int], str]:
    """Scan all sweep-runner stream logs under ``log_dir`` and return a dict
    keyed by ``(phone, app, variant, rep)`` -> ``contam_flag`` (the parenthetical
    detail from ``CONTAM(<flag>)`` lines). When the same key appears in multiple
    logs, the newest log's value wins (matches sweep_runner's on-disk state).
    Sweep-runner writes lines like:
        [HH:MM:SS] DONE  pixel3 medtimer/baseline_debug rep2 CONTAM(float_mode_suspect) wall=226s ...
    """
    cache_key = str(log_dir.resolve())
    if cache_key in _STREAM_CONTAM_CACHE:
        return _STREAM_CONTAM_CACHE[cache_key]
    result: Dict[Tuple[str, str, str, int], str] = {}
    if not log_dir.is_dir():
        _STREAM_CONTAM_CACHE[cache_key] = result
        return result
    # newest-first so newer entries overwrite older ones inside the loop
    logs = sorted(log_dir.glob("parallel_*_pixel*.log"),
                  key=lambda p: p.stat().st_mtime)
    for log_path in logs:
        try:
            with open(log_path, "r", errors="replace") as f:
                for line in f:
                    m = _CONTAM_RE.search(line)
                    if not m:
                        continue
                    phone, app, variant, rep_str, flag = m.groups()
                    try:
                        rep = int(rep_str)
                    except ValueError:
                        continue
                    result[(phone, app, variant, rep)] = flag
        except OSError:
            continue
    _STREAM_CONTAM_CACHE[cache_key] = result
    return result


def load_stream_fail_map(log_dir: Path) -> Dict[Tuple[str, str, str, int], str]:
    """Scan sweep-runner stream logs for FAIL(<tag>) lines and return a dict
    keyed by (phone, app, variant, rep) -> fail_tag (the parenthetical detail).
    Newest log wins on duplicate keys. Mirrors load_stream_contam_map's
    semantics. Added 2026-07-31.
    """
    cache_key = str(log_dir.resolve())
    if cache_key in _STREAM_FAIL_CACHE:
        return _STREAM_FAIL_CACHE[cache_key]
    result: Dict[Tuple[str, str, str, int], str] = {}
    if not log_dir.is_dir():
        _STREAM_FAIL_CACHE[cache_key] = result
        return result
    logs = sorted(log_dir.glob("parallel_*_pixel*.log"),
                  key=lambda p: p.stat().st_mtime)
    for log_path in logs:
        try:
            with open(log_path, "r", errors="replace") as f:
                for line in f:
                    m = _FAIL_RE.search(line)
                    if not m:
                        continue
                    phone, app, variant, rep_str, flag = m.groups()
                    try:
                        rep = int(rep_str)
                    except ValueError:
                        continue
                    result[(phone, app, variant, rep)] = flag
        except OSError:
            continue
    _STREAM_FAIL_CACHE[cache_key] = result
    return result


def find_bm_csv(subject_dir: Path) -> Tuple[Optional[Path], bool]:
    """Return ``(bm_csv, multi_flag)`` where ``multi_flag`` is True if we
    had to pick among multiple ``logcat_*.csv`` files."""
    bm_dir = subject_dir / "batterymanager"
    if not bm_dir.is_dir():
        return None, False
    candidates = sorted(bm_dir.glob("logcat_*.csv")) + sorted(bm_dir.glob("run_*.csv"))
    if not candidates:
        return None, False
    multi = len(candidates) > 1
    return _pick_largest(candidates), multi


def find_first_glob(directory: Path, pattern: str) -> Optional[Path]:
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Per-cell numerics: BatteryManager samples, energy windows
# ---------------------------------------------------------------------------

def load_bm_samples(bm_csv: Path, phone: str) -> Optional[pd.DataFrame]:
    """Load the BatteryManager per-sample CSV, apply P3 sign-fix, and return
    a DataFrame with columns:
        ts_ms (int), t_s (float, from first sample),
        current_uA_raw (signed, P3-flipped so negative = discharge),
        current_A_abs (float), voltage_V (float), power_W (float),
        capacity_pct (int|nan), temperature_c (float),
        status (int|nan).

    Returns None on parse failure or empty file.
    """
    try:
        df = pd.read_csv(bm_csv)
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    if df.empty:
        return None

    required = {"Timestamp", "BATTERY_PROPERTY_CURRENT_NOW", "EXTRA_VOLTAGE"}
    if not required.issubset(df.columns):
        return None

    # Coerce numerics.
    df["ts_ms"] = pd.to_numeric(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["ts_ms"]).copy()
    df["ts_ms"] = df["ts_ms"].astype("int64")

    current_uA = pd.to_numeric(df["BATTERY_PROPERTY_CURRENT_NOW"], errors="coerce")
    voltage_mV = pd.to_numeric(df["EXTRA_VOLTAGE"], errors="coerce")

    # Pixel 3 legacy sign-flip: multiply raw current by -1 so that negative
    # means discharge (matches Pixel 6/9 modern convention). Energy uses
    # abs() below so this is display-only, but downstream code that inspects
    # sign should see a consistent convention.
    sign_mult = -1 if phone == "pixel3" else +1
    df["current_uA_raw"] = current_uA * sign_mult
    df["current_A_abs"] = current_uA.abs() / 1e6
    # EXTRA_VOLTAGE is millivolts (verified against real cells 2026-07-27).
    df["voltage_V"] = voltage_mV / 1e3
    df["power_W"] = df["current_A_abs"] * df["voltage_V"]

    df["t_s"] = (df["ts_ms"] - df["ts_ms"].iloc[0]) / 1000.0

    if "BATTERY_PROPERTY_CAPACITY" in df.columns:
        df["capacity_pct"] = pd.to_numeric(df["BATTERY_PROPERTY_CAPACITY"], errors="coerce")
    else:
        df["capacity_pct"] = float("nan")
    if "EXTRA_TEMPERATURE" in df.columns:
        df["temperature_c"] = pd.to_numeric(df["EXTRA_TEMPERATURE"], errors="coerce") / 10.0
    else:
        df["temperature_c"] = float("nan")
    if "BATTERY_PROPERTY_STATUS" in df.columns:
        df["status"] = pd.to_numeric(df["BATTERY_PROPERTY_STATUS"], errors="coerce")
    else:
        df["status"] = float("nan")

    return df.reset_index(drop=True)


def trapz_over_window(df: pd.DataFrame, ts_ms_lo: Optional[int],
                      ts_ms_hi: Optional[int]) -> Optional[float]:
    """Trapezoidal integral of ``power_W`` over the sample window bounded by
    ``ts_ms_lo`` / ``ts_ms_hi`` (inclusive of both). Returns None if fewer
    than 2 samples remain."""
    sel = df
    if ts_ms_lo is not None:
        sel = sel[sel["ts_ms"] >= ts_ms_lo]
    if ts_ms_hi is not None:
        sel = sel[sel["ts_ms"] <= ts_ms_hi]
    if len(sel) < 2:
        return None
    t_s = (sel["ts_ms"].to_numpy() - sel["ts_ms"].iloc[0]) / 1000.0
    p_W = sel["power_W"].to_numpy()
    # Manual trapz to avoid depending on numpy.trapz (deprecated in numpy 2).
    energy_j = 0.0
    for i in range(1, len(t_s)):
        dt = t_s[i] - t_s[i - 1]
        if dt <= 0:
            continue
        energy_j += dt * (p_W[i] + p_W[i - 1]) / 2.0
    return energy_j


# ---------------------------------------------------------------------------
# Appium coverage jsonl + scenario_report loading
# ---------------------------------------------------------------------------

def load_coverage_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def find_scenario_report(subject_dir: Path) -> Optional[Path]:
    matches = sorted(subject_dir.glob("*_scenario_report.json"))
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Aux / frametimes / GC loaders
# ---------------------------------------------------------------------------

def load_aux_summary(subject_dir: Path) -> Optional[Dict[str, Any]]:
    return load_json_safe(subject_dir / "aux" / "aux_summary.json")


def load_frametimes_stats(subject_dir: Path) -> Tuple[Any, Any, Any]:
    """Return ``(median_ms, p95_ms, delayed_count)``. Blanks if files absent."""
    ft_dir = subject_dir / "frametimes"
    ft_csv = find_first_glob(ft_dir, "frame_times_*.csv")
    delayed_csv = find_first_glob(ft_dir, "delayed_*.csv")

    median_ms: Any = ""
    p95_ms: Any = ""
    delayed_count: Any = ""

    if ft_csv is not None:
        try:
            df = pd.read_csv(ft_csv)
            if "frame_time" in df.columns and not df.empty:
                # frame_time is nanoseconds: convert to ms.
                s = pd.to_numeric(df["frame_time"], errors="coerce").dropna() / 1e6
                if not s.empty:
                    median_ms = float(s.median())
                    p95_ms = float(s.quantile(0.95))
        except (OSError, ValueError, pd.errors.ParserError):
            pass
    if delayed_csv is not None:
        try:
            df = pd.read_csv(delayed_csv)
            if "delayed_frames_count" in df.columns and not df.empty:
                delayed_count = int(df["delayed_frames_count"].iloc[0])
        except (OSError, ValueError, pd.errors.ParserError):
            pass
    return median_ms, p95_ms, delayed_count


def load_gc_count(subject_dir: Path) -> Any:
    gc_dir = subject_dir / "garbagecollection"
    total_csv = find_first_glob(gc_dir, "total_*.csv")
    if total_csv is None:
        return ""
    try:
        df = pd.read_csv(total_csv)
        if "garbage_collection_count" in df.columns and not df.empty:
            return int(df["garbage_collection_count"].iloc[0])
    except (OSError, ValueError, pd.errors.ParserError):
        pass
    return ""


# ---------------------------------------------------------------------------
# experiment.log installed-check
# ---------------------------------------------------------------------------

_INSTALL_FAILED_RE = re.compile(r"INSTALL_FAILED_[A-Z_]+")
_AR_ERROR_RE = re.compile(r"AdbError|Could not launch|NativeExperiment:.*Error")


def scan_experiment_log(experiment_log: Path) -> Dict[str, Any]:
    """Return {installed: bool, ar_error_class: str}"""
    result = {"installed": True, "ar_error_class": ""}
    if not experiment_log.is_file():
        return result
    try:
        with open(experiment_log, "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return result
    install_hit = _INSTALL_FAILED_RE.search(text)
    if install_hit:
        result["installed"] = False
        result["ar_error_class"] = install_hit.group(0)
        return result
    err_hit = _AR_ERROR_RE.search(text)
    if err_hit:
        result["ar_error_class"] = err_hit.group(0)
    return result


# ---------------------------------------------------------------------------
# Aggregated_Results_Batterymanager.csv fallback
# ---------------------------------------------------------------------------

def load_aggregated_bm(run_dir: Path) -> Optional[Dict[str, Any]]:
    p = run_dir / "Aggregated_Results_Batterymanager.csv"
    if not p.is_file():
        return None
    try:
        df = pd.read_csv(p)
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    out: Dict[str, Any] = {}
    for key, dst in (
        ("Energy trapz (J)", "energy_trapz_j"),
        ("Energy simple (J)", "energy_simple_j"),
        ("Avg power (W)", "avg_power_w"),
        ("EXTRA_TEMPERATURE", "extra_temp"),
        ("EXTRA_VOLTAGE", "extra_voltage_mv"),
        ("BATTERY_PROPERTY_CURRENT_NOW", "current_now_ua"),
    ):
        out[dst] = _safe_float(row.get(key), default=None)
    return out


def load_aggregated_android(run_dir: Path) -> Optional[Dict[str, Any]]:
    p = run_dir / "Aggregated_Results_Android.csv"
    if not p.is_file():
        return None
    try:
        df = pd.read_csv(p)
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    return {
        "android_cpu": _safe_float(row.get("android_cpu"), default=None),
        "android_mem": _safe_float(row.get("android_mem"), default=None),
    }


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------

def _issues_join(flags: List[str]) -> str:
    seen = []
    for f in flags:
        if f and f not in seen:
            seen.append(f)
    return ";".join(seen)


def _boolish(value: Any) -> Any:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return ""


LOGCAT_DIR = REPO_ROOT / "tools" / "sweep_logs" / "logcat"
SWEEP_LOGS_DIR = REPO_ROOT / "tools" / "sweep_logs"


_THERMAL_CACHE: Dict[tuple, Dict[tuple, dict]] = {}


def parse_thermal_pre_cell(phone: str, sweep_tag: str = "") -> Dict[tuple, dict]:
    """Map (app, variant, rep) -> the thermal reading taken at that cell's start.

    tools/thermal_gate.sh writes a JSON line into the per-phone STREAM log
    immediately after each START, e.g.

        [00:11:54] START pixel3 pdfviewer/freerasp_debug rep2 cfg=...
          THERMAL pre-cell: {"phone":"pixel3","thermal_status":0,"skin_c":34.6,...}

    Getting it into the run table matters because it is the covariate batch 1
    lacked. `battery_temp_avg_c` is a sticky broadcast frozen for the whole cell
    and the pack runs ~10 C below the SoC, so it could never say whether a cell
    was measured on a throttling phone. `thermal_status` is the framework's own
    answer to exactly that question.

    Later runs win: a re-measured cell should carry the thermal state of the
    attempt that produced the data now in the table.

    SCOPED BY SWEEP TAG (fixed 2026-08-30). This used to glob
    ``parallel_*_<phone>.log`` across every sweep ever run. Cells are keyed by
    (app, variant, rep), which repeats in every batch, so later batches
    overwrote earlier ones and the PILOT table came out carrying SERIES 1's
    thermal readings for all 1200 of its rows -- 1200/1200 of its
    ``thermal_status_pre`` values matched series 1 while 0/1200 of its joule
    values did. Every pilot figure computed from those three columns is void.
    Use ``resolve_logs_for_tag``, the supported accessor, exactly as the logcat
    join in ``count_app_crashes`` already does.

    A missing tag is reported and yields NO readings rather than falling back to
    the old glob: a join that silently widens its own scope is the failure this
    bug already caused once.
    """
    ck = (phone, sweep_tag)
    if ck in _THERMAL_CACHE:
        return _THERMAL_CACHE[ck]
    out: Dict[tuple, dict] = {}
    if not sweep_tag:
        print("  thermal: no sweep tag given -- refusing to glob every sweep's "
              "logs; thermal_* columns will be blank", file=sys.stderr)
        _THERMAL_CACHE[ck] = out
        return out
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from organise_sweep_logs import resolve_logs_for_tag
        logs = [p for p in resolve_logs_for_tag(sweep_tag, kind="stream")
                if re.search(rf"_{re.escape(phone)}\.log$", p.name)]
    except Exception as exc:
        print(f"  thermal: cannot resolve logs for tag {sweep_tag!r} ({exc}); "
              "thermal_* columns will be blank", file=sys.stderr)
        _THERMAL_CACHE[ck] = out
        return out
    pat = re.compile(
        r"START\s+\S+\s+([A-Za-z0-9_]+)/([A-Za-z0-9_]+)\s+rep(\d+)")
    for log in sorted(logs):
        if "_appium_" in log.name:
            continue
        key = None
        try:
            for line in log.read_text(errors="replace").splitlines():
                m = pat.search(line)
                if m:
                    key = (m.group(1), m.group(2), m.group(3))
                    continue
                if key and "THERMAL pre-cell:" in line:
                    blob = line.split("THERMAL pre-cell:", 1)[1].strip()
                    try:
                        d = json.loads(blob)
                    except Exception:
                        key = None
                        continue
                    if d.get("ok"):
                        out[key] = d
                    key = None
        except OSError:
            continue
    _THERMAL_CACHE[ck] = out
    return out


def portwatch_midcell_on(phone: str, app: str, variant: str, rep: Any,
                         cell_end_ts: Optional[str] = None) -> Optional[int]:
    """Count ON samples that are NOT part of the trailing teardown run.

    tools/ykush_port_watch.sh samples the BOARD's port state for the duration of
    a cell. Until now nothing read those logs back into cell validity: the
    sweep runner printed a verdict to its stream log and that was the end of it.

    That gap let a genuinely contaminated cell through on 2026-08-10:
    pixel6 poetskingdom/allatori rep2 had VBUS reach the phone for 2 of 70
    samples mid-measurement, and still finished PASS with
    scientifically_valid=True, because the CONTAM classifier reads the phone's
    BATTERY_PROPERTY_STATUS and that particular contamination did not move it far
    enough. The board-side observation is the stronger evidence of the two (it
    cannot be fooled by a charger driver's opinion), so it should be able to
    invalidate a cell on its own.

    A STRICTLY trailing ON run is teardown, not contamination: AR calls
    enable_usb() at Experiment.py:207, before after_run, while the watcher is
    still sampling. Every healthy cell ends that way. Only ON samples before
    that trailing run count. Same rule as the PORT WATCH verdict in
    tools/sweep_runner_parallel_ykush.sh.

    Returns the mid-cell ON count, or None when no log exists (older sweeps ran
    before the port watch was added; absence must not read as evidence of
    cleanliness).
    """
    pattern = f"portwatch_*_{phone}_{app}_{variant}_r{rep}.log"
    candidates = sorted(SWEEP_LOGS_DIR.glob(pattern))
    if not candidates:
        return None
    # Several attempts of the same cell leave several logs. Pick the one whose
    # mtime is closest to this row's end timestamp, so a retry's log is not
    # attributed to the attempt the canonical row actually records.
    chosen = candidates[-1]
    if cell_end_ts and len(candidates) > 1:
        target = _parse_iso8601_utc(cell_end_ts)
        if target is not None:
            chosen = min(candidates,
                         key=lambda p: abs(p.stat().st_mtime - target.timestamp()))
    try:
        states = [ln.split()[1] for ln in chosen.read_text(errors="replace").splitlines()
                  if ln.startswith("20") and len(ln.split()) >= 2]
    except OSError:
        return None
    if not states:
        return None
    trailing = 0
    for s in reversed(states):
        if s == "ON":
            trailing += 1
        else:
            break
    body = states[:len(states) - trailing] if trailing else states
    return sum(1 for s in body if s == "ON")


def count_app_crashes(app: str, phone: str, variant: str, rep: Any,
                      cell_end_dt: Any, sweep_tag: str = "") -> Tuple[Any, str]:
    """Count crashes of the APP UNDER TEST in this cell's logcat.

    Added 2026-08-05. Nothing in the pipeline detected app crashes, and the
    scenario scorers cannot: documenter/allatori crashed **12 times across 2
    cells while scoring 100 % strict**, because its scorers are deliberately
    relaxed. keep_recipe likewise crashed 2-3 times per cell on every variant
    including baseline and still reported PASS. A cell where the app repeatedly
    died and cold-restarted is not measuring the workload it claims to.

    Attribution is by the `Process:` line of each FATAL EXCEPTION block, NOT by
    grepping "FATAL EXCEPTION": 23 of the 27 crashing cells in the first sweep
    were `svc bluetooth disable` dying in its own short-lived process, which is
    harmless (the disable transaction had already completed and the device-state
    script verifies it with a read-back).

    Returns (count, signature) where signature is a compact
    "Exception xN" summary, or ("", "") when no logcat can be located: absence
    of a log is reported as UNKNOWN, never as zero.
    """
    if not LOGCAT_DIR.is_dir():
        return "", ""
    pattern = f"logcat_*_{phone}_{app}_{variant}_r{rep}.log"
    hits = sorted(LOGCAT_DIR.glob(pattern))

    # RESTRICT TO THIS SWEEP'S OWN CAPTURES FIRST (2026-08-13).
    #
    # The filename carries the RUN timestamp, not the sweep tag, so the glob
    # above matches every batch that ever measured this (phone, app, variant,
    # rep): the pilot holds 1219 captures and series batch 1 holds 931, with
    # heavy key overlap. The old fallback below picks whichever file's mtime is
    # nearest the cell's end, which is a heuristic: it is usually right, and
    # nothing makes it right. Picking a pilot capture for a series cell would
    # feed the wrong crash count into APP_CRASHED, and that gate decides whether
    # the cell enters the analysis at all.
    #
    # organise_sweep_logs.py recovers the run_ts -> sweep_tag mapping exactly,
    # from the tag each wrapper log states in its own header. When it can, we
    # intersect on that and the choice becomes deterministic.
    if sweep_tag and len(hits) > 1:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from organise_sweep_logs import resolve_logs_for_tag
            own = {p.name for p in resolve_logs_for_tag(sweep_tag, kind="logcat")}
            scoped = [p for p in hits if p.name in own]
            if scoped:
                hits = scoped
        except Exception:
            pass  # index unavailable: fall through to the mtime heuristic

    if not hits:
        return "", ""
    # Fallback when the tag could not scope it: pick the capture whose mtime is
    # closest to this cell's end.
    chosen = hits[-1]
    if cell_end_dt is not None and len(hits) > 1:
        target = cell_end_dt.timestamp()
        chosen = min(hits, key=lambda p: abs(p.stat().st_mtime - target))
    try:
        lines = chosen.read_text(errors="replace").splitlines()
    except OSError:
        return "", ""
    n = 0
    excs: Dict[str, int] = {}
    for i, line in enumerate(lines):
        if "FATAL EXCEPTION" not in line:
            continue
        proc = exc = None
        for j in range(i + 1, min(i + 6, len(lines))):
            m = re.search(r"E AndroidRuntime:\s+Process:\s+([\w.]+)", lines[j])
            if m:
                proc = m.group(1)
            m2 = re.search(r"E AndroidRuntime:\s+([a-z][\w.$]*(?:Exception|Error))",
                           lines[j])
            if m2 and exc is None:
                exc = m2.group(1)
        # No Process: line at all => a standalone shell command (e.g. `svc`).
        if not proc or proc in NON_APP_CRASH_PROCESSES:
            continue
        n += 1
        key = (exc or "Unknown").rsplit(".", 1)[-1]
        excs[key] = excs.get(key, 0) + 1
    sig = ", ".join(f"{k} x{v}" for k, v in sorted(excs.items(), key=lambda kv: -kv[1]))
    return n, sig


def build_row(
    sweep_tag: str,
    state_row: Dict[str, Any],
    ar_output_dir: Path,
    stream_contam_map: Optional[Dict[Tuple[str, str, str, int], str]] = None,
    stream_fail_map: Optional[Dict[Tuple[str, str, str, int], str]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Assemble one wide row + return (row_dict, extra_issue_flags).

    The returned row uses ``""`` (empty string) for missing values so that the
    CSV round-trips cleanly without pandas coercing to NaN-in-CSV surprises.
    """
    issues: List[str] = []
    row: Dict[str, Any] = {c: "" for c in RUN_TABLE_COLUMNS}

    # Identity from state row.
    row["sweep_tag"] = sweep_tag
    row["app"] = state_row.get("app", "")
    row["phone"] = state_row.get("phone", "")
    row["variant"] = state_row.get("variant", "")
    row["negative_control"] = NEGATIVE_CONTROLS.get((row["app"], row["variant"]), "")
    row["rep"] = int(state_row.get("rep", 0))
    row["run_id"] = state_row.get("run_id", "")
    row["result"] = (state_row.get("result") or "").strip()
    row["wall_s"] = float(state_row.get("wall_s") or 0.0)

    ts_end_dt = state_row.get("ts_parsed")
    if ts_end_dt is not None:
        row["cell_end_ts_utc"] = _format_iso8601_utc(ts_end_dt)
        try:
            row["cell_start_ts_utc"] = _format_iso8601_utc(
                ts_end_dt - timedelta(seconds=float(row["wall_s"]))
            )
        except (TypeError, ValueError):
            row["cell_start_ts_utc"] = ""

    # App-crash census. Placed here because it needs `rep` and `ts_end_dt`,
    # which are resolved just above.
    _crash_n, _crash_sig = count_app_crashes(
        row["app"], row["phone"], row["variant"], row["rep"], ts_end_dt,
        sweep_tag=row.get("sweep_tag") or "")
    row["app_crash_count"] = _crash_n
    row["app_crash_signature"] = _crash_sig

    # Planned scenarios (import + count).
    scenario_list, planned_total = resolve_planned_scenarios(row["app"])
    row["scenarios_planned"] = planned_total if planned_total else ""

    # Contam flag: state CSV strips the parenthetical detail. For CONTAM rows,
    # look up the specific flag in sweep_runner stream logs (2026-07-27 audit fix).
    contam_flag = ""
    if row["result"] == "CONTAM":
        try:
            rep_int = int(row.get("rep", 0))
        except (TypeError, ValueError):
            rep_int = 0
        key = (row["phone"], row["app"], row["variant"], rep_int)
        if stream_contam_map is not None:
            contam_flag = stream_contam_map.get(key, "unknown")
        else:
            contam_flag = "unknown"
    row["contam_flag"] = contam_flag

    # AR run dir discovery.
    run_dir = find_run_dir(ar_output_dir, row["run_id"])
    if run_dir is None:
        issues.append("ar_run_dir_missing")
        row["energy_source"] = "none"
        row["issues_flags"] = _issues_join(issues)
        row["notes"] = ""
        finalize_cell_valid(row, scenario_list)
        finalize_cell_failure_class(row, subject_dir=None, bm_df=None,
                                    stream_fail_tag=(stream_fail_map or {}).get(
                                        (row["phone"], row["app"], row["variant"],
                                         int(row.get("rep") or 0))))
        return row, issues

    # config.json → ar_config_name (basename echoed in experiment.log line 1).
    experiment_log = run_dir / "experiment.log"
    if experiment_log.is_file():
        try:
            with open(experiment_log, "r", errors="replace") as f:
                first_line = f.readline()
            m = re.search(r"([A-Za-z0-9_\-]+\.json)", first_line)
            if m:
                row["ar_config_name"] = m.group(1)
        except OSError:
            pass
    log_scan = scan_experiment_log(experiment_log)
    row["installed"] = bool(log_scan["installed"])
    if not log_scan["installed"]:
        issues.append(f"ar_error:{log_scan['ar_error_class']}")

    # Subject dir + per-cell artifacts.
    phone_alias = WIFI_ALIAS.get(str(state_row.get("phone", "")).lower())
    subject_dir, wifi_alias, sd_flags = find_subject_dir(run_dir, phone_alias)
    issues.extend(sd_flags)
    if subject_dir is None:
        issues.append("subject_dir_missing")
        row["energy_source"] = "none"
        row["issues_flags"] = _issues_join(issues)
        finalize_cell_valid(row, scenario_list)
        finalize_cell_failure_class(row, subject_dir=None, bm_df=None,
                                    stream_fail_tag=(stream_fail_map or {}).get(
                                        (row["phone"], row["app"], row["variant"],
                                         int(row.get("rep") or 0))))
        return row, issues

    # apk_meta.json
    apk_meta = load_json_safe(subject_dir / "apk_meta.json") or {}
    row["apk_path"] = apk_meta.get("apk_path", "") or ""
    row["apk_sha256"] = apk_meta.get("apk_sha256", "") or ""
    row["apk_storage"] = apk_meta.get("apk_storage", "") or ""

    # device_state.json
    device_state = load_json_safe(subject_dir / "device_state.json") or {}
    row["android_release"] = str(device_state.get("android_release", "") or "")
    row["cpu_abilist"] = str(device_state.get("cpu_abilist", "") or "")
    brightness = device_state.get("screen_brightness") or {}
    if isinstance(brightness, dict):
        row["screen_brightness_value"] = _safe_int(brightness.get("value"), default="")
        row["screen_brightness_mode"] = str(brightness.get("mode_label", "") or "")
    airplane = device_state.get("airplane_mode_on")
    row["airplane_mode_on"] = _boolish(airplane)
    row["discharge_verdict"] = str(device_state.get("discharge_verdict", "") or "")

    # crash_anr_status.json
    crash_anr = load_json_safe(subject_dir / "crash_anr_status.json") or {}
    row["crash_anr_status"] = str(crash_anr.get("status", "") or "unknown")

    # appium_status.json
    appium_status = load_json_safe(subject_dir / "appium_status.json") or {}
    row["appium_workload_mode"] = str(appium_status.get("workload_mode", "") or "")
    row["appium_session_created"] = _boolish(appium_status.get("session_created"))
    row["workload_started"] = _boolish(appium_status.get("workload_started"))

    # ar_exit_rc: infer from verdict + install-fail signal.
    if row["result"] == "TIMEOUT":
        row["ar_exit_rc"] = 124
    elif row["result"] == "PASS":
        row["ar_exit_rc"] = 0
    elif row["result"] == "FAIL" and not log_scan["installed"]:
        row["ar_exit_rc"] = 1
    else:
        row["ar_exit_rc"] = ""  # unknown

    # BatteryManager per-sample energy.
    bm_csv, multi_flag = find_bm_csv(subject_dir)
    if multi_flag:
        issues.append("multi_logcat_csvs_picked_largest")

    bm_df: Optional[pd.DataFrame] = None
    if bm_csv is not None:
        bm_df = load_bm_samples(bm_csv, row["phone"])
    agg_bm = load_aggregated_bm(run_dir)

    energy_source = "none"
    total_cell_joules: Any = ""
    whole_window_avg_watts: Any = ""
    if bm_df is not None and len(bm_df) >= 2:
        energy_source = "batterymanager"
        total_cell_joules = trapz_over_window(bm_df, None, None) or ""
        duration_whole_s = float(bm_df["t_s"].iloc[-1] - bm_df["t_s"].iloc[0])
        row["duration_whole_window_s"] = duration_whole_s
        if agg_bm is not None and agg_bm.get("avg_power_w") is not None:
            whole_window_avg_watts = agg_bm["avg_power_w"]
        elif total_cell_joules != "" and duration_whole_s > 0:
            whole_window_avg_watts = total_cell_joules / duration_whole_s
    elif agg_bm is not None and agg_bm.get("energy_trapz_j") is not None:
        # Fallback: no per-sample CSV, but Aggregated_Results is present.
        energy_source = "batterymanager"
        total_cell_joules = agg_bm["energy_trapz_j"]
        whole_window_avg_watts = agg_bm.get("avg_power_w", "")
        issues.append("bm_persample_missing_used_aggregated")

    row["energy_source"] = energy_source
    row["total_cell_joules"] = total_cell_joules
    row["whole_window_avg_watts"] = whole_window_avg_watts
    if total_cell_joules != "":
        row["aggregated_energy_mwh"] = float(total_cell_joules) / 3.6

    # Device state samples from BM CSV.
    if bm_df is not None and len(bm_df) >= 1:
        cap = bm_df["capacity_pct"].dropna()
        if not cap.empty:
            row["battery_level_pre_pct"] = int(cap.iloc[0])
            row["battery_level_post_pct"] = int(cap.iloc[-1])
            # Informational ONLY: deliberately not folded into
            # scientifically_valid. Terminal-voltage and internal-resistance
            # both move with SoC, so cells compared across a wide SoC spread
            # are not strictly like-for-like. But: 611 of 911 batch1 rows have
            # no SoC recorded at all, and the 233 currently-valid cells already
            # span 26–82%. Making this a hard criterion would silently void
            # most of the collected data on a technicality. Record it, surface
            # it, and let the analysis decide its own band.
            lvl = int(cap.iloc[0])
            row["soc_pre_band_ok"] = bool(SOC_BAND_OK[0] <= lvl <= SOC_BAND_OK[1])
        temp = bm_df["temperature_c"].dropna()
        if not temp.empty:
            row["battery_temp_avg_c"] = float(temp.mean())
            row["battery_temp_max_c"] = float(temp.max())
        volt = bm_df["voltage_V"].dropna()
        if not volt.empty:
            row["voltage_pre_v"] = float(volt.iloc[0])
        curr_abs = bm_df["current_A_abs"].dropna()
        if not curr_abs.empty:
            row["current_now_abs_avg_ma"] = float(curr_abs.mean()) * 1000.0

    # Appium coverage jsonl -> scenarios_completed + workload window.
    coverage = load_coverage_jsonl(subject_dir / "appium_workload_coverage.jsonl")
    if not coverage:
        issues.append("coverage_jsonl_missing")
        row["scenarios_completed"] = 0
    else:
        unique_keys = set()
        strict_from_jsonl = 0
        action_from_jsonl = 0
        ts_list: List[datetime] = []
        ts_end_list: List[datetime] = []
        for entry in coverage:
            key = (entry.get("scenario_name"), entry.get("suite_round"))
            unique_keys.add(key)
            if entry.get("scenario_success_strict_ui"):
                strict_from_jsonl += 1
            if entry.get("scenario_success_action_only"):
                action_from_jsonl += 1
            t_start = _parse_iso8601_utc(entry.get("ts", "") or "")
            t_end = _parse_iso8601_utc(entry.get("ts_end", "") or "")
            # 2026-07-27: linkhub/scenarios.py writes `ts` + `scenario_wall_seconds`
            # but never `ts_end`, the only app in the cohort that omits it. Synthesize
            # from t_start + wall so the workload-window integration below fires.
            if t_end is None and t_start is not None:
                wall_s = entry.get("scenario_wall_seconds")
                if isinstance(wall_s, (int, float)) and wall_s >= 0:
                    t_end = t_start + timedelta(seconds=float(wall_s))
            if t_start is not None:
                ts_list.append(t_start)
            if t_end is not None:
                ts_end_list.append(t_end)
        # 2026-07-27: count TOTAL executions, not unique (name, suite_round).
        # Some scenarios legitimately repeat (e.g. pdfviewer S6_repeat2 uses the
        # SAME scenario_name as the primary, because the alias in _SCENARIO_FUNCS
        # maps to the same function). Counting unique under-reports completions and
        # flags false shortfalls even when all planned entries executed.
        row["scenarios_completed"] = len(coverage)

        # Workload window boundaries.
        if ts_list and ts_end_list:
            workload_start = min(ts_list)
            workload_end = max(ts_end_list)
            duration_workload_s = (workload_end - workload_start).total_seconds()
            row["duration_workload_s"] = duration_workload_s
            if bm_df is not None and len(bm_df) >= 2:
                ws_ms = int(workload_start.timestamp() * 1000)
                we_ms = int(workload_end.timestamp() * 1000)
                bm_ts_lo = int(bm_df["ts_ms"].iloc[0])
                bm_ts_hi = int(bm_df["ts_ms"].iloc[-1])
                # Clamp to profiler window.
                ws_ms_c = max(ws_ms, bm_ts_lo)
                we_ms_c = min(we_ms, bm_ts_hi)
                if we_ms_c > ws_ms_c:
                    wl_j = trapz_over_window(bm_df, ws_ms_c, we_ms_c)
                    pre_j = trapz_over_window(bm_df, bm_ts_lo, ws_ms_c)
                    td_j = trapz_over_window(bm_df, we_ms_c, bm_ts_hi)
                    row["workload_joules"] = wl_j if wl_j is not None else ""
                    row["pre_workload_joules"] = pre_j if pre_j is not None else ""
                    row["teardown_joules"] = td_j if td_j is not None else ""
                    if wl_j is not None and duration_workload_s > 0:
                        row["workload_avg_watts"] = wl_j / duration_workload_s
                        row["energy_workload_only_mwh"] = wl_j / 3.6
                    pre_s = (workload_start.timestamp() * 1000 - bm_ts_lo) / 1000.0
                    row["duration_pre_workload_s"] = max(pre_s, 0.0)
                else:
                    issues.append("appium_workload_boundaries_missing")
        else:
            issues.append("appium_workload_boundaries_missing")

    # Scenario report -> strict/action counts + scenarios_json.
    scenario_report_path = find_scenario_report(subject_dir)
    scenario_report = load_json_safe(scenario_report_path) if scenario_report_path else None
    strict_count: Any = ""
    action_count: Any = ""
    by_scenario: Dict[str, Any] = {}
    if scenario_report:
        by_scenario = scenario_report.get("by_scenario") or {}
        summary = scenario_report.get("summary") or {}
        strict_count = _safe_int(summary.get("total_strict_ui_pass"), default="")
        action_count = _safe_int(summary.get("total_action_only_pass"), default="")
        if strict_count == "":
            strict_count = sum(
                _safe_int(v.get("success_strict_ui"), default=0)
                for v in by_scenario.values() if isinstance(v, dict)
            )
        if action_count == "":
            action_count = sum(
                _safe_int(v.get("success_action_only"), default=0)
                for v in by_scenario.values() if isinstance(v, dict)
            )
        row["scenarios_json"] = json.dumps(by_scenario, sort_keys=True)
    elif coverage:
        # Derive strict-pass count from jsonl if report absent.
        strict_count = sum(1 for e in coverage if e.get("scenario_success_strict_ui"))
        action_count = sum(1 for e in coverage if e.get("scenario_success_action_only"))
    else:
        strict_count = 0
        action_count = 0

    row["scenarios_strict_pass_count"] = strict_count
    row["scenarios_action_pass_count"] = action_count

    completed = row["scenarios_completed"] if row["scenarios_completed"] != "" else 0
    planned = row["scenarios_planned"] if row["scenarios_planned"] != "" else 0

    if isinstance(strict_count, int) and isinstance(planned, int) and planned > 0:
        row["scenario_pass_rate_strict"] = strict_count / planned
    row["scenario_strict_pass"] = (isinstance(strict_count, int) and strict_count > 0)
    row["scenario_action_pass"] = (isinstance(action_count, int) and action_count > 0)

    if (isinstance(row["duration_workload_s"], (int, float))
            and isinstance(completed, int) and completed > 0):
        row["seconds_per_scenario"] = float(row["duration_workload_s"]) / completed
        if isinstance(row["workload_joules"], (int, float)):
            row["energy_per_scenario_joules"] = float(row["workload_joules"]) / completed
            row["energy_per_scenario_mwh"] = row["energy_per_scenario_joules"] / 3.6

    # scenario_pass_flags: one bit per planned scenario name, in canonical order.
    if scenario_list and by_scenario:
        flags = []
        for name in scenario_list:
            entry = by_scenario.get(name)
            if isinstance(entry, dict) and _safe_int(entry.get("success_strict_ui"), 0) > 0:
                flags.append("1")
            elif isinstance(entry, dict):
                flags.append("0")
            else:
                flags.append("")
        row["scenario_pass_flags"] = ";".join(flags)
    elif scenario_list:
        row["scenario_pass_flags"] = ";".join("" for _ in scenario_list)

    # Aux perf + fallback to Aggregated_Results_Android.
    aux = load_aux_summary(subject_dir)
    if aux and "columns" in aux:
        cpu = (aux["columns"] or {}).get("cpu") or {}
        mem = (aux["columns"] or {}).get("mem") or {}
        row["cpu_avg_pct"] = _safe_float(cpu.get("avg"), default="")
        row["cpu_p95_pct"] = _safe_float(cpu.get("p95"), default="")
        row["mem_pss_avg_mb"] = _safe_float(mem.get("avg"), default="")
        row["mem_pss_max_mb"] = _safe_float(mem.get("max"), default="")
        # aux memory is in kB (dumpsys meminfo TOTAL PSS); convert to MB.
        if isinstance(row["mem_pss_avg_mb"], (int, float)):
            row["mem_pss_avg_mb"] = float(row["mem_pss_avg_mb"]) / 1024.0
        if isinstance(row["mem_pss_max_mb"], (int, float)):
            row["mem_pss_max_mb"] = float(row["mem_pss_max_mb"]) / 1024.0
    else:
        agg_android = load_aggregated_android(run_dir)
        if agg_android:
            issues.append("aux_summary_missing_used_aggregated")
            if agg_android.get("android_cpu") is not None:
                row["cpu_avg_pct"] = agg_android["android_cpu"]
            if agg_android.get("android_mem") is not None:
                row["mem_pss_avg_mb"] = float(agg_android["android_mem"]) / 1024.0
        else:
            issues.append("aux_summary_missing")

    # Frametimes + GC.
    ft_median, ft_p95, delayed = load_frametimes_stats(subject_dir)
    row["frame_time_median_ms"] = ft_median
    row["frame_time_p95_ms"] = ft_p95
    row["delayed_frames_count"] = delayed
    row["gc_count"] = load_gc_count(subject_dir)

    # Warn if scenario count shortfall.
    if isinstance(planned, int) and planned > 0:
        if isinstance(completed, int) and completed < planned:
            issues.append("workload_scenario_count_below_planned")

    # P3 sign-flip acknowledgement flag (informational).
    if row["phone"] == "pixel3":
        issues.append("sign_artifact_p3")

    row["issues_flags"] = _issues_join(issues)
    row["notes"] = ""

    finalize_cell_valid(row, scenario_list)
    finalize_cell_failure_class(row, subject_dir=subject_dir, bm_df=bm_df,
                                stream_fail_tag=(stream_fail_map or {}).get(
                                    (row["phone"], row["app"], row["variant"],
                                     int(row.get("rep") or 0))))
    return row, issues


def finalize_cell_valid(row: Dict[str, Any], scenario_list: Tuple[str, ...]) -> None:
    """Populate row['cell_valid'] + row['rerun_reason'] from the current row.

    Called at the end of ``build_row`` (or from the early-exit branches when
    the AR run dir is missing).
    """
    reasons: List[str] = []

    result = row.get("result", "")
    if result != "PASS":
        # Verdict-level failure: one bucket per known verdict class.
        if result == "TIMEOUT":
            reasons.append("timeout")
        elif result == "CONTAM":
            contam = row.get("contam_flag") or "unknown"
            reasons.append(f"contam:{contam}")
        elif result == "FAIL":
            # Try to be more specific.
            issues = str(row.get("issues_flags") or "")
            if "ar_error:" in issues:
                # already covered below via issues -> reasons mapping
                pass

    crash_status = str(row.get("crash_anr_status") or "")
    if crash_status == "crash":
        reasons.append("crash")
    elif crash_status == "anr":
        reasons.append("anr")
    elif crash_status == "system_error_dialog":
        reasons.append("system_error_dialog")
    elif crash_status == "lost_foreground":
        reasons.append("lost_foreground")

    # ar_error flags from issues_flags (already prefix-encoded).
    issues_str = str(row.get("issues_flags") or "")
    for tok in issues_str.split(";"):
        tok = tok.strip()
        if tok.startswith("ar_error:"):
            reasons.append(tok)

    # Scenario shortfall / overcount (either flavour flips cell_valid to
    # False and gets its own explicit token so the reason is never empty).
    completed = row.get("scenarios_completed")
    planned = row.get("scenarios_planned")
    if (isinstance(completed, int) and isinstance(planned, int)
            and planned > 0 and completed != planned):
        reasons.append(f"scenario_shortfall:{completed}/{planned}")

    # Strict-pass count mismatch is a separate token so a reader can tell
    # "wrong number of runs" apart from "the right runs but some failed
    # strict-UI scoring".
    strict = row.get("scenarios_strict_pass_count")
    if (isinstance(strict, int) and isinstance(planned, int)
            and planned > 0 and strict != planned):
        reasons.append(f"scenario_strict_shortfall:{strict}/{planned}")

    # Per-scenario failure names.
    scenarios_json = row.get("scenarios_json") or ""
    failed_names: List[str] = []
    if scenarios_json:
        try:
            by = json.loads(scenarios_json)
            if isinstance(by, dict):
                for name in (scenario_list or tuple(by.keys())):
                    entry = by.get(name)
                    if isinstance(entry, dict):
                        strict = _safe_int(entry.get("success_strict_ui"), 0)
                        runs = _safe_int(entry.get("runs"), 0)
                        if runs > 0 and strict == 0:
                            failed_names.append(name)
        except (ValueError, TypeError):
            pass
    if failed_names:
        reasons.append("scenario_fail:" + ";".join(failed_names))

    if row.get("energy_source") == "none":
        reasons.append("energy_missing")

    # cell_valid = strict AND across the design conditions.
    conds = [
        result == "PASS",
        crash_status == "none",
        isinstance(completed, int) and isinstance(planned, int)
            and planned > 0 and completed == planned,
        isinstance(row.get("scenarios_strict_pass_count"), int)
            and isinstance(planned, int) and planned > 0
            and row["scenarios_strict_pass_count"] == planned,
        not row.get("contam_flag"),
        row.get("energy_source") not in ("", "none"),
    ]
    row["cell_valid"] = all(conds)
    # Deduplicate reasons while preserving order. If cell_valid=False but no
    # specific token was populated (should be rare: usually means an
    # invariant we forgot to enumerate), fall back to a catch-all so the
    # CSV column is never empty on an invalid row.
    seen: List[str] = []
    for r in reasons:
        if r and r not in seen:
            seen.append(r)
    if row["cell_valid"]:
        row["rerun_reason"] = ""
    else:
        row["rerun_reason"] = ";".join(seen) if seen else "invalid_unspecified"


_INVALIDATED_BUILDS_CACHE: Optional[List[Tuple[str, str, str, str]]] = None


def load_invalidated_builds() -> List[Tuple[str, str, str, str]]:
    """Read specs/invalidated_builds.csv -> [(app, variant, before_utc, reason)].

    ``variant == "ALL"`` applies to every variant of that app (used for the
    poetskingdom scenario-list change, where the APKs were fine but the
    workload itself changed and older cells stopped being comparable).
    """
    global _INVALIDATED_BUILDS_CACHE
    if _INVALIDATED_BUILDS_CACHE is not None:
        return _INVALIDATED_BUILDS_CACHE
    out: List[Tuple[str, str, str, str]] = []
    # This ledger is not part of the published package. The published run tables
    # were built with it and already carry its verdicts (none of their cells is
    # STALE_BUILD); without the file, no cell is marked.
    path = REPO_ROOT / "specs" / "invalidated_builds.csv"
    if path.exists():
        try:
            with open(path, newline="") as f:
                for parts in csv.reader(f):
                    if not parts or parts[0].startswith("#") or parts[0] == "app":
                        continue
                    if len(parts) < 4:
                        continue
                    out.append((parts[0].strip(), parts[1].strip(),
                                parts[2].strip(), parts[3].strip()))
        except Exception as e:
            print(f"  WARN: could not read {path}: {e}")
    _INVALIDATED_BUILDS_CACHE = out
    return out


def invalidated_build_reason(app: Optional[str],
                             variant: Optional[str],
                             ts_start_utc: Any) -> Optional[str]:
    """Reason string if this cell measured a since-replaced build, else None.

    A cell with no usable timestamp is treated as invalid rather than valid:
    we cannot show it ran against the fixed APK, and silently keeping an
    unprovable cell is how bad data survives.
    """
    if not app or not variant:
        return None
    ts = "" if ts_start_utc is None else str(ts_start_utc).strip()
    for e_app, e_variant, before, reason in load_invalidated_builds():
        if e_app != app:
            continue
        if e_variant not in ("ALL", variant):
            continue
        if not ts or ts < before:
            when = ts or "no-timestamp"
            return f"stale_build[{when} < {before}]: {reason}"
    return None


def finalize_cell_failure_class(row: Dict[str, Any],
                                 subject_dir: Optional[Path],
                                 bm_df: Optional[pd.DataFrame],
                                 stream_fail_tag: Optional[str] = None) -> None:
    """Populate ``all_scenarios_pass``, ``cell_failure_class``, and
    ``cell_failure_signature`` from the canonical parser.

    Source precedence:
      * Format-A jsonl (already parsed) WINS when non-empty.
      * Falls back to cell-log parse for Format-B apps (androidskk /
        medtimer / keep_recipe) whose jsonl may be empty.

    Uses ``row['cell_end_ts_utc']`` to pin ``find_cell_log`` to the log
    matching the canonical row's attempt, instead
    of the newest-by-mtime which could be a later retry.

    ``stream_fail_tag`` is the sweep_runner FAIL(<tag>) captured from the
    phone-level stream log; forwarded to the classifier so CONTAM/FAIL
    signatures reachable only via the stream log get matched.

    Does NOT touch ``cell_valid`` / ``rerun_reason``: those retain their
    prior semantics for backward compatibility. Wired 2026-07-31.
    """
    scenarios = []
    if subject_dir is not None:
        scenarios = _parse_scenarios_from_jsonl(
            subject_dir / "appium_workload_coverage.jsonl"
        )
    cell_log = _find_cell_log(
        row.get("phone", ""), row.get("app", ""), row.get("variant", ""),
        str(row.get("rep", "")),
        end_ts=row.get("cell_end_ts_utc") or None,
    )
    if not scenarios and cell_log is not None:
        scenarios = _parse_scenarios_from_log(cell_log)

    verdict = _all_scenarios_pass(scenarios)
    row["all_scenarios_pass"] = "" if verdict is None else bool(verdict)

    bs_mean: Optional[float] = None
    bs_nondis: Optional[float] = None
    if bm_df is not None and "status" in bm_df.columns:
        s = bm_df["status"].dropna()
        if not s.empty:
            bs_mean = float(s.mean())
            # FRACTION of samples that are not status=3 (Discharging).
            #
            # The mean alone cannot gate this. BATTERY_PROPERTY_STATUS is a
            # CATEGORICAL code (2=Charging, 3=Discharging, 4=Not charging), so a
            # status=4 sample pulls the mean UP by exactly as much as a status=2
            # sample pulls it down, and the two cancel. Measured 2026-08-09 on
            # run 2026.08.06_025837 (calculator/baseline/pixel9/rep5): 1446
            # discharging + 93 not-charging + 78 CHARGING samples gives a mean of
            # 3.0093, which sails through a `< 2.98` gate, while 10.6 % of the
            # window is non-discharging and current reaches +336 mA INTO the pack.
            # Cells with 5-14 % non-discharging were being excluded, and this one
            # was not, purely because of which direction its contamination leaned.
            bs_nondis = float((s != 3).mean())

    # Precedence guard (2026-07-31): stream_fail_map
    # has no per-attempt disambiguation, so a canonical PASS row can inherit a
    # stale FAIL tag from an EARLIER attempt of the same (phone,app,variant,rep).
    # A canonical PASS with a valid run_id means the last attempt succeeded:
    # no prior FAIL should poison it. Only forward the tag when EITHER the
    # canonical row itself failed OR run_id is empty (a zombie).
    #
    # Hardening (2026-07-31): use `or ""` inside the
    # str() to convert Python None to "" instead of the truthy string 'None'.
    # csv.DictReader always yields str so this never fires today, but a
    # future refactor that reads from a pandas DataFrame with NaN→None would
    # silently bypass the zombie promotion without this guard.
    #
    # 2026-07-31: use empty-string default (NOT "PASS")
    # so a corrupted / missing-result row falls through to the classifier's
    # AR_INTERNAL branch instead of being silently promoted to OK.
    result_str = str(row.get("result") or "")
    run_id_str = str(row.get("run_id") or "")
    effective_stream_tag = stream_fail_tag if (
        result_str != "PASS" or not run_id_str
    ) else None

    _crashes = row.get("app_crash_count")
    try:
        _crashes = int(_crashes) if _crashes not in (None, "") else None
    except (TypeError, ValueError):
        _crashes = None

    bucket, sig = _classify_cell_failure_class(
        cell_log=cell_log,
        stream_log_fail_tag=effective_stream_tag,
        battery_status_mean=bs_mean,
        result=result_str,
        scenarios=scenarios,
        run_id=run_id_str,
        battery_status_nondischarge_frac=bs_nondis,
        app_crash_count=_crashes,
    )
    row["cell_failure_class"] = bucket
    row["cell_failure_signature"] = sig

    # BOARD-SIDE contamination check. Overrides an OK verdict, because the board
    # is better evidence than the phone: ykush_port_watch reads the YKUSH's own
    # port state over USB, which no charger driver can misreport. If VBUS reached
    # the phone mid-measurement, the cell measured a partially-supplied device
    # regardless of what BATTERY_PROPERTY_STATUS thought.
    _pw_on = portwatch_midcell_on(
        row.get("phone", ""), row.get("app", ""), row.get("variant", ""),
        row.get("rep", ""), row.get("cell_end_ts_utc") or None,
    )
    row["portwatch_midcell_on"] = "" if _pw_on is None else _pw_on

    _th = parse_thermal_pre_cell(
        row.get("phone", ""), row.get("sweep_tag", "") or "").get(
        (row.get("app", ""), row.get("variant", ""), str(row.get("rep", ""))))
    row["thermal_status_pre"] = "" if not _th else _th.get("thermal_status", "")
    row["thermal_skin_pre_c"] = "" if not _th else _th.get("skin_c", "")
    row["thermal_cpu_pre_c"] = "" if not _th else _th.get("cpu_max_c", "")
    if _pw_on:
        bucket = "HUB_RACE_CONTAMINATED"
        sig = f"PORTWATCH_MIDCELL_ON_x{_pw_on}"
        row["cell_failure_class"] = bucket
        row["cell_failure_signature"] = sig

    # Composite convenience column (added 2026-07-31):
    # scientifically_valid = True iff all three validity criteria hold:
    # all scenarios strict-passed, valid energy data, no hub race / no
    # scenario failure / no framework crash. This is what to filter on for
    # ΔE analysis.
    row["scientifically_valid"] = bool(
        verdict is True and bucket == "OK"
    )

    # A technically-perfect cell can still be scientifically void if the APK it
    # measured has since been replaced, or was never what its filename said (a
    # protected APK whose code is byte-identical to its baseline, or a
    # "baseline" that carries a protection's hook). Such cells run cleanly and
    # would otherwise sail through every check here.
    # The timestamp column is `cell_start_ts_utc`. `ts_start_utc` was never a
    # column this builder writes, so this lookup returned None for every row,
    # and because invalidated_build_reason() fails CLOSED on a missing timestamp
    # (correctly: an unprovable cell must not pass), every cell of every app
    # listed in invalidated_builds.csv was marked STALE_BUILD regardless of when
    # it actually ran.
    #
    # Measured on the final sweep, 2026-08-09: 450 of 1200 cells were being
    # discarded. Only itsok/allatori's 30 were then believed genuine (that
    # cutoff was lifted on 2026-08-13). The other 420 ran on
    # 2026-08-04 or later, i.e. AFTER every real cutoff in the file, and were
    # valid all along. That is 35 % of the sweep thrown away by a wrong field
    # name in a check whose own fail-closed policy is right.
    stale_reason = invalidated_build_reason(
        row.get("app"), row.get("variant"), row.get("cell_start_ts_utc")
    )
    if stale_reason:
        row["scientifically_valid"] = False
        row["cell_failure_class"] = "STALE_BUILD"
        row["cell_failure_signature"] = stale_reason

    # ---- (app, phone) combinations the device cannot measure ------------------
    reason = DEVICE_UNSUITABLE.get((row.get("app"), row.get("phone")))
    if reason:
        row["scientifically_valid"] = False
        row["cell_failure_class"] = "DEVICE_UNSUITABLE"
        row["cell_failure_signature"] = reason


# (app, phone) pairs where the DEVICE, not the build, makes measurement
# impossible. Declared here rather than filtered in each analysis script, so the
# exclusion is applied once, consistently, and survives into the run table where
# a reader can see it.
#
# keep_recipe / pixel3: added 2026-08-13.
#   Not one clean cell exists: 0 of 40 in batch 1 and 0 of 27 in batch 2 reached
#   19/19 scenarios with zero app crashes. Every cell falls into one of two
#   populations, and they measure different things:
#
#     partial (10 of 19 scenarios, ~371 s, ~513 J, 0 crashes)  n=10 in batch 2
#     full    (19 of 19 scenarios, ~141-172 s, ~233-300 J, 1-2 crashes)  n=17
#
#   The cause is garbage collection, not a slow device. The partial cells log
#   **484 GC events** against 172-191 for pixel3's own crashed cells, 173 for
#   pixel6 and 137 for pixel9. The app enters an allocation storm, the collector
#   starves the UI thread, and every element wait runs to its full timeout:
#   S1_home_visible takes 73.9 s twice, identically, then succeeds, against 4.5 s
#   on pixel6. The workload budget expires at round 10.
#
#   The crash is protective: a ConcurrentModificationException kills the process,
#   the heap resets, and the restarted app runs at normal speed and completes all
#   19 rounds. That is why crashing correlates with *completing*.
#
#   A longer workload budget would make this worse, not better: a GC-bound cell
#   given more time simply collects more, and its energy increasingly measures
#   the collector rather than the app.
#
#   Dropping the pair costs at most 0.36 pp on any headline figure (batch 1
#   freerasp +9.03 -> +8.67, bangcle +4.03 -> +4.32; batch 2 freerasp +4.32 ->
#   +4.04, bangcle +0.28 -> +0.31; allatori unchanged, since keep_recipe/allatori
#   is a negative control declared before the series and was already outside the
#   treatment estimate). keep_recipe itself survives on pixel6 and pixel9, where
#   all 134 cells are clean.
DEVICE_UNSUITABLE: Dict[Tuple[str, str], str] = {
    ("keep_recipe", "pixel3"):
        "GC thrashing on a 4 GB device: 484 GC events/cell vs 137-191 elsewhere; "
        "UI thread starved so element waits hit their timeout and the workload "
        "budget expires at round 10 of 19. Zero clean cells in either batch "
        "(0/40 batch 1, 0/27 batch 2). See docs/DROPPED_AND_EXCLUDED.md.",
}


CLASS_LABEL_HUMAN = {
    "OK":                    "clean",
    "HUB_ABORTED":           "hub-crashed",
    "HUB_RACE_CONTAMINATED": "hub-race contaminated",
    "SCENARIO_FAILED":       "scenario failed",
    "AR_INTERNAL":           "AR internal error",
    "ZOMBIE_NO_RUN_ID":      "zombie (no AR output)",
    "APP_CRASHED":           "app crashed during the cell",
    "STALE_BUILD":           "measured a since-replaced APK",
    "DEVICE_UNSUITABLE":     "device cannot measure this app",
}


def format_class_counts_human(counts: Dict[str, int]) -> str:
    order = ["OK", "ZOMBIE_NO_RUN_ID", "APP_CRASHED", "HUB_ABORTED",
             "HUB_RACE_CONTAMINATED", "SCENARIO_FAILED", "AR_INTERNAL"]
    parts = [f"{counts[k]} {CLASS_LABEL_HUMAN[k]}"
             for k in order if counts.get(k)]
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Per-scenario long-format extraction
# ---------------------------------------------------------------------------

def build_per_scenario_rows(sweep_tag: str,
                            state_row: Dict[str, Any],
                            ar_output_dir: Path) -> List[Dict[str, Any]]:
    run_dir = find_run_dir(ar_output_dir, state_row.get("run_id", ""))
    if run_dir is None:
        return []
    phone_alias = WIFI_ALIAS.get(str(state_row.get("phone", "")).lower())
    subject_dir, _alias, _ = find_subject_dir(run_dir, phone_alias)
    if subject_dir is None:
        return []
    coverage = load_coverage_jsonl(subject_dir / "appium_workload_coverage.jsonl")
    if not coverage:
        return []
    out: List[Dict[str, Any]] = []
    for entry in coverage:
        out.append({
            "sweep_tag": sweep_tag,
            "app": state_row.get("app", ""),
            "phone": state_row.get("phone", ""),
            "variant": state_row.get("variant", ""),
            "rep": int(state_row.get("rep", 0)),
            "scenario_name": entry.get("scenario_name", ""),
            "suite_round": entry.get("suite_round", ""),
            "ts": entry.get("ts", ""),
            "ts_end": entry.get("ts_end", ""),
            "scenario_wall_seconds": entry.get("scenario_wall_seconds", ""),
            "scenario_success": entry.get("scenario_success", ""),
            "scenario_success_strict_ui": entry.get("scenario_success_strict_ui", ""),
            "scenario_success_action_only": entry.get("scenario_success_action_only", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build the extended per-cell run_table from a completed sweep. "
            "Idempotent: each invocation rebuilds both CSVs from scratch."
        )
    )
    p.add_argument("--sweep-tag", required=True,
                   help="Sweep tag (locates STATE_DIR=tools/sweep_logs/state_<TAG>/)")
    p.add_argument("--ar-output-dir", default=str(DEFAULT_AR_OUTPUT),
                   help="Root of AR run_id folders (default: %(default)s)")
    p.add_argument("--out", default=None,
                   help="Output CSV (default: specs/run_table_extended_<TAG>.csv)")
    p.add_argument("--per-scenario-out", default=None,
                   help="Long-format per-scenario CSV (default: specs/per_scenario_long_<TAG>.csv)")
    p.add_argument("--ingest-report", default=None,
                   help="Ingest diagnostic JSON (default: tools/sweep_logs/state_<TAG>/ingest_report.json)")
    p.add_argument("--dry-run", action="store_true",
                   help="Write CSVs to /tmp instead of specs/ (report stays in state dir).")
    return p.parse_args(argv)


def resolve_paths(args: argparse.Namespace) -> Tuple[Path, Path, Path, Path, Path]:
    tag = args.sweep_tag
    state_dir = Path(str(DEFAULT_STATE_DIR_TMPL).format(tag=tag))
    ar_output_dir = Path(args.ar_output_dir).resolve()
    if args.dry_run:
        out_path = Path("/tmp") / f"run_table_extended_{tag}.csv"
        per_scenario_path = Path("/tmp") / f"per_scenario_long_{tag}.csv"
    else:
        out_path = (Path(args.out) if args.out
                    else Path(str(DEFAULT_OUT_TMPL).format(tag=tag))).resolve()
        per_scenario_path = (Path(args.per_scenario_out) if args.per_scenario_out
                             else Path(str(DEFAULT_PER_SCENARIO_OUT_TMPL).format(tag=tag))).resolve()
    ingest_report_path = (Path(args.ingest_report) if args.ingest_report
                          else Path(str(DEFAULT_INGEST_REPORT_TMPL).format(tag=tag))).resolve()
    return state_dir, ar_output_dir, out_path, per_scenario_path, ingest_report_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tag = args.sweep_tag
    state_dir, ar_output_dir, out_path, per_scenario_path, report_path = resolve_paths(args)

    if not state_dir.is_dir():
        print(f"[build_run_table_extended] state dir not found: {state_dir}", file=sys.stderr)
        return 2

    state_rows = load_state_rows(state_dir)
    canonical, groups = pick_canonical_rows(state_rows)

    # 2026-07-27 audit fix: load stream-log contam flags once (cached per log_dir).
    # sweep_runner writes stream logs at tools/sweep_logs/parallel_*_<phone>.log
    # (same dir as state_<tag>/) so we scan that parent dir.
    stream_contam_map = load_stream_contam_map(state_dir.parent)
    stream_fail_map = load_stream_fail_map(state_dir.parent)

    wide_rows: List[Dict[str, Any]] = []
    long_rows: List[Dict[str, Any]] = []
    missing_run_ids: List[str] = []
    workload_boundaries_missing: List[str] = []
    n_pass = n_contam = n_timeout = n_fail = 0
    n_cell_valid = n_cell_invalid = 0
    unmapped_rows = 0

    for st in canonical:
        row, issues = build_row(tag, st, ar_output_dir,
                                stream_contam_map=stream_contam_map,
                                stream_fail_map=stream_fail_map)
        wide_rows.append(row)
        long_rows.extend(build_per_scenario_rows(tag, st, ar_output_dir))

        verdict = row["result"]
        if verdict == "PASS":
            n_pass += 1
        elif verdict == "CONTAM":
            n_contam += 1
        elif verdict == "TIMEOUT":
            n_timeout += 1
        elif verdict == "FAIL":
            n_fail += 1

        if row["cell_valid"]:
            n_cell_valid += 1
        else:
            n_cell_invalid += 1

        if "ar_run_dir_missing" in issues:
            missing_run_ids.append(row.get("run_id", "") or "<empty>")
            unmapped_rows += 1
        if "appium_workload_boundaries_missing" in issues:
            key = f"{row['app']}|{row['phone']}|{row['variant']}|{row['rep']}"
            workload_boundaries_missing.append(key)

    # Sort so matched-pair siblings sit adjacent.
    wide_df = pd.DataFrame(wide_rows, columns=list(RUN_TABLE_COLUMNS))
    if not wide_df.empty:
        wide_df = wide_df.sort_values(
            by=["app", "phone", "variant", "rep"],
            kind="mergesort",  # stable
        ).reset_index(drop=True)

    long_df = pd.DataFrame(long_rows, columns=list(PER_SCENARIO_COLUMNS))
    if not long_df.empty:
        long_df = long_df.sort_values(
            by=["app", "phone", "variant", "rep", "suite_round", "scenario_name"],
            kind="mergesort",
        ).reset_index(drop=True)

    out_sha = _atomic_write_csv(wide_df, out_path)
    per_sha = _atomic_write_csv(long_df, per_scenario_path)

    # Parser-derived failure-class rollup (wired 2026-07-31)
    failure_class_counts: Dict[str, int] = defaultdict(int)
    for r in wide_rows:
        cls = r.get("cell_failure_class") or ""
        if cls:
            failure_class_counts[cls] += 1
    failure_class_counts = dict(failure_class_counts)

    report = {
        "sweep_tag": tag,
        "state_dir": str(state_dir),
        "ar_output_dir": str(ar_output_dir),
        "out_path": str(out_path),
        "per_scenario_out_path": str(per_scenario_path),
        "n_state_rows_read": len(state_rows),
        "n_cells_emitted": len(wide_rows),
        "n_pass": n_pass,
        "n_contam": n_contam,
        "n_timeout": n_timeout,
        "n_fail": n_fail,
        "n_cell_valid": n_cell_valid,
        "n_cell_invalid": n_cell_invalid,
        "failure_class_counts": failure_class_counts,
        "failure_class_counts_human": format_class_counts_human(failure_class_counts),
        "missing_run_ids": missing_run_ids,
        "workload_boundaries_missing": workload_boundaries_missing,
        "output_sha256": out_sha,
        "per_scenario_sha256": per_sha,
        "duplicate_state_rows": [
            {"key": list(k), "n_rows": len(v)}
            for k, v in groups.items() if len(v) > 1
        ],
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(
        f"[build_run_table_extended] tag={tag} cells={len(wide_rows)} "
        f"PASS={n_pass} CONTAM={n_contam} TIMEOUT={n_timeout} FAIL={n_fail} "
        f"valid={n_cell_valid} invalid={n_cell_invalid} "
        f"missing_run_id={len(missing_run_ids)} out={out_path}",
        file=sys.stderr,
    )

    return 2 if unmapped_rows > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
