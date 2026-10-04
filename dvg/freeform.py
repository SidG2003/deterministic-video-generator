"""
Freeform mode: the LLM writes a COMPLETE Manim scene (Python), which we execute
in a sandbox, verify, and repair on failure. This is the max-expressiveness /
max-fragility path — the opposite of the constrained IR pipeline.

SECURITY / THREAT MODEL
-----------------------
We deliberately run model-written Python here, which is a code-execution sink.
The realistic threat is *accidents* (infinite loops, runaway memory, stray file
writes) from your own LLM's code on your own machine — NOT a determined attacker.
So the sandbox is "spike level": an AST allowlist scan (reject imports/calls
outside a small allowlist), a separate subprocess (a crash/hang can't take down
the caller), a wall-clock timeout, and an RLIMIT_CPU backstop. This is good
hygiene, NOT a hardened jail. If topics/code ever come from untrusted end users,
run this inside a network-isolated container (Docker/gVisor) with a read-only FS
instead — see README.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import llm
from .generate import DEPTH_HINTS, _slug
from .runlog import prompt_version

_FENCE = re.compile(r"^```(?:python)?\s*|\s*```$", re.MULTILINE)
_QUALITY = {"l": "low_quality", "m": "medium_quality", "h": "high_quality", "k": "fourk_quality"}

# Only these top-level modules may be imported by generated code.
_ALLOWED_IMPORTS = {"manim", "numpy", "np", "math", "random"}
# Builtins that enable escape / code-exec / IO are forbidden outright.
_FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "open", "__import__", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
    "exit", "quit", "breakpoint",
}
# Generous bound: a thorough --depth deep scene can legitimately run 600+ lines.
# Still caps pathological/runaway output.
_MAX_CODE_LEN = 60000

DEFAULT_MODEL = "claude-opus-5"


class FreeformError(RuntimeError):
    """Raised when generated code is unsafe, fails to run, or fails verification."""


# --- prompt -----------------------------------------------------------------

# freeform-v6 base, kept to compose the sectioned / fan-out scene prompts (they
# inherit its layout + API guidance). The baseline freeform prompt itself is the
# newer self-contained _PROMPT below.
_BASE_PROMPT = r"""
You are a world-class motion designer animating in ManimCE (v0.21) — think
3Blue1Brown. Write a COMPLETE, runnable Manim scene in Python that explains the
given TOPIC as a short film that is both beautiful AND perfectly legible. Clarity
is the foundation and beauty is built on top of it: a gorgeous frame with
colliding labels is a failed frame.

AIM FOR BEAUTY AND WONDER
- Use depth, motion, light, and reveal to create wonder. Every section should look
  deliberate and striking.
- Use 3D when the idea is spatial (surfaces, fields, geometry, orbits, waves in
  space): subclass ThreeDScene, set the camera with
  self.set_camera_orientation(phi=..., theta=...), and move it —
  self.begin_ambient_camera_rotation(rate=...) or
  self.move_camera(phi=..., theta=..., zoom=..., run_time=...). Use ThreeDAxes,
  Surface, Sphere, and 3D curves. In a ThreeDScene, put text/labels in the overlay
  with self.add_fixed_in_frame_mobjects(...).
- In 2D, you may subclass MovingCameraScene and move/zoom the camera to direct
  attention (self.camera.frame.animate.scale(...).move_to(...)).

TECHNIQUES (pick the 1-2 per section that serve the idea — not all of them)
- Living motion: a ValueTracker driving always_redraw(...) or an updater, for
  continuously evolving visuals (waves, orbits, a moving dot, a changing number).
- Staggered entrances: LaggedStart(...) / AnimationGroup(..., lag_ratio=...).
- Expressive pacing: rate_func=smooth / there_and_back / rush_from.
- Morphs: Transform / ReplacementTransform / TransformMatchingShapes to show one
  thing BECOMING another.
- Glow and depth: gradients (set_color_by_gradient(...)), layered opacity, a faint
  slightly larger copy of a SHAPE or stroke behind it (never a copy of text).
- Trails and fields: TracedPath; ArrowVectorField / StreamLines.
- A cohesive colour palette and generous negative space.

STRUCTURE, COVERAGE & PACING
- COVERAGE FIRST: identify the major conceptual parts of the topic and give each its
  own section, so the viewer understands the whole idea, not just a teaser.
- Open with a title moment; build one idea per section with a clear visual metaphor.
  Keep on-screen text short and purposeful — the narration carries the explanation.
- PACING: let each beat breathe — hold a finished beat ~1.5-2s so on-screen text can be
  read, use calm run_times, and never flash text or rush the viewer (no dead time either).

<<LENGTH_RULE>>

LAYOUT & LEGIBILITY — ZERO UNINTENDED OVERLAP (as important as beauty)
- SECTION LIFECYCLE: every section starts on a clean stage. At the end of a section,
  FadeOut everything it created (e.g. self.play(*[FadeOut(m) for m in self.mobjects]))
  — or, if something deliberately continues, transform it into its next form. Never
  leave labels or icons lingering into the next section. Transitions can be fades,
  morphs, or camera moves.
- REPLACE, DON'T STACK: to change a label or value, Transform/ReplacementTransform
  the old one into the new one, or FadeOut the old one in the same self.play(...).
  Never add new text where visible text already is.
- RELATIVE LAYOUT: build text/label clusters with VGroup(...).arrange(DOWN or RIGHT,
  buff=0.3 or more) and attach labels with next_to(target, direction, buff=0.25 or
  more). Avoid hand-picked coordinates for text.
- BANDS: the section title sits in the top band (to_edge(UP, buff=0.5)), the main
  visual in the middle, and at most one caption in the bottom band
  (to_edge(DOWN, buff=0.5)). Before putting a caption or formula at the bottom, make
  sure nothing else (axis labels, legends, captions) is already there.
- SAFE AREA: every text object stays fully inside x in [-6.5, 6.5] and y in
  [-3.6, 3.6] (2D). If a text is wider than its space, split it into lines (a VGroup
  of Text arranged DOWN) or lower its font_size within the size limits.
- SIZES: set text size with font_size, not a tiny .scale(): titles 40-48, body text
  28-34, labels 22-28, never below 20.
- DENSITY: at most ~6 text objects on screen at once. One focal cluster at a time.
- CAMERA: to_edge/to_corner place text relative to the UNZOOMED frame. If you zoom or
  move the 2D camera, keep text well away from the edges, and restore the camera
  (self.camera.frame.save_state() before, self.play(Restore(self.camera.frame))
  after) before the next section.
- Intersections WITHIN a single diagram/surface/field are fine; separate labels and
  objects must never collide.

<<STYLE_RULES>>

RENDER COST (keeps renders fast; the look stays the same)
- Don't rebuild large objects every frame with always_redraw (Surfaces, big
  VGroups); animate them with .animate or an updater that moves them.
  always_redraw is fine for small things (a dot, a number, a short line).
- Keep Surface resolution moderate (up to about (32, 32)).
- Split long animations in heavy 3D scenes into several shorter self.play(...) calls
  (about 3s each).

API SAFETY (the code must run on ManimCE v0.21 exactly as written)
- Use only classes, methods, and arguments that exist in ManimCE v0.21. Do not invent
  classes (e.g. there is no Checkmark — draw one with Lines). If you are not sure a
  keyword argument exists, don't pass it.
- Any helper function you define must accept exactly the arguments you call it with.
- add_fixed_in_frame_mobjects, set_camera_orientation, move_camera and
  begin_ambient_camera_rotation exist ONLY on ThreeDScene; self.camera.frame exists
  ONLY on MovingCameraScene.
- GrowArrow works only on Arrow; for CurvedArrow, DashedLine or a Line with a tip,
  use Create(...).

HARD REQUIREMENTS (all must hold)
- Define exactly ONE Scene subclass named `Generated` (subclass Scene,
  MovingCameraScene, or ThreeDScene).
- `from manim import *` is allowed; you may also import numpy, math, random. NOTHING ELSE.
- No os, sys, subprocess, open(), eval, exec, files, or network in any form.
- Follow LAYOUT & LEGIBILITY: section lifecycle, replace-don't-stack, bands, safe
  area, sizes, density.
- Light background (see THEME & STYLE): e.g. self.camera.background_color = "#f5f3ee";
  never the dark Manim default.
- If you use randomness, seed it: random.seed(0).
- Math: MathTex/Tex (LaTeX installed). Plain text: Text with an explicit font=.

NARRATION (required)
- Immediately after the imports, define a module-level list literal named NARRATION:
      NARRATION = [
          "spoken narration for section 1",
          "spoken narration for section 2",
      ]
  One entry per section, in order — the voiceover script a narrator reads ALOUD.
  This is the spoken explanation, distinct from the short on-screen text.
- KEEP IT SHORT: ONE short sentence per section (about 12-18 words), and about 80
  words for the whole script. The narration is read aloud as a voiceover that must
  FIT the ~30-35s film at a natural speaking pace — a long script overruns the
  video and gets cut off. Teach the idea in a tight sentence; do not write a
  paragraph. It must be a plain list of string literals and must NOT be referenced
  anywhere else in the code (it is metadata for a later voiceover).

FINAL CHECK — before answering, walk through the code section by section: what is
on screen after each self.play(...)? Is any text touching other text or the frame
edge? Is anything left over from the previous section? Is the background light and
every Text given a font=? Did you reveal text with FadeIn (never Write)? Are the
colours muted (no neon) and readable on the light background? Is there any class,
method, or argument you are not sure exists? Fix these first.

OUTPUT
- Return ONLY the Python code. No markdown fences, no commentary, nothing else.

# Structure reference — shows the themed layout pattern (invent fresh, richer visuals):
from manim import *

NARRATION = [
    "A short spoken line introducing the idea.",
    "The next line, explaining what the animation is showing.",
]

FONT = "Avenir Next"
INK = "#1e232b"
PRIMARY = "#3a6ea5"
ACCENT = "#c8862b"

class Generated(Scene):
    def construct(self):
        self.camera.background_color = "#f5f3ee"  # light, not the Manim default

        # --- section 1: title moment (text FADES in, never Write)
        title = Text("The Idea", font="Avenir Next", font_size=48, color=INK, weight=BOLD)
        subtitle = Text("one clear sentence about it", font="Avenir Next", font_size=28, color="#6b7280")
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.3)
        self.play(FadeIn(header, shift=UP * 0.2), run_time=1.0)
        self.wait(1.8)  # hold so the viewer can read it
        self.play(FadeOut(header), run_time=0.5)  # clean stage for the next section

        # --- section 2: one visual, labels placed relative to it
        heading = Text("How it works", font="Avenir Next", font_size=40, color=INK).to_edge(UP, buff=0.5)
        box_a = RoundedRectangle(width=3, height=1.6, corner_radius=0.2, color=PRIMARY)
        box_b = box_a.copy().set_color(ACCENT)
        boxes = VGroup(box_a, box_b).arrange(RIGHT, buff=2.0)
        arrow = Arrow(box_a.get_right(), box_b.get_left(), buff=0.15, color="#6b7280")
        label_a = Text("input", font="Avenir Next", font_size=26, color=INK).next_to(box_a, DOWN, buff=0.3)
        label_b = Text("output", font="Avenir Next", font_size=26, color=INK).next_to(box_b, DOWN, buff=0.3)
        self.play(FadeIn(heading), Create(boxes), run_time=0.8)  # Create is fine for shapes
        self.play(GrowArrow(arrow), FadeIn(label_a), FadeIn(label_b), run_time=0.6)
        new_label = Text("result", font="Avenir Next", font_size=26, color=INK).move_to(label_b)
        self.play(ReplacementTransform(label_b, new_label), run_time=0.6)  # replace, don't stack
        self.wait(1.8)  # let it land
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)  # end of section
"""


def build_freeform_base() -> str:
    """The freeform-v6 base (theme + length injected), used ONLY to compose the
    sectioned and fan-out scene prompts — not sent as the freeform system prompt."""
    from . import theme
    return (_BASE_PROMPT.strip()
            .replace("<<LENGTH_RULE>>", theme.LENGTH_RULE)
            .replace("<<STYLE_RULES>>", theme.STYLE_RULES))


# freeform (baseline) system prompt — self-contained: plan-first, controlled-change
# teaching rules, timing/narration budget, theme, and API safety all inline.
_PROMPT = r"""
You are an expert science educator and ManimCE (v0.21) animator in the style of
3Blue1Brown. Write ONE complete, runnable Manim scene that teaches the TOPIC in
about 35 seconds (30-40s) — a compressed textbook section, not a teaser.
Priorities, in order: (1) correct, (2) clear, (3) complete, (4) beautiful.

STEP 1 — PLAN (a "# PLAN" comment block at the very top of the file, max 10 lines)
- List every concept, variable, or relationship the user explicitly asked about.
  EACH ONE must get its own beat.
- Order 4-6 beats using this spine (adapt as the topic requires):
  hook/definition -> mechanism -> quantitative relationship (one variable per beat)
  -> consequence or common misconception -> one-line takeaway.
- For each beat write: the single visual, the single thing that changes, and its
  duration in seconds (durations must sum to 30-40).

TEACHING RULES
1. Show, don't decorate. Every moving or colored element must represent a quantity or
   idea in the topic. If you cannot say what an animation means, delete it.
2. Controlled change. To teach how Y depends on X, change ONLY X while everything else
   visibly stays fixed, and show the effect live (a DecimalNumber updated from a
   ValueTracker, or two systems side by side). If something does NOT matter (e.g. a
   variable that cancels out), show it staying unchanged while that variable changes.
3. Honest mechanics. Drive visuals from the true formula or a simple real simulation
   (numpy), never hand-keyed fake motion. State any approximation on screen
   (e.g. "small angles"). Use only standard textbook formulas and facts; if you are
   not sure of a fact, leave it out.
4. Colour-match symbols to what they measure (the L in the equation has the same colour
   as the length it labels). Define every symbol visually before using it.
5. Narration and screen agree: the narration may only say what the viewer can see.
6. Minimal text per beat: one heading, at most one equation, at most two short labels.
   The narration carries the explanation.
7. Motion carries time. Fill a beat with a live ValueTracker animation (rate_func=linear
   for physical time), not self.wait. Static holds are <= 1s; the final takeaway may
   hold 2s.
8. 2D by default (Scene). Use ThreeDScene only if the topic is inherently 3D.
9. Use Transform/ReplacementTransform only when the new object is literally the old one
   changing (a state evolving, a value updating). Otherwise FadeOut the old and FadeIn
   the new.

TIMING & NARRATION
- NARRATION = list of 4-6 strings (one per section), 85-100 words in total, 1-2 short
  sentences each. Speaking pace is ~2.5 words/second.
- Each section's total animation time (sum of run_times + waits) must be within +-1s of
  its narration words / 2.5. Total film: 30-40s.
- NARRATION is a plain list of string literals, defined right after the imports, and
  never referenced elsewhere in the code.

LAYOUT & LEGIBILITY (zero unintended overlap)
- Every section starts on a clean stage and ends by clearing it using the wipe() helper
  from the skeleton below. Nothing lingers into the next section.
- Bands: heading in the top band (to_edge(UP, buff=0.5)); the visual in the middle; at
  most one caption in the bottom band, and only if that band is empty.
- Two-zone layout: put the main visual in one zone (e.g. left ~60%) and its labels,
  readouts, and equation in the other (right column or below). Build text clusters with
  VGroup(...).arrange(DOWN/RIGHT, buff>=0.3); attach labels with next_to(..., buff>=0.25).
  Avoid hand-picked coordinates for text.
- Safe area: all text inside x in [-6.5, 6.5], y in [-3.6, 3.6]. If it doesn't fit,
  split into lines or reduce font_size (never below 22).
- Sizes: headings 40-44, body/equations 28-34, labels 22-28.
- Max ~6 text objects on screen at once. To change a label, ReplacementTransform it or
  fade the old one out in the same self.play(); never add text where text already is.
- Moving objects (pendulum bobs, particles) must stay within their own zone and never
  pass through labels. Overlaps inside one diagram (curve crossing an axis) are fine.
- Camera: don't move the camera unless the beat needs it.

THEME & STYLE
- Light background: self.camera.background_color = "#f5f3ee". Never dark/navy/black.
- Ink "#1e232b" for text. Muted editorial palette only (slate blue, ochre, sage green,
  terracotta, warm grey). No neon, no bright cyan/magenta/lime.
- Every Text uses the FONT constant via the T() helper. MathTex/Tex are fine for math.
- Text appears with FadeIn (optionally shift=). NEVER Write, AddTextLetterByLetter, or
  typewriter effects. Create(...) is fine for shapes, lines, and curves.
- Beauty comes from clean composition, soft layered opacity (fill_opacity 0.1-0.25 under
  a stroke), smooth rate_funcs, and negative space, not from effects.

API SAFETY (must run on ManimCE v0.21 exactly as written)
- Allowed building blocks: Text, MathTex, Tex, DecimalNumber, Integer, Dot, Circle, Arc,
  Line, DashedLine, Arrow, DoubleArrow, Rectangle, RoundedRectangle, Square, Polygon,
  VGroup, Axes, NumberPlane, Brace, SurroundingRectangle, ValueTracker, always_redraw,
  FadeIn, FadeOut, Create, GrowArrow (Arrow only), Transform, ReplacementTransform,
  LaggedStart, AnimationGroup, Succession, Indicate, Circumscribe; methods next_to,
  arrange, to_edge, move_to, shift, scale, rotate, set_color, set_opacity,
  add_updater, clear_updaters, axes.plot, axes.c2p, np.* functions.
- Do NOT use: Write, ShowCreation, TextMobject, TexMobject, get_graph, BarChart (build
  bars from Rectangles), ApplyMethod, Checkmark, or any class/argument you are not
  certain exists in v0.21. If unsure of a keyword argument, don't pass it.
- Helpers you define must accept exactly the arguments you call them with.
- Use always_redraw only for small objects (a dot, a line, a short bracket). Use
  DecimalNumber + add_updater(lambda m: m.set_value(tracker.get_value())) for live
  numbers. Never rebuild large VGroups or Text every frame.
- Simulations: use numpy only, seed with random.seed(0)/np.random.seed(0), and keep
  object counts moderate (<= ~60 particles).

HARD REQUIREMENTS
- Exactly ONE Scene subclass named `Generated`.
- Imports allowed: `from manim import *`, numpy, math, random. Nothing else. No os, sys,
  subprocess, open, eval, exec, files, or network.

FINAL CHECK (before answering)
For each section: what is on screen after each self.play()? Does anything overlap or
touch the frame edge? Was the stage wiped? Did every user-named concept get a beat?
Does each section's duration match its narration (words / 2.5)? Is every formula
standard and every claim visible on screen? Is every API call valid in v0.21?

OUTPUT: Return ONLY the Python code (the PLAN comment block, then imports, etc.). No
markdown fences, no commentary.

# Reference skeleton — copy the helpers and layout pattern; INVENT fresh content.
# PLAN
# ... (your plan here)
from manim import *
import numpy as np

NARRATION = [
    "One or two short sentences for section 1.",
    "One or two short sentences for section 2.",
]

FONT = "Avenir Next"
INK, MUTED = "#1e232b", "#6b7280"
BLUE, OCHRE, SAGE, CLAY = "#3a6ea5", "#c8862b", "#5f8a6b", "#b5654a"

def T(s, size=28, color=INK, **kw):
    return Text(s, font=FONT, font_size=size, color=color, **kw)

class Generated(Scene):
    def wipe(self, t=0.6):
        for m in self.mobjects:
            m.clear_updaters()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=t)
        self.clear()

    def construct(self):
        self.camera.background_color = "#f5f3ee"

        # Example of a controlled-change beat (generic content: replace with the topic's)
        q = ValueTracker(1.0)
        head = T("How the response grows", 40, weight=BOLD).to_edge(UP, buff=0.5)
        axes = Axes(x_range=[0, 3, 1], y_range=[0, 9, 3], x_length=5, y_length=3.6,
                    axis_config={"color": MUTED, "include_tip": False}).shift(LEFT * 2.3)
        curve = axes.plot(lambda x: x ** 2, x_range=[0, 3], color=BLUE)
        dot = always_redraw(lambda: Dot(axes.c2p(q.get_value(), q.get_value() ** 2), color=OCHRE))
        readout = VGroup(T("q =", 28), DecimalNumber(1.0, num_decimal_places=1,
                         font_size=32, color=INK)).arrange(RIGHT, buff=0.2)
        eq = MathTex(r"f(q)=q^{2}", font_size=34, color=BLUE)
        side = VGroup(readout, eq).arrange(DOWN, buff=0.5).next_to(axes, RIGHT, buff=1.0)
        readout[1].add_updater(lambda m: m.set_value(q.get_value()))
        self.play(FadeIn(head), Create(axes), Create(curve), FadeIn(side), run_time=1.2)
        self.add(dot)
        self.play(q.animate.set_value(3.0), run_time=4.5, rate_func=linear)  # motion carries time
        self.wait(0.8)
        self.wipe()
"""


def build_freeform_prompt() -> str:
    """Freeform (baseline) system prompt. Self-contained (no theme/length injection)."""
    return _PROMPT.strip()


# Must match the newest "freeform" entry in docs/prompt-log.md. When _PROMPT changes,
# log the new version (with its intent) first, then bump both the tag and the sha.
FREEFORM_PROMPT_VERSION = "freeform-v7"
FREEFORM_PROMPT_SHA = "365fb06ba302"


def freeform_prompt_version() -> str:
    return prompt_version(FREEFORM_PROMPT_VERSION, FREEFORM_PROMPT_SHA, build_freeform_prompt())


def build_freeform_user_message(topic: str, depth: str = "standard") -> str:
    """The exact user message a freeform run sends (shared with cold-sim)."""
    return (
        f"Topic: {topic.strip()}\n"
        f"{DEPTH_HINTS.get(depth, DEPTH_HINTS['standard'])}\n"
        "Write the complete Manim scene."
    )


# --- sandbox: static scan ---------------------------------------------------

def scan_code(code: str) -> None:
    """Reject generated code that imports/calls outside the allowlist. Runs
    BEFORE anything executes (fail-closed)."""
    if len(code) > _MAX_CODE_LEN:
        raise FreeformError("generated code too long")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise FreeformError(f"syntax error: {exc}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _ALLOWED_IMPORTS:
                    raise FreeformError(f"disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _ALLOWED_IMPORTS:
                raise FreeformError(f"disallowed import from: {node.module}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                # The section contract's construct() dispatches with
                # `getattr(self, name)()`; allow getattr ONLY on self (benign
                # dynamic attribute access on the scene). getattr on a module or
                # builtin — the real escape (getattr(x, "__globals__")) — stays
                # blocked, as do setattr/delattr/vars/globals/… .
                if (node.func.id == "getattr" and node.args
                        and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"):
                    pass
                else:
                    raise FreeformError(f"disallowed call: {node.func.id}()")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise FreeformError(f"dunder attribute access: {node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in {"__import__", "__builtins__", "__globals__"}:
                raise FreeformError(f"forbidden name: {node.id}")

    if "Generated" not in code:
        raise FreeformError("code must define a Scene subclass named 'Generated'")


def extract_narration(code: str) -> list[str]:
    """Pull the top-level `NARRATION = [...]` list of strings out of generated code
    (the spoken voiceover script). Returns [] if absent/malformed."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            if any(isinstance(t, ast.Name) and t.id == "NARRATION" for t in node.targets):
                return [
                    el.value for el in node.value.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)
                ]
    return []


# --- sandbox: subprocess render ---------------------------------------------

_RUNNER = '''\
import resource, sys
try:
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu}, {cpu}))
except Exception:
    pass
sys.path.insert(0, "{project_root}")
from manim import tempconfig
from dvg import overlap as _overlap
ns = {{}}
with open("{code_path}") as f:
    src = f.read()
exec(compile(src, "{code_path}", "exec"), ns)
Scene = ns.get("Generated")
if Scene is None:
    print("NO_GENERATED_CLASS", file=sys.stderr)
    sys.exit(3)
with tempconfig({{"quality": "{quality}", "output_file": "{slug}",
                  "media_dir": "{media_dir}", "disable_caching": True}}):
    with _overlap.track() as _tracker:
        try:
            Scene().render()
        finally:
            _overlap.write_report(_tracker, "{report_path}")
'''

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(code: str, slug: str, quality: str, timeout: int):
    """Execute the (already-scanned) code in an isolated subprocess. Returns
    (completed_process, media_dir). The overlap detector's report is written
    next to media_dir (read it with overlap_report(media_dir))."""
    tmp = Path(tempfile.mkdtemp(prefix="dvg_ff_"))
    code_path = tmp / "scene.py"
    code_path.write_text(code)
    media_dir = tmp / "media"
    runner = tmp / "runner.py"
    runner.write_text(_RUNNER.format(
        cpu=int(timeout * 2), code_path=str(code_path), project_root=str(_PROJECT_ROOT),
        quality=_QUALITY[quality], slug=slug, media_dir=str(media_dir),
        report_path=str(tmp / "overlaps.json"),
    ))
    # Inherit env so ffmpeg/latex on PATH are found. Spike-level: no network jail.
    try:
        proc = subprocess.run(
            [sys.executable, str(runner)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(tmp), env=dict(os.environ),
        )
    except subprocess.TimeoutExpired:
        raise FreeformError(f"render timed out after {timeout}s (possible infinite loop)")
    return proc, media_dir


def overlap_report(media_dir: Path) -> dict | None:
    """The overlap detector report written by the sandbox runner for this render."""
    from .overlap import read_report
    return read_report(Path(media_dir).parent / "overlaps.json")


def api_check(code: str, logger=None, attempt: int | None = None,
              scene: int | None = None) -> None:
    """Static ManimCE API check (dvg/kb/check.py) before anything runs. Logs all
    findings; raises a repairable FreeformError listing EVERY certain crash at once
    (a runtime crash would only show the first, possibly minutes into a render).
    `scene` (1-based), when given, tags the logged findings with their scene/section
    (fan-out / sectioned modes)."""
    from .kb.check import CHECKER_VERSION, as_dicts, check_code

    findings = check_code(code)
    if logger is not None:
        logger.record_api_check(as_dicts(findings), CHECKER_VERSION, attempt, scene)
    errors = [f for f in findings if f.severity == "error"]
    if errors:
        lines = "\n".join(f"- {f}" for f in errors)
        raise FreeformError(
            f"The code would crash on ManimCE (static API check, {len(errors)} problem(s)); "
            f"fix every one:\n{lines}")


def _verify(proc, media_dir: Path, slug: str) -> str:
    """Tiered verify: (1) ran cleanly, (2) produced a real video. Returns the
    mp4 path on success; raises FreeformError with a repair-able message."""
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-2000:]
        raise FreeformError(f"render failed (exit {proc.returncode}):\n{tail}")
    mp4s = list(media_dir.glob(f"videos/**/{slug}.mp4"))
    if not mp4s:
        raise FreeformError("code ran but produced no video file")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(mp4s[0])],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        duration = 0.0
    if duration <= 0.5:
        raise FreeformError(f"video is empty or too short (duration={duration}s)")
    return str(mp4s[0])


# --- generate + repair loop -------------------------------------------------

def generate_freeform(
    topic: str,
    model: str = DEFAULT_MODEL,
    max_repairs: int = 3,
    quality: str = "l",
    depth: str = "standard",
    timeout: int = 240,
    client=None,
    logger=None,
) -> tuple[str, str]:
    """Generate a full Manim scene for `topic`, render it in the sandbox, and
    repair on failure. Returns (code, output_mp4_path)."""
    if not topic or not topic.strip():
        raise FreeformError("topic must be a non-empty string")
    if client is None:
        client = llm.make_client(model)

    from .runlog import timed

    system = build_freeform_prompt()
    slug = _slug(topic)
    user_msg = build_freeform_user_message(topic, depth)
    if logger:
        logger.record_prompts(system, user_msg, freeform_prompt_version())
    messages = [{"role": "user", "content": user_msg}]

    last_error = ""
    for attempt in range(max_repairs + 1):
        with timed(logger, "llm_call", attempt=attempt + 1):
            reply, tokens, used_model = llm.complete(client, model, system, messages)
        if logger:
            logger.record_tokens("llm_call", tokens, model=used_model, attempt=attempt + 1)
        code = _FENCE.sub("", reply).strip()
        if logger:
            logger.artifact(f"attempt_{attempt + 1}.py", code)
        try:
            with timed(logger, "scan", attempt=attempt + 1):
                scan_code(code)  # fail closed before executing anything
            with timed(logger, "api_check", attempt=attempt + 1):
                api_check(code, logger, attempt + 1)  # certain crashes, before rendering
            with timed(logger, "sandbox_render", attempt=attempt + 1):
                proc, media_dir = _run(code, slug, quality, timeout)
            with timed(logger, "verify", attempt=attempt + 1):
                mp4 = _verify(proc, media_dir, slug)
            dest = Path("media/videos/freeform")
            dest.mkdir(parents=True, exist_ok=True)
            out = dest / f"{slug}.mp4"
            shutil.copy(mp4, out)
            if logger:
                logger.record_overlaps(overlap_report(media_dir), attempt=attempt + 1)
                logger.artifact("scene.py", code)
                import json as _json
                logger.artifact("narration.json",
                                _json.dumps(extract_narration(code), indent=2, ensure_ascii=False) + "\n")
            return code, str(out)
        except FreeformError as exc:
            last_error = str(exc)
            feedback = (
                f"That scene failed:\n{last_error}\n"
                "Return the corrected, complete Python code only."
            )
            if logger:
                logger.artifact(f"attempt_{attempt + 1}_feedback.txt", feedback)
            messages.append({"role": "assistant", "content": code})
            messages.append({"role": "user", "content": feedback})

    raise FreeformError(f"no working scene after {max_repairs + 1} attempts; last error: {last_error}")
