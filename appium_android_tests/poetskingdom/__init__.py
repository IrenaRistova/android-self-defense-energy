"""Per-app Appium harness for tinochings PoetsKingdom (``com.wendorochena.poetskingdom``).

Single MainActivity hosts a Navigation Component (NavHostFragment). FirstFragment is
the home: 4 image-tiles (createPoemImage, myImages, myPoemImage, personalisationImage)
that launch separate Activities. NavGraph also has a myImages destination reachable
via nav action.
"""
from .scenarios import run_workload
__all__ = ["run_workload"]
