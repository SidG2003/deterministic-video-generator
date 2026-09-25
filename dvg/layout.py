"""
Layout engine — the robustness half of the architecture.

Placement is driven by *measured* mobject sizes (Manim's arrange/next_to/to_edge
operate on real bounding boxes), so text can't overflow a box and elements in
different bands (top/center/bottom) can't collide, regardless of content length.
This is what structurally eliminates the overlap class of bugs the fixed
template hit — no matter what the generator throws at it.

A layout is a set of slots; each slot places a group of elements in a screen
band with an arrangement (stack/row/none).
"""

from __future__ import annotations

from manim import DOWN, LEFT, ORIGIN, RIGHT, UP, Mobject, VGroup

_EDGE_BUFF = {"top": 0.6, "bottom": 0.8}


def apply_layout(layout: dict, registry: dict[str, Mobject]) -> None:
    for slot in layout.get("slots", []):
        items = [registry[i] for i in slot.get("items", []) if i in registry]
        if not items:
            continue
        group = VGroup(*items)

        arrange = slot.get("arrange", "stack")
        gap = slot.get("gap", 0.4)
        if arrange == "stack":
            aligned = LEFT if slot.get("align") == "left" else ORIGIN
            group.arrange(DOWN, buff=gap, aligned_edge=aligned)
        elif arrange == "row":
            group.arrange(RIGHT, buff=slot.get("gap", 1.8))
        # arrange == "none": leave individual positions

        place = slot.get("place", "center")
        if place == "top":
            group.to_edge(UP, buff=_EDGE_BUFF["top"])
        elif place == "bottom":
            group.to_edge(DOWN, buff=_EDGE_BUFF["bottom"])
        else:
            group.move_to(ORIGIN)

        shift = slot.get("shift")  # optional [dx, dy] fine-tuning after placement
        if shift:
            group.shift([shift[0], shift[1], 0])


def place_dependent(el_type: str, props: dict, mob: Mobject,
                    registry: dict[str, Mobject]) -> None:
    """Position elements that anchor to another element (after layout)."""
    anchor_id = props.get("at")
    if anchor_id is None:
        return
    anchor = registry[anchor_id]
    if el_type == "dot":
        mob.move_to(anchor.get_center())
    else:
        mob.next_to(anchor, UP, buff=0.3)
