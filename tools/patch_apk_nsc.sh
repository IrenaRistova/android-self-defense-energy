#!/usr/bin/env bash
# patch_apk_nsc.sh: patch an Android APK's network_security_config.xml so
# that mitmproxy's user-installed CA is trusted, then re-sign with the
# default Android debug keystore.
#
# Why:
#   Android 7+ (API 24+) apps ignore user-installed CAs by default. For
#   mitmproxy interception during energy measurement, we need each cohort APK
#   to opt in to user CA trust via NSC. Both baseline AND Bangcle variants
#   must run on the same patched APK input so the matched-pair ΔE measurement
#   still isolates the effect of Bangcle protection.
#
# What it does:
#   1. apktool d  -> decompile to working dir
#   2. Insert/replace res/xml/network_security_config.xml with a permissive
#      base-config that trusts system + user CAs
#   3. Ensure AndroidManifest.xml <application> has
#      android:networkSecurityConfig="@xml/network_security_config"
#   4. apktool b  -> rebuild
#   5. zipalign + apksigner with ~/.android/debug.keystore
#   6. Verify the resulting APK declares the patched NSC
#
# Idempotent: if the input APK already trusts user CAs, the script logs that
# and still re-signs (so the output is a fresh, installable copy).
#
# Usage:
#   tools/patch_apk_nsc.sh <input.apk> [<output.apk>]
#
# If output not given, defaults to:
#   _external/patched_apks/<basename>.nscpatched.apk
#
# Exit codes:
#   0  success
#   1  bad args / missing tool
#   2  apktool decompile failed
#   3  apktool rebuild failed
#   4  signing failed
#   5  post-patch verification failed
#
set -euo pipefail
IFS=$'\n\t'

ROOT="/home/irena/Documents/Master Experiment"
APKTOOL="${ROOT}/tools/bin/apktool"
KEYSTORE="${HOME}/.android/debug.keystore"
KEY_ALIAS="androiddebugkey"
KEY_PASS="android"
BUILD_TOOLS_DIR=$(ls -1d "${HOME}/Android/Sdk/build-tools/"* 2>/dev/null | sort -V | tail -1)
APKSIGNER="${BUILD_TOOLS_DIR}/apksigner"
ZIPALIGN="${BUILD_TOOLS_DIR}/zipalign"
AAPT2="${BUILD_TOOLS_DIR}/aapt2"

log() { printf '[patch_apk_nsc] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit "${2:-1}"; }

# --- arg parsing ---
[[ $# -lt 1 ]] && die "usage: $0 <input.apk> [<output.apk>]" 1
INPUT_APK=$(readlink -f "$1")
[[ -f "$INPUT_APK" ]] || die "input APK not found: $1" 1
INPUT_BASENAME=$(basename "$INPUT_APK" .apk)
DEFAULT_OUT="${ROOT}/_external/patched_apks/${INPUT_BASENAME}.nscpatched.apk"
OUTPUT_APK="${2:-$DEFAULT_OUT}"
OUTPUT_APK=$(readlink -f "$(dirname "$OUTPUT_APK")")/$(basename "$OUTPUT_APK")
mkdir -p "$(dirname "$OUTPUT_APK")"

# --- tool checks ---
[[ -x "$APKTOOL" ]] || die "apktool not executable at $APKTOOL" 1
[[ -x "$APKSIGNER" ]] || die "apksigner not found at $APKSIGNER" 1
[[ -x "$ZIPALIGN" ]] || die "zipalign not found at $ZIPALIGN" 1
[[ -x "$AAPT2" ]] || die "aapt2 not found at $AAPT2" 1
[[ -f "$KEYSTORE" ]] || die "debug keystore missing at $KEYSTORE — run a gradle debug build once to create it" 1

WORK=$(mktemp -d -t nscpatch.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

log "input:    $INPUT_APK"
log "output:   $OUTPUT_APK"
log "workdir:  $WORK"
log "keystore: $KEYSTORE"

# --- step 1: decompile ---
log "decompiling with apktool..."
"$APKTOOL" d -f -o "$WORK/src" "$INPUT_APK" >"$WORK/apktool_d.log" 2>&1 \
  || { tail -50 "$WORK/apktool_d.log" >&2; die "apktool decompile failed (see log above)" 2; }

# --- step 2: ensure permissive NSC ---
NSC_DIR="$WORK/src/res/xml"
NSC_FILE="$NSC_DIR/network_security_config.xml"
mkdir -p "$NSC_DIR"

if [[ -f "$NSC_FILE" ]]; then
  if grep -q 'src="user"' "$NSC_FILE"; then
    log "NSC already trusts user CAs — re-signing only (no NSC edit)"
  else
    log "NSC exists but blocks user CAs — overwriting with permissive NSC"
  fi
else
  log "no NSC present — creating permissive NSC"
fi

cat > "$NSC_FILE" <<'NSC'
<?xml version="1.0" encoding="utf-8"?>
<!-- NSC patched by tools/patch_apk_nsc.sh for mitmproxy energy-measurement
     pipeline. Trusts both system and user-installed CAs. Both baseline and
     Bangcle variants of this APK must carry this same NSC for matched-pair
     ΔE measurement to remain valid. -->
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system"/>
            <certificates src="user"/>
        </trust-anchors>
    </base-config>
</network-security-config>
NSC

# --- step 3: ensure AndroidManifest.xml points to it ---
MANIFEST="$WORK/src/AndroidManifest.xml"
[[ -f "$MANIFEST" ]] || die "AndroidManifest.xml missing post-decompile" 2

python3 - <<PY "$MANIFEST"
import re, sys
path = sys.argv[1]
with open(path, 'r', encoding='utf-8') as f:
    txt = f.read()
# does <application have a networkSecurityConfig attr already?
app_re = re.compile(r'(<application\b[^>]*)(/?>)', re.DOTALL)
m = app_re.search(txt)
if not m:
    sys.stderr.write("FATAL: <application> tag not found in manifest\n")
    sys.exit(2)
attrs, close = m.group(1), m.group(2)
if 'android:networkSecurityConfig=' in attrs:
    new_attrs = re.sub(
        r'android:networkSecurityConfig="[^"]*"',
        'android:networkSecurityConfig="@xml/network_security_config"',
        attrs)
    action = "replaced"
else:
    new_attrs = attrs + ' android:networkSecurityConfig="@xml/network_security_config"'
    action = "inserted"
new_txt = txt[:m.start()] + new_attrs + close + txt[m.end():]
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_txt)
sys.stderr.write(f"[patch_apk_nsc] manifest: {action} android:networkSecurityConfig\n")
PY

# --- step 4: rebuild ---
REBUILT="$WORK/rebuilt.apk"
log "rebuilding APK with apktool..."
"$APKTOOL" b -f -o "$REBUILT" "$WORK/src" >"$WORK/apktool_b.log" 2>&1 \
  || { tail -80 "$WORK/apktool_b.log" >&2; die "apktool rebuild failed (see log above)" 3; }

# --- step 5: zipalign + sign ---
ALIGNED="$WORK/aligned.apk"
log "zipaligning..."
"$ZIPALIGN" -p -f 4 "$REBUILT" "$ALIGNED" >"$WORK/zipalign.log" 2>&1 \
  || { cat "$WORK/zipalign.log" >&2; die "zipalign failed" 4; }

log "signing with $KEY_ALIAS..."
"$APKSIGNER" sign \
  --ks "$KEYSTORE" \
  --ks-pass "pass:$KEY_PASS" \
  --key-pass "pass:$KEY_PASS" \
  --ks-key-alias "$KEY_ALIAS" \
  --out "$OUTPUT_APK" \
  "$ALIGNED" >"$WORK/apksigner.log" 2>&1 \
  || { cat "$WORK/apksigner.log" >&2; die "apksigner failed" 4; }

# --- step 6: verify ---
log "verifying patched APK..."
"$APKSIGNER" verify "$OUTPUT_APK" >"$WORK/apksigner_verify.log" 2>&1 \
  || { cat "$WORK/apksigner_verify.log" >&2; die "signed APK failed apksigner verify" 5; }

NSC_DUMP=$("$AAPT2" dump xmltree "$OUTPUT_APK" --file res/xml/network_security_config.xml 2>/dev/null || true)
if ! echo "$NSC_DUMP" | grep -q 'src="user"'; then
  echo "$NSC_DUMP" >&2
  die "patched APK NSC does not list user CAs — post-build went wrong" 5
fi

MANIFEST_DUMP=$("$AAPT2" dump xmltree "$OUTPUT_APK" --file AndroidManifest.xml 2>/dev/null || true)
if ! echo "$MANIFEST_DUMP" | grep -qi 'networkSecurityConfig'; then
  echo "$MANIFEST_DUMP" >&2
  die "patched APK manifest missing networkSecurityConfig attribute" 5
fi

PKG=$("$AAPT2" dump badging "$OUTPUT_APK" 2>/dev/null | grep -oE "package: name='[^']+'" | head -1 | cut -d"'" -f2)
SIZE=$(stat -c%s "$OUTPUT_APK")
log "SUCCESS: $OUTPUT_APK (pkg=$PKG size=$SIZE bytes)"

# Print a one-line summary the caller can grep
printf 'PATCHED %s %s %s %s\n' \
  "$(basename "$INPUT_APK")" \
  "$(basename "$OUTPUT_APK")" \
  "$PKG" \
  "$SIZE"
