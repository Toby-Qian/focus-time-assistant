"""Desktop entry point; Qt 6 owns per-monitor DPI awareness before any window exists."""
import argparse
import ctypes
import gc
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox
import json
import theme
import windows

def project_root():
    """Source run: the folder above app/. Packaged run: the folder holding browser_extension/,
    which is the project root when the EXE sits in <project>/程序/, so data/ is shared."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]
    here = Path(sys.executable).resolve().parent
    return next((d for d in (here, here.parent) if (d/"browser_extension").is_dir()), here)


PROJECT = project_root()
INSTANCE = "Local\\FocusTimeAssistantV1"


class Waker(QObject):
    """A second launch sets a named event; a blocked thread (zero CPU) relays it to the UI thread."""
    woke = Signal()

    def __init__(self, handle):
        super().__init__()
        self.handle = handle
        threading.Thread(target=self.watch, daemon=True, name="wake").start()

    def watch(self):
        while windows.wait(self.handle):
            self.woke.emit()


def saved_theme(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("theme", "indigo")
    except (OSError, ValueError, AttributeError):
        return "indigo"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=PROJECT/"data")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--start-hidden", action="store_true", help="start in the tray without showing the window")
    # Separate instance lock for measurements/tests, so they never wake or block the real recorder.
    parser.add_argument("--instance", default="", help=argparse.SUPPRESS)
    # Used by "restart to apply the theme": wait for the old process to exit and free the lock.
    parser.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.wait_pid: windows.wait_process(args.wait_pid, 15000)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.data_dir/"app.log", maxBytes=500000, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]; kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = INSTANCE+("Smoke" if args.smoke_test else "")+("."+args.instance if args.instance else "")
    mutex = kernel.CreateMutexW(None, False, name)
    exists = ctypes.get_last_error() == 183
    if exists:
        # Already running: bring that window forward instead of starting a second recorder.
        if mutex: kernel.CloseHandle(mutex)
        if windows.signal_wake(name+".Show"):
            return
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if not getattr(sys, "frozen", False):
        # Run from source the process is pythonw.exe, so the taskbar would group it under Python
        # and show Python's icon. An own AppUserModelID makes it use this window's icon instead.
        # (The EXE keeps the default: its embedded icon matches the pinned shortcut.)
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FocusTimeAssistant.Desktop")
        except (AttributeError, OSError):
            pass
    application = QApplication(sys.argv[:1]); application.setApplicationName("专注时间助手"); application.setQuitOnLastWindowClosed(False)
    theme.use(saved_theme(args.data_dir/"settings.json"))
    theme.setup(application, args.data_dir/"theme_cache")
    from desktop import App  # after theme.use(): the interface modules read the palette when imported
    if not mutex or exists:
        QMessageBox.information(None, "专注时间助手", "程序已在运行，请从任务栏或系统托盘打开。" if exists else "无法建立实例锁，请重试。")
        if mutex: kernel.CloseHandle(mutex)
        return
    window = None
    try:
        window = App(args.data_dir, PROJECT, testing=args.smoke_test)
        waker = Waker(windows.wake_event(name+".Show", True)); waker.woke.connect(window.bring_forward)
        if args.start_hidden and not args.smoke_test: window.tray_hinted = True; window.hide_to_tray()
        else: window.show()
        if args.smoke_test: QTimer.singleShot(2200, window.quit_requested)
        application.aboutToQuit.connect(window.shutdown)
        # Startup objects live for the whole session; keep the cyclic GC from rescanning them.
        gc.collect(); gc.freeze()
        application.exec()
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "启动失败", f"{exc}\n详情见 {args.data_dir/'app.log'}")
        raise
    finally:
        if window: window.shutdown()
        kernel.CloseHandle(mutex)


if __name__ == "__main__": main()
