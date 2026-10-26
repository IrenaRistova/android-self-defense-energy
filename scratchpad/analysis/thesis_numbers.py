#!/usr/bin/env python3
"""Every headline number the thesis quotes, emitted to one JSON file.

An independent implementation of the analysis: the notebooks in
6_results_analysis/notebooks/ check their own numbers against its output. The
default run writes `6_results_data/derived/thesis_numbers_reference.json`, and the output of the run
with `--exclude-apps ''` (androidskk kept, ten applications) as
`6_results_data/derived/thesis_numbers_ten_apps_sensitivity.json`.

How it computes intervals and tests:

  * **Exact t quantiles.** The paired t-interval uses the exact 97.5% quantile
    for df = n - 1. A fixed t = 2.045 for every n < 40 is the 97.5% quantile for
    df = 29, so it is right only at n = 30: at n = 21 the
    correct value is 2.086 and the fixed-t interval is ~2% too narrow; at n = 63
    or 87 the fixed rule switches to 1.96, which is fine. Both intervals are
    reported (`ci_naive`, `ci_naive_published_t2045`), so the size of the
    difference is visible rather than assumed.
  * **App-clustered intervals for every arm**, not just Allatori. Nine pairs
    inside one app are not independent observations; the effective sample is
    ~10 apps, not 87 pairs.
  * **Exact sign-test p-values** for the repeatability comparison.

The notebooks check the cell counts of this filter chain against their own.

Usage:  python3 scratchpad/analysis/thesis_numbers.py [--out 6_results_data/derived/thesis_numbers_reference.json]
"""
from __future__ import annotations
import argparse, collections, csv, json, math, os, random, statistics as st, sys

os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
random.seed(20260830)

BATCH = {
    "pilot":   ("6_results_data/run_tables/run_table_extended_final_ykush_2026-08-04.csv",         True),
    "series1": ("6_results_data/run_tables/run_table_extended_final_ykush_batch2_2026-08-10.csv",  False),
    "series2": ("6_results_data/run_tables/run_table_extended_final_ykush_series2_2026-08-14.csv", False),
    "series3": ("6_results_data/run_tables/run_table_extended_final_ykush_series3_2026-08-17.csv", False),
}
# Fixed order (decision 2026-09-28): the permutation test draws from ONE global
# random stream, so the order the arms run in is part of the specification.
ARMS = ("bangcle_debug", "allatori_debug", "freerasp_debug")
# K-window post-charge rule: a cell is flagged when it is within K cells of a
# workload interruption on that phone.
K_PHONE = {"pixel3": 2, "pixel6": 6, "pixel9": 4}
# written by topup_ground_truth.py; the package holds it as 6_results_data/inputs/post_charge_k_window.csv
TOPUP = "6_results_data/inputs/post_charge_k_window.csv"
# Final cohort decision 2026-09-28: androidskk is dropped from EVERY arm, because
# its control build carries obfuscated code and all four variants share it.
DEFAULT_EXCLUDE = "androidskk"
# Decision 2026-10-02: itsok's Allatori build is a release build, while the protocol
# committed before data collection (2026-07-27) requires every build to be a debug
# build. Excluded from the Allatori arm only, by that rule; itsok's other arms are
# unaffected. app:variant pairs.
# Decision 2026-10-26: medtimer's Bangcle build is excluded from the packing arm by the same
# build-parity rule: it was packed in May from a debug build carrying JaCoCo coverage probes, which its
# control (rebuilt 2026-08-03) lacks.
DEFAULT_EXCLUDE_BUILDS = "itsok:allatori_debug,medtimer:bangcle_debug"
# Decision 2026-10-26: medtimer leaves the passive ("startup") phase in every arm, control included,
# because before its first scenario the harness steps through its six-screen introduction. Its
# passive-phase values are blanked at load, so every passive-phase number skips it.
DEFAULT_PASSIVE_EXCLUDE = "medtimer"
MEASURE = {"workload": "workload_joules", "startup": "pre_workload_joules"}


# ---------------------------------------------------------------- statistics
def t_quantile(df: float, p: float = 0.975) -> float:
    """Two-sided 95% t quantile, by bisection on the Student-t CDF.

    The script needs only the standard library, so the CDF is built from the
    regularised incomplete beta function via its continued fraction.
    """
    def betacf(a, b, x):
        tiny, eps = 1e-300, 3e-16
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c, d = 1.0, 1.0 - qab * x / qap
        if abs(d) < tiny:
            d = tiny
        d = 1.0 / d
        h = d
        for m in range(1, 300):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d
            c = 1.0 + aa / c
            if abs(d) < tiny:
                d = tiny
            if abs(c) < tiny:
                c = tiny
            d = 1.0 / d
            h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d
            c = 1.0 + aa / c
            if abs(d) < tiny:
                d = tiny
            if abs(c) < tiny:
                c = tiny
            d = 1.0 / d
            de = d * c
            h *= de
            if abs(de - 1.0) < eps:
                break
        return h

    def betai(a, b, x):
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                 + a * math.log(x) + b * math.log1p(-x))
        if x < (a + 1.0) / (a + b + 2.0):
            return math.exp(lbeta) * betacf(a, b, x) / a
        return 1.0 - math.exp(lbeta) * betacf(b, a, 1.0 - x) / b

    def cdf(t):
        x = df / (df + t * t)
        s = 0.5 * betai(df / 2.0, 0.5, x)
        return 1.0 - s if t > 0 else s

    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sign_test_p(k: int, n: int) -> float:
    """Exact two-sided binomial p at theta = 0.5."""
    if n == 0:
        return float("nan")
    tail = lambda m: sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n
    return min(1.0, 2 * tail(min(k, n - k)))


def holm(pvals: dict) -> dict:
    """Holm-Bonferroni adjusted p-values, order-preserving."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, running = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


# ---------------------------------------------------------------- data
CHAIN_STEPS = ["loaded", "scientifically_valid", "not_negative_control",
               "cohort_exclusion", "build_type_parity", "scenarios_complete",
               "workload_joules_gt_0", "pilot_regime_cutoff"]


def load(path: str, regime_b: bool, batch: str, exclude: set, chain: dict,
         exclude_builds: frozenset = frozenset(), passive_exclude: frozenset = frozenset()) -> list[dict]:
    """Apply the filter chain in its fixed order, counting survivors. The passive-phase values
    of the applications in `passive_exclude` are blanked (NaN), so every passive-phase number skips them."""
    rows = []
    counts = collections.Counter()
    for r in csv.DictReader(open(path)):
        counts["loaded"] += 1
        if r.get("scientifically_valid") != "True":
            continue
        counts["scientifically_valid"] += 1
        if (r.get("negative_control") or "").strip() not in ("", "None", "False"):
            continue
        counts["not_negative_control"] += 1
        if r["app"] in exclude:
            continue
        counts["cohort_exclusion"] += 1
        if (r["app"], r["variant"]) in exclude_builds:
            continue
        counts["build_type_parity"] += 1
        try:
            c, pl = int(r.get("scenarios_completed") or -1), int(r.get("scenarios_planned") or -1)
            if c >= 0 and pl > 0 and c != pl:
                continue
        except ValueError:
            pass
        counts["scenarios_complete"] += 1
        def f(k):
            try:
                return float(r.get(k) or "nan")
            except ValueError:
                return float("nan")
        if not (f("workload_joules") > 0):
            continue
        counts["workload_joules_gt_0"] += 1
        if regime_b and (r.get("cell_start_ts_utc") or "") < "2026-08-07":
            continue
        counts["pilot_regime_cutoff"] += 1
        d = {k: f(v) for k, v in MEASURE.items()}
        d.update(app=r["app"], phone=r["phone"], variant=r["variant"], batch=batch,
                 rep=str(r.get("rep")),
                 dur_startup=f("duration_pre_workload_s"),
                 dur_workload=f("duration_workload_s"),
                 watts=f("workload_avg_watts"))
        if r["app"] in passive_exclude:
            d["startup"] = d["dur_startup"] = float("nan")
        rows.append(d)
    chain[batch] = {k: counts[k] for k in CHAIN_STEPS}
    return rows


def post_charge_flags() -> dict:
    """(batch, phone, app, variant, rep) -> True if inside the K-window."""
    flags = {}
    for r in csv.DictReader(open(TOPUP)):
        try:
            k = int(float(r["k"]))
        except (ValueError, KeyError):
            continue
        flags[(r["batch"], r["phone"], r["app"], r["variant"], str(r["rep"]))] = \
            k <= K_PHONE.get(r["phone"], -1)
    return flags


def paired(rows, arm, key, by_batch=True):
    """Matched-pair deltas, one per (app, phone[, batch]) against own baseline."""
    g = collections.defaultdict(list)
    for r in rows:
        v = r[key]
        if v == v and v > 0:
            k = (r["app"], r["phone"], r["variant"]) + ((r["batch"],) if by_batch else ())
            g[k].append(v)
    mean = {k: st.mean(v) for k, v in g.items() if v}
    out = []
    for k in mean:
        if k[2] != arm:
            continue
        bk = (k[0], k[1], "baseline_debug") + ((k[3],) if by_batch else ())
        if bk in mean:
            out.append((k[0], 100 * (mean[k] - mean[bk]) / mean[bk]))
    return out


def permutation_p(rows, arm, key, n_resample=6000, by_batch=True):
    """Two-sided. Variant labels are shuffled WITHIN each (app, phone[, batch])
    stratum -- the same stratum the pairing uses -- so the null preserves every
    app, device and batch effect and tests only the label."""
    obs = st.mean([d for _, d in paired(rows, arm, key, by_batch)])
    pool = [r for r in rows if r["variant"] in (arm, "baseline_debug")]
    strata = collections.defaultdict(list)
    for r in pool:
        strata[(r["app"], r["phone"]) + ((r["batch"],) if by_batch else ())].append(r)
    hits = 0
    for _ in range(n_resample):
        shuffled = []
        for v in strata.values():
            labels = [x["variant"] for x in v]
            random.shuffle(labels)
            shuffled += [{**x, "variant": l} for x, l in zip(v, labels)]
        d = paired(shuffled, arm, key, by_batch)
        if d and abs(st.mean([x for _, x in d])) >= abs(obs):
            hits += 1
    return obs, (hits + 1) / (n_resample + 1)


def ci_naive(deltas, exact=True):
    n = len(deltas)
    m, se = st.mean(deltas), st.stdev(deltas) / math.sqrt(n)
    t = t_quantile(n - 1) if exact else (2.045 if n < 40 else 1.96)
    return m, m - t * se, m + t * se, t


def ci_clustered(recs, n_resample=20000):
    """Resample APPS with replacement -- pairs within an app are not independent."""
    by_app = collections.defaultdict(list)
    for app, d in recs:
        by_app[app].append(d)
    apps = list(by_app)
    means = []
    for _ in range(n_resample):
        pick = random.choices(apps, k=len(apps))
        vals = [x for a in pick for x in by_app[a]]
        if vals:
            means.append(st.mean(vals))
    means.sort()
    return (means[int(.025 * len(means))], means[int(.975 * len(means))],
            sum(1 for m in means if m <= 0), len(means))


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="6_results_data/derived/thesis_numbers_reference.json")
    ap.add_argument("--resamples", type=int, default=6000)
    ap.add_argument("--exclude-apps", default=DEFAULT_EXCLUDE,
                    help="comma-separated apps dropped from every arm ('' = none)")
    ap.add_argument("--exclude-builds", default=DEFAULT_EXCLUDE_BUILDS,
                    help="comma-separated app:variant builds dropped from their arm ('' = none)")
    ap.add_argument("--passive-exclude-apps", default=DEFAULT_PASSIVE_EXCLUDE,
                    help="comma-separated apps left out of the passive phase in every arm ('' = none)")
    a = ap.parse_args()
    exclude = {x for x in a.exclude_apps.split(",") if x}
    exclude_builds = frozenset(tuple(x.split(":", 1)) for x in a.exclude_builds.split(",") if x)
    passive_exclude = frozenset(x for x in a.passive_exclude_apps.split(",") if x)

    chain: dict = {}
    data = {b: load(p, rb, b, exclude, chain, exclude_builds, passive_exclude) for b, (p, rb) in BATCH.items()}
    series = data["series1"] + data["series2"] + data["series3"]
    chain["series_pooled"] = {k: sum(chain[b][k] for b in ("series1", "series2", "series3"))
                              for k in CHAIN_STEPS}
    out: dict = {"generated_by": "scratchpad/analysis/thesis_numbers.py",
                 "cohort_excluded_apps": sorted(exclude),
                 "excluded_builds": sorted(":".join(x) for x in exclude_builds),
                 "passive_excluded_apps": sorted(passive_exclude),
                 "cohort_apps": sorted({r["app"] for r in series}),
                 "arm_order_for_permutation_stream": list(ARMS),
                 "filter_chain": chain,
                 "cells_used": {b: len(v) for b, v in data.items()},
                 "cells_used_series_pooled": len(series)}
    print("=== filter chain ===")
    for b, c in chain.items():
        print(f"  {b:<14}" + " -> ".join(str(c[k]) for k in CHAIN_STEPS))

    print("=== pooled series, N = 30, matched within batch ===")
    print(f"{'arm':<10}{'measure':<9}{'dE%':>8}{'naive 95% CI':>20}{'clustered 95% CI':>22}"
          f"{'p':>9}{'t used':>8}{'t old':>7}")
    head = {}
    for arm in ARMS:
        for mname, col in MEASURE.items():
            recs = paired(series, arm, mname)
            d = [x for _, x in recs]
            m, lo, hi, t = ci_naive(d)
            _, lo_o, hi_o, t_o = ci_naive(d, exact=False)
            clo, chi, n_le0, nb = ci_clustered(recs)
            _, p = permutation_p(series, arm, mname, a.resamples)
            key = f"{arm.replace('_debug','')}_{mname}"
            head[key] = {
                "delta_pct": round(m, 4), "n_pairs": len(d),
                "n_apps": len({app for app, _ in recs}),
                "ci_naive": [round(lo, 4), round(hi, 4)],
                "ci_naive_published_t2045": [round(lo_o, 4), round(hi_o, 4)],
                "t_exact": round(t, 4), "t_published": t_o,
                "ci_app_clustered": [round(clo, 4), round(chi, 4)],
                "bootstrap_resamples_le_zero": n_le0,
                "bootstrap_resamples": nb,
                "permutation_p": round(p, 6),
                "permutation_resamples": a.resamples,
            }
            print(f"{arm.replace('_debug',''):<10}{mname:<9}{m:>+7.2f}%"
                  f"  [{lo:>+6.2f},{hi:>+6.2f}]  [{clo:>+6.2f},{chi:>+6.2f}]"
                  f"{p:>9.4f}{t:>8.3f}{t_o:>7.3f}")
    out["headline_pooled_series"] = head

    print("\n=== per batch ===")
    per = {}
    for b in ("pilot", "series1", "series2", "series3"):
        for arm in ARMS:
            recs = paired(data[b], arm, "workload", by_batch=False)
            d = [x for _, x in recs]
            m, lo, hi, t = ci_naive(d)
            clo, chi, _, _ = ci_clustered(recs)
            per[f"{b}_{arm.replace('_debug','')}"] = {
                "delta_pct": round(m, 4), "n_pairs": len(d),
                "ci_naive": [round(lo, 4), round(hi, 4)],
                "ci_app_clustered": [round(clo, 4), round(chi, 4)],
                "t_exact": round(t, 4)}
            print(f"  {b:<9}{arm.replace('_debug',''):<10}{m:>+7.2f}%  "
                  f"[{lo:>+6.2f},{hi:>+6.2f}]  n={len(d)}  t={t:.3f}")
    out["per_batch_workload"] = per

    # ---- minimum detectable effect -------------------------------------
    # A null is only informative up to the effect the design could have seen.
    # Computed empirically from the observed standard error of the per-pair
    # deltas rather than from an assumed CV, so it describes THIS experiment:
    #     MDE = (t_{alpha/2, df} + t_{beta, df}) * SE
    # at alpha = 0.05 two-sided and 80% power. Reported for every arm and both
    # energy windows; for the two null arms this is the number the claim rests on.
    print("\n=== minimum detectable effect, alpha=0.05 two-sided, 80% power ===")
    print(f"{'arm':<10}{'measure':<9}{'pairs':>7}{'SE':>8}{'MDE %':>9}{'observed':>10}"
          f"{'  verdict'}")
    mde_out = {}
    for arm in ARMS:
        for mname, _ in MEASURE.items():
            recs = paired(series, arm, mname)
            d = [x for _, x in recs]
            n = len(d)
            se = st.stdev(d) / math.sqrt(n)
            t_a = t_quantile(n - 1, 0.975)
            t_b = t_quantile(n - 1, 0.80)
            mde = (t_a + t_b) * se
            obs = st.mean(d)
            verdict = ("detected" if abs(obs) >= mde else
                       f"null informative to +/-{mde:.2f}%")
            print(f"{arm.replace('_debug',''):<10}{mname:<9}{n:>7}{se:>8.3f}"
                  f"{mde:>9.2f}{obs:>+10.2f}%  {verdict}")
            mde_out[f"{arm.replace('_debug','')}_{mname}"] = {
                "n_pairs": n, "se": round(se, 4), "mde_pct": round(mde, 4),
                "observed_pct": round(obs, 4),
                "detected": bool(abs(obs) >= mde)}
    out["minimum_detectable_effect"] = mde_out

    print("\n=== per phone, freeRasp ===")
    pp = {}
    for phone in ("pixel3", "pixel6", "pixel9"):
        sub = [r for r in series if r["phone"] == phone]
        recs = paired(sub, "freerasp_debug", "workload")
        d = [x for _, x in recs]
        m, lo, hi, t = ci_naive(d)
        clo, chi, _, _ = ci_clustered(recs)
        pp[phone] = {"delta_pct": round(m, 4), "n_pairs": len(d),
                     "ci_naive": [round(lo, 4), round(hi, 4)],
                     "ci_app_clustered": [round(clo, 4), round(chi, 4)]}
        print(f"  {phone:<9}{m:>+7.2f}%  naive [{lo:>+6.2f},{hi:>+6.2f}]  "
              f"clustered [{clo:>+6.2f},{chi:>+6.2f}]  n={len(d)}")
    out["per_phone_freerasp_workload"] = pp

    # ---- per-app deltas, every arm ---------------------------------------
    print("\n=== per-app delta, workload energy, pooled series ===")
    per_app: dict = {}
    print(f"{'app':<14}" + ''.join(f"{x.replace('_debug',''):>11}" for x in ARMS))
    apps = sorted({r["app"] for r in series})
    for app in apps:
        row = {}
        line = f"{app:<14}"
        for arm in ARMS:
            recs = [(a, d) for a, d in paired(series, arm, "workload") if a == app]
            if recs:
                m = st.mean([d for _, d in recs])
                row[arm.replace("_debug", "")] = {"delta_pct": round(m, 4),
                                                  "n_pairs": len(recs)}
                line += f"{m:>+10.2f}%"
            else:
                line += f"{'—':>11}"
        per_app[app] = row
        print(line)
    out["per_app_workload"] = per_app

    # ---- post-charge sensitivity ------------------------------------------
    # Decision 2026-09-28: no cell is excluded on this flag. The filtered values are
    # a SENSITIVITY analysis -- the Pixel 9 freeRasp row carries both, and the pooled
    # figures show the headline is insensitive rather than asserting it.
    flags = post_charge_flags()
    def unflagged(rows):
        return [r for r in rows
                if not flags.get((r["batch"], r["phone"], r["app"], r["variant"], r["rep"]), False)]
    s_f = unflagged(series)
    print(f"\n=== post-charge sensitivity: {len(series) - len(s_f)} of {len(series)} cells flagged ===")
    pc = {"cells_flagged": len(series) - len(s_f), "cells_total": len(series),
          "rule": "k <= K_phone, K = " + json.dumps(K_PHONE), "pooled": {}, "per_phone_freerasp_workload": {}}
    for arm in ARMS:
        for mname in MEASURE:
            d0 = [x for _, x in paired(series, arm, mname)]
            d1 = [x for _, x in paired(s_f, arm, mname)]
            m1, lo1, hi1, _ = ci_naive(d1)
            key = f"{arm.replace('_debug','')}_{mname}"
            pc["pooled"][key] = {"unfiltered_pct": round(st.mean(d0), 4),
                                 "filtered_pct": round(m1, 4),
                                 "filtered_ci_naive": [round(lo1, 4), round(hi1, 4)],
                                 "shift_pp": round(m1 - st.mean(d0), 4), "n_pairs_filtered": len(d1)}
            print(f"  {key:<20}{st.mean(d0):>+7.2f}% -> {m1:>+7.2f}%  (shift {m1-st.mean(d0):+.2f} pp)")
    for phone in ("pixel3", "pixel6", "pixel9"):
        d1 = [x for _, x in paired([r for r in s_f if r["phone"] == phone], "freerasp_debug", "workload")]
        m1, lo1, hi1, _ = ci_naive(d1)
        pc["per_phone_freerasp_workload"][phone] = {
            "unfiltered_pct": pp[phone]["delta_pct"], "unfiltered_ci_naive": pp[phone]["ci_naive"],
            "filtered_pct": round(m1, 4), "filtered_ci_naive": [round(lo1, 4), round(hi1, 4)],
            "n_pairs_filtered": len(d1)}
        print(f"  freerasp {phone:<8}{pp[phone]['delta_pct']:>+7.2f}% -> {m1:>+7.2f}% [{lo1:+.2f},{hi1:+.2f}]")
    out["post_charge_sensitivity"] = pc

    # ---- phase durations ---------------------------------------------------
    print("\n=== phase-duration deltas ===")
    dur = {}
    for arm in ARMS:
        for mname in ("dur_startup", "dur_workload"):
            recs = paired(series, arm, mname)
            dpct = [x for _, x in recs]
            m, lo, hi, _ = ci_naive(dpct)
            # seconds, same pairing
            g = collections.defaultdict(list)
            for r in series:
                v = r[mname]
                if v == v and v > 0:
                    g[(r["app"], r["phone"], r["variant"], r["batch"])].append(v)
            mm = {k: st.mean(v) for k, v in g.items()}
            sec = [mm[k] - mm[(k[0], k[1], "baseline_debug", k[3])] for k in mm
                   if k[2] == arm and (k[0], k[1], "baseline_debug", k[3]) in mm]
            ms, los, his, _ = ci_naive(sec)
            key = f"{arm.replace('_debug','')}_{mname.replace('dur_', '')}"
            dur[key] = {"delta_pct": round(m, 4), "ci_naive_pct": [round(lo, 4), round(hi, 4)],
                        "delta_s": round(ms, 4), "ci_naive_s": [round(los, 4), round(his, 4)],
                        "n_pairs": len(dpct), "pairs_positive": sum(1 for x in dpct if x > 0)}
            print(f"  {key:<22}{ms:>+7.3f} s [{los:+.3f},{his:+.3f}]  {m:>+6.2f}%  "
                  f"{sum(1 for x in dpct if x > 0)}/{len(dpct)} positive")
    out["phase_duration_deltas"] = dur

    # ---- composition + leave-one-app-out ------------------------------------
    print("\n=== composition sensitivity and leave-one-app-out ===")
    comp = {}
    alla_apps = sorted({a2 for a2, _ in paired(series, "allatori_debug", "workload")})
    for label, drop in (("all_allatori_apps", set()), ("minus_pdfviewer", {"pdfviewer"})):
        d = [x for a2, x in paired(series, "allatori_debug", "workload") if a2 not in drop]
        m, lo, hi, _ = ci_naive(d)
        apps_in = [x for x in alla_apps if x not in drop]
        comp[label] = {"apps": apps_in, "n_pairs": len(d), "delta_pct": round(m, 4),
                       "ci_naive": [round(lo, 4), round(hi, 4)]}
        print(f"  allatori {label:<32}{len(apps_in)} apps {len(d)} pairs {m:+.2f}% [{lo:+.2f},{hi:+.2f}]")
    loo = {}
    for arm in ARMS:
        for mname in MEASURE:
            recs = paired(series, arm, mname)
            vals = {}
            for app in sorted({x for x, _ in recs}):
                vals[app] = round(st.mean([d for x, d in recs if x != app]), 4)
            key = f"{arm.replace('_debug','')}_{mname}"
            loo[key] = {"min": min(vals.values()), "max": max(vals.values()), "by_dropped_app": vals}
            print(f"  LOO {key:<20}[{min(vals.values()):+.2f}, {max(vals.values()):+.2f}]")
    out["allatori_composition"] = comp
    out["leave_one_app_out"] = loo

    # ---- freeRasp absolute vs percentage (flat tax) -------------------------
    print("\n=== freeRasp absolute vs percentage ===")
    def pearson(x, y):
        mx, my = st.mean(x), st.mean(y)
        sx = math.sqrt(sum((v - mx) ** 2 for v in x)); sy = math.sqrt(sum((v - my) ** 2 for v in y))
        return sum((u - mx) * (v - my) for u, v in zip(x, y)) / (sx * sy)
    fr = {}
    for mname in MEASURE:
        g = collections.defaultdict(list)
        for r in series:
            v = r[mname]
            if v == v and v > 0:
                g[(r["app"], r["phone"], r["variant"], r["batch"])].append(v)
        mm = {k: st.mean(v) for k, v in g.items()}
        per = collections.defaultdict(list)
        for k, v in mm.items():
            if k[2] == "freerasp_debug" and (k[0], k[1], "baseline_debug", k[3]) in mm:
                per[k[0]].append((mm[(k[0], k[1], "baseline_debug", k[3])], v))
        tab, B, D, P = {}, [], [], []
        for app in sorted(per):
            b = st.mean(x for x, _ in per[app]); f = st.mean(y for _, y in per[app])
            tab[app] = {"baseline_j": round(b, 2), "freerasp_j": round(f, 2),
                        "delta_j": round(f - b, 2), "delta_pct": round(100 * (f - b) / b, 3)}
            B.append(b); D.append(f - b); P.append(100 * (f - b) / b)
        r_ = pearson(B, D)
        fr[mname] = {"per_app": tab, "n_apps": len(tab),
                     "mean_delta_j": round(st.mean(D), 3),
                     "cv_absolute_pct": round(100 * st.stdev(D) / abs(st.mean(D)), 1),
                     "cv_percentage_pct": round(100 * st.stdev(P) / abs(st.mean(P)), 1),
                     "corr_baseline_vs_abs_delta": round(r_, 4)}
        print(f"  {mname:<9} n={len(tab)} mean dJ={st.mean(D):+.2f}  CV abs "
              f"{fr[mname]['cv_absolute_pct']}% vs pct {fr[mname]['cv_percentage_pct']}%  r={r_:+.3f}")
    out["freerasp_scaling"] = fr

    # ---- descriptives + repeatability ---------------------------------------
    print("\n=== descriptive statistics, series, per variant (cell level) ===")
    desc = {}
    for var in ("baseline_debug", "allatori_debug", "bangcle_debug", "freerasp_debug"):
        for mname in MEASURE:
            v = [r[mname] for r in series if r["variant"] == var and r[mname] == r[mname] and r[mname] > 0]
            key = f"{var.replace('_debug','')}_{mname}"
            desc[key] = {"n": len(v), "mean": round(st.mean(v), 3), "sd": round(st.stdev(v), 3),
                         "median": round(st.median(v), 3), "min": round(min(v), 3),
                         "max": round(max(v), 3), "cv_pct": round(100 * st.stdev(v) / st.mean(v), 2)}
            print(f"  {key:<20}n={len(v):<5}mean={st.mean(v):8.2f} sd={st.stdev(v):7.2f} "
                  f"median={st.median(v):8.2f} cv={100*st.stdev(v)/st.mean(v):5.2f}%")
    out["descriptives_series_per_variant"] = desc

    print("\n=== repeatability: pilot vs series, median within-group CV ===")
    def cvs(rows, col):
        g = collections.defaultdict(list)
        for r in rows:
            v = r[col]
            if v == v and v > 0:
                g[(r["app"], r["phone"], r["variant"])].append(v)
        return {k: 100 * st.stdev(v) / st.mean(v) for k, v in g.items() if len(v) >= 3}
    rep_out = {}
    for label, col in (("workload_energy", "workload"), ("startup_energy", "startup"),
                       ("workload_duration", "dur_workload"), ("mean_power", "watts")):
        cp, cs = cvs(data["pilot"], col), cvs(series, col)
        common = sorted(set(cp) & set(cs))
        tighter = sum(1 for k in common if cs[k] < cp[k])
        rep_out[label] = {"groups_common": len(common),
                          "pilot_median_cv": round(st.median(cp[k] for k in common), 3),
                          "series_median_cv": round(st.median(cs[k] for k in common), 3),
                          "tighter_in_series": tighter,
                          "sign_test_p": sign_test_p(tighter, len(common)),
                          "pilot_groups_cv_gt_15": sum(1 for v in cp.values() if v > 15),
                          "pilot_groups": len(cp),
                          "series_groups_cv_gt_15": sum(1 for v in cs.values() if v > 15),
                          "series_groups": len(cs)}
        r_ = rep_out[label]
        print(f"  {label:<18}{r_['pilot_median_cv']:>6.2f}% -> {r_['series_median_cv']:>6.2f}%  "
              f"tighter {tighter}/{len(common)}  CV>15: {r_['pilot_groups_cv_gt_15']}/{r_['pilot_groups']}"
              f" -> {r_['series_groups_cv_gt_15']}/{r_['series_groups']}")
    out["repeatability"] = rep_out

    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
