"""WSGI indexing and retrieval service."""

import hmac
import json
import logging
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx

from reviewpilot.rag.ingest import index_items
from reviewpilot.rag.models import Search, SearchResult, Snapshot
from reviewpilot.rag.store import DIMENSIONS, FixtureEmbedder, PostgresStore

if TYPE_CHECKING:
    from reviewpilot.rag.store import Embedder

logger = logging.getLogger(__name__)
MAX_BODY = 512 * 1024


class VertexEmbedder:
    name = "gemini-embedding-001:768"

    def __init__(self, project: str, location: str):
        from google import genai
        from google.genai import types

        self.client = genai.Client(
            vertexai=True, project=project, location=location,
            http_options=types.HttpOptions(api_version="v1", timeout=5000),
        )

    def embed(self, text: str, *, query: bool = False) -> list[float]:
        from google.genai import types

        result = self.client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
                output_dimensionality=DIMENSIONS,
            ),
        )
        if not result.embeddings or not result.embeddings[0].values:
            raise ValueError("Vertex AI returned no embedding")
        return result.embeddings[0].values


class OpenAIEmbedder:
    model = "text-embedding-3-small"
    name = f"openai:{model}:{DIMENSIONS}"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("OPENAI_API_KEY must be configured")
        self.api_key = api_key

    def embed(self, text: str, *, query: bool = False) -> list[float]:
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "dimensions": DIMENSIONS, "input": text},
            timeout=10, trust_env=False,
        )
        if not response.is_success:
            code = None
            try:
                payload = response.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict):
                    code = error.get("code")
            except ValueError:
                pass
            known_codes = (
                "invalid_api_key", "insufficient_quota", "rate_limit_exceeded",
                "credit_balance_exhausted", "project_spend_limit_exceeded",
                "organization_usage_limit_exceeded", "organization_spend_limit_exceeded",
                "model_not_found", "insufficient_permissions",
            )
            logger.error("OpenAI embeddings failed (HTTP %d, code=%s)", response.status_code,
                         code if code in known_codes else "unavailable")
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]


def create_app(repo_id: str, index_token: str, store: PostgresStore, embedder: "Embedder",
               min_similarity: float = 0.35, *, read_token: str = ""):
    if not index_token:
        raise ValueError("RAG_INDEX_TOKEN must be configured")
    if read_token and read_token == index_token:
        raise ValueError("Retrieval and indexing tokens must differ")

    def handle(environ):
        path = environ["PATH_INFO"]
        if path == "/healthz" and environ["REQUEST_METHOD"] == "GET":
            return "200 OK", {"status": "ok"}
        if environ["REQUEST_METHOD"] != "POST" or path not in {"/v1/index", "/v1/retrieve"}:
            return "404 Not Found", {"error": "Not found"}
        if path == "/v1/index" and not hmac.compare_digest(
            environ.get("HTTP_X_INDEX_TOKEN", "").encode(), index_token.encode()
        ):
            return "403 Forbidden", {"error": "Index authorization required"}
        if path == "/v1/retrieve" and read_token and not hmac.compare_digest(
            environ.get("HTTP_AUTHORIZATION", "").encode(), f"Bearer {read_token}".encode()
        ):
            return "401 Unauthorized", {"error": "Retrieval authorization required"}
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
            if not 0 < length <= MAX_BODY:
                return "413 Payload Too Large", {"error": "Invalid request size"}
            payload = environ["wsgi.input"].read(length)
            request = (Snapshot if path == "/v1/index" else Search).model_validate_json(payload)
            if request.repo_id != repo_id:
                return "403 Forbidden", {"error": "Repository is outside service scope"}
            if isinstance(request, Snapshot):
                items = index_items(request)
        except ValueError:
            # Validation errors may contain source text. Keep them out of responses and logs.
            return "400 Bad Request", {"error": "Invalid guidance request or source document"}
        try:
            if isinstance(request, Snapshot):
                count = store.replace(repo_id, items, embedder)
                return "200 OK", {"indexed": count, "revision": request.revision}
            matches = [
                match
                for change in request.changes
                for match in store.search(
                    repo_id, change.path,
                    embedder.embed(f"{change.path}\n{change.context}", query=True),
                    embedder.name, request.k, min_similarity,
                )
            ]
            return "200 OK", SearchResult(matches=matches).model_dump()
        except Exception as exc:
            logger.error("Guidance operation failed (%s)", type(exc).__name__)
            return "503 Service Unavailable", {"error": "Guidance operation failed"}

    def app(environ, start_response):
        status, data = handle(environ)
        body = json.dumps(data).encode()
        start_response(status, [
            ("Content-Type", "application/json"), ("Content-Length", str(len(body))),
        ])
        return [body]

    return app


def app_from_env():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    render = bool(os.environ.get("RENDER_SERVICE_ID"))
    read_token = os.environ.get("RAG_READ_TOKEN", "")
    dsn = os.environ["RAG_DATABASE_URL"]
    if render:
        if not read_token:
            raise ValueError("RAG_READ_TOKEN must be configured on Render")
        try:
            sslmode = conninfo_to_dict(dsn).get("sslmode")
        except psycopg.Error:
            raise ValueError("Invalid Render database connection string") from None
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("Render database connections must require TLS")

    @contextmanager
    def connect():
        with psycopg.connect(dsn, connect_timeout=4) as conn:
            conn.execute("SET LOCAL statement_timeout = '4s'")
            conn.execute("SET LOCAL lock_timeout = '3s'")
            yield conn

    store = PostgresStore(connect)
    flavor = os.environ.get("RAG_EMBEDDER", "openai" if render else "vertex")
    embedder: Embedder
    if flavor == "fixture":
        if render or os.environ.get("K_SERVICE"):
            raise ValueError("Fixture embeddings are only for local development")
        embedder = FixtureEmbedder()
    elif flavor == "vertex":
        embedder = VertexEmbedder(
            os.environ["GOOGLE_CLOUD_PROJECT"], os.environ.get("VERTEX_LOCATION", "global")
        )
    elif flavor == "openai":
        embedder = OpenAIEmbedder(os.environ["OPENAI_API_KEY"])
    else:
        raise ValueError("Unknown embedding backend")
    application = create_app(
        os.environ["RAG_REPO_ID"].lower(), os.environ["RAG_INDEX_TOKEN"], store, embedder,
        float(os.environ.get("RAG_MIN_SIMILARITY", "0.35")), read_token=read_token,
    )
    try:
        store.initialize()
    except psycopg.Error as exc:
        raise RuntimeError(f"Database initialization failed ({type(exc).__name__}).") from None
    return application


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    with make_server("127.0.0.1", int(os.environ.get("PORT", "8080")), app_from_env()) as server:
        server.serve_forever()
