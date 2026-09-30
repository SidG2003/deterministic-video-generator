"""
Per-run logging.

Every generation run is archived under runs/<timestamp>_<mode>_<slug>/ with all
its artifacts (prompt, system prompt, each attempt's output, the final IR/code,
the video) and a meta.json that records step-by-step timings, CPU usage, and,
for each llm_call, its token usage (input/output/cache tokens). This makes past
runs easy to inspect/compare and shows which steps eat the most time, CPU, and
tokens.

The logged steps differ by mode (that's the point):
  constrained : llm_call, validate, render
  freeform    : llm_call, scan, sandbox_render, verify
Each is timed per attempt; meta.json aggregates per-step totals across attempts
for seconds (step_totals_seconds), CPU-seconds (step_totals_cpu_seconds), and
tokens (token_totals). Per step, `cpu_seconds` (user+system, incl. reaped
subprocesses) and `cores_used` (cpu_seconds/seconds, i.e. cores-equivalent
utilization) show how much compute — and how many cores — a step actually used.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path("runs")

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


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
        self.tokens: list[dict] = []
        self.artifacts: list[str] = []
        self.video: str | None = None

    def record_step(self, name: str, seconds: float, cpu_seconds: float | None = None) -> None:
        """Record a step's wall time and, when available, its CPU time. `cpu_seconds`
        is user+system CPU (including reaped subprocesses) consumed during the step;
        `cores_used` = cpu_seconds/seconds is the cores-equivalent utilization (>1
        means the step used multiple cores). CPU fields are omitted when unknown
        (e.g. cold-sim, where the work happened out of process)."""
        entry: dict = {"name": name, "seconds": round(seconds, 3)}
        if cpu_seconds is not None:
            entry["cpu_seconds"] = round(cpu_seconds, 3)
            if seconds > 0:
                entry["cores_used"] = round(cpu_seconds / seconds, 2)
        self.steps.append(entry)

    def record_tokens(self, name: str, usage, model: str | None = None) -> None:
        """Record token usage for a step (e.g. `llm_call`). `usage` may be an
        Anthropic `Usage` object (from `response.usage`) or a plain dict with
        the same field names; missing/None fields are treated as 0. `model`, if
        given, records which model served this call (kept per-entry since the
        served model can vary across attempts / cold-sim providers)."""
        def _get(key: str) -> int:
            if usage is None:
                return 0
            value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            return value or 0

        entry: dict = {"name": name}
        if model:
            entry["model"] = model
        entry.update({field: _get(field) for field in _TOKEN_FIELDS})
        self.tokens.append(entry)

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
        cpu_totals: dict[str, float] = {}
        for step in self.steps:
            totals[step["name"]] = round(totals.get(step["name"], 0.0) + step["seconds"], 3)
            if "cpu_seconds" in step:
                cpu_totals[step["name"]] = round(
                    cpu_totals.get(step["name"], 0.0) + step["cpu_seconds"], 3)
        attempts = sum(1 for s in self.steps if s["name"] == "llm_call") or None
        total_cpu_seconds = round(sum(cpu_totals.values()), 3)

        token_totals: dict[str, dict[str, int]] = {}
        for entry in self.tokens:
            bucket = token_totals.setdefault(entry["name"], {field: 0 for field in _TOKEN_FIELDS})
            for field in _TOKEN_FIELDS:
                bucket[field] += entry[field]
        total_tokens = {field: sum(b[field] for b in token_totals.values()) for field in _TOKEN_FIELDS}
        total_tokens["total_tokens"] = total_tokens["input_tokens"] + total_tokens["output_tokens"]

        meta = {
            "mode": self.mode,
            "topic": self.topic,
            "params": self.params,
            "timestamp": self.timestamp,
            "success": success,
            "attempts": attempts,
            "total_seconds": round(time.perf_counter() - self._start, 3),
            "step_totals_seconds": totals,
            "cpu_count": os.cpu_count(),
            "total_cpu_seconds": total_cpu_seconds,
            "step_totals_cpu_seconds": cpu_totals,
            "steps": self.steps,
            "total_tokens": total_tokens,
            "token_totals": token_totals,
            "tokens": self.tokens,
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
    """Time a block and record its wall + CPU time on `logger` (no-op if logger
    is None). CPU is user+system across this process AND any subprocesses reaped
    during the block (so Manim/ffmpeg render work counts), via os.times()."""
    start = time.perf_counter()
    cpu_start = os.times()
    try:
        yield
    finally:
        if logger is not None:
            wall = time.perf_counter() - start
            cpu_end = os.times()
            cpu = ((cpu_end.user - cpu_start.user)
                   + (cpu_end.system - cpu_start.system)
                   + (cpu_end.children_user - cpu_start.children_user)
                   + (cpu_end.children_system - cpu_start.children_system))
            logger.record_step(name, wall, cpu_seconds=max(cpu, 0.0))
