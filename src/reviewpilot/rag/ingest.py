"""Convert trusted repository snapshots into bounded, citable excerpts."""

import hashlib
import re
from collections.abc import Iterator
from typing import Literal, NamedTuple

from pydantic import TypeAdapter

from reviewpilot.rag.models import Decision, Reference, Snapshot, Source

MAX_ITEMS = 100
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
DECISIONS = TypeAdapter(list[Decision])


class Item(NamedTuple):
    reference: Reference
    scopes: list[str]


def markdown_chunks(content: str) -> Iterator[tuple[str, str]]:
    """Group excerpts by heading and preserve exact line ranges."""
    lines = content.splitlines()
    start = 1
    chunk: list[str] = []
    length = 0
    heading = ""
    fence = ""

    for number, line in enumerate(lines, 1):
        if len(line) > 1500:
            raise ValueError("Markdown source contains a line longer than 1500 characters")
        match = HEADING.match(line) if not fence else None
        if chunk and (match or length + len(line) > 1500):
            yield f"L{start}-L{number - 1}", f"{heading}\n" + "\n".join(chunk)
            chunk, length = [], 0
        if match:
            heading = match[1][:160]
        marker = FENCE.match(line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not line[marker.end():].strip():
                fence = ""
        if not chunk:
            start = number
        chunk.append(line)
        length += len(line) + 1
    if chunk:
        yield f"L{start}-L{len(lines)}", f"{heading}\n" + "\n".join(chunk)


def reference(
    snapshot: Snapshot, source: Source, kind: Literal["markdown", "decision"],
    locator: str, excerpt: str,
) -> Reference:
    key = f"{snapshot.repo_id}\n{source.path}\n{kind}\n{locator}"
    return Reference(
        id=hashlib.sha256(key.encode()).hexdigest()[:20],
        kind=kind,
        path=source.path,
        revision=snapshot.revision,
        locator=locator,
        excerpt=excerpt,
    )


def index_items(snapshot: Snapshot) -> list[Item]:
    items: list[Item] = []
    paths: set[str] = set()
    for source in sorted(snapshot.sources, key=lambda entry: entry.path):
        if source.path in paths:
            raise ValueError("Duplicate source path")
        paths.add(source.path)
        if source.path.endswith(".md"):
            for locator, text in markdown_chunks(source.content):
                if text.strip():
                    ref = reference(snapshot, source, "markdown", locator, text.strip())
                    items.append(Item(ref, source.applies_to))
        else:
            seen: set[str] = set()
            for decision in DECISIONS.validate_json(source.content):
                if decision.id in seen:
                    raise ValueError("Duplicate decision ID")
                seen.add(decision.id)
                text = f"{decision.title}\n{decision.decision}"[:1800]
                ref = reference(snapshot, source, "decision", decision.id, text)
                items.append(Item(ref, decision.applies_to or source.applies_to))
        if len(items) > MAX_ITEMS:
            raise ValueError("Repository snapshot exceeds 100 excerpts")
    return items
