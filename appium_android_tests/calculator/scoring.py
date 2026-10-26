"""
Per-scenario scoring for Calculator (``com.exponential_groth.calculator``).

Pass-through dispatcher (each ``_run_*`` self-records strict/action booleans).
"""

from __future__ import annotations

from typing import Any


def _score_passthrough(cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    strict = bool(cov.get("scenario_success_strict_ui", False))
    action = bool(cov.get("scenario_success_action_only", False))
    reasons = list(cov.get("scenario_fail_reasons_strict_ui", []) or [])
    return strict, action, reasons


_SCORERS = {
    "S1_home_visible": _score_passthrough,
    "S2_simple_arithmetic": _score_passthrough,
    "S3_log_function_mirror": _score_passthrough,
    "S4_trig_functions": _score_passthrough,
    "S5_powers_and_roots": _score_passthrough,
    "S6_factorial_and_constants": _score_passthrough,
    "S7_brackets_complex_expr": _score_passthrough,
    "S8_clear_and_nav_arrows": _score_passthrough,
    "S9_settings_round_trip": _score_passthrough,
    "S10_long_expression": _score_passthrough,
}


def score_scenario(scenario_name: str, cov: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    fn = _SCORERS.get(scenario_name)
    if fn is None:
        return False, False, ["unknown_scenario_name"]
    return fn(cov)
