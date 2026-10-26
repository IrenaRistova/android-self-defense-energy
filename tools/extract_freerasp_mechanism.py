#!/usr/bin/env python3
"""What the protected builds spend their energy on, per cell (series batches): the inputs of the
mechanism notebook's "where the post-launch cost goes" section (2026-10-05).

For every cell of the three series batches that has a run in its run table and battery samples, this
reads the battery samples (time zero is the first one), Android Runner's CPU and memory samples, and,
where a capture of the measured run exists (6_results_data/inputs/logcat_activity_<batch>.csv), the
full logcat. The windows are the thesis's: post-launch [0, duration_pre_workload_s), workload the next
duration_workload_s seconds (as in tools/extract_denial_timing.py). Power is |current_now| x voltage,
integrated as in tools/extract_inwindow_launches.py (trapezoid, edges interpolated). Since 2026-10-26 it
also writes the explicit collections per window and the highest whole-phone power sample per window; every
earlier column is unchanged.

Read from the logcat, of every process:
  - rail power. The Pixel 6 and 9 thermal service logs the power of each CPU cluster and of the GPU
    ("pixel-thermal: <zone> binded power rails: (S4M_VDD_CPUCL0: 306.99 mW)..."), from the On-Device
    Power Monitor. One zone per phone is used (Pixel 6 VIRTUAL-SKIN-CPU-GPU, Pixel 9 VIRTUAL-SKIN-SOC);
    each reading is taken as the average since the previous reading of that zone. The Pixel 3 has no
    such log;
  - traces of the protection's checks in other processes: system_server's oversized package-list
    replies (ParceledListSlice "Overly large reply"), its package-visibility lines (AppsFilter),
    BiometricService queries naming a package, keystore2, and the Pixel 3's DRM and GL lines;
  - "URL not signed correctly" (SignatureInterceptor, first line of each trace): a local web server of
    the YouTube or YouTube Music app rejecting a request;
  - netstats_wifi_sample: logged each time Android polls its traffic statistics, which in the control
    cells happens at its data-usage alert (about every 2 MB of traffic); each event carries the Wi-Fi
    byte counters;
  - the application's own lines (its package's processes, tools/logcat_app_parser.resolve_app_pids):
    SELinux denials (auditd), freeRASP's tags, and ART's garbage-collection lines.

Usage (from the repository root):  python3 tools/extract_freerasp_mechanism.py
Outputs (provenance: 6_results_data/inputs/freerasp_mechanism.PROVENANCE.md):
  6_results_data/inputs/mechanism_cells_series.csv         one row per cell
  6_results_data/inputs/mechanism_data_alerts_series.csv   one row per interval between two polls
  6_results_data/inputs/mechanism_local_server_series.csv  one row per rejected local request
  6_results_data/inputs/proxy_connections.csv              one row per sweep's mitmproxy log
"""
import bisect
import collections
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from extract_inwindow_launches import battery, energy  # noqa: E402
from logcat_app_parser import LINE_RE, app_package, resolve_app_pids  # noqa: E402

INPUTS = ROOT / "6_results_data" / "inputs"
LOGCAT = ROOT / "tools" / "sweep_logs" / "logcat"
AR_OUT = ROOT / "android-runner" / "examples" / "batterymanager" / "output"
RUN_TABLES = {
    "series1": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": ROOT / "6_results_data" / "run_tables" / "run_table_extended_final_ykush_series3_2026-08-17.csv",
}
PROXY_LOGS = {
    "pilot": "mitmproxy_final_ykush_2026-08-04.log",
    "series1": "mitmproxy_final_ykush_batch2_2026-08-10.log",
    "series2": "mitmproxy_final_ykush_series2_2026-08-14.log",
    "series3": "mitmproxy_final_ykush_series3_2026-08-17.log",
}
PROFILE_S = 30
ZONE = {"pixel6": "VIRTUAL-SKIN-CPU-GPU", "pixel9": "VIRTUAL-SKIN-SOC"}
RAIL_NAME = {"CPUCL0": "cpu_little", "CPUCL1": "cpu_mid", "CPUCL2": "cpu_big", "G3D": "gpu"}
RAILS = ("cpu_little", "cpu_mid", "cpu_big", "gpu")
RAIL_LINE = re.compile(r"^\s*(\d+\.\d+)\s+\d+\s+\d+\s+\w\s+pixel-thermal\s*:\s*(\S+) binded power rails: (.*)$")
RAIL = re.compile(r"\((\w+): ([\d.]+) mW\)")
GC = re.compile(r"^(\w+) .*GC freed .*?(\d+(?:\.\d+)?)(B|KB|MB|GB)/(\d+(?:\.\d+)?)(B|KB|MB|GB), paused .*"
                r"total ([\d.]+)(ms|us|s)\s*$")
MB = {"B": 1 / 2**20, "KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}
TRACES = {  # name: test on (tag, message, written by the application's own processes)
    "pkg_list_reply": lambda t, m, a: t == "ParceledListSlice" and m.startswith("Overly large reply"),
    "appsfilter": lambda t, m, a: t == "AppsFilter",
    "biometric_query": lambda t, m, a: t.startswith("BiometricService/PreAuth") and "Package:" in m,
    "keystore2": lambda t, m, a: t == "keystore2",
    "local_server_hit": lambda t, m, a: t == "SignatureInterceptor" and m.startswith("URL not signed"),
    "data_alert_poll": lambda t, m, a: t == "netstats_wifi_sample",
    "drm": lambda t, m, a: t in ("DrmLibTime", "QC-time-services"),
    "gl_context": lambda t, m, a: t.startswith("AdrenoGLES") or t == "libEGL",
    "app_denial": lambda t, m, a: a and t == "auditd",
    "app_freerasp_tag": lambda t, m, a: a and (t in ("FreeRasp", "DefaultDispatch", "libc") or t.startswith("pool-")),
}
SEGMENTS = {"0_4": (0, 4), "4_15": (4, 15), "15_end": (15, None)}
RAIL_WINDOWS = ("before", "post_launch", "burst_4_15", "after_burst", "workload")


def window_of(dt, pl, wl):
    if -10 <= dt < 0:
        return "before"
    if 0 <= dt < pl:
        return "post_launch"
    if pl <= dt < pl + wl:
        return "workload"
    return None


def android_runner_samples(run_id, t0, pl, wl):
    """Android Runner's CPU (dumpsys cpuinfo TOTAL, % of all cores) and memory (PSS) by window."""
    files = [p for p in (AR_OUT / run_id / "data").glob("*/*/android/*.csv") if "Aggregated" not in p.name]
    out = {}
    if not files:
        return out
    rows = []
    with open(files[0]) as fh:
        for r in csv.DictReader(fh):
            try:
                t = datetime.strptime(r["datetime"].strip(), "%a %b %d %H:%M:%S %Z %Y").replace(
                    tzinfo=timezone.utc).timestamp() - t0
                rows.append((t, float(r["cpu"]), float(r["mem"])))
            except (KeyError, ValueError, AttributeError):
                continue
    if pl + wl > 0:
        out["ar_samples_per_s"] = sum(1 for t, _, _ in rows if 0 <= t < pl + wl) / (pl + wl)
    for w, (lo, hi) in {"post_launch": (0, pl), "workload": (pl, pl + wl)}.items():
        sel = [(c, m) for t, c, m in rows if lo <= t < hi]
        if sel:
            out[f"ar_cpu_{w}"] = sum(c for c, _ in sel) / len(sel)
            out[f"ar_mem_mb_{w}"] = sum(m for _, m in sel) / len(sel) / 1024
    # how often the CPU figure changes: Android refreshes its cpuinfo TOTAL line only now and then, so a
    # window's samples can all carry a value set before the window began
    if rows:
        cpu = [c for _, c, _ in rows]
        out["ar_cpu_changes"] = sum(1 for a, b in zip(cpu, cpu[1:]) if a != b)
        for w, (lo, hi) in {"post_launch": (0, pl), "workload": (pl, pl + wl)}.items():
            inside = [c for t, c, _ in rows if lo <= t < hi]
            if inside:
                out[f"ar_cpu_changes_{w}"] = sum(1 for a, b in zip(inside, inside[1:]) if a != b)
        before = [c for t, c, _ in rows if t < pl]
        inside = [c for t, c, _ in rows if pl <= t < pl + wl]
        if before and inside:
            k = next((i for i, c in enumerate(inside) if c != before[-1]), len(inside))
            out["ar_cpu_carried_frac_workload"] = k / len(inside)
    return out


def rail_windows(readings, wins):
    """Average rail power per window; a reading at tb covers (previous reading, tb]."""
    out = {}
    for w, (lo, hi) in wins.items():
        acc, cov = collections.Counter(), 0.0
        for (ta, _), (tb, v) in zip(readings, readings[1:]):
            ov = max(0.0, min(tb, hi) - max(ta, lo))
            if ov > 0:
                cov += ov
                for r, x in v.items():
                    acc[r] += x * ov
        if hi > lo and cov >= 0.8 * (hi - lo):
            for r in RAILS:
                out[f"rail_{w}_{r}"] = acc[r] / cov
            out[f"rail_{w}_cpu_total"] = sum(acc[r] for r in RAILS if r.startswith("cpu")) / cov
    return out


def one_cell(key, cell, logfile, alerts, hits):
    batch, phone, app, variant, rep = key
    s = battery(cell["run_id"]) if cell.get("run_id") else []
    if len(s) < 50:
        return None
    try:
        pl, wl = float(cell["duration_pre_workload_s"]), float(cell["duration_workload_s"])
    except (TypeError, ValueError):
        return None
    ts = [x[0] for x in s]
    t0 = ts[0]
    row = dict(batch=batch, phone=phone, app=app, variant=variant, rep=rep,
               post_launch_s=round(pl, 3), workload_s=round(wl, 3))
    # energy by segment, and over each window
    for name, (lo, hi) in SEGMENTS.items():
        hi = pl if hi is None else min(hi, pl)
        lo = min(lo, pl)
        row[f"e_{name}_j"] = energy(s, ts, t0 + lo, t0 + hi) if hi > lo else 0.0
    row["e_post_launch_j"] = energy(s, ts, t0, t0 + pl)
    row["e_workload_j"] = energy(s, ts, t0 + pl, t0 + pl + wl)
    # mean power in each second of the post-launch window
    for k in range(PROFILE_S):
        lo_i = bisect.bisect_left(ts, t0 + k)
        hi_i = bisect.bisect_left(ts, t0 + min(k + 1, pl))
        vals = [s[j][1] for j in range(lo_i, hi_i)] if k < pl else []
        row[f"p_{k:02d}"] = sum(vals) / len(vals) if vals else ""
    row.update(android_runner_samples(cell["run_id"], t0, pl, wl))
    # the highest whole-phone power sample in each window: a loose ceiling where there is no rail log
    for w, (lo, hi) in {"post_launch": (0, pl), "workload": (pl, pl + wl)}.items():
        lo_i, hi_i = bisect.bisect_left(ts, t0 + lo), bisect.bisect_left(ts, t0 + hi)
        if hi_i > lo_i:
            row[f"battery_max_w_{w}"] = max(s[j][1] for j in range(lo_i, hi_i))
    row["has_log"] = 0
    if not logfile or not (LOGCAT / logfile).exists():
        return row
    lines = (LOGCAT / logfile).read_text(errors="replace").splitlines()
    own, _ = resolve_app_pids(lines, app_package(app))
    if not own:
        return row
    row["has_log"] = 1
    cnt = collections.Counter()
    rails, polls = [], []
    gc_n = gc_ms = 0.0
    gc_win = collections.Counter()  # (window, "n" or "ms"): the explicit collections of each window
    gc_before = gc_after = 0
    heap = []
    rail_max = collections.defaultdict(float)
    for ln in lines:
        m = LINE_RE.match(ln)
        if not m:
            continue
        dt = float(m.group("ts")) - t0
        tag, msg = m.group("tag").strip(), m.group("msg")
        is_app = m.group("pid") in own
        if tag == "pixel-thermal" and phone in ZONE and "binded power rails" in msg:
            r = RAIL_LINE.match(ln)
            if r and r.group(2) == ZONE[phone]:
                v = {RAIL_NAME[a.split("_VDD_")[-1]]: float(b) / 1000
                     for a, b in RAIL.findall(r.group(3)) if a.split("_VDD_")[-1] in RAIL_NAME}
                rails.append((dt, v))
                for name, x in v.items():
                    rail_max[name] = max(rail_max[name], x)
        elif tag == "netstats_wifi_sample":
            v = [int(x) for x in msg.strip("[] ").split(",") if x.strip().lstrip("-").isdigit()]
            if len(v) in (9, 13):  # Pixel 6/9: rx, tx, ...; Pixel 3: rx, rx packets, tx, ...
                polls.append((dt, v[0] + (v[2] if len(v) == 13 else v[1])))
        elif is_app and "GC freed" in msg:
            g = GC.match(msg)
            if g and g.group(1) == "Explicit":
                gc_before += -10 <= dt < 0
                gc_after += 0 <= dt < 10
                if 0 <= dt < pl + wl:
                    ms = float(g.group(6)) * {"ms": 1, "us": 1e-3, "s": 1e3}[g.group(7)]
                    gc_n += 1
                    gc_ms += ms
                    heap.append(float(g.group(2)) * MB[g.group(3)])
                    gw = "post_launch" if dt < pl else "workload"
                    gc_win[gw, "n"] += 1
                    gc_win[gw, "ms"] += ms
        w = window_of(dt, pl, wl)
        if w:
            for name, test in TRACES.items():
                if test(tag, msg, is_app):
                    cnt[f"{name}_{w}"] += 1
                    if name == "local_server_hit":
                        hits.append(dict(batch=batch, phone=phone, app=app, variant=variant, rep=rep,
                                         t_s=round(dt, 3), window=w))
    for name in TRACES:
        for w in ("before", "post_launch", "workload"):
            row[f"{name}_{w}"] = cnt.get(f"{name}_{w}", 0)
    if pl + wl > 0:
        row.update(gc_explicit_per_s=gc_n / (pl + wl), gc_explicit_ms_per_s=gc_ms / (pl + wl),
                   gc_explicit_10s_before=gc_before, gc_explicit_10s_after=gc_after)
    for gw, length in (("post_launch", pl), ("workload", wl)):  # the same, per window
        if length > 0:
            row[f"gc_explicit_per_s_{gw}"] = gc_win[gw, "n"] / length
            row[f"gc_explicit_ms_per_s_{gw}"] = gc_win[gw, "ms"] / length
    if heap:
        row["gc_heap_mb_median"] = sorted(heap)[len(heap) // 2]
    if len(rails) >= 3:
        wins = {"before": (-8, 0), "post_launch": (0, pl), "burst_4_15": (4, 15), "after_burst": (15, pl),
                "workload": (pl, pl + wl)}
        row.update(rail_windows(rails, wins))
        gaps = sorted(b[0] - a[0] for a, b in zip(rails, rails[1:]))
        row["rail_reading_interval_s"] = gaps[len(gaps) // 2]
        for name, x in rail_max.items():
            row[f"rail_max_{name}"] = x
        for w, (lo, hi) in wins.items():
            e = energy(s, ts, t0 + lo, t0 + hi) if hi > lo else None
            if e is not None:
                row[f"rail_{w}_battery"] = e / (hi - lo)
    polls = [p for p in polls if -60 <= p[0] < pl + wl]
    for (ta, a), (tb, b) in zip(polls, polls[1:]):
        alerts.append(dict(batch=batch, phone=phone, app=app, variant=variant, rep=rep, t_s=round(tb, 3),
                           gap_s=round(tb - ta, 3), wifi_mb=round((b - a) / 1e6, 4),
                           window="burst_4_15" if 4 <= tb < 15 else window_of(tb, pl, wl) or "earlier"))
    inside = [p for p in polls if 0 <= p[0] < pl + wl]
    if len(inside) >= 2 and inside[-1][0] - inside[0][0] > 30:
        row["wifi_kb_per_s"] = (inside[-1][1] - inside[0][1]) / 1e3 / (inside[-1][0] - inside[0][0])
    return row


def proxy_rows():
    rows = []
    for sweep, name in PROXY_LOGS.items():
        txt = (ROOT / "tools" / "sweep_logs" / name).read_text(errors="replace")
        hosts = collections.Counter(re.findall(r"server connect ([^\s:]+):\d+", txt))
        rows.append(dict(sweep=sweep, log=name, client_connections=txt.count("client connect"),
                         connections_to_talsec=sum(n for h, n in hosts.items() if "talsec" in h.lower()),
                         lines_naming_talsec=len(re.findall("talsec", txt, re.I)), distinct_hosts=len(hosts),
                         top_hosts="; ".join(f"{h} {n}" for h, n in hosts.most_common(8))))
    return rows


def write(path, rows):
    cols = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: (f"{v:.6g}" if isinstance(v, float) else v) for c, v in r.items()})


def main():
    rows, alerts, hits = [], [], []
    for batch, rt in RUN_TABLES.items():
        cells = {(r["phone"], r["app"], r["variant"], r["rep"]): r for r in csv.DictReader(open(rt))}
        logs = {(r["phone"], r["app"], r["variant"], r["rep"]): r["logfile"]
                for r in csv.DictReader(open(INPUTS / f"logcat_activity_{batch}.csv")) if r.get("resolved") == "1"}
        for k, cell in sorted(cells.items()):
            r = one_cell((batch, *k), cell, logs.get(k), alerts, hits)
            if r:
                rows.append(r)
        print(f"{batch}: {sum(1 for r in rows if r['batch'] == batch)} cells", flush=True)
    write(INPUTS / "mechanism_cells_series.csv", rows)
    write(INPUTS / "mechanism_data_alerts_series.csv", alerts)
    write(INPUTS / "mechanism_local_server_series.csv", hits)
    write(INPUTS / "proxy_connections.csv", proxy_rows())
    print(f"wrote mechanism_cells_series.csv ({len(rows)} cells, {sum(r['has_log'] for r in rows)} with a log), "
          f"mechanism_data_alerts_series.csv ({len(alerts)}), mechanism_local_server_series.csv ({len(hits)}), "
          f"proxy_connections.csv")


if __name__ == "__main__":
    main()
