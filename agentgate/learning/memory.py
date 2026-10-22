"""Lesson memory: what past runs taught us, retrievable for new tasks.

Stored as JSON Lines so it is easy to read, diff and reset. Retrieval is a
transparent score (vendor, task kind, difficulty, word overlap) rather than
embeddings: deterministic, free, and easy to explain for a store of a few
hundred short lessons.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

ANY = "any"


@dataclass
class Lesson:
    text: str
    kind: str        # task kind it came from, or "any"
    vendor: str      # vendor it applies to, or "any"
    difficulty: str
    source: str      # reflection | demonstration
    round: int = 0


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower())}


class Memory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lessons: list[Lesson] = []
        if self.path.exists():
            self.lessons = [Lesson(**json.loads(line)) for line in self.path.read_text().splitlines() if line.strip()]

    def add(self, lessons: list[Lesson]) -> int:
        """Append new lessons, skipping near-duplicates. Demonstrations replace older ones for the same task type."""
        added = 0
        for lesson in lessons:
            if lesson.source == "demonstration":
                self.lessons = [l for l in self.lessons if not (l.source == "demonstration" and l.kind == lesson.kind
                                                                 and l.vendor == lesson.vendor)]
            elif any(self._similar(lesson, l) for l in self.lessons):
                continue
            self.lessons.append(lesson)
            added += 1
        self.save()
        return added

    @staticmethod
    def _similar(a: Lesson, b: Lesson) -> bool:
        wa, wb = _words(a.text), _words(b.text)
        return a.vendor == b.vendor and bool(wa) and len(wa & wb) / len(wa | wb) > 0.6

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(asdict(l)) + "\n" for l in self.lessons))

    def retrieve(self, kind: str, vendor: str, difficulty: str, query: str, k: int = 5) -> list[Lesson]:
        query_words = _words(query)

        def score(l: Lesson) -> float:
            if l.vendor not in (vendor, ANY):
                return 0.0  # another portal's quirks are noise
            s = 2.0 if l.vendor == vendor else 1.0
            s += 1.0 if l.kind == kind else 0.0
            s += 0.5 if l.difficulty == difficulty else 0.0
            words = _words(l.text)
            s += 2.0 * len(words & query_words) / len(words | query_words) if words else 0.0
            return s

        ranked = sorted(((score(l), i, l) for i, l in enumerate(self.lessons)), key=lambda t: (-t[0], t[1]))
        return [l for s, _, l in ranked[:k] if s >= 2.0]
