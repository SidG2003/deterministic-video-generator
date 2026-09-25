# deterministic-video-generator

Deterministic (no-AI-video-model) explainer/story video generation using
[ManimCE](https://www.manim.community/). Cheap, fast, reproducible: same input → same output.

## Status: Phase 0 spike

Two example scenes proving the two content styles:
- `scenes/concept_explainer.py` — concept explainer (title → bullets → animated diagram)
- `scenes/history_timeline.py` — history told as an animated timeline/infographic

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
- [ ] Phase 1: scene-template library + storyboard JSON schema + narration (manim-voiceover)
- [ ] Phase 2: deterministic builder (storyboard JSON → per-scene render → ffmpeg assembly)
- [ ] Phase 3: LLM authoring front-end (topic → storyboard JSON); video stays deterministic
- [ ] Phase 4: polish — captions, music, branding, render queue
