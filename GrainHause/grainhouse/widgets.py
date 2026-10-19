"""Custom widgets: tone-curve editor and the preview with crop guides and eyedropper."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

ACCENT = "#e4e7ec"
CURVE_COLOURS = {"rgb": "#e6e6e6", "r": "#ff6b5e", "g": "#6be08a", "b": "#6aa8ff"}

ASPECT_RATIOS = {"1:1": 1.0, "4:5": 4 / 5, "9:16": 9 / 16, "3:2": 3 / 2, "16:9": 16 / 9, "1.91:1": 1.91}


# ====================================================================== tone curve

class CurveEditor(QWidget):
    """Drag points to shape the curve. Double-click empty space to add a point, double-click a point to remove."""
    changed = Signal(str, list)  # channel, points

    def __init__(self):
        super().__init__()
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(260)
        self.channel = "rgb"
        self.curves = {k: [[0.0, 0.0], [1.0, 1.0]] for k in CURVE_COLOURS}
        self._drag = None
        self.setMouseTracking(True)

    def set_curves(self, curves: dict):
        for k in CURVE_COLOURS:
            self.curves[k] = [list(p) for p in curves.get(k) or [[0.0, 0.0], [1.0, 1.0]]]
        self.update()

    def set_channel(self, ch):
        self.channel = ch
        self.update()

    def _rect(self):
        m = 10
        side = min(self.width(), self.height()) - 2 * m
        return QRectF((self.width() - side) / 2, m, side, side)

    def _to_px(self, x, y):
        r = self._rect()
        return QPointF(r.left() + x * r.width(), r.bottom() - y * r.height())

    def _from_px(self, p):
        r = self._rect()
        return (float(np.clip((p.x() - r.left()) / r.width(), 0, 1)),
                float(np.clip((r.bottom() - p.y()) / r.height(), 0, 1)))

    def _eval(self, pts, n=128):
        from .engine import _curve_lut
        lut = _curve_lut(tuple(map(tuple, pts)))
        xs = np.linspace(0, 1, n)
        return xs, lut[(xs * 4095).astype(int)]

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self._rect()
        p.fillRect(r, QColor("#141416"))
        p.setPen(QPen(QColor("#2c2c31"), 1))
        for i in range(1, 4):
            p.drawLine(QPointF(r.left() + r.width() * i / 4, r.top()), QPointF(r.left() + r.width() * i / 4, r.bottom()))
            p.drawLine(QPointF(r.left(), r.top() + r.height() * i / 4), QPointF(r.right(), r.top() + r.height() * i / 4))
        p.setPen(QPen(QColor("#3a3a40"), 1, Qt.DashLine))
        p.drawLine(self._to_px(0, 0), self._to_px(1, 1))
        # inactive channels faintly
        for ch, col in CURVE_COLOURS.items():
            pts = self.curves[ch]
            active = ch == self.channel
            if not active and len(pts) == 2 and pts == [[0.0, 0.0], [1.0, 1.0]]:
                continue
            xs, ys = self._eval(pts)
            path = QPainterPath(self._to_px(xs[0], ys[0]))
            for x, y in zip(xs[1:], ys[1:]):
                path.lineTo(self._to_px(x, y))
            c = QColor(col)
            c.setAlpha(255 if active else 70)
            p.setPen(QPen(c, 2 if active else 1))
            p.drawPath(path)
        p.setPen(QPen(QColor("#111"), 1))
        p.setBrush(QColor(CURVE_COLOURS[self.channel]))
        for x, y in self.curves[self.channel]:
            p.drawEllipse(self._to_px(x, y), 5, 5)
        p.end()

    def _hit(self, pos):
        for i, (x, y) in enumerate(self.curves[self.channel]):
            q = self._to_px(x, y)
            if (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2 < 100:
                return i
        return None

    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if i is None:
            x, y = self._from_px(e.position())
            pts = self.curves[self.channel]
            pts.append([x, y])
            pts.sort(key=lambda p: p[0])
            i = pts.index([x, y])
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        pts = self.curves[self.channel]
        x, y = self._from_px(e.position())
        i = self._drag
        lo = pts[i - 1][0] + 0.01 if i > 0 else 0.0
        hi = pts[i + 1][0] - 0.01 if i < len(pts) - 1 else 1.0
        if i == 0:
            x = min(x, hi)
        elif i == len(pts) - 1:
            x = max(x, lo)
        else:
            x = float(np.clip(x, lo, hi))
        pts[i] = [x, y]
        self.update()
        self.changed.emit(self.channel, [list(p) for p in pts])

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self.changed.emit(self.channel, [list(p) for p in self.curves[self.channel]])
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        i = self._hit(e.position())
        pts = self.curves[self.channel]
        if i is not None and 0 < i < len(pts) - 1:
            pts.pop(i)
            self._drag = None
            self.update()
            self.changed.emit(self.channel, [list(p) for p in pts])


# ====================================================================== preview

class PreviewView(QWidget):
    """The developed photo, with zoom and pan.

    zoom = None means "fit"; otherwise it is device pixels per full-resolution image pixel
    (1.0 = 100%). While zoomed past the preview's own resolution the window renders a sharp
    full-resolution tile for the visible area; the softer preview fills in around it while you pan.

    view mode:       drag to pan · double-click toggles Fit / 100% · pinch or ⌘-scroll to zoom
    crop mode:       draggable crop with aspect lock and rule-of-thirds guides (always at Fit)
    eyedropper mode: click something that should be neutral grey or white
    """
    view_changed = Signal()                # zoom or pan moved
    crop_changed = Signal(object)          # (x0, y0, x1, y1) normalised
    picked = Signal(float, float)          # normalised image coords
    sample_at = Signal(float, float)       # where the user last looked closely (normalised)

    MAX_ZOOM = 8.0

    def __init__(self):
        super().__init__()
        self.image: QImage | None = None
        self.tile: QImage | None = None
        self.show_tile = True
        self.tile_rect = None                # (x0, y0, x1, y1) in full image px
        self.message = "Open a photo to start  ·  RAW, TIFF, JPEG"
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.badge = ""
        self.mode = "view"
        self.crop = [0.0, 0.0, 1.0, 1.0]
        self.ratio = None
        self.img_aspect = 1.5
        self.full_size = None                # (W, H) of the (cropped) full-resolution image
        self.zoom = None
        self.center = None
        self._drag = None
        self._pan = None

    # ------------------------------------------------------------------ state
    def set_image(self, img):
        self.image = img
        # draw from a pixmap: the window system can scale it far faster than a QImage while zooming
        self._pm = QPixmap.fromImage(img) if img is not None else None
        self.update()

    def set_full_size(self, size):
        if size != self.full_size:
            self.full_size = size
            self.tile = None
            self._clamp()
            self.update()

    def set_tile(self, img, rect):
        self.tile, self.tile_rect = img, rect
        self._tile_pm = QPixmap.fromImage(img) if img is not None else None
        self.update()

    def clear_tile(self):
        self.tile = None
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        if mode == "crop":
            self.zoom = None
        self.setCursor(Qt.CrossCursor if mode == "eyedropper" else Qt.ArrowCursor)
        self.update()

    def set_crop(self, crop, ratio, img_aspect):
        self.crop = list(crop) if crop else [0.0, 0.0, 1.0, 1.0]
        self.ratio = ratio
        self.img_aspect = img_aspect
        self.update()

    # ------------------------------------------------------------------ geometry
    def _size(self):
        if self.full_size:
            return self.full_size
        if self.image is not None:
            return self.image.width(), self.image.height()
        return 1, 1

    def fit_scale(self):
        """Logical px per image px at Fit."""
        W, H = self._size()
        return min((self.width() - 32) / W, (self.height() - 32) / H)

    def scale(self):
        return self.fit_scale() if self.zoom is None else self.zoom / self.devicePixelRatioF()

    def zoom_device(self):
        return self.scale() * self.devicePixelRatioF()

    def _centre(self):
        W, H = self._size()
        if self.zoom is None or self.center is None:
            return W / 2, H / 2
        return self.center

    def to_screen(self, x, y):
        cx, cy = self._centre()
        z = self.scale()
        return QPointF((x - cx) * z + self.width() / 2, (y - cy) * z + self.height() / 2)

    def to_image(self, p):
        cx, cy = self._centre()
        z = self.scale()
        return (p.x() - self.width() / 2) / z + cx, (p.y() - self.height() / 2) / z + cy

    def image_rect(self):
        W, H = self._size()
        a, b = self.to_screen(0, 0), self.to_screen(W, H)
        return QRectF(a, b)

    def _fit_rect(self):
        return self.image_rect() if self.image is not None else None

    def visible_region(self, margin=0.08):
        """Visible part of the image in full px, padded a little for panning."""
        W, H = self._size()
        x0, y0 = self.to_image(QPointF(0, 0))
        x1, y1 = self.to_image(QPointF(self.width(), self.height()))
        mx, my = (x1 - x0) * margin, (y1 - y0) * margin
        x0, y0 = max(0, int(x0 - mx)), max(0, int(y0 - my))
        x1, y1 = min(W, int(np.ceil(x1 + mx))), min(H, int(np.ceil(y1 + my)))
        return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)

    def _clamp(self):
        if self.zoom is None or self.center is None:
            return
        W, H = self._size()
        z = self.scale()
        hw, hh = self.width() / (2 * z), self.height() / (2 * z)
        cx = W / 2 if hw * 2 >= W else float(np.clip(self.center[0], hw, W - hw))
        cy = H / 2 if hh * 2 >= H else float(np.clip(self.center[1], hh, H - hh))
        self.center = (cx, cy)

    # ------------------------------------------------------------------ zoom API
    def set_zoom(self, z, anchor=None):
        """z in device px per image px (1.0 = 100%), None = Fit. anchor: screen point to keep still."""
        if self.mode == "crop" or self.image is None:
            if self.zoom is not None:
                self.zoom, self.center = None, None
                self.update()
                self.view_changed.emit()
            return
        fit_dev = self.fit_scale() * self.devicePixelRatioF()
        if z is not None:
            z = float(np.clip(z, min(fit_dev, 1.0) * 0.999, self.MAX_ZOOM))
            if z <= fit_dev * 1.001:
                z = None
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        ix, iy = self.to_image(anchor)
        self.zoom = z
        if z is not None:
            zl = z / self.devicePixelRatioF()
            self.center = (ix - (anchor.x() - self.width() / 2) / zl, iy - (anchor.y() - self.height() / 2) / zl)
            self._clamp()
        else:
            self.center = None
        self.update()
        self.view_changed.emit()

    def zoom_by(self, factor, anchor=None):
        self.set_zoom(self.zoom_device() * factor, anchor)

    def zoom_percent(self):
        return int(round(self.zoom_device() * 100))

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111113"))
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.image is not None:
            r = self.image_rect()
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            pm = getattr(self, "_pm", None)
            if pm is not None and not pm.isNull():
                p.drawPixmap(r, pm, QRectF(pm.rect()))
            else:
                p.drawImage(r, self.image)
            if self.show_tile and self.tile is not None and self.tile_rect is not None and self.zoom is not None:
                x0, y0, x1, y1 = self.tile_rect
                tr = QRectF(self.to_screen(x0, y0), self.to_screen(x1, y1))
                # past 200% show real pixels, not a blur
                p.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom_device() < 2.0)
                tpm = getattr(self, "_tile_pm", None)
                if tpm is not None and not tpm.isNull():
                    p.drawPixmap(tr, tpm, QRectF(tpm.rect()))
                else:
                    p.drawImage(tr, self.tile)
            if self.mode == "crop":
                self._paint_crop(p, r)
        else:
            p.setPen(QColor("#777"))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
        if self.badge:
            p.setPen(QColor(ACCENT))
            p.drawText(14, 22, self.badge)
        if self.mode == "eyedropper":
            p.setPen(QColor(ACCENT))
            p.drawText(14, self.height() - 14, "Click something that should be neutral grey or white  ·  Esc to cancel")
        p.end()

    def _crop_px(self):
        r = self.image_rect()
        x0, y0, x1, y1 = self.crop
        return QRectF(r.left() + x0 * r.width(), r.top() + y0 * r.height(),
                      (x1 - x0) * r.width(), (y1 - y0) * r.height())

    def _paint_crop(self, p, r):
        c = self._crop_px()
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), c.top() - r.top()), shade)
        p.fillRect(QRectF(r.left(), c.bottom(), r.width(), r.bottom() - c.bottom()), shade)
        p.fillRect(QRectF(r.left(), c.top(), c.left() - r.left(), c.height()), shade)
        p.fillRect(QRectF(c.right(), c.top(), r.right() - c.right(), c.height()), shade)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for i in (1, 2):
            x = c.left() + c.width() * i / 3
            y = c.top() + c.height() * i / 3
            p.drawLine(QPointF(x, c.top()), QPointF(x, c.bottom()))
            p.drawLine(QPointF(c.left(), y), QPointF(c.right(), y))
        p.setPen(QPen(QColor(ACCENT), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(c)
        p.setBrush(QColor(ACCENT))
        for pt in (c.topLeft(), c.topRight(), c.bottomLeft(), c.bottomRight()):
            p.drawRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10))
        W = (self.crop[2] - self.crop[0]) * self.img_aspect
        H = self.crop[3] - self.crop[1]
        p.setPen(QColor(ACCENT))
        p.drawText(QPointF(c.left() + 6, c.top() - 6), f"{W / H:.2f} : 1")

    # ------------------------------------------------------------------ input
    def event(self, e):
        if e.type() == QEvent.NativeGesture and self.mode != "crop":
            if e.gestureType() == Qt.ZoomNativeGesture:
                self.zoom_by(1.0 + e.value(), e.position())
                return True
            if e.gestureType() == Qt.SmartZoomNativeGesture:
                self.set_zoom(None if self.zoom else 1.0, e.position())
                return True
        return super().event(e)

    def wheelEvent(self, e):
        if self.image is None or self.mode == "crop":
            return
        mods = e.modifiers()
        if mods & (Qt.ControlModifier | Qt.MetaModifier):
            dy = e.angleDelta().y() or e.pixelDelta().y()
            self.zoom_by(2 ** (dy / 360.0), e.position())
        elif self.zoom is not None:
            d = e.pixelDelta() if not e.pixelDelta().isNull() else e.angleDelta() / 2
            z = self.scale()
            cx, cy = self._centre()
            self.center = (cx - d.x() / z, cy - d.y() / z)
            self._clamp()
            self.update()
            self.view_changed.emit()

    def mousePressEvent(self, e):
        if self.image is None:
            return
        pos = e.position()
        if self.mode == "eyedropper":
            W, H = self._size()
            x, y = self.to_image(pos)
            if 0 <= x <= W and 0 <= y <= H:
                self.picked.emit(x / W, y / H)
            return
        if self.mode == "crop":
            c = self._crop_px()
            corners = {"tl": c.topLeft(), "tr": c.topRight(), "bl": c.bottomLeft(), "br": c.bottomRight()}
            for k, pt in corners.items():
                if abs(pt.x() - pos.x()) < 14 and abs(pt.y() - pos.y()) < 14:
                    self._drag = (k, pos, list(self.crop))
                    return
            if c.contains(pos):
                self._drag = ("move", pos, list(self.crop))
            return
        if self.zoom is not None:
            self._pan = (pos, self._centre())
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            start, (cx, cy) = self._pan
            z = self.scale()
            self.center = (cx - (e.position().x() - start.x()) / z, cy - (e.position().y() - start.y()) / z)
            self._clamp()
            self.update()
            return
        if not self._drag or self.mode != "crop":
            if self.zoom is not None and self.mode == "view":
                self.setCursor(Qt.OpenHandCursor)
            return
        kind, start, orig = self._drag
        r = self.image_rect()
        dx = (e.position().x() - start.x()) / r.width()
        dy = (e.position().y() - start.y()) / r.height()
        x0, y0, x1, y1 = orig
        if kind == "move":
            w, h = x1 - x0, y1 - y0
            nx0 = float(np.clip(x0 + dx, 0, 1 - w))
            ny0 = float(np.clip(y0 + dy, 0, 1 - h))
            self.crop = [nx0, ny0, nx0 + w, ny0 + h]
        else:
            ax = x1 if "l" in kind else x0
            ay = y1 if "t" in kind else y0
            mx = float(np.clip((x0 if "l" in kind else x1) + dx, 0, 1))
            my = float(np.clip((y0 if "t" in kind else y1) + dy, 0, 1))
            w, h = abs(mx - ax), abs(my - ay)
            if self.ratio:
                want_h = w * self.img_aspect / self.ratio
                if want_h <= h:
                    h = want_h
                else:
                    w = h * self.ratio / self.img_aspect
                sx = -1 if "l" in kind else 1
                sy = -1 if "t" in kind else 1
                maxw = ax if sx < 0 else 1 - ax
                maxh = ay if sy < 0 else 1 - ay
                k = min(1.0, maxw / max(w, 1e-6), maxh / max(h, 1e-6))
                w, h = w * k, h * k
                mx, my = ax + sx * w, ay + sy * h
            w, h = abs(mx - ax), abs(my - ay)
            if w < 0.03 or h < 0.03:
                return
            self.crop = [min(ax, mx), min(ay, my), max(ax, mx), max(ay, my)]
        self.update()

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.OpenHandCursor)
            self.view_changed.emit()
        if self._drag:
            self._drag = None
            self.crop_changed.emit(tuple(self.crop))

    def mouseDoubleClickEvent(self, e):
        if self.mode != "view" or self.image is None:
            return
        W, H = self._size()
        x, y = self.to_image(e.position())
        if 0 <= x <= W and 0 <= y <= H:
            self.sample_at.emit(x / W, y / H)
        self.set_zoom(None if self.zoom is not None else 1.0, e.position())

    def resizeEvent(self, e):
        self._clamp()
        super().resizeEvent(e)
        if self.zoom is not None:
            self.view_changed.emit()


def fit_aspect(ratio: float, img_aspect: float, around=None):
    """Largest crop of `ratio` (W/H in pixels), centred on `around` (or the frame centre)."""
    # normalised width w, height h satisfy (w * img_aspect) / h = ratio
    w, h = 1.0, img_aspect / ratio
    if h > 1:
        w, h = ratio / img_aspect, 1.0
    cx, cy = around if around else (0.5, 0.5)
    x0 = float(np.clip(cx - w / 2, 0, 1 - w))
    y0 = float(np.clip(cy - h / 2, 0, 1 - h))
    return [x0, y0, x0 + w, y0 + h]


# ====================================================================== histogram

class Histogram(QWidget):
    """RGB + luminance histogram of the developed image, with clipping warnings."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.hist = None
        self.clip_lo = self.clip_hi = 0.0
        self.setToolTip("Histogram of the developed image (red, green, blue and brightness).\n"
                        "Corner triangles light up when shadows or highlights clip.")

    def set_image(self, arr):
        """arr: (h, w, 3) uint8 sRGB."""
        if arr is None:
            self.hist = None
            self.update()
            return
        a = arr[::2, ::2].reshape(-1, 3) if arr.shape[0] * arr.shape[1] > 400_000 else arr.reshape(-1, 3)
        L = (a[:, 0] * 0.2126 + a[:, 1] * 0.7152 + a[:, 2] * 0.0722).astype(np.uint8)
        h = [np.bincount(a[:, c], minlength=256).astype(np.float32) for c in range(3)]
        h.append(np.bincount(L, minlength=256).astype(np.float32))
        h = np.stack(h)
        n = float(a.shape[0])
        self.clip_lo = float(np.mean(np.all(a <= 1, axis=1)))
        self.clip_hi = float(np.mean(np.any(a >= 254, axis=1)))
        # smooth a touch and scale by a high percentile so a spike doesn't flatten everything
        k = np.array([0.25, 0.5, 0.25], np.float32)
        h = np.stack([np.convolve(x, k, mode="same") for x in h])
        top = np.percentile(h[:, 2:254], 99.5) or 1.0
        self.hist = np.clip(h / (top * 1.15), 0, 1)
        self.n = n
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.fillRect(r, QColor("#141416"))
        p.setPen(QPen(QColor("#26262b"), 1))
        for i in (1, 2, 3):
            x = r.left() + r.width() * i / 4
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        if self.hist is not None:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            for c, col in enumerate(((210, 60, 55), (60, 180, 75), (60, 105, 220))):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*col, 150))
                p.drawPath(self._path(self.hist[c], r))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(235, 235, 235, 170), 1))
            p.drawPath(self._path(self.hist[3], r, closed=False))
            for on, left in ((self.clip_lo > 0.002, True), (self.clip_hi > 0.002, False)):
                pts = ([QPointF(r.left() + 3, r.top() + 3), QPointF(r.left() + 13, r.top() + 3),
                        QPointF(r.left() + 3, r.top() + 13)] if left else
                       [QPointF(r.right() - 3, r.top() + 3), QPointF(r.right() - 13, r.top() + 3),
                        QPointF(r.right() - 3, r.top() + 13)])
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(ACCENT) if on else QColor("#3a3a40"))
                p.drawPolygon(pts)
        p.setPen(QPen(QColor("#2c2c31"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 4, 4)
        p.end()

    @staticmethod
    def _path(h, r, closed=True):
        xs = r.left() + np.arange(256) / 255.0 * r.width()
        ys = r.bottom() - 2 - h * (r.height() - 6)
        path = QPainterPath(QPointF(xs[0], r.bottom() if closed else ys[0]))
        for x, y in zip(xs, ys):
            path.lineTo(QPointF(x, y))
        if closed:
            path.lineTo(QPointF(xs[-1], r.bottom()))
            path.closeSubpath()
        return path
