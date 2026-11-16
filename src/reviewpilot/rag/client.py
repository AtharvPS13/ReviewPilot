"""Opt-in Action client for repository guidance."""

import asyncio
import logging
import os

import httpx

from reviewpilot.rag.models import Change, Match, Search, SearchResult

logger = logging.getLogger(__name__)


def is_render_url(url: httpx.URL) -> bool:
    return url.host.endswith(".onrender.com")


def service_url(value: str) -> httpx.URL:
    url = httpx.URL(value)
    if (
        url.userinfo or url.query or url.fragment or url.path != "/"
        or not (
            (url.scheme == "https" and url.port is None
             and (url.host.endswith(".run.app") or is_render_url(url)))
            or (url.scheme == "http" and url.host in {"127.0.0.1", "localhost", "::1"})
        )
    ):
        raise ValueError("Expected a Render or Cloud Run HTTPS URL, or a loopback HTTP URL")
    return url


async def _search(client: httpx.AsyncClient, url: httpx.URL, request: Search) -> list[Match]:
    response = await client.post(url.copy_with(path="/v1/retrieve"), json=request.model_dump())
    response.raise_for_status()
    return SearchResult.model_validate_json(response.content).matches


async def retrieve_guidance(repo_id: str, changes: list[dict[str, str]]) -> list[Match]:
    # Read connection details from trusted workflow inputs, never PR files.
    endpoint = os.environ.get("REVIEWPILOT_RAG_URL")
    if not endpoint or not changes:
        return []
    try:
        url = service_url(endpoint)
        token_env = "REVIEWPILOT_RAG_API_TOKEN" if is_render_url(url) else "REVIEWPILOT_RAG_TOKEN"
        token = os.environ.get(token_env)
        if url.scheme == "https" and not token:
            raise ValueError("Missing guidance service credential")
        timeout = min(30.0, max(0.1, float(os.environ.get("REVIEWPILOT_RAG_TIMEOUT", "8"))))
        request = Search(
            repo_id=repo_id.lower(),
            changes=[Change.model_validate(change) for change in changes[:8]],
        )
        headers = {"Authorization": f"Bearer {token}"} if url.scheme == "https" else {}
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, trust_env=False, verify=url.scheme == "https",
        ) as client:
            matches = await asyncio.wait_for(_search(client, url, request), timeout)
        paths = {change.path for change in request.changes}
        return [match for match in matches if match.changed_path in paths]
    except (TimeoutError, httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else ""
        logger.warning(
            "Repository guidance unavailable (%s %s). Continuing without it.",
            type(exc).__name__,
            status,
        )
        return []
