"""Pass-through scoring for MedTimer (deep scenarios)."""
from __future__ import annotations
from typing import Any


def _score_passthrough(cov):
    return (bool(cov.get("scenario_success_strict_ui", False)),
            bool(cov.get("scenario_success_action_only", False)),
            list(cov.get("scenario_fail_reasons_strict_ui", []) or []))


_SCORERS = {
    "S1_dismiss_notification_permission": _score_passthrough,
    "S2_overview_home_visible": _score_passthrough,
    "S3_navigate_to_medicines_tab": _score_passthrough,
    "S4_add_first_medicine_aspirin": _score_passthrough,
    "S5_add_reminder_to_aspirin": _score_passthrough,
    "S6_overview_shows_new_reminder": _score_passthrough,
    "S7_tap_taken_now_on_reminder": _score_passthrough,
    "S8_add_second_medicine_ibuprofen": _score_passthrough,
    "S9_add_reminder_to_ibuprofen": _score_passthrough,
    "S10_final_overview_both_persist": _score_passthrough,
}


def score_scenario(scenario_name, cov):
    fn = _SCORERS.get(scenario_name, _score_passthrough)
    return fn(cov)
