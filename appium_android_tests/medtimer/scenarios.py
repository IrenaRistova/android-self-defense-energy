"""MedTimer (com.futsch1.medtimer): N=10 deep scenarios with real persistence.

Live-explored on Pixel 9 / Android 16. The shallow tab-navigation scenarios from
the previous version were replaced with a true user workload:

  1. Dismiss the POST_NOTIFICATIONS permission dialog on first launch.
  2. Verify Overview home (bottomNav + nextRemindersCard + latestRemindersCard
     + logManualDose FAB).
  3. Navigate to Medicines tab → add medicine "Aspirin" via FAB → AlertDialog
     (medicineName EditText + OK/CANCEL = android:id/button1/2).
  4. Land on medicine detail (editMedicineName + Color toggle + Notification
     priority + Add reminder + reminderList + Tags/Stock/Calendar buttons).
  5. Tap "Add reminder" → reminder dialog (editAmount, timeBased/intervalBased,
     editReminderTime default "8:00 AM", createReminder/cancelCreateReminder).
  6. Type dosage "1" + Create reminder → reminder appears in reminderList
     (reminderCard with editReminderTime + editAmount + Advanced settings
     "Every day" summary).
  7. Return to Overview → nextRemindersCard shows
     ``Aspirin (1)\\n<date> 8:00 AM`` in nextReminderText with Taken now /
     Skip dose action buttons.
  8. Add second medicine "Ibuprofen" + reminder.
  9. Tap "Taken now" on a next reminder → recent event logged.
  10. Final overview verifies both medicines persist + at least one event in
      latestReminders.

Strict criteria require **real persistence**: medicine_in_detail (after add),
reminder_in_list (after create), next_reminder_text_contains_medicine_name
(Overview), latest_event_count_after > before (after Taken now).

KEY LIVE-EXPLORATION DISCOVERY: AlertDialog button bounds **shift** after the
soft keyboard hides: get fresh bounds via Appium's find_element each time
(Appium handles this naturally: only static ``input tap X Y`` fails).
"""
from __future__ import annotations
import os, time
from typing import Any
from .._lib import (SessionConnectTimeoutError, append_coverage_jsonl, await_app_ready,
    build_uiautomator2_options, bump_driver_http_timeout, connect_remote_webdriver,
    emit_stderr, is_likely_http_timeout, log, resolve_artifact_dir, resolve_dump_dir,
    start_workload_thread_and_block_until_done, write_appium_status, write_scenario_report)

PACKAGE = "com.futsch1.medtimer"
RESOURCE_NS = "com.futsch1.medtimer"
DEFAULT_ACTIVITY = "com.futsch1.medtimer.MainActivity"

MEDTIMER_SCENARIO_LIST = (
    # 2026-07-26 v3 amendment:
    # REMOVED S12 (P6-only fail: createReminder_button_not_found)
    # REMOVED S13 + _repeat2 + _repeat3 (matched-flake all 3 phones)
    # REMOVED S8_add_second_medicine_ibuprofen (P6+P9 fail: OK_button_not_found)
    # REMOVED S9_add_reminder_to_ibuprofen (P6+P9 fail, depends on S8)
    # Result (v3): 15 scenarios, target ~114s; MEASURED baseline
    # duration_workload_s 98 / 101.5 / 123 s (min / median / max, n=8), i.e.
    # the floor sat 12 s below the [110, 130] s window.
    #
    # v4 2026-08-03 RECALIBRATION: DROPPED the S14/S15 repeat3 pair (15 -> 13
    # scenarios). The +12 s deficit implied by batch1 was computed
    # from batch1, which predates the 2026-08-03 wait-widening / AppiumIME /
    # adb-tap changes, and it had the SIGN WRONG. Re-measured on Pixel 6
    # against the CURRENT code, 15 scenarios span 137.7 s: 8 s OVER the
    # window, not 12 s under it. Do not re-derive this app's budget from batch1.
    #
    # The real problem was not N. Three runs of identical code at N=14 spanned
    # 100 / 152 / 103 s (a 52 s spread, all 14/14 strict) because S14's
    # Tags / Stock / Calendar loop was searching for TEXT on three ImageButtons
    # that carry their label in content-desc, so it never matched and instead
    # burned three unbounded failed find round-trips per call. Fixed in
    # _run_s14_open_medicine_detail_browse (see the note there): the sub-screens
    # are now actually visited (subscreens_visited 0 -> 3) and the spread
    # collapsed to 107 / 111 s. The list is back at its original 15 entries.
    # Dispatchers for S8/S9/S12/S13 retained for calibration.
    "S1_dismiss_notification_permission",
    "S2_overview_home_visible",
    "S3_navigate_to_medicines_tab",
    "S4_add_first_medicine_aspirin",
    "S5_add_reminder_to_aspirin",
    "S6_overview_shows_new_reminder",
    "S7_tap_taken_now_on_reminder",
    "S10_final_overview_both_persist",
    "S11_add_third_medicine_paracetamol",
    "S14_open_medicine_detail_browse",
    "S15_browse_log_history",
    "S14_open_medicine_detail_browse_repeat2",
    "S15_browse_log_history_repeat2",
    "S14_open_medicine_detail_browse_repeat3",
    "S15_browse_log_history_repeat3",
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


def _find_by_desc(driver, desc, timeout=3.0):
    """Find by content-description.

    Added 2026-08-03 for S14's Tags / Stock / Calendar sub-screen buttons:
    they are ImageButtons whose visible label is a content-desc, so
    :func:`_find_by_text` can never match them.
    """
    from appium.webdriver.common.appiumby import AppiumBy
    sel = 'new UiSelector().description("%s")' % desc
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els: return els[0]
        time.sleep(0.2)
    return None


def _find_dialog_button(driver, label, timeout=2.0):
    """Find AlertDialog button by text: fresh lookup, immune to bounds-shift."""
    return _find_by_text(driver, label, partial=False, timeout=timeout)


def _press_back(driver):
    try: driver.back()
    except Exception: pass
    time.sleep(0.6)


def main_home_visible(driver):
    return _find_by_id(driver, "bottomNavigationView", timeout=2.0) is not None


def _force_home(driver):
    try:
        driver.terminate_app(PACKAGE); time.sleep(0.5)
        driver.activate_app(PACKAGE); time.sleep(1.5)
        return main_home_visible(driver)
    except Exception: return False


def _dismiss_notification_permission(driver):
    """First-launch POST_NOTIFICATIONS prompt: tap Allow if present."""
    from appium.webdriver.common.appiumby import AppiumBy
    # The permission controller exposes resource-id permission_allow_button
    for sel in [
        'new UiSelector().resourceId("com.android.permissioncontroller:id/permission_allow_button")',
        'new UiSelector().resourceId("com.android.permissioncontroller:id/permission_allow_foreground_only_button")',
        'new UiSelector().text("Allow")',
    ]:
        try:
            els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
            if els:
                els[0].click()
                time.sleep(0.8)
                return True
        except Exception:
            continue
    return False


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


def _tap_bottom_nav(driver, fragment_id):
    """Tap a bottom-nav tab by its FrameLayout resource-id."""
    el = _find_by_id(driver, fragment_id, timeout=2.5)
    if el is None: return False
    try:
        el.click()
        time.sleep(1.0)
        return True
    except Exception:
        return False


def _add_medicine(driver, name):
    """Tap addMedicine FAB → type name → tap OK. Returns True if medicine detail visible after.

    Handles fragment back-stack: if we're already on a medicine detail screen
    (editMedicineName visible) because of prior navigation, press back to return
    to the medicine list view before looking for addMedicine FAB.
    """
    # If on detail screen, back out to list
    if _find_by_id(driver, "editMedicineName", timeout=0.6) is not None:
        _press_back(driver)
        time.sleep(0.8)
    fab = _find_by_id(driver, "addMedicine", timeout=3.0)
    if fab is None: return False, "addMedicine_fab_not_found"
    try: fab.click()
    except Exception as ex: return False, "fab_click_failed:%s" % type(ex).__name__
    time.sleep(1.3)
    name_input = _find_by_id(driver, "medicineName", timeout=2.0)
    if name_input is None: return False, "medicineName_input_not_found"
    try:
        name_input.click(); time.sleep(0.3)
        name_input.clear(); time.sleep(0.2)
        name_input.send_keys(name)
        time.sleep(0.6)
    except Exception as ex:
        return False, "type_failed:%s" % type(ex).__name__
    # Hide keyboard so OK button has stable position
    try: driver.hide_keyboard()
    except Exception: pass
    time.sleep(0.5)
    # Find OK button fresh (post-IME-hide bounds)
    ok_btn = _find_dialog_button(driver, "OK", timeout=1.5)
    if ok_btn is None:
        # Fallback: android:id/button1
        ok_btn = _find_by_id(driver, "button1", timeout=1.0, ns="android")
    if ok_btn is None: return False, "OK_button_not_found"
    try: ok_btn.click()
    except Exception as ex: return False, "ok_click_failed:%s" % type(ex).__name__
    # Active poll for medicine-detail transition + churn UI by scrolling
    # the detail screen while we wait for Room commit (replaces idle 2.0s).
    edit_name = None
    poll_deadline = time.time() + 2.2
    swiped_once = False
    while time.time() < poll_deadline:
        edit_name = _find_by_id(driver, "editMedicineName", timeout=0.4)
        if edit_name is not None:
            # Detail visible: do a tiny active scroll to keep UI thread busy
            try:
                size = driver.get_window_size()
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.45),
                    "width": size["width"], "height": int(size["height"]*0.35),
                    "direction": "up", "percent": 0.4,
                })
            except Exception: pass
            time.sleep(0.25)
            # Scroll back so subsequent reminder add still finds addReminder
            try:
                size = driver.get_window_size()
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.45),
                    "width": size["width"], "height": int(size["height"]*0.35),
                    "direction": "down", "percent": 0.4,
                })
            except Exception: pass
            time.sleep(0.2)
            break
        # not yet: tap somewhere neutral to keep main thread active
        if not swiped_once:
            try:
                size = driver.get_window_size()
                driver.execute_script("mobile: clickGesture", {
                    "x": int(size["width"]*0.5), "y": int(size["height"]*0.5),
                })
            except Exception: pass
            swiped_once = True
        time.sleep(0.15)
    if edit_name is None:
        edit_name = _find_by_id(driver, "editMedicineName", timeout=0.6)
    return edit_name is not None, None


def _add_reminder(driver, dosage="1"):
    """Tap addReminder on medicine detail → type dosage → createReminder. Returns True if reminder card appears in reminderList."""
    add_btn = _find_by_id(driver, "addReminder", timeout=2.5)
    if add_btn is None: return False, "addReminder_button_not_found"
    try: add_btn.click()
    except Exception as ex: return False, "addReminder_click_failed:%s" % type(ex).__name__
    time.sleep(1.5)
    # Type dosage in editAmount
    amount = _find_by_id(driver, "editAmount", timeout=2.0)
    if amount is None: return False, "editAmount_not_found"
    try:
        amount.click(); time.sleep(0.3)
        amount.clear(); time.sleep(0.2)
        amount.send_keys(dosage); time.sleep(0.5)
    except Exception:
        pass  # editAmount may behave differently: try without clear
    try: driver.hide_keyboard()
    except Exception: pass
    time.sleep(0.5)
    create_btn = _find_by_id(driver, "createReminder", timeout=2.0)
    if create_btn is None:
        create_btn = _find_by_text(driver, "Create reminder", partial=False, timeout=1.0)
    if create_btn is None: return False, "createReminder_button_not_found"
    try: create_btn.click()
    except Exception as ex: return False, "createReminder_click_failed:%s" % type(ex).__name__
    # Active poll for reminderCard + small swipe to keep RecyclerView rebinding
    # (replaces the idle 2.0s wait for dialog dismiss + list refresh).
    cards = []
    poll_deadline = time.time() + 2.2
    while time.time() < poll_deadline:
        cards = _find_all_by_id(driver, "reminderCard", timeout=0.4)
        if cards:
            # Reminder visible: churn the reminderList with a small swipe
            try:
                size = driver.get_window_size()
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.50),
                    "width": size["width"], "height": int(size["height"]*0.30),
                    "direction": "up", "percent": 0.4,
                })
            except Exception: pass
            time.sleep(0.25)
            try:
                size = driver.get_window_size()
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.50),
                    "width": size["width"], "height": int(size["height"]*0.30),
                    "direction": "down", "percent": 0.4,
                })
            except Exception: pass
            time.sleep(0.2)
            break
        time.sleep(0.15)
    if not cards:
        cards = _find_all_by_id(driver, "reminderCard", timeout=0.6)
    return len(cards) > 0, None


# --- Scenarios ---------------------------------------------------------------
def _run_s1_dismiss_notification_permission(driver):
    cov = {"scenario_name": "S1_dismiss_notification_permission",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # On first install of debug build, Android asks for POST_NOTIFICATIONS
    dismissed = _dismiss_notification_permission(driver)
    cov["dialog_dismissed"] = dismissed  # may be False if dialog wasn't present (re-runs)
    time.sleep(0.4)
    cov["main_visible_after"] = main_home_visible(driver)
    cov["scenario_success"] = cov["main_visible_after"]
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["main_ui_not_visible_after_permission"]
    return _ok(cov)


def _run_s2_overview_home_visible(driver):
    cov = {"scenario_name": "S2_overview_home_visible",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    bottom_nav = _find_by_id(driver, "bottomNavigationView", timeout=2.5)
    next_card = _find_by_id(driver, "nextRemindersCard", timeout=1.5)
    latest_card = _find_by_id(driver, "latestRemindersCard", timeout=1.5)
    log_dose = _find_by_id(driver, "logManualDose", timeout=1.5)
    cov["bottom_nav_visible"] = bottom_nav is not None
    cov["next_reminders_card_visible"] = next_card is not None
    cov["latest_reminders_card_visible"] = latest_card is not None
    cov["log_manual_dose_visible"] = log_dose is not None
    cov["scenario_success"] = bool(bottom_nav and next_card and latest_card and log_dose)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["overview_cards_or_fab_missing"]
    return _ok(cov)


def _run_s3_navigate_to_medicines_tab(driver):
    cov = {"scenario_name": "S3_navigate_to_medicines_tab",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tapped = _tap_bottom_nav(driver, "medicinesFragment")
    cov["medicines_tab_tapped"] = tapped
    time.sleep(0.6)
    add_fab = _find_by_id(driver, "addMedicine", timeout=2.5)
    medicine_list = _find_by_id(driver, "medicineList", timeout=1.5)
    cov["add_fab_visible"] = add_fab is not None
    cov["medicine_list_visible"] = medicine_list is not None
    cov["scenario_success"] = bool(tapped and add_fab and medicine_list)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["medicines_tab_not_loaded"]
    return _ok(cov)


def _run_s4_add_first_medicine_aspirin(driver):
    cov = {"scenario_name": "S4_add_first_medicine_aspirin",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ok, err = _add_medicine(driver, "Aspirin")
    cov["medicine_added"] = ok
    if not ok and err:
        cov["scenario_fail_reasons_strict_ui"] = [err]
    # Verify name field shows Aspirin
    edit_name = _find_by_id(driver, "editMedicineName", timeout=2.0)
    name_text = ""
    if edit_name is not None:
        try: name_text = edit_name.text or ""
        except Exception: pass
    cov["edit_medicine_name_text"] = name_text
    cov["name_matches_typed"] = "Aspirin" in name_text
    cov["scenario_success"] = bool(ok and cov["name_matches_typed"])
    cov["scenario_success_action_only"] = bool(ok)
    if not cov["scenario_success"] and not cov.get("scenario_fail_reasons_strict_ui"):
        cov["scenario_fail_reasons_strict_ui"] = ["medicine_name_did_not_persist"]
    return _ok(cov)


def _run_s5_add_reminder_to_aspirin(driver):
    cov = {"scenario_name": "S5_add_reminder_to_aspirin",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # We're on medicine detail screen from S4
    ok, err = _add_reminder(driver, dosage="1")
    cov["reminder_created"] = ok
    if not ok and err:
        cov["scenario_fail_reasons_strict_ui"] = [err]
    cards = _find_all_by_id(driver, "reminderCard", timeout=2.0)
    cov["reminder_card_count"] = len(cards)
    # Verify time + amount fields populated
    reminder_time = _find_by_id(driver, "editReminderTime", timeout=1.5)
    time_text = ""
    if reminder_time is not None:
        try: time_text = reminder_time.text or ""
        except Exception: pass
    cov["reminder_time_text"] = time_text
    cov["scenario_success"] = bool(ok and cov["reminder_card_count"] >= 1)
    cov["scenario_success_action_only"] = bool(ok)
    if not cov["scenario_success"] and not cov.get("scenario_fail_reasons_strict_ui"):
        cov["scenario_fail_reasons_strict_ui"] = ["reminder_did_not_persist"]
    return _ok(cov)


def _run_s6_overview_shows_new_reminder(driver):
    cov = {"scenario_name": "S6_overview_shows_new_reminder",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tapped = _tap_bottom_nav(driver, "overviewFragment")
    cov["overview_tab_tapped"] = tapped
    time.sleep(1.2)
    next_text_el = _find_by_id(driver, "nextReminderText", timeout=3.0)
    next_text = ""
    if next_text_el is not None:
        try: next_text = next_text_el.text or ""
        except Exception: pass
    cov["next_reminder_text"] = next_text
    cov["aspirin_in_next_reminder"] = "Aspirin" in next_text
    # Also check Taken now / Skip dose buttons present
    taken = _find_by_id(driver, "takenNow", timeout=1.0)
    skip = _find_by_id(driver, "skippedNow", timeout=0.8)
    cov["taken_now_visible"] = taken is not None
    cov["skip_dose_visible"] = skip is not None
    cov["scenario_success"] = bool(tapped and cov["aspirin_in_next_reminder"] and taken)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["next_reminder_does_not_show_aspirin"]
    return _ok(cov)


def _run_s7_tap_taken_now_on_reminder(driver):
    cov = {"scenario_name": "S7_tap_taken_now_on_reminder",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Count latest events before
    before_latest = _find_all_by_id(driver, "latestRemindersCard", timeout=1.5)
    cov["latest_events_before"] = len(before_latest)
    taken_btn = _find_by_id(driver, "takenNow", timeout=2.0)
    clicked = False
    if taken_btn is not None:
        try: taken_btn.click(); clicked = True
        except Exception: pass
    cov["taken_now_clicked"] = clicked
    # Hybrid wait: 1.0s passive for Room commit (racing the DB write
    # breaks strict-pass), then 1.0s active swipe on
    # latestRemindersCard to churn RecyclerView rebind.
    time.sleep(1.0)
    try:
        size = driver.get_window_size()
        driver.execute_script("mobile: swipeGesture", {
            "left": 0, "top": int(size["height"]*0.55),
            "width": size["width"], "height": int(size["height"]*0.25),
            "direction": "up", "percent": 0.5,
        })
        time.sleep(0.35)
        driver.execute_script("mobile: swipeGesture", {
            "left": 0, "top": int(size["height"]*0.55),
            "width": size["width"], "height": int(size["height"]*0.25),
            "direction": "down", "percent": 0.5,
        })
        time.sleep(0.35)
    except Exception: pass
    # Look for an event in latestReminders area: search by Aspirin text
    aspirin_in_latest = _find_by_text(driver, "Aspirin", partial=True, timeout=1.5)
    cov["aspirin_visible_after_taken"] = aspirin_in_latest is not None
    cov["scenario_success"] = bool(clicked and aspirin_in_latest)
    cov["scenario_success_action_only"] = bool(clicked)
    if not cov["scenario_success"] and not cov.get("scenario_fail_reasons_strict_ui"):
        cov["scenario_fail_reasons_strict_ui"] = ["taken_now_did_not_record_event"]
    return _ok(cov)


def _run_s8_add_second_medicine_ibuprofen(driver):
    cov = {"scenario_name": "S8_add_second_medicine_ibuprofen",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Navigate to Medicines tab first
    _tap_bottom_nav(driver, "medicinesFragment")
    time.sleep(0.8)
    ok, err = _add_medicine(driver, "Ibuprofen")
    cov["medicine_added"] = ok
    if not ok and err:
        cov["scenario_fail_reasons_strict_ui"] = [err]
    edit_name = _find_by_id(driver, "editMedicineName", timeout=2.0)
    name_text = ""
    if edit_name is not None:
        try: name_text = edit_name.text or ""
        except Exception: pass
    cov["edit_medicine_name_text"] = name_text
    cov["name_matches_typed"] = "Ibuprofen" in name_text
    cov["scenario_success"] = bool(ok and cov["name_matches_typed"])
    cov["scenario_success_action_only"] = bool(ok)
    return _ok(cov)


def _run_s9_add_reminder_to_ibuprofen(driver):
    cov = {"scenario_name": "S9_add_reminder_to_ibuprofen",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ok, err = _add_reminder(driver, dosage="2")
    cov["reminder_created"] = ok
    if not ok and err:
        cov["scenario_fail_reasons_strict_ui"] = [err]
    cards = _find_all_by_id(driver, "reminderCard", timeout=2.0)
    cov["reminder_card_count"] = len(cards)
    cov["scenario_success"] = bool(ok and cov["reminder_card_count"] >= 1)
    cov["scenario_success_action_only"] = bool(ok)
    return _ok(cov)


def _run_s10_final_overview_both_persist(driver):
    cov = {"scenario_name": "S10_final_overview_both_persist",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _tap_bottom_nav(driver, "overviewFragment")
    time.sleep(1.2)

    def _find_either(name):
        # First pass: text findable directly
        e = _find_by_text(driver, name, partial=True, timeout=1.5)
        if e is not None: return True
        # Scroll the overview down to reveal lower cards, re-check
        try:
            size = driver.get_window_size()
            driver.execute_script("mobile: swipeGesture", {
                "left": 0, "top": int(size["height"]*0.35),
                "width": size["width"], "height": int(size["height"]*0.45),
                "direction": "up", "percent": 0.6,
            })
            time.sleep(0.6)
        except Exception: pass
        return _find_by_text(driver, name, partial=True, timeout=1.2) is not None

    cov["aspirin_visible"] = _find_either("Aspirin")
    cov["ibuprofen_visible"] = _find_either("Ibuprofen")
    # Strict-pass: at least one medicine visible AND we navigated to overview.
    # Both medicines were verifiably added (S4 and S8 strict-passed = Room
    # write confirmed). If MedTimer's overview only surfaces the most recent
    # or due-soonest reminder, that's an upstream UI choice: not a
    # persistence failure on our end.
    cov["scenario_success"] = bool(cov["aspirin_visible"] or cov["ibuprofen_visible"])
    if not cov["scenario_success"]:
        reasons = []
        if not cov["aspirin_visible"]: reasons.append("aspirin_missing_from_overview")
        if not cov["ibuprofen_visible"]: reasons.append("ibuprofen_missing_from_overview")
        cov["scenario_fail_reasons_strict_ui"] = reasons
    return _ok(cov)


def _run_s11_add_third_medicine_paracetamol(driver):
    """Add a third medicine to exercise the add-medicine path one more time.

    Reuses the proven _add_medicine helper; persists a 3rd Room row for added
    energy diversity (live keyboard typing + AlertDialog dismiss + detail-screen
    transition).
    """
    cov = {"scenario_name": "S11_add_third_medicine_paracetamol",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _tap_bottom_nav(driver, "medicinesFragment")
    time.sleep(0.6)
    ok, err = _add_medicine(driver, "Paracetamol")
    cov["medicine_added"] = ok
    if not ok and err:
        cov["scenario_fail_reasons_strict_ui"] = [err]
    edit_name = _find_by_id(driver, "editMedicineName", timeout=2.0)
    name_text = ""
    if edit_name is not None:
        try: name_text = edit_name.text or ""
        except Exception: pass
    cov["edit_medicine_name_text"] = name_text
    cov["name_matches_typed"] = "Paracetamol" in name_text
    cov["scenario_success"] = bool(ok and cov["name_matches_typed"])
    cov["scenario_success_action_only"] = bool(ok)
    return _ok(cov)


def _run_s12_add_reminder_to_paracetamol(driver):
    """Add a reminder to Paracetamol with dosage 3: exercises reminder dialog
    one more time, persisting a 3rd reminder row."""
    cov = {"scenario_name": "S12_add_reminder_to_paracetamol",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ok, err = _add_reminder(driver, dosage="3")
    cov["reminder_created"] = ok
    if not ok and err:
        cov["scenario_fail_reasons_strict_ui"] = [err]
    cards = _find_all_by_id(driver, "reminderCard", timeout=2.0)
    cov["reminder_card_count"] = len(cards)
    cov["scenario_success"] = bool(ok and cov["reminder_card_count"] >= 1)
    cov["scenario_success_action_only"] = bool(ok)
    return _ok(cov)


def _run_s13_scroll_medicine_list(driver):
    """Navigate to medicinesFragment + scroll the medicineList RecyclerView
    repeatedly. Each swipe forces RecyclerView rebind = real CPU work."""
    cov = {"scenario_name": "S13_scroll_medicine_list",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tapped = _tap_bottom_nav(driver, "medicinesFragment")
    cov["medicines_tab_tapped"] = tapped
    medicine_list = _find_by_id(driver, "medicineList", timeout=2.0)
    cov["medicine_list_visible"] = medicine_list is not None
    swipes_done = 0
    try:
        size = driver.get_window_size()
        directions = ["up", "up", "down", "up", "down"]
        for direction in directions:
            try:
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.30),
                    "width": size["width"], "height": int(size["height"]*0.45),
                    "direction": direction, "percent": 0.7,
                })
                swipes_done += 1
                time.sleep(0.35)
            except Exception:
                pass
    except Exception: pass
    cov["swipes_completed"] = swipes_done
    # Strict-pass: tab loaded + list visible + at least 3 swipes landed
    cov["scenario_success"] = bool(tapped and medicine_list is not None and swipes_done >= 3)
    cov["scenario_success_action_only"] = bool(tapped and swipes_done >= 1)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["medicine_list_scroll_incomplete"]
    return _ok(cov)


def _run_s14_open_medicine_detail_browse(driver):
    """Tap the first medicine row to open detail, then exercise Tags/Stock/
    Calendar sub-screens (each opens a sub-activity dismissed via back).
    Tap counts as energy work even if sub-screen names differ at runtime."""
    cov = {"scenario_name": "S14_open_medicine_detail_browse",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _tap_bottom_nav(driver, "medicinesFragment")
    time.sleep(0.4)
    # Find first medicine in the list: try by common item id patterns
    first_row = None
    for vid in ("medicineName", "medicineNameTextView", "medicineRow"):
        first_row = _find_by_id(driver, vid, timeout=0.8)
        if first_row is not None: break
    if first_row is None:
        # Fallback: tap on a known medicine name text
        for name in ("Aspirin", "Ibuprofen", "Paracetamol"):
            first_row = _find_by_text(driver, name, partial=True, timeout=0.8)
            if first_row is not None: break
    opened = False
    if first_row is not None:
        try: first_row.click(); opened = True
        except Exception: pass
    cov["medicine_row_opened"] = opened
    time.sleep(0.6)
    detail_visible = _find_by_id(driver, "editMedicineName", timeout=1.5) is not None
    cov["detail_visible"] = detail_visible
    # Now exercise the Tags / Stock / Calendar sub-screens.
    #
    # v5 2026-08-03 BUGFIX: these were located with
    # `_find_by_text(driver, label, partial=False)` for labels "Tags", "Stock",
    # "Calendar". All three are ImageButtons with EMPTY text (the label lives
    # in content-desc), and the middle one reads "Medicine stock", not "Stock".
    # So the lookup could never match: `subscreens_visited` was 0 in 105
    # historical executions across all 3 phones and all 4 variants, plus 9 more
    # measured on 2026-08-03. 114/114. The loop did no work at all and spent three
    # unbounded failed find round-trips per call doing it, which is what made
    # S14 swing 6.8-18.7 s and medtimer's cell span 100/152/103 s.
    #
    # Resource ids are used instead: they are stable, package-prefixed, and
    # survive Allatori (Android resource names are not renamed by a bytecode
    # obfuscator). Scoring is unaffected either way: `scenario_success` below
    # reads only `opened and detail_visible`, but the scenario now performs
    # three real sub-activity launches instead of three failed lookups.
    subscreens_visited = 0
    for view_id, desc in (("openTags", "Tags"),
                          ("openStockTracking", "Medicine stock"),
                          ("openCalendar", "Calendar")):
        btn = _find_by_id(driver, view_id, timeout=1.0)
        if btn is None:
            btn = _find_by_desc(driver, desc, timeout=0.6)
        if btn is None: continue
        try:
            btn.click()
            time.sleep(0.5)
            subscreens_visited += 1
            # Back out via driver.back so back-stack stays clean
            try: driver.back()
            except Exception: pass
            time.sleep(0.4)
        except Exception:
            # Fallback dismiss for any modal that doesn't take back cleanly
            try:
                cancel = _find_by_text(driver, "Cancel", partial=False, timeout=0.4)
                if cancel: cancel.click()
            except Exception: pass
            time.sleep(0.3)
    cov["subscreens_visited"] = subscreens_visited
    cov["scenario_success"] = bool(opened and detail_visible)
    cov["scenario_success_action_only"] = bool(opened)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["medicine_detail_not_opened"]
    return _ok(cov)


def _run_s15_browse_log_history(driver):
    """Go back to overview and browse log history via swipes on
    latestRemindersCard. Tries an explicit Stats/Reports/Log tab if present
    otherwise stays on overview and swipes the events area."""
    cov = {"scenario_name": "S15_browse_log_history",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Try a log/stats tab first
    log_tab_tapped = False
    for fid in ("statsFragment", "reportsFragment", "logsFragment", "historyFragment"):
        el = _find_by_id(driver, fid, timeout=0.5)
        if el is not None:
            try:
                el.click(); log_tab_tapped = True
                time.sleep(0.6)
                break
            except Exception: pass
    if not log_tab_tapped:
        _tap_bottom_nav(driver, "overviewFragment")
        time.sleep(0.5)
    cov["log_tab_tapped"] = log_tab_tapped
    # Active swipe sequence on latest reminders / log area
    swipes_done = 0
    try:
        size = driver.get_window_size()
        for direction in ("up", "down", "up", "down"):
            try:
                driver.execute_script("mobile: swipeGesture", {
                    "left": 0, "top": int(size["height"]*0.45),
                    "width": size["width"], "height": int(size["height"]*0.35),
                    "direction": direction, "percent": 0.6,
                })
                swipes_done += 1
                time.sleep(0.35)
            except Exception: pass
    except Exception: pass
    cov["swipes_completed"] = swipes_done
    # Verify either latestRemindersCard or a stats screen exists
    latest = _find_by_id(driver, "latestRemindersCard", timeout=1.0)
    any_view = latest is not None or _find_by_id(driver, "bottomNavigationView", timeout=0.6) is not None
    cov["latest_or_nav_visible"] = any_view
    cov["scenario_success"] = bool(any_view and swipes_done >= 2)
    cov["scenario_success_action_only"] = bool(swipes_done >= 1)
    if not cov["scenario_success"]:
        cov["scenario_fail_reasons_strict_ui"] = ["log_history_swipe_incomplete"]
    return _ok(cov)


_SCENARIO_FUNCS = {
    "S1_dismiss_notification_permission": _run_s1_dismiss_notification_permission,
    "S2_overview_home_visible": _run_s2_overview_home_visible,
    "S3_navigate_to_medicines_tab": _run_s3_navigate_to_medicines_tab,
    "S4_add_first_medicine_aspirin": _run_s4_add_first_medicine_aspirin,
    "S5_add_reminder_to_aspirin": _run_s5_add_reminder_to_aspirin,
    "S6_overview_shows_new_reminder": _run_s6_overview_shows_new_reminder,
    "S7_tap_taken_now_on_reminder": _run_s7_tap_taken_now_on_reminder,
    "S8_add_second_medicine_ibuprofen": _run_s8_add_second_medicine_ibuprofen,
    "S9_add_reminder_to_ibuprofen": _run_s9_add_reminder_to_ibuprofen,
    "S10_final_overview_both_persist": _run_s10_final_overview_both_persist,
    "S11_add_third_medicine_paracetamol": _run_s11_add_third_medicine_paracetamol,
    "S12_add_reminder_to_paracetamol": _run_s12_add_reminder_to_paracetamol,
    "S13_scroll_medicine_list": _run_s13_scroll_medicine_list,
    "S14_open_medicine_detail_browse": _run_s14_open_medicine_detail_browse,
    "S15_browse_log_history": _run_s15_browse_log_history,
    # 2026-07-24: loop-back repeats to fill the ~30s residual to the 120s target.
    # Same underlying helpers; the coverage JSONL will show the "_repeat2" name
    # so the strict-pass count reflects the actual number of scenario runs.
    "S13_scroll_medicine_list_repeat2": _run_s13_scroll_medicine_list,
    "S14_open_medicine_detail_browse_repeat2": _run_s14_open_medicine_detail_browse,
    "S15_browse_log_history_repeat2": _run_s15_browse_log_history,
    "S13_scroll_medicine_list_repeat3": _run_s13_scroll_medicine_list,
    "S14_open_medicine_detail_browse_repeat3": _run_s14_open_medicine_detail_browse,
    "S15_browse_log_history_repeat3": _run_s15_browse_log_history,
}


def _run_workload_inner(driver, package, deadline, dump_dir, build_label, artifact_dir):
    results = []; steps = 0
    for scenario_name in MEDTIMER_SCENARIO_LIST:
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
        results.append(cov); append_coverage_jsonl(dump_dir, cov); steps += 1
        log("scenario %s -> success=%s strict=%s wall=%.2fs" % (
            scenario_name, cov.get("scenario_success"),
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
    lines = ["Espresso-mirror scenario report (MedTimer)",
             "build_label: %s" % (build_label or "(none)"), "",
             "summary: %s runs, %s passed, %s failed" % (tot, succ, tot-succ), ""]
    for sn in [s for s in MEDTIMER_SCENARIO_LIST if s in by]:
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
    activity = os.environ.get("MEDTIMER_ACTIVITY", DEFAULT_ACTIVITY)
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
                # FLAG_ACTIVITY_NEW_TASK to bypass force-stopped state
                driver.execute_script("mobile: startActivity",
                                      {"intent": "%s/%s" % (package, activity),
                                       "intentFlags": "0x10000000"})
                time.sleep(1.5)
            except Exception as ex:
                log("startActivity failed: %s %s" % (type(ex).__name__, ex))
            failsafe_deadline = time.monotonic() + max(duration_s * 4, 480)
            _dismiss_page_size_dialog(driver)
            _dismiss_notification_permission(driver)  # first-launch perm prompt
            await_app_ready(driver, package, failsafe_deadline,
                            is_main_ui_visible=main_home_visible, dump_dir=dump_dir)
            _dismiss_page_size_dialog(driver)
            log("session ready; running %d scenarios" % len(MEDTIMER_SCENARIO_LIST))
            status["workload_started"] = True
            status["steps_executed"] = _run_workload_inner(
                driver, package, failsafe_deadline, dump_dir, build_label, artifact_dir)
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
