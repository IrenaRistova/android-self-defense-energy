#!/usr/bin/env bash
# pack_freerasp.sh: integrate Talsec freeRasp (v18.0.2) community edition into
# an app's source, build release-buildType APK, sign with debug keystore.
#
# Integration model: SDK-integrated RASP (NOT a post-compilation packer).
# Modifies the app's source with 4 changes, backed up under .freerasp_bak:
#   1. settings.gradle:           add Talsec + jitpack.io Maven repos
#   2. <module>/build.gradle:     add TalsecSecurity-Community dependency
#   3. <module>/src/main/AndroidManifest.xml: set android:name=".FreeRaspApplication"
#   4. Write FreeRaspApplication.kt into the app's package
#
# Runs assembleRelease; signs with debug.keystore; outputs
# <slug>_freerasp.signed.apk to Master Thesis/APKs/.
#
# PASSIVE MODE (killOnBypass=false): threats are logged but the app never
# terminates. This is required for Appium's UiAutomator2 instrumentation to
# coexist with the RASP checks so we can measure runtime overhead.
#
# On EXIT (including SIGKILL trap), restores all modified files from
# .freerasp_bak backups. NEVER commits.
#
# Usage:
#   tools/pack_freerasp.sh              # all cohort apps
#   tools/pack_freerasp.sh calculator medtimer    # specified slugs

set -u
set -o pipefail

WORKSPACE="/home/irena/Documents/Master Experiment"
DATASET_DIR="$WORKSPACE/app_repositories_newest/final_dataset"
OUT_DIR="/home/irena/Documents/Master Thesis/APKs"
LOG_DIR="$WORKSPACE/tools/sweep_logs"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/pack_freerasp_${TS}.log"

mkdir -p "$OUT_DIR" "$LOG_DIR"

JDK17=""
for j in /usr/lib/jvm/java-17-openjdk-amd64 /usr/lib/jvm/openjdk-17 /usr/lib/jvm/java-1.17.0-openjdk-amd64; do
    [ -d "$j" ] && { JDK17="$j"; break; }
done
JDK21=""
for j in /usr/lib/jvm/java-21-openjdk-amd64 /usr/lib/jvm/openjdk-21 /usr/lib/jvm/java-1.21.0-openjdk-amd64; do
    [ -d "$j" ] && { JDK21="$j"; break; }
done
declare -A APP_JDK_OVERRIDE
APP_JDK_OVERRIDE[linkhub]="21"
APP_JDK_OVERRIDE[tipuous]="21"

select_jdk_for_app() {
    local app="$1"
    local override="${APP_JDK_OVERRIDE[$app]:-}"
    if [ "$override" = "21" ] && [ -n "$JDK21" ]; then
        export JAVA_HOME="$JDK21"; export PATH="$JAVA_HOME/bin:${PATH#*:}"
    else
        export JAVA_HOME="$JDK17"; export PATH="$JAVA_HOME/bin:${PATH#*:}"
    fi
}

ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
BT_DIR="$(ls -1d "$ANDROID_HOME"/build-tools/* 2>/dev/null | sort -V | tail -1)"
ZIPALIGN="$BT_DIR/zipalign"
APKSIGNER="$BT_DIR/apksigner"
KEYSTORE="$HOME/.android/debug.keystore"
KEY_ALIAS="androiddebugkey"
KEY_PASS="android"

# SHA-256 of ~/.android/debug.keystore (androiddebugkey), base64-encoded.
# Talsec's onTamperDetected will fire against this at runtime if any release
# variant is ever signed with a different key (expected and harmless in passive mode).
DEBUG_CERT_HASH_BASE64="P0ok5Jb0iJkFZbosWSykyRERzDuSrmmNre9TYe4CUkw="

# Cohort mapping: slug → repo|module|assemble_task|flavor_dir|package_name
declare -A COHORT
COHORT[calculator]="exponentialGroth_Calculator|app|assembleRelease||com.exponential_groth.calculator"
COHORT[another_notes]="maltaisn_another-notes-app|app|assembleRelease||com.maltaisn.notes.sync"
COHORT[itsok]="qubacy_ItsOK|app|assembleRelease||com.qubacy.itsok"
COHORT[keep_recipe]="bwakessa_Keep-Recipe|app|assembleRelease||com.keeprecipes.android"
COHORT[keepitup]="ibbaa_keepitup|keepitupmain|assembleRelease||net.ibbaa.keepitup"
COHORT[medtimer]="Futsch1_medTimer|app|assembleRelease||com.futsch1.medtimer"
COHORT[poetskingdom]="tinochings_PoetsKingdom|app|assembleRelease||com.wendorochena.poetskingdom"
COHORT[pdfviewer]="afreakyelf_Pdf-Viewer|app|assembleRelease||com.rajat.sample.pdfviewer"
COHORT[horoscapp]="angelsoft071_HoroscApp|app|assembleRelease||com.angelsoft.horoscapp"
COHORT[crcontainer]="curiouslearning_CRcontainer_v2.34.2|app|assembleRelease||org.curiouslearning.container"
COHORT[documenter]="ViliusSutkus89_Documenter|app|assembleRelease||com.viliussutkus89.documenter"
COHORT[tipuous]="JoshLudahl_tipuous|app|assembleRelease||com.tips.tipuous"
COHORT[androidskk]="tamo_AndroidSKK|app|assembleRelease||jp.deadend.noname.skk"
COHORT[avnc]="gujjwal00_avnc|app|assembleRelease||com.gaurav.avnc"
COHORT[diaguard]="Faltenreich_Diaguard|app|assembleStoreRelease|store|com.faltenreich.diaguard"
COHORT[linkhub]="AmrDeveloper_LinkHub|app|assembleRelease||com.amrdeveloper.linkhub"
COHORT[podaura]="SkyD666_PodAura|app|assembleGitHubRelease|GitHub|com.skyd.podaura"

# Per-app extra Gradle flags (space-separated). podaura needs --no-configuration-cache
# due to KMP LazyResolvedConfiguration bug, as in pack_baseline_release.sh.
# Apps whose APK must NOT be run through tools/patch_apk_nsc.sh. apktool's
# decompile/rebuild corrupts documenter's multi-DEX + multi-ABI native-lib
# repack and the app then fails to start (2026-07-25). documenter does no
# network I/O during its scenarios, so skipping NSC costs nothing there.
# Mirrors the same map in pack_baseline_release.sh and the Allatori packer.
declare -A NO_NSC_PATCH
NO_NSC_PATCH[documenter]=1

declare -A GRADLE_EXTRA_FLAGS
GRADLE_EXTRA_FLAGS[podaura]="--no-configuration-cache"

SLUGS_ORDER=(calculator another_notes itsok keep_recipe keepitup medtimer poetskingdom pdfviewer horoscapp crcontainer documenter tipuous androidskk avnc diaguard linkhub podaura)

log()     { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"; }
log_pre() { printf '%s\n' "$*" | tee -a "$LOG_FILE"; }

PATCHED_FILES=()
CREATED_FILES=()

restore_patches() {
    for f in "${PATCHED_FILES[@]}"; do
        [ -f "${f}.freerasp_bak" ] && mv -f "${f}.freerasp_bak" "$f"
    done
    for f in "${CREATED_FILES[@]}"; do
        [ -f "$f" ] && rm -f "$f"
    done
}
trap restore_patches EXIT INT TERM

# patch_root_gradle: some apps declare repositories at the ROOT project
# via `allprojects { repositories { ... } }` in build.gradle{,.kts}. When they
# do, Gradle emits "The project declares repositories, effectively ignoring
# the repositories you have declared in the settings", so the module cannot
# resolve talsec-artifact-repository even though we added it to settings.
# Inject Talsec + jitpack into that root allprojects block too, when present.
patch_root_gradle() {
    local repo="$1"
    for ext in build.gradle.kts build.gradle; do
        local root_gf="$DATASET_DIR/$repo/$ext"
        [ -f "$root_gf" ] || continue
        if grep -q "talsec-artifact-repository" "$root_gf"; then
            return 0
        fi
        # Only patch if root declares allprojects { repositories { ... } }
        if ! grep -qE "allprojects\s*\{" "$root_gf"; then
            continue
        fi
        cp -p "$root_gf" "${root_gf}.freerasp_bak"
        PATCHED_FILES+=("$root_gf")
        python3 - "$root_gf" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
is_kts = p.suffix == '.kts'
if is_kts:
    add = '        maven { url = uri("https://europe-west3-maven.pkg.dev/talsec-artifact-repository/freerasp") }\n        maven { url = uri("https://jitpack.io") }\n'
else:
    add = '        maven { url "https://europe-west3-maven.pkg.dev/talsec-artifact-repository/freerasp" }\n        maven { url "https://jitpack.io" }\n'

def find_block_span(s, block_name):
    pat = re.compile(r'(?:^|\n)\s*' + re.escape(block_name) + r'\s*\{', re.MULTILINE)
    m = pat.search(s)
    if not m: return None
    start = s.find('{', m.end() - 1)
    depth = 0; i = start
    while i < len(s):
        c = s[i]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return (start, i+1)
        i += 1
    return None

span = find_block_span(src, 'allprojects')
inserted = False
if span:
    body_start, body_end = span
    body = src[body_start:body_end]
    rm = re.search(r'repositories\s*\{', body)
    if rm:
        ins = body_start + rm.end()
        src = src[:ins] + '\n' + add + src[ins:]
        inserted = True

# If the root build.gradle has no allprojects{repositories{}} block, we
# do NOT append one: modern Gradle settings.gradle uses
# `dependencyResolutionManagement { repositoriesMode.set(FAIL_ON_PROJECT_REPOS) }`
# which would reject any project-declared repositories block. Since
# patch_gradle_settings already injected Talsec repos into settings.gradle's
# dependencyResolutionManagement block, we don't need root project repos.
# The `inserted=False` case is a legitimate no-op.

if inserted:
    p.write_text(src)
PY
        return 0
    done
    return 0
}

patch_gradle_settings() {
    local settings="$1"
    [ -f "$settings" ] || return 1
    cp -p "$settings" "${settings}.freerasp_bak"
    PATCHED_FILES+=("$settings")
    if grep -q "talsec-artifact-repository" "$settings"; then
        return 0
    fi
    python3 - "$settings" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
is_kts = p.suffix == '.kts'
if is_kts:
    add = '        maven { url = uri("https://europe-west3-maven.pkg.dev/talsec-artifact-repository/freerasp") }\n        maven { url = uri("https://jitpack.io") }\n'
    fallback = 'dependencyResolutionManagement {\n    repositories {\n        google()\n        mavenCentral()\n        maven { url = uri("https://europe-west3-maven.pkg.dev/talsec-artifact-repository/freerasp") }\n        maven { url = uri("https://jitpack.io") }\n    }\n}\n'
else:
    add = '        maven { url "https://europe-west3-maven.pkg.dev/talsec-artifact-repository/freerasp" }\n        maven { url "https://jitpack.io" }\n'
    fallback = 'dependencyResolutionManagement {\n    repositories {\n        google()\n        mavenCentral()\n        maven { url "https://europe-west3-maven.pkg.dev/talsec-artifact-repository/freerasp" }\n        maven { url "https://jitpack.io" }\n    }\n}\n'

def find_block_span(s, block_name):
    """Find the outermost { ... } span of a top-level block named block_name."""
    pat = re.compile(r'(?:^|\n)\s*' + re.escape(block_name) + r'\s*\{', re.MULTILINE)
    m = pat.search(s)
    if not m: return None
    start = s.find('{', m.end() - 1)
    depth = 0; i = start
    while i < len(s):
        c = s[i]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return (start, i+1)
        i += 1
    return None

# Prefer dependencyResolutionManagement { repositories { ... } }
span = find_block_span(src, 'dependencyResolutionManagement')
inserted = False
if span:
    body_start, body_end = span
    body = src[body_start:body_end]
    rm = re.search(r'repositories\s*\{', body)
    if rm:
        ins = body_start + rm.end()
        src = src[:ins] + '\n' + add + src[ins:]
        inserted = True

# Fallback: allprojects { repositories { ... } } (older Gradle style)
if not inserted:
    span = find_block_span(src, 'allprojects')
    if span:
        body_start, body_end = span
        body = src[body_start:body_end]
        rm = re.search(r'repositories\s*\{', body)
        if rm:
            ins = body_start + rm.end()
            src = src[:ins] + '\n' + add + src[ins:]
            inserted = True

# Last resort: append a full dependencyResolutionManagement block
if not inserted:
    src = src.rstrip() + '\n\n// [freerasp-injected]\n' + fallback

p.write_text(src)
PY
}

# disable_coverage_gradle: strip JaCoCo coverage instrumentation.
#
# Must mirror the identical patch in pack_baseline_release.sh and the Allatori
# packer. medtimer is the only cohort app that enables coverage, and JaCoCo adds a
# probe array plus a $jacocoInit() call to EVERY method: per-method overhead that
# lands straight in the energy measurement and that no other app carries. It
# also makes allatori impossible to build (JaCoCo's transform sits between
# compile and dex and cannot instrument obfuscated bytecode). Coverage is off
# for ALL FOUR variants or the matched pair compares instrumentation as well as
# protection.
#
# Separate from patch_module_gradle because that function early-returns when the
# Talsec dependency is already present, which would skip this.
disable_coverage_gradle() {
    local gf="$1"
    # OPT-IN ONLY (default-off since 2026-08-03).
    [ "${PACK_DISABLE_COVERAGE:-0}" = "1" ] || return 0
    [ -f "$gf" ] || return 0
    if [ ! -f "${gf}.freerasp_bak" ]; then
        cp -p "$gf" "${gf}.freerasp_bak"
        PATCHED_FILES+=("$gf")
    fi
    python3 - "$gf" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); src = p.read_text(); before = src
src = re.sub(r'(enableUnitTestCoverage\s*=\s*)true', r'\1false', src)
src = re.sub(r'(enableAndroidTestCoverage\s*=\s*)true', r'\1false', src)
src = re.sub(r'(enableUnitTestCoverage\s+)true', r'\1false', src)
src = re.sub(r'(enableAndroidTestCoverage\s+)true', r'\1false', src)
src = re.sub(r'(testCoverageEnabled\s*=?\s*)true', r'\1false', src)
if src != before:
    p.write_text(src)
    print("[pack_freerasp] disabled JaCoCo coverage instrumentation", file=sys.stderr)
PY
    return 0
}

patch_module_gradle() {
    local gf="$1"
    [ -f "$gf" ] || return 1
    cp -p "$gf" "${gf}.freerasp_bak"
    PATCHED_FILES+=("$gf")
    if grep -q "TalsecSecurity-Community" "$gf"; then
        return 0
    fi
    python3 - "$gf" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
# 1) Add Talsec dependency
m = re.search(r'(dependencies\s*\{)', src)
if not m:
    src = src + '\n// [freerasp-injected]\ndependencies {\n    implementation("com.aheaditec.talsec.security:TalsecSecurity-Community:18.0.2")\n}\n'
else:
    is_kts = p.suffix == '.kts'
    line = '\n    // [freerasp-injected]\n    implementation("com.aheaditec.talsec.security:TalsecSecurity-Community:18.0.2")\n' if is_kts \
           else "\n    // [freerasp-injected]\n    implementation 'com.aheaditec.talsec.security:TalsecSecurity-Community:18.0.2'\n"
    src = src[:m.end()] + line + src[m.end():]

# 2) Bump minSdk to >= 23 (Talsec 18 requires 23). Handle common forms:
#    - `minSdk = 21` / `minSdkVersion 21` in KTS/Groovy
#    - `minSdk 21` (Groovy shorthand)
# Only rewrite when the numeric literal is present AND < 23. Skip refs like
# `minSdk = libs.versions.minSdk.get()`: those need version-catalog edits which
# would be app-specific; if the numeric bump doesn't apply, we still try to
# add tools:overrideLibrary in the manifest (see patch_manifest{,_perms_only}).
def bump_num(match):
    val = int(match.group('val'))
    if val >= 23:
        return match.group(0)
    return match.group(0).replace(match.group('val'), '23')

patterns = [
    r'(?P<lead>minSdk(?:Version)?\s*=\s*)(?P<val>\d+)\b',
    r'(?P<lead>minSdk(?:Version)?\s+)(?P<val>\d+)\b',
]
for pat in patterns:
    src = re.sub(pat, bump_num, src)

p.write_text(src)
PY
}

# patch_manifest_perms_only: for hilt-pattern apps that keep their own
# Application class. Applies the xmlns:tools + ACCESS_NETWORK_STATE / ACCESS_WIFI_STATE
# strip+inject steps, but does NOT rewrite <application android:name>. Talsec init
# happens from the existing App class via FreeRaspStarter.
patch_manifest_perms_only() {
    local mf="$1"
    [ -f "$mf" ] || return 1
    cp -p "$mf" "${mf}.freerasp_bak"
    PATCHED_FILES+=("$mf")
    python3 - "$mf" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()

# xmlns:tools + perm fixes (same as patch_manifest steps 2-4)
if 'xmlns:tools=' not in src:
    src = re.sub(r'(<manifest\s)',
                 r'\1xmlns:tools="http://schemas.android.com/tools"\n    ',
                 src, count=1)

# Ensure Talsec's minSdk (23) survives manifest merge even when the app
# declares minSdk=21 or lower via build.gradle / libs.versions.toml.
# tools:overrideLibrary tells the manifest merger to accept the library's
# higher requirement without failing the build. Safe here because ALL our
# test devices are API >= 30 (P3, P6, P9) so Talsec APIs will succeed at runtime.
if 'tools:overrideLibrary' not in src or 'com.aheaditec.talsec_security' not in src:
    if '<uses-sdk' in src:
        # Attach to existing <uses-sdk> element (append or merge attribute)
        src = re.sub(
            r'(<uses-sdk\b[^>]*?)(\s*/?>)',
            r'\1 tools:overrideLibrary="com.aheaditec.talsec_security"\2',
            src, count=1)
    else:
        # Inject a new <uses-sdk> element after the <manifest> opening tag
        src = re.sub(
            r'(<manifest\b[^>]*>)',
            r'\1\n    <uses-sdk tools:overrideLibrary="com.aheaditec.talsec_security" />',
            src, count=1)

for perm in ('ACCESS_NETWORK_STATE', 'ACCESS_WIFI_STATE'):
    pat = re.compile(
        r'<uses-permission\b[^>]*android:name="android\.permission\.' + perm +
        r'"[^>]*tools:node="remove"[^>]*/>', re.DOTALL)
    src = pat.sub('', src)
    pat2 = re.compile(
        r'<uses-permission\b[^>]*tools:node="remove"[^>]*android:name="android\.permission\.' + perm +
        r'"[^>]*/>', re.DOTALL)
    src = pat2.sub('', src)

for perm in ('ACCESS_NETWORK_STATE', 'ACCESS_WIFI_STATE'):
    if not re.search(
        r'<uses-permission\b[^>]*android:name="android\.permission\.' + perm + r'"', src):
        add = f'    <uses-permission android:name="android.permission.{perm}" />\n'
        m = re.search(r'(\s*<uses-permission\b)', src)
        if m:
            src = src[:m.start()] + '\n' + add + src[m.start():]
        else:
            src = re.sub(r'(<application\s)', add + r'    \1', src, count=1)

p.write_text(src)
PY
}

patch_manifest() {
    local mf="$1"
    [ -f "$mf" ] || return 1
    cp -p "$mf" "${mf}.freerasp_bak"
    PATCHED_FILES+=("$mf")
    python3 - "$mf" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()

# 1) Application subclass: set android:name=".FreeRaspApplication"
if re.search(r'<application\s[^>]*android:name=', src):
    src = re.sub(r'(<application\s[^>]*android:name=")[^"]*(")',
                 r'\1.FreeRaspApplication\2', src, count=1)
else:
    src = re.sub(r'(<application\s)', r'\1android:name=".FreeRaspApplication"\n        ',
                 src, count=1)

# 2) Ensure xmlns:tools declared (needed for tools:node manipulations we do below)
if 'xmlns:tools=' not in src:
    src = re.sub(r'(<manifest\s)',
                 r'\1xmlns:tools="http://schemas.android.com/tools"\n    ',
                 src, count=1)

# Ensure Talsec's minSdk (23) survives manifest merge even when the app
# declares minSdk=21 or lower via build.gradle / libs.versions.toml.
# tools:overrideLibrary tells the manifest merger to accept the library's
# higher requirement without failing the build. Safe here because ALL our
# test devices are API >= 30 (P3, P6, P9) so Talsec APIs will succeed at runtime.
if 'tools:overrideLibrary' not in src or 'com.aheaditec.talsec_security' not in src:
    if '<uses-sdk' in src:
        # Attach to existing <uses-sdk> element (append or merge attribute)
        src = re.sub(
            r'(<uses-sdk\b[^>]*?)(\s*/?>)',
            r'\1 tools:overrideLibrary="com.aheaditec.talsec_security"\2',
            src, count=1)
    else:
        # Inject a new <uses-sdk> element after the <manifest> opening tag
        src = re.sub(
            r'(<manifest\b[^>]*>)',
            r'\1\n    <uses-sdk tools:overrideLibrary="com.aheaditec.talsec_security" />',
            src, count=1)

# 3) freeRasp requires ACCESS_NETWORK_STATE + ACCESS_WIFI_STATE at runtime for
#    its unsecure-wifi threat detection. Some apps explicitly REMOVE these via
#    `tools:node="remove"` to strip inherited permissions from other libs
#    (medtimer is one example). Strip any such removals so the merger keeps
#    Talsec's declarations.
for perm in ('ACCESS_NETWORK_STATE', 'ACCESS_WIFI_STATE'):
    # Match multi-line <uses-permission ... tools:node="remove" .../>
    pat = re.compile(
        r'<uses-permission\b[^>]*android:name="android\.permission\.' + perm +
        r'"[^>]*tools:node="remove"[^>]*/>', re.DOTALL)
    src = pat.sub('', src)
    # Also the reverse attribute order (tools:node before android:name)
    pat2 = re.compile(
        r'<uses-permission\b[^>]*tools:node="remove"[^>]*android:name="android\.permission\.' + perm +
        r'"[^>]*/>', re.DOTALL)
    src = pat2.sub('', src)

# 4) If the app itself does not declare ACCESS_NETWORK_STATE (some apps don't;
#    Talsec's manifest merge would normally add it but manifest-merger conflicts
#    can prevent that), inject it as a plain declaration.
for perm in ('ACCESS_NETWORK_STATE', 'ACCESS_WIFI_STATE'):
    if not re.search(
        r'<uses-permission\b[^>]*android:name="android\.permission\.' + perm + r'"', src):
        # Insert before the first <uses-permission> or <application>
        add = f'    <uses-permission android:name="android.permission.{perm}" />\n'
        m = re.search(r'(\s*<uses-permission\b)', src)
        if m:
            src = src[:m.start()] + '\n' + add + src[m.start():]
        else:
            src = re.sub(r'(<application\s)', add + r'    \1', src, count=1)

p.write_text(src)
PY
}

write_application_kt() {
    local module_dir="$1"
    local pkg="$2"
    # Java-package path in the app's Kotlin source root
    local kt_path
    kt_path=$(echo "$pkg" | tr '.' '/')
    local kt_dir="$module_dir/src/main/java/$kt_path"
    mkdir -p "$kt_dir"
    local kt_file="$kt_dir/FreeRaspApplication.kt"
    CREATED_FILES+=("$kt_file")
    cat >"$kt_file" <<KOTLIN
package ${pkg}

import android.app.Application
import android.util.Log
import com.aheaditec.talsec_security.security.api.SuspiciousAppInfo
import com.aheaditec.talsec_security.security.api.Talsec
import com.aheaditec.talsec_security.security.api.TalsecConfig
import com.aheaditec.talsec_security.security.api.ThreatListener

/**
 * FreeRaspApplication (auto-generated by tools/pack_freerasp.sh) — Talsec
 * freeRasp community-edition v18.0.2 in PASSIVE / log-only mode.
 * killOnBypass(false) so Appium instrumentation coexists with the RASP checks.
 */
class FreeRaspApplication : Application() {

    private val threatListener = object : ThreatListener.ThreatDetected() {
        override fun onRootDetected() { Log.w(TAG, "onRootDetected") }
        override fun onDebuggerDetected() { Log.w(TAG, "onDebuggerDetected") }
        override fun onEmulatorDetected() { Log.w(TAG, "onEmulatorDetected") }
        override fun onTamperDetected() { Log.w(TAG, "onTamperDetected") }
        override fun onUntrustedInstallationSourceDetected() { Log.w(TAG, "onUntrustedInstallationSource") }
        override fun onHookDetected() { Log.w(TAG, "onHookDetected") }
        override fun onDeviceBindingDetected() { Log.w(TAG, "onDeviceBindingDetected") }
        override fun onObfuscationIssuesDetected() { Log.w(TAG, "onObfuscationIssuesDetected") }
        override fun onMalwareDetected(suspiciousApps: MutableList<SuspiciousAppInfo>) {
            Log.w(TAG, "onMalwareDetected count=\${suspiciousApps.size}")
        }
        override fun onScreenshotDetected() { Log.w(TAG, "onScreenshotDetected") }
        override fun onScreenRecordingDetected() { Log.w(TAG, "onScreenRecordingDetected") }
    }

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "FreeRaspApplication.onCreate — starting Talsec (passive)")
        val config = TalsecConfig.Builder(
            "${pkg}",
            arrayOf(EXPECTED_CERT_HASH_BASE64)
        )
            .watcherMail("i2.ristova@student.vu.nl")
            .prod(true)
            .build()
        ThreatListener(threatListener).registerListener(this)
        Talsec.start(this, config)
        Log.i(TAG, "Talsec started")
    }

    companion object {
        private const val TAG = "FreeRasp"
        private const val EXPECTED_CERT_HASH_BASE64 = "${DEBUG_CERT_HASH_BASE64}"
    }
}
KOTLIN
    echo "$kt_file"
}

# hilt_pattern_patch: for apps with an existing custom Application subclass
# (Hilt / Koin / vanilla: anything with an @HiltAndroidApp or app-init reflection
# our SKIP path previously rejected). Instead of replacing the manifest's
# android:name, we:
#   1. Write a self-contained FreeRaspStarter helper file (Kotlin) in the same
#      package as the App class. Kotlin is safe from Java too since AGP always
#      applies the Kotlin compiler when a .kt file is under src/main/java/.
#      Skip if the app's build.gradle doesn't apply the kotlin plugin (pure Java):
#      use a .java helper instead.
#   2. Patch the existing App class file:
#        - Add import FreeRaspStarter
#        - Insert `FreeRaspStarter.start(this)` at the END of onCreate()
#        - If no onCreate() exists, add one with super.onCreate() + FreeRaspStarter.start
# The existing App class file is added to PATCHED_FILES so restore_patches
# reverts it on exit (leaves the repo clean between builds).
hilt_pattern_patch() {
    local module_dir="$1"
    local pkg="$2"
    local existing_app_name="$3"   # e.g. ".SKKApplication"
    local mf="$4"                  # manifest path (for pkg fallback)
    local gf="$5"                  # module build.gradle{,.kts} (to detect Kotlin plugin)

    # Resolve fully-qualified class name (existing_app_name may start with '.')
    local full_class
    if [[ "$existing_app_name" == .* ]]; then
        full_class="${pkg}${existing_app_name}"
    else
        full_class="$existing_app_name"
    fi
    local class_pkg="${full_class%.*}"     # package portion
    local class_simple="${full_class##*.}" # bare class name

    # Locate source file (Kotlin first, then Java)
    local app_file kt_file java_file
    kt_file=$(find "$module_dir/src/main" -type f -name "${class_simple}.kt" 2>/dev/null | head -1)
    java_file=$(find "$module_dir/src/main" -type f -name "${class_simple}.java" 2>/dev/null | head -1)
    local app_lang
    if [ -n "$kt_file" ]; then
        app_file="$kt_file"; app_lang="kotlin"
    elif [ -n "$java_file" ]; then
        app_file="$java_file"; app_lang="java"
    else
        echo "ERR: hilt_pattern_patch — could not locate ${class_simple}.{kt,java}" >&2
        return 1
    fi

    # Decide starter language: Kotlin only if the module actually APPLIES the
    # Kotlin Android plugin (mere `kotlin-bom` platform imports don't count:
    # they leave the compiler unconfigured and the Kotlin source won't build).
    local starter_lang="java"
    if grep -qE "kotlin-android|org\.jetbrains\.kotlin\.android|kotlin\(\"android\"\)|apply plugin:\s*['\"]kotlin(-android)?['\"]" "$gf" 2>/dev/null; then
        starter_lang="kotlin"
    fi
    # If the App class itself is Kotlin, we must use Kotlin (otherwise the App
    # class's package-private symbols may not be visible to a Java helper).
    if [ "$app_lang" = "kotlin" ]; then
        starter_lang="kotlin"
    fi

    # Compute the source root and class package path
    # Source root: everything before src/main/java/ (or src/main/kotlin/)
    local src_root
    if [[ "$app_file" == */src/main/java/* ]]; then
        src_root="${app_file%%/src/main/java/*}/src/main/java"
    elif [[ "$app_file" == */src/main/kotlin/* ]]; then
        src_root="${app_file%%/src/main/kotlin/*}/src/main/kotlin"
    else
        src_root="$module_dir/src/main/java"
    fi
    local class_pkg_path
    class_pkg_path=$(echo "$class_pkg" | tr '.' '/')
    mkdir -p "$src_root/$class_pkg_path"

    # Write the helper file
    local helper_ext
    [ "$starter_lang" = "kotlin" ] && helper_ext="kt" || helper_ext="java"
    local helper_file="$src_root/$class_pkg_path/FreeRaspStarter.${helper_ext}"
    CREATED_FILES+=("$helper_file")

    if [ "$starter_lang" = "kotlin" ]; then
        cat >"$helper_file" <<KOTLIN
package ${class_pkg}

import android.content.Context
import android.util.Log
import com.aheaditec.talsec_security.security.api.SuspiciousAppInfo
import com.aheaditec.talsec_security.security.api.Talsec
import com.aheaditec.talsec_security.security.api.TalsecConfig
import com.aheaditec.talsec_security.security.api.ThreatListener

/**
 * FreeRaspStarter (auto-generated by tools/pack_freerasp.sh) — Talsec freeRasp v18
 * in PASSIVE / log-only mode. Called from ${class_simple}.onCreate() via an
 * in-place patch so we do not replace the existing Application subclass and
 * break @HiltAndroidApp / KAPT-generated setup.
 */
object FreeRaspStarter {
    private const val TAG = "FreeRasp"
    private const val EXPECTED_CERT_HASH_BASE64 = "${DEBUG_CERT_HASH_BASE64}"

    private val threatListener = object : ThreatListener.ThreatDetected() {
        override fun onRootDetected() { Log.w(TAG, "onRootDetected") }
        override fun onDebuggerDetected() { Log.w(TAG, "onDebuggerDetected") }
        override fun onEmulatorDetected() { Log.w(TAG, "onEmulatorDetected") }
        override fun onTamperDetected() { Log.w(TAG, "onTamperDetected") }
        override fun onUntrustedInstallationSourceDetected() { Log.w(TAG, "onUntrustedInstallationSource") }
        override fun onHookDetected() { Log.w(TAG, "onHookDetected") }
        override fun onDeviceBindingDetected() { Log.w(TAG, "onDeviceBindingDetected") }
        override fun onObfuscationIssuesDetected() { Log.w(TAG, "onObfuscationIssuesDetected") }
        override fun onMalwareDetected(suspiciousApps: MutableList<SuspiciousAppInfo>) {
            Log.w(TAG, "onMalwareDetected count=\${suspiciousApps.size}")
        }
        override fun onScreenshotDetected() { Log.w(TAG, "onScreenshotDetected") }
        override fun onScreenRecordingDetected() { Log.w(TAG, "onScreenRecordingDetected") }
    }

    @JvmStatic
    fun start(context: Context) {
        Log.i(TAG, "FreeRaspStarter.start — initialising Talsec (passive)")
        val config = TalsecConfig.Builder(
            "${pkg}",
            arrayOf(EXPECTED_CERT_HASH_BASE64)
        )
            .watcherMail("i2.ristova@student.vu.nl")
            .prod(true)
            .build()
        ThreatListener(threatListener).registerListener(context)
        Talsec.start(context, config)
        Log.i(TAG, "Talsec started")
    }
}
KOTLIN
    else
        # Pure Java helper
        cat >"$helper_file" <<JAVA
package ${class_pkg};

import android.content.Context;
import android.util.Log;
import com.aheaditec.talsec_security.security.api.SuspiciousAppInfo;
import com.aheaditec.talsec_security.security.api.Talsec;
import com.aheaditec.talsec_security.security.api.TalsecConfig;
import com.aheaditec.talsec_security.security.api.ThreatListener;
import java.util.List;

/**
 * FreeRaspStarter (auto-generated by tools/pack_freerasp.sh) — Talsec freeRasp v18
 * in PASSIVE / log-only mode. Called from ${class_simple}.onCreate() via an
 * in-place patch so we do not replace the existing Application subclass.
 */
public final class FreeRaspStarter {
    private static final String TAG = "FreeRasp";
    private static final String EXPECTED_CERT_HASH_BASE64 = "${DEBUG_CERT_HASH_BASE64}";

    private FreeRaspStarter() {}

    private static final ThreatListener.ThreatDetected LISTENER = new ThreatListener.ThreatDetected() {
        @Override public void onRootDetected() { Log.w(TAG, "onRootDetected"); }
        @Override public void onDebuggerDetected() { Log.w(TAG, "onDebuggerDetected"); }
        @Override public void onEmulatorDetected() { Log.w(TAG, "onEmulatorDetected"); }
        @Override public void onTamperDetected() { Log.w(TAG, "onTamperDetected"); }
        @Override public void onUntrustedInstallationSourceDetected() { Log.w(TAG, "onUntrustedInstallationSource"); }
        @Override public void onHookDetected() { Log.w(TAG, "onHookDetected"); }
        @Override public void onDeviceBindingDetected() { Log.w(TAG, "onDeviceBindingDetected"); }
        @Override public void onObfuscationIssuesDetected() { Log.w(TAG, "onObfuscationIssuesDetected"); }
        @Override public void onMalwareDetected(List<SuspiciousAppInfo> suspiciousApps) {
            Log.w(TAG, "onMalwareDetected count=" + suspiciousApps.size());
        }
        @Override public void onScreenshotDetected() { Log.w(TAG, "onScreenshotDetected"); }
        @Override public void onScreenRecordingDetected() { Log.w(TAG, "onScreenRecordingDetected"); }
    };

    public static void start(Context context) {
        Log.i(TAG, "FreeRaspStarter.start — initialising Talsec (passive)");
        TalsecConfig config = new TalsecConfig.Builder(
            "${pkg}",
            new String[] { EXPECTED_CERT_HASH_BASE64 }
        )
            .watcherMail("i2.ristova@student.vu.nl")
            .prod(true)
            .build();
        new ThreatListener(LISTENER).registerListener(context);
        Talsec.start(context, config);
        Log.i(TAG, "Talsec started");
    }
}
JAVA
    fi

    # Patch the App class file: add import + call FreeRaspStarter.start(this) in onCreate()
    cp -p "$app_file" "${app_file}.freerasp_bak"
    PATCHED_FILES+=("$app_file")

    python3 - "$app_file" "$class_pkg" "$app_lang" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
class_pkg = sys.argv[2]
lang = sys.argv[3]
src = p.read_text()

# Idempotent guard
if "FreeRaspStarter.start" in src:
    sys.exit(0)

# Insert import: place after the last existing import line, or after the package line
import_line = (
    f"import {class_pkg}.FreeRaspStarter\n"
    if lang == "kotlin"
    else f"import {class_pkg}.FreeRaspStarter;\n"
)

# Find last import
last_import_end = 0
for m in re.finditer(r'^\s*import\s+[^\n]+\n', src, re.MULTILINE):
    last_import_end = m.end()
if last_import_end > 0:
    src = src[:last_import_end] + import_line + src[last_import_end:]
else:
    # Insert after package line
    pm = re.search(r'^\s*package\s+[^\n]+\n', src, re.MULTILINE)
    if pm:
        src = src[:pm.end()] + "\n" + import_line + src[pm.end():]
    else:
        src = import_line + src

# Insert FreeRaspStarter.start(this) at the END of onCreate() body
# Look for the onCreate method signature and its body
if lang == "kotlin":
    # Kotlin: `override fun onCreate() { ... }`, body may span multiple lines
    sig_re = re.compile(r'(override\s+fun\s+onCreate\s*\([^)]*\)\s*\{)')
else:
    # Java: `public void onCreate() { ... }`, @Override optional
    sig_re = re.compile(r'((?:@Override\s+)?public\s+void\s+onCreate\s*\([^)]*\)\s*\{)')

m = sig_re.search(src)
call_line = "        FreeRaspStarter.start(this)\n" if lang == "kotlin" \
    else "        FreeRaspStarter.start(this);\n"

if m:
    # Find the matching closing brace for this onCreate() body
    start = m.end()  # position right after opening `{`
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == '{': depth += 1
        elif c == '}': depth -= 1
        i += 1
    if depth == 0:
        close_pos = i - 1  # position of closing `}`
        src = src[:close_pos] + call_line + src[close_pos:]
    else:
        raise RuntimeError("could not find matching close brace for onCreate()")
else:
    # No onCreate(): add one at end of class body (before final `}`)
    if lang == "kotlin":
        method = (
            "\n    override fun onCreate() {\n"
            "        super.onCreate()\n"
            "        FreeRaspStarter.start(this)\n"
            "    }\n"
        )
    else:
        method = (
            "\n    @Override\n"
            "    public void onCreate() {\n"
            "        super.onCreate();\n"
            "        FreeRaspStarter.start(this);\n"
            "    }\n"
        )
    # Insert before the final `}` of the file (assumed to be class close)
    last_brace = src.rfind('}')
    if last_brace < 0:
        raise RuntimeError("no closing brace found in App class file")
    src = src[:last_brace] + method + src[last_brace:]

p.write_text(src)
PY
    return 0
}

gradle_file_for() {
    local repo="$1" module="$2"
    for ext in build.gradle.kts build.gradle; do
        [ -f "$DATASET_DIR/$repo/$module/$ext" ] && { echo "$DATASET_DIR/$repo/$module/$ext"; return 0; }
    done
    return 1
}

settings_file_for() {
    local repo="$1"
    for ext in settings.gradle.kts settings.gradle; do
        [ -f "$DATASET_DIR/$repo/$ext" ] && { echo "$DATASET_DIR/$repo/$ext"; return 0; }
    done
    return 1
}

pack_one() {
    local slug="$1"
    local spec="${COHORT[$slug]:-}"
    [ -z "$spec" ] && { log "SKIP $slug — not in cohort map"; return 2; }
    IFS='|' read -r repo module task flavor pkg <<<"$spec"

    # 2026-07-25: FREERASP_BUILD_TYPE=debug swap so the freeRasp variant
    # can join the all-debug cohort (matched-pair with baseline/bangcle/allatori
    # all built as debug). Mirrors the pattern in pack_baseline_release.sh and
    # the Allatori packer: swaps Release→Debug in the gradle task and directs
    # APK lookup + output naming to the debug output tree.
    local build_type_dir="release"
    local out_suffix=""
    if [ "${FREERASP_BUILD_TYPE:-release}" = "debug" ]; then
        build_type_dir="debug"
        out_suffix="_debug"
        task="${task/Release/Debug}"
    fi

    local repo_dir="$DATASET_DIR/$repo"
    [ ! -d "$repo_dir" ] && { log "FAIL $slug — repo missing"; return 1; }
    [ ! -x "$repo_dir/gradlew" ] && { log "FAIL $slug — gradlew missing"; return 1; }

    select_jdk_for_app "$slug"
    log "---- $slug ($repo / :$module / $task, pkg=$pkg) ----"
    log_pre "    JAVA_HOME=$JAVA_HOME"

    local gf; gf="$(gradle_file_for "$repo" "$module")" || { log "FAIL $slug — build.gradle{,.kts} not found"; return 1; }
    local sf; sf="$(settings_file_for "$repo")" || { log "FAIL $slug — settings.gradle{,.kts} not found"; return 1; }
    local mf="$DATASET_DIR/$repo/$module/src/main/AndroidManifest.xml"
    [ ! -f "$mf" ] && { log "FAIL $slug — AndroidManifest.xml missing"; return 1; }

    # Detect if the app already declares a custom Application subclass in the manifest.
    # If so, replacing android:name with our .FreeRaspApplication would lose any
    # @HiltAndroidApp / KAPT-generated setup on the original class. Skip and document.
    local existing_app_name
    existing_app_name=$(python3 - "$mf" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r'<application\s[^>]*android:name="([^"]+)"', src)
print(m.group(1) if m else "")
PY
)
    log_pre "    settings=$sf"
    log_pre "    gradle=$gf"
    log_pre "    manifest=$mf"

    # Clean any prior artifacts under this app's build dir
    rm -rf "$repo_dir/$module/build" 2>/dev/null

    patch_gradle_settings "$sf"    || { log "FAIL $slug — patch settings"; return 1; }
    patch_root_gradle "$repo"       || { log "FAIL $slug — patch root gradle"; return 1; }
    patch_module_gradle "$gf"       || { log "FAIL $slug — patch module gradle"; return 1; }
    disable_coverage_gradle "$gf"   || true

    if [ -n "$existing_app_name" ] && [ "$existing_app_name" != ".FreeRaspApplication" ]; then
        # HILT-PATTERN path: in-place patch of the existing App class + helper file.
        # Do NOT patch the manifest (keep the existing android:name intact).
        # We still need the permission fixes from patch_manifest, but skip the
        # <application> android:name rewrite. Call a manifest-perms-only helper.
        log_pre "    hilt-pattern: preserving existing Application class ($existing_app_name)"
        patch_manifest_perms_only "$mf" || { log "FAIL $slug — patch manifest perms"; return 1; }
        hilt_pattern_patch "$DATASET_DIR/$repo/$module" "$pkg" "$existing_app_name" "$mf" "$gf" \
            || { log "FAIL $slug — hilt pattern patch"; return 1; }
        log_pre "    hilt-pattern patch OK — Talsec started from ${existing_app_name}.onCreate() via FreeRaspStarter"
    else
        patch_manifest "$mf"        || { log "FAIL $slug — patch manifest"; return 1; }
        local kt; kt=$(write_application_kt "$DATASET_DIR/$repo/$module" "$pkg")
        log_pre "    wrote $kt"
    fi

    local extra_flags="${GRADLE_EXTRA_FLAGS[$slug]:-}"
    # Same reason as pack_baseline_release.sh: the Allatori packer mutates javac/
    # and kotlin-classes/ out of band, so any incremental artifact left in this
    # tree by an Allatori build is unsound. medtimer's baseline was silently
    # corrupted that way on 2026-08-03 and crashed with a NoSuchMethodError.
    # PACK_FREERASP_NO_CLEAN=1 opts out.
    if [ "${PACK_FREERASP_NO_CLEAN:-0}" != "1" ] && [ -d "$repo_dir/$module/build" ]; then
        log_pre "    wiping $module/build (another variant's intermediates are not a safe base)"
        rm -rf "$repo_dir/$module/build"
    fi

    local gradle_log="$LOG_DIR/pack_freerasp_${slug}_${TS}.gradle.log"
    log_pre "    running: ./gradlew :$module:$task ${extra_flags}  (log: $gradle_log)"
    (
        cd "$repo_dir" && ./gradlew --no-daemon --no-configuration-cache -q $extra_flags ":$module:$task"
    ) >"$gradle_log" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL $slug — gradle exit $rc; tail:"
        tail -30 "$gradle_log" | sed 's/^/        /' | tee -a "$LOG_FILE" >/dev/null
        return 1
    fi

    local apk_root="$repo_dir/$module/build/outputs/apk"
    # Prefer universal/arm64 splits: our target Pixels are all arm64.
    # x86/x86_64 splits will INSTALL_FAILED_NO_MATCHING_ABIS on Pixel devices.
    pick_apk_from_dir() {
        local dir="$1"
        local found
        # Only exclude x86 (won't install on Pixel arm64 devices).
        # DO NOT exclude -debug or _debug: for FREERASP_BUILD_TYPE=debug,
        # split APKs are named like app-arm64-v8a-debug.apk, and we WANT them.
        for pat in '*universal*.apk' '*arm64-v8a*.apk' '*arm64*.apk' '*armeabi-v7a*.apk' '*armeabi*.apk' '*.apk'; do
            found=$(find "$dir" -maxdepth 2 -name "$pat" 2>/dev/null \
                | grep -vE '(x86)' | head -1)
            [ -n "$found" ] && { echo "$found"; return 0; }
        done
        return 1
    }
    local apk=""
    if [ -n "$flavor" ]; then
        local flavor_lc="$(echo "$flavor" | tr '[:upper:]' '[:lower:]')"
        for fd in "$flavor" "$flavor_lc"; do
            [ -d "$apk_root/$fd/$build_type_dir" ] || continue
            apk=$(pick_apk_from_dir "$apk_root/$fd/$build_type_dir")
            [ -n "$apk" ] && break
        done
    fi
    [ -z "$apk" ] && apk=$(pick_apk_from_dir "$apk_root/$build_type_dir")
    if [ -z "$apk" ]; then
        # Fallback: search whole tree for a build_type_dir APK
        apk=$(find "$apk_root" -name "*${build_type_dir}*.apk" 2>/dev/null | grep -vE '(x86)' | head -1)
    fi
    [ -z "$apk" ] && { log "FAIL $slug — no $build_type_dir APK produced"; return 1; }
    log_pre "    found APK: $apk"

    local aligned="$LOG_DIR/.pack_freerasp_${slug}_${TS}.aligned.apk"
    "$ZIPALIGN" -p -f 4 "$apk" "$aligned" >>"$LOG_FILE" 2>&1 \
        || { log "FAIL $slug — zipalign"; rm -f "$aligned"; return 1; }

    local out_apk="$OUT_DIR/${slug}_freerasp${out_suffix}.signed.apk"
    "$APKSIGNER" sign \
        --ks "$KEYSTORE" \
        --ks-key-alias "$KEY_ALIAS" \
        --ks-pass "pass:$KEY_PASS" \
        --key-pass "pass:$KEY_PASS" \
        --out "$out_apk" \
        "$aligned" >>"$LOG_FILE" 2>&1
    local sign_rc=$?
    rm -f "$aligned"
    [ $sign_rc -ne 0 ] && { log "FAIL $slug — apksigner (rc=$sign_rc)"; return 1; }
    "$APKSIGNER" verify "$out_apk" >>"$LOG_FILE" 2>&1 \
        || { log "FAIL $slug — apksigner verify"; return 1; }

    # ---- NSC patch, same as pack_baseline_release.sh and the Allatori packer.
    #
    # This step was MISSING here, and it cost real data. Android 24+ ignores
    # user-installed CAs unless the app opts in via a network-security-config,
    # so pdfviewer_freerasp_debug (the only Tier-A app's freeRasp build)
    # silently refused mitmproxy's certificate:
    #
    #   Client TLS handshake failed. The client does not trust the proxy's
    #   certificate for css4.pub
    #
    # The cell still ran, the app still worked, and the only symptom was that
    # the deterministic replay served nothing. Every other pdfviewer variant
    # had NSC, so its freeRasp cells were the only ones hitting the live
    # internet, precisely the confound the replay exists to remove. Found
    # 2026-08-01 by the post-cell replay-served check.
    #
    # SKIP_NSC_PATCH=1 opts out. Apps in the NO_NSC_PATCH carve-out (documenter:
    # apktool re-inject corrupts its multi-DEX + multi-ABI repack) are skipped.
    if [ "${SKIP_NSC_PATCH:-0}" = "1" ]; then
        log_pre "    NSC patch SKIPPED (SKIP_NSC_PATCH=1)"
    elif [ "${NO_NSC_PATCH[$slug]:-0}" = "1" ]; then
        log_pre "    NSC patch SKIPPED — $slug in NO_NSC_PATCH carve-out"
    else
        local nsc_out="$LOG_DIR/.pack_freerasp_${slug}_${TS}.nscpatched.apk"
        if "$WORKSPACE/tools/patch_apk_nsc.sh" "$out_apk" "$nsc_out" >>"$LOG_FILE" 2>&1; then
            mv -f "$nsc_out" "$out_apk"
            log_pre "    NSC patch applied (mitmproxy interception now viable)"
        else
            log "FAIL $slug — post-build NSC patch failed"
            rm -f "$nsc_out"
            return 1
        fi
        local nsc_check
        nsc_check=$("$BT_DIR/aapt" dump xmltree "$out_apk" AndroidManifest.xml 2>/dev/null | grep -c "networkSecurityConfig")
        if [ "$nsc_check" -eq 0 ]; then
            log "FAIL $slug — NSC missing after patch (aapt says 0 references)"
            return 1
        fi
        # apktool round-trips the DEX. Confirm the hook this whole pipeline
        # exists to install actually survived the repack.
        local hook_check
        hook_check=$(python3 - "$out_apk" <<'PYHOOK'
import re, sys, zipfile
pat = re.compile(rb"FreeRaspApplication|FreeRaspStarter")
with zipfile.ZipFile(sys.argv[1]) as z:
    blob = b"".join(z.read(n) for n in sorted(n for n in z.namelist() if n.endswith(".dex")))
print(len(pat.findall(blob)))
PYHOOK
        ) || hook_check=0
        if [ "${hook_check:-0}" -eq 0 ]; then
            log "FAIL $slug — freeRasp hook GONE after NSC repack (apktool corrupted the DEX)"
            return 1
        fi
        log_pre "    freeRasp hook survived NSC repack (${hook_check} references)"
    fi

    local size_kb=$(du -k "$out_apk" | awk '{print $1}')
    log "OK   $slug → $out_apk (${size_kb} KB, NSC verified, hook intact)"
    return 0
}

log_pre "================================================================"
log_pre "pack_freerasp.sh — $TS"
log_pre "JAVA_HOME_default=$JDK17  JDK21=${JDK21:-none}"
log_pre "build-tools=$BT_DIR"
log_pre "out_dir=$OUT_DIR"
log_pre "log=$LOG_FILE"
log_pre "================================================================"

requested=()
if [ $# -eq 0 ]; then
    requested=("${SLUGS_ORDER[@]}")
else
    for a in "$@"; do
        [ -z "${COHORT[$a]:-}" ] && { log "FATAL — unknown slug: $a"; exit 2; }
        requested+=("$a")
    done
fi

ok=0; fail=0
for slug in "${requested[@]}"; do
    if pack_one "$slug"; then ok=$((ok+1)); else fail=$((fail+1)); fi
done

log_pre "================================================================"
log "DONE — ok=$ok fail=$fail of ${#requested[@]}"
log_pre "================================================================"
exit $fail
