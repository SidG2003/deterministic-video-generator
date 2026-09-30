"""
Verify the knowledge base against the installed ManimCE.

    python -m dvg.kb.verify              # table + every technique snippet
    python -m dvg.kb.verify --render DIR # also render each snippet (low quality) +
                                         # a contact sheet per snippet, for visual review

A snippet passes when: its technique has every required field, the static API
check finds no errors, it runs to the end, and the overlap detector reports no
overlapping, clipped, edge or tiny text. Each snippet runs in its own subprocess
(fast mode, no frames rasterized) so one broken snippet can't affect the rest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .check import check_code
from .manimgl import verify_table
from .techniques import FIELDS, load_catalog


def _run_one(path: str, cls_name: str) -> None:
    """(subprocess) run one snippet class in fast mode under the overlap tracker."""
    import tempfile

    from manim import tempconfig

    from ..manim_patches import fast_forward
    from ..overlap import _FAST, track

    ns: dict = {}
    exec(compile(Path(path).read_text(), path, "exec"), ns)
    with tempfile.TemporaryDirectory() as media:
        with tempconfig({"quality": "low_quality", "media_dir": media, **_FAST}):
            with fast_forward(), track() as tracker:
                ns[cls_name]().render()
    print(json.dumps(tracker.report()))


def _render_one(path: str, cls_name: str, out_dir: Path) -> Path | None:
    cmd = [sys.executable, "-m", "manim", "-ql", "--disable_caching", "--media_dir", str(out_dir / "media"),
           "-o", f"{Path(path).stem}__{cls_name}", path, cls_name]
    subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    mp4 = next((out_dir / "media").glob(f"videos/**/{Path(path).stem}__{cls_name}.mp4"), None)
    if mp4 is None:
        return None
    sheet = out_dir / f"{Path(path).stem}__{cls_name}.png"
    frames = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(mp4)],
                            capture_output=True, text=True).stdout.strip()
    step = max(1, int(frames or 8) // 8)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vf",
                    f"select='not(mod(n\\,{step}))',scale=427:240,tile=4x2:padding=4:color=white",
                    "-frames:v", "1", str(sheet)])
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render", metavar="DIR", help="also render snippets + contact sheets into DIR")
    parser.add_argument("--one", nargs=2, metavar=("PATH", "CLASS"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one:
        _run_one(*args.one)
        return

    failures = 0
    problems = verify_table()
    print(f"manimgl->ManimCE table: {'OK' if not problems else 'PROBLEMS'}")
    for p in problems:
        print(f"  - {p}")
    failures += len(problems)

    catalog = load_catalog()
    print(f"\n{len(catalog)} techniques, {sum(len(t.snippets) for t in catalog)} snippets")
    for tech in catalog:
        missing = [f for f in FIELDS if not tech.fields.get(f)]
        findings = check_code(tech.path.read_text())
        errors = [f for f in findings if f.severity == "error"]
        print(f"\n{tech.slug} — {tech.title}" + (f"  MISSING FIELDS: {missing}" if missing else ""))
        failures += bool(missing) + len(errors)
        for f in errors:
            print(f"  api-check error: {f}")
        for f in findings:
            if f.severity == "warning":
                print(f"  api-check warning: {f}")
        for snip in tech.snippets:
            proc = subprocess.run([sys.executable, "-m", "dvg.kb.verify", "--one", str(tech.path), snip.name],
                                  capture_output=True, text=True, timeout=600)
            out = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
            if proc.returncode != 0 or not out:
                failures += 1
                err = (proc.stderr.strip().splitlines() or ["?"])[-1]
                print(f"  ✗ {snip.name} ({snip.lines} lines): CRASHED — {err[:140]}")
                continue
            r = json.loads(out[-1])
            bad = {k: r[k] for k in ("overlap_pairs", "clipped_texts", "edge_texts", "tiny_texts") if r[k]}
            failures += bool(bad)
            mark = "✓" if not bad else "✗"
            print(f"  {mark} {snip.name} ({snip.lines} lines): max texts {r['max_texts_on_screen']}"
                  + (f", ISSUES {bad}: {[i for i in r['issues'] if i['type'] != 'tiny'][:3]}" if bad else ""))
            if args.render:
                sheet = _render_one(str(tech.path), snip.name, Path(args.render))
                print(f"      rendered: {sheet or 'FAILED'}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
