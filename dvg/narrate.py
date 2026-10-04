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
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

_GAP_MS = 350  # silence between lines

# Firefly 3p-audio (ElevenLabs) defaults — the endpoint/key/bearer come from .env.
_FIREFLY_MODEL = "eleven_multilingual_v2"
_FIREFLY_VOICE_DEFAULT = "onwK4e9ZLuTAKqWW03F9"
_FIREFLY_HEADERS_EXTRA = {"accept": "*/*", "origin": "https://firefly.adobe.com",
                          "referer": "https://firefly.adobe.com/"}


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


def synthesize_line(text: str, out_path: Path, voice: str | None = None,
                    backend: str = "say", speed: float = 1.0) -> dict:
    """Synthesise one narration line to `out_path`; return a dict with at least
    {seconds, chars, model, voice}. `backend` selects the TTS provider ("say" =
    local macOS, "firefly" = Adobe Firefly 3p ElevenLabs). `speed` is the ElevenLabs
    speaking-rate multiplier (0.7-1.2; 1.0 = default), ignored by `say`. TTS is not
    token-billed, so `chars` (what ElevenLabs charges on) is the usage recorded."""
    if backend == "say":
        return _say_line(text, out_path, voice)
    if backend == "firefly":
        return _firefly_line(text, out_path, voice, speed=speed)
    raise NarrationError(f"unknown TTS backend {backend!r} (use 'say' or 'firefly')")


def _say_line(text: str, out_path: Path, voice: str | None) -> dict:
    if shutil.which("say") is None:
        raise NarrationError("macOS `say` not found — this backend needs macOS")
    cmd = ["say", "-o", str(out_path)]
    if voice:
        cmd += ["-v", voice]
    cmd += ["--", text]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.exists():
        raise NarrationError(f"`say` failed: {(proc.stderr or '').strip()[-300:]}")
    return {"seconds": _probe_duration(out_path), "chars": len(text),
            "model": "macos-say", "voice": voice}


def _firefly_line(text: str, out_path: Path, voice: str | None, timeout: int = 180,
                  speed: float = 1.0) -> dict:
    """Firefly 3p-audio ElevenLabs TTS: POST the line, poll the async job until it
    completes, download the resulting WAV. Credentials come from the environment
    (loaded from .env) and are never logged."""
    endpoint = os.environ.get("FIREFLY_AUDIO_ENDPOINT")
    api_key = os.environ.get("FIREFLY_API_KEY")
    bearer = os.environ.get("FIREFLY_BEARER")
    if not (endpoint and api_key and bearer):
        raise NarrationError("set FIREFLY_AUDIO_ENDPOINT / FIREFLY_API_KEY / FIREFLY_BEARER "
                             "in .env to use the firefly backend")
    voice = voice or os.environ.get("FIREFLY_VOICE_ID") or _FIREFLY_VOICE_DEFAULT
    headers = {"Authorization": f"Bearer {bearer}", "x-api-key": api_key,
               "content-type": "application/json", **_FIREFLY_HEADERS_EXTRA}
    body = {
        "modelId": "elevenlabs", "modelVersion": _FIREFLY_MODEL,
        "prompt": f"<speak><p>{escape(text)}</p></speak>", "seeds": [1], "voiceId": voice,
        "modelSpecificPayload": {"voice_settings": {"use_speaker_boost": True,
            "similarity_boost": 0.75, "style": 0,
            "speed": max(0.7, min(1.2, float(speed))), "stability": 0.5}},
        "output": {"storeInputs": True, "includeAlignment": "none",
                   "audioConfig": [{"codec": "pcm", "sampleRateHz": 48000}]},
        "generationMetadata": {"module": "textToSpeech", "assetName": text[:40]},
    }

    def _get(url, data=None):
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data else "GET")
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            return resp, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise NarrationError("Firefly auth failed (401) — FIREFLY_BEARER has likely "
                                     "expired; paste a fresh token into .env")
            raise NarrationError(f"Firefly request failed ({e.code}): {e.read().decode()[:200]}")

    resp, posted = _get(endpoint, json.dumps(body).encode())
    retry = int(resp.headers.get("Retry-After") or 5)
    # No token/credit COUNT is returned (TTS is not token-billed); only a quota
    # status + credit type. Record what is available for the log.
    import re
    consumption = resp.headers.get("x-access-consumption-status")
    credit_match = re.search(r'gc_credit_type="([^"]+)"',
                             resp.headers.get("x-access-user-context", "") or "")
    credit_type = credit_match.group(1) if credit_match else None
    poll = (resp.headers.get("X-Override-Status-Link")
            or posted.get("links", {}).get("result", {}).get("href"))
    if not poll:
        raise NarrationError("Firefly response had no status link to poll")

    deadline = time.time() + timeout
    result = None
    while time.time() < deadline:
        pres, pj = _get(poll)
        status = (pj.get("status") or pres.headers.get("X-Task-Status") or "").upper()
        if status == "COMPLETED" or pj.get("outputs"):
            result = pj
            break
        if status in ("FAILED", "ERROR", "CANCELLED"):
            raise NarrationError(f"Firefly job {status}")
        time.sleep(retry)
    if result is None:
        raise NarrationError(f"Firefly job did not finish within {timeout}s")

    outputs = result.get("outputs") or []
    url = outputs[0].get("audio", {}).get("presignedUrl") if outputs else None
    if not url:
        raise NarrationError("Firefly result had no audio URL")
    out_path.write_bytes(urllib.request.urlopen(url, timeout=120).read())
    return {"seconds": _probe_duration(out_path), "chars": len(text),
            "model": result.get("modelId", "elevenlabs"),
            "model_version": result.get("modelVersion", _FIREFLY_MODEL),
            "voice_id": voice, "session_id": result.get("generationSessionId"),
            "consumption_status": consumption, "credit_type": credit_type}


def build_voice_track(lines: list[str], out_wav: Path, voice: str | None = None,
                      backend: str = "say", gap_ms: int = _GAP_MS,
                      speed: float = 1.0) -> dict:
    """Synthesise every line with `backend` and join them (with a short pause) into
    one wav. Returns {provider, voice, lines, line_seconds, audio_seconds}."""
    from pydub import AudioSegment

    spoken = [ln for ln in lines if ln and ln.strip()]
    if not spoken:
        raise NarrationError("no narration lines to synthesise")

    suffix = ".wav" if backend == "firefly" else ".aiff"
    tmp = Path(tempfile.mkdtemp(prefix="dvg_tts_"))
    track = AudioSegment.silent(duration=0)
    gap = AudioSegment.silent(duration=gap_ms)
    results: list[dict] = []
    for i, line in enumerate(spoken):
        clip_path = tmp / f"line_{i:03d}{suffix}"
        results.append(synthesize_line(line, clip_path, voice, backend, speed))
        track += AudioSegment.from_file(clip_path)
        if i < len(spoken) - 1:
            track += gap
    track.export(out_wav, format="wav")
    first = results[0] if results else {}
    info = {
        "provider": backend,
        "model": first.get("model"),            # e.g. "elevenlabs" / "macos-say"
        "model_version": first.get("model_version"),  # e.g. "eleven_multilingual_v2"
        "voice": first.get("voice_id") or voice,
        "lines": len(spoken),
        "characters": sum(r["chars"] for r in results),  # TTS is billed per character, not tokens
        "chars_per_line": [r["chars"] for r in results],
        "line_seconds": [r["seconds"] for r in results],
        "audio_seconds": _probe_duration(out_wav),
    }
    # Firefly only reports a quota status + credit type, never a consumed count.
    if first.get("consumption_status"):
        info["consumption_status"] = first["consumption_status"]
        info["credit_type"] = first.get("credit_type")
        info["session_ids"] = [r.get("session_id") for r in results]
    return info


def mux(video_in: Path, audio_wav: Path, video_out: Path) -> None:
    """Add the voice track to the video (copy video stream, video is master)."""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_in), "-i", str(audio_wav),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
         "-shortest", str(video_out)], capture_output=True, text=True)
    if proc.returncode != 0 or not video_out.exists():
        raise NarrationError(f"muxing audio failed: {(proc.stderr or '').strip()[-400:]}")


def narrate(video_in: str | Path, lines: list[str], video_out: str | Path,
            voice: str | None = None, backend: str = "say", speed: float = 1.0) -> dict:
    """Synthesise `lines` with `backend`, mux over `video_in`, write `video_out`.
    Returns the voice-track info plus the output path and final duration."""
    video_in, video_out = Path(video_in), Path(video_out)
    tmp = Path(tempfile.mkdtemp(prefix="dvg_narr_"))
    wav = tmp / "voice.wav"
    t0 = time.perf_counter()
    info = build_voice_track(lines, wav, voice, backend, speed=speed)
    info["speed"] = max(0.7, min(1.2, float(speed)))
    info["tts_seconds"] = round(time.perf_counter() - t0, 2)  # wall time to synthesize the voice
    t1 = time.perf_counter()
    mux(video_in, wav, video_out)
    info["mux_seconds"] = round(time.perf_counter() - t1, 2)
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
    backend = getattr(args, "tts", "say")
    try:
        lines = json.loads(narration_path.read_text())
        out = logger.dir / "video_narrated.mp4"
        info = narrate(video, lines, out, voice=getattr(args, "voice", None), backend=backend)
        logger.artifact("narration_audio.json", json.dumps(info, indent=2) + "\n")
        # Compact summary into meta.json for KPI reading (backend, model, usage, time).
        logger.narration = {
            "provider": info["provider"], "model": info.get("model"),
            "model_version": info.get("model_version"), "voice": info.get("voice"),
            "lines": info["lines"], "characters": info.get("characters"),
            "tts_seconds": info["tts_seconds"], "audio_seconds": info["audio_seconds"],
            "video_seconds": info["video_seconds"],
            **({"consumption_status": info["consumption_status"],
                "credit_type": info.get("credit_type")}
               if "consumption_status" in info else {}),
        }
        print(f"Narrated video ({backend}): {out}  ({info['lines']} lines, "
              f"{info['audio_seconds']}s of audio in {info['tts_seconds']}s over "
              f"{info['video_seconds']}s video)")
    except NarrationError as exc:
        print(f"narration skipped: {exc}")
