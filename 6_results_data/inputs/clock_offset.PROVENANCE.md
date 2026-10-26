# `clock_offset_series.csv`: provenance

**Written by** `tools/extract_clock_offset.py` (2026-10-25). It reads three sources:
- each series run's `experiment.log`, written by Android Runner on the laptop, with millisecond local
  timestamps (Europe/Amsterdam);
- the run's battery samples, on the phone's clock;
- the three series run tables.

Neither the logs nor the samples are in this package (raw data, available on request).

**Why it exists.** The run-table builder places the passive/active boundary and the end of the
active phase at the scenario time stamps. The workload script writes those on the laptop's clock,
truncated to the whole second. The builder compares them directly with battery samples on the
phone's clock, and nothing reconciles the two clocks. The Pixel 3's clock drifts against the
laptop's, by about 2 s over the three series (see What it shows).

## Columns
- **`theta_s`: the phone clock minus the laptop clock, in seconds.** It is the first battery sample
  minus the laptop's log time of the `am start-foreground-service ... DataCollectionService` command
  that starts the battery profiler (the "returned" line).
  - On the phone, the first sample follows the service start by about 20 ms.
  - On the laptop, the command takes 30–209 ms (median 63) after the "Start profiling" line.
  - **Checked independently.** linkhub's first scenario sends a share intent that the phone logs.
    That log time minus the laptop's stamp correlates with `theta_s` at r = 0.96, 0.94 and 0.93 on
    the Pixel 3, 6 and 9 (119, 119 and 120 cells).
  - The remainder is 0.06–1.1 s, about uniform: it is the stamp's whole-second truncation.
  - So `theta_s` is the clock offset to about 0.1 s.
- `ws_ms`, `we_ms`: the builder's boundaries, recovered exactly from the run table:
  `ws = t0 + duration_pre_workload_s`, `we = ws + duration_workload_s`.
- `pre_j_check`, `wl_j_check`: the passive and active energies re-integrated with the builder's own
  loader and trapezoid rule at the builder's boundaries. They reproduce the run table to under
  1 µJ in every analysed cell.
- `pre_j_corr`, `wl_j_corr`, `td_j_corr`: the same, with the boundaries moved onto the phone's
  clock (`ws + theta`, `we + theta`, clamped to the samples).
- `active_reaches_last_sample` (uncorrected) and `active_reaches_last_sample_corr`: whether the
  active phase runs to the cell's last battery sample.
- `e_active_after_true_end_j`, `samples_active_after_true_end`: what the uncorrected active phase
  holds after the last scenario's end as placed on the phone's clock.
- `nondischarging_samples_after_true_end`, `nondischarging_j_after_true_end`: of those samples, the
  ones whose battery status is not "discharging", and their energy (power × 0.1 s per sample).

## What it shows
- **Pixel 3:** the offset drifts steadily, −0.32 to −0.47 s a day, about +1.2 → +0.1 → −1.0 s
  over series 1–3.
- **Pixel 6 and 9:** the offset moves between about 0 and 3 s from cell to cell, with no trend
  across the batches.
