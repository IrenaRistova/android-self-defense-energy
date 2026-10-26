"""
Per-app Tipuous workload (JoshLudahl ``com.tips.tipuous``).

Tipuous is a Jetpack Compose tip calculator: one ``OutlinedTextField`` for the bill amount, four
``AssistChip``s for tip percentage (``5%`` / ``10%`` / ``15%`` / ``Other``), a custom slider when
``Other`` is selected, a split-bill slider, two reactive ``Text`` rows for "Tip Amount" / "Total
Amount", and a "Share Bill" button. Every keystroke into the bill field re-runs a regex match,
mutates ``MainViewModel.bill``, fans through ``combine(_bill, _tipPercentEnum, _customTipPercent,
_splitCount)`` to ``TipCalculator.calculate(...)``, and re-renders the totals, i.e. the work is
all CPU + Compose recomposition, no native libs and no IO.

**Energy axis:** Pure Kotlin/JVM arithmetic (``billAmount * tipPercentageValue``,
``roundDoubleToTwoDecimalPlaces``) + Compose UI recomposition driven by per-keystroke ``StateFlow``
emissions; variant comparison is dominated by JIT/Compose dispatch for ``OutlinedTextField`` +
``AssistChip`` + reactive ``Text`` updates. Workload-window energy delta vs idle is expected to be
modest (~0.3-0.6 W on Pixel 3 baseline if recomputation fires per keystroke; less if Compose
smart-batches the recomposition pass).

Workload modes selected by ``APPIUM_WORKLOAD``:

- ``espresso_mirror`` (default for the master experiment): runs the locked
  :data:`TIPUOUS_SCENARIO_LIST` end-to-end, no time-box, wall time is a measured output.

Env-var contract owned here (others are documented in the relevant ``_lib`` module):

- ``TIPUOUS_ACTIVITY``: activity to launch (default ``com.tips.tipuous.MainActivity``).
- ``APPIUM_WORKLOAD``: workload selector; only ``espresso_mirror`` is implemented.
- ``APPIUM_BUILD_LABEL``: free-form tag (``baseline`` / ``r8`` / ``allatori`` /
  ``bangcle``) included in JSONL rows + the scenario report. Lets one config run across multiple
  variant builds while keeping rows distinguishable.
- ``TIPUOUS_BILL_AMOUNT_DEFAULT``: bill amount typed in single-percent scenarios (default
  ``12.99``: same value as the upstream Espresso tests, locks the expected tip / total strings).
- ``TIPUOUS_FIVE_PERCENT_LABEL``: selector text for the ``5%`` chip (default ``5%``).
- ``TIPUOUS_TEN_PERCENT_LABEL``: selector text for the ``10%`` chip (default ``10%``).
- ``TIPUOUS_FIFTEEN_PERCENT_LABEL``: selector text for the ``15%`` chip (default ``15%``).
- ``TIPUOUS_OTHER_LABEL``: selector text for the ``Other`` chip (default ``Other``).
- ``TIPUOUS_BILL_AMOUNT_LABEL_SUBSTR``: substring used to detect the bill TextField label
  (default ``Enter Bill Amount``: confirms the main screen is up).
- ``TIPUOUS_TIP_AMOUNT_PREFIX``: prefix used to detect the displayed tip line
  (default ``Tip Amount $``: comes straight from ``MainScreen.kt``).

Artifacts produced (under ``paths.OUTPUT_DIR`` when Android Runner is the parent):

- ``appium_workload_coverage.jsonl``
- ``espresso_mirror_scenario_report.{json,txt}``
- ``appium_status.json``
- ``appium_ui_dumps/*.xml`` (forensics, capped by ``APPIUM_UI_DUMP_MAX``)

Provenance summary (locked when the module was written):

- ``7`` upstream ``@Test`` methods in ``MainActivityTest.kt`` → ``7`` Appium scenarios in
  :data:`TIPUOUS_SCENARIO_LIST`.
- ``6`` mirror, ``0`` inspired, ``1`` exploration; ``1`` upstream test left unmirrored.
- The unmirrored test (``inputNumberIntoOtherAmount``) targets an "Other Amount" EditText
  that the Compose UI no longer has (see the calibration notes below).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Optional

from .._lib import (
    SessionConnectTimeoutError,
    append_coverage_jsonl,
    await_app_ready,
    build_uiautomator2_options,
    bump_driver_http_timeout,
    connect_remote_webdriver,
    dismiss_system_dialogs,
    element_present_uiautomator,
    emit_stderr,
    ensure_foreground,
    env_float,
    env_int,
    env_truthy,
    is_likely_http_timeout,
    log,
    resolve_artifact_dir,
    resolve_dump_dir,
    safe_click_text_contains,
    save_page_source,
    start_workload_thread_and_block,
    start_workload_thread_and_block_until_done,
    write_appium_status,
    write_scenario_report,
)
from .._lib.coverage import reset_dump_counter
from . import scoring as _tipuous_scoring


# ---------------------------------------------------------------------------
# Per-app calibration: locked scenario list (2026-05-09)
# ---------------------------------------------------------------------------
#
# Fixed-N scenario contract (2026-05-08): scenario count, not duration, is
# the cross-variant comparable unit. For each app, ONE number N of scenarios is calibrated on
# the slowest device (Pixel 3 baseline) targeting ~120 s wall time. That same N is then run on
# every variant × every device. Wall time becomes a *measured output*, not a controlled input.
#
# Set α (PERCENTAGE-MIRROR + COMPUTE-SWEEP): 2026-05-09
# -----------------------------------------------------
# Tipuous has 7 upstream @Test methods. 5 of them are direct percentage mirrors (5/10/15/20/
# customTip), 2 are trivial type-only tests (`inputNumberIntoAmount`, `inputNumberIntoOtherAmount`)
# whose bodies are commented out and whose intent is fully subsumed by the percentage mirrors.
# We mirror 6 (the 5 percentage tests + 1 of the trivial type-only tests) and add 1 exploration
# scenario (`tipPercentageSweep`) to engage the per-keystroke compute axis aggressively. The
# remaining trivial type-only test (`inputNumberIntoOtherAmount`) is left unmirrored because the
# Compose UI no longer has an "Other Amount" EditText (the slider replaces it), and the test's
# intent is fully exercised by `customTipAmount` and `tipPercentageSweep`.
#
# Estimated Pixel 3 wall time ~107 s (target window 100–130 s). Budget by scenario:
#
#   typeAmountOnly                     ~5 s    type 12.99 + read-back
#   inputAmountAndSelectFivePercent    ~9 s    type 12.99 + tap 5% + verify $0.65 + $13.64
#   inputAmountAndSelectTenPercent     ~9 s    type 12.99 + tap 10% + verify $1.30 + $14.29
#   inputAmountAndSelectFifteenPercent ~9 s    type 12.99 + tap 15% + verify $1.95 + $14.94
#   inputAmountAndSelectTwentyPercent  ~9 s    type 12.99 + tap Other (defaults 20%) + verify
#                                              $2.60 + $15.59 (NB: upstream test referenced
#                                              `R.id.button_twenty_percent` which does NOT exist
#                                              in the current Compose UI; the 20% surface is the
#                                              `Other` chip with default `customTipPercent=20`).
#   customTipAmount                    ~12 s   type 12.99 + tap Other + best-effort drag the
#                                              custom-tip slider right + verify the displayed
#                                              custom percent has changed away from default 20%
#                                              (NB: upstream expected `$3.25` / `$16.24` for 25%
#                                              custom; the Compose slider drag is approximate so
#                                              we score on "percent moved" not exact value).
#   tipPercentageSweep                 ~50 s   type 10 different bill amounts, tap each percent
#                                              chip between amounts (heaviest keystroke +
#                                              recomposition load: exploration scenario).
#                                              Total: 7 scenarios. Audio engine N/A (no native
#                                              audio in this app); the workload-window energy
#                                              signal is dominated by Compose recomposition +
#                                              ViewModel.combine() re-emission.
#
# This list is LOCKED. Do not reorder or change without a new calibration run.
# See:
#   - appium_android_tests/CONVENTIONS.md → § 6.5 "Per-app scenario calibration"
#   - appium_android_tests/CONVENTIONS.md → "Adding a new app" checklist
#   - appium_android_tests/tipuous/CALIBRATION.md → per-scenario timings + chosen N
TIPUOUS_SCENARIO_LIST: tuple[str, ...] = (
    "typeAmountOnly",                       # ~5 s: bare type + read-back (mirror)
    "inputAmountAndSelectFivePercent",      # ~9 s: type 12.99 + 5% chip + verify (mirror)
    "inputAmountAndSelectTenPercent",       # ~9 s: type 12.99 + 10% chip + verify (mirror)
    "inputAmountAndSelectFifteenPercent",   # ~9 s: type 12.99 + 15% chip + verify (mirror)
    "inputAmountAndSelectTwentyPercent",    # ~9 s: type 12.99 + Other chip + verify (mirror)
    "customTipAmount",                      # ~12 s: type 12.99 + Other + slider drag (mirror)
    "tipPercentageSweep",                   # ~50 s: 10× bill × 4 chips (exploration)
    # ─────────────────────────────────────────────────────────────────────────────────────────
    # Total: ~103 s on Pixel 3 baseline (estimated, not a calibration run).
)


def _tipuous_defaults() -> dict[str, str]:
    """Default selector substrings + chip labels.

    Each entry can be overridden via the corresponding ``TIPUOUS_*`` env var so non-English
    builds (or future apps that fork the strings) can reuse this workload without code changes.
    Default values come from the actual Compose source: ``MainScreen.kt`` lines 138-217 (chip
    labels are hardcoded literals "5%", "10%", "15%", "Other"; the bill TextField label is
    "Enter Bill Amount"; the displayed totals are "Tip Amount $X.XX" / "Total Amount $X.XX").
    """
    return {
        "bill_amount_default": os.environ.get("TIPUOUS_BILL_AMOUNT_DEFAULT", "12.99"),
        "five_percent_label": os.environ.get("TIPUOUS_FIVE_PERCENT_LABEL", "5%"),
        "ten_percent_label": os.environ.get("TIPUOUS_TEN_PERCENT_LABEL", "10%"),
        "fifteen_percent_label": os.environ.get("TIPUOUS_FIFTEEN_PERCENT_LABEL", "15%"),
        "other_label": os.environ.get("TIPUOUS_OTHER_LABEL", "Other"),
        "bill_label_substr": os.environ.get(
            "TIPUOUS_BILL_AMOUNT_LABEL_SUBSTR",
            "Enter Bill Amount",
        ),
        "tip_amount_prefix": os.environ.get("TIPUOUS_TIP_AMOUNT_PREFIX", "Tip Amount $"),
    }


def main_tipuous_visible(driver: Any, d: dict[str, str]) -> bool:
    """Detect the main Tipuous screen via the bill TextField label or the 5% chip.

    Both are stable text-based hooks that survive R8 minification and packing: neither relies
    on internal class names or view IDs. We accept either signal because the Compose label may
    take an extra recomposition pass to settle on slow first-run.
    """
    esc_label = d["bill_label_substr"].replace('"', '\\"')
    if element_present_uiautomator(
        driver,
        'new UiSelector().textContains("%s")' % esc_label,
        timeout=2.5,
    ):
        return True
    esc_chip = d["five_percent_label"].replace('"', '\\"')
    return element_present_uiautomator(
        driver,
        'new UiSelector().text("%s")' % esc_chip,
        timeout=1.5,
    )


def _record(cov: dict[str, Any], key: str, ok: bool) -> bool:
    """Record ``ok`` against ``cov[key]`` with **logical OR** semantics across the cycle.

    Repeated calls within one cycle keep the value sticky once it flips to True (an action that
    succeeded once still counts as having succeeded).
    """
    cov[key] = bool(cov.get(key, False) or ok)
    return ok


def _miss_dump(driver: Any, dump_dir: Optional[str], stem: str, ok: bool) -> None:
    """Save a ``page_source`` XML dump on miss, if ``APPIUM_SAVE_UI_ON_MISS`` is truthy."""
    if ok or not dump_dir:
        return
    if not env_truthy("APPIUM_SAVE_UI_ON_MISS"):
        return
    save_page_source(driver, dump_dir, stem)


def _dismiss_keyboard_safe(driver: Any) -> None:
    """``driver.hide_keyboard()`` swallowing all exceptions (no-op when no keyboard is up)."""
    try:
        driver.hide_keyboard()
    except Exception:
        pass


def _safe_call(fn: Callable[[], None], label: str) -> None:
    try:
        fn()
    except Exception as ex:
        log("%s failed (%s): %s" % (label, type(ex).__name__, ex))


# ---------------------------------------------------------------------------
# Bill-field helpers: all UI access goes through these so the rest of the
# scenario code stays declarative.
# ---------------------------------------------------------------------------


def _bill_edit_text(driver: Any) -> Any:
    """Find the bill ``OutlinedTextField`` (Compose surfaces it as ``android.widget.EditText``).

    Tipuous's main screen has exactly one EditText (the bill amount). The split-bill widget is
    a Slider, not an EditText, and the Other-percent slider only appears after tapping ``Other``.
    We grab ``instance(0)`` which is the bill field whether or not the custom-percent UI is up.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    return driver.find_element(
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().className("android.widget.EditText").instance(0)',
    )


def _read_bill_text(driver: Any) -> str:
    """Visible text inside the bill ``OutlinedTextField`` (or empty on any failure)."""
    try:
        el = _bill_edit_text(driver)
        return (el.text or "").strip()
    except Exception:
        return ""


def _clear_bill_field(driver: Any) -> bool:
    """Click + clear the bill field, with DEL-keycode fallback for Compose's quirky ``clear()``.

    Compose's OutlinedTextField sometimes ignores a single ``el.clear()`` call (the underlying
    Compose state is not always invalidated when UiAutomator clears the text), so we follow up
    with a few KEYCODE_DEL presses to clear residue character-by-character. Returns True if the
    field is empty afterwards.
    """
    try:
        el = _bill_edit_text(driver)
        el.click()
        time.sleep(0.15)
        try:
            el.clear()
        except Exception as ex:
            log("bill field clear() raised: %s %s (continuing with DEL fallback)" % (type(ex).__name__, ex))
        time.sleep(0.1)
        # 2026-05-12 fix: do all 12 DEL presses UNCONDITIONALLY. The previous early-break on
        # `el.text == ""` was unreliable on Compose because the UiAutomator-readable .text
        # attribute lags the underlying TextField state by 100–200 ms; the loop would exit on
        # a stale empty read while the actual TextField still held a residue character. That
        # residue then fused with the next send_keys, producing `bill_field_not_matching` on
        # 50 % of tipPercentageSweep sub-iterations. DEL is a no-op on an already-empty field
        # so over-clearing is safe; cost is ~0.6 s per clear (12 × 0.05 s), acceptable for the
        # 50 s sweep budget.
        for _ in range(12):
            try:
                driver.press_keycode(67)
            except Exception:
                break
            time.sleep(0.05)
        time.sleep(0.2)  # let Compose recomposition settle before any subsequent read
        observed = _read_bill_text(driver)
        return observed == ""
    except Exception as ex:
        log("clear bill field failed: %s %s" % (type(ex).__name__, ex))
        return False


def _type_bill_amount(driver: Any, text: str) -> bool:
    """Clear + ``send_keys(text)`` the bill ``OutlinedTextField``. Returns True on success."""
    try:
        _clear_bill_field(driver)
        el = _bill_edit_text(driver)
        el.send_keys(text)
        log("typed %r into bill amount" % text)
        _dismiss_keyboard_safe(driver)
        time.sleep(0.35)
        return True
    except Exception as ex:
        log("type_bill_amount failed: %s %s" % (type(ex).__name__, ex))
        return False


def _record_bill_typed_observed(
    cov: dict[str, Any],
    result_key: str,
    driver: Any,
    text: str,
) -> None:
    """Type ``text`` into the bill field and record action / observed / strict in ``cov``.

    Mirrors :func:`_lib.scoring.record_numeric_edit_observed` for Metronome's view-id-based
    EditTexts; we can't reuse that helper directly because Tipuous's bill field has no
    package-prefixed view id (Compose). Strict matches when the field text equals the typed
    value after stripping the soft-keyboard's whitespace artefacts.
    """
    ok = _type_bill_amount(driver, text)
    observed = _read_bill_text(driver)
    cov[result_key] = ok
    cov["%s_observed" % result_key] = observed[:160]
    cov["%s_strict" % result_key] = bool(ok and observed.strip() == text.strip())


# ---------------------------------------------------------------------------
# Chip helpers: Compose AssistChip surfaces with `text=label` exactly. We have
# to use **exact-text** UiSelector (not textContains) because "5%" is a strict
# substring of "15%" and "25%", which would steal clicks.
# ---------------------------------------------------------------------------


def _click_text_exact(driver: Any, text: str, timeout: float = 3.5) -> bool:
    """Click the first view whose **text equals** ``text`` (exact match, not contains).

    Used for percentage chips because ``5%`` is a substring of ``15%`` / ``25%`` etc., so the
    contains-based helper in ``_lib.driver`` would race to a wrong chip on a busy layout.
    """
    if not text:
        return False
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    esc = text.replace('"', '\\"')
    sel = 'new UiSelector().text("%s")' % esc
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((AppiumBy.ANDROID_UIAUTOMATOR, sel)),
        )
        el.click()
        log("clicked text(exact, %r)" % text)
        return True
    except Exception as ex:
        log("miss text(exact, %r): %s" % (text, type(ex).__name__))
        return False


def _ui_text_contains(driver: Any, substr: str, timeout: float = 2.0) -> bool:
    """True iff some view's visible text contains ``substr``."""
    if not substr:
        return False
    esc = substr.replace('"', '\\"')
    return element_present_uiautomator(
        driver,
        'new UiSelector().textContains("%s")' % esc,
        timeout=timeout,
    )


def _read_first_text_starting_with(driver: Any, prefix: str) -> str:
    """Find the first view whose text starts with ``prefix`` and return its full visible text.

    Used to read the displayed Tip Amount line when we want forensics on what the app actually
    shows (e.g. for ``customTipAmount`` where we don't know the exact tip number after slider
    drag). Returns an empty string on any failure.
    """
    if not prefix:
        return ""
    from appium.webdriver.common.appiumby import AppiumBy

    esc = prefix.replace('"', '\\"')
    sel = 'new UiSelector().textStartsWith("%s")' % esc
    try:
        el = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        return (el.text or "").strip()
    except Exception:
        return ""


def _drag_custom_tip_slider_right(driver: Any, fraction_of_screen: float = 0.18) -> bool:
    """Best-effort right-drag on the Compose custom-tip Slider.

    The Compose ``Slider`` with ``valueRange = 1f..50f`` and default value 20 surfaces (via
    UiAutomator2) as either ``android.widget.SeekBar`` or a generic node. We try both selector
    families. If neither resolves we fall back to a horizontal swipe at a y-coordinate
    heuristically near the slider (just below the percent chip row, ~50% down the screen);
    the swipe will land on whatever is at that location and is cheap to retry-tolerant.

    Returns True if any drag action was issued (i.e. the scenario was able to attempt the
    drag, even if we cannot prove the slider value changed). The caller checks the percent
    Box text post-drag to confirm a change.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    el = None
    for sel in (
        'new UiSelector().className("android.widget.SeekBar")',
        'new UiSelector().className("android.view.View").descriptionContains("Slider")',
    ):
        try:
            candidate = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
            el = candidate
            break
        except Exception:
            el = None
            continue
    try:
        if el is not None:
            loc = el.location
            size = el.size
            y = int(loc["y"] + size["height"] / 2)
            x_from = int(loc["x"] + size["width"] * 0.40)
            x_to = int(loc["x"] + size["width"] * 0.40 + size["width"] * 0.20)
            driver.swipe(x_from, y, x_to, y, 400)
            log("dragged slider right (element-anchored): (%d,%d) -> (%d,%d)" % (x_from, y, x_to, y))
            time.sleep(0.4)
            return True
        # Fallback: blind horizontal swipe across the middle of the screen.
        wsize = driver.get_window_size()
        w = wsize["width"]
        h = wsize["height"]
        y = int(h * 0.50)
        x_from = int(w * 0.30)
        x_to = int(w * 0.30 + w * float(fraction_of_screen))
        driver.swipe(x_from, y, x_to, y, 400)
        log("dragged slider right (blind fallback): (%d,%d) -> (%d,%d)" % (x_from, y, x_to, y))
        time.sleep(0.4)
        return True
    except Exception as ex:
        log("drag custom tip slider failed: %s %s" % (type(ex).__name__, ex))
        return False


# ---------------------------------------------------------------------------
# Per-scenario implementations.
# ---------------------------------------------------------------------------


def _build_scenarios(
    driver: Any,
    package: str,
    d: dict[str, str],
    deadline_monotonic: float,
) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
    """Construct the ``[(name, fn), ...]`` scenario list closing over driver / defaults.

    Pattern mirrors the in-function definitions of the Metronome module's workload runner
    (Metronome is not in this repository). Keeping them inside a builder avoids polluting the module namespace
    with a dozen single-use functions while still letting :func:`run_espresso_mirror_workload`
    iterate them as a registry.
    """
    bill_default = d["bill_amount_default"]

    def scenario_type_amount_only() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MainActivityTest",
            "espresso_method": "inputNumberIntoAmount",
        }
        _record_bill_typed_observed(cov, "bill_amount_default", driver, bill_default)
        return cov

    def scenario_input_and_select_five_percent() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MainActivityTest",
            "espresso_method": "inputNumberIntoAmountAndSelectFivePercent",
        }
        _record_bill_typed_observed(cov, "bill_5pct", driver, bill_default)
        ok_chip = _click_text_exact(driver, d["five_percent_label"])
        cov["five_percent_chip_clicked"] = ok_chip
        time.sleep(0.45)
        cov["tip_amount_visible_065"] = _ui_text_contains(driver, "$0.65", timeout=2.0)
        cov["total_amount_visible_1364"] = _ui_text_contains(driver, "$13.64", timeout=2.0)
        return cov

    def scenario_input_and_select_ten_percent() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MainActivityTest",
            "espresso_method": "inputNumberIntoAmountAndSelectTenPercent",
        }
        _record_bill_typed_observed(cov, "bill_10pct", driver, bill_default)
        ok_chip = _click_text_exact(driver, d["ten_percent_label"])
        cov["ten_percent_chip_clicked"] = ok_chip
        time.sleep(0.45)
        cov["tip_amount_visible_130"] = _ui_text_contains(driver, "$1.30", timeout=2.0)
        cov["total_amount_visible_1429"] = _ui_text_contains(driver, "$14.29", timeout=2.0)
        return cov

    def scenario_input_and_select_fifteen_percent() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MainActivityTest",
            "espresso_method": "inputNumberIntoAmountAndSelectFifteenPercent",
        }
        _record_bill_typed_observed(cov, "bill_15pct", driver, bill_default)
        ok_chip = _click_text_exact(driver, d["fifteen_percent_label"])
        cov["fifteen_percent_chip_clicked"] = ok_chip
        time.sleep(0.45)
        cov["tip_amount_visible_195"] = _ui_text_contains(driver, "$1.95", timeout=2.0)
        cov["total_amount_visible_1494"] = _ui_text_contains(driver, "$14.94", timeout=2.0)
        return cov

    def scenario_input_and_select_twenty_percent() -> dict[str, Any]:
        # NB: upstream test referenced `R.id.button_twenty_percent` which does NOT exist in the
        # current Compose UI (only chips for 5/10/15 plus an "Other" custom chip). The 20% surface
        # is the `Other` chip with ViewModel default `customTipPercent = 20` → tip = bill * 0.20.
        # Tapping `Other` therefore produces the upstream-expected "$2.60" / "$15.59" without any
        # slider manipulation, i.e. the upstream intent is preserved even though the path is
        # different.
        cov: dict[str, Any] = {
            "espresso_class": "MainActivityTest",
            "espresso_method": "inputNumberIntoAmountAndSelectTwentyPercent",
        }
        _record_bill_typed_observed(cov, "bill_20pct", driver, bill_default)
        ok_chip = _click_text_exact(driver, d["other_label"])
        cov["other_chip_clicked_for_default_twenty"] = ok_chip
        time.sleep(0.45)
        cov["tip_amount_visible_260"] = _ui_text_contains(driver, "$2.60", timeout=2.0)
        cov["total_amount_visible_1559"] = _ui_text_contains(driver, "$15.59", timeout=2.0)
        return cov

    def scenario_custom_tip_amount() -> dict[str, Any]:
        # NB: upstream test `inputNumberIntoOtherAmountAndIntoOtherTipAmount` expected the user
        # to type `25` into a now-removed "Other Amount" EditText and verify "$3.25" / "$16.24"
        # (= 25% of $12.99). The Compose UI replaces that EditText with a Slider whose default
        # is 20%, so we type the bill, tap Other (activating the slider), and best-effort drag
        # the slider rightward, then we assert two things: (1) the percent display Box shows a
        # value ≠ 20, and (2) the displayed tip line is non-default. Strict-UI scoring uses
        # signal #1 (percent moved); action-only scoring uses (typed_ok AND chip_clicked AND
        # drag_attempted). We cannot reliably hit exactly 25% on Pixel 3 via UiAutomator swipe,
        # so the upstream "$3.25" / "$16.24" strings are kept as forensic references in
        # `cov["custom_tip_observed_text"]` but NOT enforced.
        cov: dict[str, Any] = {
            "espresso_class": "MainActivityTest",
            "espresso_method": "inputNumberIntoOtherAmountAndIntoOtherTipAmount",
        }
        _record_bill_typed_observed(cov, "bill_custom", driver, bill_default)
        ok_chip = _click_text_exact(driver, d["other_label"])
        cov["other_chip_clicked"] = ok_chip
        time.sleep(0.6)
        cov["custom_default_percent_shown"] = _ui_text_contains(driver, "20%", timeout=1.5)
        ok_drag = _drag_custom_tip_slider_right(driver, fraction_of_screen=0.18)
        cov["custom_tip_slider_drag_attempted"] = ok_drag
        time.sleep(0.5)
        observed_after = _read_first_text_starting_with(driver, d["tip_amount_prefix"])
        cov["custom_tip_observed_text"] = observed_after[:160]
        # Strict signal: the displayed Tip Amount line is no longer the default $2.60.
        cov["custom_tip_amount_changed_from_default"] = bool(
            observed_after and "$2.60" not in observed_after,
        )
        # Forensics: did the percent Box still show "20%" after drag?
        cov["custom_percent_still_default_twenty"] = _ui_text_contains(driver, "20%", timeout=1.0)
        return cov

    # Exploration scenario: heaviest keystroke + recomputation load. Cycles 10 amounts × 4
    # percent chips (resetting to a known chip between amounts to maximize the recompose count).
    _SWEEP_AMOUNTS: list[str] = [
        "1.99", "5.50", "10.00", "25.99", "50.00",
        "99.99", "250.50", "500.00", "1234.56", "9999.99",
    ]
    _SWEEP_CHIPS: list[tuple[str, str]] = [
        ("five_percent_label", "five_pct"),
        ("ten_percent_label", "ten_pct"),
        ("fifteen_percent_label", "fifteen_pct"),
        ("other_label", "other_pct"),
    ]

    def scenario_tip_percentage_sweep() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MasterExperiment",
            "espresso_method": "tipPercentageSweep",
            "sweep_amounts": list(_SWEEP_AMOUNTS),
            "sweep_steps": [],
        }
        all_typed_ok = True
        chip_index = 0
        for amount in _SWEEP_AMOUNTS:
            if time.monotonic() >= deadline_monotonic - 4:
                cov["sweep_aborted_due_to_deadline"] = True
                break
            ok_typed = _type_bill_amount(driver, amount)
            # Compose recomposition lag: the OutlinedTextField updates state asynchronously after
            # send_keys returns. Without a settle wait, ~50% of sub-iterations on Pixel 9 read the
            # PREVIOUS amount because the recompose hasn't propagated to UiAutomator yet. 0.35 s
            # is enough for the bill TextField at the workload's typing cadence (measured
            # 2026-05-12 fix iteration: 0/5 stale reads at 0.35 s vs 5/10 at 0 s).
            time.sleep(0.35)
            observed = _read_bill_text(driver)
            typed_strict = bool(ok_typed and observed.strip() == amount.strip())
            chip_key, chip_tag = _SWEEP_CHIPS[chip_index % len(_SWEEP_CHIPS)]
            chip_label = d[chip_key]
            ok_chip = _click_text_exact(driver, chip_label, timeout=2.5)
            time.sleep(0.3)
            tip_line = _read_first_text_starting_with(driver, d["tip_amount_prefix"])
            cov["sweep_steps"].append(
                {
                    "amount": amount,
                    "chip": chip_tag,
                    "typed_ok": ok_typed,
                    "observed_bill": observed[:40],
                    "typed_strict": typed_strict,
                    "chip_clicked": ok_chip,
                    "tip_line_observed": tip_line[:80],
                },
            )
            all_typed_ok = all_typed_ok and ok_typed
            chip_index += 1
        cov["sweep_all_typed_ok"] = all_typed_ok
        return cov

    return [
        ("typeAmountOnly", scenario_type_amount_only),
        ("inputAmountAndSelectFivePercent", scenario_input_and_select_five_percent),
        ("inputAmountAndSelectTenPercent", scenario_input_and_select_ten_percent),
        ("inputAmountAndSelectFifteenPercent", scenario_input_and_select_fifteen_percent),
        ("inputAmountAndSelectTwentyPercent", scenario_input_and_select_twenty_percent),
        ("customTipAmount", scenario_custom_tip_amount),
        ("tipPercentageSweep", scenario_tip_percentage_sweep),
    ]


# ---------------------------------------------------------------------------
# Workload runner: espresso_mirror equivalent (the only mode for tipuous).
# ---------------------------------------------------------------------------


def run_espresso_mirror_workload(
    driver: Any,
    package: str,
    deadline_monotonic: float,
    dump_dir: Optional[str],
) -> int:
    """Run the locked :data:`TIPUOUS_SCENARIO_LIST` end-to-end and write the scenario report.

    Mirrors the Metronome module's ``run_espresso_mirror_workload`` (not in this repository) for
    the Tipuous app: per-scenario coverage rows go to ``appium_workload_coverage.jsonl``, aggregate
    counts go to ``espresso_mirror_scenario_report.{json,txt}``. Wall time is a measured output;
    the legacy time-box (``deadline_monotonic``) is preserved in the helper signature only:
    callers under the per-app contract pass ``+inf``.

    Override (one-time calibration only): ``ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1`` runs every
    scenario in the candidate list (currently identical to the locked list: kept symmetric with
    Metronome for forward compatibility).
    """
    d = _tipuous_defaults()
    await_app_ready(
        driver,
        package,
        deadline_monotonic,
        is_main_ui_visible=lambda drv: main_tipuous_visible(drv, d),
        dump_dir=dump_dir,
    )

    artifact_dir = resolve_artifact_dir(dump_dir)
    build_label_e = os.environ.get("APPIUM_BUILD_LABEL", "").strip()

    scenarios = _build_scenarios(driver, package, d, deadline_monotonic)

    by_scenario: dict[str, dict[str, Any]] = {
        name: {
            "runs": 0,
            "success": 0,
            "fail": 0,
            "success_strict_ui": 0,
            "fail_strict_ui": 0,
            "success_action_only": 0,
            "fail_action_only": 0,
            "example_fail_reasons": [],
        }
        for name, _ in scenarios
    }

    def record_score(sn: str, strict_ok: bool, action_ok: bool, fail_reasons: list[str]) -> None:
        row = by_scenario[sn]
        row["runs"] += 1
        headline = action_ok if env_truthy("ESPRESSO_MIRROR_SCORE_ACTION_ONLY") else strict_ok
        if headline:
            row["success"] += 1
        else:
            row["fail"] += 1
            ex = row["example_fail_reasons"]
            if len(ex) < 6:
                ex.append(fail_reasons[:16])
        if strict_ok:
            row["success_strict_ui"] += 1
        else:
            row["fail_strict_ui"] += 1
        if action_ok:
            row["success_action_only"] += 1
        else:
            row["fail_action_only"] += 1

    def finalize_report() -> None:
        total_runs = sum(v["runs"] for v in by_scenario.values())
        total_succ = sum(v["success"] for v in by_scenario.values())
        agg: dict[str, Any] = {
            "build_label": build_label_e or None,
            "by_scenario": by_scenario,
            "scoring_options": {
                "headline_uses_action_only": env_truthy("ESPRESSO_MIRROR_SCORE_ACTION_ONLY"),
            },
            "summary": {
                "fail_count": total_runs - total_succ,
                "success_count": total_succ,
                "total_scenario_executions": total_runs,
                "total_strict_ui_pass": sum(v.get("success_strict_ui", 0) for v in by_scenario.values()),
                "total_action_only_pass": sum(v.get("success_action_only", 0) for v in by_scenario.values()),
            },
        }
        lines = [
            "Tipuous espresso-mirror scenario report (Appium-derived checks)",
            "build_label: %s" % (build_label_e or "(none)"),
            "",
            "summary: %s runs, %s passed, %s failed"
            % (total_runs, total_succ, total_runs - total_succ),
            "",
        ]
        for sn in sorted(by_scenario.keys()):
            r = by_scenario[sn]
            lines.append(
                "%s  runs=%s  headline_ok=%s  headline_fail=%s  strict_ui=%s/%s  action_only=%s/%s"
                % (
                    sn,
                    r["runs"],
                    r["success"],
                    r["fail"],
                    r.get("success_strict_ui", 0),
                    r.get("fail_strict_ui", 0),
                    r.get("success_action_only", 0),
                    r.get("fail_action_only", 0),
                ),
            )
            if r["example_fail_reasons"]:
                lines.append("  examples: %s" % r["example_fail_reasons"][:3])
        text = "\n".join(lines) + "\n"
        write_scenario_report(artifact_dir, agg, text, workload_name="espresso_mirror")

    # Filter to the locked TIPUOUS_SCENARIO_LIST (in that exact order). Override with
    # ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1 for one-time calibration runs.
    if env_truthy("ESPRESSO_MIRROR_RUN_ALL_SCENARIOS"):
        log(
            "tipuous espresso_mirror: ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1 — running all %d "
            "candidate scenarios (calibration mode, NOT for production sweeps)" % len(scenarios),
        )
        ordered_scenarios = scenarios
    else:
        by_name = {n: fn for n, fn in scenarios}
        missing = [n for n in TIPUOUS_SCENARIO_LIST if n not in by_name]
        if missing:
            log(
                "ERROR: locked TIPUOUS_SCENARIO_LIST entries not implemented in scenarios.py: %s "
                "— falling back to full candidate list to avoid hard-failing the run" % missing,
            )
            ordered_scenarios = scenarios
        else:
            ordered_scenarios = [(n, by_name[n]) for n in TIPUOUS_SCENARIO_LIST]
            log(
                "tipuous espresso_mirror: per-app contract — running %d locked scenarios "
                "end-to-end (no time-box; wall time is measured output): %s"
                % (len(ordered_scenarios), [n for n, _ in ordered_scenarios]),
            )

    step_count = 0
    suite_round = 1

    for scenario_name, scenario_fn in ordered_scenarios:
        step_count += 1
        scenario_started = time.monotonic()
        # v5 2026-08-03 BUGFIX: stamp the wall-clock START before the body
        # runs. It used to be stamped after, so ``ts`` held the scenario END
        # time; build_run_table_extended.py derives the workload window as
        # ``max(ts + scenario_wall_seconds) - min(ts)``, which with ts = end
        # starts the window one scenario late and ends it one full scenario
        # wall past the real finish. Same fix applied to linkhub and
        # documenter: those three were the only cohort apps stamping ts
        # themselves; the other six get it from the coverage helper.
        scenario_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log(
            "tipuous espresso_mirror scenario %s (step #%s/%s, no time-box, locked-list)"
            % (scenario_name, step_count, len(ordered_scenarios)),
        )
        cov = scenario_fn()
        cov["workload"] = "espresso_mirror"
        cov["scenario_name"] = scenario_name
        cov["suite_round"] = suite_round
        cov["ts"] = scenario_ts
        cov["scenario_wall_seconds"] = round(time.monotonic() - scenario_started, 3)
        cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        strict_ok, action_ok, fail_reasons = _tipuous_scoring.score_scenario(scenario_name, cov)
        cov["scenario_success_strict_ui"] = strict_ok
        cov["scenario_success_action_only"] = action_ok
        cov["scenario_success"] = (
            action_ok if env_truthy("ESPRESSO_MIRROR_SCORE_ACTION_ONLY") else strict_ok
        )
        if fail_reasons:
            cov["scenario_fail_reasons_strict_ui"] = fail_reasons
        if build_label_e:
            cov["build_label"] = build_label_e
        record_score(scenario_name, strict_ok, action_ok, fail_reasons)
        cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _dismiss_keyboard_safe(driver)
        ensure_foreground(driver, package)
        log("tipuous espresso_mirror coverage: %s" % json.dumps(cov, sort_keys=True))
        append_coverage_jsonl(dump_dir, cov)

    if step_count == 0:
        log("tipuous espresso_mirror: no scenarios ran (await_app_ready failed?)")
        finalize_report()
        return 0
    finalize_report()
    return step_count


# ---------------------------------------------------------------------------
# Public entrypoint: Android Runner ``interaction`` hook target.
# ---------------------------------------------------------------------------


def run_workload(experiment: Any, device: Any) -> None:
    """Entrypoint called by the thin ``Scripts/interaction_appium_tipuous.py`` Android Runner hook.

    Reads ``package`` and ``duration`` from ``experiment``, builds Appium options, starts the
    workload thread, and blocks ``main()`` until the locked scenario list completes (with a
    failsafe cap). The calling hook should set ``"interaction_covers_duration": true`` in the
    experiment JSON to avoid a duplicate ``NativeExperiment`` sleep.

    Workload selection: ``APPIUM_WORKLOAD={espresso_mirror}`` (default ``espresso_mirror``;
    ``generic`` and ``metronome`` modes intentionally have no Tipuous analogue and would log a
    warning + fall back to ``espresso_mirror``).
    """
    package = getattr(experiment, "package", None) if experiment is not None else None
    duration_s = float(getattr(experiment, "duration", 60.0)) if experiment is not None else 60.0

    if not package:
        log("No package found; skipping.")
        return

    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723").rstrip("/")
    # 2026-08-01: default was ".MainActivity", which Appium resolves relative to
    # the APPLICATION ID, and tipuous's debug application id carries a `.debug`
    # suffix, so it tried to launch com.tips.tipuous.debug.MainActivity and got
    # "Activity class ... does not exist". The activity actually lives in the
    # unsuffixed package (aapt: launchable-activity com.tips.tipuous.MainActivity),
    # because applicationIdSuffix does not move Java packages. Fully qualify it,
    # as keep_recipe already does. Same class of bug as the podaura leading-dot
    # activity path.
    activity = os.environ.get("TIPUOUS_ACTIVITY", "com.tips.tipuous.MainActivity")
    workload_mode = os.environ.get("APPIUM_WORKLOAD", "espresso_mirror").strip().lower()
    if workload_mode and workload_mode != "espresso_mirror":
        log(
            "tipuous: APPIUM_WORKLOAD=%r is not implemented for this app; falling back to "
            "espresso_mirror." % workload_mode,
        )
        workload_mode = "espresso_mirror"
    dump_dir = resolve_dump_dir()

    print("=INTERACTION_APPIUM_TIPUOUS=")
    print("DEVICE (udid):", device.id)
    print("PACKAGE:", package)
    print("ACTIVITY:", activity)
    print("DURATION_S:", duration_s)
    print("APPIUM_SERVER:", server)
    print("APPIUM_WORKLOAD:", workload_mode)
    print("APPIUM_BUILD_LABEL:", os.environ.get("APPIUM_BUILD_LABEL", "").strip() or "(none)")
    print("APPIUM_UI_DUMP_DIR:", dump_dir or "(none)")
    print("APPIUM_WAIT_FOR_PACKAGE_S:", env_float("APPIUM_WAIT_FOR_PACKAGE_S", 60.0))
    print("APPIUM_WAIT_MAIN_UI_S:", env_float("APPIUM_WAIT_MAIN_UI_S", 45.0))
    print("APPIUM_SKIP_U2_INSTALL:", env_truthy("APPIUM_SKIP_U2_INSTALL"))
    print("APPIUM_FORCE_U2_INSTALL:", env_truthy("APPIUM_FORCE_U2_INSTALL"))
    print("APPIUM_ATTACH_ONLY:", env_truthy("APPIUM_ATTACH_ONLY"))
    print("APPIUM_APP_WAIT_DURATION_MS:", env_int("APPIUM_APP_WAIT_DURATION_MS", 10000))
    print("APPIUM_SESSION_CONNECT_TIMEOUT_S:", env_float("APPIUM_SESSION_CONNECT_TIMEOUT_S", 120.0))

    try:
        from appium import webdriver  # noqa: WPS433
    except ImportError as e:
        print(
            "INTERACTION_APPIUM: Appium Python client not installed. Run:\n"
            "  pip install -r requirements-appium.txt\n",
        )
        raise e

    def workload() -> None:
        reset_dump_counter()

        connect_timeout_s = min(max(env_float("APPIUM_SESSION_CONNECT_TIMEOUT_S", 120.0), 5.0), 900.0)
        artifact_dir = resolve_artifact_dir(dump_dir)
        status: dict[str, Any] = {
            "session_created": False,
            "session_start_seconds": None,
            "workload_started": False,
            "steps_executed": 0,
            "session_connect_timeout_seconds": connect_timeout_s,
            "workload_mode": workload_mode,
            "failure_reason": None,
            "session_timeout": False,
        }

        options = build_uiautomator2_options(
            udid=device.id,
            package=package,
            activity=activity,
            duration_s=duration_s,
        )
        attach_only = env_truthy("APPIUM_ATTACH_ONLY")

        driver = None
        thread_started = time.monotonic()
        try:
            log(
                "connecting to %s (target workload after session: %.1fs; session wall-clock cap %.1fs)"
                % (server, duration_s, connect_timeout_s),
            )
            driver = connect_remote_webdriver(webdriver, server, options, connect_timeout_s, artifact_dir)
            status["session_created"] = True
            status["session_start_seconds"] = round(time.monotonic() - thread_started, 3)
            bump_driver_http_timeout(driver, duration_s)

            if attach_only:
                try:
                    driver.activate_app(package)
                    log("activate_app(%s) after attach-only session" % package)
                except Exception as ex:
                    log("activate_app after session (%s): %s" % (type(ex).__name__, ex))

            handshake_s = time.monotonic() - thread_started
            no_deadline = float("inf")
            log(
                "session ready; per-app scenario-count contract — running locked %d-scenario "
                "list end-to-end (handshake took %.1fs; JSON duration=%.1fs is failsafe cap "
                "only, NOT a workload limit)"
                % (len(TIPUOUS_SCENARIO_LIST), handshake_s, duration_s),
            )
            status["workload_started"] = True
            status["steps_executed"] = run_espresso_mirror_workload(
                driver, package, no_deadline, dump_dir,
            )
        except SessionConnectTimeoutError as ex:
            status["failure_reason"] = str(ex)
            status["session_timeout"] = True
            log(
                "SESSION_TIMEOUT: Appium session not created within %.1fs - no UI workload ran (%s)"
                % (connect_timeout_s, ex),
            )
            emit_stderr(
                "INTERACTION_APPIUM [stderr]: SESSION_TIMEOUT after %.1fs - %s"
                % (connect_timeout_s, ex),
            )
        except Exception as ex:
            status["failure_reason"] = "%s: %s" % (type(ex).__name__, ex)
            if is_likely_http_timeout(ex):
                status["session_timeout"] = True
                log(
                    "SESSION_TIMEOUT: Appium session not created within %.1fs - no UI workload ran (%s)"
                    % (connect_timeout_s, ex),
                )
                emit_stderr(
                    "INTERACTION_APPIUM [stderr]: SESSION_TIMEOUT (HTTP) after %.1fs - %s"
                    % (connect_timeout_s, ex),
                )
            else:
                log("SESSION_CONNECT_FAILED: %s: %s" % (type(ex).__name__, ex))
                emit_stderr(
                    "INTERACTION_APPIUM [stderr]: SESSION_CONNECT_FAILED %s: %s"
                    % (type(ex).__name__, ex),
                )
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception as ex:
                    log("driver.quit(): %s %s" % (type(ex).__name__, ex))
            write_appium_status(artifact_dir, status)
            log("workload thread finished.")

    if not (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")):
        emit_stderr(
            "INTERACTION_APPIUM [stderr]: ANDROID_HOME / ANDROID_SDK_ROOT unset in this process - "
            "start Appium from a shell with SDK exports (see appium_android_tests/README.md).",
        )

    # Per-app contract: wait for the locked scenario list to finish, not for the JSON duration.
    # The cap is a generous failsafe against a stuck Appium session: max(duration_s + 600, 720)
    # = at least 12 min for a typical 2-min app, so a single hung scenario still aborts cleanly.
    failsafe_cap_s = max(duration_s + 600.0, 720.0)
    start_workload_thread_and_block_until_done(
        workload, failsafe_cap_s, thread_name="appium-tipuous-workload",
    )
