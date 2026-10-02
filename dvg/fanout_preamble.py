"""
Shared preamble for fan-out scenes (approach B).

The planner fixes the film's look once (palette + font + font sizes); this module
turns that into a small Python prelude prepended to every scene file. Because the
imports, palette constants, font family and font sizes come from one place,
independently generated scenes can't drift on style — they reference PAL_* /
FONT_FAMILY / FONT_* instead of inventing their own.

The prelude is plain data definitions only, so it passes `scan_code` and
`api_check` by itself (aside from needing a Generated class, which the scene
supplies). It stays a module (not a format string) so shared helper functions can
be added here later without touching the scene prompt.
"""

from __future__ import annotations

from . import theme

_PALETTE_KEYS = ("bg", "ink", "primary", "accent", "good", "warn", "muted")
_FONT_KEYS = ("title", "body", "label")

DEFAULT_PALETTE = dict(theme.DEFAULT_PALETTE)
DEFAULT_FONT_SIZES = dict(theme.DEFAULT_FONT_SIZES)


def build_preamble(style: dict | None) -> str:
    """The Python prelude for `style` (a plan's `style` block). Missing keys fall
    back to the light-theme defaults; the font is checked against the installed
    fonts so a missing one falls back to a real typeface rather than Manim's."""
    style = style or {}
    palette = {**DEFAULT_PALETTE, **(style.get("palette") or {})}
    fonts = {**DEFAULT_FONT_SIZES, **(style.get("font_sizes") or {})}
    family = theme.font_or_default(style.get("font"))
    lines = ["from manim import *", "import numpy as np", ""]
    for key in _PALETTE_KEYS:
        lines.append(f'PAL_{key.upper()} = "{palette[key]}"')
    lines.append("")
    lines.append(f'FONT_FAMILY = "{family}"')
    for key in _FONT_KEYS:
        lines.append(f"FONT_{key.upper()} = {int(fonts[key])}")
    return "\n".join(lines) + "\n"


def palette_names() -> list[str]:
    return [f"PAL_{k.upper()}" for k in _PALETTE_KEYS]


def font_names() -> list[str]:
    return ["FONT_FAMILY"] + [f"FONT_{k.upper()}" for k in _FONT_KEYS]
