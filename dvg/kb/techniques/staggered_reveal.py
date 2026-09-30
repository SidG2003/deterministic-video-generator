"""
Technique: Staggered reveal
When: bringing in a group of related things — list items, a row of boxes, the
  terms of an equation, a grid of cells.
Why: items that arrive one after another, overlapping in time, read as "a set, in
  this order" and lead the eye; everything at once feels flat, strictly one by
  one feels slow.
How: LaggedStart(*[FadeIn(m, shift=...) for m in group], lag_ratio=0.1-0.3), or
  LaggedStartMap(FadeIn, group, shift=UP * 0.2, lag_ratio=...). Arrange the group
  first (arrange / arrange_in_grid) so every item is in its final place.
Pitfalls: lag_ratio near 1 plays items strictly in sequence (slow); near 0 they
  are simultaneous. With 10+ items keep lag_ratio around 0.05 or the total time
  balloons. Don't reveal items that are still going to be repositioned.
In 3b1b: the most used timing tool — lag_ratio appears ~3,000 times in the
  2020-2026 videos, LaggedStart/LaggedStartMap ~2,150 times.
Tags: reveal, list, group, grid, entrance, sequence
"""

from manim import *


class StaggeredList(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        title = Text("Three steps", font_size=40).to_edge(UP, buff=0.5)
        items = VGroup(*[Text(t, font_size=32) for t in ("measure", "compare", "decide")])
        items.arrange(DOWN, aligned_edge=LEFT, buff=0.5)
        dots = VGroup(*[Dot(color=c).next_to(item, LEFT, buff=0.3)
                        for item, c in zip(items, (BLUE, TEAL, YELLOW))])
        self.play(FadeIn(title, shift=DOWN * 0.2))
        self.play(LaggedStart(*[FadeIn(VGroup(d, i), shift=RIGHT * 0.3) for d, i in zip(dots, items)],
                              lag_ratio=0.25), run_time=1.6)
        self.wait(0.6)


class StaggeredGrid(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        cells = VGroup(*[Square(0.55, stroke_width=2) for _ in range(40)]).arrange_in_grid(5, 8, buff=0.12)
        cells.set_color_by_gradient(BLUE, TEAL, YELLOW)
        for cell in cells:
            cell.set_fill(cell.get_color(), opacity=0.3)
        self.play(LaggedStart(*[GrowFromCenter(c) for c in cells], lag_ratio=0.04), run_time=2)
        self.play(LaggedStart(*[c.animate.set_fill(opacity=0.85) for c in cells], lag_ratio=0.03), run_time=1.5)
        self.wait(0.5)
