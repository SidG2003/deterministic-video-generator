"""
Technique: Labelled flow diagram
When: explaining a process as steps or components connected by arrows —
  pipelines, protocols, request/response, cause and effect.
Why: boxes in one row (or column) with arrows between them give a stable map the
  viewer can return to; changing a caption in place, instead of adding more
  text, keeps the map readable as the story advances.
How: boxes = VGroup(...).arrange(RIGHT, buff=...); each label move_to its box and
  shrunk to fit if needed (scale_to_fit_width(box.width - 0.4)); arrows =
  Arrow(a.get_right(), b.get_left(), buff=0.12); one caption in the bottom band
  (to_edge(DOWN)); ReplacementTransform(old_caption, new_caption) to advance;
  ShowPassingFlash along an arrow copy to show something travelling.
Pitfalls: every label must fit inside its box (shrink it, keeping font_size >=
  20, or widen the box); replace captions instead of stacking them; keep the
  whole row inside x in [-6.5, 6.5]; GrowArrow only works on Arrow.
In 3b1b: Arrow ~720, ReplacementTransform ~480, Rectangle ~240 uses in the
  2020-2026 videos.
Tags: diagram, flow, process, pipeline, boxes, arrows, protocol, network
"""

from manim import *


class RequestResponseFlow(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        colors = (BLUE, TEAL, YELLOW)
        boxes = VGroup(*[RoundedRectangle(width=2.6, height=1.2, corner_radius=0.18, color=c, fill_opacity=0.18)
                         for c in colors]).arrange(RIGHT, buff=1.5)
        labels = VGroup(*[Text(name, font_size=30).move_to(box)
                          for name, box in zip(("client", "resolver", "server"), boxes)])
        for label, box in zip(labels, boxes):
            if label.width > box.width - 0.4:
                label.scale_to_fit_width(box.width - 0.4)
        arrows = VGroup(*[Arrow(a.get_right(), b.get_left(), buff=0.12, color=GREY_B)
                          for a, b in zip(boxes, boxes[1:])])
        caption = Text("1. the client asks", font_size=28, color=GREY_B).to_edge(DOWN, buff=0.6)

        self.play(LaggedStart(*[DrawBorderThenFill(b) for b in boxes], lag_ratio=0.2), FadeIn(labels))
        self.play(LaggedStart(*[GrowArrow(a) for a in arrows], lag_ratio=0.3), FadeIn(caption))
        self.play(ShowPassingFlash(arrows[0].copy().set_color(YELLOW), time_width=0.5))
        next_caption = Text("2. the resolver forwards it", font_size=28, color=GREY_B).move_to(caption)
        self.play(ReplacementTransform(caption, next_caption),
                  ShowPassingFlash(arrows[1].copy().set_color(YELLOW), time_width=0.5))
        self.wait(0.6)
