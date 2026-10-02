#!/usr/bin/env python
"""
A/B(/baseline) eval harness for the freeform pipelines.

Runs a grid of (topic x mode x repeat) cells by shelling out to the normal CLI
(`python -m dvg.generate --topic-id ...`) — it never reimplements any pipeline —
then collects each run's meta.json into one CSV, a grouped summary, and a contact
sheet per run. It reads only fields every mode records and leaves missing ones
blank, so it keeps working as the modes diverge.

    python scripts/ab_eval.py --modes a,b,c --topics t01,t05,llms --repeats 2 \
        --model gpt-5.4-mini-deployment-db7e5 --render-strategy parallel

Modes: a = freeform-sectioned, b = freeform-fanout, c = freeform (baseline); the
full --mode names are accepted too. Each cell runs sequentially (one Azure
deployment rate-limits a parallel grid); use a small grid or repeats.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dvg.topics import load_topics  # noqa: E402

MODE_ALIASES = {
    "a": "freeform-sectioned", "b": "freeform-fanout", "c": "freeform",
    "baseline": "freeform", "sectioned": "freeform-sectioned", "fanout": "freeform-fanout",
}


def _resolve_mode(m: str) -> str:
    return MODE_ALIASES.get(m, m)


def _video_duration(mp4: Path) -> float | None:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "csv=p=0", str(mp4)], capture_output=True, text=True)
    try:
        return round(float(proc.stdout.strip()), 2)
    except ValueError:
        return None


def _contact_sheet(mp4: Path, out_png: Path, tiles: tuple[int, int] = (4, 3)) -> bool:
    cols, rows = tiles
    n = cols * rows
    dur = _video_duration(mp4) or 0.0
    if dur <= 0:
        return False
    # Sample n frames spread across the clip, scale, and tile into one sheet.
    fps_expr = max(n / dur, 0.1)
    vf = f"fps={fps_expr},scale=320:-1,tile={cols}x{rows}"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp4), "-vf", vf, "-frames:v", "1", str(out_png)],
        capture_output=True, text=True)
    return proc.returncode == 0 and out_png.exists()


def _collect(meta: dict, run_dir: Path) -> dict:
    """KPI row from a run's meta.json — only fields any mode may record."""
    params = meta.get("params") or {}
    tokens = meta.get("total_tokens") or {}
    steps = meta.get("step_totals_seconds") or {}
    api = meta.get("api_checks") or []
    api_errors = sum(a.get("errors", 0) for a in api)
    video = meta.get("video")
    duration = _video_duration(run_dir / video) if video and (run_dir / video).exists() else None
    # Mode-agnostic: LLM time is any step whose name mentions llm (llm_call for the
    # single-call modes, planner_llm + scene_llm for fan-out); model falls back to
    # the fan-out scene/planner model when there is no single `model` param.
    llm_seconds = round(sum(v for k, v in steps.items() if "llm" in k), 3) or ""
    model = params.get("model") or params.get("scene_model") or params.get("planner_model") or ""
    return {
        "topic_id": params.get("topic_id", ""),
        "mode": meta.get("mode", ""),
        "model": model,
        "success": meta.get("success", ""),
        "attempts": meta.get("attempts", ""),
        "api_check_errors": api_errors,
        "input_tokens": tokens.get("input_tokens", ""),
        "output_tokens": tokens.get("output_tokens", ""),
        "cached_tokens": tokens.get("cache_read_input_tokens", ""),
        "total_tokens": tokens.get("total_tokens", ""),
        "llm_seconds": llm_seconds,
        "render_seconds": steps.get("render", steps.get("sandbox_render", "")),
        "render_strategy": meta.get("render_strategy", ""),
        "render_critical_path_s": meta.get("render_critical_path_seconds", ""),
        "total_seconds": meta.get("total_seconds", ""),
        "video_duration_s": duration if duration is not None else "",
        "overlap_pairs": (meta.get("overlaps") or {}).get("overlap_pairs", ""),
        "run_dir": run_dir.name,
        "error": (meta.get("error") or "")[:160],
    }


def _run_cell(topic_id: str, mode: str, args, eval_runs: Path) -> dict | None:
    before = {p.name for p in eval_runs.iterdir()} if eval_runs.exists() else set()
    cmd = [sys.executable, "-m", "dvg.generate", "--topic-id", topic_id,
           "--mode", mode, "--model", args.model, "--quality", args.quality]
    if mode in ("freeform-sectioned", "freeform-fanout"):
        cmd += ["--render-strategy", args.render_strategy]
        if args.workers:
            cmd += ["--workers", str(args.workers)]
    if mode == "freeform-fanout":
        cmd += ["--max-concurrency", str(args.max_concurrency)]
        if args.planner_model:
            cmd += ["--planner-model", args.planner_model]
        if args.scene_model:
            cmd += ["--scene-model", args.scene_model]
        if args.escalate_model:
            cmd += ["--escalate-model", args.escalate_model]

    env = {**os.environ, "DVG_RUNS_DIR": str(eval_runs)}
    print(f"  $ {' '.join(cmd[2:])}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"    (exit {proc.returncode}) {proc.stderr.strip().splitlines()[-1:]}")
    after = {p.name for p in eval_runs.iterdir()} if eval_runs.exists() else set()
    new = sorted(after - before)
    if not new:
        return None
    run_dir = eval_runs / new[-1]
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    video = meta.get("video")
    if video and (run_dir / video).exists():
        _contact_sheet(run_dir / video, run_dir / "contact_sheet.png")
    return _collect(meta, run_dir)


def _write_summary(rows: list[dict], out_dir: Path) -> None:
    lines = ["# A/B eval summary", "",
             f"{len(rows)} runs · generated {datetime.now():%Y-%m-%d %H:%M}", ""]
    by_topic: dict[str, list[dict]] = {}
    for r in rows:
        by_topic.setdefault(str(r["topic_id"]), []).append(r)
    cols = ["mode", "success", "attempts", "total_tokens", "llm_seconds",
            "render_seconds", "render_strategy", "total_seconds", "video_duration_s",
            "overlap_pairs"]
    for topic_id in sorted(by_topic):
        lines.append(f"## {topic_id}")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * len(cols)) + "|")
        for r in by_topic[topic_id]:
            lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
        lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topics", default=None,
                        help="comma list of topic ids/names (default: all in eval/topics.toml)")
    parser.add_argument("--modes", default="a,b,c",
                        help="comma list; a=sectioned, b=fanout, c=freeform baseline")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="gpt-5.4-mini-deployment-db7e5")
    parser.add_argument("--quality", choices=["l", "m", "h", "k"], default="l")
    parser.add_argument("--render-strategy", choices=["sequential", "parallel"], default="parallel")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--scene-model", default=None)
    parser.add_argument("--escalate-model", default=None)
    parser.add_argument("--out-dir", default=None,
                        help="default: runs/_eval/<timestamp>")
    args = parser.parse_args()

    if args.topics:
        topic_ids = [t.strip() for t in args.topics.split(",") if t.strip()]
    else:
        topic_ids = [t.id for t in load_topics()]
    modes = [_resolve_mode(m.strip()) for m in args.modes.split(",") if m.strip()]

    runs_base = Path(os.environ.get("DVG_RUNS_DIR", "runs"))
    out_dir = Path(args.out_dir) if args.out_dir else runs_base / "_eval" / datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_runs = out_dir / "runs"
    eval_runs.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for topic_id in topic_ids:
        for mode in modes:
            for rep in range(args.repeats):
                print(f"[{topic_id} · {mode} · rep {rep + 1}/{args.repeats}]")
                row = _run_cell(topic_id, mode, args, eval_runs)
                if row is not None:
                    rows.append(row)

    if rows:
        fields = list(rows[0].keys())
        with (out_dir / "results.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        _write_summary(rows, out_dir)
    print(f"\nWrote {len(rows)} rows to {out_dir}")


if __name__ == "__main__":
    main()
