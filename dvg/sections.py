"""
Section contract shared by approach A (sectioned single call) and approach B
(fan-out, where each scene is a one-section file).

A contract-following scene splits cleanly into independent sections, which is
what lets the parallel render engine (dvg.parallel_render) render each section —
or an animation slice of a heavy one — in its own process and concatenate the
clips into a video that is frame-identical to a one-process sequential render.

Contract (every rule here is enforced, statically where possible and at runtime
otherwise):
  - module-level `SECTIONS = ["title", "intro", ...]` — the section method names,
    in order;
  - exactly one class `Generated` (Scene / MovingCameraScene / ThreeDScene);
  - `construct()` contains only `self.camera.background_color = "..."` and
    `for name in SECTIONS: getattr(self, name)()`;
  - each section starts on an empty stage and ends with no visible mobjects;
  - no `self.<attr> = ...` inside a section method (no shared state — the harness
    renders sections in isolated processes);
  - MovingCameraScene: restore the camera before a section returns;
    ThreeDScene: stop ambient rotation before a section returns;
  - no `random.seed` / `np.random.seed` in the code — the harness reseeds each
    section with `1000 + section_index`;
  - `NARRATION` has exactly one entry per section.

`check_static` catches every rule an AST can prove; `check_runtime` fast-forwards
the scene (sandboxed subprocess) and reports the end-of-section state rules. Both
return messages written to drop straight into a repair prompt.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .freeform import extract_narration

SECTIONS_VERSION = "sections-v1"

_SCENE_BASES = {"Scene", "MovingCameraScene", "ThreeDScene"}
_DEFAULT_BG = "#0b0f1a"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SANDBOX_SCRIPT = Path(__file__).resolve().parent / "_sandbox_runner.py"


# --- shared parsing helpers (used by the engine and the modes) --------------

def parse_sections(code: str) -> list[str] | None:
    """The module-level SECTIONS list (method names in order), or None if it is
    absent or not a plain list of string literals."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List) \
                and any(isinstance(t, ast.Name) and t.id == "SECTIONS" for t in node.targets):
            names = [el.value for el in node.value.elts
                     if isinstance(el, ast.Constant) and isinstance(el.value, str)]
            if len(names) == len(node.value.elts) and names:
                return names
    return None


def parse_background(code: str) -> str:
    """The colour assigned to self.camera.background_color in construct(), or the
    default dark background if none is found. The value may be a string literal or
    a module-level constant that holds one (fan-out scenes set it from a palette
    constant like PAL_BG defined in the shared preamble)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _DEFAULT_BG
    # module-level NAME = "#..." constants, to resolve an indirect background_color.
    consts = {t.id: node.value.value
              for node in tree.body if isinstance(node, ast.Assign)
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
              for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        tgt = node.targets[0]
        if (isinstance(tgt, ast.Attribute) and tgt.attr == "background_color"
                and isinstance(tgt.value, ast.Attribute) and tgt.value.attr == "camera"):
            val = node.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                return val.value
            if isinstance(val, ast.Name) and val.id in consts:
                return consts[val.id]
    return _DEFAULT_BG


def scene_base(code: str) -> str | None:
    """The Manim scene base class of `Generated` (Scene / MovingCameraScene /
    ThreeDScene), or None if it is missing or not one of those."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Generated":
            for b in node.bases:
                if isinstance(b, ast.Name) and b.id in _SCENE_BASES:
                    return b.id
    return None


# --- prompt snippet ---------------------------------------------------------

_SNIPPET = r"""
SECTION CONTRACT (your code is split by section and rendered in parallel — follow it exactly)
- Define a module-level list `SECTIONS = ["title", "intro", ...]` naming, in order,
  the section methods of the scene.
- Define exactly ONE class `Generated` (Scene, MovingCameraScene, or ThreeDScene).
- `construct()` must contain ONLY these two statements, nothing else:
      self.camera.background_color = "#0b0f1a"
      for name in SECTIONS:
          getattr(self, name)()
- Put each section's animation in its own method named in SECTIONS. Each section:
  - starts on an EMPTY stage (assume nothing is on screen);
  - ends with NO visible mobjects — FadeOut everything it created before it returns
    (e.g. self.play(*[FadeOut(m) for m in self.mobjects]));
  - must NOT store state on self (no `self.x = ...`): sections render in separate
    processes and cannot share variables — use local variables only;
  - if it moves/zooms a MovingCameraScene camera, it restores it before returning
    (self.camera.frame.save_state() ... self.play(Restore(self.camera.frame)));
  - if it starts ambient 3D rotation, it calls self.stop_ambient_camera_rotation()
    before returning; set the camera orientation it needs at the START of the section.
- Do NOT call random.seed(...) or np.random.seed(...) anywhere — the harness seeds
  each section deterministically.
- `NARRATION` must have exactly one entry per section, in the same order as SECTIONS.
"""


def contract_prompt_snippet() -> str:
    return _SNIPPET.strip()


# --- static checks ----------------------------------------------------------

def _construct_method(cls: ast.ClassDef) -> ast.FunctionDef | None:
    for item in cls.body:
        if isinstance(item, ast.FunctionDef) and item.name == "construct":
            return item
    return None


def _is_bg_assign(stmt: ast.stmt) -> bool:
    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
        return False
    tgt = stmt.targets[0]
    return (isinstance(tgt, ast.Attribute) and tgt.attr == "background_color"
            and isinstance(tgt.value, ast.Attribute) and tgt.value.attr == "camera")


def _is_section_loop(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.For):
        return False
    if not (isinstance(stmt.iter, ast.Name) and stmt.iter.id == "SECTIONS"):
        return False
    if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Expr):
        return False
    call = stmt.body[0].value
    # getattr(self, name)()
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Call)
            and isinstance(call.func.func, ast.Name) and call.func.func.id == "getattr")


def check_static(code: str) -> list[str]:
    """Static section-contract findings (empty list = passes every static rule).
    Each message carries a line number and is phrased to drop into a repair."""
    out: list[tuple[int, str]] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"line {exc.lineno or 0}: syntax error: {exc.msg}"]

    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Generated"]
    if not classes:
        out.append((0, "no class named 'Generated' is defined; define exactly one "
                       "Scene / MovingCameraScene / ThreeDScene subclass named 'Generated'."))
    elif len(classes) > 1:
        out.append((classes[1].lineno, "more than one class named 'Generated'; define exactly one."))
    generated = classes[0] if classes else None
    if generated is not None and scene_base(code) is None:
        out.append((generated.lineno, "class 'Generated' must subclass Scene, "
                                      "MovingCameraScene, or ThreeDScene."))

    sections = parse_sections(code)
    if sections is None:
        out.append((0, "define a module-level SECTIONS = [...] listing the section "
                       "method names in order (a plain list of string literals)."))

    # construct() must be exactly bg + section loop (a leading docstring is allowed).
    if generated is not None:
        construct = _construct_method(generated)
        if construct is None:
            out.append((generated.lineno, "class 'Generated' has no construct() method."))
        else:
            body = list(construct.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]  # drop a docstring
            saw_bg = saw_loop = False
            for stmt in body:
                if _is_bg_assign(stmt):
                    saw_bg = True
                elif _is_section_loop(stmt):
                    saw_loop = True
                else:
                    out.append((stmt.lineno,
                                "construct() may contain only "
                                "'self.camera.background_color = ...' and "
                                "'for name in SECTIONS: getattr(self, name)()' — remove this line."))
            if not saw_bg:
                out.append((construct.lineno, "construct() must set "
                                              "self.camera.background_color = \"#...\"."))
            if not saw_loop:
                out.append((construct.lineno, "construct() must end with "
                                              "'for name in SECTIONS: getattr(self, name)()'."))

    # every SECTIONS name has a method; section methods don't store self.<attr>.
    method_nodes = {}
    if generated is not None:
        method_nodes = {n.name: n for n in generated.body if isinstance(n, ast.FunctionDef)}
    if sections is not None and generated is not None:
        for name in sections:
            if name not in method_nodes:
                out.append((generated.lineno,
                            f"SECTIONS lists '{name}' but Generated has no method '{name}'."))
        for name in sections:
            node = method_nodes.get(name)
            if node is None:
                continue
            for sub in ast.walk(node):
                targets = []
                if isinstance(sub, ast.Assign):
                    targets = sub.targets
                elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
                    targets = [sub.target]
                for t in targets:
                    if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                            and t.value.id == "self"):
                        out.append((sub.lineno,
                                    f"section '{name}' assigns self.{t.attr}; sections render "
                                    f"in isolated processes and must not share state via self — "
                                    f"use a local variable."))

    # no random.seed / np.random.seed anywhere.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "seed":
            v = node.func.value
            is_random = isinstance(v, ast.Name) and v.id == "random"
            is_np = isinstance(v, ast.Attribute) and v.attr == "random"
            if is_random or is_np:
                out.append((node.lineno, "remove this seed() call; the harness reseeds "
                                         "each section deterministically."))

    # NARRATION: one entry per section.
    narration = extract_narration(code)
    if not narration:
        out.append((0, "define a module-level NARRATION = [...] with one spoken entry per section."))
    elif sections is not None and len(narration) != len(sections):
        out.append((0, f"NARRATION has {len(narration)} entries but SECTIONS has "
                       f"{len(sections)}; there must be exactly one narration per section."))

    out.sort(key=lambda p: p[0])
    seen, unique = set(), []
    for line, msg in out:
        text = f"line {line}: {msg}"
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


# --- runtime check ----------------------------------------------------------

def check_runtime(code_path: str | Path, timeout: int = 180) -> list[str]:
    """Fast-forward the scene through the section harness in a sandboxed
    subprocess and report sections that end with visible mobjects, a moved 2D
    camera, or ambient 3D rotation still running. Returns [] if the scene passes
    (or a single diagnostic message if the fast-forward itself failed)."""
    code_path = Path(code_path).resolve()
    code = code_path.read_text()
    sections = parse_sections(code)
    if sections is None:
        return ["the scene has no usable SECTIONS list (fix the static errors first)."]

    tmp = Path(tempfile.mkdtemp(prefix="dvg_contract_"))
    out_json = tmp / "result.json"
    job = {
        "kind": "contract",
        "code_path": str(code_path),
        "class_name": "Generated",
        "sections": sections,
        "bg": parse_background(code),
        "quality": "low_quality",
        "media_dir": str(tmp / "media"),
        "out_json": str(out_json),
        "project_root": str(_PROJECT_ROOT),
        "cpu_limit": int(timeout * 2),
    }
    job_path = tmp / "job.json"
    job_path.write_text(json.dumps(job))
    try:
        proc = subprocess.run([sys.executable, str(_SANDBOX_SCRIPT), str(job_path)],
                              capture_output=True, text=True, timeout=timeout, cwd=str(tmp),
                              env={**os.environ, "PYTHONHASHSEED": "0"})
    except subprocess.TimeoutExpired:
        return [f"the contract fast-forward timed out after {timeout}s (possible infinite loop)."]
    if proc.returncode != 0 or not out_json.exists():
        tail = (proc.stderr or "").strip()[-1500:]
        return [f"the scene could not be fast-forwarded for the section check:\n{tail}"]
    return json.loads(out_json.read_text()).get("messages", [])
