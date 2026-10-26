"""Keep-Recipe (com.keeprecipes.android): N=10 deep scenarios with real Room INSERT.

Live-explored on Pixel 9 / Android 16. Single-activity Hilt app with NavHost.

Home (HomeFragment):
  - search_bar (collapsed) with open_search_bar_text_view
  - add_recipe_fab (FloatingActionButton, content-desc="Add Recipe")
  - recipe_list_view (RecyclerView of recipe cards)
  - Each recipe card: card_view_without_image with recipe_title +
    recipe_instructions_abbr inside.

Add Recipe form (AddRecipeFragment):
  - toolbar with action_save button (toolbar checkmark icon)
  - title_text_input_edit_text: Recipe Title
  - instructions_text_input_edit_text: Instructions
  - cusine_auto_complete_text_view: Cuisine / Collection (note upstream typo)
  - portion_text_input_edit_text: Portion Size
  - add_ingredient_button / remove_ingredient_button: dynamic ingredient list
  - add_photo_button: pick photo via SAF (we skip photos)

Persistence test: after action_save tap, returns to home with a new
card_view_without_image visible in recipe_list_view (Room INSERT confirmed).
"""
from __future__ import annotations
import os, time
from typing import Any
from .._lib import (SessionConnectTimeoutError, append_coverage_jsonl, await_app_ready,
    build_uiautomator2_options, bump_driver_http_timeout, connect_remote_webdriver,
    emit_stderr, is_likely_http_timeout, log, resolve_artifact_dir, resolve_dump_dir,
    start_workload_thread_and_block_until_done, write_appium_status, write_scenario_report)

PACKAGE = "com.keeprecipes.android"
RESOURCE_NS = "com.keeprecipes.android"
DEFAULT_ACTIVITY = "com.keeprecipes.android.MainActivity"

KEEP_RECIPE_SCENARIO_LIST = (
    # 2026-07-27 v6 amendment: refill loop DROPPED (refill scenarios flake on
    # P3 with "extra_recipe_did_not_persist": app can't handle >1 add).
    # Replaced with 2 more full passes of PASS-EVERYWHERE scenarios.
    #
    # Pass 1 (primary): S1, S2, S3, S4, S7, S10; creates recipe #1
    # Pass 2 (repeat2): S1, S2, S3, S7, S10; types recipe #2 fields then
    #   back (discards, no save → no persistence issue)
    # Pass 3 (repeat3): S1, S2, S3, S7, S10; same
    #
    # Wall estimate:
    #   Pass 1: 7+1+13+1+4+8 = 34s
    #   Pass 2: 7+1+13+4+8   = 33s
    #   Pass 3: 7+1+13+4+8   = 33s
    # TOTAL: 16 scenarios, ~100s workload. Slightly under 110 target but
    # ZERO persistence-flake risk.
    #
    # Removed permanent list entries: S5 (fails P3), S6 (fails P3),
    # S8 (fails P3+P9), S9 (fails P3+P9, low energy). Dispatchers retained.
    "S1_home_visible",
    "S2_tap_add_recipe_fab",
    "S3_fill_first_recipe",
    "S4_save_first_recipe",
    "S7_back_to_home",
    "S10_final_home_check",
    # Pass 2 loop-back (v12: reverted to 1-save; app exits after 2nd save on
    # P3/P6 breaks Pass 2's cold-restart FAB tap: matched-pair unsafe).
    "S1_home_visible_repeat2",
    "S2_tap_add_recipe_fab_repeat2",
    "S3_fill_first_recipe_repeat2",
    "S7_back_to_home_repeat2",
    "S10_final_home_check_repeat2",
    # Pass 3 loop-back:
    "S1_home_visible_repeat3",
    "S2_tap_add_recipe_fab_repeat3",
    "S3_fill_first_recipe_repeat3",
    "S7_back_to_home_repeat3",
    "S10_final_home_check_repeat3",
    # Pass 4 loop-back (v13 2026-08-03), a PARTIAL pass: FAB -> fill form -> back.
    # A full 4th pass costs ~29 s and overshoots; this one drops the two
    # home-only scenarios (S1 warm-up, S10 final check) and keeps the three
    # that do real work. Measured +18 s -> ~115 s.
    #
    # Screen state: pass 3 ends in S10_final_home_check, i.e. on the recipe
    # list, which is exactly the screen S2 taps the FAB from. S1 is not a
    # precondition for S2 (it only does warm-up swipes plus a visibility
    # assert), so skipping it is safe.
    #
    # Like passes 2 and 3 this types into the form and backs out WITHOUT
    # saving: the app flakes above one save (see the v6 note above).
    "S2_tap_add_recipe_fab_repeat4",
    "S3_fill_first_recipe_repeat4",
    "S7_back_to_home_repeat4",
    # ─────────────────────────────────────────────────────────────────────────
    # v13 2026-08-03 RECALIBRATION. batch1 said this app needed +10 s; measured
    # against the current code it needed the opposite, then turned out to need
    # a bug fix rather than either. Do not re-derive its budget from batch1.
    #
    # Two dry runs of IDENTICAL code on the SAME phone spanned 227 s and 66 s.
    # The variable was S7_back_to_home: 2.5 s when the back press landed on
    # home, 28-36 s when `_ensure_on_home` pressed back once too often, exited
    # keep_recipe to the launcher and had to cold-restart it. Fixed in
    # `_ensure_on_home` (v13) by refusing to press back once the app has left
    # the foreground; S7's repeats went 28.1 / 36.0 / 27.7 s -> 5.2 / 5.9 s and
    # the 16-scenario span settled at 97.0 s, 16/16 strict.
    #
    # NOTE FOR THE WRITE-UP: every one of those degraded runs scored strict-PASS,
    # because S7 only asserts "home visible at the end" and the recovery ladder
    # does eventually reach home. Strict-pass rate cannot detect this class of
    # defect: only the wall-time distribution can.
)


def _adb_input_tap(driver, x, y):
    """Fire a coordinate tap via ``adb shell input tap`` directly.

    2026-07-29: mirrors the poetskingdom fix. ``mobile: clickGesture`` on
    Compose/ImageButton elements silently succeeds without firing anything
    on Pixel 3 (Android 12 / API 31), so `adb input tap` at the element
    bounds center is used as the primary click path: reliable across all
    Pixel generations. Uses the device serial from driver capabilities.
    """
    import subprocess
    udid = None
    try:
        caps = driver.capabilities or {}
        udid = caps.get("udid") or caps.get("deviceUDID") or caps.get("desired", {}).get("udid")
    except Exception: udid = None
    cmd = ["adb"]
    if udid: cmd += ["-s", udid]
    cmd += ["shell", "input", "tap", str(int(x)), str(int(y))]
    try:
        subprocess.run(cmd, check=False, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _click_element(driver, el):
    """Click an element via adb tap at its bounds center (primary), falling
    back to ``mobile: clickGesture`` then ``element.click()``. Fixes the
    Pixel 3 API 31 silent-fail on ``mobile: clickGesture``."""
    if el is None: return False
    try:
        r = el.rect
        cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
        if _adb_input_tap(driver, cx, cy):
            return True
    except Exception: pass
    try:
        driver.execute_script("mobile: clickGesture", {"elementId": el.id})
        return True
    except Exception: pass
    try:
        el.click()
        return True
    except Exception:
        return False


def _find_by_id(driver, view_id, timeout=3.0):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().resourceId("%s:id/%s")' % (RESOURCE_NS, view_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els[0]
        time.sleep(0.2)
    return None


def _find_all_by_id(driver, view_id, timeout=2.0):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().resourceId("%s:id/%s")' % (RESOURCE_NS, view_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els
        time.sleep(0.2)
    return []


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


def _find_by_desc(driver, desc, partial=False, timeout=2.0):
    from appium.webdriver.common.appiumby import AppiumBy
    sel = ('new UiSelector().descriptionContains("%s")' % desc if partial
           else 'new UiSelector().description("%s")' % desc)
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els[0]
        time.sleep(0.2)
    return None


def _press_back(driver):
    """Send a back key, unless doing so would crash the app.

    v15 2026-08-04. Home is the navigation START destination. A back press there
    pops the last entry and AndroidX Navigation throws
    ConcurrentModificationException out of `dispatchOnDestinationChanged`
    (full trace and evidence in `_on_subscreen`). Every call site in this file
    follows the shape `if element is not None: click(); _press_back()`, and a
    click that silently fails, a documented hazard on these devices, leaves us
    on home with a back press already queued. Guarding the eight call sites
    individually would leave the next one added unguarded, so the check lives
    here.

    The test confirms the DANGEROUS state positively (the home FAB IS on
    screen). v13 and v14 both tried to infer a SAFE state from an absent
    element, which fails precisely when the UI is slow: the moment the press is
    most likely to land mid-transition. A dialog, popup or search overlay covers
    the FAB, so those call sites still get their back press and still close
    normally.
    """
    try:
        if _find_by_id(driver, "add_recipe_fab", timeout=0.4) is not None:
            return
    except Exception:
        pass
    try: driver.back()
    except Exception: pass
    time.sleep(0.7)


def main_home_visible(driver, timeout=2.0):
    return _find_by_id(driver, "add_recipe_fab", timeout=timeout) is not None


def _force_home(driver):
    try:
        driver.terminate_app(PACKAGE); time.sleep(0.5)
        driver.activate_app(PACKAGE)
        # Active poll for home visibility instead of fixed sleep(1.5)
        deadline = time.time() + 2.5
        while time.time() < deadline:
            if main_home_visible(driver): return True
            time.sleep(0.2)
        return main_home_visible(driver)
    except Exception: return False


def _activate_home(driver):
    """v11 2026-07-27: soft home-recovery; bring app to foreground WITHOUT
    terminating (preserves in-memory state; avoids cold-restart penalty on
    subsequent scenarios). Used when app was backgrounded by save+finish.

    v14 2026-08-04: poll deadline 3.0 s -> 10.0 s.

    Measured on pixel6 keep_recipe/freerasp/rep4 (run 2026.08.04_032023). S7
    pressed back once too often, landed on the launcher, and `activate_app`
    could not warm-resume because the activity record was gone: logcat shows
    `realStartActivityLocked`, i.e. a COLD activity start, which took ~9 s to
    reach an interactive home:

        623.553  NexusLauncherActivity resumed        (back press exited the app)
        639.293  MainActivity realStartActivityLocked (activate_app, cold)
        648      scenario gave up

    The old 3.0 s deadline expired while that cold start was still running, so
    the scenario reported failure for an app that was on its way back. This is
    a poll with an early exit, so raising the ceiling costs nothing whenever the
    app returns quickly: it only stops giving up too early. Same defect seen on
    pixel9/freerasp/rep1 and pixel6/bangcle/rep4: 3 of the 4 keep_recipe cells in
    the sweep, always the one S7 run that took 27-31 s instead of ~5 s."""
    try:
        driver.activate_app(PACKAGE)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if main_home_visible(driver, timeout=0.4): return True
            time.sleep(0.2)
        return main_home_visible(driver)
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


def _type_into(driver, view_id, text):
    el = _find_by_id(driver, view_id, timeout=2.0)
    if el is None: return False
    try:
        el.click(); time.sleep(0.3)
        try: el.clear()
        except Exception: pass
        time.sleep(0.2)
        el.send_keys(text); time.sleep(0.4)
        return True
    except Exception: return False


def _app_foreground(driver):
    """True while PACKAGE still owns the foreground.

    Guard for the back-out ladder below. Unknown -> True, so a driver that
    cannot answer behaves exactly as it did before this guard existed.
    """
    try:
        return (driver.current_package or PACKAGE) == PACKAGE
    except Exception:
        return True


def _left_home(driver, timeout=6.0):
    """Block until the home FAB is gone, i.e. we have really navigated away.

    v16 2026-08-04. Positive confirmation of ARRIVAL on a sub-screen, used
    before touching it. Deliberately keyed on the same element `_press_back`
    guards on (`add_recipe_fab`), so the two can never disagree about which
    screen we are on.

    Not `_on_subscreen`: that walks three ids at 0.4 s each, so it can take
    1.2 s to answer and its `recipe_title` marker also appears inside home's
    list rows, which would make it true on home.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _find_by_id(driver, "add_recipe_fab", timeout=0.3) is None:
            return True
        time.sleep(0.15)
    return False


def _on_subscreen(driver):
    """True when we are inside the app but NOT on the home destination.

    v16 2026-08-04: defined as the absence of the home FAB, deliberately the
    SAME predicate `_press_back` guards on, so the two can never disagree about
    which screen we are on.

    The v15 version listed positive markers
    ("title_text_input_edit_text", "action_save", "recipe_title"): but
    `recipe_title` is a view inside each home LIST ROW (see the module docstring:
    "Each recipe card: card_view_without_image with recipe_title"), so it
    returned True while sitting on home, i.e. it claimed a back press was safe
    at precisely the moment it is not. It also could not see the detail screen,
    which carries neither form marker.

    v15 2026-08-04. The real defect behind keep_recipe's failures is not a slow
    recovery: it is an APP CRASH. Pressing back on the home/start destination
    pops the last nav entry, and AndroidX Navigation then throws out of
    `dispatchOnDestinationChanged` because the listener list is mutated while it
    is being iterated:

        java.util.ConcurrentModificationException
          at NavControllerImpl.dispatchOnDestinationChanged
          at FragmentNavigator$onAttach$2.onBackStackChangeCommitted
          at FragmentManager.handleOnBackPressed
          at OnBackPressedDispatcher.onBackPressed

    Measured 10 times across 5 cells, in EVERY variant INCLUDING baseline (the
    pixel3 baseline cell crashed the most), so it is an upstream app/AndroidX
    bug, not an obfuscation effect. It is invisible to the scorer: that same
    baseline cell reported 19/19 strict while crashing 3 times.

    v13 and v14 both guarded with a NEGATIVE test ("home is not visible, so
    press back"), and `main_home_visible` returns a false negative whenever the
    FAB is slow to appear, which is exactly the moment a back press is most
    dangerous. Hence v14 did not help: the retest still crashed.

    This is the positive form. Press back ONLY when a sub-screen is positively
    identified; if we cannot prove where we are, `_activate_home` recovers
    without touching the nav stack at all.
    """
    if not _app_foreground(driver):
        return False
    return _find_by_id(driver, "add_recipe_fab", timeout=0.6) is None


def _ensure_on_home(driver):
    """If on a detail/add screen, back out until home is visible.
    v11: last resort is _activate_home (warm re-foreground) NOT _force_home
    (terminate+activate). Preserves in-memory state so subsequent FAB tap
    doesn't hit cold-launch inflate delay.

    v13 2026-08-03: stop pressing back once the app has left the foreground.

    Verified on Pixel 3 by hand: with the add-recipe form open, ONE back press
    returns to home and the NEXT back press exits keep_recipe to the launcher.
    `main_home_visible` is a 2.0 s find for add_recipe_fab and returns a false
    negative while the form is still animating out, so this loop could press
    back while already on home, land on the launcher, then keep pressing back
    there before `_activate_home` cold-restarted the app. That turned a ~2.5 s
    S7_back_to_home into 28-36 s, and because the scenario only asserts
    "home visible at the end" it still scored strict-PASS every time: no gate
    in the pipeline could see it. Measured effect: two dry runs of identical
    code on the same phone spanned 66 s and 227 s.

    The guard can only shorten the path (it removes back presses that were
    landing outside the app) and `_activate_home` still guarantees the exit
    condition, so the scenario's assertion is unchanged.

    v14 2026-08-04: settle-and-recheck before spending a back press.

    v13 stopped the SECOND and THIRD extra back presses but not the FIRST: the
    one that actually exits. `_app_foreground` is read BEFORE the press, so on
    the iteration where the app is still foreground and `main_home_visible`
    returns its false negative mid-animation, the press still lands on an
    already-home screen and drops to the launcher. Measured on three of the four
    keep_recipe cells in the sweep; it cost ~15 s on the launcher plus a ~9 s
    cold restart, and made S7 the only failing scenario in the whole 80-cell run.

    The re-check is cheap (1 s) and only runs on the path that was about to
    press back, so a genuine sub-screen pays ~1.6 s to avoid a ~25 s recovery.
    """
    for _ in range(3):
        if main_home_visible(driver): return True
        if not _app_foreground(driver): break
        # v15: positive test. Never press back unless a sub-screen is actually
        # on display: a back press on the start destination CRASHES the app
        # (see _on_subscreen). If we cannot prove it, fall through to
        # _activate_home, which recovers without touching the nav stack.
        if not _on_subscreen(driver): break
        _press_back(driver); time.sleep(0.6)
    return _activate_home(driver)


# --- Scenarios ---------------------------------------------------------------
def _run_s1_home_visible(driver):
    cov = {"scenario_name": "S1_home_visible", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # 2026-07-29: entry self-heal; repeat iterations (S1_repeat2) can land
    # mid-transition after S10's card walk (CSV: pass1 100% → pass2 38%).
    if not main_home_visible(driver):
        _ensure_on_home(driver)
    # 2026-08-01: 4.0/3.0 -> 12.0/8.0. keep_recipe was the worst scenario
    # offender in batch1 (7 cells), ALL on Pixel 3 and all on protected
    # variants (bangcle/allatori/freerasp). The failures cascade from here:
    # S1 6 fails -> S2 9 -> S3 10, and the failing S3 runs are SHORTER than the
    # passing ones (median 8.2s vs 15.0s), i.e. they bail early because home
    # never appeared rather than timing out mid-work. Pixel 3 is the oldest
    # device (API 31) and Bangcle decrypts + injects its DEX at startup, so
    # first launch simply needs more than a 4 s window there.
    #
    # This is energy-neutral for every cell that passes: _find_by_id polls and
    # returns the moment the element exists, so a longer deadline costs nothing
    # when the element is there. It only extends the failure path, which was
    # already a discarded cell. Matched-pair comparability is preserved, unlike
    # a change to WHICH work a scenario does (cf. the poetskingdom S10->S5 swap,
    # which did require a cutoff date for the cells measured before it).
    fab = _find_by_id(driver, "add_recipe_fab", timeout=12.0)
    search = _find_by_id(driver, "search_bar", timeout=8.0)
    # Active warm-up: scroll the recipe list a few times to exercise the recycler view
    try:
        size = driver.get_window_size()
        w, h = size.get("width", 1080), size.get("height", 2400)
        cx = w // 2
        for _ in range(3):
            driver.swipe(cx, int(h * 0.7), cx, int(h * 0.35), 250)
            driver.swipe(cx, int(h * 0.35), cx, int(h * 0.7), 250)
    except Exception: pass
    # Tap search bar then dismiss to exercise the popup transition
    sb = _find_by_id(driver, "open_search_bar_text_view", timeout=1.0) or _find_by_id(driver, "search_bar", timeout=0.5)
    if sb is not None:
        try: sb.click()
        except Exception: pass
        _press_back(driver)
    cov["fab_visible"] = fab is not None
    cov["search_bar_visible"] = search is not None
    cov["scenario_success"] = bool(fab and search)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["home_fab_or_search_missing"]
    return _ok(cov)


def _run_s2_tap_add_recipe_fab(driver):
    cov = {"scenario_name": "S2_tap_add_recipe_fab", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # v8 2026-07-27: cold-launch Pass 1 was failing because AddRecipeFragment
    # inflation took longer than the previous 3s+1.5s find window. Bumped
    # both find timeouts and pre-tap settle so post-install first-FAB-tap
    # has enough headroom.
    # 2026-07-29: added entry self-heal + mobile:clickGesture (stale-ref-immune;
    # UiAutomator2 blocks `mobile: shell` taps).
    if not main_home_visible(driver):
        _ensure_on_home(driver)
    fab = _find_by_id(driver, "add_recipe_fab", timeout=12.0)
    # 2026-07-29: use _click_element (adb tap primary) instead of raw
    # mobile:clickGesture, which silently fails on Pixel 3 API 31 (same class of
    # bug as poetskingdom Confirm tap).
    clicked = _click_element(driver, fab)
    cov["fab_clicked"] = clicked
    # v8 bumps: title_input 3s -> 10s; save_btn 1.5s -> 4s (cold-launch inflate window)
    title_input = _find_by_id(driver, "title_text_input_edit_text", timeout=10.0)
    # 2026-08-01: one bounded retry of the FAB tap.
    #
    # After the S1 timeout fix, keep_recipe/bangcle/pixel3 went 15/16, with the
    # sole failure being S2 on the THIRD repeat: wall 32.5 s versus 17.8 s for
    # the passes, i.e. it sat through both find deadlines and the form never
    # appeared. Two causes are indistinguishable from here: the form was slow
    # to inflate, or the tap never registered (a documented Pixel 3 API 31
    # problem, which is why _click_element does an adb tap first). A retry
    # covers both; a longer timeout only covers one.
    #
    # Costs nothing when the first tap works: this block is skipped entirely.
    if title_input is None:
        cov["fab_retry"] = True
        if not main_home_visible(driver):
            _ensure_on_home(driver)
        fab2 = _find_by_id(driver, "add_recipe_fab", timeout=6.0)
        if fab2 is not None:
            clicked = _click_element(driver, fab2) or clicked
            title_input = _find_by_id(driver, "title_text_input_edit_text", timeout=10.0)
    save_btn = _find_by_id(driver, "action_save", timeout=8.0)
    cov["title_input_visible"] = title_input is not None
    cov["save_button_visible"] = save_btn is not None
    cov["scenario_success"] = bool(clicked and title_input and save_btn)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["add_recipe_form_not_loaded"]
    return _ok(cov)


def _run_s3_fill_first_recipe(driver):
    cov = {"scenario_name": "S3_fill_first_recipe", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # 2026-07-29: re-navigation guard; if S2 silently failed we're not on the
    # form. Three ordinary 2s finds would burn 6s here; try to recover instead.
    if _find_by_id(driver, "title_text_input_edit_text", timeout=1.0) is None:
        if not main_home_visible(driver):
            _ensure_on_home(driver)
        fab = _find_by_id(driver, "add_recipe_fab", timeout=3.0)
        _click_element(driver, fab)
        _find_by_id(driver, "title_text_input_edit_text", timeout=6.0)
    title_ok = _type_into(driver, "title_text_input_edit_text", "PastaCarbonara")
    instr_ok = _type_into(driver, "instructions_text_input_edit_text", "Boil pasta drain mix with egg")
    portion_ok = _type_into(driver, "portion_text_input_edit_text", "2")
    # Hide IME so save button is reachable
    try: driver.hide_keyboard()
    except Exception: pass
    time.sleep(0.5)
    cov["title_typed"] = title_ok
    cov["instructions_typed"] = instr_ok
    cov["portion_typed"] = portion_ok
    cov["scenario_success"] = bool(title_ok and instr_ok)
    cov["scenario_success_action_only"] = bool(title_ok or instr_ok)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["form_fields_not_filled"]
    return _ok(cov)


def _run_s4_save_first_recipe(driver):
    cov = {"scenario_name": "S4_save_first_recipe", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # 2026-08-01: was `save_btn.click()`; a RAW Appium click, the one thing
    # the rest of this file stopped doing. S2's FAB was migrated to
    # _click_element (adb tap at bounds centre, primary) back in July precisely
    # because raw clicks and mobile:clickGesture silently report success while
    # doing nothing. S4 was left behind, and it shows: pixel6 x bangcle came
    # back 12/16 with S4 failing `save_did_not_return_home` at 19.2 s wall,
    # long enough to have clicked, slept 2 s, polled home for 6 s and given up.
    # The recipe was therefore never saved, so all three S10_final_home_check
    # repeats then failed `no_recipe_or_no_fab`. One silent click, four failed
    # scenarios.
    #
    # Timeout 2.0 -> 8.0 for the same reason as S1/S2: on a protected variant
    # the form's toolbar can still be inflating.
    save_btn = _find_by_id(driver, "action_save", timeout=8.0)
    if save_btn is None:
        save_btn = _find_by_desc(driver, "Save", partial=True, timeout=2.0)
    clicked = _click_element(driver, save_btn)
    cov["save_clicked"] = clicked
    # v11 2026-07-27: post-save settle before checking home. On P3/P6 the app
    # sends itself to background after save (save+finish activity behavior);
    # give it a moment to complete rather than immediately polling.
    time.sleep(2.0)
    # Active poll for nav-pop to home (bumped 2.5s -> 6.0s in v7).
    deadline = time.time() + 6.0
    home = False
    while time.time() < deadline:
        if main_home_visible(driver):
            home = True
            break
        time.sleep(0.15)
    # v11: if still not on home, activate (warm re-foreground); the app is
    # likely just backgrounded. Preserves in-memory state so Pass 2's FAB tap
    # doesn't hit cold-launch inflate delay.
    if not home:
        home = _activate_home(driver)
    cov["home_after_save"] = home
    cov["scenario_success"] = bool(clicked and home)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["save_did_not_return_home"]
    return _ok(cov)


def _run_s5_recipe_in_list(driver):
    cov = {"scenario_name": "S5_recipe_in_list", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Recipe card may use card_view_without_image (no photo case) or card_view (with photo)
    cards_noimg = _find_all_by_id(driver, "card_view_without_image", timeout=2.5)
    cards_img = _find_all_by_id(driver, "card_view", timeout=1.0)
    total = len(cards_noimg) + len(cards_img)
    cov["card_count_no_image"] = len(cards_noimg)
    cov["card_count_with_image"] = len(cards_img)
    cov["total_recipe_cards"] = total
    # Active scrolling on the recipe list recycler view (replaces trivial visibility check)
    try:
        size = driver.get_window_size()
        w, h = size.get("width", 1080), size.get("height", 2400)
        cx = w // 2
        for _ in range(4):
            driver.swipe(cx, int(h * 0.75), cx, int(h * 0.3), 220)
        for _ in range(4):
            driver.swipe(cx, int(h * 0.3), cx, int(h * 0.75), 220)
    except Exception: pass
    # Long-press first card to invoke context menu (if any), then dismiss with back
    card = cards_noimg[0] if cards_noimg else (cards_img[0] if cards_img else None)
    if card is not None:
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(driver)
            actions.click_and_hold(card).pause(0.8).release().perform()
        except Exception: pass
        _press_back(driver)
    # Verify recipe_title view exists (will be empty text but view inflated → row in adapter)
    title_view = _find_by_id(driver, "recipe_title", timeout=1.5)
    cov["recipe_title_view_visible"] = title_view is not None
    cov["scenario_success"] = bool(total >= 1 and title_view)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["no_recipe_card_in_list"]
    return _ok(cov)


def _run_s6_tap_first_recipe_detail(driver):
    cov = {"scenario_name": "S6_tap_first_recipe_detail", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    card = _find_by_id(driver, "card_view_without_image", timeout=2.0)
    if card is None:
        card = _find_by_id(driver, "card_view", timeout=1.0)
    clicked = _click_element(driver, card)
    cov["card_clicked"] = clicked
    # Active poll for detail fragment: scroll the detail view while waiting (replaces idle sleep(1.5))
    fab_gone = False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if _find_by_id(driver, "add_recipe_fab", timeout=0.2) is None:
            fab_gone = True
            break
        time.sleep(0.15)
    # Brief scroll on detail screen to exercise rendering of detail content
    try:
        size = driver.get_window_size()
        w, h = size.get("width", 1080), size.get("height", 2400)
        cx = w // 2
        driver.swipe(cx, int(h * 0.7), cx, int(h * 0.4), 250)
        driver.swipe(cx, int(h * 0.4), cx, int(h * 0.7), 250)
    except Exception: pass
    cov["fab_gone_on_detail"] = fab_gone
    cov["scenario_success"] = bool(clicked and fab_gone)
    cov["scenario_success_action_only"] = bool(clicked)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["did_not_open_detail"]
    return _ok(cov)


def _run_s7_back_to_home(driver):
    cov = {"scenario_name": "S7_back_to_home", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # v10: guard against back-press-on-home exiting the app. After S4 saves,
    # nav-pop returns us to home; a further back-press exits the app on P9,
    # forcing a slow terminate+activate recovery. Skip the initial back if
    # we're already on home.
    # v15: guard this back press the same way as the ladder in _ensure_on_home.
    # Unguarded it fires whenever the FAB find times out, which includes the
    # case "already on home, FAB just slow", and that back press crashes the
    # app with ConcurrentModificationException.
    home = main_home_visible(driver)
    if not home and _on_subscreen(driver):
        _press_back(driver)
        home = main_home_visible(driver)
    if not home:
        _ensure_on_home(driver)
        home = main_home_visible(driver)
    # Substantive: re-open a card to exercise nav-stack + detail render, then back
    if home:
        card = _find_by_id(driver, "card_view_without_image", timeout=1.5) or _find_by_id(driver, "card_view", timeout=0.6)
        if card is not None:
            try: card.click()
            except Exception: pass
            # v16 2026-08-04: WAIT FOR ARRIVAL, then let the nav transaction
            # COMMIT, before touching anything.
            #
            # This is the actual cause of the S7 failures, and it is not the
            # extra-back-press theory of v13/v14/v15. Merged Appium + logcat
            # timeline of the crashing run (2026.08.04_111020, pixel6):
            #
            #   771.328  click card_view_without_image
            #   771.389  HomeFragment view destroyed   <- navigating to detail
            #   771.425  POST /actions  swipe 1        <- 0.1 s after the click
            #   771.944  POST /actions  swipe 2
            #   772.474  POST /back                    <- 1.15 s after the click
            #   772.502  CRASH ConcurrentModificationException
            #            in NavControllerImpl.dispatchOnDestinationChanged
            #            via FragmentManager.handleOnBackPressed
            #
            # The old code swiped 0.1 s after the click and backed out 1.15 s
            # after it: both landing while the detail fragment's transition was
            # still committing. Backing out mid-commit re-enters
            # dispatchOnDestinationChanged while its listener list is being
            # mutated, which is the CME. The previous comment here even said
            # "scroll on detail screen while it renders": that was deliberate,
            # and it is exactly what breaks.
            #
            # `_left_home` is the unambiguous arrival signal: the home FAB is
            # gone. It also matches the condition `_press_back` guards on, so
            # the two cannot disagree.
            if _left_home(driver, timeout=6.0):
                try:
                    size = driver.get_window_size()
                    w, h = size.get("width", 1080), size.get("height", 2400)
                    cx = w // 2
                    driver.swipe(cx, int(h * 0.7), cx, int(h * 0.35), 220)
                    driver.swipe(cx, int(h * 0.35), cx, int(h * 0.7), 220)
                except Exception: pass
                # Let the nav transaction settle before sending back. Costs ~1 s
                # per S7 (4 per cell) against a workload with ~20 s of headroom.
                time.sleep(1.0)
                _press_back(driver)
        home = main_home_visible(driver)
        if not home:
            _ensure_on_home(driver)
            home = main_home_visible(driver)
    cov["home_visible"] = home
    cov["scenario_success"] = bool(home)
    return _ok(cov)


def _run_s8_add_second_recipe(driver):
    cov = {"scenario_name": "S8_add_second_recipe", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    fab = _find_by_id(driver, "add_recipe_fab", timeout=2.0)
    if fab is None:
        cov["scenario_success"] = False
        cov["scenario_fail_reasons_strict_ui"] = ["fab_not_found"]
        return _ok(cov)
    try: fab.click()
    except Exception: pass
    # Active poll for AddRecipeFragment: _type_into below already polls via _find_by_id
    title_ok = _type_into(driver, "title_text_input_edit_text", "ChickenSoupWithDumplings")
    instr_ok = _type_into(driver, "instructions_text_input_edit_text", "Simmer chicken with carrots onions celery and add dumplings")
    # Also fill portion for a more realistic write
    _type_into(driver, "portion_text_input_edit_text", "4")
    try: driver.hide_keyboard()
    except Exception: pass
    time.sleep(0.5)
    save_btn = _find_by_id(driver, "action_save", timeout=2.0)
    if save_btn is None:
        save_btn = _find_by_desc(driver, "Save", partial=True, timeout=1.0)
    saved = _click_element(driver, save_btn)
    cov["title_typed"] = title_ok
    cov["instructions_typed"] = instr_ok
    cov["save_clicked"] = saved
    # Active poll for nav-pop to home + scroll over recycler while waiting (replaces idle sleep(2.0))
    deadline = time.time() + 2.5
    while time.time() < deadline:
        if main_home_visible(driver): break
        try:
            size = driver.get_window_size()
            w, h = size.get("width", 1080), size.get("height", 2400)
            cx = w // 2
            driver.swipe(cx, int(h * 0.7), cx, int(h * 0.4), 200)
        except Exception: pass
        time.sleep(0.1)
    # Now should be 2 recipes in the list
    cards = _find_all_by_id(driver, "card_view_without_image", timeout=2.0)
    cov["total_cards_after_2nd"] = len(cards)
    cov["scenario_success"] = bool(saved and len(cards) >= 2)
    cov["scenario_success_action_only"] = bool(saved)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["second_recipe_did_not_persist"]
    return _ok(cov)


def _run_s9_open_search_bar(driver):
    cov = {"scenario_name": "S9_open_search_bar", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    sb = _find_by_id(driver, "open_search_bar_text_view", timeout=2.0)
    if sb is None:
        sb = _find_by_id(driver, "search_bar", timeout=1.0)
    clicked = _click_element(driver, sb)
    cov["search_bar_clicked"] = clicked
    # Active poll: search_view find already polls; drop bare sleep
    sv = _find_by_id(driver, "search_view", timeout=2.0)
    cov["search_view_active"] = sv is not None
    # Substantive: type a real query to exercise filter logic
    typed_match = False
    typed_nomatch = False
    if sv is not None:
        # Try typing into search_view directly; fall back to any focused EditText
        try:
            sv.click(); time.sleep(0.2)
            sv.send_keys("Pas"); typed_match = True
            time.sleep(0.5)
            try: sv.clear()
            except Exception: pass
            time.sleep(0.2)
            sv.send_keys("ZZZ"); typed_nomatch = True
            time.sleep(0.4)
            try: sv.clear()
            except Exception: pass
        except Exception: pass
    cov["search_query_typed_match"] = typed_match
    cov["search_query_typed_nomatch"] = typed_nomatch
    # Close it
    _press_back(driver)
    cov["scenario_success"] = bool(clicked and sv)
    cov["scenario_success_action_only"] = bool(clicked)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["search_did_not_expand"]
    return _ok(cov)


def _run_s10_final_home_check(driver):
    cov = {"scenario_name": "S10_final_home_check", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not main_home_visible(driver):
        _ensure_on_home(driver)
    fab = _find_by_id(driver, "add_recipe_fab", timeout=2.0)
    cards = _find_all_by_id(driver, "card_view_without_image", timeout=2.0)
    cov["fab_visible"] = fab is not None
    cov["final_card_count"] = len(cards)
    # Substantive: scroll list to bottom + back to top, then tap each card -> back to exercise Room reads
    try:
        size = driver.get_window_size()
        w, h = size.get("width", 1080), size.get("height", 2400)
        cx = w // 2
        for _ in range(3):
            driver.swipe(cx, int(h * 0.75), cx, int(h * 0.3), 220)
        for _ in range(3):
            driver.swipe(cx, int(h * 0.3), cx, int(h * 0.75), 220)
    except Exception: pass
    # Tap up to first 2 cards: open detail, brief scroll, back
    cards_now = _find_all_by_id(driver, "card_view_without_image", timeout=1.0)
    opens = 0
    for c in cards_now[:2]:
        try: c.click()
        except Exception: continue
        opens += 1
        try:
            size = driver.get_window_size()
            w, h = size.get("width", 1080), size.get("height", 2400)
            cx = w // 2
            driver.swipe(cx, int(h * 0.7), cx, int(h * 0.4), 220)
        except Exception: pass
        _press_back(driver)
    cov["detail_opens"] = opens
    # v6 dropped refill loop; only S4 saves one recipe. S10 now requires >=1.
    cov["scenario_success"] = bool(fab and len(cards) >= 1)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["no_recipe_or_no_fab"]
    return _ok(cov)


# --- Extra refill scenarios (run after primary list to fill 120s window) -----
_EXTRA_RECIPE_NAMES = [
    ("BeefStewExtra", "Brown beef and slow simmer with potatoes and carrots", "6"),
    ("MisoSoupExtra", "Dissolve miso paste in dashi with tofu and seaweed", "3"),
    ("PadThaiExtra", "Stir fry noodles with tamarind sauce egg and peanuts", "2"),
    ("FocacciaExtra", "Knead flour yeast water olive oil rest and bake", "8"),
    ("CurryExtra", "Saute onion garlic ginger spices then simmer with coconut milk", "4"),
]


def _run_extra_add_recipe(driver, recipe_idx):
    name, instr, portion = _EXTRA_RECIPE_NAMES[recipe_idx % len(_EXTRA_RECIPE_NAMES)]
    sn = "SX%d_add_%s" % (recipe_idx, name)
    cov = {"scenario_name": sn, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Ensure we're on home
    if not main_home_visible(driver):
        _ensure_on_home(driver)
    fab = _find_by_id(driver, "add_recipe_fab", timeout=2.0)
    if fab is None:
        cov["scenario_success"] = False
        cov["scenario_fail_reasons_strict_ui"] = ["fab_not_found"]
        return _ok(cov)
    try: fab.click()
    except Exception: pass
    title_ok = _type_into(driver, "title_text_input_edit_text", name)
    instr_ok = _type_into(driver, "instructions_text_input_edit_text", instr)
    _type_into(driver, "portion_text_input_edit_text", portion)
    try: driver.hide_keyboard()
    except Exception: pass
    time.sleep(0.4)
    save_btn = _find_by_id(driver, "action_save", timeout=2.0)
    if save_btn is None:
        save_btn = _find_by_desc(driver, "Save", partial=True, timeout=1.0)
    saved = _click_element(driver, save_btn)
    cov["title_typed"] = title_ok
    cov["instructions_typed"] = instr_ok
    cov["save_clicked"] = saved
    # Active poll for nav-pop to home
    deadline = time.time() + 2.5
    while time.time() < deadline:
        if main_home_visible(driver): break
        time.sleep(0.15)
    cov["scenario_success"] = bool(saved and main_home_visible(driver))
    cov["scenario_success_action_only"] = bool(saved)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["extra_recipe_did_not_persist"]
    return _ok(cov)


def _run_extra_browse_cards(driver, iteration_idx):
    sn = "SX%d_browse_cards" % iteration_idx
    cov = {"scenario_name": sn, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not main_home_visible(driver):
        _ensure_on_home(driver)
    cards = _find_all_by_id(driver, "card_view_without_image", timeout=1.5)
    opens = 0
    # Scroll + open up to 3 cards, scroll detail, back
    try:
        size = driver.get_window_size()
        w, h = size.get("width", 1080), size.get("height", 2400)
        cx = w // 2
        driver.swipe(cx, int(h * 0.75), cx, int(h * 0.3), 220)
        driver.swipe(cx, int(h * 0.3), cx, int(h * 0.75), 220)
    except Exception: pass
    for c in cards[:3]:
        try: c.click()
        except Exception: continue
        opens += 1
        try:
            size = driver.get_window_size()
            w, h = size.get("width", 1080), size.get("height", 2400)
            cx = w // 2
            driver.swipe(cx, int(h * 0.7), cx, int(h * 0.35), 220)
            driver.swipe(cx, int(h * 0.35), cx, int(h * 0.7), 220)
        except Exception: pass
        _press_back(driver)
        if not main_home_visible(driver):
            _ensure_on_home(driver)
    cov["card_opens"] = opens
    cov["scenario_success"] = bool(opens >= 1)
    cov["scenario_success_action_only"] = bool(opens >= 1)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["no_cards_opened"]
    return _ok(cov)


_SCENARIO_FUNCS = {
    "S1_home_visible": _run_s1_home_visible,
    "S2_tap_add_recipe_fab": _run_s2_tap_add_recipe_fab,
    "S3_fill_first_recipe": _run_s3_fill_first_recipe,
    "S4_save_first_recipe": _run_s4_save_first_recipe,
    "S5_recipe_in_list": _run_s5_recipe_in_list,
    "S6_tap_first_recipe_detail": _run_s6_tap_first_recipe_detail,
    "S7_back_to_home": _run_s7_back_to_home,
    "S8_add_second_recipe": _run_s8_add_second_recipe,
    "S9_open_search_bar": _run_s9_open_search_bar,
    "S10_final_home_check": _run_s10_final_home_check,
    # 2026-07-27 v6 loop-back aliases: safe repeats of PASSING scenarios only.
    # Replaces the removed refill loop. Each alias maps to a passing scenario
    # function; SCENARIO_LIST orders them into 2 additional passes.
    "S1_home_visible_repeat2": _run_s1_home_visible,
    "S2_tap_add_recipe_fab_repeat2": _run_s2_tap_add_recipe_fab,
    "S3_fill_first_recipe_repeat2": _run_s3_fill_first_recipe,
    "S7_back_to_home_repeat2": _run_s7_back_to_home,
    "S10_final_home_check_repeat2": _run_s10_final_home_check,
    "S1_home_visible_repeat3": _run_s1_home_visible,
    "S2_tap_add_recipe_fab_repeat3": _run_s2_tap_add_recipe_fab,
    "S3_fill_first_recipe_repeat3": _run_s3_fill_first_recipe,
    "S7_back_to_home_repeat3": _run_s7_back_to_home,
    "S10_final_home_check_repeat3": _run_s10_final_home_check,
    # v13 2026-08-03 recalibration slots; see KEEP_RECIPE_SCENARIO_LIST.
    "S2_tap_add_recipe_fab_repeat4": _run_s2_tap_add_recipe_fab,
    "S3_fill_first_recipe_repeat4": _run_s3_fill_first_recipe,
    "S7_back_to_home_repeat4": _run_s7_back_to_home,
}


def _run_espresso_mirror_workload(driver, package, deadline, dump_dir, build_label, artifact_dir, workload_deadline=None):
    # 2026-07-29: disable LeakCanary UI activities to prevent them from
    # taking foreground focus mid-scenario (LeakCanary is bundled in all 4
    # debug variants: background heap monitoring stays, only its Activity
    # UI is silenced). pm disable-user is per-install → must re-issue each
    # cell after AR reinstalls the app.
    import subprocess as _sp
    udid = None
    try:
        caps = driver.capabilities or {}
        udid = caps.get("udid") or caps.get("deviceUDID") or caps.get("desired", {}).get("udid")
    except Exception: udid = None
    for comp in (
        "leakcanary.internal.activity.LeakLauncherActivity",
        "leakcanary.internal.activity.LeakActivity",
    ):
        cmd = ["adb"]
        if udid: cmd += ["-s", udid]
        cmd += ["shell", "pm", "disable-user", "--user", "0",
                "%s/%s" % (package, comp)]
        try:
            r = _sp.run(cmd, timeout=5, capture_output=True, text=True)
            if r.returncode == 0:
                log("LeakCanary UI disabled: %s" % comp)
            else:
                log("LeakCanary disable non-zero rc for %s: %s"
                    % (comp, (r.stderr or r.stdout or "").strip()[:120]))
        except Exception as ex:
            log("LeakCanary disable raised on %s: %s" % (comp, ex))

    scenarios_results = []; steps = 0
    # workload_deadline controls the 120s spending window; deadline is the safety failsafe
    effective_deadline = workload_deadline if workload_deadline is not None else deadline
    for scenario_name in KEEP_RECIPE_SCENARIO_LIST:
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
        cov["build_label"] = build_label; cov["workload"] = "espresso_mirror"; cov["suite_round"] = 1
        cov["scenario_wall_seconds"] = round(time.monotonic() - t0, 3)
        scenarios_results.append(cov); append_coverage_jsonl(dump_dir, cov); steps += 1
        log("scenario %s -> success=%s strict=%s wall=%.2fs" % (
            scenario_name, cov.get("scenario_success"),
            cov.get("scenario_success_strict_ui"), cov["scenario_wall_seconds"]))

    # 2026-07-27 v6 amendment: REMOVED the refill loop entirely.
    # Refill scenarios (SXn_add_recipe / SXn_browse) fail on P3 with
    # "fewer_than_2_recipes_or_no_fab" / "extra_recipe_did_not_persist":
    # accumulated Room state breaks list rendering after multiple back-to-back
    # adds. Workload budget now filled by safe idempotent loop-backs in
    # KEEP_RECIPE_SCENARIO_LIST (S1/S2/S3/S7/S10 chained twice more).
    # Functions _run_extra_add_recipe and _run_extra_browse_cards kept for
    # possible future use but no longer invoked in the workload.

    by = {}
    for cov in scenarios_results:
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
    lines = ["Espresso-mirror scenario report (Keep-Recipe)",
             "build_label: %s" % (build_label or "(none)"), "",
             "summary: %s runs, %s passed, %s failed" % (tot, succ, tot-succ), ""]
    ordered_names = [s for s in KEEP_RECIPE_SCENARIO_LIST if s in by]
    extra_names = sorted([s for s in by.keys() if s not in KEEP_RECIPE_SCENARIO_LIST])
    for sn in ordered_names + extra_names:
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
    activity = os.environ.get("KEEP_RECIPE_ACTIVITY", DEFAULT_ACTIVITY)
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
            driver = connect_remote_webdriver(webdriver, server, options, connect_timeout_s, dump_dir)
            status["session_created"] = True
            status["session_start_seconds"] = round(time.monotonic() - thread_started, 3)
            bump_driver_http_timeout(driver, duration_s)
            try:
                driver.execute_script("mobile: startActivity",
                                      {"intent": "%s/%s" % (package, activity),
                                       "intentFlags": "0x10000000"})
                time.sleep(1.5)
            except Exception as ex:
                log("startActivity failed: %s %s" % (type(ex).__name__, ex))
            failsafe_deadline = time.monotonic() + max(duration_s * 4, 480)
            workload_deadline = time.monotonic() + duration_s
            _dismiss_page_size_dialog(driver)
            await_app_ready(driver, package, failsafe_deadline,
                            is_main_ui_visible=main_home_visible, dump_dir=dump_dir)
            _dismiss_page_size_dialog(driver)
            log("session ready; running %d scenarios" % len(KEEP_RECIPE_SCENARIO_LIST))
            status["workload_started"] = True
            status["steps_executed"] = _run_espresso_mirror_workload(
                driver, package, failsafe_deadline, dump_dir, build_label, artifact_dir,
                workload_deadline=workload_deadline)
        except SessionConnectTimeoutError as ex:
            status["failure_reason"] = str(ex); status["session_timeout"] = True
            emit_stderr("INTERACTION_APPIUM: SESSION_TIMEOUT %s" % ex)
        except Exception as ex:
            status["failure_reason"] = "%s: %s" % (type(ex).__name__, ex)
            if is_likely_http_timeout(ex): status["session_timeout"] = True
            emit_stderr("INTERACTION_APPIUM: %s: %s" % (type(ex).__name__, ex))
        finally:
            if driver is not None:
                try: driver.quit()
                except Exception: pass

    failsafe_seconds = max(duration_s * 4, 480)
    start_workload_thread_and_block_until_done(_workload, failsafe_seconds)
    write_appium_status(artifact_dir, status)
    log("workload thread finished")
