# Documenter: per-app scenario calibration

> **Status: estimates of 2026-05-12, not a calibration run.**
>
> The estimates below were made when the module was written, by reading
> the upstream Espresso tests (`AboutMenuItemVisibilityTest.kt`, `OpenTest.kt`,
> `OpeningFromOtherAppTest.kt`, `NotificationTest.kt`) and the View-based XML
> layouts (`fragment_home.xml`, `fragment_document.xml`, `main_menu.xml`,
> `root_preferences.xml`). They were not confirmed against a Pixel 3 baseline
> run with `BATTERY_STATS` granted: the Pixel 9 was the only connected device
> at the time, so the wall-time target is calibrated to the Pixel 9 (the faster
> device). The list that ran in the experiment is `DOCUMENTER_SCENARIO_LIST` in
> [`scenarios.py`](scenarios.py), revised after these estimates (last on 2026-07-26,
> re-checked on 2026-08-03); its comments record the later measurements.

## Scenario list of 2026-05-12 (`DOCUMENTER_SCENARIO_LIST`, N = 7)

| # | Scenario                                       | Provenance  | Estimated wall time (Pixel 9) |
|---|------------------------------------------------|-------------|-------------------------------|
| 1 | `shownInHome`                                  | mirror      | ~4 s                          |
| 2 | `aboutMenuHiddenInAbout`                       | mirror (fused: 2 upstream tests) | ~12 s    |
| 3 | `aboutShownAfterReturnFromAboutLibraries`      | mirror (fused: 2 upstream tests) | ~14 s    |
| 4 | `openFilePicker`                               | mirror      | ~10 s                         |
| 5 | `settingsTogglePreferences`                    | exploration | ~22 s                         |
| 6 | `openDocumentAttempt`                          | inspired (energy-axis) | ~30 s            |
| 7 | `homeScrollAndIdle`                            | exploration | ~10 s                         |
|   | **Total**                                      |             | **~102 s** (target 90–120 s)  |

The full provenance + energy-axis statement live in
[`scenarios.py`](scenarios.py) module docstring; this file is a brief pointer
to the per-scenario timings + the recipe for re-calibrating.

## Recalibration recipe (per `CONVENTIONS.md` § 6.5)

1. Build / repack the Documenter baseline APK and grant `BATTERY_STATS` to the
   BatteryManager companion app (`com.example.batterymanager_utility`; the
   `before_experiment` chain does this through
   `android-runner/Scripts/before_experiment_grant_battery_stats.py`).
2. Push a sample PDF fixture to `/sdcard/Download/sample.pdf` so
   `openDocumentAttempt` exercises the native conversion energy axis. The
   upstream tree ships four fixtures under
   `app/src/androidTest/assets/testFiles/`; `geneve_1564.pdf` is a good default.
3. Run once on Pixel 3:
   ```bash
   cd android-runner
   python3 . examples/batterymanager/documenter_baseline_debug_pixel3_wifi_ykush.json
   ```
4. Read per-scenario wall times from
   `output/<run_id>/data/Pixel\ 3/com.viliussutkus89.documenter.debug/appium_workload_coverage.jsonl`
   (`scenario_wall_seconds` field).
5. Update the **Estimated wall time** column above with observed values.
6. If cumulative wall time on Pixel 3 falls outside `[100, 130] s`, adjust
   the budget: first lower `DOCUMENTER_CONVERT_WAIT_S` (default 20 s, drives
   `openDocumentAttempt`), then trim `homeScrollAndIdle` swipes, then trim
   `settingsTogglePreferences`. **Never trim mirror scenarios**: those are
   the upstream test analogues.
7. Lock the new list in `scenarios.py::DOCUMENTER_SCENARIO_LIST` and record the
   date in the status note at the top.

## Pre-conditions for the smoke run

- Packed APK at
  `<APK_DIR>/documenter-universal-debug_protected.signed.apk`
  (signed and installable; verified on the Pixel 9 on 2026-05-12).
- **Important:** the packed APK is built from the `debug` build type which sets
  `applicationIdSuffix = ".debug"` (`app/build.gradle.kts:79-80`), so the
  manifest package on-device is `com.viliussutkus89.documenter.debug`, **not**
  `com.viliussutkus89.documenter`. The configurations' `application_id`
  (`android-runner/configs/documenter_*.json`) and
  `android-runner/Scripts/before_experiment_uninstall_documenter.py::PACKAGE` are set to
  `com.viliussutkus89.documenter.debug` to match.
- **Sample document fixture** (required only for the strict-UI signal on
  `openDocumentAttempt`): push a sample PDF to a SAF-visible location, e.g.
  ```bash
  adb push <Documenter source>/app/src/androidTest/assets/testFiles/geneve_1564.pdf \
      /sdcard/Download/sample.pdf
  ```
  (`android-runner/Scripts/before_experiment_sync_fixtures.py` does this in the
  `before_experiment` chain.) Without it, `openDocumentAttempt` still runs and records
  action-only signal (FAB clicked + SAF nav attempted); the strict tag will be
  `conversion_did_not_progress` and the energy window will reflect the SAF
  picker, not Documenter's native conversion path.
- BatteryManager `BATTERY_STATS` permission granted (the `before_experiment`
  chain grants it; when running standalone, grant it with
  `adb shell pm grant com.example.batterymanager_utility android.permission.BATTERY_STATS`).
- Pixel 9 reports `arm64-v8a` only. ABI-check the packed APK before extending
  this to Pixel 6 / Pixel 3 baseline; Bangcle outputs that ship only
  `armeabi-v7a` will fail to install on Pixel 9 with
  `INSTALL_FAILED_NO_MATCHING_ABIS` (see `CONVENTIONS.md` § 9).

## Why these estimates

- `shownInHome`: pure read-only assertion (one selector check after the gating
  `await_app_ready` already confirmed the FAB / RecyclerView is visible).
  ~4 s including the framework round-trip.
- `aboutMenuHiddenInAbout` and `aboutShownAfterReturnFromAboutLibraries`:
  each fuses two upstream `@Test` methods into one Appium scenario, with
  4–5 click + back gestures per scenario at ~1.5–2.5 s per gesture under
  UiAutomator2 on Pixel 9.
- `openFilePicker`: one FAB tap + SAF overlay detection + back press. The
  SAF cold-start dominates (~5 s on first invocation on Pixel 9).
- `settingsTogglePreferences`: 3 preference toggles + state read-before /
  read-after for each, plus the safe-scroll-into-view ladder for any below-the-fold
  preferences. ~22 s on Pixel 9; faster on Pixel 3 if the PreferenceFragment
  fits on one screen.
- `openDocumentAttempt`: a generous 20 s `DOCUMENTER_CONVERT_WAIT_S` budget
  + SAF picker latency + back unwind. On Pixel 3 the native conversion path
  may need ≥30 s for `libpdf2htmlEX-android.so` on the larger fixtures
  (`geneve_1564.pdf`); raise `DOCUMENTER_CONVERT_WAIT_S` accordingly.
- `homeScrollAndIdle`: 4 swipes × ~0.55 s + 1.5 s idle. Always-passing
  exploration scenario that keeps the foreground while letting the energy
  window settle.

These are conservative; if the Pixel 9 first run measures faster than ~100 s,
the budget is healthy; if Pixel 3 measures longer than 130 s, drop one of
the exploration scenarios first.

---

**Last revision:** 2026-05-12 (estimates made when the module was written).
