# Data map: where every piece of data is, and how to know it is complete

**Read this before using, rebuilding or explaining any data.** After regenerating any input, and
before explaining any gap in the data, run:

```
python3 tools/check_data_complete.py
```

It compares every derived input with the raw files and the run tables. Its exit status is 0 when
every check passes and 1 when any check fails. It runs in about 15 seconds. Without the raw data
(§2), `python3 tools/check_data_complete.py --no-raw` runs only the checks that need none.

> **The rule: never explain a gap from a derived file. Check the raw data.**

---

## 1. The four batches

The thesis names and the file tags differ. **Series 1's files carry the tag `batch2`,** because the
pilot was the first batch.

| thesis name | in Results? | sweep tag (in file names) | run table (`6_results_data/run_tables/`) | cells | first and last cell start (UTC) |
|---|---|---|---|---|---|
| pilot | **no** (Execution only) | `final_ykush_2026-08-04` | `run_table_extended_final_ykush_2026-08-04.csv` | 1,200 | 2026-08-04 00:53 → 08-10 03:01 |
| **series 1** | yes | `final_ykush_batch2_2026-08-10` | `run_table_extended_final_ykush_batch2_2026-08-10.csv` | 1,200 | 2026-08-10 04:34 → 08-14 00:03 |
| series 2 | yes | `final_ykush_series2_2026-08-14` | `run_table_extended_final_ykush_series2_2026-08-14.csv` | 1,200 | 2026-08-14 10:55 → 08-16 18:49 |
| series 3 | yes | `final_ykush_series3_2026-08-17` | `run_table_extended_final_ykush_series3_2026-08-17.csv` | 1,200 | 2026-08-16 23:02 → 08-19 03:40 |

A batch that was paused and resumed ran in several **sessions**. Each session's files share a stamp,
`YYYYMMDD_HHMMSS`, in the laptop's local time. Log captures per session:

| batch | sessions (captures) | captures | used by results | not used |
|---|---|---|---|---|
| pilot | 20260804_023525 (8, a false start), 20260804_025319 (81), 20260805_010029 (147), 20260805_212150 (50), 20260806_000737 (118), 20260807_040913 (729), 20260808_131330 (113), 20260810_001143 (62) | 1,308 | 1,199 | 109 |
| series 1 | 20260810_063438 (619), 20260811_194751 (298), **20260813_123124 (289)** | 1,206 | 1,200 | 6 |
| series 2 | 20260814_125458 (1,200) | 1,200 | 1,199 | 1 |
| series 3 | 20260817_010207 (1,200) | 1,200 | 1,198 | 2 |

The unused captures are of three kinds: superseded attempts at cells that were re-run, captures that
stopped before measuring began, and the pilot's false-start session. "Used by results" means the
capture of the measured run. For the pilot, that is the capture its log table uses, which no Results
number reads.

**The phones.** Android Runner names each phone's data folder after the phone's Wi-Fi entry in
`devices.json`: `Pixel 3-W`, `Pixel 6-W` and `Pixel 9-W` (`android-runner/devices.example.json`).
ADB runs over Wi-Fi, because the YKUSH cuts USB power.

**Clocks: compare like with like.**
- **The run tables' `cell_start_ts_utc`** is the laptop's clock, in UTC.
- **Session stamps and `run_id`s** (`YYYY.MM.DD_HHMMSS`) are the laptop's local time, CEST (UTC+2).
- **Logcat lines and battery samples** are the phone's clock, in epoch seconds and milliseconds.

To match a log to a run, compare the log's timestamps with the run's battery samples, which are on
the same clock. Never compare them with the run table's UTC strings.

---

## 2. The raw data

Apart from the per-phone sweep logs (below), the raw data is not in this package. It is available
on request. The paths below are where the tools expect it, relative to the repository root.

`6_results_data/raw_manifest.csv.gz` lists every file: 134,522 files, 43.6 GB. Its columns are
`source, batch, path, bytes, sha256, used_by_results`. Any archive of the raw data can be checked
against it. Shared copies of the raw data must have the Wi-Fi network names, the access points'
hardware addresses and the device serials removed.

| what | where | name pattern | files |
|---|---|---|---|
| Android Runner output, one folder per run (raw, on request) | `android-runner/examples/batterymanager/output/<run_id>/` | see below | 4,794 runs, 115,180 files, 28.4 GB |
| system log (logcat) of each cell attempt (raw, on request) | `tools/sweep_logs/logcat/` | `logcat_<session>_<phone>_<app>_<variant>_r<rep>.log` | 4,914 belong to the four batches, 13.7 GB (4,796 used by the log tables) |
| stream logs (incl. thermal readings): per cell and per session raw, on request; per phone in this package | `tools/sweep_logs/` | per cell `parallel_<session>_<phone>_<app>_<variant>_r<rep>.log`; per phone `parallel_<session>_<phone>.log`; per session `parallel_<session>.log` | 14,388 sweep logs in all, 0.2 GB; the 38 per-phone logs are in this package |
| proxy logs, pdfviewer's cells only (raw, on request) | `tools/sweep_logs/` | `mitmproxy_<sweep tag>.log` | 4 |
| the 40 measured APKs (on request) | not in this package | run tables' `apk_path` (the builds' original location) | 40, 1.4 GB |
| the sweep-log index: every log file and its sweep tag | `specs/sweep_log_index.csv` | built by `tools/organise_sweep_logs.py index` (2026-10-05), which reads the wrapper logs and each session's own log | 13,372 files attributed |

**One Android Runner run folder** (`<run_id>/`):
- `config.json`, `experiment.log` (timestamped), `progress.xml`, `Aggregated_Results_*.csv`;
- `data/<phone folder>/<APK slug>/`, which holds:
  - `batterymanager/logcat_<ip>_<ts>.csv`: **the battery samples**, about 10 Hz. The source of every
    energy figure. Despite its name, it is not a system log;
  - `android/<ip>_<ts>.csv`: CPU (`dumpsys cpuinfo` TOTAL) and memory (PSS), about one sample per
    1.3 s;
  - `frametimes/`, `garbagecollection/`;
  - `dexopt_state.txt`, `appium_workload_coverage.jsonl` (scenario timings), `appium_status.json`,
    `crash_anr_status.json`, `device_state.json`, `apk_meta.json`, `aux/`.

**The logcat folder holds more than the batches.** It has 4,960 files. The other 46 come from test
and smoke runs (for example `SHORT_VALIDATION_20260731`, `smoke_ykush_020558` and the
`*_validate` sessions), and the index attributes each to its own tag.
- **Never glob the folder.** A glob once matched 2,282 files where 928 belonged to the batch.
- **To get a batch's files,** use `resolve_logs_for_tag(<sweep tag>)` from
  `tools/organise_sweep_logs.py`.

---

## 3. From one cell to its raw files

A cell is `(batch, phone, app, variant, rep)`. Apart from the log tables in
`6_results_data/inputs/`, the files named here are raw data (§2), available on request.

1. **Energy and the other profiles.** The run table gives the cell's `run_id`, which names its
   Android Runner folder (§2). The run table holds **one run per cell, the measured one**. A cell
   that failed and was re-run keeps only the run that counts.
2. **The system log.** `6_results_data/inputs/logcat_activity_<batch>.csv`, column `logfile`, names
   the capture of **that measured run**.
   - Why it is needed: a re-run cell has several captures on disk, one per attempt.
   - How it is chosen: the parser keeps the capture whose time span overlaps the run's battery
     samples (`tools/logcat_app_parser.py --run-table`).
   - Never pick a capture by filename alone.
3. **The stream log.** `tools/sweep_logs/parallel_<session>_<phone>_<app>_<variant>_r<rep>.log`,
   from the same session as the capture.

---

## 4. The derived inputs and what each is built from

The notebooks read only `6_results_data/run_tables/` and `6_results_data/inputs/`. Every input below
is checked by `tools/check_data_complete.py`.

| input | built by | from | covers |
|---|---|---|---|
| `run_tables/*.csv` | `tools/build_run_table_extended.py` | Android Runner output, sweep logs | 4 × 1,200 cells, one run each |
| `inputs/logcat_activity_<batch>.csv` | `tools/logcat_app_parser.py --sweep-tag … --run-table … --out …` (regenerated 2026-10-05) | the captures of the measured runs | pilot 1,199; series 1 1,200; series 2 1,199; series 3 1,198 cells (`logcat_activity.PROVENANCE.md`) |
| `inputs/launch_timing_series.csv` | `tools/extract_launch_timing.py` | log tables, captures, battery samples | every series cell with a log |
| `inputs/denial_timing_series.csv` | `tools/extract_denial_timing.py` | the same | every series cell with denials; totals equal the log tables' |
| `inputs/inwindow_launches_series.csv` | `tools/extract_inwindow_launches.py` | the same | relaunches inside the measured window (not used in the thesis) |
| `inputs/mechanism_*_series.csv`, `inputs/proxy_connections.csv` | `tools/extract_freerasp_mechanism.py` | battery, CPU and memory samples, captures, proxy logs | every series cell with a run (`freerasp_mechanism.PROVENANCE.md`) |
| `inputs/dexopt_status_series.csv` | `tools/extract_dexopt_status.py` | `dexopt_state.txt` | every series cell with a run |
| `inputs/post_charge_k_window.csv` | `scratchpad/analysis/topup_ground_truth.py` (written as `topup_ground_truth.csv`, published under this name); `scratchpad/analysis/cold_cell_rule.py` only fixes K per phone | run tables, sweep logs | all 4,800 cells |
| `inputs/variant_permissions.csv` | `tools/extract_variant_permissions.py` (2026-10-05) | the APKs the series cells ran (`aapt2 dump permissions`), hashes checked against the run tables | all 40 series builds (`variant_permissions.PROVENANCE.md`) |
| `inputs/clock_offset_series.csv` | `tools/extract_clock_offset.py` (2026-10-25) | Android Runner's `experiment.log` (profiler start, laptop clock, ms), battery samples (phone clock), run tables | every series cell with a run and samples (`clock_offset.PROVENANCE.md`) |
| static: `app_characteristics.csv`, `variant_parity.csv`, the build screens, `allatori_*` | the scripts named in their PROVENANCE files | the APKs | the builds |
| `raw_manifest.csv.gz` | `tools/raw_data_manifest.py` | everything in §2 | every raw file, with `used_by_results` |

**After regenerating any input:**
1. Run `python3 tools/check_data_complete.py`.
2. Rebuild the notebooks (see `README.md` at the top level).
3. Rebuild the manifest if a log table changed.

---

## 5. Do not use: stale or misleading data

| data | why |
|---|---|
| any glob over the raw logcat folder (`tools/sweep_logs/logcat/`) | mixes batches, re-run attempts and test runs |
| the pilot run table's `thermal_status_pre`, `thermal_skin_pre_c`, `thermal_cpu_pre_c` | **copies of series 1's readings** in all 1,200 rows (found 2026-10-26). This is the builder bug fixed on 2026-08-30 (`parse_thermal_pre_cell` in `tools/build_run_table_extended.py`); the published file, dated 2026-09-28, still carries the copies. Only the pilot's last 62 cells (session `20260810_001143`) had a thermal reading at all: take those from the stream logs. No Results number uses these columns |

---

## 6. Known gaps

All are checked by `tools/check_data_complete.py` and listed here, so that none can be explained
away later.

| gap | cells | why |
|---|---|---|
| pilot cell without a capture of the measured run | 1 | Pixel 3, linkhub, control, rep 3: its only capture stopped 18 s before measuring began. The pilot is in no Results number |
| series-2 cell without one | 1 | Pixel 3, calculator, Bangcle, rep 6: the capture stopped 18 s before measuring began |
| series-3 cells without one | 2 | Pixel 3, androidskk, Allatori, rep 5 (androidskk is excluded anyway); Pixel 3, poetskingdom, Allatori, rep 5: both captures stopped before measuring began |
| runs without battery samples | 2 | both invalid, so no result uses them: series 1, Pixel 9, pdfviewer, control, rep 1 (`HUB_ABORTED`); series 2, Pixel 9, calculator, Bangcle, rep 9 (`AR_INTERNAL`) |
| cells without any run | pilot 0, series 1 1, series 2 3, series 3 2 | the cell never produced a run |

**The result:** 2,734 of the 2,736 analysed series cells have a log of their measured run. Series 1
is complete.
