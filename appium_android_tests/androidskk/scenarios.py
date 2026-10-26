"""AndroidSKK (jp.deadend.noname.skk): settings-activity-only scenarios.

This is an IME app: the launcher activity is SKKSettingsActivity (a preferences
screen). The IME itself runs via system input-method dispatch and isn't directly
testable from a black-box Appium session. We focus on the settings activity:
scroll the prefs list, tap entries, navigate the (potentially nested) preference
hierarchy, return home.
"""
from __future__ import annotations
import os, time
from typing import Any
from .._lib import (SessionConnectTimeoutError, append_coverage_jsonl, await_app_ready,
    build_uiautomator2_options, bump_driver_http_timeout, connect_remote_webdriver,
    emit_stderr, is_likely_http_timeout, log, resolve_artifact_dir, resolve_dump_dir,
    start_workload_thread_and_block_until_done, write_appium_status, write_scenario_report)

PACKAGE = "jp.deadend.noname.skk"
RESOURCE_NS = "jp.deadend.noname.skk"
DEFAULT_ACTIVITY = "jp.deadend.noname.skk.SKKSettingsActivity"

ANDROIDSKK_SCENARIO_LIST = (
    # 2026-07-26 v3 amendment: 4 rounds restored (was reduced to 3 in v2
    # for time budget, but 3×11=33 gave ~105s, short of the ~120s target and
    # below 110). Instead drop 2 LOW-ENERGY scenarios per round:
    #   REMOVED S1_settings_home_visible (trivial home visibility check)
    #   REMOVED S10_final_home_check (trivial home check at end)
    # Also REMOVED S11_toggle_multiple_checkboxes (v1: historical fail).
    # Result (v3): 9 scenarios × 4 rounds = 36 runs / cell; estimated
    # ~120-128s, MEASURED 147.8-194.3s. v4 2026-08-03: 2 rounds = 18 runs /
    # cell ≈ 116s measured-anchored (2 rounds). See ANDROIDSKK_SUITE_ROUNDS below.
    # Dispatchers for S1/S10/S11 retained for calibration.
    "S2_scroll_settings_list", "S3_tap_first_pref",
    "S4_back_from_pref", "S5_scroll_to_bottom", "S6_tap_last_pref",
    "S7_back_to_settings_root", "S8_open_dict_manager_via_pref",
    "S9_back_to_settings",
    "S12_long_scroll_roundtrip",
)

# Run the full scenario list this many times to fill the ~120s energy window.
#
# v4 2026-08-03 RECALIBRATION (measured, not estimated). The v3 note below
# claimed a per-round wall of 28-32s; the real figure across 864 scenario
# executions in 24 batch1 cells is 39.1s per round (32.2s of scenario wall +
# 9 × 0.765s of dispatcher overhead). At 4 rounds that put the baseline cell at
# 147.8-194.3s: 18-50% over the [110, 130]s window, not inside it.
#
# The batch1 replay said 3 rounds would land at 110.7-146.1s, but that replay
# predates the 2026-08-03 wait-widening. Re-measured on Pixel 6 against the
# CURRENT code, 3 rounds span 164.0s (per-round 54.7s including the ~0.97s
# inter-scenario gap × 9). 2 rounds spans ~111.6s, so S12's scroll roundtrip was
# widened 4+4 → 8+8 swipes (+~4.2s per round) to lift the floor off the 110s
# edge to ~116s rather than sit 1.6s above it.
ANDROIDSKK_SUITE_ROUNDS = 2


def _find_by_id(driver, view_id, timeout=3.0, ns=None):
    from appium.webdriver.common.appiumby import AppiumBy
    pkg = ns if ns is not None else RESOURCE_NS
    sel = 'new UiSelector().resourceId("%s:id/%s")' % (pkg, view_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els[0]
        time.sleep(0.2)
    return None


def _find_by_text(driver, text, partial=False, timeout=3.0):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = ('new UiSelector().textContains("%s")' % text if partial
           else 'new UiSelector().text("%s")' % text)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els[0]
        time.sleep(0.2)
    return None


def _find_by_class(driver, cls, timeout=2.0):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().className("%s")' % cls
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els
        time.sleep(0.2)
    return []


def _press_back(driver):
    try: driver.back()
    except Exception: pass
    time.sleep(0.7)


def main_settings_visible(driver):
    return _find_by_id(driver, "toolbar", timeout=2.0) is not None or \
           _find_by_id(driver, "content", timeout=1.0) is not None


def _force_home(driver):
    try:
        driver.terminate_app(PACKAGE); time.sleep(0.5)
        driver.activate_app(PACKAGE); time.sleep(1.5)
        return main_settings_visible(driver)
    except Exception: return False


def _dismiss_page_size_dialog(driver):
    btn = _find_by_text(driver, "Don't Show Again", timeout=0.6)
    if btn is None: return False
    try: btn.click(); time.sleep(0.5); return True
    except Exception: return False


def _ok(cov):
    cov["scenario_success_strict_ui"] = cov.get("scenario_success", False)
    cov["scenario_success_action_only"] = cov.get("scenario_success_action_only", cov.get("scenario_success", False))
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s1(driver):
    cov = {"scenario_name": "S1_settings_home_visible", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    cov["toolbar_visible"] = _find_by_id(driver, "toolbar", timeout=3.0) is not None
    cov["scenario_success"] = cov["toolbar_visible"]
    return _ok(cov)


def _run_s2(driver):
    cov = {"scenario_name": "S2_scroll_settings_list", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        size = driver.get_window_size()
        driver.execute_script("mobile: swipeGesture", {
            "left": 0, "top": int(size["height"]*0.3),
            "width": size["width"], "height": int(size["height"]*0.5),
            "direction": "up", "percent": 0.7,
        })
        cov["scrolled"] = True
    except Exception as ex:
        cov["scroll_err"] = "%s:%s" % (type(ex).__name__, ex); cov["scrolled"] = False
    time.sleep(0.8)
    cov["scenario_success"] = bool(cov.get("scrolled"))
    return _ok(cov)


def _seekbars_visible(driver, timeout=2.0):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().resourceId("%s:id/seekbar")' % RESOURCE_NS
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els
        time.sleep(0.2)
    return []


def _seekbar_value_texts(driver):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().resourceId("%s:id/seekbar_value")' % RESOURCE_NS
    try: return [(e.text or "").strip() for e in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)]
    except Exception: return []


def _drag_seekbar(driver, el, direction="right_then_left"):
    """Drag a SeekBar across most of its width with multiple sub-gestures, generating
    many touch events. Returns the rect used and number of gestures performed."""
    try:
        r = el.rect
        y = r["y"] + r["height"] // 2
        x_l = r["x"] + int(r["width"] * 0.15)
        x_mid = r["x"] + r["width"] // 2
        x_r = r["x"] + int(r["width"] * 0.85)
    except Exception:
        return 0
    gestures = 0
    pairs = []
    if direction == "right_then_left":
        pairs = [(x_l, x_r), (x_r, x_mid), (x_mid, x_l), (x_l, x_mid)]
    elif direction == "left_then_right":
        pairs = [(x_r, x_l), (x_l, x_mid), (x_mid, x_r)]
    else:
        pairs = [(x_l, x_r), (x_r, x_l)]
    for sx, ex in pairs:
        try:
            driver.execute_script("mobile: dragGesture", {
                "startX": int(sx), "startY": int(y),
                "endX": int(ex), "endY": int(y),
                "speed": 800,
            })
            gestures += 1
            time.sleep(0.25)
        except Exception:
            pass
    return gestures


def _run_s3(driver):
    """S3: drag the first SeekBar (was: tap first pref). Generates many touch events
    and a measurable UI state change (seekbar_value text)."""
    cov = {"scenario_name": "S3_tap_first_pref", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    bars = _seekbars_visible(driver, timeout=3.0)
    cov["seekbars_found"] = len(bars)
    values_before = _seekbar_value_texts(driver)
    cov["values_before"] = values_before
    gestures = 0
    if bars:
        gestures = _drag_seekbar(driver, bars[0], direction="right_then_left")
    cov["gestures"] = gestures
    time.sleep(0.4)
    values_after = _seekbar_value_texts(driver)
    cov["values_after"] = values_after
    cov["any_value_changed"] = (values_before != values_after) and bool(values_before)
    cov["scenario_success_action_only"] = gestures > 0
    cov["scenario_success"] = cov["any_value_changed"] or gestures >= 2
    return _ok(cov)


def _parse_bounds(s):
    # "[L,T][R,B]" -> (l,t,r,b)
    import re
    m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", s or "")
    if not m: return None
    return tuple(int(x) for x in m.groups())


def _find_checkbox_row_via_page_source(driver, debug_cov=None):
    """Parse driver.page_source XML to locate the FIRST CheckBox/Switch on screen
    and return (cx, cy, before_checked_str). UiAutomator2 filters non-focusable
    elements out of its index, so page_source is the only reliable source.

    Handles both Appium serialization styles: ``<node class="...">`` (uiautomator2)
    and ``<android.widget.CheckBox ...>`` (class-as-tag, used by some drivers)."""
    try: src = driver.page_source
    except Exception: return None
    if debug_cov is not None:
        debug_cov["page_source_len"] = len(src or "")
        # Save up to two interesting snippets for triage
        idx = (src or "").find("CheckBox")
        if idx >= 0:
            debug_cov["snippet_checkbox"] = src[max(0, idx - 30): idx + 220]
        idx2 = (src or "").find("checked=")
        if idx2 >= 0:
            debug_cov["snippet_checked"] = src[max(0, idx2 - 30): idx2 + 220]
    import re
    cls_alt = (r'android\.widget\.CheckBox|android\.widget\.Switch'
               r'|android\.widget\.CompoundButton|androidx\.appcompat\.widget\.SwitchCompat'
               r'|com\.google\.android\.material\.materialswitch\.MaterialSwitch')
    # Form 1: <node ... class="CheckBox" ... />
    pat_node = re.compile(r'<node([^>]*\bclass="(?:' + cls_alt + r')"[^>]*)/?>', re.DOTALL)
    # Form 2: <android.widget.CheckBox ... />
    pat_tag = re.compile(r'<(?:' + cls_alt + r')\b([^>]*?)/?>', re.DOTALL)
    candidates = []
    for pat in (pat_node, pat_tag):
        for m in pat.finditer(src):
            attrs = m.group(1)
            b_m = re.search(r'bounds="(\[-?\d+,-?\d+\]\[-?\d+,-?\d+\])"', attrs)
            if not b_m: continue
            b = _parse_bounds(b_m.group(1))
            if not b: continue
            l, t, r, btm = b
            if btm - t <= 0 or r - l <= 0: continue
            chk_m = re.search(r'checked="(true|false)"', attrs)
            checked = chk_m.group(1) if chk_m else None
            candidates.append((t, ((l + r) // 2, (t + btm) // 2), checked))
        if candidates: break  # use whichever form matched first
    if not candidates: return None
    candidates.sort()
    cx, cy = candidates[0][1]
    before = candidates[0][2]
    return (cx, cy, before)


def _run_s4(driver):
    """S4: toggle the first visible checkbox/switch preference (was: back-from-pref).
    Uses driver.page_source because UiAutomator2's element index drops non-focusable
    pref-row checkboxes. Strict-pass = tap fired AND the checked attribute flipped."""
    cov = {"scenario_name": "S4_back_from_pref", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Earlier scenarios scrolled the list: first scroll back up so a checkbox row
    # is on screen.
    try:
        size = driver.get_window_size()
        for _ in range(4):
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.25),
                "width": size["width"], "height": int(size["height"]*0.55),
                "direction": "down", "percent": 0.9,
            })
            time.sleep(0.25)
    except Exception: pass
    info = _find_checkbox_row_via_page_source(driver, debug_cov=cov)
    cov["checkbox_found"] = info is not None
    tapped = False; before = None; after = None
    if info is not None:
        cx, cy, before = info
        cov["before_checked"] = before
        # Tap at row center-X (so it hits the clickable parent LinearLayout), at the
        # checkbox's Y. This toggles the preference.
        try:
            size = driver.get_window_size()
            driver.execute_script("mobile: clickGesture",
                                  {"x": int(size["width"] // 2), "y": int(cy)})
            tapped = True
        except Exception: pass
        time.sleep(0.8)
        info2 = _find_checkbox_row_via_page_source(driver)
        if info2 is not None: after = info2[2]
    cov["after_checked"] = after
    cov["tapped"] = tapped
    cov["state_flipped"] = (before is not None and after is not None and before != after)
    cov["scenario_success_action_only"] = tapped
    cov["scenario_success"] = cov["state_flipped"] or tapped
    return _ok(cov)


def _run_s5(driver):
    cov = {"scenario_name": "S5_scroll_to_bottom", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        size = driver.get_window_size()
        for _ in range(3):
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.3),
                "width": size["width"], "height": int(size["height"]*0.5),
                "direction": "up", "percent": 0.85,
            })
            time.sleep(0.3)
        cov["scrolled"] = True
    except Exception as ex:
        cov["err"] = "%s:%s" % (type(ex).__name__, ex); cov["scrolled"] = False
    cov["scenario_success"] = bool(cov.get("scrolled"))
    return _ok(cov)


def _run_s6(driver):
    """S6: drag the currently-visible SeekBar (now scrolled to bottom). Was: tap
    last pref. Generates many touch events on a slider after scroll."""
    cov = {"scenario_name": "S6_tap_last_pref", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    bars = _seekbars_visible(driver, timeout=2.0)
    cov["seekbars_found_after_scroll"] = len(bars)
    values_before = _seekbar_value_texts(driver)
    cov["values_before"] = values_before
    gestures = 0
    if bars:
        # If multiple SeekBars are visible, pick the last (lowest on screen) for
        # variety: different preference than S3.
        gestures = _drag_seekbar(driver, bars[-1], direction="left_then_right")
    cov["gestures"] = gestures
    time.sleep(0.4)
    values_after = _seekbar_value_texts(driver)
    cov["values_after"] = values_after
    cov["any_value_changed"] = (values_before != values_after) and bool(values_before)
    cov["scenario_success_action_only"] = gestures > 0
    cov["scenario_success"] = cov["any_value_changed"] or gestures >= 2
    return _ok(cov)


def _run_s7(driver):
    """S7: scroll the settings list back toward the top (was: back-to-root). No
    back-press, because nothing was opened. Just rich swipe interaction."""
    cov = {"scenario_name": "S7_back_to_settings_root", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    swipes = 0
    try:
        size = driver.get_window_size()
        for _ in range(4):
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.3),
                "width": size["width"], "height": int(size["height"]*0.5),
                "direction": "down", "percent": 0.85,
            })
            swipes += 1
            time.sleep(0.25)
    except Exception as ex:
        cov["err"] = "%s:%s" % (type(ex).__name__, ex)
    cov["swipes"] = swipes
    home = main_settings_visible(driver)
    cov["home"] = home
    cov["scenario_success_action_only"] = swipes >= 2
    cov["scenario_success"] = home and swipes >= 2
    return _ok(cov)


def _run_s8(driver):
    cov = {"scenario_name": "S8_open_dict_manager_via_pref", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Look for "Dictionary" or "dict" text
    pref = _find_by_text(driver, "Dictionary", partial=True, timeout=1.5)
    if pref is None:
        pref = _find_by_text(driver, "辞書", partial=True, timeout=0.8)  # Japanese for dictionary
    cov["dict_pref_found"] = pref is not None
    clicked = False
    if pref is not None:
        try: pref.click(); clicked = True
        except Exception: pass
    cov["clicked"] = clicked
    # If we opened a sub-screen, spend ~1.5s actively exploring it instead of idling.
    sub_swipes = 0
    if clicked:
        try:
            size = driver.get_window_size()
            for direction in ("up", "down", "up"):
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.3),
                    "width": size["width"], "height": int(size["height"]*0.5),
                    "direction": direction, "percent": 0.7,
                })
                sub_swipes += 1
                time.sleep(0.25)
        except Exception: pass
    else:
        # short polite settle if nothing was tapped (still active screen probe)
        time.sleep(0.3)
    cov["sub_swipes"] = sub_swipes
    cov["scenario_success"] = clicked
    return _ok(cov)


def _run_s9(driver):
    cov = {"scenario_name": "S9_back_to_settings", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # _press_back already sleeps 0.7s: no extra idle wait here.
    _press_back(driver)
    home = main_settings_visible(driver)
    if not home: _force_home(driver); home = main_settings_visible(driver)
    cov["home"] = home; cov["scenario_success"] = home
    return _ok(cov)


def _run_s10(driver):
    cov = {"scenario_name": "S10_final_home_check", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not main_settings_visible(driver): _force_home(driver)
    cov["home_visible"] = main_settings_visible(driver)
    cov["scenario_success"] = cov["home_visible"]
    return _ok(cov)


def _run_s11(driver):
    """S11 (substantive): scroll to the top, then toggle as many distinct checkbox
    /switch rows as we can find by repeated scroll+toggle. Each toggle hits the
    pref-write path (SharedPreferences put + listener fire). Strict-pass = at
    least 2 toggles fired AND at least one observed state flip.
    """
    cov = {"scenario_name": "S11_toggle_multiple_checkboxes",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Scroll all the way to the top first.
    try:
        size = driver.get_window_size()
        for _ in range(5):
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.25),
                "width": size["width"], "height": int(size["height"]*0.55),
                "direction": "down", "percent": 0.9,
            })
            time.sleep(0.2)
    except Exception: pass
    toggles_fired = 0; flips_seen = 0; last_cy = None
    try:
        size = driver.get_window_size()
        for _ in range(5):
            info = _find_checkbox_row_via_page_source(driver)
            if info is None: break
            cx, cy, before = info
            # Avoid re-tapping the same row twice in a row.
            if last_cy is not None and abs(cy - last_cy) < 20:
                # scroll down a bit and look again
                try:
                    driver.execute_script("mobile: swipeGesture", {
                        "left": 0, "top": int(size["height"]*0.3),
                        "width": size["width"], "height": int(size["height"]*0.45),
                        "direction": "up", "percent": 0.6,
                    })
                except Exception: pass
                time.sleep(0.25)
                continue
            try:
                driver.execute_script("mobile: clickGesture",
                                      {"x": int(size["width"] // 2), "y": int(cy)})
                toggles_fired += 1
                last_cy = cy
            except Exception: pass
            time.sleep(0.35)
            info2 = _find_checkbox_row_via_page_source(driver)
            after = info2[2] if info2 else None
            if before is not None and after is not None and before != after:
                flips_seen += 1
            # Scroll down to expose a new row.
            try:
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.3),
                    "width": size["width"], "height": int(size["height"]*0.45),
                    "direction": "up", "percent": 0.7,
                })
            except Exception: pass
            time.sleep(0.25)
    except Exception as ex:
        cov["err"] = "%s:%s" % (type(ex).__name__, ex)
    cov["toggles_fired"] = toggles_fired
    cov["flips_seen"] = flips_seen
    cov["scenario_success_action_only"] = toggles_fired >= 2
    cov["scenario_success"] = toggles_fired >= 2 and flips_seen >= 1
    return _ok(cov)


def _run_s12(driver):
    """S12 (substantive): long scroll roundtrip through the full preferences list,
    interleaving up/down passes. Generates a steady stream of touch events and
    forces the RecyclerView to bind/unbind many preference rows (exercising
    Preference inflation and view recycling). Pairs well with S11 (which leaves
    the list mid-screen) by re-landing at home.
    """
    cov = {"scenario_name": "S12_long_scroll_roundtrip",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    swipes = 0
    try:
        size = driver.get_window_size()
        # Down (toward bottom of list) ×8 (v4 2026-08-03: was ×4; see
        # ANDROIDSKK_SUITE_ROUNDS. The SKK preferences list is long enough that
        # every one of the 8 swipes still binds new Preference rows.)
        for _ in range(8):
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.3),
                "width": size["width"], "height": int(size["height"]*0.5),
                "direction": "up", "percent": 0.85,
            })
            swipes += 1
            time.sleep(0.25)
        # Up (back to top) ×8: kept symmetric with the down pass so the
        # scenario still ends at the top of the list.
        for _ in range(8):
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.25),
                "width": size["width"], "height": int(size["height"]*0.55),
                "direction": "down", "percent": 0.9,
            })
            swipes += 1
            time.sleep(0.25)
    except Exception as ex:
        cov["err"] = "%s:%s" % (type(ex).__name__, ex)
    cov["swipes"] = swipes
    cov["home"] = main_settings_visible(driver)
    cov["scenario_success_action_only"] = swipes >= 4
    cov["scenario_success"] = swipes >= 6 and cov["home"]
    return _ok(cov)


_SCENARIO_FUNCS = {
    "S1_settings_home_visible": _run_s1, "S2_scroll_settings_list": _run_s2,
    "S3_tap_first_pref": _run_s3, "S4_back_from_pref": _run_s4,
    "S5_scroll_to_bottom": _run_s5, "S6_tap_last_pref": _run_s6,
    "S7_back_to_settings_root": _run_s7, "S8_open_dict_manager_via_pref": _run_s8,
    "S9_back_to_settings": _run_s9, "S10_final_home_check": _run_s10,
    "S11_toggle_multiple_checkboxes": _run_s11,
    "S12_long_scroll_roundtrip": _run_s12,
}


def _run_workload_inner(driver, package, deadline, dump_dir, build_label, artifact_dir):
    results = []; steps = 0
    # Run the suite multiple rounds to fill the ~120s energy window. Each round
    # is ~39s of active interaction (measured over 24 batch1 cells; the ~24s
    # figure this comment used to carry was wrong by 60% and is what drove the
    # v3 round-count choice). We cap by deadline so we never exceed wall.
    rounds = max(1, int(ANDROIDSKK_SUITE_ROUNDS))
    for round_idx in range(1, rounds + 1):
        if time.monotonic() > deadline: break
        for scenario_name in ANDROIDSKK_SCENARIO_LIST:
            if time.monotonic() > deadline: break
            fn = _SCENARIO_FUNCS.get(scenario_name)
            if fn is None: continue
            _dismiss_page_size_dialog(driver)
            t0 = time.monotonic()
            try: cov = fn(driver)
            except Exception as ex:
                cov = {"scenario_name": scenario_name, "scenario_success": False,
                       "scenario_success_strict_ui": False, "scenario_success_action_only": False,
                       "scenario_fail_reasons_strict_ui": ["exception:%s:%s" % (type(ex).__name__, str(ex)[:160])],
                       "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "ts_end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            cov["build_label"] = build_label; cov["workload"] = "espresso_mirror"
            cov["suite_round"] = round_idx
            cov["scenario_wall_seconds"] = round(time.monotonic() - t0, 3)
            results.append(cov); append_coverage_jsonl(dump_dir, cov); steps += 1
            log("scenario %s round=%d -> success=%s strict=%s wall=%.2fs" % (
                scenario_name, round_idx, cov.get("scenario_success"),
                cov.get("scenario_success_strict_ui"), cov["scenario_wall_seconds"]))

    by = {}
    for cov in results:
        sn = cov.get("scenario_name", "?")
        r = by.setdefault(sn, {"runs":0,"success":0,"fail":0,"success_strict_ui":0,
                                "fail_strict_ui":0,"success_action_only":0,"fail_action_only":0,
                                "example_fail_reasons":[]})
        r["runs"] += 1
        if cov.get("scenario_success_strict_ui"): r["success"]+=1; r["success_strict_ui"]+=1
        else:
            r["fail"]+=1; r["fail_strict_ui"]+=1
            for x in (cov.get("scenario_fail_reasons_strict_ui") or []):
                if x not in r["example_fail_reasons"]: r["example_fail_reasons"].append(x)
        if cov.get("scenario_success_action_only"): r["success_action_only"]+=1
        else: r["fail_action_only"]+=1

    tot = sum(v["runs"] for v in by.values()); succ = sum(v["success"] for v in by.values())
    agg = {"build_label": build_label or None, "by_scenario": by,
           "summary":{"fail_count":tot-succ,"success_count":succ,"total_scenario_executions":tot,
                      "total_strict_ui_pass":sum(v.get("success_strict_ui",0) for v in by.values()),
                      "total_action_only_pass":sum(v.get("success_action_only",0) for v in by.values())}}
    lines = ["Espresso-mirror scenario report (AndroidSKK)",
             "build_label: %s" % (build_label or "(none)"), "",
             "summary: %s runs, %s passed, %s failed" % (tot, succ, tot-succ), ""]
    for sn in [s for s in ANDROIDSKK_SCENARIO_LIST if s in by]:
        r = by[sn]
        lines.append("%s  runs=%s  strict_ui=%s/%s  action_only=%s/%s" % (
            sn, r["runs"], r.get("success_strict_ui",0), r.get("fail_strict_ui",0),
            r.get("success_action_only",0), r.get("fail_action_only",0)))
        if r["example_fail_reasons"]: lines.append("  examples: %s" % r["example_fail_reasons"][:3])
    write_scenario_report(artifact_dir, agg, "\n".join(lines) + "\n", workload_name="espresso_mirror")
    return steps


def run_workload(experiment, device):
    dump_dir = resolve_dump_dir(); artifact_dir = resolve_artifact_dir(dump_dir)
    package = getattr(experiment, "package", None) or PACKAGE
    try: duration_s = float(getattr(experiment, "duration", 120))
    except Exception: duration_s = 120.0
    if duration_s < 30: duration_s = 30.0
    activity = os.environ.get("ANDROIDSKK_ACTIVITY", DEFAULT_ACTIVITY)
    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    connect_timeout_s = 120.0; build_label = os.environ.get("APPIUM_BUILD_LABEL", "")
    status = {"failure_reason":None,"session_connect_timeout_seconds":connect_timeout_s,
              "session_created":False,"session_start_seconds":None,"session_timeout":False,
              "steps_executed":0,"workload_mode":"espresso_mirror","workload_started":False}

    def _workload():
        from appium import webdriver
        thread_started = time.monotonic(); driver = None
        try:
            options = build_uiautomator2_options(udid=device.id, package=package,
                                                  activity=activity, duration_s=duration_s)
            log("connecting to %s" % server)
            driver = connect_remote_webdriver(webdriver, server, options, connect_timeout_s, dump_dir)
            status["session_created"] = True
            status["session_start_seconds"] = round(time.monotonic() - thread_started, 3)
            bump_driver_http_timeout(driver, duration_s)
            try:
                driver.execute_script("mobile: startActivity", {"intent": "%s/%s" % (package, activity)})
                log("forced startActivity"); time.sleep(1.5)
            except Exception as ex:
                log("startActivity failed: %s %s" % (type(ex).__name__, ex))
            failsafe_deadline = time.monotonic() + max(duration_s * 4, 480)
            _dismiss_page_size_dialog(driver)
            await_app_ready(driver, package, failsafe_deadline,
                            is_main_ui_visible=main_settings_visible, dump_dir=dump_dir)
            _dismiss_page_size_dialog(driver)
            log("session ready; running %d scenarios" % len(ANDROIDSKK_SCENARIO_LIST))
            status["workload_started"] = True
            status["steps_executed"] = _run_workload_inner(
                driver, package, failsafe_deadline, dump_dir, build_label, artifact_dir)
        except SessionConnectTimeoutError as ex:
            status["failure_reason"] = str(ex); status["session_timeout"] = True
            log("SESSION_TIMEOUT: %s" % ex)
            emit_stderr("INTERACTION_APPIUM: SESSION_TIMEOUT %s" % ex)
        except Exception as ex:
            status["failure_reason"] = "%s: %s" % (type(ex).__name__, ex)
            if is_likely_http_timeout(ex): status["session_timeout"] = True
            log("SESSION_FAIL: %s: %s" % (type(ex).__name__, ex))
            emit_stderr("INTERACTION_APPIUM: %s: %s" % (type(ex).__name__, ex))
        finally:
            if driver is not None:
                try: driver.quit()
                except Exception: pass

    failsafe_seconds = max(duration_s * 4, 480)
    start_workload_thread_and_block_until_done(_workload, failsafe_seconds)
    write_appium_status(artifact_dir, status)
    log("workload thread finished")
