# `launch_timing_series.csv`: provenance

**Written by** `tools/extract_launch_timing.py` (2026-10-03), from two raw sources that are not
in this package (they are large; available on request):

- `tools/sweep_logs/logcat/logcat_<run>_<phone>_<app>_<variant>_r<rep>.log`: the full logcat of
  every cell (`logcat -v epoch`), captured by the sweep runner from before Android Runner
  launches the app until the cell ends;
- `android-runner/examples/batterymanager/output/<run_id>/data/*/*/batterymanager/logcat_*.csv`:
  the BatteryManager samples of the same cell. The first sample is the start of the energy
  measurement.

**Which cells:** every captured cell listed in `logcat_activity_<batch>.csv` for the three
series batches (the same mapping from cell to log file that the mechanism tables use), 3,591
cells. The notebooks restrict them to the analysed cells (the filter chain). Regenerated
2026-10-05 from the current logcat inputs (`logcat_activity.PROVENANCE.md`). The application's
process start is matched by its exact package name, so its sub-processes do not count as launches.

## What each column is

- `ttid_ms`: the system's time to initial display for the subject application's first
  display in the cell, read from `ActivityTaskManager: Displayed <package>/<activity> ...: +XsYms`.
  In every analysed cell this is the cold launch: it follows the application's first process
  start by 0.18 to 3.19 s, and comes before the energy measurement starts.
- `app_process_starts`: `am_proc_start` events whose process name is exactly the package.
- `*_rel_s`: seconds relative to the first battery sample. Negative means before the energy
  measurement started.
  - `launch_start_rel_s`: Android Runner's `monkey` launch (`START u0 ... cmp=<package>/`).
  - `proc_start_rel_s`: the application's first process start.
  - `first_display_rel_s`: its first display.
  - `uia2_start_rel_s`: Appium's UiAutomator2 server process start.
  - `appium_start_rel_s`: the second START of the package. In six applications this is Appium's
    activity start at session creation, in the passive phase; no process start accompanies it. In
    documenter and linkhub it is a scenario's own activity start, in the active phase; tipuous has
    none (columns below, added 2026-10-25; the earlier columns are unchanged).
- `appium_start_action`: the intent action of that second START (`MAIN`; `SEND` for linkhub's
  share scenario).
- `appium_start_outcome`: what Android did with it, from the package's window-manager events within
  0.05 s of the START line: `delivered to the running activity` (`wm_new_intent`: calculator,
  medtimer), `new activity instance` (`wm_create_activity` in the running process: keep_recipe,
  poetskingdom, and documenter's and linkhub's scenario starts), or `no activity event` (the task is
  brought to the front: itsok, pdfviewer, and documenter on the Pixel 3).

## Why the clocks agree

Logcat's epoch timestamps and the BatteryManager timestamps are both read on the phone.

## What it cannot see

No energy is sampled during the cold launch: Android Runner launches the application, runs its
`after_launch` hook, and only then starts the profilers. The launch is measured in time only.
