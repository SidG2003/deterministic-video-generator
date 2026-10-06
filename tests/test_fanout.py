"""Unit tests for approach B plumbing that needs no LLM call: plan validation,
the shared preamble, and that an assembled one-section scene passes every check.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from dvg import sections
from dvg.fanout_preamble import build_preamble
from dvg.freeform import scan_code
from dvg.kb.check import check_code
from dvg.modes import fanout


def _good_plan():
    return {
        "title": "How X Works",
        "style": {
            "palette": {"bg": "#f5f3ee", "ink": "#1e232b", "primary": "#3a6ea5",
                        "accent": "#c8862b", "good": "#4f9d69", "warn": "#c25b4e",
                        "muted": "#6b7280"},
            "font": "Avenir Next",
            "font_sizes": {"title": 44, "body": 30, "label": 24},
            "transition": "fade",
        },
        "scenes": [
            {"id": f"s0{i}", "goal": "g", "visual": "v", "on_screen_text": ["x"],
             "narration": "A short spoken line for this scene.", "scene_type": "Scene",
             "enters_with": "empty stage", "leaves_with": "empty stage",
             "complexity": "light"}
            for i in range(1, 6)  # 5 scenes (valid: 5-8)
        ],
    }


def test_valid_plan_passes():
    assert fanout.validate_plan(_good_plan()) == []


def test_too_few_scenes():
    p = _good_plan()
    p["scenes"] = p["scenes"][:3]  # 3 scenes is now too few (needs 5-8)
    assert any("5-8" in m for m in fanout.validate_plan(p))


def test_duplicate_scene_ids():
    p = _good_plan()
    p["scenes"][1]["id"] = "s01"
    assert any("duplicated" in m for m in fanout.validate_plan(p))


def test_bad_hex_colour():
    p = _good_plan()
    p["style"]["palette"]["bg"] = "navy"
    assert any("hex" in m for m in fanout.validate_plan(p))


def test_dark_background_rejected():
    p = _good_plan()
    p["style"]["palette"]["bg"] = "#0b0f1a"  # the old Manim-dark default
    assert any("LIGHT" in m for m in fanout.validate_plan(p))


def test_missing_font_rejected():
    p = _good_plan()
    del p["style"]["font"]
    assert any("font" in m for m in fanout.validate_plan(p))


def test_too_many_heavy():
    p = _good_plan()
    for s in p["scenes"]:
        s["complexity"] = "heavy"
    assert any("heavy" in m for m in fanout.validate_plan(p))


def test_bad_scene_type():
    p = _good_plan()
    p["scenes"][0]["scene_type"] = "FancyScene"
    assert any("scene_type" in m for m in fanout.validate_plan(p))


def test_missing_field():
    p = _good_plan()
    del p["scenes"][0]["narration"]
    assert any("narration" in m for m in fanout.validate_plan(p))


def test_preamble_resolves_background():
    pre = build_preamble({"palette": {"bg": "#123456"}})
    code = fanout.assemble_scene_file(pre, _MINI_SCENE)
    assert sections.parse_background(code) == "#123456"


def test_assembled_scene_passes_all_checks(tmp_path):
    pre = build_preamble(_good_plan()["style"])
    code = fanout.assemble_scene_file(pre, _MINI_SCENE)
    scan_code(code)
    assert [str(f) for f in check_code(code) if f.severity == "error"] == []
    assert sections.check_static(code) == []
    f = tmp_path / "scene.py"
    f.write_text(code)
    assert sections.check_runtime(f) == []


def test_scene_user_message_puts_shared_prefix_first():
    plan = _good_plan()
    pre = build_preamble(plan["style"])
    msg = fanout.build_scene_user_message(plan, pre, 1)
    # the shared preamble comes before this scene's own spec
    assert msg.index("PREAMBLE already prepended") < msg.index("YOUR SCENE")
    assert "s02" in msg


_MINI_SCENE = '''NARRATION = ["A single spoken line for this scene."]
SECTIONS = ["main"]
class Generated(Scene):
    def construct(self):
        self.camera.background_color = PAL_BG
        for name in SECTIONS:
            getattr(self, name)()
    def main(self):
        title = Text("Hello", font_size=FONT_TITLE, color=PAL_PRIMARY)
        self.play(FadeIn(title))
        self.wait(0.2)
        self.play(*[FadeOut(m) for m in self.mobjects])
'''
