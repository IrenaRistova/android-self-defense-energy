"""Per-device analysis in the Green Lab recipe (2026-10-25): the helpers of Per_Device_Analysis.ipynb.

The unit is the matched pair: one application on one device in one series batch, its protected build's mean
against its control's mean. Every comparison is one technique against its control, in one phase, on one
device. Nothing here pools devices.

Phases: "passive" is the post-launch window (key "startup", column pre_workload_joules), "active" the
workload window (key "workload", column workload_joules).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

import thesis_lib as T

DEVICES = ("pixel3", "pixel6", "pixel9")
PHASES = {"active": ("workload_joules", "duration_workload_s", "workload"),
          "passive": ("pre_workload_joules", "duration_pre_workload_s", "startup")}
PHASE_DISPLAY = {"active": "Active", "passive": "Passive"}
RQ_OF_PHASE = {"active": "RQ1", "passive": "RQ2"}
ALPHA = 0.05
N_BOOT = 10000
CLIFF_THRESHOLDS = ((0.147, "negligible"), (0.33, "small"), (0.474, "medium"), (np.inf, "large"))  # Romano et al.


def cliff_label(d: float) -> str:
    a = abs(d)
    for edge, lab in CLIFF_THRESHOLDS:
        if a < edge:
            return lab
    return "large"


def cliffs_delta(x, y) -> float:
    """P(X > Y) - P(X < Y) over all pairs of values: x = protected cells, y = control cells."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.sign(x[:, None] - y[None, :]).mean())


def rank_biserial(d) -> float:
    """Matched-pairs rank-biserial correlation (Kerby 2014): (R+ - R-) / (R+ + R-), ranks of |d|, zeros dropped."""
    d = np.asarray(d, float)
    d = d[d != 0]
    r = stats.rankdata(np.abs(d))
    rp, rn = r[d > 0].sum(), r[d < 0].sum()
    return float((rp - rn) / (rp + rn)) if (rp + rn) else 0.0


def boot_ci(values, stat, rng, n_boot: int = N_BOOT):
    """Percentile interval of `stat` over resampled pairs."""
    v = np.asarray(values)
    n = len(v)
    idx = rng.integers(0, n, size=(n_boot, n))
    b = np.array([stat(v[i]) for i in idx])
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def pair_table(series: pd.DataFrame, passive_exclude=T.PASSIVE_EXCLUDED_APPS) -> pd.DataFrame:
    """One row per (technique, phase, device, app, batch): the pair's means, its relative difference, its log
    ratio, and the cell values of both builds (for the within-pair Cliff's delta). `passive_exclude`: applications
    left out of the passive phase in every arm (medtimer); () keeps them."""
    rows = []
    for arm in T.ARMS:
        for phase, (ecol, dcol, _) in PHASES.items():
            d = series[series["variant"].isin(["baseline_debug", arm]) & (series[ecol] > 0)]
            if phase == "passive":
                d = d[~d["app"].isin(passive_exclude)]
            for (app, phone, batch), g in d.groupby(["app", "phone", "batch"]):
                t = g.loc[g["variant"] == arm, ecol].to_numpy(float)
                c = g.loc[g["variant"] == "baseline_debug", ecol].to_numpy(float)
                if len(t) == 0 or len(c) == 0:
                    continue
                td = g.loc[g["variant"] == arm, dcol].to_numpy(float)
                cd = g.loc[g["variant"] == "baseline_debug", dcol].to_numpy(float)
                rows.append(dict(technique=T.name(arm), phase=phase, device=phone, app=app, batch=batch,
                                 n_treated=len(t), n_control=len(c), control_j=c.mean(), treated_j=t.mean(),
                                 delta_pct=100 * (t.mean() - c.mean()) / c.mean(), log_ratio=np.log(t.mean() / c.mean()),
                                 cliff_within=cliffs_delta(t, c), control_s=cd.mean(), treated_s=td.mean(),
                                 _t=t, _c=c))
    p = pd.DataFrame(rows)
    # the same pairs as thesis_lib.pairs (the published estimates): check
    for arm in T.ARMS:
        for phase, (ecol, _, _) in PHASES.items():
            ref = T.pairs(series if phase == "active" else series[~series["app"].isin(passive_exclude)], arm, ecol)
            mine = p[(p["technique"] == T.name(arm)) & (p["phase"] == phase)]
            assert len(ref) == len(mine), (arm, phase, len(ref), len(mine))
            assert abs(ref["delta_pct"].mean() - mine["delta_pct"].mean()) < 1e-9, (arm, phase)
    return p


def bh(pvals) -> np.ndarray:
    return stats.false_discovery_control(np.asarray(pvals, float), method="bh")


def wilcoxon(d):
    """Two-sided Wilcoxon signed-rank on the per-pair differences; exact when there are no zeros or ties."""
    d = np.asarray(d, float)
    exact = len(d) <= 50 and np.all(d != 0) and len(np.unique(np.abs(d))) == len(d)
    r = stats.wilcoxon(d, alternative="two-sided", method="exact" if exact else "approx")
    return float(r.statistic), float(r.pvalue), "exact" if exact else "normal approximation"


def write_tex_grouped(df: pd.DataFrame, stem: str, caption: str, label: str, colfmt: str, groups,
                      blocks: dict | None = None, star: bool = True, notes: str = "", bold_rows=None,
                      bold_cells=None, notes_tex: str = "", shade_cells=None, shade_colour: str = "black!15") -> list:
    """A booktabs table with a row of column-group headings above the column names (\\multicolumn +
    \\cmidrule), set at natural size (no \\resizebox). `groups`: [(heading or "", n_columns), ...] covering all
    columns. `blocks`: {row index: heading} as in thesis_lib.write_tex. `bold_rows`: row indices in bold;
    `bold_cells`: {(row index, column position)} in bold; `shade_cells`: {(row index, column position)} on a light
    grey fill, \\cellcolor{black!15} (colortbl; black!15, not gray!15, which prints almost white)."""
    env = "table*" if star else "table"
    n = len(df.columns)
    assert sum(k for _, k in groups) == n, (groups, n)
    rules, head_cells, c0 = [], [], 1
    for head, k in groups:
        if head:
            rules.append(rf"\cmidrule(lr){{{c0}-{c0 + k - 1}}}")
            head_cells.append(rf"\multicolumn{{{k}}}{{c}}{{\textbf{{{T.tex_escape(head)}}}}}")
        else:
            head_cells.append("&" * (k - 1))          # k empty cells
        c0 += k
    lines = [rf"\begin{{{env}}}[t]", r"\centering", r"\small", rf"\caption{{{caption}}}", rf"\label{{{label}}}",
             rf"\begin{{tabular}}{{{colfmt}}}", r"\toprule", " & ".join(head_cells) + r" \\", " ".join(rules),
             " & ".join(rf"\textbf{{{T.tex_escape(c)}}}" for c in df.columns) + r" \\", r"\midrule"]
    for i, (_, row) in enumerate(df.iterrows()):
        if blocks and i in blocks:
            if i:
                lines.append(r"\midrule")
            lines.append(rf"\multicolumn{{{n}}}{{l}}{{\textit{{{T.tex_escape(blocks[i])}}}}} \\")
        cells = []
        for j, v in enumerate(row):
            c = T.tex_escape("" if pd.isna(v) else v)
            if c and ((bold_rows and i in bold_rows) or (bold_cells and (i, j) in bold_cells)):
                c = rf"\textbf{{{c}}}"
            if shade_cells and (i, j) in shade_cells:
                c = rf"\cellcolor{{{shade_colour}}}" + c
            cells.append(c)
        lines.append(" & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    if notes or notes_tex:
        lines.append(rf"\par\smallskip\footnotesize {T.tex_escape(notes)}" + (f" {notes_tex}" if notes_tex else ""))
    lines.append(rf"\end{{{env}}}")
    (T.TABLES / f"{stem}.tex").write_text("\n".join(lines) + "\n")
    df.to_csv(T.TABLES / f"{stem}.csv", index=False)
    return [f"tables/{stem}.csv", f"tables/{stem}.tex"]


def fmt_p(p: float) -> str:
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def signed(x: float, d: int = 2) -> str:
    return f"{round(x, d) + 0.0:+.{d}f}"


def ci_text(lo: float, hi: float, d: int = 2) -> str:
    return f"[{signed(lo, d)}, {signed(hi, d)}]"


TECHS = ("Bangcle", "Allatori", "freeRASP")


def test_row(g, rng) -> dict:
    """Every statistic of one comparison, from its pair rows (pair_table)."""
    d = g["delta_pct"].to_numpy(float)
    m, lo, hi, _ = T.ci_naive(d)
    w, p, method = wilcoxon(d)
    r = stats.rankdata(np.abs(d))
    cw = g["cliff_within"].to_numpy(float)
    clo, chi = boot_ci(cw, np.mean, rng)
    return dict(n=len(d), delta_pct=m, ci_lo=lo, ci_hi=hi, median_delta_pct=float(np.median(d)), V=float(r[d > 0].sum()), W=w,
                p=p, p_method=method, cliff=float(cw.mean()), cliff_lo=clo, cliff_hi=chi, magnitude=cliff_label(cw.mean()),
                rank_biserial=rank_biserial(d),
                cliff_naive=cliffs_delta(np.concatenate(g["_t"].tolist()), np.concatenate(g["_c"].tolist())),
                cohen_dz=float(m / d.std(ddof=1)), mde_pct=T.mde(d))


def run_tests(pt: pd.DataFrame, rng, analysis: str) -> pd.DataFrame:
    """The 18 comparisons (phase x technique x device) with BH within each RQ and across all 18."""
    rows = []
    for ph in ("active", "passive"):
        for t in TECHS:
            for dev in DEVICES:
                g = pt[(pt["technique"] == t) & (pt["phase"] == ph) & (pt["device"] == dev)]
                rows.append(dict(analysis=analysis, rq=RQ_OF_PHASE[ph], phase=ph, technique=t, device=dev, **test_row(g, rng)))
    x = pd.DataFrame(rows)
    x["p_bh"] = x.groupby("rq")["p"].transform(lambda s: bh(s.to_numpy()))
    x["p_bh_all18"] = bh(x["p"].to_numpy())
    x["significant"] = x["p_bh"] < ALPHA
    return x


def rq_rows(x: pd.DataFrame) -> pd.DataFrame:
    """The display rows of one RQ table (CAIN Table IV layout): one row per technique x device."""
    return pd.DataFrame({"Device": x["device"].map(T.PHONE_DISPLAY), "n": x["n"].astype(int),
                         "ΔE (%) [95% CI]": [f"{signed(m)} {ci_text(lo, hi)}" for m, lo, hi in zip(x["delta_pct"], x["ci_lo"], x["ci_hi"])],
                         "V": x["V"].map(lambda v: f"{v:.0f}"), "p (BH)": x["p_bh"].map(fmt_p),
                         "Cliff's δ [95% CI]": [f"{signed(c)} {ci_text(lo, hi)}" for c, lo, hi in zip(x["cliff"], x["cliff_lo"], x["cliff_hi"])],
                         "Magnitude": x["magnitude"], "MDE (%)": x["mde_pct"].map(lambda v: f"{v:.2f}")}).reset_index(drop=True)
