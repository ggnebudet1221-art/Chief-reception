from __future__ import annotations

from dataclasses import dataclass
import socket
from typing import Any

import aiohttp

from src.core.config import get_settings
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def _log_safe(text: str, limit: int = 500) -> str:
    value = (text or "")[:limit]
    return value.encode("ascii", "backslashreplace").decode("ascii")


@dataclass(frozen=True)
class SearchResult:
    title: str
    snippet: str
    link: str


@dataclass(frozen=True)
class SearchContext:
    query: str
    results: list[SearchResult]
    answer: str = ""
    answer_title: str = ""
    answer_source: str = ""


class WebSearchUnavailable(Exception):
    pass


class SerperSearchService:
    def __init__(self, timeout_seconds: float = 15.0, max_results: int = 5) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_results = max(1, min(max_results, 5))
        self.url = "https://google.serper.dev/search"

    async def webSearch(self, query: str) -> list[SearchResult]:
        return await self.search(query)

    async def search(self, query: str) -> list[SearchResult]:
        return (await self.search_context(query)).results

    async def search_context(self, query: str) -> SearchContext:
        clean_query = " ".join((query or "").split()).strip()
        if not clean_query:
            return SearchContext(query="", results=[])

        settings = get_settings()
        if not settings.serper_api_key:
            logger.warning("[WEB_SEARCH] unavailable", query=clean_query, reason="SERPER_API_KEY is empty")
            raise WebSearchUnavailable("SERPER_API_KEY is empty")

        headers = {
            "X-API-KEY": settings.serper_api_key,
            "Content-Type": "application/json",
        }
        try:
            status, body_preview, data = await self._post_json(headers, {"q": clean_query})
            logger.info(
                "[WEB_SEARCH] response",
                query=clean_query,
                status=status,
                body_preview=_log_safe(body_preview),
            )
            if status != 200:
                logger.warning("[WEB_SEARCH] failed", query=clean_query, status=status, preview=_log_safe(body_preview, 240))
                raise WebSearchUnavailable(f"Serper status {status}")
        except WebSearchUnavailable:
            raise
        except Exception as error:
            logger.warning("[WEB_SEARCH] unavailable", query=clean_query, error=str(error))
            raise WebSearchUnavailable(str(error)) from error

        context = self._parse_context(clean_query, data)
        logger.info(
            "[WEB_SEARCH]",
            query=clean_query,
            results_count=len(context.results),
            answer_present=bool(context.answer),
            parsed_results=[_log_safe(result.title, 120) for result in context.results[:3]],
        )
        return context

    async def _post_json(self, headers: dict[str, str], payload: dict[str, str]) -> tuple[int, str, dict[str, Any]]:
        errors: list[str] = []
        for attempt in range(2):
            try:
                connector = aiohttp.TCPConnector(family=socket.AF_INET)
                async with aiohttp.ClientSession(timeout=self.timeout, trust_env=True, connector=connector) as session:
                    async with session.post(self.url, headers=headers, json=payload) as response:
                        body = await response.text()
                        data = await response.json(content_type=None) if body else {}
                        return response.status, body, data
            except Exception as error:
                errors.append(f"aiohttp#{attempt + 1}:{type(error).__name__}:{error}")
                logger.warning("[WEB_SEARCH] aiohttp attempt failed", attempt=attempt + 1, error=str(error))

        try:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout.total or 15.0, trust_env=True) as client:
                response = await client.post(self.url, headers=headers, json=payload)
                body = response.text
                data = response.json() if body else {}
                return response.status_code, body, data
        except Exception as error:
            errors.append(f"httpx:{type(error).__name__}:{error}")
            logger.warning("[WEB_SEARCH] httpx fallback failed", error=str(error))
            raise WebSearchUnavailable("; ".join(errors)) from error

    async def debug(self, query: str = "weather in Kemer") -> dict[str, Any]:
        clean_query = " ".join((query or "").split()).strip() or "weather in Kemer"
        settings = get_settings()
        loaded = bool(settings.serper_api_key)
        debug: dict[str, Any] = {
            "query": clean_query,
            "key_loaded": loaded,
            "api_reachable": False,
            "status_code": None,
            "results_count": 0,
            "raw_preview": "",
            "error": "",
        }
        if not loaded:
            debug["error"] = "SERPER_API_KEY is empty"
            logger.warning("[SERPER] debug", **debug)
            return debug

        headers = {
            "X-API-KEY": settings.serper_api_key,
            "Content-Type": "application/json",
        }
        try:
            status, text, data = await self._post_json(headers, {"q": clean_query})
            debug["status_code"] = status
            debug["api_reachable"] = status == 200
            debug["raw_preview"] = text[:1000]
            if status == 200:
                context = self._parse_context(clean_query, data)
                debug["results_count"] = len(context.results)
                debug["answer"] = context.answer
                debug["answer_title"] = context.answer_title
                debug["answer_source"] = context.answer_source
                debug["parsed_results"] = [
                    {
                        "title": result.title,
                        "snippet": result.snippet[:160],
                        "link": result.link,
                    }
                    for result in context.results[:3]
                ]
        except Exception as error:
            debug["error"] = f"{type(error).__name__}: {error}"
        log_debug = dict(debug)
        for key, value in list(log_debug.items()):
            if isinstance(value, str):
                log_debug[key] = _log_safe(value, 1000 if key == "raw_preview" else 240)
        if "parsed_results" in log_debug:
            log_debug["parsed_results"] = [
                {
                    "title": _log_safe(str(item.get("title", "")), 120),
                    "snippet": _log_safe(str(item.get("snippet", "")), 160),
                    "link": _log_safe(str(item.get("link", "")), 240),
                }
                for item in log_debug.get("parsed_results", [])
                if isinstance(item, dict)
            ]
        logger.info("[SERPER] debug", **log_debug)
        return debug

    def _parse_context(self, query: str, data: dict[str, Any]) -> SearchContext:
        answer = ""
        answer_title = ""
        answer_source = ""
        answer_box = data.get("answerBox")
        if isinstance(answer_box, dict):
            answer_title = " ".join(str(answer_box.get("title") or "").split()).strip()
            answer = " ".join(
                str(
                    answer_box.get("answer")
                    or answer_box.get("snippet")
                    or answer_box.get("description")
                    or ""
                ).split()
            ).strip()
            answer_source = " ".join(
                str(answer_box.get("sourceLink") or answer_box.get("link") or answer_box.get("source") or "").split()
            ).strip()
        return SearchContext(
            query=query,
            results=self._parse_results(data),
            answer=answer[:700],
            answer_title=answer_title[:180],
            answer_source=answer_source[:500],
        )

    def _parse_results(self, data: dict[str, Any]) -> list[SearchResult]:
        raw_results = data.get("organic") or []
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").split()).strip()
            snippet = " ".join(str(item.get("snippet") or "").split()).strip()
            link = " ".join(str(item.get("link") or "").split()).strip()
            if not title and not snippet:
                continue
            results.append(SearchResult(title=title[:180], snippet=snippet[:360], link=link[:500]))
            if len(results) >= self.max_results:
                break
        return results


def format_search_context(results: list[SearchResult] | SearchContext) -> str:
    answer = ""
    answer_title = ""
    answer_source = ""
    if isinstance(results, SearchContext):
        answer = results.answer
        answer_title = results.answer_title
        answer_source = results.answer_source
        result_items = results.results
    else:
        result_items = results

    if not result_items and not answer:
        return "Web search context: results not found."

    lines = ["Web search context:"]
    if answer:
        direct = f"Direct answer: {answer}"
        if answer_title:
            direct += f" ({answer_title})"
        if answer_source:
            direct += f" Source: {answer_source}"
        lines.append(direct)

    for index, result in enumerate(result_items[:5], start=1):
        parts = [f"{index}. {result.title}" if result.title else f"{index}. Result"]
        if result.snippet:
            parts.append(result.snippet)
        if result.link:
            parts.append(f"Source: {result.link}")
        lines.append(" - ".join(parts))
    return "\n".join(lines)


async def webSearch(query: str) -> list[dict[str, str]]:
    results = await SerperSearchService().search(query)
    return [
        {
            "title": result.title,
            "snippet": result.snippet,
            "link": result.link,
        }
        for result in results
    ]
