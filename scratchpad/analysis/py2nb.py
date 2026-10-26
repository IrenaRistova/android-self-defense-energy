"""Convert a percent-format script (# %% / # %% [markdown]) into a Jupyter notebook. Usage: py2nb.py in.py out.ipynb"""
import sys

import nbformat

src = open(sys.argv[1]).read().split("\n")
cells, kind, buf = [], None, []


def flush():
    if kind is None:
        return
    text = "\n".join(buf).strip("\n")
    if kind == "markdown":
        text = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in text.split("\n"))
        cells.append(nbformat.v4.new_markdown_cell(text))
    else:
        cells.append(nbformat.v4.new_code_cell(text))


for line in src:
    if line.startswith("# %%"):
        flush()
        kind, buf = ("markdown" if "[markdown]" in line else "code"), []
    else:
        buf.append(line)
flush()
nb = nbformat.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3",
                                                                    "language": "python"}})
nbformat.write(nb, sys.argv[2])
print(f"{len(cells)} cells -> {sys.argv[2]}")
