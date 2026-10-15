"""Membuat assets/icon.png dan assets/icon.ico (PNG tertanam, valid sejak Windows Vista). Jalankan: python build/make_icon.py"""
import os, struct, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from PySide6 import QtCore, QtGui

root = Path(__file__).resolve().parents[1]
app = QtGui.QGuiApplication([])


def render(n):
    img = QtGui.QImage(n, n, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    r = QtCore.QRectF(n * .04, n * .04, n * .92, n * .92)
    g = QtGui.QLinearGradient(0, 0, 0, n)
    g.setColorAt(0, QtGui.QColor("#3b82f6")); g.setColorAt(1, QtGui.QColor("#1e3a8a"))
    p.setBrush(g); p.setPen(QtCore.Qt.NoPen)
    p.drawRoundedRect(r, n * .2, n * .2)
    # tanda play
    tri = QtGui.QPolygonF([QtCore.QPointF(n * .38, n * .26), QtCore.QPointF(n * .38, n * .62), QtCore.QPointF(n * .68, n * .44)])
    p.setBrush(QtGui.QColor("white")); p.drawPolygon(tri)
    # film strip bawah
    p.setBrush(QtGui.QColor(255, 255, 255, 235))
    p.drawRoundedRect(QtCore.QRectF(n * .16, n * .72, n * .68, n * .14), n * .03, n * .03)
    p.setBrush(QtGui.QColor("#1e3a8a"))
    for i in range(6):
        p.drawRoundedRect(QtCore.QRectF(n * (.20 + i * .108), n * .755, n * .06, n * .07), n * .01, n * .01)
    p.end()
    return img


def png_bytes(img):
    ba = QtCore.QByteArray(); buf = QtCore.QBuffer(ba); buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG"); return bytes(ba)


sizes = [16, 24, 32, 48, 64, 128, 256]
render(256).save(str(root / "assets" / "icon.png"))
blobs = [png_bytes(render(n)) for n in sizes]
out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
off = 6 + 16 * len(sizes)
for n, b in zip(sizes, blobs):
    out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(b), off)
    off += len(b)
for b in blobs:
    out += b
(root / "assets" / "icon.ico").write_bytes(bytes(out))
print("ok", len(out), "byte")
