"""
Approach A — sectioned single call.

One LLM call writes a COMPLETE Manim scene that follows the section contract
(dvg.sections): a module-level SECTIONS list, one method per section, a construct()
that is just the section loop, each section self-contained (fresh stage in, clean
stage out, no shared self state, no manual seeding). That structure is what lets
the parallel engine render sections concurrently — and time-slice a heavy one —
with output frame-identical to a one-process render.

Pipeline per attempt: LLM call -> scan_code -> api_check -> contract (static +
runtime) -> render (dvg.parallel_render, strategy from --render-strategy) -> verify.
Any stage lists ALL of its problems at once and the repair loop feeds them back,
exactly like generate_freeform.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .. import llm
from .. import parallel_render
from .. import sections as sections_mod
from ..freeform import (FreeformError, _FENCE, _QUALITY, api_check,
                        build_freeform_prompt, build_freeform_user_message,
                        extract_narration, scan_code)
from ..generate import _slug
from ..parallel_render import ParallelRenderError
from ..runlog import RunLogger, prompt_version, timed

DEFAULT_MODEL = "claude-opus-5"


# --- prompt (sectioned-v1) --------------------------------------------------

_HEAVY = """\
HEAVY SCENES (your sections are rendered in parallel, and a heavy one is time-sliced)
- Break a long animation into several shorter self.play(...) calls of about 3s
  each, rather than one long play — the engine balances a heavy section across
  workers at play boundaries, so more, shorter plays parallelize better.
- Keep Surface resolution moderate (up to about (32, 32)); don't wrap a Surface or
  a large VGroup in always_redraw (animate it with .animate or an updater instead).
- Prefer giving a genuinely heavy idea (a 3D surface, a dense field) its own
  section so it can be sliced without dragging the lighter sections."""

_EXAMPLE = '''\
# Structure reference — themed, follows the section contract (invent richer visuals):
from manim import *
import numpy as np

NARRATION = [
    "A short spoken line introducing the idea.",
    "The next line, explaining the mechanism on screen.",
    "A closing line that lands the takeaway.",
]

SECTIONS = ["title", "mechanism", "closing"]

FONT = "Avenir Next"
INK = "#1e232b"
PRIMARY = "#3a6ea5"
ACCENT = "#c8862b"
MUTED = "#6b7280"


class Generated(Scene):
    def construct(self):
        self.camera.background_color = "#f5f3ee"  # light, not the Manim default
        for name in SECTIONS:
            getattr(self, name)()

    def title(self):
        title = Text("The Idea", font=FONT, font_size=48, color=INK, weight=BOLD)
        subtitle = Text("one clear sentence about it", font=FONT, font_size=28, color=MUTED)
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.3)
        self.play(FadeIn(header, shift=UP * 0.2), run_time=0.8)  # fade, never Write
        self.wait(1.8)  # hold so the viewer can read it
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)

    def mechanism(self):
        heading = Text("How it works", font=FONT, font_size=40, color=INK).to_edge(UP, buff=0.5)
        box_a = RoundedRectangle(width=3, height=1.6, corner_radius=0.2, color=PRIMARY)
        box_b = box_a.copy().set_color(ACCENT)
        boxes = VGroup(box_a, box_b).arrange(RIGHT, buff=2.0)
        arrow = Arrow(box_a.get_right(), box_b.get_left(), buff=0.15, color=MUTED)
        label_a = Text("input", font=FONT, font_size=26, color=INK).next_to(box_a, DOWN, buff=0.3)
        label_b = Text("output", font=FONT, font_size=26, color=INK).next_to(box_b, DOWN, buff=0.3)
        self.play(FadeIn(heading), Create(boxes), run_time=0.8)
        self.play(GrowArrow(arrow), FadeIn(label_a), FadeIn(label_b), run_time=0.6)
        self.wait(1.8)  # hold so the viewer can read it
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)

    def closing(self):
        takeaway = Text("The takeaway, in a few words.", font=FONT, font_size=34, color=ACCENT)
        self.play(FadeIn(takeaway, shift=UP * 0.2), run_time=0.8)
        self.wait(1.8)  # hold so the viewer can read it
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)'''

_FINAL_CHECK = """\
FINAL CHECK — before answering, walk through each section method: does it start
assuming an empty stage and end with every mobject faded out? Does construct()
contain only the background line and the SECTIONS loop? Is there exactly one
NARRATION entry per section? Did you avoid self.<attr> = ... inside sections and
any random.seed/np.random.seed call? Is there any class, method, or argument you
are not sure exists? Fix these first."""

_OUTPUT = """\
OUTPUT
- Return ONLY the Python code. No markdown fences, no commentary, nothing else."""


def build_sectioned_prompt() -> str:
    """freeform-v3 guidance, adapted to the section contract: the manual-seeding
    instruction is dropped (the harness seeds each section), the free-form
    construct example is replaced by a contract-following one, and the contract +
    heavy-scene guidance are added."""
    base = build_freeform_prompt()
    base = base.replace("- If you use randomness, seed it: random.seed(0).\n", "")
    head = base.split("FINAL CHECK —", 1)[0].rstrip()
    return "\n\n".join([
        head,
        sections_mod.contract_prompt_snippet(),
        _HEAVY,
        _FINAL_CHECK,
        _OUTPUT,
        _EXAMPLE,
    ])


SECTIONED_PROMPT_VERSION = "sectioned-v3"
SECTIONED_PROMPT_SHA = "a2f127785088"


def sectioned_prompt_version() -> str:
    return prompt_version(SECTIONED_PROMPT_VERSION, SECTIONED_PROMPT_SHA, build_sectioned_prompt())


# --- checks -----------------------------------------------------------------

def _contract_check(code: str) -> None:
    """Static then runtime section-contract check; raises a repairable error
    listing EVERY problem found (runtime only runs if the static rules pass)."""
    static = sections_mod.check_static(code)
    if static:
        raise FreeformError("The scene does not follow the section contract "
                            f"({len(static)} problem(s)); fix every one:\n"
                            + "\n".join(f"- {m}" for m in static))
    tmp = Path(tempfile.mkdtemp(prefix="dvg_sec_check_"))
    path = tmp / "scene.py"
    path.write_text(code)
    runtime = sections_mod.check_runtime(path)
    if runtime:
        raise FreeformError("The scene breaks the section contract at render time "
                            f"({len(runtime)} problem(s)); fix every one:\n"
                            + "\n".join(f"- {m}" for m in runtime))


# --- generate + repair loop -------------------------------------------------

def generate_sectioned(topic: str, model: str = DEFAULT_MODEL, max_repairs: int = 3,
                       quality: str = "l", depth: str = "standard",
                       strategy: str = "sequential", workers: int | None = None,
                       timeout: int = 240, client=None, logger: RunLogger | None = None
                       ) -> tuple[str, str]:
    """Generate a contract-following scene for `topic`, render it through the
    parallel engine, and repair on failure. Returns (code, output_mp4_path)."""
    if not topic or not topic.strip():
        raise FreeformError("topic must be a non-empty string")
    if client is None:
        client = llm.make_client(model)

    system = build_sectioned_prompt()
    slug = _slug(topic)
    user_msg = build_freeform_user_message(topic, depth)
    if logger:
        logger.record_prompts(system, user_msg, sectioned_prompt_version())
    messages = [{"role": "user", "content": user_msg}]
    dest = Path("media/videos/freeform-sectioned")
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{slug}.mp4"

    last_error = ""
    for attempt in range(max_repairs + 1):
        with timed(logger, "llm_call", attempt=attempt + 1):
            reply, tokens, used_model = llm.complete(client, model, system, messages)
        if logger:
            logger.record_tokens("llm_call", tokens, model=used_model, attempt=attempt + 1)
        code = _FENCE.sub("", reply).strip()
        if logger:
            logger.artifact(f"attempt_{attempt + 1}.py", code)
        try:
            with timed(logger, "scan", attempt=attempt + 1):
                scan_code(code)
            with timed(logger, "api_check", attempt=attempt + 1):
                api_check(code, logger, attempt + 1)
            with timed(logger, "contract_check", attempt=attempt + 1):
                _contract_check(code)
            with timed(logger, "render", attempt=attempt + 1):
                result = parallel_render.render(
                    code, out, sections=sections_mod.parse_sections(code),
                    strategy=strategy, workers=workers, quality=quality,
                    timeout_per_unit=timeout, logger=logger)
            with timed(logger, "verify", attempt=attempt + 1):
                narration = extract_narration(code)
                if not Path(result.video_path).exists():
                    raise FreeformError("render reported success but no video file exists")
            if logger:
                logger.artifact("scene.py", code)
                logger.artifact("narration.json",
                                json.dumps(narration, indent=2, ensure_ascii=False) + "\n")
            return code, result.video_path
        except (FreeformError, ParallelRenderError) as exc:
            last_error = str(exc)
            feedback = (f"That scene failed:\n{last_error}\n"
                        "Return the corrected, complete Python code only.")
            if logger:
                logger.artifact(f"attempt_{attempt + 1}_feedback.txt", feedback)
            messages.append({"role": "assistant", "content": code})
            messages.append({"role": "user", "content": feedback})

    raise FreeformError(
        f"no working sectioned scene after {max_repairs + 1} attempts; last error: {last_error}")


# --- CLI entry point --------------------------------------------------------

def run(args) -> None:
    from ..generate import _topic_param

    if args.quality not in _QUALITY:
        raise SystemExit(f"unknown quality {args.quality!r}")
    print(f"Generating sectioned Manim scene for: {args.topic!r} … "
          f"(render-strategy={args.render_strategy}, sandboxed)")
    logger = RunLogger("freeform-sectioned", args.topic, {
        "depth": args.depth, "model": args.model, "quality": args.quality,
        "render_strategy": args.render_strategy, "workers": args.workers,
        **_topic_param(args)})
    try:
        code, video = generate_sectioned(
            args.topic, model=args.model, quality=args.quality, depth=args.depth,
            strategy=args.render_strategy, workers=args.workers, logger=logger)
    except Exception as exc:
        logger.finish(False, error=str(exc))
        raise SystemExit(f"Sectioned generation failed: {exc}")

    out = Path(args.out) if args.out else Path("examples/generated") / f"{_slug(args.topic)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code)
    logger.save_video(video)
    logger.finish(True)
    print(f"Wrote scene code: {out}")
    print(f"Rendered: {video}")
    print(f"Run logged at {logger.dir}")
