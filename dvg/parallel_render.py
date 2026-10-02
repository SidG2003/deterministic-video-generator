"""
Parallel render engine.

A render is broken into independent WORK UNITS, each rendered in its own
sandboxed subprocess (CPU rlimit, its own temp dir and media dir), and the unit
clips are concatenated in order into the final video. Because a ~16-core machine
spends ~15 cores idle on a single Manim render, and because one heavy section can
dominate wall time (measured: 78% in one film), splitting across units — sections
in parallel, and heavy sections time-sliced by animation range — is where the
wall-time wins come from. See docs/render-parallelization.md.

A work unit is one of:
  scene    the whole file in one process (sequential strategy, or the fallback
           when nothing is splittable);
  section  one section method of a contract-following scene;
  slice    an animation-number range [a, b] of a section (sectioned) or of the
           whole scene (an unsectioned benchmark). The skipped prefix is
           fast-forwarded (manim_patches.fast_forward) so path-dependent state
           (e.g. a TracedPath) evolves exactly as in a full render.

Both `--render-strategy sequential` and `parallel` run through this one engine —
sequential is just a single whole-file unit on one worker — so their output is
produced by the same code path. For a contract-following scene the sequential
unit still runs through the per-section harness (reseed + fresh stage per
section), so sequential and parallel renders are frame-identical.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import sections as sections_mod

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SANDBOX_SCRIPT = Path(__file__).resolve().parent / "_sandbox_runner.py"
_QUALITY = {"l": "low_quality", "m": "medium_quality", "h": "high_quality", "k": "fourk_quality"}

# Split a section only when it is both a real fraction of the video and not tiny.
_SPLIT_FRACTION = 0.25
_SPLIT_MIN_FRAMES = 150


class ParallelRenderError(RuntimeError):
    """Raised when a work unit fails to render or the final video is bad."""


@dataclass
class _Unit:
    index: int                      # position in the final video (concat order)
    kind: str                       # "scene" | "section" | "slice"
    output_file: str
    sections: list[str] | None = None
    target_sections: list[int] | None = None
    anim_range: list[int] | None = None
    section: int | None = None
    est_frames: int = 0
    # filled in after rendering:
    seconds: float = 0.0
    cpu_seconds: float = 0.0
    frames: int = 0
    worker: int | None = None
    mp4: str | None = None


# --- planning ---------------------------------------------------------------

def _should_split(section_frames: int, total_frames: int, n_plays: int) -> bool:
    return (n_plays >= 2 and section_frames > _SPLIT_MIN_FRAMES
            and section_frames > _SPLIT_FRACTION * total_frames)


def _n_slices(section_frames: int, total_frames: int, workers: int, n_plays: int) -> int:
    target = max(_SPLIT_MIN_FRAMES, _SPLIT_FRACTION * total_frames)
    n = max(2, round(section_frames / target))
    return max(1, min(n, workers, n_plays))


def _balance(frame_counts: list[int], n_slices: int) -> list[tuple[int, int]]:
    """Partition play indices 0..len-1 into `n_slices` contiguous ranges with
    roughly equal frame sums (every range non-empty). Returns [(a, b), ...]
    inclusive."""
    n = len(frame_counts)
    n_slices = max(1, min(n_slices, n))
    if n_slices <= 1:
        return [(0, n - 1)]
    total = sum(frame_counts)
    target = total / n_slices
    ranges: list[tuple[int, int]] = []
    start = 0
    acc = 0
    for i, f in enumerate(frame_counts):
        acc += f
        slices_left = n_slices - len(ranges)
        plays_left_after = n - (i + 1)
        # Close now if we must (only just enough plays remain to fill the rest),
        # or if we've hit the target and can still leave one play per later slice.
        must_close = plays_left_after == slices_left - 1
        want_close = acc >= target and plays_left_after >= slices_left - 1
        if slices_left > 1 and (must_close or want_close):
            ranges.append((start, i))
            start = i + 1
            acc = 0
    ranges.append((start, n - 1))
    return ranges


def _plays_by_section(plays: list[dict], n_sections: int) -> list[list[int]]:
    """Group analysis frame counts by section, preserving play order within each."""
    grouped: list[list[int]] = [[] for _ in range(max(n_sections, 1))]
    for p in plays:
        s = p["section"] if p["section"] < len(grouped) else len(grouped) - 1
        grouped[s].append(int(p["frames"]))
    return grouped


def _plan_parallel(sections: list[str] | None, plays: list[dict], workers: int) -> list[_Unit]:
    units: list[_Unit] = []
    idx = 0
    if sections:
        grouped = _plays_by_section(plays, len(sections))
        total = sum(sum(g) for g in grouped) or 1
        for s, counts in enumerate(grouped):
            section_frames = sum(counts)
            if _should_split(section_frames, total, len(counts)):
                n = _n_slices(section_frames, total, workers, len(counts))
                for (a, b) in _balance(counts, n):
                    units.append(_Unit(index=idx, kind="slice", output_file=f"unit_{idx:04d}",
                                       sections=sections, target_sections=[s], anim_range=[a, b],
                                       section=s, est_frames=sum(counts[a:b + 1])))
                    idx += 1
            else:
                units.append(_Unit(index=idx, kind="section", output_file=f"unit_{idx:04d}",
                                   sections=sections, target_sections=[s], section=s,
                                   est_frames=section_frames))
                idx += 1
    else:
        counts = [int(p["frames"]) for p in plays]
        total = sum(counts) or 1
        if _should_split(total, total, len(counts)):
            n = _n_slices(total, total, workers, len(counts))
            for (a, b) in _balance(counts, n):
                units.append(_Unit(index=idx, kind="slice", output_file=f"unit_{idx:04d}",
                                   anim_range=[a, b], est_frames=sum(counts[a:b + 1])))
                idx += 1
    return units


# --- sandboxed subprocess jobs ----------------------------------------------

def _worker_env() -> dict:
    """Inherit the environment (ffmpeg/LaTeX on PATH) but pin PYTHONHASHSEED so
    every worker hashes identically — without it each subprocess randomizes its
    own seed, and set/dict iteration order in Manim's render path (notably 3D)
    makes otherwise-identical renders diverge frame to frame."""
    return {**os.environ, "PYTHONHASHSEED": "0"}


def _spawn(job: dict, timeout: int) -> subprocess.CompletedProcess:
    tmp = Path(job["_tmp"])
    job_path = tmp / f"job_{job.get('output_file', job['kind'])}.json"
    job_path.write_text(json.dumps({k: v for k, v in job.items() if not k.startswith("_")}))
    return subprocess.run([sys.executable, str(_SANDBOX_SCRIPT), str(job_path)],
                          capture_output=True, text=True, timeout=timeout, cwd=str(tmp),
                          env=_worker_env())


def _analyze(code_path: Path, sections: list[str] | None, bg: str, quality: str,
             tmp: Path, timeout: int) -> list[dict]:
    out_json = tmp / "analysis.json"
    job = {
        "kind": "analyze", "code_path": str(code_path), "class_name": "Generated",
        "sections": sections, "bg": bg, "quality": quality,
        "media_dir": str(tmp / "analysis_media"), "out_json": str(out_json),
        "project_root": str(_PROJECT_ROOT), "cpu_limit": int(timeout * 2),
        "_tmp": str(tmp),
    }
    proc = _spawn(job, timeout)
    if proc.returncode != 0 or not out_json.exists():
        tail = (proc.stderr or "").strip()[-1500:]
        raise ParallelRenderError(f"render analysis pass failed:\n{tail}")
    return json.loads(out_json.read_text())["plays"]


def _execute_unit(unit: _Unit, code_path: Path, bg: str, quality: str, tmp: Path,
                  timeout: int, worker: int) -> None:
    unit_dir = tmp / unit.output_file
    unit_dir.mkdir(exist_ok=True)
    media_dir = unit_dir / "media"
    out_json = unit_dir / "unit.json"
    job = {
        "kind": "render", "code_path": str(code_path), "class_name": "Generated",
        "sections": unit.sections, "target_sections": unit.target_sections,
        "anim_range": unit.anim_range, "bg": bg, "quality": quality,
        "output_file": unit.output_file, "media_dir": str(media_dir),
        "out_json": str(out_json), "project_root": str(_PROJECT_ROOT),
        "cpu_limit": int(timeout * 2), "_tmp": str(unit_dir),
    }
    unit.worker = worker
    proc = _spawn(job, timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-1500:]
        raise ParallelRenderError(
            f"unit {unit.index} ({unit.kind}"
            f"{'' if unit.section is None else f' section {unit.section}'}"
            f"{'' if unit.anim_range is None else f' anims {unit.anim_range}'}) "
            f"failed (exit {proc.returncode}):\n{tail}")
    mp4s = list(media_dir.glob(f"videos/**/{unit.output_file}.mp4"))
    if not mp4s:
        raise ParallelRenderError(f"unit {unit.index} produced no mp4")
    unit.mp4 = str(mp4s[0])
    unit.frames = _count_frames(mp4s[0])
    if out_json.exists():
        timing = json.loads(out_json.read_text())
        unit.seconds = timing.get("seconds", 0.0)
        unit.cpu_seconds = timing.get("cpu_seconds", 0.0)


def _count_frames(mp4: Path) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(mp4)],
        capture_output=True, text=True)
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


# --- assembly + verify ------------------------------------------------------

def _concat(units: list[_Unit], out_mp4: Path, tmp: Path) -> None:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    listing = tmp / "concat.txt"
    listing.write_text("".join(f"file '{u.mp4}'\n" for u in sorted(units, key=lambda u: u.index)))
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(out_mp4)],
        capture_output=True, text=True)
    if proc.returncode != 0 or not out_mp4.exists():
        raise ParallelRenderError(f"concat failed:\n{(proc.stderr or '').strip()[-1500:]}")


def _verify_final(out_mp4: Path) -> None:
    if not out_mp4.exists():
        raise ParallelRenderError("final video was not produced")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "csv=p=0", str(out_mp4)], capture_output=True, text=True)
    try:
        duration = float(proc.stdout.strip())
    except ValueError:
        duration = 0.0
    if duration <= 0.5:
        raise ParallelRenderError(f"final video is empty or too short (duration={duration}s)")


# --- public entry point -----------------------------------------------------

@dataclass
class RenderResult:
    video_path: str
    strategy: str
    workers: int
    units: list[dict] = field(default_factory=list)
    critical_path_seconds: float = 0.0


def render(code: str, out_mp4: str | Path, *, sections: list[str] | None = None,
           strategy: str = "sequential", workers: int | None = None, quality: str = "l",
           timeout_per_unit: int = 600, logger=None) -> RenderResult:
    """Render `code` to `out_mp4` with the given strategy. `sections`, when given,
    is the scene's SECTIONS list and the whole render goes through the per-section
    harness (both strategies). Records per-unit accounting on `logger` if given."""
    out_mp4 = Path(out_mp4)
    cpu = os.cpu_count() or 2
    if workers is None:
        workers = max(1, cpu - 1)
    bg = sections_mod.parse_background(code)
    q = _QUALITY[quality]

    tmp = Path(tempfile.mkdtemp(prefix="dvg_par_"))
    code_path = tmp / "scene.py"
    code_path.write_text(code)

    # Plan.
    units: list[_Unit] = []
    fallback = False
    if strategy == "parallel":
        plays = _analyze(code_path, sections, bg, q, tmp, timeout_per_unit)
        units = _plan_parallel(sections, plays, workers)
        if not units:
            fallback = True
    if strategy != "parallel" or fallback:
        # One whole-file unit on one worker (sequential, or parallel with nothing
        # splittable). A contract-following scene still runs every section through
        # the harness; an unsectioned scene runs its own construct.
        units = [_Unit(index=0, kind="scene", output_file="unit_0000",
                       sections=sections, target_sections=None)]

    pool = max(1, min(len(units), cpu - 1, workers))

    # Schedule longest-first; assemble in concat order. Each pool thread keeps a
    # stable small worker id for the per-unit accounting.
    order = sorted(units, key=lambda u: u.est_frames, reverse=True)
    errors: list[Exception] = []
    import threading
    worker_ids: dict[int, int] = {}
    lock = threading.Lock()

    def work(u: _Unit) -> None:
        tid = threading.get_ident()
        with lock:
            worker = worker_ids.setdefault(tid, len(worker_ids))
        try:
            _execute_unit(u, code_path, bg, q, tmp, timeout_per_unit, worker)
        except Exception as exc:  # collected and raised after the pool drains
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=pool) as ex:
        list(ex.map(work, order))
    if errors:
        raise errors[0]

    _concat(units, out_mp4, tmp)
    _verify_final(out_mp4)

    records = [{
        "unit": u.index, "kind": u.kind, "section": u.section,
        "anim_range": u.anim_range, "frames": u.frames,
        "seconds": u.seconds, "cpu_seconds": u.cpu_seconds, "worker": u.worker,
    } for u in sorted(units, key=lambda u: u.index)]
    critical = max((u.seconds for u in units), default=0.0)
    effective_strategy = "sequential" if (strategy != "parallel" or fallback) else "parallel"
    if logger is not None:
        logger.record_render_units(records, strategy=effective_strategy, workers=pool,
                                   critical_path_seconds=critical)
    return RenderResult(video_path=str(out_mp4), strategy=effective_strategy,
                        workers=pool, units=records, critical_path_seconds=critical)
