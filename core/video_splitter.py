import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Event


# ============================================================
# РЕЖИМЫ ОБРАБОТКИ
# ============================================================

MODE_KEEP_ORIGINAL = "Не менять исходный формат"
MODE_VERTICAL_WITH_BACKGROUND = "9:16 — вставить в вертикальный кадр, цветной фон"
MODE_VERTICAL_CENTER_CROP = "9:16 — обрезка по центру"
MODE_VERTICAL_STRETCH_REAL = "9:16 — растянуть в вертикальный кадр"
MODE_VERTICAL_PAD_BLUR_REAL = "9:16 — вставить в вертикальный кадр, размытый фон"

# Совместимость со старым ui/main_window.py
MODE_SIMPLE = MODE_KEEP_ORIGINAL
MODE_VERTICAL_CROP = MODE_VERTICAL_CENTER_CROP
MODE_VERTICAL_STRETCH = MODE_VERTICAL_STRETCH_REAL
MODE_VERTICAL_PAD_COLOR = MODE_VERTICAL_WITH_BACKGROUND
MODE_VERTICAL_PAD_BLUR = MODE_VERTICAL_PAD_BLUR_REAL

# Дополнительные старые алиасы, если где-то ещё используются
MODE_9_16_BACKGROUND = MODE_VERTICAL_WITH_BACKGROUND
MODE_9_16_CROP = MODE_VERTICAL_CENTER_CROP


# ============================================================
# ЦВЕТА
# ============================================================

BACKGROUND_COLORS = {
    "Серый": "gray",
    "Чёрный": "black",
    "Черный": "black",
    "Белый": "white",
    "Красный": "red",
    "Фиолетовый": "purple",
    "Тёмно-синий": "#000033",
    "Темно-синий": "#000033",
    "Тёмно-фиолетовый": "#1a0033",
    "Темно-фиолетовый": "#1a0033",
}

TITLE_COLORS = {
    "Жёлтый": "yellow",
    "Желтый": "yellow",
    "Ярко-зелёный": "lime",
    "Ярко-зеленый": "lime",
    "Синий": "blue",
    "Красный": "red",
    "Белый": "white",
    "Фиолетовый": "purple",
    "Чёрный": "black",
    "Черный": "black",
}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
}


# ============================================================
# ШРИФТЫ
# ============================================================

FONT_CANDIDATES = {
    "Arial": [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "Times New Roman": [
        r"C:\Windows\Fonts\times.ttf",
        r"C:\Windows\Fonts\timesbd.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
    "Comic Sans MS": [
        r"C:\Windows\Fonts\comic.ttf",
        r"C:\Windows\Fonts\comicbd.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Comic_Sans_MS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "Calibri": [
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "DejaVu Sans": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ],
}


# ============================================================
# ОСТАНОВКА НАРЕЗКИ
# ============================================================

_STOP_EVENT = Event()


def request_stop() -> None:
    """
    Запрашивает остановку нарезки из GUI.
    main_window.py вызывает эту функцию при нажатии кнопки "Остановить нарезку".
    """
    _STOP_EVENT.set()


def reset_stop_flag() -> None:
    """
    Сбрасывает флаг остановки перед новым запуском нарезки.
    """
    _STOP_EVENT.clear()


def is_stop_requested() -> bool:
    """
    Возвращает True, если пользователь запросил остановку.
    """
    return _STOP_EVENT.is_set()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _run_command(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _check_tool_exists(tool_name: str) -> bool:
    return shutil.which(tool_name) is not None


def check_ffmpeg_tools() -> None:
    if not _check_tool_exists("ffmpeg"):
        raise RuntimeError(
            "FFmpeg не найден. Установи FFmpeg и добавь его в PATH."
        )

    if not _check_tool_exists("ffprobe"):
        raise RuntimeError(
            "FFprobe не найден. Установи FFmpeg и добавь его в PATH."
        )



def _is_stop_requested(should_stop_callback=None) -> bool:
    """
    Проверяет остановку двумя способами:
    1. глобальный флаг request_stop(), который вызывает GUI;
    2. дополнительный callback, если его передали вручную.
    """
    if is_stop_requested():
        return True

    if should_stop_callback is None:
        return False

    try:
        return bool(should_stop_callback())
    except Exception:
        return False


def _run_ffmpeg_command(
    command: list[str],
    should_stop_callback=None,
) -> subprocess.CompletedProcess:
    """
    Запускает FFmpeg так, чтобы его можно было остановить из GUI.

    Важно для Windows:
    stdout/stderr нельзя оставлять в subprocess.PIPE без постоянного чтения.
    FFmpeg много пишет в stderr; если буфер PIPE заполнится, FFmpeg может
    зависнуть, а выходной mp4 останется маленьким файлом 40-50 КБ.

    Поэтому stdout/stderr пишутся во временные файлы. После завершения
    или остановки мы читаем их и возвращаем как CompletedProcess.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_run_"))
    stdout_path = temp_dir / "stdout.txt"
    stderr_path = temp_dir / "stderr.txt"

    try:
        with open(stdout_path, "w", encoding="utf-8", errors="replace") as stdout_file, \
             open(stderr_path, "w", encoding="utf-8", errors="replace") as stderr_file:

            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
            )

            while True:
                if _is_stop_requested(should_stop_callback):
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass

                    process.wait()

                    stdout_text = stdout_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ) if stdout_path.exists() else ""

                    stderr_text = stderr_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ) if stderr_path.exists() else ""

                    return subprocess.CompletedProcess(
                        command,
                        -999,
                        stdout_text,
                        stderr_text + "\nОстановлено пользователем.",
                    )

                return_code = process.poll()

                if return_code is not None:
                    stdout_text = stdout_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ) if stdout_path.exists() else ""

                    stderr_text = stderr_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ) if stderr_path.exists() else ""

                    return subprocess.CompletedProcess(
                        command,
                        return_code,
                        stdout_text,
                        stderr_text,
                    )

                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass

    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _safe_name(name: str) -> str:
    bad_chars = '<>:"/\\|?*'
    safe = str(name or "").strip()

    for char in bad_chars:
        safe = safe.replace(char, "_")

    while "  " in safe:
        safe = safe.replace("  ", " ")

    safe = safe.strip(" .")

    if not safe:
        safe = "video"

    return safe


def safe_filename(name: str) -> str:
    """
    Совместимость с telegram_watcher.py.
    """
    return _safe_name(name)


def _choose_random_music_file(music_dir: str | None) -> Path | None:
    """
    Выбирает случайный аудиофайл из папки music_dir.
    Если папки нет или файлов нет — возвращает None.
    """
    if not music_dir:
        return None

    folder = Path(music_dir)

    if not folder.exists() or not folder.is_dir():
        return None

    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]

    if not files:
        return None

    return random.choice(files)


def _resolve_music_file(
    add_music: bool,
    music_dir: str | None,
    selected_music_file: str | None,
) -> Path | None:
    """
    Возвращает конкретный выбранный трек, если он указан и существует.
    Иначе выбирает случайный трек из music_dir.
    """
    if not add_music:
        return None

    if selected_music_file and str(selected_music_file).strip():
        selected_path = Path(str(selected_music_file).strip())

        if selected_path.exists() and selected_path.is_file():
            if selected_path.suffix.lower() in AUDIO_EXTENSIONS:
                return selected_path

    return _choose_random_music_file(music_dir)


def _escape_drawtext_text(text: str) -> str:
    """
    Экранирование текста для ffmpeg drawtext.
    """
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("%", "\\%")
    return text


def _escape_drawtext_path(path: str) -> str:
    """
    Экранирование пути к шрифту для ffmpeg drawtext.
    Особенно важно для Windows-путей C:/...
    """
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    return path


def _find_font_file(
    font_family: str = "Arial",
    bold: bool = False,
    italic: bool = False,
) -> str | None:
    """
    Ищет файл шрифта.
    """
    font_family = font_family or "Arial"

    windows_fonts = Path(r"C:\Windows\Fonts")

    if windows_fonts.exists():
        family_lower = font_family.lower()

        if "arial" in family_lower:
            if bold and italic:
                candidates = ["arialbi.ttf", "arialbd.ttf", "arial.ttf"]
            elif bold:
                candidates = ["arialbd.ttf", "arial.ttf"]
            elif italic:
                candidates = ["ariali.ttf", "arial.ttf"]
            else:
                candidates = ["arial.ttf"]

        elif "times" in family_lower:
            if bold and italic:
                candidates = ["timesbi.ttf", "timesbd.ttf", "times.ttf"]
            elif bold:
                candidates = ["timesbd.ttf", "times.ttf"]
            elif italic:
                candidates = ["timesi.ttf", "times.ttf"]
            else:
                candidates = ["times.ttf"]

        elif "comic" in family_lower:
            if bold:
                candidates = ["comicbd.ttf", "comic.ttf"]
            else:
                candidates = ["comic.ttf"]

        elif "calibri" in family_lower:
            if bold and italic:
                candidates = ["calibriz.ttf", "calibrib.ttf", "calibri.ttf"]
            elif bold:
                candidates = ["calibrib.ttf", "calibri.ttf"]
            elif italic:
                candidates = ["calibrii.ttf", "calibri.ttf"]
            else:
                candidates = ["calibri.ttf"]

        else:
            candidates = ["arial.ttf"]

        for filename in candidates:
            path = windows_fonts / filename
            if path.exists():
                return str(path)

    candidates = []
    candidates.extend(FONT_CANDIDATES.get(font_family, []))
    candidates.extend(FONT_CANDIDATES.get("Arial", []))
    candidates.extend(
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    return None


def _get_color(color_name: str, color_map: dict[str, str], default: str) -> str:
    if not color_name:
        return default

    return color_map.get(color_name, default)


def _get_duration_fallback(input_path: str) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]

    result = _run_command(command)

    if result.returncode != 0:
        return 0.0

    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _get_video_info(input_path: str) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1",
        input_path,
    ]

    result = _run_command(command)

    if result.returncode != 0:
        raise RuntimeError(f"Ошибка ffprobe:\n{result.stderr}")

    width = None
    height = None
    duration = None

    for line in result.stdout.splitlines():
        line = line.strip()

        if not line or "=" not in line:
            continue

        key, value = line.split("=", 1)

        if key == "width":
            width = int(float(value))
        elif key == "height":
            height = int(float(value))
        elif key == "duration":
            try:
                duration = float(value)
            except ValueError:
                pass

    if not width or not height:
        raise RuntimeError("Не удалось определить ширину/высоту видео через ffprobe.")

    if not duration or duration <= 0:
        duration = _get_duration_fallback(input_path)

    if not duration or duration <= 0:
        raise RuntimeError("Не удалось определить длительность видео.")

    return {
        "width": width,
        "height": height,
        "duration": duration,
    }


def _needs_normalization(input_path: Path) -> bool:
    return input_path.suffix.lower() == ".avi"


def _normalize_to_mp4(input_path: Path, progress_callback=None) -> Path:
    """
    AVI иногда даёт проблемы с изображением/звуком.
    Поэтому сначала нормализуем в MP4.
    """
    if progress_callback:
        progress_callback("AVI обнаружен. Создаю нормализованный MP4...")

    temp_dir = Path(tempfile.mkdtemp(prefix="video_normalize_"))
    normalized_path = temp_dir / f"{input_path.stem}_normalized.mp4"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(normalized_path),
    ]

    result = _run_command(command)

    if result.returncode != 0:
        raise RuntimeError(f"Ошибка нормализации AVI:\n{result.stderr}")

    return normalized_path


# ============================================================
# ГЕОМЕТРИЯ ВЕРТИКАЛЬНОГО КАДРА
# ============================================================

def _calculate_scaled_video_box_for_pad(
    source_width: int,
    source_height: int,
    target_width: int = 1080,
    target_height: int = 1920,
) -> dict:
    """
    Считает, где окажется исходное видео внутри вертикального кадра
    при scale=1080:1920:force_original_aspect_ratio=decrease + pad.
    """
    scale = min(target_width / source_width, target_height / source_height)

    scaled_width = int(source_width * scale)
    scaled_height = int(source_height * scale)

    left = int((target_width - scaled_width) / 2)
    top = int((target_height - scaled_height) / 2)
    right = left + scaled_width
    bottom = top + scaled_height

    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": scaled_width,
        "height": scaled_height,
    }


# ============================================================
# ФИЛЬТРЫ FFMPEG
# ============================================================

def _build_title_filter(
    title_text: str,
    title_font_size: int,
    font_family: str,
    bold: bool,
    italic: bool,
    underline: bool,
    title_color_name: str,
    y_position: str = "h-th-90",
) -> str:
    title_color = _get_color(title_color_name, TITLE_COLORS, "yellow")

    font_file = _find_font_file(
        font_family=font_family,
        bold=bold,
        italic=italic,
    )

    safe_text = _escape_drawtext_text(title_text)

    font_size = max(10, int(title_font_size or 36))

    font_part = ""
    if font_file:
        font_part = f"fontfile='{_escape_drawtext_path(font_file)}':"

    drawtext = (
        "drawtext="
        f"{font_part}"
        f"text='{safe_text}':"
        f"fontsize={font_size}:"
        f"fontcolor={title_color}:"
        "x=(w-text_w)/2:"
        f"y={y_position}:"
        "box=0"
    )

    if underline:
        # text_h доступен внутри drawtext, но не доступен в отдельном drawbox.
        # Поэтому позицию линии считаем заранее. Для числового y_position
        # используем y + размер шрифта + отступ. Для выражения h-th-90
        # используем безопасное выражение относительно высоты кадра.
        try:
            underline_y = str(int(float(y_position)) + font_size + 8)
        except Exception:
            underline_y = "ih-82"

        underline_filter = (
            "drawbox="
            "x=80:"
            f"y={underline_y}:"
            "w=iw-160:"
            "h=4:"
            f"color={title_color}:"
            "t=fill"
        )

        return f"{drawtext},{underline_filter}"

    return drawtext


def _build_video_filter(
    mode: str,
    add_title: bool,
    title_text: str,
    title_font_size: int,
    font_family: str,
    bold: bool,
    italic: bool,
    underline: bool,
    title_color_name: str,
    background_color_name: str,
    source_width: int,
    source_height: int,
) -> str | None:
    filters = []

    if mode == MODE_KEEP_ORIGINAL or mode == MODE_SIMPLE:
        pass

    elif mode == MODE_VERTICAL_CENTER_CROP or mode == MODE_VERTICAL_CROP:
        filters.append(
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )

    elif mode == MODE_VERTICAL_STRETCH:
        filters.append("scale=1080:1920")

    elif mode == MODE_VERTICAL_PAD_BLUR:
        filters.append(
            "split=2[main][bg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "boxblur=30:1[bg];"
            "[main]scale=1080:1920:force_original_aspect_ratio=decrease[main];"
            "[bg][main]overlay=(W-w)/2:(H-h)/2"
        )

    else:
        background_color = _get_color(
            background_color_name,
            BACKGROUND_COLORS,
            "gray",
        )

        filters.append(
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:"
            f"color={background_color}"
        )

    if add_title and title_text:
        title_y_position = "h-th-90"

        if mode == MODE_VERTICAL_WITH_BACKGROUND or mode == MODE_VERTICAL_PAD_COLOR:
            box = _calculate_scaled_video_box_for_pad(
                source_width=source_width,
                source_height=source_height,
                target_width=1080,
                target_height=1920,
            )

            font_size = max(10, int(title_font_size or 36))
            title_y = int(box["bottom"] + 30)
            max_y = 1920 - font_size - 60
            title_y = min(title_y, max_y)
            title_y = max(title_y, 30)
            title_y_position = str(title_y)

        title_filter = _build_title_filter(
            title_text=title_text,
            title_font_size=title_font_size,
            font_family=font_family,
            bold=bold,
            italic=italic,
            underline=underline,
            title_color_name=title_color_name,
            y_position=title_y_position,
        )

        filters.append(title_filter)

    if not filters:
        return None

    return ",".join(filters)


# ============================================================
# ИМЕНА ФАЙЛОВ И ПОДПИСИ
# ============================================================

def _make_part_number_text(part_number: int, total_parts: int = 1) -> str:
    """
    Формирует номер части с правильным количеством нулей.

    Для больших серий важно использовать одинаковую ширину номера:
        total_parts = 12   -> 001, 002, ..., 012
        total_parts = 312  -> 001, 002, ..., 312
        total_parts = 1200 -> 0001, 0002, ..., 1200

    Минимальная ширина — 3 цифры, чтобы порядок файлов в Windows
    и в upload_queue был стабильным для длинных серий.
    """
    total_parts_int = max(1, int(total_parts or 1))
    part_number_int = max(1, int(part_number or 1))

    pad_width = max(3, len(str(total_parts_int)))

    return f"{part_number_int:0{pad_width}d}"


def _make_output_file_path(
    output_path: Path,
    base_name_for_file: str,
    part_number: int,
    no_split: bool,
    total_parts: int = 1,
) -> Path:
    """
    Формирует имя выходного файла.

    Если итоговый файл всего один:
        Clouds.mp4

    Если частей несколько:
        Clouds e.001.mp4
        Clouds e.002.mp4
        Clouds e.003.mp4
    """
    if no_split or int(total_parts or 1) <= 1:
        return output_path / f"{base_name_for_file}.mp4"

    part_number_text = _make_part_number_text(
        part_number=part_number,
        total_parts=total_parts,
    )

    return output_path / f"{base_name_for_file} e.{part_number_text}.mp4"


def _make_title_for_part(
    base_title: str,
    part_number: int,
    no_split: bool,
    total_parts: int = 1,
) -> str:
    """
    Формирует подпись внутри видео.

    Если итоговый файл всего один:
        Clouds

    Если частей несколько:
        Clouds e.001
        Clouds e.002
        Clouds e.003
    """
    if no_split or int(total_parts or 1) <= 1:
        return base_title

    part_number_text = _make_part_number_text(
        part_number=part_number,
        total_parts=total_parts,
    )

    return f"{base_title} e.{part_number_text}"


def _ensure_not_same_as_input(output_file: Path, input_file: Path) -> Path:
    """
    FFmpeg не умеет перезаписывать файл сам в себя.
    Если output совпадает с input, добавляем суффикс _processed.
    """
    try:
        if output_file.resolve().samefile(input_file.resolve()):
            return output_file.with_name(
                f"{output_file.stem}_processed{output_file.suffix}"
            )
    except FileNotFoundError:
        if str(output_file.resolve()).lower() == str(input_file.resolve()).lower():
            return output_file.with_name(
                f"{output_file.stem}_processed{output_file.suffix}"
            )
    except Exception:
        pass

    return output_file


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ НАРЕЗКИ
# ============================================================

def split_video(
    input_path: str,
    output_dir: str,
    part_duration: int = 60,
    mode: str = MODE_VERTICAL_WITH_BACKGROUND,
    output_base_name: str | None = None,
    add_title: bool = True,
    title_font_size: int = 36,
    font_family: str = "Arial",
    bold: bool = True,
    italic: bool = False,
    underline: bool = False,
    title_color_name: str = "Жёлтый",
    background_color_name: str = "Серый",
    add_music: bool = False,
    music_dir: str | None = None,
    selected_music_file: str | None = None,
    progress_callback=None,
    should_stop_callback=None,
) -> list[str]:
    """
    Основная функция обработки видео.

    part_duration:
        > 0 — резать на части указанной длительности;
        <= 0 — не резать, создать один полный файл.

    add_music:
        True — добавить музыку.

    selected_music_file:
        если указан существующий аудиофайл, используется он.
        если пусто — берётся случайный трек из music_dir.
    """
    check_ffmpeg_tools()

    input_file = Path(input_path)
    output_path = Path(output_dir)

    if not input_file.exists():
        raise FileNotFoundError(f"Исходный файл не найден: {input_file}")

    output_path.mkdir(parents=True, exist_ok=True)

    original_input_file = input_file
    temp_normalized_file = None

    try:
        if _needs_normalization(input_file):
            temp_normalized_file = _normalize_to_mp4(
                input_file,
                progress_callback=progress_callback,
            )
            input_file = temp_normalized_file

        info = _get_video_info(str(input_file))

        source_width = int(info["width"])
        source_height = int(info["height"])
        total_duration = float(info["duration"])

        if total_duration <= 0:
            raise RuntimeError("Длительность видео равна 0.")

        no_split = int(part_duration or 0) <= 0

        if no_split:
            total_parts = 1
        else:
            total_parts = max(1, math.ceil(total_duration / int(part_duration)))

        if output_base_name and output_base_name.strip():
            base_name_for_file = _safe_name(output_base_name)
        else:
            base_name_for_file = _safe_name(original_input_file.stem)

        created_files = []

        if add_music and selected_music_file and str(selected_music_file).strip():
            if progress_callback:
                progress_callback(f"Музыка: выбранный трек — {Path(selected_music_file).name}")
        elif add_music:
            if progress_callback:
                progress_callback(f"Музыка: случайный трек из папки — {music_dir}")

        for index in range(total_parts):
            if _is_stop_requested(should_stop_callback):
                if progress_callback:
                    progress_callback("Нарезка остановлена пользователем до запуска следующей части.")
                break

            part_number = index + 1

            if no_split:
                start_seconds = 0
                current_duration = total_duration
            else:
                start_seconds = index * int(part_duration)
                current_duration = min(
                    int(part_duration),
                    total_duration - start_seconds,
                )

            if current_duration <= 0:
                continue

            output_file = _make_output_file_path(
                output_path=output_path,
                base_name_for_file=base_name_for_file,
                part_number=part_number,
                no_split=no_split,
                total_parts=total_parts,
            )
            output_file = _ensure_not_same_as_input(output_file, original_input_file)

            title_text_for_part = _make_title_for_part(
                base_title=base_name_for_file,
                part_number=part_number,
                no_split=no_split,
                total_parts=total_parts,
            )

            if progress_callback:
                if no_split or total_parts <= 1:
                    progress_callback(f"Создаю полный файл: {output_file.name}")
                else:
                    progress_callback(
                        f"Создаю часть {part_number}/{total_parts}: {output_file.name}"
                    )

            video_filter = _build_video_filter(
                mode=mode,
                add_title=add_title,
                title_text=title_text_for_part,
                title_font_size=title_font_size,
                font_family=font_family,
                bold=bold,
                italic=italic,
                underline=underline,
                title_color_name=title_color_name,
                background_color_name=background_color_name,
                source_width=source_width,
                source_height=source_height,
            )

            music_file = _resolve_music_file(
                add_music=add_music,
                music_dir=music_dir,
                selected_music_file=selected_music_file,
            )

            if add_music and music_file and progress_callback:
                progress_callback(f"Добавляю музыку: {music_file.name}")
            elif add_music and not music_file and progress_callback:
                progress_callback(
                    "Добавление музыки включено, но подходящий аудиофайл не найден. "
                    "Видео будет создано со старым звуком или без звука."
                )

            command = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_seconds),
                "-i",
                str(input_file),
            ]

            if music_file:
                command += [
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(music_file),
                ]

            command += [
                "-t",
                str(current_duration),
                "-map",
                "0:v:0",
            ]

            if music_file:
                command += [
                    "-map",
                    "1:a:0",
                    "-shortest",
                ]
            else:
                command += [
                    "-map",
                    "0:a?",
                ]

            if video_filter:
                command += [
                    "-vf",
                    video_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                ]
            else:
                command += [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                ]

            command += [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(output_file),
            ]

            result = _run_ffmpeg_command(
                command,
                should_stop_callback=should_stop_callback,
            )

            if result.returncode == -999:
                if progress_callback:
                    progress_callback(f"Остановлено пользователем на части {part_number}/{total_parts}.")
                break

            if result.returncode != 0:
                raise RuntimeError(
                    f"Ошибка FFmpeg при создании файла {output_file.name}:\n"
                    f"{result.stderr}"
                )

            created_files.append(str(output_file))

            if progress_callback:
                progress_callback(f"Готово: {output_file.name}")

        return created_files

    finally:
        if temp_normalized_file:
            try:
                temp_dir = temp_normalized_file.parent
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass