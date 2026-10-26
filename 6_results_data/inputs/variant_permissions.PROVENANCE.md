# variant_permissions.csv: provenance

**What it is.** It lists every permission declared by every build the series batches measured. That
is 10 applications × 4 variants. Each build has one row per permission, together with the SHA-256 of
the APK the cells ran.

**Built by.** `python3 tools/extract_variant_permissions.py` (2026-10-05).
- It reads the three series run tables and takes each build's `apk_path` and `apk_sha256`.
- It fails when:
  - two series cells of one build name different APKs;
  - the file on disk does not have the run tables' hash;
  - `aapt2 dump permissions` lists nothing.
- `aapt2` is from the Android SDK build-tools (36.1.0 here).

**Why.** `variant_parity_full.csv` tracks only `declares_internet`, and the freeRASP integration adds
more permissions than INTERNET. The mechanism notebook reads this input
and emits `tables/added_permissions.csv`: the permissions each protected build declares that its
control does not.

**Pilot.** Not used. The pilot's builds are the same files, but nothing in Results reads them.
