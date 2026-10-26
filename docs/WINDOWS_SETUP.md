# Windows 10/11: настройка

## 1. Подготовка

Установите Python 3.12 (64-bit), Git и FFmpeg. Проверьте:

```powershell
py -3.12 --version
ffmpeg -version
ffprobe -version
git --version
```

## 2. Установка

```powershell
git clone https://github.com/iXimik/iVideoCutter.git
cd iVideoCutter
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Если PowerShell блокирует активацию, можно вызывать `.\.venv\Scripts\python.exe` напрямую.

## 3. Настройки

```powershell
New-Item -ItemType Directory -Force -Path input\music, input\telegram_downloaded, output\telegram_priority, logs, credentials, telegram_sessions
Copy-Item config\settings.example.json config\settings.json
Copy-Item config\telegram_settings.example.json config\telegram_settings.json
Copy-Item config\youtube_settings.example.json config\youtube_settings.json
Copy-Item config\vk_settings.example.json config\vk_settings.json
```

Далее настройте JSON локально. Не публикуйте реальные файлы настроек.

- `telegram_settings.json`: `api_id`, `api_hash`, `chat_name_or_id`. Для Telethon может потребоваться первый интерактивный вход и код подтверждения.
- `youtube_settings.json`: `output_dir`, лимит публикаций. Файл Google OAuth client secret разместите локально в `credentials/client_secret.json`; на первой авторизации будет создан токен.
- `vk_settings.json`: введите **свой** разрешённый VK access token и, при необходимости, ID группы.

## 4. Проверка

```powershell
python -m compileall -q core ui main.py run_telegram_watcher.py run_youtube_uploader.py run_vk_uploader.py
python main.py
```

Для очереди безопасная диагностика без публикации: `python -m core.upload_queue`.

### Важно

Указанные модули собраны из доступных редакций; проверку на вашем конкретном Windows-окружении нужно провести отдельно. Устанавливайте задачи планировщика лишь после тестов вручную.
