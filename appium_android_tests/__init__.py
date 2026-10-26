"""
Black-box Appium harness for the Master Experiment.

This package contains the Appium / UiAutomator2 test infrastructure used by the Android Runner
interaction hooks in ``Scripts/`` (``android-runner/Scripts/`` in this repository). It is layered as:

- ``appium_android_tests._lib``: app-agnostic shared library (driver lifecycle, scoring contract,
  coverage / status writers, workload-window timing).
- ``appium_android_tests.<app>``: per-app modules. Each one exposes ``run_workload(experiment, device)``
  which the corresponding ``Scripts/interaction_appium_<app>.py`` thin hook calls.

See ``CONVENTIONS.md`` for the full folder contract.
"""
