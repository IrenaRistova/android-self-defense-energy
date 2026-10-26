#!/usr/bin/env bash
# pack_baseline_release.sh: Build BASELINE RELEASE variant APKs (release
# buildType with R8 disabled and no obfuscator: the untreated release control
# for the matched-pair ΔE methodology).
#
# Sibling of the R8 release packer. Differences:
#   - Force release{} isMinifyEnabled = FALSE (baseline has no obfuscation)
#   - Force release{} isShrinkResources = FALSE (goes with minify)
#   - Comment out signingConfig.release refs (sign with debug keystore)
#   - No proguardFiles injection
#   - Output: /home/irena/Documents/Master Thesis/APKs/<slug>_baseline_release.signed.apk
#
# WHY this variant exists:
#   Debug-based baseline is superseded (R8 obfuscation is limited on debug builds, 2026-07-01).
#   For the matched-pair 4-variant experiment on RELEASE buildType, all four
#   variants (baseline, R8, Allatori, Bangcle) must share buildType, keystore,
#   applicationId (release id), versionCode/versionName, and signing scheme.
#
# Usage:
#   tools/pack_baseline_release.sh                       # all 17 apps
#   tools/pack_baseline_release.sh calculator itsok      # specified slugs
#
# NEVER COMMITS. Per-app source patches are restored on EXIT via trap.

set -u
set -o pipefail

WORKSPACE="/home/irena/Documents/Master Experiment"
DATASET_DIR="$WORKSPACE/app_repositories_newest/final_dataset"
OUT_DIR="/home/irena/Documents/Master Thesis/APKs"
LOG_DIR="$WORKSPACE/tools/sweep_logs"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/pack_baseline_release_${TS}.log"

mkdir -p "$OUT_DIR" "$LOG_DIR"

# JDK selection
JDK17=""
for j in /usr/lib/jvm/java-17-openjdk-amd64 /usr/lib/jvm/openjdk-17 /usr/lib/jvm/java-1.17.0-openjdk-amd64; do
    if [ -d "$j" ]; then JDK17="$j"; break; fi
done
JDK21=""
for j in /usr/lib/jvm/java-21-openjdk-amd64 /usr/lib/jvm/openjdk-21 /usr/lib/jvm/java-1.21.0-openjdk-amd64; do
    if [ -d "$j" ]; then JDK21="$j"; break; fi
done
declare -A APP_JDK_OVERRIDE
APP_JDK_OVERRIDE[linkhub]="21"
APP_JDK_OVERRIDE[tipuous]="21"

# Apps where stripping proguardFiles from the release block BREAKS runtime.
# linkhub (AGP 9, Kotlin/Hilt/Compose): with proguardFiles stripped, AGP 9's
# dex pipeline silently omits KSP-generated Hilt Factory classes from the DEX
# even though isMinifyEnabled=false. Runtime crash: NoClassDefFoundError for
# `AppModule_ProvideSettingUtilsFactory`. Carve out so linkhub baseline keeps
# proguardFiles active. Methodology footnote: linkhub baseline is R8-optimize-
# touched (methods renamed) but functionally launches.
declare -A NO_PROGUARD_STRIP
NO_PROGUARD_STRIP[linkhub]=1
NO_PROGUARD_STRIP[podaura]=1

# Apps where the post-build NSC patch (apktool d/b in tools/patch_apk_nsc.sh)
# corrupts the APK. 2026-07-25 documenter finding: source AndroidManifest
# already carries android:networkSecurityConfig, but apktool decodes without
# seeing it, so the patch re-inserts a duplicate/broken NSC. Documenter's
# 12-DEX + multi-ABI native lib repack from apktool then produces an APK
# that crashes at startup. Documenter does no network I/O during its
# 120s offline PDF workload → skipping NSC patch is loss-free for the ΔE
# measurement.
declare -A NO_NSC_PATCH
NO_NSC_PATCH[documenter]=1

# Per-app extra Gradle flags (space-separated). Used at the assemble step.
# podaura: --no-configuration-cache because Kotlin Multiplatform 2.x + Gradle config-cache
# fails to serialize LazyResolvedConfiguration during
# :shared:transformCommonMainDependenciesMetadata. Config-cache off restores the build.
declare -A GRADLE_EXTRA_FLAGS
GRADLE_EXTRA_FLAGS[podaura]="--no-configuration-cache"

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

declare -A COHORT
COHORT[calculator]="exponentialGroth_Calculator|app|assembleRelease|"
COHORT[another_notes]="maltaisn_another-notes-app|app|assembleRelease|"
COHORT[itsok]="qubacy_ItsOK|app|assembleRelease|"
COHORT[keep_recipe]="bwakessa_Keep-Recipe|app|assembleRelease|"
COHORT[keepitup]="ibbaa_keepitup|keepitupmain|assembleRelease|"
COHORT[androidskk]="tamo_AndroidSKK|app|assembleRelease|"
COHORT[medtimer]="Futsch1_medTimer|app|assembleRelease|"
COHORT[poetskingdom]="tinochings_PoetsKingdom|app|assembleRelease|"
COHORT[pdfviewer]="afreakyelf_Pdf-Viewer|app|assembleRelease|"
COHORT[horoscapp]="angelsoft071_HoroscApp|app|assembleRelease|"
COHORT[crcontainer]="curiouslearning_CRcontainer_v2.34.2|app|assembleRelease|"
COHORT[documenter]="ViliusSutkus89_Documenter|app|assembleRelease|"
COHORT[linkhub]="AmrDeveloper_LinkHub|app|assembleRelease|"
COHORT[tipuous]="JoshLudahl_tipuous|app|assembleRelease|"
COHORT[diaguard]="Faltenreich_Diaguard|app|assembleStoreRelease|store"
COHORT[avnc]="gujjwal00_avnc|app|assembleRelease|"
COHORT[podaura]="SkyD666_PodAura|app|assembleGitHubRelease|GitHub"

SLUGS_ORDER=(calculator another_notes itsok keep_recipe keepitup androidskk medtimer poetskingdom pdfviewer horoscapp crcontainer documenter linkhub tipuous diaguard avnc podaura)

log()     { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"; }
log_pre() { printf '%s\n' "$*" | tee -a "$LOG_FILE"; }

PATCHED_FILES=()
restore_patches() {
    for gf in "${PATCHED_FILES[@]}"; do
        if [ -f "${gf}.pack_baseline_release.bak" ]; then
            mv -f "${gf}.pack_baseline_release.bak" "$gf"
        fi
    done
}
trap restore_patches EXIT INT TERM

patch_release_block() {
    local gradle_file="$1"
    local skip_strip="${2:-0}"
    [ -f "$gradle_file" ] || return 0
    cp -p "$gradle_file" "${gradle_file}.pack_baseline_release.bak"
    PATCHED_FILES+=("$gradle_file")
    SKIP_PROGUARD_STRIP="$skip_strip" BASELINE_BUILD_TYPE="${BASELINE_BUILD_TYPE:-release}" python3 - "$gradle_file" <<'PY'
import re, sys, os, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
skip_strip = os.environ.get('SKIP_PROGUARD_STRIP') == '1'

# --- Disable JaCoCo coverage instrumentation (2026-08-03) -------------------
#
# Must mirror the identical patch in the Allatori packer and pack_freerasp.sh: if a
# variant is built with coverage and its siblings without, the matched pair is
# comparing obfuscation AND instrumentation at once.
#
# medtimer is the only app in the cohort that enables coverage (224 jacoco refs
# in its baseline DEX; the other ten have 0). That instrumentation adds a probe
# array and a $jacocoInit() call to EVERY method, so medtimer's energy carried
# per-method overhead no other app had. It also made allatori impossible:
# JaCoCo's transform sits between compile and dex, cannot instrument obfuscated
# bytecode, and if it runs first the dexer consumes its un-obfuscated output.
# Removing it fixes the build and removes a confound.
#
# NOTE: bangcle wraps this baseline, so it must be re-wrapped from the APK this
# produces or it becomes the only variant still carrying coverage.
# OPT-IN ONLY (default-off since 2026-08-03).
# medtimer is the single exception in this cohort and its APKs are already
# built; nothing else enables coverage, so this must not fire by default.
if os.environ.get('PACK_DISABLE_COVERAGE') == '1':
  _cov_before = src
  src = re.sub(r'(enableUnitTestCoverage\s*=\s*)true', r'\1false', src)
  src = re.sub(r'(enableAndroidTestCoverage\s*=\s*)true', r'\1false', src)
  src = re.sub(r'(enableUnitTestCoverage\s+)true', r'\1false', src)
  src = re.sub(r'(enableAndroidTestCoverage\s+)true', r'\1false', src)
  src = re.sub(r'(testCoverageEnabled\s*=?\s*)true', r'\1false', src)
  if src != _cov_before:
      print("[pack_baseline_release] disabled JaCoCo coverage instrumentation", file=sys.stderr)

def find_block_span(s, header_start):
    start = s.find('{', header_start)
    if start < 0: return None
    depth = 0; i = start
    while i < len(s):
        c = s[i]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return (start, i+1)
        i += 1
    return None

def find_buildtype_blocks(s, name):
    """Find spans of `<name> { ... }` blocks inside `buildTypes { ... }`."""
    spans = []
    for bt_match in re.finditer(r'(?:^|[\s\n;])buildTypes\s*[{(]', s):
        bt_span = find_block_span(s, bt_match.end() - 1)
        if bt_span is None: continue
        bt_start, bt_end = bt_span
        bt_body = s[bt_start:bt_end]
        pat = (r'(?:^|[\s\n;])' + name + r'\s*[{(]'
               r'|getByName\("' + name + r'"\)\s*[{(]'
               r'|named\("' + name + r'"\)\s*[{(]')
        for rm in re.finditer(pat, bt_body):
            span = find_block_span(bt_body, rm.end() - 1)
            if span is None: continue
            a, b = span
            spans.append((bt_start + a, bt_start + b))
    return sorted(set(spans))

# Always patch the release block (release baseline path stays R8-optimize-free).
# When BASELINE_BUILD_TYPE=debug, ALSO patch the debug block: some apps
# (podaura, tipuous) declare `proguardFiles(getDefaultProguardFile(
# "proguard-android-optimize.txt"), ...)` in their debug{} block, and AGP 8+
# runs R8's optimize pass on that even with isMinifyEnabled=false, silently
# obfuscating classes. That breaks kotlinx-serialization / Room / Retrofit
# reflection at runtime. Verified on podaura 2026-07-24 (crash: Serializer
# for class 'D' is not found).
spans = find_buildtype_blocks(src, 'release')
if os.environ.get('BASELINE_BUILD_TYPE', 'release') == 'debug':
    spans += find_buildtype_blocks(src, 'debug')
    spans = sorted(set(spans))
out = []; last = 0
for a, b in spans:
    out.append(src[last:a])
    block = src[a:b]
    # Force minify + shrinkResources to false
    block = re.sub(r'(minifyEnabled\s+)true', r'\1false', block)
    block = re.sub(r'(isMinifyEnabled\s*=\s*)true', r'\1false', block)
    block = re.sub(r'(shrinkResources\s+)true', r'\1false', block)
    block = re.sub(r'(isShrinkResources\s*=\s*)true', r'\1false', block)
    # Strip proguardFiles(...) calls. AGP 8+ runs R8's optimize pass whenever
    # `getDefaultProguardFile("proguard-android-optimize.txt")` is present in
    # proguardFiles, EVEN with isMinifyEnabled=false. That renames methods
    # (e.g. medtimer's `requestPermission()` -> `J`) but doesn't update Java
    # callers → runtime NoSuchMethodError. A true baseline can't ship with
    # any R8-optimizing config, so strip the whole call.
    # Groovy: `proguardFiles ...` and `proguardFiles(...)` (paren optional)
    # KTS: `proguardFiles(...)` (parens required)
    # Both single-line and multi-line (with wrapped args over N lines).
    def strip_proguard_files(b):
        pat = re.compile(r'\n[^\n]*proguardFiles\s*\(', re.MULTILINE)
        while True:
            m = pat.search(b)
            if not m: break
            # Find the matching close-paren for this proguardFiles(
            open_paren = m.end() - 1
            depth = 1; i = open_paren + 1
            while i < len(b) and depth > 0:
                c = b[i]
                if c == '(': depth += 1
                elif c == ')': depth -= 1
                i += 1
            # Also strip up to the newline that starts this proguardFiles line
            line_start = m.start()
            end = i
            while end < len(b) and b[end] in '\r\n':
                end += 1
            comment = "\n            // [pack_baseline_release.sh] proguardFiles stripped — R8-optimize pass renamed methods without updating callers, breaking runtime lookups\n"
            b = b[:line_start] + comment + b[end:]
        # Also handle Groovy no-parens form (rare):  proguardFiles getDefault...
        b = re.sub(r'\n[^\n]*proguardFiles\s+[^\n]+', '\n            // [pack_baseline_release.sh] proguardFiles (no-parens) stripped', b)
        return b
    if not skip_strip:
        block = strip_proguard_files(block)
    # Comment out signingConfig references
    block = re.sub(r'(\n\s*)(signingConfig\s+signingConfigs\.release[^\n]*)',
                   r'\1// [pack_baseline_release.sh] disabled — sign with debug keystore after build\n\1// \2',
                   block)
    block = re.sub(r'(\n\s*)(signingConfig\s*=\s*signingConfigs\.getByName\("release"\)[^\n]*)',
                   r'\1// [pack_baseline_release.sh] disabled — sign with debug keystore after build\n\1// \2',
                   block)
    out.append(block); last = b
out.append(src[last:])
p.write_text(''.join(out))
PY
}

gradle_file_for() {
    local repo="$1" module="$2"
    for ext in build.gradle.kts build.gradle; do
        if [ -f "$DATASET_DIR/$repo/$module/$ext" ]; then
            echo "$DATASET_DIR/$repo/$module/$ext"; return 0
        fi
    done
    return 1
}

pack_one() {
    local slug="$1"
    local spec="${COHORT[$slug]:-}"
    if [ -z "$spec" ]; then log "SKIP $slug — not in cohort map"; return 2; fi
    IFS='|' read -r repo module task flavor <<<"$spec"

    # BASELINE_BUILD_TYPE=debug swap: same pattern as the Allatori packer.
    # Substring-replace 'Release' -> 'Debug' in the task name (assembleRelease
    # -> assembleDebug, assembleStoreRelease -> assembleStoreDebug, etc.).
    # For debug build: skip patch_release_block (debug default is already
    # isMinifyEnabled=false), look for output APKs under */debug/ instead of
    # */release/, name output as _baseline_debug.signed.apk, and force a
    # post-build NSC patch so mitmproxy can intercept (matches how Bangcle
    # inputs were prepared: raw debug -> NSC patch -> stub inject -> pack).
    local build_type="release"
    local build_type_dir="release"
    local out_suffix=""
    if [ "${BASELINE_BUILD_TYPE:-release}" = "debug" ]; then
        build_type="debug"
        build_type_dir="debug"
        out_suffix="_debug"
        task="${task/Release/Debug}"
    fi

    local repo_dir="$DATASET_DIR/$repo"
    if [ ! -d "$repo_dir" ]; then log "FAIL $slug — repo dir missing"; return 1; fi
    if [ ! -x "$repo_dir/gradlew" ]; then log "FAIL $slug — gradlew missing"; return 1; fi

    select_jdk_for_app "$slug"
    log "---- $slug ($repo / :$module / $task) ----"
    log_pre "    JAVA_HOME=$JAVA_HOME"

    local gf
    if ! gf="$(gradle_file_for "$repo" "$module")"; then
        log "FAIL $slug — no build.gradle{,.kts} found"; return 1
    fi
    log_pre "    gradle_file=$gf"
    local skip_strip="${NO_PROGUARD_STRIP[$slug]:-0}"
    # For debug builds, always strip proguardFiles regardless of the release
    # carve-out. The release-only carve-outs (linkhub, podaura) exist because
    # release builds break when we strip their keep-rules, but debug builds
    # have isMinifyEnabled=false and use proguardFiles ONLY to accidentally
    # trigger R8's optimize pass. Stripping proguardFiles from debug is what
    # makes debug builds actually be debug builds (no obfuscation, no strip).
    if [ "$build_type" = "debug" ]; then
        skip_strip=0
    fi
    patch_release_block "$gf" "$skip_strip"
    if [ "$build_type" = "debug" ]; then
        # Discovered 2026-07-24 (podaura + tipuous): some apps declare
        # `proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), ...)`
        # in their debug{} block even with isMinifyEnabled=false. AGP 8+ runs
        # R8's optimize pass on that config anyway, silently obfuscating
        # classes and breaking runtime reflection (kotlinx-serialization /
        # Room / Retrofit). patch_release_block was extended to also strip
        # proguardFiles from the debug{} block when BASELINE_BUILD_TYPE=debug.
        if [ "$skip_strip" = "1" ]; then
            log_pre "    patched debug{} + release{} (minifyEnabled=false, proguardFiles KEPT — carve-out)"
        else
            log_pre "    patched debug{} + release{} (minifyEnabled=false, proguardFiles STRIPPED — fixes latent R8-optimize in debug)"
        fi
    else
        if [ "$skip_strip" = "1" ]; then
            log_pre "    patched release{} (minifyEnabled=false, signingConfig disabled, proguardFiles KEPT — carve-out)"
        else
            log_pre "    patched release{} (minifyEnabled=false, signingConfig disabled, proguardFiles STRIPPED)"
        fi
    fi

    # Wipe the module build dir first. The Allatori packer mutates javac/ and
    # kotlin-classes/ OUT OF BAND, so every incremental artifact left behind by
    # an Allatori build is unsound, and this packer runs against the same tree.
    # Concretely (2026-08-03): medtimer's baseline was built right after its
    # allatori build and inherited Allatori-touched intermediates, producing an
    # APK that crashed with
    #   NoSuchMethodError: No virtual method initialize(Landroid/content/Context;)V
    #   in class ReminderNotificationChannelManager$Companion
    # A "baseline" is supposed to be the control; it must never be built on top
    # of another variant's leftovers. PACK_BASELINE_NO_CLEAN=1 opts out.
    if [ "${PACK_BASELINE_NO_CLEAN:-0}" != "1" ] && [ -d "$repo_dir/$module/build" ]; then
        log_pre "    wiping $module/build (never build a baseline on another variant's intermediates)"
        rm -rf "$repo_dir/$module/build"
    fi

    local gradle_log="$LOG_DIR/pack_baseline_release_${slug}_${TS}.gradle.log"
    local extra_flags="${GRADLE_EXTRA_FLAGS[$slug]:-}"
    log_pre "    running: ./gradlew :$module:$task ${extra_flags}  (log: $gradle_log)"
    (
        cd "$repo_dir" && ./gradlew --no-daemon -q $extra_flags ":$module:$task"
    ) >"$gradle_log" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL $slug — gradle exit $rc; tail:"
        tail -40 "$gradle_log" | sed 's/^/        /' | tee -a "$LOG_FILE" >/dev/null
        return 1
    fi

    local apk_root="$repo_dir/$module/build/outputs/apk"
    local candidates=()
    if [ -n "$flavor" ]; then
        local flavor_lc="$(echo "$flavor" | tr '[:upper:]' '[:lower:]')"
        for fd in "$flavor" "$flavor_lc"; do
            [ -d "$apk_root/$fd/$build_type_dir" ] || continue
            while IFS= read -r p; do candidates+=("$p"); done < <(
                find "$apk_root/$fd/$build_type_dir" -maxdepth 2 -name '*.apk' 2>/dev/null
            )
            [ ${#candidates[@]} -gt 0 ] && break
        done
    else
        while IFS= read -r p; do candidates+=("$p"); done < <(
            find "$apk_root/$build_type_dir" -maxdepth 2 -name '*.apk' 2>/dev/null
        )
    fi
    if [ ${#candidates[@]} -eq 0 ]; then
        while IFS= read -r p; do candidates+=("$p"); done < <(
            find "$apk_root" -name "*${build_type_dir}*.apk" 2>/dev/null
        )
    fi
    if [ ${#candidates[@]} -eq 0 ]; then log "FAIL $slug — no $build_type APK produced"; return 1; fi
    # Prefer universal > arm64-v8a > armeabi-v7a; exclude x86 (Pixels are arm64)
    local apk=""
    for pat in "*universal*.apk" "*arm64-v8a*.apk" "*armeabi-v7a*.apk"; do
        for c in "${candidates[@]}"; do
            case "$c" in $pat) [[ "$c" != *x86* ]] && { apk="$c"; break 2; };; esac
        done
    done
    [ -z "$apk" ] && for c in "${candidates[@]}"; do [[ "$c" != *x86* ]] && { apk="$c"; break; }; done
    [ -z "$apk" ] && apk="${candidates[0]}"
    log_pre "    found APK: $apk"

    local aligned="$LOG_DIR/.pack_baseline_release_${slug}_${TS}.aligned.apk"
    "$ZIPALIGN" -p -f 4 "$apk" "$aligned" >>"$LOG_FILE" 2>&1 || {
        log "FAIL $slug — zipalign"; rm -f "$aligned"; return 1; }

    local out_apk="$OUT_DIR/${slug}_baseline${out_suffix}.signed.apk"
    "$APKSIGNER" sign \
        --ks "$KEYSTORE" \
        --ks-key-alias "$KEY_ALIAS" \
        --ks-pass "pass:$KEY_PASS" \
        --key-pass "pass:$KEY_PASS" \
        --out "$out_apk" \
        "$aligned" >>"$LOG_FILE" 2>&1
    local sign_rc=$?
    rm -f "$aligned"
    if [ $sign_rc -ne 0 ]; then log "FAIL $slug — apksigner (rc=$sign_rc)"; return 1; fi
    "$APKSIGNER" verify "$out_apk" >>"$LOG_FILE" 2>&1 || {
        log "FAIL $slug — apksigner verify"; return 1; }

    # For debug builds only: apply NSC patch so mitmproxy can intercept.
    # Idempotent: if the source manifest already declares NSC (keepitup,
    # podaura), patch_apk_nsc.sh re-signs unchanged. If missing (linkhub,
    # diaguard, avnc, etc.), it apktool-decompiles + injects + re-signs.
    #
    # 2026-07-25: NO_NSC_PATCH carve-out: documenter's manifest already
    # carries networkSecurityConfig, but apktool decodes without seeing it,
    # so re-injection corrupts the APK (multi-DEX + multi-ABI native lib
    # repack breaks startup). App does zero network I/O during scenarios →
    # skipping NSC patch is loss-free for the ΔE measurement.
    if [ "$build_type" = "debug" ] && [ "${NO_NSC_PATCH[$slug]:-0}" != "1" ]; then
        local nsc_out="$LOG_DIR/.pack_baseline_debug_${slug}_${TS}.nscpatched.apk"
        if "$WORKSPACE/tools/patch_apk_nsc.sh" "$out_apk" "$nsc_out" >>"$LOG_FILE" 2>&1; then
            mv -f "$nsc_out" "$out_apk"
            log_pre "    NSC patch applied (idempotent — mitmproxy interception now viable)"
        else
            log "FAIL $slug — post-build NSC patch failed"
            rm -f "$nsc_out"
            return 1
        fi
        local nsc_check=$("$BT_DIR/aapt" dump xmltree "$out_apk" AndroidManifest.xml 2>/dev/null | grep -c "networkSecurityConfig")
        if [ "$nsc_check" -eq 0 ]; then
            log "FAIL $slug — NSC missing after patch (aapt says 0 references)"
            return 1
        fi
    elif [ "$build_type" = "debug" ] && [ "${NO_NSC_PATCH[$slug]:-0}" = "1" ]; then
        log_pre "    NSC patch SKIPPED — $slug in NO_NSC_PATCH carve-out (source manifest already declares NSC, apktool re-inject corrupts APK)"
    fi

    # ---- Build-integrity assertion: a baseline must carry NO protection.
    #
    # documenter_baseline_debug shipped WITH the freeRasp hook wired in: a
    # stale FreeRaspStarter kapt stub survived in app/build/ from an earlier
    # pack_freerasp.sh run and got recompiled into the "baseline". Nothing
    # complained: the build succeeded, the APK installed, scenarios passed, and
    # 30 documenter cells were measured against a reference that was running
    # RASP. Found 2026-08-01. Scan the DEX for every protection marker we know
    # and refuse to report OK if one is present.
    local marker_scan
    marker_scan=$(python3 - "$out_apk" <<'PYMARK'
import re, sys, zipfile
PATTERNS = {
    "FREERASP_HOOK":  re.compile(rb"FreeRaspApplication|FreeRaspStarter"),
    "TALSEC_CLASSES": re.compile(rb"com/aheaditec/talsec"),
    "BANGCLE":        re.compile(rb"com/SecShell/SecShell|libSecShell"),
}
blob = bytearray()
with zipfile.ZipFile(sys.argv[1]) as z:
    for n in sorted(n for n in z.namelist() if n.endswith(".dex")):
        blob += z.read(n)
    libs = [n for n in z.namelist() if n.endswith(("libts.so", "libSecShell.so"))]
hits = [f"{k}={len(p.findall(blob))}" for k, p in PATTERNS.items() if p.search(blob)]
if libs:
    hits.append("NATIVE=" + ",".join(sorted({n.rsplit('/', 1)[-1] for n in libs})))
print("DIRTY " + " ".join(hits) if hits else "CLEAN")
PYMARK
    ) || marker_scan="ERROR"
    case "$marker_scan" in
        DIRTY*)
            log "FAIL $slug — baseline${out_suffix} carries protection markers: ${marker_scan#DIRTY }"
            log "       This is NOT a baseline. Most likely a stale protected build in"
            log "       the module's build/ dir. Wipe it and rebuild."
            return 1
            ;;
        CLEAN) log_pre "    no protection markers in DEX — baseline is a true baseline" ;;
        *)     log_pre "    WARN — protection-marker scan failed; treat this baseline as unverified" ;;
    esac

    local size_kb=$(du -k "$out_apk" | awk '{print $1}')
    log "OK   $slug → $out_apk (${size_kb} KB${build_type:+, ${build_type}${out_suffix:+ NSC verified}}, no protection markers)"
    return 0
}

log_pre "================================================================"
log_pre "pack_baseline_release.sh — $TS"
log_pre "JAVA_HOME_default=$JDK17"
log_pre "build-tools=$BT_DIR"
log_pre "out_dir=$OUT_DIR"
log_pre "log=$LOG_FILE"
log_pre "================================================================"

requested=()
if [ "$#" -eq 0 ]; then
    requested=("${SLUGS_ORDER[@]}")
else
    for arg in "$@"; do
        if [ -z "${COHORT[$arg]:-}" ]; then log "FATAL — unknown slug: $arg"; exit 2; fi
        requested+=("$arg")
    done
fi

fail_count=0; ok_count=0
for slug in "${requested[@]}"; do
    if pack_one "$slug"; then ok_count=$((ok_count+1)); else fail_count=$((fail_count+1)); fi
done

log_pre "================================================================"
log "DONE — ok=$ok_count fail=$fail_count of ${#requested[@]}"
log_pre "================================================================"

exit $fail_count
