"""Write a draw.io file from a node/edge spec, and export it to PDF and PNG.

The PDF is draw.io's own rendering: the official viewer (viewer.diagrams.net) draws the
diagram in headless Chrome, the SVG it produces is taken from the page and printed to a
PDF page cropped to the drawing. Text stays vector, fonts are embedded.

Intermediate files (view.html, render.svg, sheet.html) and the headless-Chrome profile (chrome-profile/)
go to `workdir`, by default `_render/` beside the .drawio file.
"""
import html
import json
import re
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

VIEWER = "https://viewer.diagrams.net/js/viewer-static.min.js"
CHROME = "google-chrome"


def style_str(d):
    return ";".join(f"{k}={v}" if v is not None else k for k, v in d.items()) + ";"


def mxfile(nodes, edges, name="Execution"):
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for n in nodes:
        parent = n.get("parent", "1")
        cells.append(
            f'<mxCell id="{n["id"]}" value={quoteattr(n.get("label", ""))} '
            f'style={quoteattr(style_str(n["style"]))} vertex="1" parent="{parent}">'
            f'<mxGeometry x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" as="geometry"/></mxCell>')
    for e in edges:
        attrs = f'id="{e["id"]}" value={quoteattr(e.get("label", ""))} style={quoteattr(style_str(e["style"]))} edge="1" parent="1"'
        if e.get("source"):
            attrs += f' source="{e["source"]}"'
        if e.get("target"):
            attrs += f' target="{e["target"]}"'
        geo = f'<mxGeometry x="{e.get("lx", 0)}" y="{e.get("ly", 0)}" relative="1" as="geometry">'
        if e.get("sp"):
            geo += f'<mxPoint x="{e["sp"][0]}" y="{e["sp"][1]}" as="sourcePoint"/>'
        if e.get("tp"):
            geo += f'<mxPoint x="{e["tp"][0]}" y="{e["tp"][1]}" as="targetPoint"/>'
        if e.get("points"):
            geo += '<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in e["points"]) + "</Array>"
        if e.get("offset"):
            geo += f'<mxPoint x="{e["offset"][0]}" y="{e["offset"][1]}" as="offset"/>'
        geo += "</mxGeometry>"
        cells.append(f"<mxCell {attrs}>{geo}</mxCell>")
    body = "".join(cells)
    return ('<mxfile host="Electron" agent="analysis-side generator" version="24.7.17">'
            f'<diagram id="exec" name="{escape(name)}"><mxGraphModel dx="900" dy="600" grid="0" gridSize="5" '
            'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" pageWidth="700" '
            'pageHeight="450" math="0" shadow="0"><root>' + body + "</root></mxGraphModel></diagram></mxfile>")


def _chrome(args, profile):
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def export(drawio_path, pdf_path, png_path=None, png_dpi=200, workdir=None):
    drawio_path, pdf_path = Path(drawio_path), Path(pdf_path)
    work = Path(workdir or drawio_path.parent / "_render")
    work.mkdir(parents=True, exist_ok=True)
    profile = work / "chrome-profile"
    xml = drawio_path.read_text()
    cfg = {"highlight": "#0000ff", "nav": False, "resize": False, "toolbar": "", "edit": None,
           "lightbox": False, "xml": xml}
    page = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="light"></head>'
            f'<body style="margin:0"><div class="mxgraph" data-mxgraph="{html.escape(json.dumps(cfg))}"></div>'
            f'<script src="{VIEWER}"></script></body></html>')
    (work / "view.html").write_text(page)
    r = _chrome(["--virtual-time-budget=20000", "--dump-dom", (work / "view.html").as_uri()], profile)
    m = re.search(r"<svg[^>]*>.*?</svg>", r.stdout, re.S)
    if not m:
        raise RuntimeError("viewer produced no SVG")
    svg = m.group(0)
    w = int(re.search(r"min-width: (\d+)px", svg).group(1))
    h = int(re.search(r"min-height: (\d+)px", svg).group(1))
    svg = re.sub(r'^<svg style="[^"]*"',
                 f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                 f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;color-scheme:light"', svg)
    (work / "render.svg").write_text(svg)
    sheet = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="light">'
             f'<style>@page{{size:{w}px {h}px;margin:0}}html,body{{margin:0;padding:0;background:#fff;'
             f'color-scheme:light}}</style></head><body>{svg}</body></html>')
    (work / "sheet.html").write_text(sheet)
    _chrome(["--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", (work / "sheet.html").as_uri()], profile)
    pages = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True).stdout
    n = int(re.search(r"Pages:\s+(\d+)", pages).group(1))
    if n != 1:
        raise RuntimeError(f"{pdf_path} has {n} pages")
    if png_path:
        stem = str(Path(png_path).with_suffix(""))
        subprocess.run(["pdftoppm", "-r", str(png_dpi), "-png", "-singlefile", str(pdf_path), stem], check=True)
    return w, h
