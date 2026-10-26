"""
Appium UiAutomator2 driver lifecycle + generic UI helpers.

This module owns:

- Construction of ``UiAutomator2Options`` from the ``APPIUM_*`` env-var contract.
- Wall-clock fail-fast ``webdriver.Remote()`` session creation
  (:func:`connect_remote_webdriver`). Selenium's ``ClientConfig`` / urllib3 read timeouts often do
  not actually abort a stuck ``POST /session``, so we run the connect in a daemon thread and
  ``join(timeout)`` against ``APPIUM_SESSION_CONNECT_TIMEOUT_S``.
- Post-handshake HTTP timeout bump (:func:`bump_driver_http_timeout`) so subsequent UI commands
  inherit a budget proportional to ``duration_s`` instead of the short connect cap.
- Generic black-box UI helpers: ``ensure_foreground``, ``dismiss_system_dialogs``,
  ``wait_for_package``, ``await_app_ready``, plus selector-based click / scroll / swipe wrappers.

Env-var contract owned here:

- ``APPIUM_SERVER_URL``: base URL for the Appium server (default ``http://127.0.0.1:4723``).
  Stripped of trailing slash. **Read by the per-app entrypoint, not directly by this module**, but
  documented here for grouping with the other connect-time env vars.
- ``APPIUM_SESSION_CONNECT_TIMEOUT_S``: wall-clock cap for ``webdriver.Remote()`` (default ``120``,
  clamped to ``[5, 900]``). Exceeding it raises :class:`SessionConnectTimeoutError` and writes an
  ``appium_status.json`` with ``session_timeout=True``.
- ``APPIUM_SKIP_U2_INSTALL``: if truthy, skip pushing UiAutomator2 server APKs (faster
  reconnects, but unsafe for first connect).
- ``APPIUM_FORCE_U2_INSTALL``: if truthy, never skip server install (overrides the SKIP
  flag).
- ``APPIUM_ATTACH_ONLY``: if truthy, set ``autoLaunch=false`` and skip
  ``app_activity``; the caller is responsible for already having the app foregrounded (e.g. monkey
  pre-launch). Default off: Appium opens the configured activity itself, which is more reliable.
- ``APPIUM_APP_WAIT_DURATION_MS``: Appium's ``app_wait_duration`` (default ``10000``). Lower
  values fail faster on stuck splashes; higher values give packed APKs more headroom.
- ``APPIUM_WAIT_FOR_PACKAGE_S``: :func:`wait_for_package` timeout for the foreground package
  to settle (default ``60``).
- ``APPIUM_WAIT_MAIN_UI_S``: :func:`await_app_ready` budget for app-specific main-UI
  detection after the foreground package is correct (default ``45``).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from . import emit_stderr, env_float, env_int, env_truthy, log


class SessionConnectTimeoutError(TimeoutError):
    """Raised when ``webdriver.Remote()`` does not finish within ``APPIUM_SESSION_CONNECT_TIMEOUT_S``.

    Distinct from generic ``TimeoutError`` so callers can bucket it as ``session_timeout=True`` in
    the status JSON without false-positives from later workload-side timeouts.
    """


def is_likely_http_timeout(exc: BaseException) -> bool:
    """Heuristic: did this exception come from an underlying urllib3 / socket read timeout?

    Used to classify connect-time exceptions as ``session_timeout`` (vs an Appium-side error
    response). Conservative: if the message contains "timeout" / "timed out" the answer is yes.
    """
    try:
        from urllib3.exceptions import ReadTimeoutError  # noqa: WPS433

        if isinstance(exc, ReadTimeoutError):
            return True
    except ImportError:
        pass
    if type(exc).__name__ in ("ReadTimeoutError", "TimeoutError", "SocketTimeout"):
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


def connect_remote_webdriver(
    wd_mod: Any,
    server: str,
    options: Any,
    connect_timeout_s: float,
    artifact_dir: Optional[str],
) -> Any:
    """Create an Appium ``Remote`` driver with a wall-clock cap on session creation.

    Selenium ``ClientConfig`` / urllib3 read timeouts have been observed to not actually abort a
    blocked ``POST /session`` against Appium. We run ``webdriver.Remote()`` in a daemon thread and
    ``join(timeout)`` against ``connect_timeout_s``. On expiry:

    - Writes ``appium_status.json`` via :func:`status.write_status_raw` (no logging dep: safe even
      late in interpreter shutdown).
    - Raises :class:`SessionConnectTimeoutError`. The connect thread may still be running when we
      raise; if Appium recovers it eventually exits on its own. If you suspect a wedged Appium
      server, restart it.
    """
    from .status import write_status_raw

    result: dict[str, Any] = {}

    def attempt() -> None:
        try:
            result["driver"] = wd_mod.Remote(server, options=options)
        except BaseException as ex:
            result["error"] = ex

    t = threading.Thread(target=attempt, name="appium-session-connect", daemon=True)
    t.start()
    deadline = time.monotonic() + float(connect_timeout_s)
    emit_stderr(
        "INTERACTION_APPIUM [stderr]: session connect wait started (%.1fs cap)" % connect_timeout_s,
    )
    while time.monotonic() < deadline:
        if not t.is_alive():
            break
        time.sleep(0.25)
    if t.is_alive():
        fail_payload = {
            "session_created": False,
            "session_start_seconds": None,
            "workload_started": False,
            "steps_executed": 0,
            "session_connect_timeout_seconds": connect_timeout_s,
            "failure_reason": "thread poll timeout waiting for webdriver.Remote()",
            "session_timeout": True,
            "note": "daemon connect thread may still be blocked inside Appium POST /session",
        }
        write_status_raw(artifact_dir, fail_payload)
        msg = (
            "session connect still blocked after %.1fs — declaring SESSION_TIMEOUT (daemon thread may "
            "still be waiting on Appium)" % connect_timeout_s
        )
        log(msg)
        emit_stderr("INTERACTION_APPIUM [stderr]: %s" % msg)
        raise SessionConnectTimeoutError(
            "Appium session creation exceeded %.1fs (thread join timeout)" % connect_timeout_s,
        )
    if "error" in result:
        raise result["error"]
    if "driver" not in result:
        raise RuntimeError("Appium Remote() finished without driver or error")
    return result["driver"]


def bump_driver_http_timeout(driver: Any, duration_s: float) -> None:
    """Lengthen the HTTP timeout once a session exists.

    The connect-time HTTP timeout is intentionally short (so a stuck connect fails fast); after the
    handshake we want subsequent UI commands to have enough budget for slow packed-APK responses.
    Best-effort: Selenium versions differ in how they expose ``ClientConfig``.
    """
    long_timeout = float(max(120.0, int(duration_s) + 60.0))
    try:
        executor = getattr(driver, "command_executor", None)
        if executor is None:
            return
        for attr in ("client_config", "_client_config"):
            cc = getattr(executor, attr, None)
            if cc is not None and hasattr(cc, "timeout"):
                setattr(cc, "timeout", long_timeout)
                return
    except Exception:
        pass


def build_uiautomator2_options(
    udid: str,
    package: str,
    activity: Optional[str],
    duration_s: float,
) -> Any:
    """Build ``UiAutomator2Options`` honoring the ``APPIUM_*`` env-var contract.

    ``activity`` is the dotted activity (or fully-qualified class name) used unless
    ``APPIUM_ATTACH_ONLY=1`` is set, in which case ``autoLaunch=false`` and ``app_activity`` is
    cleared (caller must have foregrounded the app already, e.g. via a monkey pre-launch).

    The lazy import on ``appium`` is intentional: the shared library should remain importable on
    machines without the Appium client installed (the per-app entrypoint will surface a clear
    install-hint exception when its workload thread starts).
    """
    from appium.options.android import UiAutomator2Options

    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.udid = udid
    options.app_package = package
    options.no_reset = True
    options.set_capability("appium:dontStopAppOnReset", True)
    # Compose support: surface Modifier.testTag(name) as resource-id="name" in the UiAutomator
    # accessibility tree. Without this, testTags are Compose-internal semantics that AppiumBy.ID
    # cannot query: find_elements(AppiumBy.ID, "beats_edit") returns []. Off by default in the
    # UiAutomator2 driver; enabling is safe for View-based UIs since they don't have testTags.
    # Kr0oked Metronome v2.0.0+ relies on this to expose the beats_edit / tempo_edit /
    # subdivisions_edit / loading_indicator / content / *_slider tags declared in
    # app/src/main/java/com/bobek/metronome/ui/TestConstants.kt and applied via
    # Modifier.testTag(...) in MetronomeContent.kt.
    options.set_capability("appium:useTestTagAsResourceId", True)
    options.new_command_timeout = max(120, int(duration_s) + 60)

    options.skip_server_installation = env_truthy("APPIUM_SKIP_U2_INSTALL") and not env_truthy(
        "APPIUM_FORCE_U2_INSTALL",
    )
    options.app_wait_duration = env_int("APPIUM_APP_WAIT_DURATION_MS", 10000)

    attach_only = env_truthy("APPIUM_ATTACH_ONLY")
    if attach_only:
        options.set_capability("appium:autoLaunch", False)
        options.app_activity = None
        log("APPIUM_ATTACH_ONLY=1 (autoLaunch false); if session hangs, unset APPIUM_ATTACH_ONLY")
    else:
        options.app_activity = activity
    return options


def _click_ui_selector(driver: Any, selector: str, timeout: float = 4.0) -> None:
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    locator = (AppiumBy.ANDROID_UIAUTOMATOR, selector)
    el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
    el.click()


def element_present_uiautomator(driver: Any, selector: str, timeout: float = 2.0) -> bool:
    """Return True if ``selector`` (UiAutomator string) resolves within ``timeout`` seconds."""
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((AppiumBy.ANDROID_UIAUTOMATOR, selector)),
        )
        return True
    except Exception:
        return False


def safe_click_description_contains(driver: Any, substr: str, timeout: float = 3.0) -> bool:
    """Click first view whose **content-desc** contains ``substr`` (preferred for accessibility-labelled apps)."""
    if not substr:
        return False
    esc = substr.replace('"', '\\"')
    sel = 'new UiSelector().descriptionContains("%s")' % esc
    try:
        _click_ui_selector(driver, sel, timeout=timeout)
        log("clicked descriptionContains(%r)" % substr)
        return True
    except Exception as ex:
        log("miss descriptionContains(%r): %s" % (substr, type(ex).__name__))
        return False


def safe_click_text_contains(driver: Any, substr: str, timeout: float = 3.0) -> bool:
    """Click first view whose **text** contains ``substr`` (use for visible labels / dialogs)."""
    if not substr:
        return False
    esc = substr.replace('"', '\\"')
    sel = 'new UiSelector().textContains("%s")' % esc
    try:
        _click_ui_selector(driver, sel, timeout=timeout)
        log("clicked textContains(%r)" % substr)
        return True
    except Exception as ex:
        log("miss textContains(%r): %s" % (substr, type(ex).__name__))
        return False


def safe_scroll_into_view_text_contains(driver: Any, substr: str, timeout: float = 8.0) -> bool:
    """Click ``substr`` if visible; otherwise swipe upward and retry until ``timeout``.

    PreferenceScreens are RecyclerViews: items below the fold need scrolling. We try a quick
    immediate click first to avoid unnecessary swipes when the target is already on screen.
    """
    if not substr:
        return False
    if safe_click_text_contains(driver, substr, timeout=1.8):
        log("click textContains(%r) (already visible)" % substr)
        return True
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        swipe_vertical(driver, 0.68, 0.32, 380)
        time.sleep(0.38)
        if safe_click_text_contains(driver, substr, timeout=1.4):
            log("click textContains(%r) after list swipe #%s" % (substr, attempt))
            return True
    log("failed to reach textContains(%r) within %.1fs" % (substr, timeout))
    return False


def swipe_vertical(driver: Any, from_frac: float, to_frac: float, duration_ms: int = 400) -> None:
    """Vertical swipe centered horizontally, fractional y coordinates. Wraps ``driver.swipe``."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    x = w // 2
    driver.swipe(x, int(h * from_frac), x, int(h * to_frac), duration_ms)


def press_keys_repeat(driver: Any, keycode: int, times: int, gap_s: float = 0.2) -> None:
    """Press ``keycode`` ``times`` times with ``gap_s`` between presses. Useful for volume / dpad."""
    for _ in range(times):
        driver.press_keycode(keycode)
        time.sleep(gap_s)


def ensure_foreground(driver: Any, package: str) -> None:
    """If a permission/system overlay or external link took us off the app, ``activate_app`` it back.

    Best-effort: failure to read ``current_package`` is logged but does not raise. A subsequent
    selector failure will surface the real problem.
    """
    try:
        cur = driver.current_package
        if cur != package:
            log("foreground package %r != %r; activate_app" % (cur, package))
            driver.activate_app(package)
            time.sleep(1.0)
    except Exception as ex:
        log("ensure_foreground: %s %s" % (type(ex).__name__, ex))


def dismiss_system_dialogs(driver: Any, package: str, max_rounds: int = 8) -> None:
    """Clear common permission / system overlays using English label substrings + resource IDs.

    Strictly text-based (no app class names) so it works under R8 / packers / locale changes
    where view IDs and class names rotate but the visible labels don't. Iterates up to
    ``max_rounds`` times; each round clicks at most one label and then re-checks.

    2026-07-25: extended for Android 12 (P3) button-text variants and permissioncontroller
    resource-IDs after cross-phone parity testing surfaced P3-only scenarios failing on
    first-launch overlays that the P9-tuned label list was missing.
    """
    labels = [
        # Permission-grant dialog (Android 12/13/14/15/16 variations)
        "Allow",
        "While using the app",
        "Only this time",
        "ONLY THIS TIME",
        "Allow all the time",
        "Ask every time",
        # Deny variants: safe to tap; keeps app moving to next screen
        "Don't allow",
        "Deny",
        "Not now",
        "No thanks",
        # Generic overlays / TOS / first-run
        "OK",
        "Ok",
        "Continue",
        "Next",
        "Close",
        "Cancel",
        "Dismiss",
        "Got it",
        "Accept",
        "I agree",
        # Autofill "Save password?" style
        "Never",
        "No",
        # PageSizeMismatch / App-Compat (Android 16)
        "Don't Show Again",
        "Don't show again",
    ]
    # Explicit resource IDs from Android's permissioncontroller / systemui.
    # These match on the WIDGET-ID rather than user-visible text, which
    # bypasses locale / theme / branding variation. Tried before text so
    # correct Allow-foreground-only is preferred over the ambiguous "Allow".
    resource_ids = [
        "com.android.permissioncontroller:id/permission_allow_foreground_only_button",
        "com.android.permissioncontroller:id/permission_allow_one_time_button",
        "com.android.permissioncontroller:id/permission_allow_button",
        "com.android.permissioncontroller:id/permission_deny_button",
        "com.android.permissioncontroller:id/permission_deny_and_dont_ask_again_button",
        "com.google.android.gms:id/autofill_save_no",
        "com.google.android.gms:id/autofill_save_never",
        "android:id/button1",  # generic AlertDialog positive button
        "android:id/button2",  # generic AlertDialog negative button (last resort)
    ]
    for _ in range(max_rounds):
        clicked = False
        # Round 1: prefer resource-id matches (locale/theme-independent).
        for rid in resource_ids:
            sel = 'new UiSelector().resourceId("%s")' % rid
            try:
                _click_ui_selector(driver, sel, timeout=0.4)
                log("dismiss dialog: clicked resourceId(%r)" % rid)
                clicked = True
                time.sleep(0.35)
                ensure_foreground(driver, package)
                break
            except Exception:
                continue
        if clicked:
            continue
        # Round 2: fallback to text substring matches.
        for lab in labels:
            esc = lab.replace('"', '\\"')
            sel = 'new UiSelector().textContains("%s")' % esc
            try:
                _click_ui_selector(driver, sel, timeout=0.5)
                log("dismiss dialog: clicked textContains(%r)" % lab)
                clicked = True
                time.sleep(0.35)
                ensure_foreground(driver, package)
                break
            except Exception:
                continue
        if not clicked:
            break


def wait_for_package(driver: Any, package: str, timeout: Optional[float] = None) -> bool:
    """Poll ``driver.current_package`` until it matches ``package`` or ``timeout`` elapses.

    Default timeout is ``APPIUM_WAIT_FOR_PACKAGE_S`` (60s). Returns ``True`` on match, ``False`` on
    timeout. Logs the timeout case so packed-APK boot delays are visible in ``experiment.log``.
    """
    if timeout is None:
        timeout = env_float("APPIUM_WAIT_FOR_PACKAGE_S", 60.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if driver.current_package == package:
                log("foreground package OK: %s" % package)
                return True
        except Exception:
            pass
        time.sleep(0.35)
    log("timeout waiting for package %s (%.1fs)" % (package, timeout))
    return False


def await_app_ready(
    driver: Any,
    package: str,
    deadline_monotonic: float,
    is_main_ui_visible: Callable[[Any], bool],
    dump_dir: Optional[str] = None,
    pre_check_hook: Optional[Callable[[Any], None]] = None,
) -> bool:
    """Wait for foreground package + app-specific main UI, dismissing system dialogs along the way.

    Generic version of the per-app readiness gate. Caller supplies:

    - ``is_main_ui_visible(driver)``: app-specific predicate for "main controls are on screen"
      (e.g. metronome FAB, browser address bar). Called after the foreground package is correct.
    - ``pre_check_hook(driver)``: optional extra dismissal step run inside the polling loop
      (e.g. close an app-specific welcome dialog).

    Behavior:

    1. Wait up to ``min(APPIUM_WAIT_FOR_PACKAGE_S, deadline_monotonic - now)`` for the foreground
       package to match. On miss, save a ``startup_package_timeout`` page-source dump (if
       ``dump_dir`` is set) and return ``False``.
    2. Within ``APPIUM_WAIT_MAIN_UI_S`` (capped by ``deadline_monotonic``), repeatedly:
       ``ensure_foreground``, ``dismiss_system_dialogs``, optional ``pre_check_hook``, then check
       ``is_main_ui_visible``. Return ``True`` on hit.
    3. On overall miss, save a ``startup_main_ui_timeout`` page-source dump and return ``False``.
       Workload may continue (best-effort): caller decides.
    """
    from .coverage import save_page_source

    pkg_timeout = min(
        env_float("APPIUM_WAIT_FOR_PACKAGE_S", 60.0),
        max(5.0, deadline_monotonic - time.monotonic()),
    )
    if not wait_for_package(driver, package, timeout=pkg_timeout):
        save_page_source(driver, dump_dir, "startup_package_timeout")
        return False

    ui_budget = env_float("APPIUM_WAIT_MAIN_UI_S", 45.0)
    ui_deadline = min(time.monotonic() + ui_budget, deadline_monotonic)
    while time.monotonic() < ui_deadline:
        ensure_foreground(driver, package)
        dismiss_system_dialogs(driver, package, max_rounds=6)
        if pre_check_hook is not None:
            try:
                pre_check_hook(driver)
            except Exception as ex:
                log("await_app_ready pre_check_hook: %s %s" % (type(ex).__name__, ex))
        if is_main_ui_visible(driver):
            log("main UI visible")
            return True
        time.sleep(0.55)

    log("timeout waiting for main UI (%.1fs budget)" % ui_budget)
    save_page_source(driver, dump_dir, "startup_main_ui_timeout")
    return False
