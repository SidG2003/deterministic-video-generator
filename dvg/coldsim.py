"""
Cold-generation simulation recorder.

We test the LLM without an API key by generating code in a context-free subagent
and running it through the real pipeline. But that generation happens OUTSIDE
`generate_freeform`, so `llm_call` would log 0s (and no tokens). This helper
records a cold run whose meta.json reflects a realistic end-to-end picture:
pass the subagent's reported generation time via `llm_seconds` and, optionally,
its reported token usage via `tokens`; the real scan/sandbox/verify are timed
as usual. Use it until real keys are in place.

By default the run is logged with exactly the prompts a real freeform run sends
(`build_freeform_user_message(topic, depth)` + the current freeform system
prompt), so give the subagent those same prompts. Pass `user_prompt` /
`system_prompt` explicitly only if the subagent received something different.

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

from .freeform import (FREEFORM_PROMPT_SHA, FREEFORM_PROMPT_VERSION, FreeformError, _run,
                       _verify, build_freeform_prompt, build_freeform_user_message,
                       extract_narration, freeform_prompt_version, scan_code)
from .runlog import RunLogger, _slug, prompt_version, timed


def coldsim_render(topic: str, code: str, quality: str = "l",
                   llm_seconds: float = 0.0, *, depth: str = "standard",
                   user_prompt: str | None = None, system_prompt: str | None = None,
                   timeout: int = 1200, tokens: dict | None = None) -> tuple[str | None, dict]:
    """Log a cold-sim run faithfully: `topic` is the run's topic; `user_prompt` /
    `system_prompt` are the exact prompts the subagent received (default: the
    ones a real freeform run would send for this topic/depth). Records
    `llm_seconds` as the llm_call step (the subagent's generation time),
    optionally `tokens` as its usage, then runs + times the real
    scan/sandbox/verify. Returns (video_path_or_None, meta)."""
    if topic.lstrip().startswith("Topic:"):
        raise ValueError("pass the bare topic as `topic`; pass a full user message via user_prompt=")
    if user_prompt is None:
        user_prompt = build_freeform_user_message(topic, depth)
    if system_prompt is None:
        system_prompt = build_freeform_prompt()
        version = freeform_prompt_version()
    else:
        version = prompt_version(FREEFORM_PROMPT_VERSION, FREEFORM_PROMPT_SHA, system_prompt)

    logger = RunLogger("freeform", topic,
                       {"quality": quality, "depth": depth, "source": "coldsim",
                        "llm_seconds": llm_seconds})
    logger.record_prompts(system_prompt, user_prompt, version)
    logger.artifact("scene.py", code)
    logger.record_step("llm_call", float(llm_seconds))  # provided (subagent gen time)
    if tokens is not None:
        logger.record_tokens("llm_call", tokens)  # provided (subagent usage)

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
