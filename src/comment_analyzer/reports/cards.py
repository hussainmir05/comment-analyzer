"""Post-ready card images, drawn with QPainter (needs a QGuiApplication).

1080 x 1350 is Instagram's portrait size (4:5) and works on Facebook too. Instagram's publishing
API takes JPEG only, so cards are saved as JPEG as well as PNG. Cards never show commenters'
names or photos, and always carry "Comments are not a poll" (CLAUDE.md).
"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from comment_analyzer.analysis.summarize import SENTIMENTS, mood
from comment_analyzer.reports.data import COLORS, PostReport

WIDTH, HEIGHT = 1080, 1350
MARGIN = 72
INK, MUTED, BACKGROUND, TRACK = "#141414", "#5f5e5a", "#ffffff", "#ecebe7"
MOOD_COLORS = {
    "mostly positive": COLORS["positive"],
    "mostly negative": COLORS["negative"],
    "mixed": MUTED,
}


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Malgun Gothic"])  # Malgun Gothic draws Hangul
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def render_card(report: PostReport) -> QImage:
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(QColor(BACKGROUND))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    width = WIDTH - 2 * MARGIN
    flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft)

    # The footer sits at the bottom; everything else must end above it.
    footer = f"{report.coverage()}\n{report.footer()}"
    painter.setFont(_font(24))
    footer_box = painter.boundingRect(QRectF(MARGIN, 0, width, HEIGHT), flags, footer)
    footer_top = HEIGHT - footer_box.height() - 40
    painter.fillRect(QRectF(0, footer_top - 40, WIDTH, 2), QColor(TRACK))
    painter.setPen(QColor(MUTED))
    painter.drawText(QRectF(MARGIN, footer_top, width, footer_box.height()), flags, footer)
    limit = footer_top - 60
    y = float(MARGIN)

    def text(content: str, size: int, color: str, *, bold: bool = False) -> bool:
        """Draw wrapped text at y; returns False (drawing nothing) if it would reach the footer."""
        nonlocal y
        painter.setFont(_font(size, bold))
        used = painter.boundingRect(QRectF(MARGIN, y, width, HEIGHT), flags, content)
        if y + used.height() > limit:
            return False
        painter.setPen(QColor(color))
        painter.drawText(QRectF(MARGIN, y, width, used.height()), flags, content)
        y += used.height()
        return True

    text("WHAT THE COMMENTS SAY", 28, MUTED, bold=True)
    y += 24
    text(_shorten(report.title, 80), 52, INK, bold=True)
    y += 12
    source = report.post.platform.label + (f" | {report.post.author}" if report.post.author else "")
    text(source, 30, MUTED)
    y += 56

    analysis = report.analysis
    if analysis is not None and analysis.counted:
        feeling = mood(analysis.shares)
        text(feeling.capitalize(), 84, MOOD_COLORS[feeling], bold=True)
        y += 28
        x = float(MARGIN)
        for sentiment in SENTIMENTS:
            share = analysis.shares[sentiment] / 100 * width
            painter.fillRect(QRectF(x, y, share, 56), QColor(COLORS[sentiment]))
            x += share
        y += 76
        shares = "   ".join(f"{analysis.shares[s]:.0f}% {s}" for s in SENTIMENTS)
        text(shares, 32, INK)
        y += 8
        weighted = ", ".join(f"{analysis.weighted[s]:.0f}% {s}" for s in SENTIMENTS)
        text(f"Weighted by likes: {weighted}", 26, MUTED)
        y += 48
        if analysis.issues:
            text("Negative comments mention most", 30, MUTED, bold=True)
            y += 12
            for issue in analysis.issues[:3]:
                if not text(f'"{issue.term}"  ({issue.mentions} comments)', 38, INK):
                    break
                y += 6
    else:
        text("Not analysed yet", 64, MUTED, bold=True)
    painter.end()
    return image


def _shorten(text: str, length: int) -> str:
    """Cut at a word boundary, with an ellipsis, so a long title can't push the card over."""
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-|") + "…"


def save_card(image: QImage, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    jpeg, png = folder / "card.jpg", folder / "card.png"
    image.save(str(jpeg), "JPEG", 92)  # type: ignore[call-overload]
    image.save(str(png), "PNG")  # type: ignore[call-overload]
    return [jpeg, png]
