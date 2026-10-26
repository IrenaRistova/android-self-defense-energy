"""Per-app Appium harness for qubacy ItsOK (``com.qubacy.itsok``).

Mental-health chat app: ChatFragment (start) → GeneralSettingsFragment →
PositiveMementoesFragment → MementoEditorDialogFragment. Conversation flow:
user types a "gripe" → ENTER → bot replies with a memento + positive/negative
buttons; positive → bye stage; negative → another memento.

CAUTION: the "Bye!" button calls ``finishAndRemoveTask()`` which closes the
entire app: workload scenarios MUST avoid tapping it (use "Restart" instead).
"""

from .scenarios import run_workload

__all__ = ["run_workload"]
