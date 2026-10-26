#!/usr/bin/env python3
"""Structural (APK-only) obfuscation fingerprinter, Allatori-focused.

Built 2026-07-31 as the STRUCTURAL companion to a logcat-based fingerprinter.
Motivation: Allatori is a compile-time obfuscator. It ships no runtime library,
no native lib, no log tag and no marker string, so a logcat fingerprinter is
structurally blind to it. Whether an APK was actually obfuscated therefore has
to be established from the artifact itself, per-app, BEFORE any energy claim
about the "allatori_debug" variant is made.

This matters because in this cohort the variant label does NOT imply the
treatment was applied. See --pair mode: it reports, per app, whether the
allatori APK differs from its baseline at all.

No external dependencies (no aapt, no apktool, no androguard). Pure-python
DEX parsing + a self-validating instruction walker.

Signals emitted per APK
-----------------------
  short_pkg_classes        classes whose leaf name is 1-2 chars but whose
                           package path is preserved  (Allatori rename style)
  decryptor_classes        injected static String->String methods that build a
                           char[] via xor-int/lit8 + int-to-char + aput-char
  decryptor_keysets        distinct (k1,k2) XOR literal pairs; Allatori emits
                           one key pair PER obfuscated class
  decryptor_call_sites     invoke-static call sites targeting those methods.
                           This is the static upper bound on runtime string
                           decryptions per full code sweep, NOT a per-cell count.
  obf_source_files         classes whose SourceFile attribute is 1-2 chars
                           (Allatori renames SourceFile; line numbers survive)
  hi_byte_strings          string constants containing ctrl/high bytes
                           (ciphertext proxy)
  talsec_classes/libts     freeRasp contamination check (cross-variant hygiene)

Usage
-----
  python3 scratchpad/apk_obfuscation_fingerprint.py <apk> [<apk> ...]
  python3 scratchpad/apk_obfuscation_fingerprint.py --pair <app> [<app> ...]
  python3 scratchpad/apk_obfuscation_fingerprint.py --pair-all --csv out.csv

--pair resolves baseline_debug vs allatori_debug from the standard APK dirs and
prints a PLACEBO / OBFUSCATED verdict per app.
"""
import argparse
import csv
import hashlib
import os
import re
import struct
import sys
import zipfile
from collections import Counter

APK_DIR = "/home/irena/Documents/Master Thesis/APKs"
ALLATORI_DIR = os.path.join(APK_DIR, "allatori_debug_canonical")
COHORT = ["calculator", "tipuous", "poetskingdom", "linkhub", "medtimer",
          "androidskk", "itsok", "keep_recipe", "another_notes", "documenter",
          "pdfviewer"]

ACC_STATIC = 0x8
RX_SHORT_LEAF = re.compile(r"^L(?:[A-Za-z_0-9$]+/)+[A-Za-z]{1,2};$")


# --------------------------------------------------------------------------
# DEX parsing
# --------------------------------------------------------------------------
def uleb128(buf, off):
    result = shift = 0
    while True:
        b = buf[off]
        off += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, off
        shift += 7


class Dex:
    def __init__(self, data):
        if data[:4] != b"dex\n":
            raise ValueError("not a dex: %r" % data[:8])
        self.d = data
        (self.string_ids_size, self.string_ids_off,
         self.type_ids_size, self.type_ids_off,
         self.proto_ids_size, self.proto_ids_off,
         self.field_ids_size, self.field_ids_off,
         self.method_ids_size, self.method_ids_off,
         self.class_defs_size, self.class_defs_off,
         self.data_size, self.data_off) = struct.unpack_from("<14I", data, 56)
        offs = struct.unpack_from("<%dI" % self.string_ids_size, data,
                                  self.string_ids_off)
        s = []
        for o in offs:
            _n, p = uleb128(data, o)
            s.append(data[p:data.index(b"\x00", p)])
        self.strings = s

    def type_desc(self, idx):
        return self.strings[struct.unpack_from("<I", self.d,
                                               self.type_ids_off + 4 * idx)[0]]

    def method_ref(self, idx):
        ci, pi, ni = struct.unpack_from("<HHI", self.d,
                                        self.method_ids_off + 8 * idx)
        shorty = self.strings[struct.unpack_from(
            "<I", self.d, self.proto_ids_off + 12 * pi)[0]]
        return self.type_desc(ci), self.strings[ni], shorty

    def class_def(self, i):
        return struct.unpack_from("<8I", self.d, self.class_defs_off + 32 * i)

    def class_methods(self, i):
        """Yield (method_idx, access_flags, code_off)."""
        cdo = self.class_def(i)[6]
        if cdo == 0:
            return
        d = self.d
        p = cdo
        sfz, p = uleb128(d, p)
        ifz, p = uleb128(d, p)
        dmz, p = uleb128(d, p)
        vmz, p = uleb128(d, p)
        for _ in range(sfz + ifz):
            _a, p = uleb128(d, p)
            _b, p = uleb128(d, p)
        for count in (dmz, vmz):
            idx = 0
            for _ in range(count):
                delta, p = uleb128(d, p)
                acc, p = uleb128(d, p)
                co, p = uleb128(d, p)
                idx += delta
                yield idx, acc, co

    def code_insns(self, code_off):
        if code_off == 0:
            return None
        isz = struct.unpack_from("<I", self.d, code_off + 12)[0]
        return self.d[code_off + 16: code_off + 16 + isz * 2]


# --------------------------------------------------------------------------
# Instruction walker (canonical DEX opcode widths, in 16-bit code units)
# --------------------------------------------------------------------------
_W = [1] * 256


def _set(rng, w):
    for o in rng:
        _W[o] = w


_set([0x02, 0x05, 0x08], 2)
_set([0x03, 0x06, 0x09], 3)
_set([0x13, 0x15, 0x16, 0x19, 0x1A, 0x1C, 0x1F, 0x20, 0x22, 0x23], 2)
_set([0x14, 0x17, 0x1B], 3)
_set([0x18], 5)
_set([0x24, 0x25, 0x26], 3)
_set([0x29], 2)
_set([0x2A, 0x2B, 0x2C], 3)
_set(range(0x2D, 0x32), 2)          # cmpl-float .. cmp-long      (23x)
_set(range(0x32, 0x3E), 2)          # if-eq .. if-lez
_set(range(0x44, 0x52), 2)          # aget .. aput-short          (23x)
_set(range(0x52, 0x6E), 2)          # iget/iput/sget/sput
_set(range(0x6E, 0x73), 3)          # invoke-*
_set(range(0x74, 0x79), 3)          # invoke-*/range
_set(range(0x90, 0xB0), 2)          # binop                       (23x)
_set(range(0xB0, 0xD0), 1)          # binop/2addr                 (12x)
_set(range(0xD0, 0xE3), 2)          # binop/lit16, binop/lit8
_set([0xFA, 0xFB], 4)
_set([0xFC, 0xFD], 3)
_set([0xFE, 0xFF], 2)


def walk(insns):
    """Yield (op, raw_bytes). Raises ValueError if the walk desyncs."""
    n = len(insns) // 2
    i = 0
    while i < n:
        op, b1 = insns[i * 2], insns[i * 2 + 1]
        if op == 0x00 and b1 != 0:
            if b1 == 0x01:
                w = struct.unpack_from("<H", insns, i * 2 + 2)[0] * 2 + 4
            elif b1 == 0x02:
                w = struct.unpack_from("<H", insns, i * 2 + 2)[0] * 4 + 2
            elif b1 == 0x03:
                ew = struct.unpack_from("<H", insns, i * 2 + 2)[0]
                sz = struct.unpack_from("<I", insns, i * 2 + 4)[0]
                w = (sz * ew + 1) // 2 + 4
            else:
                raise ValueError("bad payload 0x%02x" % b1)
        else:
            w = _W[op]
        yield op, insns[i * 2:(i + w) * 2]
        i += w
    if i != n:
        raise ValueError("desync %d != %d" % (i, n))


# --------------------------------------------------------------------------
# Allatori signals
# --------------------------------------------------------------------------
def find_decryptors(dex):
    """{method_idx: xor_literals} for injected static String->String decryptors.

    Shape (verified against Allatori 9.8 Educational output):
        static String f(String s) {
            char[] a = new char[s.length()];
            int i = s.length() - 1;
            while (i >= 0) { a[i] = (char)(s.charAt(i) ^ K1); i--;
                             if (i < 0) break;
                             a[i] = (char)(s.charAt(i) ^ K2); i--; }
            return new String(a);
        }
    Matched on SHAPE, not on the method name (Allatori picks the name).
    """
    out = {}
    for i in range(dex.class_defs_size):
        for midx, acc, co in dex.class_methods(i):
            if not (acc & ACC_STATIC):
                continue
            if dex.method_ref(midx)[2] != b"LL":
                continue
            insns = dex.code_insns(co)
            if not insns:
                continue
            lits, newarr, i2c, aputc = [], False, False, False
            try:
                for op, raw in walk(insns):
                    if op == 0xDF:
                        lits.append(raw[3])
                    elif op == 0x23:
                        newarr = True
                    elif op == 0x8E:
                        i2c = True
                    elif op == 0x50:
                        aputc = True
            except ValueError:
                continue
            if lits and newarr and i2c and aputc:
                out[midx] = lits
    return out


def decrypt(ciphertext, keys):
    """Allatori reverse-walk alternating XOR. ciphertext is a str."""
    n = len(ciphertext)
    out = [""] * n
    for k, i in enumerate(range(n - 1, -1, -1)):
        out[i] = chr(ord(ciphertext[i]) ^ keys[k % len(keys)])
    return "".join(out)


def fingerprint(path, samples=0):
    z = zipfile.ZipFile(path)
    names = sorted((n for n in z.namelist()
                    if re.fullmatch(r"classes\d*\.dex", n)),
                   key=lambda n: int(re.sub(r"\D", "", n) or "1"))
    r = Counter()
    keysets = set()
    src_names = Counter()
    plaintexts = []
    desync = 0
    for nm in names:
        dex = Dex(z.read(nm))
        r["classes"] += dex.class_defs_size
        r["strings"] += dex.string_ids_size
        r["methods"] += dex.method_ids_size
        for i in range(dex.class_defs_size):
            cd = dex.class_def(i)
            desc = dex.type_desc(cd[0]).decode("utf-8", "replace")
            if RX_SHORT_LEAF.match(desc):
                r["short_pkg_classes"] += 1
            if b"aheaditec" in desc.encode() or "talsec" in desc.lower():
                r["talsec_classes"] += 1
            sfi = cd[4]
            if sfi != 0xFFFFFFFF:
                sf = dex.strings[sfi].decode("utf-8", "replace")
                src_names[sf] += 1
                if len(sf) <= 2:
                    r["obf_source_files"] += 1
        for s in dex.strings:
            if any(b > 0x7E or b < 0x20 for b in s):
                r["hi_byte_strings"] += 1
        dec = find_decryptors(dex)
        r["decryptor_classes"] += len(dec)
        for lits in dec.values():
            keysets.add(tuple(lits[:2]))
        if dec:
            for i in range(dex.class_defs_size):
                for midx, acc, co in dex.class_methods(i):
                    insns = dex.code_insns(co)
                    if not insns:
                        continue
                    prev = None
                    try:
                        for op, raw in walk(insns):
                            if op == 0x1A:
                                prev = dex.strings[
                                    struct.unpack_from("<H", raw, 2)[0]]
                            elif op == 0x1B:
                                prev = dex.strings[
                                    struct.unpack_from("<I", raw, 2)[0]]
                            elif op == 0x71:
                                tgt = struct.unpack_from("<H", raw, 2)[0]
                                if tgt in dec:
                                    r["decryptor_call_sites"] += 1
                                    if prev and len(plaintexts) < samples:
                                        try:
                                            plaintexts.append(
                                                (prev[:30],
                                                 decrypt(prev.decode("utf-8"),
                                                         dec[tgt])[:50]))
                                        except UnicodeDecodeError:
                                            pass
                                prev = None
                    except ValueError:
                        desync += 1
    r["decryptor_keysets"] = len(keysets)
    r["libts_so"] = int(any("libts.so" in n for n in z.namelist()))
    r["dex_files"] = len(names)
    r["dex_bytes"] = sum(len(z.read(n)) for n in names)
    r["walker_desync"] = desync
    return dict(r), plaintexts, src_names


def entry_diff(a, b):
    """Content-level ZIP entry diff. Returns (n_entries_a, n_identical, n_diff)."""
    za, zb = zipfile.ZipFile(a), zipfile.ZipFile(b)
    ha = {i.filename: hashlib.sha256(za.read(i.filename)).hexdigest()
          for i in za.infolist()}
    hb = {i.filename: hashlib.sha256(zb.read(i.filename)).hexdigest()
          for i in zb.infolist()}
    common = set(ha) & set(hb)
    same = sum(1 for n in common if ha[n] == hb[n])
    return len(ha), len(hb), same, len(common) - same, len(set(ha) ^ set(hb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("apks", nargs="*")
    ap.add_argument("--pair", nargs="*", metavar="APP")
    ap.add_argument("--pair-all", action="store_true")
    ap.add_argument("--samples", type=int, default=0,
                    help="print N decrypted string samples")
    ap.add_argument("--csv")
    args = ap.parse_args()

    rows = []
    apps = args.pair if args.pair else (COHORT if args.pair_all else None)
    if apps:
        print("%-14s %-10s %6s %6s %7s %8s %7s  %s"
              % ("app", "verdict", "shortC", "decCls", "calls", "keysets",
                 "srcObf", "zip entries base/alla identical/diff/only"))
        for app in apps:
            bp = os.path.join(APK_DIR, "%s_baseline_debug.signed.apk" % app)
            ap_ = os.path.join(ALLATORI_DIR, "%s_allatori_debug.signed.apk" % app)
            if not (os.path.exists(bp) and os.path.exists(ap_)):
                print("%-14s MISSING" % app)
                continue
            fb, _, _ = fingerprint(bp)
            fa, _, _ = fingerprint(ap_)
            na, nb, same, diff, only = entry_diff(bp, ap_)
            d_short = fa.get("short_pkg_classes", 0) - fb.get("short_pkg_classes", 0)
            d_calls = fa.get("decryptor_call_sites", 0) - fb.get("decryptor_call_sites", 0)
            if diff == 0 and only == 0:
                verdict = "PLACEBO"
            elif d_short > 0 or d_calls > 0 or fa.get("obf_source_files", 0) > fb.get("obf_source_files", 0):
                verdict = "OBFUSCATED"
            else:
                verdict = "NO-OBF-DIFF"
            print("%-14s %-10s %+6d %6d %7d %8d %7d  %d/%d %d/%d/%d"
                  % (app, verdict, d_short, fa.get("decryptor_classes", 0),
                     fa.get("decryptor_call_sites", 0), fa.get("decryptor_keysets", 0),
                     fa.get("obf_source_files", 0), na, nb, same, diff, only))
            row = {"app": app, "verdict": verdict}
            row.update({"base_" + k: v for k, v in fb.items()})
            row.update({"alla_" + k: v for k, v in fa.items()})
            rows.append(row)
    for p in args.apks:
        f, pt, src = fingerprint(p, samples=args.samples)
        print("=== %s ===" % os.path.basename(p))
        for k in sorted(f):
            print("   %-24s %s" % (k, f[k]))
        for ct, plain in pt:
            print("   decrypt %r -> %r" % (ct, plain))
        rows.append(dict(apk=os.path.basename(p), **f))

    if args.csv and rows:
        keys = sorted({k for r in rows for k in r})
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print("\nwrote %s" % args.csv)


if __name__ == "__main__":
    main()
