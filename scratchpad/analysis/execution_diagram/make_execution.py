"""The experiment-execution diagram, as an editable draw.io file.

Content checked against the code that ran the series: tools/sweep_runner_parallel_ykush.sh,
tools/run_final_sweep_ykush.sh, and the Android Runner configs, hooks and profiler plugins. Steps 1, 2, 7 and 8
are the batch scheduler's (the sweep runner), steps 3-6 Android Runner's; Appium and mitmproxy are started once
per batch.

Layout in draw.io units (1 unit = 1 px = 0.75 pt in the PDF). The drawing is about 690 units wide, so at
\\textwidth (about 506 pt) it prints at scale ~0.97 and the 9.5-unit labels are about 6.9-7 pt. Box heights
follow the number of text lines, every line is measured against its box (Liberation Sans = the viewer's
Helvetica), arrows get exact anchors and waypoints, and no two arrows cross: the mitmproxy, install and Appium
arrows use three vertical lanes (V0 < V1 < V2) whose spans do not overlap any other arrow.
"""
import glob
import sys
from pathlib import Path

from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from drawio_render import export, mxfile  # noqa: E402

OUT = Path(__file__).parent / "out"   # the outputs are copied to 6_results_analysis/figures/figure_execution.*
OUT.mkdir(exist_ok=True)

F, FS, LH = 10, 9.5, 12.5     # body font, smallest font, line height (units)
_REG = glob.glob("/usr/share/fonts/**/LiberationSans-Regular.ttf", recursive=True)[0]
_BLD = glob.glob("/usr/share/fonts/**/LiberationSans-Bold.ttf", recursive=True)[0]


def text_w(s, size, bold=False):
    return ImageFont.truetype(_BLD if bold else _REG, int(size * 10)).getlength(s) / 10


BASE = {"whiteSpace": "wrap", "html": "1", "fontFamily": "Helvetica"}


def st(**kw):
    d = dict(BASE)
    d.update(kw)
    return d


CONTAINER = st(rounded="0", fillColor="none", strokeColor="#000066", verticalAlign="top", fontStyle="1",
               fontSize="12", spacingTop="1")
RUNNER = st(rounded="0", fillColor="#d5e8d4", strokeColor="#82b366", dashed="1", verticalAlign="top",
            align="left", spacingLeft="6", fontStyle="1", fontSize="10.5")
LOOP = st(rounded="0", fillColor="#d5e8d4", strokeColor="#82b366", dashed="1", dashPattern="4 3",
          verticalAlign="top", align="left", spacingLeft="10", fontStyle="1", fontSize=str(F))
AR = st(rounded="0", fillColor="#c3e0bd", strokeColor="#6a9a5b", dashed="1", dashPattern="2 2",
        verticalAlign="top", align="left", spacingLeft="8", fontStyle="1", fontSize=str(F))
STEP = st(rounded="1", arcSize="12", fillColor="#ffffff", strokeColor="#000000", fontSize=str(F),
          spacingLeft="8", spacingRight="2")
BADGE = st(rounded="0", fillColor="#ffff88", strokeColor="#d6b656", fontSize=str(F))
TOOL = st(rounded="0", fillColor="#f8cecc", strokeColor="#b85450", dashed="1", fontSize=str(FS))
TARGET = st(rounded="0", fillColor="#e1d5e7", strokeColor="#9673a6", fontSize=str(F))
METRICS = st(rounded="0", fillColor="#ffe6cc", strokeColor="#d79b00", dashed="1", verticalAlign="top",
             fontStyle="1", fontSize="10.5")
METRIC = st(rounded="0", fillColor="#ffffff", strokeColor="#000000", dashed="1", dashPattern="6 3",
            fontSize=str(FS))
BOARD = st(rounded="1", arcSize="10", fillColor="#f5f5f5", strokeColor="#666666", fontColor="#333333",
           fontSize=str(F))
BAND = st(rounded="0", fillColor="#dae8fc", strokeColor="none")
VLABEL = st(rounded="0", fillColor="none", strokeColor="none", horizontal="0", fontSize=str(FS),
            fontColor="#1f4e79", fontStyle="2")
NOTE = st(rounded="0", fillColor="none", strokeColor="none", fontSize=str(FS), align="center")
ALABEL = st(rounded="0", fillColor="none", strokeColor="none", fontSize=str(FS), align="center",
            fontStyle="3")                # arrow labels: bold italic
FOLDER = st(shape="folder", tabWidth="11", tabHeight="4", tabPosition="left", fillColor="#3a3a3a",
            strokeColor="none", labelPosition="center", verticalLabelPosition="bottom", align="center",
            verticalAlign="top", fontSize=str(FS), spacingTop="-3")

nodes, edges, box = [], [], {}


def node(id_, x, y, w, h, label, style, check=True):
    """Add a box; check that every line of its label fits (width minus padding)."""
    if check and label:
        size = float(style.get("fontSize", F))
        bold = style.get("fontStyle") in ("1", "3")
        pad = float(style.get("spacingLeft", 2)) + float(style.get("spacingRight", 2)) + 4
        avail = (h if style.get("horizontal") == "0" else w) - pad
        for line in label.replace("<i>", "").replace("</i>", "").replace("<b>", "").replace("</b>", "").split("<br>"):
            assert text_w(line, size, bold) <= avail, f"{id_}: '{line}' is {text_w(line, size, bold):.0f} > {avail:.0f}"
    nodes.append(dict(id=id_, x=x, y=y, w=w, h=h, label=label, style=style))
    box[id_] = (x, y, w, h)
    return y + h


def lines(*ls):
    return "<br>".join(ls), len(ls)


def h_of(n):
    return 5 + LH * n


def anchor(id_, side, frac=None, y=None):
    """Point on a box's left or right side: (fx, fy, px, py), with the fractions draw.io needs."""
    bx, by, bw, bh = box[id_]
    fx = 0 if side == "left" else 1
    py = by + bh * frac if y is None else y
    return fx, (py - by) / bh, bx + bw * fx, py


def link(id_, src, tgt, color, exit_, entry, points=(), both=False, dashed=False):
    s = {"edgeStyle": "orthogonalEdgeStyle", "rounded": "0", "html": "1", "endArrow": "block",
         "endFill": "1", "endSize": "5", "strokeWidth": "1.3", "strokeColor": color, "jumpStyle": "none",
         "exitX": f"{exit_[0]:.4f}", "exitY": f"{exit_[1]:.4f}", "entryX": f"{entry[0]:.4f}",
         "entryY": f"{entry[1]:.4f}", "exitPerimeter": "0", "entryPerimeter": "0"}
    if both:
        s.update({"startArrow": "block", "startFill": "1", "startSize": "5"})
    if dashed:
        s["dashed"] = "1"
    edges.append(dict(id=id_, source=src, target=tgt, label="", style=s, points=list(points)))


# ---- geometry -----------------------------------------------------------------------
HOST_W, GAP, PHONE_W = 332, 108, 216
PX = HOST_W + GAP                         # phone container x
SX, SW = 48, 192                          # step boxes
TX, TW = 254, 70                          # tool column (mitmproxy, Appium)
V0, V1, V2 = HOST_W + 8, HOST_W + 20, HOST_W + 32   # vertical lanes in the middle column
GAP_Y = 4

# ---- host side ---------------------------------------------------------------------
lab, n = lines("Start a batch: Appium servers, proxy;", "a shuffled queue per phone")
y = node("s1", SX, 44, SW, h_of(n), lab, STEP)
node("mitm", TX, 44, TW, h_of(2), "mitmproxy<br>(replay)", TOOL)
LOOP_Y = y + 10
lab, n = lines("Prepare: top up if low; switch USB off", "(phone on battery) and check it; settle;", "load check; thermal gate; start the log")
y = node("s2", SX, LOOP_Y + 19, SW, h_of(n), lab, STEP) + GAP_Y
AR_Y = y
y = AR_Y + 20
for sid, (lab, n) in [
        ("s3", lines("Device state, fresh install, compilation", "request; launch (timed)")),
        ("s4", lines("Start profilers: t = 0")),
        ("s5", lines("Appium session set-up (passive phase),", "then the scenarios (active phase)")),
        ("s6", lines("Stop profilers; switch USB on;", "collect the results; uninstall"))]:
    y = node(sid, SX, y, SW, h_of(n), lab, STEP) + GAP_Y
AR_H = y - 2 - AR_Y
y = AR_Y + AR_H + GAP_Y
lab, n = lines("Switch USB off again; stop the log;", "mark the measurement valid or failed")
y = node("s7", SX, y, SW, h_of(n), lab, STEP) + GAP_Y
LOOP_H = y - LOOP_Y
lab, n = lines("After the batch: build the run table")
y = node("s8", SX, LOOP_Y + LOOP_H + GAP_Y, SW, h_of(n), lab, STEP)
node("f_run", TX + 4, box["s8"][1] + 3, 20, 15, "run table", FOLDER | {"labelPosition": "right", "verticalLabelPosition": "middle", "align": "left", "verticalAlign": "middle", "spacingTop": "0", "spacingLeft": "4", "whiteSpace": "nowrap"}, check=False)
for i in range(1, 9):
    bx, by, bw, bh = box[f"s{i}"]
    node(f"b{i}", SX - 8, by + bh / 2 - 7, 14, 14, str(i), BADGE)
s5 = box["s5"]
node("appium", TX, s5[1] - 4, TW, s5[3] + 8, "Appium<br>server", TOOL)
HOST_BOTTOM = y + 16

# ---- phone side ----------------------------------------------------------------------
AX, AW = PX + 12, PHONE_W - 24
node("app", AX, 24, AW, 48, "Application under test<br>(control or protected build)", TARGET)
node("uia2", AX + 30, 82, AW - 60, 22, "UiAutomator2 server", TOOL)
ITEMS = [
    lines("Battery companion app, every 100 ms:", "current, status, charge counter;", "voltage, temperature: once per run"),
    lines("dumpsys, polled every 1 s (configured):", "device CPU load, app memory (PSS),", "frame times"),
    lines("System log, streamed to the laptop:", "launch time, crashes, SELinux denials,", "GC, Wi-Fi traffic,", "power rails (Pixel 6 and 9)"),
]
# data arrow into step 6, below the Appium lane; the recorded-data box reaches down past it
s6 = box["s6"]
Y_D = s6[1] + 9
MH = 20 + sum(h_of(n) + 4 for _, n in ITEMS) + 2
MY = max(114, Y_D + 12 - MH)
y = MY + 22
for i, (lab, n) in enumerate(ITEMS, start=1):
    y = node(f"m{i}", AX + 8, y, AW - 16, h_of(n), lab, METRIC) + 4
assert MY + 10 < Y_D < MY + MH - 4, (Y_D, MY, MH)

# ---- middle: the switch board, below the data arrow, level with steps 6-7 -------------
YK_W, YK_H = 78, 44
yk_y = max(Y_D + 10, s6[1] + s6[3] - 22)
node("ykush", HOST_W + (GAP - YK_W) / 2, yk_y, YK_W, YK_H, "YKUSH 3<br>USB switch", BOARD)
bat_y = max(MY + MH + 8, yk_y - 4)
node("battery", AX, bat_y, AW, YK_H + 8, "Battery and fuel gauge<br>(discharging while USB is off)", BOARD)
yb = box["ykush"]
node("usb_note", yb[0] - 26, yb[1] + yb[3] + 2, yb[2] + 52, 26, "USB power and data<br>off / on (steps 2, 6, 7)", NOTE)

# ---- containers (drawn first, behind everything) --------------------------------------
RUNNER_Y = 22
TOTAL_H = max(HOST_BOTTOM, box["battery"][1] + box["battery"][3] + 10, yb[1] + yb[3] + 32)
RUNNER_H = TOTAL_H - 8 - RUNNER_Y
s4b, s5b = box["s4"], box["s5"]
band_y, band_h = s4b[1] - 4, s5b[1] + s5b[3] - s4b[1] + 8
front = [
    dict(id="host", x=0, y=0, w=HOST_W, h=TOTAL_H, label="Host laptop", style=CONTAINER),
    dict(id="runner", x=8, y=RUNNER_Y, w=HOST_W - 16, h=RUNNER_H,
         label="Measurement harness (batch scheduler + Android Runner)", style=RUNNER),
    dict(id="loop", x=16, y=LOOP_Y, w=HOST_W - 32, h=LOOP_H, label="Loop: for each measurement, on each phone",
         style=LOOP),
    dict(id="ar", x=24, y=AR_Y, w=SX + SW + 8 - 24, h=AR_H, label="Android Runner: one run per measurement",
         style=AR),
    dict(id="band", x=28, y=band_y, w=SX + SW + 4 - 28, h=band_h, label="", style=BAND),
    dict(id="bandlabel", x=28, y=band_y, w=12, h=band_h, label="measured", style=VLABEL),
    dict(id="phone", x=PX, y=0, w=PHONE_W, h=TOTAL_H, label="Phone (Pixel 3, 6, 9)", style=CONTAINER),
    dict(id="metrics", x=AX, y=MY, w=AW, h=MH, label="Recorded during the run", style=METRICS),
]
for f in front:
    box[f["id"]] = (f["x"], f["y"], f["w"], f["h"])
    if f["label"] and f["style"].get("horizontal") != "0":
        sz = float(f["style"]["fontSize"]); pad = float(f["style"].get("spacingLeft", 2)) + 6
        assert text_w(f["label"], sz, f["style"].get("fontStyle") == "1") <= f["w"] - pad, f["id"]
nodes = front + nodes

# ---- arrows: no two cross -------------------------------------------------------------
ADB, APPIUM, USB, DATA, NET = "#000066", "#b85450", "#4d4d4d", "#b46504", "#7f7f7f"
ay = box["app"][1]
Y_M, Y_I = ay + 12, ay + 34                         # entries into the app box, top to bottom

ex = anchor("mitm", "right", 0.5); en = anchor("app", "left", y=Y_M)
link("e_mitm", "mitm", "app", NET, ex[:2], en[:2], [(V0, ex[3]), (V0, en[3])], both=True, dashed=True)
node("l_mitm", V0 + 2, 1, PX - V0 - 4, 26, "network traffic<br>(one application)", ALABEL)

ex = anchor("s3", "right", 0.5); en = anchor("app", "left", y=Y_I)
link("e_install", "s3", "app", ADB, ex[:2], en[:2], [(V1, ex[3]), (V1, en[3])])
node("l_install", SX + SW + 2, ex[3] - 28, HOST_W - 2 - (SX + SW + 2), 26, "install, launch<br>(ADB over Wi-Fi)", ALABEL)

ex = anchor("appium", "right", 0.5); en = anchor("uia2", "left", 0.5)
link("e_appium", "appium", "uia2", APPIUM, ex[:2], en[:2], [(V2, ex[3]), (V2, en[3])])
node("l_appium", V2 + 2, (en[3] + ex[3]) / 2 - 13, PX - V2 - 4, 26, "Appium<br>session", ALABEL)
link("e_s5", "s5", "appium", APPIUM, anchor("s5", "right", 0.5)[:2], anchor("appium", "left", 0.5)[:2])
u = box["uia2"]
edges.append(dict(id="e_drive", source="uia2", target="app", label="", points=[],
                  style={"edgeStyle": "orthogonalEdgeStyle", "rounded": "0", "html": "1", "endArrow": "block",
                         "endFill": "1", "endSize": "5", "strokeWidth": "1.3", "strokeColor": APPIUM,
                         "exitX": "0.5", "exitY": "0", "entryX": f"{(u[0] + u[2] / 2 - AX) / AW:.4f}",
                         "entryY": "1", "exitPerimeter": "0", "entryPerimeter": "0"}))

ex = anchor("metrics", "left", y=Y_D); en = anchor("s6", "right", y=Y_D)
link("e_data", "metrics", "s6", DATA, ex[:2], en[:2])
node("l_data", HOST_W + 2, Y_D - 15, GAP - 4, 14, "samples, system log", ALABEL)

ex = anchor("s6", "right", y=s6[1] + s6[3] - 7); en = anchor("ykush", "left", y=yb[1] + 10)
link("e_restore", "s6", "ykush", USB, ex[:2], en[:2],
     [(HOST_W - 10, ex[3]), (HOST_W - 10, en[3])] if abs(ex[3] - en[3]) > 1 else [])
ex = anchor("s7", "right", 0.5); en = anchor("ykush", "left", y=yb[1] + yb[3] - 10)
link("e_cut", "s7", "ykush", USB, ex[:2], en[:2],
     [(HOST_W - 4, ex[3]), (HOST_W - 4, en[3])] if abs(ex[3] - en[3]) > 1 else [])
ex = anchor("ykush", "right", 0.5); en = anchor("battery", "left", y=ex[3])
link("e_usb", "ykush", "battery", USB, ex[:2], en[:2])

xml = mxfile(nodes, edges)
(OUT / "figure_execution.drawio").write_text(xml)
w, h = export(OUT / "figure_execution.drawio", OUT / "figure_execution.pdf", OUT / "figure_execution.png",
              png_dpi=220, workdir=OUT / "_render")
print("drawing", w, "x", h, "px =", round(w * 0.75), "x", round(h * 0.75), "pt")
