# Plan: parallel freeform — sectioned (A) vs fan-out (B)

Self-contained execution plan. Read it fully before starting. Background and
measurements: [../render-parallelization.md](../render-parallelization.md);
prompt conventions: [../prompt-log.md](../prompt-log.md).

**Scope of this version: architecture and pipeline only.** The goal is to get the
two new pipelines generating and rendering correctly. The knowledge base
(`dvg/kb/`: API check, technique snippets, manimgl table) and the overlap detector
(`dvg/overlap.py`) are deliberately **not** integrated into the new modes. They
will be added as hooks afterwards, measured, and this plan revised then (see
"Hook points for later").

## Goal
Add two **opt-in** freeform pipelines next to the existing sequential
`--mode freeform`, get them working end to end, then compare all three on the
same topics.

| Mode | Generation | Rendering |
|---|---|---|
| `freeform` (baseline — must stay unchanged) | 1 LLM call, free structure | 1 process |
| `freeform-sectioned` (A) | 1 LLM call, follows the section contract | sections in parallel, heavy ones time-sliced |
| `freeform-fanout` (B) | 1 planner call → N scene calls in parallel | scenes in parallel, heavy ones time-sliced |

How code is generated and how it is rendered are independent. The new modes get a
rendering flag, **`--render-strategy {sequential,parallel}`** (default
`sequential`), so render gains and generation gains can be measured separately.
**Do not reuse `--render`** — it already exists as a boolean for constrained mode.

## Ground rules (apply to every step)
- **Don't change existing behaviour.** `--mode freeform` and `--mode constrained`
  must behave exactly as before: same prompt (`freeform-v3`, `constrained-v1`),
  same steps (the baseline keeps its existing `api_check` step and overlap
  tracking), same `meta.json` fields. Only additive changes in shared modules.
- **Keep the new modes free of the KB and the detector.** New code must not import
  `dvg.kb` or `dvg.overlap`, and must not call `freeform._run` or
  `freeform.api_check` (`_run` runs the overlap detector inside the sandbox).
  New modes render only through the new engine (step 1b).
- **Keep the security sandbox for all LLM-written code**: the security scan
  `freeform.scan_code` (an import/call allowlist — not part of the KB) runs before
  anything executes, and every render runs in a subprocess with the CPU rlimit.
  A parallel worker is just another sandboxed subprocess.
- **Prompts:** every new prompt gets a family + version (`sectioned-v1`,
  `fanout-planner-v1`, `fanout-scene-v1`). Log the entry (intent first) in
  `docs/prompt-log.md` *before* using it, with tag + 12-char SHA-256, and add
  `<NAME>_PROMPT_VERSION` / `<NAME>_PROMPT_SHA` constants next to the prompt (see
  `freeform_prompt_version()` and `dvg.runlog.prompt_version`). Every run's
  `meta.json` must record the prompt version(s) it used (`RunLogger.record_prompts`).
- **Reuse, don't reimplement:** `dvg/manim_patches.py::fast_forward` (exact
  fast-forward), `dvg/runlog.py` (`RunLogger`, `timed`, `record_*`, unique run
  dirs), `dvg/llm.py` (`make_client`, `complete`), and from `dvg/freeform.py`:
  `scan_code`, `extract_narration`, `build_freeform_prompt` (v3 text to build on),
  `DEPTH_HINTS` (via `dvg.generate`), `_QUALITY`.
- **Never commit** `.env`, `runs/`, `media/`, `examples/generated/` (gitignored).
  Commit messages end with
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Manim is pinned at 0.21.0. The fast-forward patches touch Manim internals; the
  frame-hash test (step 1) is the guard against regressions.

## Environment
- `.venv` (Python 3.11): `source .venv/bin/activate`; `export SDKROOT="$(xcrun --show-sdk-path)"`.
- Credentials in `.env` (gitignored; copy it into every worktree). Azure deployments
  are chosen by `--model`: `gpt-5.4-mini-deployment-db7e5` (cheap, default for
  scenes) and `gpt-5.4-deployment-951f1` (stronger; planner / escalation).
- System needs `ffmpeg`/`ffprobe` and LaTeX (already installed).

## Measured facts the design relies on
(from `docs/render-parallelization.md`; one 16-core Mac, 480p)
- A render uses ~1 core; ~15 of 16 cores sit idle.
- Fixed cost per Manim process ≈ **1.95s** (mostly import).
- "Entropy" film: sequential **63.5s** → sections in parallel **50.4s** → plus the
  heavy 3D section time-sliced into 3 **24.0s**; all bit-identical to sequential.
  One section took 78% of the time, so **splitting heavy sections is what wins**.
- Parallel pitfalls (all observed):
  1. A **shared `media_dir`** corrupts output: a LaTeX cache race crashes `MathTex`,
     and uncached partial clips (`uncached_00000.mp4`…) overwrite each other.
     **One isolated media dir per worker.**
  2. A **shared RNG stream** across sections → seed each section explicitly.
  3. **Manim's default skip** collapses animations (TracedPath became a straight
     line). Use `manim_patches.fast_forward()`, which steps skipped animations
     frame by frame, skips rasterizing, and runs the post-animation updater pass.
  4. **Pixel-average checks hide real errors** (the broken trail scored 0.86/255).
     Verify with exact frame hashes (`ffmpeg -f framemd5`).
- Time-slicing granularity is one `play()`/`wait()`; the longest single
  animation is the floor on wall time.

---

## Branches and order
1. **Tag** current `main` as `baseline/sequential` (restore point). Push the tag.
2. **`feat/parallel-render`** — the shared foundation (step 1). Merge to `main`.
3. From the updated `main`, two branches that can proceed in parallel:
   - **`feat/freeform-sectioned`** (step 2, approach A)
   - **`feat/freeform-fanout`** (step 3, approach B)
   Use `git worktree add ../dvg-sectioned feat/freeform-sectioned` (and
   `../dvg-fanout`) to work on both at once; copy `.env`, reuse the main `.venv`,
   and set `DVG_RUNS_DIR` to one shared absolute path so all runs land together.
4. Merge A; rebase B onto `main`; merge B. Because step 1 creates the stub modules
   and the CLI dispatch, A and B only edit their own files. Expected conflicts:
   appends to `README.md` / `docs/prompt-log.md` only.
5. **Comparison** (step 4) on `main` with all modes merged.

---

## Step 1 — Foundation (`feat/parallel-render`)

### 1a. Run-log additions (`dvg/runlog.py`) — additive only
- `RUNS_DIR = Path(os.environ.get("DVG_RUNS_DIR", "runs"))`.
- Optional `scene: int | None` (1-based) on `record_step`, `record_tokens`, and
  `timed` — same pattern as `attempt`. Omitted when None, so existing `meta.json`
  output is unchanged.
- `record_render_units(units)` → `meta.json["render_units"]`: one entry per unit
  `{unit, kind: "scene"|"section"|"slice", section, anim_range, frames,
  seconds, cpu_seconds, worker}`, plus `render_strategy`, `workers`, and
  `render_critical_path_seconds`.

### 1b. Parallel render engine — new `dvg/parallel_render.py`
- **Work unit** = (scene file, class to render, optional section method, optional
  animation range `[a, b]`).
- **Worker** = a sandboxed subprocess with its own runner (do **not** reuse
  `freeform._RUNNER`/`_run`, which run the overlap detector): CPU rlimit, its own
  temp dir **and its own `media_dir`**, `sys.path` to the project root.
  The runner:
  - builds a subclass of the generated class whose `construct()` runs only the
    unit's section (or the whole scene), reseeding `random` and `np.random` with
    `1000 + section_index` right before each section;
  - for a slice, sets `from_animation_number=a`, `upto_animation_number=b` and
    wraps the render in `manim_patches.fast_forward()`;
  - writes its mp4 and `unit.json` (timing, frames).
- **Planning:**
  - A fast analysis pass (`fast_forward`, no rasterizing, `write_to_movie=False`)
    lists every `play()` with its frame count, per section. Define the fast-pass
    config in this module (the similar `_FAST` dict lives in `dvg/overlap.py`;
    don't import it).
  - Split a section into slices only if its frames exceed a threshold (default:
    above 25% of the video's total frames and above 150 frames).
  - Balance slices by frame count. **Known limitation:** 3D surfaces cost far more
    per frame, so log per-unit seconds to tune this later.
- **Scheduling:** pool size = `min(units, os.cpu_count() - 1, --workers)`, longest
  units first. `--render-strategy sequential` = the same engine with one unit
  for the whole file and one worker, so both strategies share one code path.
- **Assembly:** `ffmpeg -f concat -safe 0 -c copy` in unit order → final mp4 at
  `media/videos/<mode>/<slug>.mp4`.
- **Verification:** each unit exits 0 and produces an mp4; the final video exists
  and lasts more than 0.5s (same checks as `freeform._verify`). Failures raise an
  error naming the unit and the tail of its stderr, so it can be fed to a repair.
- **Fallback:** if planning finds no splittable unit, render sequentially and log it.

### 1c. Section contract — new `dvg/sections.py`
Shared by A (whole file) and B (each scene behaves as a one-section file).
- **Contract:**
  - module-level `SECTIONS = ["title", "intro", ...]` — method names in order;
  - exactly one class `Generated` (Scene / MovingCameraScene / ThreeDScene);
  - `construct()` contains only the background-colour line and
    `for name in SECTIONS: getattr(self, name)()`;
  - each section starts on an empty stage and **ends with no visible mobjects**;
  - no `self.<attr> = ...` in section methods (no shared state between sections);
  - MovingCameraScene: restore the camera before the section ends;
    ThreeDScene: stop ambient rotation before it ends;
  - no `random.seed` / `np.random.seed` in the code (the harness seeds each section);
  - `NARRATION` has one entry per section.
- `contract_prompt_snippet()` → the contract text, for prompts.
- `check_static(code) -> list[str]` (AST): every rule above that can be checked
  statically, each as a message with a line number.
- `check_runtime(code_path) -> list[str]`: one fast-forward pass (subprocess,
  sandboxed) that, after each section, checks that no visible mobjects remain
  (implement a small visibility test here: has points and fill or stroke opacity
  > 0.05 — don't import `dvg.overlap`), the camera frame is back at its default
  centre/width (2D), and ambient rotation is off (3D).
- Messages are written to go straight into a repair message. Version: `sections-v1`.
- **Important:** a sectioned file must always be rendered through the section
  harness (per-section reseeding), with **both** strategies, so sequential and
  parallel output are frame-identical.

### 1d. CLI and stubs (`dvg/generate.py`)
- `--mode` choices gain `freeform-sectioned` and `freeform-fanout`, dispatched to
  `dvg/modes/sectioned.py::run(args)` and `dvg/modes/fanout.py::run(args)`.
  Create `dvg/modes/__init__.py` and both modules as stubs raising
  `NotImplementedError`. This way A and B never touch `generate.py` again.
- New flags (used by the new modes; ignored by the existing ones):
  `--render-strategy {sequential,parallel}` (default `sequential`),
  `--workers N` (default `cpu_count-1`). For B: `--planner-model`,
  `--scene-model`, `--escalate-model` (optional), `--max-concurrency` (default 4).

### 1e. Tests and tooling
- Add `requirements-dev.txt` with `pytest`. Add a `tests/` folder.
- `tests/fixtures/entropy_scene.py` (**already committed**): the 8-section
  "entropy" film from the measurements (heavy 3D section included). It doesn't
  declare `SECTIONS` and seeds `random` once globally, so use it as an
  **any-scene time-slicing benchmark** (whole scene, sliced) — not as a
  section-contract fixture. Slow (~60s at `-ql`): mark its test
  `@pytest.mark.slow` and keep it out of the default run.
- `tests/fixtures/sectioned_scene.py`: a small 3-section scene that **follows the
  contract** and uses `MathTex`, `random`, an updater and a `TracedPath`.
- `tests/test_parallel_render.py`:
  - **frame-hash equality** (sequential vs parallel sections vs time-sliced) via
    `ffmpeg -f framemd5`;
  - an isolated-media-dir regression (two `MathTex` sections in parallel);
  - a determinism check (render a unit twice → identical frames).
- `tests/test_sections.py`: the static and runtime contract checks catch each
  rule (one bad fixture per rule) and pass the good fixture.
- `scripts/ab_eval.py` (used in step 4): `--topics FILE --modes a,b,c --repeats N
  --model ... --render-strategy ...` runs each cell and collects `meta.json` into
  `runs/_eval/<timestamp>/results.csv` + `summary.md` + a contact sheet per run.
  It must call the CLI or the mode `run()` functions, not duplicate pipeline logic.
  Read only fields every mode has, and treat missing ones as blank.

### Acceptance (step 1)
- `pytest tests/` passes; frame hashes identical across all three render paths.
- Slow benchmark: time-slicing the whole `tests/fixtures/entropy_scene.py` with the
  engine is faster than its sequential render and frame-identical (sequential
  measured at 63.5s at `-ql`).
- `python -m dvg.generate "<topic>" --mode freeform` behaves exactly as before:
  same steps and `meta.json` structure (compare against a run on the
  `baseline/sequential` tag).
- Isolation check — this must print nothing (it catches relative imports such as
  `from .overlap import …` / `from .kb.check import …`, and any use of `_run`):
  ```bash
  grep -rnE '(from|import) .*(overlap|kb)|api_check|_RUNNER|(^|[^A-Za-z0-9_.])_run\(|import.*[^A-Za-z0-9_]_run([^A-Za-z0-9_]|$)' \
    dvg/parallel_render.py dvg/sections.py dvg/modes/ dvg/fanout_preamble.py 2>/dev/null
  ```
- README documents the new flags. Merge to `main`.

---

## Step 2 — Approach A: sectioned single call (`feat/freeform-sectioned`)
Owns `dvg/modes/sectioned.py` and its prompt only.
- **Prompt `sectioned-v1`** = freeform-v3 + `sections.contract_prompt_snippet()`
  + heavy-scene guidance: split long 3D animations into ~3s plays, keep Surface
  resolution moderate, no `always_redraw` on large objects. Log it in prompt-log
  first.
- **Pipeline per attempt:** LLM call → `scan_code` → `sections.check_static` →
  `sections.check_runtime` → render with the engine (strategy from the flag) →
  verify. Any failure raises a repairable error listing **all** problems found at
  that stage; repair loop as in `generate_freeform` (`max_repairs`,
  `attempt_N_feedback.txt`).
- **Logging:** steps `llm_call`, `scan`, `contract_check`, `render`, `verify` with
  `attempt`; tokens per attempt; prompt version; `render_units`.
- **Artifacts:** `scene.py`, `narration.json` (one entry per section), `video.mp4`.
- **Acceptance:** 3 topics with `gpt-5.4-mini` at `-ql` produce videos with
  `--render-strategy parallel`; for one of them a sequential render through the
  harness is frame-identical; `meta.json` has `system_prompt_version:
  sectioned-v1` and `render_units`; the step 1 isolation check still prints nothing.

## Step 3 — Approach B: planner + fan-out (`feat/freeform-fanout`)
Owns `dvg/modes/fanout.py`, `dvg/fanout_preamble.py`, and the planner and scene
prompts. May add a small, additive concurrency helper (A doesn't touch `llm.py`).

### Planner (`fanout-planner-v1`; default model `gpt-5.4-deployment-951f1`)
Input: topic + depth. Output must be strict JSON, validated before use:
```json
{
  "title": "…",
  "style": {"palette": {"bg": "#0b0f1a", "primary": "#7aa2ff", "accent": "#ffd27a",
            "good": "#6fe3c2", "warn": "#ff6b5e", "muted": "#9fb0d8"},
            "font_sizes": {"title": 44, "body": 30, "label": 24},
            "transition": "fade"},
  "scenes": [
    {"id": "s01", "goal": "what the viewer learns", "visual": "the central metaphor",
     "on_screen_text": ["short", "phrases"], "narration": "spoken script",
     "scene_type": "Scene|MovingCameraScene|ThreeDScene",
     "enters_with": "empty stage", "leaves_with": "empty stage",
     "complexity": "light|medium|heavy"}
  ]
}
```
- Validation: 3–10 scenes, unique ids, every field present, valid colours,
  `heavy` at most 2 scenes. One repair round on invalid JSON.
- Save as `plan.json` in the run dir.

### Shared preamble (`dvg/fanout_preamble.py`)
Built programmatically from `plan.style` and prepended to every scene, so scenes
can't drift on style: imports, palette constants, the background colour, font
sizes. Keep it a module so shared helpers can be added later. It must pass
`scan_code` by itself.

### Scene calls (`fanout-scene-v1`; default model `gpt-5.4-mini-deployment-db7e5`)
- **Each call gets:** system prompt (v3 layout rules + the contract for a single
  section) + the shared preamble text + a plan summary + its neighbours' specs +
  its own spec.
- **Order the prompt for caching:** the shared prefix first, the scene-specific
  part last.
- **Each scene is a one-section file:** `class Generated(...)`, `SECTIONS =
  ["main"]`, `NARRATION = [<scene narration>]`, preamble prepended. It is checked
  and rendered like any sectioned file (contract checks + engine).
- **Concurrency:** a `ThreadPoolExecutor` capped by `--max-concurrency` (default 4),
  relying on the OpenAI SDK's retries plus your own backoff on 429/5xx.
  Optionally send the first scene alone to warm the prompt cache.
- **Per scene:** scan → contract checks → render → verify; repair only that scene
  (its own message history), up to `max_repairs`.
- **Logging:** tokens and steps recorded with `scene=i` (and `attempt`). Code is
  stored as `scenes/s01.py`, …; feedback as `scenes/s01_attempt_N_feedback.txt`.
- **Failure policy:** if a scene exhausts its repairs and `--escalate-model` is
  set, retry it once with that model; otherwise the run fails with a per-scene
  report (no partial videos in v1).
- **Assembly:** scene clips in plan order → concat (hard cuts; a later option is
  `ffmpeg xfade`, which re-encodes). `narration.json` = the scenes' narration in
  order; `scene.py` in the run dir = the files concatenated, for reference.
- **Rendering:** each scene is one unit, and heavy scenes are time-sliced by the
  engine. Rendering a scene can start as soon as its code passes checks; it
  doesn't have to wait for the other LLM calls.
- **Acceptance:** 3 topics produce videos; `plan.json` and the per-scene files are
  saved; `meta.json` has the planner and scene prompt versions and per-scene token
  totals; one scene that fails is repaired without regenerating the others; the
  step 1 isolation check still prints nothing.

---

## Step 4 — Comparison (on `main`, all merged)
A first comparison to confirm the pipelines work and to get baseline numbers.
The full evaluation is redone after the KB/detector hooks are added.
- **Topics** (fixed, `depth standard`): entropy and the second law (3D), Fourier
  series (formulas + graphs), how DNS lookup works, TCP handshake, public-key
  cryptography, Bloom filters, gradient descent (3D surface), Bayes' theorem
  (formula-heavy), semaphores.
- **Cells:** 3 modes × `gpt-5.4-mini` × 2 repeats at `-ql`. Then a subset at `-qh`
  for render KPIs. Run sequentially or with a small cap — 54 runs on one Azure
  deployment will hit rate limits.
- **KPIs** (from `meta.json`):
  - success, attempts / repairs;
  - tokens in/out/cached (total, and per scene for B);
  - LLM wall time, render wall time, total wall time;
  - CPU seconds, cores used;
  - video duration.
- **Known confound:** the baseline (`--mode freeform`) still runs its existing
  `api_check` step, which catches some crashes before rendering and changes how
  repairs happen; the new modes don't yet. Report attempts/repairs with that
  caveat.
- **Visual rubric** (1–5, from contact sheets and watching): cohesion across
  scenes, wow/beauty, correctness, legibility (judged by eye), pacing. Also watch
  whether B's scenes feel disjointed and whether A's look more uniform.
- **Write-up:** `docs/results/parallel-freeform-ab.md` with tables, the cost per
  successful video, and observations. Note sample size; treat small differences
  as directional.

## Hook points for later (not part of this plan)
These are where KB and detector features plug in once the pipelines work, so that
adding them is a small, measurable change:
- **Static API check:** after `scan_code`, before contract checks (per attempt in
  A, per scene in B).
- **Overlap detector:** inside the render worker, per section / scene; once per
  section for sliced units (a slice fast-forwards earlier animations and would
  double-count).
- **Technique snippets / manimgl table:** in the scene prompt (B: per scene, from a
  `techniques` field the planner could add) or in repair messages.
- **Layout helpers:** in the fan-out preamble module.

## Out of scope (do not do in this plan)
- Any integration of `dvg/kb/` or `dvg/overlap.py` into the new modes.
- Changing freeform-v3 or the constrained pipeline.
- `--render-strategy` for the existing `--mode freeform` / `--mode constrained`.
- Time-slicing constrained IRs (a hypothesis in the render doc; untested).
- Cross-fades between scenes, partial videos on failure, audio/narration rendering.
