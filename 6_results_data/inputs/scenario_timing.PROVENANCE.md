# scenario_timing_series.csv — provenance

**What it is.** One row per Appium scenario run, in every series cell with a run: the scenario's
name and how long it took. The cohort table (Table S18, `Descriptive_Statistics.ipynb`) reads it
for each application's mean scenario duration.

**Built by.** `python3 tools/extract_scenario_timing.py` (2026-11-16). The source is each run's
`appium_workload_coverage.jsonl` in Android Runner's output folder
(`<run_id>/data/<phone folder>/<APK slug>/`), which is not in the repository
(`raw_manifest.csv.gz` lists it). The Appium workload appends one JSON line per scenario run. The
run-table builder (`tools/build_run_table_extended.py`) takes the active phase from the same file:
from the first scenario's start to the last scenario's end.
- 36,998 scenario runs in 3,592 cells.
- Two runs have no coverage file for the cell's build, and no analysed cell is among them: series 1,
  Pixel 3, medtimer, control, rep 9; series 2, Pixel 3, medtimer, Allatori, rep 4.
- `tools/check_data_complete.py` checks that every analysed cell has as many rows as the run
  table's `scenarios_completed`.

## What each column is

- `batch`, `phone`, `app`, `variant`, `rep`: the cell.
- `seq`: the scenario run's position in the cell, from 1.
- `scenario_name`: the name the workload logged. A scenario that repeats within a run to fill its
  120 s carries a suffix (`_repeat2`, `_r2`, ` round=2`); the notebook removes it to count distinct
  scenarios, as it does for the run tables' `scenarios_json`.
- `wall_s`: the scenario's duration as the workload timed it (`scenario_wall_seconds`), in seconds,
  millisecond resolution, on the laptop's clock.
- `ts`: the scenario's start, laptop clock, UTC, whole seconds.

## What it cannot see

- **The pause between two scenarios.** The active phase also holds the harness's short steps
  between scenarios, so the durations of a cell's scenarios add up to less than its active phase.
