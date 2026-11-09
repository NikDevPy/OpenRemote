"""Small first-party vector icon set for the OpenRemote desktop dashboard.

Icons are painted in Qt, not system-font glyphs, so they render consistently on
GNOME, KDE, X11, Wayland and in offscreen visual tests.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout


def icon_pixmap(name: str, color: str, px: int = 26) -> QPixmap:
    size = max(20, px)
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(size / 2, size / 2)
    p.scale(size / 28, size / 28)
    p.setPen(QPen(QColor(color), 2.1, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    def line(x1,y1,x2,y2): p.drawLine(x1,y1,x2,y2)
    if name == "monitor":
        p.drawRoundedRect(QRectF(-11,-9,22,16),2,2)
        line(0,7,0,11); line(-6,11,6,11)
    elif name == "server":
        p.drawRoundedRect(QRectF(-10,-10,20,8),2,2)
        p.drawRoundedRect(QRectF(-10,2,20,8),2,2)
        p.drawEllipse(QRectF(-7,-7,2.5,2.5))
        p.drawEllipse(QRectF(-7,5,2.5,2.5))
        line(3,-6,7,-6);line(3,6,7,6)
    elif name == "folder":
        p.drawRoundedRect(QRectF(-11,-7,22,17),2,2)
        line(-10,-7,-5,-11);line(-5,-11,0,-11);line(0,-11,3,-7)
    elif name == "cpu":
        p.drawRoundedRect(QRectF(-8,-8,16,16),2,2)
        for k in (-5,0,5):
            line(k,-12,k,-8);line(k,8,k,12);line(-12,k,-8,k);line(8,k,12,k)
    elif name == "jobs":
        p.drawRoundedRect(QRectF(-8,-10,17,21),2,2)
        line(-4,-5,5,-5);line(-4,0,5,0);line(-4,5,2,5)
        line(-3,-12,3,-12)
    elif name == "transfers":
        line(-12,-5,11,-5);line(7,-9,11,-5);line(7,-1,11,-5)
        line(12,6,-11,6);line(-7,2,-11,6);line(-7,10,-11,6)
    elif name == "settings":
        p.drawEllipse(QRectF(-7,-7,14,14))
        p.drawEllipse(QRectF(-2.5,-2.5,5,5))
        for k in range(8):
            import math
            a=k*math.pi/4
            line(9*math.cos(a),9*math.sin(a),12*math.cos(a),12*math.sin(a))
    elif name == "refresh":
        p.drawArc(QRectF(-10,-10,20,20),15*16,160*16)
        p.drawArc(QRectF(-10,-10,20,20),195*16,160*16)
        line(-10,-3,-10,-9);line(-10,-9,-4,-9)
        line(10,3,10,9);line(10,9,4,9)
    elif name == "copy":
        p.drawRoundedRect(QRectF(-9,-10,14,17),2,2)
        p.drawRoundedRect(QRectF(-4,-5,14,17),2,2)
    elif name == "shield":
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        p.drawPolygon(QPolygonF([QPointF(0,-12),QPointF(10,-7),QPointF(8,4),
                                 QPointF(0,12),QPointF(-8,4),QPointF(-10,-7)]))
        line(-4,0,-1,4);line(-1,4,5,-3)
    elif name == "check":
        line(-8,0,-2,6);line(-2,6,9,-7)
    elif name == "chevron":
        line(-5,-2,0,3);line(0,3,5,-2)
    else:
        p.drawEllipse(QRectF(-8,-8,16,16))
    p.end()
    return pix


def icon(name: str, color: str, px: int = 26) -> QIcon:
    return QIcon(icon_pixmap(name, color, px))


class IconBadge(QWidget):
    def __init__(self, name: str, foreground: str, background: str,
                 size: int = 52, parent=None):
        super().__init__(parent)
        self.name = name
        self.fg = foreground
        self.bg = background
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.bg))
        p.drawRoundedRect(self.rect().adjusted(1,1,-1,-1), 15, 15)
        p.end()
        pix = icon_pixmap(self.name, self.fg, int(self.width()*.54))
        painter = QPainter(self)
        painter.drawPixmap((self.width()-pix.width())//2,
                           (self.height()-pix.height())//2, pix)
        painter.end()
