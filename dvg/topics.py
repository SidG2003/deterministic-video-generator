"""
Fixed sample prompts for comparing pipelines (eval/topics.toml).

    python -m dvg.topics           # list all
    python -m dvg.topics 5 llms    # show some (by number, id like t05, or name)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

TOPICS_FILE = Path(__file__).resolve().parent.parent / "eval" / "topics.toml"


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    prompt: str
    depth: str
    stresses: tuple[str, ...] = ()


def load_topics(path: Path = TOPICS_FILE) -> list[Topic]:
    data = tomllib.loads(path.read_text())
    topics = [Topic(id=t["id"], name=t["name"], prompt=t["prompt"], depth=t.get("depth", "standard"),
                    stresses=tuple(t.get("stresses", ()))) for t in data.get("topic", [])]
    for key in ("id", "name"):
        values = [getattr(t, key) for t in topics]
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            raise ValueError(f"{path}: duplicate topic {key}(s): {', '.join(dupes)}")
    return topics


def get_topic(ref: str | int, path: Path = TOPICS_FILE) -> Topic:
    """Look a topic up by number (5, "05"), id ("t05") or name ("rocket-orbit")."""
    topics = load_topics(path)
    key = str(ref).strip().lower()
    if key.isdigit():
        key = f"t{int(key):02d}"
    for t in topics:
        if key in (t.id.lower(), t.name.lower()):
            return t
    known = ", ".join(f"{t.id} ({t.name})" for t in topics)
    raise KeyError(f"no topic {ref!r} in {path.name}; known: {known}")


def main() -> None:
    import sys

    refs = sys.argv[1:]
    try:
        topics = [get_topic(r) for r in refs] if refs else load_topics()
    except KeyError as exc:
        raise SystemExit(exc.args[0])
    for t in topics:
        print(f"{t.id}  {t.name:17s} [{t.depth}]  {t.prompt}")
        if refs:
            print(f"     stresses: {', '.join(t.stresses) or '-'}")


if __name__ == "__main__":
    main()
