"""
Artifact writers for the Appium black-box harness.

Owns three on-disk artifacts (paths resolve via Android Runner ``paths.OUTPUT_DIR`` first, with
``APPIUM_UI_DUMP_DIR`` as a fallback override):

- ``appium_workload_coverage.jsonl``: one JSON object per workload cycle / scenario, appended by
  :func:`append_coverage_jsonl`. Consumed by the run-table builder
  (``tools/build_run_table_extended.py``).
- ``<workload>_scenario_report.{json,txt}``: aggregate strict-UI / action-only counts per
  scenario, written by :func:`write_scenario_report` at end of run.
- ``appium_ui_dumps/<stem>_<epoch_ms>.xml``: opt-in ``driver.page_source`` snapshots for forensics
  on packed / wrong-locale / startup-failure paths, written by :func:`save_page_source`. Capped per
  process by ``APPIUM_UI_DUMP_MAX``.

Env-var contract owned here:

- ``APPIUM_UI_DUMP_DIR``: override directory for XML dumps (default: ``paths.OUTPUT_DIR/appium_ui_dumps``).
- ``APPIUM_UI_DUMP_MAX``: max dumps per process (default ``32``; ``0`` disables).
- ``APPIUM_SAVE_UI_ON_MISS``: opt-in dump on tracked-step misses (default off; startup failures
  always dump regardless).

Also resolves the parent artifact directory used by status / scenario-report writers via
:func:`resolve_artifact_dir`, which prefers ``paths.OUTPUT_DIR`` (set by Android Runner) over the
``APPIUM_UI_DUMP_DIR`` fallback.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from . import log


_dump_counter: dict[str, int] = {"n": 0}


def reset_dump_counter() -> None:
    """Reset per-process XML dump counter (call at the start of each workload run)."""
    _dump_counter["n"] = 0


def _dump_limit() -> int:
    try:
        return max(0, int(os.environ.get("APPIUM_UI_DUMP_MAX", "32")))
    except ValueError:
        return 32


def resolve_dump_dir() -> Optional[str]:
    """Directory for ``page_source`` XML dumps.

    Resolution order:

    1. ``APPIUM_UI_DUMP_DIR`` env var (explicit override).
    2. ``paths.OUTPUT_DIR/appium_ui_dumps`` if Android Runner injected ``paths`` on ``sys.path``.
    3. ``None``: caller will skip dumping.
    """
    override = os.environ.get("APPIUM_UI_DUMP_DIR", "").strip()
    if override:
        return override
    try:
        import paths as ar_paths  # type: ignore  # noqa: WPS433  (Android Runner injects this on sys.path)

        od = getattr(ar_paths, "OUTPUT_DIR", None)
        if od:
            return os.path.join(od, "appium_ui_dumps")
    except Exception:
        pass
    return None


def resolve_artifact_dir(dump_dir: Optional[str]) -> Optional[str]:
    """Directory for ``appium_status.json`` / coverage JSONL / scenario report.

    Prefers ``paths.OUTPUT_DIR`` (Android Runner). If that is unavailable, falls back to
    ``dump_dir``'s parent when the dump dir is the conventional ``.../appium_ui_dumps`` (so the
    sibling artifacts land next to it, not inside it).
    """
    try:
        import paths as ar_paths  # type: ignore  # noqa: WPS433

        od = getattr(ar_paths, "OUTPUT_DIR", None)
        if od:
            return od
    except Exception:
        pass
    if dump_dir:
        base = dump_dir.rstrip(os.sep)
        if base.endswith("appium_ui_dumps"):
            return os.path.dirname(base)
        return dump_dir
    return None


def save_page_source(driver: Any, dump_dir: Optional[str], stem: str) -> None:
    """Persist ``driver.page_source`` for post-hoc analysis (packed APK / wrong locale / startup hangs).

    ``stem`` is sanitized to ``[A-Za-z0-9_-]`` and truncated to 120 chars; the on-disk filename is
    ``<safe_stem>_<epoch_ms>.xml``. Counts toward ``APPIUM_UI_DUMP_MAX``; silently skipped past the
    cap. Failures are logged but never raise (forensics is best-effort).
    """
    if not dump_dir:
        return
    lim = _dump_limit()
    if lim == 0:
        return
    if _dump_counter["n"] >= lim:
        log("UI dump skipped (limit %s reached)" % lim)
        return
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:120]
    try:
        os.makedirs(dump_dir, exist_ok=True)
        path = os.path.join(dump_dir, "%s_%d.xml" % (safe, int(time.time() * 1000)))
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        _dump_counter["n"] += 1
        log("saved page_source -> %s" % path)
    except Exception as ex:
        log("save_page_source failed: %s %s" % (type(ex).__name__, ex))


def append_coverage_jsonl(dump_dir: Optional[str], row: dict[str, Any]) -> None:
    """Append a single JSON object to ``<base>/appium_workload_coverage.jsonl``.

    ``base`` resolution: ``paths.OUTPUT_DIR`` if available, else ``dump_dir``. If neither is set,
    the row is silently dropped (nothing to anchor the artifact tree to).

    The row is JSON-encoded with ``sort_keys=True`` and a trailing newline so the file is a valid
    JSON Lines file consumed by downstream summarizers.
    """
    base: Optional[str] = None
    try:
        import paths as ar_paths  # type: ignore  # noqa: WPS433

        base = getattr(ar_paths, "OUTPUT_DIR", None)
    except Exception:
        pass
    if not base:
        base = dump_dir
    if not base:
        return
    try:
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "appium_workload_coverage.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception as ex:
        log("coverage jsonl failed: %s" % ex)


def write_scenario_report(
    artifact_dir: Optional[str],
    aggregate: dict[str, Any],
    text_summary: str,
    workload_name: str = "espresso_mirror",
) -> None:
    """Write ``<workload_name>_scenario_report.{json,txt}`` next to other run artifacts.

    Default ``workload_name`` is ``espresso_mirror`` to preserve the existing artifact name
    consumed by the protection-grading scripts. Other workloads (future per-app modules) pass their
    own name; the JSON+TXT pair stays paired by stem.
    """
    if not artifact_dir:
        return
    try:
        os.makedirs(artifact_dir, exist_ok=True)
        json_path = os.path.join(artifact_dir, "%s_scenario_report.json" % workload_name)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(aggregate, f, indent=2, sort_keys=True)
            f.write("\n")
        txt_path = os.path.join(artifact_dir, "%s_scenario_report.txt" % workload_name)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text_summary)
            if not text_summary.endswith("\n"):
                f.write("\n")
        log("wrote %s and %s" % (json_path, txt_path))
    except Exception as ex:
        log("scenario report write failed: %s %s" % (type(ex).__name__, ex))
