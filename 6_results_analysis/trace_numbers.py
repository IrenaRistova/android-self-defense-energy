#!/usr/bin/env python3
"""Trace every number in a text (.tex or .md), such as a thesis section, to the table cells it could come from.

For each number as printed, it lists the cells of tables/*.csv and the values of
tables/headline_numbers.json that round to it at the printed precision. A number that
matches nothing was typed or computed by hand: emit it from a notebook or drop it.
A match is evidence, not proof (a common value can match by coincidence), so read the
source column.

In a .tex file, a "% src: tables/a.csv, tables/b.csv" comment names the files the text
below it draws on; matches in those files are listed first, and a number that matches
only elsewhere is marked "elsewhere". A number whose every match lies in a table that
MANIFEST.csv places in the replication package only (REPO) is marked "(replication
package only)": the superseded pooled analysis is still emitted there, so such a number
may be stale even though it is found. A number found only in the numbered supplementary tables
(placement SUPPLEMENT, Table S1-S16 of supplementary.pdf) is marked "(supplement only: ...)":
it is valid, but its sentence must point to the S-table. Method constants (the 95% interval, 80% power,
the 1.5-IQR whisker) and device names (Pixel 3/6/9) are not traced.

Usage (from 6_results_analysis/):
    python3 trace_numbers.py path/to/section.tex [-o ledger.csv] [--tables DIR]
Exit 0 if every number is found, 1 otherwise.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLES = HERE / "tables"
NUM = re.compile(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)(?![\w.]*\w)")
SKIP = [r"Pixel[~ ]\d", r"RQ\d", r"95\\?%", r"80\\?%", r"1\.5 times", r"\(Pixel~\d\)",
        r"Section~?\S*", r"Table~?\S*", r"Figure~?\S*"]


def clean_tex(line):
    line = re.sub(r"(?<!\\)%.*", "", line)                      # comments
    line = re.sub(r"\\(label|ref|cite|input|includegraphics|repofile|providecommand|nolinkurl|texttt|url)"
                  r"(\[[^]]*\])?\{[^}]*\}", " ", line)
    line = line.replace("$-$", "-").replace("−", "-").replace("--", " to ")
    line = re.sub(r"(\d),(\d{3})\b", r"\1\2", line)             # 2,736 -> 2736
    for pat in SKIP:
        line = re.sub(pat, " ", line)
    return line


def cells(tables=TABLES):
    """Yield (file, row label, column, value) for every number in the tables."""
    for f in sorted(tables.glob("*.csv")):
        rows = list(csv.reader(open(f, encoding="utf-8")))
        head = rows[0]
        for r in rows[1:]:
            label = " / ".join(x for x in r[:3] if not re.fullmatch(r"[-+\d.eE ]+", x or "x"))[:60]
            for col, x in zip(head, r):
                for tok in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", x.replace("−", "-").replace("$-$", "-")):
                    yield f.name, label, col, float(tok)
    def walk(o, key):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, f"{key}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from walk(v, f"{key}[{i}]")
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            yield "headline_numbers.json", key.lstrip("."), "", float(o)
        elif isinstance(o, str):
            for tok in re.findall(r"[-+]?\d+(?:\.\d+)?", o):
                yield "headline_numbers.json", key.lstrip("."), "", float(tok)
    yield from walk(json.load(open(tables / "headline_numbers.json")), "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("-o", "--out")
    ap.add_argument("--tables", default=str(TABLES), help="folder holding the CSVs and headline_numbers.json")
    a = ap.parse_args()
    pool = list(cells(Path(a.tables)))
    # where each table sits (MANIFEST.csv): a number found only in replication-package tables is flagged, since the
    # superseded pooled analysis is still emitted there
    man = next((p for p in (Path(a.tables).parent / "MANIFEST.csv", Path(a.tables) / "MANIFEST.csv") if p.exists()), None)
    place = {Path(r["path"]).name: r["placement"] for r in csv.DictReader(open(man, encoding="utf-8"))} if man else {}
    out, missing = [], 0
    src = []
    for n, raw in enumerate(open(a.draft, encoding="utf-8"), 1):
        m = re.match(r"\s*%\s*src:\s*(.*)", raw)
        if m:
            src = [Path(x.strip().split(",")[0]).name for x in re.split(r"[,;]", m.group(1)) if x.strip().endswith((".csv", ".json"))]
            continue
        text = clean_tex(raw) if a.draft.endswith(".tex") else raw
        for tok in NUM.findall(text):
            dec = len(tok.split(".")[1]) if "." in tok else 0
            v = float(tok)
            hits = [c for c in pool if round(abs(c[3]), dec) == abs(v)]
            signed = [c for c in hits if (c[3] < 0) == (v < 0) or tok[0] not in "+-"]
            hits = signed or hits
            pref = [c for c in hits if c[0] in src]
            status = ("emitted" if pref else "elsewhere" if hits else "NOT FOUND") if src else ("emitted" if hits else "NOT FOUND")
            if not (signed or not hits):
                status += " (magnitude only)"
            if hits and place and all(place.get(c[0]) == "REPO" for c in hits):
                status += " (replication package only)"
            elif hits and place and all(place.get(c[0]) in ("REPO", "SUPPLEMENT") for c in hits):
                # found only in the numbered supplementary tables, so the sentence needs a "Table S.." pointer
                status += f" (supplement only: {', '.join(sorted({c[0] for c in hits if place.get(c[0]) == 'SUPPLEMENT'}))})"
            shown = (pref or hits)[:3]
            out.append(dict(line=n, number=tok, status=status, matches=len(hits),
                            sources=" | ".join(f"{c[0]} [{c[1]}] {c[2]}={c[3]:g}" for c in shown),
                            context=raw.strip()[:110]))
            missing += status.startswith("NOT")
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as fh:      # a text without numbers gives a header-only ledger
            w = csv.DictWriter(fh, fieldnames=["line", "number", "status", "matches", "sources", "context"])
            w.writeheader(); w.writerows(out)
    for r in out:
        if r["status"] != "emitted":
            print(f"line {r['line']:4d}  {r['number']:>8s}  {r['status']:24s} {r['sources'][:90]}")
    print(f"{len(out)} numbers; {sum(r['status'] == 'emitted' for r in out)} emitted from their named sources; "
          f"{sum(r['status'].startswith('elsewhere') for r in out)} found elsewhere; {missing} not found; "
          f"{sum(r['status'].endswith('(replication package only)') for r in out)} found only in replication-package tables; "
          f"{sum('(supplement only:' in r['status'] for r in out)} found only in supplementary tables")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
