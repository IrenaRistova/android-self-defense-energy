# On the impact of self-defense techniques on the energy efficiency of Android apps

Replication package of the MSc thesis by Irena Ristova (Vrije Universiteit Amsterdam, 2026).

The study measures the energy cost of three Android self-defense techniques, each against the unprotected build of the
same application: packing (Bangcle), obfuscation (Allatori) and runtime shielding (freeRASP). Ten open-source
applications were measured on three generations of Google Pixel phones (Pixel 3, 6 and 9) in four batches of 1200
measurements: a pilot and three series batches. The thesis analyses the three series batches, on nine applications.

This repository holds everything needed to recompute every table, figure and number of the thesis from the measurement
data, and the code that took the measurements. The supplementary material cited in the thesis (Tables S1 to S16 and
Figures S1 to S3) is [`6_results_analysis/supplementary/supplementary.pdf`](6_results_analysis/supplementary/supplementary.pdf).

## Contents

| Folder | What it holds |
| --- | --- |
| `6_results_analysis/notebooks/` | The five notebooks that produce every table, figure and number, and their two libraries (`thesis_lib.py`, `per_device_lib.py`). `Per_Device_Analysis` makes most of the tables and figures of the thesis body. |
| `6_results_analysis/tables/`, `figures/` | Every table (`.tex` and `.csv`) and figure (`.pdf` and `.png`) as the notebooks write them. `MANIFEST.csv` lists each one with its notebook and its place: thesis body, appendix, supplement or repository only. The execution diagram (`figures/figure_execution.*`) is drawn by `scratchpad/analysis/execution_diagram/make_execution.py`. |
| `6_results_analysis/supplementary/` | `supplementary.pdf` (Tables S1 to S16, Figures S1 to S3), its LaTeX source, and `INDEX.md`, which maps every table and figure of the thesis to its file and notebook. |
| `6_results_data/run_tables/` | The four run tables, one row per measurement (4 x 1200 rows), with each measurement's validity verdict. `RUN_TABLE_COLUMNS_EXPLANATION.md` explains the columns. |
| `6_results_data/inputs/` | Per-measurement and per-build inputs extracted from the raw logs and APKs. Each `<name>.PROVENANCE.md` gives the command that made the file. |
| `6_results_data/derived/` | Results of an independent reference implementation (`scratchpad/analysis/thesis_numbers.py`), which the notebooks print next to their own numbers as a cross-check. |
| `6_results_data/DATA_MAP.md` | Where every file comes from: batches, tags, sessions, clocks, raw files and derived inputs. |
| `6_results_data/raw_manifest.csv.gz` | Size and SHA-256 of every raw file, so that the raw data (see below) can be checked. |
| `6_results_data/sweep_log_index.csv` | Maps every raw log file of the four batches to its batch. |
| `tools/` | The measurement harness (batch scheduler, thermal gate, power control, network replay, checks) and the data pipeline from raw logs to run tables and inputs. |
| `tools/sweep_logs/` | The 38 per-phone scheduler logs of the four batches, from which Table S4 (the thermal gate) is computed. |
| `appium_android_tests/` | The interaction scenarios of the ten measured applications and their shared library. |
| `android-runner/` | The Android Runner configurations, hook scripts and framework changes used in the experiment, with instructions ([`android-runner/README.md`](android-runner/README.md)). |
| `scratchpad/` | Scripts developed next to the analysis: the source of `Per_Device_Analysis` (`analysis/per_device_notebook.py`, converted by `analysis/py2nb.py`), the reference implementation, the producers of some inputs, the generator of the execution diagram, and the APK fingerprint modules the build checks import. |
| `docs/` | Three reference notes: batch naming, the scenario log formats, and the sign of the Pixel 3's current reading. |

## Recomputing the results

The notebooks need Python 3.12 and the packages in `6_results_analysis/requirements.txt`. The figures use the Linux
Libertine typeface (`fonts-linuxlibertine` on Debian and Ubuntu).

```bash
python3 -m venv .venv
.venv/bin/pip install -r 6_results_analysis/requirements.txt
PYTHON=.venv/bin/python JUPYTER=.venv/bin/jupyter bash rebuild.sh
```

`rebuild.sh` converts `per_device_notebook.py` into `Per_Device_Analysis.ipynb`, runs the five notebooks in order (about
two minutes) and rewrites `6_results_analysis/tables/`, `figures/`, `MANIFEST.csv` and `tables/README.md`. Every table
comes out byte-identical to the committed one, so `git status` afterwards shows only the notebooks (execution metadata)
and the figure PDFs (creation date) as changed. Matplotlib picks the first Linux Libertine cut its font cache lists, so
on another machine the figures can come out in the other cut of the typeface; their content is the same. To rebuild the supplement PDF as well:
`TECTONIC=/path/to/tectonic python3 6_results_analysis/make_supplement.py`.

`6_results_analysis/trace_numbers.py <file.tex>` traces every number in a LaTeX file to the table cells it can come
from, which is how the numbers in the thesis were checked.

## How the measurements were taken

The thesis describes the design (Section 4) and its execution (Section 5). This section points to the code.

- **Batches.** `tools/run_final_sweep_ykush.sh` is the entry point of a batch. It prepares the phones, runs the
  preflight checks (`tools/preflight_final_sweep_ykush.sh`), starts the mitmproxy replay and then
  `tools/sweep_runner_parallel_ykush.sh`, which gives each phone its own queue of measurements in a random order
  reproducible from a recorded seed and runs Android Runner once per measurement. `docs/BATCH_NAMING.md` explains the
  batch tags and the names the thesis uses.
- **Thermal gate.** `tools/thermal_gate.sh` holds a measurement while the phone reports moderate thermal throttling or
  worse (Android thermal status 2 or higher), with the screen off. Table S4 reports what it did.
- **Power.** The phones run on battery during a measurement. A Yepkit YKUSH 3 board cuts the USB supply line in
  hardware (`tools/ykush_power.sh`, and `tools/ykush_ar_power.sh` as Android Runner's USB handler).
  `tools/ykush_band_keeper.sh` keeps each phone's battery level within a band between measurements, and
  `tools/verify_parallel_charging.sh` checks that the external supply can charge all phones at once.
- **Energy and behaviour.** Android Runner's battery profiler samples Android's `BatteryManager` every 100 ms; other
  profilers sample processor and memory use and count garbage collections. The system log of each measurement is
  captured and parsed into the run table.
- **Network replay.** pdfviewer's network traffic is replayed from a recording by mitmproxy
  (`tools/mitmproxy_replay.sh`). `tools/record_via_scenario.sh` made the recording.
- **Builds.** `tools/pack_baseline_release.sh` builds the unprotected controls and `tools/pack_freerasp.sh` the freeRASP
  builds; `tools/patch_apk_nsc.sh` and `tools/inject_arm64_stub.sh` prepare the input to the Bangcle web service, to
  which packing is a manual upload. `tools/verify_baseline_not_obfuscated.py`, `tools/verify_allatori_obfuscation.py`
  and `tools/verify_variant_parity.py` check the builds. The Allatori build script is not included; it can be shared on
  request.
- **From raw files to data.** `tools/build_run_table_extended.py` builds the run tables, and the `tools/extract_*.py`
  scripts build the inputs. `tools/check_data_complete.py` compares every derived input with the raw files, so it needs
  the raw data. The run tables identify each measured APK by its SHA-256 checksum (column `apk_sha256`).

The harness was written for one laptop and three phones. Absolute paths (`/home/irena/Documents/Master Experiment`)
and placeholders for device-specific values (`<pixel3-serial>`, `<pixel3-ip>`, `<ykush-serial>` and so on) in
`tools/`, `scratchpad/` and `android-runner/` have to be set for a new set-up. The scripts that read APKs or raw logs
(the build checks, the input extractors and the producers in `scratchpad/analysis/`) need the raw data; the notebooks
do not.

## Data kept outside this repository

The raw Android system logs, the per-measurement logs, the raw energy samples, the 40 measured APKs and the network
recording replayed for pdfviewer are not in this repository because of their size. They can be shared on request: open an
issue in this repository.
`6_results_data/raw_manifest.csv.gz` lists every raw file with its size and SHA-256. In the 38 scheduler logs under
`tools/sweep_logs/`, local network addresses are replaced by documentation addresses (198.51.100.x and 203.0.113.x), so the checksums
of 36 of these 38 files differ from the manifest (the two logs of the pilot's false start held no address).

## Applications

The applications come from the AndroT dataset of Android applications with tests (Muradyan and Fazzini, MSR 2026).
Each one is built from a pinned commit of its open-source repository, and
`6_results_data/inputs/app_characteristics.csv` gives each application's package name and version.
`6_results_analysis/tables/candidate_tools.csv` lists the
self-defense tools that were considered and why each was or was not used, and `exclusions_by_cause.csv` gives the
excluded measurements by cause.

## Citation

Irena Ristova. *On the impact of self-defense techniques on the energy efficiency of Android apps.* MSc thesis,
Vrije Universiteit Amsterdam, 2026.
