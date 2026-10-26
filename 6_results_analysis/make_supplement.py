#!/usr/bin/env python3
"""Build the replication package's supplementary material: supplementary/supplementary.tex, supplementary.pdf
and INDEX.md.

The tables and figures that left the thesis are numbered Table S1-S16 and Figure S1-S3, in the order the thesis first cites
them. Each item is the very .tex table or .pdf figure the notebooks emit (MANIFEST.csv placement SUPPLEMENT), so the
supplement cannot drift from them. INDEX.md maps every thesis item (BODY, APPENDIX, SUPPLEMENT) to its file and the
notebook that emits it.

Fails when the S list and the SUPPLEMENT rows of MANIFEST.csv disagree in either direction.

Usage (from 6_results_analysis/):  TECTONIC=/path/to/tectonic python3 make_supplement.py
Without a LaTeX engine it writes the .tex and INDEX.md and reports the PDF as UNTESTED.
"""
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "supplementary"
TITLE = "On the impact of self-defense techniques on the energy efficiency of Android apps"

# (id, stem, what it shows, where the thesis first cites it), in the order of first citation in the thesis
S_TABLES = [
    ("S1", "table2_control_build_screen", "Control-build screen: compiler-written source-file names in each control APK", "4 Planning (validity of the control builds)"),
    ("S2", "table_obfuscation_static", "What the obfuscator changed in each application's own code", "4 Planning (the obfuscated builds)"),
    ("S3", "table_durations_per_device", "Phase length per cell by device, build and phase; medtimer's passive phase as measured", "4 Planning (workload duration)"),
    ("S4", "table_thermal_gate", "The thermal gate: charging failures, starting temperatures, and the within-pilot test", "4 Planning (the thermal gate)"),
    ("S5", "table_pilot_series", "Repeatability, pilot against series", "5 Execution (pilot and series)"),
    ("S6", "table_per_app_active", "Per application, active phase: mean energy difference per technique and device", "6 Results (per application)"),
    ("S7", "table_per_app_passive", "Per application, passive phase: mean energy difference per technique and device", "6 Results (per application)"),
    ("S8", "table_normality", "Shapiro-Wilk test on the per-pair differences of each comparison", "6.1 Results (normality)"),
    ("S9", "table_rq4_full", "RQ4 in full: each protected build against its control in memory, threads, processor share and phase length", "6 Results (RQ4)"),
    ("S10", "table5_allatori_applicability", "Applying Allatori to the nine applications: renaming and run-time outcome", "7 Discussion (obfuscation)"),
    ("S11", "table6_freerasp_postlaunch", "Where freeRASP's passive-phase energy goes (exploratory)", "7 Discussion (freeRASP's mechanism)"),
    ("S12", "table_freerasp_mechanism", "Runtime shielding per device (exploratory): the burst, rejected local requests, Wi-Fi traffic, SELinux denials", "7 Discussion (freeRASP's mechanism)"),
    ("S13", "table_freerasp_active_cost", "freeRASP's active-phase cost per device in joules, and what the larger control explains", "7 Discussion (the device gradient)"),
    ("S14", "table_fuel_gauge", "How often the fuel gauge's current reading changes, per device", "7 Discussion (lessons for measurement)"),
    ("S15", "table_arrival_temperature", "Arrival temperature: power slopes, the balance per comparison, the adjusted comparisons", "8 Threats (internal validity)"),
    ("S16", "table_sampler_load", "The memory sampler's extra load on the shielded builds", "8 Threats (construct validity)"),
]
S_FIGURES = [
    ("S1", "qq_per_device", "Q-Q plots of the paired differences for each technique, phase and device.", "4.4.3 and 6.1 (normality)"),
    ("S2", "figure_rq3_active", "Active phase: energy per measurement against phase length, processor utilisation (device-wide), memory "
                                "and thread count, per device; one point per measurement, a least-squares line per build.", "6.4 RQ3"),
    ("S3", "figure_rq3_passive", "Passive phase, as Figure S2; medtimer is not included.", "6.4 RQ3"),
]


def manifest():
    with open(HERE / "MANIFEST.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def check(rows):
    supp = {Path(r["path"]).stem for r in rows if r["placement"] == "SUPPLEMENT" and r["path"].endswith((".tex", ".pdf"))}
    listed = {s for _, s, *_ in S_TABLES} | {s for _, s, *_ in S_FIGURES}
    missing, extra = listed - supp, supp - listed
    if missing or extra:
        sys.exit(f"S list and MANIFEST disagree: listed but not SUPPLEMENT {sorted(missing)}; SUPPLEMENT but not listed {sorted(extra)}")
    for _, s, *_ in S_TABLES:
        assert (HERE / "tables" / f"{s}.tex").exists(), s
    for _, s, *_ in S_FIGURES:
        assert (HERE / "figures" / f"{s}.pdf").exists(), s


def esc(s):
    return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def write_tex(rows):
    nb = {Path(r["path"]).stem: Path(r["notebook"]).name for r in rows}
    lines = [r"\documentclass[10pt,a4paper]{article}", r"\usepackage[margin=1.25cm]{geometry}", r"\usepackage[T1]{fontenc}",
             r"\usepackage{libertine}", r"\usepackage[libertine]{newtxmath}", r"\usepackage{booktabs,graphicx,colortbl,xcolor,placeins}",
             r"\usepackage[labelfont=bf]{caption}", r"\renewcommand{\thetable}{S\arabic{table}}", r"\renewcommand{\thefigure}{S\arabic{figure}}",
             r"\providecommand{\etal}{et~al.}", r"\pagestyle{plain}", r"\begin{document}",
             r"\section*{Supplementary material}", rf"\noindent\emph{{{TITLE}}} --- Irena Ristova, Vrije Universiteit Amsterdam.\par\medskip",
             r"\noindent These tables and figures support the thesis and are cited from it as Table~S$n$ and Figure~S$n$. Each is emitted "
             r"by a notebook of the replication package; the data behind each table is the CSV file of the same name in "
             r"\texttt{6\_results\_analysis/tables/}.\par\medskip",
             r"\begin{center}\small\begin{tabular}{lp{10cm}l}\toprule \textbf{Item} & \textbf{Content} & \textbf{File (the notebook is in INDEX.md)} \\ \midrule"]
    lines += [rf"Table {i} & {esc(what)} & \texttt{{\footnotesize {esc(s)}}} \\" for i, s, what, _ in S_TABLES]
    lines += [rf"Figure {i} & {esc(what.split(';')[0].rstrip('.'))} & \texttt{{\footnotesize {esc(s)}}} \\" for i, s, what, _ in S_FIGURES]
    lines += [r"\bottomrule\end{tabular}\end{center}", r"\clearpage"]
    for _, s, *_ in S_TABLES:
        lines += [rf"\input{{../tables/{s}.tex}}", r"\FloatBarrier"]
    lines += [r"\clearpage"]
    for i, s, cap, _ in S_FIGURES:
        lines += [r"\begin{figure}[p]", r"\centering", rf"\includegraphics[width=\textwidth]{{../figures/{s}.pdf}}",
                  rf"\caption{{{cap}}}", r"\end{figure}", r"\FloatBarrier"]
    lines += [r"\end{document}"]
    OUT.mkdir(exist_ok=True)
    (OUT / "supplementary.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(rows):
    def rows_of(placement):
        return [r for r in rows if r["placement"] == placement and r["path"].endswith((".tex", ".pdf"))]
    out = ["# Index: thesis items, files and the notebooks that emit them", "",
           "Generated by `make_supplement.py` from `MANIFEST.csv`; do not edit by hand.", "",
           "## In the thesis (body and appendix)", "", "| Placement | Thesis section | File | What it shows | Notebook |", "|---|---|---|---|---|"]
    for placement in ("BODY", "APPENDIX"):
        out += [f"| {placement.lower()} | {r['thesis_section']} | `{r['path']}` | {r['description']} | `{Path(r['notebook']).name}` |"
                for r in rows_of(placement)]
    out += ["", "## Supplementary material (`supplementary/supplementary.pdf`)", "",
            "| Item | First cited in | File | What it shows | Data | Notebook |", "|---|---|---|---|---|---|"]
    nb = {Path(r["path"]).stem: Path(r["notebook"]).name for r in rows}
    out += [f"| Table {i} | {where} | `tables/{s}.tex` | {what} | `tables/{s}.csv` | `{nb.get(s, '')}` |" for i, s, what, where in S_TABLES]
    out += [f"| Figure {i} | {where} | `figures/{s}.pdf` | {cap} | | `{nb.get(s, '')}` |" for i, s, cap, where in S_FIGURES]
    (OUT / "INDEX.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def compile_pdf():
    engine = os.environ.get("TECTONIC") or shutil.which("tectonic")
    if not engine:
        print("UNTESTED: no LaTeX engine (set $TECTONIC); supplementary.tex and INDEX.md written")
        return 2
    r = subprocess.run([engine, "-X", "compile", "supplementary.tex"], cwd=OUT, capture_output=True, text=True)
    log = r.stdout + r.stderr
    errors = [l for l in log.splitlines() if l.startswith("error") or l.startswith("!")]
    overfull = [l for l in log.splitlines() if "Overfull" in l]
    ok = r.returncode == 0 and (OUT / "supplementary.pdf").exists()
    print(("PASS" if ok else "FAIL") + f": supplementary.pdf, {len(S_TABLES)} tables, {len(S_FIGURES)} figures")
    for l in (errors if not ok else overfull)[:10]:
        print("   ", l)
    return 0 if ok else 1


if __name__ == "__main__":
    rows = manifest()
    check(rows)
    write_tex(rows)
    write_index(rows)
    sys.exit(compile_pdf())
