"""
Per-app Documenter workload (ViliusSutkus89 ``com.viliussutkus89.documenter``; debug-variant
package ``com.viliussutkus89.documenter.debug``: the packed APK installed on Pixel 9 uses the
``.debug`` suffix because ``app/build.gradle.kts:78-81`` sets ``applicationIdSuffix = ".debug"`` on
the debug build type).

Documenter is a **View-based** (XML layouts, no Jetpack Compose anywhere in ``app/src/main/``)
document-conversion reference app for the native libraries ``libpdf2htmlEX-android.so`` (PDF →
HTML) and ``libwvware-android.so`` (Word .doc → HTML). The home screen exposes a single FAB
(``R.id.open_button``) that fires ``ActivityResultContracts.OpenDocument()`` to invoke the system
SAF picker; selecting a PDF / DOC routes the URI through ``ConverterViewModel.convert(...)`` to
either ``pdf2htmlEXWorker`` or ``wvWareWorker`` (each running in its own dedicated remote process,
see ``AndroidManifest.xml:28-37``) which spawns the native converter. When the worker completes,
the home screen's RecyclerView (``R.id.recycler_view``) gains a tile for the converted document
and the WebView (``R.id.documentView`` in ``fragment_document.xml``) renders it.

**Energy axis:** Native conversion via ``libpdf2htmlEX-android.so`` (PDF → HTML rasterization +
font extraction; CPU-heavy, especially for PDFs with embedded fonts and SVG backgrounds: the
upstream Espresso fixture even includes a regression PDF ``svg_background_with_page_rotation_issue402.pdf``)
and ``libwvware-android.so`` (.doc parser, also CPU-heavy on legacy binary Word documents). Both
workers run in dedicated remote processes (``:pdf2htmlEXWorker`` and ``:wvWareWorker``), so the
energy signal is dominated by **native JNI execution + IPC between the worker process and the
main process**. Variant comparison is dominated by JIT/AOT compilation of the JNI shims and the
worker dispatch path; native code itself is not Java-shrinkable so R8 / Allatori effects on the
hot path are modest, but packers that wrap native libs (Bangcle's ``SecShell``) can add
significant decryption-on-load overhead the first time each ``.so`` is dlopen'd.

Workload modes selected by ``APPIUM_WORKLOAD``:

- ``espresso_mirror`` (default for the master experiment): runs the locked
  :data:`DOCUMENTER_SCENARIO_LIST` end-to-end, no time-box, wall time is a measured output.

Env-var contract owned here (others are documented in the relevant ``_lib`` module):

- ``DOCUMENTER_ACTIVITY``: activity to launch (default ``.ui.MainActivity``;
  ``MainActivity`` lives in the ``com.viliussutkus89.documenter.ui`` package, so the
  dotted-relative form picks up the package id prefix at launch time).
- ``APPIUM_WORKLOAD``: workload selector; only ``espresso_mirror`` is implemented.
- ``APPIUM_BUILD_LABEL``: free-form tag (``baseline`` / ``r8`` / ``allatori`` /
  ``bangcle``) included in JSONL rows + the scenario report. Lets one config run across multiple
  variant builds while keeping rows distinguishable.
- ``DOCUMENTER_OPEN_FAB_DESC``: content-description substring for the home FAB
  (default ``Open``, declared as ``android:contentDescription="Open"`` in
  ``fragment_home.xml:30``).
- ``DOCUMENTER_ABOUT_TITLE``: visible text of the About menu item (default ``About``,
  from string id ``menu_about`` in ``res/values/strings.xml:48``).
- ``DOCUMENTER_SETTINGS_TITLE``: visible text of the Settings menu item (default
  ``Settings``, from string id ``menu_settings`` / ``fragment_settings_label``).
- ``DOCUMENTER_SHOW_LICENSES_TITLE``: Show licenses button text (default ``Show licenses``).
- ``DOCUMENTER_CONVERT_WAIT_S``: wall-clock budget (seconds) for the conversion to make
  observable progress in :func:`scenario_open_document_attempt`. Default ``20``: generous on
  Pixel 9, may need bumping on Pixel 3 baseline; lower for fast smoke runs.

Artifacts produced (under ``paths.OUTPUT_DIR`` when Android Runner is the parent):

- ``appium_workload_coverage.jsonl``
- ``espresso_mirror_scenario_report.{json,txt}``
- ``appium_status.json``
- ``appium_ui_dumps/*.xml`` (forensics, capped by ``APPIUM_UI_DUMP_MAX``)

Provenance summary (as locked when the module was written; the list has changed since,
see :data:`DOCUMENTER_SCENARIO_LIST`):

- ``8`` upstream ``@Test`` methods across four androidTest files
  (``AboutMenuItemVisibilityTest.kt`` × 5, ``OpenTest.kt`` × 1 (parameterized over 4 fixture
  files), ``OpeningFromOtherAppTest.kt`` × 1 (parameterized over 4 fixture files),
  ``NotificationTest.kt`` × 1 (``@SdkSuppress(maxSdkVersion = 20)``)).
- ``7`` Appium scenarios in :data:`DOCUMENTER_SCENARIO_LIST`.
- ``4`` mirror, ``1`` inspired, ``2`` exploration; ``2`` upstream tests left unmirrored
  (``OpeningFromOtherAppTest.testParameterizedFile`` requires startup-time intent injection
  that an attached Appium session cannot retroactively perform; ``NotificationTest.showNotification``
  is gated by ``@SdkSuppress(maxSdkVersion = 20)`` and Pixel 9 is API 35).
- The two unmirrored tests are left out for the reasons given above.

**Compose-selector strategy (none applies):**

A ``grep -rn 'testTag\\|@Composable'`` across ``app/src/main/`` returns zero hits, confirming
Documenter is fully View-based. The 4-layer Compose-fallback cascade in
:func:`appium_android_tests._lib.scoring._find_id_with_compose_fallback` is therefore not exercised
for this app: Layer 1 (View-based ``<package>:id/<view_id>``) always wins. The Appium
``useTestTagAsResourceId`` capability set by :func:`build_uiautomator2_options` is a no-op here
but stays enabled because the shared driver builder applies it unconditionally.

**Verification status of selectors:**

Selectors were derived from reading the XML resources (`res/layout/fragment_home.xml`,
`res/menu/main_menu.xml`, `res/values/strings.xml`, `res/xml/root_preferences.xml`) on the
upstream HEAD of the v1.0.14 source tree. **They were NOT verified with a live ``uiautomator
dump`` when the module was written.** Selector
confidence is high for the View-id-based ones (``open_button``, ``recycler_view``, ``about``,
``settings``, ``show_licenses``) because Documenter is unobfuscated by default and the manifest's
debug-variant package keeps the namespaced resource id discoverable. If a smoke run shows misses
on (e.g.) ``open_button``, switch to ``descriptionContains("Open")``: the FAB carries
``android:contentDescription="Open"`` regardless of R8 outcomes.

**Caveats for the smoke run:**

1. **Sample-document fixture push.** :func:`scenario_open_document_attempt` exercises the native
   conversion energy axis only if a PDF or DOC is selectable from the SAF picker. Recommended:
   push ``app/src/androidTest/assets/testFiles/geneve_1564.pdf`` of the Documenter source
   to ``/sdcard/Download/sample.pdf`` before each smoke run (``before_experiment_sync_fixtures.py``
   does this in the ``before_experiment`` chain). Without a pre-pushed fixture the
   scenario still records action-only signal (FAB tap + picker navigation attempt) and the
   strict-UI tag will be ``conversion_did_not_progress``: informative, not a failure mode.
2. **Permissions.** Documenter v1.0.14 only declares ``android.permission.FOREGROUND_SERVICE``
   (for the converter workers). No runtime grants are needed. The shared ``dismiss_system_dialogs``
   path will still handle stray "Allow" / "While using the app" prompts if a future build adds
   storage / media permissions.
3. **WebView dependency.** Documenter requires Android System WebView (see
   ``WebViewInstallFragment.kt`` + ``error_open_with_failed`` strings). Pixel 9 ships a recent
   WebView so this should not trigger; if it does, the scenarios still succeed for the home /
   about / settings surfaces.
4. **Launch activity.** The dotted activity ``.ui.MainActivity`` resolves to the manifest's
   ``com.viliussutkus89.documenter.ui.MainActivity`` (or ``.debug.ui.MainActivity`` for the
   debug-variant package); Appium concatenates the app_package with the dot prefix per the
   ``app_activity`` capability convention.
5. **Conversion-worker processes.** ``pdf2htmlEXWorker`` and ``wvWareWorker`` each run in
   ``:pdf2htmlEXWorker`` / ``:wvWareWorker`` remote processes, so BatteryManager / battery
   sysfs energy accounting captures the whole-device load even though the main UI process is
   idle during the convert step. This is expected and is exactly the per-app energy signal we
   want; it does NOT need any special hook configuration.
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
    safe_click_description_contains,
    safe_click_text_contains,
    safe_scroll_into_view_text_contains,
    save_page_source,
    start_workload_thread_and_block_until_done,
    swipe_vertical,
    write_appium_status,
    write_scenario_report,
)
from .._lib.coverage import reset_dump_counter
from . import scoring as _documenter_scoring


# ---------------------------------------------------------------------------
# Per-app calibration: locked scenario list (2026-05-12)
# ---------------------------------------------------------------------------
#
# Fixed-N scenario contract (2026-05-08): scenario count, not duration, is
# the cross-variant comparable unit. For each app, ONE number N of scenarios is calibrated on
# the slowest device baseline (Pixel 3) targeting ~120 s wall time. That same N is then run on
# every variant × every device. Wall time becomes a *measured output*, not a controlled input.
# Documenter was calibrated on **Pixel 9** when the module was written, because the Pixel 3
# was not connected; that calibration was an estimate, not a Pixel 3 baseline run.
#
# Set α: DOCUMENTER (4 mirror + 1 inspired + 2 exploration), 2026-05-12
# -----------------------------------------------------------------------
# Documenter has 8 upstream @Test methods across 4 androidTest files. 5 of those are in
# AboutMenuItemVisibilityTest.kt and exercise overlapping About-menu navigation; we fuse them
# into 3 Appium scenarios so the wall time budget stays inside ~95-115 s on Pixel 9. 1 test
# (OpenTest.testParameterizedFile) fires Intents.intending to inject a fake URI: Appium cannot
# do that retroactively, so we mirror only the FAB-tap portion (openFilePicker) and add an
# inspired scenario (openDocumentAttempt) that uses the live SAF picker. The remaining 2 tests
# (OpeningFromOtherAppTest needs ACTION_VIEW startup injection; NotificationTest is API-gated
# to <= 20) are left unmirrored. 2 exploration scenarios round out the
# wall time budget: settingsTogglePreferences exercises the conversion-config preferences path,
# homeScrollAndIdle exercises the RecyclerView + foreground without chasing additional surfaces.
#
# Estimated Pixel 9 wall time ~95-110 s (target window 90-120 s, allowing for Pixel 9 being
# faster than Pixel 3). Budget by scenario:
#
#   shownInHome                                ~4 s   tap menu icon (if needed), verify About item
#   aboutMenuHiddenInAbout                     ~12 s  click About → click Show licenses → assert hidden
#   aboutShownAfterReturnFromAboutLibraries    ~14 s  About → Show licenses → back → back → verify visible
#   openFilePicker                             ~10 s  tap FAB, verify SAF picker overlay, back
#   settingsTogglePreferences                  ~22 s  open Settings → toggle 3 prefs → back to home
#   openDocumentAttempt                        ~30 s  tap FAB → picker → best-effort select recent doc
#                                                    → wait for loading / WebView / converted tile
#                                                    → back to home (energy-axis-engaging step)
#   homeScrollAndIdle                          ~10 s  recyclerview swipes + foreground idle
#                                              ─────
#                                              Total: ~102 s on Pixel 9 (estimate)
#
# This list is LOCKED. Do not reorder or change without a new calibration run.
# See:
#   - appium_android_tests/CONVENTIONS.md → § 6.5 "Per-app scenario calibration"
#   - appium_android_tests/CONVENTIONS.md → "Adding a new app" checklist
#   - appium_android_tests/documenter/CALIBRATION.md → per-scenario timings + chosen N
DOCUMENTER_SCENARIO_LIST: tuple[str, ...] = (
    # 2026-07-26 amendment:
    # Verified with a WiFi-ADB dry-run harness that
    # openDocumentAttempt on ALLATORI leaves the app in a fragile state where
    # subsequent FAB / toolbar interactions fail with NoSuchElementException
    # (dry_run P3 allatori: 6/8 pass, openFilePicker_repeat2 + settingsTogglePreferences_repeat2 FAIL
    # w/ action_false:open_fab_clicked and action_false:settings_opened; app
    # activate_app calls throughout report launcher taking foreground). Under
    # baseline/bangcle/freerasp the app recovers fine, but allatori's obfuscated
    # PDF conversion path leaves the app half-alive.
    #
    # FIX: reorder so openDocumentAttempt runs LAST. All loop-backs execute
    # BEFORE the allatori fragility sets in. Matched-pair preserved (same
    # 8 scenarios across all variants, only order differs).
    #
    # Also REMOVED aboutMenuHiddenInAbout + aboutShownAfterReturnFromAboutLibraries
    # from prior edit: both click a Kotlin `setOnClickListener{}` lambda on
    # R.id.show_licenses whose NavArgs-generated Directions method Allatori
    # renames: click registers, navigation binding fails.
    "shownInHome",                              # ~4 s: about menu item visible on home (mirror)
    "openFilePicker",                           # ~10 s: tap FAB, SAF picker visible (mirror)
    "openFilePicker_repeat2",                   # ~10 s: reopens picker (no state change)
    "settingsTogglePreferences",                # ~22 s: toggle 3 conversion prefs (exploration)
    "settingsTogglePreferences_repeat2",        # ~22 s: same, deterministic reset via back
    "homeScrollAndIdle",                        # ~12 s: recyclerview swipes (exploration)
    "homeScrollAndIdle_repeat2",                # ~13 s: same, idempotent
    "openDocumentAttempt",                      # ~47 s: inspired, energy-axis; LAST due to allatori fragility
    # ─────────────────────────────────────────────────────────────────────────
    # v5 2026-08-03 RECALIBRATION VERDICT: NO CHANGE.
    #
    # Measured on Pixel 6 against the CURRENT code (dry run, baseline_debug):
    # the 8 locked scenarios span 117.7 s: already inside [110, 130] s. The
    # batch1-derived estimate that said otherwise was computed from bangcle /
    # freerasp cells and predates the 2026-08-03 wait-widening.
    #
    # Two extra loop-back slots (openFilePicker_repeat3, homeScrollAndIdle_repeat3)
    # were added and then REVERTED: they ran fine but scored
    # strict=False / unknown_scenario_name, because documenter/scoring.py keys
    # off an explicit scenario-name whitelist that the dispatcher does not
    # share. Any future slot added here MUST also be registered in scoring.py.
)


def _documenter_defaults() -> dict[str, str]:
    """Default selector substrings + visible texts.

    Each entry can be overridden via the corresponding ``DOCUMENTER_*`` env var so non-English
    builds (or future apps that fork the strings) can reuse this workload without code changes.
    Default values come from the actual source: ``res/layout/fragment_home.xml`` (FAB
    ``contentDescription``), ``res/menu/main_menu.xml`` (item ids), ``res/values/strings.xml``
    (visible labels), and ``res/xml/root_preferences.xml`` (preference titles).
    """
    return {
        "open_fab_desc": os.environ.get("DOCUMENTER_OPEN_FAB_DESC", "Open"),
        "about_title": os.environ.get("DOCUMENTER_ABOUT_TITLE", "About"),
        "settings_title": os.environ.get("DOCUMENTER_SETTINGS_TITLE", "Settings"),
        "show_licenses_title": os.environ.get("DOCUMENTER_SHOW_LICENSES_TITLE", "Show licenses"),
        # Preference row titles (root_preferences.xml maps these via @string ids):
        "pref_outline_title": os.environ.get("DOCUMENTER_PREF_OUTLINE_TITLE", "Outline"),
        "pref_annotations_title": os.environ.get(
            "DOCUMENTER_PREF_ANNOTATIONS_TITLE", "Annotations",
        ),
        "pref_nographics_title": os.environ.get(
            "DOCUMENTER_PREF_NOGRAPHICS_TITLE", "No graphics mode",
        ),
        # Loading / state text shown during conversion (string ids state_converting / state_init).
        "loading_text_substrs": os.environ.get(
            "DOCUMENTER_LOADING_TEXT_SUBSTRS", "Converting|Initializing|Caching",
        ),
    }


def main_documenter_visible(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Detect the main Documenter home screen.

    Two complementary signals (either one is enough):

    1. The FAB ``content-description="Open"`` (declared in ``fragment_home.xml:30``): stable
       across R8 because it's a string literal, not a derived label.
    2. The namespaced resource id ``<package>:id/recycler_view`` (declared in
       ``fragment_home.xml:11``): stable across debug / release / .debug-suffixed packages
       because View-based resource IDs survive the AAB / split build.

    Either signal alone is enough: we OR them so a packer rewriting one surface (e.g. a future
    Bangcle update that obfuscates resource IDs) does not block readiness gating.
    """
    fab_desc = d["open_fab_desc"].replace('"', '\\"')
    if element_present_uiautomator(
        driver,
        'new UiSelector().descriptionContains("%s")' % fab_desc,
        timeout=2.0,
    ):
        return True
    recycler_id = "%s:id/recycler_view" % package
    if element_present_uiautomator(
        driver,
        'new UiSelector().resourceId("%s")' % recycler_id.replace('"', '\\"'),
        timeout=1.5,
    ):
        return True
    return False


def _record(cov: dict[str, Any], key: str, ok: bool) -> bool:
    """Record ``ok`` against ``cov[key]`` with **logical OR** semantics across the cycle."""
    cov[key] = bool(cov.get(key, False) or ok)
    return ok


def _dismiss_keyboard_safe(driver: Any) -> None:
    try:
        driver.hide_keyboard()
    except Exception:
        pass


def _safe_back(driver: Any, label: str) -> bool:
    """Press the device back button, swallowing exceptions and returning ``True`` on success."""
    try:
        driver.back()
        log("back press: %s" % label)
        time.sleep(0.4)
        return True
    except Exception as ex:
        log("back press %s failed: %s %s" % (label, type(ex).__name__, ex))
        return False


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------


def _resource_id_present(driver: Any, package: str, view_id: str, timeout: float = 2.5) -> bool:
    """Return True if ``<package>:id/<view_id>`` resolves within ``timeout``.

    Documenter is fully View-based so this is the canonical existence check. We additionally try
    the bare view id (``testTagsAsResourceId`` opt-in) as a forward-compatibility safety net for
    future Compose migrations: it costs one extra UiAutomator round-trip on miss but yields zero
    false negatives if Documenter ever migrates to Compose.
    """
    namespaced = "%s:id/%s" % (package, view_id)
    sel_ns = 'new UiSelector().resourceId("%s")' % namespaced.replace('"', '\\"')
    if element_present_uiautomator(driver, sel_ns, timeout=timeout):
        return True
    # Fallback for hypothetical future testTagsAsResourceId-style Compose UI.
    sel_bare = 'new UiSelector().resourceIdMatches(".*:id/%s$")' % view_id.replace('"', '\\"')
    return element_present_uiautomator(driver, sel_bare, timeout=0.6)


def _click_resource_id(
    driver: Any, package: str, view_id: str, label: str, timeout: float = 4.0,
) -> bool:
    """Click the View tagged ``<package>:id/<view_id>``; log + return False on miss."""
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.common.exceptions import NoSuchElementException

    namespaced = "%s:id/%s" % (package, view_id)
    # Use find_elements (plural) so the first miss returns [] immediately without burning the
    # implicit-wait budget.
    try:
        els = driver.find_elements(AppiumBy.ID, namespaced)
        if not els:
            # Forward-compat: try a bare resource id (testTagsAsResourceId).
            els = driver.find_elements(AppiumBy.ID, view_id)
        if not els:
            raise NoSuchElementException(
                "no element resolved for id=%s (and bare %s)" % (namespaced, view_id),
            )
        els[0].click()
        log("clicked id=%s (%s)" % (view_id, label))
        time.sleep(0.45)
        return True
    except Exception as ex:
        log("click id=%s (%s) failed: %s %s" % (view_id, label, type(ex).__name__, ex))
        return False


# ---------------------------------------------------------------------------
# About-menu helpers: the upstream tests in AboutMenuItemVisibilityTest.kt navigate via
# `onView(withId(R.id.about))` (an AppBar menu item). On UiAutomator that surfaces as either:
#   - a clickable view with resource-id `<package>:id/about` if the toolbar declares it directly,
#   - OR a menu item rendered with `content-desc=<title>` (Material toolbar default), so we have
#     two complementary click paths.
# ---------------------------------------------------------------------------


def _click_about_menu(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Click the About menu item from the home toolbar.

    Order:
      1. Direct resource-id (``<package>:id/about``): the upstream Espresso path.
      2. Content-description contains ``About``: Material toolbar item default behavior.
      3. Visible text equals ``About``: fallback for menu overflow expansion.
    """
    if _click_resource_id(driver, package, "about", "about menu item", timeout=2.0):
        return True
    if safe_click_description_contains(driver, d["about_title"], timeout=2.0):
        return True
    # If the menu collapsed into a 3-dot overflow, open it then click.
    if safe_click_description_contains(driver, "More options", timeout=1.5):
        time.sleep(0.3)
        if safe_click_text_contains(driver, d["about_title"], timeout=2.0):
            return True
    return safe_click_text_contains(driver, d["about_title"], timeout=2.0)


def _click_settings_menu(driver: Any, package: str, d: dict[str, str]) -> bool:
    """Click the Settings menu item from the home toolbar. Same fallback ladder as About."""
    if _click_resource_id(driver, package, "settings", "settings menu item", timeout=2.0):
        return True
    if safe_click_description_contains(driver, d["settings_title"], timeout=2.0):
        return True
    if safe_click_description_contains(driver, "More options", timeout=1.5):
        time.sleep(0.3)
        if safe_click_text_contains(driver, d["settings_title"], timeout=2.0):
            return True
    return safe_click_text_contains(driver, d["settings_title"], timeout=2.0)


def _about_menu_item_visible(driver: Any, package: str, d: dict[str, str]) -> bool:
    """True iff the About AppBar item is currently in the home toolbar's visible affordances."""
    if _resource_id_present(driver, package, "about", timeout=1.2):
        return True
    esc = d["about_title"].replace('"', '\\"')
    if element_present_uiautomator(
        driver,
        'new UiSelector().descriptionContains("%s")' % esc,
        timeout=1.0,
    ):
        return True
    # Last fallback: visible text in toolbar (some Material themes render menu titles as text).
    return element_present_uiautomator(
        driver,
        'new UiSelector().className("android.widget.TextView").text("%s")' % esc,
        timeout=0.8,
    )


# ---------------------------------------------------------------------------
# Per-scenario implementations
# ---------------------------------------------------------------------------


def _build_scenarios(
    driver: Any,
    package: str,
    d: dict[str, str],
    deadline_monotonic: float,
) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
    """Construct the ``[(name, fn), ...]`` scenario list closing over driver / defaults.

    Pattern mirrors the tipuous + metronome modules. Keeping scenarios inside a builder avoids
    polluting the module namespace with single-use callables while still letting the workload
    runner iterate them as a registry.
    """

    # -----------------------------------------------------------------------
    # 1. shownInHome: mirror of AboutMenuItemVisibilityTest.shownInHome_Test
    # -----------------------------------------------------------------------

    def scenario_shown_in_home() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "AboutMenuItemVisibilityTest",
            "espresso_method": "shownInHome_Test",
        }
        cov["home_main_ui_visible"] = main_documenter_visible(driver, package, d)
        cov["about_menu_visible_in_home"] = _about_menu_item_visible(driver, package, d)
        # Substantive home tour (replaces the previous passive-only ~3s pre-flight): tap the FAB
        # to engage the SAF launch path, back out, then scroll the home RecyclerView to load any
        # cached tiles. Adds ~5-7s of real interaction so the scenario contributes energy signal
        # instead of acting as a free gate.
        fab_tapped = _click_resource_id(
            driver, package, "open_button", "home open FAB (shownInHome tour)", timeout=2.5,
        ) or safe_click_description_contains(driver, d["open_fab_desc"], timeout=2.0)
        cov["home_tour_fab_tapped"] = fab_tapped
        if fab_tapped:
            # Brief active poll so SAF picker has a chance to render before we back out.
            poll_deadline = time.monotonic() + 1.0
            while time.monotonic() < poll_deadline:
                try:
                    if (driver.current_package or "") != package:
                        break
                except Exception:
                    break
                if element_present_uiautomator(
                    driver, 'new UiSelector().packageName("com.google.android.documentsui")',
                    timeout=0.25,
                ):
                    break
            _safe_back(driver, "back from SAF (shownInHome tour)")
            ensure_foreground(driver, package)
        tour_swipes = 0
        for from_frac, to_frac in ((0.75, 0.35), (0.30, 0.70)):
            try:
                swipe_vertical(driver, from_frac, to_frac, 320)
                tour_swipes += 1
            except Exception as ex:
                log("home tour swipe failed: %s %s" % (type(ex).__name__, ex))
        cov["home_tour_swipes"] = tour_swipes
        # Re-assert About item visibility after the tour to confirm the toolbar survived.
        cov["about_menu_visible_after_tour"] = _about_menu_item_visible(driver, package, d)
        return cov

    # -----------------------------------------------------------------------
    # 2. aboutMenuHiddenInAbout: fused mirror of notShownInAbout_Test + notShownInAboutLibraries_Test
    # -----------------------------------------------------------------------

    def scenario_about_menu_hidden_in_about() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "AboutMenuItemVisibilityTest",
            "espresso_method": "notShownInAbout_Test+notShownInAboutLibraries_Test",
        }
        ok_about = _click_about_menu(driver, package, d)
        cov["about_menu_clicked"] = ok_about
        time.sleep(0.6)
        # Post-condition for the first upstream test: About item gone in the About fragment.
        cov["about_menu_hidden_after_about_click"] = not _about_menu_item_visible(
            driver, package, d,
        )
        # Then click Show licenses → about libs screen; About item still gone.
        ok_libs = _click_resource_id(
            driver, package, "show_licenses", "Show licenses button", timeout=3.0,
        ) or safe_click_text_contains(driver, d["show_licenses_title"], timeout=2.0)
        cov["show_licenses_clicked"] = ok_libs
        # Active fill (~1.0s): scroll the licenses RecyclerView so the libraries list does real
        # render work while energy is sampled, instead of an idle wait for the screen to settle.
        libs_swipes = 0
        for from_frac, to_frac in ((0.75, 0.35), (0.30, 0.70)):
            try:
                swipe_vertical(driver, from_frac, to_frac, 320)
                libs_swipes += 1
            except Exception as ex:
                log("about-libs swipe failed: %s %s" % (type(ex).__name__, ex))
        cov["about_libs_swipes"] = libs_swipes
        cov["about_menu_hidden_after_show_licenses"] = not _about_menu_item_visible(
            driver, package, d,
        )
        # Clean up: back twice to return to home for the next scenario.
        _safe_back(driver, "back from about libs")
        _safe_back(driver, "back from about to home")
        time.sleep(0.4)
        ensure_foreground(driver, package)
        return cov

    # -----------------------------------------------------------------------
    # 3. aboutShownAfterReturnFromAboutLibraries: fused mirror of the two return-from-libs tests
    # -----------------------------------------------------------------------

    def scenario_about_shown_after_return_from_about_libraries() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "AboutMenuItemVisibilityTest",
            "espresso_method": (
                "notShownAfterReturnFromAboutLibraries_Test"
                "+shownInHomeAfterAfterReturnFromAboutLibraries_Test"
            ),
        }
        ok_about = _click_about_menu(driver, package, d)
        cov["about_menu_clicked"] = ok_about
        time.sleep(0.55)
        ok_libs = _click_resource_id(
            driver, package, "show_licenses", "Show licenses button", timeout=3.0,
        ) or safe_click_text_contains(driver, d["show_licenses_title"], timeout=2.0)
        cov["show_licenses_clicked"] = ok_libs
        # Active fill (~1.0s): scroll the libraries list while energy is sampled.
        libs_swipes = 0
        for from_frac, to_frac in ((0.78, 0.30), (0.28, 0.72)):
            try:
                swipe_vertical(driver, from_frac, to_frac, 320)
                libs_swipes += 1
            except Exception as ex:
                log("about-libs return swipe failed: %s %s" % (type(ex).__name__, ex))
        cov["about_libs_swipes"] = libs_swipes
        ok_back_libs = _safe_back(driver, "back from about libs to about")
        cov["back_after_libs_pressed"] = ok_back_libs
        time.sleep(0.7)
        # First post-condition: we should be in the About fragment (libs left), About menu still gone.
        cov["about_menu_hidden_after_back_from_libs"] = not _about_menu_item_visible(
            driver, package, d,
        )
        ok_back_home = _safe_back(driver, "back from about to home")
        cov["back_to_home_pressed"] = ok_back_home
        time.sleep(0.6)
        ensure_foreground(driver, package)
        # Second post-condition: we should be on home and the About menu item is visible.
        cov["about_menu_visible_in_home_after_double_back"] = _about_menu_item_visible(
            driver, package, d,
        )
        return cov

    # -----------------------------------------------------------------------
    # 4. openFilePicker: mirror of OpenTest.testParameterizedFile (FAB portion only)
    # -----------------------------------------------------------------------

    def scenario_open_file_picker() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "OpenTest",
            "espresso_method": "testParameterizedFile_fab_portion",
        }
        # Try resource-id click first, then content-description fallback.
        ok_fab = _click_resource_id(
            driver, package, "open_button", "home open FAB", timeout=3.0,
        ) or safe_click_description_contains(driver, d["open_fab_desc"], timeout=2.5)
        cov["open_fab_clicked"] = ok_fab
        # Active poll (replaces 1.5s idle wait): break early as soon as a SAF signal lands;
        # use short element_present_uiautomator probes so each iteration does real lookup work.
        saf_deadline = time.monotonic() + 1.5
        cur_pkg = ""
        package_switched = False
        saf_label_visible = False
        while time.monotonic() < saf_deadline:
            try:
                cur_pkg = driver.current_package or ""
            except Exception:
                cur_pkg = ""
            package_switched = bool(cur_pkg) and cur_pkg != package
            if element_present_uiautomator(
                driver, 'new UiSelector().textContains("Recent")', timeout=0.25,
            ) or element_present_uiautomator(
                driver, 'new UiSelector().textContains("Downloads")', timeout=0.25,
            ) or element_present_uiautomator(
                driver, 'new UiSelector().packageName("com.google.android.documentsui")', timeout=0.25,
            ):
                saf_label_visible = True
                break
            if package_switched:
                break
        cov["foreground_package_after_fab"] = cur_pkg
        cov["saf_picker_or_overlay_visible"] = bool(package_switched or saf_label_visible)
        # Substantive add: try scrolling within the SAF picker to exercise the documentsui surface
        # before backing out: turns a "tap and back" trivial scenario into real picker navigation.
        saf_swipes = 0
        for from_frac, to_frac in ((0.70, 0.35), (0.30, 0.70)):
            try:
                swipe_vertical(driver, from_frac, to_frac, 320)
                saf_swipes += 1
            except Exception as ex:
                log("saf picker swipe failed: %s %s" % (type(ex).__name__, ex))
        cov["saf_picker_swipes"] = saf_swipes
        # Return to home for the next scenario.
        _safe_back(driver, "back from SAF picker to documenter home")
        time.sleep(0.6)
        ensure_foreground(driver, package)
        return cov

    # -----------------------------------------------------------------------
    # 5. settingsTogglePreferences: exploration, exercises the conversion-config preferences
    # -----------------------------------------------------------------------

    def scenario_settings_toggle_preferences() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MasterExperiment",
            "espresso_method": "settingsTogglePreferences",
            "preference_titles": [
                d["pref_outline_title"],
                d["pref_annotations_title"],
                d["pref_nographics_title"],
            ],
            "toggles_clicked_count": 0,
            "toggles_flipped_count": 0,
        }
        ok_settings = _click_settings_menu(driver, package, d)
        cov["settings_opened"] = ok_settings
        time.sleep(0.8)
        # For each preference row, record its toggle state (best-effort: read whatever switch the
        # toolbar paired with the row), click the row, record the new state, and increment the
        # appropriate counters. PreferenceScreens are RecyclerViews: items below the fold need
        # scrolling, hence safe_scroll_into_view_text_contains.
        for pref_key, title in (
            ("outline", d["pref_outline_title"]),
            ("annotations", d["pref_annotations_title"]),
            ("nographics", d["pref_nographics_title"]),
        ):
            before_checked = _read_nearest_switch_state(driver, title)
            cov["pref_%s_before_checked" % pref_key] = before_checked
            clicked = safe_scroll_into_view_text_contains(driver, title, timeout=5.0)
            cov["pref_%s_clicked" % pref_key] = clicked
            if clicked:
                cov["toggles_clicked_count"] = int(cov.get("toggles_clicked_count", 0)) + 1
                time.sleep(0.5)
                after_checked = _read_nearest_switch_state(driver, title)
                cov["pref_%s_after_checked" % pref_key] = after_checked
                flipped = (before_checked is not None and after_checked is not None
                           and before_checked != after_checked)
                cov["pref_%s_flipped" % pref_key] = flipped
                if flipped:
                    cov["toggles_flipped_count"] = int(cov.get("toggles_flipped_count", 0)) + 1
        # Return to home.
        _safe_back(driver, "back from settings to home")
        time.sleep(0.6)
        ensure_foreground(driver, package)
        cov["returned_to_home_after_settings"] = main_documenter_visible(driver, package, d)
        return cov

    # -----------------------------------------------------------------------
    # 6. openDocumentAttempt: inspired, exercises the native-conversion energy axis
    # -----------------------------------------------------------------------

    def scenario_open_document_attempt() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MasterExperiment",
            "espresso_method": "openDocumentAttempt",
        }
        # Increased default 20 s → 35 s (2026-05-12 fix): on first smoke, Pixel 9 / Android 16
        # SAF entry-point labels didn't match "Recent"/"Downloads" because the picker opens
        # directly on the per-app virtual hub (Documents UI 16 default). The conversion path
        # never engaged within the original window. We now (a) wait longer for the picker to
        # render before clicking, (b) try more entry labels including "Documents" / "All files" /
        # "Files" / "Show internal storage", (c) extend the conversion observation window so
        # libpdf2htmlEX has time to render even on slower variants.
        wait_s = max(2.0, env_float("DOCUMENTER_CONVERT_WAIT_S", 35.0))
        ok_fab = _click_resource_id(
            driver, package, "open_button", "home open FAB", timeout=3.0,
        ) or safe_click_description_contains(driver, d["open_fab_desc"], timeout=2.5)
        cov["open_fab_clicked"] = ok_fab
        # Active poll (replaces 2.5s idle wait): exit early when either the foreground package
        # switches to documentsui or a known SAF label appears. Caps at 2.5s for slow Android 16
        # picker render but normally exits in <0.8s on a warm picker.
        saf_ready_deadline = time.monotonic() + 2.5
        saf_ready = False
        while time.monotonic() < saf_ready_deadline:
            try:
                cur_pkg_saf = driver.current_package or ""
            except Exception:
                cur_pkg_saf = ""
            if cur_pkg_saf and cur_pkg_saf != package:
                saf_ready = True
                break
            if element_present_uiautomator(
                driver, 'new UiSelector().packageName("com.google.android.documentsui")',
                timeout=0.3,
            ) or element_present_uiautomator(
                driver, 'new UiSelector().textContains("Recent")', timeout=0.25,
            ) or element_present_uiautomator(
                driver, 'new UiSelector().textContains("Downloads")', timeout=0.25,
            ):
                saf_ready = True
                break
        cov["saf_picker_ready"] = saf_ready
        # Attempt to navigate the SAF picker. 2026-07-25 reordered: try sample.pdf
        # by name FIRST (SAF sometimes surfaces recent files across all sources),
        # then Downloads (where fixture-sync pushed sample.pdf), then Documents /
        # All files / drawer entries as fallbacks. "Recent" is LAST because on
        # a fresh install it's usually empty and would short-circuit navigation
        # away from the real fixture path. Once inside a nav entry the file-
        # extension inner loop tries to tap sample.pdf.
        nav_any = False
        for entry in ("sample.pdf", "Downloads", "Documents", "All files", "Files",
                      "Show internal storage", "Internal storage", "Recent"):
            if safe_click_text_contains(driver, entry, timeout=1.2):
                nav_any = True
                cov["saf_entry_clicked"] = entry
                time.sleep(0.9)
                # If we directly tapped the sample file by name, the picker fires the callback
                # immediately: skip further nav.
                if entry == "sample.pdf":
                    break
                # For drawer/storage entries we may still need an additional inner tap;
                # the next loop (file-extension tap) handles that.
                if entry not in ("Recent", "Downloads", "Documents"):
                    continue
                break
        # Once inside, try to tap the first file-list entry (UiAutomator surfaces SAF list items
        # as android.widget.TextView with the filename as visible text; we tap the first one we
        # can confirm).
        if nav_any:
            for ext in (".pdf", ".doc"):
                tapped = _click_first_text_ending_with(driver, ext, timeout=2.5)
                if tapped:
                    cov["saf_selected_extension"] = ext
                    break
            else:
                # Even if no ext-matching item, tap the topmost row to fire the chooser callback:
                # the pushed fixture may have a non-standard extension.
                _click_first_item_in_saf_list(driver)
        cov["saf_navigate_attempted"] = nav_any
        # Wait for any of: loading indicator (R.id.loading / "Converting" text), the documentView
        # WebView (R.id.documentView), or a new converted tile in the home RecyclerView.
        deadline = time.monotonic() + wait_s
        loading_seen = False
        webview_seen = False
        tile_seen = False
        # Short no-signal grace: if nav_any was False (no fixture, no nav clicks landed) OR
        # nothing fires within the first 3 s, we abandon the idle-leaning convert-poll loop and
        # spend the rest of the budget on substantive UI work instead (settings toggle + scrolls).
        no_signal_grace_s = 3.0 if not nav_any else 6.0
        grace_deadline = time.monotonic() + no_signal_grace_s
        while time.monotonic() < deadline:
            if not loading_seen:
                loading_seen = _resource_id_present(driver, package, "loading", timeout=0.6)
                if not loading_seen:
                    for substr in d["loading_text_substrs"].split("|"):
                        substr = substr.strip()
                        if substr and element_present_uiautomator(
                            driver,
                            'new UiSelector().textContains("%s")' % substr.replace('"', '\\"'),
                            timeout=0.5,
                        ):
                            loading_seen = True
                            break
            if not webview_seen:
                webview_seen = _resource_id_present(driver, package, "documentView", timeout=0.4)
            if not tile_seen:
                tile_seen = _resource_id_present(driver, package, "recycler_view", timeout=0.4) and (
                    _resource_id_present(driver, package, "filename", timeout=0.4)
                    or _resource_id_present(driver, package, "thumbnail", timeout=0.4)
                )
            if loading_seen or webview_seen or tile_seen:
                # Continue draining the wait_s budget: the energy signal benefits from the full
                # conversion window even after the first signal lands. But break if we've already
                # observed the converted-tile state (terminal).
                if tile_seen:
                    break
            elif time.monotonic() >= grace_deadline:
                # No signal fired within the grace window: break out and do active substitute
                # work below instead of polling for signals that will never come.
                break
            time.sleep(0.4)
        cov["loading_indicator_visible"] = loading_seen
        cov["document_webview_visible"] = webview_seen
        cov["converted_tile_present"] = tile_seen
        # If we broke out of the convert loop with no signal AND the budget is not yet spent,
        # spend the remainder on substantive interaction: bounce out, re-open Settings, scroll the
        # preference list. This keeps the energy-axis sampling honest (active CPU work) instead of
        # idle polling.
        substitute_swipes = 0
        if not (loading_seen or webview_seen or tile_seen) and time.monotonic() < deadline - 1.0:
            # Step out of the SAF picker so we can engage app-owned surfaces.
            _safe_back(driver, "back from idle SAF (substitute work)")
            ensure_foreground(driver, package)
            _click_settings_menu(driver, package, d)
            sub_end = deadline
            while time.monotonic() < sub_end - 0.5:
                from_frac, to_frac = (0.75, 0.30) if substitute_swipes % 2 == 0 else (0.30, 0.70)
                try:
                    swipe_vertical(driver, from_frac, to_frac, 320)
                    substitute_swipes += 1
                except Exception as ex:
                    log("substitute swipe failed: %s %s" % (type(ex).__name__, ex))
                    break
                # Toggle a preference every other swipe to exercise SharedPreferences write path.
                if substitute_swipes % 2 == 0:
                    safe_scroll_into_view_text_contains(driver, d["pref_outline_title"], timeout=1.2)
            _safe_back(driver, "back from substitute settings")
        cov["substitute_swipes"] = substitute_swipes
        # Always try to return to home regardless of outcome (back from WebView if present, then
        # back / dismiss if still in SAF or system dialog).
        _safe_back(driver, "back from documentView / saf to documenter home")
        time.sleep(0.5)
        _safe_back(driver, "back twice to home as safety")
        time.sleep(0.4)
        ensure_foreground(driver, package)
        return cov

    # -----------------------------------------------------------------------
    # 7. homeScrollAndIdle: exploration, RecyclerView swipes + foreground idle
    # -----------------------------------------------------------------------

    def scenario_home_scroll_and_idle() -> dict[str, Any]:
        cov: dict[str, Any] = {
            "espresso_class": "MasterExperiment",
            "espresso_method": "homeScrollAndIdle",
        }
        # 2026-05-12 fix: aggressive home recovery. The PREVIOUS scenario (openDocumentAttempt)
        # may have left us deep inside the SAF picker / system UI if conversion didn't engage.
        # Two safety back-presses + re-foreground + 4 s retry loop for the main-UI predicate.
        ensure_foreground(driver, package)
        time.sleep(0.4)
        for _ in range(2):
            _safe_back(driver, "home-recovery back")
            time.sleep(0.5)
        ensure_foreground(driver, package)
        # Retry main-UI predicate for up to 4 s: the FAB / recycler_view may take time to
        # re-render after the back navigation.
        home_visible = False
        for _ in range(8):
            if main_documenter_visible(driver, package, d):
                home_visible = True
                break
            time.sleep(0.5)
        cov["home_main_ui_visible"] = home_visible
        swipes = 0
        for from_frac, to_frac in (
            (0.75, 0.30),
            (0.30, 0.70),
            (0.65, 0.35),
            (0.25, 0.75),
        ):
            if time.monotonic() >= deadline_monotonic - 2:
                break
            try:
                swipe_vertical(driver, from_frac, to_frac, 380)
                swipes += 1
                time.sleep(0.55)
            except Exception as ex:
                log("home swipe failed: %s %s" % (type(ex).__name__, ex))
        cov["swipes_issued"] = swipes > 0
        cov["swipes_count"] = swipes
        # Substantive replacement for the previous 1.5s idle loop: bounce About menu open + back
        # to exercise the toolbar animation + fragment lifecycle while still on the home surface.
        about_bounces = 0
        bounce_deadline = time.monotonic() + 1.5
        while time.monotonic() < bounce_deadline and time.monotonic() < deadline_monotonic - 1:
            if _click_about_menu(driver, package, d):
                about_bounces += 1
                _safe_back(driver, "back from about bounce (homeScrollAndIdle)")
            else:
                break
        cov["about_bounces"] = about_bounces
        return cov

    return [
        ("shownInHome", scenario_shown_in_home),
        # 2026-07-26: aboutMenuHiddenInAbout + aboutShownAfterReturnFromAboutLibraries
        # removed from the workload (see DOCUMENTER_SCENARIO_LIST for rationale).
        # Dispatchers kept below but only fire under
        # ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1 (calibration mode).
        ("aboutMenuHiddenInAbout", scenario_about_menu_hidden_in_about),
        (
            "aboutShownAfterReturnFromAboutLibraries",
            scenario_about_shown_after_return_from_about_libraries,
        ),
        ("openFilePicker", scenario_open_file_picker),
        ("settingsTogglePreferences", scenario_settings_toggle_preferences),
        ("openDocumentAttempt", scenario_open_document_attempt),
        ("homeScrollAndIdle", scenario_home_scroll_and_idle),
        # 2026-07-26: aliases for loop-back scenarios (same functions, different keys).
        # openDocumentAttempt_repeat2 removed: intermittent, see SCENARIO_LIST notes.
        ("settingsTogglePreferences_repeat2", scenario_settings_toggle_preferences),
        ("openFilePicker_repeat2", scenario_open_file_picker),
        ("homeScrollAndIdle_repeat2", scenario_home_scroll_and_idle),
    ]


# ---------------------------------------------------------------------------
# Helpers used by scenarios above
# ---------------------------------------------------------------------------


def _read_nearest_switch_state(driver: Any, title_substr: str) -> Optional[bool]:
    """Best-effort: read the checked state of the SwitchPreferenceCompat row whose title contains ``title_substr``.

    PreferenceScreens render each row as a vertical LinearLayout: title + summary on the left, a
    ``android.widget.Switch`` on the right (or an ``androidx.appcompat.widget.SwitchCompat``,
    surfaced as ``android.widget.Switch`` in the UiAutomator tree). We locate the title TextView,
    walk up to the row container, and find the first Switch underneath; return ``el.is_selected()``
    (Appium maps ``checked`` to ``selected`` on Switch widgets). Returns ``None`` on any failure
    so the caller can treat it as "unknown" rather than "definitely off".
    """
    from appium.webdriver.common.appiumby import AppiumBy

    if not title_substr:
        return None
    try:
        # First locate the title TextView and grab its bounds; we'll find a Switch on the same row.
        esc = title_substr.replace('"', '\\"')
        sel = (
            'new UiSelector().className("android.widget.TextView").textContains("%s")' % esc
        )
        title_els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if not title_els:
            return None
        title_el = title_els[0]
        title_loc = title_el.location
        title_size = title_el.size
        title_center_y = title_loc["y"] + title_size["height"] // 2
        # Now enumerate Switch widgets and pick the one whose vertical center is closest to the
        # title's vertical center (proxy for "same row").
        switch_sel = 'new UiSelector().className("android.widget.Switch")'
        switch_els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, switch_sel)
        if not switch_els:
            return None
        best = None
        best_dy = 10_000_000
        for sw in switch_els:
            try:
                sw_loc = sw.location
                sw_size = sw.size
                sw_center_y = sw_loc["y"] + sw_size["height"] // 2
                dy = abs(sw_center_y - title_center_y)
                if dy < best_dy:
                    best = sw
                    best_dy = dy
            except Exception:
                continue
        if best is None:
            return None
        # The 'checked' attribute is the canonical signal on Android Switch widgets; some Appium
        # versions surface it via get_attribute("checked"), others via is_selected().
        try:
            attr = best.get_attribute("checked")
            if attr is not None:
                return str(attr).strip().lower() == "true"
        except Exception:
            pass
        try:
            return bool(best.is_selected())
        except Exception:
            return None
    except Exception as ex:
        log("read switch state for %r failed: %s %s" % (title_substr, type(ex).__name__, ex))
        return None


def _click_first_text_ending_with(driver: Any, suffix: str, timeout: float = 2.0) -> bool:
    """Click the first visible TextView whose text ends with ``suffix`` (e.g. ``.pdf``)."""
    from appium.webdriver.common.appiumby import AppiumBy

    if not suffix:
        return False
    try:
        sel = (
            'new UiSelector().className("android.widget.TextView").textEndsWith("%s")'
            % suffix.replace('"', '\\"')
        )
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if not els:
            return False
        els[0].click()
        log("clicked text ending with %r" % suffix)
        time.sleep(0.45)
        return True
    except Exception as ex:
        log("click text ending with %r failed: %s %s" % (suffix, type(ex).__name__, ex))
        return False


def _click_first_item_in_saf_list(driver: Any) -> bool:
    """Click the first selectable list item inside the SAF picker. Best-effort, never raises."""
    from appium.webdriver.common.appiumby import AppiumBy

    # SAF list items usually surface as `android.widget.LinearLayout` clickable rows or
    # `android.view.ViewGroup`-rooted entries; we try a few common containers.
    for class_name in (
        "android.widget.LinearLayout",
        "androidx.recyclerview.widget.RecyclerView",
        "android.view.ViewGroup",
    ):
        sel = (
            'new UiSelector().className("%s").clickable(true).instance(0)' % class_name
        )
        try:
            els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
            if els:
                els[0].click()
                log("clicked first SAF list item (className=%s)" % class_name)
                time.sleep(0.5)
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Workload runner: espresso_mirror equivalent (the only mode for documenter).
# ---------------------------------------------------------------------------


def run_espresso_mirror_workload(
    driver: Any,
    package: str,
    deadline_monotonic: float,
    dump_dir: Optional[str],
) -> int:
    """Run the locked :data:`DOCUMENTER_SCENARIO_LIST` end-to-end and write the scenario report.

    Mirrors the structural pattern of :func:`appium_android_tests.tipuous.scenarios.run_espresso_mirror_workload`:
    per-scenario coverage rows go to ``appium_workload_coverage.jsonl``, aggregate counts go to
    ``espresso_mirror_scenario_report.{json,txt}``. Wall time is a measured output; the legacy
    ``deadline_monotonic`` is preserved in the helper signature only: callers under the per-app
    contract pass ``+inf``.

    Override (one-time calibration only): ``ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1`` runs every
    scenario in the candidate list (currently identical to the locked list).
    """
    d = _documenter_defaults()
    await_app_ready(
        driver,
        package,
        deadline_monotonic,
        is_main_ui_visible=lambda drv: main_documenter_visible(drv, package, d),
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
                "total_strict_ui_pass": sum(
                    v.get("success_strict_ui", 0) for v in by_scenario.values()
                ),
                "total_action_only_pass": sum(
                    v.get("success_action_only", 0) for v in by_scenario.values()
                ),
            },
        }
        lines = [
            "Documenter espresso-mirror scenario report (Appium-derived checks)",
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

    # Filter to the locked DOCUMENTER_SCENARIO_LIST (in that exact order). Override with
    # ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1 for one-time calibration runs.
    if env_truthy("ESPRESSO_MIRROR_RUN_ALL_SCENARIOS"):
        log(
            "documenter espresso_mirror: ESPRESSO_MIRROR_RUN_ALL_SCENARIOS=1 — running all %d "
            "candidate scenarios (calibration mode, NOT for production sweeps)" % len(scenarios),
        )
        ordered_scenarios = scenarios
    else:
        by_name = {n: fn for n, fn in scenarios}
        missing = [n for n in DOCUMENTER_SCENARIO_LIST if n not in by_name]
        if missing:
            log(
                "ERROR: locked DOCUMENTER_SCENARIO_LIST entries not implemented in scenarios.py: "
                "%s — falling back to full candidate list to avoid hard-failing the run" % missing,
            )
            ordered_scenarios = scenarios
        else:
            ordered_scenarios = [(n, by_name[n]) for n in DOCUMENTER_SCENARIO_LIST]
            log(
                "documenter espresso_mirror: per-app contract — running %d locked scenarios "
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
            "documenter espresso_mirror scenario %s (step #%s/%s, no time-box, locked-list)"
            % (scenario_name, step_count, len(ordered_scenarios)),
        )
        try:
            cov = scenario_fn()
        except Exception as ex:
            log(
                "documenter scenario %s raised %s: %s — recording as failure and continuing"
                % (scenario_name, type(ex).__name__, ex),
            )
            cov = {
                "espresso_class": "MasterExperiment",
                "espresso_method": scenario_name,
                "scenario_raised": "%s: %s" % (type(ex).__name__, ex),
            }
            save_page_source(driver, dump_dir, "scenario_raised_%s" % scenario_name)
        cov["workload"] = "espresso_mirror"
        cov["scenario_name"] = scenario_name
        cov["suite_round"] = suite_round
        cov["ts"] = scenario_ts
        cov["scenario_wall_seconds"] = round(time.monotonic() - scenario_started, 3)
        cov["ts_end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        strict_ok, action_ok, fail_reasons = _documenter_scoring.score_scenario(
            scenario_name, cov,
        )
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
        log("documenter espresso_mirror coverage: %s" % json.dumps(cov, sort_keys=True))
        append_coverage_jsonl(dump_dir, cov)

    if step_count == 0:
        log("documenter espresso_mirror: no scenarios ran (await_app_ready failed?)")
        finalize_report()
        return 0
    finalize_report()
    return step_count


# ---------------------------------------------------------------------------
# Public entrypoint: Android Runner ``interaction`` hook target.
# ---------------------------------------------------------------------------


def run_workload(experiment: Any, device: Any) -> None:
    """Entrypoint called by the thin ``Scripts/interaction_appium_documenter.py`` Android Runner hook.

    Reads ``package`` and ``duration`` from ``experiment``, builds Appium options, starts the
    workload thread, and blocks ``main()`` until the locked scenario list completes (with a
    failsafe cap). The calling hook should set ``"interaction_covers_duration": true`` in the
    experiment JSON to avoid a duplicate ``NativeExperiment`` sleep.

    Workload selection: ``APPIUM_WORKLOAD={espresso_mirror}`` (default ``espresso_mirror``;
    ``generic`` and ``metronome`` modes intentionally have no Documenter analogue and would log a
    warning + fall back to ``espresso_mirror``).
    """
    package = getattr(experiment, "package", None) if experiment is not None else None
    duration_s = float(getattr(experiment, "duration", 60.0)) if experiment is not None else 60.0

    if not package:
        log("No package found; skipping.")
        return

    server = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723").rstrip("/")
    activity = os.environ.get("DOCUMENTER_ACTIVITY", "com.viliussutkus89.documenter.ui.MainActivity")
    workload_mode = os.environ.get("APPIUM_WORKLOAD", "espresso_mirror").strip().lower()
    if workload_mode and workload_mode != "espresso_mirror":
        log(
            "documenter: APPIUM_WORKLOAD=%r is not implemented for this app; falling back to "
            "espresso_mirror." % workload_mode,
        )
        workload_mode = "espresso_mirror"
    dump_dir = resolve_dump_dir()

    print("=INTERACTION_APPIUM_DOCUMENTER=")
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
    print("DOCUMENTER_CONVERT_WAIT_S:", env_float("DOCUMENTER_CONVERT_WAIT_S", 20.0))

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
                % (len(DOCUMENTER_SCENARIO_LIST), handshake_s, duration_s),
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
        workload, failsafe_cap_s, thread_name="appium-documenter-workload",
    )
