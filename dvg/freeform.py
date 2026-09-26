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

from .generate import _slug, _text_of

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
_MAX_CODE_LEN = 20000

DEFAULT_MODEL = "claude-opus-5"


class FreeformError(RuntimeError):
    """Raised when generated code is unsafe, fails to run, or fails verification."""


# --- prompt -----------------------------------------------------------------

_PROMPT = r"""
You are an expert ManimCE (Community Edition, v0.21) animator. Write a COMPLETE,
runnable Manim scene in Python that explains the given TOPIC as a short
(~20-40 second) explainer video.

HARD REQUIREMENTS
- Define exactly ONE Scene subclass named `Generated` (subclass Scene or MovingCameraScene).
- `from manim import *` is allowed. You may also import numpy, math, and random. NOTHING ELSE.
- Do NOT use os, sys, subprocess, open(), eval, exec, files, or the network in any form.
- Canvas is ~14.22 wide by 8.0 tall. Keep ALL content within x in [-7, 7] and
  y in [-4, 4] with a small margin — nothing off-screen or clipped.
- Set a dark background: self.camera.background_color = "#0b0f1a" (unless the topic clearly wants light).
- If you use randomness, call random.seed(0) so the render is reproducible.
- Pace it: reveal ideas step by step with self.play(...) and self.wait(...).
- Avoid UNINTENDED overlaps between separate labels/objects. (Intersections WITHIN
  a single diagram — a grid, a graph, crossing edges — are expected and fine.)
- Math: use MathTex / Tex (LaTeX is installed). Plain text: use Text.

PEDAGOGY
- Open with a title. Build intuition one idea at a time. SHOW with visuals
  (shapes, graphs, diagrams, motion) instead of walls of text; keep on-screen
  text short. Make it feel designed and clear.

OUTPUT
- Return ONLY the Python code. No markdown fences, no commentary, nothing else.

# Structure reference only (invent fresh visuals for the real topic):
from manim import *

class Generated(Scene):
    def construct(self):
        self.camera.background_color = "#0b0f1a"
        title = Text("Title", weight=BOLD).scale(0.9)
        self.play(Write(title)); self.wait(0.5)
        self.play(title.animate.scale(0.5).to_edge(UP))
        c = Circle(color=BLUE).set_fill(BLUE, 0.2)
        self.play(Create(c)); self.wait(1.0)
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
    timeout: int = 240,
    client=None,
) -> tuple[str, str]:
    """Generate a full Manim scene for `topic`, render it in the sandbox, and
    repair on failure. Returns (code, output_mp4_path)."""
    if not topic or not topic.strip():
        raise FreeformError("topic must be a non-empty string")
    if client is None:
        import anthropic  # lazy: rendering-only use shouldn't require the SDK

        client = anthropic.Anthropic()

    system = build_freeform_prompt()
    slug = _slug(topic)
    messages = [{"role": "user", "content": f"Topic: {topic.strip()}\nWrite the complete Manim scene."}]

    last_error = ""
    for _ in range(max_repairs + 1):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        code = _FENCE.sub("", _text_of(response)).strip()
        try:
            scan_code(code)  # fail closed before executing anything
            proc, media_dir = _run(code, slug, quality, timeout)
            mp4 = _verify(proc, media_dir, slug)
            dest = Path("media/videos/freeform")
            dest.mkdir(parents=True, exist_ok=True)
            out = dest / f"{slug}.mp4"
            shutil.copy(mp4, out)
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
