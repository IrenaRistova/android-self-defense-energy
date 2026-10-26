"""
Per-app Appium harness for **JoshLudahl Tipuous** (``com.tips.tipuous``).

Public surface:

- :func:`run_workload(experiment, device)`: entrypoint called by the thin
  ``Scripts/interaction_appium_tipuous.py`` Android Runner hook.

All Tipuous-specific scenarios, selector defaults, and the workload-mode dispatch
live in :mod:`.scenarios`.
"""

from .scenarios import run_workload

__all__ = ["run_workload"]
