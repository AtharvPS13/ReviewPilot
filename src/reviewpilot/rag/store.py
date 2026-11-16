"""Transactional snapshot replacement and exact cosine search in PostgreSQL."""

import hashlib
import json
import math
import re
from collections.abc import Callable
from typing import Protocol

from reviewpilot.rag.ingest import Item
from reviewpilot.rag.models import Match, Reference

DIMENSIONS = 768

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviewpilot_guidance (
    repo_id text NOT NULL,
    source_id text NOT NULL,
    source jsonb NOT NULL,
    scopes text[] NOT NULL,
    embedder text NOT NULL,
    embedding vector(768) NOT NULL,
    PRIMARY KEY (repo_id, source_id)
);
"""


class Embedder(Protocol):
    name: str

    def embed(self, text: str, *, query: bool = False) -> list[float]: ...


def vector_literal(embedding: list[float]) -> str:
    if len(embedding) != DIMENSIONS or not all(map(math.isfinite, embedding)):
        raise ValueError("Invalid embedding dimensions or values")
    if not any(embedding):
        raise ValueError("Embedding must be nonzero")
    return json.dumps(embedding, allow_nan=False)


class PostgresStore:
    def __init__(self, connect: Callable):
        self.connect = connect

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA)

    def replace(self, repo_id: str, items: list[Item], embedder: Embedder) -> int:
        with self.connect() as conn:
            # Serialize complete snapshots for this repository across service instances.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo_id,))
            previous = {
                row[0]: row[1:]
                for row in conn.execute(
                    """SELECT source_id, source, scopes, embedder, embedding::text
                       FROM reviewpilot_guidance WHERE repo_id = %s""",
                    (repo_id,),
                )
            }
            rows = []
            for ref, scopes in items:
                old = previous.get(ref.id)
                source = ref.model_dump()
                if old and old[:3] == (source, scopes, embedder.name):
                    continue
                if old and old[0]["excerpt"] == ref.excerpt and old[2] == embedder.name:
                    embedding = old[3]
                else:
                    embedding = vector_literal(embedder.embed(f"{ref.path}\n{ref.excerpt}"))
                rows.append(
                    (repo_id, ref.id, ref.model_dump_json(), scopes, embedder.name, embedding)
                )
            if rows:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO reviewpilot_guidance
                           (repo_id, source_id, source, scopes, embedder, embedding)
                           VALUES (%s,%s,%s::jsonb,%s,%s,%s::vector)
                           ON CONFLICT (repo_id, source_id) DO UPDATE SET source = EXCLUDED.source,
                           scopes = EXCLUDED.scopes, embedder = EXCLUDED.embedder,
                           embedding = EXCLUDED.embedding""",
                        rows,
                    )
            removed = sorted(previous.keys() - {ref.id for ref, _ in items})
            if removed:
                conn.execute(
                    "DELETE FROM reviewpilot_guidance WHERE repo_id = %s AND source_id = ANY(%s)",
                    (repo_id, removed),
                )
            return len(items)

    def search(
        self, repo_id: str, path: str, embedding: list[float],
        embedder: str, k: int, min_similarity: float,
    ) -> list[Match]:
        with self.connect() as conn:
            rows = conn.execute(
                """WITH candidates AS MATERIALIZED (
                       SELECT source_id, source, embedding
                       FROM reviewpilot_guidance
                       WHERE repo_id = %s AND embedder = %s AND (
                           cardinality(scopes) = 0 OR EXISTS (
                               SELECT 1 FROM unnest(scopes) AS scope(prefix)
                               WHERE %s = prefix OR starts_with(%s, prefix || '/')
                           )
                       )
                   ), ranked AS (
                       SELECT source_id, source, embedding <=> %s::vector AS distance
                       FROM candidates
                   )
                   SELECT source FROM ranked WHERE distance <= %s
                   ORDER BY distance, source_id LIMIT %s""",
                (repo_id, embedder, path, path, vector_literal(embedding), 1 - min_similarity, k),
            )
            return [
                Match(changed_path=path, source=Reference.model_validate(source))
                for (source,) in rows
            ]


class FixtureEmbedder:
    """Deterministic hashed terms for local fixtures and integration tests."""

    name = "fixture-hash-v1"

    def embed(self, text: str, *, query: bool = False) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            slot = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "big")
            vector[slot % DIMENSIONS] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return vector
