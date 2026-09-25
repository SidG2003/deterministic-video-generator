"""
Style presets — the novelty-in-*look* lever.

A style controls palette, text color, accent, background, per-role text scales,
and motion defaults. The same IR skinned with a different style looks like a
different video. Later, styles can be *generated* (sampled from a parameter
space, seeded) so every render feels bespoke without new templates.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Style:
    name: str
    bg: str
    text_color: str
    accent: str
    palette: list[str]
    node_fill: str
    scales: dict[str, float] = field(
        default_factory=lambda: {
            "title": 0.95,
            "subtitle": 0.45,
            "body": 0.5,
            "label": 0.4,
        }
    )
    run_time: float = 0.7
    node_radius: float = 0.6

    def color(self, spec) -> str:
        """Resolve a color spec: None->accent, int->palette index, str->as-is."""
        if spec is None:
            return self.accent
        if isinstance(spec, int):
            return self.palette[spec % len(self.palette)]
        return spec


PRESETS: dict[str, Style] = {
    "midnight": Style(
        name="midnight",
        bg="#0b0f1a",
        text_color="#e8ecf1",
        accent="#f5c542",
        palette=["#4a86ff", "#42d392", "#f5c542", "#ff5c7a"],
        node_fill="#141a2b",
    ),
    "paper": Style(
        name="paper",
        bg="#faf7f0",
        text_color="#1a1a1a",
        accent="#c0392b",
        palette=["#2c6fbb", "#27834f", "#c0392b", "#8e44ad"],
        node_fill="#ffffff",
    ),
}


def get_style(name: str, seed: int = 0) -> Style:
    """Return a style preset. `seed` reserved for future generative styling."""
    random.seed(seed)
    if name not in PRESETS:
        raise ValueError(f"unknown style '{name}'; have {sorted(PRESETS)}")
    return PRESETS[name]
