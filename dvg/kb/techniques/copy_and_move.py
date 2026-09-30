"""
Technique: Copy-and-move ("this comes from that")
When: a quantity in a new expression comes from something already on screen — a
  side length going into an area formula, a term carried to the next line of a
  derivation, a value read off a graph into an equation.
Why: moving a copy while the original stays in place shows provenance: the viewer
  sees where each piece comes from without losing the source.
How: build and position the new expression first, with the incoming pieces as
  separate MathTex parts (MathTex("A", "=", "a", r"\\cdot", "a")); then
  TransformFromCopy(source, expression[i]) for each incoming piece, and Write /
  FadeIn the remaining parts in the same self.play(...).
Pitfalls: the target part must exist as its own substring, and be positioned
  before the animation; don't also add the target yourself — TransformFromCopy
  puts it on screen.
In 3b1b: TransformFromCopy appears ~1,160 times in the 2020-2026 videos — a
  signature move in derivations.
Tags: derivation, algebra, provenance, formula, geometry, substitution
"""

from manim import *


class SquareAreaFromSide(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        square = Square(side_length=2.4, color=BLUE, fill_opacity=0.25).shift(LEFT * 3)
        bottom = MathTex("a", font_size=48, color=YELLOW).next_to(square, DOWN, buff=0.25)
        left = MathTex("a", font_size=48, color=YELLOW).next_to(square, LEFT, buff=0.25)
        formula = MathTex("A", "=", "a", r"\cdot", "a", font_size=64).shift(RIGHT * 2.6)
        formula[2].set_color(YELLOW)
        formula[4].set_color(YELLOW)

        self.play(DrawBorderThenFill(square), FadeIn(bottom), FadeIn(left))
        self.play(Write(formula[:2]))
        self.play(TransformFromCopy(bottom, formula[2]), TransformFromCopy(left, formula[4]),
                  FadeIn(formula[3]), run_time=1.4)
        squared = MathTex("A", "=", "a^2", font_size=64).move_to(formula)
        squared[2].set_color(YELLOW)
        self.play(TransformMatchingTex(formula, squared))
        self.wait(0.6)
