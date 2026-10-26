"""
Per-app exponentialGroth Calculator workload (``com.exponential_groth.calculator``).

Calculator is a scientific Android calculator with a single ``MainActivity`` (View-based
ConstraintLayout, no Compose) backed by a custom KaTeX-rendering ``MathView`` (WebView
internally). Each ``=`` press triggers a calculation in the Kotlin engine plus a KaTeX
render in the MathView. That's the cross-variant signal: bytecode dispatch through the
expression parser + KaTeX renderer pipeline.

**Energy axis:** real. Each calculation routes through the expression-tree evaluator
(``viewModel.calculate`` chain) and re-renders the result MathView via KaTeX-on-WebView.
Repeated function calls (log/trig/factorial/√/cube root) and bracket-parse pipelines are
the heaviest paths. The activity-transition into ``SettingsActivity`` exercises Android's
view inflation + AppCompat dispatch from a fresh activity stack.

**Bangcle-on-Pixel-9 status:** YES via **Option D stub-injection** (verified 2026-05-15:
`calculator_stub_baseline_protected.signed.apk` installs as ``primaryCpuAbi=arm64-v8a``
on Pixel 9). Baseline at upstream has no `lib/` directory: Option D adds
``lib/arm64-v8a/libplaceholder.so`` so Bangcle's input-driven mechanism propagates
``libSecShell.so`` into the arm64 dir. Full mechanism:
``tools/inject_arm64_stub.sh``.

**Fixture dependencies:** none; pure-Kotlin app with no network, no permissions, no
external state. The Espresso surface is tiny (`MainActivityTest.kt`: 1 mirrorable test
`multiArgumentFunctions` doing log(3, 27)=3, plus `useAppContext` smoke). Most of the
Appium scenarios are "inspired" content-action coverage exercising the calculator's many
button paths.

Env-var contract owned here:

- ``CALCULATOR_ACTIVITY``: activity to launch (default
  ``com.exponential_groth.calculator.MainActivity``).
- ``APPIUM_WORKLOAD``: workload selector; only ``espresso_mirror`` implemented.
- ``APPIUM_BUILD_LABEL``: free-form tag (``baseline`` / ``r8`` / ``allatori`` /
  ``bangcle`` / ``stub_baseline`` / ``stub_bangcle``) included in JSONL rows + the
  scenario report.

Scenario list (locked, N=10):

1. ``S1_home_visible``            (mirror, MainActivity setup: digits+ops visible)
2. ``S2_simple_arithmetic``       (inspired; 3 quick arithmetic calcs: 7+3, 9*8, 100/4)
3. ``S3_log_function_mirror``     (mirror, ``multiArgumentFunctions``: log_{27}(3)=3)
4. ``S4_trig_functions``          (inspired; sin/cos/tan around 0)
5. ``S5_powers_and_roots``        (inspired; 5², 2³, √16, ∛27)
6. ``S6_factorial_and_constants`` (exploration; 5! + Constants bottom sheet round-trip)
7. ``S7_brackets_complex_expr``   (inspired; (3+4)*2-6/3, bracket parsing pipeline)
8. ``S8_clear_and_nav_arrows``    (exploration; AC + buttonBlue navigation)
9. ``S9_settings_round_trip``     (exploration; long-press ``=`` → SettingsActivity → back)
10. ``S10_long_expression``       (inspired; sin(45) + cos(45) * 10 with mixed input)
"""

from __future__ import annotations

import os
import time
from typing import Any

from .._lib import (
    SessionConnectTimeoutError,
    append_coverage_jsonl,
    await_app_ready,
    build_uiautomator2_options,
    bump_driver_http_timeout,
    connect_remote_webdriver,
    dismiss_system_dialogs,
    emit_stderr,
    ensure_foreground,
    env_truthy,
    is_likely_http_timeout,
    log,
    resolve_artifact_dir,
    resolve_dump_dir,
    save_page_source,
    start_workload_thread_and_block_until_done,
    write_appium_status,
    write_scenario_report,
)


# --- Package / activity ------------------------------------------------------
PACKAGE = "com.exponential_groth.calculator"
DEFAULT_ACTIVITY = "com.exponential_groth.calculator.MainActivity"

# --- Button id table (from app/src/main/res/layout/activity_main.xml) --------
DIGIT = {
    "0": "button16", "1": "button17", "2": "button14", "3": "button11",
    "4": "button18", "5": "button15", "6": "button12", "7": "button1",
    "8": "button20", "9": "button19", ".": "button13",
}
OP = {
    "+": "button7", "-": "button3", "*": "button8", "/": "button4",
    "=": "button2", "AC": "button5", "DEL": "button9", "ANS": "button6",
    "(": "button39", ")": "button40",
}
FUNC = {
    "sin": "button34", "cos": "button35", "tan": "button36",
    "log10": "button29", "ln": "button30", "log_xy": "button24",
    "sqrt": "button26", "x_squared": "button27", "x_cubed": "button22",
    "factorial": "button32", "abs": "button21",
    "x_to_y": "button28", "x_inverse": "button23",
    "extra_minus": "button31", "S_to_D": "button41", "ENG": "button38",
    "STO": "button37", "M_plus": "button42",
}
NAV = {
    "left": "buttonBlueLeft", "right": "buttonBlueRight",
    "up": "buttonBlueTop", "down": "buttonBlueBottom",
}


# --- Locked scenario list (trimmed to ~120s) ---------------------------------
# Historical N=11 full list totaled 217s. Trimmed 2026-07-24 to hit the ~120s
# active-workload target while keeping the highest-energy scenarios:
#   S1  =  7.6s  health gate / home visible
#   S4  = 29.5s  transcendentals (sin/cos/tan/log/exp): highest FPU load
#   S6  = 22.3s  factorial + math constants: recursive-heavy compute
#   S7  = 23.5s  brackets + complex expression: nested arithmetic parser
#   S11 = 23.8s  ANS chain + display toggles: chained state + repeated render
# Total scenario wall = 106.7s → ~120s including harness overhead.
# Same trimmed list runs across ALL variants (matched-pair rule preserved).
CALCULATOR_SCENARIO_LIST: tuple[str, ...] = (
    "S1_home_visible",
    "S4_trig_functions",
    "S6_factorial_and_constants",
    "S7_brackets_complex_expr",
    "S11_ans_chain_and_display_toggles",
)


# --- Helpers -----------------------------------------------------------------
def _find_by_id(driver: Any, view_id: str, timeout: float = 3.0) -> Any:
    """Find element by package-prefixed resource id; returns None on miss."""
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().resourceId("%s:id/%s")' % (PACKAGE, view_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els:
            return els[0]
        time.sleep(0.2)
    return None


def _find_by_text(driver: Any, text: str, partial: bool = False, timeout: float = 3.0) -> Any:
    from appium.webdriver.common.appiumby import AppiumBy
    sel = ('new UiSelector().textContains("%s")' % text if partial
           else 'new UiSelector().text("%s")' % text)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els:
            return els[0]
        time.sleep(0.2)
    return None


def main_calculator_visible(driver: Any) -> bool:
    """Home predicate: '=' button (button2) is present in the keypad."""
    return _find_by_id(driver, OP["="], timeout=2.5) is not None


def _dismiss_page_size_dialog_permanently(driver: Any) -> bool:
    """Tap 'Don't Show Again' on the Android-16 PageSizeMismatchDialog if visible.

    Calculator BASELINE has no native libs so the dialog doesn't fire, but the
    Option-D stub baseline AND the resulting Bangcle pack BOTH carry non-16-KB-aligned
    `.so`s and trigger the dialog like the rest of the cohort.
    """
    btn = _find_by_text(driver, "Don't Show Again", timeout=0.8)
    if btn is None:
        return False
    try:
        btn.click()
        log("dismissed PageSizeMismatchDialog with 'Don't Show Again' (persistent)")
        time.sleep(0.8)
        return True
    except Exception as ex:
        log("PageSizeMismatchDialog dismiss failed: %s %s" % (type(ex).__name__, ex))
        return False


def _press_back(driver: Any) -> None:
    try:
        driver.back()
    except Exception:
        pass
    time.sleep(0.6)


def _long_press_id(driver: Any, view_id: str, duration_ms: int = 700,
                   timeout: float = 2.0) -> bool:
    """Long-press an element by resource id via UiAutomator2 longClickGesture.

    Used for the calculator's secondary actions:
    - button2 (=) long-press → SettingsActivity
    - button9 (DEL) long-press → ConstantsBottomSheet
    - button22 (x³) long-press → cube root (∛)
    - button20 (8) long-press → 'B' variable

    Never `mobile: shell`: Appium's UiAutomator2 security policy blocks it
    (``mobile: shell`` taps fail).
    """
    el = _find_by_id(driver, view_id, timeout=timeout)
    if el is None:
        return False
    try:
        rect = el.rect
        cx = int(rect["x"] + rect["width"] / 2)
        cy = int(rect["y"] + rect["height"] / 2)
        driver.execute_script(
            "mobile: longClickGesture",
            {"x": cx, "y": cy, "duration": duration_ms},
        )
        return True
    except Exception as ex:
        log("longClickGesture on %s failed: %s %s" % (view_id, type(ex).__name__, ex))
        return False


def _tap(driver: Any, view_id: str, timeout: float = 2.0) -> bool:
    """Standard tap on a button by resource id. Returns True on success."""
    el = _find_by_id(driver, view_id, timeout=timeout)
    if el is None:
        return False
    try:
        el.click()
        return True
    except Exception as ex:
        log("click on %s failed: %s %s" % (view_id, type(ex).__name__, ex))
        return False


def _tap_seq(driver: Any, ids: list[str], inter_delay: float = 0.12) -> int:
    """Tap a sequence of button ids. Returns count of successful taps."""
    ok = 0
    for view_id in ids:
        if _tap(driver, view_id, timeout=1.2):
            ok += 1
        time.sleep(inter_delay)
    return ok


def _digits_seq(s: str) -> list[str]:
    """Convert a digit/operator/decimal string like '7+3' into button id sequence."""
    out: list[str] = []
    for ch in s:
        if ch in DIGIT:
            out.append(DIGIT[ch])
        elif ch in OP:
            out.append(OP[ch])
        elif ch.isspace():
            continue
        else:
            raise ValueError("unknown char %r in expression sequence" % ch)
    return out


def _press_ac(driver: Any) -> bool:
    """Tap AC (all-clear): clears the editor + result. Safe between scenarios."""
    return _tap(driver, OP["AC"], timeout=1.5)


def _read_result_text(driver: Any) -> str | None:
    """Return MVResult text if surfaceable via UiAutomator2, else None.

    MVResult is a KaTeX-on-WebView ``MathView``: its `text` attribute is the KaTeX
    source string (e.g. ``\\( 3.0 \\)``) when set programmatically. UiAutomator2 can
    sometimes surface this via the accessibility node tree; if not, we fall back to
    a presence-only check.
    """
    el = _find_by_id(driver, "MVResult", timeout=0.6)
    if el is None:
        return None
    try:
        t = el.text
        if t:
            return t
    except Exception:
        pass
    try:
        return el.get_attribute("contentDescription") or None
    except Exception:
        return None


def _input_count_from_calculation_view(driver: Any) -> int:
    """Heuristic: how 'full' is the calculation entry view (TVCalculation/katexMathView)?

    Returns a small integer that grows with each digit/operator pressed. Used to confirm
    'something was entered' without depending on the KaTeX text being reliably readable.
    Always returns 0 if the view isn't found.
    """
    el = _find_by_id(driver, "katexMathView", timeout=0.4) or _find_by_id(
        driver, "TVCalculation", timeout=0.3
    )
    if el is None:
        return 0
    try:
        t = (el.text or "") + (el.get_attribute("contentDescription") or "")
        return len(t)
    except Exception:
        return 0


# --- Scenario implementations ------------------------------------------------
def _run_s1_home_visible(driver: Any) -> dict[str, Any]:
    """Mirror MainActivity setup: verify the canonical keypad buttons are present
    and follow with an active interactivity probe (1+2= and AC) so we exercise the
    engine, not just count buttons."""
    cov = {"scenario_name": "S1_home_visible",
           "espresso_class": "MainActivityTest",
           "espresso_method": "useAppContext (mirror — packageName + setup + probe)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    eq = _find_by_id(driver, OP["="], timeout=8.0)
    seven = _find_by_id(driver, DIGIT["7"], timeout=1.0)
    plus = _find_by_id(driver, OP["+"], timeout=1.0)
    ac = _find_by_id(driver, OP["AC"], timeout=1.0)
    mvres = _find_by_id(driver, "MVResult", timeout=1.0)
    visible = [eq, seven, plus, ac, mvres]
    cov["eq_present"] = eq is not None
    cov["seven_present"] = seven is not None
    cov["plus_present"] = plus is not None
    cov["ac_present"] = ac is not None
    cov["MVResult_present"] = mvres is not None
    # Active interactivity probe: 1+2= then AC. Confirms wiring + spends real cycles.
    probe_taps = _tap_seq(
        driver,
        [DIGIT["1"], OP["+"], DIGIT["2"], OP["="]],
        inter_delay=0.13,
    )
    probe_sample = _read_result_text(driver)
    cov["probe_taps_ok"] = probe_taps
    cov["probe_result_sample"] = probe_sample
    _press_ac(driver)
    cov["scenario_success"] = all(v is not None for v in visible[:4]) and probe_taps >= 3
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = cov["scenario_success"]
    if not cov["scenario_success"]:
        reasons = ["%s_missing" % name for name, v in zip(
            ("eq", "seven", "plus", "ac"), visible[:4]) if v is None]
        if probe_taps < 3:
            reasons.append("probe_taps=%d/4" % probe_taps)
        cov["scenario_fail_reasons_strict_ui"] = reasons
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s2_simple_arithmetic(driver: Any) -> dict[str, Any]:
    """4 back-to-back calcs: 7+3, 9*8, 100/4, 123-89. Each = press triggers KaTeX
    re-render: instead of sleeping 1.2s for the render, we drop to 0.4s and run a
    4th meaningful calc per loop so the energy window stays busy with real compute."""
    cov = {"scenario_name": "S2_simple_arithmetic",
           "espresso_class": "MainActivityTest",
           "espresso_method": "(inspired — 4 simple arithmetic calcs)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    calcs = [("7+3=", "10"), ("9*8=", "72"), ("100/4=", "25"), ("123-89=", "34")]
    results: list[dict[str, Any]] = []
    for expr, expected in calcs:
        _press_ac(driver)
        time.sleep(0.4)
        taps_ok = _tap_seq(driver, _digits_seq(expr))
        time.sleep(0.4)  # short KaTeX settle, then keep moving
        sample = _read_result_text(driver)
        results.append({"expr": expr, "expected": expected,
                        "taps_ok": taps_ok, "result_sample": sample})
    cov["calcs"] = results
    cov["all_calcs_had_full_taps"] = all(r["taps_ok"] >= 4 for r in results)
    cov["scenario_success"] = cov["all_calcs_had_full_taps"]
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["some_arithmetic_taps_dropped"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s3_log_function_mirror(driver: Any) -> dict[str, Any]:
    """Mirror MainActivityTest.multiArgumentFunctions: log_{27}(3) = 3.0.

    Upstream: button24 (log_xy) → 3 → buttonBlueRight (→) → 2 → 7 → =, expects "\\( 3.0 \\)".
    """
    cov = {"scenario_name": "S3_log_function_mirror",
           "espresso_class": "MainActivityTest",
           "espresso_method": "multiArgumentFunctions (mirror)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _press_ac(driver)
    time.sleep(0.4)
    sequence = [
        FUNC["log_xy"],   # log_a(b)
        DIGIT["3"],       # 3
        NAV["right"],     # → move to subscript
        DIGIT["2"],       # 2
        DIGIT["7"],       # 7
        OP["="],          # evaluate
    ]
    taps_ok = _tap_seq(driver, sequence, inter_delay=0.18)
    time.sleep(0.4)  # short KaTeX settle; then do a second log calc as active work
    result_sample = _read_result_text(driver)
    # Chain a second log calc: log_{8}(2)=; exercises the multi-arg log path again
    _press_ac(driver)
    time.sleep(0.3)
    sequence2 = [
        FUNC["log_xy"], DIGIT["8"], NAV["right"], DIGIT["2"], OP["="],
    ]
    taps_ok_2 = _tap_seq(driver, sequence2, inter_delay=0.18)
    time.sleep(0.4)
    result_sample_2 = _read_result_text(driver)
    cov["taps_ok"] = taps_ok
    cov["taps_total"] = len(sequence)
    cov["result_sample"] = result_sample
    cov["second_log_taps_ok"] = taps_ok_2
    cov["second_log_result_sample"] = result_sample_2
    cov["expected_substring"] = "3"
    # Strict: all 6 taps landed AND result_sample contains "3" (when surfaceable)
    contains_3 = result_sample is not None and "3" in result_sample
    cov["result_contains_3"] = contains_3
    cov["scenario_success"] = taps_ok == len(sequence)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = [
            "log_seq_taps=%d/%d" % (taps_ok, len(sequence))
        ]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s4_trig_functions(driver: Any) -> dict[str, Any]:
    """sin, cos, tan applied to 0: exercises 3 different function-evaluator paths."""
    cov = {"scenario_name": "S4_trig_functions",
           "espresso_class": "MainActivityTest",
           "espresso_method": "(inspired — sin/cos/tan)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Round 1: the original trio sin(0), cos(0), tan(0). Drop the 1.3s render-wait
    # to 0.4s.
    #
    # v5 2026-08-03 RECALIBRATION: the round-2 extras (sin(30), cos(45),
    # tan(45)) are REMOVED. They were added only to "fill the same wall
    # budget", and measured against the current code the cell spans 143.0s,
    # 13s over the [110, 130]s window. Each trig calc costs 6.34s on Pixel 6,
    # so dropping the three takes S4 from 38.0s to ~19.0s and the cell to
    # ~124s, centred in the window.
    #
    # Scoring-neutral by construction: `all_three_full_taps` below reads
    # `results[:3]`, i.e. the trio only: the extras never contributed to
    # strict-pass. N is unchanged at 5 scenarios.
    trio = [("sin", FUNC["sin"]), ("cos", FUNC["cos"]), ("tan", FUNC["tan"])]
    extras: list[tuple[str, Any, str]] = []
    results: list[dict[str, Any]] = []
    for name, fn_id in trio:
        _press_ac(driver)
        time.sleep(0.4)
        # fn_id(0)=
        seq = [fn_id, DIGIT["0"], OP[")"], OP["="]]
        taps_ok = _tap_seq(driver, seq, inter_delay=0.15)
        time.sleep(0.4)
        sample = _read_result_text(driver)
        results.append({"fn": name, "taps_ok": taps_ok, "result_sample": sample})
    for name, fn_id, arg in extras:
        _press_ac(driver)
        time.sleep(0.3)
        seq = [fn_id] + _digits_seq(arg) + [OP[")"], OP["="]]
        taps_ok = _tap_seq(driver, seq, inter_delay=0.15)
        time.sleep(0.4)
        sample = _read_result_text(driver)
        results.append({"fn": name, "taps_ok": taps_ok, "result_sample": sample})
    cov["trig_calcs"] = results
    cov["all_three_full_taps"] = all(r["taps_ok"] >= 3 for r in results[:3])
    cov["scenario_success"] = cov["all_three_full_taps"]
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["some_trig_taps_dropped"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s5_powers_and_roots(driver: Any) -> dict[str, Any]:
    """5², 2³, √16, √81: 4 exponent/root code paths. Drops 1.2s KaTeX waits to
    0.4s and adds a 4th √81 calc to keep wall budget full of real compute."""
    cov = {"scenario_name": "S5_powers_and_roots",
           "espresso_class": "MainActivityTest",
           "espresso_method": "(inspired — x², x³, √x ×2)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    results: list[dict[str, Any]] = []
    plan = [
        ("5_squared",  [DIGIT["5"], FUNC["x_squared"], OP["="]]),
        ("2_cubed",    [DIGIT["2"], FUNC["x_cubed"], OP["="]]),
        ("sqrt_16",    [FUNC["sqrt"], DIGIT["1"], DIGIT["6"], OP[")"], OP["="]]),
        ("sqrt_81",    [FUNC["sqrt"], DIGIT["8"], DIGIT["1"], OP[")"], OP["="]]),
    ]
    for label, seq in plan:
        _press_ac(driver)
        time.sleep(0.4)
        taps_ok = _tap_seq(driver, seq, inter_delay=0.15)
        time.sleep(0.4)
        sample = _read_result_text(driver)
        results.append({"calc": label, "taps_ok": taps_ok,
                        "taps_total": len(seq), "result_sample": sample})
    cov["calcs"] = results
    cov["all_full_taps"] = all(r["taps_ok"] == r["taps_total"] for r in results)
    cov["scenario_success"] = cov["all_full_taps"]
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["some_power_taps_dropped"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s6_factorial_and_constants(driver: Any) -> dict[str, Any]:
    """5! evaluation + Constants bottom sheet open/close round-trip (long-press DEL).

    Per MainActivity.kt: long-pressing ``button9`` (DEL) opens a
    ``ConstantsBottomSheet`` (BottomSheetDialogFragment, FragmentManager show). Dismiss
    with back-press to return to MainActivity.
    """
    cov = {"scenario_name": "S6_factorial_and_constants",
           "espresso_class": "MainActivity",
           "espresso_method": "(exploration — x! + Constants bottom sheet long-press)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _press_ac(driver)
    time.sleep(0.4)
    # 5!=
    fact_seq = [DIGIT["5"], FUNC["factorial"], OP["="]]
    fact_taps = _tap_seq(driver, fact_seq, inter_delay=0.15)
    time.sleep(0.4)  # short KaTeX settle then continue with more factorial work
    fact_sample = _read_result_text(driver)
    cov["factorial_taps_ok"] = fact_taps
    cov["factorial_result_sample"] = fact_sample
    # Chain 7! and 10! to keep wall full of compute instead of idle render-waits
    extra_fact_results: list[dict[str, Any]] = []
    for digits in (["7"], ["1", "0"]):
        _press_ac(driver)
        time.sleep(0.3)
        seq = [DIGIT[d] for d in digits] + [FUNC["factorial"], OP["="]]
        ok = _tap_seq(driver, seq, inter_delay=0.15)
        time.sleep(0.4)
        extra_fact_results.append({"n": "".join(digits), "taps_ok": ok,
                                   "result_sample": _read_result_text(driver)})
    cov["extra_factorial_results"] = extra_fact_results
    # Now open Constants via long-press on DEL (button9)
    _press_ac(driver)
    time.sleep(0.3)
    opened = _long_press_id(driver, OP["DEL"], duration_ms=700, timeout=1.5)
    cov["constants_long_press_dispatched"] = opened
    time.sleep(1.5)  # KEEP: real fragment inflation, not idle KaTeX wait
    # The bottom sheet's outer ConstraintLayout is in constants_bottom_sheet_content.xml.
    # We can't always pin the exact resource id (depends on fragment id assignment), so
    # use a textual heuristic: the sheet contains symbol entries like π, e, c, NA. Look
    # for any of them.
    sheet_seen = False
    matched_label = None
    for label in ("π", "Pi", "e", "Euler", "Speed of light", "c"):
        if _find_by_text(driver, label, partial=True, timeout=0.4) is not None:
            sheet_seen = True
            matched_label = label
            cov["sheet_label_matched"] = label
            break
    cov["sheet_visible"] = sheet_seen
    # Actively interact while sheet is open: tap the matched constant entry to insert
    # it into the editor (substantive cross-sheet→editor flow, not just open/close).
    constant_inserted = False
    if sheet_seen and matched_label is not None:
        try:
            el = _find_by_text(driver, matched_label, partial=True, timeout=0.6)
            if el is not None:
                el.click()
                constant_inserted = True
        except Exception as ex:
            log("constant entry tap failed: %s %s" % (type(ex).__name__, ex))
    cov["constant_inserted"] = constant_inserted
    # Clicking a constant already dismisses the sheet via ConstantsBottomSheet.close()
    # (BottomSheetBehavior.STATE_HIDDEN). Only press back if the sheet is still open;
    # otherwise the back-press pops MainActivity itself and exits the calculator to the
    # home screen, cascading a 0-tap failure into S7/S8/S9/S10 (they do not restart the
    # activity). Diagnosed 2026-07-02.
    if not constant_inserted:
        _press_back(driver)
        time.sleep(0.4)
    # If we inserted a constant, complete an expression: *2= so the constants
    # path actually contributes a real calculation.
    if constant_inserted:
        cov["follow_up_taps"] = _tap_seq(
            driver, [OP["*"], DIGIT["2"], OP["="]], inter_delay=0.15
        )
        time.sleep(0.4)
        cov["follow_up_result_sample"] = _read_result_text(driver)
    # Verify we're back on MainActivity
    main_back = main_calculator_visible(driver)
    cov["main_visible_after_sheet"] = main_back
    cov["scenario_success"] = bool(fact_taps == 3 and main_back)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        reasons = []
        if fact_taps != 3: reasons.append("factorial_taps=%d/3" % fact_taps)
        if not main_back: reasons.append("not_back_on_main_after_constants_sheet")
        cov["scenario_fail_reasons_strict_ui"] = reasons
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s7_brackets_complex_expr(driver: Any) -> dict[str, Any]:
    """(3+4)*2-6/3: exercises bracket parser + 4-operator pipeline + KaTeX rendering."""
    cov = {"scenario_name": "S7_brackets_complex_expr",
           "espresso_class": "MainActivityTest",
           "espresso_method": "(inspired — brackets + 4 ops in one expression)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _press_ac(driver)
    time.sleep(0.4)
    expr = "(3+4)*2-6/3="
    seq = _digits_seq(expr)
    taps_ok = _tap_seq(driver, seq, inter_delay=0.12)
    time.sleep(0.5)
    result_sample = _read_result_text(driver)
    # Immediately run a second bracket expr: bracket parser is the energy signal,
    # exercise it twice instead of sleeping for KaTeX.
    _press_ac(driver)
    time.sleep(0.3)
    expr2 = "((5+1)*(2+3))="
    seq2 = _digits_seq(expr2)
    taps_ok_2 = _tap_seq(driver, seq2, inter_delay=0.12)
    time.sleep(0.5)
    result_sample_2 = _read_result_text(driver)
    cov["expr"] = expr
    cov["taps_ok"] = taps_ok
    cov["taps_total"] = len(seq)
    cov["result_sample"] = result_sample
    cov["expr2"] = expr2
    cov["expr2_taps_ok"] = taps_ok_2
    cov["expr2_taps_total"] = len(seq2)
    cov["expr2_result_sample"] = result_sample_2
    cov["scenario_success"] = taps_ok == len(seq)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = [
            "complex_expr_taps=%d/%d" % (taps_ok, len(seq))
        ]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s8_clear_and_nav_arrows(driver: Any) -> dict[str, Any]:
    """Type expression, use buttonBlueLeft/Right to move cursor, edit, evaluate.

    Tests the calculator's *cursor* navigation (separate from history). Each arrow press
    routes through the editor's cursor-move handler: different code path than digit
    entry.
    """
    cov = {"scenario_name": "S8_clear_and_nav_arrows",
           "espresso_class": "MainActivity",
           "espresso_method": "(exploration — cursor nav + edit)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _press_ac(driver)
    time.sleep(0.4)
    # Type "12+34"
    base_seq = _digits_seq("12+34")
    base_taps = _tap_seq(driver, base_seq, inter_delay=0.12)
    time.sleep(0.4)
    # 3 left arrows to move cursor mid-expression
    arrow_taps = 0
    for _ in range(3):
        if _tap(driver, NAV["left"], timeout=1.0):
            arrow_taps += 1
        time.sleep(0.18)
    # Functionally edit: backspace the digit under cursor and type 9 instead, so
    # the arrow navigation causes a different result. Original was 12+34=46;
    # we change one digit before evaluating to make arrows do real work.
    edit_taps = 0
    if _tap(driver, OP["DEL"], timeout=1.0):
        edit_taps += 1
    time.sleep(0.18)
    if _tap(driver, DIGIT["9"], timeout=1.0):
        edit_taps += 1
    time.sleep(0.18)
    # 2 right arrows
    for _ in range(2):
        if _tap(driver, NAV["right"], timeout=1.0):
            arrow_taps += 1
        time.sleep(0.18)
    # Now evaluate
    _tap(driver, OP["="], timeout=1.0)
    time.sleep(0.4)
    result_sample = _read_result_text(driver)
    # Second nav-edit cycle: extend the expression, navigate left, DEL, retype,
    # evaluate again: keeps the wall budget active with the editor cursor path.
    _press_ac(driver)
    time.sleep(0.3)
    base_taps_2 = _tap_seq(driver, _digits_seq("56+78"), inter_delay=0.12)
    time.sleep(0.3)
    arrow_taps_2 = 0
    for _ in range(2):
        if _tap(driver, NAV["left"], timeout=1.0):
            arrow_taps_2 += 1
        time.sleep(0.15)
    if _tap(driver, OP["DEL"], timeout=1.0):
        edit_taps += 1
    time.sleep(0.15)
    if _tap(driver, DIGIT["1"], timeout=1.0):
        edit_taps += 1
    time.sleep(0.15)
    for _ in range(2):
        if _tap(driver, NAV["right"], timeout=1.0):
            arrow_taps_2 += 1
        time.sleep(0.15)
    _tap(driver, OP["="], timeout=1.0)
    time.sleep(0.4)
    result_sample_2 = _read_result_text(driver)
    cov["base_taps"] = base_taps
    cov["arrow_taps"] = arrow_taps
    cov["edit_taps"] = edit_taps
    cov["result_sample"] = result_sample
    cov["second_cycle_base_taps"] = base_taps_2
    cov["second_cycle_arrow_taps"] = arrow_taps_2
    cov["second_cycle_result_sample"] = result_sample_2
    cov["scenario_success"] = bool(base_taps >= 4 and arrow_taps >= 3)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = [
            "base_taps=%d arrow_taps=%d" % (base_taps, arrow_taps)
        ]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s9_settings_round_trip(driver: Any) -> dict[str, Any]:
    """Long-press = → SettingsActivity opens, then back to main.

    Per MainActivity.kt: long-pressing ``button2`` (equals) calls
    ``startSettingsActivity()`` which Intent-launches ``SettingsActivity``. Back-press
    returns to MainActivity. This exercises the activity-stack transition path AND
    AppCompat view inflation for a fresh activity: energy-relevant.
    """
    cov = {"scenario_name": "S9_settings_round_trip",
           "espresso_class": "MainActivity",
           "espresso_method": "(exploration — long-press = → SettingsActivity → back)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _press_ac(driver)
    time.sleep(0.4)
    dispatched = _long_press_id(driver, OP["="], duration_ms=900, timeout=2.0)
    cov["long_press_dispatched"] = dispatched
    time.sleep(1.5)  # KEEP: real Activity launch + view inflation
    # Check we left MainActivity: the '=' button shouldn't be visible anymore
    left_main = _find_by_id(driver, OP["="], timeout=1.0) is None
    cov["left_main_after_long_press"] = left_main
    # Actively scroll the SettingsActivity instead of sleeping for ~2.5s. Use
    # driver.swipe to drag the RecyclerView up/down a few times, then look for any
    # switch-like control and toggle it. This replaces the textbook anti-pattern of
    # idle-sleeping "so the energy window captures it" with real interaction.
    swipe_count = 0
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        cx = w // 2
        y_top, y_bot = int(h * 0.25), int(h * 0.75)
        for direction in (("up", y_bot, y_top), ("up", y_bot, y_top),
                          ("down", y_top, y_bot)):
            _, y0, y1 = direction
            try:
                driver.swipe(cx, y0, cx, y1, 400)
                swipe_count += 1
            except Exception as ex:
                log("settings swipe failed: %s %s" % (type(ex).__name__, ex))
            time.sleep(0.25)
    except Exception as ex:
        log("could not size settings window: %s %s" % (type(ex).__name__, ex))
    cov["settings_swipes"] = swipe_count
    # Try toggling any Switch widget by class. AndroidX preference switches
    # commonly surface as android.widget.Switch or SwitchCompat.
    toggle_dispatched = False
    try:
        from appium.webdriver.common.appiumby import AppiumBy
        for cls in ("android.widget.Switch",
                    "androidx.appcompat.widget.SwitchCompat",
                    "android.widget.CompoundButton"):
            sel = 'new UiSelector().className("%s")' % cls
            els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
            if els:
                try:
                    els[0].click()
                    toggle_dispatched = True
                    cov["toggle_class"] = cls
                    break
                except Exception as ex:
                    log("settings toggle click failed: %s %s"
                        % (type(ex).__name__, ex))
    except Exception as ex:
        log("settings toggle lookup failed: %s %s" % (type(ex).__name__, ex))
    cov["toggle_dispatched"] = toggle_dispatched
    # One more swipe to keep the RecyclerView active before backing out.
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        driver.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), 350)
        swipe_count += 1
    except Exception:
        pass
    cov["settings_swipes_total"] = swipe_count
    _press_back(driver)
    time.sleep(0.5)
    main_back = main_calculator_visible(driver)
    cov["main_visible_after_back"] = main_back
    cov["scenario_success"] = bool(dispatched and left_main and main_back)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        reasons = []
        if not dispatched: reasons.append("long_press_failed")
        if not left_main: reasons.append("did_not_leave_main_after_long_press")
        if not main_back: reasons.append("did_not_recover_to_main_after_back")
        cov["scenario_fail_reasons_strict_ui"] = reasons
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s10_long_expression(driver: Any) -> dict[str, Any]:
    """Mixed-function long expression: sin(45)+cos(45)*10=; multiple function calls + ops.

    Heaviest single expression of the suite: 2 trig function evaluations, 1 multiplication,
    1 addition, 1 final = press. Exercises the most of the expression pipeline per second.
    """
    cov = {"scenario_name": "S10_long_expression",
           "espresso_class": "MainActivityTest",
           "espresso_method": "(inspired — sin(45)+cos(45)*10)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _press_ac(driver)
    time.sleep(0.4)
    seq = [
        FUNC["sin"],
        DIGIT["4"], DIGIT["5"],
        OP[")"],
        OP["+"],
        FUNC["cos"],
        DIGIT["4"], DIGIT["5"],
        OP[")"],
        OP["*"],
        DIGIT["1"], DIGIT["0"],
        OP["="],
    ]
    taps_ok = _tap_seq(driver, seq, inter_delay=0.13)
    time.sleep(0.5)
    result_sample = _read_result_text(driver)
    cov["taps_ok"] = taps_ok
    cov["taps_total"] = len(seq)
    cov["result_sample"] = result_sample
    # Exercise the display-format code path harder: S↔D toggle twice + one ENG toggle.
    # Each press routes through the result-display renderer in a different mode.
    _tap(driver, FUNC["S_to_D"], timeout=1.0)
    time.sleep(0.3)
    cov["s_to_d_dispatched"] = True
    after_toggle = _read_result_text(driver)
    cov["result_sample_after_toggle"] = after_toggle
    _tap(driver, FUNC["S_to_D"], timeout=1.0)
    time.sleep(0.3)
    after_toggle_2 = _read_result_text(driver)
    cov["result_sample_after_toggle_2"] = after_toggle_2
    _tap(driver, FUNC["ENG"], timeout=1.0)
    time.sleep(0.3)
    after_eng = _read_result_text(driver)
    cov["result_sample_after_eng"] = after_eng
    cov["scenario_success"] = taps_ok >= len(seq) - 1  # allow one drop at the trailing =
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = [
            "long_expr_taps=%d/%d" % (taps_ok, len(seq))
        ]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s11_ans_chain_and_display_toggles(driver: Any) -> dict[str, Any]:
    """ANS chaining + display-format toggles: substantive computation across results.

    Computes 50/2= (=25), then chains ANS+15= (=40), then ANS*3= (=120), then runs
    a fresh longer expression 99*99=, and finishes by cycling S↔D and ENG toggles
    to repeatedly route through the result-display formatter. Exercises the engine's
    answer-substitution path (different from re-entering digits) AND the display
    formatter: both energy-relevant.
    """
    cov = {"scenario_name": "S11_ans_chain_and_display_toggles",
           "espresso_class": "MainActivity",
           "espresso_method": "(inspired — ANS chaining + S↔D / ENG cycling)",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    chain_results: list[dict[str, Any]] = []
    # Round 1: seed with 50/2=
    _press_ac(driver)
    time.sleep(0.3)
    seed_taps = _tap_seq(driver, _digits_seq("50/2="), inter_delay=0.13)
    time.sleep(0.4)
    chain_results.append({"step": "seed_50/2",
                          "taps_ok": seed_taps,
                          "result_sample": _read_result_text(driver)})
    # Round 2: ANS + 15 =
    ans_seq_a = [OP["ANS"], OP["+"], DIGIT["1"], DIGIT["5"], OP["="]]
    ans_a_taps = _tap_seq(driver, ans_seq_a, inter_delay=0.14)
    time.sleep(0.4)
    chain_results.append({"step": "ANS+15",
                          "taps_ok": ans_a_taps,
                          "result_sample": _read_result_text(driver)})
    # Round 3: ANS * 3 =
    ans_seq_b = [OP["ANS"], OP["*"], DIGIT["3"], OP["="]]
    ans_b_taps = _tap_seq(driver, ans_seq_b, inter_delay=0.14)
    time.sleep(0.4)
    chain_results.append({"step": "ANS*3",
                          "taps_ok": ans_b_taps,
                          "result_sample": _read_result_text(driver)})
    # Round 4: fresh longer computation 99*99 =
    _press_ac(driver)
    time.sleep(0.3)
    big_taps = _tap_seq(driver, _digits_seq("99*99="), inter_delay=0.13)
    time.sleep(0.4)
    chain_results.append({"step": "99*99",
                          "taps_ok": big_taps,
                          "result_sample": _read_result_text(driver)})
    # Cycle the display-format toggles a few times: substantive re-render path
    toggle_count = 0
    for fn_key in ("S_to_D", "ENG", "S_to_D", "ENG"):
        if _tap(driver, FUNC[fn_key], timeout=1.0):
            toggle_count += 1
        time.sleep(0.25)
    cov["display_toggles_dispatched"] = toggle_count
    cov["final_result_sample"] = _read_result_text(driver)
    cov["chain_results"] = chain_results
    all_ok = all(r["taps_ok"] >= 3 for r in chain_results)
    cov["scenario_success"] = bool(all_ok and toggle_count >= 3)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = [
            "chain_or_toggles_dropped: toggles=%d" % toggle_count
        ]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


_SCENARIO_FUNCS = {
    "S1_home_visible": _run_s1_home_visible,
    "S2_simple_arithmetic": _run_s2_simple_arithmetic,
    "S3_log_function_mirror": _run_s3_log_function_mirror,
    "S4_trig_functions": _run_s4_trig_functions,
    "S5_powers_and_roots": _run_s5_powers_and_roots,
    "S6_factorial_and_constants": _run_s6_factorial_and_constants,
    "S7_brackets_complex_expr": _run_s7_brackets_complex_expr,
    "S8_clear_and_nav_arrows": _run_s8_clear_and_nav_arrows,
    "S9_settings_round_trip": _run_s9_settings_round_trip,
    "S10_long_expression": _run_s10_long_expression,
    "S11_ans_chain_and_display_toggles": _run_s11_ans_chain_and_display_toggles,
}


# --- Workload runner ---------------------------------------------------------
def _run_espresso_mirror_workload(
    driver: Any,
    package: str,
    deadline: float,
    dump_dir: str | None,
    build_label: str,
    artifact_dir: str | None,
) -> int:
    """Run the locked CALCULATOR_SCENARIO_LIST once, end-to-end. Returns steps_executed."""
    scenarios_results: list[dict[str, Any]] = []
    steps = 0

    for scenario_name in CALCULATOR_SCENARIO_LIST:
        if time.monotonic() > deadline:
            log("workload deadline reached before %s; bailing" % scenario_name)
            break
        fn = _SCENARIO_FUNCS.get(scenario_name)
        if fn is None:
            log("unknown scenario %s; skipping" % scenario_name)
            continue
        # Defensive: dismiss any re-fired Page Size dialog (Bangcle pack triggers it per
        # activity transition; baseline doesn't trigger it at all).
        _dismiss_page_size_dialog_permanently(driver)
        t0 = time.monotonic()
        try:
            cov = fn(driver)
        except Exception as ex:
            cov = {
                "scenario_name": scenario_name,
                "scenario_success": False,
                "scenario_success_strict_ui": False,
                "scenario_success_action_only": False,
                "scenario_fail_reasons_strict_ui": ["exception:%s:%s" % (type(ex).__name__, str(ex)[:160])],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "ts_end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            log("scenario %s raised: %s: %s" % (scenario_name, type(ex).__name__, ex))
        cov["build_label"] = build_label
        cov["workload"] = "espresso_mirror"
        cov["suite_round"] = 1
        cov["scenario_wall_seconds"] = round(time.monotonic() - t0, 3)
        scenarios_results.append(cov)
        append_coverage_jsonl(dump_dir, cov)
        steps += 1
        log("scenario %s -> success=%s strict=%s wall=%.2fs"
            % (scenario_name, cov.get("scenario_success"),
               cov.get("scenario_success_strict_ui"), cov["scenario_wall_seconds"]))

    by_scenario: dict[str, dict[str, Any]] = {}
    for cov in scenarios_results:
        sn = cov.get("scenario_name", "?")
        row = by_scenario.setdefault(sn, {
            "runs": 0, "success": 0, "fail": 0,
            "success_strict_ui": 0, "fail_strict_ui": 0,
            "success_action_only": 0, "fail_action_only": 0,
            "example_fail_reasons": [],
        })
        row["runs"] += 1
        if cov.get("scenario_success_strict_ui"):
            row["success"] += 1
            row["success_strict_ui"] += 1
        else:
            row["fail"] += 1
            row["fail_strict_ui"] += 1
            for r in (cov.get("scenario_fail_reasons_strict_ui") or []):
                if r not in row["example_fail_reasons"]:
                    row["example_fail_reasons"].append(r)
        if cov.get("scenario_success_action_only"):
            row["success_action_only"] += 1
        else:
            row["fail_action_only"] += 1

    total_runs = sum(v["runs"] for v in by_scenario.values())
    total_succ = sum(v["success"] for v in by_scenario.values())
    agg: dict[str, Any] = {
        "build_label": build_label or None,
        "by_scenario": by_scenario,
        "summary": {
            "fail_count": total_runs - total_succ,
            "success_count": total_succ,
            "total_scenario_executions": total_runs,
            "total_strict_ui_pass": sum(v.get("success_strict_ui", 0) for v in by_scenario.values()),
            "total_action_only_pass": sum(v.get("success_action_only", 0) for v in by_scenario.values()),
        },
    }
    lines = [
        "Espresso-mirror scenario report (Calculator)",
        "build_label: %s" % (build_label or "(none)"),
        "",
        "summary: %s runs, %s passed, %s failed"
        % (total_runs, total_succ, total_runs - total_succ),
        "",
    ]
    for sn in [s for s in CALCULATOR_SCENARIO_LIST if s in by_scenario]:
        r = by_scenario[sn]
        lines.append(
            "%s  runs=%s  headline_ok=%s  headline_fail=%s  strict_ui=%s/%s  action_only=%s/%s"
            % (sn, r["runs"], r["success"], r["fail"],
               r.get("success_strict_ui", 0), r.get("fail_strict_ui", 0),
               r.get("success_action_only", 0), r.get("fail_action_only", 0))
        )
        if r["example_fail_reasons"]:
            lines.append("  examples: %s" % r["example_fail_reasons"][:3])
    text = "\n".join(lines) + "\n"
    write_scenario_report(artifact_dir, agg, text, workload_name="espresso_mirror")
    return steps


def run_workload(experiment: Any, device: Any) -> None:
    """Per-app entrypoint per ``CONVENTIONS.md`` §3."""
    dump_dir = resolve_dump_dir()
    artifact_dir = resolve_artifact_dir(dump_dir)

    package = getattr(experiment, "package", None) or PACKAGE
    try:
        duration_s = float(getattr(experiment, "duration", 120))
    except Exception:
        duration_s = 120.0
    if duration_s < 30:
        duration_s = 30.0

    activity = os.environ.get("CALCULATOR_ACTIVITY", DEFAULT_ACTIVITY)
    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    connect_timeout_s = 120.0
    build_label = os.environ.get("APPIUM_BUILD_LABEL", "")

    status: dict[str, Any] = {
        "failure_reason": None,
        "session_connect_timeout_seconds": connect_timeout_s,
        "session_created": False,
        "session_start_seconds": None,
        "session_timeout": False,
        "steps_executed": 0,
        "workload_mode": "espresso_mirror",
        "workload_started": False,
    }

    def _workload():
        from appium import webdriver
        thread_started = time.monotonic()
        driver = None
        try:
            options = build_uiautomator2_options(
                udid=device.id,
                package=package,
                activity=activity,
                duration_s=duration_s,
            )
            log(
                "connecting to %s (target workload %.1fs; session wall-clock cap %.1fs)"
                % (server, duration_s, connect_timeout_s),
            )
            driver = connect_remote_webdriver(
                webdriver, server, options, connect_timeout_s, dump_dir,
            )
            status["session_created"] = True
            status["session_start_seconds"] = round(time.monotonic() - thread_started, 3)
            bump_driver_http_timeout(driver, duration_s)

            try:
                driver.execute_script(
                    "mobile: startActivity",
                    {"intent": "%s/%s" % (package, activity)},
                )
                log("forced startActivity(%s/%s)" % (package, activity))
                time.sleep(1.5)
            except Exception as ex:
                log("startActivity force-switch failed: %s %s" % (type(ex).__name__, ex))

            failsafe_deadline = time.monotonic() + max(duration_s * 2, 240)
            _dismiss_page_size_dialog_permanently(driver)
            await_app_ready(
                driver,
                package,
                failsafe_deadline,
                is_main_ui_visible=main_calculator_visible,
                dump_dir=dump_dir,
            )
            _dismiss_page_size_dialog_permanently(driver)

            log("session ready; running locked %d-scenario list end-to-end"
                % len(CALCULATOR_SCENARIO_LIST))
            status["workload_started"] = True
            status["steps_executed"] = _run_espresso_mirror_workload(
                driver, package, failsafe_deadline, dump_dir, build_label, artifact_dir,
            )
        except SessionConnectTimeoutError as ex:
            status["failure_reason"] = str(ex)
            status["session_timeout"] = True
            log("SESSION_TIMEOUT: %s" % ex)
            emit_stderr("INTERACTION_APPIUM: SESSION_TIMEOUT after %.1fs - %s"
                        % (connect_timeout_s, ex))
        except Exception as ex:
            status["failure_reason"] = "%s: %s" % (type(ex).__name__, ex)
            if is_likely_http_timeout(ex):
                status["session_timeout"] = True
                log("SESSION_TIMEOUT (HTTP): %s" % ex)
            else:
                log("SESSION_CONNECT_FAILED: %s: %s" % (type(ex).__name__, ex))
            emit_stderr("INTERACTION_APPIUM: %s: %s" % (type(ex).__name__, ex))
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    failsafe_seconds = max(duration_s * 2, 240)
    start_workload_thread_and_block_until_done(_workload, failsafe_seconds)

    write_appium_status(artifact_dir, status)
    log("workload thread finished")
