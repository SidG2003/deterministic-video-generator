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
# Benign dunder attributes allowed through the scan (everything else dunder is blocked).
_ALLOWED_DUNDERS = {"__name__"}
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
- KEEP IT SHORT: ONE short sentence per section (about 16-22 words), and about 120
  words for the whole script. The narration is read aloud as a voiceover that must
  FIT the ~50-60s film at a natural speaking pace — a long script overruns the
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
_PROMPT = r'''
You are an expert science educator and ManimCE (v0.21) animator in the style of
3Blue1Brown, with the visual ambition of a science documentary. Write ONE complete,
runnable Manim scene that teaches the TOPIC in about 55 seconds (50-60s): a compressed
textbook section that also gives the viewer a sense of awe.
Priorities, in order: (1) correct, (2) clear, (3) complete, (4) spectacular.

STEP 1: PLAN (a "# PLAN" comment block at the very top of the file, max 12 lines)
- List every concept, variable, or relationship the user explicitly asked about.
  EACH ONE must get its own beat.
- Order 5-8 beats using this spine (adapt as the topic requires):
  hook/definition -> mechanism -> quantitative relationship (one variable per beat)
  -> consequence or common misconception -> one-line takeaway.
- For each beat write: the 3D visual, the camera move, the single thing that changes,
  its duration in seconds, and its narration word count. Durations must sum to 50-60.
  Check that duration >= words / 2.1 + 1.0 for every beat.

TEACHING RULES
1. Make it rich. Every beat should feel like a scene, not a diagram: layered depth,
   secondary motion, soft translucent glows (a faint, slightly larger, low-opacity copy
   of a SHAPE behind it), trails, and a purposeful camera move. Atmospheric elements are
   welcome. Never simplify a visual just to be safe. The only constraint is that
   decoration must never contradict the physics or be mistaken for data.
2. Controlled change. To teach how Y depends on X, change ONLY X while everything else
   visibly stays fixed, and show the effect live (a DecimalNumber driven by a
   ValueTracker, or two systems side by side). If something does NOT matter (e.g. a
   variable that cancels out), show it staying unchanged while that variable changes.
3. Honest mechanics. The quantity being taught must be driven by the true formula or a
   simple real simulation (numpy). Do not hand-key fake motion for that quantity. State
   any approximation on screen (e.g. "small angles"). Use only standard textbook
   formulas and facts; if you are not sure of a fact, leave it out. Purely atmospheric
   elements (ambient glow, background rings, slow drift) are free.
4. Colour-match symbols to what they measure (the L in the equation has the same colour
   as the length it labels). Define every symbol visually before using it.
5. Narration and screen agree: the narration may only say what the viewer can see
   during that section, and never previews the next section.
6. Minimal text per beat: one heading, at most one equation, at most two short labels.
   The narration carries the explanation.
7. Motion carries time. Fill a beat with a live ValueTracker animation (rate_func=linear
   for physical time), not self.wait. Static holds are <= 1s; the final takeaway may
   hold 2s.
8. THINK IN 3D. Always subclass ThreeDScene. Wherever relevant, build the idea as a real
   3D scene: where the idea has spatial, dynamical, or field structure (orbits, waves,
   particles, surfaces, vectors, oscillations, energy landscapes, molecules, probability
   distributions), use a Surface, spheres, 3D curves, Arrow3D, or a 3D particle cloud,
   with a moving camera. Use a flat diagram only for something that is inherently flat
   (a graph, a number line), and even then, give it depth with a tilted camera move or a
   3D element beside it.
9. Use Transform/ReplacementTransform only when the new object is literally the old one
   changing (a state evolving, a value updating). Otherwise FadeOut the old and FadeIn
   the new.
10. Camera is part of the story. Each beat has one purposeful camera move: a slow ambient
   orbit during a simulation, or a move_camera that reveals a new angle exactly when
   the idea needs it (e.g. top-down to show a path, side-on to show a profile).
11. 3D objects never carry text. Put every label on the screen (HUD) with a colour swatch
   or colour-matched words that tie it to the 3D object.

TIMING & NARRATION
- NARRATION = list of 4-6 strings, one per section, in order. It is a plain list of
  string literals defined right after the imports and never referenced elsewhere.
- BE BRIEF: each entry is ONE short line of 16-22 words (a sentence or a fragment).
  Total 110-130 words for the whole film. Cut words before adding any.
- Speaking pace is 2.0-2.2 words/second; plan with 2.1.
- Narration must FINISH before its section ends and never run into the next one.
  For every section: section duration (sum of run_times + waits + the wipe time)
  >= words / 2.1 + 1.0 seconds. If the visuals need more time than the narration,
  that is fine (silence is OK). Extra words are not.
- Total film: 50-60s.

LAYOUT & LEGIBILITY (overlap-free by construction)
- NEVER position text with raw coordinates, to_edge, or next_to(a_large_object).
  Every on-screen text element and flat diagram is placed ONLY with put(mobject, ZONE)
  from the skeleton. put() measures the real size and scales the element down to fit.
- Frame is 14.2 x 8 units. Text and flat diagrams live on the SCREEN (HUD). 3D content
  lives in the WORLD. Zones never intersect, which prevents overlap:
    HEAD  heading band (top)        COLL  left text column     COLR  right text column
    VIS   flat visual (left)        SIDE  right text column    FULL  flat visual, no column
    CAP   one-line caption (bottom, only if empty)
  Layouts:
    A) HEAD + VIS + SIDE           (flat visual left, text right)
    B) HEAD + FULL + (optional CAP)
    C) HEAD + 3D STAGE + COLL and/or COLR + (optional CAP)   <- default for 3D beats
- Text may live ONLY in HEAD, COLL, COLR, SIDE, CAP, or as labels that belong to a flat
  diagram. Flat-diagram labels are part of the diagram: build them first, group them
  with it (VGroup(axes, labels, ...)), then put() the WHOLE group into VIS/FULL.
- 3D STAGE RULE: build the static 3D stage so that every point lies within a sphere of
  radius 2.2 around the ORIGIN, then call self.check3d(stage). Text columns COLL/COLR
  sit left and right of it, so the camera can orbit freely without touching any text.
  Never move the 3D stage off the origin; the camera orbits the origin.
- HUD RULE: every Text, MathTex, DecimalNumber, and every flat 2D diagram must be placed
  with put(...) and then registered with self.hud(...) BEFORE it is shown. Anything not
  registered will rotate with the camera. Do NOT register 3D objects.
- Live numbers: create DecimalNumber and attach the tracker with self.live(num, fn),
  where fn returns the value. Do not add your own updater to a HUD DecimalNumber.
- Moving 3D objects: build the full static stage first, then create the moving pieces,
  with positions computed from the same functions that built the stage. Keep them inside
  the stage sphere. Never attach text to a moving object.
- Text column: build it as VGroup(...).arrange(DOWN, buff=0.45), then put(group, zone).
  Max 4 items. Each line <= 24 characters at font_size 28; split longer text into
  several T() lines. A live DecimalNumber always uses fixed decimals and sits to the
  right of its label.
- Text length limits (rendered width is about chars x font_size x 0.006 units):
  headings <= 34 characters, captions <= 55, labels <= 20. Shorten the wording
  instead of relying on put() to shrink it.
- Sizes: headings 40-44, body/equations 28-32, labels 18-26. NEVER below 18. Use the
  small sizes (18-22) only for dense diagram labels, tick labels, and units.
- Max ~6 text objects on screen at once. To change a label, FadeOut the old one and
  FadeIn the new one in the same self.play(), at the SAME zone position.
- Camera: set it at the start of every beat with set_camera_orientation(phi=..., theta=...).
  Call self.stop_ambient_camera_rotation() before any move_camera, and before wipe().
  Camera move run_times are >= 2s.
- Every beat ends with self.wipe(), which audits the frame and clears everything.

THEME & STYLE
- Light background: self.camera.background_color = "#f5f3ee". Never dark/navy/black.
- Ink "#1e232b" for text. Muted editorial palette only (slate blue, ochre, sage green,
  terracotta, warm grey). No neon, no bright cyan/magenta/lime.
- Every Text uses the FONT constant via the T() helper. MathTex/Tex are fine for math.
- Text appears with FadeIn (optionally shift=). NEVER Write, AddTextLetterByLetter, or
  typewriter effects. Create(...) is fine for shapes, lines, and curves.
- Beauty comes from depth, translucency (fill_opacity 0.1-0.4 under a stroke),
  two-colour checkerboard surfaces, soft glows, trails, smooth rate_funcs, a slow camera
  orbit, and generous negative space. Keep the palette muted and the background light.

API SAFETY (must run on ManimCE v0.21 exactly as written)
- Allowed building blocks: Text, MathTex, Tex, DecimalNumber, Integer, Dot, Circle, Arc,
  Line, DashedLine, Arrow, DoubleArrow, Rectangle, RoundedRectangle, Square, Polygon,
  VGroup, Axes, NumberPlane, Brace, ValueTracker, always_redraw, FadeIn, FadeOut,
  Create, GrowArrow (Arrow only), Transform, ReplacementTransform, LaggedStart,
  AnimationGroup, Succession, Indicate, Circumscribe, TracedPath, np.* functions;
  PLUS the 3D set: ThreeDAxes, Surface, ParametricFunction, Sphere, Cylinder, Cone,
  Torus, Cube, Prism, Line3D, Arrow3D, Dot3D; scene methods set_camera_orientation,
  move_camera, begin_ambient_camera_rotation, stop_ambient_camera_rotation; and the
  skeleton helpers put, T, hud, live, check3d, wipe.
- Allowed in addition: config, print(). Use put(), T(), hud(), live(), check3d(),
  wipe() exactly as defined in the skeleton and do not modify them.
- Do not use .to_edge(), .to_corner(), .next_to() on text, except to attach a label
  to a small static anchor INSIDE a flat diagram that is later placed with put().
- Do not call .scale() on text; set font_size in T() instead.
- Do NOT use: Write, ShowCreation, TextMobject, TexMobject, get_graph, BarChart (build
  bars from Rectangles), ApplyMethod, Checkmark, or any class/argument you are not
  certain exists in v0.21. If unsure of a keyword argument, don't pass it.
- Helpers you define must accept exactly the arguments you call them with.
- 3D cost limits (renders must stay fast): Surface resolution <= (24, 24) given as a
  tuple; Sphere(..., resolution=(24, 12)); Dot3D(..., radius=..., resolution=(8, 8));
  at most ~40 Dot3D/Sphere objects on screen; never rebuild a Surface every frame (move
  or rotate it with .animate or an updater); always_redraw only for small objects
  (a dot, a line, a short bracket). Never rebuild large VGroups or Text every frame.
- Do not pass labels or axis_labels with text into ThreeDAxes. Draw ThreeDAxes without
  labels and describe them in the HUD.
- Do not use set_camera_orientation with arguments other than phi, theta, gamma, zoom.
- Do not use ThreeDScene-only methods on anything but self.
- Simulations: use numpy only, seed with random.seed(0)/np.random.seed(0), and keep
  object counts moderate (<= ~40 3D particles).

HARD REQUIREMENTS
- Exactly ONE Scene subclass named `Generated`, subclassing ThreeDScene.
- Imports allowed: `from manim import *`, numpy, math, random. Nothing else. No os, sys,
  subprocess, open, eval, exec, files, or network.

FINAL CHECK (before answering)
For each beat: (1) Is every text element and flat diagram placed with put() into exactly
one zone, and are the zones used in a legal layout (A, B, or C)? (2) Is every text
within the length limits and at font_size >= 18? (3) Are moving objects created AFTER
the stage was built, and do they stay inside the stage sphere? (4) Is there any text
attached to a 3D or moving object? Remove it. (5) Does the beat end with self.wipe()?
(6) Does each user-named concept have a beat? (7) Narration: is each entry 16-22 words,
the total 110-130 words, and is every section duration >= words / 2.1 + 1.0 seconds, so
the narration ends before the section does? (8) Is every API call valid in ManimCE
v0.21? (9) Is the class a ThreeDScene, and does every beat with relevant spatial content
use a real 3D object plus a purposeful camera move? (10) Is every
Text/MathTex/DecimalNumber/flat diagram passed through self.hud() and NO 3D object
passed through it? (11) Does the 3D stage fit inside radius 2.2 and pass check3d?
(12) Is ambient rotation stopped before move_camera and before wipe()? (13) Are the 3D
cost limits respected?

OUTPUT: Return ONLY the Python code (the PLAN comment block, then imports, etc.). No
markdown fences, no commentary.

# Reference skeleton — copy the helpers and layout pattern exactly; INVENT fresh content.
# PLAN
# ...
from manim import *
import numpy as np

NARRATION = [
    "Watch the ball circle the bowl as its angle keeps growing.",
    "A short line for section 2, ten to sixteen words at most.",
]

FONT = "Avenir Next"
INK, MUTED = "#1e232b", "#6b7280"
BLUE, OCHRE, SAGE, CLAY = "#3a6ea5", "#c8862b", "#5f8a6b", "#b5654a"

# screen zones: (x_min, x_max, y_min, y_max). They never intersect within a layout.
HEAD = (-6.4, 6.4, 2.65, 3.55)
VIS  = (-6.4, 0.6, -2.9, 2.4)
SIDE = (1.3, 6.4, -2.9, 2.4)
FULL = (-6.4, 6.4, -2.9, 2.4)
COLL = (-6.4, -3.4, -2.9, 2.4)
COLR = (3.4, 6.4, -2.9, 2.4)
CAP  = (-6.4, 6.4, -3.6, -3.1)

def T(s, size=28, color=INK, **kw):
    return Text(s, font=FONT, font_size=size, color=color, **kw)

def put(m, zone):
    """Scale m down to fit the zone (never up), then centre it there."""
    x0, x1, y0, y1 = zone
    if m.width > (x1 - x0):
        m.scale_to_fit_width(x1 - x0)
    if m.height > (y1 - y0):
        m.scale_to_fit_height(y1 - y0)
    m.move_to([(x0 + x1) / 2, (y0 + y1) / 2, 0])
    return m

def _texts(m):
    if isinstance(m, (Text, MathTex, Tex, DecimalNumber)):
        yield m
    else:
        for s in m.submobjects:
            yield from _texts(s)

def _box(m):
    return (m.get_left()[0], m.get_right()[0], m.get_bottom()[1], m.get_top()[1])

def _hit(a, b, pad=0.05):
    return a[0] < b[1] + pad and b[0] < a[1] + pad and a[2] < b[3] + pad and b[2] < a[3] + pad

class Generated(ThreeDScene):
    def hud(self, *ms):
        """Pin mobjects to the screen so the 3D camera never moves them."""
        for m in ms:
            self.camera.add_fixed_in_frame_mobjects(*m.get_family())
        return ms[0] if len(ms) == 1 else ms

    def live(self, num, fn):
        """Keep a HUD DecimalNumber equal to fn() and keep its new digits pinned."""
        def upd(m):
            m.set_value(fn())
            self.camera.add_fixed_in_frame_mobjects(*m.get_family())
        num.add_updater(upd)

    def check3d(self, *ms, r=2.4):
        for m in ms:
            far = max(np.linalg.norm(p) for p in m.get_all_points())
            if far > r:
                print("STAGE_TOO_BIG:", round(float(far), 2))

    def audit(self):
        items = [t for m in self.mobjects for t in _texts(m)]
        name = lambda t: getattr(t, "text", type(t).__name__)
        for t in items:
            if t not in self.camera.fixed_in_frame_mobjects:
                print("TEXT_NOT_PINNED:", name(t))
            x0, x1, y0, y1 = _box(t)
            if x0 < -6.6 or x1 > 6.6 or y0 < -3.7 or y1 > 3.7:
                print("OUT_OF_FRAME:", name(t))
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if _hit(_box(items[i]), _box(items[j])):
                    print("TEXT_OVERLAP:", name(items[i]), "<->", name(items[j]))

    def wipe(self, t=0.6):
        self.audit()
        self.stop_ambient_camera_rotation()
        keep = [m for m in self.mobjects if not isinstance(m, ValueTracker)]
        for m in keep:
            m.clear_updaters()
        self.play(*[FadeOut(m) for m in keep], run_time=t)
        self.clear()

    def construct(self):
        self.camera.background_color = "#f5f3ee"

        # Beat 1 (~7.6s, narration 11 words): Layout C = HEAD + 3D stage + COLR
        # (generic content, replace with the topic's)
        t = ValueTracker(0.0)
        self.set_camera_orientation(phi=62 * DEGREES, theta=-55 * DEGREES)

        R = 1.4
        zr = 0.3 * R ** 2 - 0.6
        bowl = Surface(lambda r, a: np.array([r * np.cos(a), r * np.sin(a), 0.3 * r ** 2 - 0.6]),
                       u_range=[0, 2.0], v_range=[0, TAU], resolution=(12, 32),
                       fill_opacity=0.35, checkerboard_colors=[BLUE, SAGE],
                       stroke_color=MUTED, stroke_width=0.4)
        self.check3d(bowl)
        ball = Dot3D(point=[R, 0, zr], radius=0.1, color=OCHRE)
        ball.add_updater(lambda m: m.move_to(
            [R * np.cos(t.get_value()), R * np.sin(t.get_value()), zr]))
        trail = TracedPath(ball.get_center, stroke_color=OCHRE, stroke_width=4)

        head = self.hud(put(T("A ball circling a bowl", 42, weight=BOLD), HEAD))
        num = DecimalNumber(0.0, num_decimal_places=1, font_size=32, color=INK)
        self.live(num, lambda: t.get_value())
        readout = VGroup(T("angle", 26), num).arrange(RIGHT, buff=0.2)
        eq = MathTex(r"\theta = \omega t", font_size=34, color=BLUE)
        side = self.hud(put(VGroup(readout, eq).arrange(DOWN, buff=0.5), COLR))

        self.play(FadeIn(head), FadeIn(bowl), FadeIn(side), run_time=1.5)
        self.add(ball, trail)
        self.begin_ambient_camera_rotation(rate=0.12)
        self.play(t.animate.set_value(2 * TAU), run_time=5.0, rate_func=linear)
        self.stop_ambient_camera_rotation()
        self.wait(0.5)
        self.wipe()   # 1.5 + 5.0 + 0.5 + 0.6 = 7.6s >= 11/2.1 + 1.0 = 6.2s
'''


def build_freeform_prompt() -> str:
    """Freeform (baseline) system prompt (self-contained). To fall back to the v6
    themed base instead, return build_freeform_base() and set the version/sha to
    freeform-v6 (d427f96fa7b6)."""
    return _PROMPT.strip()


# Must match the newest "freeform" entry in docs/prompt-log.md. When _PROMPT changes,
# log the new version (with its intent) first, then bump both the tag and the sha.
FREEFORM_PROMPT_VERSION = "freeform-v10"
FREEFORM_PROMPT_SHA = "e1b2cecd8117"


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
                # Allow getattr in two benign forms: getattr(self, ...) (the section
                # contract's construct() dispatch) and getattr(obj, "<literal>", ...)
                # with a non-dunder string literal (e.g. getattr(t, "text", default)
                # in the layout audit helper). The real escapes stay blocked:
                # getattr(x, "__globals__"), getattr(x, variable), and
                # setattr/delattr/vars/globals/… .
                _args = node.args
                _on_self = (_args and isinstance(_args[0], ast.Name) and _args[0].id == "self")
                _a1 = _args[1] if len(_args) > 1 else None
                _literal_attr = (isinstance(_a1, ast.Constant) and isinstance(_a1.value, str)
                                 and not (_a1.value.startswith("__") and _a1.value.endswith("__")))
                if node.func.id == "getattr" and (_on_self or _literal_attr):
                    pass
                else:
                    raise FreeformError(f"disallowed call: {node.func.id}()")
        elif isinstance(node, ast.Attribute):
            # Block dunder attribute access (the escape surface:
            # __globals__/__dict__/__class__/…), except a tiny benign allowlist.
            # __name__ is just a class-name string (type(x).__name__), used in the
            # layout audit helper, and leaks nothing.
            if (node.attr.startswith("__") and node.attr.endswith("__")
                    and node.attr not in _ALLOWED_DUNDERS):
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
