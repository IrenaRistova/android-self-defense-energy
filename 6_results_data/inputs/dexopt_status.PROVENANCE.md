# `dexopt_status_series.csv`: provenance

**Written by** `tools/extract_dexopt_status.py` (2026-10-03). The source is each run's
`dexopt_state.txt`, written by the `before_run` hook
(`android-runner/Scripts/before_run.py`, "DEXOPT PINNING") in Android Runner's output folder.
That folder is not in this package (raw data, available on request; `raw_manifest.csv.gz` lists it).

**What the hook does, before every launch:**
1. `cmd package compile -f -m speed <package>`: a request to compile the application ahead of
   time.
2. `dumpsys package dexopt`, filtered to the package, saved with the requested filter.

## What each column is

- `batch`, `phone`, `app`, `variant`, `rep`: the cell (every series cell with a run, 3,594).
- `requested_filter`: the filter the hook requested (`speed` in every cell).
- `status`: the status Android reports for the application's `base.apk`.
  - `speed`: compiled ahead of time.
  - `verify`: verified only; the code runs without ahead-of-time compilation.

## Why the status is not what was requested

Android forces debuggable applications to verification only, "even if they are done via adb
shell commands". Two copies of the source say so:
- Android 12, `PackageDexOptimizer.getRealCompilerFilter` (`[aosp_pdo_debuggable]`);
- Android 14 and later, ART Service `Dexopter` (`[aosp_artservice_debuggable]`).

Every analysed build is a debug build, so every analysed cell reads `verify`
(`6_results_analysis/tables/dexopt_status.csv`).

**The check that could fail:** the one non-debuggable build among the nine applications,
itsok's Allatori release build, reads `speed` in every cell. It is excluded from the analysis
by the build-type rule.

## What it cannot see

- **Whether, and when, ART later compiled code just in time** during a cell.
- **What the packed build's runtime does with the code its stub decrypts.** Bangcle's
  application code is not in the APK as DEX (it is `assets/classes0.jar`), so the request
  never reaches it in any case.
- **The cells' power state during the request.** The hook's own comment calls the
  compilation "wall-powered". That comment predates the YKUSH sweep runner, which cuts and
  verifies the phone's power before Android Runner starts. So in the final sweep the request
  ran on battery, before the energy measurement.
