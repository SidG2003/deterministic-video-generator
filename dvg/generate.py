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

DEFAULT_MODEL = "claude-opus-5"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Length is prompt-driven (duration is emergent from beats/animations/holds).
# Kept in the per-request user message so the cached system prompt stays stable.
LENGTH_HINTS = {
    "short": "Target length ~20-30 seconds: tight, 3-4 beats/sections.",
    "medium": "Target length ~45-70 seconds: 5-6 beats/sections with clear pacing.",
    "long": "Target length ~90-150 seconds: thorough, 6-9 beats/sections; build the "
            "idea in depth, one step at a time, with slightly longer pauses (holds/waits).",
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
    length: str = "medium",
    client=None,
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
        f"{LENGTH_HINTS.get(length, LENGTH_HINTS['medium'])}\n"
        "Produce the IR JSON for an explainer video on this topic."
    )
    messages = [{"role": "user", "content": ask}]

    last_error = ""
    for attempt in range(max_repairs + 1):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        reply = _text_of(response)
        try:
            data = _extract_json(reply)
            validate_ir(data)  # the hard contract
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
    try:
        code, video = generate_freeform(args.topic, model=args.model, quality=args.quality, length=args.length)
    except Exception as exc:
        raise SystemExit(f"Freeform generation failed: {exc}")

    out = Path(args.out) if args.out else Path("examples/generated") / f"{_slug(args.topic)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code)
    print(f"Wrote scene code: {out}")
    print(f"Rendered: {video}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an explainer-video from a topic.")
    parser.add_argument("topic", help="the concept/story to explain")
    parser.add_argument("--mode", choices=["constrained", "freeform"], default="constrained",
                        help="constrained = safe IR vocabulary (default); "
                             "freeform = LLM writes full Manim code, sandboxed")
    parser.add_argument("--style", default="midnight", choices=["midnight", "paper"])
    parser.add_argument("--length", default="medium", choices=["short", "medium", "long"],
                        help="target video length (prompt-driven)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id")
    parser.add_argument("--out", help="path to write the IR JSON (default: examples/generated/<slug>.json)")
    parser.add_argument("--render", action="store_true", help="render the video after generating (constrained mode)")
    parser.add_argument("--quality", choices=["l", "m", "h", "k"], default="l")
    args = parser.parse_args()

    if args.mode == "freeform":
        _run_freeform(args)
        return

    print(f"Generating IR for: {args.topic!r} …")
    try:
        data = generate_ir(args.topic, style=args.style, model=args.model, length=args.length)
    except Exception as exc:  # surface a clean message; the SDK raises many types
        raise SystemExit(f"Generation failed: {exc}")

    out = Path(args.out) if args.out else Path("examples/generated") / f"{_slug(args.topic)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {out}  ({len(data.get('beats', []))} beats)")

    if args.render:
        from .build import render

        print("Rendering …")
        video = render(str(out), args.quality)
        print(f"Rendered: {video}" if video else "Render finished but output not found.")


if __name__ == "__main__":
    main()
