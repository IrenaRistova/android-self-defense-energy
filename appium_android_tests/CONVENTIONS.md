# `appium_android_tests/`: harness conventions

## 1. Purpose

This document describes the conventions for a per-app Appium black-box module and its matching
Android Runner experiment configuration. A contributor can add a new `(app, variant, device)` cell
of the experiment from this document and the code it names.

---

## 2. Folder layout

The harness is split into a shared library and per-app modules, so each new subject app ships
~1–3 KB of scenarios instead of ~75 KB of boilerplate. The layout is:

```
appium_android_tests/
├── __init__.py                  # package marker (no logic)
├── CONVENTIONS.md               # this file
├── _lib/                        # app-agnostic shared library
│   ├── __init__.py              # public re-exports + log/env utilities
│   ├── driver.py                # UiAutomator2 options + fail-fast connect + UI helpers
│   ├── scoring.py               # record_numeric_edit_observed, score_espresso_mirror_scenario
│   ├── coverage.py              # appium_workload_coverage.jsonl + <workload>_scenario_report.{json,txt}
│   ├── timing.py                # start_workload_thread_and_block_until_done (keeps NativeExperiment child alive)
│   └── status.py                # appium_status.json writer
└── <app_id>/                    # one folder per subject app
    ├── __init__.py              # MUST re-export run_workload
    ├── scenarios.py             # app-specific selectors, scenarios, run_workload(experiment, device)
    ├── scoring.py               # per-scenario scoring for this app
    └── CALIBRATION.md           # scenario timing notes (documenter, linkhub and tipuous only)
```

`<app_id>` is the **lowercase ASCII slug** of the app's display name (the same key as the `app`
column of the run tables and the first part of the Android Runner configuration names). Examples:
`calculator`, `keep_recipe`, `poetskingdom`. No spaces, no dots, no hyphens: only `[a-z0-9_]`.
The same slug is used by the Android Runner-side `Scripts/interaction_appium_<app_id>.py` wrapper
and the `Scripts/before_experiment_uninstall_<app_id>.py` hook. `Scripts/` is
`android-runner/Scripts/` in this repository and `examples/batterymanager/Scripts/` in an Android
Runner installation (`android-runner/README.md`).

The matching Android Runner hook is the per-app wrapper `Scripts/interaction_appium_<app_id>.py`
(see § 13). It sets `APPIUM_APP=<app_id>` and calls the module's `run_workload`.

Any per-app module in this folder (for example [`calculator/`](calculator/)) can serve as the
starting point for a new one.

---

## 3. The `run_workload(experiment, device)` contract

Every per-app module MUST expose `run_workload(experiment, device)`. This uniform signature is
what lets every Android Runner wrapper treat its app as a single import indirection.

Signature (as in each `<app_id>/scenarios.py`):

```python
def run_workload(experiment: Any, device: Any) -> None:
    ...
```

- `experiment` is AndroidRunner's `NativeExperiment`. Read
  `experiment.package` (manifest application id, equivalent to
  `application_id` in the JSON) and `experiment.duration` (workload
  window in **seconds**, derived from the JSON `duration` ms by
  AndroidRunner).
- `device` is AndroidRunner's `Device`. `device.id` is the adb serial:
  pass it to `build_uiautomator2_options(udid=…)`.
- Env vars (set by the wrapper / launcher / `pre_run` hook) are read
  via `os.environ`; canonical contract in § 7.

Required behaviour, in order:

1. Resolve `package` and `duration_s` from `experiment`; bail with a
   logged status JSON if `package` is missing.
2. Build options via `_lib.driver.build_uiautomator2_options`.
3. Connect via `_lib.driver.connect_remote_webdriver` (never the raw
   `webdriver.Remote()`).
4. Gate readiness via `_lib.driver.await_app_ready` (with an app-specific
   "main UI visible" predicate).
5. Drive the locked scenario list end-to-end (§ 6), so the energy
   windows stay comparable across variants. The deadline derived from
   `experiment.duration` is only a failsafe ceiling.
6. Block `main()` until the workload thread finishes, via
   [`_lib.timing.start_workload_thread_and_block_until_done`](_lib/timing.py).
   Non-negotiable: the AndroidRunner Python3 hook runs in a child process
   killed when `main()` returns.
7. **Never raise out of `run_workload`**: failures are data; record them
   into `appium_status.json` (`failure_reason`, `session_timeout`) and
   the JSONL coverage rows. An exception that escapes `run_workload`
   loses the per-app bookkeeping.

Artifacts that MUST be written every call (full list in § 5): the JSONL
coverage row, the `<workload>_scenario_report.{json,txt}` pair, and
`appium_status.json`. Always via `_lib` helpers, never by hand.

---

## 4. Scoring contract (strict UI vs action-only)

Strict UI scoring is the **headline thesis-grade signal**: it requires
that the device actually shows the value the workload tried to enter,
regardless of internal class names or view IDs the protection layer may
have rewritten. Action-only scoring is preserved as a forensic fallback
for protected variants where view IDs are obfuscated to the point that
strict UI cannot read the field back.

Per-step recording. Every numeric edit calls
`_lib.scoring.record_numeric_edit_observed(cov, key, driver, package,
view_id, text, label)`. That helper writes **three** keys into the
per-scenario coverage dict `cov`:

- `<key>` (bool): automation step succeeded (clicks +
  `send_keys` returned without raising). **Action-only signal.**
- `<key>_observed` (str): visible field text after the edit
  (truncated to 160 chars). Forensics for "typing succeeded but the UI
  rejected the value".
- `<key>_strict` (bool): `True` iff action succeeded **and**
  `observed.strip() == text.strip()`. **Strict UI signal.**

Only `<key>_strict=true` counts toward `scenario_success_strict_ui`;
`<key>=true` counts toward `scenario_success_action_only`.

Per-scenario collapse via
`_lib.scoring.score_espresso_mirror_scenario(scenario_name, cov)` returns
`(strict_ui_pass, action_only_pass, strict_fail_reasons)`. `strict_ui_pass`
is the per-scenario strict verdict (`scenario_success_strict_ui` in the
coverage row).

The shared helper knows only the Metronome scenario names
(`initialState`, `tempoMarkingsWalk`, etc.); for any other name it returns
`(False, False, ["unknown_scenario_name"])`, so silent passes are
impossible. Each per-app module in this repository scores its own
scenarios in `<app_id>/scoring.py`. The tag set for `strict_fail_reasons`
(`action_false:<key>`, `ui_not_matching:<base>`,
`initial_marking_mismatch`, etc.) lives in [`_lib/scoring.py`](_lib/scoring.py).

---

## 5. Required artifacts per run

The run tables are built by `tools/build_run_table_extended.py`, which reads
specific files at fixed paths in Android Runner's output folder (columns in
`6_results_data/RUN_TABLE_COLUMNS_EXPLANATION.md`). A missing artifact leaves its
columns empty or `unknown` (for example `crash_anr_status`), which weakens
cross-variant analysis. Write all of these every run:

Under `output/<run_id>/data/<device>/<subject_slug>/`:

| Artifact                                              | Writer                                | Purpose                                                                  |
|-------------------------------------------------------|---------------------------------------|--------------------------------------------------------------------------|
| `appium_status.json`                                  | `_lib.status.write_appium_status`     | Session lifecycle (created / timeout / workload_started / steps_executed). |
| `appium_workload_coverage.jsonl`                      | `_lib.coverage.append_coverage_jsonl` | One JSON object per scenario / cycle (the strict/action keys live here). |
| `<workload>_scenario_report.json`                     | `_lib.coverage.write_scenario_report` | Per-scenario aggregate counts (strict / action / fail reasons).          |
| `<workload>_scenario_report.txt`                      | same call                             | Human-readable companion of the JSON.                                    |
| `appium_ui_dumps/<stem>_<epoch_ms>.xml`               | `_lib.coverage.save_page_source`      | `driver.page_source` snapshot, capped by `APPIUM_UI_DUMP_MAX`. Always written on startup-package-timeout / startup-main-ui-timeout; opt-in elsewhere via `APPIUM_SAVE_UI_ON_MISS`. |
| `crash_anr_status.json`                               | `Scripts/detect_crash_anr.py`         | `none` / `crash` / `anr` / `system_error_dialog` / `lost_foreground` plus evidence lines. |
| `batterymanager/logcat_<serial>_*.csv`                | AndroidRunner BatteryManager plugin   | Per-sample raw current + voltage; sliced post-hoc into the three energy windows (§ 11). |
| `Aggregated_Results_Batterymanager.csv` *(per-device)*| AndroidRunner aggregator              | `Energy trapz (J)` whole-window aggregate (mWh = J / 3.6).               |

The default `<workload>` stem is `espresso_mirror`; per-app modules that
implement other workloads pass their own name to `write_scenario_report`.

For the AndroidRunner-side hook order producing these artifacts (the
`scripts.before_experiment` / `before_run` / `after_launch` / `interaction` /
`before_close` / `after_run` / `after_experiment` chain) see any configuration in
[`android-runner/configs/`](../android-runner/configs/).

---

## 6. Workload contract (since 2026-05-08)

> **The contract (2026-05-08):** run all `N` scenarios end-to-end and record
> the actual wall time as a measured output. It replaces an earlier strict
> 120 s time-box.

### Workload completion criterion

Each subject app exposes an `APP_SCENARIO_LIST` (e.g.
`CALCULATOR_SCENARIO_LIST = (...)`, length `N`, locked per § 6.5 below)
which is the **single canonical scenario sequence** for that app. The
`run_workload(experiment, device)` implementation MUST run exactly
`APP_SCENARIO_LIST` end-to-end, in order, with no time-box and no
foreground-keeping idle loop. The workload is complete when the last
scenario returns. The workload thread joins, `appium_status.json` is
written, and `main()` returns immediately afterwards.

`scenarios_planned` (= `len(APP_SCENARIO_LIST)`) and
`scenarios_completed` (counted from `appium_workload_coverage.jsonl`)
both land in the run tables. A run is valid for cross-variant
energy comparison only if `scenarios_completed == scenarios_planned`.

### Wall time is a measured output, not a controlled input

The workload's wall time (`duration_workload_s` in the run tables) is
the on-device duration between the first scenario `ts` and the last
scenario `ts`. It is **observed**, not constrained. Per-device speed
differences (e.g. Pixel 9 finishing the same 7 Metronome scenarios in
~50 s vs Pixel 3's ~107 s) appear as a real measured output rather than
as a silent denominator change.

### Blocking contract preserved

The AndroidRunner Python3 hook still runs in a child process that is
killed when `main()` returns. The per-app `run_workload` MUST therefore
still block `main()` until the workload thread completes, with
`_lib.timing.start_workload_thread_and_block_until_done(workload, max_wait_s)`,
where `max_wait_s` is derived from `experiment.duration` as a
**hard ceiling** (e.g. `max(2 × duration, 240 s)` in calculator), not as the
workload length. The deadline only triggers if a scenario hangs catastrophically;
healthy runs end when the last scenario returns, well below the ceiling.

The experiment JSON keeps `interaction_covers_duration: true` so
AndroidRunner does not add a second `time.sleep(duration)` after the
script returns.

### Energy windows still composed from the same per-sample CSV

The three-window split (`pre_workload`, `workload_only`, `whole_window`)
composes WITH the contract:

- `pre_workload` = Profiler start → first scenario `ts`.
- `workload_only` = first scenario `ts` → last scenario `ts` (bounded by
  scenario completion, not by a 120 s timer).
- `whole_window` = Profiler start → Profiler stop (the Profiler is still
  bounded by `experiment.duration` as a ceiling).

The window boundaries (Profiler start and stop, Appium first and last
scenario `ts`) stay well-defined, so the per-sample split
(`tools/build_run_table_extended.py`) does not change.

### The `duration` field

In the configurations, `duration` (120000 ms, or 150000 ms for androidskk,
calculator and poetskingdom) is a **profiler-window ceiling**, not the
workload length.

---

## 6.5 Per-app scenario calibration (since 2026-05-08)

Each subject app has a single locked scenario count `N` chosen once on
Pixel 3 baseline with `BATTERY_STATS` granted, then reused unchanged on
every variant × every device. This makes scenario count the cross-variant
comparable unit (see § 6 above).

Recipe (locked once per app):

1. Implement all candidate scenarios in
   `appium_android_tests/<app>/scenarios.py` with no time-box.
2. Run once on Pixel 3 baseline with `BATTERY_STATS` granted to the
   BatteryManager companion app (`Scripts/before_experiment_grant_battery_stats.py`
   grants it in the `before_experiment` chain).
3. Read per-scenario wall times from
   `appium_workload_coverage.jsonl` (each row carries a `ts` field).
4. Compute cumulative wall time. Pick the largest `N` where cumulative
   `≤ 120 s` (allow up to 130 s if scenario `N+1` would push it over;
   accept `N` if cumulative lands within `[100, 130] s`).
5. Document the calibration in
   `appium_android_tests/<app>/CALIBRATION.md` (per-scenario wall times,
   chosen `N`, why).
6. Lock `N` as `APP_SCENARIO_LIST` in `scenarios.py`.
7. Use exactly that list on all 3 devices forever.

For Metronome (an app outside the final cohort), `N` was locked at 8 (Set α,
audio-enriched, calibrated 2026-05-08 on Pixel 3 baseline at ~117 s wall
time). Set α drops `tempoMarkingsWalk` (its wall time was dominated by
Appium-RTT, not the app under test) and adds two audio-heavy scenarios
(`playFastSubdivisions`, `tempoSweepPlay`) so audio playback covers ~43%
of scenario time (the energy axis where packers actually differ).

The scenarios that are excluded from the energy comparison stay in
`scenarios.py` as a separate functional-validation suite: they are
exercised in non-energy diagnostic runs to catch UI-correctness
regressions, but the energy comparison never integrates over them.

---

## 7. Env-var ownership

Per-app modules must NOT shadow shared env vars (no per-app
`APPIUM_SERVER_URL` interpretation). Touch shared vars only via the
`_lib.env_*` helpers, which keep parsing consistent (truthy values are
`1` / `true` / `yes` / `on` case-insensitive; numerics fall back to a
default on empty/non-numeric).

| Env var                              | Owner                | Default                  | Purpose                                                                                   |
|--------------------------------------|----------------------|--------------------------|-------------------------------------------------------------------------------------------|
| `APPIUM_APP`                         | Android Runner wrapper | set by the wrapper     | App id; each `interaction_appium_<app_id>.py` sets it (`os.environ.setdefault`) and imports `appium_android_tests.<app_id>`. |
| `APPIUM_WORKLOAD`                    | per-app module       | per-app default          | Selects the scenario suite within a per-app module; the modules in this repository implement only `espresso_mirror`. |
| `APPIUM_BUILD_LABEL`                 | per-app module       | `""`                     | Free-form tag (`baseline` / `r8` / `allatori` / `bangcle`) included in JSONL rows + scenario report. Lets one config run across multiple variant builds while keeping rows distinguishable. |
| `APPIUM_USE_VIEW_IDS`                | per-app module       | `false`                  | Not read by the modules in this repository (linkhub only prints it). |
| `APPIUM_UI_DUMP_DIR`                 | `_lib.coverage`      | `<OUTPUT_DIR>/appium_ui_dumps` | Override directory for `page_source` XML dumps.                                       |
| `APPIUM_UI_DUMP_MAX`                 | `_lib.coverage`      | `32`                     | Max XML dumps per process. `0` disables.                                                  |
| `APPIUM_SAVE_UI_ON_MISS`             | `_lib.coverage`      | `false`                  | If truthy, dump `page_source` on tracked-step misses (startup failures always dump regardless). |
| `APPIUM_WAIT_FOR_PACKAGE_S`          | `_lib.driver`        | `60`                     | Timeout for the foreground package to settle.                                             |
| `APPIUM_WAIT_MAIN_UI_S`              | `_lib.driver`        | `45`                     | Timeout for app-specific main-UI predicate after the package is correct.                  |
| `APPIUM_SKIP_U2_INSTALL`             | `_lib.driver`        | `false`                  | If truthy, skip pushing UiAutomator2 server APKs (faster reconnects; unsafe for first connect). |
| `APPIUM_FORCE_U2_INSTALL`            | `_lib.driver`        | `false`                  | If truthy, never skip server install (overrides SKIP).                                    |
| `APPIUM_ATTACH_ONLY`                 | `_lib.driver`        | `false`                  | If truthy, set `autoLaunch=false` and skip `app_activity`; caller must already have the app foregrounded. |
| `APPIUM_APP_WAIT_DURATION_MS`        | `_lib.driver`        | `10000`                  | Appium's `app_wait_duration` capability. Lower fails faster on stuck splashes; higher gives packed APKs more headroom. |
| `APPIUM_SESSION_CONNECT_TIMEOUT_S`   | `_lib.driver`        | `120` (clamped `[5,900]`)| Wall-clock cap for `webdriver.Remote()`. Exceeding it raises `SessionConnectTimeoutError` and writes `appium_status.json` with `session_timeout=True`. |
| `APPIUM_SERVER_URL`                  | `_lib.driver` (read in per-app) | `http://127.0.0.1:4723` | Base URL for the Appium server. Read by the per-app entrypoint and passed to `connect_remote_webdriver`. |

Per-app extensions (`CALCULATOR_*`, `DOCUMENTER_*`, `LINKHUB_*`, `TIPUOUS_*`
and so on) are documented in the module docstring of each
`<app_id>/scenarios.py`.

---

## 8. Crash / ANR isolation

Crash and ANR runs are **not** silently averaged with healthy runs because
the system UI / recovery behaviour they trigger affects device-level energy
measurements. Without explicit isolation, a single Bangcle-induced ANR run
can compress the median of three reps toward "low energy" purely because
the app died and the device idled.

`Scripts/after_run.py` invokes `Scripts/detect_crash_anr.py` after every
Run. The detector:

1. Parses logcat for crash / ANR signatures (`FATAL EXCEPTION`,
   `ANR in <pkg>`, system error dialog markers).
2. Writes
   `output/<run_id>/data/<device>/<subject_slug>/crash_anr_status.json`
   with one of `none` / `crash` / `anr` / `system_error_dialog` /
   `lost_foreground`, plus the matching log lines as evidence.

The run-table builder (`tools/build_run_table_extended.py`) reads that JSON
into the `crash_anr_status` column; if the JSON is missing, the column is
`unknown`.

**Fallback path.** When the experiment JSON does not enable the
`adb_log` persistency strategy for logcat, the detector falls back to
parsing the BatteryManager CSV-side logcat dumps that AndroidRunner
captures alongside the energy CSV. Both paths produce the same
`crash_anr_status.json` schema.

Implementation: [`Scripts/detect_crash_anr.py`](../android-runner/Scripts/detect_crash_anr.py).

---

## 8.5 Android 16 PageSizeMismatchDialog dismissal (added 2026-05-15)

Apps whose native libraries are not 16-KB ELF-aligned trigger a system warning dialog
(`AppWarnings.PageSizeMismatchDialog`: "Android App Compatibility", "App isn't 16 KB
compatible") on each new Activity start when run on Android 16 in a debug build. The dialog
blocks the UI behind it until dismissed and re-fires on each Activity transition unless the
**"Don't Show Again"** button is tapped (which sets a per-package persistent flag).

Affected variants in our cohort:

- **All Bangcle packs** (`libSecShell.so` isn't 16-KB-aligned: Bangcle's published toolchain
  predates Android 16's 16-KB page-size requirement).
- **Baseline debug builds** of apps that ship transitive native deps from Compose
  (`libandroidx.graphics.path.so`), CameraX (`libimage_processing_util_jni.so`), Rive
  (`librive-android.so` + `libc++_shared.so`), LibVNCClient (`libnative-vnc.so`), FFmpeg
  + libtorrent4j (`libmpv.so` etc.), or Chaquopy (`libpython.so`).

Per-app modules whose APKs trigger this dialog MUST add the
``_dismiss_page_size_dialog_permanently(driver)`` helper, modeled after
[`appium_android_tests/pdfviewer/scenarios.py`](pdfviewer/scenarios.py) (also used in
[`appium_android_tests/calculator/scenarios.py`](calculator/scenarios.py)):

```python
def _dismiss_page_size_dialog_permanently(driver: Any) -> bool:
    """Tap "Don't Show Again" on the Android-16 PageSizeMismatchDialog if visible."""
    btn = _find_by_text(driver, "Don't Show Again", timeout=0.8)
    if btn is None:
        return False
    try:
        btn.click()
        log("dismissed PageSizeMismatchDialog with 'Don't Show Again' (persistent)")
        time.sleep(0.8)
        return True
    except Exception as ex:
        log("PageSizeMismatchDialog dismiss failed: %s %s" % (type(ex).__name__, ex))
        return False
```

Call the helper:

1. **At session start, before `await_app_ready`**: the dialog may be on top of the
   launcher activity on first install.
2. **After `await_app_ready` returns**: some Android 16 builds re-fire the dialog once
   the activity stack settles.
3. **Defensively before each scenario in the per-scenario dispatch loop**: each new
   Activity transition (PdfViewerActivity, HoroscopeDetailActivity, ComposeActivity, etc.)
   can re-fire the dialog if the persistent flag hasn't been set yet.

**Energy / methodology impact: zero.** Wall-time impact is ~1 second once at session start
to dismiss; the energy windows (`workload_only`, `whole_window`) start AFTER the dialog is
dismissed, and the dialog itself is a passive alert view that doesn't consume meaningful
power. Verified on Pdf-Viewer on 2026-05-15: baseline + Bangcle Pixel 9 runs both
landed 10/10 strict-UI pass at near-identical wall times (137.5s / 137.6s) with the helper
enabled.

The dialog is also a Bangcle market-viability finding: the vendor toolchain has not been
refreshed for Android 16's 16-KB page-size requirement.

---

## 9. ABI compatibility check per device

ABI mismatches are an **artifact-side** failure, not a device or adb bug.
Some packers (notably Bangcle on legacy pipelines) emit only
`armeabi-v7a` JNI libs; modern Pixel-class / Android 15 devices accept
only `arm64-v8a`. Adding such an `(app, packed-variant, Pixel 9)` cell to
the experiment without checking ABIs first wastes a measurement slot and
yields `INSTALL_FAILED_NO_MATCHING_ABIS` instead of an energy trace.

Before adding a new `(app, variant, device)` cell, verify ABI compatibility:

**Read APK ABIs.**

```bash
aapt dump badging <apk> | grep native-code
```

The output looks like `native-code: 'arm64-v8a' 'armeabi-v7a'` (fat) or
`native-code: 'armeabi-v7a'` (32-bit only). An empty / missing
`native-code:` line means the APK has no native libs and runs on every
ABI.

**Read device ABIs.**

```bash
adb -s <serial> shell getprop ro.product.cpu.abilist
```

Pixel 3 → `arm64-v8a,armeabi-v7a` (accepts both). Pixel 6 / Pixel 9 →
`arm64-v8a` (64-bit only).

**The Metronome × Bangcle × Pixel 9 canonical example.** Bangcle's output
on Kr0oked Metronome ships only `lib/armeabi-v7a/` plus
`libSecShell*.so`; Pixel 9 reports `arm64-v8a` only; install fails with
`INSTALL_FAILED_NO_MATCHING_ABIS`. The mitigation is to repack with
`arm64-v8a` (or fat-ABI) shells, **not** to weaken the protection to force
install. For pure-bytecode apps, `tools/inject_arm64_stub.sh` adds an
`arm64-v8a` stub library to the baseline APK before packing, so the Bangcle
pack installs on the Pixel 9. If the toolchain cannot emit 64-bit shells,
mark that cell as `variant-device-excluded` and record the reason; never
skip it silently.

---

## 10. `application_id` and the fresh-install hook

Packed APK filenames are not reliable indicators of the manifest package
(Bangcle outputs are often named after the protector, not the app). The
experiment JSON's `application_id` field is what AndroidRunner uses to
launch the app, force-stop it between runs, and route logcat. **Always set
`application_id` to the manifest package**, not to a filename heuristic.

Reading the manifest package from an APK:

```bash
aapt dump badging <apk> | grep "package: name="
# → package: name='com.bobek.metronome' versionCode='...' ...
```

This is the value of `application_id` in each configuration
(`android-runner/configs/`). For Kr0oked Metronome that is
`com.bobek.metronome` regardless of whether the APK file is
`app-debug.apk`, `metronome-bangcle.apk`, or `SecShell_signed.apk`.

**Fresh-install hook.** Each Run installs the APK from the experiment JSON
`paths`. AndroidRunner does not by default uninstall a previous variant:
it overlays. That is wrong for cross-variant comparisons because the
previous variant's data dir, ART caches, and JIT state can persist. We
prevent the overlay with a `before_experiment` hook that uninstalls the
package before the install.

Each application has its own hook,
`Scripts/before_experiment_uninstall_<app_id>.py` (for example
[`before_experiment_uninstall_calculator.py`](../android-runner/Scripts/before_experiment_uninstall_calculator.py)).
After chaining the device-preparation hooks (device state, `BATTERY_STATS`
grant, radios and savers, fixtures), it:

- Hardcodes `PACKAGE` (the manifest application id).
- `device.uninstall(PACKAGE)` if the package is currently installed; logs
  "not installed" otherwise so the experiment.log explains why no
  uninstall happened.
- Returns cleanly: it never raises into AndroidRunner's experiment loop
  (apart from the opt-in strict discharge check, which exits).

Each new app needs **its own** `before_experiment_uninstall_<app_id>.py`
copy with `PACKAGE` set to that app's manifest id, named in the
configuration's `scripts.before_experiment`.

---

## 11. BatteryManager is the only energy source

Decision of **2026-05-08**: all experiments use the AndroidRunner
BatteryManager plugin, with no sysfs sampling. This keeps the energy
column comparable across devices, since per-device sysfs schemas diverge
on Android 13+.

The run tables carry the whole-window energy plus the **three-window
post-hoc split**:

| Energy column                                         | Duration column            | Window                                                       |
|-------------------------------------------------------|----------------------------|--------------------------------------------------------------|
| `pre_workload_joules`                                 | `duration_pre_workload_s`  | Profiler start → first Appium scenario `ts`                  |
| `workload_joules` (in mWh: `energy_workload_only_mwh`) | `duration_workload_s`      | First Appium scenario `ts` → last Appium scenario `ts`       |
| `total_cell_joules` (in mWh: `aggregated_energy_mwh`) | `duration_whole_window_s`  | Profiler start → Profiler stop                               |

The runtime profiler boundaries are unchanged: the split is a post-hoc
analysis of the same raw CSV, computed by `tools/build_run_table_extended.py`.
`6_results_data/RUN_TABLE_COLUMNS_EXPLANATION.md` describes the columns.

**Caveat: USB-supplied current can mask discharge.** When USB stays
physically plugged, the charge IC may keep supplying current and
BatteryManager reads ~0 W instead of the real load (Pixel 3, 2026-05-08:
baseline Metronome read 0.009 W vs Bangcle 0.67 W same device same
protocol). Cross-variant comparisons made under that condition are
invalid. In the experiment, the sweep runner cuts the phone's USB power
with a YKUSH 3 switch and verifies that the phone discharges before each
cell (`tools/sweep_runner_parallel_ykush.sh`,
`android-runner/Scripts/_lib_device_state.py`).

---

## 12. Run-table row contract

Every cell `(batch, phone, app, variant, rep)` lands as exactly one row
in its batch's run table (`6_results_data/run_tables/`): the row of the
measured run. `run_id` (the AndroidRunner output timestamp folder, e.g.
`2026.05.05_235701`) names that run's output folder. A cell that failed
and was re-run keeps only the run that counts.

APK provenance (`apk_path`, `apk_sha256`, `apk_storage`) is populated
automatically: `Scripts/before_run.py` writes `apk_meta.json` via
`_lib_apk_meta.write_apk_meta()`, and the run-table builder reads it.

Row-level issues go into `issues_flags`, so they are greppable without
having to dig into per-run artifacts. When in doubt, prefer adding a flag
over silently dropping the row.

---

## 13. Adding a new app: step-by-step checklist

This is the worked recipe for scaffolding a new per-app module + its
matching experiment configs, up to a clean run for
`(new_app, baseline, pixel3)`.

1. **Pick the `<app_id>` slug** (lowercase ASCII, `[a-z0-9_]` only,
   matching the `app` column convention from § 2; e.g. `calculator`,
   `keep_recipe`).
2. **Copy an existing per-app module.**
   ```bash
   cp -r appium_android_tests/calculator appium_android_tests/<app_id>
   ```
   This brings `__init__.py` with the `from .scenarios import
   run_workload` re-export already in place, plus a `scenarios.py` and a
   `scoring.py` to adapt.
3. **Rewrite `scenarios.py` for the new app's UI.** Replace the
   calculator-specific selectors and scenario list with the app's own
   espresso-mirror suite. Keep the § 3 contract: build options via
   `build_uiautomator2_options`, connect via `connect_remote_webdriver`,
   gate readiness via `await_app_ready`, block via
   `start_workload_thread_and_block_until_done`, write `appium_status.json` /
   coverage JSONL / scenario report on the way out, never raise.
   Implement the candidate scenarios with **no time-box**: all timing
   constraints flow through the per-app calibration (next step) into the
   locked `APP_SCENARIO_LIST` (step 4).
4. **Calibrate `N` on Pixel 3 baseline.** Per § 6.5:
   1. Run the candidate suite once on Pixel 3 baseline with
      `BATTERY_STATS` granted to the BatteryManager companion app
      (`Scripts/before_experiment_grant_battery_stats.py`).
   2. Read per-scenario wall times from
      `appium_workload_coverage.jsonl`.
   3. Pick the largest `N` where cumulative wall time `≤ 120 s` (allow
      up to 130 s if scenario `N+1` would push it over; accept `N` if
      cumulative lands within `[100, 130] s`).
   4. Document the calibration in
      `appium_android_tests/<app_id>/CALIBRATION.md`: per-scenario
      timings, chosen `N`, rationale.
   5. Lock `N` as `<APP>_SCENARIO_LIST` (e.g. `CALCULATOR_SCENARIO_LIST`)
      in `scenarios.py`. The remaining candidate scenarios stay in
      `scenarios.py` for separate functional-validation runs but are
      excluded from the energy comparison.
   6. Use exactly that list on all 3 devices, all 4 variants, forever.
5. **Add the per-app uninstall hook.**
   ```bash
   cp Scripts/before_experiment_uninstall_calculator.py \
      Scripts/before_experiment_uninstall_<app_id>.py
   ```
   Edit the new file's `PACKAGE = "..."` to the manifest application id
   (§ 10).
6. **Wire the AndroidRunner interaction hook.** Copy
   `Scripts/interaction_appium_calculator.py` to
   `Scripts/interaction_appium_<app_id>.py`, replace `calculator` with
   `<app_id>` (the `APPIUM_APP` default and the import), and name the new
   wrapper in the configuration's `scripts.interaction`.
7. **ABI-check each variant APK against the target devices** (§ 9). Any
   failing `(variant, device)` pair is recorded as variant-device-excluded,
   never silently skipped.
8. **Write the experiment configurations** from an existing one in
   [`android-runner/configs/`](../android-runner/configs/) (for example
   `calculator_baseline_debug_pixel3_wifi_ykush.json`): set the APK in
   `paths`, `application_id`, the device in `devices`, and the app's hooks
   in `scripts`. Keep the shared settings unchanged: `repetitions: 1` (the
   batch scheduler repeats the cells), `interaction_covers_duration: true`,
   `time_between_run: 5000`, the `profilers` block, and `duration` as a
   profiler-window ceiling (§ 6).
9. **Smoke run on Pixel 3 first**: most permissive device (both ABIs,
   BatteryManager works after the `BATTERY_STATS` grant):
   ```bash
   cd android-runner
   python3 . examples/batterymanager/<app_id>_baseline_debug_pixel3_wifi_ykush.json
   ```
   Expect every artifact from § 5 under `output/<run_id>/`, with
   `appium_status.json.failure_reason == null`, a non-empty
   `appium_workload_coverage.jsonl`, and `scenarios_completed == N`.
10. **Check the run's artifacts.** `crash_anr_status.json` should read
    `none`, `appium_status.json` should show the session created and the
    workload started, and the coverage rows should hold `N` scenarios. The
    run-table builder (`tools/build_run_table_extended.py`) reads them into
    the cell's row (`appium_session_created`, `workload_started`,
    `crash_anr_status`, `scenarios_planned`, `scenarios_completed`).
11. **Scale to Pixel 6 and Pixel 9** only after the Pixel 3 smoke is
    clean. Write the matching device configurations; the `BATTERY_STATS`
    grant runs on each device in the `before_experiment` chain
    (`Scripts/before_experiment_grant_battery_stats.py`).
