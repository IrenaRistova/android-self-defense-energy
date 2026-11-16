# tools/: the measurement harness and the data pipeline

One line per file. Each description comes from the script's own header, which says more. The scripts were written for
one laptop and three phones; the top-level `README.md` says what has to be set for a new set-up.

## Batches

| File | What it does |
|---|---|
| `run_final_sweep_ykush.sh` | Entry point of a batch: prepares the phones, runs the preflight checks, starts the mitmproxy replay and then the scheduler. |
| `preflight_final_sweep_ykush.sh` | The checks before a batch starts: the board and its port map, that a port cut really stops the supply (read on the phone), the phones' connections, the mitmproxy certificate, that each configuration's APK is the build it claims, no leftover application, the battery levels, and that the builds are distinct and the Allatori builds obfuscated. |
| `sweep_runner_parallel_ykush.sh` | The batch scheduler: one queue of runs per phone, in a random order reproducible from a recorded seed, one Android Runner call per run. |
| `thermal_gate.sh` | Holds the next run, with the screen off, while the phone reports moderate thermal throttling or worse. |
| `pause_sweep.sh` | Stops a batch after its current runs; `--resume` continues it. |
| `invalidate_cells.py` | Marks named runs as failed, so that `--resume` measures exactly those again. |
| `revalidate_state.py` | Marks runs that passed but are not scientifically valid, so that `--resume` measures them again. |
| `setup_wifi_adb.sh` | One-time Wi-Fi ADB set-up of one phone. |
| `uninstall_cohort_ykush.sh` | Removes every cohort package from every phone. |

## Power

| File | What it does |
|---|---|
| `ykush_power.sh` | Switches the USB supply of one YKUSH 3 port on or off (through `ykushcmd`). |
| `ykush_ar_power.sh` | The command Android Runner calls as its USB handler to switch a phone's supply. |
| `ykush_band_keeper.sh` | Keeps each phone's battery level within a band between runs. |
| `ykush_calibrate.py` | Finds which board port feeds which phone, by observation; writes `ykush_portmap.json`. |
| `ykush_port_watch.sh` | Records a port's on or off state for the duration of one run. |
| `ykush_sweep_watchdog.sh` | Cuts every port if the batch dies or a phone charges above the ceiling. |
| `ykush_sync_ar_configs.py` | Wrote the `*_wifi_ykush.json` Android Runner configurations, whose USB handler drives the board. |
| `verify_parallel_charging.sh` | Checks that the external supply can charge every phone at once. |

## Network replay

| File | What it does |
|---|---|
| `mitmproxy_replay.sh` | Serves a recorded flow file to the phones, so that the application gets the same network responses in every run. |
| `record_via_scenario.sh` | Records a flow file while the application's Appium scenarios run. |
| `mitm_replay_count.sh` | Counts the replayed responses served to one phone. |

## Builds

| File | What it does |
|---|---|
| `pack_baseline_release.sh` | Builds the unprotected controls; release builds by default, `BASELINE_BUILD_TYPE=debug` for the analysed debug builds. |
| `pack_freerasp.sh` | Integrates freeRASP (Talsec Community 18.0.2) into an application's source and builds it; release builds by default, `FREERASP_BUILD_TYPE=debug` for the analysed debug builds. |
| `patch_apk_nsc.sh` | Patches an APK's network security configuration so that it trusts mitmproxy's user certificate, and signs it again. |
| `inject_arm64_stub.sh` | Adds an arm64-v8a placeholder library, so that the Bangcle packs install on the Pixel 9. |
| `stubs/libplaceholder.so` | The placeholder library `inject_arm64_stub.sh` adds (a stripped arm64-v8a ELF file). |
| `verify_baseline_not_obfuscated.py` | Checks that a control APK is not obfuscated. |
| `verify_allatori_obfuscation.py` | Checks that each Allatori APK is obfuscated. |
| `verify_variant_parity.py` | Checks that the four builds of each application differ only by the protection; writes `6_results_data/inputs/variant_parity.csv`. |
| `check_coverage_instrumentation.py` | Checks whether any measured APK carries JaCoCo coverage instrumentation. |

## From raw files to data

| File | What it does |
|---|---|
| `build_run_table_extended.py` | Builds the run tables from the scheduler state, Android Runner's output and the scenario modules. |
| `scenario_log_parser.py` | Parses the scenario results from the run logs (both formats in `docs/SCENARIO_LOG_FORMATS.md`) and classifies failures. |
| `logcat_app_parser.py` | Extracts the application's own lines from a run's system log capture. |
| `organise_sweep_logs.py` | Indexes the scheduler logs and system log captures by batch (`6_results_data/sweep_log_index.csv`). |
| `extract_androt_cohort.py` | Copies AndroT's record of each measured application's repository (`inputs/androt_cohort.csv`). |
| `extract_clock_offset.py` | The offset between each phone's clock and the laptop's per run, and the phase energies computed with it. |
| `extract_denial_timing.py` | When the SELinux denials of the application happen, relative to the phases. |
| `extract_dexopt_status.py` | The compilation state of the application before each run's launch. |
| `extract_freerasp_mechanism.py` | What the protected builds spend their energy on, per run (inputs of `Mechanism_Analysis`). |
| `extract_inwindow_launches.py` | Every launch of the application while energy was being measured, with its energy. |
| `extract_launch_timing.py` | Launch timing of each run, from its system log capture. |
| `extract_scenario_timing.py` | The duration of every scenario in every series run. |
| `extract_variant_permissions.py` | The permissions each measured build declares, read from the APK. |
| `raw_data_manifest.py` | Writes `6_results_data/raw_manifest.csv.gz`: size and SHA-256 of every raw file. |
| `check_data_complete.py` | Compares every derived input with the raw files, and fails loudly on a gap. |

## sweep_logs/

The 38 per-phone scheduler logs of the four batches (see the top-level `README.md`).
