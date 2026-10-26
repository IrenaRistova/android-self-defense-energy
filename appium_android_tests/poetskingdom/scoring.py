"""Per-scenario scoring for PoetsKingdom: pass-through."""
from __future__ import annotations
from typing import Any


def _score_passthrough(cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    strict = bool(cov.get("scenario_success_strict_ui", False))
    action = bool(cov.get("scenario_success_action_only", False))
    reasons = list(cov.get("scenario_fail_reasons_strict_ui", []) or [])
    return strict, action, reasons


_SCORERS = {
    "S1_home_visible": _score_passthrough,
    "S2_dismiss_first_use_dialog": _score_passthrough,
    "S3_tap_myImages_nav": _score_passthrough,
    "S4_back_from_myImages": _score_passthrough,
    "S5_tap_createPoemImage": _score_passthrough,
    "S6_back_from_poemTheme": _score_passthrough,
    "S7_tap_myPoemImage": _score_passthrough,
    "S8_back_from_myPoems": _score_passthrough,
    "S9_options_menu_round_trip": _score_passthrough,
    "S10_tap_personalisation_and_back": _score_passthrough,
}


def score_scenario(scenario_name, cov):
    fn = _SCORERS.get(scenario_name)
    if fn is None:
        return False, False, ["unknown_scenario_name"]
    return fn(cov)
