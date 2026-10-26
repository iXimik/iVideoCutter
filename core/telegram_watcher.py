import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument

from core.cutter_service import cut_video_by_saved_settings
from core.video_splitter import safe_filename


PROJECT_DIR = Path(__file__).resolve().parents[1]

TELEGRAM_SETTINGS_FILE = PROJECT_DIR / "config" / "telegram_settings.json"
TELEGRAM_PROCESSED_FILE = PROJECT_DIR / "config" / "telegram_processed.json"
APP_SETTINGS_FILE = PROJECT_DIR / "config" / "settings.json"

LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "telegram_watcher.log"

MUSIC_MODE_RANDOM = "Случайный трек из папки"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("")
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        return

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, default)
        return default.copy()

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def load_telegram_settings() -> dict:
    return load_json(
        TELEGRAM_SETTINGS_FILE,
        {
            "api_id": 0,
            "api_hash": "",
            "session_name": "telegram_sessions/video_group_session",
            "chat_name_or_id": "",
            "local_download_dir": str(PROJECT_DIR / "input" / "telegram_downloaded"),
            "allowed_extensions": [".mp4", ".mov", ".mkv", ".avi"],
            "process_limit_per_run": 3,
            "require_caption": True,
            "mark_processed_messages": True,
        }
    )


def load_app_settings() -> dict:
    return load_json(
        APP_SETTINGS_FILE,
        {
            "output_dir": str(PROJECT_DIR / "output"),
            "telegram_output_dir": str(PROJECT_DIR / "output" / "telegram_priority"),
            "add_music": False,
            "music_mode": MUSIC_MODE_RANDOM,
            "music_dir": str(PROJECT_DIR / "input" / "music"),
            "selected_music_file": "",
        }
    )


def save_app_settings(settings: dict) -> None:
    save_json(APP_SETTINGS_FILE, settings)


def load_processed() -> dict:
    return load_json(
        TELEGRAM_PROCESSED_FILE,
        {
            "processed_message_ids": []
        }
    )


def save_processed(processed: dict) -> None:
    save_json(TELEGRAM_PROCESSED_FILE, processed)


def is_allowed_video_file(filename: str, allowed_extensions: list[str]) -> bool:
    suffix = Path(filename).suffix.lower()
    allowed = [ext.lower() for ext in allowed_extensions]
    return suffix in allowed


def get_document_filename(message) -> str | None:
    """
    Пытается получить оригинальное имя файла из Telegram-сообщения.
    """
    if not message or not message.file:
        return None

    if message.file.name:
        return message.file.name

    extension = message.file.ext or ".mp4"
    return f"telegram_video_{message.id}{extension}"


def has_video_media(message) -> bool:
    """
    Проверяет, есть ли в сообщении медиафайл.
    """
    if not message:
        return False

    if not message.media:
        return False

    if message.video:
        return True

    if isinstance(message.media, MessageMediaDocument):
        return True

    return False


def prepare_title_from_caption(caption: str) -> str:
    """
    Берёт первую непустую строку caption.
    """
    if not caption:
        return ""

    lines = caption.splitlines()

    for line in lines:
        text = line.strip()
        if text:
            return text

    return ""


def video_has_audio(video_path: str | Path) -> bool:
    """
    Проверяет не просто наличие аудиодорожки, а наличие слышимого звука.

    Почему так:
    некоторые ролики "без звука" технически всё равно содержат пустую
    AAC/MP3-аудиодорожку. Обычный ffprobe видит такую дорожку как audio,
    поэтому старая логика считала ролик звуковым и музыку не добавляла.

    True  = есть аудиодорожка и в ней найден слышимый уровень.
    False = аудиодорожки нет или дорожка есть, но она фактически тихая/пустая.
    """
    path = Path(video_path)

    # 1. Сначала проверяем, есть ли аудиопоток вообще.
    probe_command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    try:
        probe_result = subprocess.run(
            probe_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        probe_output = (probe_result.stdout or "").strip().lower()

        if "audio" not in probe_output:
            logging.info("В исходном Telegram-видео аудиопотока нет.")
            return False

    except Exception as error:
        logging.warning(
            f"Не удалось проверить аудиопоток через ffprobe: {error}. "
            "Считаю, что звук есть, чтобы не заменить важный оригинальный звук."
        )
        return True

    # 2. Аудиопоток есть. Теперь проверяем, есть ли в нём реальный уровень громкости.
    # Используем volumedetect. Это решает случай, когда в файле есть пустая/немая дорожка.
    volume_command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(path),
        "-vn",
        "-af", "volumedetect",
        "-f", "null",
        "-",
    ]

    try:
        volume_result = subprocess.run(
            volume_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )

        diagnostic_text = (volume_result.stderr or "") + "\n" + (volume_result.stdout or "")
        diagnostic_text = (volume_result.stderr or "") + "\n" + (volume_result.stdout or "")

        # Примеры строк ffmpeg:
        # max_volume: -12.3 dB
        # max_volume: -inf dB
        match = re.search(r"max_volume:\s*(-inf|[-+]?[0-9]*\.?[0-9]+)\s*dB", diagnostic_text)

        if not match:
            logging.warning(
                "Не удалось определить max_volume через ffmpeg volumedetect. "
                "Считаю, что звук есть, чтобы не заменить важный оригинальный звук."
            )
            return True

        value = match.group(1).strip().lower()

        if value == "-inf":
            logging.info("Аудиопоток есть, но он полностью тихий: max_volume = -inf dB.")
            return False

        max_volume = float(value)
        logging.info(f"Проверка громкости Telegram-видео: max_volume = {max_volume} dB")

        # Порог тишины. Если максимум ниже -55 dB, считаем ролик фактически немым.
        if max_volume <= -55.0:
            logging.info(
                "Аудиопоток есть, но уровень очень тихий. "
                "Считаю ролик видео без слышимого звука."
            )
            return False

        return True

    except Exception as error:
        logging.warning(
            f"Не удалось проверить громкость через ffmpeg volumedetect: {error}. "
            "Считаю, что звук есть, чтобы не заменить важный оригинальный звук."
        )
        return True


def folder_has_music_files(music_dir: str | Path) -> bool:
    folder = Path(music_dir)

    if not folder.exists() or not folder.is_dir():
        return False

    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            return True

    return False


def cut_telegram_video_by_audio_rule(
    input_path: str | Path,
    title_text: str,
    progress_callback,
) -> list:
    """
    Нарезка Telegram-видео с отдельной аудиологикой:

    - если в исходном Telegram-видео есть звук:
      сохраняем оригинальный звук и НЕ добавляем музыку,
      даже если чекбокс "Добавлять музыку" включён в интерфейсе;

    - если в исходном Telegram-видео нет звука:
      автоматически включаем случайную музыку из папки music_dir,
      даже если чекбокс "Добавлять музыку" выключен.

    Чтобы не менять постоянные настройки пользователя, временно меняем
    config/settings.json только на время вызова cut_video_by_saved_settings,
    затем возвращаем исходные настройки обратно.
    """
    input_path = Path(input_path)

    original_settings = load_app_settings()
    temporary_settings = original_settings.copy()

    # Telegram-видео должны попадать в отдельную приоритетную папку.
    # Потом YouTube/VK uploader будут забирать её первой.
    telegram_output_dir = str(
        temporary_settings.get("telegram_output_dir", "")
    ).strip()

    if telegram_output_dir:
        telegram_output_path = Path(telegram_output_dir)
    else:
        telegram_output_path = PROJECT_DIR / "output" / "telegram_priority"
        temporary_settings["telegram_output_dir"] = str(telegram_output_path)

    telegram_output_path.mkdir(parents=True, exist_ok=True)
    temporary_settings["output_dir"] = str(telegram_output_path)

    logging.info(
        f"Telegram-режим: папка приоритетной нарезки — {telegram_output_path}"
    )

    has_audio = video_has_audio(input_path)

    if has_audio:
        logging.info("В исходном Telegram-видео есть слышимый оригинальный звук.")
        logging.info(
            "Telegram-режим: сохраняю оригинальный звук. "
            "Музыку не добавляю, даже если чекбокс включён."
        )

        temporary_settings["add_music"] = False

    else:
        logging.info("В исходном Telegram-видео нет слышимого звука.")
        logging.info(
            "Telegram-режим: автоматически добавляю случайную музыку "
            "из папки музыки, даже если чекбокс выключен."
        )

        music_dir = temporary_settings.get(
            "music_dir",
            str(PROJECT_DIR / "input" / "music"),
        )

        if folder_has_music_files(music_dir):
            temporary_settings["add_music"] = True
            temporary_settings["music_mode"] = MUSIC_MODE_RANDOM
            temporary_settings["selected_music_file"] = ""
            temporary_settings["music_dir"] = music_dir
            logging.info(f"Папка случайной музыки: {music_dir}")
        else:
            temporary_settings["add_music"] = False
            logging.warning(
                f"В папке музыки нет аудиофайлов: {music_dir}. "
                "Видео будет обработано без музыки."
            )

    try:
        save_app_settings(temporary_settings)

        created_files = cut_video_by_saved_settings(
            input_path=str(input_path),
            title_text=title_text,
            progress_callback=progress_callback,
        )

        return created_files

    finally:
        save_app_settings(original_settings)
        logging.info("Настройки музыки после Telegram-обработки восстановлены.")


async def run_telegram_once_async():
    setup_logger()

    logging.info("========================================")
    logging.info("Старт проверки Telegram-группы.")

    settings = load_telegram_settings()
    processed = load_processed()

    processed_ids = set(processed.get("processed_message_ids", []))

    api_id = int(settings["api_id"])
    api_hash = settings["api_hash"]
    session_name = settings.get(
        "session_name",
        "telegram_sessions/video_group_session"
    )

    chat_name_or_id = settings["chat_name_or_id"]

    local_download_dir = Path(
        settings.get(
            "local_download_dir",
            str(PROJECT_DIR / "input" / "telegram_downloaded")
        )
    )
    local_download_dir.mkdir(parents=True, exist_ok=True)

    allowed_extensions = settings.get(
        "allowed_extensions",
        [".mp4", ".mov", ".mkv", ".avi"]
    )

    process_limit_per_run = int(
        settings.get("process_limit_per_run", 3)
    )

    require_caption = bool(
        settings.get("require_caption", True)
    )

    mark_processed_messages = bool(
        settings.get("mark_processed_messages", True)
    )

    if not api_id or not api_hash:
        raise RuntimeError(
            "В config/telegram_settings.json не заполнены api_id или api_hash."
        )

    if not chat_name_or_id:
        raise RuntimeError(
            "В config/telegram_settings.json не заполнен chat_name_or_id."
        )

    client = TelegramClient(session_name, api_id, api_hash)

    try:
        await client.start()

        logging.info("Telegram-клиент запущен.")

        try:
            entity = await client.get_entity(chat_name_or_id)
        except Exception as error:
            raise RuntimeError(
                f"Не удалось найти чат/группу: {chat_name_or_id}\n"
                f"Проверь название группы или укажи ID.\n\n{error}"
            )

        logging.info(f"Группа найдена: {chat_name_or_id}")

        processed_count = 0

        # Берём последние 50 сообщений, старые обрабатываем первыми.
        messages = []

        async for message in client.iter_messages(entity, limit=50):
            messages.append(message)

        messages.reverse()

        for message in messages:
            message_id = message.id

            if message_id in processed_ids:
                continue

            if not has_video_media(message):
                continue

            original_filename = get_document_filename(message)

            if not original_filename:
                logging.info(f"Сообщение {message_id}: не удалось определить имя файла.")
                continue

            if not is_allowed_video_file(original_filename, allowed_extensions):
                logging.info(
                    f"Сообщение {message_id}: расширение не подходит: {original_filename}"
                )
                continue

            caption = message.message or ""
            title_text = prepare_title_from_caption(caption)

            if require_caption and not title_text:
                logging.info(
                    f"Сообщение {message_id}: видео найдено, но caption пустой. Пропускаю."
                )
                continue

            if not title_text:
                title_text = Path(original_filename).stem

            safe_title = safe_filename(title_text)
            extension = Path(original_filename).suffix.lower()

            local_video_path = local_download_dir / f"{safe_title}{extension}"

            logging.info("----------------------------------------")
            logging.info(f"Сообщение: {message_id}")
            logging.info(f"Оригинальный файл: {original_filename}")
            logging.info(f"Caption / название: {title_text}")
            logging.info(f"Скачиваю в: {local_video_path}")

            await client.download_media(
                message,
                file=str(local_video_path)
            )

            if not local_video_path.exists() or local_video_path.stat().st_size == 0:
                raise RuntimeError(
                    f"Файл не был скачан или пустой: {local_video_path}"
                )

            logging.info(
                f"Файл скачан. Размер: {local_video_path.stat().st_size} байт"
            )

            logging.info("Запускаю нарезку по сохранённым настройкам.")

            created_files = cut_telegram_video_by_audio_rule(
                input_path=local_video_path,
                title_text=title_text,
                progress_callback=lambda msg: logging.info(msg),
            )

            logging.info(f"Нарезка завершена. Создано файлов: {len(created_files)}")

            if mark_processed_messages:
                processed_ids.add(message_id)
                processed["processed_message_ids"] = sorted(list(processed_ids))
                save_processed(processed)

            processed_count += 1

            if processed_count >= process_limit_per_run:
                logging.info(
                    f"Достигнут лимит обработки за запуск: {process_limit_per_run}"
                )
                break

        if processed_count == 0:
            logging.info("Новых подходящих видео в Telegram-группе нет.")

        logging.info("Проверка Telegram-группы завершена.")

    finally:
        await client.disconnect()
        logging.info("Telegram-клиент отключён.")


def run_telegram_once():
    asyncio.run(run_telegram_once_async())


if __name__ == "__main__":
    run_telegram_once()