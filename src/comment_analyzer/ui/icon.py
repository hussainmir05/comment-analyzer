"""The app icon: a speech bubble with a blue / gray / red bar chart (from the previous version).

python -m comment_analyzer.ui.icon build\\icon.ico   writes the .ico used by the Windows build.
"""

import os
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap, QPolygonF

BLUE, GRAY, RED = "#2a78d6", "#8a8984", "#e34948"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_icon(size: int) -> QImage:
    """Draw the icon at size x size pixels (drawn on a 256 grid, then scaled)."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 256, size / 256)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BLUE))
    painter.drawRoundedRect(QRectF(8, 8, 240, 240), 52, 52)
    painter.setBrush(QColor("white"))
    painter.drawRoundedRect(QRectF(44, 52, 168, 116), 28, 28)
    painter.drawPolygon(QPolygonF([QPointF(78, 160), QPointF(70, 206), QPointF(118, 164)]))
    for x, height, color in ((74, 70, BLUE), (116, 46, GRAY), (158, 28, RED)):
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(x, 148 - height, 26, height), 5, 5)
    painter.end()
    return image


def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(render_icon(size)))
    return icon


def write_ico(path: Path, sizes: tuple[int, ...] = ICON_SIZES) -> None:
    """Save a multi-size .ico. Entries are PNG-compressed, which Windows reads since Vista."""
    images = [_png(render_icon(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))  # reserved, type 1 = icon, image count
    offset = len(header) + 16 * len(sizes)
    entries = b""
    for size, data in zip(sizes, images, strict=True):
        side = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(images))


def _png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    # PySide6 accepts the format only as str here, although its type stubs say bytes
    image.save(buffer, "PNG")  # type: ignore[call-overload]
    return bytes(buffer.data().data())


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv[:1])
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "icon.ico")
    write_ico(target)
    print(f"Wrote {target}")
