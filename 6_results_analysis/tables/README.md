# Tables: source notebooks

Every table here is written by a notebook under `6_results_analysis/notebooks/`. **BODY** tables appear in the thesis (`.tex` to `\input`, `.csv` for the data); **REPO** tables are cited by path; **SUPERVISOR** tables (`supervisor_only/`) are never cited. This list is generated from `../MANIFEST.csv`, which also covers the figures.

**The `.tex` tables were test-compiled on 2026-10-02** in ACM `sigconf` (Tectonic 0.15.0): all compile; Table 1 is a `table*` (both columns). The `.csv` beside each holds the same table.

## BODY

| File | Thesis section | What it holds | Notebook |
|---|---|---|---|
| `table_rq12.csv`, `table_rq12.tex` | 6.2-6.3 RQ1 and RQ2 (per device) | RQ1 and RQ2 merged: per technique x device, both phases: delta-E with CI, BH p, Cliff's delta magnitude (values in the CSV) | `Per_Device_Analysis.ipynb` |
| `table_rq3_correlations.csv`, `table_rq3_correlations.tex` | 6.4 RQ3 | RQ3 table: Spearman rho per device x build, both phases, one column (bold = BH < 0.05, no stars; BH over 96) | `Per_Device_Analysis.ipynb` |
| `table_rq4.csv`, `table_rq4.tex` | 6 Results (RQ4) | RQ4 body table: mean difference per technique x device in memory (active, %), threads (count), CPU (active, points), passive length (%); bold = BH < 0.05 within RQ4 | `Per_Device_Analysis.ipynb` |

## REPO

| File | Thesis section | What it holds | Notebook |
|---|---|---|---|
| `active_phase_to_last_sample.csv` | 5.2 Energy measurement (phase boundaries) | cells whose uncorrected active phase reaches the last battery sample, per device, batch and build: energy after the last scenario's end on the phone's clock, and non-discharging samples there | `Per_Device_Analysis.ipynb` |
| `added_permissions.csv` | 4.2 / 7.3 (what each protection adds to the manifest) | permissions each protected build declares that its control does not, with the applications (analysed applications) | `Mechanism_Analysis.ipynb` |
| `allatori_applicability_detail.csv` | 6.2 RQ1 (Allatori applicability) | the Allatori applicability table: classes renamed, run-time outcome and valid cells per application | `Headline_Effects.ipynb` |
| `allatori_composition.csv` | Threats (Allatori arm composition) | Allatori workload: the arm, without pdfviewer, and for reference with itsok's excluded release build | `Headline_Effects.ipynb` |
| `allatori_static_code_effects.csv` | 6.4 RQ3 (mechanism) | Allatori: classes renamed, decryptors, call sites, change in the app's own code | `Mechanism_Analysis.ipynb` |
| `appium_second_start_per_app.csv` | 5.2 Energy measurement (what the passive phase contains) | the application's second activity start per application: intent, phase, and what Android did with it (no new process in any case) | `Per_Device_Analysis.ipynb` |
| `arrival_temperature_adjusted_check.csv` | 8 Threats (measurement precision) | per device and phase: power against arrival temperature (within-group slope, r); the balance of each technique against its control with the implied and largest shift in delta-E; the 18 comparisons with energies adjusted for arrival temperature | `Per_Device_Analysis.ipynb` |
| `arrival_temperature_balance.csv` | Threats (measurement precision) | arrival temperature, each technique minus its control, per device and over all pairs, with the implied shift in delta-E | `Descriptive_Statistics.ipynb` |
| `arrival_temperature_per_device.csv` | 8 Threats (measurement precision) | per device and phase: power against arrival temperature (within-group slope, r); the balance of each technique against its control with the implied and largest shift in delta-E; the 18 comparisons with energies adjusted for arrival temperature | `Per_Device_Analysis.ipynb` |
| `auditd_discriminator.csv` | 6.4 RQ3 (mechanism) | analysed cells with SELinux denials per technique: from any of the application's processes, from its main process only, and outside documenter | `Mechanism_Analysis.ipynb` |
| `auditd_per_device.csv` | Supplement (runtime shielding per device) | analysed cells with SELinux denials per device and technique: from any of the application's processes, and from its main process | `Mechanism_Analysis.ipynb` |
| `candidate_tools.csv` | Appendix (candidate tools; replication package) | every candidate tool: family, licence or access, why it was not used, an example (licence terms stated generally) | `Per_Device_Analysis.ipynb` |
| `cell_counts_per_device.csv` | 5 Execution (cells planned, valid, analysed) | cells planned, valid and analysed in the three series batches, overall and per device x build | `Per_Device_Analysis.ipynb` |
| `cpu_figure_refresh.csv` | 8 Threats (construct validity: the CPU measure) | how often Android Runner's CPU figure (dumpsys cpuinfo TOTAL) changes: cells never changing, changes per run, cells whose active phase holds one value or carries the value from before it, active equal to passive; per device and build | `Per_Device_Analysis.ipynb` |
| `descriptives_app_technique_device.csv` | Appendix | descriptives per application x technique x device x window | `Descriptive_Statistics.ipynb` |
| `descriptives_per_device.csv` | 6.1 Descriptives (per device) | per device x build x phase: cells, mean, median, SD, CV of energy (J) and of phase length (s) | `Per_Device_Analysis.ipynb` |
| `descriptives_per_technique.csv` | 6.1 Descriptives | cell-level energy per technique and window: n, mean, sd, median, min, max, CV | `Descriptive_Statistics.ipynb` |
| `device_power_vs_arrival_temperature.csv` | Threats (measurement precision) | per device, within groups: power vs arrival temperature (r; % per degC, also within the same 3-h block); spread of power and duration; post-charge cells | `Descriptive_Statistics.ipynb` |
| `dexopt_status.csv` | 4 Planning / Methods (compilation before each launch) | the application's compilation state before each launch: the filter requested (speed) and the status Android reports, analysed cells per technique and all series cells by build type (debuggable builds are only verified) | `Descriptive_Statistics.ipynb` |
| `exclusions_by_cause.csv` | 5.7 Deviations and failures (exclusions) | excluded cells by cause in the three series batches, with totals and valid cells; the pilot's exclusions in the note | `Descriptive_Statistics.ipynb` |
| `exclusions_by_cause.tex` | 5.7 Deviations and failures (exclusions) | excluded cells by cause in the three series batches, with totals and valid cells; the pilot's exclusions in the note | `Descriptive_Statistics.ipynb` |
| `filter_chain.csv` | 5 Execution | cells surviving each step of the filter chain, per batch | `Descriptive_Statistics.ipynb` |
| `frame_timing.csv` | 6.4 RQ3 (frame timing; exploratory) | matched-pair change in median and 95th-percentile frame time and in delayed frames, per technique | `Mechanism_Analysis.ipynb` |
| `freerasp_active_cost_device_steps.csv` | 7 Discussion (device gradient) | the steps in freeRASP's active-phase cost between devices (extra J; extra device-wide CPU share), matched on application x batch over the eight applications measured on all three: mean, t-interval, Wilcoxon p | `Per_Device_Analysis.ipynb` |
| `freerasp_active_cost_per_device.csv` | 7 Discussion (device gradient) | freeRASP's active-phase cost per device in absolute terms: extra J per measurement and extra W with the control's energy, extra device-wide CPU share (pp), the percentage had the extra energy stayed at the Pixel 3's; all applications and the eight on all three | `Per_Device_Analysis.ipynb` |
| `freerasp_burst_per_device.csv` | Discussion (freeRASP's mechanism, exploratory) | freeRASP's extra passive-phase energy per device: in the device's own burst core, by segment, and whole; peak extra power and its second | `Per_Device_Analysis.ipynb` |
| `freerasp_burst_timing.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | when freeRASP's passive-phase burst starts and ends, per device: longest runs of seconds with extra power >= 1.0 W and >= 0.5 W, peak | `Mechanism_Analysis.ipynb` |
| `freerasp_by_device.csv` | Discussion (device gradient) | freeRASP per device: energy effect both windows (% and J; the passive phase without medtimer), control CPU load and added CPU load (active phase), added logging threads, Android version | `Mechanism_Analysis.ipynb` |
| `freerasp_cpu_rails.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | extra power on the battery and on each CPU cluster and the GPU (Pixel 6 and 9 rail log), per window and technique; Android Runner's CPU share beside it | `Mechanism_Analysis.ipynb` |
| `freerasp_data_alert_polls.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | Wi-Fi traffic between consecutive polls of the traffic counters, and the time between them, per device, build and window; extra polls per cell in the passive phase | `Mechanism_Analysis.ipynb` |
| `freerasp_data_alerts.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | Wi-Fi traffic between consecutive polls of the traffic counters, and the time between them, per device, build and window; extra polls per cell in the passive phase | `Mechanism_Analysis.ipynb` |
| `freerasp_fixed_cost_per_app.csv` | 6.3 RQ2 (phases) | freeRASP overhead per application in joules and percent, both windows | `Phase_Analysis.ipynb` |
| `freerasp_fixed_cost_summary.csv` | 6.3 RQ2 (phases) | joule band and CV in joules vs CV in percent, passive-phase and workload, with and without medtimer | `Phase_Analysis.ipynb` |
| `freerasp_local_probe_timing.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | requests rejected by other applications' local servers, per cell, per application, device and build; when they happen, per device | `Mechanism_Analysis.ipynb` |
| `freerasp_local_probes.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | requests rejected by other applications' local servers, per cell, per application, device and build; when they happen, per device | `Mechanism_Analysis.ipynb` |
| `freerasp_medtimer_check.csv` | 6.3 RQ2 / Threats (freeRASP network) | medtimer (no INTERNET): its overhead vs. what the other eight apps' trend predicts from its control size | `Phase_Analysis.ipynb` |
| `freerasp_medtimer_contrast.csv` | 6.3 / 6.4 (medtimer; where the passive-phase cost goes) | medtimer (freeRASP build without INTERNET) against the other eight on every measure of the passive-phase mechanism: pair means, intervals, the eight's range | `Mechanism_Analysis.ipynb` |
| `freerasp_postlaunch_profile.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | extra power in each second of the passive phase, per technique and device, with interval and pair count | `Mechanism_Analysis.ipynb` |
| `freerasp_postlaunch_segments.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | extra energy per cell by segment of the passive phase (0-4 s, 4-15 s, the rest) and in the active phase, per technique; freeRASP also per application and device; the passive segments without medtimer except in its own row | `Mechanism_Analysis.ipynb` |
| `freerasp_proxy_check.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | Wi-Fi traffic over the measured cell, freeRASP minus control, per device; the four sweeps' proxy logs: connections and any to Talsec (pdfviewer cells only) | `Mechanism_Analysis.ipynb` |
| `freerasp_system_traces.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | traces of freeRASP's checks per cell, freeRASP and control, before measurement and in the passive phase; most are written by other processes | `Mechanism_Analysis.ipynb` |
| `freerasp_threads_summary.csv` | Discussion (RQ3) | freeRASP added logging threads: spread and Spearman rho with app size, all nine and without poetskingdom | `Mechanism_Analysis.ipynb` |
| `freerasp_threads_vs_app.csv` | Discussion (RQ3) | freeRASP's added logging threads per application, beside app size, control threads and freeRASP's own log lines | `Mechanism_Analysis.ipynb` |
| `freerasp_wifi_rate.csv` | 6.4 RQ3 (where the passive-phase cost goes; exploratory) | Wi-Fi traffic over the measured cell, freeRASP minus control, per device; the four sweeps' proxy logs: connections and any to Talsec (pdfviewer cells only) | `Mechanism_Analysis.ipynb` |
| `fuel_gauge_resolution.csv` | Threats (the launch: the instrument's resolution) | per device: how often the fuel gauge's current reading changes, and how many battery samples and gauge readings one launch spans | `Phase_Analysis.ipynb` |
| `headline.csv` | 6.2 RQ1 (headline) | six headline contrasts, machine-readable: delta-E, paired t CI, permutation p, n, MDE | `Headline_Effects.ipynb` |
| `headline_numbers.json` | all of 4-6 | every headline number with interval, p-value (and method) and n; for mechanical checking of the thesis text | `Headline_Effects.ipynb` |
| `launch_energy_estimate.csv` | not used in the thesis (replication package only) | launch energy measured on poetskingdom's in-window relaunches, each technique against its control, with the window checks | `Phase_Analysis.ipynb` |
| `launch_time.csv` | 6.3 RQ2 (the launch) | cold-launch time to initial display (system log), each technique against its control, over all devices and per device: pairs, %, ms, 95% CI, MDE, pairs longer | `Phase_Analysis.ipynb` |
| `leave_one_app_out.csv` | 6.2 RQ1 / Threats (leave one application out) | range of delta-E when each application is left out in turn: the application behind each end, its 95% CI, and whether any drop changes the conclusion | `Headline_Effects.ipynb` |
| `linkhub_leave_out.csv` | Threats (linkhub control residue) | Bangcle and freeRASP with and without linkhub | `Headline_Effects.ipynb` |
| `log_coverage.csv` | 5 Execution / Methods (log coverage) | cells with a system-log capture of the measured run, per series batch, of all cells and of the analysed cells | `Mechanism_Analysis.ipynb` |
| `minimum_detectable_effect.csv` | 6.2 RQ1 / Threats (power) | MDE per arm and window, with pairs, SD and the observed effect (the MDE column of Table 4, with its inputs) | `Headline_Effects.ipynb` |
| `negative_controls.csv` | 4.3.2 Negative controls / Threats (noise floor) | the two declared null builds (obfuscator did not run) against their own controls, matched pairs, both windows | `Headline_Effects.ipynb` |
| `normality_per_device.csv` | Appendix (normality) | Shapiro-Wilk on the paired differences of the 18 comparisons, raw and log | `Per_Device_Analysis.ipynb` |
| `pairs_delta.csv` | 6.2 RQ1 (headline) | every matched pair's delta-E, with valid cells and within-group CV on each side: the points behind Figure 1 and Table 4 | `Headline_Effects.ipynb` |
| `pairs_per_device.csv` | 6 Results (every matched pair) | every matched pair per technique, phase and device: control and protected means, delta-E %, log ratio, within-pair Cliff's delta | `Per_Device_Analysis.ipynb` |
| `per_app.csv` | 6.2 RQ1 (per application) | active-phase delta-E per application and technique, nulls marked | `Headline_Effects.ipynb` |
| `per_batch.csv` | 6.2 RQ1 (per batch) | active-phase delta-E per batch and technique | `Headline_Effects.ipynb` |
| `per_device_checks.csv` | 6 Results (checks of the per-device results) | the 18 comparisons: primary (medtimer's packed build excluded; medtimer out of the passive phase), with medtimer in the passive phase, as measured, with the phase boundaries on the phone's clock, both, without the cold cells after charging, and without linkhub; full rows | `Per_Device_Analysis.ipynb` |
| `per_device_effects.csv` | 6.2 RQ1 (per device, exploratory) | delta-E per technique, window and device: mean over matched pairs with paired t-interval (exploratory, no p-values; superseded by per_device_tests.csv) | `Headline_Effects.ipynb` |
| `per_device_permutation_check.csv` | 6 Results (robustness: permutation test per device) | the 18 comparisons: Wilcoxon p against the stratified permutation p (6,000 permutations), each BH-adjusted within the RQ | `Per_Device_Analysis.ipynb` |
| `per_device_tests.csv` | 6.2-6.3 RQ1 and RQ2 (per device) | the 18 comparisons: n, delta-E with t-interval, median, V, W, exact p, BH within the RQ and across 18, within-pair Cliff's delta with bootstrap interval and magnitude, rank-biserial r, naive delta, Cohen's dz, MDE | `Per_Device_Analysis.ipynb` |
| `per_phone_freerasp.csv` | 6.2 RQ1 (per device) | freeRASP active-phase delta-E per phone, unfiltered (primary) and post-charge filtered | `Headline_Effects.ipynb` |
| `phase_boundaries_per_device.csv` | 5.2 Energy measurement (phase boundaries) | per device and series batch: passive and active lengths, the phone-minus-laptop clock offset, whole-second active lengths, the tail after the last scenario, cells whose active phase reaches the last sample, energy counted in no phase, linkhub's check | `Per_Device_Analysis.ipynb` |
| `phase_contrast.csv` | 6.3 RQ2 (phases) | passive vs active delta-E per technique | `Phase_Analysis.ipynb` |
| `phase_durations_and_power.csv` | 6.3 RQ2 (phases) | paired change in window duration and mean power, per technique | `Phase_Analysis.ipynb` |
| `post_charge_pooled_sensitivity.csv` | Threats (post-charge) | pooled headline with and without post-charge cells | `Headline_Effects.ipynb` |
| `profiler_gc_load.csv` | 8 Threats (instrument load) | collections forced by the memory sampler: rate, duration and heap per technique (pairs); an upper bound on the extra collections' power against freeRASP's workload difference | `Mechanism_Analysis.ipynb` |
| `repeatability_cv.csv` | 5 Execution | median within-group CV, pilot vs series, four measures | `Descriptive_Statistics.ipynb` |
| `robustness_app_clustered_bootstrap.csv` | 6.2 RQ1 (robustness) | ROBUSTNESS ONLY: application-clustered percentile bootstrap, Bangcle and freeRASP (not Allatori: five clusters); not the reported interval | `Headline_Effects.ipynb` |
| `rq3_correlations.csv` | 6.4 RQ3 | Spearman rho of per-cell energy with phase length, device-wide CPU, app PSS and log-derived threads, per build x device x phase, applications pooled; BH over the 96 | `Per_Device_Analysis.ipynb` |
| `rq3_difference_correlations.csv` | 6.4 RQ3 (difference-based check) | Spearman rho of the matched pairs' delta-E with their differences in phase length (%), CPU (points), memory (%), threads (%); BH over 72 | `Per_Device_Analysis.ipynb` |
| `rq4_runtime_differences.csv` | 6 Results (RQ4) | RQ4: protected build minus control on matched pairs, per technique x device: memory (PSS, both phases, %), threads that log (whole run, count), device-wide CPU (active phase, points), phase length (both phases, %); mean, 95% t-interval, MB/s/raw difference, pairs higher, Wilcoxon p, BH within RQ4 (54), Cliff's delta within pairs | `Per_Device_Analysis.ipynb` |
| `runtime_channels.csv` | 6.4 RQ3 (channels) | per technique: memory vs control (matched pairs), Bangcle-stub log lines and hidden-API lines per cell | `Mechanism_Analysis.ipynb` |
| `sampler_load_per_device.csv` | 8 Threats (instrument load) | the memory sampler's forced collections per technique, device and phase, minus the control: rate, time per second; the ceilings (one cluster, all clusters, the whole phone; the newer phones' figures standing in for the Pixel 3), the bound in points of delta-E, and the test re-run with each bound subtracted (BH p; the break-even ceiling) | `Per_Device_Analysis.ipynb` |
| `scenarios_per_app.csv` | 5 Execution (workload) | distinct scenarios per application, and scenario runs per cell (some scenarios repeat to fill 120 s) | `Descriptive_Statistics.ipynb` |
| `selinux_denial_timing.csv` | 6.4 RQ3 / Discussion (when the denials happen) | freeRASP's SELinux denials by time: before measurement, passive phase, active phase, after; share and rate per minute; the eight applications of the passive analysis, all nine, and medtimer | `Mechanism_Analysis.ipynb` |
| `shapiro_power.csv` | Appendix (normality: test reliability) | simulated Shapiro-Wilk rejection rates (%) at n = 15, 24, 27, 10,000 samples each | `Per_Device_Analysis.ipynb` |
| `single_pair_influence.csv` | 6.2 RQ1 / Threats | range of each headline when any one pair is dropped; the pair behind each end and the 95% CI without it | `Headline_Effects.ipynb` |
| `startup_window_composition.csv` | 5 Execution / 6.3 RQ2 (what the passive phase contains) | when the cold launch, Appium's session and the first scenario happen, relative to the start of energy measurement | `Phase_Analysis.ipynb` |
| `startup_window_resolution.csv` | 6.3 RQ2 / Threats (resolution) | the passive phase's mean and its minimum detectable effect in percent, joules and seconds, per technique | `Phase_Analysis.ipynb` |
| `table3_descriptives.csv` | 6.1 Descriptives (pooled; the thesis uses the per-device table) | energy per cell per technique and, for the control, per device: cells, mean, median, SD, CV, within-group CV | `Descriptive_Statistics.ipynb` |
| `table3_descriptives.tex` | 6.1 Descriptives (pooled; the thesis uses the per-device table) | energy per cell per technique and, for the control, per device: cells, mean, median, SD, CV, within-group CV | `Descriptive_Statistics.ipynb` |
| `table4_main_results.csv` | 6.2 RQ1 (headline) | main results: n apps, delta-E, paired t CI, p, MDE; common-subset block | `Headline_Effects.ipynb` |
| `table4_main_results.tex` | 6.2 RQ1 (headline) | main results: n apps, delta-E, paired t CI, p, MDE; common-subset block | `Headline_Effects.ipynb` |
| `table_packing_checks.csv` | 6 Results (checks of packing's Pixel 3 result) | packing per device and phase under the two checks, separately and together | `Per_Device_Analysis.ipynb` |
| `table_packing_checks.tex` | 6 Results (checks of packing's Pixel 3 result) | packing per device and phase under the two checks, separately and together | `Per_Device_Analysis.ipynb` |
| `table_rq12_medtimer_in_passive.csv` | 6 Results (replication package: medtimer in the passive phase) | the merged RQ table with medtimer in the passive phase | `Per_Device_Analysis.ipynb` |
| `table_rq12_medtimer_in_passive.tex` | 6 Results (replication package: medtimer in the passive phase) | the merged RQ table with medtimer in the passive phase | `Per_Device_Analysis.ipynb` |
| `table_rq12_with_medtimer.csv` | 6 Results (replication package: as measured) | the merged RQ table as measured: medtimer's packed build in, medtimer in the passive phase | `Per_Device_Analysis.ipynb` |
| `table_rq12_with_medtimer.tex` | 6 Results (replication package: as measured) | the merged RQ table as measured: medtimer's packed build in, medtimer in the passive phase | `Per_Device_Analysis.ipynb` |
| `table_rq1_active.csv` | 6 Results (RQ1, per device; replaced by the merged table) | RQ1 (active phase), one row per technique x device, with V and MDE | `Per_Device_Analysis.ipynb` |
| `table_rq1_active.tex` | 6 Results (RQ1, per device; replaced by the merged table) | RQ1 (active phase), one row per technique x device, with V and MDE | `Per_Device_Analysis.ipynb` |
| `table_rq2_passive.csv` | 6 Results (RQ2, per device; replaced by the merged table) | RQ2 (passive phase), one row per technique x device, with V and MDE | `Per_Device_Analysis.ipynb` |
| `table_rq2_passive.tex` | 6 Results (RQ2, per device; replaced by the merged table) | RQ2 (passive phase), one row per technique x device, with V and MDE | `Per_Device_Analysis.ipynb` |
| `test_rule.csv` | 4.4 Analysis (test rule) | the one test rule for the 18 comparisons and the counts it was decided from | `Per_Device_Analysis.ipynb` |
| `thread_deltas_per_app.csv` | 6.4 RQ3 (mechanism) | change in thread count vs control, per application and technique | `Mechanism_Analysis.ipynb` |
| `timeline_per_device.csv` | 5.2 Energy measurement (timeline) | the timeline's durations per device and pooled: launch, process start and first display before t = 0, Appium's server and session start (six applications), passive and active lengths, the active range by application | `Per_Device_Analysis.ipynb` |
| `variant_parity.csv` | 4 Planning (variants; one sentence in the body) | per application: same version, APK hash = run tables, protection markers, disclosed differences (NSC, INTERNET, debug build) | `Descriptive_Statistics.ipynb` |
| `variant_parity_full.csv` | 4 Planning (variants) | one row per build (36): version, SDKs, NSC, INTERNET, marker, hash | `Descriptive_Statistics.ipynb` |
| `workload_duration.csv` | 4 Planning / 5 Execution (active-phase length) | workload-window duration over the analysed series cells, overall and per application; Pixel 3 control medians against the 100-130 s band | `Descriptive_Statistics.ipynb` |

## SUPERVISOR

| File | Thesis section | What it holds | Notebook |
|---|---|---|---|
| `supervisor_only/filter_chain_ten_apps.csv` | none | filter chain with androidskk kept (ten applications); not cited in the thesis | `Descriptive_Statistics.ipynb` |
| `supervisor_only/scenarios_ten_apps.csv` | none | scenario counts with androidskk; not cited in the thesis | `Descriptive_Statistics.ipynb` |
| `supervisor_only/sensitivity_ten_vs_nine_apps.csv` | none | headline delta-E with and without androidskk; not cited in the thesis | `Headline_Effects.ipynb` |

To regenerate: run the notebooks from `6_results_analysis/notebooks/` (see the top-level `README.md`). Inputs: `6_results_data/run_tables/` and `6_results_data/inputs/`.
