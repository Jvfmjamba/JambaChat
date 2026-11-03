from jambachat.reminders.tools import REMINDER_TOOLS
from jambachat.tools.registry import ToolRegistry


def build_registry() -> ToolRegistry:
    return ToolRegistry([*REMINDER_TOOLS])
