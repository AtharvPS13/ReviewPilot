"""Compare RAG off/on through the Action entry point without GitHub writes."""

import argparse
import difflib
import json
import logging
import os
import statistics
import sys
import threading
import time
import uuid
from contextlib import ExitStack
from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote
from wsgiref.simple_server import make_server

import httpx

import reviewpilot.main as action
from reviewpilot.config import ReviewPilotConfig
from reviewpilot.rag.client import retrieve_guidance
from reviewpilot.rag.service import create_app
from reviewpilot.rag.store import FixtureEmbedder, PostgresStore
from reviewpilot.review.models import GuidanceIssue, GuidanceReviewResponse, PRReviewResponse
from tests.rag_support import CORPUS, REPO, MemoryStore, fixture_snapshot


def synthetic_review(*, messages, response_model, **settings):
    """Fixed response policy standing in for the provider in both arms."""
    del settings
    user_text = messages[-1]["content"]
    if response_model is PRReviewResponse:
        return PRReviewResponse(summary="Fixture", risk_level="low")
    code, reference_json = user_text.split("\n\nRepository reference data (JSON):\n", 1)
    issues = []
    for item in json.loads(reference_json):
        explanation = ""
        if "error_code and message" in item["excerpt"] and "return {'message':" in code:
            explanation = 'The handler returns a message without the required error_code.'
        elif "timezone-aware" in item["excerpt"] and "return datetime.now()" in code:
            explanation = 'Use datetime.now(timezone.utc) to produce a timezone-aware timestamp.'
        if explanation:
            issues.append(GuidanceIssue(
                repository_convention=True, guidance_id=item["id"], file_path=item["changed_path"],
                start_line=2, end_line=2, severity="major", category="maintainability",
                reasoning="Fixture response policy", explanation=explanation,
                can_generate_patch=False,
            ))
    return GuidanceReviewResponse(summary="Fixture", risk_level="low", issues=issues)


def run_case(case, enabled, *, repo, dry_transport, real_model):
    file = case["path"]
    diff = f"diff --git a/{file} b/{file}\n" + "".join(difflib.unified_diff(
        case["old"].splitlines(True), case["new"].splitlines(True), f"a/{file}", f"b/{file}",
    ))
    github = MagicMock()
    github.get_pr_diff.return_value = diff
    github.get_pr_files_content.return_value = {file: case["new"]}
    github.get_pr.return_value = SimpleNamespace(title=case["name"], body="")
    config = ReviewPilotConfig(sandbox={"enabled": False}, llm={"temperature": 0.0})
    retrieval_ms = None
    locators = []
    expected_citations = set()

    async def capture(repo_id, changes):
        nonlocal retrieval_ms
        start = time.perf_counter()
        matches = await retrieve_guidance(repo_id, changes)
        if enabled:
            retrieval_ms = (time.perf_counter() - start) * 1000
        for match in matches:
            locators.append(match.source.locator)
            if match.source.locator == case["expected_locator"]:
                latter = f"#{match.source.locator}" if match.source.kind == "markdown" else ""
                label = escape(match.source.label()).replace("[", r"\[").replace("]", r"\]")
                url = (
                    f"https://github.com/{repo_id}/blob/{match.source.revision}/"
                    f"{quote(match.source.path)}{latter}"
                )
                expected_citations.add(f"Repository guidance: [{label}]({url})")
        return matches

    endpoint = os.environ.get("REVIEWPILOT_RAG_URL", "http://127.0.0.1:8080")
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "GITHUB_REPOSITORY": repo, "PR_NUMBER": "1",
            "REVIEWPILOT_RAG_URL": endpoint if enabled else "",
        }))
        if dry_transport:
            stack.enter_context(patch("reviewpilot.rag.client._search", dry_transport))
        stack.enter_context(patch.object(action, "GitHubClient", return_value=github))
        stack.enter_context(patch.object(action, "load_config", return_value=config))
        stack.enter_context(patch.object(action, "retrieve_guidance", side_effect=capture))
        if not real_model:
            fake = SimpleNamespace(create=AsyncMock(side_effect=synthetic_review))
            stack.enter_context(patch("instructor.from_litellm", return_value=fake))
        started = time.perf_counter()
        action.main()
        elapsed_ms = (time.perf_counter() - started) * 1000
    comments = github.post_review.call_args
    bodies = [comment["body"] for comment in comments.args[2]] if comments else []
    expected_terms = {
        "api_error": ("error_code",),
        "naive_job_time": ("timezone", "utc"),
    }
    correct = [
        body for body in bodies
        if case["violation"]
        and not expected_citations.isdisjoint(body.splitlines())
        and any(term in body.split("\nRepository guidance:", 1)[0].lower().replace("\\", "")
                for term in expected_terms.get(case["name"], ()))
    ]
    return {
        "case": case["name"], "enabled": enabled, "hit": case["expected_locator"] in locators,
        "has_reference": case["expected_locator"] is not None, "violation": case["violation"],
        "grounded": bool(correct), "false_positives": len(bodies) - min(1, len(correct)),
        "review_ms": round(elapsed_ms, 2),
        "retrieval_ms": round(retrieval_ms, 2) if retrieval_ms is not None else None,
        "comments": bodies,
    }


def evaluate(*, live_service=False, real_model=False):
    corpus = json.loads((CORPUS / "prs.json").read_text(encoding="utf-8"))
    repo = os.environ.get("RAG_REPO_ID", REPO) if live_service else REPO
    transport = None
    if live_service and not os.environ.get("REVIEWPILOT_RAG_URL"):
        raise RuntimeError("Set REVIEWPILOT_RAG_URL for --live-service")
    if not live_service:
        store = MemoryStore()
        application = create_app(repo, "fixture-token", store, FixtureEmbedder(), 0.09)
        client = httpx.Client(transport=httpx.WSGITransport(app=application), base_url="http://test")
        response = client.post(
            "/v1/index", json=fixture_snapshot().model_dump(),
            headers={"X-Index-Token": "fixture-token"},
        )
        response.raise_for_status()

        async def transport(_client, _url, request):
            from reviewpilot.rag.models import SearchResult

            response = client.post("/v1/retrieve", json=request.model_dump())
            response.raise_for_status()
            return SearchResult.model_validate_json(response.content).matches

    rows = [
        run_case(case, enabled, repo=repo, dry_transport=transport, real_model=real_model)
        for enabled in (False, True)
        for case in corpus
    ]
    if not live_service:
        client.close()
    return rows


def evaluate_loopback(*, real_model=False, repo=REPO, store=None):
    """Use a real local HTTP connection with deterministic fixture embeddings."""
    if store is None:
        store = MemoryStore()
    application = create_app(repo, "fixture-writer", store, FixtureEmbedder(), 0.09)
    with make_server("127.0.0.1", 0, application) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        try:
            response = httpx.post(
                endpoint + "/v1/index", json=fixture_snapshot(repo).model_dump(),
                headers={"X-Index-Token": "fixture-writer"}, trust_env=False, timeout=15,
            )
            response.raise_for_status()
            with patch.dict(os.environ, {"REVIEWPILOT_RAG_URL": endpoint, "RAG_REPO_ID": repo}):
                return evaluate(live_service=True, real_model=real_model)
        finally:
            server.shutdown()
            worker.join()


def evaluate_postgres(*, real_model=False):
    import psycopg

    def connect():
        return psycopg.connect(os.environ["REVIEWPILOT_TEST_PG_DSN"], connect_timeout=5)

    store = PostgresStore(connect)
    repo = f"fixture/{uuid.uuid4().hex}"
    try:
        store.initialize()
        try:
            return evaluate_loopback(real_model=real_model, repo=repo, store=store)
        finally:
            store.replace(repo, [], FixtureEmbedder())
    except psycopg.Error as exc:
        raise SystemExit(f"PostgreSQL fixture run failed ({type(exc).__name__}).") from None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live-service", action="store_true")
    mode.add_argument("--loopback", action="store_true")
    mode.add_argument("--postgres", action="store_true", help="loopback HTTP with real pgvector")
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--details", action="store_true", help="print results for each fixture")
    options = parser.parse_args()
    if options.real_model and not os.environ.get("OPENAI_API_KEY"):
        parser.error("--real-model requires OPENAI_API_KEY")
    if options.postgres and not os.environ.get("REVIEWPILOT_TEST_PG_DSN"):
        parser.error("--postgres requires REVIEWPILOT_TEST_PG_DSN")
    if not options.real_model:
        logging.getLogger("reviewpilot.review.llm_reviewer").setLevel(logging.ERROR)
    service_label = "in-process WSGI with in-memory store"
    if options.loopback:
        service_label = "loopback HTTP with in-memory store"
    elif options.postgres:
        service_label = "loopback HTTP with PostgreSQL/pgvector and deterministic embeddings"
    elif options.live_service:
        service_label = "configured HTTP service"
    print(json.dumps({
        "service": service_label,
        "review_model": "gpt-4o-mini" if options.real_model else "synthetic fixture model",
        "github": "mocked",
    }))
    if options.loopback:
        rows = evaluate_loopback(real_model=options.real_model)
    elif options.postgres:
        rows = evaluate_postgres(real_model=options.real_model)
    else:
        rows = evaluate(live_service=options.live_service, real_model=options.real_model)
    for enabled in (False, True):
        arm = [row for row in rows if row["enabled"] == enabled]
        latency = [row["retrieval_ms"] for row in arm if row["retrieval_ms"] is not None]
        print(json.dumps({
            "retrieval": "on" if enabled else "off", "fixtures": len(arm),
            "hits_at_2": sum(row["hit"] for row in arm),
            "applicable_fixtures": sum(row["has_reference"] for row in arm),
            "correctly_grounded": sum(row["grounded"] for row in arm),
            "labeled_violations": sum(row["violation"] for row in arm),
            "false_positive_findings": sum(row["false_positives"] for row in arm),
            "review_median_ms": round(statistics.median(row["review_ms"] for row in arm), 2),
            "retrieval_median_ms": round(statistics.median(latency), 2) if latency else None,
        }))
    if options.details:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    positive = next(row for row in rows if row["enabled"] and row["case"] == "api_error")
    if positive["comments"]:
        print("\n" + positive["comments"][0])
    enabled_rows = [row for row in rows if row["enabled"]]
    passed = all(
        (not row["has_reference"] or row["hit"])
        and (not row["violation"] or row["grounded"])
        and not row["false_positives"]
        for row in enabled_rows
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
