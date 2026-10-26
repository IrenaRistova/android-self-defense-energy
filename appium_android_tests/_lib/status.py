"""
``appium_status.json`` writer.

This artifact summarises whether the Appium hook actually got a session up and how many UI steps
ran. It is the primary forensic signal for distinguishing "the energy run looks low because the
app idled" from "the energy run looks low because Appium never connected".

Schema (all keys are populated even when the run aborts early):

- ``session_created``               (bool): ``webdriver.Remote()`` returned a driver.
- ``session_start_seconds``         (float|null): wall-clock seconds taken by the connect call.
- ``workload_started``              (bool): UI workload entered after handshake.
- ``steps_executed``                (int): workload-specific step / cycle count.
- ``session_connect_timeout_seconds`` (float): wall-clock cap used for the connect attempt.
- ``workload_mode``                 (str): value of ``APPIUM_WORKLOAD`` at run time.
- ``failure_reason``                (str|null): first exception summary, if any.
- ``session_timeout``               (bool): ``True`` when the failure was a connect-timeout
  (used by run-classification scripts to bucket SESSION_TIMEOUT separately from crashes).

Two writers are exposed:

- :func:`write_appium_status`: uses ``log()`` (visible in ``experiment.log``).
- :func:`write_status_raw`: same payload, no logging dependency. Safe during interpreter
  shutdown / early connect-timeout paths where the logging stack may be partially torn down.

The artifact is written under :func:`coverage.resolve_artifact_dir` (i.e. ``paths.OUTPUT_DIR``
when Android Runner is the parent, else ``APPIUM_UI_DUMP_DIR`` parent).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import log


def write_appium_status(artifact_dir: Optional[str], payload: dict[str, Any]) -> None:
    """Write ``appium_status.json`` under ``artifact_dir`` (no-op if ``artifact_dir`` is None).

    Uses ``fsync`` to survive SIGTERM from Android Runner cleanup, and ``log()`` to record the path
    in ``experiment.log``. Failures are logged but do not raise.
    """
    if not artifact_dir:
        return
    path = os.path.join(artifact_dir, "appium_status.json")
    try:
        os.makedirs(artifact_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass
        log("wrote %s" % path)
    except Exception as ex:
        log("appium_status.json write failed: %s %s" % (type(ex).__name__, ex))


def write_status_raw(artifact_dir: Optional[str], payload: dict[str, Any]) -> None:
    """Same payload as :func:`write_appium_status` but **without** the ``log()`` dependency.

    Used by the connect-timeout fast path when the logging stack might already be partially shut
    down (the daemon connect thread can outlive the main process and we still want the JSON
    artifact on disk).
    """
    if not artifact_dir:
        return
    path = os.path.join(artifact_dir, "appium_status.json")
    try:
        os.makedirs(artifact_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass
    except Exception:
        pass
