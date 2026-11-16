#!/usr/bin/env python3
"""Build the thesis's online appendix: online_appendix/online_appendix.tex, online_appendix.pdf, INDEX.md and SOURCES.md.

The tables and figures that the thesis cites from its online appendix are numbered Table OA1-OA20 and Figure OA1-OA4,
in the order the thesis first cites them (sections 3 Definition, 4 Planning, 5 Execution, 6 A New Technique: The Thermal
Gate, 7 Results, 8 Discussion, 9 Threats to Validity). Each item is the very .tex table or .pdf figure
the notebooks emit (MANIFEST.csv placement ONLINE_APPENDIX), so the online appendix cannot drift from them. INDEX.md maps
every thesis item (BODY, APPENDIX, ONLINE_APPENDIX) to its file and the notebook that emits it.

Layout: every table and figure stands where it is input (placement [H], also for table*), several to a page, under a
heading that names the thesis section that first cites it; a running head gives "Online appendix" and the thesis title.
In a table, the tabular is centred and the note under it is set ragged-right.

The PDF holds only tables and figures: no OA table may cite (the build fails when one does), and there is no reference
list. The sources of Table OA5 (the papers that panel (a) cites, the vendor pages behind each entry of panel (b)) are in
online_appendix/SOURCES.md, which this script writes from online_appendix/references.bib and SOURCES below; it fails
when a key of SOURCES is missing from references.bib or a key of references.bib is not used in SOURCES, and when an entry
of SOURCES is not in Table OA5 as SOURCES gives it.

Fails when the OA list and the ONLINE_APPENDIX rows of MANIFEST.csv disagree in either direction, or when the OA numbers
are not 1, 2, 3, ... in list order.

Usage (from 6_results_analysis/):  TECTONIC=/path/to/tectonic python3 make_online_appendix.py
Without a LaTeX engine it writes the .tex, INDEX.md and SOURCES.md and reports the PDF as UNTESTED.
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
# the thesis's sections, for the headings that group the items by the section that first cites them
SECTIONS = {3: "Experiment Definition", 4: "Experiment Planning", 5: "Experiment Execution", 6: "A New Technique: The Thermal Gate",
            7: "Results", 8: "Discussion", 9: "Threats to Validity"}

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

# Every table, table* and figure is placed where it stands ([H] of the float package), so several share a page in the
# thesis's order. table* must end through \endtable: the float package redefines \endtable for [H] at the start of each
# float, and ending through a saved copy of the macro loses the table.
PREAMBLE = [
    r"\documentclass[10pt,a4paper]{article}", r"\usepackage[margin=1.25cm]{geometry}", r"\usepackage[T1]{fontenc}",
    r"\usepackage{libertine}", r"\usepackage[libertine]{newtxmath}",
    r"\DeclareSymbolFont{operators}{TU}{\rmdefault}{m}{n}   % digits and operators in math in Libertine, not Computer Modern", r"\usepackage{booktabs,graphicx,colortbl,xcolor,placeins}",
    r"\usepackage[labelfont=bf]{caption}", r"\renewcommand{\thetable}{OA\arabic{table}}", r"\renewcommand{\thefigure}{OA\arabic{figure}}",
    r"\providecommand{\etal}{et~al.}",
    r"\usepackage{float}",
    r"\usepackage{fancyhdr}",
    r"\pagestyle{fancy}\fancyhf{}",
    r"\fancyhead[L]{\small Online appendix}",
    rf"\fancyhead[R]{{\small\itshape {TITLE}}}",
    r"\fancyfoot[C]{\small \thepage}",
    r"\renewcommand{\headrulewidth}{0.4pt}",
    r"% every table and figure is placed where it stands, in order, several to a page",
    r"\makeatletter",
    r"\let\oa@table\table \let\oa@endtable\endtable",
    r"\renewenvironment{table}[1][]{\oa@table[H]\oa@tablesetup}{\oa@endtable}",
    r"\expandafter\def\csname table*\endcsname{\@ifnextchar[{\oa@tablestar}{\oa@tablestar[]}}",
    r"\def\oa@tablestar[#1]{\oa@table[H]\oa@tablesetup}",
    r"\expandafter\def\csname endtable*\endcsname{\endtable}",
    r"\let\oa@figure\figure \let\oa@endfigure\endfigure",
    r"\renewenvironment{figure}[1][]{\oa@figure[H]}{\oa@endfigure}",
    r"\makeatother",
    r"\setlength{\intextsep}{16pt plus 4pt minus 2pt}",
    r"% in a table, each top-level tabular stands centred on a line of its own, and the text around it (the note, a panel",
    r"% label) is set ragged-right at the text width; a tabular nested in a cell is untouched",
    r"\usepackage{etoolbox}",
    r"\makeatletter",
    r"\newif\ifoa@top \let\oa@centering\centering",
    r"\def\oa@ragged{\let\\\@centercr \leftskip\z@skip \rightskip\z@ \@plus 2em\relax \parfillskip\z@ \@plus 1fil\relax}",
    r"\def\oa@tablesetup{\oa@toptrue \let\centering\oa@ragged}",
    r"\AtBeginEnvironment{tabular}{\oa@topfalse}",
    r"\BeforeBeginEnvironment{tabular}{\ifoa@top\par\oa@centering\fi}",
    r"\AfterEndEnvironment{tabular}{\ifoa@top\par\oa@ragged\fi}",
    r"\makeatother",
]

# SOURCES.md: what backs each entry of Table OA5, by the table's rows. Keys are those of online_appendix/references.bib.
READ_ON = "16 November 2026"
SOURCES_A = [   # panel (a): family, candidate tools, the papers the family was cited with
    ("Obfuscation (code transformation)", "ProGuard, R8, DexGuard, Allatori (measured), DashO, Obfuscapk",
     ["Faruki2017AndroidCodeProtectionSurvey"]),
    ("Packing (loader-based)", "Bangcle (measured), Qihoo 360 Jiagu, Tencent Legu, Ijiami, AliProtect, Baidu, Naga, Manxi, APKProtect, SecNeo",
     ["Duan2018AndroidUnpackers", "Dong2022PackDiff", "BlazquezTapiador2025AndroidProtectionWild"]),
    ("Runtime shielding (RASP)", "freeRASP (measured), DexProtector, Appdome, Promon SHIELD, LIAPP, WhiteCryption, V-Key, Virbox Protector, AppSealing",
     ["Faruki2017AndroidCodeProtectionSurvey", "BlazquezTapiador2025AndroidProtectionWild"]),
]
SOURCES_B = [   # panel (b): tool, then (column, the entry, what the page says, keys of the pages that back it)
    ("Allatori", [
        ("Vendor (country)", "Smardec Inc. (country not stated)",
         "The footer of the vendor's pages reads \"Copyright (c) 2005-2026 Smardec Inc.\". None of the vendor's pages "
         "(allatori.com and smardec.com) nor the licence that comes with the tool states an address, a city or a country.",
         ["oa_allatori_overview"]),
        ("Version", "9.8, educational edition (released 1 June 2026)",
         "The changelog lists \"Version 9.8 - June 01, 2026\". The measured builds use the educational edition of 9.8; "
         "9.9 came out on 1 September 2026.", ["oa_allatori_changelog"]),
        ("Licence and cost", "Educational licence (used here): free on request. Commercial: US$290 per developer, a licence with "
         "no time limit (12 months of updates included)",
         "The price page: \"Free for educational and non-commercial projects\"; such developers \"may be eligible to receive "
         "Allatori Obfuscator for free\" by contacting the vendor. Single Developer Licence US$290, with 12 months of email "
         "support, updates and upgrades. The licence in the tool's distribution: \"There is no time limitation on using the "
         "Software Product\".", ["oa_allatori_price"]),
        ("Key characteristic", "Renames classes, methods and fields, encrypts strings (an added method decrypts them at run time) "
         "and obfuscates control flow, in the compiled classes at build time",
         "The documentation: string encryption \"enable (default) ... Allatori will add a method to decrypt strings on "
         "run-time\"; control-flow obfuscation \"enable (default)\".", ["oa_allatori_doc"]),
    ]),
    ("Bangcle", [
        ("Vendor (country)", "Beijing Bangcle Security Technology Co., Ltd. (China)",
         "Clause 1.1 of the user service agreement names the company 北京梆梆安全科技有限公司 (北京: Beijing) and the mark "
         "\"Bangcle\". The English company name is a translation.", ["oa_bangcle_agreement"]),
        ("Version", "None reported by the service (builds packed in May and June 2026)",
         "No version is published. The measured version is the service as it ran when the builds were packed "
         "(12 May, 15 May and 29 June 2026).", []),
        ("Licence and cost", "Standard tier (used here): free. Customised hardening: paid, price on request",
         "The help centre: \"标准版应用加固服务是免费的，定制版加固服务为收费版本，可以在线联系客服人员咨询详情\" (the standard "
         "hardening service is free; the customised one is paid; ask customer service for details).", ["oa_bangcle_faq"]),
        ("Key characteristic", "Encrypts the bytecode into a payload that a stub decrypts and loads at run time, applied to the "
         "finished APK",
         "The feature page: the service extracts all code from classes.dex and encrypts and packs the whole file "
         "(\"抽取classes.dex中所有代码 ... 整体文件深度加密加壳\"). The packed APKs carry the payload assets/classes0.jar, "
         "a stub classes.dex (com.SecShell) and libSecShell.so.", ["oa_bangcle_features"]),
    ]),
    ("freeRASP", [
        ("Vendor (country)", "Talsec, Lynx SFT s.r.o. (Czech Republic)",
         "The contact page: \"Lynx SFT, s.r.o Main office: Lidická 2030/20 Brno, 602 00 Czech Republic\".", ["oa_talsec_contact"]),
        ("Version", "Community 18.0.2 (released 10 February 2026)",
         "Release 18.0.2 of the Android SDK was published on 10 February 2026. The measured builds use "
         "TalsecSecurity-Community 18.0.2 (tools/pack_freerasp.sh).", ["oa_freerasp_release"]),
        ("Licence and cost", "Community tier (used here): free up to 100,000 devices, with mandatory data collection by the "
         "vendor. Paid plans from EUR 284 per month",
         "The Fair Usage Policy (version 1.1, effective 13 October 2025): freeRASP is free while the customer's applications "
         "together stay under 100,000 unique devices, and Talsec's collection of threat and telemetry data is mandatory. The "
         "plans on the vendor's home page start at EUR 284 per month (RASP+ Starter).", ["oa_freerasp_fup", "oa_talsec_plans"]),
        ("Key characteristic", "A library in the application's process that checks for root, debuggers, hooking frameworks, "
         "emulators and tampering, at launch and periodically",
         "The features page lists the checks. The FAQ: \"One-time checks are executed only once when the app is launched "
         "... Periodic checks are performed regularly, at defined intervals\". Here its threat callbacks only log each threat "
         "and never stop the application (the listener tools/pack_freerasp.sh adds).", ["oa_freerasp_features", "oa_freerasp_faq"]),
    ]),
]


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
    for where in [w for *_, w in OA_TABLES + OA_FIGURES]:
        assert int(where.split()[0]) in SECTIONS, where


CITE = re.compile(r"\\(no)?cite[a-zA-Z]*\{")
BIB = OUT / "references.bib"


def no_cites():
    """The online appendix holds only tables and figures: fails when an OA table cites (its sources go to SOURCES.md)."""
    citing = [s for _, s, *_ in OA_TABLES if CITE.search((HERE / "tables" / f"{s}.tex").read_text(encoding="utf-8"))]
    if citing:
        sys.exit(f"OA tables with a citation (list the source in SOURCES.md instead): {citing}")


def sources_match_table():
    """SOURCES repeats Table OA5's entries: fails when a tool, a family, a list of candidate tools or an entry of panel (b)
    is not in tables/table_tool_families.tex as SOURCES gives it (the .tex unescaped; $^{*}$ read as " (measured)")."""
    tex = (HERE / "tables" / "table_tool_families.tex").read_text(encoding="utf-8")
    text = tex.replace("\\ ", " ").replace("~", " ").replace("\\$", "$").replace("$^{*}$", " (measured)")
    want = ([x for fam, tools, _ in SOURCES_A for x in (fam, tools)]
            + [x for tool, facts in SOURCES_B for x in [tool] + [entry for _, entry, *_ in facts]])
    drift = [x for x in want if x not in text]
    if drift:
        sys.exit(f"SOURCES and tables/table_tool_families.tex disagree; not in the table as SOURCES gives it: {drift}")


def esc(s):
    return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def no_csv():
    """The OA tables without a CSV of the same name: tables of text, written by the notebook as .tex only."""
    return [i for i, s, *_ in OA_TABLES if not (HERE / "tables" / f"{s}.csv").exists()]


def heading(where, figures=False):
    n = int(where.split()[0])
    return (rf"\subsection*{{Figures, cited in Section {n}, {SECTIONS[n]}}}" if figures else
            rf"\subsection*{{Cited in Section {n}, {SECTIONS[n]}}}")


def write_tex():
    text_only = (" (" + ", ".join(f"Table~{i}" for i in no_csv()) + (" has" if len(no_csv()) == 1 else " have") + " no CSV file)") if no_csv() else ""
    lines = PREAMBLE + [r"\begin{document}",
             r"\section*{Online appendix}", rf"\noindent\emph{{{TITLE}}}\par\noindent Irena Ristova, Vrije Universiteit Amsterdam\par\medskip",
             r"\noindent These tables and figures support the thesis, which cites them as Table~OA$n$ and Figure~OA$n$, numbered in "
             r"the order the thesis first cites them. Each is emitted by a notebook of the replication package; the data behind each "
             r"table is the CSV file of the same name in \texttt{6\_results\_analysis/tables/}" + text_only + r".\par\medskip",
             r"\begin{center}\small\begin{tabular}{lp{10cm}l}\toprule \textbf{Item} & \textbf{Content} & \textbf{File (the notebook is in INDEX.md)} \\ \midrule"]
    lines += [rf"Table {i} & {esc(what)} & \texttt{{\footnotesize {esc(s)}}} \\" for i, s, what, _ in OA_TABLES]
    lines += [rf"Figure {i} & {esc(what.split(';')[0].rstrip('.'))} & \texttt{{\footnotesize {esc(s)}}} \\" for i, s, what, _ in OA_FIGURES]
    lines += [r"\bottomrule\end{tabular}\end{center}"]
    section = None
    for _, s, _, where in OA_TABLES:
        if int(where.split()[0]) != section:
            section = int(where.split()[0])
            lines += [heading(where)]
        lines += [rf"\input{{../tables/{s}.tex}}", r"\FloatBarrier"]
    lines += [heading(OA_FIGURES[0][3], figures=True)]
    assert len({int(w.split()[0]) for *_, w in OA_FIGURES}) == 1, "the figures come from more than one section: one heading each"
    for i, s, cap, _ in OA_FIGURES:
        width = FIG_WIDTH.get(s, r"\textwidth")
        lines += [r"\begin{figure}[H]", r"\centering", rf"\includegraphics[width={width}]{{../figures/{s}.pdf}}",
                  rf"\caption{{{cap}}}", r"\end{figure}", r"\FloatBarrier"]
    lines += [r"\end{document}"]
    OUT.mkdir(exist_ok=True)
    (OUT / f"{NAME}.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def bib_entries():
    """references.bib as {key: {field: value}}; one field per line, as the file is written."""
    entries, key = {}, None
    for line in BIB.read_text(encoding="utf-8").splitlines():
        m = re.match(r"@(\w+)\{([^,\s]+),", line)
        if m:
            key = m.group(2)
            entries[key] = {"type": m.group(1).lower()}
            continue
        m = re.match(r"\s*(\w+)\s*=\s*\{(.*)\},?\s*$", line)
        if m and key:
            entries[key][m.group(1).lower()] = m.group(2)
    return entries


def plain(s):
    """A BibTeX value as plain text."""
    s = re.sub(r"\\url\{([^}]*)\}", r"\1", s)
    s = s.replace(r"{\'a}", "á").replace("``", "\"").replace("''", "\"").replace("--", "-")
    return s.replace("{", "").replace("}", "")


def reference(key, e):
    """One readable reference line for a references.bib entry."""
    if e["type"] == "misc":
        url = plain(e["howpublished"])
        note = plain(e.get("note", "")).replace("Accessed 2026-11-16", "").strip().rstrip(".")
        note = note[:1].lower() + note[1:]
        return f"{plain(e['title'])}, {url}" + (f" ({note})" if note else "") + f", read on {READ_ON}. `[{key}]`"
    authors = plain(e["author"]).split(" and ")
    venue = plain(e.get("journal") or e.get("booktitle", ""))
    extra = "".join(f", {lab}{plain(e[f])}" for f, lab in (("volume", "vol. "), ("number", "no. "), ("pages", "pp. ")) if f in e)
    link = f" https://doi.org/{e['doi']}" if "doi" in e else (f" {e['url']}" if "url" in e else "")
    return f"{', '.join(authors[:-1])} and {authors[-1]} ({e['year']}). {plain(e['title'])}. {venue}{extra}.{link} `[{key}]`"


def write_sources():
    """online_appendix/SOURCES.md: the papers and vendor pages behind each entry of Table OA5."""
    bib = bib_entries()
    used = {k for *_, keys in SOURCES_A for k in keys} | {k for _, facts in SOURCES_B for *_, keys in facts for k in keys}
    missing, unused = used - set(bib), set(bib) - used
    if missing or unused:
        sys.exit(f"SOURCES and {BIB.relative_to(HERE)} disagree: not in the .bib {sorted(missing)}; in the .bib but not used {sorted(unused)}")
    oa5 = next(i for i, s, *_ in OA_TABLES if s == "table_tool_families")
    out = [f"# Sources of Table {oa5}", "",
           f"Written by `make_online_appendix.py`; do not edit by hand. Table {oa5} of the online appendix (`tables/table_tool_families.tex`) "
           "cites nothing itself: this file gives the source of each of its entries, by the table's rows. "
           f"`references.bib` holds the same {len(bib)} works in BibTeX. The vendor pages were read on {READ_ON}.", "",
           "## Panel (a): the three protection families and their candidate tools", "",
           "The papers below back each family. Per-tool sources and reasons for exclusion: "
           "`6_results_analysis/tables/candidate_tools.csv`.", ""]
    for fam, tools, keys in SOURCES_A:
        out += [f"### {fam}", "", f"Candidate tools: {tools}.", ""] + [f"- {reference(k, bib[k])}" for k in keys] + [""]
    out += ["## Panel (b): the three tools measured", "",
            "Each entry as the table gives it, the evidence for it, and the page it comes from.", ""]
    for tool, facts in SOURCES_B:
        out += [f"### {tool}", ""]
        for col, entry, says, keys in facts:
            out += [f"- **{col}**: {entry}.", f"  - Evidence: {says}"]
            out += [f"  - Source: {reference(k, bib[k])}" for k in keys] or ["  - Source: the packed builds themselves; no page of the service gives a version."]
        out += [""]
    (OUT / "SOURCES.md").write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


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
    oa5 = next(i for i, s, *_ in OA_TABLES if s == "table_tool_families")
    out += ["", f"## Online appendix (`{NAME}/{NAME}.pdf`)", "",
            f"The PDF holds only tables and figures. The sources of Table {oa5}'s entries (papers and vendor pages) are in "
            f"`{NAME}/SOURCES.md`; `{NAME}/references.bib` holds the same works in BibTeX.", "",
            "| Item | First cited in | File | What it shows | Data | Notebook |", "|---|---|---|---|---|---|"]
    nb = {Path(r["path"]).stem: Path(r["notebook"]).name for r in rows}
    out += [f"| Table {i} | {where} | `tables/{s}.tex` | {what} | " + (f"`tables/{s}.csv`" if i not in no_csv() else "no CSV file")
            + f" | `{nb.get(s, '')}` |" for i, s, what, where in OA_TABLES]
    out += [f"| Figure {i} | {where} | `figures/{s}.pdf` | {cap} | | `{nb.get(s, '')}` |" for i, s, cap, where in OA_FIGURES]
    (OUT / "INDEX.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def compile_pdf():
    engine = os.environ.get("TECTONIC") or shutil.which("tectonic")
    if not engine:
        print(f"UNTESTED: no LaTeX engine (set $TECTONIC); {NAME}.tex, INDEX.md and SOURCES.md written")
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
    no_cites()
    sources_match_table()
    write_tex()
    write_index(rows)
    write_sources()
    sys.exit(compile_pdf())
