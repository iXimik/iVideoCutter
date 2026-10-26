"""Lightweight FFmpeg availability checks for the UI."""
import shutil
import subprocess


def _is_available(name: str) -> bool:
    path = shutil.which(name)
    if not path:
        return False
    try:
        probe = subprocess.run(
            [path, "-version"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def check_ffmpeg() -> bool:
    return _is_available("ffmpeg")


def check_ffprobe() -> bool:
    return _is_available("ffprobe")
