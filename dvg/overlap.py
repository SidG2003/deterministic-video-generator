"""
Overlap detector — shared by every render path (constrained, freeform sandbox,
cold-sim, and future parallel/sectioned renders).

It hooks Manim's `Scene.play` (which `wait` also goes through) in whatever
process is rendering, and after each animation — i.e. at every settled state —
inspects the visible TEXT objects on screen:

  overlap : two separate text objects whose on-screen boxes intersect by at
            least OVERLAP_RATIO of the smaller box (text-vs-shape is ignored,
            since labels inside boxes are intentional; identical text in nearly
            the same box is a glow/shadow copy and is ignored too)
  clipped : a partly visible text object with at least CLIP_FRACTION of its box
            outside the visible frame (fully off-screen text is invisible, so
            it isn't counted)
  edge    : a text object (not clipped) that pokes into the SAFE_MARGIN band at
            the frame border — it reads as cut off and its container usually is
  tiny    : a text object whose effective font size (after scaling and camera
            zoom) is below MIN_FONT_SIZE

Tracking only: it never fails a render. The report lands in meta.json so
prompts, models and pipeline modes can be compared on the same numbers.
Changing a rule or threshold changes what the numbers mean, so bump
DETECTOR_VERSION when you do.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np

DETECTOR_VERSION = "overlap-v1"
OVERLAP_RATIO = 0.2
DECORATION_IOU = 0.6  # same text, boxes this similar -> intentional glow/shadow copy
CLIP_FRACTION = 0.1
SAFE_MARGIN = 0.03  # fraction of frame width/height kept clear at each border
MIN_FONT_SIZE = 18.0
MIN_OPACITY = 0.05
MAX_ISSUES = 40

_MANIM_DIR = None  # resolved lazily (path prefix of the manim package)


def _text_types():
    from manim import DecimalNumber, MarkupText, Paragraph, SingleStringMathTex, Text
    return (Text, MarkupText, SingleStringMathTex, DecimalNumber, Paragraph)


def _label(mob) -> str:
    for attr in ("original_text", "text", "tex_string"):
        value = getattr(mob, attr, None)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:40]
    if hasattr(mob, "get_value"):
        try:
            return str(mob.get_value())[:40]
        except Exception:
            pass
    return type(mob).__name__


def _visible(mob) -> bool:
    fam = [m for m in mob.get_family() if m.has_points()]
    if not fam:
        return False
    opacity = 0.0
    for m in fam:
        try:
            opacity = max(opacity, float(m.get_fill_opacity()))
            if m.get_stroke_width() > 0:
                opacity = max(opacity, float(m.get_stroke_opacity()))
        except Exception:
            return True  # unknown opacity model -> assume visible
    return opacity > MIN_OPACITY


def _collect_texts(mobjects, types):
    """Top-most text units (a MathTex's own parts are not separate texts)."""
    out = []

    def walk(m):
        if isinstance(m, types):
            out.append(m)
            return
        for s in m.submobjects:
            walk(s)

    for m in mobjects:
        walk(m)
    return out


def _frame(scene):
    """(center_xy, width, height) of the visible frame in scene coordinates."""
    from manim import config
    frame = getattr(scene.camera, "frame", None)  # MovingCameraScene
    if frame is not None and hasattr(frame, "width"):
        c = frame.get_center()
        return np.array(c[:2]), float(frame.width), float(frame.height)
    return np.zeros(2), float(config.frame_width), float(config.frame_height)


def _box(scene, mob):
    """On-screen bounding box (xmin, ymin, xmax, ymax) and a projection scale."""
    cam = scene.camera
    fixed = getattr(cam, "fixed_in_frame_mobjects", None)
    if hasattr(cam, "project_points") and fixed is not None and mob not in fixed:
        pts = mob.get_all_points()
        if len(pts):
            proj = cam.project_points(pts)
            lo, hi = proj[:, :2].min(axis=0), proj[:, :2].max(axis=0)
            scale = (hi[1] - lo[1]) / mob.height if mob.height > 1e-6 else 1.0
            return (lo[0], lo[1], hi[0], hi[1]), scale
    lo, hi = mob.get_corner(np.array([-1, -1, 0])), mob.get_corner(np.array([1, 1, 0]))
    return (lo[0], lo[1], hi[0], hi[1]), 1.0


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersection(a, b):
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


class OverlapTracker:
    """Collects overlap/clipping/tiny-text findings at every settled state."""

    def __init__(self):
        self.checkpoints = 0
        self.overlap_checkpoints = 0
        self.max_texts = 0
        self.errors: list[str] = []
        self._overlaps: dict[tuple[int, int], dict] = {}
        self._clipped: dict[int, dict] = {}
        self._edge: dict[int, dict] = {}
        self._tiny: dict[int, dict] = {}

    def _source_line(self):
        global _MANIM_DIR
        if _MANIM_DIR is None:
            import manim
            _MANIM_DIR = os.path.dirname(manim.__file__)
        f = sys._getframe(2)
        while f is not None:
            name = f.f_code.co_filename
            if not name.startswith(_MANIM_DIR) and name != __file__:
                return f"{os.path.basename(name)}:{f.f_lineno}"
            f = f.f_back
        return None

    def check(self, scene, where: str | None):
        types = _text_types()
        texts = [m for m in _collect_texts(scene.mobjects, types) if _visible(m)]
        self.checkpoints += 1
        self.max_texts = max(self.max_texts, len(texts))
        t = round(float(getattr(scene.renderer, "time", 0.0)), 2)
        context = getattr(scene, "dvg_context", None)
        at = {"t": t, "where": where, **({"context": context} if context else {})}

        center, fw, fh = _frame(scene)
        fx0, fy0, fx1, fy1 = center[0] - fw / 2, center[1] - fh / 2, center[0] + fw / 2, center[1] + fh / 2
        mx, my = SAFE_MARGIN * fw, SAFE_MARGIN * fh
        from manim import config
        zoom = float(config.frame_height) / fh if fh > 0 else 1.0

        boxes = []
        for m in texts:
            box, proj_scale = _box(scene, m)
            boxes.append(box)
            key = id(m)
            area = _area(box)
            if area > 0 and key not in self._clipped:
                inside = _intersection(box, (fx0, fy0, fx1, fy1))
                outside = 1.0 - inside / area
                if outside >= 0.99:
                    pass  # entirely off-screen -> invisible, not a defect
                elif outside >= CLIP_FRACTION:
                    self._clipped[key] = {"type": "clipped", "text": _label(m),
                                          "outside": round(outside, 2), **at}
                    self._edge.pop(key, None)
                elif key not in self._edge and (box[0] < fx0 + mx or box[2] > fx1 - mx
                                                or box[1] < fy0 + my or box[3] > fy1 - my):
                    self._edge[key] = {"type": "edge", "text": _label(m), **at}
            size = getattr(m, "font_size", None)
            if size is not None and key not in self._tiny:
                effective = float(size) * zoom * proj_scale
                if effective < MIN_FONT_SIZE:
                    self._tiny[key] = {"type": "tiny", "text": _label(m),
                                       "font_size": round(effective, 1), **at}

        labels = [_label(m) for m in texts]
        any_overlap = False
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a, b = boxes[i], boxes[j]
                smaller = min(_area(a), _area(b))
                if smaller <= 0:
                    continue
                inter = _intersection(a, b)
                # same text in (nearly) the same box = a glow/shadow copy, not a collision
                if labels[i] == labels[j] and inter / (_area(a) + _area(b) - inter) >= DECORATION_IOU:
                    continue
                ratio = inter / smaller
                if ratio >= OVERLAP_RATIO:
                    any_overlap = True
                    key = tuple(sorted((id(texts[i]), id(texts[j]))))
                    if key not in self._overlaps:
                        self._overlaps[key] = {"type": "overlap", "a": labels[i],
                                               "b": labels[j], "ratio": round(ratio, 2), **at}
        if any_overlap:
            self.overlap_checkpoints += 1

    def report(self) -> dict:
        issues = (list(self._overlaps.values()) + list(self._clipped.values())
                  + list(self._edge.values()) + list(self._tiny.values()))
        issues.sort(key=lambda d: d["t"])
        rep = {
            "detector": DETECTOR_VERSION,
            "thresholds": {"overlap_ratio": OVERLAP_RATIO, "decoration_iou": DECORATION_IOU,
                           "clip_fraction": CLIP_FRACTION,
                           "safe_margin": SAFE_MARGIN, "min_font_size": MIN_FONT_SIZE},
            "checkpoints": self.checkpoints,
            "max_texts_on_screen": self.max_texts,
            "overlap_pairs": len(self._overlaps),
            "overlap_checkpoints": self.overlap_checkpoints,
            "clipped_texts": len(self._clipped),
            "edge_texts": len(self._edge),
            "tiny_texts": len(self._tiny),
            "issues": issues[:MAX_ISSUES],
            "issues_truncated": max(0, len(issues) - MAX_ISSUES),
        }
        if self.errors:
            rep["checker_errors"] = self.errors
        return rep


@contextmanager
def track():
    """Patch Scene.play for the duration of the block; yields the tracker. A
    checker error is recorded, never raised — tracking must not break renders."""
    from manim.scene.scene import Scene

    tracker = OverlapTracker()
    original = Scene.play

    def play(self, *args, **kwargs):
        where = tracker._source_line()
        result = original(self, *args, **kwargs)
        try:
            tracker.check(self, where)
        except Exception as exc:  # pragma: no cover - defensive
            if len(tracker.errors) < 3:
                tracker.errors.append(f"{type(exc).__name__}: {exc}")
        return result

    Scene.play = play
    try:
        yield tracker
    finally:
        Scene.play = original


def write_report(tracker, path: str | Path) -> None:
    Path(path).write_text(json.dumps(tracker.report(), indent=2, ensure_ascii=False) + "\n")


def read_report(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# --- scoring existing runs (fast: no frames are rasterized) -------------------

_FAST = {"from_animation_number": 10**9, "write_to_movie": False,
         "disable_caching": True, "verbosity": "ERROR", "progress_bar": "none"}


def score_scene_file(path: str | Path) -> dict:
    """Score a freeform scene.py in this process without rendering frames."""
    import tempfile

    from manim import tempconfig

    from .freeform import scan_code
    from .manim_patches import fast_forward

    src = Path(path).read_text()
    scan_code(src)  # same fail-closed check as the sandbox, before executing anything
    ns: dict = {}
    exec(compile(src, str(path), "exec"), ns)
    with tempfile.TemporaryDirectory() as media:
        with tempconfig({"quality": "low_quality", "media_dir": media, **_FAST}):
            with fast_forward(), track() as tracker:
                ns["Generated"]().render()
    return tracker.report()


def score_ir_file(path: str | Path) -> dict:
    """Score a constrained ir.json without rendering frames."""
    import tempfile

    from .build import render_with_report
    from .manim_patches import fast_forward

    with tempfile.TemporaryDirectory() as media, fast_forward():
        _, report = render_with_report(str(path), "l", {"media_dir": media, **_FAST})
    return report


def _score_run_dir(run_dir: Path, timeout: int) -> dict:
    import subprocess

    if (run_dir / "scene.py").exists():
        kind, target = "scene", run_dir / "scene.py"
    elif (run_dir / "ir.json").exists():
        kind, target = "ir", run_dir / "ir.json"
    else:
        return {"error": "no scene.py or ir.json"}
    try:
        proc = subprocess.run([sys.executable, "-m", "dvg.overlap", "--score-one", kind, str(target)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode != 0 or not lines:
        return {"error": (proc.stderr.strip().splitlines() or ["failed"])[-1][:120]}
    return json.loads(lines[-1])


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Score run dirs with the overlap detector (fast: no frames rendered).")
    parser.add_argument("run_dirs", nargs="*", help="runs/<timestamp>_<mode>_<slug> directories")
    parser.add_argument("--write", action="store_true",
                        help="store the report in each run's meta.json as `overlaps` (rescored: true)")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--score-one", nargs=2, metavar=("KIND", "PATH"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.score_one:
        kind, path = args.score_one
        report = score_scene_file(path) if kind == "scene" else score_ir_file(path)
        print(json.dumps(report, ensure_ascii=False))
        return

    print(f"{'run':60s} {'overlap':>7s} {'clip':>4s} {'edge':>4s} {'tiny':>4s} {'max_txt':>7s}")
    for d in args.run_dirs:
        run_dir = Path(d)
        report = _score_run_dir(run_dir, args.timeout)
        if "error" in report:
            print(f"{run_dir.name[:60]:60s} ERROR: {report['error']}")
            continue
        print(f"{run_dir.name[:60]:60s} {report['overlap_pairs']:7d} {report['clipped_texts']:4d} "
              f"{report['edge_texts']:4d} {report['tiny_texts']:4d} {report['max_texts_on_screen']:7d}")
        meta_path = run_dir / "meta.json"
        if args.write and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            meta["overlaps"] = {"rescored": True, **report}
            meta_path.write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()
