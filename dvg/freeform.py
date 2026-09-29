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
from .generate import _slug

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
3Blue1Brown, then push further for real visual sophistication and beauty. Write a
COMPLETE, runnable Manim scene in Python that explains the given TOPIC as a short
film with genuine AWE factor. Ambition is the point: this should feel designed and
cinematic, never like a plain diagram.

AIM FOR BEAUTY AND WONDER
- Use depth, motion, light, and reveal to create wonder. Every section should look
  deliberate and striking.
- Use 3D whenever the idea is spatial (surfaces, fields, geometry, spacetime,
  orbits, waves in space): subclass ThreeDScene, set the camera with
  self.set_camera_orientation(phi=..., theta=...), and MOVE it —
  self.begin_ambient_camera_rotation(rate=...) to slowly orbit, or
  self.move_camera(phi=..., theta=..., zoom=..., run_time=...) to reveal structure.
  Use ThreeDAxes, Surface (parametric surfaces), Sphere, and 3D curves. Put
  text/labels in the overlay with self.add_fixed_in_frame_mobjects(...).
- In 2D, use MovingCameraScene and move/zoom the camera to direct attention
  (self.camera.frame.animate.scale(...).move_to(...)).

TECHNIQUES THAT CREATE SOPHISTICATION (use several, not just one)
- Living motion: always_redraw(...) driven by a ValueTracker for continuously
  evolving visuals (waves, fields, orbits, deforming surfaces).
- Layered, staggered motion: LaggedStart(...) / AnimationGroup(..., lag_ratio=...)
  so elements cascade in instead of popping at once.
- Expressive pacing: rate_func=smooth / there_and_back / rush_from; ease in and
  out; let key moments breathe, then build to a climax.
- Elegant morphs: Transform / ReplacementTransform / TransformMatchingShapes to
  show one thing BECOMING another.
- Depth and glow: layered opacity, gradients (set_color_by_gradient(...)), faint
  large blurred/low-opacity copies behind bright strokes, subtle background detail.
- Trails and fields: TracedPath for motion trails; ArrowVectorField / StreamLines
  for vector fields.
- A cohesive, deliberate colour palette and strong use of negative space.

STRUCTURE, COVERAGE & PACING
- COVERAGE FIRST: identify the major conceptual parts of the topic, then give each
  its own section. The film must be coherent and COMPLETE — cover every major part
  so the viewer truly understands the whole idea, not just a teaser. Use as many
  sections as the concept genuinely needs.
- Open with a title moment; build one beat at a time in a logical through-line, each
  with a clear visual metaphor. Transition ELEGANTLY between sections (fade/morph/
  camera move, never a hard cut). Keep on-screen text short and purposeful.
- PACING: let length follow content — NO target duration. Keep it tight: holds/
  waits ~0.5-1.5s (just long enough to read/absorb), snappy run_times, no dead
  time or lingering. A section runs only as long as its idea justifies. Longer is
  fine ONLY when more concept earns it.

LAYOUT & LEGIBILITY — NO OVERLAP (this matters as much as beauty)
- Position with RELATIVE layout, not hand-picked absolute coordinates that
  collide: build text/label clusters as VGroup(...).arrange(DOWN/RIGHT,
  buff=0.3+) and attach labels with next_to(target, DIR, buff=0.3+). Avoid
  stacking multiple Text/MathTex at the same point.
- Reserve regions: pin section titles to a band (to_edge(UP, buff=0.5)) and keep
  body content clearly below/apart; use consistent bands so nothing collides.
- CLEAR BEFORE YOU CROWD: before introducing new content in an area, FadeOut (or
  transform) what was there. Keep only a FEW text elements on screen at once —
  don't accumulate labels until they pile up.
- One focal cluster at a time; use camera move/zoom or fade transitions to shift
  attention instead of piling more elements into the same space.
- Mind sizes: scale down long text (font_size / .scale(...)) so measured widths
  fit their region; a label must not run into its neighbour or off-frame.
- Intersections WITHIN a single diagram/surface/field are expected and welcome;
  what to avoid is UNINTENDED overlap of SEPARATE labels/objects.

HARD REQUIREMENTS (all must hold)
- Define exactly ONE Scene subclass named `Generated` (subclass Scene,
  MovingCameraScene, or ThreeDScene).
- `from manim import *` is allowed; you may also import numpy, math, random. NOTHING ELSE.
- No os, sys, subprocess, open(), eval, exec, files, or network in any form.
- Keep content in view (roughly x in [-7,7], y in [-4,4] for 2D; keep 3D objects
  framed). Nothing important clipped or off-screen.
- No UNINTENDED overlap of separate labels/objects — follow LAYOUT & LEGIBILITY
  above (relative layout, reserved bands, clear-before-crowd).
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

OUTPUT
- Return ONLY the Python code. No markdown fences, no commentary, nothing else.

# Structure reference (invent fresh, richer visuals for the real topic):
from manim import *
import numpy as np

NARRATION = [
    "A short spoken line introducing the idea.",
    "The next line, explaining what the animation is showing.",
]

class Generated(ThreeDScene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        self.set_camera_orientation(phi=62 * DEGREES, theta=-45 * DEGREES)
        title = Text("Title", weight=BOLD).scale(0.7).to_corner(UL)
        self.add_fixed_in_frame_mobjects(title)
        t = ValueTracker(0)
        surf = always_redraw(lambda: Surface(
            lambda u, v: np.array([u, v, 0.6 * np.sin(2 * np.sqrt(u * u + v * v) - 3 * t.get_value())]),
            u_range=[-4, 4], v_range=[-4, 4], resolution=(24, 24),
            fill_opacity=0.3, checkerboard_colors=False,
        ).set_fill("#1c4fa0", 0.3).set_stroke("#7aa2ff", 1, 0.7))
        self.add(surf)
        self.begin_ambient_camera_rotation(rate=0.15)
        self.play(t.animate.set_value(3 * PI), run_time=6, rate_func=linear)
"""


def build_freeform_prompt() -> str:
    return _PROMPT.strip()


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
from manim import tempconfig
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
    Scene().render()
'''


def _run(code: str, slug: str, quality: str, timeout: int):
    """Execute the (already-scanned) code in an isolated subprocess. Returns
    (completed_process, media_dir)."""
    tmp = Path(tempfile.mkdtemp(prefix="dvg_ff_"))
    code_path = tmp / "scene.py"
    code_path.write_text(code)
    media_dir = tmp / "media"
    runner = tmp / "runner.py"
    runner.write_text(_RUNNER.format(
        cpu=int(timeout * 2), code_path=str(code_path),
        quality=_QUALITY[quality], slug=slug, media_dir=str(media_dir),
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

    from .generate import DEPTH_HINTS
    from .runlog import timed

    system = build_freeform_prompt()
    slug = _slug(topic)
    user_msg = (
        f"Topic: {topic.strip()}\n"
        f"{DEPTH_HINTS.get(depth, DEPTH_HINTS['standard'])}\n"
        "Write the complete Manim scene."
    )
    if logger:
        logger.artifact("system_prompt.txt", system)
        logger.artifact("prompt.txt", user_msg)
    messages = [{"role": "user", "content": user_msg}]

    last_error = ""
    for attempt in range(max_repairs + 1):
        with timed(logger, "llm_call"):
            reply, tokens, used_model = llm.complete(client, model, system, messages)
        if logger:
            logger.record_tokens("llm_call", tokens, model=used_model)
        code = _FENCE.sub("", reply).strip()
        if logger:
            logger.artifact(f"attempt_{attempt + 1}.py", code)
        try:
            with timed(logger, "scan"):
                scan_code(code)  # fail closed before executing anything
            with timed(logger, "sandbox_render"):
                proc, media_dir = _run(code, slug, quality, timeout)
            with timed(logger, "verify"):
                mp4 = _verify(proc, media_dir, slug)
            dest = Path("media/videos/freeform")
            dest.mkdir(parents=True, exist_ok=True)
            out = dest / f"{slug}.mp4"
            shutil.copy(mp4, out)
            if logger:
                logger.artifact("scene.py", code)
                import json as _json
                logger.artifact("narration.json",
                                _json.dumps(extract_narration(code), indent=2, ensure_ascii=False) + "\n")
            return code, str(out)
        except FreeformError as exc:
            last_error = str(exc)
            messages.append({"role": "assistant", "content": code})
            messages.append({
                "role": "user",
                "content": (
                    f"That scene failed:\n{last_error}\n"
                    "Return the corrected, complete Python code only."
                ),
            })

    raise FreeformError(f"no working scene after {max_repairs + 1} attempts; last error: {last_error}")
