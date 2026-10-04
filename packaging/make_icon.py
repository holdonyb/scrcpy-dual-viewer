"""Generate the simple application icon from native Qt shapes."""
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import QApplication

app = QApplication([])
folder = Path(__file__).with_name("assets")
folder.mkdir(exist_ok=True)
image = QImage(256, 256, QImage.Format_ARGB32)
image.fill(Qt.transparent)
painter = QPainter(image)
painter.setRenderHint(QPainter.Antialiasing)
painter.setPen(Qt.NoPen)
painter.setBrush(QColor("#142237"))
painter.drawRoundedRect(QRectF(4, 4, 248, 248), 54, 54)
painter.setBrush(QColor("#263e58"))
painter.setPen(QPen(QColor("#66ddd0"), 8))
painter.drawRoundedRect(QRectF(39, 49, 58, 148), 11, 11)
painter.drawRoundedRect(QRectF(117, 49, 101, 65), 11, 11)
painter.drawRoundedRect(QRectF(117, 132, 101, 65), 11, 11)
painter.setPen(Qt.NoPen)
painter.setBrush(QColor("#ff6f78"))
painter.drawEllipse(QRectF(165, 154, 22, 22))
painter.end()
assert image.save(str(folder / "icon.png"))
assert image.save(str(folder / "icon.ico"))
