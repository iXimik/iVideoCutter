import json
import logging
import time
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from core.upload_queue import (
    get_mixed_upload_batch,
    get_queue_summary,
    mark_file_uploaded,
    mark_files_uploaded,
)


PROJECT_DIR = Path.cwd()

YOUTUBE_SETTINGS_FILE = PROJECT_DIR / "config" / "youtube_settings.json"
YOUTUBE_UPLOADED_FILE = PROJECT_DIR / "config" / "youtube_uploaded.json"
YOUTUBE_QUEUE_STATE_FILE = PROJECT_DIR / "logs" / "youtube_upload_queue_state.json"

CREDENTIALS_DIR = PROJECT_DIR / "credentials"
CLIENT_SECRET_FILE = CREDENTIALS_DIR / "client_secret.json"
TOKEN_FILE = CREDENTIALS_DIR / "youtube_token.json"

LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "youtube_uploader.log"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]


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


def load_youtube_settings() -> dict:
    default_settings = {
        "output_dir": str(PROJECT_DIR / "output"),
        "privacy_status": "private",
        "default_description": "#shorts",
        "default_tags": ["shorts", "video"],
        "category_id": "22",
        "made_for_kids": False,
        "upload_limit_per_run": 3,
        "delete_after_upload": False,
        "mixed_series_mode": True,
        "max_per_series_per_run": 2,
        "include_root_files": False,
        "upload_queue_state_file": str(YOUTUBE_QUEUE_STATE_FILE)
    }

    settings = load_json_file(YOUTUBE_SETTINGS_FILE, default_settings)

    merged = default_settings.copy()
    merged.update(settings)

    return merged


def load_uploaded_db() -> dict:
    return load_json_file(
        YOUTUBE_UPLOADED_FILE,
        {
            "uploaded_files": [],
            "uploaded_records": []
        }
    )


def save_uploaded_db(data: dict) -> None:
    save_json_file(YOUTUBE_UPLOADED_FILE, data)


def get_youtube_service():
    if not CLIENT_SECRET_FILE.exists() or CLIENT_SECRET_FILE.stat().st_size == 0:
        raise FileNotFoundError(
            f"Не найден или пустой OAuth-файл: {CLIENT_SECRET_FILE}"
        )

    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES
        )

    if credentials and credentials.expired and credentials.refresh_token:
        logging.info("Обновляю OAuth-токен YouTube.")
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        logging.info("Нужна авторизация YouTube. Откроется браузер.")

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRET_FILE),
            SCOPES
        )

        credentials = flow.run_local_server(
            port=0,
            prompt="consent"
        )

        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(credentials.to_json())

        logging.info(f"OAuth-токен сохранён: {TOKEN_FILE}")

    return build(
        "youtube",
        "v3",
        credentials=credentials
    )


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


def is_youtube_upload_limit_error(error: Exception) -> bool:
    """
    Определяет ошибку лимита загрузки YouTube.

    Такая ошибка приходит, когда канал временно превысил допустимое
    количество загружаемых видео.
    """
    error_text = str(error)

    markers = [
        "uploadLimitExceeded",
        "The user has exceeded the number of videos they may upload",
        "exceeded the number of videos",
    ]

    return any(marker in error_text for marker in markers)


def upload_video(
    youtube,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy_status: str,
    made_for_kids: bool,
) -> str | None:
    """
    Загружает одно видео на YouTube.

    Возвращает:
    - video_id, если загрузка успешна;
    - None, если YouTube временно запретил новые загрузки по лимиту.
    """
    logging.info(f"Начинаю загрузку на YouTube: {video_path.name}")
    logging.info(f"Название: {title}")
    logging.info(f"Статус приватности: {privacy_status}")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=1024 * 1024 * 8,
        resumable=True,
        mimetype="video/mp4",
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None

    while response is None:
        try:
            status, response = request.next_chunk()

            if status:
                percent = int(status.progress() * 100)
                logging.info(f"Загрузка: {percent}%")

        except HttpError as error:
            if is_youtube_upload_limit_error(error):
                logging.error(
                    "YouTube временно запретил новые загрузки: превышен лимит "
                    "количества видео для канала. Файл НЕ отмечен как загруженный. "
                    "Попробуем снова при следующем запуске."
                )
                return None

            raise RuntimeError(f"Ошибка YouTube API:\n{error}") from error

        except Exception as error:
            if is_youtube_upload_limit_error(error):
                logging.error(
                    "YouTube временно запретил новые загрузки: превышен лимит "
                    "количества видео для канала. Файл НЕ отмечен как загруженный. "
                    "Попробуем снова при следующем запуске."
                )
                return None

            logging.info(f"Временная ошибка загрузки: {error}")
            logging.info("Повтор через 5 секунд.")
            time.sleep(5)

    video_id = response.get("id")

    if not video_id:
        raise RuntimeError(f"YouTube не вернул video_id. Ответ: {response}")

    logging.info(f"Видео загружено. YouTube video_id: {video_id}")

    return video_id


def run_youtube_upload_once():
    setup_logger()

    logging.info("========================================")
    logging.info("Старт загрузчика YouTube.")

    settings = load_youtube_settings()
    uploaded_db = load_uploaded_db()

    output_dir = Path(settings["output_dir"])

    privacy_status = settings.get("privacy_status", "private")
    default_description = settings.get("default_description", "#shorts")
    default_tags = settings.get("default_tags", ["shorts"])
    category_id = str(settings.get("category_id", "22"))
    made_for_kids = bool(settings.get("made_for_kids", False))
    upload_limit_per_run = int(settings.get("upload_limit_per_run", 3))
    delete_after_upload = bool(settings.get("delete_after_upload", False))

    mixed_series_mode = bool(settings.get("mixed_series_mode", True))
    max_per_series_per_run = int(settings.get("max_per_series_per_run", 2))
    include_root_files = bool(settings.get("include_root_files", False))
    queue_state_file = Path(
        settings.get(
            "upload_queue_state_file",
            str(YOUTUBE_QUEUE_STATE_FILE),
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
            "Очередь YouTube: "
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

        # Дополнительная защита от повторов по старой базе config/youtube_uploaded.json.
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
        logging.info("Новых MP4-файлов для загрузки нет.")
        return

    logging.info(f"Найдено файлов к загрузке: {len(videos)}")

    for index, video_path in enumerate(videos, start=1):
        logging.info(f"Позиция в пачке YouTube {index}/{len(videos)}: {video_path}")

    youtube = get_youtube_service()

    for video_path in videos:
        title = make_title_from_file(video_path)
        description = make_description(video_path, default_description)

        video_id = upload_video(
            youtube=youtube,
            video_path=video_path,
            title=title,
            description=description,
            tags=default_tags,
            category_id=category_id,
            privacy_status=privacy_status,
            made_for_kids=made_for_kids,
        )

        if not video_id:
            logging.info(
                "Загрузка не выполнена из-за временного лимита YouTube. "
                "Файл остаётся в очереди и будет повторно проверен "
                "при следующем запуске."
            )
            return

        uploaded_record = {
            "file": str(video_path.resolve()),
            "title": title,
            "youtube_video_id": video_id,
            "privacy_status": privacy_status,
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

        logging.info(f"Файл отмечен как загруженный: {video_path.name}")

        if delete_after_upload:
            logging.info(f"Удаляю локальный файл после загрузки: {video_path}")
            video_path.unlink()

    logging.info("Загрузчик YouTube завершил работу.")