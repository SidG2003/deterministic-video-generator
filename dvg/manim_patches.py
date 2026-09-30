"""
Small, targeted patches to Manim's scene loop, kept in one place.

`fast_forward()` makes skipped animations evolve state EXACTLY as a real render
would, just without rasterizing frames. Manim's own skip collapses each
animation into a single time step, which silently changes path-dependent state
(e.g. TracedPath draws a straight line) — see docs/render-parallelization.md.
Used to run a whole scene fast for analysis (overlap scoring) and, later, to
time-slice renders across workers.

These patch Manim internals: keep Manim pinned (requirements.txt) and re-verify
frame equality when upgrading.
"""

from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def fast_forward():
    """While active, skipped animations: (1) are stepped frame by frame,
    (2) are not rasterized, and (3) get the post-animation updater pass that
    Manim normally runs only when not skipping."""
    from manim.renderer.cairo_renderer import CairoRenderer
    from manim.scene.scene import Scene

    orig_progression = Scene.get_time_progression
    orig_render = CairoRenderer.render
    orig_play_internal = Scene.play_internal

    def get_time_progression(self, run_time, description, n_iterations=None,
                             override_skip_animations=False):
        return orig_progression(self, run_time, description, n_iterations, True)

    def render(self, scene, time, moving_mobjects=None):
        if self.skip_animations:
            return None
        return orig_render(self, scene, time, moving_mobjects)

    def play_internal(self, skip_rendering=False):
        orig_play_internal(self, skip_rendering)
        if self.renderer.skip_animations:
            self.update_mobjects(0)

    Scene.get_time_progression = get_time_progression
    CairoRenderer.render = render
    Scene.play_internal = play_internal
    try:
        yield
    finally:
        Scene.get_time_progression = orig_progression
        CairoRenderer.render = orig_render
        Scene.play_internal = orig_play_internal
