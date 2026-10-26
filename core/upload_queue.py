"""
core/upload_queue.py

Умная очередь загрузки MP4 из подпапок output.

Задача:
- папка = отдельная серия / игра;
- имя файла может быть любым;
- порядок внутри папки не определяется по названию;
- при первом сканировании порядок фиксируется в state JSON;
- между папками файлы выбираются случайно;
- один и тот же файл повторно не выбирается после отметки uploaded.

Пример:
    from core.upload_queue import get_mixed_upload_batch, mark_files_uploaded

    batch = get_mixed_upload_batch(
        output_dir="output",
        state_file="logs/youtube_upload_queue_state.json",
        limit=3,
        max_per_series_per_run=2,
    )

    for file_path in batch:
        upload(file_path)

    mark_files_uploaded(
        state_file="logs/youtube_upload_queue_state.json",
        uploaded_files=batch,
        output_dir="output",
    )
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
}


# Папки с повышенным приоритетом.
# Если в этих папках есть незагруженные ролики, YouTube/VK uploader
# сначала берёт их, а уже потом возвращается к обычной случайной очереди.
PRIORITY_SERIES_NAMES = {
    "telegram_priority",
}


@dataclass(frozen=True)
class QueueItem:
    """
    Один файл в очереди.

    series_name:
        Название серии. Обычно это имя подпапки в output.

    path:
        Абсолютный путь к видеофайлу.
    """
    series_name: str
    path: Path


def _normalize_relative_path(path: Path, base_dir: Path) -> str:
    """
    Возвращает относительный путь в стабильном виде для JSON.
    Используем прямой слеш, чтобы файл состояния был переносимее.
    """
    return path.resolve().relative_to(base_dir.resolve()).as_posix()


def _load_state(state_file: str | Path) -> dict:
    state_path = Path(state_file)

    if not state_path.exists():
        return {
            "version": 1,
            "series_order": {},
            "uploaded_files": [],
        }

    try:
        with state_path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except json.JSONDecodeError:
        backup_path = state_path.with_suffix(state_path.suffix + ".broken")
        state_path.replace(backup_path)
        return {
            "version": 1,
            "series_order": {},
            "uploaded_files": [],
            "warning": f"Broken state moved to {backup_path}",
        }

    if "series_order" not in state or not isinstance(state["series_order"], dict):
        state["series_order"] = {}

    if "uploaded_files" not in state or not isinstance(state["uploaded_files"], list):
        state["uploaded_files"] = []

    state.setdefault("version", 1)
    return state


def _save_state(state_file: str | Path, state: dict) -> None:
    state_path = Path(state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with state_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=4)


def _get_series_dirs(output_dir: Path) -> list[Path]:
    """
    Возвращает подпапки первого уровня.

    Важно:
    - каждая подпапка считается отдельной серией / игрой;
    - файлы прямо в output тоже можно обработать через служебную серию "_root".
    """
    if not output_dir.exists():
        return []

    return sorted(
        [path for path in output_dir.iterdir() if path.is_dir()],
        key=lambda path: path.name.lower(),
    )


def _scan_video_files(folder: Path, extensions: set[str]) -> list[Path]:
    """
    Сканирует видеофайлы только внутри конкретной папки серии.

    Порядок первичного добавления:
    1. время изменения файла;
    2. имя файла как запасной вариант.

    Название не используется для извлечения номера эпизода.
    """
    if not folder.exists():
        return []

    files = [
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    ]

    return sorted(
        files,
        key=lambda path: (path.stat().st_mtime, path.name.lower()),
    )


def _is_priority_series(series_name: str) -> bool:
    """
    Проверяет, является ли серия приоритетной.

    Сейчас это папка:
        output/telegram_priority

    Имя сравниваем без учёта регистра, чтобы Windows-регистр
    не влиял на работу очереди.
    """
    return str(series_name or "").strip().lower() in PRIORITY_SERIES_NAMES


def _get_priority_pending_items(
    output_dir: Path,
    series_order: dict[str, list[str]],
    uploaded_set: set[str],
    limit: int,
) -> list[QueueItem]:
    """
    Возвращает незагруженные файлы из приоритетных папок.

    Важное правило:
    если есть хотя бы один файл в telegram_priority, обычная рандомная
    очередь временно не используется. Сначала загружаем Telegram.
    """
    result: list[QueueItem] = []

    priority_series_names = [
        series_name
        for series_name in series_order.keys()
        if _is_priority_series(series_name)
    ]

    # Держим стабильный порядок приоритетных папок.
    priority_series_names.sort(key=lambda name: name.lower())

    for series_name in priority_series_names:
        if len(result) >= limit:
            break

        rel_paths = series_order.get(series_name, [])

        next_items = _get_next_unuploaded_for_series(
            output_dir=output_dir,
            series_name=series_name,
            rel_paths=rel_paths,
            uploaded_set=uploaded_set,
            count=limit - len(result),
        )

        for item in next_items:
            result.append(item)
            uploaded_set.add(_normalize_relative_path(item.path, output_dir))

            if len(result) >= limit:
                break

    return result


def _build_or_update_series_order(
    output_dir: Path,
    state: dict,
    extensions: set[str],
    include_root_files: bool,
) -> None:
    """
    Обновляет state["series_order"].

    Главное:
    - если файл уже был в очереди, его позиция сохраняется;
    - новые файлы добавляются в конец очереди своей серии;
    - порядок не зависит от шаблона имени ролика.
    """
    series_order = state["series_order"]

    series_dirs = _get_series_dirs(output_dir)

    for series_dir in series_dirs:
        series_name = series_dir.name
        known_files = series_order.setdefault(series_name, [])

        current_files = _scan_video_files(series_dir, extensions)
        current_rel_paths = [
            _normalize_relative_path(path, output_dir)
            for path in current_files
        ]

        known_files = [
            rel_path for rel_path in known_files
            if rel_path in current_rel_paths
        ]

        for rel_path in current_rel_paths:
            if rel_path not in known_files:
                known_files.append(rel_path)

        series_order[series_name] = known_files

    if include_root_files:
        root_series_name = "_root"
        known_files = series_order.setdefault(root_series_name, [])

        current_files = _scan_video_files(output_dir, extensions)
        current_rel_paths = [
            _normalize_relative_path(path, output_dir)
            for path in current_files
        ]

        known_files = [
            rel_path for rel_path in known_files
            if rel_path in current_rel_paths
        ]

        for rel_path in current_rel_paths:
            if rel_path not in known_files:
                known_files.append(rel_path)

        if known_files:
            series_order[root_series_name] = known_files
        elif root_series_name in series_order:
            del series_order[root_series_name]

    existing_series_names = {path.name for path in series_dirs}
    if include_root_files:
        existing_series_names.add("_root")

    for series_name in list(series_order.keys()):
        if series_name not in existing_series_names:
            del series_order[series_name]


def _get_next_unuploaded_for_series(
    output_dir: Path,
    series_name: str,
    rel_paths: list[str],
    uploaded_set: set[str],
    count: int,
) -> list[QueueItem]:
    """
    Возвращает следующие не загруженные файлы конкретной серии.
    Порядок берётся из state["series_order"], а не из имени файла.
    """
    result: list[QueueItem] = []

    for rel_path in rel_paths:
        if rel_path in uploaded_set:
            continue

        absolute_path = output_dir / rel_path
        if not absolute_path.exists():
            continue

        result.append(
            QueueItem(
                series_name=series_name,
                path=absolute_path,
            )
        )

        if len(result) >= count:
            break

    return result


def get_mixed_upload_batch(
    output_dir: str | Path,
    state_file: str | Path,
    limit: int = 3,
    max_per_series_per_run: int = 2,
    extensions: Iterable[str] | None = None,
    include_root_files: bool = False,
    seed: int | None = None,
    save_state_after_scan: bool = True,
) -> list[Path]:
    """
    Возвращает пачку файлов для загрузки.

    Правила:
    - берёт видео из подпапок output;
    - каждая подпапка = серия / игра;
    - внутри серии сохраняет порядок из JSON-очереди;
    - между сериями выбирает случайно;
    - не выбирает файлы, отмеченные как uploaded;
    - не зависит от названия ролика.

    Для YouTube и VK лучше использовать разные state_file:
        logs/youtube_upload_queue_state.json
        logs/vk_upload_queue_state.json
    """
    if limit <= 0:
        return []

    if max_per_series_per_run <= 0:
        max_per_series_per_run = 1

    output_path = Path(output_dir)
    state = _load_state(state_file)

    allowed_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in (extensions or DEFAULT_VIDEO_EXTENSIONS)
    }

    _build_or_update_series_order(
        output_dir=output_path,
        state=state,
        extensions=allowed_extensions,
        include_root_files=include_root_files,
    )

    if save_state_after_scan:
        _save_state(state_file, state)

    uploaded_set = set(state.get("uploaded_files", []))
    series_order: dict[str, list[str]] = state.get("series_order", {})

    # Приоритет Telegram:
    # если в output/telegram_priority есть незагруженные файлы,
    # возвращаем их первыми и не трогаем обычную случайную очередь.
    priority_items = _get_priority_pending_items(
        output_dir=output_path,
        series_order=series_order,
        uploaded_set=set(uploaded_set),
        limit=limit,
    )

    if priority_items:
        return [item.path for item in priority_items]

    available_series: list[str] = []

    for series_name, rel_paths in series_order.items():
        next_items = _get_next_unuploaded_for_series(
            output_dir=output_path,
            series_name=series_name,
            rel_paths=rel_paths,
            uploaded_set=uploaded_set,
            count=1,
        )

        if next_items:
            available_series.append(series_name)

    if not available_series:
        return []

    rng = random.Random(seed)
    rng.shuffle(available_series)

    selected_items: list[QueueItem] = []

    # Первый проход: стараемся взять по одному ролику из разных серий.
    for series_name in available_series:
        if len(selected_items) >= limit:
            break

        rel_paths = series_order[series_name]
        next_items = _get_next_unuploaded_for_series(
            output_dir=output_path,
            series_name=series_name,
            rel_paths=rel_paths,
            uploaded_set=uploaded_set,
            count=1,
        )

        for item in next_items:
            selected_items.append(item)
            uploaded_set.add(_normalize_relative_path(item.path, output_path))

            if len(selected_items) >= limit:
                break

    # Второй проход: если лимит не добран, можно взять дополнительные ролики
    # из уже выбранных или других серий, но не больше max_per_series_per_run.
    while len(selected_items) < limit:
        progressed = False

        series_names = list(series_order.keys())
        rng.shuffle(series_names)

        counts_by_series: dict[str, int] = {}
        for item in selected_items:
            counts_by_series[item.series_name] = counts_by_series.get(item.series_name, 0) + 1

        for series_name in series_names:
            if len(selected_items) >= limit:
                break

            current_count = counts_by_series.get(series_name, 0)
            if current_count >= max_per_series_per_run:
                continue

            rel_paths = series_order[series_name]
            next_items = _get_next_unuploaded_for_series(
                output_dir=output_path,
                series_name=series_name,
                rel_paths=rel_paths,
                uploaded_set=uploaded_set,
                count=1,
            )

            if not next_items:
                continue

            item = next_items[0]
            selected_items.append(item)
            uploaded_set.add(_normalize_relative_path(item.path, output_path))
            counts_by_series[series_name] = current_count + 1
            progressed = True

        if not progressed:
            break

    return [item.path for item in selected_items]


def mark_files_uploaded(
    state_file: str | Path,
    uploaded_files: Iterable[str | Path],
    output_dir: str | Path,
) -> None:
    """
    Отмечает файлы как успешно загруженные.

    Важно:
    Вызывать только после успешной загрузки файла в YouTube/VK.
    """
    output_path = Path(output_dir)
    state = _load_state(state_file)

    uploaded_list = state.setdefault("uploaded_files", [])
    uploaded_set = set(uploaded_list)

    for file_path in uploaded_files:
        path = Path(file_path)

        try:
            rel_path = _normalize_relative_path(path, output_path)
        except ValueError:
            rel_path = path.resolve().as_posix()

        if rel_path not in uploaded_set:
            uploaded_list.append(rel_path)
            uploaded_set.add(rel_path)

    _save_state(state_file, state)


def mark_file_uploaded(
    state_file: str | Path,
    uploaded_file: str | Path,
    output_dir: str | Path,
) -> None:
    """
    Удобная обёртка для одного файла.
    """
    mark_files_uploaded(
        state_file=state_file,
        uploaded_files=[uploaded_file],
        output_dir=output_dir,
    )


def get_queue_summary(
    output_dir: str | Path,
    state_file: str | Path,
    extensions: Iterable[str] | None = None,
    include_root_files: bool = False,
) -> dict:
    """
    Возвращает сводку по очереди.
    Удобно для логов и диагностики.
    """
    output_path = Path(output_dir)
    state = _load_state(state_file)

    allowed_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in (extensions or DEFAULT_VIDEO_EXTENSIONS)
    }

    _build_or_update_series_order(
        output_dir=output_path,
        state=state,
        extensions=allowed_extensions,
        include_root_files=include_root_files,
    )

    _save_state(state_file, state)

    uploaded_set = set(state.get("uploaded_files", []))
    series_order: dict[str, list[str]] = state.get("series_order", {})

    summary = {
        "series_count": 0,
        "total_files": 0,
        "uploaded_files": 0,
        "pending_files": 0,
        "series": {},
    }

    for series_name, rel_paths in series_order.items():
        total = len(rel_paths)
        uploaded = sum(1 for rel_path in rel_paths if rel_path in uploaded_set)
        pending = total - uploaded

        summary["series"][series_name] = {
            "total": total,
            "uploaded": uploaded,
            "pending": pending,
        }

        summary["series_count"] += 1
        summary["total_files"] += total
        summary["uploaded_files"] += uploaded
        summary["pending_files"] += pending

    return summary


def print_test_batch(
    output_dir: str | Path = "output",
    state_file: str | Path = "logs/test_upload_queue_state.json",
    limit: int = 3,
    max_per_series_per_run: int = 2,
) -> None:
    """
    Быстрый ручной тест из PowerShell:

        python -m core.upload_queue

    Файлы не отмечаются загруженными.
    """
    summary = get_queue_summary(
        output_dir=output_dir,
        state_file=state_file,
    )

    print("Очередь загрузки:")
    print(f"Серий: {summary['series_count']}")
    print(f"Всего файлов: {summary['total_files']}")
    print(f"Загружено: {summary['uploaded_files']}")
    print(f"Ожидает: {summary['pending_files']}")
    print()

    batch = get_mixed_upload_batch(
        output_dir=output_dir,
        state_file=state_file,
        limit=limit,
        max_per_series_per_run=max_per_series_per_run,
    )

    print("Следующая пачка:")
    if not batch:
        print("Нет файлов для загрузки.")
        return

    for index, path in enumerate(batch, start=1):
        print(f"{index}. {path}")


if __name__ == "__main__":
    print_test_batch()