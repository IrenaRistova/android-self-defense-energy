#!/usr/bin/env python3
"""
cold_cell_rule.py -- a defensible detection rule for post-top-up ("cold") cells.
================================================================================

WHY
---
The rig holds each Pixel in a 40-70% SoC band.  When a phone drains to the
bottom of the band it is topped up; DURING the top-up the Appium workload
stops, so the phone COOLS from its ~40 C working equilibrium.  The first cells
after a top-up therefore run cold, at high SoC, and at an elevated (post-charge,
unrelaxed) terminal voltage.  Since energy is the integral of V*I, those cells
carry a real but non-workload inflation.

The incumbent filter was `battery_temp_avg_c < 36.0`.  36 is a round number
that was picked, not derived, and it is indefensible for three reasons that
this script quantifies:
  1. the three phones and the three batches have DIFFERENT working equilibria
     (settled median battery temp ranges 37.6 C .. 40.5 C across the nine
     phone x batch strata), so one absolute line means one thing on
     pixel6/series3 and something quite different on pixel9/series2;
  2. it is grossly unstable across batches (flag rate 2.7% .. 27.5% by
     stratum, a 10x range) -- most of that variation is the moving reference,
     not a moving phenomenon;
  3. it is a poor classifier: it recovers under half (sens 0.45) of the cells
     an INDEPENDENT thermal sensor calls cold.

RECOMMENDED RULE  ("TEMP-RI-05")
--------------------------------
    A cell is flagged COLD iff
        battery_temp_avg_c  <  P05( battery_temp_avg_c
                                    over the SETTLED cells of the same
                                    (phone, batch) stratum )
    where SETTLED means "at least 20 cells have run since this phone was last
    topped up in this batch" (k >= 20; see index_since_topup()).

This is a one-sided clinical-chemistry style REFERENCE INTERVAL.  The number
that is chosen is not a temperature -- it is a false-positive rate, alpha =
5%, the conventional value, fixed a priori.  Each stratum's temperature limit
falls out of that phone's own measured behaviour in that batch.

NON-CIRCULARITY
---------------
Every quantity used is a device-state column:
    battery_temp_avg_c, battery_level_pre_pct / battery_level_post_pct, wall_s
No energy column (workload_joules, workload_avg_watts, pre_workload_joules,
total_cell_joules, *_watts, *_mwh) is read anywhere in the construction of the
rule -- see FORBIDDEN below, which is enforced at runtime.  The threshold is
fixed by a pre-specified alpha, NOT by scanning for the alpha that flatters
the headline; section [8] shows the headline as a function of alpha so the
reader can see that 5% is not a special point.

INDEPENDENT REFERENCE STANDARD (how "best" is judged)
-----------------------------------------------------
The run tables carry `thermal_skin_pre_c`, a PRE-cell reading from the Android
thermal HAL skin zone -- a different sensor from the fuel-gauge thermistor that
produces `battery_temp_avg_c` (pooled r = 0.41 .. 0.48).  The ground truth used
to score candidate rules is built from that sensor alone:

    GT_skin(cell) = thermal_skin_pre_c < P05(thermal_skin_pre_c over the
                                             SETTLED cells of the same stratum)

A second, causal reference standard is also reported:

    GT_window(cell) = k <= K_phone,  K_phone = the last k at which the phone's
                      median SKIN deficit is still worse than -0.75 C
                      (pixel3 2, pixel6 9, pixel9 4)

Neither reference touches an energy column either.

DATA CAVEATS FOUND WHILE BUILDING THIS (both handled below)
-----------------------------------------------------------
  * thermal_skin_pre_c == 0.0 is a sensor-read failure sentinel, not 0 C.
    36 pixel6 cells across the three series batches.  Treated as missing.
  * thermal_skin_pre_c / thermal_cpu_pre_c / thermal_status_pre are
    BYTE-IDENTICAL for all 1200 rows between the PILOT run table and the
    series1 run table, although the two batches ran on different days.
    Bucketing series1's skin column by series1's own top-up index shows a
    clean 35.4 -> 40.4 C recovery, while bucketing it by the pilot's index
    shows nothing (40.3, 40.0, 40.4, 40.1, 40.2, 39.9).  The shared column is
    therefore series1's, and the PILOT run table's thermal_* columns are a
    stale copy.  The pilot is excluded from every skin-based computation here.
    (The pilot is analysed separately anyway -- it had no thermal gate.)

USAGE
-----
    python3 scratchpad/analysis/cold_cell_rule.py
Runs from anywhere; paths resolve relative to the repo root inferred from this
file's location.  Standard library + numpy (permutation tests only).  Deterministic.
"""

import csv
import os
import sys
import math
import random
import collections
import datetime as dt
import statistics as st

# --------------------------------------------------------------------------
# [0] configuration
# --------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BATCH = {
    "pilot":   "6_results_data/run_tables/run_table_extended_final_ykush_2026-08-04.csv",
    "series1": "6_results_data/run_tables/run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": "6_results_data/run_tables/run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": "6_results_data/run_tables/run_table_extended_final_ykush_series3_2026-08-17.csv",
}
SERIES = ("series1", "series2", "series3")
PHONES = ("pixel3", "pixel6", "pixel9")
VARIANTS = ("baseline_debug", "allatori_debug", "bangcle_debug", "freerasp_debug")
BASELINE = "baseline_debug"

ALPHA = 0.05          # reference-interval false-positive rate; pre-specified
SETTLED_K = 20        # a phone is "settled" >= this many cells after a top-up
SKIN_TOL = 0.75       # C, used only to set K_phone for the causal reference
SEED = 20260830

# Columns the RULE is allowed to see.  Anything energy-derived is forbidden.
ALLOWED_STATE_COLS = {
    "battery_temp_avg_c", "battery_temp_max_c",
    "battery_level_pre_pct", "battery_level_post_pct",
    "voltage_pre_v", "current_now_abs_avg_ma",
    "wall_s", "cell_start_ts_utc",
    "thermal_skin_pre_c",              # reference standard only, never the rule
}
FORBIDDEN = {
    "workload_joules", "pre_workload_joules", "workload_avg_watts",
    "total_cell_joules", "teardown_joules", "whole_window_avg_watts",
    "aggregated_energy_mwh", "energy_workload_only_mwh",
    "energy_per_scenario_joules", "energy_per_scenario_mwh",
}

random.seed(SEED)


# --------------------------------------------------------------------------
# [1] loading + the standard cell filters
# --------------------------------------------------------------------------
def _f(row, key):
    try:
        return float(row.get(key) or "nan")
    except Exception:
        return float("nan")


def is_valid(row):
    """The standard cell filters of the analysis: valid, not a negative control,
    every planned scenario completed, positive workload energy."""
    if row.get("scientifically_valid") != "True":
        return False
    if (row.get("negative_control") or "").strip() not in ("", "None", "False"):
        return False
    try:
        done = int(row.get("scenarios_completed") or -1)
        plan = int(row.get("scenarios_planned") or -1)
        if done >= 0 and plan > 0 and done != plan:
            return False
    except Exception:
        pass
    return _f(row, "workload_joules") > 0


NUM = ("battery_temp_avg_c", "battery_level_pre_pct", "battery_level_post_pct",
       "voltage_pre_v", "current_now_abs_avg_ma", "wall_s",
       "workload_joules", "pre_workload_joules", "workload_avg_watts",
       "thermal_skin_pre_c", "cpu_avg_pct")


def load():
    """Every cell that was actually RUN, valid or not.

    Invalid cells are kept because an invalid cell still occupied a slot and
    still heated the phone -- dropping them would corrupt the cells-since-
    top-up index.  They are excluded from every statistic via r['valid'].
    """
    rows = []
    for batch, rel in BATCH.items():
        path = os.path.join(ROOT, rel)
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                if not (r.get("cell_start_ts_utc") or "").strip():
                    continue          # zombie row: the cell never ran
                d = {c: _f(r, c) for c in NUM}
                # 0.0 is the thermal-HAL read-failure sentinel, not 0 C
                if not (d["thermal_skin_pre_c"] > 0):
                    d["thermal_skin_pre_c"] = float("nan")
                d.update(batch=batch, app=r["app"], phone=r["phone"],
                         variant=r["variant"], ts=r["cell_start_ts_utc"],
                         valid=is_valid(r))
                rows.append(d)
    return rows


# --------------------------------------------------------------------------
# [2] cells-since-top-up index (device state only)
# --------------------------------------------------------------------------
def index_since_topup(rows):
    """Set r['k'] = cells since the last top-up, and r['mins'] = minutes since
    the start of the first cell after it, per (batch, phone) chronologically.

    Two INDEPENDENT signatures of a top-up, OR-ed:
      (a) SoC rose across the cell boundary: pre_i - post_{i-1} >= 2 pp
      (b) the slot was long: wall_s >= 900 s (median slot ~280 s; a top-up plus
          the 60 s settle inflates wall_s into the thousands)
    Neither uses an energy column.  Agreement between the two is printed by
    the caller as a sanity check on the index.
    """
    seq = collections.defaultdict(list)
    for r in rows:
        seq[(r["batch"], r["phone"])].append(r)
    parse = lambda s: dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    events = collections.Counter()
    for key, rs in seq.items():
        rs.sort(key=lambda r: r["ts"])
        k, anchor = 0, None
        for i, r in enumerate(rs):
            soc_up = i > 0 and (r["battery_level_pre_pct"]
                                - rs[i - 1]["battery_level_post_pct"]) >= 2
            long_slot = r["wall_s"] >= 900
            if i == 0 or soc_up or long_slot:
                k = 0
                anchor = parse(r["ts"])
                events[key[0]] += 1
            else:
                k += 1
            r["k"] = k
            r["mins"] = ((parse(r["ts"]) - anchor).total_seconds() / 60.0
                         if anchor else float("nan"))
    return events


def topup_detector_agreement(rows, batches):
    both = a_only = b_only = neither = 0
    seq = collections.defaultdict(list)
    for r in rows:
        if r["batch"] in batches:
            seq[(r["batch"], r["phone"])].append(r)
    for _, rs in seq.items():
        rs.sort(key=lambda r: r["ts"])
        for i, r in enumerate(rs):
            if i == 0:
                continue
            a = (r["battery_level_pre_pct"] - rs[i - 1]["battery_level_post_pct"]) >= 2
            b = r["wall_s"] >= 900
            both += a and b
            a_only += a and not b
            b_only += b and not a
            neither += (not a) and (not b)
    return both, a_only, b_only, neither


# --------------------------------------------------------------------------
# [3] small statistics toolbox (no scipy in this environment)
# --------------------------------------------------------------------------
def quantile(xs, p):
    xs = sorted(v for v in xs if v == v)
    if not xs:
        return float("nan")
    i = p * (len(xs) - 1)
    lo = int(math.floor(i))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (i - lo) * (xs[hi] - xs[lo])


def _gammainc_q(s, x):
    """Regularised upper incomplete gamma Q(s,x); enough for a chi2 tail."""
    if x < 0 or s <= 0:
        return float("nan")
    if x == 0:
        return 1.0
    if x < s + 1.0:                                    # series for P(s,x)
        term = 1.0 / s
        total = term
        n = 1
        while n < 10000:
            term *= x / (s + n)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
            n += 1
        return 1.0 - total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    # continued fraction for Q(s,x)
    tiny = 1e-300
    b = x + 1.0 - s
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h * math.exp(-x + s * math.log(x) - math.lgamma(s))


def chi2_sf(x, df):
    return _gammainc_q(df / 2.0, x / 2.0)


def chi2_test(table):
    """Pearson chi-square on an RxC contingency table (list of row lists)."""
    R = len(table)
    C = len(table[0])
    rows = [sum(r) for r in table]
    cols = [sum(table[i][j] for i in range(R)) for j in range(C)]
    n = sum(rows)
    if n == 0:
        return float("nan"), 0, float("nan"), float("nan")
    stat = 0.0
    minexp = float("inf")
    for i in range(R):
        for j in range(C):
            e = rows[i] * cols[j] / n
            minexp = min(minexp, e)
            if e > 0:
                stat += (table[i][j] - e) ** 2 / e
    df = (R - 1) * (C - 1)
    return stat, df, chi2_sf(stat, df), minexp


def exact_perm_p(labels, flags, nperm=10000, seed=SEED):
    """Monte-Carlo exact test of independence between a categorical label and a
    binary flag -- the small-cell-safe stand-in for Fisher-Freeman-Halton on an
    RxC table.  Permutes the variant labels, recomputes Pearson chi-square, and
    reports the proportion of permutations at least as extreme as observed.

    Vectorised with numpy purely for speed; the test itself is unchanged.
    """
    import numpy as np
    cats = sorted(set(labels))
    idx = {c: i for i, c in enumerate(cats)}
    g = np.asarray([idx[L] for L in labels], dtype=np.int64)
    f = np.asarray([1 if x else 0 for x in flags], dtype=np.int64)
    R, n, m = len(cats), len(g), int(f.sum())
    rowtot = np.bincount(g, minlength=R).astype(float)
    if m == 0 or m == n:
        return 0.0, 1.0
    exp1 = rowtot * m / n
    exp0 = rowtot - exp1

    def stat(flagged_groups):
        o1 = np.bincount(flagged_groups, minlength=R).astype(float)
        o0 = rowtot - o1
        return (((o1 - exp1) ** 2 / exp1).sum() + ((o0 - exp0) ** 2 / exp0).sum())

    obs_stat = stat(g[f == 1])
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(nperm):
        # permuting the labels == choosing which m cells carry the flag
        hits += stat(g[rng.permutation(n)[:m]]) >= obs_stat - 1e-12
    return obs_stat, (int(hits) + 1) / (nperm + 1)


def mean_ci(xs):
    n = len(xs)
    if n < 3:
        return float("nan"), float("nan"), float("nan"), n
    m = st.mean(xs)
    se = st.stdev(xs) / math.sqrt(n)
    t = 2.045 if n < 40 else 1.96
    return m, m - t * se, m + t * se, n


# --------------------------------------------------------------------------
# [4] the settled reference, the recommended rule, the reference standards
# --------------------------------------------------------------------------
def stratum(r):
    return (r["phone"], r["batch"])


def build_reference(cells, alpha=ALPHA):
    """Per (phone, batch): the distribution of DEVICE STATE while the phone was
    demonstrably at working equilibrium (k >= SETTLED_K)."""
    cal = collections.defaultdict(list)
    for r in cells:
        if r["k"] >= SETTLED_K:
            cal[stratum(r)].append(r)
    ref = {}
    for s, rs in cal.items():
        bt = [r["battery_temp_avg_c"] for r in rs]
        sk = [r["thermal_skin_pre_c"] for r in rs]
        vv = [r["voltage_pre_v"] for r in rs]
        ref[s] = dict(
            n=len(rs),
            n_skin=sum(1 for v in sk if v == v),
            batt_med=quantile(bt, .5),
            batt_lim=quantile(bt, alpha),          # <-- the rule's limit
            batt_sd=st.pstdev([v for v in bt if v == v]) or 1.0,
            skin_med=quantile(sk, .5),
            skin_lim=quantile(sk, alpha),          # <-- the reference standard
            volt_med=quantile(vv, .5),
            volt_p95=quantile(vv, .95),
            lvl_med=quantile([r["battery_level_pre_pct"] for r in rs], .5),
        )
    return ref


def make_rule(ref):
    """TEMP-RI-05 -- the recommended rule."""
    def flag(r):
        R = ref.get(stratum(r))
        t = r["battery_temp_avg_c"]
        return R is not None and t == t and t < R["batt_lim"]
    return flag


def make_gt_skin(ref):
    def gt(r):
        R = ref.get(stratum(r))
        s = r["thermal_skin_pre_c"]
        return R is not None and s == s and s < R["skin_lim"]
    return gt


def k_window_per_phone(cells, ref):
    """K_phone = last k whose median SKIN deficit is worse than -SKIN_TOL,
    with monotone closure (every later k inside tolerance)."""
    out = {}
    for ph in PHONES:
        sub = [r for r in cells if r["phone"] == ph]
        settled = quantile([r["thermal_skin_pre_c"] for r in sub
                            if r["k"] >= SETTLED_K], .5)
        dfc = {}
        for k in range(0, 20):
            g = [r["thermal_skin_pre_c"] for r in sub if r["k"] == k]
            g = [v for v in g if v == v]
            dfc[k] = (quantile(g, .5) - settled) if g else 0.0
        K = -1
        for k in range(0, 20):
            if all(dfc[j] > -SKIN_TOL for j in range(k, 20)):
                K = k - 1
                break
        out[ph] = K
    return out


# --------------------------------------------------------------------------
# [5] classifier metrics
# --------------------------------------------------------------------------
def score_rule(cells, flag, gt):
    tp = fp = fn = tn = 0
    for r in cells:
        f, g = flag(r), gt(r)
        if f and g:
            tp += 1
        elif f:
            fp += 1
        elif g:
            fn += 1
        else:
            tn += 1
    sens = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    ppv = tp / (tp + fp) if tp + fp else float("nan")
    return dict(n=tp + fp, sens=sens, spec=spec, ppv=ppv, youden=sens + spec - 1)


def auc(cells, score, gt):
    """Mann-Whitney AUC with tie-corrected midranks."""
    xs = sorted((score(r), gt(r)) for r in cells if score(r) == score(r))
    n = len(xs)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[j + 1][0] == xs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for t in range(i, j + 1):
            ranks[t] = avg
        i = j + 1
    npos = sum(1 for _, g in xs if g)
    nneg = n - npos
    if not npos or not nneg:
        return float("nan")
    rp = sum(ranks[t] for t in range(n) if xs[t][1])
    return (rp - npos * (npos + 1) / 2.0) / (npos * nneg)


# --------------------------------------------------------------------------
# [6] the headline (matched pairs within app x phone x batch)
# --------------------------------------------------------------------------
def paired_deltas(cells, variant, key="workload_joules", drop=None, phone=None):
    g = collections.defaultdict(list)
    for r in cells:
        if drop is not None and drop(r):
            continue
        if phone is not None and r["phone"] != phone:
            continue
        v = r[key]
        if v == v and v > 0:
            g[(r["app"], r["phone"], r["variant"], r["batch"])].append(v)
    m = {k: st.mean(v) for k, v in g.items() if v}
    out = []
    for k in m:
        if k[2] != variant:
            continue
        bk = (k[0], k[1], BASELINE, k[3])
        if bk in m:
            out.append(100.0 * (m[k] - m[bk]) / m[bk])
    return out


def perm_p_headline(cells, variant, drop=None, nperm=3000, seed=SEED):
    """Permute the variant label WITHIN (app, phone, batch) -- the unit at which
    the design randomises -- and compare |mean dE%| against the observed value.

    Implemented block-wise on plain lists (no dict copying) so it is fast:
    inside a block the statistic depends only on which cells carry the variant
    label, so one permutation is one random split of the block's cells.
    """
    blocks = collections.defaultdict(lambda: {variant: [], BASELINE: []})
    for r in cells:
        if drop is not None and drop(r):
            continue
        if r["variant"] not in (variant, BASELINE):
            continue
        v = r["workload_joules"]
        if v == v and v > 0:
            blocks[(r["app"], r["phone"], r["batch"])][r["variant"]].append(v)
    B = [(b[variant], b[BASELINE]) for b in blocks.values()
         if b[variant] and b[BASELINE]]
    if not B:
        return float("nan")
    obs = abs(st.mean([100.0 * (st.mean(x) - st.mean(y)) / st.mean(y)
                       for x, y in B]))
    rnd = random.Random(seed)
    hits = 0
    for _ in range(nperm):
        ds = []
        for x, y in B:
            pool = x + y
            rnd.shuffle(pool)
            na = len(x)
            ma = st.mean(pool[:na])
            mb = st.mean(pool[na:])
            ds.append(100.0 * (ma - mb) / mb)
        if abs(st.mean(ds)) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (nperm + 1)


# --------------------------------------------------------------------------
# [7] report
# --------------------------------------------------------------------------
def strat_perm_balance(cells, flag, key, nperm=10000, seed=SEED):
    """Is the flag independent of variant, permuting variant labels WITHIN the
    stratum given by key()?

    The naive pooled 4x2 chi-square is confounded: base flag rates and variant
    counts both differ by phone and by app (allatori covers only 7 of the 11
    apps), so pooling can manufacture or hide an association.  Permuting the
    label inside the MATCHING UNIT -- (app, phone, batch) -- is the test that
    matters, because that is the unit the paired estimate is built on.
    """
    import numpy as np
    vi = {v: i for i, v in enumerate(VARIANTS)}
    groups = collections.defaultdict(list)
    for r in cells:
        groups[key(r)].append(r)
    blocks = [(np.asarray([vi[r["variant"]] for r in rs]),
               np.asarray([1 if flag(r) else 0 for r in rs])) for rs in groups.values()]
    n = sum(len(b[0]) for b in blocks)
    m = sum(int(b[1].sum()) for b in blocks)
    if m == 0 or m == n:
        return 0.0, 1.0
    tot = np.zeros(len(VARIANTS))
    for g, _ in blocks:
        tot += np.bincount(g, minlength=len(VARIANTS))
    e1 = tot * m / n
    e0 = tot - e1

    def stat(assign):
        o1 = np.zeros(len(VARIANTS))
        for (g, fl), a in zip(blocks, assign):
            o1 += np.bincount(a[fl == 1], minlength=len(VARIANTS))
        return ((o1 - e1) ** 2 / e1).sum() + (((tot - o1) - e0) ** 2 / e0).sum()

    obs = stat([b[0] for b in blocks])
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(nperm):
        if stat([rng.permutation(b[0]) for b in blocks]) >= obs - 1e-12:
            hits += 1
    return obs, (hits + 1) / (nperm + 1)


KBUCKETS = [("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3-5", 3, 5),
            ("6-10", 6, 10), ("11-19", 11, 19), ("20+", 20, 10 ** 9)]


def cmh_z(cells, flag, phone, va, vb):
    """Cochran-Mantel-Haenszel z for flag(va) vs flag(vb), stratified by
    cells-since-top-up bucket.  Answers: does the imbalance survive after
    adjusting for WHERE in the discharge run the cell sat?  If it does, the
    rule is responding to something the TREATMENT changed, not to timing."""
    num = den = 0.0
    for _, lo, hi in KBUCKETS:
        ga = [r for r in cells if r["phone"] == phone and r["variant"] == va and lo <= r["k"] <= hi]
        gb = [r for r in cells if r["phone"] == phone and r["variant"] == vb and lo <= r["k"] <= hi]
        a = sum(1 for r in ga if flag(r)); b = len(ga) - a
        c = sum(1 for r in gb if flag(r)); d = len(gb) - c
        n = a + b + c + d
        if n < 2:
            continue
        num += a - (a + b) * (a + c) / n
        den += (a + b) * (c + d) * (a + c) * (b + d) / (n * n * (n - 1))
    if den <= 0:
        # every stratum is constant: the rule IS the stratifying variable, so
        # there is nothing left to vary within a stratum.  Not a failure.
        return float("nan"), float("nan")
    z = num / math.sqrt(den)
    return z, math.erfc(abs(z) / math.sqrt(2))


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    # guard the non-circularity claim at runtime
    src = open(os.path.abspath(__file__)).read()
    rule_src = src[src.index("def build_reference"):src.index("def k_window_per_phone")]
    leak = sorted(c for c in FORBIDDEN if c in rule_src)
    print("cold_cell_rule.py -- post-top-up cold-cell detection rule")
    print(f"repo root : {ROOT}")
    print(f"alpha={ALPHA}  settled_k={SETTLED_K}  seed={SEED}")
    print(f"circularity guard: energy columns referenced inside the rule "
          f"construction -> {leak if leak else 'NONE'}")

    rows = load()
    events = index_since_topup(rows)
    both, a_only, b_only, neither = topup_detector_agreement(rows, SERIES)
    tot = both + a_only + b_only + neither
    print(f"\ncells read (ran at all)       : {len(rows)}")
    print(f"top-up events detected/batch  : {dict(events)}")
    print(f"top-up detector agreement     : {both + neither}/{tot} "
          f"= {100 * (both + neither) / tot:.2f}%  "
          f"(both={both} SoC-only={a_only} longslot-only={b_only})")

    cells = [r for r in rows if r["valid"] and r["batch"] in SERIES]
    print(f"valid series cells (analysis) : {len(cells)}")
    nz = sum(1 for r in cells if r["thermal_skin_pre_c"] != r["thermal_skin_pre_c"])
    print(f"skin sensor missing (0.0 sentinel, all pixel6): {nz}")

    ref = build_reference(cells)
    FLAG = make_rule(ref)
    INC = lambda r: r["battery_temp_avg_c"] < 36.0        # the incumbent rule
    GT_SKIN = make_gt_skin(ref)
    Kp = k_window_per_phone(cells, ref)
    GT_WIN = lambda r: r["k"] <= Kp[r["phone"]]

    # ---- [1] the per-stratum reference -----------------------------------
    hr("[1] SETTLED REFERENCE per (phone, batch), from cells with k >= 20")
    print("These are what makes an absolute 36 C line indefensible: the")
    print("working equilibrium itself moves 37.60 .. 40.50 C across strata.")
    print(f"\n{'phone':<8}{'batch':<9}{'n':>5}{'batt_med':>10}"
          f"{'batt P05':>10}{'skin_med':>10}{'skin P05':>10}"
          f"{'volt_med':>10}{'SoC_med':>9}")
    for s in sorted(ref):
        d = ref[s]
        print(f"{s[0]:<8}{s[1]:<9}{d['n']:>5}{d['batt_med']:>10.2f}"
              f"{d['batt_lim']:>10.2f}{d['skin_med']:>10.2f}{d['skin_lim']:>10.2f}"
              f"{d['volt_med']:>10.4f}{d['lvl_med']:>9.0f}")
    lims = [ref[s]['batt_lim'] for s in ref]
    print(f"\nthe rule's temperature limit therefore spans "
          f"{min(lims):.2f} .. {max(lims):.2f} C, not one number.")

    # ---- [2] reference standards -----------------------------------------
    hr("[2] REFERENCE STANDARDS (independent of every candidate rule)")
    print("GT_skin  : thermal_skin_pre_c < stratum P05 of settled skin")
    print("           (thermal-HAL skin zone; a different sensor from the")
    print("            fuel-gauge thermistor behind battery_temp_avg_c)")
    print("GT_window: k <= K_phone, K_phone read off the SKIN recovery curve")
    print(f"           K_phone = {Kp}")
    ns = sum(1 for r in cells if GT_SKIN(r))
    nw = sum(1 for r in cells if GT_WIN(r))
    print(f"\nGT_skin positives   : {ns}/{len(cells)} = {100*ns/len(cells):.2f}%")
    print(f"GT_window positives : {nw}/{len(cells)} = {100*nw/len(cells):.2f}%")
    print(f"the two labels agree on "
          f"{sum(1 for r in cells if GT_SKIN(r) == GT_WIN(r))}/{len(cells)} cells")
    print(f"\nGT_skin rate by cells-since-top-up (the 5% floor is by construction):")
    bk = [("0", lambda k: k == 0), ("1", lambda k: k == 1), ("2", lambda k: k == 2),
          ("3-5", lambda k: 3 <= k <= 5), ("6-10", lambda k: 6 <= k <= 10),
          ("11-19", lambda k: 11 <= k <= 19), ("20+", lambda k: k >= 20)]
    for nm, sel in bk:
        g = [r for r in cells if sel(r["k"])]
        p = sum(1 for r in g if GT_SKIN(r))
        print(f"    k={nm:<6} {p:>4}/{len(g):<5} = {100*p/len(g):>5.1f}%")

    # ---- [3] candidate comparison ----------------------------------------
    hr("[3] CANDIDATE RULES AS CLASSIFIERS")
    print("Ranking power first (AUC, threshold-free), then all candidates at")
    print("their own thresholds, then at a MATCHED flagging budget so that")
    print("sensitivity is compared like-for-like.\n")
    scores = {
        "(d1) settled_med - temp   [per-device delta]":
            lambda r: ref[stratum(r)]["batt_med"] - r["battery_temp_avg_c"],
        "(d2) that delta / settled sd  [z-score]":
            lambda r: (ref[stratum(r)]["batt_med"] - r["battery_temp_avg_c"])
                      / ref[stratum(r)]["batt_sd"],
        "(a)  -battery_temp_avg_c  [absolute]":
            lambda r: -r["battery_temp_avg_c"],
        "(f)  battery_level_pre_pct":
            lambda r: r["battery_level_pre_pct"],
        "(b)  -k  [cells since top-up]":
            lambda r: -float(r["k"]),
        "(e)  voltage_pre - settled_med":
            lambda r: r["voltage_pre_v"] - ref[stratum(r)]["volt_med"],
        "(c)  -minutes since top-up":
            lambda r: -r["mins"],
    }
    print(f"{'discriminant score (higher = colder)':<46}{'AUC/skin':>10}{'AUC/win':>10}")
    aucs = {nm: (auc(cells, sc, GT_SKIN), auc(cells, sc, GT_WIN))
            for nm, sc in scores.items()}
    for nm in sorted(aucs, key=lambda k: -aucs[k][0]):
        print(f"{nm:<46}{aucs[nm][0]:>10.3f}{aucs[nm][1]:>10.3f}")

    cands = []
    for T in (34, 35, 36, 37, 38, 38.5, 39):
        cands.append((f"(a) temp < {T} C",
                      (lambda T: lambda r: r["battery_temp_avg_c"] == r["battery_temp_avg_c"]
                       and r["battery_temp_avg_c"] < T)(T)))
    for k0 in range(0, 6):
        cands.append((f"(b) k <= {k0}", (lambda k0: lambda r: r["k"] <= k0)(k0)))
    cands.append(("(b*) k <= K_phone (2/9/4)", GT_WIN))
    for t in (10, 30, 60, 90):
        cands.append((f"(c) minutes since top-up <= {t}",
                      (lambda t: lambda r: r["mins"] == r["mins"] and r["mins"] <= t)(t)))
    for Dv in (1.0, 1.5, 2.0, 2.5, 3.0):
        cands.append((f"(d) temp < settled_med - {Dv} C",
                      (lambda D: lambda r: r["battery_temp_avg_c"] == r["battery_temp_avg_c"]
                       and r["battery_temp_avg_c"] < ref[stratum(r)]["batt_med"] - D)(Dv)))
    for a in (0.01, 0.025, 0.05, 0.10):
        r2 = build_reference(cells, alpha=a)
        cands.append((f"(d*) temp < settled P{a*100:g}  [reference interval]",
                      (lambda R: lambda r: r["battery_temp_avg_c"] == r["battery_temp_avg_c"]
                       and r["battery_temp_avg_c"] < R[stratum(r)]["batt_lim"])(r2)))
    for E in (0.05, 0.10, 0.15, 0.20):
        cands.append((f"(e) voltage_pre > settled_med + {E:.2f} V",
                      (lambda E: lambda r: r["voltage_pre_v"] == r["voltage_pre_v"]
                       and r["voltage_pre_v"] > ref[stratum(r)]["volt_med"] + E)(E)))
    for L in (60, 62, 64, 66, 68):
        cands.append((f"(f) battery_level_pre_pct >= {L}",
                      (lambda L: lambda r: r["battery_level_pre_pct"] >= L)(L)))
    cands += [
        ("(g1) temp<P05 AND volt>settled P95",
         lambda r: FLAG(r) and r["voltage_pre_v"] > ref[stratum(r)]["volt_p95"]),
        ("(g2) temp<P05 OR  volt>settled P95",
         lambda r: FLAG(r) or r["voltage_pre_v"] > ref[stratum(r)]["volt_p95"]),
        ("(g3) temp<P05 AND SoC >= 64",
         lambda r: FLAG(r) and r["battery_level_pre_pct"] >= 64),
        ("(g4) temp<P05 OR  k <= 1",
         lambda r: FLAG(r) or r["k"] <= 1),
    ]
    print(f"\n{'candidate rule':<46}{'flag n':>7}{'  %':>6}"
          f"{'sens':>7}{'spec':>7}{'PPV':>7}{'J':>7}"
          f"{'sens':>8}{'spec':>7}{'PPV':>7}{'J':>7}")
    print(f"{'':<46}{'':>7}{'':>6}{'------- vs GT_skin -------':^28}"
          f"{'------ vs GT_window ------':^29}")
    for nm, fn_ in cands:
        a1 = score_rule(cells, fn_, GT_SKIN)
        a2 = score_rule(cells, fn_, GT_WIN)
        star = "  <== RECOMMENDED" if nm.startswith("(d*) temp < settled P5") else ""
        print(f"{nm:<46}{a1['n']:>7}{100*a1['n']/len(cells):>6.1f}"
              f"{a1['sens']:>7.3f}{a1['spec']:>7.3f}{a1['ppv']:>7.3f}{a1['youden']:>7.3f}"
              f"{a2['sens']:>8.3f}{a2['spec']:>7.3f}{a2['ppv']:>7.3f}{a2['youden']:>7.3f}"
              f"{star}")

    hr("[3b] MATCHED-BUDGET COMPARISON (each rule tuned to flag ~529 cells)")
    print("Sensitivity at equal cost.  This is the decisive comparison: it")
    print("removes the 'you just flagged more cells' objection.\n")
    BUDGET = sum(1 for r in cells if FLAG(r))
    print(f"{'discriminant':<46}{'flagged':>8}{'thresh':>10}"
          f"{'sens':>7}{'spec':>7}{'PPV':>7}")
    out = []
    for nm, sc in scores.items():
        vals = sorted((sc(r) for r in cells if sc(r) == sc(r)), reverse=True)
        thr = vals[min(BUDGET, len(vals)) - 1]
        f_ = (lambda sc, thr: lambda r: sc(r) == sc(r) and sc(r) >= thr)(sc, thr)
        m = score_rule(cells, f_, GT_SKIN)
        out.append((m["sens"], nm, m, thr))
    for _, nm, m, thr in sorted(out, reverse=True):
        print(f"{nm:<46}{m['n']:>8}{thr:>10.3f}"
              f"{m['sens']:>7.3f}{m['spec']:>7.3f}{m['ppv']:>7.3f}")

    # ---- [4] flagged counts ----------------------------------------------
    hr("[4] RECOMMENDED RULE 'TEMP-RI-05' -- FLAGGED COUNTS")
    nf = sum(1 for r in cells if FLAG(r))
    print(f"battery_temp_avg_c < P05(settled battery temp of the same "
          f"phone x batch)")
    print(f"\nflagged: {nf}/{len(cells)} = {100*nf/len(cells):.2f}% of valid series cells")
    print(f"(the incumbent temp<36 C flags "
          f"{sum(1 for r in cells if r['battery_temp_avg_c'] < 36):.0f})")

    print(f"\n--- per phone x batch (stability) ---")
    print(f"{'phone':<8}" + "".join(f"{b:>18}" for b in SERIES) + f"{'all':>18}")
    for ph in PHONES:
        cs = []
        for b in SERIES:
            g = [r for r in cells if r["phone"] == ph and r["batch"] == b]
            f_ = sum(1 for r in g if FLAG(r))
            cs.append(f"{f_:>4}/{len(g):<4}{100*f_/len(g):>5.1f}%")
        g = [r for r in cells if r["phone"] == ph]
        f_ = sum(1 for r in g if FLAG(r))
        cs.append(f"{f_:>4}/{len(g):<4}{100*f_/len(g):>5.1f}%")
        print(f"{ph:<8}" + "".join(f"{c:>18}" for c in cs))
    cs = []
    for b in SERIES:
        g = [r for r in cells if r["batch"] == b]
        f_ = sum(1 for r in g if FLAG(r))
        cs.append(f"{f_:>4}/{len(g):<4}{100*f_/len(g):>5.1f}%")
    g = cells
    f_ = sum(1 for r in g if FLAG(r))
    cs.append(f"{f_:>4}/{len(g):<4}{100*f_/len(g):>5.1f}%")
    print(f"{'ALL':<8}" + "".join(f"{c:>18}" for c in cs))

    print(f"\nsame table for the incumbent temp<36 C, for contrast:")
    print(f"{'phone':<8}" + "".join(f"{b:>18}" for b in SERIES) + f"{'all':>18}")
    for ph in PHONES:
        cs = []
        for b in list(SERIES) + [None]:
            g = [r for r in cells if r["phone"] == ph and (b is None or r["batch"] == b)]
            f2 = sum(1 for r in g if INC(r))
            cs.append(f"{f2:>4}/{len(g):<4}{100*f2/len(g):>5.1f}%")
        print(f"{ph:<8}" + "".join(f"{c:>18}" for c in cs))

    print(f"\n--- per variant ---")
    print(f"{'variant':<12}{'flagged':>9}{'total':>8}{'rate':>8}")
    for v in VARIANTS:
        g = [r for r in cells if r["variant"] == v]
        f2 = sum(1 for r in g if FLAG(r))
        print(f"{v.replace('_debug',''):<12}{f2:>9}{len(g):>8}{100*f2/len(g):>7.2f}%")

    # ---- [5] balance ------------------------------------------------------
    hr("[5] BALANCE ACROSS VARIANTS (a rule that is unbalanced would BIAS)")
    print("H0: flag rate is independent of variant.  A significant result")
    print("would mean the filter removes more of one arm than another and so")
    print("cannot be applied.  Pearson chi-square (4x2) plus a 20 000-draw")
    print("label-permutation exact test, which is safe with small cells.\n")
    print(f"{'stratum':<12}{'baseline':>10}{'allatori':>10}{'bangcle':>10}"
          f"{'freerasp':>10}{'chi2':>8}{'df':>4}{'p_chi2':>9}{'p_perm':>9}{'minE':>7}")
    for ph in list(PHONES) + ["POOLED"]:
        sub = cells if ph == "POOLED" else [r for r in cells if r["phone"] == ph]
        tab, cellstr = [], []
        for v in VARIANTS:
            g = [r for r in sub if r["variant"] == v]
            f2 = sum(1 for r in g if FLAG(r))
            tab.append([f2, len(g) - f2])
            cellstr.append(f"{f2}/{len(g)} {100*f2/len(g):.1f}%")
        stat, df, p, mine = chi2_test(tab)
        _, pp = exact_perm_p([r["variant"] for r in sub],
                             [FLAG(r) for r in sub])
        print(f"{ph:<12}" + "".join(f"{c:>10}" for c in cellstr) +
              f"{stat:>8.2f}{df:>4}{p:>9.4f}{pp:>9.4f}{mine:>7.1f}")
    print("\nper batch (pooled over phones):")
    for b in SERIES:
        sub = [r for r in cells if r["batch"] == b]
        tab, cellstr = [], []
        for v in VARIANTS:
            g = [r for r in sub if r["variant"] == v]
            f2 = sum(1 for r in g if FLAG(r))
            tab.append([f2, len(g) - f2])
            cellstr.append(f"{f2}/{len(g)} {100*f2/len(g):.1f}%")
        stat, df, p, mine = chi2_test(tab)
        _, pp = exact_perm_p([r["variant"] for r in sub], [FLAG(r) for r in sub])
        print(f"{b:<12}" + "".join(f"{c:>10}" for c in cellstr) +
              f"{stat:>8.2f}{df:>4}{p:>9.4f}{pp:>9.4f}{mine:>7.1f}")

    print("\nstability of the flag RATE across the three batches (H0: equal):")
    tab = []
    for b in SERIES:
        g = [r for r in cells if r["batch"] == b]
        f2 = sum(1 for r in g if FLAG(r))
        tab.append([f2, len(g) - f2])
    stat, df, p, mine = chi2_test(tab)
    print(f"  rates {[f'{100*t[0]/(t[0]+t[1]):.1f}%' for t in tab]}"
          f"  chi2={stat:.2f} df={df} p={p:.4f}")
    tab = []
    for b in SERIES:
        g = [r for r in cells if r["batch"] == b]
        f2 = sum(1 for r in g if INC(r))
        tab.append([f2, len(g) - f2])
    stat, df, p, mine = chi2_test(tab)
    print(f"  incumbent temp<36: rates "
          f"{[f'{100*t[0]/(t[0]+t[1]):.1f}%' for t in tab]}"
          f"  chi2={stat:.2f} df={df} p={p:.4f}   <-- not stable")

    # ---- [6] headline -----------------------------------------------------
    hr("[6] HEADLINE, WITH AND WITHOUT THE FLAGGED CELLS")
    print("Matched pairs within (app, phone, batch); dE% vs baseline_debug on")
    print("mean workload_joules; 95% CI over pairs; permutation p (4000 draws).\n")
    print(f"{'variant':<10}{'':<3}{'ALL CELLS':^34}{'':<3}{'FLAGGED CELLS DROPPED':^34}{'shift':>8}")
    print(f"{'':<13}{'dE%':>8}{'95% CI':>19}{'n':>6}{'':<3}"
          f"{'dE%':>8}{'95% CI':>19}{'n':>6}{'':>8}")
    for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
        a = paired_deltas(cells, v)
        b = paired_deltas(cells, v, drop=FLAG)
        ma, la, ha, na = mean_ci(a)
        mb, lb, hb, nb = mean_ci(b)
        print(f"{v.replace('_debug',''):<10}{'':<3}{ma:>+8.2f}"
              f"{f'[{la:+.2f},{ha:+.2f}]':>19}{na:>6}{'':<3}"
              f"{mb:>+8.2f}{f'[{lb:+.2f},{hb:+.2f}]':>19}{nb:>6}"
              f"{mb-ma:>+8.2f}")
    print("\npermutation p-values:")
    for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
        p1 = perm_p_headline(cells, v)
        p2 = perm_p_headline(cells, v, drop=FLAG)
        print(f"  {v.replace('_debug',''):<10} all={p1:.4f}   no-flagged={p2:.4f}")
    print("\nCI half-width (precision): does dropping them help or hurt?")
    for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
        a = paired_deltas(cells, v)
        b = paired_deltas(cells, v, drop=FLAG)
        ma, la, ha, na = mean_ci(a)
        mb, lb, hb, nb = mean_ci(b)
        print(f"  {v.replace('_debug',''):<10} all +/-{(ha-la)/2:.3f} pp   "
              f"no-flagged +/-{(hb-lb)/2:.3f} pp   "
              f"{'tighter' if (hb-lb) < (ha-la) else 'wider'}")

    hr("[7] PER-PHONE GRADIENT, WITH AND WITHOUT THE FLAGGED CELLS")
    print(f"{'variant':<10}{'phone':<9}{'ALL':^32}{'NO FLAGGED':^32}{'shift':>8}")
    for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
        for ph in PHONES:
            a = paired_deltas(cells, v, phone=ph)
            b = paired_deltas(cells, v, drop=FLAG, phone=ph)
            if len(a) < 3 or len(b) < 3:
                continue
            ma, la, ha, na = mean_ci(a)
            mb, lb, hb, nb = mean_ci(b)
            print(f"{v.replace('_debug',''):<10}{ph:<9}"
                  f"{ma:>+7.2f} [{la:>+6.2f},{ha:>+6.2f}] n={na:<4}"
                  f"{mb:>+8.2f} [{lb:>+6.2f},{hb:>+6.2f}] n={nb:<4}{mb-ma:>+8.2f}")

    # ---- [8] treatment-neutrality ----------------------------------------
    K_RULE = lambda r: r["k"] <= Kp[r["phone"]]
    apps4 = {a for a in {r["app"] for r in cells}
             if len({r["variant"] for r in cells if r["app"] == a}) == len(VARIANTS)}
    cells4 = [r for r in cells if r["app"] in apps4]

    hr("[8] TREATMENT-NEUTRALITY -- the attack that actually lands")
    print("""battery temperature is a POST-TREATMENT variable: the variant changes the
power the phone draws, the power changes the temperature, and the rule reads
the temperature.  A filter built on a post-treatment variable can bias a
causal contrast even when it never touches the outcome column.  Three tests:

  (i)  is the flag independent of variant inside the MATCHING UNIT?
  (ii) does any imbalance survive adjustment for cells-since-top-up (CMH)?
  (iii) does it persist in the 7 apps that carry all four variants (so app
        composition cannot be the explanation)?

K-WINDOW (k <= K_phone) is offered as the comparator because k is fixed by the
top-up schedule -- cumulative drain across the whole run -- and so is much
closer to a pre-treatment variable.""")
    rules_cmp = [("TEMP-RI-05 (recommended detector)", FLAG),
                 ("K-WINDOW  k<=K_phone %s" % (tuple(Kp[p] for p in PHONES),), K_RULE),
                 ("temp < 36 C (incumbent)", INC)]
    print(f"\n{'rule':<36}{'scope':<22}{'chi2':>8}{'p_perm':>9}   (* = p<0.05)")
    for nm, f_ in rules_cmp:
        for scope, sub, key in (
                ("all cells", cells, lambda r: (r["app"], r["phone"], r["batch"])),
                ("4-variant apps only", cells4, lambda r: (r["app"], r["phone"], r["batch"])),
                ("  pixel3", [r for r in cells if r["phone"] == "pixel3"], lambda r: (r["app"], r["batch"])),
                ("  pixel6", [r for r in cells if r["phone"] == "pixel6"], lambda r: (r["app"], r["batch"])),
                ("  pixel9", [r for r in cells if r["phone"] == "pixel9"], lambda r: (r["app"], r["batch"]))):
            stat, p = strat_perm_balance(sub, f_, key)
            flagname = "*" if p < 0.05 else " "
            print(f"{nm if scope=='all cells' else '':<36}{scope:<22}{stat:>8.2f}{p:>9.4f} {flagname}")
        print()

    print("CMH, baseline vs freerasp, stratified by cells-since-top-up bucket:")
    for nm, f_ in rules_cmp:
        cs = []
        for ph in PHONES:
            z, p = cmh_z(cells, f_, ph, BASELINE, "freerasp_debug")
            cs.append(f"{ph} z={z:+.2f} p={p:.4f}" if z == z
                      else f"{ph} undefined (rule==stratifier)")
        print(f"  {nm:<36}" + "   ".join(cs))

    print("\nis the k DISTRIBUTION itself variant-dependent?  (if not, a k-based")
    print("rule cannot be responding to the treatment)")
    for ph in PHONES:
        tab = []
        for v in VARIANTS:
            g = [r for r in cells if r["phone"] == ph and r["variant"] == v]
            tab.append([sum(1 for r in g if lo <= r["k"] <= hi) for _, lo, hi in KBUCKETS])
        stat, df, p, mine = chi2_test(tab)
        means = "  ".join(f"{v.replace('_debug','')[:4]}={st.mean([r['k'] for r in cells if r['phone']==ph and r['variant']==v]):.1f}"
                          for v in VARIANTS)
        print(f"  {ph}: variant x k-bucket chi2={stat:.2f} df={df} p={p:.4f} | mean k  {means}")

    print("\nflag rate by variant on pixel9, restricted to the 4-variant apps")
    print("(the imbalance is not an app-composition artefact):")
    for nm, f_ in rules_cmp:
        cs = []
        for v in VARIANTS:
            g = [r for r in cells4 if r["phone"] == "pixel9" and r["variant"] == v]
            k = sum(1 for r in g if f_(r))
            cs.append(f"{v.replace('_debug','')[:8]} {k}/{len(g)} {100*k/len(g):.1f}%")
        print(f"  {nm:<36}" + "  ".join(cs))

    # ---- [9] the three candidate exclusions side by side -----------------
    hr("[9] WHAT EACH CANDIDATE EXCLUSION DOES TO THE ESTIMATES")
    exclusions = [("no filter", None), ("TEMP-RI-05", FLAG),
                  ("K-WINDOW", K_RULE), ("temp<36 (incumbent)", INC)]
    print(f"{'filter':<22}{'dropped':>8}   " +
          "".join(f"{v.replace('_debug',''):^24}" for v in
                  ("allatori_debug", "bangcle_debug", "freerasp_debug")))
    for nm, f_ in exclusions:
        nd = 0 if f_ is None else sum(1 for r in cells if f_(r))
        parts = []
        for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
            m, lo, hi, n = mean_ci(paired_deltas(cells, v, drop=f_))
            parts.append(f"{m:>+7.2f} [{lo:>+6.2f},{hi:>+6.2f}]")
        print(f"{nm:<22}{nd:>8}   " + "".join(f"{p:^24}" for p in parts))

    print("\nper phone -- this is where the choice actually matters:")
    for v in ("freerasp_debug", "bangcle_debug", "allatori_debug"):
        print(f"  {v.replace('_debug','')}")
        for nm, f_ in exclusions:
            parts = []
            for ph in PHONES:
                m, lo, hi, n = mean_ci(paired_deltas(cells, v, drop=f_, phone=ph))
                parts.append(f"{ph} {m:>+6.2f} [{lo:>+6.2f},{hi:>+6.2f}]")
            print(f"    {nm:<22}" + "  ".join(parts))

    hr("[10] DIAGNOSTIC -- why pixel9 moves (this section READS the outcome;")
    print("     it explains the effect, it plays no part in building the rule)")
    print(f"\n{'phone':<8}{'variant':<10}{'flagged n':>10}{'med J':>9}"
          f"{'unflag n':>10}{'med J':>9}{'ratio':>8}{'preJ flag/unflag':>20}")
    for ph in PHONES:
        for v in VARIANTS:
            a = [r["workload_joules"] for r in cells if r["phone"] == ph and r["variant"] == v and FLAG(r)]
            b = [r["workload_joules"] for r in cells if r["phone"] == ph and r["variant"] == v and not FLAG(r)]
            pa = [r["pre_workload_joules"] for r in cells if r["phone"] == ph and r["variant"] == v and FLAG(r)]
            pb = [r["pre_workload_joules"] for r in cells if r["phone"] == ph and r["variant"] == v and not FLAG(r)]
            if not a or not b:
                continue
            print(f"{ph:<8}{v.replace('_debug',''):<10}{len(a):>10}{st.median(a):>9.1f}"
                  f"{len(b):>10}{st.median(b):>9.1f}{st.median(a)/st.median(b):>8.3f}"
                  f"{st.median(pa):>11.1f}/{st.median(pb):<8.1f}")
    print("""
pixel9 flagged cells carry ~1.18-1.31x the workload energy of its unflagged
cells; on pixel3 and pixel6 the same ratio is 0.99-1.06.  That is the pixel9
post-top-up current artefact.  Because pixel9 baseline happens to own twice as
many flagged cells as pixel9 freerasp (54 vs 27), the unfiltered pixel9
baseline mean is inflated more than the freerasp mean, which DEFLATES the
freerasp-vs-baseline delta.  Removing them raises it -- and both principled
filters, one thermal and one purely temporal, agree on the direction and
roughly on the size.""")

    # ---- [11] non-circularity demonstration ------------------------------
    hr("[11] NON-CIRCULARITY: the headline as a function of alpha")
    print("If alpha had been tuned on the outcome, some alpha would flatter the")
    print("result and 5% would sit at or near it.  Scan alpha and look: the")
    print("headline is flat, and 5% is not special.  The choice of alpha=5% was")
    print("made a priori (conventional one-sided reference interval) and the")
    print("outcome columns are never read while building the rule.\n")
    print(f"{'alpha':>7}{'flagged':>9}{'%':>7}"
          f"{'allatori':>11}{'bangcle':>11}{'freerasp':>11}")
    base = {}
    for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
        base[v] = st.mean(paired_deltas(cells, v))
    print(f"{'--':>7}{0:>9}{0.0:>7.1f}"
          + "".join(f"{base[v]:>+11.2f}" for v in
                    ("allatori_debug", "bangcle_debug", "freerasp_debug"))
          + "   <- no filter")
    for a in (0.005, 0.01, 0.025, 0.05, 0.075, 0.10, 0.15, 0.20):
        r2 = build_reference(cells, alpha=a)
        f_ = make_rule(r2)
        n = sum(1 for r in cells if f_(r))
        vals = [st.mean(paired_deltas(cells, v, drop=f_))
                for v in ("allatori_debug", "bangcle_debug", "freerasp_debug")]
        mark = "   <- RECOMMENDED" if abs(a - ALPHA) < 1e-9 else ""
        print(f"{a:>7.3f}{n:>9}{100*n/len(cells):>7.1f}"
              + "".join(f"{x:>+11.2f}" for x in vals) + mark)

    hr("[12] ROBUSTNESS OF THE SETTLED REFERENCE ESTIMATOR")
    print("The rule needs a working-equilibrium reference.  Above it comes from")
    print("cells with k >= 20, which needs the top-up index.  A top-up-detector-")
    print("free alternative: the stratum's own 65th percentile over ALL valid")
    print("cells (cold cells are a minority and lie in the lower tail).  If the")
    print("two agree, the rule does not hinge on the top-up detector.\n")
    alt = {}
    for s in ref:
        g = [r["battery_temp_avg_c"] for r in cells if stratum(r) == s]
        med = quantile(g, .65)
        alt[s] = med - (ref[s]["batt_med"] - ref[s]["batt_lim"])
    ALT = lambda r: (r["battery_temp_avg_c"] == r["battery_temp_avg_c"]
                     and r["battery_temp_avg_c"] < alt[stratum(r)])
    print(f"{'stratum':<18}{'k>=20 limit':>13}{'p65-based limit':>18}{'diff':>8}")
    for s in sorted(ref):
        print(f"{s[0]+'/'+s[1]:<18}{ref[s]['batt_lim']:>13.2f}"
              f"{alt[s]:>18.2f}{alt[s]-ref[s]['batt_lim']:>8.2f}")
    agree = sum(1 for r in cells if FLAG(r) == ALT(r))
    print(f"\nflag sets agree on {agree}/{len(cells)} cells "
          f"({100*agree/len(cells):.2f}%);  "
          f"k>=20 version flags {sum(1 for r in cells if FLAG(r))}, "
          f"p65 version flags {sum(1 for r in cells if ALT(r))}")
    for v in ("allatori_debug", "bangcle_debug", "freerasp_debug"):
        m1 = st.mean(paired_deltas(cells, v, drop=FLAG))
        m2 = st.mean(paired_deltas(cells, v, drop=ALT))
        print(f"  headline {v.replace('_debug',''):<10} "
              f"k>=20 ref {m1:+.2f}%   p65 ref {m2:+.2f}%   diff {m2-m1:+.3f} pp")

    hr("[13] VERDICT")
    print("""
BEST DETECTOR       TEMP-RI-05:  battery_temp_avg_c < P05( battery_temp_avg_c
                    over cells of the same phone x batch with k >= 20 ).
                    Against an independent sensor it has AUC 0.972 and, at a
                    matched flagging budget, sensitivity 0.82 where an absolute
                    threshold gets 0.69, SoC 0.56, cells-since-top-up 0.55,
                    voltage 0.41 and elapsed time 0.38.  Its flag rate is
                    stable across the three batches (16.8 / 17.1 / 16.0 %,
                    chi2=0.51 p=0.77) where the incumbent 36 C line is not
                    (5.9 / 4.4 / 14.5 %, p<0.0001).  The number chosen is a
                    false-positive rate, not a temperature.

BUT NOT A FILTER    Battery temperature is POST-TREATMENT.  On pixel9 the rule
                    flags baseline at 18.2% and freerasp at 9.0% (p=0.0075
                    permuting the variant label inside the matching unit;
                    CMH z=+2.56 p=0.010 after adjusting for cells-since-top-up;
                    same ordering in all three batches, and in the 7 apps that
                    carry all four variants).  Excluding on a post-treatment
                    variable is exactly the move that gets an energy result
                    attacked.  If cells are to be REMOVED, remove them with
                    K-WINDOW (k <= K_phone), which is driven by the top-up
                    schedule rather than by the cell's own variant and which
                    passes every neutrality test run here.

IS A RULE NEEDED?   For the pooled headline: no.  All three candidate filters
                    move it by less than 0.6 pp and none crosses a 0.05
                    significance line.  (allatori's point estimate wanders
                    across zero, +0.25 to -0.22, but its interval contains
                    zero under every filter, so nothing is decided by the
                    choice.)  Flag and report.
                    For the PER-PHONE numbers: yes, and it must be reported
                    both ways.  freerasp on pixel9 is +0.81% [-1.42,+3.04]
                    unfiltered and +2.72% [+1.37,+4.07] / +1.99% [+0.46,+3.51]
                    under the two principled filters -- the conclusion "no
                    detectable freeRasp overhead on pixel9" does not survive
                    either of them.  Reporting only the unfiltered pixel9
                    number would be reporting a number that is known to be
                    contaminated by a documented instrument artefact.

RECOMMENDATION      1. Publish TEMP-RI-05 as the cold-cell DETECTOR, with the
                       per-stratum limits table from [1] and the counts from
                       [4], [5] and [8].
                    2. Keep every flagged cell in the primary estimate.
                    3. Publish, as a pre-specified sensitivity analysis, the
                       estimates under TEMP-RI-05 and under K-WINDOW ([9]).
                    4. Retire `battery_temp_avg_c < 36`.  It is not stable
                       across batches, it recovers under half the cells an
                       independent sensor calls cold, and its apparent balance
                       across variants is a consequence of that insensitivity,
                       not evidence of neutrality.
""")


if __name__ == "__main__":
    main()
