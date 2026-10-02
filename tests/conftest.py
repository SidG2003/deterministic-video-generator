"""Shared pytest setup: make `dvg` importable and provide common render helpers.

The frame-equality tests compare videos with `ffmpeg -f framemd5`, which emits a
per-frame MD5. Two renders are frame-identical iff their framemd5 hash columns
match exactly (pixel-average checks are not enough — a broken TracedPath scored
0.86/255, see docs/render-parallelization.md)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def frame_hashes(mp4: str | Path) -> list[str]:
    """The per-frame MD5 hash column of a video (empty list if it can't be read)."""
    md5 = Path(tempfile.mktemp(suffix=".framemd5"))
    subprocess.run(["ffmpeg", "-y", "-i", str(mp4), "-f", "framemd5", str(md5)],
                   capture_output=True)
    if not md5.exists():
        return []
    return [line.rsplit(",", 1)[-1].strip()
            for line in md5.read_text().splitlines()
            if line and not line.startswith("#")]


def pytest_addoption(parser):
    parser.addoption("--run-slow", action="store_true", default=False,
                     help="run tests marked @pytest.mark.slow (minute-scale renders)")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: minute-scale render benchmark, skipped by default")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip = pytest.mark.skip(reason="needs --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
