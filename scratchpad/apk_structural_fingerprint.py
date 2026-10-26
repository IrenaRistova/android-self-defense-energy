#!/usr/bin/env python3
"""Structural APK fingerprint: verified per-variant detector.

Built 2026-07-31. It reads ARTIFACT structure, one row per (app, variant), and
complements a logcat-based fingerprinter that reads RUNTIME behaviour, one row
per CELL.

WHY STRUCTURAL RATHER THAN TEXTUAL:
Twice on 2026-07-31 a pattern library written blind silently matched nothing
(the ART GC tag is the truncated process name, not "art"; the Bangcle token
list contained "Aheaditec" which is Talsec's vendor name and produced a false
positive on freeRasp cells). Structural detection reads the artifact itself
and cannot silently no-op: if assets/classes0.jar is present, Bangcle packed
this APK; there is nothing to guess.

VERIFIED SIGNATURES (checked against all 11 cohort apps, 2026-07-31):

  bangcle_debug
    - classes.dex collapses to a ~1.1 MB stub regardless of original size
      (baseline DEX ranges 10.5 MB .. 78.4 MB; stub stays ~1.1-3.0 MB)
    - encrypted payload at assets/classes*.jar (4.1 MB .. 22.2 MB)
    - native: lib/arm64-v8a/libSecShell.so, lib/arm64-v8a/libSecShell-x86.so,
      assets/libSecShell32, lib/arm64-v8a/libplaceholder.so
    -> dex_shrink_ratio is the single strongest discriminator.

  freerasp_debug
    - Talsec classes present in DEX (com/aheaditec/talsec/...)
    - source-integrated, so DEX size is ~baseline + Talsec, NOT collapsed

  allatori_debug
    - compile-time only: renamed classes, encrypted string constants.
      No added runtime library. DEX size similar to baseline.
      Detected via short-class-name density, not by any added file.

  baseline_debug
    - the null state. Every detector above must read FALSE here or it is
      not a discriminator.

Usage:
    python3 scratchpad/apk_structural_fingerprint.py
    python3 scratchpad/apk_structural_fingerprint.py --app calculator
    python3 scratchpad/apk_structural_fingerprint.py --json
"""
import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CFG_DIR = REPO / "android-runner" / "examples" / "batterymanager"

APPS = [
    "calculator", "tipuous", "poetskingdom", "linkhub", "medtimer",
    "androidskk", "itsok", "keep_recipe", "another_notes", "documenter",
    "pdfviewer",
]
VARIANTS = ["baseline_debug", "bangcle_debug", "allatori_debug", "freerasp_debug"]

# --- Verified structural markers -------------------------------------------
BANGCLE_NATIVE_RE = re.compile(r"(libSecShell|libplaceholder)", re.I)
BANGCLE_PAYLOAD_RE = re.compile(r"^assets/classes\d*\.jar$")
# Anchor on the PACKAGE PATH, never the bare word. A case-insensitive /talsec/
# matched "talSec" inside getToTalSeconds() and reported medtimer_baseline_debug
# as carrying a dormant Talsec library on 2026-08-01: 2 hits, both from
# java.time method names. Its DEX has zero Talsec code. Same discipline as
# FREERASP_HOOK_RE below: match the thing itself, not a substring of it.
TALSEC_ENTRY_RE = re.compile(rb"com/aheaditec/talsec|com\.aheaditec\.talsec")
# CRITICAL distinction (learned 2026-07-31): Talsec CLASSES being present only
# means the library shipped as a dependency. It does NOT mean Talsec runs.
# The thing that actually starts Talsec is the injected Application hook that
# tools/pack_freerasp.sh writes (FreeRaspApplication, or FreeRaspStarter for
# the in-place onCreate-patch variant used on Hilt/Koin apps).
# documenter_baseline_debug.signed.apk contains 313 Talsec class strings AND
# the hook in classes9.dex, i.e. its "baseline" actually runs freeRasp.
# Always test for the HOOK, not for the library.
FREERASP_HOOK_RE = re.compile(rb"FreeRaspApplication|FreeRaspStarter")
# Allatori renames to very short identifiers. We count them in the DEX string
# table as a density proxy. Not a hard signature: reported as a metric.
SHORT_CLASS_RE = re.compile(rb"L(?:[a-z]{1,2}/){0,3}[a-z]{1,2};")


def load_known_broken_builds():
    """(app, variant) pairs whose build is known-bad and excluded from sweeps.

    Encoded in 6_results_data/invalidated_builds.csv as an ``invalid_before_utc`` far in
    the future: "no cell of this pair is valid yet, at any timestamp". Anything
    with a real timestamp is a build that HAS been fixed, and must still trip
    the gate if it somehow regresses. The ledger is not part of this package;
    without it the set is empty.
    """
    ledger = REPO / "6_results_data" / "invalidated_builds.csv"
    out = set()
    if not ledger.exists():
        return out
    try:
        with open(ledger) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("app,"):
                    continue
                parts = line.split(",")
                if len(parts) < 3:
                    continue
                app, variant, before = parts[0], parts[1], parts[2]
                if before >= "2090":
                    out.add((app, variant))
    except Exception:
        pass
    return out


def apk_path_for(app: str, variant: str, phone: str = "pixel9"):
    """Resolve the APK path from the AR config (single source of truth)."""
    cfg = CFG_DIR / f"{app}_{variant}_{phone}_wifi_hubctl.json"
    if not cfg.exists():
        return None
    try:
        with open(cfg) as f:
            paths = json.load(f).get("paths", [])
        return Path(paths[0]) if paths else None
    except Exception:
        return None


def _dex_strings(apk: Path, limit_bytes: int = 0) -> bytes:
    """Concatenate ALL DEX blobs so we can run cheap byte scans.

    2026-07-31: the original 24 MB cap produced a FALSE NEGATIVE on tipuous
    (86 MB of DEX: the Talsec classes live past the cap, so talsec_entries
    read 0 on a genuine freeRasp build). Caps on a correctness detector are
    how detectors silently lie. Default is now uncapped; pass limit_bytes>0
    only if you knowingly accept false negatives.
    """
    buf = bytearray()
    try:
        with zipfile.ZipFile(apk) as z:
            for n in sorted(x for x in z.namelist() if x.endswith(".dex")):
                buf += z.read(n)
                if limit_bytes and len(buf) >= limit_bytes:
                    break
    except Exception:
        return b""
    return bytes(buf)


def dex_set_hash(apk: Path) -> str:
    """Stable hash over the FULL ordered set of DEX entries.

    This is the single most valuable build-integrity check in the project.
    Two variants of the same app sharing this hash means one AR config points
    at the wrong build, or a protection step changed no code. Hash the DEX set,
    never the APK file: signing changes the file hash even when the code is
    untouched.
    """
    h = hashlib.sha256()
    try:
        with zipfile.ZipFile(apk) as z:
            for n in sorted(x for x in z.namelist() if x.endswith(".dex")):
                h.update(n.encode())
                h.update(z.read(n))
        return h.hexdigest()[:16]
    except Exception:
        return "ERR"


def fingerprint_apk(apk: Path) -> dict:
    if apk is None or not apk.exists():
        return {"error": "apk_missing"}

    out = {
        "apk_name": apk.name,
        "apk_bytes": apk.stat().st_size,
        "dex_files": 0,
        "dex_bytes": 0,
        "native_libs": 0,
        "native_lib_names": "",
        "bangcle_native_files": 0,
        "bangcle_payload_bytes": 0,
        "bangcle_payload_names": "",
        "talsec_entries": 0,
        "abi_dirs": "",
        "x86_lib_bytes_in_arm_dir": 0,
    }

    try:
        with zipfile.ZipFile(apk) as z:
            infos = z.infolist()
    except Exception as e:
        return {"error": f"zip: {e}"}

    native_names = []
    payload_names = []
    abis = set()

    for i in infos:
        n = i.filename
        if n.endswith(".dex"):
            out["dex_files"] += 1
            out["dex_bytes"] += i.file_size
        if n.endswith(".so"):
            out["native_libs"] += 1
            native_names.append(n.split("/")[-1])
            m = re.match(r"lib/([^/]+)/", n)
            if m:
                abis.add(m.group(1))
            # x86 payload physically located in an ARM ABI dir = dead weight
            if "x86" in n.lower() and m and "arm" in m.group(1).lower():
                out["x86_lib_bytes_in_arm_dir"] += i.file_size
        if BANGCLE_NATIVE_RE.search(n):
            out["bangcle_native_files"] += 1
        if BANGCLE_PAYLOAD_RE.match(n):
            out["bangcle_payload_bytes"] += i.file_size
            payload_names.append(n)
        # assets/libSecShell32 has no .so extension
        if n.startswith("assets/") and BANGCLE_NATIVE_RE.search(n):
            out["bangcle_native_files"] += 1

    out["native_lib_names"] = ";".join(sorted(set(native_names))[:8])
    out["bangcle_payload_names"] = ";".join(payload_names)
    out["abi_dirs"] = ";".join(sorted(abis))

    # Full-DEX-set hash: the definitive build-integrity identifier.
    out["dex_set_hash"] = dex_set_hash(apk)

    # DEX byte scans (Talsec classes, freeRasp hook, short-identifier density)
    dex = _dex_strings(apk)
    if dex:
        out["talsec_entries"] = len(TALSEC_ENTRY_RE.findall(dex))
        out["freerasp_hook_hits"] = len(FREERASP_HOOK_RE.findall(dex))
        short_hits = len(SHORT_CLASS_RE.findall(dex))
        out["short_class_hits"] = short_hits
        out["short_class_per_mb"] = round(
            short_hits / max(1, len(dex) / 1_000_000), 1
        )
    else:
        out["freerasp_hook_hits"] = 0
        out["short_class_hits"] = 0
        out["short_class_per_mb"] = 0.0

    return out


def classify(fp_variant: dict, fp_baseline: dict) -> dict:
    """Derive discriminating verdicts by comparing a variant to its baseline."""
    v = dict(fp_variant)
    if fp_baseline.get("error") or v.get("error"):
        v["dex_shrink_ratio"] = ""
        v["verdict"] = "NO_BASELINE"
        return v

    b_dex = fp_baseline.get("dex_bytes", 0) or 1
    ratio = v.get("dex_bytes", 0) / b_dex
    v["dex_shrink_ratio"] = round(ratio, 4)
    v["baseline_dex_bytes"] = fp_baseline.get("dex_bytes", 0)

    # --- verdicts (each must be FALSE on baseline to be a discriminator) ---
    is_bangcle = (
        v.get("bangcle_payload_bytes", 0) > 0
        and v.get("bangcle_native_files", 0) > 0
        and ratio < 0.5
    )
    # Test for the HOOK, not the library. Talsec classes can ship as a dormant
    # dependency; only the injected Application hook actually starts it.
    is_freerasp = v.get("freerasp_hook_hits", 0) > 0
    v["talsec_lib_present_but_not_wired"] = bool(
        v.get("talsec_entries", 0) > 0 and v.get("freerasp_hook_hits", 0) == 0
    )
    # Allatori has no added file; flag only as a metric delta, never a hard verdict.
    short_delta = (v.get("short_class_per_mb", 0)
                   - fp_baseline.get("short_class_per_mb", 0))
    v["short_class_per_mb_delta_vs_baseline"] = round(short_delta, 1)

    verdicts = []
    if is_bangcle:
        verdicts.append("BANGCLE_STUB_DEX")
    if is_freerasp:
        verdicts.append("FREERASP_WIRED")
    elif v.get("talsec_lib_present_but_not_wired"):
        verdicts.append("TALSEC_LIB_DORMANT")
    v["verdict"] = ";".join(verdicts) if verdicts else "NO_STRUCTURAL_MARKER"
    return v


def main():
    ap = argparse.ArgumentParser(
        description="Structural APK fingerprint — one row per (app, variant)."
    )
    ap.add_argument("--app", help="single app")
    ap.add_argument("--phone", default="pixel9",
                    help="which phone's config to resolve APK paths from")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="CSV output path")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 if any build-integrity gate fails "
                         "(run this before every sweep)")
    args = ap.parse_args()

    apps = [args.app] if args.app else APPS
    rows = []

    for app in apps:
        fps = {}
        for variant in VARIANTS:
            apk = apk_path_for(app, variant, args.phone)
            fps[variant] = fingerprint_apk(apk)

        base = fps.get("baseline_debug", {})
        for variant in VARIANTS:
            row = {"app": app, "variant": variant}
            row.update(classify(fps[variant], base))
            rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return

    # Console table
    print("=" * 118)
    print("STRUCTURAL APK FINGERPRINT")
    print("=" * 118)
    print(f"  {'app':<14s} {'variant':<16s} {'dex_bytes':>10s} {'shrink':>7s} "
          f"{'payload':>9s} {'talsec':>7s} {'natlib':>6s}  verdict")
    for r in rows:
        if r.get("error"):
            print(f"  {r['app']:<14s} {r['variant']:<16s}  ERROR: {r['error']}")
            continue
        print(f"  {r['app']:<14s} {r['variant']:<16s} "
              f"{r.get('dex_bytes',0):>10d} "
              f"{r.get('dex_shrink_ratio',''):>7} "
              f"{r.get('bangcle_payload_bytes',0):>9d} "
              f"{r.get('talsec_entries',0):>7d} "
              f"{r.get('native_libs',0):>6d}  {r.get('verdict','')}")

    # ---- GATE 1: cross-variant DEX collisions (the highest-value check) ----
    print()
    print("=" * 118)
    print("GATE 1 — CROSS-VARIANT DEX COLLISION")
    print("  Two variants of one app sharing a DEX-set hash means an AR config")
    print("  points at the wrong build. Hash the DEX set, NEVER the APK file:")
    print("  re-signing changes the file hash while leaving the code identical,")
    print("  which is exactly what masked this bug until 2026-07-31.")
    print("=" * 118)
    known_broken = load_known_broken_builds()
    collisions = []
    excluded = []
    by_app = defaultdict(dict)
    for r in rows:
        if not r.get("error") and r.get("dex_set_hash"):
            by_app[r["app"]][r["variant"]] = r["dex_set_hash"]
    for app, hv in sorted(by_app.items()):
        seen = defaultdict(list)
        for v, h in hv.items():
            seen[h].append(v)
        for h, vs in seen.items():
            if len(vs) > 1:
                # A build we already know is broken and have removed from the
                # sweep is not a NEW finding: it would otherwise wedge the
                # preflight forever and train us to pass --skip-preflight,
                # which is how a real collision gets waved through. Report it
                # separately and keep the gate's verdict about the rest.
                if any((app, v) in known_broken for v in vs):
                    excluded.append((app, vs, h))
                    print(f"  EXCLUDED   {app:<14s} {' == '.join(sorted(vs))}  (dex {h})"
                          f"  — known broken, removed from the sweep")
                    continue
                collisions.append((app, vs, h))
                print(f"  COLLISION  {app:<14s} {' == '.join(sorted(vs))}  (dex {h})")
    if not collisions:
        print("  OK — every variant of every app is a distinct build"
              + (f" ({len(excluded)} known-broken pair(s) excluded)." if excluded else "."))

    # ---- GATE 2: contaminated baseline ----
    print()
    print("=" * 118)
    print("GATE 2 — BASELINE CONTAMINATION")
    print("  A baseline_debug APK carrying a protection marker is not a baseline.")
    print("=" * 118)
    bad = [r for r in rows
           if r["variant"] == "baseline_debug"
           and r.get("verdict") not in ("NO_STRUCTURAL_MARKER", "NO_BASELINE")]
    if bad:
        for r in bad:
            note = ""
            if r.get("verdict") == "FREERASP_WIRED":
                note = "  <-- baseline RUNS freeRasp; every comparison for this app is compromised"
            elif r.get("verdict") == "TALSEC_LIB_DORMANT":
                note = "  <-- Talsec library shipped but NOT started; dead code only"
            print(f"  {r['app']:<14s} -> {r['verdict']}{note}")
    else:
        print("  OK — no baseline_debug APK carries a protection marker.")

    # ---- GATE 3: variant missing its own marker ----
    print()
    print("=" * 118)
    print("GATE 3 — VARIANT MISSING ITS EXPECTED MARKER")
    print("=" * 118)
    missing = []
    for r in rows:
        if r.get("error"):
            continue
        if r["variant"] == "bangcle_debug" and "BANGCLE_STUB_DEX" not in (r.get("verdict") or ""):
            missing.append((r["app"], "bangcle_debug", "no stub-DEX/payload"))
        if r["variant"] == "freerasp_debug" and "FREERASP_WIRED" not in (r.get("verdict") or ""):
            missing.append((r["app"], "freerasp_debug", "no FreeRasp Application hook"))
    if missing:
        for app, v, why in missing:
            print(f"  {app:<14s} {v:<16s} {why}")
    else:
        print("  OK — bangcle and freerasp variants all carry their marker.")

    gate_failed = bool(collisions or bad or missing)

    # Dead-weight x86 report
    print()
    dead = [(r["app"], r["variant"], r["x86_lib_bytes_in_arm_dir"])
            for r in rows if r.get("x86_lib_bytes_in_arm_dir", 0) > 0]
    if dead:
        print("=" * 118)
        print("DEAD-WEIGHT x86 NATIVE CODE INSIDE ARM ABI DIRS")
        print("  (inflates install size on arm64 devices; never loaded; do NOT")
        print("   attribute this to runtime energy)")
        print("=" * 118)
        total = 0
        for app, variant, b in dead:
            print(f"  {app:<14s} {variant:<16s} {b:>10d} bytes")
            total += b
        print(f"  {'TOTAL':<14s} {'':<16s} {total:>10d} bytes")

    out = Path(args.out or REPO / "6_results_data" / "inputs" / "apk_structural_fingerprint.csv")
    if rows:
        fields = sorted({k for r in rows for k in r.keys()})
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print()
        print(f"CSV: {out}")

    if args.gate:
        print()
        if gate_failed:
            print("BUILD-INTEGRITY GATE: FAILED — do not run a sweep against these APKs.")
            sys.exit(1)
        print("BUILD-INTEGRITY GATE: PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
