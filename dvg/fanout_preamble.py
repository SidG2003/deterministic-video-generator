"""
Shared preamble for fan-out scenes (approach B).

The planner fixes the film's look once (palette + font sizes); this module turns
that `style` block into a small Python prelude that is prepended to every scene
file. Because the imports, palette constants and font sizes come from one place,
independently generated scenes can't drift on style — they reference PAL_* /
FONT_* instead of inventing their own colours and sizes.

The prelude is plain data definitions only, so it passes `scan_code` and
`api_check` by itself. It stays a module (not a format string) so shared helper
functions can be added here later without touching the scene prompt.
"""

from __future__ import annotations

_PALETTE_KEYS = ("bg", "primary", "accent", "good", "warn", "muted")
_FONT_KEYS = ("title", "body", "label")

DEFAULT_PALETTE = {"bg": "#0b0f1a", "primary": "#7aa2ff", "accent": "#ffd27a",
                   "good": "#6fe3c2", "warn": "#ff6b5e", "muted": "#9fb0d8"}
DEFAULT_FONT_SIZES = {"title": 44, "body": 30, "label": 24}


def build_preamble(style: dict | None) -> str:
    """The Python prelude for `style` (a plan's `style` block). Missing keys fall
    back to the defaults, so a thin or partial style still yields a valid prelude."""
    style = style or {}
    palette = {**DEFAULT_PALETTE, **(style.get("palette") or {})}
    fonts = {**DEFAULT_FONT_SIZES, **(style.get("font_sizes") or {})}
    lines = ["from manim import *", "import numpy as np", ""]
    for key in _PALETTE_KEYS:
        lines.append(f'PAL_{key.upper()} = "{palette[key]}"')
    lines.append("")
    for key in _FONT_KEYS:
        lines.append(f"FONT_{key.upper()} = {int(fonts[key])}")
    return "\n".join(lines) + "\n"


def palette_names() -> list[str]:
    return [f"PAL_{k.upper()}" for k in _PALETTE_KEYS]


def font_names() -> list[str]:
    return [f"FONT_{k.upper()}" for k in _FONT_KEYS]
