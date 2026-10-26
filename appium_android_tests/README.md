# Interaction scenarios

The Appium scenarios that drive each measured application through its user interface in the active phase of a
measurement. Each application has its own module (`<app>/scenarios.py` and `<app>/scoring.py`), and `_lib/` holds the
code they share. [`CONVENTIONS.md`](CONVENTIONS.md) describes the layout and how a module is built.

## Running them

- The modules are not run on their own in the experiment: Android Runner's interaction scripts
  (`android-runner/Scripts/interaction_appium_<app>.py`) import them during each measurement.
- They need an Appium server with the UiAutomator2 driver for each phone. Start Appium from a shell in which
  `ANDROID_HOME` (or `ANDROID_SDK_ROOT`) points to the Android SDK; a scenario that finds neither reports it on stderr.
- The scenario log lines that the data pipeline parses are described in
  [`docs/SCENARIO_LOG_FORMATS.md`](../docs/SCENARIO_LOG_FORMATS.md).
