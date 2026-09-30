"""
Technique: 3D surface with a guided camera
When: the idea lives in space — a function of two variables, a landscape of
  possibilities, a field over a plane.
Why: a slow, deliberate camera move reveals depth a still frame hides; colouring
  by height makes the shape readable at a glance.
How: ThreeDScene; axes = ThreeDAxes(...); surface = Surface(lambda u, v:
  axes.c2p(u, v, f(u, v)), u_range, v_range, resolution=(24, 24));
  surface.set_fill_by_value(axes=axes, colorscale=[...], axis=2);
  self.set_camera_orientation(phi=..., theta=...); self.move_camera(phi=..., theta=...,
  run_time=...); titles go in the overlay with add_fixed_in_frame_mobjects.
Pitfalls: render cost grows with resolution and with always_redraw — build the
  surface once and move the camera instead of rebuilding; split camera moves
  into ~3s plays (keeps renders splittable); text belongs in the fixed overlay,
  not in 3D space; stop any ambient rotation before the section ends.
In 3b1b: camera reorientation (frame.reorient) ~1,080 uses, ThreeDAxes ~90 in the
  2020-2026 videos (ManimCE: set_camera_orientation / move_camera).
Tags: 3d, surface, camera, landscape, multivariable, height
"""

from manim import *


class SurfaceCameraMove(ThreeDScene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        axes = ThreeDAxes(x_range=[-3, 3, 1], y_range=[-3, 3, 1], z_range=[0, 2, 1],
                          x_length=6, y_length=6, z_length=2.5)
        surface = Surface(lambda u, v: axes.c2p(u, v, 1.6 * np.exp(-(u ** 2 + v ** 2) / 2.5)),
                          u_range=[-3, 3], v_range=[-3, 3], resolution=(24, 24),
                          fill_opacity=0.85, stroke_width=0.4)
        surface.set_fill_by_value(axes=axes, colorscale=[(BLUE_E, 0), (BLUE, 0.6), (TEAL, 1.1), (YELLOW, 1.6)],
                                  axis=2)
        title = Text("A single peak", font_size=36).to_corner(UL, buff=0.5)

        self.set_camera_orientation(phi=65 * DEGREES, theta=-60 * DEGREES)
        self.add_fixed_in_frame_mobjects(title)
        self.play(Create(axes), FadeIn(title), run_time=1)
        self.play(Create(surface), run_time=2)
        self.move_camera(phi=45 * DEGREES, theta=-20 * DEGREES, run_time=3)
        self.move_camera(phi=70 * DEGREES, theta=20 * DEGREES, run_time=3)
        self.wait(0.5)
