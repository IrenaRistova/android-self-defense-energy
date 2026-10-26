"""
Per-app Appium harness for **afreakyelf Pdf-Viewer sample app** (``com.rajat.sample.pdfviewer``).

Public surface:

- :func:`run_workload(experiment, device)`: entrypoint called by the thin
  ``Scripts/interaction_appium_pdfviewer.py`` Android Runner hook.

All Pdf-Viewer-specific scenarios, selector defaults, and workload-mode dispatch live in
:mod:`.scenarios`.
"""

from .scenarios import run_workload

__all__ = ["run_workload"]
