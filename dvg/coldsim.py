"""
Cold-generation simulation recorder.

We test the LLM without an API key by generating code in a context-free subagent
and running it through the real pipeline. But that generation happens OUTSIDE
`generate_freeform`, so `llm_call` would log 0s (and no tokens). This helper
records a cold run whose meta.json reflects a realistic end-to-end picture:
pass the subagent's reported generation time via `llm_seconds` and, optionally,
its reported token usage via `tokens`; the real scan/sandbox/verify are timed
as usual. Use it until real keys are in place.

Usage:
    from dvg.coldsim import coldsim_render
    code = open("scene.py").read()
    video, meta = coldsim_render("my topic", code, quality="l", llm_seconds=133.0,
                                 tokens={"input_tokens": 1200, "output_tokens": 3400})
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .freeform import FreeformError, _run, _verify, extract_narration, scan_code
from .runlog import RunLogger, _slug, timed


def coldsim_render(user_prompt: str, code: str, quality: str = "l",
                   llm_seconds: float = 0.0, system_prompt: str = "",
                   timeout: int = 1200, tokens: dict | None = None) -> tuple[str | None, dict]:
    """Log a cold-sim run faithfully: archive the REAL prompts that produced the
    code (`user_prompt` = the exact user message fed to the model, `system_prompt`
    = the system prompt), record `llm_seconds` as the llm_call step (the subagent's
    generation time), optionally record `tokens` (a dict with input_tokens/
    output_tokens/etc., if the subagent reported its usage) as the llm_call's
    token usage, then run + time the real scan/sandbox/verify. `user_prompt`
    is also the run's topic. Returns (video_path_or_None, meta)."""
    logger = RunLogger("freeform", user_prompt,
                       {"quality": quality, "source": "coldsim", "llm_seconds": llm_seconds})
    if system_prompt:
        logger.artifact("system_prompt.txt", system_prompt)
    logger.artifact("prompt.txt", user_prompt)  # the actual user message
    logger.artifact("scene.py", code)
    logger.record_step("llm_call", float(llm_seconds))  # provided (subagent gen time)
    if tokens is not None:
        logger.record_tokens("llm_call", tokens)  # provided (subagent usage)

    slug = _slug(user_prompt)
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
