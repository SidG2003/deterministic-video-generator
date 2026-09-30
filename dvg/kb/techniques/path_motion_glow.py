"""
Technique: Motion along a path with a glowing trail
When: showing a trajectory, a process moving through states, an orbit, a signal
  travelling.
Why: a moving point pulls the eye; the trail keeps its history visible so the
  shape of the motion is understood once it's done; a soft glow makes the lead
  point feel alive without adding elements.
How: path = ParametricFunction(...) (or any VMobject); dot = Dot(...); glow = a
  VGroup of larger, fainter Dots kept on the dot with add_updater;
  trail = TracedPath(dot.get_center, stroke_color=..., dissipating_time=...);
  self.add(trail) before moving; self.play(MoveAlongPath(dot, path)).
Pitfalls: add the trail to the scene before the motion starts; glow copies are
  shapes, never text; a dissipating trail keeps long motions tidy.
In 3b1b: GlowDot ~230 uses (not in ManimCE — layer Dots instead), MoveAlongPath
  ~40, TracedPath ~17 in the 2020-2026 videos.
Tags: path, trajectory, orbit, motion, trail, glow
"""

from manim import *


class GlowingTracer(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        path = ParametricFunction(lambda t: np.array([3.2 * np.sin(2 * t), 2.2 * np.sin(3 * t), 0]),
                                  t_range=[0, TAU], color=GREY_D, stroke_width=1.5)
        dot = Dot(path.point_from_proportion(0), color=YELLOW, radius=0.09)
        glow = VGroup(*[Dot(radius=0.09 * k, color=YELLOW, fill_opacity=0.18 / k) for k in (2, 3, 4)])
        glow.add_updater(lambda g: g.move_to(dot))
        trail = TracedPath(dot.get_center, stroke_color=YELLOW, stroke_width=3, dissipating_time=1.2)

        self.play(Create(path), run_time=1)
        self.add(trail, glow, dot)
        self.play(MoveAlongPath(dot, path), run_time=5, rate_func=linear)
        glow.clear_updaters()
        self.wait(0.5)
