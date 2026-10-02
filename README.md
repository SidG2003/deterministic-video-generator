# deterministic-video-generator

Deterministic (no-AI-video-model) explainer/story video generation using
[ManimCE](https://www.manim.community/). Cheap, fast, reproducible: same input → same output.

## Architecture: IR + layout + style (Option D)

Novelty per video comes from freely *composing* and *styling* a scene-graph, not
from a fixed set of templates. Robustness comes from that scene-graph being
validated data (not code) and a layout engine that places each beat's elements by
measured size (it does not yet prevent collisions with objects that persist from
earlier beats — overlaps are tracked per run, see Run logs).

```
topic ──► (generator: LLM/planner) ──► IR JSON ──► [ validate ] ──► [ layout ] ──► [ style ] ──► Manim ──► MP4
             novel & intelligent          data        safe          measured        look        deterministic
```

- `dvg/ir.py` — scene-graph schema + strict validation (the generator's contract)
- `dvg/layout.py` — measured placement within a beat's layout slots
- `dvg/style.py` — palette/typography/motion presets (novelty-in-look lever)
- `dvg/elements.py` — IR element → Manim mobject factories
- `dvg/director.py` — interprets IR against a Manim Scene
- `dvg/build.py` — `python -m dvg.build <ir.json>` deterministic renderer
- `examples/cache_explainer.json` — a whole video as data

Render a video from IR:
```bash
source .venv/bin/activate && export SDKROOT="$(xcrun --show-sdk-path)"
python -m dvg.build examples/cache_explainer.json --quality l
```
Verified deterministic: same IR + seed → byte-identical video frames.

## Generate a video from a topic (the AI authoring step)

An LLM writes the IR; the renderer stays deterministic. Only this step uses AI,
and a **validate-and-repair loop** checks each candidate against the IR contract
and feeds the validator's error back to the model until it's valid — so
generation can never produce something that renders broken.

```bash
cp .env.example .env                # then fill in a key for your provider
python -m dvg.generate "How does a DNS lookup work?" --style midnight --render
```
Provider is chosen from `--model`: `claude-*` → Anthropic (`ANTHROPIC_API_KEY`),
`gpt-*`/`o*` → Azure OpenAI or public OpenAI. For Azure, configure one numbered
block per deployment (`AZURE_OPENAI_<N>_ENDPOINT/DEPLOYMENT/API_KEY`) — each can
live on its own resource — and pass the deployment name as `--model`. See
[.env.example](.env.example) for all variables. Keys load automatically from
`.env` (gitignored).
- `dvg/prompt.py` — the pedagogy + schema system prompt (self-checks that it
  documents every element/animation the renderer supports).
- `dvg/generate.py` — the LLM call + repair loop (`--out`, `--render`, `--model`).
- Default model `claude-opus-5`; system prompt is prompt-cached across runs.
- Graph `expr` strings are evaluated by a safe AST interpreter (`dvg/mathexpr.py`),
  never `eval` — the IR is treated as untrusted input.
- Workflow: generate IR once (AI) → optionally hand-edit the JSON → render
  (deterministic, cheap, repeatable).
- `--depth {overview,standard,deep}` (default `standard`) controls breadth of
  conceptual coverage. There is no target duration — length follows the content,
  and the prompts enforce tight pacing (no lingering/padding).

### Two modes: `--mode constrained` (default) vs `--mode freeform`
- **constrained** (default): the LLM writes safe IR against our vocabulary;
  validation is static/instant; rendering is deterministic; layout spaces each
  beat's elements, but LLM-written IRs can still overlap (objects persisting from
  earlier beats, several elements in one band) — measured by the overlap detector.
- **freeform**: the LLM writes a COMPLETE Manim scene (Python), which is
  sandboxed, rendered, verified, and repaired on failure. Max expressiveness,
  but crashes/off-screen/overlaps are possible and "validation" means actually
  executing + rendering. Use to explore the ceiling.
```bash
python -m dvg.generate "How does gradient descent work?" --mode freeform
```
The freeform sandbox ([dvg/freeform.py](dvg/freeform.py)) is **spike-level**: an
AST allowlist scan (only manim/numpy/math/random imports; no os/eval/open/dunder),
a separate subprocess, a wall-clock timeout, and an RLIMIT_CPU backstop. This
guards against *accidents* in your own LLM's code — it is NOT a hardened jail. If
topics/code ever come from untrusted users, run it inside a network-isolated
container (Docker/gVisor) with a read-only filesystem instead.

### Sample prompts for comparisons
A fixed set of prompts lives in [eval/topics.toml](eval/topics.toml) (`t01`–`t11`),
so different modes, models and prompt versions can be compared on the same inputs.
Pick one by number, id or name; the run's `meta.json` records `params.topic_id`.
```bash
python -m dvg.topics                                  # list them
python -m dvg.generate --topic-id 5 --mode freeform   # same as --topic-id t05 / rocket-orbit
```
Don't edit an existing entry's prompt or depth (past runs would stop being
comparable); add a new id instead.

### Phase 0 spike (kept for reference)
- `scenes/concept_explainer.py` — concept explainer, hand-coded
- `scenes/history_timeline.py` — history as a timeline (shows the overlap bug the
  layout engine now solves)

## Setup (macOS, Apple Silicon)

System libs (once):
```bash
brew install cairo pango pkg-config ffmpeg
```

Python env:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
export SDKROOT="$(xcrun --show-sdk-path)"   # see note below
pip install -r requirements.txt
```

> **Note — SDKROOT:** This machine's Command Line Tools have broken default SDK
> resolution (`ld: library 'System' not found`). Exporting `SDKROOT` fixes any C
> compilation (needed to build pycairo). Keep it exported when pip-installing.
> A cleaner long-term fix is reinstalling CLT: `sudo rm -rf /Library/Developer/CommandLineTools && sudo xcode-select --install`.

## Render

```bash
source .venv/bin/activate
export SDKROOT="$(xcrun --show-sdk-path)"
manim -pql scenes/concept_explainer.py ConceptExplainer   # -ql draft/480p, -p autoplay
```
Quality: `-ql` (480p, fast) · `-qm` (720p) · `-qh` (1080p) · `-qk` (4K)

## IR vocabulary
- **Elements:** `text`, `node`, `dot`, `connector`, `card`, `timeline`, `shape`,
  `math`, `axes`, `graph` (`math` needs LaTeX — see below)
- **Animations:** `write`, `create`, `fade_in`, `fade_out`, `grow`, `move`,
  `shift`, `reveal`, `transform`, `replace`, `morph_tex`, `indicate`,
  `circumscribe`, `flash`, `focus`, `reset_camera` (`morph_tex` needs LaTeX)

A `graph` plots `f(x)` on an `axes`. The function is written as a string
(`"sin(2*x)"`) and evaluated by a safe AST-whitelisted interpreter in
`dvg/mathexpr.py` — never `eval`/`exec`, since the IR is untrusted input. Two
graphs on the same axes can `transform` into each other (a curve deforming —
the signature 3b1b move). `focus`/`reset_camera` pan-and-zoom the camera.

## Persistent canvas
The canvas (id → mobject) persists across the whole video. Element ids are
video-global stable handles: a beat declares only the objects it *introduces*,
and later beats reference existing objects by id (in `move`/`shift`/`transform`/
`fade_out`). Objects persist by default; a beat removes them explicitly:
- `"clear": true` — fade everything out at beat end (slideshow cut)
- `"exit": ["id", ...]` — fade out just these

Use `transform` (not `replace`) to morph a persistent object in place — it keeps
the source's identity so later beats can keep referencing it. See
`examples/continuity_demo.json`.

## LaTeX (for `math` / `morph_tex`)
The Homebrew cask needs an admin password, so run it interactively in your terminal:
```bash
brew install --cask basictex               # or: sudo installer -pkg /opt/homebrew/Caskroom/basictex/*/mactex-basictex-*.pkg -target /
brew install dvisvgm                        # BasicTeX omits it; Manim needs it for DVI->SVG
eval "$(/usr/libexec/path_helper)"          # add TeX to PATH in this shell
sudo tlmgr update --self
sudo tlmgr install standalone preview doublestroke physics wasysym ragged2e relsize
```
Then the equation demo renders:
```bash
python -m dvg.build examples/pythagoras.json --quality l
```

## Run logs
Every generation run is archived under `runs/<timestamp>_<mode>_<slug>/` (git-ignored):
- `prompt.txt`, `system_prompt.txt` — the exact user message and system prompt sent to the model
- `attempt_N.txt` / `attempt_N.py` — each LLM attempt (so repair rounds are visible)
- `attempt_N_feedback.txt` — the repair message sent back after attempt N failed
- `ir.json` (constrained) or `scene.py` (freeform) — the final artifact
- `video.mp4` — the rendered output
- `meta.json` — topic, params, `user_prompt`, `system_prompt_version` + `system_prompt_sha256`
  (tag and hash of the exact system prompt, matching [docs/prompt-log.md](docs/prompt-log.md);
  a `+modified` tag means the prompt was edited without logging a new version),
  success, attempts, **per-step timings, CPU usage, token usage**, `overlaps` and `api_checks`

Steps timed differ by mode: constrained → `llm_call`, `validate`, `render`;
freeform → `llm_call`, `scan`, `api_check`, `sandbox_render`, `verify`. `meta.json` aggregates
`step_totals_seconds` so you can see which steps eat the most time. Each step also
records `cpu_seconds` (user+system CPU, including reaped subprocesses) and
`cores_used` (`cpu_seconds/seconds`, i.e. cores-equivalent utilization), with
`step_totals_cpu_seconds`, `total_cpu_seconds`, and the machine's `cpu_count` — so
you can see, e.g., that `llm_call` is I/O-bound (~0 cores) while `render` is
CPU-bound (~1 core). Each `llm_call` also records its token usage (`input_tokens`,
`output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`),
aggregated per step in `token_totals` and overall in `total_tokens`, so you can
see cost alongside time. Per-attempt `steps[]` and `tokens[]` entries each carry
their 1-based `attempt` number (repair rounds), so a specific attempt's time,
CPU, and tokens are directly identifiable; `token_totals` is keyed by step name
while `total_tokens` is the whole-run rollup (today only `llm_call` records
tokens, so they coincide).

### Static API check (knowledge base, layer 1)
Freeform code is checked against the **installed** ManimCE API before it runs
(`dvg/kb/`, see [docs/knowledge-base.md](docs/knowledge-base.md)): undefined names,
scene/camera attributes the scene type lacks, GrowArrow on non-Arrows, helpers called
with wrong arguments, missing methods. Errors skip the render and go to the repair
loop all at once; findings are logged in `meta.json` as `api_checks`.
```bash
python -m dvg.kb.api Arrow Text     # API reference for classes (from the installed version)
python -m dvg.kb.check scene.py     # check a scene file
python -m dvg.kb.verify             # verify the KB: manimgl table + technique snippets
```
The knowledge base also holds a manimgl -> ManimCE translation table and 11
technique recipes with verified ManimCE snippets (`dvg/kb/techniques/`); they are
not sent to the model yet — see the doc for status.

### Overlap tracking
Every render (constrained, freeform, cold-sim) runs the shared overlap detector
(`dvg/overlap.py`). After each animation it checks the visible text on screen and
writes a report to `meta.json` as `overlaps`: `overlap_pairs` (two separate text
objects colliding), `clipped_texts` (partly off-frame), `edge_texts` (touching the
frame border), `tiny_texts` (effective font size < 18), `max_texts_on_screen`, plus
the individual issues with time and source line (freeform) or beat id (constrained).
It only measures — it never fails a run — so prompts, models and modes can be
compared on the same numbers. Thresholds live in the report; the detector is
versioned (`overlap-v1`) like the prompts.

Score existing runs without re-rendering (no frames are drawn, so it's fast):
```bash
python -m dvg.overlap runs/*/            # print a table
python -m dvg.overlap runs/<dir> --write # also store the report in meta.json
```

## Not yet installed
- **manim-voiceover** — TTS + auto-timed narration. Planned for Phase 1c.

## Roadmap
- [x] Phase 0: spike — render both content styles
- [x] Phase 1a: IR schema + layout + style engines + end-to-end render from JSON
- [x] Phase 1b: timeline/card (overlap-safe), shape/math elements, transform anims
- [x] Phase 1c: persistent canvas (object identity across beats) + shift/exit/clear
- [x] Phase 1d: axes/graph (safe f(x) eval), emphasis anims, camera moves
- [x] Phase 2: LLM authoring front-end (topic → IR JSON) + validate-repair loop
- [x] Phase 2b: freeform mode (LLM writes full Manim code) + sandbox/verify/repair
- [ ] Phase 3: narration (manim-voiceover) + audio-driven timing
- [ ] Phase 4: polish — captions, music, branding, render queue
