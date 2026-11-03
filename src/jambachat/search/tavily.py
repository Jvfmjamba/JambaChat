from dataclasses import dataclass
from typing import Literal

import httpx

from jambachat.tools.http import request_json
from jambachat.tools.registry import ToolError

TAVILY_URL = "https://api.tavily.com/search"
MAX_SNIPPET = 600
Topic = Literal["general", "news"]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    answer: str | None
    results: list[SearchResult]


class TavilyClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str, timeout: float = 20.0) -> None:
        self._http = http
        self._api_key = api_key
        self._timeout = timeout

    async def search(
        self, query: str, *, topic: Topic = "general", max_results: int = 5, days: int | None = None
    ) -> SearchResponse:
        payload: dict[str, object] = {
            "query": query,
            "topic": topic,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": True,
        }
        if days is not None and topic == "news":
            payload["days"] = days
        data = await request_json(
            self._http,
            "POST",
            TAVILY_URL,
            service="pesquisa",
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout_seconds=self._timeout,
        )
        if not isinstance(data, dict):
            raise ToolError("o serviço de pesquisa respondeu algo inesperado")
        results = []
        for item in data.get("results") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            snippet = " ".join(str(item.get("content") or "").split())
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    snippet=snippet[:MAX_SNIPPET],
                    published=item.get("published_date"),
                )
            )
        answer = data.get("answer")
        return SearchResponse(answer=str(answer) if answer else None, results=results)
