"""Pass-through scoring for Keep-Recipe (deep scenarios)."""
from __future__ import annotations
from typing import Any


def _score_passthrough(cov):
    return (bool(cov.get("scenario_success_strict_ui", False)),
            bool(cov.get("scenario_success_action_only", False)),
            list(cov.get("scenario_fail_reasons_strict_ui", []) or []))


_SCORERS = {
    "S1_home_visible": _score_passthrough,
    "S2_tap_add_recipe_fab": _score_passthrough,
    "S3_fill_first_recipe": _score_passthrough,
    "S4_save_first_recipe": _score_passthrough,
    "S5_recipe_in_list": _score_passthrough,
    "S6_tap_first_recipe_detail": _score_passthrough,
    "S7_back_to_home": _score_passthrough,
    "S8_add_second_recipe": _score_passthrough,
    "S9_open_search_bar": _score_passthrough,
    "S10_final_home_check": _score_passthrough,
}


def score_scenario(scenario_name, cov):
    fn = _SCORERS.get(scenario_name, _score_passthrough)
    return fn(cov)
