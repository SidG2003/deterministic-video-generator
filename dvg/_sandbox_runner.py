"""
Sandboxed single-job runner for the parallel render engine (dvg.parallel_render)
and the section-contract runtime check (dvg.sections).

It is never imported into the generating process. It is spawned as a separate
Python subprocess — `python dvg/_sandbox_runner.py <job.json>` — so model-written
code runs with a CPU rlimit, its own temp dir and its own media dir, and a
crash/hang cannot take down the caller (same spike-level sandbox as
dvg.freeform, see its SECURITY note). The caller never runs the generated code
in-process.

One job (a JSON file given as argv[1]) does exactly one of:
  analyze  — fast-forward the scene without rasterizing and report, per play(),
             its section index and frame count, for the planner to slice on.
  render   — render one work unit (whole scene, one section, or an animation
             slice) to an mp4, reseeding per section so output is reproducible
             and frame-identical whether a section renders alone or in sequence.
  contract — fast-forward through the section harness and report any section
             that ends with visible mobjects, a moved 2D camera, or ambient 3D
             rotation still running.

The result is written as JSON to job["out_json"]. Exit code is 0 on success
(including a clean EndSceneEarlyException from an animation-slice range) and
non-zero on an unhandled error, with a stderr tail the caller can surface.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time


def _seed(idx: int) -> None:
    import random

    import numpy as np

    random.seed(1000 + idx)
    np.random.seed(1000 + idx)


def _camera_snapshot(scene) -> dict:
    """The camera's fresh/default state, captured before any section runs, so
    every section can be given an identical starting camera in both the
    one-process (sequential) and one-process-per-section (parallel) paths."""
    cam = scene.camera
    snap: dict = {}
    frame = getattr(cam, "frame", None)
    if frame is not None:
        snap["frame"] = frame.copy()
    for name in ("phi_tracker", "theta_tracker", "focal_distance_tracker",
                 "gamma_tracker", "zoom_tracker"):
        tracker = getattr(cam, name, None)
        if tracker is not None:
            snap[name] = float(tracker.get_value())
    return snap


def _camera_restore(scene, snap: dict) -> None:
    cam = scene.camera
    frame = getattr(cam, "frame", None)
    if "frame" in snap and frame is not None:
        frame.become(snap["frame"].copy())
    for name, value in snap.items():
        if name == "frame":
            continue
        tracker = getattr(cam, name, None)
        if tracker is not None:
            tracker.clear_updaters()  # stop any ambient rotation
            tracker.set_value(value)


def _reset_stage(scene, snap: dict) -> None:
    """Return the scene to the fresh state a section expects: empty stage, camera
    at its defaults, no ambient rotation."""
    if scene.mobjects:
        scene.remove(*list(scene.mobjects))
    scene.foreground_mobjects = []
    _camera_restore(scene, snap)


def _make_sectioned_scene(base, bg: str, sections: list[str], target: set[int] | None,
                          per_section=None):
    """A subclass of the generated scene whose construct() runs the requested
    sections (all of them, or just `target`), reseeding and resetting to a fresh
    stage before each — the single source of per-section reproducibility shared by
    sequential and parallel renders."""

    def construct(self):
        self.camera.background_color = bg
        snap = _camera_snapshot(self)
        for idx, name in enumerate(sections):
            if target is not None and idx not in target:
                continue
            _reset_stage(self, snap)
            _seed(idx)
            self._dvg_section = idx
            getattr(self, name)()
            if per_section is not None:
                per_section(self, idx, name)

    return type("Generated", (base,), {"construct": construct})


def _load_class(job: dict):
    code_path = job["code_path"]
    with open(code_path) as f:
        src = f.read()
    namespace: dict = {}
    exec(compile(src, code_path, "exec"), namespace)
    cls = namespace.get(job["class_name"])
    if cls is None:
        print(f"NO_CLASS:{job['class_name']}", file=sys.stderr)
        sys.exit(3)
    return cls


def _play_counter_hook(record: list):
    """Patch Scene.play to append {section, frames} once per call. This matches
    Manim's own `num_plays` 1:1 — including frozen waits, which the time
    progression skips — so the play indices the planner slices on line up exactly
    with from_animation_number / upto_animation_number at render time. `frames` is
    an estimate (run_time x fps) used only to balance slices."""
    from manim import config
    from manim.scene.scene import Scene

    current = Scene.play

    def counting(self, *args, **kwargs):
        run_time = kwargs.get("run_time")
        if run_time is None:
            times = [getattr(a, "run_time", None) for a in args]
            times = [t for t in times if t is not None]
            run_time = max(times) if times else 1.0
        frames = max(1, round(run_time * float(config.frame_rate)))
        record.append({"section": int(getattr(self, "_dvg_section", 0)), "frames": frames})
        return current(self, *args, **kwargs)

    Scene.play = counting


def _fast_config() -> dict:
    # Fast pass (analyze / contract): skip past every play so none is rasterized
    # and nothing is written. analyze uses Manim's default skip (only play counts
    # matter); contract wraps this in fast_forward so end-of-section state is real.
    return {"from_animation_number": 10 ** 9, "write_to_movie": False,
            "disable_caching": True, "verbosity": "ERROR", "progress_bar": "none"}


def _do_analyze(job: dict) -> dict:
    from manim import tempconfig

    base = _load_class(job)
    sections = job.get("sections")
    record: list = []
    if sections:
        scene_cls = _make_sectioned_scene(base, job["bg"], sections, None)
    else:
        scene_cls = base
    # Plain skip (no fast_forward): we only enumerate play() calls per section, so
    # Manim's default animation-collapsing skip is the fastest correct way to do it.
    with tempconfig({"quality": job["quality"], "media_dir": job["media_dir"],
                     **_fast_config()}):
        _play_counter_hook(record)
        scene_cls().render()
    return {"plays": record}


def _do_contract(job: dict) -> dict:
    from manim import config, tempconfig

    from dvg.manim_patches import fast_forward
    from dvg.overlap import _visible

    base = _load_class(job)
    sections = job["sections"]
    messages: list = []

    def after_section(scene, idx, name):
        leftover = [m for m in scene.mobjects if _visible(m)]
        if leftover:
            from dvg.overlap import _label
            labels = ", ".join(sorted({_label(m) for m in leftover}))[:120]
            messages.append(
                f"section '{name}' (#{idx + 1}) ends with {len(leftover)} visible "
                f"mobject(s) still on stage ({labels}); FadeOut everything it "
                f"created before the section returns.")
        frame = getattr(scene.camera, "frame", None)
        if frame is not None:
            import numpy as np
            center = frame.get_center()
            off_center = float(np.linalg.norm(center[:2]))
            width_off = abs(float(frame.width) - float(config.frame_width))
            if off_center > 0.05 or width_off > 0.05:
                messages.append(
                    f"section '{name}' (#{idx + 1}) leaves the 2D camera moved "
                    f"(center offset {off_center:.2f}, width delta {width_off:.2f}); "
                    f"restore it (self.play(Restore(self.camera.frame))) before the "
                    f"section returns.")
        for tracker_name in ("theta_tracker", "phi_tracker", "gamma_tracker"):
            tracker = getattr(scene.camera, tracker_name, None)
            if tracker is not None and tracker.get_updaters():
                messages.append(
                    f"section '{name}' (#{idx + 1}) leaves ambient camera rotation "
                    f"running; call self.stop_ambient_camera_rotation() before the "
                    f"section returns.")
                break

    scene_cls = _make_sectioned_scene(base, job["bg"], sections, None,
                                      per_section=after_section)
    with tempconfig({"quality": job["quality"], "media_dir": job["media_dir"],
                     **_fast_config()}):
        with fast_forward():
            scene_cls().render()
    return {"messages": messages}


def _do_render(job: dict) -> dict:
    from manim import tempconfig

    from dvg.manim_patches import fast_forward

    base = _load_class(job)
    sections = job.get("sections")
    anim_range = job.get("anim_range")
    if sections:
        target = set(job["target_sections"]) if job.get("target_sections") is not None else None
        scene_cls = _make_sectioned_scene(base, job["bg"], sections, target)
    else:
        scene_cls = base

    cfg = {"quality": job["quality"], "media_dir": job["media_dir"],
           "output_file": job["output_file"], "disable_caching": True,
           "progress_bar": "none", "verbosity": "ERROR"}
    use_fast_forward = False
    if anim_range is not None:
        cfg["from_animation_number"] = anim_range[0]
        cfg["upto_animation_number"] = anim_range[1]
        use_fast_forward = anim_range[0] > 0  # fast-forward only the skipped prefix

    start = time.perf_counter()
    cpu0 = os.times()
    with tempconfig(cfg):
        if use_fast_forward:
            with fast_forward():
                scene_cls().render()
        else:
            scene_cls().render()
    cpu1 = os.times()
    seconds = time.perf_counter() - start
    cpu = ((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
           + (cpu1.children_user - cpu0.children_user)
           + (cpu1.children_system - cpu0.children_system))
    return {"seconds": round(seconds, 3), "cpu_seconds": round(max(cpu, 0.0), 3)}


_DISPATCH = {"analyze": _do_analyze, "render": _do_render, "contract": _do_contract}


def main() -> None:
    job_path = sys.argv[1]
    with open(job_path) as f:
        job = json.load(f)

    cpu_limit = int(job.get("cpu_limit", 600))
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    except Exception:
        pass
    sys.path.insert(0, job["project_root"])

    result = _DISPATCH[job["kind"]](job)
    with open(job["out_json"], "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
