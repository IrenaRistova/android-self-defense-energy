# Build-check inputs: provenance

Four inputs to the results notebooks are measured from the APKs themselves. The APKs
are not in this package (available on request), so no notebook can
regenerate these files; each is one command, run on 2026-10-02, over **the exact
APK every Android Runner config swept** (resolved from the Android Runner configurations, published as
`android-runner/configs/<app>_<variant>_<phone>_wifi_ykush.json`).

| file | command | what it answers |
|---|---|---|
| `control_build_screen.csv` | `tools/verify_baseline_not_obfuscated.py --all --csv …` | is each control build free of obfuscation? |
| `freerasp_build_screen.csv` | `tools/verify_baseline_not_obfuscated.py --all --variant freerasp_debug --csv …` | the same rule on the freeRASP builds (also source builds), as a comparison |
| `allatori_build_screen.csv` | `tools/verify_baseline_not_obfuscated.py --all --variant allatori_debug --csv …` | the same rule on the Allatori builds: how much of the signature obfuscation leaves, the basis for the minimum below |
| `variant_parity.csv` | `tools/verify_variant_parity.py --csv …` | do the four builds of each app differ only by the protection? |
| `allatori_renaming.csv` | `tools/verify_allatori_obfuscation.py --swept --csv …` | how many of each app's own classes did Allatori rename? |

## The control-build screen

**Detection rule.** For every class in the application's own package (third-party
libraries are not counted), read the `SourceFile` attribute the compiler wrote into
the DEX. An unobfuscated debug build keeps names such as `MainActivity.kt`;
Allatori rewrites them to one or two characters as a by-product of renaming.

- **real**: matches `^[A-Za-z_$][\w$]*\.(kt|java)$`
- **1–2 char**: one or two characters, the obfuscation signature
- **other**: any other string (generated code, e.g. Hilt and Dagger)
- **no attribute**: the class carries no `SourceFile` at all. Counted **in
  neither direction**: missing debug information is not evidence of obfuscation,
  and not evidence against it.

**Verdict.** FAIL if fewer than 25% of the classes that carry an attribute have a real
name; WARN if any carry a 1–2 character name; PASS otherwise. **UNPROVEN, never PASS,
when fewer than 10 classes carry an attribute** (added 2026-10-02): in the working
Allatori builds a large share of named classes carries the 1–2 character signature
(`allatori_build_screen.csv`), so ten or more named classes with none of them short is
strong evidence of no obfuscation, and fewer is no evidence either way. No cohort
verdict changed; the smallest control, pdfviewer, clears the minimum comfortably.

**Bangcle builds cannot be screened**: the packer encrypts the DEX, so the class
table is not readable from the APK.

**Hash check.** `apk_sha256` in the screen equals the hash the run tables recorded
for every cell of that (app, variant) that recorded one, so the screened APK is
the APK that ran.

## The linkhub residue (short note)

linkhub's control carries 44 classes with 1–2 character source names. They are
**not** a bundled third-party library: the screen counts only classes inside the
application's own package, and the 44 sit in linkhub's own namespace
(`com.amrdeveloper.linkhub` and its subpackages `ui.explorer`, `ui.folder`,
`ui.link`, `ui.importexport`, plus the single-letter packages `c`, `f`, `k`, `v`
typical of Allatori's package renaming; the counts per package are in the
`short_name_packages` column). linkhub's freeRASP build carries none.

The pattern matches androidskk's (all 158 of its 1–2 character classes are in
`jp.deadend.noname.skk` and its subpackages): obfuscated classes from an Allatori
build of the same source tree ending up in the control. For androidskk that
mechanism is established (a stale `app-debug.apk` shipped as the control, see
the tool's docstring); for linkhub it is the likely explanation but **not proven**.
Whether the 44 classes are ever loaded at run time is also not established: a
cross-DEX reference test does not separate them from ordinary generated classes.
The difference from androidskk is scale: linkhub's control keeps 451 real source
names (androidskk's keeps 0), so its own code is intact and the residue is extra.

## Allatori renaming on the swept APKs

**The tool's default is not what ran.** `tools/verify_allatori_obfuscation.py` by
default reads the `<variant>_canonical/` folders under its `APK_ROOT`, a set of builds that
is not the set the Android Runner configs pointed at. For medtimer the two differ: 430
classes renamed in the canonical build, 400 in the APK that ran (documenter: 145 vs
143). Always run it with `--swept`, which resolves each APK from the sweep configs;
its counts equal `allatori_static_code_effects.csv` for all six applications, and
`allatori_renaming.csv` was produced that way.

## Variant parity

Within every application the four builds share `versionCode`, `versionName` and
`targetSdk`, and every build but one is a debug build (`debuggable` column, added
2026-10-02). **The exception is itsok's Allatori build, a release build** compared against
a debug control: the Allatori packing script (not in this package) builds release variants
unless run with `ALLATORI_BUILD_TYPE=debug`, and the 2026-08-04 itsok rebuild was evidently made without
it. Its sensitivity is in `6_results_analysis/tables/allatori_composition.csv`; every APK's hash equals the run tables'; every Bangcle build carries
the Bangcle stub DEX, every freeRASP build the Talsec start-up hook, and every
control and Allatori build neither. Two attributes differ by design or without
consequence, and are disclosed rather than hidden:

- **INTERNET permission**: seven of the nine cohort controls do not declare it; the
  freeRASP build adds it in six of those seven (not medtimer). It comes with the
  SDK and is part of what freeRASP is.
- **network-security-config**: present on most control and Allatori builds and
  absent on most Bangcle and freeRASP builds. The attribute only decides whether
  the app trusts a user-installed CA, which matters only behind the mitmproxy
  replay proxy. Only pdfviewer exercises the network and runs behind the proxy,
  and all four pdfviewer builds carry it.
- **minSdk**: documenter and itsok freeRASP builds raise minSdk to 23 (the SDK's
  floor). All three test devices run far above it; no effect on what runs.
