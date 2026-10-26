"""Tests use temporary video placeholders; no APIs or real media involved."""
from pathlib import Path

from core.upload_queue import (
    get_mixed_upload_batch,
    mark_file_uploaded,
)
from core.video_splitter import _make_part_number_text


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mock-video")


def test_telegram_priority_preempts_other_series(tmp_path):
    output = tmp_path / "output"
    ordinary = output / "output_1" / "Game e.001.mp4"
    telegram = output / "telegram_priority" / "Latest.mp4"
    _touch(ordinary)
    _touch(telegram)
    state = tmp_path / "youtube.json"

    batch = get_mixed_upload_batch(output, state, limit=3, seed=42)
    assert batch == [telegram]

    mark_file_uploaded(state, telegram, output)
    next_batch = get_mixed_upload_batch(output, state, limit=3, seed=42)
    assert next_batch == [ordinary]


def test_platform_states_are_independent(tmp_path):
    output = tmp_path / "output"
    telegram = output / "telegram_priority" / "Latest.mp4"
    _touch(telegram)

    youtube_state = tmp_path / "youtube.json"
    vk_state = tmp_path / "vk.json"
    assert get_mixed_upload_batch(output, youtube_state) == [telegram]
    mark_file_uploaded(youtube_state, telegram, output)
    assert get_mixed_upload_batch(output, youtube_state) == []
    assert get_mixed_upload_batch(output, vk_state) == [telegram]


def test_number_padding():
    assert _make_part_number_text(1, 312) == "001"
    assert _make_part_number_text(11, 312) == "011"
    assert _make_part_number_text(111, 312) == "111"
    assert _make_part_number_text(1200, 1200) == "1200"
