"""
Static API checker for generated Manim code (uses dvg.kb.api).

Runs on the source BEFORE anything executes and reports calls that would crash
on the installed ManimCE — catching all of them at once, with a precise message,
without spending render time (a runtime crash only shows the first error, and
possibly minutes into a render).

Rules — "error" rules only fire when the crash is certain:
  undefined-name   a name used but defined nowhere: not in the code, not in
                   `from manim import *`, not a builtin or imported module.
  scene-attr       self.<x> in a Scene subclass where the scene type has no <x>
                   (e.g. add_fixed_in_frame_mobjects in a 2D Scene).
  camera-attr      self.camera.<x> that this scene type's camera lacks
                   (e.g. self.camera.frame outside MovingCameraScene).
  grow-arrow       GrowArrow(...) on something that is not an Arrow.
  helper-call      a function/method defined in the code, called with arguments
                   its signature doesn't accept.
  method-missing   obj.<m>(...) where obj was built by a Manim class that has no
                   method <m> (get_*/set_* are skipped: Manim creates those
                   dynamically).
Advisory ("warning", never blocks):
  unknown-kwarg    a keyword not accepted by any __init__ in a Manim class's MRO
                   (Manim forwards **kwargs deeply, so this can't be certain).
"""

from __future__ import annotations

import ast
import symtable
from dataclasses import asdict, dataclass

from .api import manim_api

CHECKER_VERSION = "api-check-v1"  # bump when rules change, so run logs stay comparable

# Mobject methods that return the mobject itself, so `x = Circle().scale(2)` is a Circle.
_SELF_RETURNING = {
    "scale", "shift", "move_to", "next_to", "to_edge", "to_corner", "align_to", "center",
    "rotate", "flip", "stretch", "arrange", "arrange_in_grid", "add", "add_tip",
    "set_color", "set_fill", "set_stroke", "set_opacity", "set_color_by_gradient",
    "set_z_index", "fade", "add_updater", "scale_to_fit_width", "scale_to_fit_height",
    "set_width", "set_height", "match_width", "match_height", "surround", "round_corners",
}


@dataclass
class Finding:
    rule: str
    severity: str  # "error" | "warning"
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


def _module_defs(tree: ast.Module, table: symtable.SymbolTable) -> set[str]:
    defs = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}

    def walk(t):
        for child in t.get_children():
            for s in child.get_symbols():
                if s.is_declared_global() and s.is_assigned():
                    defs.add(s.get_name())
            walk(child)

    walk(table)
    return defs


def _undefined_names(tree, table, api, star_manim: bool) -> list[Finding]:
    known = _module_defs(tree, table) | api.builtins | {"__name__", "__file__"}
    if star_manim:
        known |= api.names
    missing: dict[str, None] = {}

    def walk(t):
        for s in t.get_symbols():
            name = s.get_name()
            if not s.is_referenced() or name in known:
                continue
            is_module = t.get_type() == "module"
            if (is_module and not (s.is_assigned() or s.is_imported())) or (not is_module and s.is_global()):
                missing.setdefault(name)
        for child in t.get_children():
            walk(child)

    walk(table)
    findings = []
    for name in missing:
        line = next((n.lineno for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name), 0)
        findings.append(Finding("undefined-name", "error", line,
                                f"'{name}' is not defined — not in the code, not in manim {api.version}, "
                                "not a builtin. Don't invent classes/helpers; use existing ManimCE ones."))
    return findings


def _call_name(node) -> str | None:
    return node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else None


def _built_class(expr, api) -> str | None:
    """Manim class an expression certainly evaluates to: Cls(...) or Cls(...).scale(..)..."""
    while isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr not in _SELF_RETURNING:
            return None
        expr = expr.func.value
    name = _call_name(expr)
    return name if name in api.classes else None


def _local_types(func: ast.FunctionDef, api) -> dict[str, str]:
    """name -> Manim class, for names assigned exactly once in this function from a
    constructor (so the type is certain)."""
    assigned: dict[str, list] = {}
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [(t, node.value) for t in node.targets]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            targets = [(node.target, node.value if isinstance(node, ast.AnnAssign) else None)]
        elif isinstance(node, (ast.For, ast.comprehension)):
            targets = [(node.target, None)]
        elif isinstance(node, (ast.With,)):
            targets = [(i.optional_vars, None) for i in node.items if i.optional_vars is not None]
        for target, value in targets:
            for t in ast.walk(target):
                if isinstance(t, ast.Name):
                    assigned.setdefault(t.id, []).append(value if t is target else None)
    for arg in func.args.args + func.args.kwonlyargs:
        assigned.setdefault(arg.arg, []).append(None)
    types = {}
    for name, values in assigned.items():
        if len(values) == 1 and values[0] is not None:
            cls = _built_class(values[0], api)
            if cls:
                types[name] = cls
    return types


def _scene_classes(tree: ast.Module, api) -> dict[str, tuple[ast.ClassDef, str]]:
    """User classes deriving (directly or via other user classes) from a Manim scene."""
    user = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    resolved: dict[str, str] = {}

    def base_of(name, seen=()):
        if name in api.scene_attrs:
            return name
        node = user.get(name)
        if node is None or name in seen:
            return None
        for b in node.bases:
            if isinstance(b, ast.Name):
                found = base_of(b.id, seen + (name,))
                if found:
                    return found
        return None

    for name, node in user.items():
        base = base_of(name)
        if base and name not in api.scene_attrs:
            resolved[name] = base
    return {n: (user[n], b) for n, b in resolved.items()}


def _class_own_attrs(node: ast.ClassDef, user_classes: dict[str, ast.ClassDef]) -> set[str]:
    """Methods, class attributes and self.<x> assignments in a user class and its user bases."""
    attrs: set[str] = set()
    stack, seen = [node], set()
    while stack:
        cls = stack.pop()
        if cls.name in seen:
            continue
        seen.add(cls.name)
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                attrs.add(item.name)
            elif isinstance(item, ast.Assign):
                attrs.update(t.id for t in item.targets if isinstance(t, ast.Name))
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                attrs.add(n.attr)
        stack.extend(user_classes[b.id] for b in cls.bases if isinstance(b, ast.Name) and b.id in user_classes)
    return attrs


def _signature_problem(func: ast.FunctionDef, call: ast.Call, is_method: bool) -> str | None:
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return None  # *args / **kwargs at the call site: can't tell statically
    a = func.args
    positional = [p.arg for p in a.posonlyargs + a.args]
    if is_method and positional:
        positional = positional[1:]  # self
    names = set(positional) | {p.arg for p in a.kwonlyargs}
    if a.kwarg is None:
        bad = [k.arg for k in call.keywords if k.arg not in names]
        if bad:
            return f"got unexpected keyword argument(s) {', '.join(repr(b) for b in bad)}"
    if a.vararg is None and len(call.args) > len(positional):
        return f"takes {len(positional)} positional argument(s) but {len(call.args)} were given"
    n_defaults = len(a.defaults)
    required = positional[: len(positional) - n_defaults] if n_defaults else positional
    given = set(positional[: len(call.args)]) | {k.arg for k in call.keywords}
    missing = [r for r in required if r not in given]
    kw_required = [p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is None and p.arg not in given]
    if missing or kw_required:
        return f"missing required argument(s) {', '.join(repr(m) for m in missing + kw_required)}"
    return None


def check_code(src: str) -> list[Finding]:
    """Static API findings for a generated scene (empty list = nothing certain to crash)."""
    api = manim_api()
    tree = ast.parse(src)
    table = symtable.symtable(src, "<scene>", "exec")
    star_manim = any(isinstance(n, ast.ImportFrom) and n.module == "manim"
                     and any(a.name == "*" for a in n.names) for n in tree.body)
    findings = _undefined_names(tree, table, api, star_manim)

    user_classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    module_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    for fname, fnode in module_funcs.items():
        findings += _check_function(fnode, api, module_funcs, None, None, set(), user_classes)
    for cname, (cnode, base) in _scene_classes(tree, api).items():
        own = _class_own_attrs(cnode, user_classes)
        methods = {n.name: n for n in cnode.body if isinstance(n, ast.FunctionDef)}
        for m in methods.values():
            findings += _check_function(m, api, module_funcs, base, methods, own, user_classes)

    findings.sort(key=lambda f: (f.line, f.rule))
    unique, seen = [], set()
    for f in findings:
        key = (f.rule, f.line, f.message)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _check_function(func, api, module_funcs, scene_base, methods, own_attrs, user_classes) -> list[Finding]:
    findings: list[Finding] = []
    types = _local_types(func, api)
    # helpers defined inside this function (e.g. `def make_arrow(...)` within construct)
    local_funcs = {n.name: n for n in ast.walk(func) if isinstance(n, ast.FunctionDef) and n is not func}
    for node in ast.walk(func):
        # self.<x> / self.camera.<x> on a scene
        if scene_base and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            v = node.value
            if isinstance(v, ast.Name) and v.id == "self":
                if node.attr not in api.scene_attrs[scene_base] and node.attr not in own_attrs:
                    findings.append(Finding("scene-attr", "error", node.lineno,
                                            f"self.{node.attr} does not exist on {scene_base} "
                                            f"(manim {api.version}).{_scene_hint(node.attr, api)}"))
            elif (isinstance(v, ast.Attribute) and v.attr == "camera"
                  and isinstance(v.value, ast.Name) and v.value.id == "self"
                  and node.attr not in api.camera_attrs[scene_base]):
                owners = sorted(s for s, attrs in api.camera_attrs.items() if node.attr in attrs)
                hint = f" It exists on the camera of: {', '.join(owners)}." if owners else ""
                findings.append(Finding("camera-attr", "error", node.lineno,
                                        f"self.camera.{node.attr} does not exist in a {scene_base}.{hint}"))
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        # GrowArrow on a non-Arrow
        if name == "GrowArrow" and node.args:
            target = node.args[0]
            cls = _built_class(target, api) or (types.get(target.id) if isinstance(target, ast.Name) else None)
            if cls and not api.is_subclass(cls, "Arrow"):
                findings.append(Finding("grow-arrow", "error", node.lineno,
                                        f"GrowArrow only works on Arrow objects, but this is a {cls}. "
                                        "Use Create(...) instead."))
        # calls to helpers defined in the code
        target_def, is_method = None, False
        if name and name in local_funcs:
            target_def = local_funcs[name]
        elif name and name in module_funcs:
            target_def = module_funcs[name]
        elif (methods and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "self" and node.func.attr in methods):
            target_def, is_method = methods[node.func.attr], True
        if target_def is not None:
            problem = _signature_problem(target_def, node, is_method)
            if problem:
                findings.append(Finding("helper-call", "error", node.lineno,
                                        f"{target_def.name}() {problem} — its definition is "
                                        f"def {target_def.name}({ast.unparse(target_def.args)})."))
        # obj.<method>(...) on a known Manim type
        if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id in types):
            cls, attr = types[node.func.value.id], node.func.attr
            if (not attr.startswith(("get_", "set_", "_")) and attr not in api.classes[cls].members):
                findings.append(Finding("method-missing", "error", node.lineno,
                                        f"{cls} has no method '{attr}' (manim {api.version})."))
        # constructor keywords (advisory)
        if name in api.classes and not api.is_subclass(name, "Scene"):
            accepted = api.classes[name].init_params
            for kw in node.keywords:
                if kw.arg and kw.arg not in accepted:
                    findings.append(Finding("unknown-kwarg", "warning", node.lineno,
                                            f"{name}(...) is not known to accept '{kw.arg}'."))
    return findings


def _scene_hint(attr: str, api) -> str:
    owners = sorted(s for s, attrs in api.scene_attrs.items() if attr in attrs)
    return f" It exists on: {', '.join(owners)}." if owners else ""


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]


def main() -> None:
    import sys
    from pathlib import Path

    for path in sys.argv[1:]:
        findings = check_code(Path(path).read_text())
        print(f"{path}: {len(findings)} finding(s)")
        for f in findings:
            print(f"  [{f.severity}] {f.rule}: {f}")


if __name__ == "__main__":
    main()
