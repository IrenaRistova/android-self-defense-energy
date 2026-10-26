# `logcat_activity_<batch>.csv`: provenance

**Written by** `tools/logcat_app_parser.py`. The raw source is the full logcat of each cell
(`tools/sweep_logs/logcat/`, raw data that is not in this package but available on request;
`raw_manifest.csv.gz` lists it). The files were regenerated on 2026-10-05. Command, from the
repository root:

```
python3 tools/logcat_app_parser.py --sweep-tag <tag> --run-table 6_results_data/run_tables/<run table> \
    --out 6_results_data/inputs/logcat_activity_<batch>.csv
```

| batch | sweep tag | run table |
|---|---|---|
| pilot | `final_ykush_2026-08-04` | `run_table_extended_final_ykush_2026-08-04.csv` |
| series1 | `final_ykush_batch2_2026-08-10` | `run_table_extended_final_ykush_batch2_2026-08-10.csv` |
| series2 | `final_ykush_series2_2026-08-14` | `run_table_extended_final_ykush_series2_2026-08-14.csv` |
| series3 | `final_ykush_series3_2026-08-17` | `run_table_extended_final_ykush_series3_2026-08-17.csv` |

## What a row is

One row per cell: the capture of the run the run table measured.
- `--sweep-tag` selects the batch's captures through `tools/organise_sweep_logs.py`.
- `--run-table` keeps, per cell, the capture whose time span overlaps the measured run's battery
  samples. Both are on the phone's clock.

## Which lines are the application's

The processes `am_proc_start` reports under the application's own package: its main process,
named after the package, and its own sub-processes, named `<package>:<name>`. The package comes
from `app_characteristics.csv`, keyed by the application named in the capture's filename.

- **Why not a denylist of infrastructure prefixes** (`com.google.android.`, `com.android.`,
  `android.`): Google Play Services' shared processes are named `com.google.process.gapps` and
  `com.google.process.gservices`, outside every prefix, and `com.google.RilConfigService` too.
  A denylist would count them as the application.
- **The sub-processes matter.** In the analysed cells there is one, documenter's
  `:pdf2htmlEXWorker`, a conversion worker that runs in nearly every documenter cell. It logs one
  SELinux denial per cell in every build, a read of the root directory `/`. In the freeRASP build,
  freeRASP also runs its full set of checks inside it.

## Columns

- `phone`, `app`, `variant`, `rep`: the cell.
- `resolved`: 1 if the application's process appears in the capture.
- `total_lines`, `app_lines`, `noise_pct`: the capture's lines, the application's, and the share
  that is not the application's.
- `threads`: distinct threads of the application's processes that wrote at least one line.
- `gc_events`: garbage-collection lines. Most are forced by the memory profiler's polling.
- `span_s`: time from the application's first line to its last.
- `auditd_lines`: SELinux denial lines from the application's processes.
- `auditd_lines_main`: those from its main process only (added 2026-10-05).
- `hiddenapi_calls`, `talsec_calls`, `secshell_calls`: hidden-API access lines; those whose caller
  is freeRASP's or Bangcle's code.
- `native_libs`, `exceptions`: native libraries named in the application's lines; the commonest
  exceptions.
- `logfile`: the capture.
- `capture_matches_run`: 1 when the capture was checked against the measured run; blank when the
  run table has no run or battery samples to check it against.

## Which captures are kept

- **Series 1** ran in three sessions, on 10, 11 and 13 August. Its file has 1,200 rows, every one
  a capture of the measured run.
- **Superseded captures**, of earlier attempts at cells that were re-run, are dropped in every
  batch. The pilot had 100 of this kind, and 8 more from a false-start session (20260804_023525).
- **Truncated captures:** three captures stop 8–19 s before the first battery sample (series 2:
  Pixel 3 calculator Bangcle rep 6; series 3: Pixel 3 androidskk Allatori rep 5 and Pixel 3
  poetskingdom Allatori rep 5). They overlap nothing measured and are dropped too. One pilot cell
  lost its only capture the same way.
- **Coverage:** 2,734 of the 2,736 analysed series cells have a log of their measured run; the
  pilot, 1,199 of 1,200 cells.

**The index.** `specs/sweep_log_index.csv` maps every log file to its sweep tag, and `--sweep-tag`
selects captures through it. `tools/organise_sweep_logs.py` builds it from the wrapper logs and from
each session's own log (`parallel_<run_ts>.log`), whose header names the tag. The pilot's first
session (20260804_025319, 81 captures) has no wrapper log, so only its own log attributes it.

**The check.** `tools/check_data_complete.py` compares these tables with every capture on disk, by
time overlap with the measured run, so it does not depend on the index. See
`6_results_data/DATA_MAP.md`.
