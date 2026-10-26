"""Every results figure must read against the control without its caption.

Checked from the text inside each PDF (pdftotext), so it tests what a reader sees:
  1. an axis says "vs. control"
  2. a reference line is labelled "unprotected control"
  3. no tick or bar is labelled "Control" (the control drawn as a category of its own)
  4. no axis presents absolute energy (the word "energy" does not appear)
  5. drawn at the single-column width, 3.33 in

Usage (from 6_results_analysis/): python3 check_figure_convention.py figures
Needs pdftotext and pdfinfo (poppler-utils). Exit 1 if any figure fails.
"""
import re
import subprocess
import sys
from pathlib import Path

d = Path(sys.argv[1])
NOT_RESULTS = {"selection_funnel", "F6_selection_funnel"}     # counts applications, not energy
bad = 0
for pdf in sorted(d.glob("*.pdf")):
    if pdf.stem in NOT_RESULTS:
        print(f"skip  {pdf.name}: not a results figure (application counts)")
        continue
    text = subprocess.run(["pdftotext", "-raw", str(pdf), "-"], capture_output=True, text=True).stdout
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    w = float(re.search(r"Page size:\s+([\d.]+)", info)[1]) / 72
    text = re.sub(r"\s+", "", text)   # rotated labels come out one character per line
    fails = []
    if "vs.control" not in text:
        fails.append('no axis says "vs. control"')
    if "unprotectedcontrol" not in text:
        fails.append('no line labelled "unprotected control"')
    if re.search(r"(Control|[Bb]aseline)", text):
        fails.append('the control shown as a quantity of its own (tick, bar or axis)')
    if re.search(r"energy", text, re.I):
        fails.append("an axis presents absolute energy")
    if abs(w - 3.33) > 0.01:
        fails.append(f"width {w:.2f} in, not 3.33")
    print(("PASS " if not fails else "FAIL ") + pdf.name + ("" if not fails else ": " + "; ".join(fails)))
    bad += bool(fails)
sys.exit(1 if bad else 0)
