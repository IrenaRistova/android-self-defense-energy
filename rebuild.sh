#!/usr/bin/env bash
# Rebuild every table, figure and number of the thesis from the data in this repository.
#
# Usage, from the repository root, with the packages of 6_results_analysis/requirements.txt installed:
#     bash rebuild.sh
# PYTHON and JUPYTER can point to a specific interpreter, e.g. PYTHON=.venv/bin/python JUPYTER=.venv/bin/jupyter.
#
# It converts scratchpad/analysis/per_device_notebook.py into Per_Device_Analysis.ipynb, runs the five notebooks in
# order (about two minutes) and rewrites 6_results_analysis/tables/, figures/ and MANIFEST.csv. Afterwards
# `git status` shows the notebooks (execution metadata) and the figure PDFs (creation date) as changed; every table
# and CSV is byte-identical to the committed one.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolve the interpreters before changing folder, so relative paths such as .venv/bin/python work.
abspath() { case "$1" in /*) printf '%s' "$1" ;; */*) printf '%s/%s' "$PWD" "$1" ;; *) command -v "$1" ;; esac; }
PY="$(abspath "${PYTHON:-python3}")"
JUPYTER="$(abspath "${JUPYTER:-jupyter}")"
cd "$ROOT/6_results_analysis/notebooks"
"$PY" "$ROOT/scratchpad/analysis/py2nb.py" "$ROOT/scratchpad/analysis/per_device_notebook.py" Per_Device_Analysis.ipynb
# The notebooks append to these two files, so they start empty.
rm -f ../MANIFEST.csv ../tables/headline_numbers.json
for nb in Descriptive_Statistics Headline_Effects Phase_Analysis Mechanism_Analysis Per_Device_Analysis; do
  start=$(date +%s)
  if ! "$JUPYTER" nbconvert --to notebook --execute --inplace "$nb.ipynb" > "$ROOT/$nb.nbconvert.log" 2>&1; then
    echo "$nb FAILED, last lines of $nb.nbconvert.log:"; tail -30 "$ROOT/$nb.nbconvert.log"; exit 1
  fi
  echo "$nb OK ($(( $(date +%s) - start )) s)"
done
"$PY" ../write_tables_readme.py
echo "Done. Optional: TECTONIC=/path/to/tectonic $PY 6_results_analysis/make_supplement.py rebuilds the supplement PDF."
