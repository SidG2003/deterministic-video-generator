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

import hashlib
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


def prompt_sha(text: str) -> str:
    """Short content hash of a prompt (first 12 hex chars of SHA-256)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_version(tag: str, expected_sha: str, text: str) -> str:
    """Return `tag` if `text` is exactly the prompt logged under that version in
    docs/prompt-log.md, else `tag+modified` — so an edit made without bumping the
    version (and logging it) shows up in every run's meta.json."""
    return tag if prompt_sha(text) == expected_sha else f"{tag}+modified"


def _unique_run_dir(base: Path, name: str) -> Path:
    """Create and return base/name, or base/name_2, _3, ... if taken — runs started
    in the same second (concurrent evals, fan-out) must never share a directory.
    mkdir(exist_ok=False) makes the claim atomic across processes."""
    base.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        path = base / (name if n == 1 else f"{name}_{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create a unique run dir for {name}")


class RunLogger:
    def __init__(self, mode: str, topic: str, params: dict | None = None, base: Path = RUNS_DIR):
        self.mode = mode
        self.topic = topic
        self.params = params or {}
        now = datetime.now()
        self.timestamp = now.isoformat(timespec="seconds")
        self.dir = _unique_run_dir(Path(base), f"{now:%Y%m%d_%H%M%S}_{mode}_{_slug(topic)}")
        self._start = time.perf_counter()
        self.steps: list[dict] = []
        self.tokens: list[dict] = []
        self.artifacts: list[str] = []
        self.video: str | None = None
        self.user_prompt: str | None = None
        self.system_prompt_version: str | None = None
        self.system_prompt_sha256: str | None = None
        self.overlaps: dict | None = None

    def record_overlaps(self, report: dict | None, attempt: int | None = None) -> None:
        """Keep the overlap detector's report (dvg/overlap.py) for the delivered
        video; written to meta.json as `overlaps`."""
        if report is None:
            return
        self.overlaps = {**({"attempt": attempt} if attempt is not None else {}), **report}

    def record_prompts(self, system_prompt: str | None, user_prompt: str,
                       system_prompt_version: str | None = None) -> None:
        """Archive the exact prompts sent to the model (system_prompt.txt, prompt.txt)
        and keep the user prompt, the system prompt's version tag (as in
        docs/prompt-log.md) and its content hash for meta.json."""
        if system_prompt is not None:
            self.artifact("system_prompt.txt", system_prompt)
            self.system_prompt_sha256 = prompt_sha(system_prompt)
        self.artifact("prompt.txt", user_prompt)
        self.user_prompt = user_prompt
        self.system_prompt_version = system_prompt_version

    def record_step(self, name: str, seconds: float, cpu_seconds: float | None = None,
                    attempt: int | None = None) -> None:
        """Record a step's wall time and, when available, its CPU time. `cpu_seconds`
        is user+system CPU (including reaped subprocesses) consumed during the step;
        `cores_used` = cpu_seconds/seconds is the cores-equivalent utilization (>1
        means the step used multiple cores). CPU fields are omitted when unknown
        (e.g. cold-sim, where the work happened out of process). `attempt` (1-based),
        when given, records which repair-loop attempt this step belongs to."""
        entry: dict = {"name": name}
        if attempt is not None:
            entry["attempt"] = attempt
        entry["seconds"] = round(seconds, 3)
        if cpu_seconds is not None:
            entry["cpu_seconds"] = round(cpu_seconds, 3)
            if seconds > 0:
                entry["cores_used"] = round(cpu_seconds / seconds, 2)
        self.steps.append(entry)

    def record_tokens(self, name: str, usage, model: str | None = None,
                      attempt: int | None = None) -> None:
        """Record token usage for a step (e.g. `llm_call`). `usage` may be an
        Anthropic `Usage` object (from `response.usage`) or a plain dict with
        the same field names; missing/None fields are treated as 0. `model`, if
        given, records which model served this call (kept per-entry since the
        served model can vary across attempts / cold-sim providers). `attempt`
        (1-based), when given, records which repair-loop attempt produced this call."""
        def _get(key: str) -> int:
            if usage is None:
                return 0
            value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            return value or 0

        entry: dict = {"name": name}
        if attempt is not None:
            entry["attempt"] = attempt
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
            "system_prompt_version": self.system_prompt_version,
            "system_prompt_sha256": self.system_prompt_sha256,
            "user_prompt": self.user_prompt,
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
            "overlaps": self.overlaps,
            "artifacts": self.artifacts,
            "video": self.video,
            "error": error,
        }
        if extra:
            meta.update(extra)
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        return meta


@contextmanager
def timed(logger: RunLogger | None, name: str, attempt: int | None = None):
    """Time a block and record its wall + CPU time on `logger` (no-op if logger
    is None). CPU is user+system across this process AND any subprocesses reaped
    during the block (so Manim/ffmpeg render work counts), via os.times().
    `attempt` (1-based), when given, tags the step with its repair-loop attempt."""
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
            logger.record_step(name, wall, cpu_seconds=max(cpu, 0.0), attempt=attempt)
