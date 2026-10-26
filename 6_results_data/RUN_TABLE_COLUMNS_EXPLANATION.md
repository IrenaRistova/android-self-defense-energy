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
| `cell_start_ts_utc` | cell start, UTC. Used for the pilot regime cutoff (2026-08-07) |
| `scientifically_valid` | `"True"` if the cell passed every validity gate. A string, not a boolean |
| `cell_failure_class` | why an invalid cell is invalid |
| `negative_control` | non-empty for keep_recipe/allatori and linkhub/allatori, where the obfuscator never ran |
| `scenarios_completed`, `scenarios_planned` | scenario **executions**, including repeats |
| `workload_joules` | energy over the interactive workload window, J |
| `pre_workload_joules` | energy from profiler start to the first scenario, the **startup** window, J. It opens after the application has been launched (about seven seconds after its first frame), so it does not contain the cold launch |
| `duration_workload_s`, `duration_pre_workload_s` | window durations, s |
| `workload_avg_watts` | mean power over the workload window, W |

## Columns that must NOT be used naively

| column | why |
|---|---|
| `battery_temp_avg_c`, `battery_temp_max_c` | both are the **same pre-cell snapshot**, equal to floating-point noise in all 4800 rows. The battery broadcast holds one value for the whole cell |
| `teardown_joules` | not a measured phase. The profiler stops asynchronously and USB power is restored while it is still sampling, so it contains charge current |
| `thermal_status_pre`, `thermal_skin_pre_c`, `thermal_cpu_pre_c` | **void for the pilot**: an unscoped file glob imported series 1's readings. ~97% correct for the series |
| `wall_s` | includes the battery top-up before the cell and reaches ~7000 s. The measurement window is `duration_whole_window_s` |
| `cell_failure_class` | a later label can overwrite an earlier one: one series-3 cell that tripped the power-integrity gate is labelled `DEVICE_UNSUITABLE`. It is excluded either way |

## Other inputs (`inputs/`)

| file | used by | notes |
|---|---|---|
| `post_charge_k_window.csv` | Headline_Effects | per-cell `k` for the post-charge K-window rule |
| `logcat_activity_<batch>.csv` | Mechanism_Analysis | per-cell app-attributed logcat, attribution by process ID |
| `allatori_static_code_effects.csv` | Mechanism_Analysis | measured from the APKs; no notebook regenerates it (see its PROVENANCE file) |
| `app_characteristics.csv` | Descriptive_Statistics, Mechanism_Analysis | per-app category, size, SDK levels, permissions |
| `selection_funnel.csv` | Descriptive_Statistics | every count with its source |
| `control_build_screen.csv` | Descriptive_Statistics (Table 3) | source-file-name screen of the ten control APKs, with packages of any 1–2 character names |
| `freerasp_build_screen.csv` | Descriptive_Statistics | the same screen on the freeRASP builds, for comparison |
| `allatori_build_screen.csv` | Descriptive_Statistics | the same screen on the Allatori builds: the basis for the screen's minimum |
| `variant_parity.csv` | Descriptive_Statistics, Phase_Analysis | one row per build: version, SDKs, NSC, INTERNET, debuggable, protection marker, hash vs run tables |
| `allatori_renaming.csv` | Headline_Effects (Table 5) | classes Allatori renamed, measured on the swept APKs |

The build checks, like the static code effects, are measured from the APKs, which are not in
this package (available on request); `inputs/build_checks.PROVENANCE.md` gives the command for each.

`derived/thesis_numbers_reference.json` is the independent implementation's output,
used only to check the notebooks. `derived/thesis_numbers_ten_apps_sensitivity.json`
is the pre-exclusion cohort, for the sensitivity comparison.
