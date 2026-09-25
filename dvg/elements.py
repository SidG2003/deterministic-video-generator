"""
Element factories: IR Element -> Manim Mobject.

Independent elements (text/node/dot) are built first. Dependent elements
(connector) are built in a second pass once positions are known.
"""

from __future__ import annotations

from manim import Arrow, Circle, Dot, Mobject, Text, VGroup

from .ir import Element
from .style import Style


def build_element(el: Element, style: Style) -> Mobject:
    """Build a position-independent element. Positioning happens later in layout."""
    p = el.props
    if el.type == "text":
        role = p.get("role", "body")
        m = Text(
            p["content"],
            color=style.color(p.get("color")) if p.get("color") is not None else style.text_color,
            weight=p.get("weight", "NORMAL"),
            slant=p.get("slant", "NORMAL"),
        )
        return m.scale(style.scales.get(role, 0.5))

    if el.type == "node":
        radius = p.get("radius", style.node_radius)
        circ = Circle(
            radius=radius,
            color=style.color(p.get("color")),
            fill_color=style.node_fill,
            fill_opacity=1.0,
            stroke_width=3,
        )
        label = Text(p.get("label", ""), color=style.text_color).scale(
            style.scales["label"] * 0.9
        )
        label.move_to(circ.get_center())
        return VGroup(circ, label)

    if el.type == "dot":
        return Dot(color=style.color(p.get("color")), radius=p.get("radius", 0.12))

    raise ValueError(f"build_element cannot build type '{el.type}'")


def build_connector(el: Element, registry: dict[str, Mobject], style: Style) -> Mobject:
    """Build an arrow between two already-positioned elements."""
    a = registry[el.props["from"]]
    b = registry[el.props["to"]]
    return Arrow(
        a.get_right(),
        b.get_left(),
        buff=0.15,
        color=style.text_color,
        stroke_width=3,
        max_tip_length_to_length_ratio=0.15,
    )
