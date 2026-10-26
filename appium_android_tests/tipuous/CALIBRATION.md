# Tipuous: per-app scenario calibration

> **Status: estimates of 2026-05-09, not a calibration run.**
>
> The estimates below were made when the module was written, by reading the
> upstream Espresso tests (`MainActivityTest.kt`) and the Compose source
> (`MainScreen.kt`). They were not confirmed against a Pixel 3 baseline run with
> `BATTERY_STATS` granted. The experiment ran this list (`TIPUOUS_SCENARIO_LIST`
> in [`scenarios.py`](scenarios.py)); the run tables record the measured workload
> durations (`duration_workload_s`).

## Locked scenario list (`TIPUOUS_SCENARIO_LIST`, N = 7)

| # | Scenario                                | Provenance | Estimated wall time (Pixel 3) |
|---|-----------------------------------------|------------|-------------------------------|
| 1 | `typeAmountOnly`                        | mirror     | ~5 s                          |
| 2 | `inputAmountAndSelectFivePercent`       | mirror     | ~9 s                          |
| 3 | `inputAmountAndSelectTenPercent`        | mirror     | ~9 s                          |
| 4 | `inputAmountAndSelectFifteenPercent`    | mirror     | ~9 s                          |
| 5 | `inputAmountAndSelectTwentyPercent`     | mirror     | ~9 s                          |
| 6 | `customTipAmount`                       | mirror     | ~12 s                         |
| 7 | `tipPercentageSweep`                    | exploration| ~50 s                         |
|   | **Total**                               |            | **~103 s** (target 100–130 s) |

The full provenance + energy-axis statement live in
[`scenarios.py`](scenarios.py) module docstring; this file is a brief pointer
to the per-scenario timings + the recipe for re-calibrating.

## Recalibration recipe (per `CONVENTIONS.md` § 6.5)

1. Build the Tipuous baseline APK (debug or release; see "Pre-conditions"
   below) and grant `BATTERY_STATS` to the BatteryManager companion app
   (`com.example.batterymanager_utility`; the `before_experiment` chain does this
   through `android-runner/Scripts/before_experiment_grant_battery_stats.py`).
2. Run once on Pixel 3:
   ```bash
   cd android-runner
   python3 . examples/batterymanager/tipuous_baseline_debug_pixel3_wifi_ykush.json
   ```
3. Read per-scenario wall times from
   `output/<run_id>/data/Pixel\ 3/com.tips.tipuous/appium_workload_coverage.jsonl`
   (`scenario_wall_seconds` field).
4. Update the **Estimated wall time** column above with observed values.
5. If cumulative wall time falls outside `[100, 130] s`, drop or merge
   exploration scenarios (`tipPercentageSweep`) until cumulative lands in
   range. Never trim mirror scenarios: those are the upstream test analogues.
6. Lock the new list in `scenarios.py::TIPUOUS_SCENARIO_LIST` and record the
   date in the status note at the top.

## Pre-conditions for the smoke run

- A Tipuous baseline APK built from the upstream source
  ([JoshLudahl/tipuous](https://github.com/JoshLudahl/tipuous)): `./gradlew assembleDebug`
  in the source tree writes `app/build/outputs/apk/debug/app-debug.apk`.
- **Important:** the debug build sets `applicationIdSuffix = ".debug"`
  (`app/build.gradle.kts:46`), so the debug APK's manifest package is
  `com.tips.tipuous.debug`, **not** `com.tips.tipuous`. Either:
  - build a release APK to keep `application_id` = `com.tips.tipuous`, or
  - set the configuration's `application_id` to `com.tips.tipuous.debug` and the
    uninstall hook's `PACKAGE` to match.
  The experiment's configurations (`android-runner/configs/tipuous_*.json`) use the
  debug build, with `application_id` `com.tips.tipuous.debug`; the uninstall hook's
  `PACKAGE` (`android-runner/Scripts/before_experiment_uninstall_tipuous.py`) is
  `com.tips.tipuous`.
- BatteryManager `BATTERY_STATS` permission granted (the `before_experiment`
  chain grants it; when running standalone, grant it with
  `adb shell pm grant com.example.batterymanager_utility android.permission.BATTERY_STATS`).

## Why these estimates

- The bill `OutlinedTextField` accepts ~5 keystrokes for `12.99` plus a click
  + clear: ~2.5 s on Pixel 3 with UiAutomator2.
- Each chip click returns ~0.6–1.0 s.
- The reactive Tip / Total `Text` re-renders inline, so `_ui_text_contains`
  finds the result strings inside ~1 s.
- `customTipAmount` adds ~3 s for the slider drag + observation read.
- `tipPercentageSweep` types 10 different bill amounts × tap one of 4 chips
  cyclically; budget ~5 s per pair × 10 = ~50 s.

These are conservative; if the Pixel 3 first run measures faster, the budget
is healthy; if slower, drop the sweep length first.

---

**Last revision:** 2026-05-09 (estimates made when the module was written).
