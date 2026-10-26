# `inwindow_launches_series.csv`: provenance

> **Not used in the thesis.** The launch-energy estimate built on this file
> (`launch_energy_estimate.csv`) is kept for the record. `fuel_gauge_resolution.csv`, built
> from the same file, is used in Threats.

**Written by** `tools/extract_inwindow_launches.py` (2026-10-03), from the same two raw sources
as `launch_timing_series.csv` (`launch_timing.PROVENANCE.md`): the full logcat of every cell, and
the cell's BatteryManager samples. Neither is in this package (they are large; available on request).

**Which launches:** every cold launch (a process start of the subject application, followed by
its first display within 5 s) whose window lies inside the cell's energy samples. The launch
request must come at least 2 s after the first sample, and the display at least 1 s before the
last. Series batches only, every captured cell; the notebooks restrict them to the analysed
cells (the filter chain). Regenerated 2026-10-05 from the current logcat inputs
(`logcat_activity.PROVENANCE.md`): 1,442 launches.

- **A cell's first launch never qualifies,** because it precedes the energy measurement
  (`launch_timing.PROVENANCE.md`).
- **In the analysed cells, the qualifying launches are poetskingdom's, plus one other.** Its
  workload terminates and relaunches the application by design, for every build
  (`appium_android_tests/poetskingdom/scenarios.py`, `_create_poem`: `terminate_app`, 0.4 s,
  `activate_app`).

## What each column is

- `batch`, `phone`, `app`, `variant`, `rep`: the cell.
- `launch_number`: the launch's position among the cell's cold launches. 1 is the first, which
  is never in this file.
- `ttid_ms`: the launch's time to initial display, from
  `ActivityTaskManager: Displayed <package>/<activity> ...: +XsYms`.
- **The launch window** runs from the launch request (the display's log time minus `ttid_ms`)
  to the first display.
- `e_launch_j`: energy over the launch window (J).
- `e_launch_plus1s_j`: the same, extended 1 s past the first display.
- `p_before_w`: mean power over the 2 s before the launch request (W). In poetskingdom this
  includes the application being terminated.
- `e_launch_early_j`, `e_launch_late_j`: `e_launch_j` with the window shifted 0.33 s earlier
  and later.
- `samples_in_launch`: battery samples strictly inside the launch window.
- `gauge_updates_in_launch`: changes of the current reading inside the launch window, i.e.
  new fuel-gauge readings.
- `gauge_update_ms`: the median interval between changes of the current reading, over the
  whole cell.

## How the energy is computed

- **Power** is |current_now| × voltage, as in the run-table builder.
- **Integration:** the trapezoid rule over exactly the window. The power at each edge is
  interpolated linearly between the two samples around it.
- **Why the interpolation matters.** A launch lasts 0.3 to 1.2 s and spans only a few samples.
  Integrating only the samples inside the window would leave out up to one sample interval at
  each end. For a window this short, that is a large share.

## Why the clocks agree

Logcat's epoch timestamps and the BatteryManager timestamps are both read on the phone.

## What it cannot see

- **The first launch of a cell,** which happens before the energy measurement starts.
- **Anything finer than the fuel gauge.** The current reading changes only every 217 to 334 ms,
  depending on the device, so a control relaunch (poetskingdom) spans one or two readings
  (`6_results_analysis/tables/fuel_gauge_resolution.csv`). A reading is an average over the
  interval before it, so part of a launch's draw shows after its window. That is why the
  file holds the + 1 s variant and the shifted windows.
- **Whether a relaunch costs what a first launch costs.** These relaunches happen with the
  application already installed, compiled and recently run. The first launch after
  installation is not measured.
