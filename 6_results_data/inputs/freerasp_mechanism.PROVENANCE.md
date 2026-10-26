# `mechanism_*_series.csv` and `proxy_connections.csv`: provenance

**Written by** `tools/extract_freerasp_mechanism.py` (2026-10-05; per-window collections, the highest whole-phone power sample and the CPU figure's changes added 2026-10-26, every earlier column unchanged). It covers all four
builds. Raw sources, none of them in this package (available on request; `raw_manifest.csv.gz` lists them):
- the battery samples of each run;
- Android Runner's CPU and memory samples;
- the full logcat of each cell, the capture of the measured run named in `logcat_activity_<batch>.csv`;
- the four sweeps' mitmproxy logs (`tools/sweep_logs/mitmproxy_final_ykush_*.log`).

**Which cells.** Every cell of the three series batches that has a run in its run table and battery
samples: 3,589 cells, 3,586 of them with a log. The notebook restricts them to the analysed cells.

**Time and windows.** Time zero is the cell's first battery sample. The windows are the thesis's:
post-launch `[0, duration_pre_workload_s)`, and the workload the next `duration_workload_s` seconds.
Energies are integrated as in the run table: the notebook checks that they reproduce its window
energies, a median difference of 0.13% (post-launch) and 0.09% (workload).

## `mechanism_cells_series.csv`: one row per cell

**The cell**
- `batch`, `phone`, `app`, `variant`, `rep`: the cell.
- `post_launch_s`, `workload_s`: the window lengths.

**Energy and power**
- `e_0_4_j`, `e_4_15_j`, `e_15_end_j`: energy in three segments of the post-launch window, in seconds
  from the first sample. The boundaries were chosen after inspecting the per-second profile.
- `e_post_launch_j`, `e_workload_j`: energy in each window.
- `p_00` … `p_29`: mean power in each second of the post-launch window; blank past its end.
- `ar_cpu_post_launch`, `ar_cpu_workload`, `ar_mem_mb_*`, `ar_samples_per_s`: Android Runner's CPU
  (`dumpsys cpuinfo` TOTAL, a share of time over all cores) and memory (PSS), by window, and its
  sampling rate.
- `ar_cpu_changes`, `ar_cpu_changes_post_launch`, `ar_cpu_changes_workload`, `ar_cpu_carried_frac_workload`
  (2026-10-26): how often Android Runner's CPU figure changes. Android refreshes the `dumpsys cpuinfo` TOTAL line only now
  and then, so consecutive samples repeat it: the number of changes over the run and within each window, and the share of
  the active phase's samples that still carry the last value from before the phase (up to its first change).
- `battery_max_w_post_launch`, `battery_max_w_workload` (2026-10-26): the highest
  whole-phone power sample (|current_now| × voltage) in each window. A loose ceiling on what any part
  of the phone can draw, used where there is no rail log (the Pixel 3).

**Log traces** (only where there is a log)
- `has_log`: 1 when a capture of the measured run resolves the application's process.
- `<trace>_<window>`: lines in the 10 s before measurement, the post-launch window and the workload
  window, for these traces:
  - `pkg_list_reply`: system_server's "Overly large reply" warnings on package-list requests;
  - `appsfilter`: its package-visibility lines;
  - `biometric_query`: BiometricService queries naming a package;
  - `keystore2`;
  - `local_server_hit`: "URL not signed correctly", a request rejected by the YouTube or YouTube Music
    app's local web server;
  - `data_alert_poll`: `netstats_wifi_sample`, a poll of the traffic counters;
  - `drm`, `gl_context`: the Pixel 3's DRM and GL lines;
  - `app_denial`: SELinux denials from the application's processes;
  - `app_freerasp_tag`: freeRASP's tags in the application's processes.

**Garbage collections forced by the memory sampler**
- `gc_explicit_per_s`, `gc_explicit_ms_per_s`, `gc_heap_mb_median`: the application's explicit
  collections in the measured windows: their rate, their duration per second, and the heap left after
  them.
- `gc_explicit_10s_before`, `gc_explicit_10s_after`: their count in the 10 s on either side of
  measurement start.
- `gc_explicit_per_s_<window>`, `gc_explicit_ms_per_s_<window>`, for `post_launch` and `workload`
  (2026-10-26): the same rate and duration per second within each window. Weighted by the
  window lengths, they add up to the whole-window columns (checked in the per-device notebook).

**Rail power (Pixel 6 and 9)**
- `rail_<window>_<rail>`, for windows `before` (8 s), `post_launch`, `burst_4_15`, `after_burst` and
  `workload`, and rails `cpu_little`, `cpu_mid`, `cpu_big`, `gpu`, `cpu_total`.
  - The source is the thermal service's On-Device Power Monitor readings: one zone per device
    (Pixel 6 `VIRTUAL-SKIN-CPU-GPU`, Pixel 9 `VIRTUAL-SKIN-SOC`).
  - Each reading is the average since that zone's previous reading, a median of 7.0 s.
  - A window is reported only when the readings cover 80% of it.
- `rail_<window>_battery`: the battery power over the same window.
- `rail_max_<rail>`: the cell's highest single reading.
- `rail_reading_interval_s`: the median time between readings.
- The Pixel 3 has no such log.

**Wi-Fi traffic**
- `wifi_kb_per_s`: Wi-Fi traffic over the measured cell, from the first to the last poll inside it,
  when they are more than 30 s apart.

## The other three files

- **`mechanism_data_alerts_series.csv`:** one row per interval between two consecutive polls, from
  60 s before measurement to the end of the workload. Columns: the time of the later poll (`t_s`), the
  gap, the Wi-Fi traffic in between (MB), and the window of the later poll (`burst_4_15` overrides
  `post_launch`). The byte counters are read as: Pixel 6 and 9, values 1 and 2; Pixel 3, values 1
  and 3.
- **`mechanism_local_server_series.csv`:** one row per rejected local request (first line of each
  trace), with its time and window.
- **`proxy_connections.csv`:** per sweep log: connections, connections to any Talsec host, lines
  naming Talsec, and the commonest hosts. The proxy was set only for pdfviewer's cells, so these logs
  speak for pdfviewer only.

## What these files cannot show

- **Which process does the CPU work.** No per-process CPU was recorded. That the burst is freeRASP's
  rests on two things: its timing, and medtimer, the one build without INTERNET, which has neither
  the burst nor the traces that need a socket.
- **What freeRASP's code does.** It is closed source, so the probing is inferred from its side
  effects.
- **Loopback traffic itself.** The polls show only that traffic outside Wi-Fi and mobile set the data
  alert off.
