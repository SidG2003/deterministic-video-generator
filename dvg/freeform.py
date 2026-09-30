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

_PROMPT = r"""
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
- PACING: no target duration; length follows content. Holds/waits ~0.5-1.5s, snappy
  run_times, no dead time.

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
- Dark background by default: self.camera.background_color = "#0b0f1a".
- If you use randomness, seed it: random.seed(0).
- Math: MathTex/Tex (LaTeX installed). Plain text: Text.

NARRATION (required)
- Immediately after the imports, define a module-level list literal named NARRATION:
      NARRATION = [
          "spoken narration for section 1",
          "spoken narration for section 2",
      ]
  One entry per section, in order — the voiceover script a narrator reads ALOUD.
  This is the spoken explanation, distinct from the short on-screen text; write it
  as clear, flowing sentences that teach the idea. It must be a plain list of string
  literals and must NOT be referenced anywhere else in the code (it does not affect
  the animation — it is metadata for a later voiceover).

FINAL CHECK — before answering, walk through the code section by section: what is
on screen after each self.play(...)? Is any text touching other text or the frame
edge? Is anything left over from the previous section? Is there any class, method,
or argument you are not sure exists? Fix these first.

OUTPUT
- Return ONLY the Python code. No markdown fences, no commentary, nothing else.

# Structure reference — shows the layout pattern (invent fresh, richer visuals for the real topic):
from manim import *

NARRATION = [
    "A short spoken line introducing the idea.",
    "The next line, explaining what the animation is showing.",
]

class Generated(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"

        # --- section 1: title moment
        title = Text("The Idea", font_size=48, weight=BOLD)
        subtitle = Text("one clear sentence about it", font_size=28, color="#9fb0d8")
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.3)
        self.play(FadeIn(header, shift=UP * 0.2), run_time=1.0)
        self.wait(0.8)
        self.play(FadeOut(header), run_time=0.6)  # clean stage for the next section

        # --- section 2: one visual, labels placed relative to it
        heading = Text("How it works", font_size=40).to_edge(UP, buff=0.5)
        box_a = RoundedRectangle(width=3, height=1.6, corner_radius=0.2, color="#7aa2ff")
        box_b = box_a.copy().set_color("#6fe3c2")
        boxes = VGroup(box_a, box_b).arrange(RIGHT, buff=2.0)
        arrow = Arrow(box_a.get_right(), box_b.get_left(), buff=0.15)
        label_a = Text("input", font_size=26).next_to(box_a, DOWN, buff=0.3)
        label_b = Text("output", font_size=26).next_to(box_b, DOWN, buff=0.3)
        self.play(FadeIn(heading), Create(boxes), run_time=1.0)
        self.play(GrowArrow(arrow), FadeIn(label_a), FadeIn(label_b), run_time=0.8)
        new_label = Text("result", font_size=26).move_to(label_b)
        self.play(ReplacementTransform(label_b, new_label), run_time=0.6)  # replace, don't stack
        self.wait(0.8)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.6)  # end of section
"""


def build_freeform_prompt() -> str:
    return _PROMPT.strip()


# Must match the newest "freeform" entry in docs/prompt-log.md. When _PROMPT changes,
# log the new version (with its intent) first, then bump both the tag and the sha.
FREEFORM_PROMPT_VERSION = "freeform-v3"
FREEFORM_PROMPT_SHA = "418a8e636f5a"


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
