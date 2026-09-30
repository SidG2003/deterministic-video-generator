"""
Technique: Planned end state (target, then move)
When: rearranging things into a new layout — sorting, regrouping, lining terms
  up — or changing something temporarily and returning to how it was.
Why: the final layout is computed with ordinary layout calls on a copy (the
  target), so the motion ends exactly where a clean static layout would be;
  Restore returns precisely to a saved look.
How: group.generate_target(); rearrange group.target (arrange, move_to,
  set_color, scale); self.play(MoveToTarget(group, path_arc=...)). For a
  temporary change: m.save_state() ... later self.play(Restore(m)).
Pitfalls: generate_target copies the current state — call it once the mobject is
  final; rearrange the target, not the originals; large path_arc values can
  swing items out of the frame.
In 3b1b: generate_target ~820 and MoveToTarget ~770 uses; save_state ~670 and
  Restore ~390 uses in the 2020-2026 videos.
Tags: rearrange, sort, layout, swap, return, reorder
"""

from manim import *


class SortWithTargets(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        values = [5, 2, 8, 1, 6]
        cards = VGroup(*[
            VGroup(RoundedRectangle(width=1.1, height=1.1, corner_radius=0.15, color=BLUE, fill_opacity=0.2),
                   Text(str(v), font_size=36))
            for v in values
        ]).arrange(RIGHT, buff=0.35)
        label = Text("unsorted", font_size=30, color=GREY_B).next_to(cards, UP, buff=0.6)
        self.play(LaggedStart(*[FadeIn(c, shift=UP * 0.2) for c in cards], lag_ratio=0.15), FadeIn(label))

        cards.save_state()
        cards.generate_target()
        order = sorted(range(len(values)), key=lambda i: values[i])
        VGroup(*[cards.target[i] for i in order]).arrange(RIGHT, buff=0.35).move_to(cards)
        sorted_label = Text("sorted", font_size=30, color=YELLOW).move_to(label)
        self.play(MoveToTarget(cards, path_arc=PI / 3), ReplacementTransform(label, sorted_label), run_time=1.6)
        self.wait(0.6)

        back_label = Text("unsorted", font_size=30, color=GREY_B).move_to(sorted_label)
        self.play(Restore(cards), ReplacementTransform(sorted_label, back_label), run_time=1.2)
        self.wait(0.4)
