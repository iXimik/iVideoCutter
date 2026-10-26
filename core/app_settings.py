"""Application configuration. Local JSON is ignored by Git."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
EXAMPLE_FILE = CONFIG_DIR / "settings.example.json"

DEFAULT_SETTINGS = {
    "mode": "9:16 — вставить в вертикальный кадр, цветной фон",
    "part_duration": 60,
    "output_dir": str(PROJECT_DIR / "output"),
    "telegram_output_dir": str(PROJECT_DIR / "output" / "telegram_priority"),
    "background_color_name": "Серый",
    "add_title": True,
    "title_font_size": 36,
    "font_family": "Arial",
    "title_color_name": "Жёлтый",
    "bold": True,
    "italic": False,
    "underline": False,
    "add_music": False,
    "music_mode": "Случайный трек из папки",
    "music_dir": str(PROJECT_DIR / "input" / "music"),
    "selected_music_file": "",
}


def get_settings_file_path() -> str:
    return str(SETTINGS_FILE)


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=str(path.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=4)
            stream.write("\n")
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def load_settings() -> dict:
    """Load saved settings with defaults. Create local config if absent."""
    defaults = DEFAULT_SETTINGS.copy()
    if EXAMPLE_FILE.is_file():
        with EXAMPLE_FILE.open("r", encoding="utf-8") as stream:
            example = json.load(stream)
        if not isinstance(example, dict):
            raise ValueError("config/settings.example.json must contain an object")
        defaults.update(example)

    if not SETTINGS_FILE.exists():
        _atomic_write_json(SETTINGS_FILE, defaults)
        return defaults

    with SETTINGS_FILE.open("r", encoding="utf-8") as stream:
        saved = json.load(stream)
    if not isinstance(saved, dict):
        raise ValueError("config/settings.json must contain an object")

    defaults.update(saved)
    return defaults


def save_settings(settings: dict) -> None:
    """Merge updates to preserve keys not present in the GUI, e.g. Telegram output dir."""
    data = load_settings()
    data.update(settings)
    _atomic_write_json(SETTINGS_FILE, data)
