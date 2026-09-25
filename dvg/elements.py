"""
Element factories: IR Element -> Manim Mobject.

Independent elements (text/node/dot) are built first. Dependent elements
(connector) are built in a second pass once positions are known.
"""

from __future__ import annotations

from manim import (
    DOWN,
    LEFT,
    Arrow,
    Axes,
    Circle,
    Dot,
    Line,
    MathTex,
    Mobject,
    Square,
    SurroundingRectangle,
    Text,
    Triangle,
    VGroup,
)

from .ir import Element
from .mathexpr import compile_expr
from .style import Style


def _wrapped_text(text: str, style: Style, scale: float, max_width: float) -> VGroup:
    """Greedily wrap text into lines that each fit `max_width` (measured, not
    estimated), so a card's text can never overflow its box."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        if Text(trial).scale(scale).width > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    line_mobs = VGroup(*[Text(ln, color=style.text_color).scale(scale) for ln in lines])
    line_mobs.arrange(DOWN, buff=0.12, aligned_edge=LEFT)
    return line_mobs


def _card(content: str, style: Style, scale: float, max_width: float) -> VGroup:
    """A rounded box auto-sized to its (wrapped) text — no overflow possible."""
    lines = _wrapped_text(content, style, scale, max_width)
    box = (
        SurroundingRectangle(lines, buff=0.2, color=style.text_color)
        .set_stroke(width=2)
        .set_fill(style.node_fill, opacity=1.0)
    )
    return VGroup(box, lines)  # box behind, text on top


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

    if el.type == "card":
        return _card(
            p["content"],
            style,
            style.scales["body"] * p.get("scale", 0.9),
            p.get("max_width", 4.5),
        )

    if el.type == "timeline":
        return build_timeline(el, style)

    if el.type == "shape":
        kind = p.get("kind", "square")
        size = p.get("size", 1.2)
        col = style.color(p.get("color"))
        if kind == "circle":
            m = Circle(radius=size / 2)
        elif kind == "triangle":
            m = Triangle().scale(size / 2)
        else:
            m = Square(side_length=size)
        return m.set_stroke(col, width=3).set_fill(col, opacity=0.18)

    if el.type == "math":
        # requires a LaTeX distribution (see README). MathTex renders TeX.
        m = MathTex(p["tex"], color=style.color(p.get("color")) if p.get("color") is not None else style.text_color)
        return m.scale(p.get("scale", 1.4))

    if el.type == "axes":
        return Axes(
            x_range=p.get("x_range", [-5, 5, 1]),
            y_range=p.get("y_range", [-3, 3, 1]),
            x_length=p.get("x_length", 9),
            y_length=p.get("y_length", 5),
            axis_config={
                "color": style.text_color,
                "stroke_width": 2,
                "include_tip": p.get("tips", True),
            },
        )

    raise ValueError(f"build_element cannot build type '{el.type}'")


def build_graph(el: Element, registry: dict[str, Mobject], style: Style) -> Mobject:
    """Plot a function on an already-positioned axes. The expression is compiled
    via the safe evaluator (never eval'd)."""
    p = el.props
    axes = registry[p["axes"]]
    func = compile_expr(p["expr"])
    kwargs = {"color": style.color(p.get("color"))}
    if "x_range" in p:
        kwargs["x_range"] = p["x_range"]
    return axes.plot(func, **kwargs)


def build_timeline(el: Element, style: Style) -> VGroup:
    """Composite element: a horizontal spine with event stations.

    Cards alternate above/below the spine and each card's max width is derived
    from the spacing between same-side neighbours, so cards provably never
    overlap regardless of how long the labels are (long text wraps instead).
    Returns a VGroup ordered [spine, station0, station1, ...] so a `reveal`
    animation can stagger it left-to-right.
    """
    p = el.props
    events = p["events"]
    # leave margin so the outermost cards (which extend ~max_w/2 past the end
    # dots) stay fully on-screen; frame half-width is ~7.1.
    x_min, x_max = -5.2, 5.2
    n = len(events)
    xs = [0.0] if n == 1 else [x_min + (x_max - x_min) * i / (n - 1) for i in range(n)]
    spacing = (x_max - x_min) / (n - 1) if n > 1 else 0.0
    # same-side neighbours sit 2*spacing apart; keep cards safely inside that.
    max_w = max(1.6, min(3.2, 2 * spacing - 0.6)) if n > 1 else 4.5

    spine = Line(
        [x_min - 0.2, 0, 0], [x_max + 0.2, 0, 0], color=style.text_color, stroke_width=2
    ).set_opacity(0.5)

    stations = []
    label_scale = style.scales["label"] * 0.85
    for i, (ev, x) in enumerate(zip(events, xs)):
        above = i % 2 == 0
        dot = Dot([x, 0, 0], color=style.accent, radius=0.09)
        year = (
            Text(str(ev["year"]), color=style.color(0), weight="BOLD")
            .scale(style.scales["label"] * 0.95)
        )
        year.next_to(dot, DOWN, buff=0.18)

        card = _card(ev["label"], style, label_scale, max_w)
        offset = 1.25 + card.height / 2
        card.move_to([x, offset if above else -offset, 0])

        start = [x, 0.09 if above else -0.09, 0]
        end = [x, card.get_bottom()[1], 0] if above else [x, card.get_top()[1], 0]
        connector = Line(start, end, color=style.text_color, stroke_width=2).set_opacity(0.5)

        stations.append(VGroup(dot, year, connector, card))

    return VGroup(spine, *stations)


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
