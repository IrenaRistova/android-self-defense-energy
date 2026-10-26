# LinkHub: per-app scenario calibration

> **Status: estimates of 2026-05-12 (v2.1.0 Compose re-derive below), not a calibration run.**
>
> Per-scenario wall times below are **estimated**, derived from reading the
> upstream Espresso test + the navigation graph + the v2.1.0 Compose source (no
> actual on-device timing). The `N=7` scenario count was the planning target at
> the time. The list that ran in the experiment is `LINKHUB_SCENARIO_LIST` in
> [`scenarios.py`](scenarios.py): five scenarios, recalibrated on 2026-08-03 from
> measured wall times (see its comments). See
> [`appium_android_tests/CONVENTIONS.md`](../CONVENTIONS.md) §6.5 for the
> calibration recipe.

## 2026-05-12: v2.1.0 Compose re-derive

LinkHub upstream migrated to Jetpack Compose (Material3) at v2.1.0
(versionCode 46). The original 2026-05-09 scaffold was authored against the
View-based v1.6.4 UI and used `<package>:id/...` selectors throughout; none of
those resource-ids exist in the Compose tree. The `_lib._find_id_with_compose_fallback`
cascade does NOT save the scenarios because the Compose source has NO
`Modifier.testTag(...)` calls anywhere (verified by grep over
`app/src/main/java/com/amrdeveloper/linkhub/ui/`).

The re-derive (2026-05-12) replaced every selector:

- Toolbar overflow `contentDescription="Options"` → `DropdownMenuItem text="New Folder"` / `"New Link"` (replaces the v1.6.4 FAB chain; note that `isQuickActionButtonEnabled()` defaults to **false**, so the optional FAB is NOT visible on fresh install).
- `SaveDeleteActionsRow` `contentDescription="Save"` / `"Delete"` replaces `<package>:id/save_action`.
- `OutlinedTextField` inputs addressed by `className("android.widget.EditText").instance(N)`: LinkScreen has 3 (Title=0, Url=1, SubTitle=2), FolderScreen has 1 (Name=0), SearchBar contributes 1 when expanded.
- Toolbar SearchBar leading icon `contentDescription="Search"` expands the search input (no more `<package>:id/search_action` → `android:id/search_src_text` chain).
- `swipeDeleteAndUndo` **DROPPED** (v2.1.0 removed SwipeToDismiss and the Undo snackbar entirely). Replaced by `longClickEditLink` which uses the same energy axis (SQLite write + Compose recomposition + navigation animation) but exercises `LinkViewModel.updateLink` instead of `deleteLink`/`insertLink`.

**What these estimates do not measure:**

- `shareLinkReceiveGeneral`: the actual wall time (intent re-launch + 3 EditText reads + 3 backs).
- `createFolderViaUi`: the cost of the overflow tap → DropdownMenuItem chain against the v1.6.4 FAB tap.
- `createLinkViaUi`: the 3-EditText typing latency on Compose `OutlinedTextField` (possibly slower than View-based AppCompat EditText, because of Compose recomposition).
- `createMultipleFolders`: the 3× burst, and whether a `cycle[*]_form_open_miss` cascade occurs if the overflow menu stays sticky.
- `createMultipleLinks`: the same.
- `searchKeywordFilter`: 4 keystrokes on the Material3 SearchBar (3 in v1.6.4; the extra one pushes the wall time toward the target).
- `longClickEditLink`: the reliability of `mobile: longClickGesture` on the Pixel 3 (older Appium servers may need a TouchAction fallback).

## Per-scenario estimates of 2026-05-12 (Pixel 3, baseline build, v2.1.0 Compose)

Based on reading the LinkScreen / FolderScreen / SearchScreen Compose sources
+ counting the Appium round-trips in each `_run_*` function in
[`scenarios.py`](scenarios.py) and using Metronome / Tipuous calibrated
wall-time constants as a reference (~0.3 s per `safe_click_description_contains`,
~0.6 s per `_record_typed_observed`, ~1.0 s per fragment-transition wait,
~0.4 s extra per overflow-DropdownMenuItem chain vs the v1.6.4 single-FAB).

| # | Scenario                  | Provenance   | Est. wall (s) | Notes                                                                                                                                |
|---|---------------------------|--------------|---------------|--------------------------------------------------------------------------------------------------------------------------------------|
| 1 | `shareLinkReceiveGeneral` | mirror       | ~12           | `mobile: shell` round-trip + activity re-launch + 3 EditText reads via instance(0/1/2) + 3 backs.                                    |
| 2 | `createFolderViaUi`       | inspired     | ~14           | Overflow tap + DropdownMenuItem tap + 1 type (instance 0) + Save (contentDescription) + transition.                                  |
| 3 | `createLinkViaUi`         | inspired     | ~18           | Overflow tap + DropdownMenuItem tap + 3 types (instances 0/1/2) + Save + transition.                                                 |
| 4 | `createMultipleFolders`   | exploration  | ~24           | 3× scenario 2 (each cycle navigates back through overflow, with no animation reuse benefit).                                         |
| 5 | `createMultipleLinks`     | exploration  | ~28           | 3× scenario 3 inner loop.                                                                                                            |
| 6 | `searchKeywordFilter`     | exploration  | ~14           | Toolbar SearchBar expand via contentDescription=Search + 4 query strokes (one extra vs v1.6.4) + 2× back to collapse.                |
| 7 | `longClickEditLink`       | exploration  | ~14           | Long-press card (`mobile: longClickGesture`) + DropdownMenu Edit tap + form opens + modify title (instance 0) + Save + back to home. |
|   | **Total**                 |              | **~124**      | Within target window `[100, 130]` s ✓.                                                                                               |

## Lock procedure (after a smoke run)

1. Run on Pixel 3 baseline with `BATTERY_STATS` granted to the BatteryManager companion app:

   ```bash
   adb -s <PIXEL3_SERIAL> shell pm grant com.example.batterymanager_utility \
     android.permission.BATTERY_STATS

   cd <repository root>/android-runner
   python3 . examples/batterymanager/linkhub_baseline_debug_pixel3_wifi_ykush.json
   ```

2. Read per-scenario `scenario_wall_seconds` from `appium_workload_coverage.jsonl`.
3. Replace the **Est. wall (s)** column above with measured medians (or means
   over the 3 repetitions).
4. If cumulative wall-time lands outside `[100, 130]` s, adjust `N` per
   `CONVENTIONS.md` §6.5 step 4: either drop the slowest scenario or pad the
   list with one more exploration. The locked list is
   [`scenarios.py::LINKHUB_SCENARIO_LIST`](scenarios.py) (`N=7` when this was
   written; five scenarios since 2026-08-03).
5. Record the lock (date and `run_id`) in the status note at the top of this file.

## Why this scenario count (2026-05-12)

LinkHub's energy axis is moderate (Room IO + RecyclerView, no audio / sensor
loop), so we trade scenario depth for breadth: 7 distinct UI surfaces sized
to fit the 100–130 s budget rather than 1–2 deep ones. The mirror + 2 inspired
scenarios cover the persistence-write-from-UI path; the 4 exploration scenarios
each hit a different secondary surface (multi-create burst, search-by-keyword
SQLite query, swipe-delete + undo CRUD churn). This maximises the chance that a
variant's protection layer is exercised on at least one path even if the
others fall back to the unwrapped baseline.
