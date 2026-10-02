from manim import *
import numpy as np
import random

NARRATION = [
    "We open on a scatter of points that settles into a title.",
    "A short equation appears and rearranges itself.",
    "Finally a dot traces a circular path, leaving a glowing trail.",
]

SECTIONS = ["title", "equation", "orbit"]


class Generated(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        for name in SECTIONS:
            getattr(self, name)()

    def title(self):
        # random is reseeded by the harness before this section
        dots = VGroup()
        for _ in range(24):
            p = np.array([random.uniform(-6, 6), random.uniform(-3, 3), 0.0])
            d = Dot(p, radius=0.05, color="#7aa2ff").set_opacity(random.uniform(0.2, 0.6))
            dots.add(d)
        title = Text("Sections", font_size=48, weight=BOLD)
        subtitle = Text("rendered in parallel", font_size=28, color="#9fb0d8")
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.3)
        self.play(FadeIn(dots, lag_ratio=0.05), run_time=0.6)
        self.play(Write(title), FadeIn(subtitle, shift=UP * 0.2), run_time=0.6)
        self.wait(0.3)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)

    def equation(self):
        heading = Text("An identity", font_size=40).to_edge(UP, buff=0.5)
        eq = MathTex(r"e^{i\pi}", "+", "1", "=", "0", font_size=60)
        eq[0].set_color("#7aa2ff")
        eq[4].set_color("#6fe3c2")
        self.play(FadeIn(heading), Write(eq), run_time=0.8)
        self.wait(0.3)
        eq2 = MathTex(r"e^{i\pi}", "=", "-", "1", font_size=60).move_to(eq)
        self.play(TransformMatchingShapes(eq, eq2), run_time=0.6)
        self.wait(0.3)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)

    def orbit(self):
        label = MathTex(r"\theta(t) = \omega t", font_size=44, color="#ffd27a").to_edge(UP, buff=0.5)
        circle = Circle(radius=1.8, color="#3a4568", stroke_width=2)
        angle = ValueTracker(0.0)

        def dot_point():
            a = angle.get_value()
            return circle.get_center() + 1.8 * np.array([np.cos(a), np.sin(a), 0.0])

        dot = always_redraw(lambda: Dot(dot_point(), radius=0.12, color="#ff6b5e"))
        trail = TracedPath(dot_point, stroke_color="#ffd27a", stroke_width=4, stroke_opacity=0.8)
        self.play(FadeIn(label), Create(circle), run_time=0.5)
        self.add(trail, dot)
        self.play(angle.animate.set_value(TAU), run_time=1.2, rate_func=linear)
        self.wait(0.2)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)
