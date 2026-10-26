"""FFprobe adapter exposing fields expected by the GUI."""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    codec: str
    duration: float
    duration_text: str
    aspect_ratio: str


def get_video_info(file_path: str) -> VideoInfo:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name:format=duration",
        "-of", "json", str(file_path),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe error: {result.stderr.strip()}")

    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if not streams:
        raise ValueError("No video stream found")

    stream = streams[0]
    width = int(stream["width"])
    height = int(stream["height"])
    if not width or not height:
        raise ValueError("Invalid video dimensions")

    duration = float(payload.get("format", {}).get("duration", 0) or 0)
    total_seconds = max(0, int(duration))
    hours, minutes_and_seconds = divmod(total_seconds, 3600)
    minutes, seconds = divmod(minutes_and_seconds, 60)
    duration_text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    divisor = math.gcd(width, height)
    aspect_ratio = f"{width // divisor}:{height // divisor}"

    return VideoInfo(
        width=width, height=height,
        codec=str(stream.get("codec_name", "unknown")),
        duration=duration, duration_text=duration_text,
        aspect_ratio=aspect_ratio,
    )
