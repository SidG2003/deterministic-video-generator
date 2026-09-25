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

## Not yet installed
- **manim-voiceover** — TTS + auto-timed narration. Planned for Phase 1c.

## Roadmap
- [x] Phase 0: spike — render both content styles
- [x] Phase 1a: IR schema + layout + style engines + end-to-end render from JSON
- [x] Phase 1b: timeline/card (overlap-safe), shape/math elements, transform anims
- [x] Phase 1c: persistent canvas (object identity across beats) + shift/exit/clear
- [x] Phase 1d: axes/graph (safe f(x) eval), emphasis anims, camera moves
- [ ] Phase 1e: narration (manim-voiceover) + audio-driven timing
- [ ] Phase 2: LLM authoring front-end (topic → IR JSON); video stays deterministic
- [ ] Phase 3: polish — captions, music, branding, render queue
