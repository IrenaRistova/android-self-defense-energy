"""
Per-app Appium harness for **AmrDeveloper LinkHub** (``com.amrdeveloper.linkhub``).

Public surface:

- :func:`run_workload(experiment, device)`: entrypoint called by the thin
  ``Scripts/interaction_appium_linkhub.py`` Android Runner hook.

All LinkHub-specific scenarios, selector defaults, and the workload entrypoint live in
:mod:`.scenarios`. Per-scenario scoring lives in :mod:`.scoring`.
"""

from .scenarios import run_workload

__all__ = ["run_workload"]
