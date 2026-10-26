# Android Runner set-up used in the experiment

The measurements were run with [Android Runner](https://github.com/S2-group/android-runner) (S2 group, VU Amsterdam),
extended for this experiment. This folder holds only what this study added: the configurations, the hook scripts and
the changes to the framework. The complete, ready-to-run environment is the public fork
[IrenaRistova/android-runner-master-experiment](https://github.com/IrenaRistova/android-runner-master-experiment),
branch `master-experiment`, commit `acb54ea` (tag `thesis-batches`).

## What is here

- `configs/`: the 120 configurations that ran the four batches, one per application, build and phone
  (`<app>_<build>_<phone>_wifi_ykush.json`; 10 applications, 4 builds, 3 phones).
- `Scripts/`: the hook scripts these configurations call, directly or through one another (device preparation,
  launch, the Appium interaction for each application, result collection), and the two helper modules they import
  (`_lib_apk_meta.py`, `_lib_device_state.py`); 36 files in all.
- `framework-changes.patch`: the changes to Android Runner itself, against the original commit `cca271c3`
  (the launch handling, the USB handler for a switchable hub, and the battery-manager, Android, frame-time and
  garbage-collection profiler plugins).
- `devices.example.json`: the device entries the configurations expect, with placeholders.
- Each configuration also has a `_comment` field, which Android Runner ignores. It was written by the script that
  generated the configurations, and its build label is wrong for the protected builds; their `paths` are correct.
- `batterymanager-companion.patch`: the local changes to
  [S2-group/batterymanager-companion](https://github.com/S2-group/batterymanager-companion) at commit `9c796aa`,
  from which the battery-manager companion app used in the experiment was built (the `BATTERY_STATS` permission in
  the manifest, and Gradle 8.5).

## Using it

The tools in this repository expect the Android Runner installation at `android-runner/` in the repository root, with
the configurations and `Scripts/` in `examples/batterymanager/`: the batch scheduler
(`tools/sweep_runner_parallel_ykush.sh`) runs Android Runner from there, and the interaction scripts and
`Scripts/before_experiment_sync_fixtures.py` take the folder four levels above `Scripts/` as the repository root (to
import `appium_android_tests/` and to find the fixtures). This folder holds only the study's additions, so move it
aside first (for example to `android-runner-additions/`).

1. Either clone the fork named above at `android-runner/` (branch `master-experiment`), which already has this layout,
   or build the same installation from Android Runner itself:
   1. Clone it and check out the commit the changes are based on:
      `git clone https://github.com/S2-group/android-runner && cd android-runner && git checkout cca271c3`
   2. Apply the framework changes: `git apply ../android-runner-additions/framework-changes.patch`.
   3. Copy `configs/*.json` and `Scripts/` into `examples/batterymanager/`.
2. Add your phones to `devices.json` (see `devices.example.json`), and adapt the absolute paths in the configurations
   (`adb_path`, the APK in `paths`, and the `usb_handler` commands, which call `tools/ykush_ar_power.sh` from this
   repository). `Scripts/before_run_direct_launch.py` looks for `aapt` or `aapt2` on `PATH` first and then in two
   fixed build-tools paths, so put one of them on `PATH` or adapt those paths.
3. The batch scheduler in `tools/` (see the main README) drives Android Runner, one configuration per measurement, in a
   randomised order per phone. A single configuration can also be run directly from the repository root:
   `python3 android-runner <config>`.

The interaction scripts call the scenarios in `appium_android_tests/` of this repository and expect an Appium server
per phone. The measured APKs are not in the repository (see the main README).

`Scripts/before_experiment_sync_fixtures.py` pushes documenter's sample PDF to every phone. It expects the Documenter
source ([ViliusSutkus89/Documenter](https://github.com/ViliusSutkus89/Documenter)) at
`app_repositories_newest/final_dataset/ViliusSutkus89_Documenter/` in the repository root, which is not in this
repository; the file is `app/src/androidTest/assets/testFiles/geneve_1564.pdf` of that source.
