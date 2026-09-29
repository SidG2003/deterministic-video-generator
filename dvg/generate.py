"""
Topic -> IR generator.

An LLM (Claude) writes the scene-graph IR; a validate-and-repair loop checks each
candidate against the IR contract (dvg.ir) and, on failure, hands the model the
exact error to fix. The renderer stays fully deterministic — only this authoring
step uses AI, and it can never emit something that renders broken, because
invalid IR is rejected here and repaired before it reaches the renderer.

Usage (needs ANTHROPIC_API_KEY or an `ant auth login` profile):
    python -m dvg.generate "How does a DNS lookup work?" --style midnight --render
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .ir import IRError, validate_ir
from .prompt import build_system_prompt
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


def _text_of(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


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
        import anthropic  # imported lazily so rendering doesn't require the SDK

        client = anthropic.Anthropic()

    system = build_system_prompt()
    ask = (
        f"Topic: {topic.strip()}\n"
        f"Style: {style}\n"
        f"{DEPTH_HINTS.get(depth, DEPTH_HINTS['standard'])}\n"
        "Produce the IR JSON for an explainer video on this topic."
    )
    if logger:
        logger.artifact("system_prompt.txt", system)
        logger.artifact("prompt.txt", ask)
    messages = [{"role": "user", "content": ask}]

    last_error = ""
    for attempt in range(max_repairs + 1):
        with timed(logger, "llm_call"):
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
            )
        if logger:
            logger.record_tokens("llm_call", response.usage)
        reply = _text_of(response)
        if logger:
            logger.artifact(f"attempt_{attempt + 1}.txt", reply)
        try:
            with timed(logger, "validate"):
                data = _extract_json(reply)
                validate_ir(data)  # the hard contract
            if logger:
                logger.artifact("ir.json", json.dumps(data, indent=2) + "\n")
            return data
        except (json.JSONDecodeError, IRError) as exc:
            last_error = str(exc)
            # Feed the error back and ask for a corrected full document.
            messages.append({"role": "assistant", "content": reply})
            messages.append({
                "role": "user",
                "content": (
                    f"That IR was invalid: {last_error}\n"
                    "Return the corrected, complete JSON object only."
                ),
            })

    raise GenerationError(f"no valid IR after {max_repairs + 1} attempts; last error: {last_error}")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "video"


def _run_freeform(args) -> None:
    from .freeform import generate_freeform

    print(f"Generating freeform Manim scene for: {args.topic!r} … (sandboxed, may take a bit)")
    logger = RunLogger("freeform", args.topic,
                       {"depth": args.depth, "model": args.model, "quality": args.quality})
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
    logger.finish(True)
    print(f"Wrote scene code: {out}")
    print(f"Rendered: {video}")
    print(f"Run logged at {logger.dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an explainer-video from a topic.")
    parser.add_argument("topic", help="the concept/story to explain")
    parser.add_argument("--mode", choices=["constrained", "freeform"], default="constrained",
                        help="constrained = safe IR vocabulary (default); "
                             "freeform = LLM writes full Manim code, sandboxed")
    parser.add_argument("--style", default="midnight", choices=["midnight", "paper"])
    parser.add_argument("--depth", default="standard", choices=["overview", "standard", "deep"],
                        help="breadth of conceptual coverage (length follows content, not a target)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id")
    parser.add_argument("--out", help="path to write the IR JSON (default: examples/generated/<slug>.json)")
    parser.add_argument("--render", action="store_true", help="render the video after generating (constrained mode)")
    parser.add_argument("--quality", choices=["l", "m", "h", "k"], default="l")
    args = parser.parse_args()

    if args.mode == "freeform":
        _run_freeform(args)
        return

    print(f"Generating IR for: {args.topic!r} …")
    logger = RunLogger("constrained", args.topic,
                       {"style": args.style, "depth": args.depth, "model": args.model,
                        "quality": args.quality, "render": args.render})
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
        from .build import render

        print("Rendering …")
        with timed(logger, "render"):
            video = render(str(out), args.quality)
        logger.save_video(video)
        print(f"Rendered: {video}" if video else "Render finished but output not found.")

    logger.finish(True)
    print(f"Run logged at {logger.dir}")


if __name__ == "__main__":
    main()
