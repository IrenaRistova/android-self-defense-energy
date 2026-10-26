#!/usr/bin/env python3
"""Is Allatori's branch-count change real app-code obfuscation, or just the
decryptor methods it injects?

String encryption injects a static String->String decryptor whose body is an XOR
loop, and a loop is branches. So a rise in conditional branches across the app
package is NOT by itself evidence that Allatori restructured app code: the
decryptors alone would produce one.

This separates them. A decryptor is identified structurally, not by name: a
static method taking one String and returning String whose body contains the
xor + int-to-char + aput-char triple that Allatori's decryptor always emits.
Same detection rule as scratchpad/apk_obfuscation_fingerprint.py.

Usage:  python3 scratchpad/analysis/allatori_cf_exclude_decryptors.py --apps a,b
"""
from __future__ import annotations
import argparse, glob, json, pathlib, re, subprocess, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CFG_DIR = REPO / "android-runner" / "examples" / "batterymanager"
BRANCH = re.compile(r"^(goto|if-|packed-switch|sparse-switch)")

def dexdump() -> str:
    hits = sorted(glob.glob(str(pathlib.Path.home() / "Android/Sdk/build-tools/*/dexdump")),
                  reverse=True)
    if not hits:
        sys.exit("dexdump not found")
    return hits[0]

def config_for(app: str, variant: str):
    for phone in ("pixel6", "pixel9", "pixel3"):
        p = CFG_DIR / f"{app}_{variant}_{phone}_wifi_ykush.json"
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
    return None

def apk_for(app: str, variant: str):
    cfg = config_for(app, variant)
    if not cfg:
        return None, None
    for p in cfg.get("paths") or []:
        q = pathlib.Path(p)
        if q.suffix == ".apk" and q.exists():
            return q, cfg.get("application_id")
    return None, cfg.get("application_id")

def class_prefix(pkg: str) -> str:
    for suf in (".debug", ".release"):
        if pkg.endswith(suf):
            pkg = pkg[: -len(suf)]
    return "L" + pkg.replace(".", "/")

def scan(dd: str, apk: pathlib.Path, pkg: str) -> dict:
    prefix = class_prefix(pkg)
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["unzip", "-qo", str(apk), "*.dex", "-d", td],
                       check=False, capture_output=True)
        dexes = sorted(glob.glob(f"{td}/*.dex"))
        if not dexes:
            return {}
        out = subprocess.run([dd, "-d", *dexes], capture_output=True, check=False)
    text = out.stdout.decode("utf-8", errors="replace")

    tot = {"instructions": 0, "branches": 0, "goto": 0, "if": 0}
    dec = {"instructions": 0, "branches": 0, "goto": 0, "if": 0}
    n_dec = 0
    # Decrypt CALL SITES, under one stated definition so the number is
    # reproducible: an invoke-static, inside the app package, whose target is
    # also in the app package and whose descriptor is (String)String.
    n_calls = 0
    RX_CALL = re.compile(
        r"invoke-static[^,]*, (L[^;]+);\.[^:]+:\(Ljava/lang/String;\)Ljava/lang/String;")
    in_app = False
    ops: list[str] = []
    sig = ""

    def flush():
        nonlocal ops, sig, n_dec
        if not ops or not in_app:
            ops = []
            return
        body = set(ops)
        is_dec = (sig.endswith("Ljava/lang/String;")
                  and "Ljava/lang/String;)" in sig
                  and any(o.startswith("xor-int") for o in body)
                  and "int-to-char" in body
                  and "aput-char" in body)
        b = sum(1 for o in ops if BRANCH.match(o))
        g = sum(1 for o in ops if o.startswith("goto"))
        i = sum(1 for o in ops if o.startswith("if-"))
        tot["instructions"] += len(ops); tot["branches"] += b
        tot["goto"] += g; tot["if"] += i
        if is_dec:
            n_dec += 1
            dec["instructions"] += len(ops); dec["branches"] += b
            dec["goto"] += g; dec["if"] += i
        ops = []

    for line in text.splitlines():
        m = re.match(r"\s*Class descriptor\s*:\s*'([^']+)'", line)
        if m:
            flush()
            in_app = m.group(1).startswith(prefix)
            continue
        m = re.match(r"\s*type\s*:\s*'([^']+)'", line)
        if m:
            sig = m.group(1)
            continue
        m = re.match(r"\s*name\s*:\s*'([^']+)'", line)
        if m:
            flush()
            continue
        m = re.search(r"\|[0-9a-f]{4}: ([a-z0-9/-]+)", line)
        if m:
            ops.append(m.group(1))
            if in_app and m.group(1) == "invoke-static":
                c = RX_CALL.search(line)
                if c and c.group(1).startswith(prefix):
                    n_calls += 1
    flush()
    return {"total": tot, "decryptors": dec, "n_decryptors": n_dec,
            "n_call_sites": n_calls}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="poetskingdom,calculator")
    a = ap.parse_args()
    dd = dexdump()
    print("App-package branch counts, with Allatori's injected string decryptors")
    print("separated out. 'app code' is everything else -- the part where a")
    print("control-flow transformation would have to show up.\n")
    for app in a.apps.split(","):
        b_apk, pkg = apk_for(app, "baseline_debug")
        o_apk, _ = apk_for(app, "allatori_debug")
        if not (b_apk and o_apk and pkg):
            print(f"{app}: SKIP")
            continue
        B, O = scan(dd, b_apk, pkg), scan(dd, o_apk, pkg)
        if not (B and O):
            print(f"{app}: SKIP (no dex)")
            continue
        print(f"{app}  decryptor ROUTINES {B['n_decryptors']} -> {O['n_decryptors']}   "
              f"decrypt CALL SITES {B['n_call_sites']} -> {O['n_call_sites']}")
        print(f"    {'':<12}{'instrs':>10}{'branches':>10}{'goto':>8}{'if-*':>8}")
        for tag, S in (("baseline", B), ("allatori", O)):
            t, d = S["total"], S["decryptors"]
            code = {k: t[k] - d[k] for k in t}
            print(f"    {tag+' all':<12}{t['instructions']:>10}{t['branches']:>10}"
                  f"{t['goto']:>8}{t['if']:>8}")
            print(f"    {tag+' code':<12}{code['instructions']:>10}{code['branches']:>10}"
                  f"{code['goto']:>8}{code['if']:>8}")
        bc = {k: B["total"][k] - B["decryptors"][k] for k in B["total"]}
        oc = {k: O["total"][k] - O["decryptors"][k] for k in O["total"]}
        print(f"    {'APP CODE Δ':<12}", end="")
        for k in ("instructions", "branches", "goto", "if"):
            pc = 100.0 * (oc[k] - bc[k]) / bc[k] if bc[k] else float("nan")
            print(f"{pc:>+9.2f}%" if k != "instructions" else f"{pc:>+9.2f}%", end="")
        print("\n")

if __name__ == "__main__":
    main()
