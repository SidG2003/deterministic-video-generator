"""
Technique: Legible text over busy visuals
When: a label or caption has to sit on top of grids, fields, surfaces or many
  lines.
Why: a dark outline or panel behind text separates it from the background, so
  it stays readable without moving the visual out of the way.
How: text.set_stroke(BLACK, width=6-8, background=True) draws a dark outline
  behind the letters; or BackgroundRectangle(text, fill_opacity=0.85, buff=0.1)
  for a panel. MathTex also accepts background_stroke_color / width.
Pitfalls: this fixes contrast, not collisions — labels still must not overlap
  each other; keep panels tight (buff about 0.1-0.15).
In 3b1b: set_backstroke ~330 uses (ManimCE: set_stroke(..., background=True)),
  BackgroundRectangle ~90 in the 2020-2026 videos.
Tags: legibility, contrast, label, caption, background, readability
"""

from manim import *


class LabelsOverField(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        plane = NumberPlane(background_line_style={"stroke_color": BLUE_D, "stroke_width": 2,
                                                   "stroke_opacity": 0.6})
        field = ArrowVectorField(lambda p: np.array([-p[1], p[0], 0]) * 0.35,
                                 x_range=[-7, 7, 1], y_range=[-4, 4, 1])
        plain = Text("plain label", font_size=34).move_to(LEFT * 3.2 + UP * 1.3)
        backed = Text("with a back stroke", font_size=34).move_to(RIGHT * 3 + UP * 1.3)
        backed.set_stroke(BLACK, width=8, background=True)
        paneled = Text("on a panel", font_size=34).move_to(DOWN * 2)
        panel = BackgroundRectangle(paneled, fill_opacity=0.85, buff=0.15)

        self.play(Create(plane), run_time=1)
        self.play(Create(field), run_time=1.5)
        self.play(FadeIn(plain), FadeIn(backed), FadeIn(panel), FadeIn(paneled))
        self.wait(0.8)
