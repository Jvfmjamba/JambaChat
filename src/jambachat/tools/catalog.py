import httpx

from jambachat.config import Settings
from jambachat.expenses.tools import EXPENSE_TOOLS
from jambachat.reminders.tools import REMINDER_TOOLS
from jambachat.search.tavily import TavilyClient
from jambachat.search.tools import build_search_tools
from jambachat.tools.registry import Tool, ToolRegistry
from jambachat.utilities.tools import OFFLINE_TOOLS, build_utility_tools


def build_registry(
    settings: Settings | None = None, http: httpx.AsyncClient | None = None
) -> ToolRegistry:
    tools: list[Tool] = [*REMINDER_TOOLS, *EXPENSE_TOOLS, *OFFLINE_TOOLS]
    if http is not None:
        tools += build_utility_tools(http)
        tavily_key = settings.tavily_api_key.get_secret_value() if settings else ""
        if tavily_key:
            tools += build_search_tools(TavilyClient(http, tavily_key))
    return ToolRegistry(tools)
