"""
Per-app Appium harness for **exponentialGroth Calculator** (``com.exponential_groth.calculator``).

Public surface:

- :func:`run_workload(experiment, device)`: entrypoint called by the thin
  ``Scripts/interaction_appium_calculator.py`` Android Runner hook.

All Calculator-specific scenarios, selector defaults, and the workload-mode dispatch
live in :mod:`.scenarios`.
"""

from .scenarios import run_workload

__all__ = ["run_workload"]
