"""
manimgl (3Blue1Brown's Manim) -> ManimCE translation table.

LLMs have seen a lot of 3Blue1Brown's code, which is written for manimgl, and
mix its API into ManimCE code (e.g. the invented `Checkmark` in a gpt-5.4-mini
run — it is a manimgl/3b1b class). This table maps the manimgl names 3b1b uses
most (counted in 3b1b/videos 2020-2026) to what works in ManimCE.

Every entry is verified against the INSTALLED ManimCE (`verify_table()`), so a
Manim upgrade that changes any of this shows up in `python -m dvg.kb.verify`.
Written from API knowledge + introspection; no 3b1b code is included.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Translation:
    manimgl: str       # what manimgl / 3b1b code writes
    manimce: str       # what to write in ManimCE instead
    note: str          # the gotcha, in one line
    gl_only: bool = True               # manimgl name is absent from ManimCE
    ce_names: tuple[str, ...] = ()     # ManimCE names that must exist for the advice to hold


TABLE: list[Translation] = [
    # --- renamed classes
    Translation("ShowCreation(m)", "Create(m)", "manimgl name for drawing a mobject's outline in.",
                ce_names=("Create",)),
    Translation("TexText('words')", "Tex('words')", "text-mode LaTeX is called Tex in ManimCE.",
                ce_names=("Tex",)),
    Translation("FlashAround(m)", "Circumscribe(m)", "a highlight that traces around a mobject.",
                ce_names=("Circumscribe",)),
    Translation("VShowPassingFlash(m)", "ShowPassingFlash(m)", "a flash travelling along a path.",
                ce_names=("ShowPassingFlash",)),
    Translation("ParametricCurve(f, t_range)", "ParametricFunction(f, t_range=...)",
                "parametric curves are ParametricFunction in ManimCE.", ce_names=("ParametricFunction",)),
    Translation("ParametricSurface(f, ...)", "Surface(f, u_range=..., v_range=...)",
                "parametric surfaces are Surface in ManimCE.", ce_names=("Surface",)),
    Translation("TransformMatchingStrings(a, b)", "TransformMatchingShapes(a, b) / TransformMatchingTex(a, b)",
                "use Shapes for Text, Tex for MathTex split into parts.",
                ce_names=("TransformMatchingShapes", "TransformMatchingTex")),
    Translation("InteractiveScene", "Scene / MovingCameraScene / ThreeDScene",
                "InteractiveScene is manimgl's dev-mode scene.", ce_names=("Scene",)),
    Translation("CountInFrom(number)", "ChangeDecimalToValue(number, value) or a ValueTracker",
                "animate a DecimalNumber/Integer changing value.", ce_names=("ChangeDecimalToValue", "ValueTracker")),
    # --- same name, different meaning (the checker can't catch these)
    Translation("Tex(R'x^2')  # maths", "MathTex(r'x^2')",
                "SAME NAME, DIFFERENT MEANING: manimgl Tex is maths; ManimCE Tex is text-mode LaTeX "
                "(maths in it fails with 'Missing $ inserted'). Use MathTex for maths.",
                gl_only=False, ce_names=("MathTex", "Tex")),
    Translation("Tex(..., t2c={'x': BLUE})", "MathTex(..., tex_to_color_map={'x': BLUE}) or .set_color_by_tex('x', BLUE)",
                "colour parts of maths (Text keeps t2c).", gl_only=False, ce_names=("MathTex",)),
    Translation("VectorField(func, ...)", "ArrowVectorField(func) / StreamLines(func)",
                "ManimCE's VectorField is an abstract base; use the concrete classes.",
                gl_only=False, ce_names=("ArrowVectorField", "StreamLines")),
    # --- camera
    Translation("frame = self.frame", "self.camera.frame  (only in MovingCameraScene)",
                "2D camera moves need a MovingCameraScene.", gl_only=False, ce_names=("MovingCameraScene",)),
    Translation("self.frame.reorient(theta, phi)", "self.set_camera_orientation(phi=..., theta=...) / "
                "self.move_camera(phi=..., theta=...)  (only in ThreeDScene)",
                "3D camera angles; note ManimCE's parameter order is phi then theta.",
                gl_only=False, ce_names=("ThreeDScene",)),
    # --- methods
    Translation("m.set_backstroke(BLACK, 5)", "m.set_stroke(BLACK, 5, background=True)",
                "dark outline behind text for legibility over busy visuals.", gl_only=False, ce_names=()),
    Translation("m.f_always.move_to(other.get_center)", "m.add_updater(lambda mob: mob.move_to(other.get_center()))",
                "manimgl shorthand for updaters.", gl_only=False, ce_names=()),
    # --- not available in ManimCE
    Translation("GlowDot(point)", "Dot(point) plus a few larger, faint copies behind it",
                "no GlowDot in ManimCE; layer Dots with decreasing opacity.", ce_names=("Dot",)),
    Translation("Checkmark() / Exmark()", "Tex(r'\\checkmark') / MathTex(r'\\times'), or draw with Lines",
                "3b1b-only classes, not part of ManimCE.", ce_names=("Tex", "MathTex")),
    Translation("Randolph() / PiCreature()", "(not available)", "3b1b's Pi creatures are not in ManimCE.",
                ce_names=()),
]


def _gl_symbol(entry: Translation) -> str:
    """The bare manimgl identifier(s) in the entry, e.g. 'ShowCreation', 'Checkmark'."""
    import re
    return re.split(r"[^A-Za-z_]", entry.manimgl.strip())[0]


def lookup(name: str) -> Translation | None:
    """The translation for a manimgl-only name used in generated code, if any."""
    for entry in TABLE:
        if entry.gl_only and name in {_gl_symbol(entry), *entry.manimgl.replace("/", " ").split()}:
            return entry
    return None


def verify_table() -> list[str]:
    """Problems with the table against the installed ManimCE (empty = all good)."""
    import manim

    from .api import manim_api

    api = manim_api()
    problems = []
    for entry in TABLE:
        for ce in entry.ce_names:
            if ce not in api.names:
                problems.append(f"{entry.manimgl}: ManimCE target '{ce}' does not exist in manim {api.version}")
        if entry.gl_only:
            sym = _gl_symbol(entry)
            if sym in api.names:
                problems.append(f"{entry.manimgl}: '{sym}' now EXISTS in manim {api.version} — update the entry")
    import inspect
    if "background" not in inspect.signature(manim.VMobject.set_stroke).parameters:
        problems.append("set_stroke(background=True) no longer supported")
    return problems
