# On the impact of self-defense techniques on the energy efficiency of Android apps

Replication package of the MSc thesis by Irena Ristova (Vrije Universiteit Amsterdam, 2026).

The study measures the energy cost of three Android self-defense techniques, each against the unprotected
build of the same application: packing (Bangcle), obfuscation (Allatori) and runtime shielding (freeRASP).
Nine open-source applications were measured on three generations of Google Pixel phones (Pixel 3, 6 and 9),
in three randomised batches.

## Contents

The package is being uploaded. It will contain:

- `6_results_analysis/`: the notebooks that produce every table, figure and number in the thesis, and the
  supplementary material (Tables S1 to S16, Figures S1 to S3) with an index from each thesis item to its source.
- `6_results_data/`: the run tables and the derived inputs, with a data map that says where every file comes from.
- `tools/` and `appium_android_tests/`: the measurement harness and the interaction scenarios.
- `android-runner/`: the Android Runner configurations, hooks and framework changes used in the experiment,
  with instructions for applying them to Android Runner.

## Data kept outside this repository

The raw per-measurement logs (Android system logs and harness logs), the 40 measured APKs and the raw energy
samples are not in this repository because of their size. The logs and the APKs are shared with the examiners on
request.
