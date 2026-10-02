# Render parallelization — findings (not implemented yet)

Notes from investigating whether renders can be split and rendered in parallel.
All numbers: one 16-core Mac, 480p (`-ql`) unless noted, Manim 0.21.0 (pinned).

## Summary (current verdict)
- **Short constrained clips at l/m quality: not worth it.** The ~2s Manim import
  per worker exceeds the whole frame-render workload (1.3–2.1s).
- **Long freeform films with heavy scenes: worth it.** Measured on the "entropy"
  film: **63.5s → 24.0s (2.65×)** with output bit-identical to the sequential
  render, by combining two techniques:
  1. **Section split** — render independent sections in parallel, concat.
  2. **Exact time-slicing** — split any scene at `play()` boundaries; each worker
     fast-forwards the earlier animations without rasterizing them.
- Time-slicing needs **no special code structure** — it works on any
  deterministic Manim scene, including continuous (non-sectioned) freeform.
- Parallel rendering is orthogonal to how the code is generated.

## Corrections to earlier claims in this doc
Earlier versions overstated several points; corrected by measurement:
1. ~~"Not worth building at current scale"~~ → true only for short constrained
   clips at l/m. Heavy freeform films measured 2.65× faster.
2. ~~Freeform is "effectively unsplittable" / "whole-video only"~~ → wrong.
   Sectioned freeform splits at sections (verified 1422/1422 identical frames),
   and *any* freeform scene splits by time-slicing (verified 357/357 on a
   continuous scene).
3. ~~"Making freeform splittable reinvents constrained mode"~~ → wrong. Sectioned
   freeform keeps full Manim expressiveness inside each section; only
   cross-section continuity (morphing objects across sections) is lost. And
   time-slicing needs no generation change at all.
4. ~~"Option B needs an instant-finish path; more work/risk"~~ → Manim's own
   `from_animation_number`/`upto_animation_number` plus three small patches give
   an **exact** fast-forward. Manim's default "instant finish" skip is precisely
   what produces *wrong* frames (see pitfalls).
5. ~~"Both options are constrained-mode only"~~ → wrong; both work on freeform.
6. ~~"Within one video: still import-bound"~~ → only when segments are short.
   Heavy freeform segments render 10–50s each, so the ~2s import is minor.
7. ~~"Determinism is not the differentiator; structure is"~~ → too strong.
   Determinism + in-order execution is exactly what makes time-slicing exact.
   Structure only buys the cheaper section split (no fast-forward needed).

## Why "it's deterministic" isn't enough on its own
- Determinism (same input → same frames) gives reproducibility, not independent
  units. Scenes carry evolving state (constrained: the `Director`'s persistent
  `canvas`; freeform: Python locals, ValueTrackers, updaters, camera).
- Two ways around that:
  - **Cut where no state carries** (constrained `clear` beats; freeform sections
    that end on an empty screen) → units are independent.
  - **Reconstruct the state cheaply** → time-slicing with exact fast-forward.

## Technique 1 — section split (independent units)
Requirements a section must meet (the entropy film met all of them):
- sets its own camera at the start; starts/stops its own ambient rotation;
- ends with no **visible** objects (invisible zero-point `Mobject`s are harmless);
- shares no `self.*` state with other sections (locals are method-scoped);
- gets its **own RNG seed** (the film seeded once globally and drew from the
  shared stream across sections — rendered alone, a section got different values).

## Technique 2 — exact time-slicing (any scene)
Render slice `[a, b]` with `from_animation_number=a, upto_animation_number=b`.
Earlier animations are skipped, and three patches make that skip exact:
1. Step skipped animations **frame by frame** (Manim's skip collapses each
   animation into one time step).
2. **Don't rasterize** skipped frames (rasterizing is the expensive part).
3. After each skipped animation, run the **post-animation updater pass** too
   (Manim does `update_mobjects(0)` only when *not* skipping).
- Fast-forward cost is negligible here (heavy slice: 20.6s vs 20.1s without it),
  but grows for scenes with heavy per-frame Python updaters.
- Granularity is one `play()`/`wait()` call; the longest single animation is the
  floor on wall time.
- Patches touch Manim internals → keep Manim pinned and guard with a frame-hash
  regression test (sequential vs sliced on a fixture scene).
- Constrained mode also runs as plays in one `construct()`, so this should make
  zero-`clear` IRs splittable too — **hypothesis, not yet tested.**

## KPIs — the "entropy" freeform film (8 sections, 480p, 1422 frames)
| Run | Wall (incl. import) | Workers | Output vs sequential |
|---|--:|--:|---|
| Sequential, 1 process | **63.5s** | 1 | baseline |
| Section-parallel, **shared** media dir | 51.7s | 8 | ❌ 2 sections crashed; others corrupted (1417 frames) |
| Section-parallel, isolated media dirs | **50.4s** | 8 | ✅ 1422/1422 bit-identical |
| Section-parallel + heavy section in 3 exact slices | **24.0s** | 10 | ✅ each part verified bit-identical |
- Section render times: seven sections 1.1–3.1s each; `entropy_landscape` (3D
  surface + rotating camera) **49.5s = 78%** → the critical path until sliced.
- Lossless concat (`ffmpeg -f concat -c copy`): 0.15s, no frames dropped.
- Same section rendered twice → identical frames (the parallel path is deterministic).
- The original 1080p render of this film took 250s sequentially; a similar
  ratio is expected at 1080p but **not measured**.

## KPIs — optimizing the heavy 3D scene (`entropy_landscape` alone, 232 frames)
| Variant | Wall | Visual result |
|---|--:|---|
| Baseline (40×40 surface, 1,600 faces) | 50.5s | reference |
| Lower resolution (24×24) | 22.9s | ❌ visibly faceted, banded gradient (loses the wow) |
| OpenGL renderer | — | ❌ crashes on this code (would need a rewrite) |
| Time-sliced, Manim default skip | 23.4s | ❌ marble trail drawn as a straight line |
| **Time-sliced, exact fast-forward (3 slices)** | **23.3s** | ✅ 232/232 bit-identical |
- Remaining floor: the single 6s marble animation (22.7s). Generating heavy
  scenes as several shorter plays would let slicing go finer (not yet tested).
- Continuous semaphore scene (32 plays, 3 slices): 350/357 without patch 3 (the
  counter showed a stale "2" instead of "3"), **357/357** with it.

## Pitfalls found (must handle in any implementation)
- **Shared `media_dir` breaks parallel renders** in two ways:
  - LaTeX cache race → `MathTex` sections crash (dvisvgm error);
  - with caching off, partial clips are named `uncached_00000.mp4`, … under the
    scene class name → workers overwrite each other's clips (silent corruption).
  → **one isolated media dir per worker.**
- **Shared RNG stream** across sections → seed each section explicitly.
- **Pixel-average metrics hide semantic errors**: the broken marble trail scored
  a mean diff of only 0.86/255. Verify with exact frame hashes plus visual review.

## Constrained mode data (from the first pass)
- `clear` boundaries: 5 of 9 sample IRs have **zero** clears; the rest give 2–4
  segments (median ~2). Time-slicing would remove this limit (untested).
- Fixed per-process overhead ≈ **1.95s** (almost all Manim import).
- Frame-render wall (import excluded), `how_dns_lookup`: l 1.3s · m 2.1s ·
  h 4.9s · k 11.9s → at l/m the import exceeds the work (break-even or slower);
  at 4K a 2-way split saves ~40%.

## CPU utilization (from run logs)
- `llm_call` ~0.02 cores (network wait); a render ~1 core (Manim/Cairo is
  single-threaded) → ~15 of 16 cores idle during a render.
- So both **batch concurrency across videos** and **splitting one heavy video**
  use otherwise-idle cores. Cap workers by cores and RAM.
- `cpu_seconds` is an exact per-process counter (os.times()); unrelated machine
  activity can't inflate it, whereas a live psutil sampler would.

## Next steps
- **Execution plan:** [plans/parallel-freeform.md](plans/parallel-freeform.md)
  (foundation + sectioned mode A + fan-out mode B + evaluation).
- Build a parallel render engine (isolated dirs, per-section seeding, exact
  time-slicing, lossless concat, frame-hash regression test) as an **opt-in**
  path; the default sequential render stays unchanged.
- Generation-side guidance for heavy scenes: several shorter plays, no
  `always_redraw` of large objects, moderate surface resolutions.
- Test time-slicing on constrained IRs.
