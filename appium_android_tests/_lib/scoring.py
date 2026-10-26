"""
Strict-UI vs action-only scoring for the Espresso-mirror workload.

The "strict UI" contract: after performing a typed edit, we re-read the field's visible text and
require ``observed.strip() == typed.strip()`` for the step to count as a strict-UI pass. This is
the protection-tolerant analogue of an Espresso ``onView(withText(…))`` assertion: the device
must show the value the workload tried to enter, regardless of internal class names.

Coverage row contract per typed step (used by :func:`record_numeric_edit_observed`):

- ``<key>``           (bool): automation step succeeded (clicks + ``send_keys`` returned without
  raising). Action-only signal.
- ``<key>_observed``  (str): visible text observed in the field after the edit (truncated to 160
  chars). Forensics aid for "the typing succeeded but the UI rejected the value".
- ``<key>_strict``    (bool): ``True`` iff action succeeded **and** observed text equals typed
  text after strip. Strict-UI signal.

:func:`score_espresso_mirror_scenario` collapses a per-scenario coverage dict into the tuple
``(strict_ui_pass, action_only_pass, strict_fail_reasons)``. Headline scoring (which one becomes
``scenario_success`` in the JSONL row) is selected by the caller: typically via the
``ESPRESSO_MIRROR_SCORE_ACTION_ONLY`` env var, but that's a per-app concern and not enforced here.

Env-var contract owned here:

- ``ESPRESSO_MIRROR_RELAX_INITIAL_MARKING``: if truthy, ``initialState`` ignores the
  ``espresso_marking_matches_initial_state`` substring check. Use when the Andante translation
  varies on a localized device.
- ``ESPRESSO_MIRROR_RELAX_TEMPO_MARKINGS``: if truthy, ``tempoMarkingsWalk`` skips ``marking_ok``
  per step (still checks the typed tempo field text). Useful when only the marking strings
  localize but the digit field text is locale-stable.
"""

from __future__ import annotations

import time
from typing import Any

from . import env_truthy, log


# Compose className+instance fallback table for Kr0oked Metronome v2.0.0+ (and any future
# Compose app that ships an EditText-based form with the same logical layout). Index is the
# 0-based occurrence of `android.widget.EditText` in the rendered UI tree. Verified by live
# `adb shell uiautomator dump` on Pixel 3 / v2.1.1 / 2026-05-12: beats appears first,
# subdivisions second, tempo third. If a future app diverges, register a per-app override.
_COMPOSE_EDITTEXT_INDEX_BY_VIEW_ID = {
    "beats_edit": 0,
    "subdivisions_edit": 1,
    "tempo_edit": 2,
}

# Tempo-marking text-content fallback. Compose v2.1.1 renders the marking as a bare
# `android.widget.TextView` with `text="Andante"` etc.: no resource-id, no content-desc.
# Strict reads find it by matching one of the known marking substrings (full upstream set
# from MetronomeContent.kt's tempoMarkings(...) call, ordered Largo → Prestissimo).
_TEMPO_MARKING_SUBSTRS = (
    "Largo", "Larghetto", "Adagio", "Andante",
    "Moderato", "Allegro", "Presto", "Prestissimo",
)


def _find_id_with_compose_fallback(driver: Any, package: str, view_id: str) -> Any:
    """Resolve an element by ID across View-based AND Compose UIs.

    Cascade (first match wins, no implicit-wait stalls between steps):

    1. ``<package>:id/<view_id>``: full View-based resource ID. Matches legacy XML/View
       builds (e.g. Kr0oked Metronome v1.7.2 and earlier).
    2. Bare ``<view_id>``: matches Compose UIs that opt in via
       ``Modifier.semantics { testTagsAsResourceId = true }`` at the root. Many apps don't
       enable this; the Metronome upstream in particular does not as of v2.1.1.
    3. ``className("android.widget.EditText").instance(N)``: Compose fallback for known
       EditText positions (see ``_COMPOSE_EDITTEXT_INDEX_BY_VIEW_ID``). This handles apps
       that use ``Modifier.testTag(...)`` internally for their own Espresso tests but
       don't expose them as accessibility resource-ids (Kr0oked Metronome v2.0.0+).
    4. For ``tempo_marking_text``: TextView whose ``text`` contains one of the known
       tempo-marking substrings (``Largo``..``Prestissimo``). The Compose UI renders the
       marking as a bare TextView with no resource-id.

    Uses ``find_elements`` (plural) at every step so a miss returns ``[]`` immediately
    instead of blocking on the Appium implicit wait. Only raises ``NoSuchElementException``
    after the entire cascade comes up empty.
    """
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.common.exceptions import NoSuchElementException

    # 1. View-based namespaced resource-id
    namespaced = "%s:id/%s" % (package, view_id)
    els = driver.find_elements(AppiumBy.ID, namespaced)
    if els:
        return els[0]

    # 2. Compose with testTagsAsResourceId opt-in (bare resource-id)
    els = driver.find_elements(AppiumBy.ID, view_id)
    if els:
        return els[0]

    # 3. Compose EditText fallback by class+instance for the known numeric fields
    idx = _COMPOSE_EDITTEXT_INDEX_BY_VIEW_ID.get(view_id)
    if idx is not None:
        sel = ('new UiSelector().className("android.widget.EditText").instance(%d)' % idx)
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els:
            return els[0]

    # 4. Tempo-marking TextView text-content fallback
    if view_id == "tempo_marking_text":
        for marking in _TEMPO_MARKING_SUBSTRS:
            esc = marking.replace('"', '\\"')
            sel = ('new UiSelector().className("android.widget.TextView").textContains("%s")' % esc)
            els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
            if els:
                return els[0]

    raise NoSuchElementException(
        "no element for view_id=%r — tried View-id '%s', bare testTag, EditText "
        "class-instance fallback, and tempo-marking text-content fallback" % (view_id, namespaced)
    )


def read_edit_text_by_id(driver: Any, package: str, view_id: str) -> str:
    """Visible text in the field tagged ``<view_id>`` (returns empty string on any failure).

    Looks the element up via :func:`_find_id_with_compose_fallback` so the same call site
    works against View-based ``<package>:id/<view_id>`` and Compose ``Modifier.testTag(view_id)``
    UIs without scenario changes.

    Used for post-condition black-box checks. Stripped before return so callers don't have to
    repeat the strip. Failures are swallowed because this is observation-only: a missing field
    just yields an empty string, which the strict-pass rule then treats as a non-match.
    """
    try:
        el = _find_id_with_compose_fallback(driver, package, view_id)
        return (el.text or "").strip()
    except Exception:
        return ""


def type_numeric_edit_by_id(driver: Any, package: str, view_id: str, text: str, label: str) -> bool:
    """Click + clear + ``send_keys(text)`` against the field tagged ``<view_id>``.

    Resolves the element via :func:`_find_id_with_compose_fallback` so the same call site works
    against View-based ``<package>:id/<view_id>`` and Compose ``Modifier.testTag(view_id)`` UIs.

    Mirrors the surfaces Espresso addresses via ``onView(withId(…))``. Returns ``True`` on success,
    ``False`` on any exception (with a log line carrying the exception type and the human-readable
    ``label`` so failures are easy to grep). Hides the soft keyboard afterwards (best-effort) and
    sleeps briefly so subsequent reads pick up the new text.
    """
    try:
        el = _find_id_with_compose_fallback(driver, package, view_id)
        el.click()
        time.sleep(0.15)
        el.clear()
        el.send_keys(text)
        log("typed %s via id %s (%s)" % (text, view_id, label))
        try:
            driver.hide_keyboard()
        except Exception:
            pass
        time.sleep(0.28)
        return True
    except Exception as ex:
        log("id edit %s failed: %s %s" % (label, type(ex).__name__, ex))
        return False


def record_numeric_edit_observed(
    cov: dict[str, Any],
    result_key: str,
    driver: Any,
    package: str,
    view_id: str,
    text: str,
    label: str,
) -> None:
    """Perform an edit, then record action ok, observed field text, and strict UI match.

    Mutates ``cov`` in-place with three keys (see module docstring): ``<result_key>``,
    ``<result_key>_observed``, ``<result_key>_strict``. The combination is what
    :func:`score_espresso_mirror_scenario` consumes.
    """
    ok = type_numeric_edit_by_id(driver, package, view_id, text, label)
    observed = read_edit_text_by_id(driver, package, view_id)
    cov[result_key] = ok
    cov["%s_observed" % result_key] = observed[:160]
    cov["%s_strict" % result_key] = bool(ok and observed.strip() == text.strip())


def _espresso_pass_strict_key(cov: dict[str, Any], base: str) -> bool:
    """Prefer ``<base>_strict`` (observed text matched intent); else fall back to legacy action bool."""
    sk = "%s_strict" % base
    if sk in cov:
        return bool(cov.get(sk))
    return bool(cov.get(base))


def score_espresso_mirror_scenario(
    scenario_name: str,
    cov: dict[str, Any],
) -> tuple[bool, bool, list[str]]:
    """Collapse one scenario's coverage row into ``(strict_ui_pass, action_only_pass, fail_reasons)``.

    Strict requires observable edit text to match the intended value (``_*_strict`` keys), plus
    marking checks where applicable. Action-only uses whether automation steps returned ``True``.
    The returned ``fail_reasons`` list is keyed by reason tag (``action_false:<key>``,
    ``ui_not_matching:<base>``, ``initial_marking_mismatch``, etc.) and is used to populate
    ``example_fail_reasons`` in the per-scenario report.

    Knows only the Metronome scenarios (``contentVisibleTouch``, ``initialState``,
    ``beatsReflect``, ``subdivisionsReflect``, ``tempoReflect``, ``tempoMarkingsWalk``,
    ``invalidBeatsReset``, ``beatsErrorNonNumericTouch``, ``tempoErrorTooBigTouch``,
    ``subdivisionsErrorTooBigTouch``, ``subdivisionsErrorNonNumericTouch``,
    ``tempoErrorNonNumericTouch``). Per-app modules score their own scenarios (``<app>/scoring.py``);
    here, unknown scenario names return ``(False, False,
    ["unknown_scenario_name"])``.
    """
    reasons_s: list[str] = []

    def need_action(keys: list[str]) -> bool:
        bad = [k for k in keys if not cov.get(k)]
        if bad:
            reasons_s.extend("action_false:%s" % k for k in bad)
            return False
        return True

    def need_strict_bases(bases: list[str]) -> bool:
        bad = [b for b in bases if not _espresso_pass_strict_key(cov, b)]
        if bad:
            reasons_s.extend("ui_not_matching:%s" % b for b in bad)
            return False
        return True

    if scenario_name == "contentVisibleTouch":
        ok = bool(cov.get("content_displayed")) and bool(cov.get("loading_indicator_hidden"))
        if not cov.get("content_displayed"):
            reasons_s.append("content_not_displayed")
        if not cov.get("loading_indicator_hidden"):
            reasons_s.append("loading_indicator_shown")
        return ok, ok, reasons_s

    if scenario_name == "initialState":
        bases = [
            "espresso_initial_beats_4",
            "espresso_initial_subdivisions_1",
            "espresso_initial_tempo_80",
        ]
        action_ok = need_action(bases)
        strict_edits = need_strict_bases(bases)
        if env_truthy("ESPRESSO_MIRROR_RELAX_INITIAL_MARKING"):
            strict_ok = strict_edits
        else:
            mk = bool(cov.get("espresso_marking_matches_initial_state"))
            if not mk:
                reasons_s.append("initial_marking_mismatch")
            strict_ok = strict_edits and mk
        return strict_ok, action_ok, reasons_s

    if scenario_name in ("beatsReflect", "subdivisionsReflect", "tempoReflect"):
        bases_map = {
            "beatsReflect": ["step_beats_1", "step_beats_2"],
            "subdivisionsReflect": ["step_subdivisions_1", "step_subdivisions_2"],
            "tempoReflect": ["step_tempo_30", "step_tempo_40"],
        }
        bases = bases_map[scenario_name]
        action_ok = need_action(bases)
        strict_ok = action_ok and need_strict_bases(bases)
        return strict_ok, action_ok, reasons_s

    if scenario_name == "tempoMarkingsWalk":
        steps = cov.get("tempo_marking_steps") or []
        if not steps:
            return False, False, ["no_tempo_marking_steps_recorded"]
        relax_m = env_truthy("ESPRESSO_MIRROR_RELAX_TEMPO_MARKINGS")
        action_ok = all(s.get("typed_ok") for s in steps)
        if not action_ok:
            for i, s in enumerate(steps):
                if not s.get("typed_ok"):
                    reasons_s.append("step[%s]_typed_failed" % i)
        strict_ok = True
        for i, s in enumerate(steps):
            if not s.get("typed_ok"):
                strict_ok = False
                continue
            if "tempo_edit_strict" in s:
                if not s.get("tempo_edit_strict"):
                    reasons_s.append("step[%s]_tempo_field_not_matching" % i)
                    strict_ok = False
            if not relax_m and not s.get("marking_ok"):
                reasons_s.append("step[%s]_marking_failed" % i)
                strict_ok = False
        return strict_ok, action_ok, reasons_s

    if scenario_name in (
        "invalidBeatsReset",
        "beatsErrorNonNumericTouch",
        "tempoErrorTooBigTouch",
        "subdivisionsErrorTooBigTouch",
        "subdivisionsErrorNonNumericTouch",
        "tempoErrorNonNumericTouch",
    ):
        bases_map = {
            "invalidBeatsReset": ["reset_beats_1", "invalid_beats_9", "restore_beats_4"],
            "beatsErrorNonNumericTouch": ["beats_to_1", "beats_dot", "beats_restore_4"],
            "tempoErrorTooBigTouch": ["tempo_30", "tempo_invalid_253", "tempo_restore_80"],
            "subdivisionsErrorTooBigTouch": ["sub_to_1", "sub_invalid_5", "sub_restore_2"],
            "subdivisionsErrorNonNumericTouch": ["sub_to_1", "sub_dot", "sub_restore_2"],
            "tempoErrorNonNumericTouch": ["tempo_30", "tempo_dot", "tempo_restore_80"],
        }
        bases = bases_map[scenario_name]
        action_ok = need_action(bases)
        strict_ok = action_ok and need_strict_bases(bases)
        return strict_ok, action_ok, reasons_s

    # Set α (2026-05-08): audio-engine-heavy Metronome scenarios.
    # Strict requires: all edit fields set strict + audio playback FAB tap started AND
    # stopped (proves the native audio engine actually ran for the requested duration).
    if scenario_name == "playFastSubdivisions":
        bases = ["fast_beats_4", "fast_subdivisions_4", "fast_tempo_200"]
        action_ok = need_action(bases)
        strict_edits = need_strict_bases(bases)
        play_started = bool(cov.get("fast_play_started"))
        play_stopped = bool(cov.get("fast_play_stopped"))
        if not play_started:
            reasons_s.append("audio_play_not_started")
        if not play_stopped:
            reasons_s.append("audio_play_not_stopped")
        play_ok = play_started and play_stopped
        action_ok = action_ok and play_ok
        strict_ok = strict_edits and play_ok
        return strict_ok, action_ok, reasons_s

    if scenario_name == "tempoSweepPlay":
        bases = ["sweep_beats_4", "sweep_subdivisions_1"]
        action_ok = need_action(bases)
        strict_edits = need_strict_bases(bases)
        steps = cov.get("sweep_steps") or []
        if not steps:
            return False, False, ["no_sweep_steps_recorded"]
        steps_ok = True
        for i, s in enumerate(steps):
            if not s.get("set_ok"):
                reasons_s.append("sweep[%s]_set_failed" % i)
                steps_ok = False
            elif not s.get("tempo_edit_strict"):
                reasons_s.append("sweep[%s]_tempo_field_not_matching" % i)
                steps_ok = False
            if not s.get("play_started"):
                reasons_s.append("sweep[%s]_audio_play_not_started" % i)
                steps_ok = False
            if not s.get("play_stopped"):
                reasons_s.append("sweep[%s]_audio_play_not_stopped" % i)
                steps_ok = False
        action_ok = action_ok and steps_ok
        strict_ok = strict_edits and steps_ok
        return strict_ok, action_ok, reasons_s

    reasons_s.append("unknown_scenario_name")
    return False, False, reasons_s
