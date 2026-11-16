#!/usr/bin/env python3
"""Build the thesis's online appendix: online_appendix/online_appendix.tex, online_appendix.pdf and INDEX.md.

The tables and figures that the thesis cites from its online appendix are numbered Table OA1-OA20 and Figure OA1-OA4,
in the order the thesis first cites them (sections 3 Definition, 4 Planning, 5 Execution, 6 A New Technique: The Thermal
Gate, 7 Results, 8 Discussion, 9 Threats to Validity). Each item is the very .tex table or .pdf figure
the notebooks emit (MANIFEST.csv placement ONLINE_APPENDIX), so the online appendix cannot drift from them. INDEX.md maps
every thesis item (BODY, APPENDIX, ONLINE_APPENDIX) to its file and the notebook that emits it.

A table that cites sources (OA5: the literature in panel (a), copied from the thesis's references.bib, and the vendors'
pages in panel (b)) gets a reference list from online_appendix/references.bib; the build fails when a cited key is missing
there.

Fails when the OA list and the ONLINE_APPENDIX rows of MANIFEST.csv disagree in either direction, or when the OA numbers
are not 1, 2, 3, ... in list order.

Usage (from 6_results_analysis/):  TECTONIC=/path/to/tectonic python3 make_online_appendix.py
Without a LaTeX engine it writes the .tex and INDEX.md and reports the PDF as UNTESTED.
"""
import csv
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "online_appendix"
NAME = "online_appendix"
PLACEMENT = "ONLINE_APPENDIX"
TITLE = "On the Impact of Self-Defense Techniques on the Energy Consumption of Android Apps"

# (id, stem, what it shows, where the thesis first cites it), in the order of first citation in the thesis
OA_TABLES = [
    ("OA1", "table_launch_per_device", "Time to first display, technique minus control, per device", "3 Definition (metrics)"),
    ("OA2", "table1_cohort", "The nine applications: identity, size, store listings, scenarios, devices and the arms each contributes matched pairs to", "4 Planning (applications)"),
    ("OA3", "table2_control_build_screen", "Control-build screen: compiler-written source-file names in each control APK", "4 Planning (validity of the control builds)"),
    ("OA4", "table_durations_per_device", "Phase duration per run by device, build and phase; medtimer's passive phase as measured", "4 Planning (workload duration)"),
    ("OA5", "table_tool_families", "The three protection families considered and their candidate tools, and the three tools measured: "
                                 "vendor, version, licence and cost, key characteristic", "4 Planning (tool choice)"),
    ("OA6", "table_obfuscation_static", "What the obfuscator changed in each application's own code", "4 Planning (the obfuscated builds)"),
    ("OA7", "table_thermal_gate", "The thermal gate: charging failures, starting temperatures, and the within-pilot test", "6 Technique (the charge refusal and the thermal gate)"),
    ("OA8", "table_pilot_series", "Repeatability, pilot against series", "6 Technique (pilot against series)"),
    ("OA9", "table_fuel_gauge", "How often the fuel gauge's current reading changes, per device", "6 Technique (the fuel gauge)"),
    ("OA10", "table_descriptives_per_device", "Energy per run by device, build and phase", "7 Results (energy per run)"),
    ("OA11", "table_normality", "Shapiro-Wilk test on the per-pair differences of each comparison", "7 Results (normality)"),
    ("OA12", "table_per_app_active", "Per application, active phase: mean energy difference per technique and device", "7 Results (per application)"),
    ("OA13", "table_per_app_passive", "Per application, passive phase: mean energy difference per technique and device", "7 Results (per application)"),
    ("OA14", "table_rq4_full", "RQ3 in full: each protected build against its control in memory, threads, processor share and phase duration", "7 Results (RQ3)"),
    ("OA15", "table_freerasp_active_cost", "freeRASP's active-phase cost per device in joules, and what the larger control explains", "8 Discussion (the device gradient)"),
    ("OA16", "table5_allatori_applicability", "Applying Allatori to the nine applications: renaming and run-time outcome", "8 Discussion (obfuscation)"),
    ("OA17", "table6_freerasp_postlaunch", "Where freeRASP's passive-phase energy goes (exploratory)", "8 Discussion (what runtime shielding does)"),
    ("OA18", "table_freerasp_mechanism", "Runtime shielding per device (exploratory): the burst, rejected local requests, Wi-Fi traffic, SELinux denials", "8 Discussion (what runtime shielding does)"),
    ("OA19", "table_arrival_temperature", "Arrival temperature: power slopes, the balance per comparison, the adjusted comparisons", "9 Threats (internal validity)"),
    ("OA20", "table_sampler_load", "The memory sampler's extra load on the shielded builds", "9 Threats (construct validity)"),
]
OA_FIGURES = [
    ("OA1", "qq_per_device", "Q-Q plots of the paired differences for each technique, phase and device.", "7 Results (normality)"),
    ("OA2", "figure_rq3_length", "Energy per run against phase duration, per device and phase, with a least-squares line per build.",
     "7 Results (RQ4)"),
    ("OA3", "figure_rq3_active", "Active phase: energy per run against phase duration, processor utilisation (device-wide), memory "
                                 "and thread count, per device; one point per run, a least-squares line per build.", "7 Results (RQ4)"),
    ("OA4", "figure_rq3_passive", "Passive phase, as Figure OA3; medtimer is not included.", "7 Results (RQ4)"),
]
# a figure drawn one column wide for the thesis is shown at this width, not stretched to the full text width
FIG_WIDTH = {"figure_rq3_length": r"0.6\textwidth"}


def manifest():
    with open(HERE / "MANIFEST.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def check(rows):
    for items in (OA_TABLES, OA_FIGURES):
        ids = [i for i, *_ in items]
        if ids != [f"OA{k}" for k in range(1, len(items) + 1)]:
            sys.exit(f"OA numbers out of order: {ids}")
    oa = {Path(r["path"]).stem for r in rows if r["placement"] == PLACEMENT and r["path"].endswith((".tex", ".pdf"))}
    listed = {s for _, s, *_ in OA_TABLES} | {s for _, s, *_ in OA_FIGURES}
    missing, extra = listed - oa, oa - listed
    if missing or extra:
        sys.exit(f"OA list and MANIFEST disagree: listed but not {PLACEMENT} {sorted(missing)}; {PLACEMENT} but not listed {sorted(extra)}")
    for _, s, *_ in OA_TABLES:
        assert (HERE / "tables" / f"{s}.tex").exists(), s
    for _, s, *_ in OA_FIGURES:
        assert (HERE / "figures" / f"{s}.pdf").exists(), s


CITE = re.compile(r"\\cite[a-zA-Z]*\{([^}]*)\}")
BIB = OUT / "references.bib"


def cited_keys():
    """Every key an OA table cites; fails when one is missing from online_appendix/references.bib."""
    keys = sorted({k.strip() for _, s, *_ in OA_TABLES for m in CITE.finditer((HERE / "tables" / f"{s}.tex").read_text(encoding="utf-8"))
                   for k in m.group(1).split(",") if k.strip()})
    if keys:
        have = set(re.findall(r"@\w+\{([^,\s]+),", BIB.read_text(encoding="utf-8"))) if BIB.exists() else set()
        missing = [k for k in keys if k not in have]
        if missing:
            sys.exit(f"cited in an OA table but not in {BIB.relative_to(HERE)}: {missing}")
    return keys


def esc(s):
    return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def no_csv():
    """The OA tables without a CSV of the same name: tables of text, written by the notebook as .tex only."""
    return [i for i, s, *_ in OA_TABLES if not (HERE / "tables" / f"{s}.csv").exists()]


def write_tex():
    text_only = (" (" + ", ".join(f"Table~{i}" for i in no_csv()) + (" has" if len(no_csv()) == 1 else " have") + " no CSV file)") if no_csv() else ""
    lines = [r"\documentclass[10pt,a4paper]{article}", r"\usepackage[margin=1.25cm]{geometry}", r"\usepackage[T1]{fontenc}",
             r"\usepackage{libertine}", r"\usepackage[libertine]{newtxmath}", r"\usepackage{booktabs,graphicx,colortbl,xcolor,placeins}",
             r"\usepackage[labelfont=bf]{caption}", r"\usepackage{cite}", r"\usepackage{url}", r"\urlstyle{same}",
             r"\renewcommand{\thetable}{OA\arabic{table}}",
             r"\renewcommand{\thefigure}{OA\arabic{figure}}", r"\providecommand{\etal}{et~al.}", r"\pagestyle{plain}", r"\begin{document}",
             r"\section*{Online appendix}", rf"\noindent\emph{{{TITLE}}}\par\noindent Irena Ristova, Vrije Universiteit Amsterdam\par\medskip",
             r"\noindent These tables and figures support the thesis, which cites them as Table~OA$n$ and Figure~OA$n$, numbered in "
             r"the order the thesis first cites them. Each is emitted by a notebook of the replication package; the data behind each "
             r"table is the CSV file of the same name in \texttt{6\_results\_analysis/tables/}" + text_only + r".\par\medskip",
             r"\begin{center}\small\begin{tabular}{lp{10cm}l}\toprule \textbf{Item} & \textbf{Content} & \textbf{File (the notebook is in INDEX.md)} \\ \midrule"]
    lines += [rf"Table {i} & {esc(what)} & \texttt{{\footnotesize {esc(s)}}} \\" for i, s, what, _ in OA_TABLES]
    lines += [rf"Figure {i} & {esc(what.split(';')[0].rstrip('.'))} & \texttt{{\footnotesize {esc(s)}}} \\" for i, s, what, _ in OA_FIGURES]
    lines += [r"\bottomrule\end{tabular}\end{center}", r"\clearpage"]
    for _, s, *_ in OA_TABLES:
        lines += [rf"\input{{../tables/{s}.tex}}", r"\FloatBarrier"]
    lines += [r"\clearpage"]
    for i, s, cap, _ in OA_FIGURES:
        width = FIG_WIDTH.get(s, r"\textwidth")
        lines += [r"\begin{figure}[p]", r"\centering", rf"\includegraphics[width={width}]{{../figures/{s}.pdf}}",
                  rf"\caption{{{cap}}}", r"\end{figure}", r"\FloatBarrier"]
    if cited_keys():
        lines += [r"\bibliographystyle{abbrv}", r"\bibliography{references}"]
    lines += [r"\end{document}"]
    OUT.mkdir(exist_ok=True)
    (OUT / f"{NAME}.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(rows):
    def rows_of(placement):
        return [r for r in rows if r["placement"] == placement and r["path"].endswith((".tex", ".pdf"))]
    out = ["# Index: thesis items, files and the notebooks that emit them", "",
           "Generated by `make_online_appendix.py` from `MANIFEST.csv`; do not edit by hand.", "",
           "## In the thesis (body" + (" and appendix" if rows_of("APPENDIX") else "") + ")", "",
           "| Placement | Thesis section | File | What it shows | Notebook |", "|---|---|---|---|---|"]
    for placement in ("BODY", "APPENDIX"):
        out += [f"| {placement.lower()} | {r['thesis_section']} | `{r['path']}` | {r['description']} | `{Path(r['notebook']).name}` |"
                for r in rows_of(placement)]
    out += ["", f"## Online appendix (`{NAME}/{NAME}.pdf`)", "",
            "| Item | First cited in | File | What it shows | Data | Notebook |", "|---|---|---|---|---|---|"]
    nb = {Path(r["path"]).stem: Path(r["notebook"]).name for r in rows}
    out += [f"| Table {i} | {where} | `tables/{s}.tex` | {what} | " + (f"`tables/{s}.csv`" if i not in no_csv() else "no CSV file")
            + f" | `{nb.get(s, '')}` |" for i, s, what, where in OA_TABLES]
    out += [f"| Figure {i} | {where} | `figures/{s}.pdf` | {cap} | | `{nb.get(s, '')}` |" for i, s, cap, where in OA_FIGURES]
    (OUT / "INDEX.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def compile_pdf():
    engine = os.environ.get("TECTONIC") or shutil.which("tectonic")
    if not engine:
        print(f"UNTESTED: no LaTeX engine (set $TECTONIC); {NAME}.tex and INDEX.md written")
        return 2
    r = subprocess.run([engine, "-X", "compile", f"{NAME}.tex"], cwd=OUT, capture_output=True, text=True)
    log = r.stdout + r.stderr
    errors = [l for l in log.splitlines() if l.startswith("error") or l.startswith("!")]
    overfull = [l for l in log.splitlines() if "Overfull" in l]
    ok = r.returncode == 0 and (OUT / f"{NAME}.pdf").exists()
    print(("PASS" if ok else "FAIL") + f": {NAME}.pdf, {len(OA_TABLES)} tables, {len(OA_FIGURES)} figures")
    for l in (errors if not ok else overfull)[:10]:
        print("   ", l)
    return 0 if ok else 1


if __name__ == "__main__":
    rows = manifest()
    check(rows)
    write_tex()
    write_index(rows)
    sys.exit(compile_pdf())
