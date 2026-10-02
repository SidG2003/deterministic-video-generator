"""
Post-hoc narration: turn a run's NARRATION lines into a voice track and mux it
over the already-rendered (silent) video.

This is deliberately decoupled from rendering — the video is produced exactly as
before, then an optional pass adds audio — so it works identically for every
generation path (baseline / sectioned / fan-out) from the `narration.json` each
one already writes, and it never affects the parallel render engine.

v1 is a CONTINUOUS voice track: each narration line is synthesised, the lines are
joined with a short pause, and the result is muxed over the video with the video
as the master length. This gives roughly scene-level timing when the spoken pace
tracks the animation; true per-section alignment (placing each line at its
section's start) is a follow-up that needs per-section durations.

TTS is pluggable behind `synthesize_line`; v1 ships the local macOS `say` backend
(zero setup, offline) so the pipeline can be proven before wiring a cloud voice.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

PROVIDER = "macos-say"
_GAP_MS = 350  # silence between lines


class NarrationError(RuntimeError):
    pass


def _probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return round(float(proc.stdout.strip()), 3)
    except ValueError:
        return 0.0


def synthesize_line(text: str, out_path: Path, voice: str | None = None) -> float:
    """Synthesise one line to an audio file with macOS `say`; return its duration.
    Swap this function (or dispatch on a backend arg) to add other TTS providers."""
    if shutil.which("say") is None:
        raise NarrationError("macOS `say` not found — this backend needs macOS")
    cmd = ["say", "-o", str(out_path)]
    if voice:
        cmd += ["-v", voice]
    cmd += ["--", text]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.exists():
        raise NarrationError(f"`say` failed: {(proc.stderr or '').strip()[-300:]}")
    return _probe_duration(out_path)


def build_voice_track(lines: list[str], out_wav: Path, voice: str | None = None,
                      gap_ms: int = _GAP_MS) -> dict:
    """Synthesise every line and join them (with a short pause) into one wav.
    Returns {provider, voice, lines, line_seconds, audio_seconds}."""
    from pydub import AudioSegment

    spoken = [ln for ln in lines if ln and ln.strip()]
    if not spoken:
        raise NarrationError("no narration lines to synthesise")

    tmp = Path(tempfile.mkdtemp(prefix="dvg_tts_"))
    track = AudioSegment.silent(duration=0)
    gap = AudioSegment.silent(duration=gap_ms)
    line_seconds = []
    for i, line in enumerate(spoken):
        clip_path = tmp / f"line_{i:03d}.aiff"
        dur = synthesize_line(line, clip_path, voice)
        line_seconds.append(dur)
        track += AudioSegment.from_file(clip_path)
        if i < len(spoken) - 1:
            track += gap
    track.export(out_wav, format="wav")
    return {"provider": PROVIDER, "voice": voice, "lines": len(spoken),
            "line_seconds": line_seconds, "audio_seconds": _probe_duration(out_wav)}


def mux(video_in: Path, audio_wav: Path, video_out: Path) -> None:
    """Add the voice track to the video (copy video stream, video is master)."""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_in), "-i", str(audio_wav),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
         "-shortest", str(video_out)], capture_output=True, text=True)
    if proc.returncode != 0 or not video_out.exists():
        raise NarrationError(f"muxing audio failed: {(proc.stderr or '').strip()[-400:]}")


def narrate(video_in: str | Path, lines: list[str], video_out: str | Path,
            voice: str | None = None) -> dict:
    """Synthesise `lines`, mux over `video_in`, write `video_out`. Returns the
    voice-track info plus the output path and final (video-master) duration."""
    video_in, video_out = Path(video_in), Path(video_out)
    tmp = Path(tempfile.mkdtemp(prefix="dvg_narr_"))
    wav = tmp / "voice.wav"
    info = build_voice_track(lines, wav, voice)
    mux(video_in, wav, video_out)
    info["video_out"] = str(video_out)
    info["video_seconds"] = _probe_duration(video_out)
    return info


def apply_if_requested(args, logger) -> None:
    """If --narrate was passed, read the run's narration.json, build a voice track,
    and write video_narrated.mp4 into the run dir. No-op otherwise. Never fails the
    run — a TTS error is reported and the silent video is kept."""
    if not getattr(args, "narrate", False):
        return
    narration_path = logger.dir / "narration.json"
    video = logger.dir / "video.mp4"
    if not narration_path.exists() or not video.exists():
        print("narration skipped: no narration.json or video.mp4 in the run dir")
        return
    try:
        lines = json.loads(narration_path.read_text())
        out = logger.dir / "video_narrated.mp4"
        info = narrate(video, lines, out, voice=getattr(args, "voice", None))
        logger.artifact("narration_audio.json", json.dumps(info, indent=2) + "\n")
        print(f"Narrated video: {out}  ({info['lines']} lines, "
              f"{info['audio_seconds']}s of audio over {info['video_seconds']}s video)")
    except NarrationError as exc:
        print(f"narration skipped: {exc}")
