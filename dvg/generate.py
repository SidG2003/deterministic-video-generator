"""
Topic -> IR generator.

An LLM (Claude) writes the scene-graph IR; a validate-and-repair loop checks each
candidate against the IR contract (dvg.ir) and, on failure, hands the model the
exact error to fix. The renderer stays fully deterministic — only this authoring
step uses AI, and it can never emit something that renders broken, because
invalid IR is rejected here and repaired before it reaches the renderer.

Usage (needs ANTHROPIC_API_KEY for Claude models, or OPENAI_API_KEY for GPT/o*
models -- either in the environment or in a .env file; see dvg.llm):
    python -m dvg.generate "How does a DNS lookup work?" --style midnight --render
    python -m dvg.generate "How does a DNS lookup work?" --model gpt-5.4-mini
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import llm
from .ir import IRError, validate_ir
from .prompt import build_system_prompt, system_prompt_version
from .runlog import RunLogger, timed

DEFAULT_MODEL = "claude-opus-5"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Depth = breadth of conceptual coverage, NOT a duration target. Length follows
# content. Kept in the per-request user message so the cached system prompt stays stable.
DEPTH_HINTS = {
    "overview": "Depth: a concise overview — the core idea plus its 2-3 most "
                "important parts. Length follows content; keep it tight.",
    "standard": "Depth: cover ALL the major conceptual parts of the topic so the "
                "video is coherent, complete, and genuinely useful. No target "
                "duration — let length follow the content, and keep pacing tight.",
    "deep": "Depth: a thorough, in-depth treatment — every major conceptual part "
            "plus the key nuances and a worked example where it helps. No target "
            "duration; be complete but never pad.",
}


class GenerationError(RuntimeError):
    """Raised when generation fails to produce valid IR within the repair budget."""


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the model's reply, tolerating stray fences/prose."""
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def generate_ir(
    topic: str,
    style: str = "midnight",
    model: str = DEFAULT_MODEL,
    max_repairs: int = 3,
    depth: str = "standard",
    client=None,
    logger: RunLogger | None = None,
) -> dict:
    """Generate and validate an IR document for `topic`. Retries with the
    validator's error message until valid or the repair budget is exhausted."""
    if not topic or not topic.strip():
        raise GenerationError("topic must be a non-empty string")
    if client is None:
        client = llm.make_client(model)

    system = build_system_prompt()
    ask = (
        f"Topic: {topic.strip()}\n"
        f"Style: {style}\n"
        f"{DEPTH_HINTS.get(depth, DEPTH_HINTS['standard'])}\n"
        "Produce the IR JSON for an explainer video on this topic."
    )
    if logger:
        logger.record_prompts(system, ask, system_prompt_version())
    messages = [{"role": "user", "content": ask}]

    last_error = ""
    for attempt in range(max_repairs + 1):
        with timed(logger, "llm_call", attempt=attempt + 1):
            reply, tokens, used_model = llm.complete(client, model, system, messages)
        if logger:
            logger.record_tokens("llm_call", tokens, model=used_model, attempt=attempt + 1)
        if logger:
            logger.artifact(f"attempt_{attempt + 1}.txt", reply)
        try:
            with timed(logger, "validate", attempt=attempt + 1):
                data = _extract_json(reply)
                validate_ir(data)  # the hard contract
            if logger:
                logger.artifact("ir.json", json.dumps(data, indent=2) + "\n")
            return data
        except (json.JSONDecodeError, IRError) as exc:
            last_error = str(exc)
            # Feed the error back and ask for a corrected full document.
            feedback = (
                f"That IR was invalid: {last_error}\n"
                "Return the corrected, complete JSON object only."
            )
            if logger:
                logger.artifact(f"attempt_{attempt + 1}_feedback.txt", feedback)
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": feedback})

    raise GenerationError(f"no valid IR after {max_repairs + 1} attempts; last error: {last_error}")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "video"


def _run_freeform(args) -> None:
    from .freeform import generate_freeform

    print(f"Generating freeform Manim scene for: {args.topic!r} … (sandboxed, may take a bit)")
    logger = RunLogger("freeform", args.topic,
                       {"depth": args.depth, "model": args.model, "quality": args.quality,
                        **_topic_param(args)})
    try:
        code, video = generate_freeform(args.topic, model=args.model, quality=args.quality,
                                        depth=args.depth, logger=logger)
    except Exception as exc:
        logger.finish(False, error=str(exc))
        raise SystemExit(f"Freeform generation failed: {exc}")

    out = Path(args.out) if args.out else Path("examples/generated") / f"{_slug(args.topic)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code)
    logger.save_video(video)
    from .narrate import apply_if_requested
    apply_if_requested(args, logger)
    logger.finish(True)
    print(f"Wrote scene code: {out}")
    print(f"Rendered: {video}")
    print(f"Run logged at {logger.dir}")


def _topic_param(args) -> dict:
    """{"topic_id": ...} when the topic came from eval/topics.toml, else nothing."""
    return {"topic_id": args.topic_id} if getattr(args, "topic_id", None) else {}


def _resolve_topic(parser, args) -> None:
    """Fill args.topic/args.depth from --topic-id; exactly one topic source allowed."""
    if args.topic_id and args.topic:
        parser.error("give either a topic or --topic-id, not both")
    if args.topic_id:
        from .topics import get_topic

        try:
            t = get_topic(args.topic_id)
        except KeyError as exc:
            parser.error(exc.args[0])
        args.topic_id, args.topic = t.id, t.prompt
        if args.depth is None:
            args.depth = t.depth
    elif not args.topic:
        parser.error("a topic is required (or --topic-id; list them with: python -m dvg.topics)")
    if args.depth is None:
        args.depth = "standard"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an explainer-video from a topic.")
    parser.add_argument("topic", nargs="?", help="the concept/story to explain")
    parser.add_argument("--topic-id",
                        help="use a fixed sample prompt from eval/topics.toml by number, id or name "
                             "(e.g. 5, t05, rocket-orbit); list them with: python -m dvg.topics")
    parser.add_argument("--mode",
                        choices=["constrained", "freeform", "freeform-sectioned", "freeform-fanout"],
                        default="constrained",
                        help="constrained = safe IR vocabulary (default); "
                             "freeform = LLM writes full Manim code, sandboxed; "
                             "freeform-sectioned = one call following the section contract, "
                             "rendered section-parallel; "
                             "freeform-fanout = planner + per-scene calls, rendered in parallel")
    parser.add_argument("--style", default="midnight", choices=["midnight", "paper"])
    parser.add_argument("--depth", default=None, choices=["overview", "standard", "deep"],
                        help="breadth of conceptual coverage (length follows content, not a target); "
                             "default: standard, or the topic's depth with --topic-id")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="model id: 'claude-*' (Anthropic), 'anthropic.*' (Bedrock) or 'gpt-*'/'o*' (OpenAI)")
    parser.add_argument("--out", help="path to write the IR JSON (default: examples/generated/<slug>.json)")
    parser.add_argument("--render", action="store_true", help="render the video after generating (constrained mode)")
    parser.add_argument("--quality", choices=["l", "m", "h", "k"], default="l")
    # New-mode rendering + fan-out flags (ignored by constrained/freeform).
    parser.add_argument("--render-strategy", choices=["sequential", "parallel"], default="sequential",
                        help="freeform-sectioned/-fanout: render units one at a time or in parallel "
                             "(default: sequential)")
    parser.add_argument("--workers", type=int, default=None,
                        help="freeform-sectioned/-fanout: parallel render pool size "
                             "(default: cpu_count - 1)")
    parser.add_argument("--planner-model", default=None,
                        help="freeform-fanout: model for the planner call (default: a strong model)")
    parser.add_argument("--scene-model", default=None,
                        help="freeform-fanout: model for each scene call (default: --model)")
    parser.add_argument("--escalate-model", default=None,
                        help="freeform-fanout: model to retry a scene that exhausts its repairs")
    parser.add_argument("--max-concurrency", type=int, default=4,
                        help="freeform-fanout: max concurrent scene LLM calls (default: 4)")
    parser.add_argument("--narrate", action="store_true",
                        help="after rendering, synthesize the NARRATION as a voice track and "
                             "mux it over the video (writes video_narrated.mp4; macOS `say`)")
    parser.add_argument("--tts", choices=["say", "firefly"], default="say",
                        help="--narrate TTS backend: 'say' (local macOS) or 'firefly' "
                             "(Adobe Firefly 3p ElevenLabs; needs FIREFLY_* in .env)")
    parser.add_argument("--voice", default=None,
                        help="voice for --narrate: macOS `say -v` name, or a Firefly voiceId")
    args = parser.parse_args()
    _resolve_topic(parser, args)

    if args.mode == "freeform":
        _run_freeform(args)
        return
    if args.mode == "freeform-sectioned":
        from .modes.sectioned import run as run_sectioned
        run_sectioned(args)
        return
    if args.mode == "freeform-fanout":
        from .modes.fanout import run as run_fanout
        run_fanout(args)
        return

    print(f"Generating IR for: {args.topic!r} …")
    logger = RunLogger("constrained", args.topic,
                       {"style": args.style, "depth": args.depth, "model": args.model,
                        "quality": args.quality, "render": args.render, **_topic_param(args)})
    try:
        data = generate_ir(args.topic, style=args.style, model=args.model,
                           depth=args.depth, logger=logger)
    except Exception as exc:  # surface a clean message; the SDK raises many types
        logger.finish(False, error=str(exc))
        raise SystemExit(f"Generation failed: {exc}")

    out = Path(args.out) if args.out else Path("examples/generated") / f"{_slug(args.topic)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {out}  ({len(data.get('beats', []))} beats)")

    if args.render:
        from .build import render_with_report

        print("Rendering …")
        with timed(logger, "render"):
            video, report = render_with_report(str(out), args.quality)
        logger.save_video(video)
        logger.record_overlaps(report)
        print(f"Rendered: {video}" if video else "Render finished but output not found.")

    logger.finish(True)
    print(f"Run logged at {logger.dir}")


if __name__ == "__main__":
    main()
