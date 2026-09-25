"""
Build a video from an IR JSON file.

Usage (venv active, SDKROOT exported):
    python -m dvg.build examples/cache_explainer.json --quality l

This is the deterministic renderer: given the same IR + seed, it produces the
same MP4 every time. Generation (writing the IR) can be intelligent/novel; this
stage stays reproducible and cheap.
"""

from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path

from manim import Scene, tempconfig

from .director import Director
from .ir import load_video
from .style import get_style

_QUALITY = {"l": "low_quality", "m": "medium_quality", "h": "high_quality", "k": "fourk_quality"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "video"


def render(ir_path: str, quality: str = "l") -> str:
    video = load_video(ir_path)
    style = get_style(video.style, video.seed)
    name = _slug(video.title)

    class IRScene(Scene):
        def construct(self):
            Director(video, style).run(self)

    with tempconfig(
        {
            "quality": _QUALITY[quality],
            "output_file": name,
            "media_dir": "media",
            "disable_caching": True,
        }
    ):
        IRScene().render()

    matches = sorted(glob.glob(f"media/videos/**/{name}.mp4", recursive=True),
                     key=lambda p: Path(p).stat().st_mtime)
    return matches[-1] if matches else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a video from an IR JSON file.")
    parser.add_argument("ir_path", help="path to the IR JSON")
    parser.add_argument("--quality", choices=list(_QUALITY), default="l",
                        help="l=480p (default), m=720p, h=1080p, k=4K")
    args = parser.parse_args()
    out = render(args.ir_path, args.quality)
    print(f"\nRendered: {out}" if out else "\nRender finished but output not found.")


if __name__ == "__main__":
    main()
