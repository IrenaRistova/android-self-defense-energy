"""
App-agnostic shared library for the Appium black-box harness.

Submodules:

- ``driver``: UiAutomator2 options builder, fail-fast ``webdriver.Remote()`` connect, generic
  UI helpers (``ensure_foreground``, ``dismiss_system_dialogs``, ``wait_for_package``,
  ``await_app_ready``, swipe / click / scroll wrappers).
- ``scoring``: strict-UI vs action-only scoring (``record_numeric_edit_observed``,
  ``score_espresso_mirror_scenario``).
- ``coverage``: ``appium_workload_coverage.jsonl`` writer, ``<workload>_scenario_report.{json,txt}``
  generator, and ``driver.page_source`` XML dump helper.
- ``timing``: workload-window enforcement (start workload thread, block ``main()`` for
  ``experiment.duration`` seconds so AndroidRunner's child process stays alive).
- ``status``: ``appium_status.json`` writer.

Common module-level utilities (``log``, ``emit_stderr``, ``env_truthy``, ``env_float``, ``env_int``)
are exposed here so submodules can ``from . import log`` etc.

This module owns no env-var contract directly; the contract is defined per submodule and
summarised in ``appium_android_tests/CONVENTIONS.md`` § 7.
"""

from __future__ import annotations

import logging
import os
import sys


def log(msg: str) -> None:
    """Print + route through the Android Runner root logger so lines reach ``experiment.log``.

    Worker threads don't always inherit the parent logger's handlers; printing AND logging is
    intentional belt-and-braces.
    """
    line = "INTERACTION_APPIUM: %s" % msg
    print(line)
    logging.getLogger("appium_android_tests").info(line)


def emit_stderr(msg: str) -> None:
    """Guaranteed console line even if logging handlers miss worker-thread records.

    Used around session connect / shutdown for forensics: survives buffered stdout.
    """
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def env_truthy(name: str) -> bool:
    """``os.environ[name]`` interpreted as a boolean.

    Truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive). Anything else is False.
    """
    v = os.environ.get(name, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    """Read float-valued env var with a default fallback (also covers empty / non-numeric)."""
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    """Read int-valued env var with a default fallback (also covers empty / non-numeric)."""
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


from .coverage import (  # noqa: E402
    append_coverage_jsonl,
    resolve_artifact_dir,
    resolve_dump_dir,
    save_page_source,
    write_scenario_report,
)
from .driver import (  # noqa: E402
    SessionConnectTimeoutError,
    await_app_ready,
    build_uiautomator2_options,
    bump_driver_http_timeout,
    connect_remote_webdriver,
    dismiss_system_dialogs,
    element_present_uiautomator,
    ensure_foreground,
    is_likely_http_timeout,
    press_keys_repeat,
    safe_click_description_contains,
    safe_click_text_contains,
    safe_scroll_into_view_text_contains,
    swipe_vertical,
    wait_for_package,
)
from .scoring import (  # noqa: E402
    read_edit_text_by_id,
    record_numeric_edit_observed,
    score_espresso_mirror_scenario,
    type_numeric_edit_by_id,
)
from .status import write_appium_status, write_status_raw  # noqa: E402
from .timing import (  # noqa: E402
    start_workload_thread_and_block,
    start_workload_thread_and_block_until_done,
)

__all__ = [
    "log",
    "emit_stderr",
    "env_truthy",
    "env_float",
    "env_int",
    "append_coverage_jsonl",
    "resolve_artifact_dir",
    "resolve_dump_dir",
    "save_page_source",
    "write_scenario_report",
    "SessionConnectTimeoutError",
    "await_app_ready",
    "build_uiautomator2_options",
    "bump_driver_http_timeout",
    "connect_remote_webdriver",
    "dismiss_system_dialogs",
    "element_present_uiautomator",
    "ensure_foreground",
    "is_likely_http_timeout",
    "press_keys_repeat",
    "safe_click_description_contains",
    "safe_click_text_contains",
    "safe_scroll_into_view_text_contains",
    "swipe_vertical",
    "wait_for_package",
    "read_edit_text_by_id",
    "record_numeric_edit_observed",
    "score_espresso_mirror_scenario",
    "type_numeric_edit_by_id",
    "write_appium_status",
    "write_status_raw",
    "start_workload_thread_and_block",
    "start_workload_thread_and_block_until_done",
]
