"""Controlled external window for native embedding/recording lifecycle tests."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

parser = argparse.ArgumentParser()
parser.add_argument("--title", required=True)
parser.add_argument("--record")
parser.add_argument("--marker", required=True)
parser.add_argument("--ignore-close", action="store_true")
parser.add_argument("--close-delay", type=int, default=0)
parser.add_argument("--exit-code", type=int, default=0)
args = parser.parse_args()


class TestWindow(QPushButton):
    def __init__(self):
        super().__init__("Synthetic native window\nNo Android device attached")
        self.setWindowTitle(args.title)
        self.resize(360, 480)
        self.writer = None
        self.delayed = False
        self.allow_close = False
        self.clicked.connect(lambda: Path(args.marker + ".click").write_text("clicked", encoding="utf-8"))
        if args.record and not args.ignore_close:
            self.writer = subprocess.Popen([
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
                "-i", "testsrc2=size=160x120:rate=10", "-c:v", "mpeg4", "-y", args.record,
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    def closeEvent(self, event):
        Path(args.marker).write_text("close requested", encoding="utf-8")
        if args.ignore_close:
            event.ignore()
            return
        if args.close_delay and not self.allow_close:
            event.ignore()
            if not self.delayed:
                self.delayed = True
                QTimer.singleShot(args.close_delay, self.finish_close)
            return
        if self.writer is not None:
            try:
                self.writer.communicate(input=b"q\n", timeout=10)
            finally:
                if self.writer.poll() is None:
                    self.writer.kill()
                    self.writer.wait(timeout=5)
        super().closeEvent(event)
        if args.exit_code:
            QApplication.instance().exit(args.exit_code)

    def finish_close(self):
        self.allow_close = True
        self.close()


app = QApplication([])
window = TestWindow()
window.show()
sys.exit(app.exec())
