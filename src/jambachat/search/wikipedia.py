from typing import Any
from urllib.parse import quote

import httpx

from jambachat.tools.http import TTLCache, request_json
from jambachat.tools.registry import ToolError

SEARCH_URL = "https://{lang}.wikipedia.org/w/rest.php/v1/search/page"
SUMMARY_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
MAX_EXTRACT = 1500


class WikipediaClient:
    def __init__(self, http: httpx.AsyncClient, language: str = "pt") -> None:
        self._http = http
        self._lang = language
        self._cache = TTLCache(6 * 3600)

    async def lookup(self, query: str) -> dict[str, Any]:
        key = query.strip().casefold()
        cached = self._cache.get(key)
        if isinstance(cached, dict):
            return cached
        found = await request_json(
            self._http,
            "GET",
            SEARCH_URL.format(lang=self._lang),
            service="enciclopédia",
            params={"q": query, "limit": 3},
        )
        pages = found.get("pages") if isinstance(found, dict) else None
        if not pages:
            raise ToolError(f"nada encontrado na Wikipédia para {query!r}")
        best = pages[0]
        title = str(best.get("key") or best.get("title") or "")
        summary = await request_json(
            self._http,
            "GET",
            SUMMARY_URL.format(lang=self._lang, title=quote(title, safe="")),
            service="enciclopédia",
            not_found_ok=True,
        )
        if not isinstance(summary, dict):
            raise ToolError(f"nada encontrado na Wikipédia para {query!r}")
        extract = " ".join(str(summary.get("extract") or "").split())
        result = {
            "title": summary.get("title") or best.get("title"),
            "description": summary.get("description"),
            "extract": extract[:MAX_EXTRACT],
            "url": ((summary.get("content_urls") or {}).get("desktop") or {}).get("page"),
            "other_matches": [str(p.get("title")) for p in pages[1:] if p.get("title")],
        }
        self._cache.set(key, result)
        return result
