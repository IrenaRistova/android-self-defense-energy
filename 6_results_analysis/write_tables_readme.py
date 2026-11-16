#!/usr/bin/env python3
"""Write tables/README.md, the list of every table by placement, from MANIFEST.csv (which the
notebooks write). Re-run after regenerating the notebooks.

Usage (from 6_results_analysis/):  python3 write_tables_readme.py [--manifest PATH] [-o PATH]
"""
import argparse
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent

HEAD = """# Tables: source notebooks

Every table here is written by a notebook under `6_results_analysis/notebooks/`. **BODY** tables appear in the thesis (`.tex` to `\\input`, `.csv` for the data); **ONLINE_APPENDIX** tables are Tables OA1 to OA20 of the online appendix (`../online_appendix/online_appendix.pdf`), which the thesis cites; **REPO** tables are in this repository only; the thesis refers to them as \"the replication package\" (for example the exclusions by cause, `exclusions_by_cause.csv`); **SENSITIVITY** tables (`sensitivity_ten_apps/`) hold the ten-application sensitivity check, which the thesis does not cite. This list is generated from `../MANIFEST.csv`, which also covers the figures.

**The BODY `.tex` tables compile** in ACM `sigconf` (`python3 check_tables_compile.py`: PASS on 2026-11-16, Tectonic 0.17.0); Table 1 is a `table*` (both columns). The online-appendix tables compile into `online_appendix.pdf` (`make_online_appendix.py`). The `.csv` beside each holds the same table.
"""
FOOT = ("To regenerate: run the notebooks from `6_results_analysis/notebooks/` (see the top-level `README.md`). "
        "Inputs: `6_results_data/run_tables/`, `6_results_data/inputs/`, `6_results_data/derived/` and, for Table OA7, `tools/sweep_logs/`.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=HERE / "MANIFEST.csv", type=Path)
    ap.add_argument("-o", "--out", default=HERE / "tables" / "README.md", type=Path)
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(open(a.manifest, encoding="utf-8")) if r["path"].startswith("tables/")]
    out = [HEAD]
    for placement in ("BODY", "APPENDIX", "ONLINE_APPENDIX", "REPO", "SENSITIVITY"):
        group = {}
        for r in sorted(rows, key=lambda r: r["path"]):
            if r["placement"] != placement:
                continue
            f = r["path"][len("tables/"):]
            stem = f.rsplit(".", 1)[0] if placement in ("BODY", "APPENDIX", "ONLINE_APPENDIX") else f      # a body table's .csv and .tex share a row
            group.setdefault(stem, [r, []])[1].append(f)
        if not group:                                                    # no table has this placement (APPENDIX: the thesis's appendix is the online appendix PDF, placement ONLINE_APPENDIX)
            continue
        out += [f"## {placement}", "", "| File | Thesis section | What it holds | Notebook |", "|---|---|---|---|"]
        for r, files in group.values():
            out.append(f"| {', '.join(f'`{f}`' for f in files)} | {r['thesis_section']} | {r['description']} | "
                       f"`{r['notebook'].split('/')[-1]}` |")
        out.append("")
    out.append(FOOT)
    a.out.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: {sum(1 for r in rows)} manifest rows")


if __name__ == "__main__":
    main()
