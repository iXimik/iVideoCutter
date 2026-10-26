from pathlib import Path

from core.app_settings import load_settings
from core.video_splitter import split_video


PROJECT_DIR = Path.cwd()


def cut_video_by_saved_settings(
    input_path: str,
    output_base_name: str | None = None,
    title_text: str | None = None,
    progress_callback=None,
) -> list[str]:
    """
    Запускает нарезку видео по настройкам из config/settings.json.

    Используется Telegram watcher-ом, поэтому сюда добавлены все параметры,
    которые выбираются в главном окне: режим, подпись, папка output, музыка.
    """
    settings = load_settings()

    output_dir = settings.get("output_dir", str(PROJECT_DIR / "output"))

    if title_text and title_text.strip():
        final_output_base_name = title_text.strip()
    elif output_base_name and output_base_name.strip():
        final_output_base_name = output_base_name.strip()
    else:
        final_output_base_name = Path(input_path).stem

    add_music = bool(settings.get("add_music", settings.get("add_random_music", False)))
    music_mode = settings.get("music_mode", "Случайный трек из папки")
    music_dir = settings.get(
        "music_dir",
        str(PROJECT_DIR / "input" / "music"),
    )
    selected_music_file = settings.get("selected_music_file", "")

    if music_mode == "Случайный трек из папки":
        selected_music_file = ""

    return split_video(
        input_path=input_path,
        output_dir=output_dir,
        part_duration=int(settings.get("part_duration", 60)),
        mode=settings.get(
            "mode",
            "9:16 — вставить в вертикальный кадр, цветной фон",
        ),
        output_base_name=final_output_base_name,
        add_title=bool(settings.get("add_title", True)),
        title_font_size=int(settings.get("title_font_size", 36)),
        font_family=settings.get("font_family", "Arial"),
        bold=bool(settings.get("bold", True)),
        italic=bool(settings.get("italic", False)),
        underline=bool(settings.get("underline", False)),
        title_color_name=settings.get("title_color_name", "Жёлтый"),
        background_color_name=settings.get("background_color_name", "Серый"),
        add_music=add_music,
        music_dir=music_dir,
        selected_music_file=selected_music_file,
        progress_callback=progress_callback,
    )