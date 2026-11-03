import httpx

from jambachat.config import Settings
from jambachat.expenses.budgets import BUDGET_TOOLS
from jambachat.expenses.tools import EXPENSE_TOOLS
from jambachat.lists.tools import LIST_TOOLS
from jambachat.reminders.tools import REMINDER_TOOLS
from jambachat.search.tavily import TavilyClient
from jambachat.search.tools import build_encyclopedia_tools, build_search_tools
from jambachat.search.wikipedia import WikipediaClient
from jambachat.tools.registry import Tool, ToolRegistry
from jambachat.users.tools import PROFILE_TOOLS
from jambachat.utilities.tools import OFFLINE_TOOLS, build_utility_tools


def build_registry(
    settings: Settings | None = None, http: httpx.AsyncClient | None = None
) -> ToolRegistry:
    tools: list[Tool] = [
        *REMINDER_TOOLS,
        *EXPENSE_TOOLS,
        *BUDGET_TOOLS,
        *LIST_TOOLS,
        *OFFLINE_TOOLS,
        *PROFILE_TOOLS,
    ]
    if http is not None:
        tools += build_utility_tools(http)
        tools += build_encyclopedia_tools(WikipediaClient(http))
        tavily_key = settings.tavily_api_key.get_secret_value() if settings else ""
        if tavily_key:
            tools += build_search_tools(TavilyClient(http, tavily_key))
    return ToolRegistry(tools)
