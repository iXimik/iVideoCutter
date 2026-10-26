"""Entry point for the iVideoCutter desktop GUI on Windows.

Double-click iVideoCutter.vbs to launch without PowerShell or a console.
Direct execution of main.py on Windows will prefer the project's own .venv.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def _prefer_project_interpreter() -> bool:
    """Re-launch from local venv if main.py was opened with a system Python.

    Returns True only in the original parent process after starting the GUI.
    Running with the venv's python.exe/pythonw.exe does not spawn again.
    """
    if os.name != "nt":
        return False

    venv_scripts = PROJECT_DIR / ".venv" / "Scripts"
    pythonw_exe = venv_scripts / "pythonw.exe"
    if not pythonw_exe.is_file():
        return False

    if Path(sys.executable).resolve().parent == venv_scripts.resolve():
        return False

    subprocess.Popen(
        [str(pythonw_exe), str(PROJECT_DIR / "main.py"), *sys.argv[1:]],
        cwd=str(PROJECT_DIR),
    )
    return True


def _show_startup_error() -> None:
    details = traceback.format_exc()
    log_file = PROJECT_DIR / "logs" / "startup_errors.log"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as stream:
            stream.write(details + "\n")
    except OSError:
        pass

    message = (
        "iVideoCutter could not start.\n\n"
        "Check logs/startup_errors.log for details.\n\n"
        + details[-1400:]
    )
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "iVideoCutter", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def main() -> int:
    """Start the PySide6 window."""
    os.chdir(PROJECT_DIR)  # Resolve relative config, output and log paths.
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        if _prefer_project_interpreter():
            sys.exit(0)
        sys.exit(main())
    except Exception:
        _show_startup_error()
        sys.exit(1)
