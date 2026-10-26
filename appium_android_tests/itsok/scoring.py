"""Per-scenario scoring for ItsOK: pass-through."""

from __future__ import annotations

from typing import Any


def _score_passthrough(cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    strict = bool(cov.get("scenario_success_strict_ui", False))
    action = bool(cov.get("scenario_success_action_only", False))
    reasons = list(cov.get("scenario_fail_reasons_strict_ui", []) or [])
    return strict, action, reasons


_SCORERS = {
    "S1_home_visible": _score_passthrough,
    "S2_type_gripe": _score_passthrough,
    "S3_view_memento_response": _score_passthrough,
    "S4_negative_then_another_memento": _score_passthrough,
    "S5_positive_to_bye_stage": _score_passthrough,
    "S6_restart_conversation": _score_passthrough,
    "S7_open_settings": _score_passthrough,
    "S8_open_positive_mementoes": _score_passthrough,
    "S9_open_memento_editor": _score_passthrough,
    "S10_back_to_chat": _score_passthrough,
}


def score_scenario(scenario_name: str, cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    fn = _SCORERS.get(scenario_name)
    if fn is None:
        return False, False, ["unknown_scenario_name"]
    return fn(cov)
