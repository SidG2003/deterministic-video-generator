"""Static and runtime checks for the section contract (dvg.sections).

One bad input per rule confirms the matching message fires; the good fixture
confirms a contract-following scene passes both checks cleanly.
"""

from __future__ import annotations

import textwrap

import pytest

from conftest import FIXTURES

from dvg import sections
from dvg.freeform import FreeformError, scan_code

GOOD = (FIXTURES / "sectioned_scene.py").read_text()


def test_scan_allows_getattr_on_self_only():
    # The contract's construct() dispatches via getattr(self, name)(); the scanner
    # must permit that but still block getattr used as an escape.
    scan_code(GOOD)  # contains `getattr(self, name)()` and must pass
    for bad in ["getattr(np, '__dict__')", "getattr(obj, 'x')"]:
        with pytest.raises(FreeformError):
            scan_code("from manim import *\n" + bad)

# A compact contract-following scene used as the base for the "bad" mutations.
MINI = textwrap.dedent('''
    from manim import *
    NARRATION = ["one", "two"]
    SECTIONS = ["one", "two"]
    class Generated(Scene):
        def construct(self):
            self.camera.background_color = "#0b0f1a"
            for name in SECTIONS:
                getattr(self, name)()
        def one(self):
            t = Text("1")
            self.play(FadeIn(t))
            self.play(FadeOut(t))
        def two(self):
            t = Text("2")
            self.play(FadeIn(t))
            self.play(FadeOut(t))
''')


def _msgs(code):
    return " | ".join(sections.check_static(code))


def test_good_fixture_passes_static():
    assert sections.check_static(GOOD) == []
    assert sections.check_static(MINI) == []


def test_missing_sections_list():
    code = MINI.replace('SECTIONS = ["one", "two"]\n', "")
    assert "SECTIONS" in _msgs(code)


def test_wrong_base_class():
    code = MINI.replace("class Generated(Scene):", "class Generated(VGroup):")
    assert "subclass Scene" in _msgs(code)


def test_construct_has_extra_statement():
    code = MINI.replace("        for name in SECTIONS:",
                        "        extra = 5\n        for name in SECTIONS:")
    assert "construct() may contain only" in _msgs(code)


def test_section_name_without_method():
    code = MINI.replace('SECTIONS = ["one", "two"]', 'SECTIONS = ["one", "three"]')
    assert "no method 'three'" in _msgs(code)


def test_section_assigns_self_state():
    code = MINI.replace('        t = Text("1")',
                        '        self.shared = 5\n        t = Text("1")')
    assert "share state via self" in _msgs(code)


def test_seed_call_rejected():
    code = MINI.replace("from manim import *",
                        "from manim import *\nimport random")
    code = code.replace('        t = Text("1")',
                        '        random.seed(0)\n        t = Text("1")')
    assert "seed" in _msgs(code)


def test_narration_count_mismatch():
    code = MINI.replace('NARRATION = ["one", "two"]', 'NARRATION = ["only one"]')
    assert "NARRATION has" in _msgs(code)


# --- runtime checks ---------------------------------------------------------

def test_runtime_good_fixture_passes(tmp_path):
    f = tmp_path / "good.py"
    f.write_text(GOOD)
    assert sections.check_runtime(f) == []


def test_runtime_leftover_visible_mobjects(tmp_path):
    code = MINI.replace('        t = Text("1")\n        self.play(FadeIn(t))\n        self.play(FadeOut(t))',
                        '        t = Text("1")\n        self.play(FadeIn(t))')  # no FadeOut
    f = tmp_path / "leftover.py"
    f.write_text(code)
    assert sections.check_static(code) == []  # passes static; the defect is runtime
    msgs = sections.check_runtime(f)
    assert any("visible" in m for m in msgs)


def test_runtime_ambient_rotation_left_on(tmp_path):
    code = textwrap.dedent('''
        from manim import *
        NARRATION = ["spin"]
        SECTIONS = ["spin"]
        class Generated(ThreeDScene):
            def construct(self):
                self.camera.background_color = "#0b0f1a"
                for name in SECTIONS:
                    getattr(self, name)()
            def spin(self):
                axes = ThreeDAxes()
                self.add(axes)
                self.begin_ambient_camera_rotation(rate=0.2)
                self.play(Create(axes))
                self.play(FadeOut(axes))
    ''')
    f = tmp_path / "spin.py"
    f.write_text(code)
    assert sections.check_static(code) == []
    msgs = sections.check_runtime(f)
    assert any("ambient" in m for m in msgs)
