from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
YOUTUBE_TOKEN_FILE = PROJECT_DIR / "credentials" / "youtube_token.json"


def refresh_youtube_token() -> str:
    """
    Удаляет старый YouTube OAuth-токен и запускает новую авторизацию.

    Важно:
    get_youtube_service() только создаёт сервис и авторизует аккаунт.
    Видео при этом не загружается.
    """
    if YOUTUBE_TOKEN_FILE.exists():
        YOUTUBE_TOKEN_FILE.unlink()

    from core.youtube_uploader import get_youtube_service

    get_youtube_service()

    if not YOUTUBE_TOKEN_FILE.exists():
        raise RuntimeError(
            "Авторизация прошла, но файл youtube_token.json не был создан."
        )

    return str(YOUTUBE_TOKEN_FILE)