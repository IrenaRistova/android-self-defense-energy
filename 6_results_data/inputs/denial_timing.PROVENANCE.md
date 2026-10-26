# `denial_timing_series.csv`: provenance

**Written by** `tools/extract_denial_timing.py` (2026-10-05). It reads two raw sources, neither
of them in this package (available on request; `raw_manifest.csv.gz` lists them):
- **the full logcat of each cell** (`tools/sweep_logs/logcat/`);
- **the cell's battery samples,** whose first sample is time zero.

**Which lines.** The SELinux denials the mechanism tables count, attributed exactly as
`tools/logcat_app_parser.py` attributes them: lines tagged `auditd` written by the app's own
processes. Those are the processes `am_proc_start` reports under its package, its main process and
its own sub-processes (`logcat_activity.PROVENANCE.md`).

**Which cells.** Every series cell whose `auditd_lines` in `logcat_activity_<batch>.csv` is
above zero: 1,123 cells.
- **Most are freeRASP builds.** Every freeRASP cell with a capture has such lines.
- **The rest are documenter's other builds.** Its conversion worker (`:pdf2htmlEXWorker`) logs one
  denial per cell, a read of the root directory, in every build.
- **The mechanism notebook's timing table** is restricted to freeRASP cells.

## What each column is

- `batch`, `phone`, `app`, `variant`, `rep`: the cell.
- `auditd_lines`: the cell's total. It must equal the parser's count, and the script stops on
  any mismatch.
- `before`: lines before the first energy sample, i.e. the launch and the post-launch hook.
- `post_launch`: lines in the post-launch window, `[0, post_launch_s)`.
- `workload`: lines in the workload window, the next `workload_s` seconds.
- `after`: lines after the workload (teardown).
- `post_launch_s`, `workload_s`: the window lengths, from the run table
  (`duration_pre_workload_s`, `duration_workload_s`).

## What it cannot see

- **A probe that is not refused.** A check the security policy allows writes no denial, so
  the counts show refused probes, not all probing.
- **The work behind a denial,** and how much energy it costs.
