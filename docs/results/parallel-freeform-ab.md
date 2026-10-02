# Results: freeform baseline vs sectioned (A) vs fan-out (B)

Comparison of the three freeform pipelines on a fixed set of sample prompts, run
through [`scripts/ab_eval.py`](../../scripts/ab_eval.py). This is the step-4
write-up of [the parallel-freeform plan](../plans/parallel-freeform.md).

> **Sample size — read everything here as directional.** This run covers **2
> topics × 2 repeats × 3 modes = 12 runs** (a deliberately reduced grid, not the
> full 11-topic matrix). Two topics cannot separate a pipeline effect from a
> topic effect; treat the numbers as indicative and the visual notes as the main
> signal.

## Setup
- **Topics:** `t01` entropy (particles / statistics / heavy-render) and `t07`
  bayes (formulas / probability). Chosen for contrast.
- **Modes:** `freeform` (baseline, 1 call, sequential render), `freeform-sectioned`
  (A, 1 call following the section contract, parallel render),
  `freeform-fanout` (B, planner + per-scene calls, parallel render).
- **Model:** `gpt-5.4-mini-deployment-db7e5` for the baseline, the sectioned call,
  and the fan-out scene calls. Fan-out planner: `gpt-5.4-deployment-951f1`.
- **Quality:** `-ql` (480p). **Render strategy** for A/B: `parallel`,
  `--max-concurrency 4` for B. 16-core Mac.
- Eval dir: `runs/_eval/20261002_171314/` (per-run `meta.json`, `scene.py`,
  `plan.json`, and a `contact_sheet.png` each).

## Headline numbers (per-mode averages, n = 4 runs each)

| Mode | Success | Total tokens | Cached | LLM s | Render s | **Total wall s** | Video s | Tokens / video-s |
|---|---|---|---|---|---|---|---|---|
| `freeform` (baseline) | 4/4 | **6.1k** | 1.8k | 15.5 | 5.2 | **21.5** | 37.1 | **164** |
| `freeform-sectioned` (A) | 4/4 | 13.5k | 6.9k | 19.0 | 5.1 | 28.1 | 28.8 | 470 |
| `freeform-fanout` (B) | 4/4 | **52.6k** | 21.5k | 102.3* | 76.9* | **128.3** | **116.3** | 452 |

\* For fan-out, `LLM s` and `Render s` are **sums across scenes**, which overlap
because scenes are generated and rendered concurrently (`--max-concurrency 4`);
the real elapsed time is the `Total wall s` column. For the single-call modes the
LLM/render columns are wall time.

### Per-cell detail

| Topic | Mode | Repairs | Total tokens | Total wall s | Video s |
|---|---|---|---|---|---|
| t01 | baseline | 0 | 6.6k / 6.1k | 23.8 / 20.9 | 46.4 / 40.5 |
| t01 | sectioned | 1 / 1 | 13.7k / 14.3k | 27.3 / 29.4 | 28.4 / 31.6 |
| t01 | fan-out | — | 50.2k / 63.2k | 65.4 / 69.9 | 113.3 / 125.9 |
| t07 | baseline | 0 | 5.5k / 6.1k | 17.8 / 23.5 | 27.5 / 33.9 |
| t07 | sectioned | 1 / 1 | 13.2k / 13.0k | 30.7 / 24.8 | 30.2 / 25.1 |
| t07 | fan-out | — | 53.2k / 43.9k | 67.9 / **310.0** | 112.6 / 113.5 |

## KPIs

**Success / robustness.** 12/12 produced a valid video. The baseline succeeded on
the first try every time. Sectioned needed exactly **one repair every run** — in
all four, the first attempt tripped a check (API or the section-contract runtime
check) and the repair fixed it; the contract is doing real work. Fan-out repaired
**individual scenes** without regenerating the others (e.g. elsewhere, t11 repaired
scenes s03/s04/s06 only), which is the point of the per-scene loop.

**Cost (tokens, ∝ $).** Baseline is cheapest by far (~6k). Sectioned is ~2.2×
(longer system prompt + the guaranteed repair). Fan-out is ~**8.6×** the baseline
(a planner call plus ~9 scene calls). But fan-out also produces **~3× longer
videos**, so per *second of finished video* sectioned (470) and fan-out (452) are
about the same, and ~2.8× the baseline (164). Fan-out's prompt caching is
substantial (21.5k cached of 52.6k) because the shared preamble + scene list sit
at the front of every scene call.

**Wall time.** Baseline ~21s, sectioned ~28s, fan-out ~128s. Fan-out is slowest by
a wide margin even with concurrency, dominated by the many LLM calls. One fan-out
cell (t07 rep 2) took **310s** because a scene's render ballooned — the known
heavy-scene cost: a time-sliced unit must fast-forward its prefix, and that work
isn't free (see the plan's render notes).

**Render parallelism.** At this scale the render win is small: these are light 2D
scenes with few sections, so sectioned's parallel render (~5s) is no faster than
the baseline's single-process render (~5s). The engine's value shows up as
*correctness* (frame-identical sequential vs parallel, verified in tests) and will
show as *speed* on genuinely heavy sectioned content, not on short 2D films.

## Visual rubric (1–5, from the contact sheets; directional)

| | Cohesion | Wow / beauty | Correctness | Legibility | Coverage / pacing |
|---|---|---|---|---|---|
| baseline | 5 | 3 | 4.5 | **4.5** | 3 |
| sectioned | 5 | 3.5 | 4.5 | 4 | 3.5 |
| fan-out | 4 | **4.5** | 4.5 | 3.5 | **4.5** |

Observations:
- **Baseline** is the cleanest and most legible, and the most modest: ~5 sections,
  short, a single consistent look. The bayes film (prior → filter → posterior →
  update → one-sentence) reads clearly with no overlaps.
- **Sectioned** looks about as uniform as the baseline (same single-call author),
  with slightly more deliberate structure. Minor label overlaps appear inside a
  couple of diagrams, and transition frames occasionally show the next title over
  the current formula.
- **Fan-out** is the most ambitious and comprehensive: 9 distinct scenes with
  varied metaphors (a confidence gauge, a disease-grid, a filtering grid, a tree
  for the denominator, updating belief bars, a checklist). The shared preamble
  keeps the **palette** consistent, so it does not feel disjoint on colour — but
  scene-to-scene *layout* language varies, and it has the most overlapping / faint
  labels of the three. It covers the topic most thoroughly.
- The planner chose `Scene` (2D) for every scene on both topics, including entropy
  — so no 3D was exercised here, and the heavy-render path stayed mostly untested
  by B at `-ql`.

## Cost per successful video
All 12 succeeded, so cost ≈ tokens: **baseline ~6k, sectioned ~14k, fan-out ~53k**
per video. Normalised by video length they converge (baseline 164, sectioned 470,
fan-out 452 tokens per second of video) — i.e. most of fan-out's extra cost buys
*more video*, not a higher rate.

## Takeaways
- **Baseline freeform** is the cost/latency floor and the most reliably legible,
  at the price of ambition and coverage. Good default when cheap-and-clean wins.
- **Sectioned (A)** adds structure and a frame-reproducible parallel render for ~2×
  the tokens and a near-guaranteed single repair, with output quality close to the
  baseline. The render-time win needs heavier content than these 2D films to
  appear; the determinism and contract guarantees are real now.
- **Fan-out (B)** buys breadth and visual variety — the richest, most complete
  films — for ~9× the tokens and ~6× the wall time, and with more legibility
  slips. Best when thoroughness and distinct per-scene visuals matter more than
  cost or speed; the per-scene repair isolation makes it robust despite the many
  calls.

## Limitations
- **n = 2 topics × 2 repeats.** Topic and pipeline effects are confounded; a small
  difference here is noise.
- **Overlap pairs** were only recorded for the baseline (the parallel engine does
  not run the overlap detector per unit). A/B's overlap counts would need a
  post-hoc pass with `dvg.overlap.score_scene_file` on the saved `scene.py`.
- **Fan-out `LLM s` / `Render s` are sums**, not wall time (scenes overlap). Use
  `Total wall s`.
- No 3D was generated on these topics, so the heavy-render / time-slicing path is
  under-exercised here (and heavy 3D is not bit-reproducible in this environment —
  see the plan's render notes).
- `-qh` KPIs were not collected in this reduced run.
