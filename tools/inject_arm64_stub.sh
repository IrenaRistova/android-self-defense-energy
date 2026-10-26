#!/usr/bin/env bash
#
# inject_arm64_stub.sh: add an arm64-v8a stub library so that Bangcle packs install on the Pixel 9.
#
# Implements stub-ABI injection: takes a pure-bytecode baseline APK (no `lib/` directory),
# adds `lib/arm64-v8a/libplaceholder.so` (a stripped 10 KB arm64-v8a ELF), zipaligns, and
# signs with the standard Android debug keystore. The resulting APK installs
# unchanged on Pixel 9 (= proves the stub doesn't break the signature) AND, when subsequently
# Bangcle-packed, propagates `libSecShell.so` into `lib/arm64-v8a/` so the pack installs on
# Pixel 9 too, which unblocks the `(bangcle, pixel9)` cell for the 12 pure-bytecode apps in the
# cohort.
#
# Why this preserves the black-box principle:
#
#   - Source code unchanged.
#   - Resources unchanged.
#   - Bytecode (classes.dex) unchanged.
#   - Only one ZIP entry added: a stripped ELF shared object that the app never dlopen()s
#     (no symbol the app references: the JVM and System.loadLibrary() are never invoked
#     against it).
#   - Same APK signing chain (debug keystore) that produced the original baseline.
#
# The injected `.so` is dead weight from the JVM's perspective: present in `lib/arm64-v8a/`
# so the installer accepts the install on arm64-only devices and so Bangcle's input-driven
# mechanism sees an arm64 ABI directory to propagate `libSecShell.so` into, but the
# Android runtime never loads `libplaceholder.so` because no code calls `loadLibrary`.
#
# This modifies packaging, not source. The injection is auditable (anyone can `unzip -l` the
# output APK and see exactly one extra file vs. the original) and reversible (just delete
# the entry from the ZIP).
#
# Usage:
#
#   tools/inject_arm64_stub.sh <input-baseline.apk> <output-dir> [<output-name>]
#
# Output: `<output-dir>/<output-name>.apk` (default name: `<input-basename>_stub.apk`).
#
# Exit codes:
#   0  success: APK signed and ready to install
#   1  invalid arguments
#   2  missing SDK tools / keystore / stub
#   3  zipalign or apksigner failure
#
# Prerequisites:
#   - Android SDK build-tools (zipalign + apksigner), searched under $ANDROID_HOME / common paths.
#   - `~/.android/debug.keystore` with default password `android` (standard Android debug keystore).
#   - The stub at `tools/stubs/libplaceholder.so` (10 KB stripped arm64-v8a ELF, built from
#     a copy of `libandroidx.graphics.path.so` from the Pdf-Viewer baseline).
#
set -euo pipefail

usage() {
  echo "Usage: $0 <input-baseline.apk> <output-dir> [<output-name>]" >&2
  echo "" >&2
  echo "Example:" >&2
  echo "  $0 path/to/Calculator/app-debug.apk \"\$HOME/Documents/Master Thesis/APKs/stub_injected\" calculator_stub_baseline" >&2
  exit 1
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
fi

INPUT_APK="$1"
OUTPUT_DIR="$2"
OUTPUT_NAME="${3:-$(basename "$INPUT_APK" .apk)_stub}"

if [[ ! -f "$INPUT_APK" ]]; then
  echo "ERROR: input APK not found: $INPUT_APK" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUB_SO="$SCRIPT_DIR/stubs/libplaceholder.so"
DEBUG_KEYSTORE="${ANDROID_DEBUG_KEYSTORE:-$HOME/.android/debug.keystore}"

if [[ ! -f "$STUB_SO" ]]; then
  echo "ERROR: stub libplaceholder.so missing at $STUB_SO" >&2
  echo "       Regenerate with:" >&2
  echo "       unzip -p path/to/PdfViewer/app-debug.apk lib/arm64-v8a/libandroidx.graphics.path.so > $STUB_SO" >&2
  exit 2
fi

if [[ ! -f "$DEBUG_KEYSTORE" ]]; then
  echo "ERROR: debug keystore not found at $DEBUG_KEYSTORE" >&2
  echo "       Set ANDROID_DEBUG_KEYSTORE or run an Android build to create the default." >&2
  exit 2
fi

# Locate Android SDK build-tools. Prefer 36.0.0 (Android 16) when available; fall back to
# the highest version found.
find_sdk_tool() {
  local tool_name="$1"
  if command -v "$tool_name" >/dev/null 2>&1; then
    command -v "$tool_name"
    return 0
  fi
  local candidate_root="${ANDROID_HOME:-$HOME/Android/Sdk}"
  if [[ -d "$candidate_root/build-tools" ]]; then
    # Highest-numbered build-tools dir
    local best
    best=$(find "$candidate_root/build-tools" -maxdepth 2 -name "$tool_name" -type f \
      | sort -V | tail -1)
    if [[ -n "$best" ]]; then
      echo "$best"
      return 0
    fi
  fi
  return 1
}

ZIPALIGN=$(find_sdk_tool zipalign) || { echo "ERROR: zipalign not found (set ANDROID_HOME)" >&2; exit 2; }
APKSIGNER=$(find_sdk_tool apksigner) || { echo "ERROR: apksigner not found (set ANDROID_HOME)" >&2; exit 2; }

echo "[inject_arm64_stub] zipalign:  $ZIPALIGN"
echo "[inject_arm64_stub] apksigner: $APKSIGNER"
echo "[inject_arm64_stub] keystore:  $DEBUG_KEYSTORE"
echo "[inject_arm64_stub] stub:      $STUB_SO ($(stat -c%s "$STUB_SO") bytes)"
echo "[inject_arm64_stub] input:     $INPUT_APK"

WORK_DIR=$(mktemp -d -t apk_stub_inject.XXXXXX)
trap 'rm -rf "$WORK_DIR"' EXIT

# Build the entry path inside a temp dir so `zip` preserves the hierarchy when we add it.
mkdir -p "$WORK_DIR/lib/arm64-v8a"
cp "$STUB_SO" "$WORK_DIR/lib/arm64-v8a/libplaceholder.so"

STAGED_APK="$WORK_DIR/staged.apk"
ALIGNED_APK="$WORK_DIR/aligned.apk"
FINAL_APK="$OUTPUT_DIR/$OUTPUT_NAME.apk"

cp "$INPUT_APK" "$STAGED_APK"

# Add the stub entry. `zip -j` would strip the path; we need the lib/arm64-v8a/ hierarchy
# preserved, so we cd into the work dir first. Use -0 (store / no compression) so the
# Android installer can mmap the .so directly: modern APKs default to
# `extractNativeLibs="false"`, which only works with uncompressed + page-aligned entries.
( cd "$WORK_DIR" && zip -q -0 "$STAGED_APK" "lib/arm64-v8a/libplaceholder.so" )

# Sanity: verify entry is present
if ! unzip -l "$STAGED_APK" | grep -q "lib/arm64-v8a/libplaceholder.so"; then
  echo "ERROR: stub entry missing from staged APK after zip" >&2
  exit 3
fi

# zipalign for installer
"$ZIPALIGN" -p -f 4 "$STAGED_APK" "$ALIGNED_APK"

# Strip any prior signature blocks (META-INF/CERT.* / *.SF / MANIFEST.MF lines), then sign
# fresh with the debug keystore. apksigner handles re-signing cleanly without explicit strip.
"$APKSIGNER" sign \
  --ks "$DEBUG_KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --ks-key-alias androiddebugkey \
  "$ALIGNED_APK"

# Verify signature
"$APKSIGNER" verify --verbose "$ALIGNED_APK" >/dev/null

mv "$ALIGNED_APK" "$FINAL_APK"

echo "[inject_arm64_stub] OK -> $FINAL_APK ($(stat -c%s "$FINAL_APK") bytes)"
echo "[inject_arm64_stub] verify lib/ shape:"
unzip -l "$FINAL_APK" | grep ' lib/' || echo "  (no lib/ entries — UNEXPECTED, injection failed)"
