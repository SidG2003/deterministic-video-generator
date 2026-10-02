"""Frame-equality and determinism tests for the parallel render engine.

The guarantee under test: however a contract-following scene is split — one
process, one process per section, or per-animation slices of each section — the
final video is frame-for-frame identical, because each section is reseeded and
started on a fresh stage, and sliced ranges fast-forward their skipped prefix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FIXTURES, frame_hashes

from dvg import parallel_render as pr
from dvg import sections


def _render(code, out, **kw):
    return pr.render(code, out, **kw)


@pytest.fixture
def sectioned_code():
    return (FIXTURES / "sectioned_scene.py").read_text()


def test_sequential_matches_parallel_sections(sectioned_code, tmp_path):
    secs = sections.parse_sections(sectioned_code)
    seq = tmp_path / "seq.mp4"
    par = tmp_path / "par.mp4"
    r_seq = _render(sectioned_code, seq, sections=secs, strategy="sequential")
    r_par = _render(sectioned_code, par, sections=secs, strategy="parallel", workers=4)

    assert [u["kind"] for u in r_seq.units] == ["scene"]
    assert {u["kind"] for u in r_par.units} == {"section"}
    assert len(r_par.units) == len(secs)  # one unit per section
    assert frame_hashes(seq) == frame_hashes(par) != []


def test_time_sliced_matches_sequential(sectioned_code, tmp_path, monkeypatch):
    # Force every multi-play section to slice, so the fast-forward path is exercised.
    monkeypatch.setattr(pr, "_SPLIT_MIN_FRAMES", 1)
    monkeypatch.setattr(pr, "_SPLIT_FRACTION", 0.0)
    secs = sections.parse_sections(sectioned_code)
    seq = tmp_path / "seq.mp4"
    sliced = tmp_path / "sliced.mp4"
    _render(sectioned_code, seq, sections=secs, strategy="sequential")
    r = _render(sectioned_code, sliced, sections=secs, strategy="parallel", workers=4)

    assert any(u["kind"] == "slice" for u in r.units)
    assert frame_hashes(seq) == frame_hashes(sliced) != []


def test_isolated_media_dirs_two_mathtex_sections(sectioned_code, tmp_path):
    # 'equation' and 'orbit' both build MathTex; rendered in parallel they must not
    # race on a shared LaTeX cache or clobber each other's uncached clips.
    secs = sections.parse_sections(sectioned_code)
    out = tmp_path / "par.mp4"
    r = _render(sectioned_code, out, sections=secs, strategy="parallel", workers=4)
    assert Path(r.video_path).exists()
    seq = tmp_path / "seq.mp4"
    _render(sectioned_code, seq, sections=secs, strategy="sequential")
    assert frame_hashes(out) == frame_hashes(seq) != []


def test_unit_renders_deterministically(sectioned_code, tmp_path):
    secs = sections.parse_sections(sectioned_code)
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _render(sectioned_code, a, sections=secs, strategy="parallel", workers=4)
    _render(sectioned_code, b, sections=secs, strategy="parallel", workers=4)
    assert frame_hashes(a) == frame_hashes(b) != []


def test_render_units_recorded_on_logger(sectioned_code, tmp_path):
    from dvg.runlog import RunLogger

    secs = sections.parse_sections(sectioned_code)
    logger = RunLogger("freeform-sectioned", "t", base=tmp_path / "runs")
    pr.render(sectioned_code, tmp_path / "v.mp4", sections=secs,
              strategy="parallel", workers=4, logger=logger)
    meta = logger.finish(True)
    assert meta["render_strategy"] == "parallel"
    assert meta["workers"] >= 1
    assert meta["render_units"] and all("frames" in u for u in meta["render_units"])
    assert meta["render_critical_path_seconds"] >= 0


@pytest.mark.slow
def test_entropy_timeslice_whole_scene(tmp_path, capsys):
    # The any-scene benchmark: time-slice the whole (unsectioned) entropy film and
    # confirm it still produces a structurally equivalent video (same frame count,
    # real slices, valid output), and report both wall times.
    #
    # Two caveats this benchmark surfaced, both inherent to a HEAVY 3D scene rather
    # than to the engine:
    #  - It is not bit-reproducible here: two plain sequential renders already
    #    differ (float-accumulation / threading in Manim's 3D path), so exact
    #    frame-hash equality is asserted on the deterministic 2D sectioned fixture
    #    above, not here.
    #  - Whole-scene slicing is NOT faster for this scene: a non-sectioned slice
    #    must fast-forward its skipped prefix for correctness, and entropy's cost
    #    is per-frame 3D surface recomputation (which fast-forward still pays), so
    #    later slices replay most of the scene. The wall-time win is SECTION
    #    parallelism (independent sections, no prefix replay) — see the sectioned
    #    tests — not whole-scene slicing of a heavy scene.
    import time

    code = (FIXTURES / "entropy_scene.py").read_text()
    seq = tmp_path / "seq.mp4"
    par = tmp_path / "par.mp4"

    t0 = time.perf_counter()
    pr.render(code, seq, sections=None, strategy="sequential")
    seq_wall = time.perf_counter() - t0

    t0 = time.perf_counter()
    r = pr.render(code, par, sections=None, strategy="parallel", workers=4)
    par_wall = time.perf_counter() - t0

    with capsys.disabled():
        print(f"\nentropy whole-scene: sequential {seq_wall:.1f}s, "
              f"sliced x{len(r.units)} {par_wall:.1f}s")
    assert any(u["kind"] == "slice" for u in r.units)
    assert len(frame_hashes(seq)) == len(frame_hashes(par)) > 0  # same frame count
