"""Shared definitions for the results notebooks.

Every function here implements one numbered item of the analysis specification,
and says which. The specification's items: A1 files and columns, A2 the filter
chain, A3 the pilot; B1 pair formation, B2 the Delta-E statistic, B3 the stratified
permutation test, B4 the confidence intervals, B5 the minimum detectable effect,
B6 descriptive statistics, B7 the remaining reported quantities.
The notebooks call these functions one step per cell, so each
reported number can be traced to a single definition. The output helpers at the
bottom (display names, p-value formatting, figure style, LaTeX export, the artifact
manifest and the headline-number file) fix one convention for all four notebooks.

Decisions fixed on 2026-09-28 and implemented here:
  * cohort: nine applications; androidskk is dropped from every arm (its control
    build carries obfuscated code, and all four variants share one control)
  * primary inference: naive interval (exact per-n t quantile) + stratified
    permutation test; the application-clustered bootstrap is a robustness column
  * permutation arms run in the fixed order bangcle, allatori, freerasp, drawing
    from one seeded generator
  * no cell is excluded on the post-charge flag; the filtered values are a
    sensitivity analysis only

Decision fixed on 2026-10-02:
  * itsok's Allatori build is excluded from the Allatori arm only. It is a release
    build; the protocol committed before data collection (2026-07-27) requires every
    build to be a debug build. Rule enforcement on a property of the build, decided
    independently of its result; itsok's other arms are unaffected

Decisions fixed on 2026-10-26:
  * medtimer's Bangcle build is excluded from the packing arm only, by the same build-parity
    rule: it was packed in May from a debug build compiled with JaCoCo coverage probes, which
    its control (rebuilt 2026-08-03 without them) lacks. medtimer's other arms are unaffected.
    The analysis with it is kept in the replication package (Per_Device_Analysis.ipynb)
  * medtimer leaves the passive phase in every arm (control included): before its first scenario the harness steps
    through its six-screen introduction (six taps, about 85 s), so its passive phase (median 104 s, against 24-47 s)
    does not measure the same thing. medtimer stays in the active phase for obfuscation and freeRASP. Every analysis
    applies this wherever it forms passive-phase pairs or cells (`phase_set`): the per-device analysis,
    the pooled replication-package analyses and the reference implementation. The per-device version
    with medtimer is kept in the replication package (per_device_checks.csv, table_rq12_medtimer_in_passive).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "6_results_data"
ANALYSIS = ROOT / "6_results_analysis"
TABLES = ANALYSIS / "tables"
FIGURES = ANALYSIS / "figures"
SUPERVISOR = TABLES / "supervisor_only"   # ten-application sensitivity; never cited in the thesis
MANIFEST = ANALYSIS / "MANIFEST.csv"
HEADLINE_NUMBERS = TABLES / "headline_numbers.json"

# Spec A1. The file tag is NOT the thesis name: series batch 1's file says "batch2".
RUN_TABLES = {
    "pilot":   DATA / "run_tables" / "run_table_extended_final_ykush_2026-08-04.csv",
    "series1": DATA / "run_tables" / "run_table_extended_final_ykush_batch2_2026-08-10.csv",
    "series2": DATA / "run_tables" / "run_table_extended_final_ykush_series2_2026-08-14.csv",
    "series3": DATA / "run_tables" / "run_table_extended_final_ykush_series3_2026-08-17.csv",
}
SERIES = ("series1", "series2", "series3")
EXCLUDED_APPS = ("androidskk",)
EXCLUDED_BUILDS = (("itsok", "allatori_debug"), ("medtimer", "bangcle_debug"))   # build-parity rule; see the module docstring
PASSIVE_EXCLUDED_APPS = ("medtimer",)   # out of the passive phase in every arm; see the module docstring
BUILD_EXCLUSION_STEP = {("itsok", "allatori_debug"): "build type (drop itsok's release Allatori build)",
                        ("medtimer", "bangcle_debug"): "build parity (drop medtimer's packed build: coverage probes)"}
ARMS = ("bangcle_debug", "allatori_debug", "freerasp_debug")  # permutation order
WINDOWS = {"workload": "workload_joules", "startup": "pre_workload_joules"}
PILOT_CUTOFF = "2026-08-07"            # external supply fitted; spec A3
K_PHONE = {"pixel3": 2, "pixel6": 6, "pixel9": 4}   # post-charge K-window
SEED = 20260830
N_PERMUTATIONS = 6000
N_BOOTSTRAP = 20000

USECOLS = [
    "app", "phone", "variant", "rep", "cell_start_ts_utc",
    "scientifically_valid", "negative_control", "scenarios_completed",
    "scenarios_planned", "cell_failure_class",
    "workload_joules", "pre_workload_joules", "workload_avg_watts",
    "duration_workload_s", "duration_pre_workload_s",
    "thermal_skin_pre_c",
]


def short(variant: str) -> str:
    return variant.replace("_debug", "")


# ---------------------------------------------------------------- spec A2
def load_raw() -> pd.DataFrame:
    """All four run tables, 4800 rows, with a `batch` label added here."""
    frames = []
    for batch, path in RUN_TABLES.items():
        df = pd.read_csv(path, usecols=USECOLS, dtype=str)
        df["batch"] = batch
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    for col in ("workload_joules", "pre_workload_joules", "workload_avg_watts",
                "duration_workload_s", "duration_pre_workload_s"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Arrival temperature: the skin temperature the thermal gate read when the cell
    # arrived, before any rest it imposed and before the 60 s settle (series batches only;
    # the pilot ran without the gate). A reading of 0 is a failed read, not a temperature.
    t0 = pd.to_numeric(df["thermal_skin_pre_c"], errors="coerce")
    df["arrival_temp_c"] = t0.where(t0 > 0)
    df["rep"] = df["rep"].astype(str)
    return df


def filter_steps(exclude_apps=EXCLUDED_APPS, exclude_builds=EXCLUDED_BUILDS):
    """The spec-A2 chain, in order: (name, predicate) pairs.

    Order does not change the final set (these are independent predicates),
    only the intermediate counts printed on the page.
    """
    def not_negative_control(d):
        nc = d["negative_control"].fillna("").str.strip()
        return nc.isin(["", "None", "False"])

    def scenarios_complete(d):
        c = pd.to_numeric(d["scenarios_completed"], errors="coerce")
        p = pd.to_numeric(d["scenarios_planned"], errors="coerce")
        partial = c.notna() & p.notna() & (c >= 0) & (p > 0) & (c != p)
        return ~partial

    def pilot_regime(d):
        return (d["batch"] != "pilot") | (d["cell_start_ts_utc"].fillna("") >= PILOT_CUTOFF)

    return [
        ("scientifically_valid", lambda d: d["scientifically_valid"] == "True"),
        ("not a negative control", not_negative_control),
        (f"cohort (drop {', '.join(exclude_apps) or 'none'})",
         lambda d: ~d["app"].isin(list(exclude_apps))),
        *[(BUILD_EXCLUSION_STEP.get((a, v), f"build parity (drop {a} {short(v)})"),
           (lambda a_, v_: (lambda d: ~((d["app"] == a_) & (d["variant"] == v_))))(a, v)) for a, v in exclude_builds],
        ("scenarios complete", scenarios_complete),
        ("workload_joules > 0", lambda d: d["workload_joules"] > 0),
        (f"pilot regime (start >= {PILOT_CUTOFF})", pilot_regime),
    ]


def apply_filters(df: pd.DataFrame, exclude_apps=EXCLUDED_APPS, exclude_builds=EXCLUDED_BUILDS):
    """Return the filtered frame and a table of survivors after each step."""
    counts = [("loaded", df.groupby("batch").size())]
    for name, pred in filter_steps(exclude_apps, exclude_builds):
        df = df[pred(df)]
        counts.append((name, df.groupby("batch").size()))
    chain = pd.DataFrame({name: s for name, s in counts}).T.fillna(0).astype(int)
    chain = chain[list(RUN_TABLES)]
    chain["series pooled"] = chain[list(SERIES)].sum(axis=1)
    return df.copy(), chain


# ---------------------------------------------------------------- spec B1, B2
def group_means(df: pd.DataFrame, col: str, by_batch: bool = True) -> pd.Series:
    """Arithmetic mean per group; reps are averaged away before pairing."""
    keys = ["app", "phone", "variant"] + (["batch"] if by_batch else [])
    d = df[df[col] > 0]
    return d.groupby(keys)[col].mean()


def pairs(df: pd.DataFrame, arm: str, col: str, by_batch: bool = True) -> pd.DataFrame:
    """One row per matched pair: the treated group against its own baseline group.

    The per-pair delta is 100 * (treated - baseline) / baseline.
    """
    g = group_means(df, col, by_batch).reset_index()
    keys = ["app", "phone"] + (["batch"] if by_batch else [])
    base = g[g["variant"] == "baseline_debug"].rename(columns={col: "baseline"})
    treat = g[g["variant"] == arm].rename(columns={col: "treated"})
    p = treat.merge(base[keys + ["baseline"]], on=keys, how="inner")
    p["delta_pct"] = 100.0 * (p["treated"] - p["baseline"]) / p["baseline"]
    p["delta_abs"] = p["treated"] - p["baseline"]
    return p


def phase_set(df: pd.DataFrame, window: str) -> pd.DataFrame:
    """The cells a phase's analysis uses: the passive phase leaves out
    PASSIVE_EXCLUDED_APPS in every arm; the active phase keeps every analysed cell. `window`: a key of WINDOWS, a
    phase name or a phase's column."""
    if window in ("workload", "active", "workload_joules", "duration_workload_s"):
        return df
    if window in ("startup", "passive", "post_launch", "pre_workload_joules", "duration_pre_workload_s"):
        return df[~df["app"].isin(PASSIVE_EXCLUDED_APPS)]
    raise ValueError(f"not a phase: {window!r}")


def delta_e(p: pd.DataFrame) -> float:
    """Mean over PAIRS. No trimming, winsorising or outlier removal."""
    return float(p["delta_pct"].mean())


# ---------------------------------------------------------------- spec B4
def ci_naive(x, exact: bool = True):
    """Mean +/- t * s / sqrt(n). exact=False uses a fixed t instead: 2.045 for n < 40, else 1.96."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    se = x.std(ddof=1) / np.sqrt(n)
    t = stats.t.ppf(0.975, n - 1) if exact else (2.045 if n < 40 else 1.96)
    m = x.mean()
    return m, m - t * se, m + t * se, float(t)


def ci_app_clustered(p: pd.DataFrame, rng: np.random.Generator, n_boot: int = N_BOOTSTRAP):
    """Resample APPS with replacement; pool every pair of each drawn app; mean.

    Percentile method, 2.5 / 97.5. Returns (lo, hi, count of resample means <= 0).
    Robustness column only: with few apps this interval is known to be too narrow.
    """
    by_app = [g["delta_pct"].to_numpy() for _, g in p.groupby("app")]
    k = len(by_app)
    sums = np.array([a.sum() for a in by_app])
    counts = np.array([len(a) for a in by_app])
    draws = rng.integers(0, k, size=(n_boot, k))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi), int((means <= 0).sum())


# ---------------------------------------------------------------- spec B3
def permutation_p(df: pd.DataFrame, arm: str, col: str, rng: np.random.Generator,
                  n_perm: int = N_PERMUTATIONS):
    """(observed, p). See permutation_test."""
    t = permutation_test(df, arm, col, rng, n_perm)
    return t["observed"], t["p"]


def permutation_test(df: pd.DataFrame, arm: str, col: str, rng: np.random.Generator,
                     n_perm: int = N_PERMUTATIONS) -> dict:
    """Stratified permutation test, two-sided.

    Within each (app, phone, batch) stratum the variant labels of the baseline and
    treated cells are shuffled; Delta-E is recomputed exactly as for the observed
    data. p = (hits + 1) / (n + 1), hit when |shuffled| >= |observed|.

    With hits = 0 the result is the floor of the test, 1 / (n + 1), not a measured
    probability; report it with fmt_p, which prints "< 0.001".
    """
    d = df[df["variant"].isin(["baseline_debug", arm]) & (df[col] > 0)]
    strata = []
    for _, g in d.groupby(["app", "phone", "batch"]):
        is_t = (g["variant"] == arm).to_numpy()
        if is_t.any() and (~is_t).any():
            strata.append((g[col].to_numpy(), is_t))
    observed = np.mean([100 * (v[t].mean() - v[~t].mean()) / v[~t].mean() for v, t in strata])
    hits = 0
    for _ in range(n_perm):
        s = 0.0
        for v, t in strata:
            lab = rng.permutation(t)
            b = v[~lab].mean()
            s += 100 * (v[lab].mean() - b) / b
        if abs(s / len(strata)) >= abs(observed):
            hits += 1
    return {"observed": float(observed), "p": (hits + 1) / (n_perm + 1),
            "hits": hits, "permutations": n_perm}


# ---------------------------------------------------------------- spec B5
def mde(x, alpha: float = 0.05, power: float = 0.80) -> float:
    """(t_{1-alpha/2} + t_{power}) * SE, SE over PAIRS (not clustered)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    se = x.std(ddof=1) / np.sqrt(n)
    return float((stats.t.ppf(1 - alpha / 2, n - 1) + stats.t.ppf(power, n - 1)) * se)


# ---------------------------------------------------------------- post-charge
def post_charge_flag(df: pd.DataFrame) -> pd.Series:
    """True where the cell falls within K_phone cells of a workload interruption."""
    k = pd.read_csv(DATA / "inputs" / "post_charge_k_window.csv", dtype=str)
    k["k"] = pd.to_numeric(k["k"], errors="coerce")
    k["flag"] = k["k"] <= k["phone"].map(K_PHONE)
    key = ["batch", "phone", "app", "variant", "rep"]
    m = df[key].merge(k[key + ["flag"]], on=key, how="left")
    return pd.Series(m["flag"].fillna(False).to_numpy(), index=df.index)


# ---------------------------------------------------------------- checking
_REF = None


def reference():
    """The independent implementation's output, for side-by-side checking."""
    global _REF
    if _REF is None:
        import json
        _REF = json.loads((DATA / "derived" / "thesis_numbers_reference.json").read_text())
    return _REF


def check(label: str, got: float, expected, tol: float = 0.005, show: bool = True) -> bool:
    """Print a computed value beside its expected value. A mismatch is flagged,
    never corrected toward the expected value. show=False checks a quantity that is
    not a reported statistic without printing its value."""
    if not show:
        ok = expected is not None and abs(got - expected) <= tol
        print(f"  {label}: matches the independent implementation (value not printed)"
              if ok else f"  {label}: <<< DIVERGES from the independent implementation")
        return ok
    if expected is None:
        print(f"  {label}: {got:+.4f}   (no reference value)")
        return True
    ok = abs(got - expected) <= tol
    print(f"  {label}: {got:+.4f}   (expected {expected:+.4f})  {'OK' if ok else '<<< DIVERGES'}")
    return ok


# ---------------------------------------------------------------- output conventions
DISPLAY = {"baseline": "Control", "bangcle": "Bangcle", "allatori": "Allatori",
           "freerasp": "freeRASP"}                      # vendor spelling: freeRASP
# The keys stay "workload" and "startup" (column names, JSON keys); a reader sees the phase names
# "Active" (the scenarios) and "Passive" (from the first energy sample to the first
# scenario; it opens after the cold launch, which is timed, not measured in energy).
WINDOW_DISPLAY = {"workload": "Active", "startup": "Passive"}   # the phases' display names
PHONE_DISPLAY = {"pixel3": "Pixel 3", "pixel6": "Pixel 6", "pixel9": "Pixel 9"}
# Okabe-Ito bluish green / blue / vermillion: all pairs pass the colour-vision check (worst deutan dE 11.0) and
# each reaches 3:1 contrast on white. Techniques are also separated by position and label in every
# figure, so greyscale print does not rely on hue. Devices: one marker each, in every figure.
COLOURS = {"Bangcle": "#009E73", "Allatori": "#0072B2", "freeRASP": "#D55E00", "Control": "#8c8c8c"}
DEVICE_MARKERS = {"pixel3": "o", "pixel6": "s", "pixel9": "^"}
DEVICE_LINESTYLES = {"pixel3": "-", "pixel6": "--", "pixel9": ":"}
DEVICE_SHADE = {"pixel3": 0.0, "pixel6": 0.35, "pixel9": 0.6}   # mix with white: Pixel 3 darkest


def device_colour(technique: str, phone: str) -> tuple:
    """The technique's colour, lightened for the newer devices (Pixel 3 full, Pixel 6 and 9 lighter)."""
    import matplotlib.colors as mc
    r, g, b = mc.to_rgb(COLOURS[technique])
    f = DEVICE_SHADE[phone]
    return (r + (1 - r) * f, g + (1 - g) * f, b + (1 - b) * f)
P_METHOD = (f"stratified permutation test, {N_PERMUTATIONS:,} permutations "
            "within (application, device, batch)")


def name(variant_or_short: str) -> str:
    """Display name of a technique: 'freerasp_debug' or 'freerasp' -> 'freeRASP'."""
    return DISPLAY[short(variant_or_short)]


def fmt_p(t: dict) -> str:
    """p for a table: '< 0.001' when below 0.001 (always so when no permutation was as
    extreme as observed: the value is then the 1/(n+1) floor, not a measurement)."""
    return "< 0.001" if t["p"] < 0.001 else f"{t['p']:.3f}"


def p_note(t: dict) -> str:
    if t["hits"] == 0:
        return f"none of {t['permutations']:,} permutations as extreme as observed"
    return f"{t['hits']:,} of {t['permutations']:,} permutations as extreme as observed"


def fmt_ci(lo: float, hi: float, d: int = 2) -> str:
    return f"[{lo:+.{d}f}, {hi:+.{d}f}]"


# Figures: ACM sigconf, single column 3.33 in, figure* 7 in, 7-8 pt text to sit
# beside 9 pt body text. No titles or captions in the image; captions live in LaTeX.
#
# Every results figure must make the comparison with the control legible without
# the caption: what is plotted is the matched-pair difference from the unprotected
# control, so zero IS the control. It is drawn as a labelled reference line
# (control_line), the axis says "... vs. control", and the control is never drawn
# as a bar or a point of its own. The one panel that plots a rate rather than a
# difference (SELinux denials) draws the control's rate as that labelled line.
FIG_SINGLE = 3.33
FIG_DOUBLE = 7.0
DELTA_LABEL = "ΔE vs. control (%)"
CONTROL_LABEL = "unprotected control"


def control_line(ax, axis: str = "y", label: str = CONTROL_LABEL, at: float = 0.98,
                 side: str = "above", outside: bool = False, value: float = 0.0):
    """Draw the reference line that stands for the unprotected control, and label it.

    axis="y": a horizontal line (differences on the y-axis); axis="x": a vertical line.
    The label always sits directly ABOVE the line, so it reads as the line's name:
    on a horizontal line, just on top of it at `at` (axes fraction along it); with
    outside=True, on top of its right end, outside the plot area where no data is.
    On a vertical line it is centred over the line's top end (outside the plot area).
    `side` places it under a horizontal line instead, for a panel with no room above.
    `value` is where the control sits: 0 for a difference, the control's own value
    for a rate.
    """
    import matplotlib.transforms as mtransforms
    style = dict(color="0.25", fontsize=7, fontstyle="italic", zorder=5, annotation_clip=False)
    if axis == "y":
        ax.axhline(value, color="0.25", lw=0.7, zorder=2)
        tr = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
        if outside:
            ax.annotate(label, xy=(1, value), xycoords=tr, xytext=(3, 1), textcoords="offset points",
                        ha="left", va="bottom", **style)
        else:
            ax.annotate(label, xy=(at, value), xycoords=tr, xytext=(0, 2 if side == "above" else -2),
                        textcoords="offset points", ha="right" if at > 0.5 else "left",
                        va="bottom" if side == "above" else "top", **style)
    else:
        ax.axvline(value, color="0.25", lw=0.7, zorder=2)
        tr = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
        if outside:
            ax.annotate(label, xy=(value, 1), xycoords=tr, xytext=(0, 2), textcoords="offset points",
                        ha="center", va="bottom", **style)
        else:
            ax.annotate(label, xy=(value, at), xycoords=tr, xytext=(2 if side == "right" else -2, 0),
                        textcoords="offset points", ha="left" if side == "right" else "right",
                        va="top" if at > 0.5 else "bottom", **style)


def figure_style():
    import matplotlib as mpl
    from matplotlib import font_manager
    serif = [f.name for f in font_manager.fontManager.ttflist if "Libertin" in f.name]
    mpl.rcParams.update({
        "font.family": "serif" if serif else "sans-serif",
        "font.serif": serif + ["DejaVu Serif"],
        "font.size": 7, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
        "legend.fontsize": 7, "axes.titlesize": 8,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "lines.linewidth": 0.9, "pdf.fonttype": 42, "ps.fonttype": 42,
        "savefig.bbox": "standard", "figure.dpi": 100,
    })


def save_figure(fig, stem: str, width: float):
    """Write PDF (vector, for LaTeX) and PNG at exactly `width` inches, so LaTeX
    includes it at 100% and the 7-8 pt text stays 7-8 pt."""
    w, h = fig.get_size_inches()
    assert abs(w - width) < 1e-6, f"{stem}: drawn at {w} in, expected {width} in"
    fig.savefig(FIGURES / f"{stem}.pdf")
    fig.savefig(FIGURES / f"{stem}.png", dpi=300)
    return [f"figures/{stem}.pdf", f"figures/{stem}.png"]


def tex_escape(x) -> str:
    s = str(x)
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("_", r"\_"),
                 ("#", r"\#"), ("<", r"$<$"), (">", r"$>$"), ("±", r"$\pm$"), ("−", "--"),
                 ("Δ", r"$\Delta$"), ("α", r"$\alpha$"), ("×", r"$\times$"), ("δ", r"$\delta$"), ("ρ", r"$\rho$"),
                 ("θ", r"$\theta$"), ("≈", r"$\approx$")):
        s = s.replace(a, b)
    import re
    return re.sub(r"(?<![\w.])-(?=\d)", r"$-$", s)     # a minus sign, not a hyphen


def write_tex(df: pd.DataFrame, stem: str, caption: str, label: str, colfmt: str,
              blocks: dict | None = None, star: bool = False, notes: str = "", notes_tex: str = "",
              resize: bool = False, bold_rows=None, colsep: str | None = None, bold_cells=None, shade_cells=None,
              shade_colour: str = "red!12") -> str:
    """A booktabs table (caption + label + tabular). `blocks` maps a row index to a block heading
    printed above that row as a full-width italic line. `notes` is escaped;
    `notes_tex` is appended to it as raw LaTeX (for a \\ref to another table). Tables are set at their natural
    size (no \\resizebox, so no text shrinks); a table too wide for a column is made star=True
    (table*). `bold_rows`: row indices set in bold (significant results). `colsep`: a tighter \\tabcolsep (e.g. "4pt") for a
    table slightly too wide at its natural size; the text keeps its size. `bold_cells`: {(row index, column position)} in
    bold (the values the thesis text states in words, so a reader can find them)."""
    env = "table*" if star else "table"
    width = r"\textwidth" if star else r"\columnwidth"
    n = len(df.columns)
    lines = [rf"\begin{{{env}}}[t]", r"\centering", r"\small", *([rf"\setlength{{\tabcolsep}}{{{colsep}}}"] if colsep else []),
             rf"\caption{{{caption}}}", rf"\label{{{label}}}",
             *([rf"\resizebox{{{width}}}{{!}}{{%"] if resize else []), rf"\begin{{tabular}}{{{colfmt}}}", r"\toprule",
             " & ".join(rf"\textbf{{{tex_escape(c)}}}" for c in df.columns) + r" \\", r"\midrule"]
    for i, (_, row) in enumerate(df.iterrows()):
        if blocks and i in blocks:
            if i:
                lines.append(r"\midrule")
            lines.append(rf"\multicolumn{{{n}}}{{l}}{{\textit{{{tex_escape(blocks[i])}}}}} \\")
        cells = [tex_escape("" if pd.isna(v) else v) for v in row]
        if bold_rows and i in bold_rows:
            cells = [rf"\textbf{{{c}}}" if c else c for c in cells]
        if bold_cells:
            cells = [rf"\textbf{{{c}}}" if c and (i, j) in bold_cells and not c.startswith(r"\textbf") else c
                     for j, c in enumerate(cells)]
        if shade_cells:                     # a light colour behind the cells that carry a detected cost
            cells = [rf"\cellcolor{{{shade_colour}}}" + c if (i, j) in shade_cells else c for j, c in enumerate(cells)]
        lines.append(" & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}}" if resize else r"\end{tabular}"]
    if notes or notes_tex:
        lines.append(rf"\par\smallskip\footnotesize {tex_escape(notes)}" + (f" {notes_tex}" if notes_tex else ""))
    lines.append(rf"\end{{{env}}}")
    (TABLES / f"{stem}.tex").write_text("\n".join(lines) + "\n")
    df.to_csv(TABLES / f"{stem}.csv", index=False)
    return [f"tables/{stem}.csv", f"tables/{stem}.tex"]


def register(paths, placement: str, section: str, description: str, notebook: str):
    """Record artifacts in MANIFEST.csv: path, BODY | APPENDIX | SUPPLEMENT | REPO | SUPERVISOR, thesis
    section, one-line description, source notebook. SUPPLEMENT: a numbered supplementary item
    (Table/Figure S1, S2, ...) in the replication package's supplementary.pdf, which the thesis cites.
    Re-running a notebook replaces its own rows only."""
    if isinstance(paths, str):
        paths = [paths]
    assert placement in ("BODY", "APPENDIX", "SUPPLEMENT", "REPO", "SUPERVISOR"), placement
    cols = ["path", "placement", "thesis_section", "description", "notebook", "status"]
    m = pd.read_csv(MANIFEST) if MANIFEST.exists() else pd.DataFrame(columns=cols)
    m = m[~m["path"].isin(paths)]
    new = pd.DataFrame([dict(path=p, placement=placement, thesis_section=section,
                             description=description, notebook=notebook,
                             status="test-compiled with check_tables_compile.py: PASS on 2026-10-02 (ACM sigconf, Tectonic 0.15.0); re-run after regenerating"
                             if p.endswith(".tex") else "") for p in paths])
    m = pd.concat([m, new], ignore_index=True)
    m["_o"] = m["placement"].map({"BODY": 0, "APPENDIX": 1, "REPO": 2, "SUPERVISOR": 3})
    m = m.sort_values(["_o", "path"]).drop(columns="_o")
    m.to_csv(MANIFEST, index=False)


def record(key: str, **fields):
    """Add one headline number to tables/headline_numbers.json, the single
    machine-readable file the thesis text is checked against. Floats are rounded to 4 dp."""
    import json
    d = json.loads(HEADLINE_NUMBERS.read_text()) if HEADLINE_NUMBERS.exists() else {}
    clean = {}
    for k, v in fields.items():
        if isinstance(v, (float, np.floating)):
            v = round(float(v), 4)
        elif isinstance(v, (np.integer,)):
            v = int(v)
        elif isinstance(v, (list, tuple)):
            v = [round(float(x), 4) if isinstance(x, (float, np.floating)) else x for x in v]
        clean[k] = v
    d[key] = clean
    HEADLINE_NUMBERS.write_text(json.dumps(dict(sorted(d.items())), indent=1, ensure_ascii=False) + "\n")
