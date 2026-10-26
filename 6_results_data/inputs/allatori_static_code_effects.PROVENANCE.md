# allatori_static_code_effects.csv: provenance

The one input to the results notebooks that no notebook regenerates. Producing it
requires `dexdump` over the baseline and Allatori APKs, which are not in this package
(available on request).

Produced 2026-08-30 by `scratchpad/analysis/allatori_cf_exclude_decryptors.py`
(routines, call sites, app-code percentages) and `tools/verify_allatori_obfuscation.py`
(classes renamed, corrected to the actually-swept pairs: medtimer 400, documenter 143).
The mechanism notebook copies it to `6_results_analysis/tables/allatori_static_code_effects.csv`,
the per-device notebook builds `table_obfuscation_static` from it, and the thesis reports the figures.

Definitions:
- **decryptor routine**: a static `(String)String` method carrying Allatori's xor /
  `int-to-char` / `aput-char` triple
- **decrypt call site**: an `invoke-static` inside the app package targeting one
- **app_code_*_pct**: change in the app package **excluding** the injected decryptors,
  so it measures the app's own code; instructions, all branches, `goto`, `if-*`

Rows are the six applications in the nine-application cohort's obfuscation arm.
documenter has no working Allatori build; keep_recipe and linkhub are declared nulls;
androidskk is excluded from the cohort.
