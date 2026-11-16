# app_store_facts_2026-11-16.csv — provenance

**What it is.** For each of the nine applications, whether Google Play and F-Droid list it, and the
download range Google Play shows. Read from the stores' public pages on 16 November 2026, after the
experiment. The cohort table (Table OA2, `Descriptive_Statistics.ipynb`) reads it.

**How it was read.** By hand from saved copies of the pages, on 16 November 2026 between 03:46 and
03:51 UTC. The values were typed into this file once. Each row's `source_url` names the two pages.
- **Google Play:** `https://play.google.com/store/apps/details?id=<id>&hl=en_US&gl=US`, with the
  store id: the measured package id without the `.debug` suffix the debug build type adds
  (documenter, tipuous). The same pages with `gl=DE` gave the same ranges.
- **F-Droid:** the package page `https://f-droid.org/en/packages/<id>/` and the API
  `https://f-droid.org/api/v1/packages/<id>`.

## What each column is

- `app`: the application's short name in the run tables.
- `google_play_downloads`: the download range Google Play shows (`10+`, `5K+`, ...), a lower bound
  of the installs. `not listed`: no page for the id (HTTP 404).
- `f_droid`: `listed` or `not listed` (HTTP 404). F-Droid shows no download count.
- `access_date`: 2026-11-16 in every row.
- `source_url`: the Google Play page, then the F-Droid page.

## What it cannot say

- **The state during the experiment** (August 2026). Store pages change. AndroT, collected
  earlier, recorded pdfviewer and poetskingdom as published on Google Play. On 16 November 2026
  neither had a page, with the `gl` parameter set to US, DE, NL, GB, IN, ZA or ZW, or left out, and
  the Wayback Machine holds no copy of either page.
- **Installs of the measured version.** Google Play's current version is newer than the measured
  one for calculator, linkhub, medtimer and tipuous, and older for documenter (1.0.13, measured
  1.0.14). The range counts every version.
- **F-Droid downloads.** F-Droid publishes per-mirror access logs
  (`https://fdroid.gitlab.io/metrics/`), which it does not present as download counts.
