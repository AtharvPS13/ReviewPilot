"""Fixture corpus and a PostgreSQL substitute for offline tests."""

import json
import math
from pathlib import Path

from reviewpilot.rag.models import Match, Snapshot, Source

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests/fixtures/rag"
REPO = "fixture/reviewpilot"
REVISION = "a" * 40


def fixture_snapshot(repo=REPO, revision=REVISION):
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    sources = [
        Source(**entry, content=(ROOT / entry["path"]).read_text(encoding="utf-8"))
        for entry in manifest["sources"]
    ]
    return Snapshot(repo_id=repo, revision=revision, sources=sources)


class MemoryStore:
    """Used only when PostgreSQL is unavailable. PostgreSQL has its own integration test."""

    def __init__(self):
        self.rows = {}

    def replace(self, repo_id, items, embedder):
        self.rows[repo_id] = [
            (ref, scopes, embedder.embed(f"{ref.path}\n{ref.excerpt}"))
            for ref, scopes in items
        ]
        return len(items)

    def search(self, repo_id, path, embedding, embedder, k, min_similarity):
        ranked = []
        for ref, scopes, vector in self.rows.get(repo_id, []):
            in_scope = any(path == scope or path.startswith(scope + "/") for scope in scopes)
            if scopes and not in_scope:
                continue
            similarity = sum(a * b for a, b in zip(vector, embedding, strict=True))
            similarity /= math.hypot(*vector) * math.hypot(*embedding)
            if similarity >= min_similarity:
                ranked.append((-similarity, ref.id, ref))
        return [
            Match(changed_path=path, source=ref) for _, _, ref in sorted(ranked)[:k]
        ]
