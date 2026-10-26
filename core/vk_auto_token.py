"""Placeholder for the private VK interactive authorization workflow.

The original browser-based token updater depends on a specific app configuration
and is not part of this public recovered snapshot. Never put tokens into Git.
"""
import sys


def main() -> int:
    print(
        "Автоматическое обновление VK OAuth здесь не настроено. "
        "Получите разрешённый VK access token для своего приложения и "
        "сохраните его ЛОКАЛЬНО в config/vk_settings.json. "
        "Этот файл игнорируется Git."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
