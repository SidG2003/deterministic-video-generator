# deterministic-video-generator

Deterministic (no-AI-video-model) explainer/story video generation using
[ManimCE](https://www.manim.community/). Cheap, fast, reproducible: same input → same output.

## Architecture: IR + layout + style (Option D)

Novelty per video comes from freely *composing* and *styling* a scene-graph, not
from a fixed set of templates. Robustness comes from that scene-graph being
validated data (not code) and a layout engine that guarantees legibility.

```
topic ──► (generator: LLM/planner) ──► IR JSON ──► [ validate ] ──► [ layout ] ──► [ style ] ──► Manim ──► MP4
             novel & intelligent          data        safe          no-overlap      look        deterministic
```

- `dvg/ir.py` — scene-graph schema + strict validation (the generator's contract)
- `dvg/layout.py` — measured, no-overlap placement (kills the overlap bug class)
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

## Not yet installed
- **LaTeX** — only needed for `MathTex`/`Tex` (real math typesetting). Add with
  `brew install --cask basictex` when required.
- **manim-voiceover** — TTS + auto-timed narration. Add in Phase 1.

## Roadmap
- [x] Phase 0: spike — render both content styles
- [x] Phase 1a: IR schema + layout + style engines + end-to-end render from JSON
- [ ] Phase 1b: expand element/layout vocabulary (timeline, cards, images); style variants
- [ ] Phase 1c: narration (manim-voiceover) + audio-driven timing
- [ ] Phase 2: LLM authoring front-end (topic → IR JSON); video stays deterministic
- [ ] Phase 3: polish — captions, music, branding, render queue
