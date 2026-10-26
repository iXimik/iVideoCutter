import os
import sys
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QComboBox,
    QSpinBox,
    QTextEdit,
    QMessageBox,
    QProgressBar,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QCheckBox,
    QSplitter,
)

from core.ffmpeg_tools import check_ffmpeg, check_ffprobe
from core.video_info import get_video_info
from core.video_splitter import (
    split_video,
    MODE_SIMPLE,
    MODE_VERTICAL_CROP,
    MODE_VERTICAL_STRETCH,
    MODE_VERTICAL_PAD_COLOR,
    MODE_VERTICAL_PAD_BLUR,
    TITLE_COLORS,
    BACKGROUND_COLORS,
)

from core.app_settings import load_settings, save_settings, get_settings_file_path

# Если в video_splitter.py уже есть логика остановки — используем её.
# Если нет — приложение не упадёт.
try:
    from core.video_splitter import request_stop, reset_stop_flag
except Exception:
    def request_stop():
        pass

    def reset_stop_flag():
        pass


MUSIC_MODE_RANDOM = "Случайный трек из папки"
MUSIC_MODE_SELECTED = "Выбранный трек"


class SplitWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(list)
    error_signal = Signal(str)

    def __init__(
        self,
        input_path: str,
        output_dir: str,
        part_duration: int,
        mode: str,
        output_base_name: str,
        add_title: bool,
        title_font_size: int,
        font_family: str,
        bold: bool,
        italic: bool,
        underline: bool,
        title_color_name: str,
        background_color_name: str,
        add_music: bool,
        music_mode: str,
        music_dir: str,
        selected_music_file: str,
    ):
        super().__init__()

        self.input_path = input_path
        self.output_dir = output_dir
        self.part_duration = part_duration
        self.mode = mode
        self.output_base_name = output_base_name
        self.add_title = add_title
        self.title_font_size = title_font_size
        self.font_family = font_family
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.title_color_name = title_color_name
        self.background_color_name = background_color_name
        self.add_music = add_music
        self.music_mode = music_mode
        self.music_dir = music_dir
        self.selected_music_file = selected_music_file

    def run(self):
        try:
            reset_stop_flag()

            selected_music_file = ""
            if self.add_music and self.music_mode == MUSIC_MODE_SELECTED:
                selected_music_file = self.selected_music_file

            files = split_video(
                input_path=self.input_path,
                output_dir=self.output_dir,
                part_duration=self.part_duration,
                mode=self.mode,
                output_base_name=self.output_base_name,
                add_title=self.add_title,
                title_font_size=self.title_font_size,
                font_family=self.font_family,
                bold=self.bold,
                italic=self.italic,
                underline=self.underline,
                title_color_name=self.title_color_name,
                background_color_name=self.background_color_name,
                add_music=self.add_music,
                music_dir=self.music_dir,
                selected_music_file=selected_music_file,
                progress_callback=self.log_signal.emit,
            )

            self.finished_signal.emit(files)

        except Exception as error:
            self.error_signal.emit(str(error))


class CommandWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(int)

    def __init__(self, title: str, command: list[str], workdir: str):
        super().__init__()
        self.title = title
        self.command = command
        self.workdir = workdir

    def run(self):
        try:
            self.log_signal.emit("--------------------------------")
            self.log_signal.emit(f"Запускаю: {self.title}")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            process = subprocess.Popen(
                self.command,
                cwd=self.workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )

            if process.stdout:
                for line in process.stdout:
                    line = line.rstrip()
                    if line:
                        self.log_signal.emit(line)

            process.wait()
            self.finished_signal.emit(process.returncode)

        except Exception as error:
            self.log_signal.emit(f"Ошибка запуска {self.title}: {error}")
            self.finished_signal.emit(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Video Cutter App — нарезка видео")
        self.resize(1400, 900)

        self.video_path = None
        self.worker = None
        self.command_workers = []
        self.settings = load_settings()

        self.project_dir = Path(__file__).resolve().parents[1]
        self.output_dir_default = str(self.project_dir / "output")
        self.music_dir_default = str(self.project_dir / "input" / "music")

        self.create_ui()
        self.apply_saved_settings()
        self.check_tools_on_start()

    def create_ui(self):
        central_widget = QWidget()
        root_layout = QVBoxLayout(central_widget)

        title = QLabel("Нарезка видео для YouTube Shorts / VK / Telegram")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        root_layout.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)

        # ============================================================
        # ЛЕВАЯ КОЛОНКА
        # ============================================================
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 1. Исходное видео
        file_group = QGroupBox("1. Исходное видео")
        file_layout = QVBoxLayout(file_group)

        file_row = QHBoxLayout()

        self.file_label = QLabel("Файл не выбран")
        self.file_label.setWordWrap(True)

        self.choose_button = QPushButton("Выбрать видео")
        self.choose_button.clicked.connect(self.choose_video)

        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.choose_button)

        file_layout.addLayout(file_row)

        self.info_label = QLabel("Информация о видео появится после выбора файла.")
        self.info_label.setWordWrap(True)
        file_layout.addWidget(self.info_label)

        left_layout.addWidget(file_group)

        # 2. Настройки нарезки
        settings_group = QGroupBox("2. Настройки нарезки")
        settings_layout = QFormLayout(settings_group)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            MODE_SIMPLE,
            MODE_VERTICAL_CROP,
            MODE_VERTICAL_STRETCH,
            MODE_VERTICAL_PAD_COLOR,
            MODE_VERTICAL_PAD_BLUR,
        ])

        self.duration_spin = QSpinBox()
        self.duration_spin.setMinimum(1)
        self.duration_spin.setMaximum(3600)
        self.duration_spin.setValue(60)
        self.duration_spin.setSuffix(" секунд")

        self.output_name_edit = QLineEdit()
        self.output_name_edit.setPlaceholderText(
            "Например: Mario. Если пусто — имя исходного файла"
        )

        self.background_color_combo = QComboBox()
        self.background_color_combo.addItems(list(BACKGROUND_COLORS.keys()))

        self.add_title_checkbox = QCheckBox("Добавлять название файла в видео")
        self.add_title_checkbox.setChecked(True)
        self.add_title_checkbox.toggled.connect(self.on_title_controls_toggle)

        self.title_font_size_spin = QSpinBox()
        self.title_font_size_spin.setMinimum(12)
        self.title_font_size_spin.setMaximum(150)
        self.title_font_size_spin.setValue(36)
        self.title_font_size_spin.setSuffix(" px")

        self.font_family_combo = QComboBox()
        self.font_family_combo.addItems([
            "Arial",
            "Comic Sans MS",
            "Times New Roman",
            "Calibri",
            "DejaVu Sans",
        ])

        self.title_color_combo = QComboBox()
        self.title_color_combo.addItems(list(TITLE_COLORS.keys()))

        self.bold_checkbox = QCheckBox("Жирный")
        self.italic_checkbox = QCheckBox("Курсив")
        self.underline_checkbox = QCheckBox("Подчёркнутый")

        style_row = QHBoxLayout()
        style_row.addWidget(self.bold_checkbox)
        style_row.addWidget(self.italic_checkbox)
        style_row.addWidget(self.underline_checkbox)

        style_widget = QWidget()
        style_widget.setLayout(style_row)

        self.output_label = QLabel(self.output_dir_default)
        self.output_label.setWordWrap(True)

        self.output_button = QPushButton("Выбрать папку output")
        self.output_button.clicked.connect(self.choose_output_dir)

        output_row = QHBoxLayout()
        output_row.addWidget(self.output_label, 1)
        output_row.addWidget(self.output_button)

        output_widget = QWidget()
        output_widget.setLayout(output_row)

        settings_layout.addRow("Режим обработки:", self.mode_combo)
        settings_layout.addRow("Длительность части:", self.duration_spin)
        settings_layout.addRow("Имя нового файла:", self.output_name_edit)
        settings_layout.addRow("Цвет фона:", self.background_color_combo)
        settings_layout.addRow("", self.add_title_checkbox)
        settings_layout.addRow("Размер названия:", self.title_font_size_spin)
        settings_layout.addRow("Шрифт:", self.font_family_combo)
        settings_layout.addRow("Цвет подписи:", self.title_color_combo)
        settings_layout.addRow("Стиль текста:", style_widget)
        settings_layout.addRow("Папка результата:", output_widget)

        left_layout.addWidget(settings_group)

        # 3. Музыка
        music_group = QGroupBox("3. Музыка")
        music_layout = QFormLayout(music_group)

        self.add_music_checkbox = QCheckBox("Добавлять музыку")
        self.add_music_checkbox.toggled.connect(self.on_music_controls_toggle)

        self.music_mode_combo = QComboBox()
        self.music_mode_combo.addItems([MUSIC_MODE_RANDOM, MUSIC_MODE_SELECTED])
        self.music_mode_combo.currentTextChanged.connect(self.on_music_mode_changed)

        self.music_dir_label = QLabel(self.music_dir_default)
        self.music_dir_label.setWordWrap(True)

        self.music_dir_button = QPushButton("Выбрать папку")
        self.music_dir_button.clicked.connect(self.choose_music_dir)

        music_dir_row = QHBoxLayout()
        music_dir_row.addWidget(self.music_dir_label, 1)
        music_dir_row.addWidget(self.music_dir_button)

        music_dir_widget = QWidget()
        music_dir_widget.setLayout(music_dir_row)

        self.selected_music_label = QLabel("не выбран")
        self.selected_music_label.setWordWrap(True)

        self.selected_music_button = QPushButton("Выбрать трек")
        self.selected_music_button.clicked.connect(self.choose_music_file)

        selected_music_row = QHBoxLayout()
        selected_music_row.addWidget(self.selected_music_label, 1)
        selected_music_row.addWidget(self.selected_music_button)

        selected_music_widget = QWidget()
        selected_music_widget.setLayout(selected_music_row)

        music_layout.addRow("", self.add_music_checkbox)
        music_layout.addRow("Режим музыки:", self.music_mode_combo)
        music_layout.addRow("Папка музыки:", music_dir_widget)
        music_layout.addRow("Выбранный трек:", selected_music_widget)

        left_layout.addWidget(music_group)

        # 4. Действия
        actions_group = QGroupBox("4. Действия")
        actions_layout = QVBoxLayout(actions_group)

        row1 = QHBoxLayout()
        self.start_button = QPushButton("Начать нарезку")
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self.start_split)

        self.stop_button = QPushButton("Остановить нарезку")
        self.stop_button.setMinimumHeight(40)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_split)

        self.save_defaults_button = QPushButton("Сохранить настройки")
        self.save_defaults_button.setMinimumHeight(40)
        self.save_defaults_button.clicked.connect(self.save_current_settings_as_default)

        self.open_output_button = QPushButton("Открыть папку output")
        self.open_output_button.setMinimumHeight(40)
        self.open_output_button.clicked.connect(self.open_output_folder)

        row1.addWidget(self.start_button)
        row1.addWidget(self.stop_button)
        row1.addWidget(self.save_defaults_button)
        row1.addWidget(self.open_output_button)
        actions_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.telegram_button = QPushButton("Опросить Telegram")
        self.telegram_button.setMinimumHeight(40)
        self.telegram_button.clicked.connect(self.run_telegram_watcher)

        self.vk_uploader_button = QPushButton("Запустить VK uploader")
        self.vk_uploader_button.setMinimumHeight(40)
        self.vk_uploader_button.clicked.connect(self.run_vk_uploader)

        self.youtube_uploader_button = QPushButton("Запустить YouTube uploader")
        self.youtube_uploader_button.setMinimumHeight(40)
        self.youtube_uploader_button.clicked.connect(self.run_youtube_uploader)

        row2.addWidget(self.telegram_button)
        row2.addWidget(self.vk_uploader_button)
        row2.addWidget(self.youtube_uploader_button)
        actions_layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.refresh_vk_token_button = QPushButton("Обновить VK токен")
        self.refresh_vk_token_button.setMinimumHeight(40)
        self.refresh_vk_token_button.clicked.connect(self.refresh_vk_token)

        self.refresh_youtube_token_button = QPushButton("Обновить YouTube токен")
        self.refresh_youtube_token_button.setMinimumHeight(40)
        self.refresh_youtube_token_button.clicked.connect(self.refresh_youtube_token)

        row3.addWidget(self.refresh_vk_token_button)
        row3.addWidget(self.refresh_youtube_token_button)
        actions_layout.addLayout(row3)

        left_layout.addWidget(actions_group)

        self.settings_file_label = QLabel(
            f"Файл настроек: {get_settings_file_path()}"
        )
        self.settings_file_label.setWordWrap(True)
        self.settings_file_label.setStyleSheet("color: gray;")
        left_layout.addWidget(self.settings_file_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        left_layout.addWidget(self.progress_bar)

        left_layout.addStretch()

        # ============================================================
        # ПРАВАЯ КОЛОНКА: ЖУРНАЛ
        # ============================================================
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        log_group = QGroupBox("Журнал")
        log_layout = QVBoxLayout(log_group)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.log_edit.setPlaceholderText(
            "Здесь будет вывод Telegram / VK / YouTube / FFmpeg..."
        )

        log_layout.addWidget(self.log_edit)
        right_layout.addWidget(log_group)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 500])

        root_layout.addWidget(splitter)
        self.setCentralWidget(central_widget)

    # ============================================================
    # НАСТРОЙКИ
    # ============================================================

    def apply_saved_settings(self):
        mode = self.settings.get("mode")
        if mode:
            index = self.mode_combo.findText(mode)
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)

        self.duration_spin.setValue(int(self.settings.get("part_duration", 60)))

        output_dir = self.settings.get("output_dir", self.output_dir_default)
        self.output_label.setText(output_dir)

        background_color_name = self.settings.get("background_color_name", "Серый")
        index = self.background_color_combo.findText(background_color_name)
        if index >= 0:
            self.background_color_combo.setCurrentIndex(index)

        self.add_title_checkbox.setChecked(bool(self.settings.get("add_title", True)))
        self.title_font_size_spin.setValue(int(self.settings.get("title_font_size", 36)))

        font_family = self.settings.get("font_family", "Arial")
        index = self.font_family_combo.findText(font_family)
        if index >= 0:
            self.font_family_combo.setCurrentIndex(index)

        title_color_name = self.settings.get("title_color_name", "Жёлтый")
        index = self.title_color_combo.findText(title_color_name)
        if index >= 0:
            self.title_color_combo.setCurrentIndex(index)

        self.bold_checkbox.setChecked(bool(self.settings.get("bold", True)))
        self.italic_checkbox.setChecked(bool(self.settings.get("italic", False)))
        self.underline_checkbox.setChecked(bool(self.settings.get("underline", False)))

        self.add_music_checkbox.setChecked(bool(self.settings.get("add_music", False)))

        music_mode = self.settings.get("music_mode", MUSIC_MODE_SELECTED)
        index = self.music_mode_combo.findText(music_mode)
        if index >= 0:
            self.music_mode_combo.setCurrentIndex(index)

        self.music_dir_label.setText(
            self.settings.get("music_dir", self.music_dir_default)
        )

        selected_music_file = self.settings.get("selected_music_file", "")
        self.selected_music_label.setText(
            selected_music_file if selected_music_file else "не выбран"
        )

        self.on_title_controls_toggle(self.add_title_checkbox.isChecked())
        self.on_music_controls_toggle(self.add_music_checkbox.isChecked())
        self.on_music_mode_changed(self.music_mode_combo.currentText())

    def collect_current_settings(self) -> dict:
        return {
            "mode": self.mode_combo.currentText(),
            "part_duration": self.duration_spin.value(),
            "output_dir": self.output_label.text(),

            "background_color_name": self.background_color_combo.currentText(),

            "add_title": self.add_title_checkbox.isChecked(),
            "title_font_size": self.title_font_size_spin.value(),
            "font_family": self.font_family_combo.currentText(),
            "title_color_name": self.title_color_combo.currentText(),
            "bold": self.bold_checkbox.isChecked(),
            "italic": self.italic_checkbox.isChecked(),
            "underline": self.underline_checkbox.isChecked(),

            "add_music": self.add_music_checkbox.isChecked(),
            "music_mode": self.music_mode_combo.currentText(),
            "music_dir": self.music_dir_label.text(),
            "selected_music_file": (
                "" if self.selected_music_label.text() == "не выбран"
                else self.selected_music_label.text()
            ),
        }

    def save_current_settings_as_default(self):
        settings = self.collect_current_settings()
        save_settings(settings)
        self.settings = settings

        self.log("Настройки сохранены.")
        self.log(f"Файл настроек: {get_settings_file_path()}")

        QMessageBox.information(
            self,
            "Настройки сохранены",
            f"Настройки сохранены:\n{get_settings_file_path()}"
        )

    def save_current_settings_silently(self):
        settings = self.collect_current_settings()
        save_settings(settings)
        self.settings = settings

    # ============================================================
    # ПЕРЕКЛЮЧАТЕЛИ UI
    # ============================================================

    def on_title_controls_toggle(self, checked: bool):
        self.title_font_size_spin.setEnabled(checked)
        self.font_family_combo.setEnabled(checked)
        self.title_color_combo.setEnabled(checked)
        self.bold_checkbox.setEnabled(checked)
        self.italic_checkbox.setEnabled(checked)
        self.underline_checkbox.setEnabled(checked)

    def on_music_controls_toggle(self, checked: bool):
        self.music_mode_combo.setEnabled(checked)
        self.music_dir_button.setEnabled(checked)
        self.music_dir_label.setEnabled(checked)
        self.selected_music_button.setEnabled(checked)
        self.selected_music_label.setEnabled(checked)

        self.on_music_mode_changed(self.music_mode_combo.currentText())

        if not checked:
            self.music_mode_combo.setEnabled(False)
            self.music_dir_button.setEnabled(False)
            self.music_dir_label.setEnabled(False)
            self.selected_music_button.setEnabled(False)
            self.selected_music_label.setEnabled(False)

    def on_music_mode_changed(self, mode_text: str):
        add_music = self.add_music_checkbox.isChecked()

        if not add_music:
            self.selected_music_button.setEnabled(False)
            return

        if mode_text == MUSIC_MODE_SELECTED:
            self.selected_music_button.setEnabled(True)
        else:
            self.selected_music_button.setEnabled(False)

    # ============================================================
    # ПРОВЕРКИ И ЛОГ
    # ============================================================

    def check_tools_on_start(self):
        self.log("Проверка инструментов...")

        if check_ffmpeg():
            self.log("FFmpeg найден.")
        else:
            self.log("ОШИБКА: FFmpeg не найден.")

        if check_ffprobe():
            self.log("FFprobe найден.")
        else:
            self.log("ОШИБКА: FFprobe не найден.")

        self.log("Настройки по умолчанию загружены.")
        self.log(f"Файл настроек: {get_settings_file_path()}")

    def log(self, text: str):
        self.log_edit.append(text)

    # ============================================================
    # ВЫБОР ФАЙЛОВ
    # ============================================================

    def choose_video(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать видео",
            "",
            "Видео файлы (*.mp4 *.mov *.avi *.mkv *.webm);;Все файлы (*.*)"
        )

        if not file_path:
            return

        self.video_path = file_path
        self.file_label.setText(file_path)
        self.log(f"Выбран файл: {file_path}")

        source_name = Path(file_path).stem
        self.output_name_edit.setText(source_name)

        try:
            info = get_video_info(file_path)

            self.info_label.setText(
                f"Длительность: {info.duration_text}\n"
                f"Разрешение: {info.width}x{info.height}\n"
                f"Соотношение сторон: {info.aspect_ratio}\n"
                f"Кодек: {info.codec}"
            )

            self.log(
                f"Информация: {info.duration_text}, "
                f"{info.width}x{info.height}, {info.aspect_ratio}, {info.codec}"
            )

        except Exception as error:
            QMessageBox.critical(self, "Ошибка", str(error))
            self.log(f"Ошибка чтения видео: {error}")

    def choose_output_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выбрать папку для результата",
            self.output_label.text()
        )

        if folder:
            self.output_label.setText(folder)
            self.log(f"Папка результата: {folder}")

    def choose_music_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выбрать папку с музыкой",
            self.music_dir_label.text()
        )

        if folder:
            self.music_dir_label.setText(folder)
            self.log(f"Папка музыки: {folder}")

    def choose_music_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать музыкальный трек",
            self.music_dir_label.text(),
            "Аудио файлы (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;Все файлы (*.*)"
        )

        if not file_path:
            return

        self.selected_music_label.setText(file_path)
        self.log(f"Выбран музыкальный трек: {file_path}")

    # ============================================================
    # НАРЕЗКА
    # ============================================================

    def start_split(self):
        if not self.video_path:
            QMessageBox.warning(
                self,
                "Нет видео",
                "Сначала выбери видеофайл."
            )
            return

        self.save_current_settings_silently()

        output_dir = self.output_label.text()
        part_duration = self.duration_spin.value()
        mode = self.mode_combo.currentText()
        output_base_name = self.output_name_edit.text().strip()

        background_color_name = self.background_color_combo.currentText()

        add_title = self.add_title_checkbox.isChecked()
        title_font_size = self.title_font_size_spin.value()
        font_family = self.font_family_combo.currentText()
        title_color_name = self.title_color_combo.currentText()

        bold = self.bold_checkbox.isChecked()
        italic = self.italic_checkbox.isChecked()
        underline = self.underline_checkbox.isChecked()

        add_music = self.add_music_checkbox.isChecked()
        music_mode = self.music_mode_combo.currentText()
        music_dir = self.music_dir_label.text()
        selected_music_file = (
            "" if self.selected_music_label.text() == "не выбран"
            else self.selected_music_label.text()
        )

        self.log("--------------------------------")
        self.log("Начинаю нарезку.")
        self.log(f"Режим: {mode}")
        self.log(f"Длительность части: {part_duration} секунд")
        self.log(
            f"Имя нового файла: "
            f"{output_base_name if output_base_name else 'имя исходного видео'}"
        )

        if mode == MODE_VERTICAL_PAD_COLOR:
            self.log(f"Цвет фона: {background_color_name}")

        self.log(f"Добавлять название в видео: {'да' if add_title else 'нет'}")

        if add_title:
            self.log(f"Размер названия: {title_font_size}px")
            self.log(f"Шрифт: {font_family}")
            self.log(f"Цвет подписи: {title_color_name}")
            self.log(f"Жирный: {'да' if bold else 'нет'}")
            self.log(f"Курсив: {'да' if italic else 'нет'}")
            self.log(f"Подчёркнутый: {'да' if underline else 'нет'}")

        self.log(f"Добавлять музыку: {'да' if add_music else 'нет'}")
        if add_music:
            self.log(f"Режим музыки: {music_mode}")
            self.log(f"Папка музыки: {music_dir}")
            self.log(f"Выбранный трек: {selected_music_file if selected_music_file else 'не выбран'}")

        self.log(f"Папка результата: {output_dir}")

        self.set_split_controls_enabled(False)
        self.stop_button.setEnabled(True)
        self.progress_bar.show()

        self.worker = SplitWorker(
            input_path=self.video_path,
            output_dir=output_dir,
            part_duration=part_duration,
            mode=mode,
            output_base_name=output_base_name,
            add_title=add_title,
            title_font_size=title_font_size,
            font_family=font_family,
            bold=bold,
            italic=italic,
            underline=underline,
            title_color_name=title_color_name,
            background_color_name=background_color_name,
            add_music=add_music,
            music_mode=music_mode,
            music_dir=music_dir,
            selected_music_file=selected_music_file,
        )

        self.worker.log_signal.connect(self.log)
        self.worker.finished_signal.connect(self.on_split_finished)
        self.worker.error_signal.connect(self.on_split_error)
        self.worker.start()

    def stop_split(self):
        if not self.worker or not self.worker.isRunning():
            return

        self.log("--------------------------------")
        self.log("Остановка нарезки запрошена...")
        request_stop()
        self.stop_button.setEnabled(False)

    def set_split_controls_enabled(self, enabled: bool):
        self.start_button.setEnabled(enabled)
        self.save_defaults_button.setEnabled(enabled)
        self.choose_button.setEnabled(enabled)
        self.output_button.setEnabled(enabled)
        self.open_output_button.setEnabled(enabled)

        self.mode_combo.setEnabled(enabled)
        self.duration_spin.setEnabled(enabled)
        self.output_name_edit.setEnabled(enabled)
        self.background_color_combo.setEnabled(enabled)
        self.add_title_checkbox.setEnabled(enabled)

        self.add_music_checkbox.setEnabled(enabled)
        if enabled:
            self.on_title_controls_toggle(self.add_title_checkbox.isChecked())
            self.on_music_controls_toggle(self.add_music_checkbox.isChecked())
        else:
            self.title_font_size_spin.setEnabled(False)
            self.font_family_combo.setEnabled(False)
            self.title_color_combo.setEnabled(False)
            self.bold_checkbox.setEnabled(False)
            self.italic_checkbox.setEnabled(False)
            self.underline_checkbox.setEnabled(False)

            self.music_mode_combo.setEnabled(False)
            self.music_dir_button.setEnabled(False)
            self.selected_music_button.setEnabled(False)

    def on_split_finished(self, files: list):
        self.progress_bar.hide()
        self.set_split_controls_enabled(True)
        self.stop_button.setEnabled(False)

        self.log("--------------------------------")
        self.log(f"Нарезка завершена. Создано файлов: {len(files)}")

        QMessageBox.information(
            self,
            "Готово",
            f"Нарезка завершена.\nСоздано файлов: {len(files)}"
        )

    def on_split_error(self, error_text: str):
        self.progress_bar.hide()
        self.set_split_controls_enabled(True)
        self.stop_button.setEnabled(False)

        self.log("--------------------------------")
        self.log("Ошибка нарезки:")
        self.log(error_text)

        QMessageBox.critical(
            self,
            "Ошибка нарезки",
            error_text
        )

    # ============================================================
    # ВНЕШНИЕ КОМАНДЫ
    # ============================================================

    def run_command_worker(self, title: str, command: list[str]):
        worker = CommandWorker(title=title, command=command, workdir=str(self.project_dir))
        worker.log_signal.connect(self.log)
        worker.finished_signal.connect(lambda code, t=title, w=worker: self.on_command_finished(t, code, w))
        self.command_workers.append(worker)
        worker.start()

    def on_command_finished(self, title: str, exit_code: int, worker: CommandWorker):
        self.log("--------------------------------")
        if exit_code == 0:
            self.log(f"{title} завершён успешно.")
        else:
            self.log(f"{title} завершён с кодом ошибки: {exit_code}")

        if worker in self.command_workers:
            self.command_workers.remove(worker)

        if title == "Обновление YouTube токена":
            if exit_code == 0:
                QMessageBox.information(self, "YouTube токен", "YouTube токен успешно обновлён.")
            else:
                QMessageBox.warning(self, "YouTube токен", f"Обновление YouTube токена завершилось с кодом: {exit_code}")

        if title == "Обновление VK токена":
            if exit_code == 0:
                QMessageBox.information(self, "VK токен", "VK токен успешно обновлён.")
            else:
                QMessageBox.warning(self, "VK токен", f"Обновление VK токена завершилось с кодом: {exit_code}")

    def run_telegram_watcher(self):
        self.log("--------------------------------")
        self.log("Ручной опрос Telegram запущен.")
        self.run_command_worker(
            "Telegram watcher",
            [sys.executable, "run_telegram_watcher.py"]
        )

    def run_vk_uploader(self):
        self.log("--------------------------------")
        self.log("Ручной запуск VK uploader.")
        self.run_command_worker(
            "VK uploader",
            [sys.executable, "run_vk_uploader.py"]
        )

    def run_youtube_uploader(self):
        self.log("--------------------------------")
        self.log("Ручной запуск YouTube uploader.")
        self.run_command_worker(
            "YouTube uploader",
            [sys.executable, "run_youtube_uploader.py"]
        )

    def refresh_vk_token(self):
        self.log("--------------------------------")
        self.log("Запуск обновления VK токена.")
        self.run_command_worker(
            "Обновление VK токена",
            [sys.executable, "core/vk_auto_token.py"]
        )

    def refresh_youtube_token(self):
        self.log("--------------------------------")
        self.log("Запуск обновления YouTube токена.")
        self.run_command_worker(
            "Обновление YouTube токена",
            [sys.executable, "-m", "core.youtube_token_tools"]
        )

    # ============================================================
    # ПРОЧЕЕ
    # ============================================================

    def open_output_folder(self):
        output_dir = Path(self.output_label.text())
        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir)

    def closeEvent(self, event):
        self.save_current_settings_silently()
        event.accept()