"""Ground-truth labelling of post-interruption (post-top-up / post-pause) cells.

Emits scratchpad/analysis/topup_ground_truth.csv with, per run-table cell:
  event_kind  charge | pause | start | (blank): what opened the current window
  k           cells since that event (0 = first cell after it)
  dt_s        seconds since that event (approx. workload-start to workload-start)
  dsoc        battery_level_pre_pct(i) - battery_level_post_pct(i-1)
  off_s       off-workload seconds immediately before this cell
  log_topup / log_outcome  what the sweep stream log says happened in this slot

Label definition: SoC jump >= 15 pp (any threshold in [12,20] gives identical
labels, because the histogram is empty there) OR off-workload >= 1800 s (plateau:
1800-7200 s identical) OR first cell of a phone-batch stream.
Uses device-state and timing columns only: no energy column is read.
The package holds the output as 6_results_data/inputs/post_charge_k_window.csv.
"""
import sys, os, re, glob, csv, datetime as dt, collections, json, statistics as st, math, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT="/home/irena/Documents/Master Experiment"
LOG=os.path.join(ROOT,"tools/sweep_logs")

START=re.compile(r'^\[(\d\d):(\d\d):(\d\d)\] START (\w+) (\S+)/(\S+) rep(\d+)')
DONE =re.compile(r'^\[(\d\d):(\d\d):(\d\d)\] DONE  (\w+) (\S+)/(\S+) rep(\d+) (\S+) wall=(\d+)s')
PRE  =re.compile(r'^\s+pre-cell: level=(\S+) status=(\S+)')
TOPUP=re.compile(r'^\s+top-up: level=(\d+) < (\d+) -> charging')
SKIP =re.compile(r'^\s+top-up SKIPPED \(level=(\d+)')
BAND =re.compile(r'^\s+band reached: level=(\d+) >= (\d+) after (\d+)s')
STALL=re.compile(r'^\s+STALLED at level=(\S+)')
REFUS=re.compile(r'^\s+REFUSED: VBUS is present')
CEIL =re.compile(r'^\s+HARD CEILING: level=(\d+)')
WAIT =re.compile(r'^\s+waited (\d+)s for the charge lock')
THERM=re.compile(r'^\s+THERMAL pre-cell: (\{.*\})')
BACKOFF=re.compile(r'below band .*backoff|charge-refusal backoff', re.I)

def parse(path):
    base=os.path.basename(path)
    m=re.match(r'parallel_(\d{8})_(\d{6})_(pixel\d)\.log$', base)
    if not m: return []
    d0=dt.datetime.strptime(m.group(1),"%Y%m%d")
    phone=m.group(3)
    out=[]; cur=None; prev_h=None; daycarry=0
    for line in open(path, errors="replace"):
        ms=START.match(line)
        if ms:
            h,mi,s=int(ms.group(1)),int(ms.group(2)),int(ms.group(3))
            if prev_h is not None and h<prev_h-6: daycarry+=1
            prev_h=h
            cur=dict(phone=phone, app=ms.group(5), variant=ms.group(6), rep=int(ms.group(7)),
                     t_start_local=d0+dt.timedelta(days=daycarry,hours=h,minutes=mi,seconds=s),
                     logfile=base, topup=0, outcome=None, dur=None, wait=0,
                     pre_lvl=None, therm=None, wall=None, done=None, result=None)
            out.append(cur); continue
        if cur is None: continue
        md=DONE.match(line)
        if md:
            h,mi,s=int(md.group(1)),int(md.group(2)),int(md.group(3))
            dc=daycarry+(1 if (h<cur.t_start_local.hour-6 if False else False) else 0)
            cur["result"]=md.group(8); cur["wall"]=int(md.group(9))
            cur["done"]=cur["t_start_local"]+dt.timedelta(seconds=int(md.group(9)))
            cur=None; continue
        m2=PRE.match(line)
        if m2:
            try: cur["pre_lvl"]=int(m2.group(1))
            except: pass
            cur["pre_status"]=m2.group(2); continue
        if TOPUP.match(line): cur["topup"]=1; continue
        if SKIP.match(line): cur["outcome"]="skip"; continue
        if CEIL.match(line): cur["outcome"]="ceiling"; continue
        m3=BAND.match(line)
        if m3: cur["outcome"]="band"; cur["dur"]=int(m3.group(3)); continue
        if STALL.match(line): cur["outcome"]="stall"; continue
        if REFUS.match(line): cur["outcome"]="refused"; continue
        m4=WAIT.match(line)
        if m4: cur["wait"]=int(m4.group(1)); continue
        m5=THERM.match(line)
        if m5:
            try: cur["therm"]=json.loads(m5.group(1))
            except: pass
            continue
    return out


BATCH={"pilot":"6_results_data/run_tables/run_table_extended_final_ykush_2026-08-04.csv",
 "series1":"6_results_data/run_tables/run_table_extended_final_ykush_batch2_2026-08-10.csv",
 "series2":"6_results_data/run_tables/run_table_extended_final_ykush_series2_2026-08-14.csv",
 "series3":"6_results_data/run_tables/run_table_extended_final_ykush_series3_2026-08-17.csv"}
NUM=("workload_joules","pre_workload_joules","workload_avg_watts","battery_temp_avg_c",
     "battery_temp_max_c","battery_level_pre_pct","battery_level_post_pct","voltage_pre_v",
     "current_now_abs_avg_ma","cpu_avg_pct","duration_workload_s","wall_s",
     "thermal_skin_pre_c","thermal_cpu_pre_c","total_cell_joules","duration_pre_workload_s")
def f(r,k):
    try: return float(r.get(k) or "nan")
    except: return float("nan")
def valid(r):
    if r.get("scientifically_valid")!="True": return False
    if (r.get("negative_control") or "").strip() not in ("","None","False"): return False
    try:
        c=int(r.get("scenarios_completed") or -1); p=int(r.get("scenarios_planned") or -1)
        if c>=0 and p>0 and c!=p: return False
    except: pass
    return f(r,"workload_joules")>0

def load():
    rt=[]
    for b,p in BATCH.items():
        for r in csv.DictReader(open(os.path.join(ROOT,p))):
            d={k:f(r,k) for k in NUM}
            d.update(batch=b,app=r["app"],phone=r["phone"],variant=r["variant"],rep=int(r["rep"]),
                     ts=dt.datetime.strptime(r["cell_start_ts_utc"],"%Y-%m-%dT%H:%M:%SZ"),
                     tend=dt.datetime.strptime(r["cell_end_ts_utc"],"%Y-%m-%dT%H:%M:%SZ") if (r.get("cell_end_ts_utc") or "").strip() else None,
                     result=r.get("result"), valid=valid(r), raw=r)
            rt.append(d)
    return rt

def logslots():
    ev=[]
    for p in sorted(glob.glob(os.path.join(LOG,"parallel_*_pixel?.log"))):
        b=os.path.basename(p); stamp=b.split("_")[1]
        if not ("20260804"<=stamp<="20260819"): continue
        ev+=parse(p)
    for e in ev: e["utc"]=e["t_start_local"]-dt.timedelta(hours=2)
    return ev

def join(rt,ev):
    idx=collections.defaultdict(list)
    for e in ev: idx[(e["phone"],e["app"],e["variant"],e["rep"])].append(e)
    nm=0
    for r in rt:
        best=None
        for e in idx.get((r["phone"],r["app"],r["variant"],r["rep"]),[]):
            d=abs((e["utc"]-r["ts"]).total_seconds())
            if d<120 and (best is None or d<best[0]): best=(d,e)
        if best:
            e=best[1]; e["matched"]=True
            r["log"]=e
            r["gt_topup"]=e["topup"]; r["gt_outcome"]=e["outcome"]
            r["gt_dur"]=e["dur"]; r["gt_wait"]=e["wait"]; r["gt_prelvl"]=e["pre_lvl"]
            nm+=1
        else:
            r["log"]=None; r["gt_topup"]=None; r["gt_outcome"]=None
            r["gt_dur"]=None; r["gt_wait"]=0; r["gt_prelvl"]=None
    return nm

SETTLE=60.0
def build(soc_thr=15.0, pause_thr=1200.0):
    rt=load(); ev=logslots(); join(rt,ev)
    seq=collections.defaultdict(list)
    for r in rt: seq[(r["batch"],r["phone"])].append(r)
    for key,rs in seq.items():
        rs.sort(key=lambda r:r["ts"])
        for i,r in enumerate(rs):
            try: ww=float(r["raw"].get("duration_whole_window_s") or "nan")
            except: ww=float("nan")
            ovh=r["wall_s"]-ww
            gap=(r["ts"]-rs[i-1]["tend"]).total_seconds() if i>0 and rs[i-1]["tend"] else 0.0
            pw=r["duration_pre_workload_s"]
            r["gap"]=gap; r["ovh"]=ovh
            r["off"]=(gap if gap==gap else 0)+max(0.0,(ovh-SETTLE) if ovh==ovh else 0.0)+(pw if pw==pw else 0.0)+(SETTLE if i>0 else 0.0)
            # approximate workload start
            r["ws"]=r["ts"]+dt.timedelta(seconds=(max(0.0,(ovh-SETTLE) if ovh==ovh else 0.0)+(pw if pw==pw else 0.0)))
            r["dsoc"]=(r["battery_level_pre_pct"]-rs[i-1]["battery_level_post_pct"]) if i>0 else float("nan")
        k=0; ev_kind=None; t0=None
        for i,r in enumerate(rs):
            charge = (i>0 and r["dsoc"]==r["dsoc"] and r["dsoc"]>=soc_thr)
            pause  = (i>0 and not charge and r["off"]>=pause_thr)
            if i==0 or charge or pause:
                k=0; ev_kind=("start" if i==0 else ("charge" if charge else "pause")); t0=r["ws"]
            else:
                k+=1
            r["k"]=k; r["ev"]=ev_kind
            r["dt"]=(r["ws"]-t0).total_seconds()
    return rt,seq

if __name__ == "__main__":
    import csv as _csv
    rt, _ = build(15.0, 1800.0)
    out = os.path.join(ROOT, "scratchpad/analysis/topup_ground_truth.csv")
    ev = logslots(); rt2 = load(); join(rt2, ev)
    lg = {(r["phone"], r["ts"]): r for r in rt2}
    with open(out, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["batch","phone","app","variant","rep","cell_start_ts_utc","valid",
                    "event_kind","k","dt_s","dsoc","off_s","gap_s",
                    "log_topup","log_outcome","log_topup_dur_s",
                    "battery_temp_avg_c","voltage_pre_v","battery_level_pre_pct"])
        for r in sorted(rt, key=lambda r: (r["batch"], r["phone"], r["ts"])):
            q = lg.get((r["phone"], r["ts"]))
            w.writerow([r["batch"], r["phone"], r["app"], r["variant"], r["rep"],
                        r["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), r["valid"],
                        r["ev"], r["k"], round(r["dt"], 1), r["dsoc"], round(r["off"], 1),
                        round(r["gap"], 1),
                        (q or {}).get("gt_topup"), (q or {}).get("gt_outcome"),
                        (q or {}).get("gt_dur"),
                        r["battery_temp_avg_c"], r["voltage_pre_v"], r["battery_level_pre_pct"]])
    print("wrote", out, len(rt), "rows")
