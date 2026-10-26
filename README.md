# iVideoCutter

**iVideoCutter** — Windows/Python-проект для нарезки видео в Shorts/Reels, получения роликов из Telegram и публикации в YouTube и VK.

> Репозиторий восстановлен на основе отдельных файлов, ранее использовавшихся при разработке. Он отражает архитектуру и доступные реализации, но **не является проверенной копией всей текущей локальной Windows-сборки**. Перед рабочим развёртыванием проверьте зависимости, авторизацию и обработку тестового видео.

## Возможности

- GUI на **PySide6**: выбор видео, вертикальный монтаж, текст, музыка, журнал выполнения.
- **FFmpeg/FFprobe**: обработка MP4/MOV/MKV/AVI, вариант 9:16 с фоном, кадрированием или растяжением.
- Нарезка по заданной длительности; одинаковая разрядность номеров: `e.001 … e.312`.
- Управляемая остановка конвертации FFmpeg из GUI.
- Музыка из указанной папки либо выбранный трек; для Telegram-видео с реальным звуком сохраняется исходная дорожка, для беззвучных автоматически выбирается музыка.
- **Telegram watcher**: опрашивает группу, скачивает новые файлы, берёт подпись из caption, обрабатывает их.
- **Приоритет Telegram**: новые видео попадают в `output/telegram_priority/` и выбираются загрузчиками раньше обычных серий.
- **Смешанная очередь** для YouTube и VK: каждая папка в `output` — отдельная серия; порядок внутри серии сохраняется, между сериями выбор случайный.
- Отдельный журнал загруженных файлов и состояние очереди **на каждую платформу**, чтобы не загружать ролик повторно.
- Плановые запуски через **Windows Task Scheduler**.

## Конвейер

```text
Telegram group ──> Telegram watcher ──> FFmpeg ──> output/telegram_priority ─┐
                                                                            ├─> YouTube uploader
Local video ──> GUI + FFmpeg ──> output/output_1, output/output_2, ... ─────┤
                                                                            └─> VK uploader
```

Правило приоритета: если в `telegram_priority` остались файлы, не опубликованные **для данной платформы**, загрузчик берёт только их. Когда они исчерпаны, возобновляет случайную очередь. Приоритет применяется на **каждом запуске**, включая запуск по расписанию. Он не прерывает загрузку, которая уже началась.

## Структура

```text
iVideoCutter/
├── main.py
├── run_telegram_watcher.py
├── run_youtube_uploader.py
├── run_vk_uploader.py
├── core/
│   ├── video_splitter.py
│   ├── cutter_service.py
│   ├── telegram_watcher.py
│   ├── upload_queue.py
│   ├── youtube_uploader.py
│   ├── vk_uploader.py
│   ├── app_settings.py
│   ├── ffmpeg_tools.py
│   └── video_info.py
├── ui/main_window.py
├── config/*.example.json
├── scripts/*.bat
├── docs/
└── tests/
```

Папки с роликами, музыка, приватные конфигурации, база очередей и OAuth-токены **не находятся в Git**.

## Запуск двойным щелчком — без PowerShell

После однократной установки `.venv` и зависимостей откройте папку проекта в Проводнике и **дважды щёлкните `iVideoCutter.vbs`**. Он запускает `main.py` через `.venv\\Scripts\\pythonw.exe`, не открывая консоль. Для удобства создайте ярлык на рабочем столе.

При запуске обычным двойным щелчком по `main.py` приложение тоже попытается автоматически использовать локальную `.venv` (если файл `.py` связан с Python в Windows). Однако для постоянного запуска рекомендуем `iVideoCutter.vbs`.

Если GUI не открывается, запустите двойным щелчком `start_iVideoCutter_debug.bat`: он покажет ошибку, а `main.py` дополнительно пишет журнал в `logs/startup_errors.log`.

## Быстрый запуск (Windows)

Нужны **Python 3.12**, **FFmpeg**, **FFprobe** и доступ к необходимым API.

```powershell
git clone https://github.com/iXimik/iVideoCutter.git
cd iVideoCutter
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config\settings.example.json config\settings.json
Copy-Item config\telegram_settings.example.json config\telegram_settings.json
Copy-Item config\youtube_settings.example.json config\youtube_settings.json
Copy-Item config\vk_settings.example.json config\vk_settings.json
python main.py
```

Отдельно укажите свои значения **только в локальных игнорируемых JSON**. Токены в Git не добавляйте. Для YouTube OAuth локально требуется `credentials/client_secret.json`, который не публикуется. В Telegram необходимы ваш API ID, hash и авторизованная локальная сессия. Для VK нужен действующий локальный access token.

Команды отдельного запуска:

```powershell
python run_telegram_watcher.py
python run_youtube_uploader.py
python run_vk_uploader.py
python -m core.upload_queue
```

## Документация

- [Установка на Windows](docs/WINDOWS_SETUP.md)
- [Архитектура и очереди](docs/ARCHITECTURE.md)
- [Безопасность и персональные данные](docs/SECURITY.md)
- [Планировщик задач](docs/SCHEDULER.md)
- [План развития](docs/ROADMAP.md)

## Важные ограничения

- Конкретные текущие файлы `vk_auto_token.py` и все пользовательские скрипты планировщика из рабочего ПК здесь не представлены: для них приведён безопасный способ настройки; кнопка автоматического обновления VK может требовать отдельной локальной реализации.
- Не запускайте одновременно несколько Telegram watcher с одной Telethon-сессией.
- Сейчас журналы и JSON-очереди рассчитаны на последовательные запуски загрузчиков; одновременные процессы могут конфликтовать. Планировщик нужно настроить на запрет параллельных экземпляров.
- Некоторые восстановленные модули из истории проекта ещё требуют интеграционных тестов на Windows.

## Лицензия

[MIT](LICENSE).
