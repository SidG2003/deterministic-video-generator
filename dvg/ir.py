"""
Intermediate Representation (IR) for a video.

A video is a validated scene-graph, NOT Manim code. The director interprets it.
Because it's validated data (not code), a generator can't emit something that
crashes the renderer, and the layout engine guarantees legibility regardless of
content. This is the safety half of the "novel but robust" architecture.

Structure:
    Video
      └─ beats: [Beat]                 sequential segments (each clears out)
           ├─ elements: [Element]      typed nodes (text, node, dot, connector)
           ├─ layout: {slots: [...]}   declarative placement (measured, no overlap)
           └─ animations: [[Animation]] outer=sequential steps, inner=concurrent
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Element types that are positioned by *reference* to other elements, so they
# must be built/placed AFTER the layout pass has positioned everything else.
DEPENDENT_TYPES = {"connector"}
ELEMENT_TYPES = {"text", "node", "dot", "connector", "card", "timeline", "shape", "math"}
ANIM_TYPES = {
    "write", "create", "fade_in", "fade_out", "grow", "move", "reveal",
    "transform", "replace", "morph_tex", "shift",
}
# animations that morph a source element INTO another element (need a valid `to`)
MORPH_TYPES = {"transform", "replace", "morph_tex"}
PLACES = {"top", "center", "bottom"}
ARRANGES = {"stack", "row", "none"}


@dataclass
class Element:
    id: str
    type: str
    props: dict = field(default_factory=dict)


@dataclass
class Animation:
    target: str
    type: str
    run_time: float | None = None
    to: str | None = None  # for move/transform/replace/morph_tex: destination id
    dx: float = 0.0  # for "shift"
    dy: float = 0.0  # for "shift"


@dataclass
class Beat:
    id: str
    elements: list[Element]
    layout: dict
    animations: list[list[Animation]]
    narration: str | None = None
    hold: float = 0.5
    clear: bool = False  # fade everything out at beat end (slideshow cut)
    exit: list[str] = field(default_factory=list)  # fade out just these ids


@dataclass
class Video:
    title: str
    beats: list[Beat]
    style: str = "midnight"
    seed: int = 0
    fps: int = 30


class IRError(ValueError):
    """Raised when an IR document is structurally invalid."""


def load_video(path: str | Path) -> Video:
    data = json.loads(Path(path).read_text())
    try:
        video = _parse_video(data)
    except (KeyError, TypeError) as exc:
        raise IRError(f"malformed IR: {exc}") from exc
    _validate(video)
    return video


def _parse_video(data: dict) -> Video:
    beats = [_parse_beat(b) for b in data["beats"]]
    return Video(
        title=data["title"],
        beats=beats,
        style=data.get("style", "midnight"),
        seed=int(data.get("seed", 0)),
        fps=int(data.get("fps", 30)),
    )


def _parse_beat(b: dict) -> Beat:
    elements = [Element(e["id"], e["type"], e.get("props", {})) for e in b.get("elements", [])]
    steps = [
        [
            Animation(
                a["target"], a["type"], a.get("run_time"), a.get("to"),
                float(a.get("dx", 0.0)), float(a.get("dy", 0.0)),
            )
            for a in step
        ]
        for step in b.get("animations", [])
    ]
    return Beat(
        id=b["id"],
        elements=elements,
        layout=b.get("layout", {"slots": []}),
        animations=steps,
        narration=b.get("narration"),
        hold=float(b.get("hold", 0.5)),
        clear=bool(b.get("clear", False)),
        exit=list(b.get("exit", [])),
    )


def _validate(video: Video) -> None:
    """Fail loudly and specifically. This is the contract a generator writes to."""
    if not video.beats:
        raise IRError("video has no beats")

    # ids are video-global stable handles (persistent canvas). `available` grows
    # cumulatively so a beat can reference anything declared this beat or earlier.
    available: dict[str, str] = {}
    for beat in video.beats:
        beat_new: dict[str, str] = {}
        for el in beat.elements:
            if el.id in available or el.id in beat_new:
                raise IRError(f"beat '{beat.id}': duplicate element id '{el.id}'")
            if el.type not in ELEMENT_TYPES:
                raise IRError(f"beat '{beat.id}': unknown element type '{el.type}'")
            beat_new[el.id] = el.type
        ids = {**available, **beat_new}

        # connector endpoints and 'at' references must resolve
        for el in beat.elements:
            for ref_key in ("from", "to", "at"):
                ref = el.props.get(ref_key)
                if ref is not None and ref not in ids:
                    raise IRError(
                        f"beat '{beat.id}': element '{el.id}' references "
                        f"unknown element '{ref}' via '{ref_key}'"
                    )
            if el.type == "connector":
                for req in ("from", "to"):
                    if req not in el.props:
                        raise IRError(
                            f"beat '{beat.id}': connector '{el.id}' missing '{req}'"
                        )
            if el.type == "card" and "content" not in el.props:
                raise IRError(f"beat '{beat.id}': card '{el.id}' missing 'content'")
            if el.type == "math" and "tex" not in el.props:
                raise IRError(f"beat '{beat.id}': math '{el.id}' missing 'tex'")
            if el.type == "shape" and el.props.get("kind", "square") not in {
                "square", "circle", "triangle"
            }:
                raise IRError(f"beat '{beat.id}': shape '{el.id}' has unknown kind")
            if el.type == "timeline":
                events = el.props.get("events")
                if not isinstance(events, list) or not events:
                    raise IRError(f"beat '{beat.id}': timeline '{el.id}' needs non-empty 'events'")
                for ev in events:
                    if "year" not in ev or "label" not in ev:
                        raise IRError(
                            f"beat '{beat.id}': timeline '{el.id}' event needs 'year' and 'label'"
                        )

        # layout slots
        for slot in beat.layout.get("slots", []):
            if slot.get("place", "center") not in PLACES:
                raise IRError(f"beat '{beat.id}': unknown place '{slot.get('place')}'")
            if slot.get("arrange", "stack") not in ARRANGES:
                raise IRError(f"beat '{beat.id}': unknown arrange '{slot.get('arrange')}'")
            for item in slot.get("items", []):
                if item not in ids:
                    raise IRError(f"beat '{beat.id}': slot references unknown '{item}'")

        # animations
        for step in beat.animations:
            for a in step:
                if a.type not in ANIM_TYPES:
                    raise IRError(f"beat '{beat.id}': unknown animation '{a.type}'")
                if a.target not in ids:
                    raise IRError(f"beat '{beat.id}': animation targets unknown '{a.target}'")
                if a.type in ({"move"} | MORPH_TYPES) and (a.to is None or a.to not in ids):
                    raise IRError(f"beat '{beat.id}': '{a.type}' on '{a.target}' needs valid 'to'")

        # exit list must reference live objects
        for eid in beat.exit:
            if eid not in ids:
                raise IRError(f"beat '{beat.id}': exit references unknown '{eid}'")

        available = ids
