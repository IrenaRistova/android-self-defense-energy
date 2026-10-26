"""
Workload-window timing: keeps Android Runner's child process alive for the full UI window.

Android Runner launches Python ``interaction`` hooks via ``AndroidRunner.Script.Python3``, which
runs them in a **child process**. If ``main()`` returns immediately, that process is terminated
while ``POST /session`` may still be in flight, and Appium ends up hung or aborted (standalone
smoke tests don't see this because they run in-process).

The contract enforced here:

1. Caller hands us a ``workload`` callable (no args) and a ``duration_s``.
2. We start ``workload`` in a non-daemon thread and ``time.sleep(duration_s)`` so the child
   process stays alive for the full UI window.
3. After the sleep, we ``join()`` the workload thread with a generous budget
   (``max(180s, duration_s + 90s)``) to absorb tail-end teardown (driver.quit, status writes).
4. If the thread is still alive after the join, we log a warning to stderr: the parent then has
   evidence to investigate the Appium server or a stuck UI command.

Pair this with ``"interaction_covers_duration": true`` in the experiment JSON so
``NativeExperiment.interaction()`` does not add a second ``sleep(duration)`` (which would double
the run length and the energy window).
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import emit_stderr, log


def start_workload_thread_and_block(
    workload: Callable[[], None],
    duration_s: float,
    thread_name: str = "appium-workload",
) -> threading.Thread:
    """Start ``workload`` in a thread and block ``main()`` for ``duration_s`` seconds, then join.

    Returns the (possibly still-alive) thread for caller inspection. Never raises: the workload's
    own try/except is responsible for capturing failures into ``appium_status.json``.

    Implementation notes:

    - The workload thread is **non-daemon** so the process does not exit before Appium teardown.
    - The block is a single ``time.sleep(duration_s)``. We deliberately do not interrupt the sleep
      on early thread completion: the Android Runner profiler windows are sized to ``duration``
      and an early return would compress them.
    - The join budget after the sleep is generous to avoid orphaning a still-active driver.quit.
    """
    t = threading.Thread(target=workload, name=thread_name, daemon=False)
    t.start()
    log(
        "workload thread started; blocking %.1fs to keep Android Runner Script subprocess alive "
        "(interaction_covers_duration in JSON avoids duplicate NativeExperiment sleep)"
        % duration_s,
    )
    emit_stderr(
        "INTERACTION_APPIUM [stderr]: sleeping %.1fs — Python3 hooks run in a subprocess that is "
        "terminated when main() returns; without this, POST /session is cut off (standalone smoke "
        "works because it is not multiprocess)."
        % duration_s,
    )
    time.sleep(float(duration_s))
    join_budget = max(180.0, float(duration_s) + 90.0)
    t.join(timeout=join_budget)
    if t.is_alive():
        emit_stderr(
            "INTERACTION_APPIUM [stderr]: WARNING workload thread still running after %.1fs join"
            % join_budget,
        )
        log("WARNING: workload thread still alive after join — Appium session may still be busy")
    return t


def start_workload_thread_and_block_until_done(
    workload: Callable[[], None],
    max_wait_s: float,
    thread_name: str = "appium-workload",
) -> threading.Thread:
    """Start ``workload`` in a non-daemon thread and block until it finishes (with safety cap).

    Used by the per-app fixed-N scenario contract (2026-05-08):
    the workload itself dictates the wall-clock window, so wall time is a *measured output*, not a
    controlled input. We don't pad with idle sleep. ``max_wait_s`` exists only as a failsafe
    against hangs (a stuck Appium session, an infinite loop in a scenario, an unkillable wait).

    Differs from :func:`start_workload_thread_and_block` in that we ``join`` the thread directly
    instead of sleeping for a fixed ``duration_s`` first. This causes the AndroidRunner profiler
    window to track the actual workload duration: shorter on fast devices, longer on slow ones.

    Returns the (possibly still-alive) thread for caller inspection. Never raises: the
    workload's own try/except is responsible for capturing failures into ``appium_status.json``.
    """
    t = threading.Thread(target=workload, name=thread_name, daemon=False)
    t.start()
    log(
        "workload thread started; blocking on join (failsafe cap %.1fs) — per-app scenario-count "
        "contract, wall time is measured output (interaction_covers_duration in JSON avoids "
        "duplicate NativeExperiment sleep)" % max_wait_s,
    )
    emit_stderr(
        "INTERACTION_APPIUM [stderr]: per-app scenario-count contract — blocking on thread join "
        "with failsafe cap %.1fs (NOT a fixed sleep; thread exits when scenarios complete)."
        % max_wait_s,
    )
    t.join(timeout=float(max_wait_s))
    if t.is_alive():
        emit_stderr(
            "INTERACTION_APPIUM [stderr]: WARNING workload thread still running after %.1fs cap "
            "— Appium session may be stuck on a single scenario (check appium_status.json + "
            "espresso_mirror coverage tail for the last completed scenario)." % max_wait_s,
        )
        log(
            "WARNING: workload thread still alive after %.1fs failsafe cap — investigate stuck "
            "scenario (last coverage row in appium_ui_dumps/coverage.jsonl)" % max_wait_s,
        )
    return t
