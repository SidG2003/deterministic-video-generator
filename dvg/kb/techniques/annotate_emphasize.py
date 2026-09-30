"""
Technique: Annotate and emphasize
When: pointing at part of something already on screen — naming a term, grouping
  a span, marking the answer, calling attention to what just changed.
Why: an annotation ties words to the exact part they describe without adding a
  new layout region; a brief emphasis directs the eye and then gets out of the
  way.
How: Brace(part, DOWN) with a Text label placed next_to the brace;
  SurroundingRectangle(part, buff=0.1) to box; Underline(part);
  Circumscribe(part) / Indicate(part) / ShowPassingFlash(...) for a transient
  highlight. Labels sit outside the thing they label (next_to), never on it.
Pitfalls: remove an old annotation before adding a new one on the same part;
  brace labels count toward the ~6-texts-on-screen budget; transient emphasis
  (Circumscribe, Indicate) beats leaving permanent boxes everywhere.
In 3b1b: SurroundingRectangle ~1,000, Brace ~380, FlashAround ~200 (ManimCE:
  Circumscribe), Underline ~155 uses in the 2020-2026 videos.
Tags: highlight, brace, box, label, emphasis, annotation, underline
"""

from manim import *


class AnnotateTerms(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        eq = MathTex("E", "=", "m", "c^2", font_size=96)
        eq.set_color_by_tex("m", BLUE)
        eq.set_color_by_tex("c^2", YELLOW)
        self.play(Write(eq))

        brace_m = Brace(eq[2], DOWN, buff=0.15)
        label_m = Text("mass", font_size=30, color=BLUE).next_to(brace_m, DOWN, buff=0.15)
        brace_c = Brace(eq[3], UP, buff=0.15)
        label_c = Text("speed of light, squared", font_size=30, color=YELLOW).next_to(brace_c, UP, buff=0.15)
        self.play(GrowFromCenter(brace_m), FadeIn(label_m, shift=UP * 0.1))
        self.play(GrowFromCenter(brace_c), FadeIn(label_c, shift=DOWN * 0.1))
        self.play(Circumscribe(eq[3], color=YELLOW, fade_out=True))

        box = SurroundingRectangle(eq, color=TEAL, buff=0.25)
        self.play(FadeOut(brace_m, label_m, brace_c, label_c), Create(box))
        self.play(Indicate(eq, color=TEAL))
        self.wait(0.5)
