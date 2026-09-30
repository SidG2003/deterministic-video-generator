"""
Technique: Formula morph with colour-coded symbols
When: stepping through algebra — solving, simplifying, substituting — where the
  same symbols persist from one step to the next.
Why: a symbol that keeps its colour and glides to its new place lets the viewer
  track each quantity; unchanged parts stay still, so the eye goes to what
  changed.
How: write each step as MathTex with every symbol as its own substring
  (MathTex("2", "x", "+", "3", "=", "11")), colour symbols with
  set_color_by_tex("x", YELLOW) or tex_to_color_map={"x": YELLOW}, place each
  step where the previous one was (move_to), then TransformMatchingTex(prev, next).
Pitfalls: TransformMatchingTex matches identical substrings — a symbol buried in
  a longer substring won't match. ManimCE's Tex is text mode: use MathTex for
  maths (manimgl code calls maths Tex). Keep font_size equal across steps.
In 3b1b: TransformMatchingTex ~200 and TransformMatchingStrings ~70 uses; t2c
  colour maps ~640 uses in the 2020-2026 videos.
Tags: algebra, equation, solve, derivation, math, colour
"""

from manim import *


class SolveLinearEquation(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        title = Text("Solve for x", font_size=36, color=GREY_B).to_edge(UP, buff=0.5)
        steps = [
            MathTex("2", "x", "+", "3", "=", "11", font_size=72),
            MathTex("2", "x", "=", "11", "-", "3", font_size=72),
            MathTex("2", "x", "=", "8", font_size=72),
            MathTex("x", "=", "4", font_size=72),
        ]
        for step in steps:
            step.set_color_by_tex("x", YELLOW)
            step.move_to(ORIGIN)

        self.play(FadeIn(title), Write(steps[0]))
        for previous, following in zip(steps, steps[1:]):
            self.wait(0.4)
            self.play(TransformMatchingTex(previous, following), run_time=1.1)
        box = SurroundingRectangle(steps[-1], color=YELLOW, buff=0.25)
        self.play(Create(box))
        self.wait(0.6)
