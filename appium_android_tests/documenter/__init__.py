"""
Per-app Appium harness for **ViliusSutkus89 Documenter** (``com.viliussutkus89.documenter``,
debug-variant package ``com.viliussutkus89.documenter.debug``).

Public surface:

- :func:`run_workload(experiment, device)`: entrypoint called by the thin
  ``Scripts/interaction_appium_documenter.py`` Android Runner hook.

All Documenter-specific scenarios, selector defaults, and the workload-mode dispatch
live in :mod:`.scenarios`; per-scenario scoring in :mod:`.scoring`.
"""

from .scenarios import run_workload

__all__ = ["run_workload"]
