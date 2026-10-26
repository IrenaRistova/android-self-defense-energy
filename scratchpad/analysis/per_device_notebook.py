# %% [markdown]
# # Per-device analysis: RQ1 to RQ4
#
# Results are reported **per device**, never pooled. The statistics follow the Green Lab course: Shapiro–Wilk and Q-Q
# plots on the paired differences, a log transformation attempt, one test rule for all comparisons fixed before the
# tests are read (it gives the Wilcoxon signed-rank test throughout), Benjamini–Hochberg within each research question,
# and the within-pair Cliff's delta as the effect size. CAIN 2025 (Nguyen et al.) is the model for the tables and figures.
#
# **Phases.** The **passive** phase runs from the first energy sample (t = 0) to the first scenario (RQ2); the
# **active** phase is the scenarios (RQ1). The **launch**, before t = 0, is timed, not measured in energy
# (Discussion only). No total of the two phases is reported.
#
# **Unit.** The matched pair: one application on one device in one series batch, the mean of the protected build's
# cells against the mean of its control's cells. **Comparisons:** 3 techniques × 2 phases × 3 devices = 18.
#
# **Checks of packing's Pixel 3 result**: with medtimer's packed build, which carries JaCoCo coverage probes its control
# lacks and which the primary analysis leaves out; with the phase boundaries moved onto the phone's clock
# (`inputs/clock_offset_series.csv`); and both together. Also the cold cells after charging, and the permutation test.
# The pooled estimates and the other notebooks' robustness checks stay in the replication package.
#
# Runs last: it reads tables the other four notebooks emit.

# %%
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy import stats
import thesis_lib as T
import per_device_lib as P

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)
for d in (T.TABLES, T.FIGURES):
    d.mkdir(exist_ok=True)
T.figure_style()
NB = "notebooks/Per_Device_Analysis.ipynb"
raw = T.load_raw()
df, chain = T.apply_filters(raw)
series = df[df["batch"].isin(T.SERIES)].copy()          # the pilot enters no Results number
key = ["batch", "phone", "app", "variant", "rep"]
ss = np.random.SeedSequence([T.SEED, 24])               # this notebook's own streams; the other notebooks' draws are unchanged
rng_boot, rng_perm, rng_sim = (np.random.default_rng(s) for s in ss.spawn(3))
DEVS = list(P.DEVICES)                                  # Pixel 3, 6, 9: the order everywhere
TECHS = list(P.TECHS)                                   # Bangcle, Allatori, freeRASP: the order everywhere
_TECH_WORD = {"Bangcle": "packing", "Allatori": "obfuscation", "freeRASP": "runtime shielding"}
ARM_OF = {T.name(a): a for a in T.ARMS}
DEV = T.PHONE_DISPLAY
FAMILY = {"Bangcle": "packing", "Allatori": "obfuscation", "freeRASP": "runtime shielding"}
print(f"{len(series)} analysed series cells")

# %% [markdown]
# ## Cells planned, valid and analysed
#
# Counts only; the exclusions by cause are in the replication package (`exclusions_by_cause.csv`). Planned: the
# design, 10 repetitions of every build of every application on every device in each of the three series batches.
# Valid: cells that passed every validity check. Analysed: valid cells of the nine-application cohort, without the
# builds outside an arm (itsok's release build; medtimer's packed build; keep_recipe's and linkhub's untransformed
# obfuscated builds).

# %%
_ct_raw = raw[raw["batch"].isin(T.SERIES)]
_ct_valid = _ct_raw[_ct_raw["scientifically_valid"].astype(str).str.lower().isin(["true", "1"])]
rows = []
for dev in DEVS + ["all"]:
    for v in ["baseline_debug"] + list(T.ARMS) + ["all"]:
        sel = lambda d: d[((d["phone"] == dev) if dev != "all" else pd.Series(True, index=d.index))
                          & ((d["variant"] == v) if v != "all" else pd.Series(True, index=d.index))]
        n_dev, n_var = (1 if dev != "all" else 3), (1 if v != "all" else 4)
        rows.append({"device": DEV.get(dev, "all devices"), "build": T.name(v) if v != "all" else "all builds",
                     "planned": 3 * 10 * 10 * n_dev * n_var, "valid": len(sel(_ct_valid)), "analysed": len(sel(series)),
                     "analysed_active": len(sel(series)), "analysed_passive": len(sel(series[~series["app"].isin(T.PASSIVE_EXCLUDED_APPS)]))})
counts = pd.DataFrame(rows)
counts.to_csv(T.TABLES / "cell_counts_per_device.csv", index=False)
T.register("tables/cell_counts_per_device.csv", "REPO", "5 Execution (cells planned, valid, analysed)",
           "cells planned, valid and analysed in the three series batches, overall and per device x build", NB)
_ct_all = counts[(counts["device"] == "all devices") & (counts["build"] == "all builds")].iloc[0]
assert _ct_all["planned"] == 3600 and _ct_all["analysed"] == len(series), _ct_all
print(counts.pivot(index="build", columns="device", values=["planned", "valid", "analysed"]).to_string())
print(chain.to_string(index=False) if hasattr(chain, "to_string") else chain)
T.record("cell_counts", planned=int(_ct_all["planned"]), valid=int(_ct_all["valid"]), analysed=int(_ct_all["analysed"]),
         per_device_analysed=[int(counts[(counts["device"] == DEV[d]) & (counts["build"] == "all builds")]["analysed"].iloc[0]) for d in DEVS])

# %% [markdown]
# ## A1: t = 0 and the phase boundaries, per device
#
# **What the code does** (`tools/build_run_table_extended.py`). t = 0 is the cell's first battery sample. The passive
# phase is integrated from it to the first scenario's start, the active phase from there to the last scenario's end,
# the rest ("teardown") from there to the last sample; each integral uses only the samples inside it, so the windows
# never overlap and the sample interval straddling each boundary falls in none (the gap below). The scenario times are
# stamped on the **laptop's** clock, truncated to the whole second; the samples carry the **phone's** clock; the
# builder compares them directly. `inputs/clock_offset_series.csv` measures the offset in every cell (phone minus
# laptop, from Android Runner's millisecond log of the profiler start against the first battery sample; validated
# against linkhub's share intent at r = 0.93–0.96) and re-integrates the phases with the boundaries on the phone's clock.

# %%
_a1_ext = pd.concat(pd.read_csv(T.RUN_TABLES[b], usecols=["app", "phone", "variant", "rep", "cell_start_ts_utc", "total_cell_joules",
                                                           "teardown_joules", "duration_whole_window_s"], dtype=str).assign(batch=b)
                    for b in T.SERIES)
cells = series.merge(_a1_ext, on=["app", "phone", "variant", "rep", "batch", "cell_start_ts_utc"], how="left", validate="one_to_one")
for _c in ("total_cell_joules", "teardown_joules", "duration_whole_window_s"):
    cells[_c] = pd.to_numeric(cells[_c], errors="coerce")
clk = pd.read_csv(T.DATA / "inputs" / "clock_offset_series.csv", dtype={"rep": str})
cells = cells.merge(clk.drop(columns=["run_id"]), on=key + ["cell_start_ts_utc"], how="left", validate="one_to_one")
assert cells["theta_s"].notna().all(), "a cell has no clock offset"
assert (cells["pre_j_check"] - cells["pre_workload_joules"]).abs().max() < 1e-5, "re-integration does not reproduce the passive phase"
assert (cells["wl_j_check"] - cells["workload_joules"]).abs().max() < 1e-5, "re-integration does not reproduce the active phase"
cells["gap_j"] = cells["total_cell_joules"] - cells["pre_workload_joules"] - cells["workload_joules"] - cells["teardown_joules"]
cells["tail_s"] = cells["duration_whole_window_s"] - cells["duration_pre_workload_s"] - cells["duration_workload_s"]
cells["passive_s_corr"] = cells["duration_pre_workload_s"] + cells["theta_s"]
lt = pd.read_csv(T.DATA / "inputs" / "launch_timing_series.csv", dtype={"rep": str}).merge(
    series[key + ["duration_pre_workload_s", "duration_workload_s"]], on=key, how="inner")
_a1_lk = lt[(lt["app"] == "linkhub") & (lt["appium_start_action"] == "SEND")].merge(cells[key + ["theta_s"]], on=key)
_a1_lk["intent_minus_stamp_s"] = _a1_lk["appium_start_rel_s"] - _a1_lk["duration_pre_workload_s"]
rows = []
for dev in DEVS:
    for b in list(T.SERIES) + ["all series"]:
        c = cells[(cells["phone"] == dev) & ((cells["batch"] == b) if b != "all series" else True)]
        cp = T.phase_set(c, "passive")                  # passive lengths: the passive-phase set, no medtimer
        k = _a1_lk[(_a1_lk["phone"] == dev) & ((_a1_lk["batch"] == b) if b != "all series" else True)]
        rows.append({"device": DEV[dev], "batch": b, "cells": len(c), "cells_passive": len(cp),
                     "passive_s_median": cp["duration_pre_workload_s"].median(), "passive_s_median_clock_corrected": cp["passive_s_corr"].median(),
                     "active_s_median": c["duration_workload_s"].median(),
                     "active_whole_seconds_pct": 100 * (c["duration_workload_s"] % 1 == 0).mean(),
                     "clock_offset_s_median": c["theta_s"].median(), "clock_offset_s_p5": c["theta_s"].quantile(0.05),
                     "clock_offset_s_p95": c["theta_s"].quantile(0.95),
                     "tail_s_median": c["tail_s"].median(), "active_runs_to_last_sample": int(c["active_reaches_last_sample"].sum()),
                     "active_runs_to_last_sample_corrected": int(c["active_reaches_last_sample_corr"].sum()),
                     "teardown_j_median": c["teardown_joules"].median(),
                     "gap_j_median": c["gap_j"].median(), "gap_j_p95": c["gap_j"].quantile(0.95),
                     "gap_pct_of_cell_median": (100 * c["gap_j"] / c["total_cell_joules"]).median(),
                     "linkhub_cells": len(k), "linkhub_intent_minus_stamp_s_median": k["intent_minus_stamp_s"].median(),
                     "linkhub_vs_offset_r": np.corrcoef(k["intent_minus_stamp_s"], k["theta_s"])[0, 1] if len(k) > 2 else np.nan})
a1 = pd.DataFrame(rows)
a1.to_csv(T.TABLES / "phase_boundaries_per_device.csv", index=False)
T.register("tables/phase_boundaries_per_device.csv", "REPO", "5.2 Energy measurement (phase boundaries)",
           "per device and series batch: passive and active lengths, the phone-minus-laptop clock offset, whole-second active lengths, "
           "the tail after the last scenario, cells whose active phase reaches the last sample, energy counted in no phase, linkhub's check", NB)
print(a1.round(3).to_string(index=False))
assert (a1["active_whole_seconds_pct"] == 100).all(), "an active-phase length is not a whole number of seconds"
for dev in DEVS:
    rb = a1[(a1["device"] == DEV[dev]) & (a1["batch"] != "all series")].set_index("batch")
    r = a1[(a1["device"] == DEV[dev]) & (a1["batch"] == "all series")].iloc[0]
    T.record(f"phase_boundaries.{dev}", passive_s_median=r.passive_s_median, active_s_median=r.active_s_median,
             gap_j_median=r.gap_j_median, gap_pct_of_cell_median=r.gap_pct_of_cell_median,
             clock_offset_s_median_by_series=[rb.loc[b, "clock_offset_s_median"] for b in T.SERIES],
             passive_s_median_by_series=[rb.loc[b, "passive_s_median"] for b in T.SERIES],
             passive_s_median_corrected_by_series=[rb.loc[b, "passive_s_median_clock_corrected"] for b in T.SERIES],
             linkhub_vs_offset_r=r.linkhub_vs_offset_r)

# %% [markdown]
# ### The cells whose active phase reaches the last battery sample
#
# Android Runner restores USB power right after it stops the profilers, so the last samples of a cell can carry
# charging current. Without the clock correction, the active phase reaches the last sample in these cells; how many
# per build, and what the active phase holds after the last scenario's end placed on the phone's clock.

# %%
_e = cells[cells["active_reaches_last_sample"] == 1]
e54 = (_e.groupby(["phone", "batch", "variant"])
       .agg(cells=("app", "size"), e_after_true_end_j_median=("e_active_after_true_end_j", "median"),
            e_after_true_end_j_max=("e_active_after_true_end_j", "max"),
            share_of_active_pct_median=("e_active_after_true_end_j", lambda s: np.nan),
            nondischarging_samples=("nondischarging_samples_after_true_end", "sum"),
            nondischarging_j=("nondischarging_j_after_true_end", "sum")).reset_index())
_e_share = (100 * _e["e_active_after_true_end_j"] / _e["workload_joules"]).groupby([_e["phone"], _e["batch"], _e["variant"]]).median()
e54["share_of_active_pct_median"] = _e_share.to_numpy()
e54["variant"] = e54["variant"].map(T.name)
e54.to_csv(T.TABLES / "active_phase_to_last_sample.csv", index=False)
T.register("tables/active_phase_to_last_sample.csv", "REPO", "5.2 Energy measurement (phase boundaries)",
           "cells whose uncorrected active phase reaches the last battery sample, per device, batch and build: energy after the last "
           "scenario's end on the phone's clock, and non-discharging samples there", NB)
print(e54.round(3).to_string(index=False))
_e3 = e54[(e54["phone"] == "pixel3") & (e54["batch"] == "series3")]
T.record("phase_boundaries.pixel3_series3_active_to_last_sample", cells=int(_e3["cells"].sum()),
         by_build={r.variant: int(r.cells) for r in _e3.itertuples()},
         nondischarging_samples=int(_e3["nondischarging_samples"].sum()))

# %% [markdown]
# ## A2: the timeline's durations, and what Appium does at its session start
#
# Medians over the analysed cells with a log capture (the control's for the launch events), seconds from t = 0.
# The application's second activity start in the log is Appium's session start in six applications only; what Android
# does with it is in `appium_second_start_per_app.csv`. No new process starts in any case.

# %%
_a2_ctrl = lt[lt["variant"] == "baseline_debug"]
_a2_sess = lt[(lt["appium_start_action"] == "MAIN") & (lt["appium_start_rel_s"] < lt["duration_pre_workload_s"])]


def _a2_row(label, c, s, cc):
    return {"device": label, "control_cells_with_log": len(c), "launch_ttid_ms_median": c["ttid_ms"].median(),
            "launch_ttid_ms_mean": c["ttid_ms"].mean(), "process_start_s": c["proc_start_rel_s"].median(),
            "first_display_s": c["first_display_rel_s"].median(), "uiautomator2_server_start_s": c["uia2_start_rel_s"].median(),
            "appium_session_activity_start_s": s["appium_start_rel_s"].median(), "passive_s": cc.loc[~cc["app"].isin(T.PASSIVE_EXCLUDED_APPS), "duration_pre_workload_s"].median(),
            "active_s": cc["duration_workload_s"].median(),
            "active_s_app_median_min": cc.groupby("app")["duration_workload_s"].median().min(),
            "active_s_app_median_max": cc.groupby("app")["duration_workload_s"].median().max()}


a2 = pd.DataFrame([_a2_row(DEV[d], _a2_ctrl[_a2_ctrl["phone"] == d], _a2_sess[(_a2_sess["phone"] == d) & (_a2_sess["variant"] == "baseline_debug")],
                           cells[cells["phone"] == d]) for d in DEVS]
                  + [_a2_row("all devices", _a2_ctrl, _a2_sess[_a2_sess["variant"] == "baseline_debug"], cells)])
a2.to_csv(T.TABLES / "timeline_per_device.csv", index=False)
T.register("tables/timeline_per_device.csv", "REPO", "5.2 Energy measurement (timeline)",
           "the timeline's durations per device and pooled: launch, process start and first display before t = 0, Appium's server and "
           "session start (six applications), passive and active lengths, the active range by application", NB)
print(a2.round(2).T.to_string())
lt["second_start_phase"] = np.where(lt["appium_start_rel_s"].isna(), "none",
                                    np.where(lt["appium_start_rel_s"] < lt["duration_pre_workload_s"], "passive", "active"))
_a2_o = (lt.assign(outcome=lt["appium_start_outcome"].fillna("no second start"), action=lt["appium_start_action"].fillna("-"))
         .groupby(["app", "action", "second_start_phase", "outcome"]).size().rename("cells").reset_index())
_a2_o.to_csv(T.TABLES / "appium_second_start_per_app.csv", index=False)
T.register("tables/appium_second_start_per_app.csv", "REPO", "5.2 Energy measurement (what the passive phase contains)",
           "the application's second activity start per application: intent, phase, and what Android did with it (no new process in any case)", NB)
print(_a2_o.to_string(index=False))
_a2_all = a2.set_index("device")
T.record("timeline.pooled_control_medians_s", process_start=_a2_all.loc["all devices", "process_start_s"],
         first_display=_a2_all.loc["all devices", "first_display_s"], uiautomator2_start=_a2_all.loc["all devices", "uiautomator2_server_start_s"],
         appium_session_start_six_apps=_a2_all.loc["all devices", "appium_session_activity_start_s"],
         passive=_a2_all.loc["all devices", "passive_s"], active=_a2_all.loc["all devices", "active_s"],
         ranges_pixel3_6_9={c: [_a2_all.loc[DEV[d], c] for d in DEVS] for c in
                            ("process_start_s", "first_display_s", "uiautomator2_server_start_s", "appium_session_activity_start_s", "passive_s", "active_s")})

# %% [markdown]
# ## B: the pairs
#
# One row per matched pair: technique, phase, device, application, batch; the control and protected means; ΔE % =
# 100 (T − C) / C; the log ratio ln(T / C); and the within-pair Cliff's delta over the pair's cells. The constructor
# checks that the pairs and their mean are those of `thesis_lib.pairs`, used everywhere else.

# %%
pt = P.pair_table(series)
counts_pairs = pt.groupby(["technique", "phase", "device"]).size().unstack("device")[DEVS]
print(counts_pairs.to_string())
for t in TECHS:
    for ph in P.PHASES:
        assert tuple(counts_pairs.loc[(t, ph)]) == {("Bangcle", "active"): (21, 24, 24), ("Allatori", "active"): (15, 15, 15),
                                                    ("freeRASP", "active"): (24, 27, 27), ("Bangcle", "passive"): (21, 24, 24),
                                                    ("Allatori", "passive"): (12, 12, 12), ("freeRASP", "passive"): (21, 24, 24)}[(t, ph)], (t, ph)
APPS = {(t, dev, ph): sorted(pt[(pt["technique"] == t) & (pt["device"] == dev) & (pt["phase"] == ph)]["app"].unique())
        for t in TECHS for dev in DEVS for ph in P.PHASES}
for dev in DEVS:          # packing: every application but medtimer; the passive phase: no medtimer in any arm
    assert APPS[("Bangcle", dev, "active")] == [a for a in APPS[("freeRASP", dev, "active")] if a != "medtimer"], dev
    assert APPS[("freeRASP", dev, "passive")] == APPS[("Bangcle", dev, "passive")], dev
pt.drop(columns=["_t", "_c"]).to_csv(T.TABLES / "pairs_per_device.csv", index=False)
T.register("tables/pairs_per_device.csv", "REPO", "6 Results (every matched pair)",
           "every matched pair per technique, phase and device: control and protected means, delta-E %, log ratio, within-pair Cliff's delta", NB)
T.record("pairs_per_device", active_bangcle_allatori_freerasp=[[21, 24, 24], [15, 15, 15], [24, 27, 27]],
         passive_bangcle_allatori_freerasp=[[21, 24, 24], [12, 12, 12], [21, 24, 24]],
         not_on_pixel3=sorted(set(APPS[("freeRASP", "pixel6", "active")]) - set(APPS[("freeRASP", "pixel3", "active")])))

# %% [markdown]
# ## B1: descriptive statistics per device, build and phase
#
# Over the analysed cells that enter the pairs. Each device lists the control over its applications, then each
# technique, each with its own arm's control over the same applications directly above it: packing leaves out medtimer,
# obfuscation covers five applications. CV = SD / mean, over cells of all the build's applications, so it
# mixes applications.

# %%
def _b1_stats(x):
    x = np.asarray(x, float)
    return dict(mean=x.mean(), median=np.median(x), sd=x.std(ddof=1), cv_pct=100 * x.std(ddof=1) / x.mean())


rows = []
for dev in DEVS:
    s_dev = series[series["phone"] == dev]
    for label, variant, arm in (("Control", "baseline_debug", "freeRASP"), ("Control, packing arm", "baseline_debug", "Bangcle"),
                                ("Bangcle", "bangcle_debug", "Bangcle"),
                                ("Control, obfuscation arm", "baseline_debug", "Allatori"), ("Allatori", "allatori_debug", "Allatori"),
                                ("freeRASP", "freerasp_debug", "freeRASP")):
        r = {"device": DEV[dev], "build": label}
        for ph, (ecol, dcol, _) in P.PHASES.items():
            apps = APPS[(arm, dev, ph)]
            e = s_dev[(s_dev["variant"] == variant) & s_dev["app"].isin(apps) & (s_dev[ecol] > 0)]
            r[f"{ph}_apps"], r[f"{ph}_cells"] = len(apps), len(e)
            r.update({f"{ph}_energy_{k}": v for k, v in _b1_stats(e[ecol]).items()})
            r.update({f"{ph}_duration_{k}": v for k, v in _b1_stats(e[dcol]).items()})
        rows.append(r)
b1 = pd.DataFrame(rows)
_b1_n = len(b1) // len(DEVS)                                   # rows per device
b1.to_csv(T.TABLES / "descriptives_per_device.csv", index=False)
T.register("tables/descriptives_per_device.csv", "REPO", "6.1 Descriptives (per device)",
           "per device x build x phase: cells, mean, median, SD, CV of energy (J) and of phase length (s)", NB)


_B1_ORDER = ("Control, packing arm", "Bangcle", "Control, obfuscation arm", "Allatori", "Control", "freeRASP")   # three pairs: each arm's control, then its technique
_B1_NAME = {"Control, packing arm": "Control (packing arm)", "Control, obfuscation arm": "Control (obfuscation arm)",
            "Control": "Control (freeRASP arm)"}   # the freeRASP arm: every application (eight on the Pixel 3; passive: no medtimer)


def _b1_tex(kind, unit, stem, caption, label, placement, section, desc, extra=None, bold=None):
    """Each device's rows as three pairs, each technique directly under its own arm's control; no CV column (it
    mixes applications; the CV stays in descriptives_per_device.csv). `extra(dev)` adds rows after a device's
    pairs; `bold(shown)` returns the cells to set in bold."""
    out, blocks = [], {}
    for dev in DEVS:
        blocks[len(out)] = DEV[dev]
        d = b1[b1["device"] == DEV[dev]].set_index("build")
        for b in _B1_ORDER:
            r = d.loc[b]
            row = {"Build": _B1_NAME.get(b, b)}
            for ph in ("passive", "active"):
                row[f"{ph}_cells"] = r[f"{ph}_cells"]
                for k in ("mean", "median", "sd"):
                    row[f"{ph}_{k}"] = f"{r[f'{ph}_{kind}_{k}']:.1f}"
            out.append(row)
        if extra:
            out.extend(extra(dev))
    shown = pd.DataFrame(out)
    shown.columns = ["Build"] + ["Cells", "Mean", "Median", "SD"] * 2
    T.register(P.write_tex_grouped(shown, stem, caption=caption, label=label, colfmt="l" + "rrrr" * 2,
                                   groups=[("", 1), (f"Passive phase ({unit})", 4), (f"Active phase ({unit})", 4)],
                                   blocks=blocks, star=True, bold_cells=bold(shown) if bold else None), placement, section, desc, NB)
    return shown


print(_b1_tex("energy", "J", "table_descriptives_per_device",
              caption="Energy per cell (J) by device, build and phase, three series batches; each technique is listed under its own arm's control.",
              label="tab:descriptives-device", placement="APPENDIX", section="Appendix (descriptives)",
              desc="CAIN Table III: energy per cell per device x build, passive and active phase: cells, mean, median, SD; "
                   "each technique under its own arm's control").to_string(index=False))
def _b1_medtimer(dev):
    """medtimer's passive phase, which contains its introduction and is not in the passive analysis, over its
    analysed builds on the device (its packed build is out)."""
    x = series[(series["phone"] == dev) & (series["app"] == "medtimer") & (series["pre_workload_joules"] > 0)]["duration_pre_workload_s"]
    st = _b1_stats(x)
    return [{"Build": "medtimer, as measured", "passive_cells": len(x), **{f"passive_{k}": f"{st[k]:.1f}" for k in ("mean", "median", "sd")},
             "active_cells": "–", **{f"active_{k}": "–" for k in ("mean", "median", "sd")}}]


def _b1_bold(shown):
    """The control of the freeRASP arm (every application but medtimer in the passive phase): its passive and active median;
    medtimer's passive median."""
    b = set()
    for i, name in enumerate(shown["Build"]):
        if name == "Control (freeRASP arm)":
            b |= {(i, 3), (i, 7)}
        elif name == "medtimer, as measured":
            b.add((i, 3))
    return b


_b1_tex("duration", "s", "table_durations_per_device",
        caption="Phase length per cell (s) by device, build and phase, three series batches; each technique is listed under its own arm's "
                "control. medtimer's passive phase, which contains its introduction and is not in the passive-phase analysis, is given in its "
                "own row, over its analysed builds. Bold: the values the thesis states in words.",
        label="tab:durations-device", placement="SUPPLEMENT", section="Supplement (descriptives: phase lengths)",
        desc="phase length per cell per device x build, passive and active: cells, mean, median, SD; medtimer's passive phase as measured",
        extra=_b1_medtimer, bold=_b1_bold)

# %% [markdown]
# ## B2–B3: normality of the paired differences, raw and log-transformed

# %%
rows = []
for ph in ("active", "passive"):
    for t in TECHS:
        for dev in DEVS:
            g = pt[(pt["technique"] == t) & (pt["phase"] == ph) & (pt["device"] == dev)]
            wr, pr_ = stats.shapiro(g["delta_pct"])
            wl, pl_ = stats.shapiro(g["log_ratio"])
            rows.append({"rq": P.RQ_OF_PHASE[ph], "phase": P.PHASE_DISPLAY[ph], "technique": t, "device": DEV[dev], "n": len(g),
                         "shapiro_w_raw": wr, "shapiro_p_raw": pr_, "normal_raw": bool(pr_ >= P.ALPHA),
                         "shapiro_w_log": wl, "shapiro_p_log": pl_, "normal_log": bool(pl_ >= P.ALPHA)})
norm = pd.DataFrame(rows)
norm.to_csv(T.TABLES / "normality_per_device.csv", index=False)
T.register("tables/normality_per_device.csv", "REPO", "Appendix (normality)", "Shapiro-Wilk on the paired differences of the 18 comparisons, raw and log", NB)
_n = norm.assign(**{"W (raw)": norm["shapiro_w_raw"].map(lambda v: f"{v:.3f}"), "p (raw)": norm["shapiro_p_raw"].map(P.fmt_p),
                    "W (log)": norm["shapiro_w_log"].map(lambda v: f"{v:.3f}"), "p (log)": norm["shapiro_p_log"].map(P.fmt_p)})
_n = _n[["device", "n", "W (raw)", "p (raw)", "W (log)", "p (log)"]]
_n.columns = ["Device", "n", "W", "p", "W", "p"]
T.register(P.write_tex_grouped(
    _n, "table_normality", caption=("Shapiro--Wilk test on the per-pair differences of each comparison: relative differences (raw) and log "
                                    "ratios (log). n: matched pairs. Bold: normality rejected ($p < 0.05$) on the raw scale."),
    label="tab:normality", colfmt="lrrrrr", groups=[("", 2), ("Raw", 2), ("Log", 2)],
    blocks={3 * i: f"{P.PHASE_DISPLAY[ph]} phase, {t}" for i, (ph, t) in enumerate((ph, t) for ph in ("active", "passive") for t in TECHS)},
    star=False, bold_rows=[i for i, r in enumerate(norm.itertuples()) if not r.normal_raw]),
    "SUPPLEMENT", "Supplement (normality)", "Shapiro-Wilk W and p per comparison, raw and log", NB)
print(norm.round(3).to_string(index=False))
T.record("normality", normal_raw=f"{int(norm['normal_raw'].sum())} of 18", normal_log=f"{int(norm['normal_log'].sum())} of 18",
         rejected_raw=[f"{r.technique} {r.phase.lower()} {r.device}" for r in norm.itertuples() if not r.normal_raw])

# %%
_SIM = {"normal (false alarms)": lambda n: rng_sim.normal(size=n),
        "Student t, 5 df (heavier tails)": lambda n: rng_sim.standard_t(5, size=n),
        "Student t, 3 df (heavy tails)": lambda n: rng_sim.standard_t(3, size=n),
        "10% from a 3x wider normal": lambda n: np.where(rng_sim.uniform(size=n) < 0.1, 3, 1) * rng_sim.normal(size=n),
        "normal plus one value at +4 SD": lambda n: np.append(rng_sim.normal(size=n - 1), 4.0),
        "log-normal, sigma 0.5 (skewed)": lambda n: rng_sim.lognormal(0, 0.5, size=n)}
sw = pd.DataFrame([{"distribution": k, **{f"n_{n}": 100 * np.mean([stats.shapiro(f(n)).pvalue < P.ALPHA for _ in range(10000)])
                                         for n in (15, 24, 27)}} for k, f in _SIM.items()])
sw.to_csv(T.TABLES / "shapiro_power.csv", index=False)
T.register("tables/shapiro_power.csv", "REPO", "Appendix (normality: test reliability)",
           "simulated Shapiro-Wilk rejection rates (%) at n = 15, 24, 27, 10,000 samples each", NB)
print(sw.round(1).to_string(index=False))
T.record("shapiro_power_pct", **{r.distribution: [r.n_15, r.n_24, r.n_27] for r in sw.itertuples()})

# %% [markdown]
# ## B4: the test rule, fixed before any test is run
#
# A paired t-test for all 18 if normality holds in all 18 on the raw scale, otherwise in all 18 on the log scale;
# otherwise the Wilcoxon signed-rank test for all 18, on the raw per-pair relative differences. Two-sided, α = 0.05.

# %%
assert "tests" not in globals(), "the rule must be fixed before the tests are computed"
RULE = ("paired t-test, raw" if norm["normal_raw"].all() else "paired t-test, log" if norm["normal_log"].all()
        else "Wilcoxon signed-rank, raw")
assert RULE.startswith("Wilcoxon"), "the tests below implement the Wilcoxon branch of the rule"
pd.DataFrame([{"rule": "t-test throughout if all 18 are normal (raw, else log); otherwise Wilcoxon signed-rank throughout",
               "normal_raw": f"{int(norm['normal_raw'].sum())} of 18", "normal_log": f"{int(norm['normal_log'].sum())} of 18",
               "applied": RULE, "sided": "two-sided", "alpha": P.ALPHA,
               "p": "exact null distribution when no zero or tied differences (n <= 50), else normal approximation",
               "statistic": "V = sum of the ranks of the positive differences (R's wilcox.test); W = min(V, n(n+1)/2 - V) (SciPy)"}]
             ).to_csv(T.TABLES / "test_rule.csv", index=False)
T.register("tables/test_rule.csv", "REPO", "4.4 Analysis (test rule)", "the one test rule for the 18 comparisons and the counts it was decided from", NB)
T.record("test_rule", applied=RULE)
print(RULE)

# %% [markdown]
# ## B5–B7: the 18 comparisons (RQ1: active phase; RQ2: passive phase)
#
# ΔE with its 95 % paired t-interval and MDE; the Wilcoxon signed-rank test (V, exact p); Benjamini–Hochberg within each
# RQ's nine comparisons; the within-pair Cliff's δ (each pair's protected cells against its control's cells, averaged
# over pairs; 95 % bootstrap interval over pairs; Romano et al.'s magnitude). The rank-biserial r and the naïve
# device-level δ are kept in the CSV.

# %%
tests = P.run_tests(pt, rng_boot, "primary")
tests.to_csv(T.TABLES / "per_device_tests.csv", index=False)
T.register("tables/per_device_tests.csv", "REPO", "6.2-6.3 RQ1 and RQ2 (per device)",
           "the 18 comparisons: n, delta-E with t-interval, median, V, W, exact p, BH within the RQ and across 18, within-pair Cliff's delta "
           "with bootstrap interval and magnitude, rank-biserial r, naive delta, Cohen's dz, MDE", NB)
assert (tests["significant"] == (tests["p_bh_all18"] < P.ALPHA)).all(), "BH over 18 changes a verdict: report it"
_RQ_NOTE = ("n: matched pairs (one application in one series batch). ΔE: mean per-pair relative difference with 95% paired t-interval. "
            "V: Wilcoxon signed-rank statistic (sum of the ranks of the positive differences), two-sided, exact p; p (BH): "
            "Benjamini-Hochberg adjusted within the research question's nine comparisons. Cliff's δ: protected cells against the "
            "same application's control cells on the same device and batch, averaged over pairs, with 95% bootstrap interval over "
            "pairs; magnitude after")
_RQ_NOTE_TEX = r"\cite{romano2006cliff}. MDE: minimum detectable effect at 80\% power. Bold: adjusted $p < 0.05$."
for rq, ph, lab in (("RQ1", "active", "tab:rq1"), ("RQ2", "passive", "tab:rq2")):
    x = tests[tests["rq"] == rq].reset_index(drop=True)
    T.register(T.write_tex(P.rq_rows(x), f"table_{rq.lower()}_{ph}",
                           caption=f"{rq}, {ph} phase: each technique against its unprotected control, per device.", label=lab,
                           colfmt="lrrrrrlr", blocks={0: "Bangcle (packing)", 3: "Allatori (obfuscation)", 6: "freeRASP (runtime shielding)"},
                           star=True, notes=_RQ_NOTE, notes_tex=_RQ_NOTE_TEX, bold_rows=[i for i, s in enumerate(x["significant"]) if s]),
               "REPO", f"6 Results ({rq}, per device; replaced by the merged table)", f"{rq} ({ph} phase), one row per technique x device, with V and MDE", NB)
# The thesis's table: RQ1 and RQ2 merged, phases side by side; V, MDE and the delta interval stay in the CSV
def _rq12(tx, stem, label, placement, section, desc, caption_extra=""):
    """The merged RQ1 + RQ2 table: one row per technique x device, phases side by side; V, MDE, delta interval in the CSV."""
    rows_, bold_, odd, shade_ = [], set(), [], set()
    for t in TECHS:
        for dev in DEVS:
            r_ = {ph: tx[(tx["technique"] == t) & (tx["device"] == dev) & (tx["phase"] == ph)].iloc[0] for ph in ("active", "passive")}
            row = {"Device": DEV[dev]}
            for k, ph in enumerate(("active", "passive")):
                x_ = r_[ph]
                row[f"n_{ph}"] = int(x_.n)
                row[f"d_{ph}"] = f"{P.signed(x_.delta_pct)} {P.ci_text(x_.ci_lo, x_.ci_hi)}"
                row[f"p_{ph}"] = P.fmt_p(x_.p_bh)
                row[f"c_{ph}"] = x_.magnitude                       # the magnitude word only; the value is in the CSV
                if x_.significant:
                    bold_ |= {(len(rows_), 2 + 4 * k + j) for j in range(3)}
                    if x_.delta_pct > 0:                            # a detected energy cost, shaded light red
                        shade_.add((len(rows_), 2 + 4 * k))
                elif x_.ci_lo > 0 or x_.ci_hi < 0:                  # the interval excludes zero but the adjusted test does not
                    odd.append(f"{_TECH_WORD[t]} on the {DEV[dev]} ({ph})")
            rows_.append(row)
    out = pd.DataFrame(rows_)
    out.columns = ["Device"] + ["n", "ΔE (%) [95% CI]", "p (BH)", "Cliff's δ"] * 2
    odd_text = (f" {' and '.join(odd)[0].upper() + ' and '.join(odd)[1:]}: the unadjusted interval excludes zero, the adjusted test "
                f"does not.") if odd else ""
    T.register(P.write_tex_grouped(
        out, stem, caption=("RQ1 (active phase) and RQ2 (passive phase): $\\Delta$E (\\%) per technique and device, the mean of n matched "
                            "pairs with its 95\\% interval, the BH-adjusted Wilcoxon $p$ and the magnitude of Cliff's "
                            "$\\delta$~\\cite{romano2006cliff}. Bold: adjusted $p < 0.05$; shaded: a detected energy cost." + odd_text
                            + caption_extra),
        label=label, colfmt="l" + "rrrl" * 2, groups=[("", 1), ("Active phase (RQ1)", 4), ("Passive phase (RQ2)", 4)],
        blocks={0: "Bangcle (packing)", 3: "Allatori (obfuscation)", 6: "freeRASP (runtime shielding)"}, star=True, bold_cells=bold_,
        shade_cells=shade_, shade_colour="red!12"),
        placement, section, desc, NB)
    return out


print(_rq12(tests, "table_rq12", "tab:rq12", "BODY", "6.2-6.3 RQ1 and RQ2 (per device)",
            "RQ1 and RQ2 merged: per technique x device, both phases: delta-E with CI, BH p, Cliff's delta magnitude (values in the CSV)"
            ).to_string(index=False))      # the exclusions are stated in the text, not the caption
for r in tests.itertuples():
    T.record(f"per_device.{T.short(ARM_OF[r.technique])}.{r.phase}.{r.device}", delta_pct=r.delta_pct, ci95=[r.ci_lo, r.ci_hi], n=r.n,
             V=r.V, p=P.fmt_p(r.p), p_bh=P.fmt_p(r.p_bh), cliff=r.cliff, cliff_ci95=[r.cliff_lo, r.cliff_hi], magnitude=r.magnitude,
             mde_pct=r.mde_pct, significant=bool(r.significant), method="Wilcoxon signed-rank, exact, BH within RQ")
print(tests[["rq", "technique", "device", "n", "delta_pct", "ci_lo", "ci_hi", "V", "p", "p_bh", "cliff", "magnitude", "rank_biserial",
             "cliff_naive", "mde_pct"]].round(3).to_string(index=False))

# %% [markdown]
# ## Checks of packing's Pixel 3 result, each separately and together
#
# **medtimer's packed build carries JaCoCo coverage probes** its control lacks: its packed APK holds
# `jacoco-agent.properties`, the JaCoCo runtime's `jacoco.properties` and `$jacocoInit` calls in the part the packer
# leaves unencrypted; medtimer's own build file enables coverage for debug builds; its control, obfuscated and freeRASP
# builds were rebuilt on 3 August without it, and the packing service could not re-wrap it. No other analysed build
# carries JaCoCo files or probe calls, and no other cohort application enables coverage. The primary analysis drops
# medtimer from the packing arm only (the rule applied to itsok's release build excludes the build, not the
# application); the check puts the packed build back ("as measured").
#
# **The clock.** The phases re-integrated with the boundaries moved onto the phone's clock, per cell, all devices
# (`clock_offset_series.csv`). **Both together**, and the cold cells after charging (`post_charge_flag`).

# %%
_df_med, _ = T.apply_filters(raw, exclude_builds=(("itsok", "allatori_debug"),))
series_med = _df_med[_df_med["batch"].isin(T.SERIES)].copy()          # medtimer's packed build kept (as measured)
pt_med = P.pair_table(series_med, passive_exclude=())      # as measured: medtimer's packed build in, medtimer in the passive phase
pt_pas = P.pair_table(series, passive_exclude=())          # medtimer in the passive phase; its packed build stays out
_clkcols = clk[key + ["cell_start_ts_utc", "pre_j_corr", "wl_j_corr", "theta_s"]]


def _clocked(sx):
    """The same cells with the phase energies re-integrated on the phone's clock (clock_offset_series.csv)."""
    out = sx.merge(_clkcols, on=key + ["cell_start_ts_utc"], how="left", validate="one_to_one")
    assert out["pre_j_corr"].notna().all()
    out["pre_workload_joules"], out["workload_joules"] = out["pre_j_corr"], out["wl_j_corr"]
    out["duration_pre_workload_s"] = out["duration_pre_workload_s"] + out["theta_s"]
    return out


series_clk = _clocked(series)
pt_clk = P.pair_table(series_clk)
_cold = T.post_charge_flag(series)
pt_cold = P.pair_table(series[~_cold])
checks = pd.concat([tests, P.run_tests(pt_pas, rng_boot, "with medtimer in the passive phase"),
                    P.run_tests(pt_med, rng_boot, "as measured (medtimer's packed build in; medtimer in the passive phase)"),
                    P.run_tests(pt_clk, rng_boot, "clock-corrected boundaries"),
                    P.run_tests(P.pair_table(_clocked(series_med), passive_exclude=()), rng_boot, "clock-corrected, as measured"),
                    P.run_tests(pt_cold, rng_boot, "without the cold cells after charging"),
                    P.run_tests(P.pair_table(series[series["app"] != "linkhub"]), np.random.default_rng(np.random.SeedSequence([T.SEED, 292])),
                                "without linkhub (its control carries a small residue)")], ignore_index=True)
checks.to_csv(T.TABLES / "per_device_checks.csv", index=False)
_rq12(checks[checks["analysis"] == "with medtimer in the passive phase"], "table_rq12_medtimer_in_passive", "tab:rq12-medtimer-in-passive",
      "REPO", "6 Results (replication package: medtimer in the passive phase)", "the merged RQ table with medtimer in the passive phase",
      caption_extra=" With medtimer in the passive phase; packing excludes medtimer's packed build.")
_rq12(checks[checks["analysis"].str.startswith("as measured")], "table_rq12_with_medtimer", "tab:rq12-with-medtimer", "REPO",
      "6 Results (replication package: as measured)", "the merged RQ table as measured: medtimer's packed build in, medtimer in the passive phase",
      caption_extra=" As measured: medtimer's packed build in the packing arm and medtimer in the passive phase.")
T.register("tables/per_device_checks.csv", "REPO", "6 Results (checks of the per-device results)",
           "the 18 comparisons: primary (medtimer's packed build excluded; medtimer out of the passive phase), with medtimer in the passive phase, "
           "as measured, with the phase boundaries on the phone's clock, both, without the cold cells after charging, and without linkhub; "
           "full rows", NB)
_ck = checks.pivot_table(index=["rq", "technique", "device"], columns="analysis", values="significant", aggfunc="first")
_ck_changes = _ck[_ck.nunique(axis=1) > 1]
print("verdicts that differ between the analyses:\n", _ck_changes.to_string() if len(_ck_changes) else "none")
print(f"cold cells removed: {int(_cold.sum())} of {len(series)}")
_pk = checks[checks["technique"] == "Bangcle"]
print(_pk[["analysis", "rq", "device", "n", "delta_pct", "ci_lo", "ci_hi", "V", "p", "p_bh", "cliff", "cliff_lo", "cliff_hi", "magnitude",
           "mde_pct"]].round(3).to_string(index=False))
for a, g in checks.groupby("analysis"):
    for r in g[g["technique"] == "Bangcle"].itertuples():
        T.record(f"checks.{a.replace(' ', '_').replace(',', '').replace(chr(39), '')}.bangcle.{r.phase}.{r.device}", delta_pct=r.delta_pct,
                 ci95=[r.ci_lo, r.ci_hi], n=r.n, p_bh=P.fmt_p(r.p_bh), cliff=r.cliff, magnitude=r.magnitude, significant=bool(r.significant))
for r in checks[checks["analysis"].str.startswith("without linkhub")].itertuples():
    T.record(f"checks.without_linkhub.{r.technique.lower()}.{r.phase}.{r.device}", delta_pct=r.delta_pct, ci95=[r.ci_lo, r.ci_hi], n=r.n,
             p_bh=P.fmt_p(r.p_bh), significant=bool(r.significant))
T.record("checks.verdict_changes", changes=[f"{i[1]} {i[0]} {i[2]}: " + ", ".join(f"{c}={bool(v)}" for c, v in row.items())
                                            for i, row in _ck_changes.iterrows()], cold_cells_removed=int(_cold.sum()))

# %%
# Both checks' packing rows as one table for the replication package
_pk_order = ("primary", "as measured (medtimer's packed build in; medtimer in the passive phase)", "clock-corrected boundaries", "clock-corrected, as measured")
_pk_x = pd.concat([checks[(checks["analysis"] == a) & (checks["technique"] == "Bangcle")] for a in _pk_order], ignore_index=True)
_pk_tab = P.rq_rows(_pk_x).assign(Phase=[P.PHASE_DISPLAY[p] for p in _pk_x["phase"]])
_pk_tab = _pk_tab[["Phase"] + [c for c in _pk_tab.columns if c != "Phase"]]
T.register(T.write_tex(_pk_tab, "table_packing_checks",
                       caption="Packing (Bangcle) against its control per device: the primary analysis (medtimer's packed build excluded), with it, "
                               "with the phase boundaries on the phone's clock, and both.", label="tab:packing-checks", colfmt="llrrrrrlr",
                       blocks={0: "Primary (medtimer's packed build excluded)", 6: "As measured (medtimer's packed build in)",
                               12: "Phase boundaries on the phone's clock", 18: "Phase boundaries on the phone's clock, as measured"},
                       star=True, notes=_RQ_NOTE, notes_tex=_RQ_NOTE_TEX,
                       bold_rows=[i for i, sig in enumerate(_pk_x["significant"]) if sig]),
           "REPO", "6 Results (checks of packing's Pixel 3 result)", "packing per device and phase under the two checks, separately and together", NB)

# %% [markdown]
# ## B8: the permutation test per device against the Wilcoxon test
#
# The stratified permutation test of the pooled analysis, run within each device (6,000 permutations), BH within each RQ.

# %%
rows = []
for r in tests.itertuples():
    sx = series[series["phone"] == r.device]
    if r.phase == "passive":                                   # the passive phase's analysis set
        sx = sx[~sx["app"].isin(T.PASSIVE_EXCLUDED_APPS)]
    pt_ = T.permutation_test(sx, ARM_OF[r.technique], P.PHASES[r.phase][0], rng_perm)
    assert abs(pt_["observed"] - r.delta_pct) < 1e-9
    rows.append({"rq": r.rq, "technique": r.technique, "device": DEV[r.device], "p_wilcoxon": r.p, "p_bh_wilcoxon": r.p_bh,
                 "p_permutation": pt_["p"], "permutations_as_extreme": pt_["hits"]})
perm = pd.DataFrame(rows)
perm["p_bh_permutation"] = perm.groupby("rq")["p_permutation"].transform(lambda s: P.bh(s.to_numpy()))
perm["same_verdict"] = (perm["p_bh_wilcoxon"] < P.ALPHA) == (perm["p_bh_permutation"] < P.ALPHA)
perm.to_csv(T.TABLES / "per_device_permutation_check.csv", index=False)
T.register("tables/per_device_permutation_check.csv", "REPO", "6 Results (robustness: permutation test per device)",
           "the 18 comparisons: Wilcoxon p against the stratified permutation p (6,000 permutations), each BH-adjusted within the RQ", NB)
print(perm.round(4).to_string(index=False))
T.record("permutation_vs_wilcoxon", same_verdict=f"{int(perm['same_verdict'].sum())} of 18")

# %% [markdown]
# ## Figure 1: the 18 effects as a forest plot, (a) active and (b) passive phase
#
# One column. Each row: the mean of the matched pairs with its 95 % paired t-interval, from `tests`, the same cells as
# Table 1, so the two cannot differ. Each technique sits in its own grey band under its name in bold. Colour = device
# (Pixel 3 blue, Pixel 6 orange, Pixel 9 green, a palette checked for colour-blind separation), and every row is labelled
# with its device, so nothing depends on colour alone. Filled circle: BH-adjusted p < 0.05 (a difference is detected);
# open: no detectable difference. Each panel has its own x-axis: the passive effects reach about +50 %, the active ones
# stay small.

# %%
import matplotlib.patches as mpatches
from matplotlib import font_manager as _fm
# the figures' default serif (Libertine Display) has no bold cut: bold labels use the regular Libertine family
_FP_BOLD = "Linux Libertine O" if "LinLibertine_RB" in _fm.findfont(_fm.FontProperties(family="Linux Libertine O", weight="bold")) else None
_FP_DEV_COLOURS = {"pixel3": "#1c5cab", "pixel6": "#eb6834", "pixel9": "#1baf7a"}   # checked for colour-blind separation
_FP_WORD = {"Bangcle": "Packing (Bangcle)", "Allatori": "Obfuscation (Allatori)", "freeRASP": "Runtime shielding (freeRASP)"}
_fp_rows = [(t, dev) for t in TECHS for dev in DEVS]             # top to bottom: Bangcle, Allatori, freeRASP
_fp_y, _fp_head, _y = {}, {}, 0.0
for t in TECHS:                                                  # a header row per technique, then its three devices
    _fp_head[t] = -_y
    for dev in DEVS:
        _y += 1.0
        _fp_y[(t, dev)] = -_y
    _y += 1.6
fig, axes = plt.subplots(2, 1, figsize=(T.FIG_SINGLE, 4.2), layout="constrained")   # 4.2 in tall: at 3.5 in the rows nearly touch
import matplotlib.transforms as _mtr
for ax, ph, tag in zip(axes, ("active", "passive"), "ab"):
    _sub = tests[tests["phase"] == ph]
    _pad = 0.06 * (_sub["ci_hi"].max() - _sub["ci_lo"].min())
    _xl = (min(_sub["ci_lo"].min(), 0) - _pad, _sub["ci_hi"].max() + _pad)
    for t in TECHS:                                              # the technique's band: its header row and three devices
        ax.add_patch(mpatches.Rectangle((_xl[0], _fp_head[t] - 3.55), _xl[1] - _xl[0], 4.1, facecolor="0.94", edgecolor="none",
                                        zorder=0))
    for t, dev in _fp_rows:
        tr = _sub[(_sub["device"] == dev) & (_sub["technique"] == t)].iloc[0]
        yy, col = _fp_y[(t, dev)], _FP_DEV_COLOURS[dev]
        ax.plot([tr.ci_lo, tr.ci_hi], [yy, yy], color=col, lw=1.2, solid_capstyle="butt", zorder=2)
        ax.plot(tr.delta_pct, yy, ls="", marker="o", ms=4.6, mec=col, mew=1.0, mfc=col if tr.significant else "white", zorder=3)
        ax.text(1.03, yy, P.signed(tr.delta_pct, 1).replace("-", "\u2212"), transform=_mtr.blended_transform_factory(ax.transAxes, ax.transData),
                ha="left", va="center", fontsize=6.5, color="0.1", clip_on=False,
                fontweight="bold" if tr.significant else "normal", fontfamily=_FP_BOLD if tr.significant else None)
    ax.axvline(0, color="0.3", lw=0.8, zorder=1)
    ax.set_xlim(*_xl)
    ax.text(0, _fp_head[TECHS[0]] + 0.05, " control", ha="left", va="center", fontsize=6.5, fontstyle="italic", color="0.3")
    ax.text(1.03, _fp_head[TECHS[0]] + 0.05, "ΔE %", transform=_mtr.blended_transform_factory(ax.transAxes, ax.transData),
            ha="left", va="center", fontsize=6.5, fontstyle="italic", color="0.3", clip_on=False)
    ax.set_yticks([_fp_y[r] for r in _fp_rows])
    ax.set_yticklabels([DEV[d] for _, d in _fp_rows], fontsize=7)
    _bl = _mtr.blended_transform_factory(ax.transAxes, ax.transData)
    for t in TECHS:                                              # the technique's name in bold, aligned with the device labels
        ax.text(-0.02, _fp_head[t] + 0.05, _FP_WORD[t], transform=_bl, ha="right", va="center", fontsize=7, fontweight="bold",
                fontfamily=_FP_BOLD, color="0.1")
    ax.set_ylim(min(_fp_y.values()) - 0.8, _fp_head[TECHS[0]] + 0.75)
    ax.set_title(f"({tag}) {ph.capitalize()} phase", fontsize=8, loc="left", fontweight="bold", fontfamily=_FP_BOLD)
    ax.set_xlabel(T.DELTA_LABEL, fontsize=7.5)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
# legend: the devices on the first row, filled/open on the second (the legend fills column by column)
_fp_dev_h = [Line2D([], [], ls="-", marker="o", color=_FP_DEV_COLOURS[d], mfc=_FP_DEV_COLOURS[d], ms=4, lw=1.2, label=DEV[d]) for d in DEVS]
_fp_fill_h = [Line2D([], [], ls="", marker="o", mfc="0.3", mec="0.3", ms=4.6, label="difference detected (adjusted p < 0.05)"),
              Line2D([], [], ls="", marker="o", mfc="white", mec="0.3", ms=4.6, label="no detectable difference"),
              Line2D([], [], ls="", label=" ")]
fig.legend(handles=[h for pair in zip(_fp_dev_h, _fp_fill_h) for h in pair], loc="outside lower center", ncol=3, frameon=False,
           fontsize=6.5, handlelength=1.2, columnspacing=0.8, handletextpad=0.3)
T.register(T.save_figure(fig, "figure1_per_device", T.FIG_SINGLE), "BODY", "6 Results (RQ1 and RQ2, per device)",
           "forest plot of the 18 comparisons: mean delta-E vs. control with its 95% paired t-interval, (a) active and (b) passive "
           "phase; techniques in grey bands under bold names; colour and row label = device; filled circle: BH-adjusted p < 0.05", NB)

# %% [markdown]
# ## Q-Q plots of the paired differences (appendix)

# %%
fig, axes = plt.subplots(6, 3, figsize=(T.FIG_DOUBLE, 8.6), layout="constrained")
for i, (ph, t) in enumerate((ph, t) for ph in ("active", "passive") for t in TECHS):
    for j, dev in enumerate(DEVS):
        ax = axes[i, j]
        d = pt[(pt["phase"] == ph) & (pt["technique"] == t) & (pt["device"] == dev)]["delta_pct"].to_numpy()
        (osm, osr), (slope, icpt, _) = stats.probplot(d, dist="norm")
        ax.scatter(osm, osr, s=10, marker=T.DEVICE_MARKERS[dev], facecolors="none", edgecolors=T.COLOURS[t], linewidths=0.6)
        ax.plot(osm, icpt + slope * osm, color="0.3", lw=0.7)
        nr = norm[(norm["phase"] == P.PHASE_DISPLAY[ph]) & (norm["technique"] == t) & (norm["device"] == DEV[dev])].iloc[0]
        ax.text(0.03, 0.95, f"n = {nr.n}\nShapiro p = {P.fmt_p(nr.shapiro_p_raw)}", transform=ax.transAxes, va="top", fontsize=7)
        ax.set_title(f"{t}, {ph}, {DEV[dev]}", fontsize=7.5)
        ax.spines[["top", "right"]].set_visible(False)
        if j == 0:
            ax.set_ylabel("ΔE (%)")
        if i == 5:
            ax.set_xlabel("normal quantile")
T.register(T.save_figure(fig, "qq_per_device", T.FIG_DOUBLE), "SUPPLEMENT", "Supplement (normality)",
           "Q-Q plots of the per-pair relative differences, 18 comparisons, with the Shapiro-Wilk p", NB)

# %% [markdown]
# ## RQ3: correlation of runtime characteristics with energy, the CAIN way
#
# Per build, device and phase, Spearman's ρ between **per-cell energy** and per-cell phase length, CPU share, memory
# and threads, all the build's applications pooled (as CAIN pools its models). **CPU** is `dumpsys cpuinfo` TOTAL,
# the device-wide share: the application's own share is not recorded, and Android refreshes the figure only occasionally
# (in about half the cells the whole active phase carries the value from before it; `cpu_figure_refresh.csv`), so it
# describes the load around a cell more than within each phase. **Memory** is the application's PSS. **Threads**:
# no true thread count is recorded (the profilers record CPU and memory only); this is the number of the
# application's threads that wrote to the system log, a lower bound counted per cell, so the same value enters both
# phases. BH within RQ3 (96 tests). Because the cells of different applications are pooled, these correlations
# largely measure how applications differ; the difference-based version below correlates the matched pairs' differences.

# %%
_m3 = pd.read_csv(T.DATA / "inputs" / "mechanism_cells_series.csv", dtype={"rep": str})
assert not _m3.duplicated(key).any()
_l3 = pd.concat(pd.read_csv(T.DATA / "inputs" / f"logcat_activity_{b}.csv", dtype={"rep": str}).assign(batch=b) for b in T.SERIES)
_l3 = _l3.drop_duplicates(key, keep="last")
cm = (series.merge(_m3[key + ["ar_cpu_post_launch", "ar_cpu_workload", "ar_mem_mb_post_launch", "ar_mem_mb_workload"]], on=key, how="left",
                   validate="one_to_one").merge(_l3[key + ["threads"]], on=key, how="left", validate="one_to_one"))
METRICS = {"length": ("duration_pre_workload_s", "duration_workload_s", "Phase length (s)"),
           "cpu": ("ar_cpu_post_launch", "ar_cpu_workload", "CPU, device-wide (%)"),
           "memory": ("ar_mem_mb_post_launch", "ar_mem_mb_workload", "Memory, app PSS (MB)"),
           "threads": ("threads", "threads", "Threads (log-derived)")}
BUILDS = {"Control": "baseline_debug", "Bangcle": "bangcle_debug", "Allatori": "allatori_debug", "freeRASP": "freerasp_debug"}
CM = {"active": cm, "passive": cm[~cm["app"].isin(T.PASSIVE_EXCLUDED_APPS)]}      # no medtimer in the passive phase
rows = []
for ph, (ecol, _, _) in P.PHASES.items():
    for dev in DEVS:
        for b, v in BUILDS.items():
            c_ = CM[ph]
            d = c_[(c_["phone"] == dev) & (c_["variant"] == v) & (c_[ecol] > 0)]
            for m, (c_pass, c_act, _) in METRICS.items():
                mcol = c_pass if ph == "passive" else c_act
                dd = d[[ecol, mcol]].dropna()
                rho, p = stats.spearmanr(dd[ecol], dd[mcol])
                rows.append({"phase": ph, "device": DEV[dev], "build": b, "metric": m, "n": len(dd), "apps": d["app"].nunique(),
                             "rho": rho, "p": p})
rq3 = pd.DataFrame(rows)
rq3["p_bh"] = P.bh(rq3["p"].to_numpy())
rq3["stars"] = np.select([rq3["p_bh"] < 0.001, rq3["p_bh"] < 0.01, rq3["p_bh"] < 0.05], ["***", "**", "*"], "")
rq3.to_csv(T.TABLES / "rq3_correlations.csv", index=False)
T.register("tables/rq3_correlations.csv", "REPO", "6.4 RQ3", "Spearman rho of per-cell energy with phase length, device-wide CPU, app PSS and "
           "log-derived threads, per build x device x phase, applications pooled; BH over the 96", NB)
RHO_STRONG = 0.5          # |rho| >= 0.5 shaded, a large association after Cohen (1988)
_r3_tab, _r3_bold, _r3_shade = [], set(), set()
for i, (dev, b) in enumerate((DEV[d], b) for d in DEVS for b in BUILDS):
    row = {"Build": b}
    for j, (ph, m) in enumerate((ph, m) for ph in ("passive", "active") for m in METRICS):
        r = rq3[(rq3["phase"] == ph) & (rq3["device"] == dev) & (rq3["build"] == b) & (rq3["metric"] == m)].iloc[0]
        row[f"{ph}_{m}"] = P.signed(r.rho).replace("0.", ".", 1)      # one column: no stars, ".70" as is usual for rho
        if r.p_bh < P.ALPHA:
            _r3_bold.add((i, j + 1))
        if abs(r.rho) >= RHO_STRONG:
            _r3_shade.add((i, j + 1))
    _r3_tab.append(row)
assert all(c in _r3_bold for c in _r3_shade), "a shaded |rho| >= 0.5 cell that is not significant: say so in the caption"
_r3_tab = pd.DataFrame(_r3_tab)
_r3_tab.columns = ["Build"] + ["Len.", "CPU", "Mem.", "Thr."] * 2
_r3_paths = P.write_tex_grouped(
    _r3_tab, "table_rq3_correlations",
    caption=("RQ3: Spearman's $\\rho$ between energy per measurement and phase length (Len.), device-wide processor share (CPU), "
             "the application's memory (Mem.) and threads (Thr.), per device, build and phase. Bold: BH-adjusted $p < 0.05$; "
             "shaded: $|\\rho| \\geq 0.5$~\\cite{cohen1988}."),
    label="tab:rq3", colfmt="l" + "rrrr" * 2, groups=[("", 1), ("Passive", 4), ("Active", 4)],
    blocks={4 * i: DEV[d] for i, d in enumerate(DEVS)}, star=False, bold_cells=_r3_bold, shade_cells=_r3_shade)
_f3 = T.TABLES / "table_rq3_correlations.tex"           # one column, so a smaller type and tighter columns
import re as _re
_t3 = _f3.read_text().replace("\\small\n", "\\footnotesize\n\\setlength{\\tabcolsep}{3.2pt}\n", 1)
_f3.write_text(_re.sub(r"(?<![\w$])-\.(?=\d)", "$-$.", _t3))             # a minus sign, not a hyphen, before ".26"
T.register(_r3_paths, "BODY", "6.4 RQ3", "RQ3 table: Spearman rho per device x build, both phases, one column (bold = BH < 0.05, "
           "no stars; BH over 96)", NB)
print(_r3_tab.to_string(index=False))
T.record("rq3.cain", significant=f"{int((rq3['p_bh'] < P.ALPHA).sum())} of {len(rq3)}", method="Spearman, cells pooled over applications, BH over 96",
         strong_shaded=f"{len(_r3_shade)} of {len(rq3)}", strong_threshold=RHO_STRONG,
         median_abs_rho_by_metric={m: float(rq3[rq3['metric'] == m]['rho'].abs().median()) for m in METRICS})

# %%
def _r3_fig(ph, stem, placement):
    ecol = P.PHASES[ph][0]
    fig, axes = plt.subplots(4, 3, figsize=(T.FIG_DOUBLE, 7.4), layout="constrained")
    styles = {"Control": "-", "Bangcle": "--", "Allatori": "-.", "freeRASP": ":"}
    for i, (m, (c_pass, c_act, mlab)) in enumerate(METRICS.items()):
        mcol = c_pass if ph == "passive" else c_act
        for j, dev in enumerate(DEVS):
            ax = axes[i, j]
            for b, v in BUILDS.items():
                c_ = CM[ph]
                d = c_[(c_["phone"] == dev) & (c_["variant"] == v) & (c_[ecol] > 0)][[ecol, mcol]].dropna()
                ax.scatter(d[mcol], d[ecol], s=3, marker=T.DEVICE_MARKERS[dev], color=T.COLOURS[b], alpha=0.35, linewidths=0, zorder=2)
                if len(d) > 2 and d[mcol].nunique() > 1:
                    k, c0 = np.polyfit(d[mcol], d[ecol], 1)
                    xs = np.linspace(d[mcol].min(), d[mcol].max(), 2)
                    ax.plot(xs, c0 + k * xs, color=T.COLOURS[b], lw=1.1, ls=styles[b], zorder=3)
            ax.set_title(f"({'abcdefghijkl'[3 * i + j]}) {DEV[dev]}", fontsize=8)
            ax.set_xlabel(mlab)
            if j == 0:
                ax.set_ylabel(f"{P.PHASE_DISPLAY[ph]}-phase energy (J)")
            ax.spines[["top", "right"]].set_visible(False)
    fig.legend(handles=[Line2D([], [], color=T.COLOURS[b], ls=styles[b], lw=1.1, marker="o", ms=3, label=b) for b in BUILDS],
               loc="outside lower center", ncol=4, frameon=False)
    T.register(T.save_figure(fig, stem, T.FIG_DOUBLE), placement, "6.4 RQ3" if placement == "BODY" else "Supplement (RQ3)",
               f"RQ3 scatter, {ph} phase: per-cell energy against phase length, CPU, memory, threads (rows) per device (columns); colour = build, "
               "least-squares line per build", NB)


_r3_fig("active", "figure_rq3_active", "SUPPLEMENT")
_r3_fig("passive", "figure_rq3_passive", "SUPPLEMENT")

# %% [markdown]
# ### The compact RQ3 figure for the body: phase length only
#
# Rows: the passive and the active phase; columns: the devices; one point per cell, a least-squares line per build.
# At the same phase length, freeRASP's passive-phase line lies above the others.

# %%
_rl_style = {"Control": "-", "Bangcle": "--", "Allatori": "-.", "freeRASP": ":"}
fig, axes = plt.subplots(3, 2, figsize=(T.FIG_SINGLE, 3.4), layout="constrained", sharex="col")   # one column wide
for j, ph in enumerate(("passive", "active")):
    ecol, dcol = P.PHASES[ph][0], P.PHASES[ph][1]
    for i, dev in enumerate(DEVS):
        ax = axes[i, j]
        for b, v in BUILDS.items():
            c_ = CM[ph]
            d = c_[(c_["phone"] == dev) & (c_["variant"] == v) & (c_[ecol] > 0)][[dcol, ecol]].dropna()
            ax.scatter(d[dcol], d[ecol], s=2.5, marker=T.DEVICE_MARKERS[dev], color=T.COLOURS[b], alpha=0.3, linewidths=0, zorder=2)
            k, c0 = np.polyfit(d[dcol], d[ecol], 1)
            xs = np.linspace(d[dcol].min(), d[dcol].max(), 2)
            ax.plot(xs, c0 + k * xs, color=T.COLOURS[b], lw=1.2, ls=_rl_style[b], zorder=3)
        ax.set_title(f"({'abcdef'[2 * i + j]}) {DEV[dev]}, {ph}", fontsize=7.5)
        ax.spines[["top", "right"]].set_visible(False)
        if j == 0:
            ax.set_ylabel("energy (J)")
    axes[-1, j].set_xlabel("phase length (s)")
fig.legend(handles=[Line2D([], [], color=T.COLOURS[b], ls=_rl_style[b], lw=1.2, label=b) for b in BUILDS],
           loc="outside lower center", ncol=4, frameon=False, handlelength=2.0, columnspacing=1.0)
T.register(T.save_figure(fig, "figure_rq3_length", T.FIG_SINGLE), "BODY", "6.4 RQ3",
           "energy per cell against phase length, per device (rows) and phase (columns: passive, active): one point per cell, a "
           "least-squares line per build (one column)", NB)

# %%
# The difference-based version: per technique, phase and device, Spearman of the pairs' delta-E with the pairs' differences
_r3_g = cm.groupby(["app", "phone", "batch", "variant"])[list(dict.fromkeys(c for m in METRICS.values() for c in m[:2]))].mean()
rows = []
for t in TECHS:
    for ph, (ecol, _, _) in P.PHASES.items():
        for dev in DEVS:
            g = pt[(pt["technique"] == t) & (pt["phase"] == ph) & (pt["device"] == dev)]
            for m, (c_pass, c_act, _) in METRICS.items():
                mcol = c_pass if ph == "passive" else c_act
                tv = _r3_g.loc[[(a, dev, b, ARM_OF[t]) for a, b in zip(g["app"], g["batch"])], mcol].to_numpy()
                cv = _r3_g.loc[[(a, dev, b, "baseline_debug") for a, b in zip(g["app"], g["batch"])], mcol].to_numpy()
                dm = (tv - cv) if m == "cpu" else 100 * (tv - cv) / cv
                ok = np.isfinite(dm)
                rho, p = stats.spearmanr(g["delta_pct"].to_numpy()[ok], dm[ok])
                rows.append({"technique": t, "phase": ph, "device": DEV[dev], "metric": m, "n": int(ok.sum()), "rho": rho, "p": p})
r3d = pd.DataFrame(rows)
r3d["p_bh"] = P.bh(r3d["p"].to_numpy())
r3d.to_csv(T.TABLES / "rq3_difference_correlations.csv", index=False)
T.register("tables/rq3_difference_correlations.csv", "REPO", "6.4 RQ3 (difference-based check)",
           "Spearman rho of the matched pairs' delta-E with their differences in phase length (%), CPU (points), memory (%), threads (%); BH over 72", NB)
print(f"difference-based: {int((r3d['p_bh'] < P.ALPHA).sum())} of {len(r3d)} significant after BH; largest |rho|:")
print(r3d.reindex(r3d["rho"].abs().sort_values(ascending=False).index).head(4).round(3).to_string(index=False))
T.record("rq3.differences", significant=f"{int((r3d['p_bh'] < P.ALPHA).sum())} of {len(r3d)}", method="Spearman on matched-pair differences, BH over 72")

# %% [markdown]
# ## RQ4: how the protected builds run differently from their controls
#
# The same matched pairs as RQ1 and RQ2 (`pt`): per technique, device and pair, the protected build's mean against its
# control's mean, now for runtime characteristics instead of energy. **Memory**: the application's PSS (`dumpsys meminfo`),
# per phase, as a percentage of the control. **Threads**: the application's threads that wrote to the system log during the
# run, its launch included (one value per measurement, so one test per technique and device, on the active-phase pairs), as a
# count. **Processor share**: device-wide (`dumpsys cpuinfo` TOTAL), active phase only, in percentage points; inside the
# passive phase the figure is refreshed in under a fifth of the measurements (`cpu_figure_refresh.csv`), so it mostly carries
# the load from before the phase. **Phase length**, per phase, as a percentage. Wilcoxon signed-rank on the pair differences,
# BH within RQ4 (its 54 tests), and Cliff's delta within pairs with a bootstrap interval, as for RQ1 and RQ2, on its own random
# stream so that no other table changes. Measurements are those of the pair table (energy > 0 in the phase).

# %%
_r4_rng = np.random.default_rng(np.random.SeedSequence([T.SEED, 35]))
_R4 = [  # characteristic, block heading, phase, column, unit ("pct": % of the control's mean; "abs": the difference itself)
    ("memory_active", "Memory, active phase (the application's PSS, % of the control)", "active", "ar_mem_mb_workload", "pct"),
    ("memory_passive", "Memory, passive phase (the application's PSS, % of the control)", "passive", "ar_mem_mb_post_launch", "pct"),
    ("threads", "Threads that log during the run, launch included (count)", "active", "threads", "abs"),
    ("cpu_active", "Processor share, device-wide, active phase (percentage points)", "active", "ar_cpu_workload", "abs"),
    ("length_active", "Active-phase length (% of the control)", "active", "duration_workload_s", "pct"),
    ("length_passive", "Passive-phase length (% of the control)", "passive", "duration_pre_workload_s", "pct"),
]
_r4_cells = {k: g for k, g in cm.groupby(["app", "phone", "batch", "variant"])}
rows = []
for _rk, _rh, _rph, _rcol, _runit in _R4:
    ecol = P.PHASES[_rph][0]
    for t in TECHS:
        for dev in DEVS:
            g = pt[(pt["technique"] == t) & (pt["phase"] == _rph) & (pt["device"] == dev)]
            d, d_raw, cw, dropped = [], [], [], 0
            for a, b in zip(g["app"], g["batch"]):
                tg, cg = _r4_cells[(a, dev, b, ARM_OF[t])], _r4_cells[(a, dev, b, "baseline_debug")]
                tg, cg = tg[tg[ecol] > 0], cg[cg[ecol] > 0]                  # the pair table's measurements
                tv, cv = tg[_rcol].dropna().to_numpy(float), cg[_rcol].dropna().to_numpy(float)
                dropped += (len(tg) - len(tv)) + (len(cg) - len(cv))
                assert len(tv) and len(cv), (_rk, t, dev, a, b)
                d_raw.append(tv.mean() - cv.mean())
                d.append(100 * (tv.mean() - cv.mean()) / cv.mean() if _runit == "pct" else tv.mean() - cv.mean())
                cw.append(P.cliffs_delta(tv, cv))
            d, cw = np.asarray(d), np.asarray(cw)
            assert len(d) == len(g) > 0
            m, lo, hi, _ = T.ci_naive(d)
            w, p, method = P.wilcoxon(d)
            clo, chi = P.boot_ci(cw, np.mean, _r4_rng)
            rows.append(dict(rq="RQ4", characteristic=_rk, phase=_rph, technique=t, device=dev, unit=_runit, n=len(d),
                             difference=m, ci_lo=lo, ci_hi=hi, diff_raw=float(np.mean(d_raw)), higher=int((d > 0).sum()),
                             lower=int((d < 0).sum()), W=w, p=p, p_method=method, cliff=float(cw.mean()), cliff_lo=clo,
                             cliff_hi=chi, magnitude=P.cliff_label(cw.mean()), measurements_without_value=dropped))
rq4 = pd.DataFrame(rows)
assert len(rq4) == 54
rq4["p_bh"] = P.bh(rq4["p"].to_numpy())
rq4["significant"] = rq4["p_bh"] < P.ALPHA
rq4.to_csv(T.TABLES / "rq4_runtime_differences.csv", index=False)
T.register("tables/rq4_runtime_differences.csv", "REPO", "6 Results (RQ4)",
           "RQ4: protected build minus control on matched pairs, per technique x device: memory (PSS, both phases, %), threads that "
           "log (whole run, count), device-wide CPU (active phase, points), phase length (both phases, %); mean, 95% t-interval, "
           "MB/s/raw difference, pairs higher, Wilcoxon p, BH within RQ4 (54), Cliff's delta within pairs", NB)

# the body table: one row per technique and device, the four characteristics that carry the result
_r4_get = lambda _rk, t, dev: rq4[(rq4["characteristic"] == _rk) & (rq4["technique"] == t) & (rq4["device"] == dev)].iloc[0]
_R4_BODY = [("memory_active", "Memory (%)"), ("threads", "Threads"), ("cpu_active", "CPU (pp)"), ("length_passive", "Passive length (%)")]
_r4_tab, _r4_bold, _r4_blocks, _r4_shade = [], set(), {}, set()
for t in TECHS:
    _r4_blocks[len(_r4_tab)] = {"Bangcle": "Packing (Bangcle)", "Allatori": "Obfuscation (Allatori)", "freeRASP": "Runtime shielding (freeRASP)"}[t]
    for dev in DEVS:
        row = {"Device": DEV[dev]}
        for j, (_rk, _rlab) in enumerate(_R4_BODY, start=1):
            r = _r4_get(_rk, t, dev)
            row[_rlab] = P.signed(r.difference, 1)
            if r.significant:
                _r4_bold.add((len(_r4_tab), j))
                if r.difference > 0:                                 # a detected increase: more memory, threads or processor use
                    _r4_shade.add((len(_r4_tab), j))
        _r4_tab.append(row)
_r4_tab = pd.DataFrame(_r4_tab)
T.register(T.write_tex(_r4_tab, "table_rq4", caption=(
    "RQ4: mean difference from the control over the matched pairs, per device: memory (PSS, active phase, \\%), threads that "
    "log (count), device-wide processor share in the active phase (points) and passive-phase length (\\%). Bold: BH-adjusted "
    "$p < 0.05$; shaded: a detected increase."), label="tab:rq4", colfmt="lrrrr", blocks=_r4_blocks,
    star=False, colsep="4pt", bold_cells=_r4_bold, shade_cells=_r4_shade),
    "BODY", "6 Results (RQ4)", "RQ4 body table: mean difference per technique x device in memory (active, %), threads (count), "
    "CPU (active, points), passive length (%); bold = BH < 0.05 within RQ4", NB)
print(_r4_tab.to_string(index=False))

# why the processor share is compared in the active phase only: how often the device-wide figure changes inside the
# passive phase (mechanism_cells_series.csv, ar_cpu_changes_post_launch > 0), per device, on the passive-phase measurements
_r4_m = pd.read_csv(T.DATA / "inputs" / "mechanism_cells_series.csv", dtype={"rep": str})
_r4_ref = series.merge(_r4_m[key + ["ar_cpu_changes_post_launch"]], on=key, how="left", validate="one_to_one")
_r4_ref = _r4_ref[~_r4_ref["app"].isin(T.PASSIVE_EXCLUDED_APPS) & (_r4_ref["pre_workload_joules"] > 0)]
_r4_ref = _r4_ref.dropna(subset=["ar_cpu_changes_post_launch"])
_r4_refresh = {DEV[d]: 100 * float((_r4_ref.loc[_r4_ref["phone"] == d, "ar_cpu_changes_post_launch"] > 0).mean()) for d in DEVS}
print("device-wide CPU figure refreshed inside the passive phase (% of measurements):", {k: round(v, 1) for k, v in _r4_refresh.items()})

# the supplementary table: every test of RQ4
_r4_full, _r4_fb, _r4_fblocks = [], set(), {}
for _rk, _rh, _rph, _rcol, _runit in _R4:
    _r4_fblocks[len(_r4_full)] = _rh
    for t in TECHS:
        for dev in DEVS:
            r = _r4_get(_rk, t, dev)
            _r4_full.append({"Technique": t, "Device": DEV[dev], "Pairs": int(r.n),
                             "Difference [95% CI]": f"{P.signed(r.difference, 2)} {P.ci_text(r.ci_lo, r.ci_hi, 2)}",
                             "Higher": f"{r.higher} of {r.n}", "p (BH)": P.fmt_p(r.p_bh), "Cliff's δ": P.signed(r.cliff, 2)})
            if r.significant:
                _r4_fb.add((len(_r4_full) - 1, 3))
_r4_full = pd.DataFrame(_r4_full)
_paths = T.write_tex(_r4_full, "table_rq4_full", caption=(
    "RQ4 in full: the protected build minus its control on the matched pairs, per characteristic, technique and device, with "
    "the 95\\% interval, the pairs in which the protected build is higher, the BH-adjusted $p$ (within RQ4's 54 tests) and "
    "Cliff's $\\delta$ within pairs. The processor share is given for the active phase only: inside the passive phase the "
    f"device-wide figure changes in only {min(_r4_refresh.values()):.0f} to {max(_r4_refresh.values()):.0f}\\% of the measurements, so it "
    "mostly carries the load from before the phase. Threads are counted once per measurement, over the "
    "whole run, its launch included; the count is a lower bound (threads that never log are not seen). Bold: BH-adjusted "
    "$p < 0.05$."), label="tab:rq4-full", colfmt="llrrrrr", blocks=_r4_fblocks, star=True, bold_cells=_r4_fb)
_f = T.TABLES / "table_rq4_full.tex"
_f.write_text(_f.read_text().replace("\\small\n", "\\footnotesize\n", 1))      # 54 rows: one supplement page
T.register(_paths, "SUPPLEMENT", "Supplement (RQ4)", "RQ4 in full: all 54 tests with intervals, pairs higher, BH p and Cliff's delta", NB)
_sig = rq4[rq4["significant"]]
T.record("rq4", tests=len(rq4), cpu_refreshed_in_passive_pct=_r4_refresh, significant=f"{len(_sig)} of {len(rq4)}", family="BH within RQ4",
         freerasp_threads={DEV[d]: round(float(_r4_get("threads", "freeRASP", d).difference), 2) for d in DEVS},
         freerasp_memory_active_pct={DEV[d]: round(float(_r4_get("memory_active", "freeRASP", d).difference), 1) for d in DEVS},
         bangcle_memory_active_pct={DEV[d]: round(float(_r4_get("memory_active", "Bangcle", d).difference), 1) for d in DEVS},
         freerasp_cpu_active_pp={DEV[d]: round(float(_r4_get("cpu_active", "freeRASP", d).difference), 2) for d in DEVS},
         freerasp_passive_length_pct={DEV[d]: round(float(_r4_get("length_passive", "freeRASP", d).difference), 1) for d in DEVS},
         significant_cells=[f"{r.characteristic}/{r.technique}/{DEV[r.device]}" for r in _sig.itertuples()])
print(f"RQ4: {len(_sig)} of {len(rq4)} significant after BH within RQ4")
print(rq4[["characteristic", "technique", "device", "n", "difference", "ci_lo", "ci_hi", "higher", "p_bh", "cliff"]].round(3).to_string(index=False))

# %% [markdown]
# ## Appendix: per-application results per device
#
# Mean ΔE % of each application's matched pairs (its three series batches) per technique and device; "–": no pair
# (the obfuscation arm's five applications; keep_recipe does not run on the Pixel 3).

# %%
_pa_apps = sorted(pt["app"].unique())
for ph in ("active", "passive"):
    g = pt[pt["phase"] == ph].groupby(["app", "technique", "device"])["delta_pct"].mean()
    tab = pd.DataFrame({"Application": _pa_apps})
    for t in TECHS:
        for dev in DEVS:
            tab[f"{t}_{dev}"] = [f"{P.signed(g[(a, t, dev)], 1)}" if (a, t, dev) in g.index else "–" for a in _pa_apps]
    bold = None
    if ph == "passive":   # medtimer is outside the passive analysis; its runtime-shielding values, as measured, for the Discussion
        gm = pt_pas[(pt_pas["phase"] == "passive") & (pt_pas["app"] == "medtimer") & (pt_pas["technique"] == "freeRASP")
                    ].groupby("device")["delta_pct"].mean()
        im = int(tab.index[tab["Application"] == "medtimer"][0])
        assert (tab.loc[im, tab.columns[1:]] == "–").all(), "medtimer has passive values in the analysis"
        tab.loc[im, "Application"] = "medtimer, as measured"
        for dev in DEVS:
            tab.loc[im, f"freeRASP_{dev}"] = P.signed(gm[dev], 1)
        bold = {(im, 1 + 3 * TECHS.index("freeRASP") + k) for k in range(3)}
    tab.columns = ["Application"] + [DEV[d].replace("Pixel ", "P") for d in DEVS] * 3
    T.register(P.write_tex_grouped(tab, f"table_per_app_{ph}", bold_cells=bold,
                                   caption=f"Per application, {ph} phase: mean $\\Delta E$ (\\%) of its matched pairs (three series batches) per technique and "
                                           "device. –: no pair (packing leaves out medtimer, the obfuscation arm covers five applications, keep\\_recipe has no Pixel~3 cells"
                                           + ("). medtimer is not in the passive-phase analysis, whose phase for it contains its introduction; its row "
                                              "gives its runtime-shielding values from that phase as measured, for the comparison in the Discussion. Bold: the "
                                              "values the thesis states in words." if ph == "passive" else ")."),
                                   label=f"tab:per-app-{ph}", colfmt="l" + "rrr" * 3, groups=[("", 1)] + [(t, 3) for t in TECHS], star=True),
               "SUPPLEMENT", "Supplement (per application)", f"mean delta-E per application x technique x device, {ph} phase", NB)
    print(tab.to_string(index=False))

# %% [markdown]
# ## Discussion: the launch per device (time to first display), freeRASP's burst per device

# %%
_ln = pd.read_csv(T.TABLES / "launch_time.csv")
_ln = _ln[_ln["device"] != "all devices"]
_ln_tab = pd.DataFrame({"Device": _ln["device"], "Pairs": _ln["pairs"],     # one column wide, the control's ms in launch_time.csv
                        "Δ (ms) [95% CI]": [f"{P.signed(m, 0)} {P.ci_text(lo, hi, 0)}" for m, lo, hi in zip(_ln["delta_ms"], _ln["ms_ci_lo"], _ln["ms_ci_hi"])],
                        "Δ (%) [95% CI]": [f"{P.signed(m, 1)} {P.ci_text(lo, hi, 1)}" for m, lo, hi in zip(_ln["delta_pct"], _ln["ci_lo"], _ln["ci_hi"])]})
T.register(T.write_tex(_ln_tab.reset_index(drop=True), "table_launch_per_device",
                       caption="Time to initial display, technique minus control, per device: mean of the matched pairs with its 95\\% paired $t$-interval.",
                       label="tab:launch", colfmt="lrrr", blocks={0: "Bangcle (packing)", 3: "Allatori (obfuscation)", 6: "freeRASP (runtime shielding)"},
                       star=True),      # one column wide, in a page-wide float so it can share the appendix page with Table 8
           "APPENDIX", "Discussion / Appendix (the launch)", "launch time to first display per technique x device", NB)
print(_ln_tab.to_string(index=False))

_bu = pd.read_csv(T.DATA / "inputs" / "mechanism_cells_series.csv", dtype={"rep": str}).merge(series[key], on=key, how="inner")
_bu = _bu[~_bu["app"].isin(T.PASSIVE_EXCLUDED_APPS)]          # the passive-phase analysis set
_bt = pd.read_csv(T.TABLES / "freerasp_burst_timing.csv").set_index("device")
rows = []
for dev in DEVS:
    d = _bu[_bu["phone"] == dev].copy()
    c0, c1 = int(_bt.loc[DEV[dev], "core_from_s"]), int(_bt.loc[DEV[dev], "core_to_s"])
    d["e_core_j"] = d[[f"p_{k:02d}" for k in range(c0, c1)]].sum(axis=1)          # mean power per second x 1 s
    g = d.groupby(["app", "batch", "variant"])[["e_0_4_j", "e_4_15_j", "e_15_end_j", "e_post_launch_j", "e_core_j"]].mean()
    for seg in ("e_core_j", "e_0_4_j", "e_4_15_j", "e_15_end_j", "e_post_launch_j"):
        u = g[seg].unstack("variant")[["freerasp_debug", "baseline_debug"]].dropna()
        m, lo, hi, _ = T.ci_naive(u["freerasp_debug"] - u["baseline_debug"])
        rows.append({"device": DEV[dev], "segment": {"e_core_j": f"core of the burst, {c0}-{c1} s", "e_0_4_j": "0-4 s", "e_4_15_j": "4-15 s",
                                                    "e_15_end_j": "15 s to the end of the phase", "e_post_launch_j": "whole passive phase"}[seg],
                     "pairs": len(u), "extra_j": m, "ci_lo": lo, "ci_hi": hi, "peak_extra_w": _bt.loc[DEV[dev], "peak_delta_w"],
                     "peak_second": int(_bt.loc[DEV[dev], "peak_second_from"])})
burst = pd.DataFrame(rows)
burst.to_csv(T.TABLES / "freerasp_burst_per_device.csv", index=False)
T.register("tables/freerasp_burst_per_device.csv", "REPO", "Discussion (freeRASP's mechanism, exploratory)",
           "freeRASP's extra passive-phase energy per device: in the device's own burst core, by segment, and whole; peak extra power and its second", NB)
print(burst.round(2).to_string(index=False))

# %% [markdown]
# ## Threats and Discussion: three per-device quantities
#
# On the final analysis set: medtimer's packed build out of packing, medtimer out of the passive phase. Each part writes
# one table for the replication package.
#
# **1. The memory sampler's load** (`sampler_load_per_device.csv`, Threats). Android Runner samples memory with
# `dumpsys meminfo <app>`, and the application runs one explicit garbage collection for each call; freeRASP's larger
# heap makes each collection longer, so part of its measured cost is the instrument's. Per device and phase: the extra
# collection time per second, freeRASP minus control (matched pairs, cells with a log; the per-window columns of
# `tools/extract_freerasp_mechanism.py`), times the power that time could draw, at three levels: one CPU
# cluster at its highest reading on the device (one 7-s rail reading, over all analysed cells; an estimate, since a
# brief peak or work spread over clusters can exceed it); every cluster at its highest
# reading at once (conservative); and the whole phone's highest power sample in the phase (the only strict bound, and
# far looser: it includes the screen and every other component). The Pixel 3 logs no rails: the newer phones' cluster
# figures stand in for its own (an assumption, labelled as such), beside its own whole-phone figure. The bound is in
# percentage points of ΔE (bound power × the pair's phase length / the pair's control energy, averaged over the
# device's pairs), beside the device's ΔE; `delta_ci_lo_less_bound_*` is ΔE's lower 95 % limit less the bound taken at
# the upper end of the collection time's interval, so a positive value means the verdict survives that ceiling.
#
# **2. Arrival temperature** (`arrival_temperature_per_device.csv`, Threats). The within-group slope of a cell's power on
# the skin temperature at which it starts, per device and phase, on each phase's analysis set (the active-phase slopes
# are Descriptive Statistics'). The balance: each technique's mean arrival temperature minus its control's over the
# matched pairs, the shift in ΔE that difference implies (difference × slope), and the largest shift within the
# difference's 95 % interval. A check (`arrival_temperature_adjusted_check.csv`): every cell's energy adjusted to its
# device's mean arrival temperature with the device's slope, and the 18 comparisons recomputed.
#
# **3. freeRASP's active-phase cost in absolute terms** (`freerasp_active_cost_per_device.csv`, Discussion). Per device:
# the extra energy per measurement (J) and the extra mean power (W), freeRASP minus control over the matched pairs, with
# the control's energy beside it (the percentage's denominator); the extra device-wide CPU share (Android Runner's
# `dumpsys cpuinfo` TOTAL samples taken in the active phase, percentage points; that figure refreshes rarely and mostly
# carries values from before the phase, see the last part, so it is also given over only the cells whose figure
# refreshed inside the phase); and, as a ratio of means, the percentage each device would show had the extra energy
# stayed at the Pixel 3's. The same over the eight applications measured on all three devices (keep_recipe has no
# Pixel 3 cells): the like-for-like comparison.

# %%
_sb = pd.read_csv(T.DATA / "inputs" / "mechanism_cells_series.csv", dtype={"rep": str}).merge(series[key], on=key, how="inner")
assert len(_sb) == len(series), "an analysed cell is missing from the mechanism input"
assert {"gc_explicit_ms_per_s_workload", "battery_max_w_workload"} <= set(_sb.columns), \
    "re-run tools/extract_freerasp_mechanism.py: the per-window columns are missing"
_sb_log = _sb[_sb["has_log"] == 1].dropna(subset=["gc_explicit_ms_per_s"])
_sb_re = ((_sb_log["gc_explicit_ms_per_s_post_launch"] * _sb_log["post_launch_s"] + _sb_log["gc_explicit_ms_per_s_workload"] * _sb_log["workload_s"])
          / (_sb_log["post_launch_s"] + _sb_log["workload_s"]))
assert (_sb_re - _sb_log["gc_explicit_ms_per_s"]).abs().max() < 1e-3, "the two windows' collection time does not add up to the whole"
_SB_W = {"active": "workload", "passive": "post_launch"}
_t_idx = tests.set_index(["phase", "technique", "device"])


def _arm_pairs(d, col, arm="freerasp_debug"):
    """The arm minus control per matched pair (application x batch, on one device), over the cells with a value."""
    g = d.dropna(subset=[col]).groupby(["app", "batch", "variant"])[col].mean().unstack("variant")
    g = g.reindex(columns=[arm, "baseline_debug"]).dropna()
    return g[arm] - g["baseline_debug"], g["baseline_debug"]


_fr_pairs = _arm_pairs          # freeRASP, the default


def _p_bh_with(ph, t, dev, deltas):
    """The comparison's Benjamini-Hochberg p within its research question's nine, had its pairs' delta-E been `deltas`."""
    fam = tests[tests["phase"] == ph].copy()
    this = (fam["technique"] == t) & (fam["device"] == dev)
    fam.loc[this, "p"] = P.wilcoxon(deltas)[1]
    return float(P.bh(fam["p"].to_numpy())[np.flatnonzero(this.to_numpy())[0]])


rows = []
for t in TECHS:
    for ph in P.PHASES:
        w = _SB_W[ph]
        for dev in DEVS:
            d_all = _sb[_sb["phone"] == dev]
            if ph == "passive":
                d_all = d_all[~d_all["app"].isin(T.PASSIVE_EXCLUDED_APPS)]
            d = d_all[d_all["has_log"] == 1]
            x, base = _arm_pairs(d, f"gc_explicit_ms_per_s_{w}", ARM_OF[t])
            m, lo, hi, _ = T.ci_naive(x)
            xn, basen = _arm_pairs(d, f"gc_explicit_per_s_{w}", ARM_OF[t])
            _cl = d[["rail_max_cpu_big", "rail_max_cpu_mid", "rail_max_cpu_little"]].max()     # each cluster's highest 7-s reading
            prs = pt[(pt["technique"] == t) & (pt["phase"] == ph) & (pt["device"] == dev)]
            pp_per_w = (100 * prs["treated_s"] / prs["control_j"]).mean()       # percentage points of delta-E per watt held
            te = _t_idx.loc[(ph, t, dev)]
            rows.append(dict(technique=t, phase=ph, device=DEV[dev], pairs=len(prs), pairs_with_log=len(x),
                             control_collections_per_s=basen.mean(), extra_collections_per_s=xn.mean(),
                             extra_collections_ci_lo=T.ci_naive(xn)[1], extra_collections_ci_hi=T.ci_naive(xn)[2],
                             control_collection_ms_per_s=base.mean(), extra_collection_ms_per_s=m, ci_lo=lo, ci_hi=hi,
                             rail_ceiling_w=_cl.max() if dev != "pixel3" else np.nan,
                             rail_sum_ceiling_w=_cl.sum() if dev != "pixel3" else np.nan, phone_ceiling_w=d_all[f"battery_max_w_{w}"].max(),
                             phone_ceiling_p999_w=d_all[f"battery_max_w_{w}"].quantile(0.999),
                             phone_ceiling_median_cell_max_w=d_all[f"battery_max_w_{w}"].median(), pp_of_delta_e_per_w=pp_per_w,
                             delta_pct=te["delta_pct"], delta_ci_lo=te["ci_lo"], delta_ci_hi=te["ci_hi"], p_bh=te["p_bh"],
                             significant=bool(te["significant"])))
sampler = pd.DataFrame(rows)
for t in TECHS:        # the Pixel 3 has no rails: the newer phones' readings as a stand-in (an assumption, labelled as such)
    for ph in P.PHASES:
        k = (sampler["technique"] == t) & (sampler["phase"] == ph)
        i3 = k & (sampler["device"] == "Pixel 3")
        sampler.loc[i3, "standin_ceiling_w"] = sampler.loc[k & ~i3, "rail_ceiling_w"].max()
        sampler.loc[i3, "standin_sum_ceiling_w"] = sampler.loc[k & ~i3, "rail_sum_ceiling_w"].max()
# rail: one cluster at its highest 7-s reading (an estimate of scale: a brief peak, work on several clusters or memory
# power can exceed it); rail_sum: every cluster at its highest reading at once (conservative); phone: the whole phone's
# highest sample (the weakest assumption available, and far looser); standin, standin_sum: the newer phones' rail
# figures, for the Pixel 3
_SB_C = ("rail", "rail_sum", "phone", "standin", "standin_sum")
for c in _SB_C:
    sampler[f"bound_{c}_w"] = sampler["extra_collection_ms_per_s"] / 1000 * sampler[f"{c}_ceiling_w"]
    sampler[f"bound_{c}_pp"] = sampler[f"bound_{c}_w"] * sampler["pp_of_delta_e_per_w"]
    sampler[f"bound_{c}_pp_at_ci_hi"] = sampler["ci_hi"] / 1000 * sampler[f"{c}_ceiling_w"] * sampler["pp_of_delta_e_per_w"]
    sampler[f"bound_{c}_share_of_delta_pct"] = 100 * sampler[f"bound_{c}_pp"] / sampler["delta_pct"]
# does the verdict survive? The test itself (Wilcoxon, BH within the research question) on each pair's delta-E less
# the bound for that pair (the device's extra collection time x the ceiling x the pair's phase length / its control
# energy), at the point estimate of the extra time and at the upper end of its interval; and, for the significant
# comparisons, the ceiling in watts at which the adjusted p would reach 0.05
for i, r in sampler.iterrows():
    prs = pt[(pt["technique"] == r["technique"]) & (pt["phase"] == r["phase"]) & (pt["device"] == {v: k for k, v in DEV.items()}[r["device"]])]
    pp_pair = 100 * prs["treated_s"] / prs["control_j"]
    dev = prs["device"].iloc[0]
    for c in _SB_C:
        if pd.notna(r[f"{c}_ceiling_w"]):
            for tag, ms in (("", r["extra_collection_ms_per_s"]), ("_at_ci_hi", r["ci_hi"])):
                sampler.loc[i, f"p_bh_less_bound_{c}{tag}"] = _p_bh_with(r["phase"], r["technique"], dev,
                                                                       prs["delta_pct"] - ms / 1000 * r[f"{c}_ceiling_w"] * pp_pair)
    if r["significant"]:   # the FIRST ceiling, scanning up from 0 W, at which the effect stops being significant (the test is
        # two-sided: a much larger subtraction would make the difference significantly negative instead); none up to 50 W: inf
        f = lambda W: _p_bh_with(r["phase"], r["technique"], dev, prs["delta_pct"] - r["extra_collection_ms_per_s"] / 1000 * W * pp_pair)
        grid = np.arange(0.0, 50.0 + 1e-9, 0.25)
        k = next((j for j, W in enumerate(grid) if f(W) >= P.ALPHA), None)
        if k is None:
            sampler.loc[i, "breakeven_ceiling_w"] = np.inf
        else:
            lo_w, hi_w = grid[k - 1], grid[k]
            for _ in range(30):
                mid = (lo_w + hi_w) / 2
                lo_w, hi_w = (mid, hi_w) if f(mid) < P.ALPHA else (lo_w, mid)
            sampler.loc[i, "breakeven_ceiling_w"] = hi_w
_frs = sampler[sampler["technique"] == "freeRASP"]
assert (_frs.loc[_frs["phase"] == "active", "pairs"].tolist() == [24, 27, 27]
        and _frs.loc[_frs["phase"] == "passive", "pairs"].tolist() == [21, 24, 24]), "not the final analysis set's pairs"
sampler.to_csv(T.TABLES / "sampler_load_per_device.csv", index=False)
T.register("tables/sampler_load_per_device.csv", "REPO", "8 Threats (instrument load)",
           "the memory sampler's forced collections per technique, device and phase, minus the control: rate, time per second; the "
           "ceilings (one cluster, all clusters, the whole phone; the newer phones' figures standing in for the Pixel 3), the bound in "
           "points of delta-E, and the test re-run with each bound subtracted (BH p; the break-even ceiling)", NB)
print(sampler.round(3).T.to_string())
for r in sampler[(sampler["phase"] == "active") & (sampler["technique"] == "freeRASP")].itertuples():
    T.record(f"threats.sampler_load.{r.device.replace(' ', '').lower()}", phase="active", pairs=r.pairs,
             extra_collection_ms_per_s=r.extra_collection_ms_per_s, ci95=[r.ci_lo, r.ci_hi], rail_ceiling_w=r.rail_ceiling_w,
             phone_ceiling_w=r.phone_ceiling_w, bound_rail_pp=r.bound_rail_pp, bound_phone_pp=r.bound_phone_pp,
             delta_pct=r.delta_pct, bound_rail_share_pct=r.bound_rail_share_of_delta_pct,
             bound_phone_share_pct=r.bound_phone_share_of_delta_pct, standin_ceiling_w=r.standin_ceiling_w,
             bound_standin_pp=r.bound_standin_pp, bound_standin_share_pct=r.bound_standin_share_of_delta_pct,
             rail_sum_ceiling_w=r.rail_sum_ceiling_w, bound_rail_sum_pp=r.bound_rail_sum_pp,
             bound_rail_sum_share_pct=r.bound_rail_sum_share_of_delta_pct, standin_sum_ceiling_w=r.standin_sum_ceiling_w,
             bound_standin_sum_pp=r.bound_standin_sum_pp, bound_standin_sum_share_pct=r.bound_standin_sum_share_of_delta_pct,
             p_bh_less_bound={c: getattr(r, f"p_bh_less_bound_{c}", np.nan) for c in _SB_C},
             p_bh_less_bound_at_ci_hi={c: getattr(r, f"p_bh_less_bound_{c}_at_ci_hi", np.nan) for c in _SB_C},
             breakeven_ceiling_w=getattr(r, "breakeven_ceiling_w", np.nan))
for r in sampler[sampler["phase"] == "active"].itertuples():     # every technique's extra collection time, active phase
    T.record(f"threats.sampler_load.{r.technique.lower()}.{r.device.replace(' ', '').lower()}",
             extra_collection_ms_per_s=r.extra_collection_ms_per_s, ci95=[r.ci_lo, r.ci_hi], pairs=r.pairs)

# %%
_at = series.assign(passive_avg_watts=series["pre_workload_joules"] / series["duration_pre_workload_s"])
_AT_POWER = {"active": ("workload_avg_watts", "workload_joules"), "passive": ("passive_avg_watts", "pre_workload_joules")}
_AT_G = ["app", "phone", "variant", "batch"]
_at_ref = pd.read_csv(T.TABLES / "device_power_vs_arrival_temperature.csv").set_index("device")
_at_bal_ref = pd.read_csv(T.TABLES / "arrival_temperature_balance.csv").set_index(["technique", "device"])
_at_slope, _at_slope_e, _at_slope_b, rows = {}, {}, {}, []
for ph in P.PHASES:
    pw, ecol = _AT_POWER[ph]
    d = _at[_at[ecol] > 0]
    if ph == "passive":
        d = d[~d["app"].isin(T.PASSIVE_EXCLUDED_APPS)]
    d = d.assign(power_dev_pct=100 * (d[pw] / d.groupby(_AT_G)[pw].transform("mean") - 1),
                 energy_dev_pct=100 * (d[ecol] / d.groupby(_AT_G)[ecol].transform("mean") - 1),
                 temp_dev_c=d["arrival_temp_c"] - d.groupby(_AT_G)["arrival_temp_c"].transform("mean"))
    for dev in DEVS:
        v = d[d["phone"] == dev].dropna(subset=["temp_dev_c"])
        s = float(np.polyfit(v["temp_dev_c"], v["power_dev_pct"], 1)[0])
        _at_slope[(ph, dev)] = s
        _at_slope_e[(ph, dev)] = float(np.polyfit(v["temp_dev_c"], v["energy_dev_pct"], 1)[0])   # energy's own, for the check
        for var, vb in v.groupby("variant"):                                                   # one slope per build, for the check
            _at_slope_b[(ph, dev, var)] = float(np.polyfit(vb["temp_dev_c"], vb["power_dev_pct"], 1)[0])
            rows.append(dict(part="slope_by_build", phase=ph, device=DEV[dev], technique=T.name(var), cells_with_temperature=len(vb),
                             slope_pct_per_degC=_at_slope_b[(ph, dev, var)], r=vb["power_dev_pct"].corr(vb["temp_dev_c"])))
        if ph == "active":   # Descriptive Statistics' slope, on the same cells
            assert abs(s - _at_ref.loc[DEV[dev], "power_change_pct_per_degC"]) < 1e-9, (dev, s)
        r = v["power_dev_pct"].corr(v["temp_dev_c"])
        rows.append(dict(part="slope", phase=ph, device=DEV[dev], cells=int((d["phone"] == dev).sum()), cells_with_temperature=len(v),
                         slope_pct_per_degC=s, energy_slope_pct_per_degC=_at_slope_e[(ph, dev)], r=r, r_squared=r ** 2,
                         sd_power_dev_pct=v["power_dev_pct"].std(),
                         sd_temp_dev_c=v["temp_dev_c"].std()))
    gtemp = d.groupby(["app", "phone", "batch", "variant"])["arrival_temp_c"].mean()
    for t in TECHS:
        for dev in DEVS:
            prs = pt[(pt["technique"] == t) & (pt["phase"] == ph) & (pt["device"] == dev)]
            diff = np.array([gtemp.get((a, dev, b, ARM_OF[t]), np.nan) - gtemp.get((a, dev, b, "baseline_debug"), np.nan)
                             for a, b in zip(prs["app"], prs["batch"])])
            assert not np.isnan(diff).any(), ("a pair without an arrival temperature on one side", ph, t, dev)
            m, lo, hi, _ = T.ci_naive(diff)
            if ph == "active":   # Descriptive Statistics' balance, on the same pairs
                assert abs(m - _at_bal_ref.loc[(t, DEV[dev]), "arrival_temp_diff_degC"]) < 1e-9, (t, dev)
            s = _at_slope[(ph, dev)]
            rows.append(dict(part="balance", phase=ph, technique=t, device=DEV[dev], pairs=len(prs), temp_diff_c=m, ci_lo=lo, ci_hi=hi,
                             slope_pct_per_degC=s, implied_shift_pp=m * s, largest_shift_pp=max(abs(lo), abs(hi)) * abs(s),
                             delta_pct=_t_idx.loc[(ph, t, dev), "delta_pct"]))
temperature = pd.DataFrame(rows)
temperature.to_csv(T.TABLES / "arrival_temperature_per_device.csv", index=False)
print(temperature[temperature["part"] == "slope"].dropna(axis=1, how="all").round(3).to_string(index=False))
print(temperature[temperature["part"] == "balance"].dropna(axis=1, how="all").round(3).to_string(index=False))

# the check: every cell's energy adjusted to its device's mean arrival temperature with the device's slope for that phase
# (the power slope, as reported; and, as a variant, energy's own slope, which is shallower where duration also varies)
def _adjusted_tests(slope_of, label):
    a = series.copy()
    for ph in P.PHASES:
        ecol = P.PHASES[ph][0]
        for dev in DEVS:
            tref = series.loc[series["phone"] == dev, "arrival_temp_c"].mean()
            for var in a["variant"].unique():
                sel = (a["phone"] == dev) & (a["variant"] == var) & a["arrival_temp_c"].notna()
                a.loc[sel, ecol] = a.loc[sel, ecol] / (1 + slope_of(ph, dev, var) / 100 * (a.loc[sel, "arrival_temp_c"] - tref))
    return P.run_tests(P.pair_table(a), np.random.default_rng(np.random.SeedSequence([T.SEED, 29])), label)


_tests_adj = _adjusted_tests(lambda ph, dev, var: _at_slope[(ph, dev)], "energies adjusted to the device's mean arrival temperature")
_tests_adj_e = _adjusted_tests(lambda ph, dev, var: _at_slope_e[(ph, dev)], "the same with energy's own slope")
_tests_adj_b = _adjusted_tests(lambda ph, dev, var: _at_slope_b[(ph, dev, var)], "the same with a slope per build")
_ta = tests.merge(_tests_adj, on=["rq", "phase", "technique", "device"], suffixes=("", "_adjusted"))
_ta = _ta.merge(_tests_adj_e[["rq", "phase", "technique", "device", "delta_pct", "significant"]].rename(
    columns={"delta_pct": "delta_pct_adjusted_energy_slope", "significant": "significant_adjusted_energy_slope"}),
    on=["rq", "phase", "technique", "device"])
_ta = _ta.merge(_tests_adj_b[["rq", "phase", "technique", "device", "delta_pct", "significant"]].rename(
    columns={"delta_pct": "delta_pct_adjusted_build_slopes", "significant": "significant_adjusted_build_slopes"}),
    on=["rq", "phase", "technique", "device"])
_ta["ci_width"] = _ta["ci_hi"] - _ta["ci_lo"]
_ta["ci_width_adjusted"] = _ta["ci_hi_adjusted"] - _ta["ci_lo_adjusted"]
_ta["ci_width_change_pct"] = 100 * (_ta["ci_width_adjusted"] / _ta["ci_width"] - 1)
_ta["delta_shift_pp"] = _ta["delta_pct_adjusted"] - _ta["delta_pct"]
_ta = _ta[["rq", "phase", "technique", "device", "n", "delta_pct", "delta_pct_adjusted", "delta_shift_pp", "ci_lo", "ci_hi", "ci_lo_adjusted",
           "ci_hi_adjusted", "ci_width", "ci_width_adjusted", "ci_width_change_pct", "p_bh", "p_bh_adjusted", "significant", "significant_adjusted",
           "delta_pct_adjusted_energy_slope", "significant_adjusted_energy_slope", "delta_pct_adjusted_build_slopes",
           "significant_adjusted_build_slopes"]]
_ta.to_csv(T.TABLES / "arrival_temperature_adjusted_check.csv", index=False)
T.register(["tables/arrival_temperature_per_device.csv", "tables/arrival_temperature_adjusted_check.csv"], "REPO",
           "8 Threats (measurement precision)",
           "per device and phase: power against arrival temperature (within-group slope, r); the balance of each technique against its control "
           "with the implied and largest shift in delta-E; the 18 comparisons with energies adjusted for arrival temperature", NB)
print(_ta.round(3).to_string(index=False))
for r in temperature[temperature["part"] == "slope"].itertuples():
    T.record(f"threats.arrival_temperature.{r.phase}.{r.device.replace(' ', '').lower()}", slope_pct_per_degC=r.slope_pct_per_degC, r=r.r,
             cells=r.cells, energy_slope_pct_per_degC=r.energy_slope_pct_per_degC)
_bal = temperature[temperature["part"] == "balance"]
T.record("threats.arrival_temperature.balance", intervals_excluding_zero=int(((_bal["ci_lo"] > 0) | (_bal["ci_hi"] < 0)).sum()),
         comparisons=len(_bal), max_abs_implied_shift_pp=_bal["implied_shift_pp"].abs().max(), max_largest_shift_pp=_bal["largest_shift_pp"].max(),
         verdicts_changed_by_adjustment=int((_ta["significant"] != _ta["significant_adjusted"]).sum()),
         max_abs_delta_shift_pp=_ta["delta_shift_pp"].abs().max(),
         verdicts_changed_with_energy_slope=int((_ta["significant"] != _ta["significant_adjusted_energy_slope"]).sum()),
         max_abs_difference_power_vs_energy_slope_pp=(_ta["delta_pct_adjusted"] - _ta["delta_pct_adjusted_energy_slope"]).abs().max(),
         pixel9_ci_width_change_pct=_ta.loc[_ta["device"] == "pixel9", "ci_width_change_pct"].tolist(),
         pixel9_delta_shift_pp=_ta.loc[_ta["device"] == "pixel9", "delta_shift_pp"].tolist(),
         verdicts_changed_with_build_slopes=int((_ta["significant"] != _ta["significant_adjusted_build_slopes"]).sum()),
         max_abs_shift_build_slopes_pp=(_ta["delta_pct_adjusted_build_slopes"] - _ta["delta_pct"]).abs().max())

# %%
_common8 = sorted(pt.loc[(pt["technique"] == "freeRASP") & (pt["phase"] == "active") & (pt["device"] == "pixel3"), "app"].unique())
rows = []
for subset in ("all applications", "the eight measured on all three devices"):
    for dev in DEVS:
        p_ = pt[(pt["technique"] == "freeRASP") & (pt["phase"] == "active") & (pt["device"] == dev)]
        c_ = _sb[_sb["phone"] == dev]
        if subset != "all applications":
            p_, c_ = p_[p_["app"].isin(_common8)], c_[c_["app"].isin(_common8)]
        m, lo, hi, _ = T.ci_naive(p_["treated_j"] - p_["control_j"])
        mw, wlo, whi, _ = T.ci_naive(p_["treated_j"] / p_["treated_s"] - p_["control_j"] / p_["control_s"])
        x, base = _fr_pairs(c_, "ar_cpu_workload")
        mc, clo, chi, _ = T.ci_naive(x)
        xr, _ = _fr_pairs(c_[c_["ar_cpu_changes_workload"] > 0], "ar_cpu_workload")   # only cells whose CPU figure refreshed in the phase
        mr, rlo, rhi, _ = T.ci_naive(xr)
        rows.append(dict(subset=subset, device=DEV[dev], pairs=len(p_), applications=p_["app"].nunique(), control_j=p_["control_j"].mean(),
                         extra_j=m, extra_j_ci_lo=lo, extra_j_ci_hi=hi, delta_pct=p_["delta_pct"].mean(),
                         ratio_of_means_pct=100 * m / p_["control_j"].mean(), control_s=p_["control_s"].mean(),
                         extra_s=(p_["treated_s"] - p_["control_s"]).mean(), control_w=(p_["control_j"] / p_["control_s"]).mean(),
                         extra_w=mw, extra_w_ci_lo=wlo, extra_w_ci_hi=whi, cpu_pairs=len(x), control_cpu_pct=base.mean(),
                         extra_cpu_pp=mc, extra_cpu_ci_lo=clo, extra_cpu_ci_hi=chi, cpu_refreshed_pairs=len(xr),
                         extra_cpu_pp_refreshed_only=mr, extra_cpu_refreshed_ci_lo=rlo, extra_cpu_refreshed_ci_hi=rhi))
cost = pd.DataFrame(rows)
for subset, g in cost.groupby("subset"):
    cost.loc[g.index, "pct_had_extra_j_stayed_at_pixel3"] = 100 * g.loc[g["device"] == "Pixel 3", "extra_j"].iloc[0] / g["control_j"]
for subset, g in cost.groupby("subset"):     # the share of the fall from the Pixel 3 that the larger denominator explains,
    p3 = g[g["device"] == "Pixel 3"].iloc[0]  # in both orders of decomposition and their average (the Shapley value)
    s3 = p3["extra_j"] / p3["control_j"]
    for i, r in g[g["device"] != "Pixel 3"].iterrows():
        sd = r["extra_j"] / r["control_j"]
        den_first = (s3 - p3["extra_j"] / r["control_j"]) / (s3 - sd)
        cost_first = (r["extra_j"] / p3["control_j"] - sd) / (s3 - sd)
        cost.loc[i, ["denominator_share_of_fall_denominator_first_pct", "denominator_share_of_fall_cost_first_pct",
                     "denominator_share_of_fall_average_pct"]] = [100 * den_first, 100 * cost_first, 50 * (den_first + cost_first)]
cost["extra_cpu_relative_to_control_pct"] = 100 * cost["extra_cpu_pp"] / cost["control_cpu_pct"]
_c_all = cost[cost["subset"] == "all applications"]
assert np.allclose(_c_all["delta_pct"], [_t_idx.loc[("active", "freeRASP", dev), "delta_pct"] for dev in DEVS]), "not the published delta-E"
assert _c_all["pairs"].tolist() == [24, 27, 27], "not the final analysis set's pairs"
cost.to_csv(T.TABLES / "freerasp_active_cost_per_device.csv", index=False)
T.register("tables/freerasp_active_cost_per_device.csv", "REPO", "7 Discussion (device gradient)",
           "freeRASP's active-phase cost per device in absolute terms: extra J per measurement and extra W with the control's energy, extra "
           "device-wide CPU share (pp), the percentage had the extra energy stayed at the Pixel 3's; all applications and the eight on all three", NB)
print(cost.round(3).T.to_string())
for r in _c_all.itertuples():
    T.record(f"discussion.freerasp_active_cost.{r.device.replace(' ', '').lower()}", pairs=r.pairs, control_j=r.control_j, extra_j=r.extra_j,
             extra_j_ci95=[r.extra_j_ci_lo, r.extra_j_ci_hi], extra_w=r.extra_w, extra_cpu_pp=r.extra_cpu_pp,
             extra_cpu_ci95=[r.extra_cpu_ci_lo, r.extra_cpu_ci_hi], control_cpu_pct=r.control_cpu_pct, ratio_of_means_pct=r.ratio_of_means_pct,
             pct_had_extra_j_stayed_at_pixel3=r.pct_had_extra_j_stayed_at_pixel3, extra_cpu_pp_refreshed_only=r.extra_cpu_pp_refreshed_only,
             extra_cpu_refreshed_ci95=[r.extra_cpu_refreshed_ci_lo, r.extra_cpu_refreshed_ci_hi], cpu_refreshed_pairs=r.cpu_refreshed_pairs)
_c8 = cost[cost["subset"] != "all applications"].set_index("device")
T.record("discussion.freerasp_active_cost.eight_common_apps", extra_j=_c8["extra_j"].tolist(), control_j=_c8["control_j"].tolist(),
         ratio_of_means_pct=_c8["ratio_of_means_pct"].tolist(), pct_had_extra_j_stayed_at_pixel3=_c8["pct_had_extra_j_stayed_at_pixel3"].tolist(),
         devices=list(_c8.index), denominator_share_of_fall_pct={k: _c8[f"denominator_share_of_fall_{k}_pct"].tolist()[1:]
                                                                  for k in ("denominator_first", "cost_first", "average")})

# the steps between devices, matched on application x batch over the eight applications measured on all three
_dd = pt[(pt["technique"] == "freeRASP") & (pt["phase"] == "active") & pt["app"].isin(_common8)]
_ddj = _dd.assign(extra_j=_dd["treated_j"] - _dd["control_j"]).pivot_table(index=["app", "batch"], columns="device", values="extra_j")
_cp = _sb[_sb["app"].isin(_common8)].groupby(["app", "batch", "phone", "variant"])["ar_cpu_workload"].mean().unstack("variant")
_ddc = (_cp["freerasp_debug"] - _cp["baseline_debug"]).unstack("phone")
rows = []
for meas, wide, unit in (("extra energy per measurement", _ddj, "J"), ("extra device-wide CPU share", _ddc, "percentage points")):
    for a, b in (("pixel3", "pixel6"), ("pixel3", "pixel9"), ("pixel6", "pixel9")):
        dd = (wide[a] - wide[b]).dropna()
        m, lo, hi, _ = T.ci_naive(dd)
        _, pw, meth = P.wilcoxon(dd)
        rows.append(dict(measure=meas, unit=unit, comparison=f"{DEV[a]} minus {DEV[b]}", pairs=len(dd), delta=m, ci_lo=lo, ci_hi=hi,
                         p_wilcoxon=pw, p_method=meth))
cost_steps = pd.DataFrame(rows)
cost_steps.to_csv(T.TABLES / "freerasp_active_cost_device_steps.csv", index=False)
T.register("tables/freerasp_active_cost_device_steps.csv", "REPO", "7 Discussion (device gradient)",
           "the steps in freeRASP's active-phase cost between devices (extra J; extra device-wide CPU share), matched on application x "
           "batch over the eight applications measured on all three: mean, t-interval, Wilcoxon p", NB)
print(cost_steps.round(3).to_string(index=False))
for r in cost_steps.itertuples():
    T.record(f"discussion.freerasp_active_cost.step.{r.unit.split()[0].lower()}.{r.comparison.replace(' minus ', '_minus_').replace(' ', '').lower()}",
             delta=r.delta, ci95=[r.ci_lo, r.ci_hi], p_wilcoxon=r.p_wilcoxon, pairs=r.pairs)

# %% [markdown]
# ## Supplement: the tables behind the numbers the text gives in words
#
# The thesis text points to these tables instead of giving every number; every value the text states in words
# is set in bold, so a reader can go from the sentence to the number. Built from the tables above and from Mechanism
# Analysis' (`freerasp_postlaunch_segments.csv`, `freerasp_local_probes.csv`, `freerasp_wifi_rate.csv`,
# `auditd_per_device.csv`) and Phase Analysis' (`fuel_gauge_resolution.csv`) outputs, which run before this notebook.

# %%
_BOLD = "Bold: the values the thesis states in words."
_C3 = [DEV[d] for d in DEVS]


def _ci(m, lo, hi, d=1):
    return f"{P.signed(m, d)} {P.ci_text(lo, hi, d)}"


def _rows(cols):
    rows = []
    return rows, (lambda label, vals: rows.append(dict(zip(cols, [label, *vals]))))


# --- runtime shielding per device: the burst, the rejected local requests, Wi-Fi, SELinux denials ---------------------
_seg = pd.read_csv(T.TABLES / "freerasp_postlaunch_segments.csv")
_seg8 = _seg[(_seg["technique"] == "freeRASP") & (_seg["group"] == "the eight other than medtimer")].set_index("segment")
_lp = pd.read_csv(T.TABLES / "freerasp_local_probes.csv")
_lp = _lp[_lp["application"] == "the eight other than medtimer"].set_index(["device", "build"])
_wf = pd.read_csv(T.TABLES / "freerasp_wifi_rate.csv").set_index("device")
_ad = pd.read_csv(T.TABLES / "auditd_per_device.csv").set_index(["device", "technique"])
_bc = {dv: burst[(burst["device"] == dv) & burst["segment"].str.startswith("core")].iloc[0] for dv in _C3}
_bw = {dv: burst[(burst["device"] == dv) & (burst["segment"] == "whole passive phase")].iloc[0] for dv in _C3}
_pc, _pw = _seg8.loc["4-15 s (the burst)"], _seg8.loc["whole passive phase"]
assert all(abs(_bw[dv]["extra_j"] - _seg[(_seg["group"] == dv) & (_seg["segment"] == "whole passive phase")
                                       & (_seg["technique"] == "freeRASP")]["delta"].iloc[0]) < 1e-6 for dv in _C3), \
    "the per-device burst and Mechanism Analysis' segments disagree"
_OTHER = ("Control", "Bangcle", "Allatori")
for dv in _C3 + ["all devices"]:
    assert all(_lp.loc[(dv, b), "per_cell_post_launch"] == 0 for b in _OTHER if (dv, b) in _lp.index), dv
assert all((_ad.loc[(dv, b), "with_auditd_main_process"] == 0) for dv in _C3 for b in _OTHER), "a main-process denial outside freeRASP"
_cols = ["", *_C3, "All devices"]
rows, add = _rows(_cols)
add("Pairs", [int(_bc[dv]["pairs"]) for dv in _C3] + [int(_pc["n_pairs"])])
add("Core of the burst (s)", [_bc[dv]["segment"].split(", ")[1].replace(" s", "").replace("-", "–") for dv in _C3] + ["4–15"])
add("Extra energy in the core (J)", [_ci(_bc[dv]["extra_j"], _bc[dv]["ci_lo"], _bc[dv]["ci_hi"]) for dv in _C3]
    + [_ci(_pc["delta"], _pc["ci_lo"], _pc["ci_hi"])])
add("Extra energy, whole phase (J)", [_ci(_bw[dv]["extra_j"], _bw[dv]["ci_lo"], _bw[dv]["ci_hi"]) for dv in _C3]
    + [_ci(_pw["delta"], _pw["ci_lo"], _pw["ci_hi"])])
_share = [100 * _bc[dv]["extra_j"] / _bw[dv]["extra_j"] for dv in _C3] + [100 * _pc["delta"] / _pw["delta"]]
add("The core's share of the extra energy (%)", [f"{v:.0f}" for v in _share])
i_lp = len(rows)
add("freeRASP, per measurement", [f"{_lp.loc[(dv, 'freeRASP'), 'per_cell_post_launch']:.2f}" for dv in _C3]
    + [f"{_lp.loc[('all devices', 'freeRASP'), 'per_cell_post_launch']:.2f}"])
add("freeRASP, measurements with any (%)", [f"{_lp.loc[(dv, 'freeRASP'), 'cells_with_any_post_launch_pct']:.0f}" if
                                             _lp.loc[(dv, 'freeRASP'), 'per_cell_post_launch'] > 0 else "0" for dv in _C3]
    + [f"{_lp.loc[('all devices', 'freeRASP'), 'cells_with_any_post_launch_pct']:.0f}"])
add("Every other build, per measurement", ["0"] * 4)
i_wf = len(rows)
add("Pairs", [int(_wf.loc[dv, "n_pairs"]) for dv in _C3] + [int(_wf.loc["all devices", "n_pairs"])])
add("Control (kB/s)", [f"{_wf.loc[dv, 'control_mean']:.0f}" for dv in _C3 + ["all devices"]])
add("freeRASP minus control (kB/s)", [_ci(_wf.loc[dv, "delta"], _wf.loc[dv, "ci_lo"], _wf.loc[dv, "ci_hi"], 2) for dv in _C3 + ["all devices"]])
i_ad = len(rows)
_ad_fr = [(int(_ad.loc[(dv, "freeRASP"), "with_auditd_main_process"]), int(_ad.loc[(dv, "freeRASP"), "cells"])) for dv in _C3]
_ad_ot = [(0, int(sum(_ad.loc[(dv, b), "cells"] for b in _OTHER if (dv, b) in _ad.index))) for dv in _C3]
add("freeRASP: with a denial", [f"{a} of {n}" for a, n in _ad_fr] + [f"{sum(a for a, _ in _ad_fr)} of {sum(n for _, n in _ad_fr)}"])
add("Other builds: from the main process", [f"{a} of {n}" for a, n in _ad_ot] + [f"0 of {sum(n for _, n in _ad_ot)}"])
assert all(a == n for a, n in _ad_fr), "a shielded measurement without a denial from the library"
mech = pd.DataFrame(rows)
_mb = ({(4, j) for j in range(1, 5)} | {(i_lp, 2), (i_lp, 3)} | {(i_wf + 2, j) for j in range(1, 4)}
       | {(i_ad, j) for j in range(1, 5)} | {(i_ad + 1, j) for j in range(1, 5)})
T.register(T.write_tex(mech, "table_freerasp_mechanism", caption=(
    "Runtime shielding per device, passive phase unless stated (exploratory, not pre-specified): freeRASP's burst of extra power, the "
    "requests other applications' local web servers rejected, Wi-Fi traffic, and SELinux denials of the library's file and directory "
    "accesses. Differences: freeRASP minus control, mean of matched pairs with its 95\\% paired $t$-interval. " + _BOLD),
    label="tab:freerasp-mechanism", colfmt="lrrrr", bold_cells=_mb,
    blocks={0: "The burst: the eight applications of the passive-phase analysis",
            i_lp: "Requests rejected by other applications' local web servers (the eight applications)",
            i_wf: "Wi-Fi traffic over the whole measurement (all nine applications)",
            i_ad: "SELinux denials of file and directory accesses (all analysed measurements)"},
    notes="Core of the burst: each device's own window of highest extra power; for all devices together, 4-15 s. "
          "The rejected requests are logged by two unrelated applications on the devices; the Pixel 3 logs none."),
    "SUPPLEMENT", "Supplement (runtime shielding per device, exploratory)",
    "freeRASP per device: the burst's core and its share of the passive phase's extra energy, rejected local-server requests, "
    "Wi-Fi traffic, SELinux denials", NB)
print(mech.to_string(index=False))

# --- freeRASP's active-phase cost per device, and how much of its fall in percent the larger control explains ----------
_ca = cost[cost["subset"] == "all applications"].set_index("device")
_c8x = cost[cost["subset"] != "all applications"].set_index("device")
rows, add = _rows(["", *_C3])
add("Pairs", [int(_ca.loc[dv, "pairs"]) for dv in _C3])
add("Control energy (J)", [f"{_ca.loc[dv, 'control_j']:.1f}" for dv in _C3])
add("Extra energy (J)", [_ci(_ca.loc[dv, "extra_j"], _ca.loc[dv, "extra_j_ci_lo"], _ca.loc[dv, "extra_j_ci_hi"]) for dv in _C3])
add("ΔE (%)", [P.signed(_ca.loc[dv, "delta_pct"], 2) for dv in _C3])
i8 = len(rows)
add("Pairs", [int(_c8x.loc[dv, "pairs"]) for dv in _C3])
add("Control energy (J)", [f"{_c8x.loc[dv, 'control_j']:.1f}" for dv in _C3])
_rel = [100 * (_c8x.loc[dv, "control_j"] / _c8x.loc["Pixel 3", "control_j"] - 1) for dv in _C3]
add("Control energy against the Pixel 3's (%)", ["–"] + [P.signed(v, 1) for v in _rel[1:]])
add("Extra energy (J)", [P.signed(_c8x.loc[dv, "extra_j"], 1) for dv in _C3])
add("Extra energy as a share of the control's (%)", [f"{_c8x.loc[dv, 'ratio_of_means_pct']:.1f}" for dv in _C3])
add("The same, had the extra energy stayed at the Pixel 3's (%)", [f"{_c8x.loc[dv, 'pct_had_extra_j_stayed_at_pixel3']:.1f}" for dv in _C3])
_fall = []
for dv in _C3[1:]:
    a, b, m = (_c8x.loc[dv, f"denominator_share_of_fall_{k}_pct"] for k in ("cost_first", "denominator_first", "average"))
    lo, hi = sorted((a, b))
    assert hi < 50, f"the larger control explains half or more of the fall on the {dv}"
    _fall.append(f"{m:.0f} ({lo:.0f} to {hi:.0f})")
add("Share of the fall in the percentage that the larger control explains (%)", ["–"] + _fall)
active_cost = pd.DataFrame(rows)
_ab = ({(2, j) for j in (1, 2, 3)} | {(i8 + 2, 2), (i8 + 2, 3)} | {(i8 + 4, j) for j in (1, 2, 3)} | {(i8 + 5, 2), (i8 + 5, 3)}
       | {(i8 + 6, 2), (i8 + 6, 3)})
_st_e = cost_steps[cost_steps["unit"] == "J"].set_index("comparison")
_steps_txt = "; ".join(f"{c}: {P.signed(r.delta, 1)} J {P.ci_text(r.ci_lo, r.ci_hi, 1)}, Wilcoxon p "
                       + (P.fmt_p(r.p_wilcoxon) if P.fmt_p(r.p_wilcoxon).startswith("<") else f"= {P.fmt_p(r.p_wilcoxon)}")
                       for c, r in _st_e.iterrows())
T.register(T.write_tex(active_cost, "table_freerasp_active_cost", caption=(
    "freeRASP's active-phase cost per device in joules, and how much of its fall in percent the newer devices' larger control "
    "energy explains. Extra energy: freeRASP minus control, mean of matched pairs with its 95\\% paired $t$-interval. " + _BOLD),
    label="tab:freerasp-active-cost", colfmt="lrrr", bold_cells=_ab,
    blocks={0: "All applications", i8: "The eight applications measured on all three devices"},
    notes="The share of the fall: the part of the drop from the Pixel 3's percentage that the larger control alone accounts for, "
          "averaged over the two orders of decomposing the change (in brackets, each order). Steps in the extra energy between "
          f"devices, matched on application and batch over the eight applications: {_steps_txt}."),
    "SUPPLEMENT", "Supplement (freeRASP's active-phase cost)",
    "freeRASP's active-phase extra energy per device with the control's energy, and the share of the fall in percent the larger control explains", NB)
print(active_cost.to_string(index=False))

# --- the fuel gauge --------------------------------------------------------------------------------------------------
_fg = pd.read_csv(T.TABLES / "fuel_gauge_resolution.csv").set_index("phone")
rows, add = _rows(["", *_C3])
add("Measurements", [int(_fg.loc[d, "cells"]) for d in DEVS])
add("Median interval between changes (ms)", [f"{_fg.loc[d, 'gauge_update_ms_median']:.0f}" for d in DEVS])
add("Range over the measurements (ms)", [f"{_fg.loc[d, 'gauge_update_ms_min']:.0f}–{_fg.loc[d, 'gauge_update_ms_max']:.0f}" for d in DEVS])
add("Changes per second", [f"{1000 / _fg.loc[d, 'gauge_update_ms_median']:.1f}" for d in DEVS])
add("Readings during one launch, control", [f"{_fg.loc[d, 'gauge_updates_in_launch_median_baseline']:.0f}" for d in DEVS])
add("Readings during one launch, packed", [f"{_fg.loc[d, 'gauge_updates_in_launch_median_bangcle']:.0f}" for d in DEVS])
gauge = pd.DataFrame(rows)
T.register(T.write_tex(gauge, "table_fuel_gauge", caption=(
    "How often the battery's current reading changes, per device: the median interval between successive changes within a "
    "measurement (poetskingdom's analysed measurements, every build), and how many distinct readings one launch spans (median over "
    "poetskingdom's relaunches during its workload). The voltage is read once per measurement. " + _BOLD),
    label="tab:fuel-gauge", colfmt="lrrr", bold_cells={(1, 1), (1, 2), (1, 3), (3, 1), (3, 2), (3, 3)}),
    "SUPPLEMENT", "Supplement (the fuel gauge)", "the fuel gauge's update interval per device, and the readings one launch spans", NB)
print(gauge.to_string(index=False))

# --- arrival temperature -------------------------------------------------------------------------------------------
_sl = temperature[temperature["part"] == "slope"].set_index(["phase", "device"])
_ba = temperature[temperature["part"] == "balance"].set_index(["phase", "technique", "device"])
_tai = _ta.set_index(["phase", "technique", "device"])
_TW = {"Bangcle": "Packing", "Allatori": "Obfuscation", "freeRASP": "Runtime shielding"}
rows, add = _rows(["", *_C3])
for ph in ("active", "passive"):
    add(f"{ph.capitalize()} phase", [P.signed(_sl.loc[(ph, dv), "slope_pct_per_degC"], 2) for dv in _C3])
i_bal = len(rows)
for ph in ("active", "passive"):
    for t in TECHS:
        add(f"{_TW[t]}, {ph}", [_ci(_ba.loc[(ph, t, dv), "temp_diff_c"], _ba.loc[(ph, t, dv), "ci_lo"], _ba.loc[(ph, t, dv), "ci_hi"], 2)
                                for dv in _C3])
i_adj = len(rows)
for ph in ("active", "passive"):
    for t in TECHS:
        add(f"{_TW[t]}, {ph}", [f"{P.signed(_tai.loc[(ph, t, d), 'delta_shift_pp'], 2)} / {round(_tai.loc[(ph, t, d), 'ci_width_change_pct']) + 0:.0f}"
                                for d in DEVS])
temp_tab = pd.DataFrame(rows)
_bal = temperature[temperature["part"] == "balance"]
_tb = {(0, j) for j in (1, 2, 3)}
for k, (ph, t) in enumerate((ph, t) for ph in ("active", "passive") for t in TECHS):
    for j, dv in enumerate(_C3, start=1):
        r = _ba.loc[(ph, t, dv)]
        if r["temp_diff_c"] in (_bal["temp_diff_c"].min(), _bal["temp_diff_c"].max()) or r["ci_lo"] > 0 or r["ci_hi"] < 0:
            _tb.add((i_bal + k, j))
        _tb.add((i_adj + k, 3)) if dv == "Pixel 9" else None
_imax = _ta["delta_shift_pp"].abs().idxmax()
_tb.add((i_adj + [(ph, t) for ph in ("active", "passive") for t in TECHS].index((_ta.loc[_imax, "phase"], _ta.loc[_imax, "technique"])),
         1 + DEVS.index(_ta.loc[_imax, "device"])))
assert (_ta["significant"] == _ta["significant_adjusted"]).all(), "the adjustment changes a verdict: the caption says it does not"
T.register(T.write_tex(temp_tab, "table_arrival_temperature", caption=(
    "Arrival temperature per device: how strongly power depends on it, how far each technique's arrival temperature differs from "
    "its control's, and the comparisons recomputed with every measurement's energy adjusted to its device's mean arrival "
    "temperature, which changes no verdict. " + _BOLD),
    label="tab:arrival-temperature", colfmt="lrrr", bold_cells=_tb,
    blocks={0: "Power change per degree of arrival temperature (% per °C, within groups)",
            i_bal: "Arrival temperature, technique minus control (°C), mean of matched pairs with its 95% paired t-interval",
            i_adj: "With energies adjusted: shift in ΔE (percentage points) / change in the interval's width (%)"}),
    "SUPPLEMENT", "Supplement (arrival temperature)",
    "per device: power against arrival temperature, the balance of each technique against its control, and the adjusted comparisons", NB)
print(temp_tab.to_string(index=False))

# --- the memory sampler's load, active phase ------------------------------------------------------------------------
_sa = sampler[(sampler["technique"] == "freeRASP") & (sampler["phase"] == "active")].set_index("device")
rows, add = _rows(["", *_C3])
add("Pairs", [int(_sa.loc[dv, "pairs"]) for dv in _C3])
add("Extra collections per second", [P.signed(_sa.loc[dv, "extra_collections_per_s"], 2) for dv in _C3])
add("Extra collection time (ms per s)", [_ci(_sa.loc[dv, "extra_collection_ms_per_s"], _sa.loc[dv, "ci_lo"], _sa.loc[dv, "ci_hi"]) for dv in _C3])
_lev = (("rail", "standin", "One processor cluster at its highest reading"),
        ("rail_sum", "standin_sum", "Every cluster at its highest reading at once"),
        ("phone", "phone", "The whole phone's highest power sample"))
_sb_i = {}
for c, c3, head in _lev:
    _sb_i[c] = len(rows)
    pick = lambda dv, col: _sa.loc[dv, col.replace(c, c3, 1) if dv == "Pixel 3" else col]
    add("Power (W)", [f"{pick(dv, f'{c}_ceiling_w'):.2f}" + ("*" if dv == "Pixel 3" and c != "phone" else "") for dv in _C3])
    add("Bound (points of ΔE)", [f"{pick(dv, f'bound_{c}_pp'):.2f}" for dv in _C3])
    add("Bound as a share of ΔE (%)", [f"{pick(dv, f'bound_{c}_share_of_delta_pct'):.0f}" for dv in _C3])
add("BH p with the whole-phone bound subtracted", [P.fmt_p(_sa.loc[dv, "p_bh_less_bound_phone"]) for dv in _C3])
i_end = len(rows)
add("ΔE (%)", [P.signed(_sa.loc[dv, "delta_pct"], 2) for dv in _C3])
add("Power at which the verdict would change (W)", [f"{_sa.loc[dv, 'breakeven_ceiling_w']:.1f}" if _sa.loc[dv, "significant"] else "–"
                                                     for dv in _C3])
sampler_tab = pd.DataFrame(rows)
_sbb = ({(_sb_i["rail"] + 2, j) for j in (1, 2, 3)} | {(_sb_i["rail_sum"] + 2, j) for j in (1, 2, 3)} | {(i_end + 1, 1), (i_end + 1, 2)})
T.register(T.write_tex(sampler_tab, "table_sampler_load", caption=(
    "The memory sampler's extra load on the shielded builds, active phase, per device: the extra garbage-collection time its "
    "forced collections take, freeRASP minus control (mean of matched pairs with its 95\\% paired $t$-interval), and the share of "
    "$\\Delta E$ that time could account for at three assumed powers. " + _BOLD),
    label="tab:sampler-load", colfmt="lrrr", bold_cells=_sbb,
    blocks={0: "Extra collection time, freeRASP minus control", _sb_i["rail"]: _lev[0][2], _sb_i["rail_sum"]: _lev[1][2],
            _sb_i["phone"]: _lev[2][2], i_end: "The comparison"},
    notes="* The Pixel 3 logs no power rails: the newer devices' highest cluster readings stand in for its own. The power at which "
          "the verdict would change: the lowest power that, held during the extra collection time, makes the comparison "
          "non-significant after the Benjamini-Hochberg adjustment; '–': the comparison is not significant."),
    "SUPPLEMENT", "Supplement (the memory sampler's load)",
    "freeRASP's extra collection time per device, active phase, and the share of delta-E it could account for at three powers", NB)
print(sampler_tab.to_string(index=False))

# %% [markdown]
# ### Android Runner's CPU figure: how often it refreshes (Threats)
#
# Android Runner records CPU as the `TOTAL` line of `dumpsys cpuinfo`, about once every 1.3 s. Android refreshes that line
# only now and then, so consecutive samples repeat it, and a phase's samples can all carry a value set before the phase
# began. Per device and build, over the analysed cells (`tools/extract_freerasp_mechanism.py`): the
# cells whose figure never changes during the run, the changes per run, the cells whose active phase holds one value,
# those whose every active-phase sample carries the value from before the phase, and those whose active and passive
# means are equal. The figure is the same for every build, so it does not bias a comparison; it does limit what CPU can
# say about a phase (RQ3, and the CPU share in the cost table above).

# %%
assert {"ar_cpu_changes", "ar_cpu_changes_workload", "ar_cpu_carried_frac_workload"} <= set(_sb.columns), \
    "re-run tools/extract_freerasp_mechanism.py: the CPU columns are missing"


def _cpu_stats(g):
    return {"cells": len(g), "never_changes_pct": 100 * (g["ar_cpu_changes"] == 0).mean(),
            "changes_per_cell_median": g["ar_cpu_changes"].median(),
            "active_one_value_pct": 100 * (g["ar_cpu_changes_workload"] == 0).mean(),
            "active_all_carried_pct": 100 * (g["ar_cpu_carried_frac_workload"] == 1).mean(),
            "active_carried_frac_mean": g["ar_cpu_carried_frac_workload"].mean(),
            "active_equals_passive_pct": 100 * (g["ar_cpu_workload"] == g["ar_cpu_post_launch"]).mean()}


rows = [dict(device="all devices", build="all builds", **_cpu_stats(_sb))]
for dev in DEVS:
    rows.append(dict(device=DEV[dev], build="all builds", **_cpu_stats(_sb[_sb["phone"] == dev])))
    for v in ("baseline_debug",) + T.ARMS:
        rows.append(dict(device=DEV[dev], build=T.name(v), **_cpu_stats(_sb[(_sb["phone"] == dev) & (_sb["variant"] == v)])))
cpu_refresh = pd.DataFrame(rows)
assert cpu_refresh.iloc[0]["cells"] == len(series), "not every analysed cell"
cpu_refresh.to_csv(T.TABLES / "cpu_figure_refresh.csv", index=False)
T.register("tables/cpu_figure_refresh.csv", "REPO", "8 Threats (construct validity: the CPU measure)",
           "how often Android Runner's CPU figure (dumpsys cpuinfo TOTAL) changes: cells never changing, changes per run, cells whose "
           "active phase holds one value or carries the value from before it, active equal to passive; per device and build", NB)
print(cpu_refresh.round(1).to_string(index=False))
_cr = cpu_refresh.iloc[0]
T.record("threats.cpu_figure_refresh", cells=int(_cr["cells"]), never_changes_pct=_cr["never_changes_pct"],
         changes_per_cell_median=_cr["changes_per_cell_median"], active_one_value_pct=_cr["active_one_value_pct"],
         active_all_carried_pct=_cr["active_all_carried_pct"], active_carried_frac_mean=_cr["active_carried_frac_mean"],
         active_equals_passive_pct=_cr["active_equals_passive_pct"],
         never_changes_pct_range_by_device_build=[cpu_refresh.iloc[1:]["never_changes_pct"].min(), cpu_refresh.iloc[1:]["never_changes_pct"].max()])

# %% [markdown]
# ## Appendix: pilot to series repeatability, and the obfuscator's static changes

# %%
_pl = df[df["batch"] == "pilot"].assign(watts=lambda d: d["workload_avg_watts"])
_se = series.assign(watts=series["workload_avg_watts"])


def _cvs(d, col):
    g = d[d[col] > 0].groupby(["app", "phone", "variant"])[col].agg(["count", "mean", lambda x: x.std(ddof=1)])
    g.columns = ["n", "m", "s"]
    g = g[g["n"] >= 3]
    return 100 * g["s"] / g["m"]


rows = []
for lab, col in (("Active-phase energy", "workload_joules"), ("Passive-phase energy", "pre_workload_joules"),
                 ("Active-phase length", "duration_workload_s"), ("Active-phase mean power", "watts")):
    _x = T.PASSIVE_EXCLUDED_APPS if col == "pre_workload_joules" else ()      # the passive phase's analysis set
    a, b = _cvs(_pl[~_pl["app"].isin(_x)], col), _cvs(_se[~_se["app"].isin(_x)], col)
    common = a.index.intersection(b.index)
    rows.append({"Measure": lab, "Groups": len(common), "Pilot median CV (%)": f"{a[common].median():.2f}",
                 "Series median CV (%)": f"{b[common].median():.2f}", "Pilot groups > 15%": f"{int((a[common] > 15).sum())} of {len(common)}",
                 "Series groups > 15%": f"{int((b[common] > 15).sum())} of {len(common)}"})
_ps = pd.DataFrame(rows)

# What changed between the two. The thermal gate's first production run was a resume of the pilot tag, session
# 20260810_001143, which also raised the cell timeout from 480 to 660 s: every pilot
# cell from that session on ran behind the gate, none before it. Every session whose cells are compared here ran with a
# 40-70 % battery band (one early pilot session, before PILOT_CUTOFF, ran with 30-70 %); what differed is the lowest level a
# cell started at.
_PILOT_GATED_SESSION, _PILOT_GATED_FROM_UTC = "20260810_001143", "2026-08-09T22:11:43Z"   # 00:11:43 CEST
_pl_raw = raw[raw["batch"] == "pilot"]
_pl_gated = int((_pl_raw["cell_start_ts_utc"].fillna("") >= _PILOT_GATED_FROM_UTC).sum())


def _gate_check():
    """The session boundary against the stream logs, which record the gate's reading after each cell's START. Raw data
    outside the package's tables: without them the count rests on the session start alone (UNPROVEN, not a pass)."""
    import re as _re, sys as _sys
    try:
        _sys.path.insert(0, str(T.ROOT / "tools"))
        from organise_sweep_logs import resolve_logs_for_tag
        logs = [p for p in resolve_logs_for_tag(T.RUN_TABLES["pilot"].stem.replace("run_table_extended_", ""), kind="stream")
                if _re.search(r"parallel_\d{8}_\d{6}_pixel\d\.log$", p.name)]
    except Exception as exc:
        return f"UNPROVEN: stream logs unavailable ({exc.__class__.__name__})"
    if not logs:
        return "UNPROVEN: no stream logs for the pilot tag"
    before = after = 0
    for p in logs:
        sess, started = _re.search(r"parallel_(\d{8}_\d{6})_", p.name).group(1), False
        for line in p.read_text(errors="replace").splitlines():
            if _re.search(r"START\s+\S+\s+\w+/\w+\s+rep\d+", line):
                started = True
            elif started and "THERMAL pre-cell:" in line:
                started = False
                before, after = (before + 1, after) if sess < _PILOT_GATED_SESSION else (before, after + 1)
    assert before == 0, f"{before} gate readings in pilot sessions before {_PILOT_GATED_SESSION}"
    assert after == _pl_gated, f"stream logs: {after} gated cells; run table from {_PILOT_GATED_FROM_UTC}: {_pl_gated}"
    return f"OK: {after} gated pilot cells in the stream logs, none before session {_PILOT_GATED_SESSION}"


print("thermal gate in the pilot:", _gate_check())
_lvl = pd.concat(pd.read_csv(T.RUN_TABLES[b], usecols=["phone", "app", "variant", "rep", "battery_level_pre_pct"],
                             dtype={"rep": str}).assign(batch=b) for b in T.RUN_TABLES)
_lvl = {g: d.merge(_lvl, on=key, how="left", validate="one_to_one")["battery_level_pre_pct"] for g, d in (("pilot", _pl), ("series", _se))}
assert all(v.notna().all() for v in _lvl.values())
_lowest = {g: int(v.min()) for g, v in _lvl.items()}
print(f"lowest battery level a cell started at: pilot cells used {_lowest['pilot']} %, series {_lowest['series']} %")

T.register(T.write_tex(_ps, "table_pilot_series", caption=(
    "Repeatability, pilot against series: median within-group coefficient of variation over the groups (application, device, build) "
    "with at least three valid measurements in both, and the groups above 15\\%. Among the things that changed between the two are the "
    f"thermal gate and the longer measurement timeout, which only the pilot's last {_pl_gated} measurements had, and the lowest battery "
    "level at which measurements started, so this comparison alone cannot credit the reduction to any one of them. The thermal-gate "
    "table tests cooler starts inside the pilot. The pilot measurements from before the external supply was fitted are not used. The "
    "passive-phase row leaves out medtimer, as the passive-phase analysis does. Bold: the values the thesis states in words."),
    label="tab:pilot-series", colfmt="lrrrrr", star=True,
    bold_cells={(int(_ps.index[_ps["Measure"] == "Active-phase energy"][0]), j) for j in (2, 3)}), "SUPPLEMENT", "Supplement (pilot to series)", "pilot vs series repeatability over the common groups", NB)
print(_ps.to_string(index=False))
T.record("pilot_series", **{r["Measure"]: [r["Pilot median CV (%)"], r["Series median CV (%)"], r["Pilot groups > 15%"], r["Series groups > 15%"]]
                            for r in rows},
         gated_pilot_cells=_pl_gated, pilot_cells=len(_pl_raw), lowest_start_battery_pct=_lowest,
         basis="pilot cells from the external supply on (PILOT_CUTOFF); gated: from session 20260810_001143 on")

# The thermal gate: what it changed. Exploratory.
# (1) Charging failures: phone streams the harness stopped at its battery safety floor (a SAFETY_MIN line in a stream log),
# per regime; the pilot ran without the gate until its last 62 cells, the series with it. (2) The battery temperature a cell
# started at: the run table's battery temperature, which the frozen battery broadcast holds at one value for the whole cell,
# read before it starts (the pilot's thermal_* columns are series-1 copies and are never used). (3) A test inside the pilot,
# whose cells ran without the gate apart from its last 62 (leaving those out changes little): keep each group's
# coolest-starting 74 % of cells, a share fixed in August, which drops each group's hottest quarter and leaves a
# start-temperature 95th percentile close to the series', and compare the median within-group CV with the series' and
# with a random 74 % of the same groups. If cooler starts are what tightens the measurement, the coolest subset lands
# near the series and the random one does not. The gate itself does not drop cells: it rests a phone that arrives at
# moderate throttling or worse, then measures it (about one series cell in six).
def _floor_stops(batch):
    import re as _re, sys as _sys
    _sys.path.insert(0, str(T.ROOT / "tools"))
    from organise_sweep_logs import resolve_logs_for_tag
    logs = [q for q in resolve_logs_for_tag(T.RUN_TABLES[batch].stem.replace("run_table_extended_", ""), kind="stream")
            if _re.search(r"parallel_\d{8}_\d{6}_pixel\d\.log$", q.name)]
    txt = {q: q.read_text(errors="replace") for q in logs}
    stops = [q for q in logs if "SAFETY_MIN" in txt[q]]
    after = lambda q: q.name.split("_")[1] >= T.PILOT_CUTOFF.replace("-", "")
    # top-ups a phone refused (VBUS present, the phone not charging), and how many refusal episodes ended in a successful
    # top-up ("band reached") rather than another refusal or the end of the stream
    refused = recovered = 0
    for q in logs:
        if not after(q):
            continue
        open_ = False
        for line in txt[q].splitlines():
            if "REFUSED: VBUS is present" in line:
                refused += 1
                open_ = True
            elif "band reached" in line and open_:
                recovered += 1
                open_ = False
    return dict(logs=len(logs), stops=len(stops), logs_after=sum(after(q) for q in logs), stops_after=sum(after(q) for q in stops),
                refused_after=refused, recovered_after=recovered,
                gate_lines=sum(len(_re.findall(r"THERMAL", txt[q])) for q in logs),
                still_hot=sum(len(_re.findall(r"STILL HOT after", txt[q])) for q in logs),
                unproven=sum(len(_re.findall(r"UNPROVEN", txt[q])) for q in logs))


_fs = {b: _floor_stops(b) for b in T.RUN_TABLES}
assert _fs["pilot"]["logs"] > 0 and all(_fs[b]["logs"] > 0 for b in T.SERIES), "stream logs missing: the stop counts would read zero"
assert all(_fs[b]["gate_lines"] > 0 for b in T.SERIES), "no gate line in a series batch's stream logs: the give-up count would read zero"
_fs_series = {k: sum(_fs[b][k] for b in T.SERIES) for k in _fs["pilot"]}
print("stream logs:", {b: _fs[b] for b in _fs})
# the thermal status each analysed series measurement arrived at (the run table's reading before any rest; the pilot's
# thermal_* columns are series-1 copies and are never used): only arrivals at moderate throttling or worse (>= 2) are rested
_ts = pd.concat(pd.read_csv(T.RUN_TABLES[b], usecols=["phone", "app", "variant", "rep", "thermal_status_pre"], dtype={"rep": str}).assign(batch=b)
                for b in T.SERIES)
_ts = pd.to_numeric(_se.merge(_ts, on=key, how="left", validate="one_to_one")["thermal_status_pre"], errors="coerce")
assert _ts.notna().all() and _ts.between(0, 6).all(), "an analysed series measurement without a thermal status"
_ts_share = {lab: 100 * float(m.mean()) for lab, m in (("none", _ts == 0), ("light", _ts == 1), ("rested", _ts >= 2))}
print(f"thermal status on arrival, {len(_ts)} analysed series measurements:", {k: round(v, 1) for k, v in _ts_share.items()})
_bt = pd.concat(pd.read_csv(T.RUN_TABLES[b], usecols=["phone", "app", "variant", "rep", "battery_temp_avg_c", "battery_temp_max_c"],
                            dtype={"rep": str}).assign(batch=b) for b in T.RUN_TABLES)
assert (_bt["battery_temp_max_c"] - _bt["battery_temp_avg_c"]).abs().max() < 1e-6, "the battery temperature is not one value per cell"
_tg_pl = _pl.merge(_bt, on=key, how="left", validate="one_to_one")
_tg_se = _se.merge(_bt, on=key, how="left", validate="one_to_one")
assert (_tg_pl["battery_temp_avg_c"] > 0).all() and (_tg_se["battery_temp_avg_c"] > 0).all(), "a cell without a battery temperature"
_TG_KEEP, _TG_DRAWS = 0.74, 2000
_tg_rng = np.random.default_rng(np.random.SeedSequence([T.SEED, 34]))


def _tg_test(col):
    cv = lambda x: 100 * np.std(x, ddof=1) / np.mean(x)
    kn = lambda n: min(n, max(3, int(round(_TG_KEEP * n))))
    gp = {k: g for k, g in _tg_pl[_tg_pl[col] > 0].groupby(["app", "phone", "variant"]) if len(g) >= 3}
    gs = {k: g for k, g in _tg_se[_tg_se[col] > 0].groupby(["app", "phone", "variant"]) if len(g) >= 3}
    common = sorted(set(gp) & set(gs))
    p_all = np.median([cv(gp[k][col].to_numpy()) for k in common])
    s_all = np.median([cv(gs[k][col].to_numpy()) for k in common])
    cool = np.median([cv(gp[k].sort_values("battery_temp_avg_c", kind="stable")[col].to_numpy()[:kn(len(gp[k]))]) for k in common])
    arrs = [gp[k][col].to_numpy() for k in common]
    draws = np.array([np.median([cv(a[_tg_rng.choice(len(a), kn(len(a)), replace=False)]) for a in arrs]) for _ in range(_TG_DRAWS)])
    gap = p_all - s_all
    return dict(groups=len(common), pilot=p_all, series=s_all, coolest=cool, random=draws.mean(), random_lo=np.quantile(draws, 0.025),
                random_hi=np.quantile(draws, 0.975), closed_coolest_pct=100 * (p_all - cool) / gap,
                closed_random_pct=100 * (p_all - draws.mean()) / gap)


_tg_e, _tg_d = _tg_test("workload_joules"), _tg_test("duration_workload_s")


def _tg_unrested(col):
    """median within-group CV of the series measurements the gate did not rest (arrival status < 2), over the test's groups"""
    cv = lambda x: 100 * np.std(x, ddof=1) / np.mean(x)
    se = _tg_se.assign(_st=_ts.to_numpy())
    gp = {k for k, g in _tg_pl[_tg_pl[col] > 0].groupby(["app", "phone", "variant"]) if len(g) >= 3}
    gs = {k: g for k, g in _tg_se[_tg_se[col] > 0].groupby(["app", "phone", "variant"]) if len(g) >= 3}
    gu = {k: g for k, g in se[(se[col] > 0) & (se["_st"] < 2)].groupby(["app", "phone", "variant"]) if len(g) >= 3}
    common = sorted(gp & set(gs) & set(gu))
    return dict(groups=len(common), unrested=float(np.median([cv(gu[k][col].to_numpy()) for k in common])),
                all_series=float(np.median([cv(gs[k][col].to_numpy()) for k in common])))


_tg_u = _tg_unrested("workload_joules")
_kn = lambda n: min(n, max(3, int(round(_TG_KEEP * n))))
_tg_kept = pd.concat(g.sort_values("battery_temp_avg_c", kind="stable").head(_kn(len(g)))
                     for k, g in _tg_pl[_tg_pl["workload_joules"] > 0].groupby(["app", "phone", "variant"]) if len(g) >= 3)
_tg_p95 = dict(kept=float(_tg_kept["battery_temp_avg_c"].quantile(0.95)), series=float(_tg_se["battery_temp_avg_c"].quantile(0.95)))
print("never-rested series measurements:", _tg_u, "| start-temperature p95, kept pilot vs series:", _tg_p95)
assert abs(_tg_e["pilot"] - float(_ps.loc[_ps["Measure"] == "Active-phase energy", "Pilot median CV (%)"].iloc[0])) < 0.006, \
    "the test's groups are not Table S4's"
_tq = {g: d["battery_temp_avg_c"] for g, d in (("pilot", _tg_pl), ("series", _tg_se))}
rows, _tgb, _blk = [], set(), {}
_blk[len(rows)] = "Charging, from the external supply on (stream logs)"
rows.append({"": "Top-ups the phone refused", "Pilot": f"{_fs['pilot']['refused_after']}", "Series": f"{_fs_series['refused_after']}"})
rows.append({"": "refusals followed by a successful top-up", "Pilot": f"{_fs['pilot']['recovered_after']}",
             "Series": f"{_fs_series['recovered_after']}"})
_tgb |= {(len(rows) - 1, 2)}
rows.append({"": "Phones that ran down to the safety floor (of stream logs)",
             "Pilot": f"{_fs['pilot']['stops_after']} of {_fs['pilot']['logs_after']}", "Series": f"{_fs_series['stops']} of {_fs_series['logs']}"})
_tgb |= {(len(rows) - 1, 1), (len(rows) - 1, 2)}
rows.append({"": "the same, all pilot runs", "Pilot": f"{_fs['pilot']['stops']} of {_fs['pilot']['logs']}", "Series": "–"})
_blk[len(rows)] = "What the gate does (series)"
rows.append({"": "Arrival status none / light / moderate or worse (%)", "Pilot": "–",
             "Series": " / ".join(f"{_ts_share[k]:.1f}" for k in ("none", "light", "rested"))})
_tgb |= {(len(rows) - 1, 2)}
rows.append({"": "Measurements the gate rested first (of analysed)", "Pilot": "–", "Series": f"{int((_ts >= 2).sum())} of {len(_ts)}"})
_tgb |= {(len(rows) - 1, 2)}
rows.append({"": "Rests that gave up while still hot", "Pilot": "–", "Series": f"{_fs_series['still_hot']}"})
_blk[len(rows)] = "Battery temperature when a measurement starts (°C)"
for lab, f in (("Median", lambda t: t.median()), ("95th percentile", lambda t: t.quantile(0.95)), ("Maximum", lambda t: t.max())):
    rows.append({"": lab, "Pilot": f"{f(_tq['pilot']):.1f}", "Series": f"{f(_tq['series']):.1f}"})
_tgb |= {(len(rows) - 1, 1), (len(rows) - 1, 2)}
_blk[len(rows)] = f"Variation between repeated measurements (median within-group CV, %; {_tg_e['groups']} groups)"
for lab, r in (("Active-phase energy", _tg_e), ("Active-phase length", _tg_d)):
    i0 = len(rows)
    rows += [{"": f"{lab}: all measurements", "Pilot": f"{r['pilot']:.2f}", "Series": f"{r['series']:.2f}"},
             {"": "the coolest-starting 74 % of each group", "Pilot": f"{r['coolest']:.2f}", "Series": "–"},
             {"": "a random 74 % of each group", "Pilot": f"{r['random']:.2f} [{r['random_lo']:.2f}, {r['random_hi']:.2f}]", "Series": "–"},
             {"": "share of the drop reproduced: coolest / random (%)",
              "Pilot": f"{round(r['closed_coolest_pct']) + 0:.0f} / {round(r['closed_random_pct']) + 0:.0f}", "Series": "–"}]
    _tgb |= {(i0 + 3, 1)}
    if r is _tg_e:
        rows.append({"": f"measurements the gate did not rest ({_tg_u['groups']} groups)", "Pilot": "–",
                     "Series": f"{_tg_u['unrested']:.2f}"})
thermal = pd.DataFrame(rows)
T.register(T.write_tex(thermal, "table_thermal_gate", caption=(
    "The thermal gate (exploratory): phones that ran down, the thermal status at which measurements arrive, the battery temperature "
    "at which they start, and a test inside the pilot of whether cooler starts alone make repeated measurements steadier. The pilot ran "
    "without the gate (apart from its last " + str(_pl_gated) + " measurements), the series with it. Bold: the values the thesis "
    "states in words."),
    label="tab:thermal-gate", colfmt="lrr", bold_cells=_tgb,
    blocks=_blk,
    notes="Refused top-ups: the charger is on but the phone reports that it is not charging; a refusal counts as followed by a "
          "successful top-up when the phone next reaches the top of its charge band. Stops: phone streams the harness stopped at its "
          "battery safety floor, counted in each batch's stream logs. Before the "
          "external supply was fitted, top-ups ran on laptop power, which could not always charge a phone, so the comparison starts at "
          "the supply. Arrival status: Android's thermal status read before each analysed series measurement (0 none, 1 light, 2 "
          "moderate or worse); the gate rests only the last group, then measures. Temperature: "
          "the battery temperature Android reports, read once before each measurement; analysed measurements, the pilot's from the "
          "external supply on. Variation: within each application, device and build with at least three measurements in both "
          "regimes. Keeping the coolest-starting 74 % of each pilot group drops its hottest quarter of measurements and leaves a start "
          f"temperature 95th percentile of {_tg_p95['kept']:.1f} °C, close to the series' {_tg_p95['series']:.1f} °C; "
          f"the random subsets are {_TG_DRAWS} draws (mean, 95 % band). The share reproduced is the part of the drop from the pilot's "
          "to the series' value that the subset alone achieves."),
    "SUPPLEMENT", "Supplement (the thermal gate)",
    "the thermal gate: safety-floor stops per regime, pre-measurement battery temperature, and the within-pilot coolest-subset test", NB)
print(thermal.to_string(index=False))
T.record("thermal_gate", safety_floor_stops={b: _fs[b]["stops"] for b in _fs}, stream_logs={b: _fs[b]["logs"] for b in _fs},
         pilot_stops_after_external_supply=_fs["pilot"]["stops_after"], pilot_logs_after_external_supply=_fs["pilot"]["logs_after"],
         refused_topups_after_supply={"pilot": _fs["pilot"]["refused_after"], "series": _fs_series["refused_after"]},
         refusals_recovered_after_supply={"pilot": _fs["pilot"]["recovered_after"], "series": _fs_series["recovered_after"]},
         series_arrival_status_pct=_ts_share, series_rested=f"{int((_ts >= 2).sum())} of {len(_ts)}", series_rests_given_up_hot=_fs_series["still_hot"], series_unrested_energy_cv=_tg_u,
         start_temp_p95_kept_vs_series=_tg_p95,
         battery_temp_start_c={g: dict(median=float(t.median()), p95=float(t.quantile(0.95)), max=float(t.max())) for g, t in _tq.items()},
         within_pilot_energy=_tg_e, within_pilot_length=_tg_d, keep=_TG_KEEP, draws=_TG_DRAWS,
         basis="exploratory; pilot regime from the external supply on; groups as in table_pilot_series")

_st = pd.read_csv(T.TABLES / "allatori_static_code_effects.csv")
_st_tab = pd.DataFrame({"Application": _st["app"], "Classes renamed": _st["classes_renamed"], "Decryption routines": _st["decryptor_routines"],
                        "Decryption call sites": _st["decrypt_call_sites"], "Instructions (%)": _st["app_code_instructions_pct"].map(lambda v: P.signed(v, 1)),
                        "Branches (%)": _st["app_code_branches_pct"].map(lambda v: P.signed(v, 1))})
T.register(T.write_tex(_st_tab, "table_obfuscation_static", caption=(
    "What the obfuscator changed in each application's own code (static analysis of the measured builds against their controls): classes "
    "renamed, string-decryption routines added and their call sites, and the change in instructions and branches. Bold: the "
    "largest change in instructions, which the thesis states in words."),
    label="tab:obfuscation-static", colfmt="lrrrrr", star=True,
    bold_cells={(int(_st["app_code_instructions_pct"].abs().values.argmax()), 4)}), "SUPPLEMENT", "Supplement (obfuscation)", "Allatori static changes per application", NB)
print(_st_tab.to_string(index=False))

# %% [markdown]
# ## Appendix: the protection families and the candidate tools
#
# One row per family; the tool measured is marked. Per-tool reasons for exclusion are in the replication package
# (`candidate_tools.csv`).

# %%
_fam = [("Obfuscation (code transformation)", "names, strings and control flow of the Java/DEX code", "at build time (Gradle plugin or command line)",
         "statically: the transformed code runs as it is", "ProGuard, R8, DexGuard, Allatori$^{*}$, DashO, Obfuscapk",
         r"\cite{Faruki2017AndroidCodeProtectionSurvey}"),
        ("Packing (loader-based)", "the DEX bytecode, encrypted as a payload", "after compilation, on the APK (vendor service)",
         "at run time: a stub decrypts and loads the payload",
         "Bangcle$^{*}$, Qihoo 360 Jiagu, Tencent Legu, Ijiami, AliProtect, Baidu, Naga, Manxi, APKProtect, SecNeo",
         r"\cite{Duan2018AndroidUnpackers,Dong2022PackDiff,BlazquezTapiador2025AndroidProtectionWild}"),
        ("Runtime shielding (RASP)", "the running application and its environment (debugger, root, hooks, tampering)",
         "as an SDK at build time, or by wrapping the APK", "at run time: checks and reacts",
         "freeRASP$^{*}$, DexProtector, Appdome, Promon SHIELD, LIAPP, WhiteCryption, V-Key, Virbox Protector, AppSealing",
         r"\cite{Faruki2017AndroidCodeProtectionSurvey,BlazquezTapiador2025AndroidProtectionWild}")]
_tex = [r"\begin{table*}[t]", r"\centering", r"\small",
        r"\caption{The three protection families considered and their candidate tools. $^{*}$: the tool measured.}", r"\label{tab:tools}",
        r"\begin{tabular}{p{2.6cm}p{2.6cm}p{2.2cm}p{2.3cm}p{3.6cm}p{2.2cm}}", r"\toprule",
        r"\textbf{Family} & \textbf{Protects} & \textbf{Inserted} & \textbf{Acts} & \textbf{Candidate tools} & \textbf{References} \\", r"\midrule"]
_tex += [" & ".join(row) + r" \\" for row in _fam]
_tex += [r"\bottomrule", r"\end{tabular}",
         r"\par\smallskip\footnotesize Per-tool sources and reasons for exclusion: \texttt{candidate\_tools.csv} in the replication package. A fourth family, compilation of the bytecode to native code, was considered and not pursued; its one "
         r"candidate, DroidPro, is a research prototype. freeRASP is freemium: an open-source integration layer around a proprietary binary core.",
         r"\end{table*}"]
(T.TABLES / "table_tool_families.tex").write_text("\n".join(_tex) + "\n")
T.register("tables/table_tool_families.tex", "APPENDIX", "Appendix (candidate tools)", "one row per protection family: what it protects, where it is "
           "inserted, how it acts, candidate tools (measured one marked), references", NB)

_tools = pd.DataFrame([
    ("R8", "obfuscation", "open source (Android build tools)", "On debug builds R8 shrinks but does not rename; release builds were abandoned on 24 July 2026 because the packing service could not process them and release-mode R8 broke applications", "calculator's debug R8 build: 11.1 to 7.7 MB, class names unchanged (identity mapping)"),
    ("ProGuard", "obfuscation", "open source", "Superseded by R8 in the Android build", "inventory note: renaming only"),
    ("DexGuard", "obfuscation (with RASP features)", "commercial (Guardsquare)", "Access refused", "requested access and pricing"),
    ("Allatori", "obfuscation", "commercial; academic licence (9.8 Educational)", "Measured", ""),
    ("DashO", "obfuscation", "commercial (PreEmptive)", "Commercial licence required; evaluation access did not arrive in time (July 2026)", "installer present, never activated"),
    ("Obfuscapk", "obfuscation", "open source (academic)", "Not tried; doubts about maintenance", ""),
    ("Bangcle", "packing", "commercial web service", "Measured", ""),
    ("Baidu packer", "packing", "commercial web service", "Crashes about 2 s after launch on the Pixel 9 (Android 16): its hidden-API exemption call is denied", "one attempted run, 19 May 2026; no energy data"),
    ("Qihoo 360 Jiagu", "packing", "commercial web service", "No free tier; a licence could not be arranged in time (July 2026)", "nothing packed"),
    ("Tencent Legu", "packing", "commercial cloud service", "No account; a meeting requested and not pursued", ""),
    ("Ijiami, AliProtect, Naga, Manxi, APKProtect", "packing", "commercial", "No reply", ""),
    ("SecNeo", "packing", "commercial", "No contact recorded", ""),
    ("freeRASP", "runtime shielding", "freemium: open-source integration layer, proprietary binary core (Talsec)", "Measured", ""),
    ("DexProtector", "runtime shielding", "commercial (Licel)", "Its licence requirements could not be met within the project (July 2026)", ""),
    ("Virbox Protector", "runtime shielding", "commercial; time-limited trial", "Trial builds are for short-term internal evaluation only, and the licence requirements could not be met in time (July 2026)", "8 release-build applications protected; scenario checks only, Pixel 3"),
    ("Promon SHIELD", "runtime shielding", "commercial", "No academic licence; the licence requirements could not be met in time", ""),
    ("Appdome", "runtime shielding", "commercial service", "Trial account only; licence terms not settled in time", ""),
    ("LIAPP, WhiteCryption, V-Key, AppSealing", "runtime shielding", "commercial", "No reply", ""),
    ("DroidPro", "native compilation (family not pursued)", "research prototype", "Reference only", ""),
    ("Kiwisec, NetEase YiDun", "packing (also contacted)", "commercial", "No reply", ""),
    ("Zimperium zShield, Verimatrix XTD Protect", "runtime shielding (also contacted)", "commercial", "No reply", ""),
    ("DexHunter, DexRay, DroidPDF", "analysis tools, not protections", "research", "Not protection tools", ""),
], columns=["tool", "family", "licence_or_access", "why_not_used", "example_or_evidence"])
_tools.to_csv(T.TABLES / "candidate_tools.csv", index=False)
T.register("tables/candidate_tools.csv", "REPO", "Appendix (candidate tools; replication package)",
           "every candidate tool: family, licence or access, why it was not used, an example (licence terms stated generally)", NB)

# %% [markdown]
# ## §5: the experiment setup, with one cell's timeline folded in (body figure)
#
# (a) hardware and software; (b) one cell as a loop, checked against the code and the series logs;
# (c) one measured run to scale, pooled medians (`timeline_per_device.csv`; the device ranges go in the caption).

# %%
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

_tl = a2.set_index("device").loc["all devices"]
_tl_proc, _tl_disp = float(_tl["process_start_s"]), float(_tl["first_display_s"])
_tl_ui, _tl_sess = float(_tl["uiautomator2_server_start_s"]), float(_tl["appium_session_activity_start_s"])
_tl_first, _tl_act = float(_tl["passive_s"]), float(_tl["active_s"])
_tl_lo, _tl_hi = float(_tl["active_s_app_median_min"]), float(_tl["active_s_app_median_max"])
_tl_end = _tl_first + _tl_act
assert _tl_proc < _tl_disp < 0 < _tl_ui < _tl_sess < _tl_first


def _box(ax, x0, y0, w, h, text, fc="white", ec="0.25", fs=7, weight="normal", ha="center"):
    ax.add_patch(mpatches.FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0.004,rounding_size=0.012", fc=fc, ec=ec, lw=0.6,
                                         transform=ax.transAxes))
    ax.text(x0 + (w / 2 if ha == "center" else 0.015), y0 + h / 2, text, ha=ha, va="center", fontsize=fs, weight=weight, transform=ax.transAxes)


def _arrow(ax, xy0, xy1, ls="-", style="-|>", rad=0.0):
    ax.annotate("", xy=xy1, xytext=xy0, xycoords="axes fraction",
                arrowprops=dict(arrowstyle=style, lw=0.7, color="0.25", ls=ls, shrinkA=0, shrinkB=0, connectionstyle=f"arc3,rad={rad}"))


# The setup is three one-column figures: a photo of the rig, one measurement as ten steps, one measured run to scale.

# (1) the photo of the rig: in the thesis as a photograph, not drawn here

# (2) one measurement as ten steps, the measured ones shaded (7 pt, the long steps on two lines)
fig, axb = plt.subplots(figsize=(T.FIG_SINGLE, 3.75), layout="constrained")
axb.set_axis_off()
_steps = ["State of charge checked;\ntop-up below 40 % (to 70 %)", "USB cut, verified on the phone", "Settle 60 s; maintenance guard",
          "Thermal gate: rest while the phone throttles", "Log capture starts", "Device state; fresh install;\ncompilation request",
          "Launch (timed);\ncompatibility dialog dismissed", "Profilers start (t = 0);\nAppium session: passive phase",
          "Scenarios: active phase; profilers stop", "USB restored; uninstall; USB cut again"]
_h1, _h2, _gap, _top, _x0, _w = 0.058, 0.094, 0.0195, 0.995, 0.105, 0.80
_hs = [(_h2 if "\n" in t else _h1) for t in _steps]
_ys, _y = [], _top
for _hh in _hs:
    _y -= _hh
    _ys.append(_y)
    _y -= _gap
axb.add_patch(mpatches.Rectangle((0.068, _ys[8] - 0.009), 0.85, _ys[7] + _hs[7] - _ys[8] + 0.018, fc="#deebf7", ec="none", transform=axb.transAxes))
axb.text(0.086, (_ys[8] + _ys[7] + _hs[7]) / 2, "measured", rotation=90, ha="center", va="center", fontsize=7, transform=axb.transAxes)
for i, (y, s_) in enumerate(zip(_ys, _steps)):
    _box(axb, _x0, y, _w, _hs[i], f"{i + 1}. {s_}".replace("\n", "\n     "), ha="left", fs=7)
    if i:
        _arrow(axb, (_x0 + _w / 2, _ys[i - 1]), (_x0 + _w / 2, y + _hs[i]))
axb.plot([0.04, 0.04], [_ys[9], _ys[5] + _hs[5]], color="0.4", lw=0.6, transform=axb.transAxes)
axb.text(0.02, (_ys[9] + _ys[5] + _hs[5]) / 2, "Android Runner", rotation=90, ha="center", va="center", fontsize=7, color="0.3",
         transform=axb.transAxes)
_arrow(axb, (_x0 + _w, _ys[9] + _hs[9] / 2), (_x0 + _w, _ys[0] + _hs[0] / 2), rad=0.32)
axb.text(0.985, (_ys[0] + _hs[0] + _ys[9]) / 2, "next cell, in the phone's own random order", rotation=90, ha="center", va="center",
         fontsize=7, transform=axb.transAxes)
T.register(T.save_figure(fig, "figure_measurement", T.FIG_SINGLE), "REPO", "5 Experiment execution (one measurement)",
           "one measurement (cell) as ten steps in a loop, the measured steps shaded; replaced in the thesis by the execution diagram "
           "(figures/figure_execution.drawio, drawn in draw.io and checked against the harness code)", NB)

# (3) one measured run, to scale (pooled medians)
# the figures' default serif is Libertine Display, which has no bold cut: bold labels use the regular Libertine family
from matplotlib import font_manager as _fm
_BOLD_FAMILY = "Linux Libertine O" if "LinLibertine_RB" in _fm.findfont(_fm.FontProperties(family="Linux Libertine O", weight="bold")) else None
fig, axc = plt.subplots(figsize=(T.FIG_SINGLE, 1.35), layout="constrained")
for a_, b_, fc in ((_tl_proc, 0.0, "0.82"), (_tl_proc, _tl_disp, "0.35"), (0.0, _tl_first, "#a6cee3"), (_tl_first, _tl_end, "#1f78b4")):
    axc.add_patch(mpatches.Rectangle((a_, 0), b_ - a_, 1, fc=fc, ec="0.25", lw=0.6))
axc.text(_tl_first / 2, 0.66, "Passive", ha="center", va="center", fontsize=7, fontweight="bold", fontfamily=_BOLD_FAMILY)
axc.text(_tl_first / 2, 0.32, f"≈ {_tl_first:.0f} s", ha="center", va="center", fontsize=7)
axc.text((_tl_first + _tl_end) / 2, 0.66, "Active", ha="center", va="center", fontsize=7, fontweight="bold", color="white",
         fontfamily=_BOLD_FAMILY)
axc.text((_tl_first + _tl_end) / 2, 0.32, f"≈ {_tl_act:.0f} s", ha="center", va="center", fontsize=7, color="white")
_ln_ = dict(arrowstyle="-", lw=0.5, color="0.25", shrinkA=0, shrinkB=0)
axc.annotate("Launch", xy=((_tl_proc + _tl_disp) / 2, 0.5), xytext=(_tl_proc - 5, 0.5), ha="right", va="center", fontsize=7,
             fontweight="bold", fontfamily=_BOLD_FAMILY, arrowprops=_ln_)   # no "(timed)" here: the caption and §5 say so
for x_, txt, ha, lvl in ((0.0, "t = 0: profilers start", "right", 1.45), (_tl_first, "first scenario", "left", 1.45),
                         (_tl_end, "last scenario ends", "right", 1.45)):
    axc.plot([x_, x_], [1, lvl - 0.05], color="0.25", lw=0.6, ls=":")
    axc.text(x_ + (0.8 if ha == "left" else -0.8), lvl, txt, ha=ha, va="bottom", fontsize=7)
axc.set_xlim(-36, _tl_end + 3)
axc.set_ylim(-0.15, 2.0)
axc.set_yticks([])
axc.set_xticks([0, 30, 60, 90, 120])
axc.spines[["top", "right", "left"]].set_visible(False)
axc.spines["bottom"].set_bounds(_tl_proc, _tl_end)
axc.set_xlabel("time from the first energy sample (s)")
T.register(T.save_figure(fig, "figure_run", T.FIG_SINGLE), "BODY", "5 Experiment execution (one run to scale)",
           "one measured run to scale (pooled medians): launch, passive phase, active phase; phase names in bold, no other labels on the phases", NB)
