# Batch naming: sweep tags and thesis names

Two naming systems, deliberately kept separate.

**Sweep tags** are filesystem keys. They name the state directory, the Android
Runner output directories, the per-cell logs and the run table. They are dated so
they can never collide and so a reader can always tell when data was taken. Once
a sweep has started, **its tag cannot be changed**: it is the key `--resume`
uses to find what has already run.

**Thesis names** are what appears in the write-up. The pilot is not counted, so
the series numbering starts at the first batch measured with the thermal gate.

| Thesis name | Sweep tag | Cells | Thermal gate | Role |
|---|---|---|---|---|
| **Pilot** | `final_ykush_2026-08-04` | 1200 | no | 1120 valid. Motivates the thermal gate and documents the harness defects. Not pooled with the series. |
| **Series batch 1** | `final_ykush_batch2_2026-08-10` | 1200 | yes | complete, 1112 valid |
| **Series batch 2** | `final_ykush_series2_2026-08-14` | 1200 | yes | complete, 1106 valid |
| **Series batch 3** | `final_ykush_series3_2026-08-17` | 1200 | yes | complete, 1113 valid. Reaches N = 30 |

## Why the mismatch on series batch 1

Its tag says `batch2` because at the time it was the second sweep run. The
decision to treat the first sweep as a pilot came later, once the thermal-gate
comparison showed the two were measured under different conditions and could not
be pooled. The tag was already embedded in ~900 cells of state and output by
then.

Renaming it would mean rewriting the state directory, every AR output directory,
every per-cell log path and the run table, while a sweep was mid-flight, for a
cosmetic gain. The tag stays; this table is the mapping.

Later sweeps use `series<N>` so the two systems converge from here.

## Status

All four batches are complete as of 2026-08-22. Data collection is finished:
4800 cells, 3331 valid in the pooled series. The thesis reports the results.

## Names in the thesis

The thesis uses the thesis names (for example "series batch 1"), not the sweep
tags; the table above maps one to the other.

## What must stay identical across the series

Series batches 1–3 are pooled, so they must be measured identically or they
inherit exactly the pilot's problem:

- thermal gate at `STATUS_MAX=1`, 10-minute cap
- band 40–70, cell timeout 660 s, `SAFETY_MIN_LEVEL` 20
- **no post-gate thermal reading**: series batch 1 has none, so neither can 2 or 3
- the same APKs, unrebuilt, and the same scenario code
- the **full grid**, including the cells known to fail
  (`documenter/allatori`, `keep_recipe/pixel3`): they cost ~6 h per batch, but
  keeping the structure identical matters more, and the analysis gates discard
  them consistently.

The one thing that **must** differ per batch is the random seed: a different
seed is what stops an order-dependent artefact repeating identically instead of
averaging out.

| Batch | Seed |
|---|---|
| Pilot | `9af8fb394643d815` |
| Series 1 | `3c7e1a9b52d4f860` |
| Series 2 | `7d2f4b8e1a6c9053` |
| Series 3 | `5e9a3c7f2b81d640` |
