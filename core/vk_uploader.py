import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from core.upload_queue import (
    get_mixed_upload_batch,
    get_queue_summary,
    mark_file_uploaded,
    mark_files_uploaded,
)


PROJECT_DIR = Path.cwd()

VK_SETTINGS_FILE = PROJECT_DIR / "config" / "vk_settings.json"
VK_UPLOADED_FILE = PROJECT_DIR / "config" / "vk_uploaded.json"
VK_QUEUE_STATE_FILE = PROJECT_DIR / "logs" / "vk_upload_queue_state.json"

LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "vk_uploader.log"

VK_API_URL = "https://api.vk.com/method"


def setup_logger() -> None:
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


def load_json_file(path: Path, default_data: dict) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json_file(path, default_data)
        return default_data.copy()

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def load_vk_settings() -> dict:
    default_settings = {
        "output_dir": str(PROJECT_DIR / "output"),
        "access_token": "",
        "api_version": "5.199",
        "upload_to_group": False,
        "group_id": None,
        "privacy_view": "all",
        "privacy_comment": "all",
        "default_description": "#shorts",
        "upload_limit_per_run": 3,
        "delete_after_upload": False,
        "mixed_series_mode": True,
        "max_per_series_per_run": 2,
        "include_root_files": False,
        "upload_queue_state_file": str(VK_QUEUE_STATE_FILE)
    }

    settings = load_json_file(VK_SETTINGS_FILE, default_settings)

    merged = default_settings.copy()
    merged.update(settings)

    return merged


def load_uploaded_db() -> dict:
    return load_json_file(
        VK_UPLOADED_FILE,
        {
            "uploaded_files": [],
            "uploaded_records": []
        }
    )


def save_uploaded_db(data: dict) -> None:
    save_json_file(VK_UPLOADED_FILE, data)


def is_mp4_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".mp4"


def make_title_from_file(video_path: Path) -> str:
    title = video_path.stem.strip()

    if "#shorts" not in title.lower():
        title = f"{title} #shorts"

    return title


def make_description(video_path: Path, default_description: str) -> str:
    txt_path = video_path.with_suffix(".txt")

    if txt_path.exists():
        try:
            text = txt_path.read_text(encoding="utf-8-sig").strip()
            if text:
                return text
        except Exception as error:
            logging.warning(
                f"Не удалось прочитать описание из txt-файла {txt_path}: {error}"
            )

    return default_description


def get_video_files_for_upload(
    output_dir: Path,
    uploaded_files: list[str]
) -> list[Path]:
    if not output_dir.exists():
        return []

    uploaded_set = set(uploaded_files)

    files = []

    for file_path in sorted(output_dir.glob("*.mp4")):
        file_key = str(file_path.resolve())

        if file_key in uploaded_set:
            continue

        if is_mp4_file(file_path):
            files.append(file_path)

    return files


def vk_api_call(
    method: str,
    access_token: str,
    api_version: str,
    params: dict[str, Any] | None = None,
) -> dict:
    if params is None:
        params = {}

    url = f"{VK_API_URL}/{method}"

    payload = {
        **params,
        "access_token": access_token,
        "v": api_version,
    }

    response = requests.post(
        url,
        data=payload,
        timeout=60,
    )

    try:
        data = response.json()
    except Exception as error:
        raise RuntimeError(
            f"VK API вернул не JSON. HTTP {response.status_code}. "
            f"Текст ответа: {response.text[:500]}"
        ) from error

    if "error" in data:
        error_data = data["error"]
        error_code = error_data.get("error_code")
        error_msg = error_data.get("error_msg")

        raise RuntimeError(
            f"Ошибка VK API {error_code}: {error_msg}\n"
            f"Полный ответ: {json.dumps(data, ensure_ascii=False, indent=2)}"
        )

    return data.get("response", data)


def get_upload_url(
    access_token: str,
    api_version: str,
    title: str,
    description: str,
    upload_to_group: bool,
    group_id: int | None,
    privacy_view: str,
    privacy_comment: str,
) -> dict:
    params: dict[str, Any] = {
        "name": title,
        "description": description,
        "privacy_view": privacy_view,
        "privacy_comment": privacy_comment,
    }

    if upload_to_group:
        if not group_id:
            raise RuntimeError(
                "Включена загрузка в группу, но в config/vk_settings.json "
                "не указан group_id."
            )

        params["group_id"] = int(group_id)

    logging.info("Запрашиваю upload_url через VK video.save.")

    response = vk_api_call(
        method="video.save",
        access_token=access_token,
        api_version=api_version,
        params=params,
    )

    upload_url = response.get("upload_url")

    if not upload_url:
        raise RuntimeError(
            f"VK video.save не вернул upload_url. Ответ: {response}"
        )

    logging.info("VK upload_url получен.")

    return response


def upload_file_to_vk(upload_url: str, video_path: Path) -> dict:
    logging.info(f"Отправляю файл во VK: {video_path.name}")

    with open(video_path, "rb") as file:
        files = {
            "video_file": (
                video_path.name,
                file,
                "video/mp4",
            )
        }

        response = requests.post(
            upload_url,
            files=files,
            timeout=60 * 30,
        )

    try:
        data = response.json()
    except Exception as error:
        raise RuntimeError(
            f"VK upload_url вернул не JSON. HTTP {response.status_code}. "
            f"Текст ответа: {response.text[:500]}"
        ) from error

    if "error" in data:
        raise RuntimeError(
            f"Ошибка при загрузке файла во VK: "
            f"{json.dumps(data, ensure_ascii=False, indent=2)}"
        )

    logging.info(
        f"Ответ загрузки VK: {json.dumps(data, ensure_ascii=False)}"
    )

    return data


def upload_video_to_vk(
    video_path: Path,
    settings: dict,
) -> dict:
    access_token = settings.get("access_token", "").strip()

    if not access_token or access_token == "ВСТАВЬ_СЮДА_VK_ACCESS_TOKEN":
        raise RuntimeError(
            "В config/vk_settings.json не заполнен access_token VK."
        )

    api_version = str(settings.get("api_version", "5.199"))

    title = make_title_from_file(video_path)
    description = make_description(
        video_path=video_path,
        default_description=settings.get("default_description", "#shorts"),
    )

    upload_to_group = bool(settings.get("upload_to_group", False))
    group_id = settings.get("group_id", None)

    privacy_view = settings.get("privacy_view", "all")
    privacy_comment = settings.get("privacy_comment", "all")

    logging.info(f"Начинаю загрузку во VK: {video_path.name}")
    logging.info(f"Название: {title}")
    logging.info(
        "Куда загружаю: "
        + ("в группу" if upload_to_group else "в личные видео")
    )

    save_response = get_upload_url(
        access_token=access_token,
        api_version=api_version,
        title=title,
        description=description,
        upload_to_group=upload_to_group,
        group_id=group_id,
        privacy_view=privacy_view,
        privacy_comment=privacy_comment,
    )

    upload_url = save_response["upload_url"]

    upload_response = upload_file_to_vk(
        upload_url=upload_url,
        video_path=video_path,
    )

    result = {
        "title": title,
        "save_response": save_response,
        "upload_response": upload_response,
    }

    logging.info("Видео отправлено во VK. Обработка на стороне VK может занять время.")

    return result


def run_vk_upload_once() -> None:
    setup_logger()

    logging.info("========================================")
    logging.info("Старт загрузчика VK.")

    settings = load_vk_settings()
    uploaded_db = load_uploaded_db()

    output_dir = Path(settings["output_dir"])
    upload_limit_per_run = int(settings.get("upload_limit_per_run", 3))
    delete_after_upload = bool(settings.get("delete_after_upload", False))

    mixed_series_mode = bool(settings.get("mixed_series_mode", True))
    max_per_series_per_run = int(settings.get("max_per_series_per_run", 2))
    include_root_files = bool(settings.get("include_root_files", False))
    queue_state_file = Path(
        settings.get(
            "upload_queue_state_file",
            str(VK_QUEUE_STATE_FILE),
        )
    )

    uploaded_files = uploaded_db.get("uploaded_files", [])

    if mixed_series_mode:
        logging.info("Режим очереди: смешанная загрузка из подпапок output.")
        logging.info(f"Файл состояния очереди: {queue_state_file}")

        if uploaded_files:
            mark_files_uploaded(
                state_file=queue_state_file,
                uploaded_files=uploaded_files,
                output_dir=output_dir,
            )

        summary = get_queue_summary(
            output_dir=output_dir,
            state_file=queue_state_file,
            extensions=[".mp4"],
            include_root_files=include_root_files,
        )

        logging.info(
            "Очередь VK: "
            f"серий {summary['series_count']}, "
            f"всего файлов {summary['total_files']}, "
            f"ожидает {summary['pending_files']}"
        )

        videos = get_mixed_upload_batch(
            output_dir=output_dir,
            state_file=queue_state_file,
            limit=upload_limit_per_run,
            max_per_series_per_run=max_per_series_per_run,
            extensions=[".mp4"],
            include_root_files=include_root_files,
        )

        # Дополнительная защита от повторов по старой базе config/vk_uploaded.json.
        old_uploaded_set = set(uploaded_files)
        videos = [
            video_path
            for video_path in videos
            if str(video_path.resolve()) not in old_uploaded_set
        ]

    else:
        logging.info("Режим очереди: старый режим, только MP4 прямо в output.")
        videos = get_video_files_for_upload(
            output_dir=output_dir,
            uploaded_files=uploaded_files,
        )
        videos = videos[:upload_limit_per_run]

    if not videos:
        logging.info("Новых MP4-файлов для загрузки во VK нет.")
        return

    logging.info(f"Найдено файлов к загрузке во VK: {len(videos)}")

    for index, video_path in enumerate(videos, start=1):
        logging.info(f"Позиция в пачке VK {index}/{len(videos)}: {video_path}")

    for video_path in videos:
        result = upload_video_to_vk(
            video_path=video_path,
            settings=settings,
        )

        uploaded_record = {
            "file": str(video_path.resolve()),
            "title": result["title"],
            "vk_save_response": result["save_response"],
            "vk_upload_response": result["upload_response"],
        }

        file_key = str(video_path.resolve())
        if file_key not in uploaded_files:
            uploaded_files.append(file_key)

        uploaded_db["uploaded_files"] = uploaded_files

        if "uploaded_records" not in uploaded_db:
            uploaded_db["uploaded_records"] = []

        uploaded_db["uploaded_records"].append(uploaded_record)

        save_uploaded_db(uploaded_db)

        if mixed_series_mode:
            mark_file_uploaded(
                state_file=queue_state_file,
                uploaded_file=video_path,
                output_dir=output_dir,
            )

        logging.info(f"Файл отмечен как загруженный во VK: {video_path.name}")

        if delete_after_upload:
            logging.info(f"Удаляю локальный файл после загрузки: {video_path}")
            video_path.unlink()

        time.sleep(2)

    logging.info("Загрузчик VK завершил работу.")