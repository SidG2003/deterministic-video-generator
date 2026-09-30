"""
Technique recipes: one standalone ManimCE scene file per technique.

Each file starts with a docstring of "Field: value" sections and contains one or
more short example scenes (original ManimCE code). Files render directly with
`manim`, so they are easy to review, and a snippet can be shown to an LLM as-is.

Required docstring fields:
  Technique  name
  When       situations it fits
  Why        what it does for the viewer
  How        the ManimCE calls involved
  Pitfalls   what goes wrong and how to avoid it
  In 3b1b    how much 3Blue1Brown relies on it (usage counts in 3b1b/videos
             2020-2026; design reference only — no 3b1b code is included)
  Tags       comma-separated keywords (for retrieval later)
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

DIR = Path(__file__).parent
FIELDS = ("Technique", "When", "Why", "How", "Pitfalls", "In 3b1b", "Tags")


@dataclass
class Snippet:
    name: str
    source: str

    @property
    def lines(self) -> int:
        return self.source.count("\n") + 1


@dataclass
class Technique:
    slug: str
    path: Path
    fields: dict[str, str]
    snippets: list[Snippet] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.fields.get("Technique", self.slug)

    @property
    def tags(self) -> list[str]:
        return [t.strip() for t in self.fields.get("Tags", "").split(",") if t.strip()]


def _parse_fields(doc: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    key = None
    pattern = re.compile(rf"^({'|'.join(re.escape(f) for f in FIELDS)}):\s*(.*)$")
    for line in doc.splitlines():
        m = pattern.match(line)
        if m:
            key = m.group(1)
            fields[key] = m.group(2).strip()
        elif key and line.strip():
            fields[key] += " " + line.strip()
    return fields


def load_technique(path: Path) -> Technique:
    src = path.read_text()
    tree = ast.parse(src)
    lines = src.splitlines()
    snippets = [Snippet(n.name, "\n".join(lines[n.lineno - 1:n.end_lineno]))
                for n in tree.body if isinstance(n, ast.ClassDef)]
    return Technique(path.stem, path, _parse_fields(ast.get_docstring(tree) or ""), snippets)


def load_catalog() -> list[Technique]:
    return [load_technique(p) for p in sorted(DIR.glob("*.py")) if p.name != "__init__.py"]
