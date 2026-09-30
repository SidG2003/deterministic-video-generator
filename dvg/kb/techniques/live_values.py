"""
Technique: Live values (trackers and updaters)
When: a number or shape should change continuously and stay consistent with
  something else — a readout following a slider, a point riding a curve, a label
  that follows an object.
Why: one ValueTracker drives every linked piece, so they move together and stay
  exactly in sync — the viewer sees a relationship, not a sequence of edits.
How: t = ValueTracker(v0); number.add_updater(lambda m: m.set_value(t.get_value()));
  pointer = always_redraw(lambda: ...uses t.get_value()...);
  self.play(t.animate.set_value(v1)).
Pitfalls: always_redraw rebuilds every frame — fine for a dot, a pointer or a
  number, costly for big objects (move an existing object with an updater
  instead). clear_updaters() before fading out or transforming a live object.
  Position live labels relative to what they follow (next_to) so they never
  collide.
In 3b1b: add_updater ~1,390, ValueTracker ~370, ChangeDecimalToValue ~130 uses
  in the 2020-2026 videos.
Tags: tracker, updater, number, slider, dynamic, continuous, readout
"""

from manim import *


class SliderReadout(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        line = NumberLine(x_range=[0, 10, 1], length=10, include_numbers=True, font_size=28).shift(DOWN * 0.5)
        t = ValueTracker(2)
        pointer = always_redraw(lambda: Triangle(color=YELLOW, fill_opacity=1).scale(0.15).rotate(PI)
                                .next_to(line.n2p(t.get_value()), UP, buff=0.1))
        value = DecimalNumber(2, num_decimal_places=2, font_size=56, color=YELLOW)
        readout = VGroup(MathTex("t =", font_size=56), value).arrange(RIGHT, buff=0.25).to_edge(UP, buff=1.0)
        value.add_updater(lambda m: m.set_value(t.get_value()))

        self.play(Create(line), FadeIn(pointer), FadeIn(readout))
        self.play(t.animate.set_value(8.5), run_time=2.5, rate_func=smooth)
        self.play(t.animate.set_value(4), run_time=1.5)
        value.clear_updaters()
        self.wait(0.5)
