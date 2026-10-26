"""Pass-through scoring for AndroidSKK."""
from __future__ import annotations
from typing import Any


def _score_passthrough(cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    return bool(cov.get("scenario_success_strict_ui", False)), bool(cov.get("scenario_success_action_only", False)), list(cov.get("scenario_fail_reasons_strict_ui", []) or [])


_SCORERS = {
    "S1_settings_home_visible": _score_passthrough,
    "S2_scroll_settings_list": _score_passthrough,
    "S3_tap_first_pref": _score_passthrough,
    "S4_back_from_pref": _score_passthrough,
    "S5_scroll_to_bottom": _score_passthrough,
    "S6_tap_last_pref": _score_passthrough,
    "S7_back_to_settings_root": _score_passthrough,
    "S8_open_dict_manager_via_pref": _score_passthrough,
    "S9_back_to_settings": _score_passthrough,
    "S10_final_home_check": _score_passthrough,
}


def score_scenario(scenario_name, cov):
    fn = _SCORERS.get(scenario_name)
    if fn is None: return False, False, ["unknown_scenario_name"]
    return fn(cov)
