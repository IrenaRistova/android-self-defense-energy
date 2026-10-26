#!/usr/bin/env python3
"""Test-compile every BODY table and figure in the thesis's document class.

Builds one ACM `sigconf` document that \\input-s every BODY `.tex` table and
\\includegraphics-es every BODY figure at 100%, and compiles it. Reports PASS, FAIL (with
the LaTeX error), or UNTESTED when no LaTeX engine is found: never PASS without a compile.

Engine: $TECTONIC, else `tectonic`, `latexmk` or `pdflatex` on the PATH.
Usage (from 6_results_analysis/):  python3 check_tables_compile.py   (exit 0 PASS, 1 FAIL, 2 UNTESTED)
"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
body = [r["path"] for r in csv.DictReader(open(HERE / "MANIFEST.csv")) if r["placement"] in ("BODY", "APPENDIX")]
tables = sorted(p for p in body if p.endswith(".tex"))
figures = sorted(p for p in body if p.endswith(".pdf"))

engine = os.environ.get("TECTONIC") or shutil.which("tectonic") or shutil.which("latexmk") or shutil.which("pdflatex")
if not engine:
    print("UNTESTED: no LaTeX engine (set $TECTONIC or install tectonic / latexmk / pdflatex)")
    sys.exit(2)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    for p in tables + figures:
        (td / Path(p).name).write_bytes((HERE / p).read_bytes())
    doc = [r"\documentclass[sigconf,nonacm]{acmart}", r"\usepackage{booktabs,graphicx,colortbl}", r"\begin{document}",   # as in the thesis
           r"\title{Compile test}", r"\maketitle"]
    doc += [rf"\input{{{Path(p).name}}}" for p in tables]
    def env(p):  # a figure drawn wider than one column (FIG_DOUBLE) is placed as figure*, as the thesis must place it
        import re
        box = re.search(rb"/MediaBox\s*\[\s*[\d.]+\s+[\d.]+\s+([\d.]+)", (HERE / p).read_bytes())
        return "figure*" if box and float(box.group(1)) > 3.5 * 72 else "figure"
    doc += [rf"\begin{{{env(p)}}}[t]\includegraphics{{{Path(p).name}}}\caption{{test}}\end{{{env(p)}}}" for p in figures]
    doc += [r"\clearpage", r"\end{document}"]
    (td / "check.tex").write_text("\n".join(doc) + "\n")
    name = Path(engine).name
    cmd = ([engine, "-X", "compile", "check.tex"] if name == "tectonic" else
           [engine, "-pdf", "-interaction=nonstopmode", "check.tex"] if name == "latexmk" else
           [engine, "-interaction=nonstopmode", "check.tex"])
    r = subprocess.run(cmd, cwd=td, capture_output=True, text=True)
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and (td / "check.pdf").exists()
    errors = [l for l in out.splitlines() if "error" in l.lower() or l.startswith("!")]
    overfull = [l for l in out.splitlines() if "Overfull" in l]
    print(("PASS" if ok else "FAIL") + f": {len(tables)} tables, {len(figures)} figures, engine {name}")
    for l in (errors if not ok else overfull)[:10]:
        print("   ", l)
    sys.exit(0 if ok else 1)
