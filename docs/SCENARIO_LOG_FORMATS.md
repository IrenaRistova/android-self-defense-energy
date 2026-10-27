# Scenario log formats: parser reference

**Purpose:** authoritative guide to how each app in the cohort emits per-scenario pass/fail data in the sweep-runner cell logs. Any tool that parses scenario results (`tools/scenario_log_parser.py`, which `tools/build_run_table_extended.py` imports, plus any future analysis script) MUST handle every format listed here, or it will silently drop scenarios.

**Why this matters:** a regex that misses `androidskk`'s `round=N` variant of Format B reports androidskk's cells as "0 scenarios ran" although the app works (94 cells on 2026-07-31). Every regex-based parser in the repository must be validated against this document.

---

## Two formats used across the cohort

### Format A: coverage JSON blob (majority of apps)

**Log line shape:**
```
<APP> <workload>? coverage: {"scenario_name": "...", "scenario_success": true/false, "scenario_success_strict_ui": true/false, ...}
```

**Example (linkhub):**
```
INTERACTION_APPIUM: linkhub coverage: {"build_label": "freerasp_debug", "card_found": true, "edit_form_opened": true, ..., "scenario_name": "longClickEditLink", "scenario_success": true, "scenario_success_strict_ui": true, "scenario_wall_seconds": 25.768, ...}
```

**Regex to extract the JSON:** `coverage:\s*(\{[^\n]+\})`

Then `json.loads()` the captured group. Read `scenario_name`, `scenario_success_strict_ui` (fallback to `scenario_success` if strict field absent).

### Format B: single-line "scenario NAME -> success=X strict=Y wall=..."

**Log line shape (basic):**
```
INTERACTION_APPIUM: scenario <NAME> -> success=<True|False> strict=<True|False> wall=<X.XX>s
```

**Log line shape with round (androidskk only):**
```
INTERACTION_APPIUM: scenario <NAME> round=<N> -> success=<True|False> strict=<True|False> wall=<X.XX>s
```

**Regex covering both:** `scenario\s+([A-Za-z0-9_]+)(?:\s+round=\d+)?\s+->\s+success=(True|False)\s+strict=(True|False)`

The `(?:\s+round=\d+)?` non-capturing group is CRITICAL. Regexes that expect `-> success=` immediately after the name silently drop androidskk (as of 2026-07-31).

---

## Per-app format table (the ten measured applications)

| App | Format | Notes |
|---|---|---|
| documenter | A | Relaxed scoring for homeScrollAndIdle and openDocumentAttempt (they pass on the primary action alone) |
| linkhub | A | `strict_ok` computed in the scoring module |
| tipuous | A | `strict_ok` computed in the scoring module |
| calculator | **B** basic | |
| itsok | **B** basic | |
| keep_recipe | **B** basic | |
| medtimer | **B** basic | `log("scenario %s -> success=%s strict=%s wall=%.2fs" % ...)` |
| pdfviewer | **B** basic | Needs the mitmproxy replay for `S9_open_online_pdf` and `S10_show_inline_pdf` |
| poetskingdom | **B** basic | |
| **androidskk** | **B with round** | `log("scenario %s round=%d -> success=%s strict=%s wall=%.2fs" % ...)`; the parser must include `round=N` |

`tools/scenario_log_parser.py` reads both formats.

## Other apps with scenarios (not measured in the four batches; same formats)

| App | Format |
|---|---|
| avnc, crcontainer, diaguard, gallerywall, horoscapp, iamspeed, metronome, repertoire | A |
| keepitup, podaura, ytalarm | B basic |

---

## Duplicate-line trap

Every `INTERACTION_APPIUM:` log line is emitted TWICE in cell logs: once with the `INTERACTION_APPIUM:` prefix, once with `appium_android_tests: INTERACTION_APPIUM:` prefix. Any parser that doesn't skip lines starting with `appium_android_tests:` will double-count scenarios.

**In `tools/scenario_log_parser.py`:**
```python
if line.startswith("appium_android_tests:"):
    continue
```

---

## Canonical parser (implemented in `tools/scenario_log_parser.py`)

```python
import re, json

FMT_A = re.compile(r"coverage:\s*(\{[^\n]+\})")
FMT_B = re.compile(r"scenario\s+([A-Za-z0-9_]+)(?:\s+round=\d+)?\s+->\s+success=(True|False)\s+strict=(True|False)")

def parse_scenarios_from_log(log_path):
    seen = set()
    results = []
    with open(log_path, errors="replace") as f:
        for line in f:
            if line.startswith("appium_android_tests:"):
                continue  # skip Appium's duplicate-with-prefix
            # Format A: coverage JSON
            m = FMT_A.search(line)
            if m:
                try:
                    d = json.loads(m.group(1))
                    name = d.get("scenario_name")
                    strict = d.get("scenario_success_strict_ui", d.get("scenario_success"))
                    if name and name not in seen:
                        seen.add(name)
                        results.append({"name": name, "strict": bool(strict) if strict is not None else None})
                    continue
                except Exception:
                    pass
            # Format B: single-line (with optional round=N)
            m = FMT_B.search(line)
            if m:
                name, success, strict = m.group(1), m.group(2), m.group(3)
                if name not in seen:
                    seen.add(name)
                    results.append({"name": name, "strict": strict == "True"})
    return results
```

**Import the parser from `tools/scenario_log_parser.py` rather than copying it**, so that a new format (a Format C in some future app) needs a change in one file only.

---

## Validation: how to catch a new format

After any sweep, count the cells with parsed scenarios and the cells that have a log but no scenarios (for example with `parse_scenarios_from_log` from `tools/scenario_log_parser.py`). A cell with a log but no scenarios is expected only when Android Runner crashed before any scenario ran.

If the `no scenarios` count is unexpectedly high for cells that DID complete AR successfully, a new format is being dropped. Manually inspect one such cell log (the per-cell stream logs are raw data, available on request) with:
```
grep -iE "scenario|coverage" tools/sweep_logs/parallel_*_<phone>_<app>_<variant>_r<rep>.log
```

Any line that mentions "scenario" but isn't captured by the two regexes above is a new format. Update this document and `tools/scenario_log_parser.py`, then re-run.
