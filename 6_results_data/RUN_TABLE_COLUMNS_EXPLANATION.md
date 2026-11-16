# Run tables: the columns the analysis uses

Four files in `run_tables/`, one per batch, 1200 rows each (4800 cells).

| thesis name | file |
|---|---|
| pilot | `run_table_extended_final_ykush_2026-08-04.csv` |
| series batch 1 | `run_table_extended_final_ykush_batch2_2026-08-10.csv` (**the tag says batch2**) |
| series batch 2 | `run_table_extended_final_ykush_series2_2026-08-14.csv` |
| series batch 3 | `run_table_extended_final_ykush_series3_2026-08-17.csv` |

One row is one **cell**: one application × one technique × one device × one repetition.

## Columns used

| column | meaning |
|---|---|
| `app`, `phone`, `variant`, `rep` | identity of the cell. `variant` is the technique: `baseline_debug` (control), `allatori_debug`, `bangcle_debug`, `freerasp_debug` |
| `cell_start_ts_utc` | cell start, UTC. Used for the pilot regime cutoff (2026-08-07, when the YKUSH board's external supply was fitted; `thesis_lib.PILOT_CUTOFF`) |
| `scientifically_valid` | `"True"` if the cell passed every validity gate. A string, not a boolean |
| `cell_failure_class` | why an invalid cell is invalid |
| `negative_control` | non-empty for keep_recipe/allatori and linkhub/allatori, where the obfuscator never ran |
| `scenarios_completed`, `scenarios_planned` | scenario **executions**, including repeats |
| `workload_joules` | energy of the **active phase** (RQ2, the scenarios), J |
| `pre_workload_joules` | energy of the **passive phase** (RQ1), from the first energy sample to the first scenario, J (earlier drafts called it the startup window). It opens after the application has been launched (about seven seconds after its first frame), so it does not contain the cold launch |
| `duration_workload_s`, `duration_pre_workload_s` | window durations, s |
| `workload_avg_watts` | mean power over the workload window, W |
| `run_id` | name of the run's Android Runner output folder (laptop local time) |
| `cell_end_ts_utc` | run end, UTC |
| `apk_sha256` | SHA-256 of the measured APK |
| `android_release` | the phone's Android version |
| `total_cell_joules` | energy over all energy samples of the run, J |
| `battery_level_pre_pct` | battery level at the first energy sample, % |
| `cpu_avg_pct` | mean processor utilisation over the run from Android Runner's Android profiler (device-wide), % |
| `mem_pss_avg_mb` | the application's mean total PSS (`dumpsys meminfo`), MB |
| `frame_time_median_ms`, `frame_time_p95_ms`, `delayed_frames_count` | frame timing from the frame-time profiler |
| `scenarios_json` | per-scenario outcomes |
| `app_crash_signature` | the crash recorded in the system log, if any |

## Columns that must NOT be used naively

| column | why |
|---|---|
| `battery_temp_avg_c`, `battery_temp_max_c` | both are the **same pre-cell snapshot**, equal to floating-point noise in every row that has them (8 of the 4,800 are blank). The battery broadcast holds one value for the whole cell |
| `teardown_joules` | not a measured phase. The profiler stops asynchronously and USB power is restored while it is still sampling, so it contains charge current |
| `thermal_status_pre`, `thermal_skin_pre_c`, `thermal_cpu_pre_c` | **void for the pilot**: an unscoped file glob imported series 1's readings. ~97% correct for the series |
| `wall_s` | includes the battery top-up before the cell and reaches about 7,700 s in the series and 11,556 s in the pilot. The measurement window is `duration_whole_window_s` |
| `cell_failure_class` | a later label can overwrite an earlier one: one series-3 cell that tripped the power-integrity gate is labelled `DEVICE_UNSUITABLE`. It is excluded either way |

## Other inputs (`inputs/`)

Every file in `inputs/` is listed in `DATA_MAP.md` §4 with the script that builds it and its PROVENANCE file.

## Builds the analysis sets aside

The run tables keep every measurement. Apart from the two negative controls (column `negative_control`), they do not
mark the builds the analysis leaves out; `6_results_analysis/notebooks/thesis_lib.py` removes them (`EXCLUDED_APPS`,
`EXCLUDED_BUILDS` and `PASSIVE_EXCLUDED_APPS`), and each reason can be checked in this package:

| left out | reason | where to check |
|---|---|---|
| androidskk, every build | its unprotected control already carries obfuscated code | Table OA3, `inputs/control_build_screen.csv` |
| itsok's Allatori build | a release build in an otherwise all-debug experiment | Table OA16, `inputs/variant_parity.csv` (`debuggable`) |
| medtimer's packed build | it carries JaCoCo coverage probes that its control lacks | `inputs/coverage_screen.csv` |
| medtimer in the passive phase, every build | its introduction screens put interaction into that phase | Table OA4 |

The build checks, like the static code effects, are measured from the APKs, which are not in
this package (available on request); `inputs/build_checks.PROVENANCE.md` gives the command for each.

`derived/thesis_numbers_reference.json` is the independent implementation's output,
used only to check the notebooks. `derived/thesis_numbers_ten_apps_sensitivity.json`
is the pre-exclusion cohort, for the sensitivity comparison.
