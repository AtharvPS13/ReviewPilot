"""Bounded request and response schemas shared by the Action and service."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

RepoId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_.-]+/[a-z0-9_.-]+$", max_length=200)]
Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=1800)]
PathText = Annotated[str, StringConstraints(min_length=1, max_length=300)]
SourceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{20}$")]


def validate_path(path: str) -> str:
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError("Use a repository-relative path")
    if re.search(r"[\x00-\x1f\x7f\\]", path):
        raise ValueError("Invalid path character")
    return path


class Source(BaseModel):
    path: PathText
    content: Annotated[str, StringConstraints(max_length=40_000)]
    applies_to: list[PathText] = Field(default_factory=list, max_length=16)

    @field_validator("path")
    @classmethod
    def check_source(cls, path: str) -> str:
        if not path.endswith((".md", ".json")):
            raise ValueError("Only Markdown and JSON sources are supported")
        return validate_path(path)

    @field_validator("applies_to")
    @classmethod
    def check_scopes(cls, values: list[str]) -> list[str]:
        return sorted({validate_path(path) for path in values})


class Snapshot(BaseModel):
    repo_id: RepoId
    revision: Commit
    sources: list[Source] = Field(max_length=32)


class Decision(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    decision: Text
    applies_to: list[PathText] = Field(default_factory=list, max_length=16)

    @field_validator("applies_to")
    @classmethod
    def check_scopes(cls, values: list[str]) -> list[str]:
        return sorted({validate_path(path) for path in values})


class Reference(BaseModel):
    id: SourceId
    kind: Literal["markdown", "decision"]
    path: PathText
    revision: Commit
    locator: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    excerpt: Text

    @field_validator("path")
    @classmethod
    def check_path(cls, path: str) -> str:
        return validate_path(path)

    def label(self) -> str:
        return f"{self.path} ({self.locator})"


class Change(BaseModel):
    path: PathText
    context: Annotated[str, StringConstraints(min_length=1, max_length=3000)]

    @field_validator("path")
    @classmethod
    def check_path(cls, path: str) -> str:
        return validate_path(path)


class Search(BaseModel):
    repo_id: RepoId
    changes: list[Change] = Field(min_length=1, max_length=8)
    k: int = Field(default=2, ge=1, le=3)


class Match(BaseModel):
    changed_path: PathText
    source: Reference


class SearchResult(BaseModel):
    matches: list[Match] = Field(max_length=24)
