"""
Per-scenario scoring for Pdf-Viewer (``com.rajat.sample.pdfviewer``).

Same pattern as the other pass-through scorers (e.g.
:mod:`appium_android_tests.calculator.scoring`): each ``_run_*`` already writes the strict/action
booleans + fail-reasons list into the coverage row, so the dispatcher is a pass-through.
"""

from __future__ import annotations

from typing import Any


def _score_passthrough(cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """Read the strict/action booleans + fail reasons that the scenario function already wrote."""
    strict = bool(cov.get("scenario_success_strict_ui", False))
    action = bool(cov.get("scenario_success_action_only", False))
    reasons = list(cov.get("scenario_fail_reasons_strict_ui", []) or [])
    return strict, action, reasons


_SCORERS = {
    "S1_home_visible": _score_passthrough,
    "S2_open_assets_pdf": _score_passthrough,
    "S3_pdf_initial_render": _score_passthrough,
    "S4_pdf_scroll_through_all": _score_passthrough,
    "S5_pdf_scroll_back_to_start": _score_passthrough,
    "S6_pdf_pinch_zoom_cycles": _score_passthrough,
    "S7_pdf_close_reopen_rescroll": _score_passthrough,
    "S8_back_open_compose": _score_passthrough,
    "S9_open_online_pdf": _score_passthrough,
    "S10_show_inline_pdf": _score_passthrough,
}


def score_scenario(scenario_name: str, cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """Dispatcher. Unknown scenario names return ``(False, False, ["unknown_scenario_name"])``."""
    fn = _SCORERS.get(scenario_name)
    if fn is None:
        return False, False, ["unknown_scenario_name"]
    return fn(cov)
