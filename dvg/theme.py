"""
Shared visual theme + length rules for every generation path.

The goal is to make the output NOT look like a default Manim video: a light
background (not the traditional dark/navy/black), a cohesive MUTED palette (no
neon), a real typeface instead of Manim's default, and text that appears by
fading rather than the tell-tale "written" stroke. These rules are injected into
the freeform prompt (and therefore into the sectioned and fan-out scene prompts,
which build on it) and summarised for the fan-out planner.

We fix the RULES, not the exact colours — each video picks specifics that suit it,
within the rules. `dvg.fanout_preamble` turns the planner's chosen values into
constants every fan-out scene must use; the single-call paths follow the same
rules directly in their own code.
"""

from __future__ import annotations

# Real, installed typefaces that read as "designed", not default-Manim. Verified
# present via manimpango on the target machine; `font_or_default` falls back if a
# requested one is missing so a render never silently uses the Manim default.
FONT_SHORTLIST = [
    "Avenir Next", "Helvetica Neue", "Optima", "Gill Sans", "Futura",
    "Georgia", "Palatino", "Baskerville",
]
DEFAULT_FONT = "Avenir Next"

# A light, non-neon starting point (used as the fan-out default / fallbacks).
DEFAULT_PALETTE = {
    "bg": "#f5f3ee",      # warm off-white
    "ink": "#1e232b",     # charcoal (primary text on a light bg)
    "primary": "#3a6ea5",  # muted slate blue
    "accent": "#c8862b",   # ochre
    "good": "#4f9d69",     # muted green
    "warn": "#c25b4e",     # terracotta
    "muted": "#6b7280",    # warm grey (secondary text / axes)
}
DEFAULT_FONT_SIZES = {"title": 44, "body": 30, "label": 24}

TARGET_SECONDS = (50, 60)


def _luminance(hex_color: str) -> float:
    """Relative luminance 0..1 of a #rrggbb colour (sRGB, perceptual weights)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_light(hex_color: str) -> bool:
    return _luminance(hex_color) >= 0.6


def is_dark(hex_color: str) -> bool:
    return _luminance(hex_color) <= 0.35


def font_or_default(name: str | None) -> str:
    """A requested font if it is actually installed, else the default — so a plan
    that names a missing font still renders in a real typeface, not Manim's."""
    try:
        import manimpango
        available = set(manimpango.list_fonts())
    except Exception:
        available = set()
    if name and (not available or name in available):
        return name
    return DEFAULT_FONT


# --- prompt blocks (injected into the freeform/sectioned/scene prompts) ------

STYLE_RULES = """\
THEME & STYLE — do NOT make it look like a default Manim video (as important as beauty)
- LIGHT BACKGROUND: set a LIGHT background that suits the topic — white, off-white,
  or a soft light tint (e.g. "#f5f3ee", "#f4f6f8", "#fbfaf7"). NEVER the Manim-default
  dark/navy/black (no "#0b0f1a", no near-black). Set it once:
  self.camera.background_color = "#f5f3ee".
- READABLE INK ON LIGHT: text is a dark, near-black ink (e.g. "#1e232b"), not pure
  black and never light/white (it would vanish on the light background).
- MUTED, NON-NEON PALETTE: choose a small, cohesive palette of DESATURATED, editorial
  colours — muted slate blues, ochres, muted greens, terracotta, warm greys. AVOID the
  neon/electric Manim look (no "#7aa2ff", no bright cyan/magenta/lime). A couple of
  accent colours at most; let the light background and negative space carry the design.
- REAL TYPEFACE: give EVERY Text(...) an explicit font from this list:
  Avenir Next, Helvetica Neue, Optima, Gill Sans, Futura, Georgia, Palatino, Baskerville.
  Pick ONE family for the whole video, e.g. Text("...", font="Avenir Next"). Do NOT rely
  on Manim's default font. (MathTex/Tex still render as LaTeX — that is fine.)
- TEXT APPEARS BY FADING, NOT WRITING: reveal text with FadeIn(...), FadeIn(..., shift=...)
  or Transform/FadeTransform — NEVER Write(...), AddTextLetterByLetter(...) or a typewriter
  effect (the drawn-stroke look is a dead giveaway it is Manim). Create(...) is still fine
  for shapes, lines and diagrams.
- NO UNINTENDED TEXT OVERLAP: separate text objects must never overlap each other or the
  frame edge — this is the most common defect, so keep clusters apart (arrange/next_to with
  buffers) and clear old text before new text enters the same area."""

LENGTH_RULE = """\
LENGTH & PACING — the whole film should run about 50-60 seconds
- Aim for ~50-60s total, and prefer the upper end: slightly long is better than rushed.
- Let each idea LAND. After a beat finishes, hold it long enough to actually read and
  absorb (about 1.5-2s), and use calm, unhurried run_times. Do not race through scenes
  or flash text — the viewer needs time on each one.
- Reach the target by covering the idea across enough sections (usually about 7-10) with
  these relaxed holds — not by padding with dead time, and not by cramming."""
