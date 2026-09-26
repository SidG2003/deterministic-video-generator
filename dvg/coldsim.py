"""
Cold-generation simulation recorder.

We test the LLM without an API key by generating code in a context-free subagent
and running it through the real pipeline. But that generation happens OUTSIDE
`generate_freeform`, so `llm_call` would log 0s. This helper records a cold run
whose meta.json reflects a realistic end-to-end picture: pass the subagent's
reported generation time via `llm_seconds`, and the real scan/sandbox/verify are
timed as usual. Use it until real keys are in place.

Usage:
    from dvg.coldsim import coldsim_render
    code = open("scene.py").read()
    video, meta = coldsim_render("my topic", code, quality="l", llm_seconds=133.0)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .freeform import FreeformError, _run, _verify, extract_narration, scan_code
from .runlog import RunLogger, _slug, timed


def coldsim_render(topic: str, code: str, quality: str = "l",
                   llm_seconds: float = 0.0, timeout: int = 1200) -> tuple[str | None, dict]:
    """Log a cold-sim run: record `llm_seconds` as the llm_call step (the subagent's
    generation time), then run + time the real scan/sandbox/verify. Returns
    (video_path_or_None, meta)."""
    logger = RunLogger("freeform", topic,
                       {"quality": quality, "source": "coldsim", "llm_seconds": llm_seconds})
    logger.artifact("scene.py", code)
    logger.record_step("llm_call", float(llm_seconds))  # provided (subagent gen time)

    slug = _slug(topic)
    video, error = None, None
    try:
        with timed(logger, "scan"):
            scan_code(code)
        with timed(logger, "sandbox_render"):
            proc, media_dir = _run(code, slug, quality, timeout)
        with timed(logger, "verify"):
            mp4 = _verify(proc, media_dir, slug)
        dest = Path("media/videos/freeform")
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{slug}.mp4"
        shutil.copy(mp4, out)
        video = str(out)
        logger.save_video(video)
        logger.artifact("narration.json",
                        json.dumps(extract_narration(code), indent=2, ensure_ascii=False) + "\n")
    except FreeformError as exc:
        error = str(exc)

    end_to_end = round(sum(s["seconds"] for s in logger.steps), 3)
    meta = logger.finish(error is None, error=error, extra={
        "estimated_end_to_end_seconds": end_to_end,
        "note": ("llm_call = subagent generation time (provided via llm_seconds); "
                 "total_seconds is pipeline wall time only, since the LLM ran "
                 "out-of-band in a subagent. Use estimated_end_to_end_seconds for "
                 "a realistic real-run total."),
    })
    return video, meta
