"""
PoetsKingdom (``com.wendorochena.poetskingdom``): N=10 scenarios.

Home (FirstFragment) has 4 clickable images: createPoemImage, myImages,
myPoemImage, personalisationImage. createPoemImage launches PoemThemeActivity,
myImages navigates to the MyImages fragment, myPoemImage launches MyPoems
activity, personalisationImage launches PersonalisationActivity. First-use
shows an AlertDialog with title "Welcome": must dismiss before scenarios
proceed past S1.
"""
from __future__ import annotations
import os, time
from typing import Any

from .._lib import (
    SessionConnectTimeoutError, append_coverage_jsonl, await_app_ready,
    build_uiautomator2_options, bump_driver_http_timeout, connect_remote_webdriver,
    emit_stderr, is_likely_http_timeout, log, resolve_artifact_dir, resolve_dump_dir,
    start_workload_thread_and_block_until_done, write_appium_status, write_scenario_report,
)

PACKAGE = "com.wendorochena.poetskingdom"
RESOURCE_NS = "com.wendorochena.poetskingdom"
DEFAULT_ACTIVITY = "com.wendorochena.poetskingdom.MainActivity"

POETSKINGDOM_SCENARIO_LIST: tuple[str, ...] = (
    # 2026-07-29 amendment (energy-rich refactor):
    # Swapped S10_final_home_check (12s, mostly idle swipes) for
    # S5_create_second_poem (33s, real poem-creation work with SQLite write +
    # thumbnail render). Ordered S5 between S4 (verify TestPoem1) and S7 (edit
    # TestPoem1) so the chain flows naturally: create1 → verify → create2 →
    # edit1 → personalisation. Budget: 118s (fits under 120s AR cap).
    #
    # BREAKS matched-pair comparability with cells collected under the OLD
    # scenario list (2026-07-26). Cells under this list must be re-collected
    # under a NEW sweep tag.
    #
    # 2026-07-26 amendment history (kept for context):
    # DROPPED: S1_home_visible, S6_verify_both_poems, S9_swipe.
    # (S5 was also dropped 2026-07-26 for budget; re-added 2026-07-29 in
    #  place of S10 for higher energy density.)
    #
    # Also 2026-07-29: fixed S3/S5 confirm_clicked=False cascade by (a) using
    # mobile:clickGesture in _tap_text (immune to keyboard overlay) and
    # (b) rewriting _dismiss_ime to use mobile:hideKeyboard + KEYCODE_ESCAPE
    # instead of driver.back() which was dismissing the whole dialog.
    #
    # Dispatchers for S1/S6/S9/S10 remain in _SCENARIO_FUNCS so calibration
    # mode (ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1) still runs them.
    "S2_dismiss_first_use_dialog",
    "S3_create_first_poem",
    "S4_verify_first_poem_in_my_poems",
    "S5_create_second_poem",
    "S7_tap_first_poem_to_edit",
    "S8_tap_personalisation_roundtrip",
)


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
    try: driver.back()
    except Exception: pass
    time.sleep(0.5)


def _poll_for_text(driver, text, partial=False, max_wait=1.5, settle=0.2):
    """Active poll for a text-based UI element, returning True as soon as it
    appears (or False at deadline). Replaces idle ``time.sleep(N)`` waits with
    short find_elements probes spaced by ``settle`` seconds. The probes are
    themselves cheap UiAutomator queries that fire pointer-less work."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        el = _find_by_text(driver, text, partial=partial, timeout=settle)
        if el is not None: return True
    return False


def _poll_for_id(driver, view_id, max_wait=1.5, settle=0.2):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        el = _find_by_id(driver, view_id, timeout=settle)
        if el is not None: return True
    return False


def _poll_for_home(driver, max_wait=1.5):
    """Active poll for home tiles: bails as soon as ``main_home_visible``
    returns True. Replaces ``time.sleep(1.5)`` after activate_app."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if main_home_visible(driver): return True
    return False


_HOME_TILE_TEXTS = ("Create Poem", "My Poems", "My Images", "Personalisation")


def main_home_visible(driver):
    """PoetsKingdom uses Jetpack Compose: home tiles don't have resource-ids.
    Identify the home screen by presence of two distinctive tile labels."""
    create = _find_by_text(driver, "Create Poem", partial=False, timeout=1.5)
    if create is None: return False
    mypoems = _find_by_text(driver, "My Poems", partial=False, timeout=1.0)
    return mypoems is not None


def _adb_input_tap(driver, x, y):
    """Fire a coordinate tap via ``adb shell input tap`` directly. This bypasses
    Appium's synthetic gesture path, which Compose's pointer input ignores on
    Pixel 9 / Android 16. Uses the device serial from driver capabilities."""
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


def _tap_tile(driver, tile_text, timeout=2.5):
    """Tap a home-screen tile by its label. Compose's pointer input is unreliable
    via Appium's synthetic gestures: we use ``adb shell input tap`` directly at
    the clickable parent's center, which dispatches a real touch event through
    InputManager and reliably triggers Compose's click handler."""
    from appium.webdriver.common.appiumby import AppiumBy
    tv = _find_by_text(driver, tile_text, partial=False, timeout=timeout)
    if tv is None: return False
    try:
        r = tv.rect
        cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
    except Exception:
        return False
    # Find the smallest clickable=true ancestor View whose bounds contain (cx, cy)
    # and use its center for the tap so the touch lands solidly inside the
    # clickable region.
    parent_el = None
    try:
        clickable_els = driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().clickable(true).className("android.view.View")')
        best_area = None
        for el in clickable_els:
            try:
                er = el.rect
                if (er["x"] <= cx <= er["x"] + er["width"]
                        and er["y"] <= cy <= er["y"] + er["height"]):
                    area = er["width"] * er["height"]
                    if best_area is None or area < best_area:
                        best_area = area; parent_el = el
            except Exception: continue
    except Exception: pass
    if parent_el is not None:
        try:
            er = parent_el.rect
            cx, cy = er["x"] + er["width"] // 2, er["y"] + er["height"] // 2
        except Exception: pass
    # adb shell input tap: works reliably on Compose, unlike mobile:clickGesture.
    if _adb_input_tap(driver, cx, cy):
        return True
    # Appium fallbacks if adb is somehow unavailable
    strategies = [
        lambda: driver.execute_script("mobile: clickGesture", {"x": int(cx), "y": int(cy)}),
        lambda: driver.execute_script("mobile: longClickGesture",
                                       {"x": int(cx), "y": int(cy), "duration": 50}),
        lambda: _w3c_tap(driver, cx, cy),
        lambda: tv.click(),
    ]
    for fn in strategies:
        try: fn(); return True
        except Exception: continue
    return False


def _w3c_tap(driver, cx, cy):
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    from selenium.webdriver.common.actions import interaction
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    ab = ActionBuilder(driver, mouse=finger)
    ab.pointer_action.move_to_location(int(cx), int(cy))
    ab.pointer_action.pointer_down()
    ab.pointer_action.pause(0.05)
    ab.pointer_action.pointer_up()
    ab.perform()


def _force_home(driver):
    try:
        driver.terminate_app(PACKAGE); time.sleep(0.4)
        driver.activate_app(PACKAGE)
        # Active poll for home: bail as soon as tiles appear.
        _poll_for_home(driver, max_wait=1.5)
        return main_home_visible(driver)
    except Exception:
        return False


def _tap_understood_in_dialog(driver, timeout=2.5):
    """Dismiss a Guide dialog by tapping its UNDERSTOOD/Understood/OK positive
    button. Try ``android:id/button1`` first (AlertDialog), then text-based
    finders for Compose dialogs which use a TextView with text="Understood"."""
    from appium.webdriver.common.appiumby import AppiumBy
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label, sel in (
            ("button1", 'new UiSelector().resourceId("android:id/button1")'),
            ("UNDERSTOOD_text", 'new UiSelector().text("UNDERSTOOD")'),
            ("Understood_text", 'new UiSelector().text("Understood")'),
        ):
            try:
                els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                for e in els:
                    try:
                        r = e.rect
                        cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
                        if _adb_input_tap(driver, cx, cy):
                            time.sleep(0.4); return True
                    except Exception: continue
            except Exception: continue
        time.sleep(0.2)
    return False


def _tap_text(driver, text, partial=False, timeout=2.5):
    """Find a TextView/Button by text and tap it.

    2026-07-29 (v3): re-ordered so ``_adb_input_tap`` is PRIMARY.
    Empirically confirmed 2026-07-29 that ``mobile: clickGesture`` on a
    non-clickable TextView (Compose ``Text`` inside a ``Button`` wrapper,
    exactly the poetskingdom "Confirm" case) silently succeeds without
    firing anything on Pixel 3 (Android 12 / API 31); the fallback
    therefore never runs and S3 cascades. ``adb input tap`` at the element
    bounds center is reliable on all Pixel generations (verified manually
    on P3: coord center hits the clickable parent's onClick).
    ``mobile: clickGesture`` kept as fallback for cases where a visual
    overlay would block the touch.
    """
    el = _find_by_text(driver, text, partial=partial, timeout=timeout)
    if el is None: return False
    try:
        r = el.rect
        cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
        if _adb_input_tap(driver, cx, cy):
            return True
    except Exception:
        pass
    try:
        driver.execute_script("mobile: clickGesture", {"elementId": el.id})
        return True
    except Exception:
        return False


def _type_in_edittext(driver, text, edittext_index=0, timeout=2.5):
    """Find the first visible EditText and type ``text`` into it. Returns True
    if at least one EditText was found and the text dispatch did not raise."""
    from appium.webdriver.common.appiumby import AppiumBy
    deadline = time.time() + timeout
    target = None
    while time.time() < deadline:
        try:
            els = driver.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().className("android.widget.EditText")')
            if len(els) > edittext_index:
                target = els[edittext_index]; break
        except Exception: pass
        time.sleep(0.2)
    if target is None: return False
    try:
        target.click(); time.sleep(0.4)
        target.send_keys(text); time.sleep(0.3)
        return True
    except Exception:
        return False


def _dismiss_ime(driver):
    """Formerly closed the soft keyboard so subsequent taps hit the dialog.

    2026-07-29 (v4): now a pure settle sleep. The upstream fix is that
    ``before_experiment_apply_device_state.py`` sets AppiumIME as the
    default IME on every phone: AppiumIME accepts programmatic text input
    without rendering a soft keyboard, so there is nothing to dismiss and
    the "Confirm" button stays at its layout position. Sending explicit
    key events here (KEYCODE_ESCAPE, KEYCODE_BACK, hide_keyboard) risks
    being interpreted as a dialog dismiss on some API levels (P3 Android
    12 in particular), which then cascaded S3→S4→S7 failures. A short
    settle sleep is enough for AppiumIME's async text-injection to
    complete before the next tap.
    """
    time.sleep(0.4)


def _ensure_home_screen(driver, max_attempts=5):
    """Make best effort to land on MainActivityCompose home with all 4 tiles
    visible and no blocking dialogs.

    On fresh installs the app shows a chain: Android-App-Compatibility system
    warning → Welcome dialog (MainActivity) → Personalisation forced-flow with
    its own Guide dialog. Dismissing Welcome auto-launches PersonalisationActivity,
    so we need to back-press out of it. This helper iterates dismiss → back-press
    → check until home tiles are stable AND no dialogs remain."""
    for _ in range(max_attempts):
        # Pop any blocking dialog (App Compat, Welcome guide, Personalisation guide)
        _dismiss_first_use_if_any(driver)
        # Confirm all 4 home tiles are visible (Create Poem + My Poems + Personalisation)
        on_home = (
            _find_by_text(driver, "Create Poem", partial=False, timeout=0.6) is not None
            and _find_by_text(driver, "My Poems", partial=False, timeout=0.3) is not None
            and _find_by_text(driver, "Personalisation", partial=False, timeout=0.3) is not None
        )
        # Verify no Understood/UNDERSTOOD dialog still up
        no_dialog = (
            _find_by_text(driver, "UNDERSTOOD", partial=False, timeout=0.2) is None
            and _find_by_text(driver, "Understood", partial=False, timeout=0.2) is None
            and _find_by_text(driver, "OK", partial=False, timeout=0.2) is None
            and _find_by_text(driver, "Don't Show Again", partial=False, timeout=0.2) is None
        )
        if on_home and no_dialog:
            return True
        if not on_home:
            # We may be on Personalisation (post-Welcome auto-flow); back to MainActivity
            _press_back(driver)
    # Last resort: terminate + activate to fully reset
    _force_home(driver)
    _dismiss_first_use_if_any(driver)
    # One more back-press in case we landed on Personalisation post-force-home
    if _find_by_text(driver, "Create Poem", partial=False, timeout=0.6) is None:
        _press_back(driver)
        _dismiss_first_use_if_any(driver)
    return main_home_visible(driver)


def _create_poem(driver, title, body, category_text="NONE", skip_restart=False):
    """End-to-end Create Poem flow. Returns a dict with progress flags:
      - poemTheme_visible, title_dialog_visible, title_typed, confirm_clicked,
        createPoem_visible, body_typed, save_dialog_visible, save_as_file_clicked,
        category_picked
    The poem is saved to Repo only on category pick (final step).

    If ``skip_restart`` is True, skip the terminate+activate at the top: useful
    for the second poem in a row (S5) since S4 already left us on a stable home."""
    progress: dict[str, Any] = {}
    # 0. Force-restart the app for a clean state. Without this, the very first
    # _create_poem invocation hits a race where the Welcome→Personalisation
    # forced-flow dialogs interleave with the tile tap and absorb it.
    if not skip_restart:
        try:
            driver.terminate_app(PACKAGE); time.sleep(0.4)
            driver.activate_app(PACKAGE)
            _poll_for_home(driver, max_wait=1.5)
        except Exception:
            pass
    progress["home_pre_create"] = _ensure_home_screen(driver, max_attempts=5)
    # 1. Tap Create Poem tile: retry up to 2× for Compose frame-timing.
    poem_theme_reached = False
    for attempt in range(2):
        progress["create_poem_tile_clicked"] = _tap_tile(driver, "Create Poem", timeout=2.0)
        # 2026-07-29: bumped 0.8 → 2.5; PoemTheme renders slower on P6/P9 with
        # obfuscation, sub-second poll expired before render → S3 cascade to S4/S7.
        _poll_for_text(driver, "Outline", partial=False, max_wait=2.5, settle=0.2)
        _tap_understood_in_dialog(driver, timeout=1.0)
        if _find_by_text(driver, "Outline", partial=False, timeout=0.6) is not None:
            poem_theme_reached = True; break
        _ensure_home_screen(driver, max_attempts=2)
    progress["create_poem_tap_attempts"] = attempt + 1
    progress["poemTheme_visible"] = poem_theme_reached
    # 3. Tap the "Create Poem" top-right button (advances to title dialog).
    # There are TWO "Create Poem" texts in PoemThemeActivity: the toolbar title
    # AND a clickable button next to it. Find the clickable one by picking the
    # element with the smallest area (the toolbar one is larger).
    from appium.webdriver.common.appiumby import AppiumBy
    # 2026-07-29: guard on poem_theme_reached so we don't tap the toolbar-title
    # "Create Poem" text when PoemTheme never rendered (S3 fell through to home).
    # (Earlier version also added a `clickable(true)` filter: reverted because
    # bangcle-obfuscated builds do NOT propagate the clickable attribute on the
    # Create Poem button; the smallest-area heuristic still picks the button
    # correctly in the non-obfuscated case and works on obfuscated too.)
    if poem_theme_reached:
        cp_els = driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().text("Create Poem")')
        best = None; best_area = None
        for el in cp_els:
            try:
                r = el.rect
                a = r["width"] * r["height"]
                if best_area is None or a < best_area:
                    best_area = a; best = el
            except Exception: continue
        if best is not None:
            try:
                r = best.rect
                cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
                _adb_input_tap(driver, cx, cy)
            except Exception: pass
    # Active poll for the title dialog text: bails as soon as it shows.
    # 2026-07-26: bumped max_wait 1.0 → 3.0 to tolerate bangcle+P9 slowness (S3 was
    # failing at confirm_clicked because Confirm button rendered slower than 1.0s).
    _poll_for_text(driver, "Give Your Poem", partial=True, max_wait=3.0, settle=0.2)
    # 4. Title dialog: type title, Confirm
    progress["title_dialog_visible"] = _find_by_text(
        driver, "Give Your Poem", partial=True, timeout=0.4) is not None
    progress["title_typed"] = _type_in_edittext(driver, title, timeout=1.5)
    _dismiss_ime(driver)
    progress["confirm_clicked"] = _tap_text(driver, "Confirm", partial=False, timeout=3.0)
    # Active poll for the editor Guide dialog (UNDERSTOOD): bails on first hit.
    _poll_for_text(driver, "UNDERSTOOD", partial=False, max_wait=1.5, settle=0.2)
    # 5. Dismiss CreatePoem editor Guide dialog
    _tap_understood_in_dialog(driver, timeout=1.0)
    progress["createPoem_visible"] = (
        _find_by_id(driver, "bottomDrawer", timeout=0.8) is not None
        or _find_by_id(driver, "saveOptions", timeout=0.4) is not None
    )
    # 6. Type poem body
    progress["body_typed"] = _type_in_edittext(driver, body, timeout=1.5)
    _dismiss_ime(driver)
    # 7. Tap saveOptions ImageButton
    save_btn = _find_by_id(driver, "saveOptions", timeout=1.5)
    if save_btn is not None:
        try:
            r = save_btn.rect
            cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
            progress["save_button_clicked"] = _adb_input_tap(driver, cx, cy)
        except Exception:
            progress["save_button_clicked"] = False
    else:
        progress["save_button_clicked"] = False
    # Active poll for the save options dialog text: bails on first hit.
    _poll_for_text(driver, "Save as a File", partial=False, max_wait=3.0, settle=0.2)
    # 8. Save dialog: tap "Save as a File"
    progress["save_dialog_visible"] = _find_by_text(
        driver, "Save as a File", partial=False, timeout=0.4) is not None
    progress["save_as_file_clicked"] = _tap_text(
        driver, "Save as a File", partial=False, timeout=1.5)
    # Active poll for the Category picker dialog.
    _poll_for_text(driver, "Category", partial=False, max_wait=3.0, settle=0.2)
    # 9. Category picker: tap NONE (or chosen category)
    progress["category_picker_visible"] = _find_by_text(
        driver, "Category", partial=False, timeout=0.4) is not None
    progress["category_picked"] = _tap_text(
        driver, category_text, partial=False, timeout=1.5)
    # Active poll for return to home tile.
    _poll_for_text(driver, "Create Poem", partial=False, max_wait=3.0, settle=0.2)
    # 10. Return to home: back-press until home tiles visible OR the back-stack
    # has been fully unwound. Stops as soon as home is reached so we don't
    # overshoot and land on the launcher.
    home_reached = False
    backs_pressed = 0
    for _ in range(4):
        if main_home_visible(driver):
            home_reached = True; break
        _press_back(driver)
        backs_pressed += 1
    if not home_reached:
        _force_home(driver)
        if _find_by_text(driver, "Create Poem", partial=False, timeout=0.6) is None:
            _press_back(driver)
        home_reached = main_home_visible(driver)
    progress["back_to_home_presses"] = backs_pressed
    progress["back_to_home"] = home_reached
    return progress


def _open_my_poems(driver):
    """Tap My Poems tile and wait for MyPoemsCompose to appear. Active polls
    replace idle waits: bail as soon as the MyPoems screen shows up."""
    _tap_tile(driver, "My Poems", timeout=2.5)
    # Poll for any MyPoems marker: Albums header, All Poems chip, or any poem.
    deadline = time.time() + 1.2
    while time.time() < deadline:
        if (_find_by_text(driver, "Albums", partial=False, timeout=0.2) is not None
                or _find_by_text(driver, "All Poems", partial=False, timeout=0.2) is not None
                or _find_by_text(driver, "TestPoem1", partial=False, timeout=0.2) is not None):
            break
    _tap_understood_in_dialog(driver, timeout=1.0)


def _poem_present_in_my_poems(driver, title, timeout=3.0):
    """Return True if a TextView with text == title is visible in MyPoemsCompose."""
    el = _find_by_text(driver, title, partial=False, timeout=timeout)
    return el is not None


def _dismiss_page_size_dialog(driver):
    btn = _find_by_text(driver, "Don't Show Again", timeout=0.6)
    if btn is None: return False
    try:
        btn.click(); time.sleep(0.5)
        return True
    except Exception: return False


def _dismiss_app_compat_warning(driver):
    """Bangcle/debuggable APKs trigger Android's 'App Compatibility' system
    dialog (libs not 16KB-aligned) on every fresh launch. Tap 'Don't Show Again'
    if present so the dialog doesn't block subsequent scenarios. Returns True
    if a dismiss was dispatched."""
    btn = _find_by_text(driver, "Don't Show Again", partial=False, timeout=0.6)
    if btn is None:
        btn = _find_by_text(driver, "OK", partial=False, timeout=0.3)
    if btn is None: return False
    try:
        r = btn.rect
        cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
        if _adb_input_tap(driver, cx, cy):
            time.sleep(0.5); return True
    except Exception: pass
    return False


def _dismiss_first_use_if_any(driver):
    """First-use AlertDialog shows an 'UNDERSTOOD' or 'Understood' positive button.

    PoetsKingdom shows a Guide dialog on FIRST entry of:
      - MainActivity (welcome) → dismissing Welcome auto-navigates to Personalisation
      - PersonalisationActivity (cover-page guide)
      - PoemThemeActivity (pre-made outlines guide)
      - CreatePoemActivity (long-press / double-click / save guide)

    Also dismisses the system Android-App-Compatibility warning if it pops up
    (Bangcle libs trip the 16KB-alignment check on fresh installs).

    Loop until no UNDERSTOOD button visible (max 4 iterations to bound: Welcome
    → Personalisation Guide chain takes 2 dismisses).
    """
    dismissed_any = False
    # First: kill the App Compat system dialog (separate window from app dialogs)
    if _dismiss_app_compat_warning(driver):
        dismissed_any = True
    for _ in range(4):
        # Find Understood/UNDERSTOOD/OK and tap via adb (Compose dialogs ignore
        # synthetic Appium clicks).
        btn = _find_by_text(driver, "UNDERSTOOD", partial=False, timeout=0.6)
        if btn is None:
            btn = _find_by_text(driver, "Understood", partial=False, timeout=0.4)
        if btn is None:
            btn = _find_by_text(driver, "OK", partial=False, timeout=0.3)
        if btn is None: break
        try:
            r = btn.rect
            cx, cy = r["x"] + r["width"] // 2, r["y"] + r["height"] // 2
            if _adb_input_tap(driver, cx, cy):
                time.sleep(0.5)
                dismissed_any = True
            else: break
        except Exception:
            break
    return dismissed_any


def _run_s1_home_visible(driver):
    """S1: verify home tiles AND do real touch work (2 swipes) to generate
    pointer events while UI is in the foreground (this also primes Compose's
    pointer-input pipeline for subsequent tile taps)."""
    cov = {"scenario_name": "S1_home_visible", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _dismiss_first_use_if_any(driver)
    create = _find_by_text(driver, "Create Poem", partial=False, timeout=3.0)
    mypoems = _find_by_text(driver, "My Poems", partial=False, timeout=1.5)
    cov["createPoemImage_visible"] = create is not None
    cov["myPoemImage_visible"] = mypoems is not None
    # 2 home swipes to generate real touch events while we're verifying tiles.
    cov["home_swipes"] = _multi_swipe_home(driver, directions=("up", "down"))
    cov["scenario_success"] = bool(create and mypoems)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = cov["scenario_success"]
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["home_images_not_found"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s2_dismiss_first_use_dialog(driver):
    """S2: fully complete the fresh-install forced first-use chain so subsequent
    scenarios don't get hit by late-appearing dialogs.

    Sequence (fresh install): App Compat warning → Welcome guide → tapping
    Understood auto-launches Personalisation → Personalisation Guide → tapping
    Understood reveals Personalisation main → BACK returns to home.

    On a re-used install the chain may be empty: we still wait for any of those
    dialogs that show up with a generous timeout, then verify home is stable."""
    cov = {"scenario_name": "S2_dismiss_first_use_dialog", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Active poll for the Welcome dialog post-launch: bail on first UNDERSTOOD sighting.
    _poll_for_text(driver, "UNDERSTOOD", partial=False, max_wait=1.0, settle=0.2)
    dismissed = _dismiss_first_use_if_any(driver)
    cov["initial_dismissals"] = dismissed
    # Repeatedly: if home tiles not visible, press back; then dismiss any
    # dialog that appears. Loop until home is stable and no dialog up.
    stable = False
    for i in range(5):
        on_home = main_home_visible(driver)
        any_dialog = (
            _find_by_text(driver, "UNDERSTOOD", partial=False, timeout=0.2) is not None
            or _find_by_text(driver, "Understood", partial=False, timeout=0.2) is not None
            or _find_by_text(driver, "OK", partial=False, timeout=0.2) is not None
            or _find_by_text(driver, "Don't Show Again", partial=False, timeout=0.2) is not None
        )
        if on_home and not any_dialog:
            stable = True; break
        if any_dialog:
            _dismiss_first_use_if_any(driver)
        elif not on_home:
            _press_back(driver)
    cov["first_use_chain_completed"] = stable
    if not stable:
        _force_home(driver)
        _dismiss_first_use_if_any(driver)
        cov["first_use_chain_completed"] = main_home_visible(driver)
    # Real interaction: scroll home + briefly tap each tile then back, so the
    # scenario carries actual touch-event load even on warm re-runs where the
    # dialog chain is a no-op.
    cov["home_swipes"] = _multi_swipe_home(
        driver, directions=("up", "down", "left", "right"))
    cov["my_poems_tap"] = _tap_tile(driver, "My Poems", timeout=2.0)
    _poll_for_text(driver, "Albums", partial=False, max_wait=0.8, settle=0.2)
    _press_back(driver)
    if not main_home_visible(driver):
        _force_home(driver)
    cov["home_after"] = main_home_visible(driver)
    cov["scenario_success"] = bool(cov["home_after"])
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["home_not_visible_after_dialog_handling"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _multi_swipe_home(driver, directions=("up", "down", "left", "right")):
    """Issue a swipe gesture in each direction over the home screen, returning the
    count of successful swipes. Used by multiple scenarios for raw touch-event
    interaction; each swipe = several pointer events."""
    swipes = 0
    try:
        size = driver.get_window_size()
        for direction in directions:
            driver.execute_script("mobile: swipeGesture", {
                "left": int(size["width"] * 0.1), "top": int(size["height"] * 0.3),
                "width": int(size["width"] * 0.8), "height": int(size["height"] * 0.4),
                "direction": direction, "percent": 0.7,
            })
            swipes += 1
            time.sleep(0.3)
    except Exception:
        pass
    return swipes


def _interact_in_activity(driver, swipe_count=4):
    """Spend interaction time within a sub-activity: swipe up/down/left/right then
    short sleeps. Returns swipe count. Don't verify state: just fire events."""
    pattern = ("up", "down", "up", "down", "left", "right", "up", "down",
               "left", "right", "up", "down")
    return _multi_swipe_home(driver, directions=pattern[:swipe_count])


def _activity_round_trip(driver, tile_text, cov, sub_swipe_count=4):
    """Tap a home tile, wait for activity launch, dismiss any first-use guide,
    swipe within the activity to generate touch events, then back-press to home.
    Records progress in cov. Strict-pass = home visible at the end."""
    cov["tile_visible"] = _find_by_text(driver, tile_text, partial=False, timeout=2.5) is not None
    clicked = _tap_tile(driver, tile_text, timeout=2.5)
    cov["tile_clicked"] = clicked
    # Active poll for any sub-activity marker (Guide text or activity-specific
    # element): bail as soon as we leave home.
    deadline = time.time() + 1.2
    while time.time() < deadline:
        if (_find_by_text(driver, "UNDERSTOOD", partial=False, timeout=0.2) is not None
                or _find_by_text(driver, "Understood", partial=False, timeout=0.2) is not None
                or _find_by_text(driver, tile_text, partial=False, timeout=0.2) is None):
            break
    cov["dialog_dismissed"] = _dismiss_first_use_if_any(driver)
    cov["sub_activity_swipes"] = _interact_in_activity(driver, swipe_count=sub_swipe_count)
    # Some sub-activities show a Discard/confirm dialog when backing out
    _press_back(driver)
    discard_btn = _find_by_text(driver, "Discard", partial=True, timeout=0.4)
    if discard_btn is None:
        discard_btn = _find_by_text(driver, "Yes", partial=False, timeout=0.3)
    if discard_btn is not None:
        try: discard_btn.click(); time.sleep(0.5)
        except Exception: pass
    _dismiss_first_use_if_any(driver)
    home = main_home_visible(driver)
    if not home:
        _force_home(driver); home = main_home_visible(driver)
    cov["home_after_roundtrip"] = home
    # Strict-pass: we returned to home (the tap, swipes, and back all fired).
    cov["scenario_success"] = bool(home)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = bool(clicked)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["did_not_return_home"]


def _run_s3_create_first_poem(driver):
    """S3: the full Create Poem flow (tile → theme picker → title dialog → editor
    → type body → save → category pick). Verifies poem made it through Save-as-a-File.
    Strict-pass = back to home AND each step recorded a True progress flag."""
    cov = {"scenario_name": "S3_create_first_poem", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    title = "TestPoem1"
    body = "Roses are red\nViolets are blue"
    progress = _create_poem(driver, title=title, body=body, category_text="NONE")
    cov.update(progress)
    cov["title"] = title
    must_have = (
        progress.get("create_poem_tile_clicked", False)
        and progress.get("confirm_clicked", False)
        and progress.get("save_button_clicked", False)
        and progress.get("save_as_file_clicked", False)
        and progress.get("category_picked", False)
    )
    cov["scenario_success"] = bool(must_have and progress.get("back_to_home", False))
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = bool(progress.get("create_poem_tile_clicked"))
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["create_poem_flow_incomplete"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s4_verify_first_poem_in_my_poems(driver):
    """S4: open My Poems, verify "TestPoem1" card visible. Back to home."""
    cov = {"scenario_name": "S4_verify_first_poem_in_my_poems", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _open_my_poems(driver)
    cov["my_poems_visible"] = _find_by_text(driver, "Albums", partial=False, timeout=1.5) is not None \
        or _find_by_text(driver, "All Poems", partial=False, timeout=1.0) is not None \
        or _find_by_text(driver, "TestPoem1", partial=False, timeout=1.0) is not None
    # 2026-07-29: scroll fallback for slow-render / S3-cascade; try a swipe if
    # TestPoem1 not immediately visible. Fires only on failing paths; passing
    # cells unchanged (matched-pair preserved).
    found = _poem_present_in_my_poems(driver, "TestPoem1", timeout=2.5)
    if not found:
        _multi_swipe_home(driver, directions=("down",))
        found = _poem_present_in_my_poems(driver, "TestPoem1", timeout=1.5)
    cov["first_poem_present"] = found
    # Back to home
    _press_back(driver)
    if not main_home_visible(driver):
        _force_home(driver)
    cov["home_after"] = main_home_visible(driver)
    cov["scenario_success"] = bool(cov["first_poem_present"] and cov["home_after"])
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = bool(cov["my_poems_visible"])
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["first_poem_not_in_list"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s5_create_second_poem(driver):
    """S5: same flow again with a different title + category. Warm path for
    SQLite write + thumbnail render."""
    cov = {"scenario_name": "S5_create_second_poem", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    title = "TestPoem2"
    body = "Code compiles\nTests pass"
    # Skip restart: S4 already left us on a stable home, saves ~2s vs cold path.
    progress = _create_poem(driver, title=title, body=body, category_text="ROMANTIC",
                             skip_restart=True)
    cov.update(progress)
    cov["title"] = title
    must_have = (
        progress.get("create_poem_tile_clicked", False)
        and progress.get("confirm_clicked", False)
        and progress.get("save_button_clicked", False)
        and progress.get("save_as_file_clicked", False)
        and progress.get("category_picked", False)
    )
    cov["scenario_success"] = bool(must_have and progress.get("back_to_home", False))
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = bool(progress.get("create_poem_tile_clicked"))
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["create_second_poem_flow_incomplete"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s6_verify_both_poems_in_my_poems(driver):
    """S6: re-open My Poems, verify BOTH TestPoem1 and TestPoem2 visible."""
    cov = {"scenario_name": "S6_verify_both_poems_in_my_poems", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _open_my_poems(driver)
    cov["first_poem_present"] = _poem_present_in_my_poems(driver, "TestPoem1", timeout=2.5)
    cov["second_poem_present"] = _poem_present_in_my_poems(driver, "TestPoem2", timeout=2.0)
    _press_back(driver)
    if not main_home_visible(driver):
        _force_home(driver)
    cov["home_after"] = main_home_visible(driver)
    cov["scenario_success"] = bool(
        cov["first_poem_present"] and cov["second_poem_present"] and cov["home_after"]
    )
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["one_or_both_poems_missing"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s7_tap_first_poem_to_edit(driver):
    """S7: tap TestPoem1 in My Poems → CreatePoem editor opens with poem
    pre-loaded → back to MyPoems → back to home. Exercises poem-load hot path."""
    cov = {"scenario_name": "S7_tap_first_poem_to_edit", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _open_my_poems(driver)
    poem = _find_by_text(driver, "TestPoem1", partial=False, timeout=2.5)
    # 2026-07-29: scroll fallback for slow-render / S3-cascade; try a swipe if
    # the poem isn't immediately visible. Failure-path only.
    if poem is None:
        _multi_swipe_home(driver, directions=("down",))
        poem = _find_by_text(driver, "TestPoem1", partial=False, timeout=1.5)
    cov["first_poem_visible"] = poem is not None
    opened_editor = False
    if poem is not None:
        # In MyPoems the *clickable* element for a poem is the thumbnail
        # ImageView above the title TextView. Find the clickable ImageView
        # whose bounds sit directly above the title and tap its center.
        from appium.webdriver.common.appiumby import AppiumBy
        try:
            tr = poem.rect
            poem_y = tr["y"]; poem_cx = tr["x"] + tr["width"] // 2
        except Exception:
            poem_y = None; poem_cx = None
        target_x, target_y = None, None
        if poem_y is not None:
            try:
                thumbs = driver.find_elements(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiSelector().clickable(true).className("android.widget.ImageView")')
                # Pick the one whose bottom is closest to (and above) the title's top
                best = None; best_gap = None
                for el in thumbs:
                    try:
                        er = el.rect
                        bottom = er["y"] + er["height"]
                        if bottom <= poem_y + 30:  # thumbnail above title (allow tiny overlap)
                            gap = poem_y - bottom
                            if best_gap is None or gap < best_gap:
                                best_gap = gap; best = er
                    except Exception: continue
                if best is not None:
                    target_x = best["x"] + best["width"] // 2
                    target_y = best["y"] + best["height"] // 2
            except Exception: pass
        if target_x is None:
            # Fallback: tap ~250px above the title (typical thumbnail offset)
            target_x = poem_cx
            target_y = max(0, poem_y - 250)
        cov["tap_target"] = [target_x, target_y]
        _adb_input_tap(driver, target_x, target_y)
        # Active poll for editor markers: bail as soon as bottomDrawer/saveOptions appear.
        _poll_for_id(driver, "bottomDrawer", max_wait=1.5, settle=0.2)
        _tap_understood_in_dialog(driver, timeout=1.0)
        opened_editor = (_find_by_id(driver, "bottomDrawer", timeout=0.8) is not None
                         or _find_by_id(driver, "saveOptions", timeout=0.4) is not None)
    cov["editor_opened"] = opened_editor
    # Back twice → MyPoems → home
    _press_back(driver)
    _press_back(driver)
    if not main_home_visible(driver):
        _force_home(driver)
    cov["home_after"] = main_home_visible(driver)
    cov["scenario_success"] = bool(opened_editor and cov["home_after"])
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = bool(cov["first_poem_visible"])
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["edit_open_failed"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s8_tap_personalisation_roundtrip(driver):
    """S8: tap Personalisation tile → activity loads → swipe + try to tap
    visible widgets (colour swatches, radio buttons, outline picker) to
    generate real settings-write touch events → back to home."""
    cov = {"scenario_name": "S8_tap_personalisation_roundtrip", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    from appium.webdriver.common.appiumby import AppiumBy
    cov["tile_visible"] = _find_by_text(driver, "Personalisation", partial=False, timeout=2.5) is not None
    clicked = _tap_tile(driver, "Personalisation", timeout=2.5)
    cov["tile_clicked"] = clicked
    # Active poll until Personalisation activity ready (not on home anymore)
    deadline = time.time() + 1.2
    while time.time() < deadline:
        if (_find_by_text(driver, "UNDERSTOOD", partial=False, timeout=0.2) is not None
                or _find_by_text(driver, "Understood", partial=False, timeout=0.2) is not None
                or _find_by_text(driver, "Create Poem", partial=False, timeout=0.2) is None):
            break
    cov["dialog_dismissed"] = _dismiss_first_use_if_any(driver)
    # Swipe to expose more widgets, then try to tap up to 3 clickable controls
    # (radio buttons / colour swatches / outline thumbnails): generates real
    # settings-write touch events. Each tap exercises the Personalisation
    # state-write hot path which writes to SharedPreferences.
    cov["sub_activity_swipes"] = _multi_swipe_home(
        driver, directions=("up", "down", "up", "down", "left", "right"))
    widget_taps = 0
    try:
        clickables = driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().clickable(true)')
        # Skip the topmost ~150px (toolbar): tap a few in the content body
        for el in clickables[:20]:
            if widget_taps >= 3: break
            try:
                er = el.rect
                if er["y"] < 150: continue  # toolbar
                if er["width"] < 20 or er["height"] < 20: continue
                cx, cy = er["x"] + er["width"] // 2, er["y"] + er["height"] // 2
                if _adb_input_tap(driver, cx, cy):
                    widget_taps += 1
                    time.sleep(0.3)
            except Exception: continue
    except Exception: pass
    cov["widget_taps"] = widget_taps
    # Back-press to leave; handle Discard if a settings-changed dialog appears
    _press_back(driver)
    discard_btn = _find_by_text(driver, "Discard", partial=True, timeout=0.4)
    if discard_btn is None:
        discard_btn = _find_by_text(driver, "Yes", partial=False, timeout=0.3)
    if discard_btn is not None:
        try: discard_btn.click(); time.sleep(0.5)
        except Exception: pass
    _dismiss_first_use_if_any(driver)
    home = main_home_visible(driver)
    if not home:
        _force_home(driver); home = main_home_visible(driver)
    cov["home_after_roundtrip"] = home
    cov["scenario_success"] = bool(home)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = bool(clicked)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["did_not_return_home"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s9_swipe_my_poems_list(driver):
    """S9: open My Poems and swipe through the list (2 cards now; scroll +
    long-press attempts). Pure interaction load on the RecyclerView."""
    cov = {"scenario_name": "S9_swipe_my_poems_list", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _open_my_poems(driver)
    cov["my_poems_visible"] = (
        _find_by_text(driver, "TestPoem1", partial=False, timeout=1.5) is not None
        or _find_by_text(driver, "Albums", partial=False, timeout=0.6) is not None
    )
    swipes = _multi_swipe_home(
        driver, directions=("up", "down", "up", "down", "left", "right", "up", "down"))
    cov["swipes"] = swipes
    _press_back(driver)
    if not main_home_visible(driver):
        _force_home(driver)
    cov["home_after"] = main_home_visible(driver)
    cov["scenario_success"] = bool(swipes >= 3 and cov["home_after"])
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = swipes >= 3
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["swipe_or_home_failed"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


def _run_s10_final_home_check(driver):
    """S10 (substantive final check): 2 swipes + a My Images tile round-trip
    before asserting home is stable. Generates real touch events vs. the prior
    no-op flow which only read the screen."""
    cov = {"scenario_name": "S10_final_home_check", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _dismiss_first_use_if_any(driver)
    # 2 home swipes
    cov["home_swipes"] = _multi_swipe_home(driver, directions=("up", "down"))
    # Brief My Images tile round-trip: tap, dismiss any guide, back to home.
    cov["my_images_tap"] = _tap_tile(driver, "My Images", timeout=2.0)
    _poll_for_text(driver, "UNDERSTOOD", partial=False, max_wait=0.8, settle=0.2)
    _dismiss_first_use_if_any(driver)
    _press_back(driver)
    if not main_home_visible(driver):
        _force_home(driver)
    home = main_home_visible(driver)
    cov["home_visible"] = home
    cov["scenario_success"] = bool(home)
    cov["scenario_success_strict_ui"] = cov["scenario_success"]
    cov["scenario_success_action_only"] = True
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["home_not_visible_at_end"]
    cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return cov


_SCENARIO_FUNCS = {
    "S1_home_visible": _run_s1_home_visible,
    "S2_dismiss_first_use_dialog": _run_s2_dismiss_first_use_dialog,
    "S3_create_first_poem": _run_s3_create_first_poem,
    "S4_verify_first_poem_in_my_poems": _run_s4_verify_first_poem_in_my_poems,
    "S5_create_second_poem": _run_s5_create_second_poem,
    "S6_verify_both_poems_in_my_poems": _run_s6_verify_both_poems_in_my_poems,
    "S7_tap_first_poem_to_edit": _run_s7_tap_first_poem_to_edit,
    "S8_tap_personalisation_roundtrip": _run_s8_tap_personalisation_roundtrip,
    "S9_swipe_my_poems_list": _run_s9_swipe_my_poems_list,
    "S10_final_home_check": _run_s10_final_home_check,
}


def _run_espresso_mirror_workload(driver, package, deadline, dump_dir, build_label, artifact_dir):
    scenarios_results = []
    steps = 0
    for scenario_name in POETSKINGDOM_SCENARIO_LIST:
        if time.monotonic() > deadline:
            log("workload deadline reached before %s; bailing" % scenario_name)
            break
        fn = _SCENARIO_FUNCS.get(scenario_name)
        if fn is None: continue
        _dismiss_page_size_dialog(driver)
        t0 = time.monotonic()
        try:
            cov = fn(driver)
        except Exception as ex:
            cov = {"scenario_name": scenario_name, "scenario_success": False,
                   "scenario_success_strict_ui": False, "scenario_success_action_only": False,
                   "scenario_fail_reasons_strict_ui": ["exception:%s:%s" % (type(ex).__name__, str(ex)[:160])],
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "ts_end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            log("scenario %s raised: %s: %s" % (scenario_name, type(ex).__name__, ex))
        cov["build_label"] = build_label
        cov["workload"] = "espresso_mirror"
        cov["suite_round"] = 1
        cov["scenario_wall_seconds"] = round(time.monotonic() - t0, 3)
        scenarios_results.append(cov)
        append_coverage_jsonl(dump_dir, cov)
        steps += 1
        log("scenario %s -> success=%s strict=%s wall=%.2fs" % (
            scenario_name, cov.get("scenario_success"),
            cov.get("scenario_success_strict_ui"), cov["scenario_wall_seconds"]))

    by_scenario = {}
    for cov in scenarios_results:
        sn = cov.get("scenario_name", "?")
        row = by_scenario.setdefault(sn, {"runs":0,"success":0,"fail":0,"success_strict_ui":0,
                                          "fail_strict_ui":0,"success_action_only":0,"fail_action_only":0,
                                          "example_fail_reasons":[]})
        row["runs"] += 1
        if cov.get("scenario_success_strict_ui"): row["success"]+=1; row["success_strict_ui"]+=1
        else:
            row["fail"]+=1; row["fail_strict_ui"]+=1
            for r in (cov.get("scenario_fail_reasons_strict_ui") or []):
                if r not in row["example_fail_reasons"]: row["example_fail_reasons"].append(r)
        if cov.get("scenario_success_action_only"): row["success_action_only"]+=1
        else: row["fail_action_only"]+=1

    total_runs = sum(v["runs"] for v in by_scenario.values())
    total_succ = sum(v["success"] for v in by_scenario.values())
    agg = {"build_label": build_label or None, "by_scenario": by_scenario,
           "summary":{"fail_count":total_runs-total_succ,"success_count":total_succ,
                      "total_scenario_executions":total_runs,
                      "total_strict_ui_pass": sum(v.get("success_strict_ui",0) for v in by_scenario.values()),
                      "total_action_only_pass": sum(v.get("success_action_only",0) for v in by_scenario.values())}}
    lines = ["Espresso-mirror scenario report (PoetsKingdom)",
             "build_label: %s" % (build_label or "(none)"), "",
             "summary: %s runs, %s passed, %s failed" % (total_runs, total_succ, total_runs-total_succ), ""]
    for sn in [s for s in POETSKINGDOM_SCENARIO_LIST if s in by_scenario]:
        r = by_scenario[sn]
        lines.append("%s  runs=%s  strict_ui=%s/%s  action_only=%s/%s"
                     % (sn, r["runs"], r.get("success_strict_ui",0), r.get("fail_strict_ui",0),
                        r.get("success_action_only",0), r.get("fail_action_only",0)))
        if r["example_fail_reasons"]: lines.append("  examples: %s" % r["example_fail_reasons"][:3])
    text = "\n".join(lines) + "\n"
    write_scenario_report(artifact_dir, agg, text, workload_name="espresso_mirror")
    return steps


def run_workload(experiment, device):
    dump_dir = resolve_dump_dir()
    artifact_dir = resolve_artifact_dir(dump_dir)
    package = getattr(experiment, "package", None) or PACKAGE
    try: duration_s = float(getattr(experiment, "duration", 120))
    except Exception: duration_s = 120.0
    if duration_s < 30: duration_s = 30.0
    activity = os.environ.get("POETSKINGDOM_ACTIVITY", DEFAULT_ACTIVITY)
    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    connect_timeout_s = 120.0
    build_label = os.environ.get("APPIUM_BUILD_LABEL", "")
    status = {"failure_reason":None,"session_connect_timeout_seconds":connect_timeout_s,
              "session_created":False,"session_start_seconds":None,"session_timeout":False,
              "steps_executed":0,"workload_mode":"espresso_mirror","workload_started":False}

    def _workload():
        from appium import webdriver
        thread_started = time.monotonic(); driver = None
        try:
            options = build_uiautomator2_options(udid=device.id, package=package,
                                                  activity=activity, duration_s=duration_s)
            log("connecting to %s (target workload %.1fs)" % (server, duration_s))
            driver = connect_remote_webdriver(webdriver, server, options, connect_timeout_s, dump_dir)
            status["session_created"] = True
            status["session_start_seconds"] = round(time.monotonic() - thread_started, 3)
            bump_driver_http_timeout(driver, duration_s)
            try:
                driver.execute_script("mobile: startActivity", {"intent": "%s/%s" % (package, activity)})
                log("forced startActivity(%s/%s)" % (package, activity)); time.sleep(1.5)
            except Exception as ex:
                log("startActivity force-switch failed: %s %s" % (type(ex).__name__, ex))
            failsafe_deadline = time.monotonic() + max(duration_s * 2, 240)
            _dismiss_page_size_dialog(driver)
            await_app_ready(driver, package, failsafe_deadline,
                            is_main_ui_visible=main_home_visible, dump_dir=dump_dir)
            _dismiss_page_size_dialog(driver)
            _dismiss_first_use_if_any(driver)
            log("session ready; running locked %d-scenario list end-to-end"
                % len(POETSKINGDOM_SCENARIO_LIST))
            status["workload_started"] = True
            status["steps_executed"] = _run_espresso_mirror_workload(
                driver, package, failsafe_deadline, dump_dir, build_label, artifact_dir)
        except SessionConnectTimeoutError as ex:
            status["failure_reason"] = str(ex); status["session_timeout"] = True
            log("SESSION_TIMEOUT: %s" % ex); emit_stderr("INTERACTION_APPIUM: SESSION_TIMEOUT %s" % ex)
        except Exception as ex:
            status["failure_reason"] = "%s: %s" % (type(ex).__name__, ex)
            if is_likely_http_timeout(ex): status["session_timeout"] = True
            log("SESSION_FAIL: %s: %s" % (type(ex).__name__, ex))
            emit_stderr("INTERACTION_APPIUM: %s: %s" % (type(ex).__name__, ex))
        finally:
            if driver is not None:
                try: driver.quit()
                except Exception: pass

    failsafe_seconds = max(duration_s * 2, 240)
    start_workload_thread_and_block_until_done(_workload, failsafe_seconds)
    write_appium_status(artifact_dir, status)
    log("workload thread finished")
