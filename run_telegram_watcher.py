"""Run one Telegram poll and video-processing cycle."""
from core.telegram_watcher import run_telegram_once


if __name__ == "__main__":
    run_telegram_once()
