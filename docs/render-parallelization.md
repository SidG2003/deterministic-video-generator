# Render parallelization — findings (not implemented)

Notes from investigating whether constrained-mode renders can be split and
rendered in parallel (e.g. scene/beat-wise). Conclusion: **not worth building
at current scale; revisit only for 4K or long-form output.** Kept for later.

## Why "it's deterministic" isn't enough
- Determinism (same IR + seed → same frames) gives *reproducibility*, not
  *independence*. Parallelism needs independent units.
- The `Director` keeps a single `self.canvas` (id → mobject) that **persists and
  mutates across all beats** (`dvg/director.py`). Beat N's starting visual state
  is the accumulated result of beats 0..N-1 → a sequential data dependency (like
  a reduce, not a map). So a beat can't be rendered in isolation without first
  reconstructing all prior state.

## Natural split points
- `clear=True` beats fully empty the canvas → true "hard cuts" with no carried
  state. Segments between clears are genuinely independent.
- Confirmed independence-friendly facts: beats do **not** consume the `random`
  stream during rendering (seed is a future hook; `style.py` seeds separately),
  and the camera resets from a fixed snapshot. So clear-boundary segments need
  no RNG/camera fast-forward.

## Measured clear boundaries (sample IRs)
| IR | beats | clears | segments |
|---|--:|--:|--:|
| continuity_demo, graph_demo, internet_history, pythagoras, transform_demo | 1–3 | 0 | **1 (unsplittable)** |
| how_dns_lookup | 5 | 1 | 2 |
| cache_explainer | 3 | 2 | 3 |
| what_is_a_hash_table | 6 | 2 | 3 |
| compound_interest | 4 | 3 | 4 |

→ 5 of 9 have **zero** clears (can't split via this method); the rest give only
2–4 segments (median ~2).

## Measured costs (single machine)
- **Fixed per-process overhead ≈ 1.95s**, almost all Manim import. Paid once in
  serial, but **once per worker** in parallel.
- Frame-render-only wall (import excluded), `how_dns_lookup`:
  - l/480p 1.3s · m/720p 2.1s · h/1080p 4.9s · k/4K 11.9s

## The verdict (plug in the numbers)
Parallel wall ≈ `import(1.95s)` + `longest_segment_render` + concat/spawn(~0.3s).
- **l/m quality:** the ~2s import **exceeds the entire frame-render workload**
  (1.3–2.1s). 2-way split of `how_dns` at l ≈ 2.9s vs 3.25s serial — ~10% before
  contention/uneven segments erase it. **Break-even or net slower.**
- **4K:** `how_dns` at k ≈ 8.2s (2-way) vs 13.85s serial → **~40%**; a 4-segment
  4K video ~50–60%. **Worth it.**
- **Crossover rule of thumb:** per-segment render must comfortably exceed the
  ~2s import for parallelism to win → 4K or much longer videos, AND only the
  ~half of videos that contain `clear` cuts, at 2–4× width.

## Options (if revisited)
- **Option A — split at `clear` boundaries + ffmpeg concat.** Highest
  correctness-per-effort; segments are already independent. Limited by clear
  count; useless for 0-clear videos.
- **Option B — split anywhere via state fast-forward.** Each worker replays
  prior beats in a no-frame "advance to end state" mode, then renders its beats.
  More parallelism, but needs an instant-finish path for animations
  (camera/layout end states) and adds redundant replay cost. More work/risk.
- **Freeform mode:** effectively unsplittable — one monolithic `construct()`
  with local vars and interleaved play/wait; no beat structure to partition.

## Higher-leverage alternative at current scale
- The biggest fixed cost is the ~1.95s Manim import. A **persistent warm render
  worker** (import once, render many IRs sequentially) amortizes it across a
  batch with none of the state-dependency risk of splitting a single video.

## If/when implementing Option A
- Validate output is **byte-identical** to the current single-pass render
  (determinism makes seams line up). Concat losslessly (`ffmpeg -f concat -c copy`).
- Add a `--parallel N` flag to `dvg.build`; group beats at clear boundaries.
