"""
Per-run logging.

Every generation run is archived under runs/<timestamp>_<mode>_<slug>/ with all
its artifacts (prompt, system prompt, each attempt's output, the final IR/code,
the video) and a meta.json that records step-by-step timings. This makes past
runs easy to inspect/compare and shows which steps eat the most time.

The logged steps differ by mode (that's the point):
  constrained : llm_call, validate, render
  freeform    : llm_call, scan, sandbox_render, verify
Each is timed per attempt; meta.json aggregates per-step totals across attempts.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path("runs")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:50] or "run"


class RunLogger:
    def __init__(self, mode: str, topic: str, params: dict | None = None, base: Path = RUNS_DIR):
        self.mode = mode
        self.topic = topic
        self.params = params or {}
        now = datetime.now()
        self.timestamp = now.isoformat(timespec="seconds")
        self.dir = Path(base) / f"{now:%Y%m%d_%H%M%S}_{mode}_{_slug(topic)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._start = time.perf_counter()
        self.steps: list[dict] = []
        self.artifacts: list[str] = []
        self.video: str | None = None

    def record_step(self, name: str, seconds: float) -> None:
        self.steps.append({"name": name, "seconds": round(seconds, 3)})

    def artifact(self, name: str, content: str) -> Path:
        path = self.dir / name
        path.write_text(content)
        if name not in self.artifacts:
            self.artifacts.append(name)
        return path

    def save_video(self, src: str | None) -> Path | None:
        if src and Path(src).exists():
            dest = self.dir / "video.mp4"
            shutil.copy(src, dest)
            self.video = "video.mp4"
            if "video.mp4" not in self.artifacts:
                self.artifacts.append("video.mp4")
            return dest
        return None

    def finish(self, success: bool, error: str | None = None, extra: dict | None = None) -> dict:
        totals: dict[str, float] = {}
        for step in self.steps:
            totals[step["name"]] = round(totals.get(step["name"], 0.0) + step["seconds"], 3)
        attempts = sum(1 for s in self.steps if s["name"] == "llm_call") or None
        meta = {
            "mode": self.mode,
            "topic": self.topic,
            "params": self.params,
            "timestamp": self.timestamp,
            "success": success,
            "attempts": attempts,
            "total_seconds": round(time.perf_counter() - self._start, 3),
            "step_totals_seconds": totals,
            "steps": self.steps,
            "artifacts": self.artifacts,
            "video": self.video,
            "error": error,
        }
        if extra:
            meta.update(extra)
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        return meta


@contextmanager
def timed(logger: RunLogger | None, name: str):
    """Time a block and record it on `logger` (no-op if logger is None)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        if logger is not None:
            logger.record_step(name, time.perf_counter() - start)
