"""
Technique: Graph on axes with a moving point
When: showing how one quantity depends on another — a curve, its slope at a
  point, the area under it, a point sliding along it.
Why: axes give instant context; a tracked point with a live tangent turns a
  static picture into a question the viewer watches being answered.
How: axes = Axes(x_range, y_range, x_length, y_length, tips=False);
  graph = axes.plot(f, x_range=[a, b]); labels = axes.get_axis_labels(...);
  dot = always_redraw(lambda: Dot(axes.input_to_graph_point(t.get_value(), graph)));
  a tangent as an always_redraw Line through that point; area = axes.get_area(graph, x_range=[a, b]).
Pitfalls: keep x_length/y_length inside the frame (about 10 x 5.5) and leave the
  top band free for a title; place axis labels with get_axis_labels, not by
  hand; plot only the range you need (discontinuities explode).
In 3b1b: Axes ~170 and get_graph ~260 uses in the 2020-2026 videos (manimgl
  get_graph = ManimCE plot).
Tags: graph, function, axes, calculus, slope, tangent, area, plot
"""

from manim import *


class SlidingTangent(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        axes = Axes(x_range=[0, 6, 1], y_range=[0, 4, 1], x_length=9, y_length=5, tips=False,
                    axis_config={"color": GREY_B}).shift(DOWN * 0.3)

        def f(x):
            return 0.12 * (x - 1) * (x - 3.5) * (x - 6) + 2

        graph = axes.plot(f, x_range=[0, 6], color=BLUE)
        labels = axes.get_axis_labels(MathTex("x", font_size=36), MathTex("f(x)", font_size=36))
        t = ValueTracker(0.8)
        dot = always_redraw(lambda: Dot(axes.input_to_graph_point(t.get_value(), graph), color=YELLOW))

        def tangent():
            x = t.get_value()
            slope = (f(x + 1e-3) - f(x - 1e-3)) / 2e-3
            p = axes.c2p(x, f(x))
            direction = axes.c2p(x + 1, f(x) + slope) - p
            direction = direction / np.linalg.norm(direction)
            return Line(p - 1.4 * direction, p + 1.4 * direction, color=YELLOW, stroke_width=3)

        tangent_line = always_redraw(tangent)
        area = axes.get_area(graph, x_range=[0.8, 5.2], color=[BLUE, TEAL], opacity=0.25)

        self.play(Create(axes), Write(labels))
        self.play(Create(graph), run_time=1.5)
        self.play(FadeIn(dot), Create(tangent_line))
        self.play(t.animate.set_value(5.2), run_time=3, rate_func=smooth)
        self.play(FadeIn(area))
        self.wait(0.5)
