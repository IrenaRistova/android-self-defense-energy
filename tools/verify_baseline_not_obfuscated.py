#!/usr/bin/env python3
"""verify_baseline_not_obfuscated.py: is this baseline APK really unobfuscated?

Written 2026-08-30.

WHY THIS EXISTS
---------------
On 2026-08-22 androidskk's `baseline_debug` was found to be Allatori-obfuscated:
`pack_baseline_release`'s Gradle step produced a zero-byte log and shipped the
stale `app-debug.apk` left behind by the previous Allatori build. Its whole arm
was therefore re-obfuscation vs obfuscation, not obfuscation vs none, and nothing
in the pipeline noticed for three weeks.

Every check the pipeline had was DIFFERENTIAL -- it compared the baseline against
the variant and asked whether they differed. That family of check cannot catch
this failure, because when the baseline IS the variant the two agree and the
check goes green. A check that cannot fail is worse than no check.

This one is ABSOLUTE. It asks a question of the baseline alone:

    does this APK still carry the .kt/.java source-file names its own
    compiler wrote?

An unobfuscated debug build does. Allatori rewrites the SourceFile attribute to
one or two characters as a byproduct of renaming, so an obfuscated build does
not. Measured on the cohort: calculator baseline 91 real source names,
androidskk baseline 0.

Run over all ten baselines it returns nine PASS and one FAIL -- androidskk, the
known contamination -- with no false alarms. It also independently surfaces
linkhub, whose baseline carries 44 classes with rewritten source names (WARN):
that app's obfuscated residue really is in its baseline, and this check finds it
without being told to look.

THE TRAP THIS DELIBERATELY AVOIDS
---------------------------------
"No .kt names" has two causes, and only one of them is obfuscation: a release
build compiled without debug info has no SourceFile attributes at ALL. Treating
that as obfuscation would be a false alarm; treating it as a pass would be the
original bug again. So a class with NO SourceFile is counted separately, and an
APK whose app package carries no SourceFile attributes at all is reported
UNPROVEN -- never PASS, never FAIL. Silence is not evidence.

Usage:
    tools/verify_baseline_not_obfuscated.py --all
    tools/verify_baseline_not_obfuscated.py --apps androidskk,calculator
    tools/verify_baseline_not_obfuscated.py --all --gate     # exit 1 on any FAIL
    tools/verify_baseline_not_obfuscated.py path/to/app.apk --package com.foo
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scratchpad"))
from apk_obfuscation_fingerprint import Dex          # noqa: E402  pure-python DEX reader

CFG_DIR = REPO / "android-runner" / "examples" / "batterymanager"
COHORT = ["androidskk", "calculator", "documenter", "itsok", "keep_recipe",
          "linkhub", "medtimer", "pdfviewer", "poetskingdom", "tipuous"]

# A real source name: an identifier plus a Kotlin/Java extension. Allatori's
# rewritten names are 1-2 characters and carry no extension.
RX_REAL_SOURCE = re.compile(r"^[A-Za-z_$][\w$]*\.(kt|java)$")

# Below this share of app-package classes carrying a real source name, call it
# obfuscated. The two observed populations are far apart -- healthy baselines
# sit near 1.0, androidskk's sits at 0.0 -- so the threshold is not doing
# delicate work, and any value in the wide empty middle gives the same verdict.
MIN_REAL_SHARE = 0.25
# Below this many classes carrying a source name the screen cannot tell: in the
# cohort's working Allatori builds 41-93% of named app classes carry a 1-2 char
# name, so with fewer than 10 named classes an obfuscated build could show none
# with probability above 1% (0.59**10 = 0.5%). UNPROVEN, never PASS. (2026-10-02)
MIN_NAMED = 10


def class_prefix(pkg: str) -> str:
    """Class package for an applicationId (strips Gradle's build-type suffix)."""
    for suffix in (".debug", ".release"):
        if pkg.endswith(suffix):
            pkg = pkg[: -len(suffix)]
    return "L" + pkg.replace(".", "/")


def apk_and_package(app: str, variant: str = "baseline_debug"):
    """The APK the sweep actually ran, and its applicationId, from the AR config."""
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


def inspect(apk: pathlib.Path, pkg: str) -> dict:
    """Count app-package classes by what their SourceFile attribute holds."""
    import zipfile
    prefix = class_prefix(pkg).encode()
    z = zipfile.ZipFile(apk)
    dexes = sorted((n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)),
                   key=lambda n: int(re.sub(r"\D", "", n) or "1"))
    real = short = absent = other = 0
    examples: Counter = Counter()
    short_pkgs: Counter = Counter()
    for nm in dexes:
        dex = Dex(z.read(nm))
        for i in range(dex.class_defs_size):
            cd = dex.class_def(i)
            if not dex.type_desc(cd[0]).startswith(prefix):
                continue
            sfi = cd[4]
            if sfi == 0xFFFFFFFF:
                absent += 1
                continue
            sf = dex.strings[sfi].decode("utf-8", "replace")
            if RX_REAL_SOURCE.match(sf):
                real += 1
                examples[sf] += 1
            elif len(sf) <= 2:
                short += 1
                short_pkgs[dex.type_desc(cd[0]).decode().rsplit("/", 1)[0][1:].replace("/", ".")] += 1
            else:
                other += 1
    return {"real": real, "short": short, "absent": absent, "other": other,
            "app_classes": real + short + absent + other,
            "examples": [s for s, _ in examples.most_common(3)],
            "short_packages": short_pkgs}


def verdict(r: dict) -> tuple[str, str]:
    if r["app_classes"] == 0:
        return "UNPROVEN", "no classes in the app package -- wrong applicationId?"
    named = r["real"] + r["short"] + r["other"]
    if named == 0:
        return ("UNPROVEN",
                f"all {r['app_classes']} app classes lack a SourceFile attribute; "
                "debug info was stripped, so this APK cannot answer the question")
    if named < MIN_NAMED:
        return ("UNPROVEN",
                f"only {named} app classes carry a source name; fewer than {MIN_NAMED} "
                "cannot show the absence of obfuscation")
    share = r["real"] / named
    if share < MIN_REAL_SHARE:
        return ("FAIL",
                f"only {r['real']}/{named} classes keep a real source name "
                f"({r['short']} rewritten to 1-2 chars) -- this baseline is obfuscated")
    if r["short"]:
        # A baseline that mostly looks healthy can still carry obfuscated classes.
        # linkhub's does: 44 of them, and they are in the BASELINE, not the
        # allatori build. A pass/fail verdict alone would have hidden that.
        return ("WARN",
                f"{r['real']}/{named} keep a real source name, but {r['short']} "
                f"class(es) carry a 1-2 char name -- partial obfuscation present")
    return "PASS", f"{r['real']}/{named} classes keep a real source name"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("apks", nargs="*", help="APK paths (with --package)")
    ap.add_argument("--package", default="", help="applicationId for a bare APK path")
    ap.add_argument("--apps", default="", help="comma-separated cohort apps")
    ap.add_argument("--all", action="store_true", help="every cohort app's baseline")
    ap.add_argument("--variant", default="baseline_debug")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 on FAIL or UNPROVEN (WARN passes the gate)")
    ap.add_argument("--csv", default="",
                    help="also write one row per APK to this CSV (results-notebook input)")
    a = ap.parse_args()

    targets = []
    if a.all or a.apps:
        for app in (COHORT if a.all else a.apps.split(",")):
            apk, pkg = apk_and_package(app, a.variant)
            targets.append((app, apk, pkg))
    for p in a.apks:
        targets.append((pathlib.Path(p).stem, pathlib.Path(p), a.package))
    if not targets:
        ap.error("give --all, --apps, or an APK path with --package")

    print(f"{'app':<14}{'verdict':<10}{'real':>6}{'1-2ch':>7}{'no-attr':>9}"
          f"{'other':>7}  detail")
    bad = 0
    rows = []
    for app, apk, pkg in targets:
        if apk is None or not pkg:
            print(f"{app:<14}{'SKIP':<10}{'':>6}{'':>7}{'':>9}{'':>7}  "
                  f"no config, APK or applicationId")
            bad += 1
            continue
        r = inspect(apk, pkg)
        v, why = verdict(r)
        print(f"{app:<14}{v:<10}{r['real']:>6}{r['short']:>7}{r['absent']:>9}"
              f"{r['other']:>7}  {why}")
        if r["examples"]:
            print(f"{'':<14}{'':<10}{'':>6}{'':>7}{'':>9}{'':>7}  "
                  f"e.g. {', '.join(r['examples'])}")
        rows.append({"app": app, "variant": a.variant, "verdict": v,
                     "real_source_names": r["real"], "short_source_names": r["short"],
                     "no_source_attribute": r["absent"], "other_source_names": r["other"],
                     "app_package_classes": r["app_classes"],
                     "short_name_packages": ";".join(f"{k}:{v}" for k, v in
                                                     sorted(r["short_packages"].items())),
                     "apk_sha256": hashlib.sha256(apk.read_bytes()).hexdigest()})
        if v not in ("PASS", "WARN"):
            bad += 1
    if a.csv:
        import csv
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    if a.gate and bad:
        print(f"\nGATE FAILED: {bad} of {len(targets)} did not PASS", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
