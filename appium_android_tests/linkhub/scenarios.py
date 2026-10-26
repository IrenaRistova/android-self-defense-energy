"""
Per-app LinkHub workload (AmrDeveloper ``com.amrdeveloper.linkhub``).

LinkHub is a personal link / folder organizer: a local-first bookmark manager that
stores folder + link metadata in Room (SQLite). The manifest declares NO ``INTERNET``
permission (the only network-flavored helper, ``generateLinkInfo``, is a pure URL
parser; it does not fetch the page). The app's only network surface is "receive a
shared URL via :class:`Intent.ACTION_SEND` and pre-populate the LinkFragment form".

**Energy axis statement.** SQLite IO via Room (CRUD on Folder + Link entities) +
Compose recomposition of folder + link lists; variant comparison is dominated by
storage IO, recomposition, and per-tap navigation animations. There is no audio,
no sensor, no continuous CPU loop: energy delta vs idle on Pixel 3 baseline is
expected to be moderate (small per scenario, additive across the locked N-scenario
list because every scenario triggers Room insert/update/query + a list rebind).

UI-tech change (2026-05-12 re-derive for v2.1.0):
the v1.6.4 View-based layout (RecyclerView + FAB + AppCompat Toolbar +
``<package>:id/...`` EditTexts + SwipeToDismiss + Undo snackbar) was replaced by
Jetpack Compose Material3 in v2.1.0. **No ``Modifier.testTag(...)`` is used
anywhere in the v2.1.0 Compose source**, so the ``_lib._find_id_with_compose_fallback``
cascade Layer 2 (bare testTag resource-id) does not fire for any LinkHub view-id.
This file's selectors are therefore rewritten to use:

- ``contentDescription`` strings for icons / FABs / save+delete row buttons
  (the Compose Icon composables consistently set ``contentDescription``,
  e.g. ``"Save"``, ``"Delete"``, ``"Options"``, ``"Search"``).
- Visible text labels for DropdownMenu items (``"New Link"``, ``"New Folder"``,
  ``"Folders"``, etc.) and for percentage-chip / OutlinedTextField labels.
- ``className("android.widget.EditText").instance(N)`` for the fragment-positional
  ``OutlinedTextField`` inputs (Compose surfaces each ``OutlinedTextField`` as an
  ``android.widget.EditText`` to UiAutomator2).

Workload model (the fixed-N scenario contract; see CONVENTIONS.md §6 + §6.5):
this module exposes ONE locked scenario list ``LINKHUB_SCENARIO_LIST`` of length
``N``; ``run_workload`` runs it end-to-end, records each scenario's wall time as
a measured output, and exits. There is no time-box and no idle filler. The
JSON ``duration`` becomes a profiler-window failsafe ceiling, not a workload
length.

Env-var contract owned here (others are documented in the relevant ``_lib`` module):

- ``LINKHUB_ACTIVITY``: activity to launch (default
  ``.ui.MainActivity``).
- ``APPIUM_BUILD_LABEL``: free-form tag (``baseline`` /
  ``r8`` / ``allatori`` / ``bangcle``) included in JSONL rows + the scenario report.
- ``APPIUM_USE_VIEW_IDS``: informational only; LinkHub
  v2.1.0 has NO ``<package>:id/...`` for the Compose surfaces we drive: every
  selector is content-desc / text / className+instance.
- ``LINKHUB_SHARE_LINK_URL_SUBSTR``: override the URL fired by the
  ``shareLinkReceiveGeneral`` mirror scenario (default ``https://amr.com``; matches
  upstream ``ShareLinkTest.generalLink`` exactly so the strict-UI assertion is the
  literal post-condition of the Espresso test).
- ``LINKHUB_SHARE_LINK_TITLE_SUBSTR``: expected title-field text after
  the share intent (default ``amr Link``; mirrors ``util/LinkInfoGenerator.kt``
  non-special-domain branch). Must be kept in sync with
  ``LINKHUB_SHARE_LINK_URL_SUBSTR`` if overridden.
- ``LINKHUB_SHARE_LINK_SUBTITLE_SUBSTR``: expected subtitle-field text
  (default ``Link from amr website``).
- ``LINKHUB_NEW_LINK_LABEL``: text of the toolbar overflow
  menu item that navigates to the LinkScreen (default ``New Link``).
- ``LINKHUB_NEW_FOLDER_LABEL``: text of the toolbar overflow
  menu item that navigates to the FolderScreen (default ``New Folder``).
- ``LINKHUB_OPTIONS_DESC``: contentDescription of the
  toolbar's overflow icon (default ``Options``).
- ``LINKHUB_SAVE_DESC``: contentDescription of the Save
  icon inside ``SaveDeleteActionsRow`` (default ``Save``).
- ``LINKHUB_DELETE_DESC``: contentDescription of the Delete
  icon inside ``SaveDeleteActionsRow`` (default ``Delete``).
- ``LINKHUB_SEARCH_DESC``: contentDescription of the search
  icon inside the toolbar SearchBar (default ``Search``).
- ``LINKHUB_LINK_TITLE_LABEL``: visible label of the title
  ``OutlinedTextField`` on the Link form (default ``Title``).
- ``LINKHUB_LINK_URL_LABEL``: visible label of the URL
  ``OutlinedTextField`` on the Link form (default ``Url``).
- ``LINKHUB_FOLDER_NAME_LABEL``: visible label of the Name
  ``OutlinedTextField`` on the Folder form (default ``Name``).
- ``LINKHUB_RUN_ALL_SCENARIOS``: opt-in calibration mode, which runs the
  full candidate list (currently identical to ``LINKHUB_SCENARIO_LIST``). Reserved
  for future expansion.

Provenance summary (as recorded when the module was written; the list has changed since):
``15 upstream @Test methods → 7 Appium scenarios (1 mirror / 2 inspired / 4
exploration)``. The 12 DAO-only tests in ``FolderDaoTest.kt`` + ``LinkDaoTest.kt``
are not mirrorable as UI scenarios (they exercise Room directly, not the UI
surface) and the 2 additional ``ShareLinkTest`` variants beyond ``generalLink``
fold into the same mirror scenario's intent, so both are left unmirrored.
**2026-05-12 update**: the original
``swipeDeleteAndUndo`` exploration scenario was replaced by ``longClickEditLink``
because v2.1.0's Compose UI removed the swipe-to-dismiss + Undo-snackbar pattern
entirely (Links / Folders use a long-click DropdownMenu now; the only Delete UI
lives inside the per-entity Form via ``SaveDeleteActionsRow``).

Artifacts produced (under ``paths.OUTPUT_DIR`` when Android Runner is the parent):

- ``appium_workload_coverage.jsonl``
- ``linkhub_scenario_report.{json,txt}``
- ``appium_status.json``
- ``appium_ui_dumps/*.xml`` (forensics, capped by ``APPIUM_UI_DUMP_MAX``)
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
    safe_click_description_contains,
    safe_click_text_contains,
    save_page_source,
    start_workload_thread_and_block_until_done,
    write_appium_status,
    write_scenario_report,
)
from .._lib.coverage import reset_dump_counter
from .scoring import score_scenario


# ---------------------------------------------------------------------------
# Per-app calibration: locked scenario list (2026-05-08 contract)
# ---------------------------------------------------------------------------
#
# LinkHub is a moderate-energy app (Room IO + Compose recomposition, no audio /
# sensor). The scenario list intentionally pairs a few CRUD-heavy explorations
# (createMultiple*) with the data-query path (searchKeywordFilter) and the
# edit-from-list path (longClickEditLink): those are the surfaces where R8 /
# Allatori / Bangcle either inline / rewrite / hook into the most code per second
# and where their variant delta should be visible against the moderate baseline.
#
# Per-scenario wall times: see the 2026-08-03 recalibration note under the list;
# CALIBRATION.md holds the earlier estimates (2026-05-12).
LINKHUB_SCENARIO_LIST: tuple[str, ...] = (
    "shareLinkReceiveGeneral",      # ~14 s: fire ACTION_SEND + verify form prepop
    "createMultipleFolders",        # ~37 s: 3 folders rapidly (SQLite IO + LazyGrid)
    "createMultipleLinks",          # ~35 s: 2 links rapidly (SQLite IO + LazyColumn)
    "searchKeywordFilter",          # ~16 s: toolbar SearchBar expand + 4 queries
    "longClickEditLink",            # ~12 s: long-press a link card → Edit → modify → save
    # ─────────────────────────────────────────────────────────────────────────
    # v5 2026-08-03 RECALIBRATION: measured, not estimated.
    #
    # The pre-v5 list ran 7 scenarios at 5+5 entities and measured 220-272 s of
    # real scenario wall on batch1 (n=19 complete cells, all variants), i.e.
    # baseline min 245.9 s: roughly double the [110, 130] s calibration window.
    # The reported ``duration_workload_s`` was higher still (264-345 s) because
    # of the ``cov["ts"]`` bug fixed in this same change; see run_linkhub_workload.
    #
    # Replaying every batch1 cell against candidate configurations showed the
    # window is NOT reachable at N=7: the cheapest honest N=7 config (2 folders +
    # 2 links, "multiple" ≥ 2) still floors at 132 s baseline / 126 s all-variant.
    # DROPPED ``createFolderViaUi`` and ``createLinkViaUi``: each is byte-for-byte
    # one iteration of the createMultiple* loop that follows it (12.3 s / 17.3 s
    # measured, vs 12.28 s / 17.32 s per loop item), so removing them costs no
    # distinct UI coverage: their upstream intent (FolderDaoTest.insert_
    # uniqueFolder / LinkDaoTest.insert_uniqueLink) is re-attributed to
    # createMultipleFolders / createMultipleLinks.
    #
    # Chosen config = 5 scenarios, 3 folders + 2 links. Batch1 replay, baseline
    # cells: min 114.0 / median 131.4 / max 136.8 s. MIN is the calibration
    # criterion, so the floor is what is pinned; the slower phones (P3, P9) run
    # long by ~10-20 % and that spread is device, not scenario code.
    # Locked: do not reorder or change without a new calibration run
    # (CONVENTIONS.md §6.5).
)


def _linkhub_defaults() -> dict[str, str]:
    """Default selector substrings + activity targets for LinkHub v2.1.0 Compose UI.

    Each entry can be overridden via the corresponding ``LINKHUB_*`` env var so
    non-English builds (or future forks) can reuse this workload without code
    changes. Defaults are taken directly from the v2.1.0 Compose source under
    ``com/amrdeveloper/linkhub/ui/``: every selector substring is grep-anchored
    to a literal in ``Toolbar.kt`` / ``SaveDeleteActionsRow.kt`` /
    ``SearchScreen.kt`` / ``LinkScreen.kt`` / ``FolderScreen.kt``.
    """
    return {
        # Toolbar overflow menu (Toolbar.kt:90-178)
        "options_desc": os.environ.get("LINKHUB_OPTIONS_DESC", "Options"),
        "new_link_label": os.environ.get("LINKHUB_NEW_LINK_LABEL", "New Link"),
        "new_folder_label": os.environ.get("LINKHUB_NEW_FOLDER_LABEL", "New Folder"),
        # SaveDeleteActionsRow (SaveDeleteActionsRow.kt:29,40)
        "save_desc": os.environ.get("LINKHUB_SAVE_DESC", "Save"),
        "delete_desc": os.environ.get("LINKHUB_DELETE_DESC", "Delete"),
        # SearchBar leadingIcon (SearchScreen.kt:94)
        "search_desc": os.environ.get("LINKHUB_SEARCH_DESC", "Search"),
        # OutlinedTextField labels (LinkScreen.kt:131,153,175 + FolderScreen.kt:129)
        "link_title_label": os.environ.get("LINKHUB_LINK_TITLE_LABEL", "Title"),
        "link_url_label": os.environ.get("LINKHUB_LINK_URL_LABEL", "Url"),
        "folder_name_label": os.environ.get("LINKHUB_FOLDER_NAME_LABEL", "Name"),
        # Long-click menu item (Links.kt:167-172)
        "edit_label": os.environ.get("LINKHUB_EDIT_LABEL", "Edit"),
        # ShareLinkTest mirror constants (util/LinkInfoGenerator.kt non-special-domain branch)
        "share_url": os.environ.get("LINKHUB_SHARE_LINK_URL_SUBSTR", "https://amr.com"),
        "share_title": os.environ.get("LINKHUB_SHARE_LINK_TITLE_SUBSTR", "amr Link"),
        "share_subtitle": os.environ.get(
            "LINKHUB_SHARE_LINK_SUBTITLE_SUBSTR",
            "Link from amr website",
        ),
        "main_activity": os.environ.get("LINKHUB_ACTIVITY", ".ui.MainActivity"),
    }


# ---------------------------------------------------------------------------
# Compose selector helpers (no view-IDs in v2.1.0)
# ---------------------------------------------------------------------------


def _find_edit_text_by_instance(driver: Any, instance: int) -> Any:
    """Locate the ``instance``-th ``android.widget.EditText`` in the current UI tree.

    Compose ``OutlinedTextField`` composables surface to UiAutomator2 as
    ``android.widget.EditText`` nodes in DFS order. The Link form has 3 of them
    (Title / Url / SubTitle); the Folder form has 1 (Name); the SearchBar
    contributes one when expanded. Caller must know which fragment is up so the
    instance index is unambiguous.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    sel = 'new UiSelector().className("android.widget.EditText").instance(%d)' % instance
    return driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)


def _read_edit_text_instance(driver: Any, instance: int) -> str:
    """Visible text inside the ``instance``-th ``EditText`` (empty on any failure)."""
    try:
        el = _find_edit_text_by_instance(driver, instance)
        return (el.text or "").strip()
    except Exception:
        return ""


def _hide_keyboard_safe(driver: Any) -> None:
    try:
        driver.hide_keyboard()
    except Exception:
        pass


def _clear_edit_text_instance(driver: Any, instance: int) -> bool:
    """Click + clear the field; DEL-keycode fallback for Compose's quirky ``clear()``.

    Compose's OutlinedTextField sometimes ignores a single ``el.clear()`` call (the
    underlying Compose state is not always invalidated when UiAutomator clears the
    text), so we follow up with a few KEYCODE_DEL presses to clear residue
    character-by-character. Returns True if the field is empty afterwards.
    """
    try:
        el = _find_edit_text_by_instance(driver, instance)
        try:
            el.click()
        except Exception:
            pass
        time.sleep(0.15)
        try:
            el.clear()
        except Exception as ex:
            log("clear() raised on EditText instance(%d): %s %s (continuing with DEL)"
                % (instance, type(ex).__name__, ex))
        time.sleep(0.1)
        for _ in range(16):
            try:
                el2 = _find_edit_text_by_instance(driver, instance)
                cur = (el2.text or "")
            except Exception:
                break
            if cur == "":
                break
            try:
                driver.press_keycode(67)  # KEYCODE_DEL
            except Exception:
                break
            time.sleep(0.04)
        return _read_edit_text_instance(driver, instance) == ""
    except Exception as ex:
        log("clear EditText instance(%d) failed: %s %s" % (instance, type(ex).__name__, ex))
        return False


def _type_edit_text_instance(driver: Any, instance: int, text: str) -> bool:
    """Clear + ``send_keys(text)`` into the ``instance``-th EditText. Returns True on success."""
    try:
        _clear_edit_text_instance(driver, instance)
        el = _find_edit_text_by_instance(driver, instance)
        el.send_keys(text)
        log("typed %r into EditText instance(%d)" % (text, instance))
        _hide_keyboard_safe(driver)
        time.sleep(0.35)
        return True
    except Exception as ex:
        log("_type_edit_text_instance(%d, %r) failed: %s %s"
            % (instance, text, type(ex).__name__, ex))
        return False


def _record_typed_observed(
    cov: dict[str, Any],
    result_key: str,
    driver: Any,
    instance: int,
    text: str,
) -> None:
    """Type ``text`` into ``EditText.instance(instance)`` and record action / observed / strict.

    Compose-aware analogue of :func:`_lib.scoring.record_numeric_edit_observed`,
    using className+instance instead of namespaced view-id. Strict matches when the
    field text equals the typed value after stripping the soft-keyboard's
    whitespace artefacts. Writes three keys per the standard
    ``<key>`` / ``<key>_observed`` / ``<key>_strict`` contract.
    """
    ok = _type_edit_text_instance(driver, instance, text)
    observed = _read_edit_text_instance(driver, instance)
    cov[result_key] = ok
    cov["%s_observed" % result_key] = observed[:160]
    cov["%s_strict" % result_key] = bool(ok and observed.strip() == text.strip())


def _safe_call(fn: Callable[[], Any], label: str) -> None:
    try:
        fn()
    except Exception as ex:
        log("%s failed (%s): %s" % (label, type(ex).__name__, ex))


def _back(driver: Any) -> None:
    """Press the device back button: wraps ``driver.back()`` swallowing exceptions."""
    _safe_call(lambda: driver.back(), "driver.back()")


def _swipe_list_active(driver: Any, direction: str = "down", duration_ms: int = 250) -> None:
    """Fire a swipe gesture over the home LazyColumn/LazyGrid to exercise recomposition.

    Used as active filler in place of idle ``time.sleep``: converts a "wait for
    animation" gap into measurable Compose work (list rebind + scroll fling). Pure
    no-op on any exception so it never breaks a scenario.
    """
    try:
        size = driver.get_window_size()
        w = int(size.get("width", 1080))
        h = int(size.get("height", 1920))
    except Exception:
        w, h = 1080, 1920
    cx = w // 2
    try:
        if direction == "down":
            driver.swipe(cx, int(h * 0.70), cx, int(h * 0.30), duration_ms)
        else:
            driver.swipe(cx, int(h * 0.30), cx, int(h * 0.70), duration_ms)
    except Exception:
        pass


def _poll_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.05) -> bool:
    """Poll ``predicate`` every ``interval`` seconds until True or ``timeout`` elapses.

    Replaces fixed idle ``time.sleep(N)`` for UI-readiness waits: exits as soon
    as the condition is met so we don't burn idle time. Always honors at least
    one check.
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            if predicate():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _active_wait_home(
    driver: Any,
    package: str,
    d: dict[str, str],
    budget_s: float,
) -> bool:
    """Wait for the home screen up to ``budget_s`` seconds, scrolling the list actively.

    Replaces an idle ``time.sleep`` post-save wait: poll ``main_home_visible`` and,
    while polling, fire a LazyColumn/LazyGrid swipe pair (down, then up) which
    exercises Compose recomposition + ``HomeViewModel`` paging rebind without
    burning idle wall-time. Returns True if home was seen before the budget ran
    out.
    """
    deadline = time.monotonic() + max(budget_s, 0.0)
    seen = False
    swiped = 0
    while time.monotonic() < deadline:
        if main_home_visible(driver, package, d):
            seen = True
            break
        time.sleep(0.05)
    # Whether or not we saw home early, spend remaining budget doing active list
    # work so the scenario fills its measurement window with real Compose work.
    while time.monotonic() < deadline:
        _swipe_list_active(driver, "down" if swiped % 2 == 0 else "up", duration_ms=220)
        swiped += 1
        if not seen and main_home_visible(driver, package, d):
            seen = True
    return seen or main_home_visible(driver, package, d)


def main_home_visible(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Detect the home screen by looking for the toolbar's Options icon.

    The Options icon (contentDescription="Options") is rendered by
    :func:`OptionsMenuWithDropDownActions` in ``Toolbar.kt`` and is present on the
    home screen as long as the search bar is collapsed. We accept either the
    Options icon OR the LinkhubToolbar's app-icon (contentDescription="LinkHub")
    as a "we're on home / Explorer / FolderList" signal: they share the same
    Toolbar composable.
    """
    esc = d["options_desc"].replace('"', '\\"')
    if element_present_uiautomator(
        driver,
        'new UiSelector().descriptionContains("%s")' % esc,
        timeout=2.0,
    ):
        return True
    # Fallback: the LinkHub-branded app icon in the toolbar (Toolbar.kt:60-66).
    return element_present_uiautomator(
        driver,
        'new UiSelector().descriptionContains("LinkHub")',
        timeout=1.5,
    )


def link_form_visible(driver: Any, package: str, d: dict[str, str]) -> bool:
    """The LinkScreen is up: detect via either the Title label or the Save icon.

    Two independent signals so a slow first recomposition doesn't false-negative:
    the OutlinedTextField label "Title" (LinkScreen.kt:131) and the Save
    OutlinedIconButton's icon contentDescription "Save"
    (SaveDeleteActionsRow.kt:29) which is present on both LinkScreen and
    FolderScreen.
    """
    title_label = d["link_title_label"].replace('"', '\\"')
    if element_present_uiautomator(
        driver,
        'new UiSelector().text("%s")' % title_label,
        timeout=2.0,
    ):
        return True
    save_desc = d["save_desc"].replace('"', '\\"')
    return element_present_uiautomator(
        driver,
        'new UiSelector().descriptionContains("%s")' % save_desc,
        timeout=1.5,
    )


def folder_form_visible(driver: Any, package: str, d: dict[str, str]) -> bool:
    """The FolderScreen is up: detect via the Name label or the FolderHeader Icon.

    The FolderScreen has only ONE OutlinedTextField (the Name field) whereas the
    LinkScreen has three; the Name label "Name" (FolderScreen.kt:129) is the
    most stable anchor. Falls back to the FolderHeaderIcon's contentDescription
    "Folder Icon" (FolderScreen.kt:212-213).
    """
    name_label = d["folder_name_label"].replace('"', '\\"')
    if element_present_uiautomator(
        driver,
        'new UiSelector().text("%s")' % name_label,
        timeout=2.0,
    ):
        return True
    return element_present_uiautomator(
        driver,
        'new UiSelector().descriptionContains("Folder Icon")',
        timeout=1.5,
    )


def _tap_save_action(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Tap the Save OutlinedIconButton (SaveDeleteActionsRow.kt:23-31)."""
    return safe_click_description_contains(driver, d["save_desc"], timeout=8.0)


def _open_overflow_menu(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Tap the toolbar's Options icon to expand the DropdownMenu (Toolbar.kt:91-97)."""
    ensure_foreground(driver, package)
    return safe_click_description_contains(driver, d["options_desc"], timeout=8.0)


# 2026-08-03: waits widened for the slower variants.
#
# createLinkViaUi / createMultipleLinks / longClickEditLink were the last
# unfixed flakes in the cohort: baseline 14-17%, bangcle 28.6%, freerasp 50%,
# allatori clean. allatori only renames (no runtime cost) while bangcle
# decrypts+splices its DEX and freerasp runs RASP checks, so the pattern is a
# timing-marginal wait, not a logic bug. A 1.5 s ceiling for a Compose screen to
# materialize is simply not enough once the app starts slower.
#
# _poll_until returns the instant its predicate is true and safe_click_* return
# as soon as the target is found, so a longer ceiling costs nothing when the app
# is fast. It only extends the path that was previously a DISCARDED cell,
# which is what we want: the protection overhead then lands in the measured
# energy instead of biasing the sample by dropping the slowest runs.
def _open_link_form_via_overflow(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Toolbar overflow → "New Link" DropdownMenuItem → LinkScreen.

    This is the v2.1.0 entrypoint that does NOT require the user to have toggled
    on the optional ``isQuickActionButtonEnabled`` setting (default OFF per
    UiPreferences.kt:108-111). The FAB ``FloatingQuickActionsButton`` is therefore
    NOT a reliable entry point on a fresh install: the toolbar overflow is.
    """
    if not _open_overflow_menu(driver, package, d):
        return False
    time.sleep(0.45)
    ok = safe_click_text_contains(driver, d["new_link_label"], timeout=8.0)
    # Poll for LinkScreen materialize; exits as soon as the form is up.
    _poll_until(lambda: link_form_visible(driver, package, d), timeout=8.0)
    if not link_form_visible(driver, package, d):
        return False
    return ok


def _open_folder_form_via_overflow(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Toolbar overflow → "New Folder" DropdownMenuItem → FolderScreen."""
    if not _open_overflow_menu(driver, package, d):
        return False
    time.sleep(0.45)
    ok = safe_click_text_contains(driver, d["new_folder_label"], timeout=8.0)
    # Poll for FolderScreen materialize; exits as soon as the form is up.
    _poll_until(lambda: folder_form_visible(driver, package, d), timeout=8.0)
    if not folder_form_visible(driver, package, d):
        return False
    return ok


def _navigate_home_via_back(
    driver: Any, package: str, d: dict[str, str], max_back: int = 4,
) -> bool:
    """Press back up to ``max_back`` times until the home screen is detected."""
    for _ in range(max_back):
        if main_home_visible(driver, package, d):
            return True
        _back(driver)
        time.sleep(0.55)
        ensure_foreground(driver, package)
    return main_home_visible(driver, package, d)


def _try_fire_share_intent(
    driver: Any,
    package: str,
    activity: str,
    shared_text: str,
) -> tuple[bool, str]:
    """Send :class:`Intent.ACTION_SEND` to LinkHub's MainActivity with EXTRA_TEXT.

    Tries two transports in order:

    1. ``mobile: shell`` + ``am start`` (most reliable on UiAutomator2; needs the
       Appium server flag ``--relaxed-security`` or
       ``--allow-insecure adb_shell``).
    2. ``mobile: startActivity`` (no server flag required, but extras format for
       ``EXTRA_TEXT`` is dialect-sensitive across Appium versions).

    Returns ``(ok, transport_label_or_error_string)``. Never raises.
    """
    comp_activity = (
        activity if activity.startswith(".") or "/" in activity else ".%s" % activity
    )
    component = "%s/%s" % (package, comp_activity)

    am_args = [
        "start",
        "-W",
        "-a",
        "android.intent.action.SEND",
        "-t",
        "text/plain",
        "--es",
        "android.intent.extra.TEXT",
        shared_text,
        "-n",
        component,
    ]
    try:
        driver.execute_script("mobile: shell", {"command": "am", "args": am_args})
        return True, "mobile_shell"
    except Exception as ex_shell:
        shell_err = "%s: %s" % (type(ex_shell).__name__, ex_shell)
        log("share intent via mobile:shell failed (%s); trying mobile:startActivity"
            % shell_err)

    try:
        driver.execute_script(
            "mobile: startActivity",
            {
                "component": component,
                "action": "android.intent.action.SEND",
                "mimeType": "text/plain",
                "es": "android.intent.extra.TEXT,%s" % shared_text,
            },
        )
        return True, "mobile_startActivity"
    except Exception as ex_act:
        act_err = "%s: %s" % (type(ex_act).__name__, ex_act)
        log("share intent via mobile:startActivity failed (%s)" % act_err)
        return False, "shell:%s | startActivity:%s" % (shell_err, act_err)


# ---------------------------------------------------------------------------
# Per-scenario implementations
# ---------------------------------------------------------------------------


def _run_share_link_receive_general(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Mirror of upstream ``ShareLinkTest.generalLink``.

    Upstream test fires :class:`Intent.ACTION_SEND` with
    ``EXTRA_TEXT="https://amr.com"``, then asserts the LinkScreen form fields
    show the URL, the derived title (``"amr Link"``), and the derived subtitle
    (``"Link from amr website"``). Our black-box analogue does exactly the same
    thing and reads the EditText text back via Appium for the strict-UI pass.
    """
    cov: dict[str, Any] = {
        "espresso_class": "ShareLinkTest",
        "espresso_method": "generalLink",
        "share_url": d["share_url"],
        "share_title_expected": d["share_title"],
        "share_subtitle_expected": d["share_subtitle"],
    }

    activity = d["main_activity"]
    ok_intent, transport = _try_fire_share_intent(driver, package, activity, d["share_url"])
    cov["share_intent_fired"] = ok_intent
    cov["share_intent_transport"] = transport
    if not ok_intent:
        save_page_source(driver, dump_dir, "share_intent_send_failed")
        return cov

    # The intent re-launches MainActivity which navigates to LinkScreen via the
    # navigation graph (handleLinkHubIntent → linkFragment with shared_link arg).
    # Poll for BOTH link_form_visible AND a populated Url field (instance 1) so
    # we exit as soon as the LiveData / StateFlow observers have fired. Ceiling
    # 2.5s: same coverage as the prior 2.2s idle sleep but exits early on hit.
    _poll_until(
        lambda: (
            link_form_visible(driver, package, d)
            and _read_edit_text_instance(driver, 1) != ""
        ),
        timeout=2.5,
        interval=0.1,
    )
    ensure_foreground(driver, package)
    cov["share_link_form_visible"] = link_form_visible(driver, package, d)

    # Compose LinkScreen has 3 OutlinedTextFields in this DFS order:
    #   instance 0 = Title, instance 1 = Url, instance 2 = SubTitle (Optional).
    observed_title = _read_edit_text_instance(driver, 0)
    observed_url = _read_edit_text_instance(driver, 1)
    observed_subtitle = _read_edit_text_instance(driver, 2)
    cov["share_url_observed"] = observed_url[:160]
    cov["share_title_observed"] = observed_title[:160]
    cov["share_subtitle_observed"] = observed_subtitle[:160]
    cov["share_url_strict"] = bool(observed_url.strip() == d["share_url"].strip())
    cov["share_title_strict"] = bool(observed_title.strip() == d["share_title"].strip())
    cov["share_subtitle_strict"] = bool(
        observed_subtitle.strip() == d["share_subtitle"].strip(),
    )

    # Leave the form WITHOUT saving: the form is in transient navigation state.
    # NB: v2.1.0 BackHandler triggers an auto-save if isAutoSavingEnabled() is
    # true (default true per UiPreferences.kt:115). To prevent polluting the DB
    # for downstream scenarios, we drop a long-enough subtitle so the auto-save
    # is harmless: the link is named "amr Link" and will be visible at the top
    # of the link list: downstream scenarios assume nothing about its presence.
    _navigate_home_via_back(driver, package, d, max_back=3)
    cov["returned_to_home"] = main_home_visible(driver, package, d)
    return cov


def _run_create_folder_via_ui(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Inspired by ``FolderDaoTest.insert_uniqueFolder``.

    DAO test inserts ``Folder("Word")`` directly via Room and asserts the row
    landed (``insertResult > -1``). Our UI analogue performs the same insert via
    the toolbar overflow → "New Folder" → FolderScreen → save action: the
    persistence layer call chain is identical
    (``FolderRepository.insertFolder`` → ``FolderDao.insert``) plus the
    additional Compose recomposition layers we want to measure.
    """
    cov: dict[str, Any] = {
        "espresso_class": "FolderDaoTest",
        "espresso_method": "insert_uniqueFolder (UI-inspired)",
        "folder_name_target": "AlphaFolder",
    }

    cov["folder_form_opened"] = _open_folder_form_via_overflow(driver, package, d)
    if not cov["folder_form_opened"]:
        save_page_source(driver, dump_dir, "createFolder_form_open_miss")
        return cov

    # FolderScreen has exactly one OutlinedTextField (the Name field at instance 0).
    _record_typed_observed(cov, "folder_title_typed", driver, 0, "AlphaFolder")
    _hide_keyboard_safe(driver)
    cov["save_clicked"] = _tap_save_action(driver, package, d)
    # Active wait: poll for home + fire LazyGrid swipes to exercise rebind.
    _active_wait_home(driver, package, d, budget_s=1.5)
    ensure_foreground(driver, package)
    cov["returned_to_home"] = main_home_visible(driver, package, d)
    return cov


def _run_create_link_via_ui(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Inspired by ``LinkDaoTest.insert_uniqueLink``.

    DAO test inserts ``Link("title", "subtitle", "url")`` via Room. Our UI
    analogue fills the LinkScreen OutlinedTextFields and taps Save: same
    persistence write through ``LinkRepository.insertLink`` → ``LinkDao.insert``
    plus the UI surface (OutlinedTextFields + URLUtil validation +
    FolderSelector adapter).
    """
    cov: dict[str, Any] = {
        "espresso_class": "LinkDaoTest",
        "espresso_method": "insert_uniqueLink (UI-inspired)",
        "link_title_target": "FirstLink",
        "link_subtitle_target": "FirstSub",
        "link_url_target": "https://example.com/first",
    }

    cov["link_form_opened"] = _open_link_form_via_overflow(driver, package, d)
    if not cov["link_form_opened"]:
        save_page_source(driver, dump_dir, "createLink_form_open_miss")
        return cov

    # LinkScreen has 3 OutlinedTextFields in this DFS order:
    #   instance 0 = Title, instance 1 = Url, instance 2 = SubTitle (Optional).
    _record_typed_observed(cov, "link_title_typed", driver, 0, "FirstLink")
    _record_typed_observed(cov, "link_url_typed", driver, 1, "https://example.com/first")
    _record_typed_observed(cov, "link_subtitle_typed", driver, 2, "FirstSub")
    _hide_keyboard_safe(driver)
    cov["save_clicked"] = _tap_save_action(driver, package, d)
    # Active wait: poll for home + LazyColumn swipes to rebind PinnedLinksWidget.
    _active_wait_home(driver, package, d, budget_s=1.8)
    ensure_foreground(driver, package)
    cov["returned_to_home"] = main_home_visible(driver, package, d)
    return cov


def _run_create_multiple_folders(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Exploration: create three folders rapidly to stress Room IO + Compose recomposition.

    Re-attributed 2026-08-03: also carries the upstream intent of
    ``FolderDaoTest.insert_uniqueFolder``, previously mirrored by the dropped
    ``createFolderViaUi``. Triggers ``FolderRepository.insertFolder`` three
    times back-to-back, each followed by ``HomeViewModel`` paging re-issue +
    a LazyVerticalGrid rebind. This is the persistence-IO heart of the energy
    axis.
    """
    cov: dict[str, Any] = {
        "espresso_class": "MasterExperiment",
        "espresso_method": "createMultipleFolders",
        # v5 2026-08-03: 5 -> 3 entities. Measured 12.28 s per folder on the
        # batch1 baseline; see the calibration block on LINKHUB_SCENARIO_LIST.
        "folder_names_target": [
            "BetaFolder", "GammaFolder", "DeltaFolder",
        ],
        "folders_steps": [],
    }
    for name in cov["folder_names_target"]:
        step: dict[str, Any] = {
            "name": name, "form_opened": False, "typed_ok": False, "typed_strict": False,
            "save_ok": False, "observed": "",
        }
        step["form_opened"] = _open_folder_form_via_overflow(driver, package, d)
        if not step["form_opened"]:
            cov["folders_steps"].append(step)
            save_page_source(
                driver, dump_dir, "createMultipleFolders_form_open_miss_%s" % name,
            )
            continue

        ok_typed = _type_edit_text_instance(driver, 0, name)
        observed = _read_edit_text_instance(driver, 0)
        step["typed_ok"] = bool(ok_typed)
        step["observed"] = observed[:80]
        step["typed_strict"] = bool(ok_typed and observed.strip() == name)
        _hide_keyboard_safe(driver)
        step["save_ok"] = _tap_save_action(driver, package, d)
        # Active wait: poll for home + LazyVerticalGrid scroll to exercise
        # Compose recomposition under measurement.
        _active_wait_home(driver, package, d, budget_s=1.2)
        ensure_foreground(driver, package)
        step["returned_to_home"] = main_home_visible(driver, package, d)
        cov["folders_steps"].append(step)
    return cov


def _run_create_multiple_links(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Exploration: create two links rapidly. Mirror of the folder burst on the Link surface.

    Re-attributed 2026-08-03: also carries the upstream intent of
    ``LinkDaoTest.insert_uniqueLink``, previously mirrored by the dropped
    ``createLinkViaUi``. Drives ``LinkRepository.insertLink`` twice + 2
    LazyColumn rebinds + 2 PinnedLinksWidget refreshes. The first entity
    (``SecondLink``) is the card ``longClickEditLink`` later long-presses.
    """
    cov: dict[str, Any] = {
        "espresso_class": "MasterExperiment",
        "espresso_method": "createMultipleLinks",
        # v5 2026-08-03: 5 -> 2 entities. Measured 17.32 s per link on the
        # batch1 baseline: the single most expensive unit in the suite, and
        # the one that decides whether the cell lands in [110, 130] s.
        "link_targets": [
            ("SecondLink", "SecondSub", "https://example.com/second"),
            ("ThirdLink", "ThirdSub", "https://example.com/third"),
        ],
        "links_steps": [],
    }
    for title, subtitle, url in cov["link_targets"]:
        step: dict[str, Any] = {
            "title": title,
            "subtitle": subtitle,
            "url": url,
            "form_opened": False,
            "title_strict": False,
            "subtitle_strict": False,
            "url_strict": False,
            "save_ok": False,
        }
        step["form_opened"] = _open_link_form_via_overflow(driver, package, d)
        if not step["form_opened"]:
            cov["links_steps"].append(step)
            save_page_source(
                driver, dump_dir, "createMultipleLinks_form_open_miss_%s" % title,
            )
            continue

        # LinkScreen field order: instance 0 = Title, 1 = Url, 2 = SubTitle.
        ok_t = _type_edit_text_instance(driver, 0, title)
        obs_t = _read_edit_text_instance(driver, 0)
        step["title_strict"] = bool(ok_t and obs_t.strip() == title)
        ok_u = _type_edit_text_instance(driver, 1, url)
        obs_u = _read_edit_text_instance(driver, 1)
        step["url_strict"] = bool(ok_u and obs_u.strip() == url)
        ok_s = _type_edit_text_instance(driver, 2, subtitle)
        obs_s = _read_edit_text_instance(driver, 2)
        step["subtitle_strict"] = bool(ok_s and obs_s.strip() == subtitle)
        _hide_keyboard_safe(driver)
        step["save_ok"] = _tap_save_action(driver, package, d)
        # Active wait: poll for home + LazyColumn scroll down/up to rebind
        # PinnedLinksWidget and link cards.
        _active_wait_home(driver, package, d, budget_s=1.4)
        ensure_foreground(driver, package)
        step["returned_to_home"] = main_home_visible(driver, package, d)
        cov["links_steps"].append(step)
    return cov


def _run_search_keyword_filter(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Exploration: expand the toolbar SearchBar and exercise per-keystroke queries.

    Each non-empty query routes through ``SearchViewModel.updateSearchParams``
    (collectedAsStateWithLifecycle) → ``LinkRepository.getSortedLinkListByKeyword``
    → SQLite ``SELECT … LIKE %?%``; each empty / cleared query routes back to
    ``getSortedLinks()`` (full list re-bind). The Compose SearchBar uses
    ``LaunchedEffect(searchQuery, ...)`` so each keystroke triggers a re-query.
    Three different queries + one clear = four SQLite read round-trips.
    """
    cov: dict[str, Any] = {
        "espresso_class": "MasterExperiment",
        "espresso_method": "searchKeywordFilter",
        # v5 2026-08-03: "First" replaced by "Second"; FirstLink was created
        # by the now-dropped createLinkViaUi, so "First" would match nothing and
        # the query would silently degrade to a full-list rebind.
        "queries_target": ["Link", "Second", "", "Beta"],
        "queries_steps": [],
    }
    ensure_foreground(driver, package)

    # The SearchBar is initially in collapsed form; tap the leading Search icon
    # (contentDescription="Search", SearchScreen.kt:94) to expand the input.
    cov["search_action_clicked"] = safe_click_description_contains(
        driver, d["search_desc"], timeout=3.5,
    )
    if not cov["search_action_clicked"]:
        # On some devices the leading icon is non-clickable; falling back to
        # the placeholder text "Search" (SearchScreen.kt:87).
        cov["search_action_clicked"] = safe_click_text_contains(
            driver, "Search", timeout=2.5,
        )
    time.sleep(0.75)

    # After expansion the SearchBar's InputField is an android.widget.EditText.
    # In the expanded state the SearchBar is the ONLY visible EditText, so
    # instance(0) reliably grabs it. (Home screen has no other EditText
    # off-search-mode.)
    for q in cov["queries_target"]:
        step: dict[str, Any] = {"query": q, "typed_ok": False, "observed": ""}
        try:
            _clear_edit_text_instance(driver, 0)
            if q:
                el = _find_edit_text_by_instance(driver, 0)
                el.send_keys(q)
            step["typed_ok"] = True
            time.sleep(0.6)
            step["observed"] = _read_edit_text_instance(driver, 0)[:80]
        except Exception as ex:
            step["error"] = "%s: %s" % (type(ex).__name__, ex)
        cov["queries_steps"].append(step)

    _hide_keyboard_safe(driver)
    # Collapse the SearchBar so we land cleanly back on the home screen for the
    # next scenario. Two backs typically: first dismisses keyboard if still up,
    # second collapses the SearchBar (onExpandedChange sets expanded=false).
    _back(driver)
    time.sleep(0.45)
    _back(driver)
    time.sleep(0.45)
    ensure_foreground(driver, package)
    cov["returned_to_home"] = main_home_visible(driver, package, d)
    return cov


def _run_long_click_edit_link(
    driver: Any,
    package: str,
    d: dict[str, str],
    dump_dir: Optional[str],
) -> dict[str, Any]:
    """Exploration: long-press a link card → "Edit" → modify title → Save.

    REPLACES the v1.6.4 ``swipeDeleteAndUndo`` scenario. v2.1.0 removed both
    SwipeToDismiss and the Undo snackbar; the Compose Link list now exposes a
    DropdownMenu on long-press (Links.kt:167-172 = Edit/Copy/Shortcut/Share).
    The Edit option navigates to LinkScreen with the link's bundle so
    ``LinkViewModel.updateLink`` fires on save (vs createNewLink): this
    exercises a different DAO code path than the create* scenarios and is the
    closest v2.1.0 analog to the v1.6.4 swipe-delete in terms of "list-driven
    persistence write".

    Two passes: first long-press triggers the menu (combinedClickable), tap
    "Edit", modify the title field, save, return home.
    """
    cov: dict[str, Any] = {
        "espresso_class": "MasterExperiment",
        "espresso_method": "longClickEditLink",
        "edited_title_target": "EditedSecondLink",
    }
    ensure_foreground(driver, package)
    if not main_home_visible(driver, package, d):
        _navigate_home_via_back(driver, package, d, max_back=3)

    from appium.webdriver.common.appiumby import AppiumBy

    # Find the first link card. Each LinkContent renders a Card whose first
    # child is an Icon with contentDescription pointing at the platform icon
    # (ic_link by default: Links.kt:91-95). We use a coarser locator: the
    # link card title TextView. Since v2.1.0 has no resource-id, we search for
    # the first TextView under a Card. Instead we just use long-press at the
    # location of the link card's title text "SecondLink" (created earlier by
    # createMultipleLinks). This is robust to the absence of testTags.
    #
    # v5 2026-08-03: was "FirstLink", created by createLinkViaUi; that
    # scenario was dropped in the v5 recalibration, so the primary selector
    # would now always miss and the scenario would exit at card_not_found.
    cov["card_found"] = False
    cov["long_press_attempted"] = False
    cov["edit_menu_clicked"] = False
    cov["edit_form_opened"] = False
    cov["title_typed_strict"] = False
    cov["save_clicked"] = False

    target = None
    try:
        # First-created link's title from createMultipleLinks.
        target = driver.find_element(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().text("SecondLink")',
        )
        cov["card_found"] = True
    except Exception:
        # Fallback: any link card created during this run (look for the share
        # scenario's auto-saved "amr Link" or any of the createMultipleLinks
        # entries). The DropdownMenu options are identical.
        for fallback_text in ("amr Link", "ThirdLink"):
            try:
                target = driver.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiSelector().text("%s")' % fallback_text,
                )
                cov["card_found"] = True
                cov["fallback_card_text"] = fallback_text
                break
            except Exception:
                continue

    if not cov["card_found"] or target is None:
        save_page_source(driver, dump_dir, "longClickEditLink_card_not_found")
        return cov

    # Long-press the card. UiAutomator2's long_click is exposed via
    # mobile: longClickGesture (Appium 2+). Fall back to a manual touch action
    # via TouchAction if needed.
    try:
        rect = target.rect
        cx = rect["x"] + rect["width"] // 2
        cy = rect["y"] + rect["height"] // 2
        try:
            driver.execute_script(
                "mobile: longClickGesture",
                {"x": cx, "y": cy, "duration": 800},
            )
            cov["long_press_attempted"] = True
        except Exception as ex_lc:
            log("mobile: longClickGesture failed (%s); trying TouchAction"
                % (type(ex_lc).__name__,))
            try:
                from appium.webdriver.common.touch_action import TouchAction
                ta = TouchAction(driver)
                ta.press(x=cx, y=cy).wait(800).release().perform()
                cov["long_press_attempted"] = True
            except Exception as ex_ta:
                cov["long_press_error"] = "%s: %s" % (type(ex_ta).__name__, ex_ta)
    except Exception as ex:
        cov["long_press_error"] = "%s: %s" % (type(ex).__name__, ex)
        save_page_source(driver, dump_dir, "longClickEditLink_long_press_failed")
        return cov

    time.sleep(0.85)  # let DropdownMenu materialize

    cov["edit_menu_clicked"] = safe_click_text_contains(
        driver, d["edit_label"], timeout=8.0,
    )
    if not cov["edit_menu_clicked"]:
        save_page_source(driver, dump_dir, "longClickEditLink_edit_menu_miss")
        # We need to dismiss any open menu so the next scenario starts clean.
        _back(driver)
        time.sleep(0.4)
        ensure_foreground(driver, package)
        cov["returned_to_home"] = main_home_visible(driver, package, d)
        return cov

    # Poll for the LinkScreen Edit form (ceiling 1.5s): exits on first hit.
    _poll_until(lambda: link_form_visible(driver, package, d), timeout=8.0)
    cov["edit_form_opened"] = link_form_visible(driver, package, d)
    if not cov["edit_form_opened"]:
        save_page_source(driver, dump_dir, "longClickEditLink_form_open_miss")
        _navigate_home_via_back(driver, package, d, max_back=3)
        cov["returned_to_home"] = main_home_visible(driver, package, d)
        return cov

    # Modify the title (instance 0). Type+observe gives us the strict signal.
    _record_typed_observed(
        cov, "edited_title_typed", driver, 0, "EditedSecondLink",
    )
    cov["title_typed_strict"] = bool(cov.get("edited_title_typed_strict"))
    _hide_keyboard_safe(driver)
    cov["save_clicked"] = _tap_save_action(driver, package, d)
    # Active wait: poll for home + LazyColumn scroll to rebind updated card.
    _active_wait_home(driver, package, d, budget_s=1.5)
    ensure_foreground(driver, package)
    cov["returned_to_home"] = main_home_visible(driver, package, d)
    return cov


# ---------------------------------------------------------------------------
# Workload entrypoint
# ---------------------------------------------------------------------------


def run_linkhub_workload(
    driver: Any,
    package: str,
    deadline_monotonic: float,
    dump_dir: Optional[str],
) -> int:
    """Run the locked ``LINKHUB_SCENARIO_LIST`` end-to-end.

    Each scenario:

    1. Captures its own coverage dict.
    2. Records ``ts`` (start) and ``scenario_wall_seconds`` (end - start).
    3. Routes through :func:`appium_android_tests.linkhub.scoring.score_scenario`
       to derive ``scenario_success_strict_ui`` and ``scenario_success_action_only``.
    4. Appends the row to ``appium_workload_coverage.jsonl``.

    No time-box: ``deadline_monotonic`` is accepted for signature parity with the
    Metronome workload pattern but only consulted as a hard ceiling (we log if a
    scenario completes after it; we do NOT short-circuit on it).
    """
    d = _linkhub_defaults()
    await_app_ready(
        driver,
        package,
        deadline_monotonic,
        is_main_ui_visible=lambda drv: main_home_visible(drv, package, d),
        dump_dir=dump_dir,
    )

    artifact_dir = resolve_artifact_dir(dump_dir)
    build_label = os.environ.get("APPIUM_BUILD_LABEL", "").strip()

    scenario_table: dict[str, Callable[[], dict[str, Any]]] = {
        "shareLinkReceiveGeneral": lambda: _run_share_link_receive_general(driver, package, d, dump_dir),
        "createFolderViaUi": lambda: _run_create_folder_via_ui(driver, package, d, dump_dir),
        "createLinkViaUi": lambda: _run_create_link_via_ui(driver, package, d, dump_dir),
        "createMultipleFolders": lambda: _run_create_multiple_folders(driver, package, d, dump_dir),
        "createMultipleLinks": lambda: _run_create_multiple_links(driver, package, d, dump_dir),
        "searchKeywordFilter": lambda: _run_search_keyword_filter(driver, package, d, dump_dir),
        "longClickEditLink": lambda: _run_long_click_edit_link(driver, package, d, dump_dir),
    }

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
        for name in LINKHUB_SCENARIO_LIST
    }

    if env_truthy("LINKHUB_RUN_ALL_SCENARIOS"):
        ordered = list(LINKHUB_SCENARIO_LIST)
        log("linkhub: LINKHUB_RUN_ALL_SCENARIOS=1 — running all %d candidates"
            % len(ordered))
    else:
        missing = [n for n in LINKHUB_SCENARIO_LIST if n not in scenario_table]
        if missing:
            log("ERROR: locked LINKHUB_SCENARIO_LIST entries not implemented: %s" % missing)
            ordered = [n for n in LINKHUB_SCENARIO_LIST if n in scenario_table]
        else:
            ordered = list(LINKHUB_SCENARIO_LIST)
        log(
            "linkhub: per-app contract — running %d locked scenarios end-to-end (no time-box; "
            "wall time is measured output): %s" % (len(ordered), ordered),
        )

    step_count = 0
    suite_round = 1

    for scenario_name in ordered:
        step_count += 1
        scenario_started = time.monotonic()
        # v5 2026-08-03 BUGFIX: capture the wall-clock START here, before the
        # scenario body runs. It used to be stamped after the body returned, so
        # ``ts`` held the scenario's END time. build_run_table_extended.py
        # derives the workload window as ``max(ts + scenario_wall_seconds) -
        # min(ts)``; with ts = end, the window started one scenario late and
        # ended ~44 s past the real finish (the longest scenario's wall was
        # added to its own end time). That inflated linkhub's reported
        # duration_workload_s from a true ~246 s to ~304 s AND charged ~44 s of
        # post-workload idle into workload_avg_watts. Measured inter-scenario
        # gaps are 0.13 s median, so with this fix reported duration ≈ sum of
        # scenario walls, matching every other cohort app.
        scenario_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log(
            "linkhub scenario %s (step #%s/%s, no time-box, locked-list)"
            % (scenario_name, step_count, len(ordered)),
        )
        try:
            cov = scenario_table[scenario_name]()
        except Exception as ex:
            cov = {
                "scenario_runtime_error": "%s: %s" % (type(ex).__name__, ex),
            }
            log("linkhub scenario %s raised %s — recorded as scenario_runtime_error"
                % (scenario_name, ex))
        cov["workload"] = "linkhub"
        cov["scenario_name"] = scenario_name
        cov["suite_round"] = suite_round
        cov["ts"] = scenario_ts
        cov["scenario_wall_seconds"] = round(time.monotonic() - scenario_started, 3)
        cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        strict_ok, action_ok, fail_reasons = score_scenario(scenario_name, cov)
        cov["scenario_success_strict_ui"] = strict_ok
        cov["scenario_success_action_only"] = action_ok
        cov["scenario_success"] = strict_ok
        if fail_reasons:
            cov["scenario_fail_reasons_strict_ui"] = fail_reasons
        if build_label:
            cov["build_label"] = build_label

        row = by_scenario.setdefault(
            scenario_name,
            {
                "runs": 0,
                "success": 0,
                "fail": 0,
                "success_strict_ui": 0,
                "fail_strict_ui": 0,
                "success_action_only": 0,
                "fail_action_only": 0,
                "example_fail_reasons": [],
            },
        )
        row["runs"] += 1
        if strict_ok:
            row["success"] += 1
            row["success_strict_ui"] += 1
        else:
            row["fail"] += 1
            row["fail_strict_ui"] += 1
            ex = row["example_fail_reasons"]
            if len(ex) < 6:
                ex.append(fail_reasons[:16])
        if action_ok:
            row["success_action_only"] += 1
        else:
            row["fail_action_only"] += 1

        ensure_foreground(driver, package)
        log("linkhub coverage: %s" % json.dumps(cov, sort_keys=True, default=str))
        append_coverage_jsonl(dump_dir, cov)

    # Aggregated scenario report
    total_runs = sum(v["runs"] for v in by_scenario.values())
    total_succ = sum(v["success"] for v in by_scenario.values())
    agg: dict[str, Any] = {
        "build_label": build_label or None,
        "by_scenario": by_scenario,
        "summary": {
            "fail_count": total_runs - total_succ,
            "success_count": total_succ,
            "total_scenario_executions": total_runs,
            "total_strict_ui_pass": sum(
                v.get("success_strict_ui", 0) for v in by_scenario.values()
            ),
            "total_action_only_pass": sum(
                v.get("success_action_only", 0) for v in by_scenario.values()
            ),
        },
    }
    lines = [
        "LinkHub scenario report (Appium-derived checks)",
        "build_label: %s" % (build_label or "(none)"),
        "",
        "summary: %s runs, %s passed, %s failed"
        % (total_runs, total_succ, total_runs - total_succ),
        "",
    ]
    for sn in LINKHUB_SCENARIO_LIST:
        r = by_scenario.get(sn, {})
        if not r:
            continue
        lines.append(
            "%s  runs=%s  headline_ok=%s  headline_fail=%s  "
            "strict_ui=%s/%s  action_only=%s/%s"
            % (
                sn,
                r.get("runs", 0),
                r.get("success", 0),
                r.get("fail", 0),
                r.get("success_strict_ui", 0),
                r.get("fail_strict_ui", 0),
                r.get("success_action_only", 0),
                r.get("fail_action_only", 0),
            ),
        )
        if r.get("example_fail_reasons"):
            lines.append("  examples: %s" % r["example_fail_reasons"][:3])
    text = "\n".join(lines) + "\n"
    write_scenario_report(artifact_dir, agg, text, workload_name="linkhub")
    return step_count


def run_workload(experiment: Any, device: Any) -> None:
    """Entrypoint called by ``Scripts/interaction_appium_linkhub.py`` (Android Runner hook).

    Reads ``package`` and ``duration`` from ``experiment``, builds Appium options,
    starts the workload thread, and blocks ``main()`` on join (per-app fixed-N
    contract: wall time is a measured output, not a controlled input). Failsafe
    cap = ``max(duration_s + 600, 720)`` seconds.
    """
    package = getattr(experiment, "package", None) if experiment is not None else None
    duration_s = float(getattr(experiment, "duration", 60.0)) if experiment is not None else 60.0

    if not package:
        log("No package found; skipping.")
        return

    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723").rstrip("/")
    activity = os.environ.get("LINKHUB_ACTIVITY", ".ui.MainActivity")
    dump_dir = resolve_dump_dir()

    print("=INTERACTION_APPIUM_LINKHUB=")
    print("DEVICE (udid):", device.id)
    print("PACKAGE:", package)
    print("ACTIVITY:", activity)
    print("DURATION_S (failsafe ceiling only):", duration_s)
    print("APPIUM_SERVER:", server)
    print("APPIUM_BUILD_LABEL:", os.environ.get("APPIUM_BUILD_LABEL", "").strip() or "(none)")
    print("APPIUM_USE_VIEW_IDS:", env_truthy("APPIUM_USE_VIEW_IDS"))
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

        connect_timeout_s = min(
            max(env_float("APPIUM_SESSION_CONNECT_TIMEOUT_S", 120.0), 5.0), 900.0,
        )
        artifact_dir = resolve_artifact_dir(dump_dir)
        status: dict[str, Any] = {
            "session_created": False,
            "session_start_seconds": None,
            "workload_started": False,
            "steps_executed": 0,
            "session_connect_timeout_seconds": connect_timeout_s,
            "workload_mode": "linkhub",
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
                "connecting to %s (target workload after session: %.1fs; "
                "session wall-clock cap %.1fs)"
                % (server, duration_s, connect_timeout_s),
            )
            driver = connect_remote_webdriver(
                webdriver, server, options, connect_timeout_s, artifact_dir,
            )
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
                % (len(LINKHUB_SCENARIO_LIST), handshake_s, duration_s),
            )
            status["workload_started"] = True
            status["steps_executed"] = run_linkhub_workload(
                driver, package, no_deadline, dump_dir,
            )
        except SessionConnectTimeoutError as ex:
            status["failure_reason"] = str(ex)
            status["session_timeout"] = True
            log(
                "SESSION_TIMEOUT: Appium session not created within %.1fs - "
                "no UI workload ran (%s)"
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
                    "SESSION_TIMEOUT: Appium session not created within %.1fs - "
                    "no UI workload ran (%s)"
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
            "INTERACTION_APPIUM [stderr]: ANDROID_HOME / ANDROID_SDK_ROOT unset in this "
            "process - start Appium from a shell with SDK exports (see "
            "appium_android_tests/README.md).",
        )

    failsafe_cap_s = max(duration_s + 600.0, 720.0)
    start_workload_thread_and_block_until_done(
        workload, failsafe_cap_s, thread_name="appium-linkhub-workload",
    )
