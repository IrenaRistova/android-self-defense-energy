#!/usr/bin/env python3
"""Per-app characteristics table for the thesis Subjects section (§4.1).

One extraction pass over the BASELINE APK the sweep actually ran, so the table is
a replication artefact rather than a set of hand-copied fields.

Emits: functional category, min/target SDK, APK size, app-package class and
method counts, and every permission the manifest declares, INTERNET in
particular, which §4.1 and §5 both need.

The APK is resolved from the Android Runner config, not from the directory
layout: for documenter and medtimer those two point at different builds.

Usage:  python3 scratchpad/analysis/app_characteristics.py [--csv out.csv]
"""
from __future__ import annotations
import argparse, csv, glob, json, os, pathlib, re, subprocess, sys, zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
os.chdir(REPO)
sys.path.insert(0, str(REPO / "scratchpad"))
from apk_obfuscation_fingerprint import Dex          # noqa: E402

CFG_DIR = REPO / "android-runner" / "examples" / "batterymanager"
COHORT = ["androidskk", "calculator", "documenter", "itsok", "keep_recipe",
          "linkhub", "medtimer", "pdfviewer", "poetskingdom", "tipuous"]

# app_inclusion.json folder names, for the upstream instrumentation-test count.
# That count is the one "how well tested is this app" figure that exists for
# every app in the cohort, unlike the provenance split, which exists for six.
INCLUSION_FOLDER = {
    "itsok": "qubacy_ItsOK", "keep_recipe": "bwakessa_Keep-Recipe",
    "linkhub": "AmrDeveloper_LinkHub", "poetskingdom": "tinochings_PoetsKingdom",
    "documenter": "ViliusSutkus89_Documenter", "tipuous": "JoshLudahl_tipuous",
    "calculator": "exponentialGroth_Calculator", "medtimer": "Futsch1_medTimer",
    "pdfviewer": "afreakyelf_Pdf-Viewer", "androidskk": "tamo_AndroidSKK"}


def upstream_test_counts() -> dict:
    import json as _json
    try:
        d = _json.load(open(REPO / "specs" / "app_inclusion.json"))
    except Exception:
        return {}
    by_folder = {a["app_folder"]: a for a in d.get("apps", [])}
    out = {}
    for app, folder in INCLUSION_FOLDER.items():
        pb = (by_folder.get(folder) or {}).get("provenance_breakdown") or {}
        out[app] = pb.get("tests_total_in_androidTest")
    return out

# Assigned by hand from each app's upstream README and its scenario list.
CATEGORY = {
    "androidskk":   "input method (Japanese SKK keyboard)",
    "calculator":   "calculator",
    "documenter":   "document viewer / converter",
    "itsok":        "mental-health journal",
    "keep_recipe":  "recipe manager",
    "linkhub":      "bookmark manager",
    "medtimer":     "medication reminder",
    "pdfviewer":    "PDF viewer",
    "poetskingdom": "creative writing / poetry editor",
    "tipuous":      "receipt and tip tracker",
}

def aapt() -> str | None:
    hits = sorted(glob.glob(str(pathlib.Path.home() / "Android/Sdk/build-tools/*/aapt2")),
                  reverse=True)
    return hits[0] if hits else None

def apk_for(app: str, variant: str = "baseline_debug"):
    for phone in ("pixel6", "pixel9", "pixel3"):
        cfg = CFG_DIR / f"{app}_{variant}_{phone}_wifi_ykush.json"
        if not cfg.exists():
            continue
        try:
            d = json.loads(cfg.read_text())
        except Exception:
            continue
        for p in d.get("paths") or []:
            q = pathlib.Path(p)
            if q.suffix == ".apk" and q.exists():
                return q, d.get("application_id") or ""
    return None, ""

def manifest_facts(tool: str, apk: pathlib.Path) -> dict:
    """minSdk / targetSdk / permissions, from aapt2's manifest dump."""
    out = subprocess.run([tool, "dump", "badging", str(apk)],
                         capture_output=True, check=False)
    t = out.stdout.decode("utf-8", errors="replace")
    perms = sorted(set(re.findall(r"uses-permission: name='([^']+)'", t)))
    def one(pat):
        m = re.search(pat, t)
        return m.group(1) if m else ""
    return {
        # aapt2 prints minSdkVersion, not sdkVersion: a pattern without the
        # "min" matches nothing and leaves the column blank.
        "min_sdk": one(r"minSdkVersion:'(\d+)'"),
        "target_sdk": one(r"targetSdkVersion:'(\d+)'"),
        "version_code": one(r"versionCode='([^']*)'"),
        "version_name": one(r"versionName='([^']*)'"),
        "permissions": perms,
    }

def class_prefix(pkg: str) -> str:
    for suf in (".debug", ".release"):
        if pkg.endswith(suf):
            pkg = pkg[: -len(suf)]
    return "L" + pkg.replace(".", "/")

def dex_counts(apk: pathlib.Path, pkg: str) -> dict:
    """Class and method counts, APP PACKAGE ONLY: a whole-APK count is ~97%
    library code and says nothing about the subject."""
    prefix = class_prefix(pkg).encode()
    z = zipfile.ZipFile(apk)
    names = sorted((n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)),
                   key=lambda n: int(re.sub(r"\D", "", n) or "1"))
    app_classes = app_methods = all_classes = 0
    dex_bytes = 0
    for nm in names:
        raw = z.read(nm)
        dex_bytes += len(raw)
        dex = Dex(raw)
        all_classes += dex.class_defs_size
        for i in range(dex.class_defs_size):
            cd = dex.class_def(i)
            if not dex.type_desc(cd[0]).startswith(prefix):
                continue
            app_classes += 1
            try:
                app_methods += sum(1 for _ in dex.class_methods(i))
            except Exception:
                pass
    return {"app_classes": app_classes, "app_methods": app_methods,
            "all_classes": all_classes, "dex_files": len(names),
            "dex_bytes": dex_bytes}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="specs/app_characteristics.csv")
    a = ap.parse_args()
    tool = aapt()
    if not tool:
        print("aapt2 not found under ~/Android/Sdk/build-tools/*/", file=sys.stderr)
        return 2

    upstream = upstream_test_counts()
    rows = []
    for app in COHORT:
        apk, pkg = apk_for(app)
        if apk is None:
            print(f"{app}: no baseline APK resolved from its AR config", file=sys.stderr)
            continue
        mf = manifest_facts(tool, apk)
        dx = dex_counts(apk, pkg)
        rows.append({
            "app": app,
            "category": CATEGORY.get(app, ""),
            "package": pkg,
            "version_name": mf["version_name"],
            "version_code": mf["version_code"],
            "min_sdk": mf["min_sdk"],
            "target_sdk": mf["target_sdk"],
            "apk_bytes": apk.stat().st_size,
            "app_classes": dx["app_classes"],
            "app_methods": dx["app_methods"],
            # Whole-DEX bytes (uncompressed, all classesN.dex). The app package
            # is a small share of this for every app in the cohort, so read it as
            # "how much code ships", not "how much of the app there is".
            "dex_bytes": dx["dex_bytes"],
            "all_classes": dx["all_classes"],
            "dex_files": dx["dex_files"],
            "declares_INTERNET": "INTERNET" in " ".join(mf["permissions"]),
            "declares_NETWORK_STATE": "ACCESS_NETWORK_STATE" in " ".join(mf["permissions"]),
            "upstream_instrumentation_tests": upstream.get(app),
            "n_permissions": len(mf["permissions"]),
            "permissions": ";".join(p.replace("android.permission.", "")
                                    for p in mf["permissions"]),
            "baseline_apk": str(apk),
        })

    hdr = ["app", "category", "min_sdk", "target_sdk", "apk_bytes",
           "app_classes", "app_methods", "declares_INTERNET"]
    print(f"{'app':<14}{'min':>4}{'tgt':>4}{'APK KB':>8}{'DEX KB':>8}"
          f"{'app cls':>8}{'app mth':>8}{'tests':>7}{'INTERNET':>9}")
    for r in rows:
        print(f"{r['app']:<14}{r['min_sdk']:>4}{r['target_sdk']:>4}"
              f"{r['apk_bytes']//1024:>8}{r['dex_bytes']//1024:>8}"
              f"{r['app_classes']:>8}{r['app_methods']:>8}"
              f"{str(r['upstream_instrumentation_tests'] or '?'):>7}"
              f"{str(r['declares_INTERNET']):>9}")
    n_net = sum(1 for r in rows if r["declares_INTERNET"])
    print(f"\n{n_net} of {len(rows)} baseline APKs declare android.permission.INTERNET")

    out = REPO / a.csv
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
