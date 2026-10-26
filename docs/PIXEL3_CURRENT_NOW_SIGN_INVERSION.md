# Pixel 3 (Android 12): BATTERY_PROPERTY_CURRENT_NOW sign inversion

**Discovered:** 2026-06-02 during first 3-phone parallel S2 baseline smoke
**Affects:** Pixel 3 only. Pixel 6 (Android 13) + Pixel 9 (Android 16) read the standard sign convention.

## The bug in one sentence

On Pixel 3 (Google "blueline", Android 12), the Java
`BatteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW)`
call returns the current with **inverted sign** compared to the standard
Android convention used by Pixel 6 + Pixel 9.

| Phone | sign convention via `BATTERY_PROPERTY_CURRENT_NOW` |
|---|---|
| **Pixel 3** | positive = **discharge**, negative = **charge** (old / pre-2017) |
| **Pixel 6** | positive = charge, negative = discharge (modern) |
| **Pixel 9** | positive = charge, negative = discharge (modern) |

## Root cause

Pixel 3 has multiple fuel-gauge entries under `/sys/class/power_supply/`:

```
$ ls /sys/class/power_supply/
battery   bms   dc   main   maxfg   parallel   pc_port   usb   wireless
```

And these report **opposite signs**. When Pixel 3 is charging at +259 mA
through USB, the four `current_*` sysfs files report:

| sysfs path | reading | sign |
|---|---|---|
| `/sys/class/power_supply/battery/current_now` | +258788 µA | modern (positive=charge) |
| `/sys/class/power_supply/bms/current_now` | -258788 µA | inverted |
| `/sys/class/power_supply/maxfg/current_now` | -282500 µA | inverted (Maxim fuel gauge) |
| `/sys/class/power_supply/maxfg/current_avg` | -250000 µA | inverted |

The **Pixel 3 Health HAL** (vendor `android.hardware.health@2.0` from
the blueline device tree) populates `BatteryProperties.batteryCurrentNow`
by reading from **`maxfg/current_now`**, not from `battery/current_now`.
The Maxim MAX17XXX fuel-gauge family, which the Pixel 3 PMIC uses,
reports current as "positive when current is flowing OUT of the battery",
i.e. the legacy convention.

The Java `BatteryManager.getIntProperty()` API contract documents the
modern convention ("positive values indicate net current entering the
battery from a charge source"), but on Pixel 3 the underlying HAL
delivers the legacy convention, and Android 12 doesn't normalize it.

Pixel 6 + Pixel 9 use different fuel gauges and a more recent Health HAL
(`@2.1` / `@3.0`) that normalizes to the modern convention.

## How we observed it

First 3-phone parallel S2-baseline smoke (2026-06-02):

| Phone | hub-ctrl mean current (BM API) | sysfs `battery/current_now` mean | matched? |
|---|---|---|---|
| Pixel 9 (Android 16) | -431 mA | -431 mA | ✓ |
| Pixel 6 (Android 13) | -161 mA | -161 mA | ✓ |
| Pixel 3 (Android 12) | **+201 mA** | **-279 mA** | ❌ sign flipped |

`BATTERY_PROPERTY_CURRENT_NOW` on Pixel 3 reports +201 mA *during a
real discharge*, the opposite sign from sysfs `battery/current_now`,
which correctly reports -279 mA.

## Impact on the thesis

### Energy / power computations: **NOT AFFECTED** ✓

Android Runner's `AndroidRunner/Plugins/batterymanager/Batterymanager.py:182-184`
(in the fork named in `android-runner/README.md`) uses `abs()` when computing
instantaneous power:

```python
@staticmethod
def calculate_power(df):
    df['power'] = (abs(df['BATTERY_PROPERTY_CURRENT_NOW']) / 1000 / 1000) * (df['EXTRA_VOLTAGE'] / 1000)
    return df
```

So all `Avg power (W)`, `Energy simple (J)`, `Energy trapz (J)` values
in the `Aggregated_Results_Batterymanager.csv` are **correct on
Pixel 3** despite the sign inversion. Magnitude is the right number.

### Matched-pair ratios: **NOT AFFECTED** ✓

Both Pixel 3 baseline and Pixel 3 Bangcle runs read via the same HAL
with the same inverted sign. The ratio
`bangcle_avg_power / baseline_avg_power` is identical whether we
multiply both by +1 or both by -1. The matched-pair claim holds.

### `verified_discharge` gate: **AFFECTED** ❌

`before_experiment_apply_device_state.py` and `_lib_device_state.py`
check `current_now < 0` to verify the phone is in discharge state.
On Pixel 3 this gate is **inverted**: it would say "USB-supplying"
during a real discharge and "verified_discharge" while charging.

### Cross-device absolute-energy comparisons: **AFFECTED for raw current** ⚠️

Raw `current_now` samples from the Pixel 3 appear "mirrored" against the
Pixel 6 and 9. Cross-device plots of raw current must normalize the Pixel 3
by multiplying by -1.

## Code-path audit (2026-06-02)

Every place in the codebase that reads `BATTERY_PROPERTY_CURRENT_NOW`
or `current_now` was inspected to verify that the sign inversion
**does not contaminate any thesis claim**. Result: every energy /
power computation already uses `abs()`, and the discharge gate reads
from sysfs (which is sign-consistent across all 3 devices).

| Code path | Reads from | Uses sign? | Effect of Pixel 3 inversion |
|---|---|---|---|
| `AndroidRunner/Plugins/batterymanager/Batterymanager.py:183` `calculate_power` | Java API via CSV | `abs(current)` | **None**: magnitude correct |
| `Batterymanager.py:187` `trapezoid_method` | uses `df['power']` (already abs) | n/a | **None** |
| `Batterymanager.py:196-220` `aggregate_batterymanager_runs` | Java API via CSV, signed mean | signed | Display-only: `BATTERY_PROPERTY_CURRENT_NOW` mean column in `Aggregated_Results_Batterymanager.csv` shows +mA on Pixel 3 during discharge |
| validation-gate script (development tool, not in this package), `integrate_power` | Java API via CSV, `abs()` | abs for power | **None** for power; `currents[]` signed array is display-only |
| tracking-matrix script (development tool, not in this package), `_integrate_sysfs_power` | sysfs sample log, `abs(cur_ua)` | abs | **None** |
| the same script, energy-window split | Java API via CSV, `abs(cur_ua)` | abs | **None** for energy |
| `android-runner/Scripts/_lib_device_state.py` `read_current_now_ua` | **sysfs `/sys/class/power_supply/battery/current_now`** | n/a | **None**: sysfs uses modern convention on Pixel 3 |
| `_lib_device_state.py` `verify_discharging` | sysfs via `read_current_now_ua` | `current < 0` | **None**: already correct on Pixel 3 (gate uses sysfs, not Java API) |
| the tracking-matrix script, `energy_invalid_usb_supplying` tag | sysfs verdict from `_lib_device_state` | verdict | **None**: already correct on Pixel 3 |

**Net effect on thesis claims:**

| Thesis claim type | Affected? |
|---|---|
| Energy magnitude (`Energy trapz (J)`, `Avg power (W)`) | **No**: `abs()` everywhere |
| Matched-pair ratio (`Bangcle_E / baseline_E` on Pixel 3) | **No**: sign cancels |
| Cross-device matched-pair comparison (Bangcle overhead on P3 vs P6 vs P9) | **No**: same reasoning per-device |
| Discharge-state classification (`verified_discharge` tag) | **No**: gate uses sysfs |
| `energy_invalid_usb_supplying` matrix-row tagging | **No**: uses sysfs |
| Display of signed `avg_current_mA` column | **Yes**: Pixel 3 inverted (mitigated below) |
| Time-series raw `current_now` plots over a run | **Yes**: Pixel 3 mirrored (analysis scripts normalize) |

## Mitigations

### Implemented (2026-06-02)

1. **`android-runner/Scripts/before_experiment_apply_device_state.py` Play Protect + dumpsys
   reset chain.** Battery service not frozen on Android 12; sideloaded
   APKs not blocked by Play Protect on any phone.
2. **Per-device sign-convention table** in
   `_lib_device_state.py::CURRENT_NOW_API_SIGN`:
   ```python
   CURRENT_NOW_API_SIGN = {
       "blueline": -1,   # Pixel 3, Android 12, Maxim MAX17XXX via Health HAL @2.0
       "oriole":   +1,   # Pixel 6, Android 13
       "tokay":    +1,   # Pixel 9, Android 16
   }
   ```
   Plus `current_now_api_sign(device)` and `normalize_current_now_api(device, raw)`
   helpers for any future Java-API consumer that wants modern-convention output.
3. **`verify_discharging()` docstring** states that the function uses
   sysfs and is unaffected by the Java-API inversion; no per-device
   multiplier is needed there.
4. **Sign normalization in the validation-gate script** (a development
   tool, not in this package): a `detect_sign_multiplier(run_dir)`
   heuristic detects Pixel 3 runs from the `data/Pixel 3-W/...` data
   subdirectory; `integrate_power` takes a `sign_multiplier=` parameter and
   applies it to the displayed signed avg-current (energy / power
   untouched). Its Pixel 3 output shows
   `-194.67 mA [Pixel 3 sign-normalized]` instead of `+194.67 mA`.

### Not implemented

5. **The `BATTERY_PROPERTY_CURRENT_NOW` mean column in
   `Aggregated_Results_Batterymanager.csv` is not normalized.** That would
   require patching `Batterymanager.aggregate_batterymanager_runs` in the
   Android Runner fork to apply the per-device multiplier. The column is for
   display only, and the analysis scripts (above) handle it.
6. **The companion APK reads the Java API, not sysfs.** A sysfs-first read
   path (Java File I/O on `/sys/class/power_supply/battery/current_now`)
   would guard against a phone with an unknown convention; the sign table
   covers the three phones used.

## Methodology note

> On Pixel 3 (Android 12, `blueline`), the Java
> `BatteryManager.getIntProperty(BATTERY_PROPERTY_CURRENT_NOW)` API returns
> values with the legacy "positive = discharging" sign convention because
> the Health HAL reads from the Maxim MAX17XXX fuel gauge driver. Pixel 6
> (Android 13) and Pixel 9 (Android 16) use the modern "positive = charging"
> convention. Energy values are unaffected (all integrations use `|I| · V`),
> and matched-pair ratios are unaffected (the sign multiplier cancels
> within each pair). Cross-device comparisons normalize Pixel 3 to the
> modern convention via the per-device sign multiplier in
> `_lib_device_state.CURRENT_NOW_API_SIGN`; raw current plots and
> validation-gate output apply this normalization automatically.

## Cross-reference

The Android Runner output folders below are from the 2026-06-02 smoke runs and
are not in this package.

- `Aggregated_Results_Batterymanager.csv` rows from
  `output/2026.06.02_192618/` (Pixel 3 solo) and `output/2026.06.02_192105/`
  (Pixel 3 parallel) show the `BATTERY_PROPERTY_CURRENT_NOW`
  positive mean despite real discharge.
- Pixel 6 + Pixel 9 same-day runs (`output/2026.06.02_192106/`,
  `output/2026.06.02_190408/`) both report negative mean during
  discharge, matching the modern convention.
