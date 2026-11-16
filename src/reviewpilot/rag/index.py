"""Publish a complete guidance snapshot from a trusted Git commit."""

import argparse
import os
import subprocess
import sys

import httpx
from pydantic import BaseModel, Field

from reviewpilot.rag.client import is_render_url, service_url
from reviewpilot.rag.models import Snapshot, Source, validate_path


class ManifestEntry(BaseModel):
    path: str
    applies_to: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    sources: list[ManifestEntry] = Field(max_length=32)


def git_text(*args: str) -> str:
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE).decode("utf-8")


def snapshot_from_git(repo_id: str, revision: str, manifest_path: str) -> Snapshot:
    commit = git_text(
        "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
    ).strip()
    manifest = Manifest.model_validate_json(git_text("show", f"{commit}:{manifest_path}"))
    return Snapshot(
        repo_id=repo_id.lower(),
        revision=commit,
        sources=[
            Source(
                path=entry.path, applies_to=entry.applies_to,
                content=git_text("show", f"{commit}:{validate_path(entry.path)}"),
            )
            for entry in manifest.sources
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_id")
    parser.add_argument("--revision", required=True, help="Trusted commit or branch to index")
    parser.add_argument("--manifest", required=True, help="Manifest path within that commit")
    args = parser.parse_args()
    try:
        snapshot = snapshot_from_git(args.repo_id, args.revision, validate_path(args.manifest))
        url = service_url(os.environ["REVIEWPILOT_RAG_URL"])
        token = os.environ.get("REVIEWPILOT_RAG_TOKEN")
        headers = {"X-Index-Token": os.environ["RAG_INDEX_TOKEN"]}
        if url.scheme == "https" and not is_render_url(url):
            if not token:
                raise ValueError("Missing Cloud Run identity token")
            headers["Authorization"] = f"Bearer {token}"
        response = httpx.post(
            url.copy_with(path="/v1/index"), json=snapshot.model_dump(),
            headers=headers, timeout=600, trust_env=False,
        )
        response.raise_for_status()
        result = response.json()
        print(f"Indexed {int(result['indexed'])} excerpts from {snapshot.revision}")
        return 0
    except (KeyError, ValueError, subprocess.CalledProcessError, httpx.HTTPError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else ""
        print(f"Indexing failed ({type(exc).__name__} {status})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
