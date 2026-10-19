"""GrainHause desktop app (PySide6)."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressDialog,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter, QStackedWidget, QStatusBar, QToolButton, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__, color, engine, film, imageio, settings as gs, stocks, textures
from .widgets import ASPECT_RATIOS, CurveEditor, Histogram, PreviewView, fit_aspect
from .settings import Settings

PREVIEW_EDGE = 1800
THUMB_EDGE = 600
ACCENT = "#e4e7ec"        # chrome silver, from the GH logo
ACCENT_DIM = "#8d929b"
CHROME = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbfcfe, stop:0.48 #c9ced6, stop:0.52 #8f959f, stop:1 #e8ebf0)"
BRAND_FONT = "Unbounded"


# ====================================================================== threading

class _Signals(QObject):
    done = Signal(object, object)   # (token, result)
    item = Signal(object, object)   # (token, partial result)
    failed = Signal(object, str)
    progress = Signal(object, float)


class Job(QRunnable):
    def __init__(self, token, fn, *args, **kw):
        super().__init__()
        self.token, self.fn, self.args, self.kw = token, fn, args, kw
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self):
        try:
            res = self.fn(*self.args, **self.kw)
            self.signals.done.emit(self.token, res)
        except Exception:
            self.signals.failed.emit(self.token, traceback.format_exc())


FIELD_NAMES = {
    "stock": "film stock", "strength": "film strength", "auto_meter": "auto-meter", "grain_roughness": "grain roughness",
    "grain_size": "grain size", "wb_temp": "temperature", "wb_tint": "tint", "leak_amount": "leak amount",
    "leak_seed": "leak shuffle", "texture_amount": "texture amount", "curve_rgb": "tone curve", "curve_r": "red curve",
    "curve_g": "green curve", "curve_b": "blue curve", "seed": "grain pattern", "lab": "lab process",
    "denoise": "digital noise cleanup",
}


def describe_change(a, b) -> str:
    da, db = a.to_dict(), b.to_dict()
    diff = [k for k in da if da[k] != db.get(k)]
    if not diff:
        return "change"
    if len(diff) > 3:
        return f"{len(diff)} changes"
    return ", ".join(FIELD_NAMES.get(k, k.replace("_", " ")) for k in diff)


class SwatchView(QWidget):
    """Grain sample. Holds a render of an area a few times larger than the window, so dragging just
    slides over pixels that already exist; never asks the panel for width."""
    dragged = Signal(float, float)
    released = Signal()

    def __init__(self):
        super().__init__()
        self._img = None
        self._origin = (0.0, 0.0)      # rendered area's top-left, in source px
        self._centre = None            # what the window is centred on, in source px
        self._last = None
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedHeight(140)
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(120, 140)

    def minimumSizeHint(self):
        return QSize(1, 140)

    def set_image(self, img, origin=(0.0, 0.0), centre=None):
        self._img = img
        self._origin = origin
        self._centre = centre
        self.update()

    def set_centre(self, centre):
        self._centre = centre
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._last = ev.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if self._last is not None:
            p = ev.position()
            dx, dy = p.x() - self._last.x(), p.y() - self._last.y()
            self._last = p
            if dx or dy:
                self.dragged.emit(dx, dy)

    def mouseReleaseEvent(self, ev):
        self._last = None
        self.setCursor(Qt.OpenHandCursor)
        self.released.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#0b0b0d"))
        if self._img is not None and not self._img.isNull():
            dpr = self._img.devicePixelRatio() or 1.0
            if self._centre is not None:
                x = r.width() / 2 - (self._centre[0] - self._origin[0]) / dpr
                y = r.height() / 2 - (self._centre[1] - self._origin[1]) / dpr
            else:
                x = (r.width() - self._img.width() / dpr) / 2
                y = (r.height() - self._img.height() / dpr) / 2
            p.setClipRect(r)
            p.drawImage(QPointF(x, y), self._img)
        p.setPen(QColor(255, 255, 255, 40))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.end()


class WheelToScroll(QObject):
    """Scrolling over a slider, dropdown or number box scrolls the panel instead of changing the value."""

    def __init__(self, scroll_area):
        super().__init__(scroll_area)
        self.area = scroll_area

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Wheel:
            QApplication.sendEvent(self.area.verticalScrollBar(), e)
            return True
        return False


def to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


# ====================================================================== photo model

class Photo:
    def __init__(self, path: str):
        self.path = path
        self.preview: imageio.Source | None = None
        self.full: imageio.Source | None = None
        self.meter_ev: float | None = None
        self.settings = gs.load_sidecar(path) or None
        self.thumb: QPixmap | None = None
        self.lightroom = _looks_like_lightroom([path])
        self.before_img = None      # pre-rendered original for instant before/after
        self.before_key = None
        self.after_img = None
        self.undo: list[Settings] = []
        self.redo: list[Settings] = []
        self.committed: Settings | None = None

    @property
    def name(self):
        return os.path.basename(self.path)


# ====================================================================== widgets

class Slider(QWidget):
    """Label + slider + numeric box + reset. Double-click the label or press ↺ to reset."""
    changed = Signal(float)
    reset_requested = Signal()

    def __init__(self, label, lo, hi, default=0.0, step=1.0, decimals=0, suffix=""):
        super().__init__()
        self.lo, self.hi, self.step, self._default = lo, hi, step, default
        self._mute = False
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(0)
        self.label = QLabel(label)
        self.label.setToolTip("Double-click to reset")
        self.label.mouseDoubleClickEvent = lambda e: self.reset()
        self.box = QDoubleSpinBox()
        self.box.setRange(lo, hi)
        self.box.setDecimals(decimals)
        self.box.setSingleStep(step)
        self.box.setSuffix(suffix)
        self.box.setFixedWidth(78)
        self.box.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.box.setAlignment(Qt.AlignRight)
        self.box.setFocusPolicy(Qt.ClickFocus)
        self.box.setKeyboardTracking(False)
        self.box.editingFinished.connect(self.box.clearFocus)
        self.btn_reset = QToolButton(text="↺")
        self.btn_reset.setObjectName("resetBtn")
        self.btn_reset.setToolTip(f"Reset {label.lower()}")
        self.btn_reset.setFixedSize(22, 20)
        self.btn_reset.setFocusPolicy(Qt.NoFocus)
        self.btn_reset.clicked.connect(self.reset)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.slider.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.label, 0, 0)
        lay.addWidget(self.box, 0, 1, Qt.AlignRight)
        lay.addWidget(self.btn_reset, 0, 2)
        lay.addWidget(self.slider, 1, 0, 1, 3)
        lay.setColumnStretch(0, 1)
        self.slider.valueChanged.connect(self._from_slider)
        self.box.valueChanged.connect(self._from_box)
        self.set(default, emit=False)

    @property
    def default(self):
        return self._default

    @default.setter
    def default(self, v):
        self._default = v
        self._update_reset()

    def value(self) -> float:
        return float(self.box.value())

    def _update_reset(self):
        self.btn_reset.setEnabled(abs(self.value() - self._default) > self.step * 0.5)

    def set(self, v, emit=True):
        self._mute = not emit
        v = float(np.clip(v, self.lo, self.hi))
        self.box.blockSignals(True)
        self.box.setValue(v)
        self.box.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self._mute = False
        self._update_reset()
        if emit:
            self.changed.emit(v)

    def reset(self):
        # the window decides what "default" means (e.g. "the stock's own halation")
        if self.receivers(SIGNAL_RESET) > 0:
            self.reset_requested.emit()
        else:
            self.set(self._default)

    def _from_slider(self, i):
        v = self.lo + i * self.step
        self.box.blockSignals(True)
        self.box.setValue(v)
        self.box.blockSignals(False)
        self._update_reset()
        if not self._mute:
            self.changed.emit(v)

    def _from_box(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self._update_reset()
        if not self._mute:
            self.changed.emit(v)


SIGNAL_RESET = "2reset_requested()"


class Section(QWidget):
    reset_clicked = Signal()

    def __init__(self, title, resettable=True):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 10, 0, 4)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        head = QLabel(title.upper())
        head.setStyleSheet(f"color:{ACCENT}; font-family:'{BRAND_FONT}'; font-weight:600; letter-spacing:2px; font-size:10px;")
        top.addWidget(head)
        top.addStretch(1)
        if resettable:
            b = QToolButton(text="Reset")
            b.setObjectName("sectionReset")
            b.setToolTip(f"Reset everything in {title}")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(self.reset_clicked.emit)
            top.addWidget(b)
        v.addLayout(top)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#333;")
        v.addWidget(line)
        self.body = v

    def add(self, w):
        self.body.addWidget(w)
        return w


# ====================================================================== main window

class MainWindow(QMainWindow):
    def __init__(self, paths=None, lightroom=False):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — film developer")
        self.resize(1500, 920)
        self.pool = QThreadPool.globalInstance()
        self.swatch_pool = QThreadPool(self)        # the grain sample never waits behind a big render
        self.swatch_pool.setMaxThreadCount(1)
        self.thumb_pool = QThreadPool(self)         # stock thumbnails, several at once
        self.thumb_pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 4) // 2)))
        self.photos: list[Photo] = []
        self.current: Photo | None = None
        self.s = Settings()
        self.lightroom = lightroom
        self.presets = gs.load_presets()
        self.prefs = gs.load_prefs()
        self.clipboard: Settings | None = None
        self._gen = 0
        self._busy = False
        self._pending = False
        self._before = False
        self._rev = 0
        self._preview_rev = -1
        self._tile_info = None
        self._thumb_gen = 0
        self._building = False

        self.zoom_timer = QTimer(self, singleShot=True, interval=140)
        self.zoom_timer.timeout.connect(self._render_now)
        self.render_timer = QTimer(self, singleShot=True, interval=40)
        self.render_timer.timeout.connect(self._render_now)
        self.save_timer = QTimer(self, singleShot=True, interval=600)
        self.save_timer.timeout.connect(self._save_sidecar)
        self.idle_timer = QTimer(self, singleShot=True, interval=320)
        self.idle_timer.timeout.connect(self._settled)
        self._dragging = False
        self._gesture = False
        self._last_field = None
        self._swatch_busy = False
        self._swatch_pending = False
        self._swatch_center = None
        self._swatch_manual = False
        self._swatch_geom = None
        self._tile_gen = 0
        self.swatch_timer = QTimer(self, singleShot=True, interval=15)
        self.swatch_timer.timeout.connect(self._render_swatch)
        self.thumb_timer = QTimer(self, singleShot=True, interval=700)
        self.thumb_timer.timeout.connect(self._render_stock_thumbs)

        self._build_ui()
        QTimer.singleShot(200, self._render_swatch)   # a film sample even before a photo is open
        self._wire_resets()
        self._build_menu()
        self._sync_controls()
        if paths:
            self.open_paths(paths)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        split = QSplitter(Qt.Horizontal)
        self.setCentralWidget(split)

        # --- left: photos
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 6, 8)
        lv.setSpacing(8)
        brand = QHBoxLayout()
        logo = QLabel()
        icon_path = _asset("icon.png")
        if icon_path:
            pm = QPixmap(icon_path)
            pm.setDevicePixelRatio(2.0)
            logo.setPixmap(pm.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        word = QLabel("GRAINHAUSE")
        word.setObjectName("brand")
        brand.addWidget(logo)
        brand.addWidget(word)
        brand.addStretch(1)
        lv.addLayout(brand)
        row = QHBoxLayout()
        b_open = QPushButton("Open…")
        b_open.clicked.connect(self.open_dialog)
        b_folder = QPushButton("Folder…")
        b_folder.clicked.connect(self.open_folder_dialog)
        row.addWidget(b_open)
        row.addWidget(b_folder)
        lv.addLayout(row)
        self.photo_list = QListWidget()
        self.photo_list.setObjectName("filmstrip")
        self.photo_list.setViewMode(QListWidget.IconMode)
        self.photo_list.setFlow(QListWidget.TopToBottom)
        self.photo_list.setWrapping(False)
        self.photo_list.setMovement(QListWidget.Static)
        self.photo_list.setResizeMode(QListWidget.Adjust)
        self.photo_list.setUniformItemSizes(True)
        self.photo_list.setSpacing(4)
        self.photo_list.setTextElideMode(Qt.ElideMiddle)
        self.photo_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.photo_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.photo_list.setIconSize(QSize(176, 118))
        self.photo_list.setSelectionMode(QListWidget.ExtendedSelection)
        _orig_strip_resize = self.photo_list.resizeEvent

        def _strip_resized(e, _orig=_orig_strip_resize):
            _orig(e)
            QTimer.singleShot(0, self._fit_filmstrip)
        self.photo_list.resizeEvent = _strip_resized
        self.photo_list.currentRowChanged.connect(self._select_photo)
        self.photo_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.photo_list.customContextMenuRequested.connect(self._photo_menu)
        lv.addWidget(self.photo_list)
        left.setMinimumWidth(170)
        split.addWidget(left)

        # --- centre: preview
        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self.lr_banner = QLabel()
        self.lr_banner.setStyleSheet(f"background:#16171a; color:{ACCENT}; padding:8px 12px; border-bottom:1px solid #26272c;")
        self.lr_banner.setVisible(False)
        cv.addWidget(self.lr_banner)
        self.view = PreviewView()
        self.view.view_changed.connect(self._view_moved)
        self.view.sample_at.connect(lambda x, y: (setattr(self, "_swatch_center", (x, y)), setattr(self, "_swatch_manual", True), self.swatch_timer.start()))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        cmp = QWidget()
        cl = QVBoxLayout(cmp)
        cl.setContentsMargins(14, 10, 14, 6)
        top = QHBoxLayout()
        t = QLabel("COMPARE STOCKS")
        t.setStyleSheet(f"color:{ACCENT}; font-family:'{BRAND_FONT}'; font-weight:600; letter-spacing:2px;")
        top.addWidget(t)
        hint = QLabel("Click a stock to use it  ·  Esc to go back")
        hint.setStyleSheet("color:#888; padding-left:12px;")
        top.addWidget(hint)
        top.addStretch(1)
        self.cmp_size = QSlider(Qt.Horizontal)
        self.cmp_size.setRange(200, 520)
        self.cmp_size.setValue(int(self.prefs.get("compare_size", 320)))
        self.cmp_size.setFixedWidth(140)
        self.cmp_size.setFocusPolicy(Qt.NoFocus)
        self.cmp_size.valueChanged.connect(self._compare_resize)
        top.addWidget(QLabel("Size"))
        top.addWidget(self.cmp_size)
        back = QPushButton("Back to photo")
        back.clicked.connect(lambda: self._show_compare(False))
        top.addWidget(back)
        cl.addLayout(top)
        self.compare_grid = self._make_stock_grid(QSize(320, 214), QSize(334, 250), big=True)
        self.compare_grid.itemClicked.connect(self._compare_picked)
        self.compare_grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.compare_grid.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        _orig_resize = self.compare_grid.resizeEvent

        def _grid_resized(e, _orig=_orig_resize):
            _orig(e)
            QTimer.singleShot(0, self._compare_resize)
        self.compare_grid.resizeEvent = _grid_resized
        self._big_items = {it.data(Qt.UserRole): it for it in self._grid_items(self.compare_grid)}
        cl.addWidget(self.compare_grid, 1)
        self.stack.addWidget(cmp)
        cv.addWidget(self.stack, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 6, 10, 6)
        self.btn_before = QPushButton("Hold for before  ( \\ )")
        self.btn_before.pressed.connect(lambda: self._set_before(True))
        self.btn_before.released.connect(lambda: self._set_before(False))
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        self.info.setMinimumWidth(10)
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar.addWidget(self.btn_before)
        self.btn_reset_photo = QPushButton("Reset photo")
        self.btn_reset_photo.setToolTip("Remove every change on this photo (⇧⌘R). ⌘Z brings them back.")
        self.btn_reset_photo.setFocusPolicy(Qt.NoFocus)
        self.btn_reset_photo.clicked.connect(lambda: self.reset_photo())
        bar.addWidget(self.btn_reset_photo)
        bar.addSpacing(16)
        for text, tip, fn in (("Fit", "Fit to window (⌘0)", self.zoom_fit), ("100%", "Actual pixels (⌘1)", self.zoom_100),
                              ("−", "Zoom out (⌘−)", self.zoom_out), ("+", "Zoom in (⌘+)", self.zoom_in)):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.zoom_label = QLabel("Fit")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setStyleSheet("color:#bbb; padding-left:6px;")
        bar.addWidget(self.zoom_label)
        hint = QLabel("pinch or ⌘-scroll to zoom · drag to pan")
        hint.setStyleSheet("color:#777; padding-left:10px;")
        bar.addWidget(hint)
        bar.addStretch(1)
        bar.addWidget(self.info)
        barw = QWidget()
        barw.setLayout(bar)
        barw.setStyleSheet("background:#18181b;")
        cv.addWidget(barw)
        split.addWidget(centre)

        # --- right: controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(370)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(10, 0, 8, 12)
        self._controls(pv)
        pv.addStretch(1)
        scroll.setWidget(panel)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(12, 10, 14, 0)
        rv.setSpacing(6)
        self.histogram = Histogram()
        rv.addWidget(self.histogram)
        self.hist_info = QLabel("")
        self.hist_info.setStyleSheet("color:#888; font-size:11px;")
        rv.addWidget(self.hist_info)
        rv.addWidget(scroll, 1)
        right.setMinimumWidth(392)
        self._wheel_guard = WheelToScroll(scroll)
        for cls in (QSlider, QComboBox, QDoubleSpinBox):
            for wdg in panel.findChildren(cls):
                wdg.installEventFilter(self._wheel_guard)
                wdg.setFocusPolicy(Qt.ClickFocus if cls is not QSlider else Qt.NoFocus)
        split.addWidget(right)
        split.setSizes([200, 980, 400])
        split.setCollapsible(2, False)
        split.setStretchFactor(1, 1)

        self.setStatusBar(QStatusBar())

    def _controls(self, pv):
        S = lambda *a, **k: Slider(*a, **k)

        # --- film stock
        sec = self._section("Film stock")
        pv.addWidget(sec)
        row = QHBoxLayout()
        row.setSpacing(2)
        self._stock_filter_btns = {}
        for key, label in (("all", "All"), ("Kodak", "Kodak"), ("Fujifilm", "Fujifilm"), ("CineStill", "CineStill"),
                           ("bw", "B&&W")):
            b = QToolButton(text=label)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setObjectName("filterBtn")
            b.clicked.connect(lambda _=False, k=key: self._filter_stocks(k))
            row.addWidget(b)
            self._stock_filter_btns[key] = b
        self._stock_filter_btns["all"].setChecked(True)
        row.addStretch(1)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        self.stock_list = self._make_stock_grid(QSize(170, 114), QSize(181, 150))
        self.stock_list.setFixedHeight(3 * 150 + 8)
        self.stock_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stock_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        _orig_panel_resize = self.stock_list.resizeEvent

        def _panel_resized(e, _orig=_orig_panel_resize):
            _orig(e)
            QTimer.singleShot(0, self._fit_panel_grid)
        self.stock_list.resizeEvent = _panel_resized
        self.stock_list.currentItemChanged.connect(self._stock_picked)
        self._stock_items = {it.data(Qt.UserRole): it for it in self._grid_items(self.stock_list)}
        sec.add(self.stock_list)
        b_cmp = QPushButton("⊞  Compare all stocks on this photo  (C)")
        b_cmp.setFocusPolicy(Qt.NoFocus)
        b_cmp.clicked.connect(lambda: self._show_compare(True))
        sec.add(b_cmp)
        self.stock_blurb = QLabel()
        self.stock_blurb.setWordWrap(True)
        self.stock_blurb.setStyleSheet("color:#aaa; font-size:12px;")
        sec.add(self.stock_blurb)
        self.c_strength = sec.add(S("Film strength", 0, 100, 100, 1, suffix=" %"))

        # --- exposure
        sec = self._section("Exposure")
        pv.addWidget(sec)
        self.c_meter = QCheckBox("Auto-meter RAW files (like a film camera)")
        self.c_meter.setChecked(True)
        sec.add(self.c_meter)
        self.c_exposure = sec.add(S("Exposure", -4, 4, 0, 0.05, 2, " EV"))
        self.c_push = sec.add(S("Push / pull", -2, 2, 0, 0.5, 1, " stops"))
        self.c_highlights = sec.add(S("Highlights", -100, 100, 0))
        self.c_shadows = sec.add(S("Shadows", -100, 100, 0))
        self.c_contrast = sec.add(S("Contrast", -100, 100, 0))

        # --- white balance
        sec = self._section("White balance")
        pv.addWidget(sec)
        row = QHBoxLayout()
        self.c_wb_preset = QComboBox()
        for k in gs.WB_PRESETS:
            self.c_wb_preset.addItem(k, k)
        self.c_wb_preset.addItem("Custom", "Custom")
        self.c_wb_preset.activated.connect(self._wb_preset)
        self.btn_pick = QToolButton(text="Eyedropper")
        self.btn_pick.setCheckable(True)
        self.btn_pick.setToolTip("Click something in the photo that should be neutral grey or white")
        self.btn_pick.toggled.connect(self._toggle_eyedropper)
        row.addWidget(self.c_wb_preset, 1)
        row.addWidget(self.btn_pick)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        self.c_wb_temp = sec.add(S("Temperature", 2000, 12000, 5500, 50, 0, " K"))
        self.c_wb_tint = sec.add(S("Tint", -150, 150, 0, 1))
        note = QLabel("Set as light hitting the film — the stock still adds its own colour.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:11px;")
        sec.add(note)

        # --- colour & lab
        sec = self._section("Colour & lab")
        pv.addWidget(sec)
        self.c_lab = QComboBox()
        for k in film.LABS:
            self.c_lab.addItem(film.LAB_LABELS[k], k)
        sec.add(self._labeled("Process", self.c_lab))
        self.c_depth = sec.add(S("Film depth", 0, 200, 100, 1, suffix=" %"))
        self.c_depth.setToolTip("How strongly each dye layer's development carves edges and separates tones")
        self.c_saturation = sec.add(S("Saturation", -100, 100, 0))
        self.c_fade = sec.add(S("Fade", 0, 100, 0))

        # --- skin
        sec = self._section("Skin tones")
        pv.addWidget(sec)
        self.c_skin_warmth = sec.add(S("Skin warmth  (rosy ↔ golden)", -100, 100, 0))
        self.c_skin_saturation = sec.add(S("Skin saturation", -100, 100, 0))
        note = QLabel("Only affects skin — works for every complexion.")
        note.setStyleSheet("color:#888; font-size:11px;")
        sec.add(note)

        # --- tone curve
        sec = self._section("Tone curve")
        pv.addWidget(sec)
        row = QHBoxLayout()
        self._curve_btns = {}
        for ch, label in (("rgb", "RGB"), ("r", "Red"), ("g", "Green"), ("b", "Blue")):
            b = QToolButton(text=label)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.clicked.connect(lambda _=False, c=ch: self.curve.set_channel(c))
            row.addWidget(b)
            self._curve_btns[ch] = b
        self._curve_btns["rgb"].setChecked(True)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        self.curve = CurveEditor()
        self.curve.changed.connect(self._curve_changed)
        sec.add(self.curve)
        row = QHBoxLayout()
        for label, pts in (("Linear", [[0, 0], [1, 1]]),
                           ("Soft contrast", [[0, 0], [0.25, 0.21], [0.75, 0.79], [1, 1]]),
                           ("Matte", [[0, 0.07], [0.25, 0.26], [0.75, 0.77], [1, 0.97]]),
                           ("Punchy", [[0, 0], [0.25, 0.17], [0.75, 0.84], [1, 1]])):
            b = QToolButton(text=label)
            b.clicked.connect(lambda _=False, p=pts: self._curve_preset(p))
            row.addWidget(b)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        hint = QLabel("Click to add a point · drag to move · double-click a point to remove")
        hint.setStyleSheet("color:#888; font-size:11px;")
        hint.setWordWrap(True)
        sec.add(hint)

        # --- crop
        sec = self._section("Crop")
        pv.addWidget(sec)
        self.c_aspect = QComboBox()
        for k in gs.ASPECTS:
            self.c_aspect.addItem(gs.ASPECT_LABELS[k], k)
        self.c_aspect.activated.connect(self._aspect_picked)
        sec.add(self._labeled("Aspect", self.c_aspect))
        row = QHBoxLayout()
        self.btn_crop = QPushButton("Crop…")
        self.btn_crop.setCheckable(True)
        self.btn_crop.toggled.connect(self._toggle_crop)
        b_rot = QToolButton(text="↻ Rotate ratio")
        b_rot.clicked.connect(self._flip_aspect)
        b_reset = QToolButton(text="Clear")
        b_reset.clicked.connect(self._clear_crop)
        row.addWidget(self.btn_crop, 1)
        row.addWidget(b_rot)
        row.addWidget(b_reset)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)

        # --- grain
        sec = self._section("Grain")
        pv.addWidget(sec)
        self.grain_swatch = SwatchView()
        self.grain_swatch.setObjectName("swatch")
        self.grain_swatch.setToolTip("A 100% sample of your photo with the current grain — updates instantly.\n"
                                     "Drag it to look at another part of the photo (or double-click the photo).")
        self.grain_swatch.dragged.connect(self._swatch_dragged)
        self.grain_swatch.released.connect(self._swatch_released)
        sec.add(self.grain_swatch)
        self.swatch_caption = QLabel("100% sample")
        self.swatch_caption.setObjectName("swatchCaption")
        sec.add(self.swatch_caption)
        row = QHBoxLayout()
        row.setSpacing(3)
        self._grain_btns = {}
        for name in gs.GRAIN_PRESETS:
            b = QToolButton(text=name)
            b.setObjectName("filterBtn")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, n=name: self._grain_preset(n))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumWidth(10)
            b.setStyleSheet("padding-left: 2px; padding-right: 2px;")
            row.addWidget(b)
            self._grain_btns[name] = b
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        self.c_grain = sec.add(S("Amount", 0, 200, 100, 1, suffix=" %"))
        self.c_grain_size = sec.add(S("Size", 50, 250, 100, 1, suffix=" %"))
        self.c_grain_rough = sec.add(S("Roughness", 0, 100, 50))
        self.c_softness = sec.add(S("Film softness", 0, 200, 100, 1, suffix=" %"))
        self.c_sharpen = sec.add(S("Sharpening", -100, 100, 0))
        self.c_sharpen.setToolTip("Drag right to sharpen edges and grain, left to soften")
        self.c_denoise = sec.add(S("Clean digital noise", 0, 100, 0))
        b = QPushButton("New grain pattern")
        b.clicked.connect(self._reseed)
        sec.add(b)

        # --- light
        sec = self._section("Light")
        pv.addWidget(sec)
        self.c_halation = sec.add(S("Halation", 0, 100, 15))
        self.c_bloom = sec.add(S("Bloom", 0, 100, 8))
        self.c_vignette = sec.add(S("Vignette", -100, 100, 0))

        # --- leaks
        sec = self._section("Light leaks")
        pv.addWidget(sec)
        self.c_leak = QComboBox()
        for k in gs.LEAK_STYLES:
            self.c_leak.addItem(gs.LEAK_LABELS[k], k)
        sec.add(self._labeled("Style", self.c_leak))
        self.c_leak_amount = sec.add(S("Leak amount", 0, 100, 50))
        b = QPushButton("Shuffle leak")
        b.clicked.connect(self._shuffle_leak)
        sec.add(b)

        # --- dust, scratches, texture
        sec = self._section("Dust, scratches & texture")
        pv.addWidget(sec)
        self.c_dust = sec.add(S("Dust", 0, 100, 0))
        self.c_scratches = sec.add(S("Scratches", 0, 100, 0))
        self.c_texture = QComboBox()
        for k in textures.TEXTURES:
            self.c_texture.addItem(textures.TEXTURE_LABELS[k], k)
        sec.add(self._labeled("Texture", self.c_texture))
        self.c_texture_amount = sec.add(S("Texture amount", 0, 100, 50))
        b = QPushButton("Shuffle dust & scratches")
        b.clicked.connect(self._reseed)
        sec.add(b)

        # --- frame
        sec = self._section("Frame & border")
        pv.addWidget(sec)
        self.c_border = QComboBox()
        for k in gs.BORDERS:
            self.c_border.addItem(gs.BORDER_LABELS[k], k)
        sec.add(self._labeled("Border", self.c_border))

        # --- presets
        sec = self._section("Your looks")
        pv.addWidget(sec)
        row = QHBoxLayout()
        self.c_preset = QComboBox()
        self._refresh_presets()
        self.c_preset.activated.connect(self._apply_preset)
        bsave = QToolButton(text="Save")
        bsave.clicked.connect(self._save_preset)
        bdel = QToolButton(text="Delete")
        bdel.clicked.connect(self._delete_preset)
        row.addWidget(self.c_preset, 1)
        row.addWidget(bsave)
        row.addWidget(bdel)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for i, (text, fn) in enumerate((("Copy", self._copy), ("Paste", self._paste),
                                        ("Sync to selected", self._sync_selected),
                                        ("Reset adjustments", self._reset))):
            b = QToolButton(text=text)
            b.clicked.connect(fn)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.addWidget(b, i // 2, i % 2)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)

        # --- export
        sec = self._section("Export")
        pv.addWidget(sec)
        self.btn_lr = QPushButton("Save && return to Lightroom")
        self.btn_lr.setToolTip("Writes the film look into the TIFF and closes. ⌘S saves without closing.")
        self.btn_lr.setObjectName("primary")
        self.btn_lr.clicked.connect(lambda: self.save_to_lightroom(close_after=True))
        self.btn_lr.setVisible(False)
        sec.add(self.btn_lr)
        self.c_format = QComboBox()
        for label, key in (("JPEG", "jpg"), ("TIFF 16-bit", "tif"), ("PNG", "png")):
            self.c_format.addItem(label, key)
        sec.add(self._labeled("Format", self.c_format))
        self.c_space = QComboBox()
        for k in imageio.OUTPUT_SPACES:
            self.c_space.addItem(k, k)
        sec.add(self._labeled("Colour space", self.c_space))
        self.c_size = QComboBox()
        for label, v in (("Full size", 0), ("4096 px", 4096), ("2048 px", 2048), ("Instagram 1080 px", 1080)):
            self.c_size.addItem(label, v)
        sec.add(self._labeled("Long edge", self.c_size))
        self.c_quality = Slider("JPEG quality", 60, 100, 95)
        sec.add(self.c_quality)
        row = QHBoxLayout()
        b1 = QPushButton("Export this")
        b1.clicked.connect(lambda: self.export(which="current"))
        b2 = QPushButton("Export selected")
        b2.clicked.connect(lambda: self.export(which="selected"))
        row.addWidget(b1)
        row.addWidget(b2)
        w = QWidget()
        w.setLayout(row)
        sec.add(w)
        b3 = QPushButton("Export look as .cube LUT (for video)")
        b3.clicked.connect(self.export_lut)
        sec.add(b3)

        # wire everything
        for name in ("strength", "exposure", "push", "highlights", "shadows", "contrast",
                     "saturation", "fade", "denoise", "vignette", "leak_amount", "dust", "scratches",
                     "texture_amount", "skin_warmth", "skin_saturation", "depth"):
            getattr(self, f"c_{name}").changed.connect(lambda v, n=name: self._set(n, v))
        self.c_grain.changed.connect(lambda v: self._set("grain", v))
        self.c_grain_size.changed.connect(lambda v: self._set("grain_size", v))
        self.c_grain_rough.changed.connect(lambda v: self._set("grain_roughness", v))
        self.c_halation.changed.connect(lambda v: self._set("halation", v))
        self.c_bloom.changed.connect(lambda v: self._set("bloom", v))
        self.c_meter.toggled.connect(lambda v: self._set("auto_meter", v))
        self.c_leak.currentIndexChanged.connect(lambda i: self._set("leak", self.c_leak.itemData(i)))
        self.c_border.currentIndexChanged.connect(lambda i: self._set("border", self.c_border.itemData(i)))
        self.c_lab.currentIndexChanged.connect(lambda i: self._set("lab", self.c_lab.itemData(i)))
        self.c_texture.currentIndexChanged.connect(lambda i: self._set("texture", self.c_texture.itemData(i)))
        self.c_softness.changed.connect(lambda v: self._set("softness", v))
        self.c_sharpen.changed.connect(lambda v: self._set("sharpen", v))
        self.c_wb_temp.changed.connect(lambda v: self._set_wb(temp=v))
        self.c_wb_tint.changed.connect(lambda v: self._set_wb(tint=v))
        self.view.crop_changed.connect(self._crop_dragged)
        self.view.picked.connect(self._picked)

    SECTION_FIELDS = {
        "Film stock": ["strength"],
        "Exposure": ["auto_meter", "exposure", "push", "highlights", "shadows", "contrast"],
        "White balance": ["wb_temp", "wb_tint"],
        "Colour & lab": ["lab", "depth", "saturation", "fade"],
        "Skin tones": ["skin_warmth", "skin_saturation"],
        "Tone curve": ["curve_rgb", "curve_r", "curve_g", "curve_b"],
        "Crop": ["crop", "aspect"],
        "Grain": ["grain", "grain_size", "grain_roughness", "softness", "sharpen", "denoise"],
        "Light": ["halation", "bloom", "vignette"],
        "Light leaks": ["leak", "leak_amount"],
        "Dust, scratches & texture": ["dust", "scratches", "texture", "texture_amount"],
        "Frame & border": ["border"],
    }
    SLIDER_FIELDS = {
        "c_strength": "strength", "c_exposure": "exposure", "c_push": "push", "c_highlights": "highlights",
        "c_shadows": "shadows", "c_contrast": "contrast", "c_saturation": "saturation", "c_fade": "fade",
        "c_denoise": "denoise", "c_vignette": "vignette", "c_leak_amount": "leak_amount", "c_dust": "dust",
        "c_scratches": "scratches", "c_texture_amount": "texture_amount", "c_grain": "grain",
        "c_grain_size": "grain_size", "c_grain_rough": "grain_roughness", "c_halation": "halation",
        "c_bloom": "bloom", "c_softness": "softness", "c_sharpen": "sharpen", "c_wb_temp": "wb_temp", "c_wb_tint": "wb_tint",
        "c_skin_warmth": "skin_warmth", "c_skin_saturation": "skin_saturation", "c_depth": "depth",
    }

    def _section(self, title):
        sec = Section(title, resettable=title in self.SECTION_FIELDS)
        if title in self.SECTION_FIELDS:
            sec.reset_clicked.connect(lambda f=self.SECTION_FIELDS[title]: self._reset_fields(f))
        return sec

    def _wire_resets(self):
        for attr, field in self.SLIDER_FIELDS.items():
            getattr(self, attr).reset_requested.connect(lambda f=field: self._reset_fields([f]))

    def _reset_fields(self, fields):
        import copy
        d = Settings()
        changed = False
        for f in fields:
            v = copy.deepcopy(getattr(d, f))
            if getattr(self.s, f) != v:
                setattr(self.s, f, v)
                changed = True
        if "crop" in fields or "aspect" in fields:
            self._aspect_flip = False
        if changed:
            self._sync_controls()
            self._changed()

    _strip_cell = None

    def _placeholder_icon(self):
        pm = QPixmap(self.photo_list.iconSize())
        pm.fill(QColor("#141417"))
        return QIcon(pm)

    def _fit_filmstrip(self):
        """One column of thumbnails as wide as the panel; the file name sits small underneath."""
        w = max(120, self.photo_list.viewport().width() - 14)
        if self._strip_cell is not None and self._strip_cell.width() == w + 6:
            return
        icon = QSize(w, int(w * 2 / 3))
        self.photo_list.setIconSize(icon)
        self._strip_cell = QSize(w + 6, icon.height() + 26)
        self.photo_list.setGridSize(self._strip_cell)
        for i in range(self.photo_list.count()):
            self.photo_list.item(i).setSizeHint(self._strip_cell)

    def _make_stock_grid(self, icon, grid, big=False):
        g = QListWidget()
        g.setViewMode(QListWidget.IconMode)
        g.setIconSize(icon)
        g.setGridSize(grid)
        g.setResizeMode(QListWidget.Adjust)
        g.setMovement(QListWidget.Static)
        g.setWrapping(True)
        g.setUniformItemSizes(True)
        g.setWordWrap(True)
        g.setSpacing(0)
        g.setSelectionMode(QListWidget.SingleSelection)
        g.setObjectName("stockGrid")
        g.setFocusPolicy(Qt.NoFocus)
        placeholder = QPixmap(icon)
        placeholder.fill(QColor("#202024"))
        for sid in stocks.ORDER:
            st = stocks.STOCKS[sid]
            short = st.name.replace("Kodak ", "").replace("Fujifilm ", "")
            text = f"{short}\n{st.family} · ISO {st.iso}" if big else short
            it = QListWidgetItem(QIcon(placeholder), text)
            it.setData(Qt.UserRole, sid)
            it.setToolTip(f"{st.name} — {st.blurb}")
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            g.addItem(it)
        return g

    @staticmethod
    def _grid_items(g):
        return [g.item(i) for i in range(g.count())]

    def _filter_stocks(self, key):
        for sid, it in self._stock_items.items():
            st = stocks.STOCKS[sid]
            show = key == "all" or (key == "bw" and st.kind == "bw") or (key == st.family and st.kind != "bw")
            it.setHidden(not show)
        cur = self._stock_items.get(self.s.stock)
        if cur is not None and not cur.isHidden():
            self.stock_list.scrollToItem(cur)

    def _show_compare(self, on):
        if on and self.current is None:
            return
        self.stack.setCurrentIndex(1 if on else 0)
        if on:
            it = self._big_items.get(self.s.stock)
            if it is not None:
                self.compare_grid.setCurrentItem(it)
                self.compare_grid.scrollToItem(it)
            self._compare_resize(self.cmp_size.value())
        self._render_stock_thumbs()               # sharper ones for the big view

    def _fit_panel_grid(self):
        """Two columns that always fill the panel width.

        Sized from the list's own width with the scrollbar's space always reserved, and only
        re-laid out when that width really changes — otherwise a filter whose items exactly fill
        the box makes the scrollbar flick on and off and the grid shake."""
        avail = max(200, self.stock_list.viewport().width() - 10)   # scrollbar always shown -> stable
        if getattr(self, "_panel_grid_w", None) == avail:
            return
        self._panel_grid_w = avail
        cell = avail // 2
        icon = cell - 12
        self.stock_list.setIconSize(QSize(icon, int(icon * 2 / 3)))
        self.stock_list.setGridSize(QSize(cell, int(icon * 2 / 3) + 34))
        self.stock_list.setFixedHeight(int(3 * (icon * 2 / 3 + 34)) + 8)

    def _compare_resize(self, v=None):
        v = int(self.cmp_size.value() if v is None else v)
        self.prefs["compare_size"] = v
        avail = max(200, self.compare_grid.viewport().width() - 10)
        key = (avail, v)
        if getattr(self, "_cmp_grid_key", None) == key:
            return
        self._cmp_grid_key = key
        cols = max(1, avail // (v + 16))
        cell = avail // cols                      # spread columns evenly across the width
        icon = cell - 16
        self.compare_grid.setIconSize(QSize(icon, int(icon * 2 / 3)))
        self.compare_grid.setGridSize(QSize(cell, int(icon * 2 / 3) + 52))

    def _compare_picked(self, it):
        sid = it.data(Qt.UserRole)
        if sid:
            pit = self._stock_items.get(sid)
            if pit is not None:
                self.stock_list.setCurrentItem(pit)   # goes through _stock_picked -> undoable
            self._show_compare(False)

    @staticmethod
    def _kind_label(st):
        return {"neg": "colour negative", "rev": "slide", "bw": "black & white"}[st.kind]

    @staticmethod
    def _labeled(text, w):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 2, 0, 2)
        h.addWidget(QLabel(text))
        h.addWidget(w, 1)
        return box

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        self.act_save = QAction("Save", self)
        self.act_save.setShortcut(QKeySequence(QKeySequence.Save))
        self.act_save.setShortcutContext(Qt.ApplicationShortcut)
        self.act_save.triggered.connect(self.quick_save)
        m.addAction(self.act_save)
        for text, key, fn in (("Open…", QKeySequence.Open, self.open_dialog),
                              ("Open folder…", "Ctrl+Shift+O", self.open_folder_dialog),
                              ("Export…", "Ctrl+E", lambda: self.export(which="current")),
                              ("Export selected…", "Ctrl+Shift+E", lambda: self.export(which="selected"))):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            m.addAction(a)
        m = self.menuBar().addMenu("&Edit")
        self.act_undo = QAction("Undo", self)
        self.act_undo.setShortcuts([QKeySequence(QKeySequence.Undo)])
        self.act_undo.setShortcutContext(Qt.ApplicationShortcut)
        self.act_undo.triggered.connect(self.undo)
        self.act_redo = QAction("Redo", self)
        self.act_redo.setShortcuts([QKeySequence(QKeySequence.Redo), QKeySequence("Ctrl+Y")])
        self.act_redo.setShortcutContext(Qt.ApplicationShortcut)
        self.act_redo.triggered.connect(self.redo)
        m.addAction(self.act_undo)
        m.addAction(self.act_redo)
        m.addSeparator()
        self._update_undo_actions()
        for text, key, fn in (("Copy settings", "Ctrl+Shift+C", self._copy),
                              ("Paste settings", "Ctrl+Shift+V", self._paste),
                              ("Reset adjustments (keep film stock)", "Ctrl+R", self._reset),
                              ("Reset photo (remove all changes)", "Ctrl+Shift+R", self.reset_photo)):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            m.addAction(a)
        m = self.menuBar().addMenu("&View")
        for text, keys, fn in (("Zoom in", ["Ctrl+=", "Ctrl++"], self.zoom_in), ("Zoom out", ["Ctrl+-"], self.zoom_out),
                               ("Fit to window", ["Ctrl+0"], self.zoom_fit), ("Actual pixels (100%)", ["Ctrl+1"], self.zoom_100),
                               ("Toggle Fit / 100%", ["Z"], lambda: self.view.set_zoom(None if self.view.zoom else 1.0))):
            a = QAction(text, self)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.triggered.connect(fn)
            m.addAction(a)
        m = self.menuBar().addMenu("&Help")
        a = QAction("Using GrainHause with Lightroom…", self)
        a.triggered.connect(self._lightroom_help)
        m.addAction(a)
        a = QAction(f"About {APP_NAME}", self)
        a.triggered.connect(lambda: QMessageBox.about(
            self, APP_NAME, f"{APP_NAME} {__version__}\nFilm emulation for RAW, TIFF and JPEG.\n"
                            "Stocks are modelled on the published character of each film."))
        m.addAction(a)

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.stack.currentIndex() == 1:
            self._show_compare(False)
            return
        if e.key() == Qt.Key_C and not e.modifiers() and self.current is not None:
            self._show_compare(self.stack.currentIndex() == 0)
            return
        if e.key() == Qt.Key_Escape:
            self.btn_pick.setChecked(False)
            if self.btn_crop.isChecked():
                self.btn_crop.setChecked(False)
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.btn_crop.isChecked():
            self.btn_crop.setChecked(False)
            return
        if e.key() in (Qt.Key_Right, Qt.Key_Down) and self.photos:
            self.photo_list.setCurrentRow(min(self.photo_list.currentRow() + 1, len(self.photos) - 1))
        elif e.key() in (Qt.Key_Left, Qt.Key_Up) and self.photos:
            self.photo_list.setCurrentRow(max(self.photo_list.currentRow() - 1, 0))
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        super().keyReleaseEvent(e)

    # ------------------------------------------------------------------ files
    def open_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(imageio.SUPPORTED_EXTS))
        exts += " " + exts.upper()
        start = self.prefs.get("last_dir", str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(self, "Open photos", start, f"Photos ({exts});;All files (*)")
        if paths:
            self.prefs["last_dir"] = os.path.dirname(paths[0])
            gs.save_prefs(self.prefs)
            self.open_paths(paths)

    def open_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Open folder", self.prefs.get("last_dir", str(Path.home())))
        if d:
            self.prefs["last_dir"] = d
            gs.save_prefs(self.prefs)
            paths = sorted(str(p) for p in Path(d).iterdir() if p.is_file() and imageio.is_supported(str(p)))
            if not paths:
                QMessageBox.information(self, APP_NAME, "No supported photos in that folder.")
            self.open_paths(paths)

    def open_paths(self, paths, lightroom: bool | None = None, select=True, restoring=False):
        paths = [os.path.abspath(p) for p in paths if os.path.isfile(p) and imageio.is_supported(p)]
        if not paths:
            return
        known = {p.path: i for i, p in enumerate(self.photos)}
        target = None
        for p in paths:
            if p in known:
                target = known[p] if target is None else target
                if lightroom:
                    self.photos[known[p]].lightroom = True
                continue
            ph = Photo(p)
            if lightroom:
                ph.lightroom = True
            self.photos.append(ph)
            it = QListWidgetItem(self._placeholder_icon(), ph.name)
            it.setToolTip(p + ("\nFrom Lightroom" if ph.lightroom else ""))
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            if self._strip_cell:
                it.setSizeHint(self._strip_cell)
            self.photo_list.addItem(it)
            if target is None or not restoring:
                target = len(self.photos) - 1 if not restoring else target
        if not restoring:
            self._save_session()
        if select and target is not None:
            self.photo_list.setCurrentRow(target)
        self._update_lr_mode()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ session memory
    def _save_session(self):
        self.prefs["session"] = [{"path": ph.path, "lightroom": ph.lightroom} for ph in self.photos][-80:]
        if self.current is not None:
            self.prefs["session_current"] = self.current.path
        gs.save_prefs(self.prefs)

    def restore_session(self):
        items = [it for it in self.prefs.get("session", []) if os.path.isfile(it.get("path", ""))]
        if not items:
            return False
        for it in items:
            self.open_paths([it["path"]], lightroom=it.get("lightroom", False), select=False, restoring=True)
        cur = self.prefs.get("session_current")
        idx = next((i for i, ph in enumerate(self.photos) if ph.path == cur), len(self.photos) - 1)
        self.photo_list.setCurrentRow(idx)
        return True

    def _photo_menu(self, pos):
        rows = sorted({i.row() for i in self.photo_list.selectedIndexes()})
        if not rows:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        a_reset = m.addAction(f"Reset {'photo' if len(rows) == 1 else f'{len(rows)} photos'} (remove all changes)")
        m.addSeparator()
        a_rm = m.addAction(f"Remove {'photo' if len(rows) == 1 else f'{len(rows)} photos'} from list")
        a_clear = m.addAction("Clear the whole list")
        a_show = m.addAction("Show in Finder")
        act = m.exec(self.photo_list.mapToGlobal(pos))
        if act == a_reset:
            self._reset_photos(rows)
        elif act == a_rm:
            self._remove_photos(rows)
        elif act == a_clear:
            self._remove_photos(list(range(len(self.photos))))
        elif act == a_show:
            import subprocess
            path = self.photos[rows[0]].path
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            elif os.name == "nt":
                subprocess.Popen(["explorer", "/select,", path])

    def _remove_photos(self, rows):
        self._commit()
        self._store_current()
        self._save_sidecar()
        for r in sorted(rows, reverse=True):
            ph = self.photos.pop(r)
            self.photo_list.takeItem(r)
            if ph is self.current:
                self.current = None
        if self.current is None:
            self.view.set_image(None)
            self.view.message = "Open a photo to start  ·  RAW, TIFF, JPEG"
            self.histogram.set_image(None)
            if self.photos:
                self.photo_list.setCurrentRow(0)
        self._save_session()
        self._update_lr_mode()

    def _update_lr_mode(self):
        ph = self.current
        on = bool(ph and ph.lightroom)
        self.lightroom = on
        self.btn_lr.setVisible(on)
        self.lr_banner.setVisible(on)
        if on:
            self.lr_banner.setText("From Lightroom — ⌘S writes the film look back into this TIFF and "
                                   "Lightroom updates it. Other photos in the list stay open.")

    def _select_photo(self, row):
        if row < 0 or row >= len(self.photos):
            return
        self._commit()
        self._store_current()
        ph = self.photos[row]
        self.current = ph
        self._swatch_center = None
        self._swatch_manual = False
        self._swatch_geom = None
        self.grain_swatch.set_image(None)
        self.swatch_caption.setText("sample")
        self._tile_info = None
        self.view.clear_tile()
        self._close_loupe(render=False)
        self.s = (ph.settings or self._default_settings_for(ph)).copy()
        if ph.committed is None:
            ph.committed = self.s.copy()
        self._update_undo_actions()
        self._update_lr_mode()
        self.prefs["session_current"] = ph.path
        self._sync_controls()
        self.view.set_image(None)
        self.view.message = f"Developing {ph.name}…"
        self.view.update()
        if ph.preview is None:
            self._gen += 1
            job = Job((self._gen, ph), self._load_preview, ph)
            job.signals.done.connect(self._preview_loaded)
            job.signals.failed.connect(self._failed)
            self.pool.start(job)
        else:
            self._preview_loaded((self._gen, ph), None)

    def _default_settings_for(self, ph):
        base = self.clipboard.copy() if self.clipboard else Settings(stock=self.s.stock)
        return base

    @staticmethod
    def _load_preview(ph: Photo):
        src = imageio.load(ph.path, preview_long_edge=PREVIEW_EDGE)
        ev = engine.scene_meter(src.linear, src.scene_referred)
        return src, ev

    def _preview_loaded(self, token, res):
        _, ph = token
        if res is not None:
            ph.preview, ph.meter_ev = res
        if ph is not self.current:
            return
        src = ph.preview
        self.swatch_timer.start()
        if ph.full is None:
            self._load_full(ph)         # the grain sample is at 100%: bring the full file in quietly
        self._sync_controls()   # as-shot white balance and frame shape are known now
        kind = {"raw": "RAW", "tiff": "TIFF", "image": "image"}[src.kind]
        self.info.setText(f"{kind} · {src.full_size[0]}×{src.full_size[1]} · {src.space.name}"
                          + (f" · metered {ph.meter_ev:+.1f} EV" if src.scene_referred else ""))
        self._request_render()
        self.thumb_timer.start()

    def _failed(self, token, tb):
        self.view.message = "Couldn't open or develop this file."
        self.view.update()
        self.statusBar().showMessage("Error — see details", 8000)
        QMessageBox.warning(self, APP_NAME, f"Something went wrong:\n\n{tb[-1500:]}")

    # ------------------------------------------------------------------ settings <-> controls
    def _sync_controls(self):
        self._building = True
        s = self.s
        st = stocks.get(s.stock)
        it = self._stock_items.get(st.id)
        if it:
            self.stock_list.blockSignals(True)
            self.stock_list.setCurrentItem(it)
            self.stock_list.blockSignals(False)
        self.stock_blurb.setText(f"<b>{st.name}</b> — {st.blurb}")
        self.c_halation.default = st.halation
        self.c_bloom.default = st.bloom
        vals = dict(strength=s.strength, exposure=s.exposure, push=s.push, highlights=s.highlights,
                    shadows=s.shadows, contrast=s.contrast, saturation=s.saturation,
                    fade=s.fade, denoise=s.denoise, vignette=s.vignette, leak_amount=s.leak_amount,
                    grain=s.grain_pct(), grain_size=s.grain_size_pct(), grain_rough=s.grain_roughness,
                    halation=s.halation_value(), bloom=s.bloom_value(), softness=s.softness_pct(), sharpen=s.sharpen,
                    dust=s.dust, scratches=s.scratches, texture_amount=s.texture_amount,
                    skin_warmth=s.skin_warmth, skin_saturation=s.skin_saturation, depth=s.depth)
        T, tint = self._wb_values()
        vals.update(wb_temp=T, wb_tint=tint)
        for k, v in vals.items():
            getattr(self, f"c_{k}").set(v, emit=False)
        T0, t0 = self._as_shot()
        self.c_wb_temp.default, self.c_wb_tint.default = T0, t0
        self.c_meter.blockSignals(True)
        self.c_meter.setChecked(s.auto_meter)
        self.c_meter.blockSignals(False)
        for combo, val in ((self.c_leak, s.leak), (self.c_border, s.border), (self.c_lab, s.lab_value()),
                           (self.c_texture, s.texture), (self.c_aspect, s.aspect)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(val)))
            combo.blockSignals(False)
        self.c_lab.setEnabled(st.kind != "rev" and st.id != "neutral")
        self.c_lab.setToolTip("Slide film is viewed directly — no scan or print step" if st.kind == "rev" else "")
        self.c_wb_preset.blockSignals(True)
        self.c_wb_preset.setCurrentIndex(self.c_wb_preset.findData(self._wb_preset_name()))
        self.c_wb_preset.blockSignals(False)
        self.curve.set_curves({"rgb": s.curve_rgb, "r": s.curve_r, "g": s.curve_g, "b": s.curve_b})
        self._update_view_crop()
        self._update_full_size()
        self._update_grain_btns()
        self._building = False

    # ------------------------------------------------------------------ white balance
    def _meta(self):
        ph = self.current
        if ph is None or ph.preview is None:
            return None
        return ph.preview.meta if ph.preview.scene_referred else None

    def _as_shot(self):
        return engine.as_shot_wb(self._meta())

    def _wb_values(self):
        T0, t0 = self._as_shot()
        return (T0 if self.s.wb_temp is None else self.s.wb_temp,
                t0 if self.s.wb_tint is None else self.s.wb_tint)

    def _wb_preset_name(self):
        if self.s.wb_temp is None and self.s.wb_tint is None:
            return "As shot"
        for k, v in gs.WB_PRESETS.items():
            if v and abs(v[0] - (self.s.wb_temp or 0)) < 1 and abs(v[1] - (self.s.wb_tint or 0)) < 0.5:
                return k
        return "Custom"

    def _wb_preset(self, i):
        k = self.c_wb_preset.itemData(i)
        if k == "Custom":
            return
        v = gs.WB_PRESETS.get(k)
        self.s.wb_temp, self.s.wb_tint = (None, None) if v is None else (float(v[0]), float(v[1]))
        self._sync_controls()
        self._changed()

    def _set_wb(self, temp=None, tint=None):
        if self._building:
            return
        T, t = self._wb_values()
        self.s.wb_temp = T if temp is None else float(temp)
        self.s.wb_tint = t if tint is None else float(tint)
        self.c_wb_preset.blockSignals(True)
        self.c_wb_preset.setCurrentIndex(self.c_wb_preset.findData(self._wb_preset_name()))
        self.c_wb_preset.blockSignals(False)
        self._changed()

    def _toggle_eyedropper(self, on):
        if on and self.btn_crop.isChecked():
            self.btn_crop.setChecked(False)
        self.view.set_mode("eyedropper" if on else ("crop" if self.btn_crop.isChecked() else "view"))

    def _picked(self, nx, ny):
        ph = self.current
        if ph is None or ph.preview is None:
            return
        src = ph.preview
        H, W = src.linear.shape[:2]
        a, b, c, d = engine.crop_box(self.s, W, H) if (self.s.crop and self.view.mode != "crop") else (0, 0, W, H)
        x = int(a + nx * (c - a))
        y = int(b + ny * (d - b))
        r = max(2, W // 300)
        patch = src.linear[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1].reshape(-1, 3)
        rgb = engine.prepare_scene(np.median(patch, axis=0)[None, :], src.scene_referred)[0]
        g_cur = engine.wb_gains_for(self.s, self._meta())
        g_new = g_cur / np.maximum(rgb * g_cur, 1e-6)
        T, tint = color.wb_from_gains(g_new)
        self.s.wb_temp, self.s.wb_tint = round(T / 10) * 10.0, round(tint, 1)
        self.btn_pick.setChecked(False)
        self._sync_controls()
        self._changed()
        self.statusBar().showMessage(f"White balance set to {self.s.wb_temp:.0f} K, tint {self.s.wb_tint:+.0f}", 4000)

    # ------------------------------------------------------------------ tone curve
    def _curve_changed(self, ch, pts):
        setattr(self.s, f"curve_{ch}", pts)
        self._changed()

    def _curve_preset(self, pts):
        ch = self.curve.channel
        setattr(self.s, f"curve_{ch}", [list(map(float, p)) for p in pts])
        self.curve.set_curves({"rgb": self.s.curve_rgb, "r": self.s.curve_r, "g": self.s.curve_g,
                               "b": self.s.curve_b})
        self._changed()

    # ------------------------------------------------------------------ crop
    def _img_aspect(self):
        ph = self.current
        if ph is None or ph.preview is None:
            return 1.5
        H, W = ph.preview.linear.shape[:2]
        return W / H

    def _ratio(self):
        k = self.s.aspect
        if k == "free":
            return None
        if k == "original":
            r = self._img_aspect()
        else:
            r = ASPECT_RATIOS[k]
        if getattr(self, "_aspect_flip", False):
            r = 1 / r
        return r

    def _update_view_crop(self):
        self.view.set_crop(self.s.crop, self._ratio(), self._img_aspect())

    def _aspect_picked(self, i):
        self.s.aspect = self.c_aspect.itemData(i)
        self._aspect_flip = False
        self._apply_ratio()

    def _flip_aspect(self):
        self._aspect_flip = not getattr(self, "_aspect_flip", False)
        self._apply_ratio()

    def _apply_ratio(self):
        r = self._ratio()
        if r is None:
            self.s.crop = self.s.crop or [0.0, 0.0, 1.0, 1.0]
        else:
            c = self.s.crop or [0, 0, 1, 1]
            centre = ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)
            self.s.crop = fit_aspect(r, self._img_aspect(), centre)
        if self.s.aspect == "original" and not getattr(self, "_aspect_flip", False):
            self.s.crop = None
        self._update_view_crop()
        if not self.btn_crop.isChecked():
            self.btn_crop.setChecked(True)
        else:
            self._changed()

    def _toggle_crop(self, on):
        if on and self.btn_pick.isChecked():
            self.btn_pick.setChecked(False)
        self.btn_crop.setText("Done" if on else "Crop…")
        self._close_loupe(render=False)
        self._update_view_crop()
        self.view.set_mode("crop" if on else "view")
        self._update_full_size()
        self._request_render()

    def _crop_dragged(self, crop):
        x0, y0, x1, y1 = crop
        full = x0 <= 0.001 and y0 <= 0.001 and x1 >= 0.999 and y1 >= 0.999
        self._begin_edit()
        self.s.crop = None if full else [float(v) for v in crop]
        self._store_current()
        self.save_timer.start()
        self.idle_timer.start()

    def _clear_crop(self):
        self.s.crop = None
        self.s.aspect = "original"
        self._aspect_flip = False
        self._sync_controls()
        self._changed()

    def _grain_preset(self, name):
        g, size, rough = gs.GRAIN_PRESETS[name]
        self.s.grain, self.s.grain_size, self.s.grain_roughness = float(g), float(size), float(rough)
        self._sync_controls()
        self._changed()

    def _update_grain_btns(self):
        cur = (self.s.grain_pct(), self.s.grain_size_pct(), self.s.grain_roughness)
        for name, b in self._grain_btns.items():
            g, size, rough = gs.GRAIN_PRESETS[name]
            b.setChecked(abs(cur[0] - g) < 0.5 and abs(cur[1] - size) < 0.5 and abs(cur[2] - rough) < 0.5)

    NO_DRAFT_FIELDS = {"grain", "grain_size", "grain_roughness", "softness", "sharpen", "dust", "scratches",
                       "texture_amount", "depth"}

    def _set(self, name, value):
        self._last_field = name
        if self._building:
            return
        setattr(self.s, name, value)
        if name in ("grain", "grain_size", "grain_roughness"):
            self._update_grain_btns()
        self._changed()

    def _stock_picked(self, cur, prev):
        if cur is None or self._building:
            return
        sid = cur.data(Qt.UserRole)
        if not sid:
            return
        self.s.stock = sid
        self._sync_controls()
        self._changed()

    def _settled(self):
        self._dragging = False
        self._commit()
        self._request_render()
        self.thumb_timer.start()

    # ------------------------------------------------------------------ undo / redo
    def _begin_edit(self):
        ph = self.current
        if ph is None or self._gesture:
            return
        if ph.committed is None:
            ph.committed = self.s.copy()
        ph.undo.append(ph.committed)
        del ph.undo[:-200]
        ph.redo.clear()
        self._gesture = True
        self._update_undo_actions()

    def _commit(self):
        ph = self.current
        if ph is not None and self._gesture:
            ph.committed = self.s.copy()
        self._gesture = False

    def undo(self):
        self._step_history(back=True)

    def redo(self):
        self._step_history(back=False)

    def _step_history(self, back):
        ph = self.current
        if ph is None:
            return
        if self._gesture:
            self._commit()
        src, dst = (ph.undo, ph.redo) if back else (ph.redo, ph.undo)
        if not src:
            return
        before = self.s
        dst.append(self.s.copy())
        self.s = src.pop()
        ph.committed = self.s.copy()
        ph.settings = self.s.copy()
        self._aspect_flip = False
        self._sync_controls()
        self.save_timer.start()
        self._request_render()
        what = describe_change(before, self.s)
        self.statusBar().showMessage(f"{'Undid' if back else 'Redid'} {what}", 2500)
        self._update_undo_actions()

    def _update_undo_actions(self):
        ph = self.current
        if hasattr(self, "act_undo"):
            self.act_undo.setEnabled(bool(ph and (ph.undo or self._gesture)))
            self.act_redo.setEnabled(bool(ph and ph.redo))

    def _changed(self):
        self.swatch_timer.start()
        self._begin_edit()
        self._dragging = True
        self.idle_timer.start()
        self._store_current()
        self.save_timer.start()
        self._request_render()

    def _store_current(self):
        if self.current is not None:
            self.current.settings = self.s.copy()

    def _save_sidecar(self):
        if self.current is not None and self.current.settings is not None:
            gs.save_sidecar(self.current.path, self.current.settings)

    def _reseed(self):
        self.s.seed = int(np.random.default_rng().integers(1, 10 ** 6))
        self._changed()

    def _shuffle_leak(self):
        self.s.leak_seed = int(np.random.default_rng().integers(1, 10 ** 6))
        if self.s.leak == "none":
            self.s.leak = "edge_fog"
            self._sync_controls()
        self._changed()

    def _reset(self):
        """Reset every adjustment but keep the chosen film stock."""
        self._begin_edit()
        self.s = Settings(stock=self.s.stock)
        self._aspect_flip = False
        self._sync_controls()
        self._changed()

    def reset_photo(self):
        """Remove all changes on this photo: every adjustment, crop, curve and the film stock."""
        if self.current is None:
            return
        self._begin_edit()
        self.s = Settings()
        self._aspect_flip = False
        self.btn_pick.setChecked(False)
        if self.btn_crop.isChecked():
            self.btn_crop.setChecked(False)
        self._sync_controls()
        self._changed()
        self.statusBar().showMessage("All edits removed from this photo — ⌘Z to bring them back", 5000)

    def _reset_photos(self, rows):
        """Reset several photos from the list at once (each one can still be undone on its own)."""
        for r in rows:
            ph = self.photos[r]
            if ph is self.current:
                self.reset_photo()
                continue
            ph.undo.append((ph.settings or Settings()).copy())
            ph.redo.clear()
            ph.settings = Settings()
            ph.committed = ph.settings.copy()
            ph.thumb = None
            gs.save_sidecar(ph.path, ph.settings)
        self.statusBar().showMessage(f"Reset {len(rows)} photo{'s' if len(rows) != 1 else ''}", 4000)

    def _copy(self):
        self.clipboard = self.s.copy()
        self.statusBar().showMessage("Settings copied", 3000)

    def _paste(self):
        if self.clipboard:
            self.s = self.clipboard.copy()
            self._sync_controls()
            self._changed()

    def _sync_selected(self):
        rows = {i.row() for i in self.photo_list.selectedIndexes()}
        for r in rows:
            self.photos[r].settings = self.s.copy()
            gs.save_sidecar(self.photos[r].path, self.s)
        self.statusBar().showMessage(f"Applied this look to {len(rows)} photo(s)", 4000)

    # presets
    def _refresh_presets(self):
        self.c_preset.blockSignals(True)
        self.c_preset.clear()
        self.c_preset.addItem("Choose a saved look…", None)
        for k in sorted(self.presets):
            self.c_preset.addItem(k, k)
        self.c_preset.blockSignals(False)

    def _apply_preset(self, i):
        k = self.c_preset.itemData(i)
        if k and k in self.presets:
            self.s = self.presets[k].copy()
            self._sync_controls()
            self._changed()

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Save look", "Name this look:")
        if ok and name.strip():
            self.presets[name.strip()] = self.s.copy()
            gs.save_presets(self.presets)
            self._refresh_presets()
            self.c_preset.setCurrentIndex(self.c_preset.findData(name.strip()))

    def _delete_preset(self):
        k = self.c_preset.currentData()
        if k and k in self.presets:
            del self.presets[k]
            gs.save_presets(self.presets)
            self._refresh_presets()

    # ------------------------------------------------------------------ rendering
    def _request_render(self):
        if self.current is None or self.current.preview is None:
            return
        self._rev += 1
        v = self.view
        if v.zoom is None or v.zoom_device() < 1.0:
            self._tile_gen += 1      # zoomed out a little, a tile is slow: restart it with the new look
        if v.tile is not None and (v.zoom is None or v.zoom_device() < 1.0):
            # a zoomed-out tile takes a moment to redo: don't leave the old look sitting on top
            v.clear_tile()
            self._tile_info = None
        self.render_timer.start()

    # what's on screen vs. what's wanted
    def _tile_needed(self):
        v = self.view
        if v.zoom is None or v.image is None or v.mode == "crop" or not v.full_size:
            return False
        preview_scale = v.image.width() / max(1, v.full_size[0])   # preview px per full px
        return v.zoom_device() > preview_scale * 1.12

    def _tile_wanted(self):
        if not self._tile_needed():
            return False
        info = self._tile_info
        if info is None:
            return True
        rev, zoom, (x0, y0, x1, y1) = info
        if rev != self._rev or abs(zoom - self.view.zoom_device()) / zoom > 0.01:
            return True
        vx0, vy0, vx1, vy1 = self.view.visible_region(margin=0.0)
        return not (vx0 >= x0 and vy0 >= y0 and vx1 <= x1 and vy1 <= y1)

    def _render_now(self):
        if self._busy:
            self._pending = True
            return
        ph = self.current
        if ph is None or ph.preview is None:
            return
        want_preview = self._preview_rev != self._rev
        want_tile = self._tile_wanted()
        if want_tile and ph.full is None:
            self._load_full(ph)
            want_tile = False
        if not (want_preview or want_tile):
            return
        self._busy = True
        self._pending = False
        s = self.s.copy()
        before = False          # the original is pre-rendered separately, see _ensure_before
        rev = self._rev
        # while dragging a slider at high zoom, the tile is what you're looking at: render it first
        if want_tile and (self._dragging or not want_preview):
            region = self.view.visible_region()
            zoom = self.view.zoom_device()
            gen = self._tile_gen
            job = Job(("tile", ph, rev, zoom, region), self._render_tile, ph, s, before, region, zoom,
                      lambda: self._tile_gen != gen)
        else:
            crop_mode = self.view.mode == "crop"
            draft = (self._dragging and ph.preview.linear.shape[0] * ph.preview.linear.shape[1] > 900_000
                     and self._last_field not in self.NO_DRAFT_FIELDS)
            job = Job(("preview", ph, rev, draft), self._render_preview, ph, s, before, crop_mode, draft)
        job.signals.done.connect(self._rendered)
        job.signals.failed.connect(self._render_failed)
        self.pool.start(job)

    @staticmethod
    def _render_preview(ph, s, before, crop_mode=False, draft=False):
        src = ph.preview
        lin = src.linear
        if draft:
            if getattr(ph, "draft", None) is None:
                ph.draft = imageio.resize_linear(src.linear, 960)
            lin = ph.draft
        if crop_mode:
            s = s.copy()
            s.border = "none"
        if before and not src.scene_referred:
            if s.crop and not crop_mode:
                H, W = lin.shape[:2]
                a, b, c, d = engine.crop_box(s, W, H)
                lin = lin[b:d, a:c]
            out = np.clip(color.apply_matrix(lin, color.conversion_matrix(color.WORKING, color.SRGB)), 0, 1)
            return np.round(color.srgb_encode(out) * 255).astype(np.uint8)
        # frame-wide work (halation, bloom, glare, density maps) is reused while only grain,
        # texture, curves and the like change
        cache = ph.__dict__.setdefault("_low_draft" if draft else "_low_main", {})
        return engine.render(lin, s, scene_referred=src.scene_referred, out_space=color.SRGB,
                             out_dtype=np.uint8, before=before, meter_ev=ph.meter_ev, meta=src.meta,
                             ignore_crop=crop_mode, low_cache=cache)

    TILE_LEVELS = (0.25, 0.3, 0.35, 0.42, 0.5, 0.6, 0.71, 0.85, 1.0)

    @staticmethod
    def _tile_source(ph, zoom):
        """The full image at just enough resolution for this zoom (cached per photo). Rendering a
        zoomed-out view from all 25+ megapixels wastes seconds on detail the screen can't show."""
        lvl = next((l for l in MainWindow.TILE_LEVELS if l >= zoom * 0.98), 1.0)
        if lvl >= 1.0:
            return ph.full.linear, 1.0
        cache = ph.__dict__.setdefault("_tile_srcs", {})
        if lvl not in cache:
            H, W = ph.full.linear.shape[:2]
            cache[lvl] = imageio.resize_linear(ph.full.linear, int(round(max(H, W) * lvl)))
        return cache[lvl], lvl

    @staticmethod
    def _render_tile(ph, s, before, region, zoom=1.0, cancel=None):
        """Render of just the visible area (region is in cropped full-res pixels)."""
        src = ph.full
        lin, lvl = MainWindow._tile_source(ph, zoom)
        H0, W0 = lin.shape[:2]
        sx = W0 / src.linear.shape[1]
        sy = H0 / src.linear.shape[0]
        a, b, c, d = engine.crop_box(s, W0, H0)
        W, H = c - a, d - b
        x0, y0, x1, y1 = region
        x0, x1 = int(np.floor(x0 * sx)), int(np.ceil(x1 * sx))
        y0, y1 = int(np.floor(y0 * sy)), int(np.ceil(y1 * sy))
        x0, y0 = max(0, min(x0, W - 1)), max(0, min(y0, H - 1))
        x1, y1 = max(x0 + 1, min(x1, W)), max(y0 + 1, min(y1, H))
        cache = ph.__dict__.setdefault("_tile_low", {}).setdefault(lvl, {})
        arr = engine.render(lin, s, scene_referred=src.scene_referred, out_space=color.SRGB,
                            out_dtype=np.uint8, before=before, meter_ev=ph.meter_ev, meta=ph.preview.meta,
                            region=(x0, y0, x1, y1), low_cache=cache, cancel=cancel)
        return arr, (x0 / sx, y0 / sy, x1 / sx, y1 / sy)

    def _rendered(self, token, result):
        self._busy = False
        kind, ph = token[0], token[1]
        if ph is self.current and result is not None:
            if kind == "tile":
                _, _, rev, zoom, _ = token
                arr, rect = result
                # zoomed in, a tile is small and quick, so a moment-old one is fine while dragging;
                # zoomed out, a stale one would sit there for seconds looking wrong
                if arr is not None and (rev == self._rev or zoom >= 1.0):
                    self.view.set_tile(to_qimage(arr), rect)
                    self.view.show_tile = not self._before
                    self._tile_info = (rev, zoom, rect)
            else:
                _, _, rev, draft = token
                img = to_qimage(result)
                ph.after_img = img
                ph.after_arr = result
                if not self._before:
                    self.view.set_image(img)
                    self._update_histogram(result)
                if not draft:
                    self._ensure_before(ph)
                if not draft:
                    self._preview_rev = rev
                if not draft:
                    pm = QPixmap.fromImage(img.scaled(420, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    ph.thumb = pm
                    self.photo_list.item(self.photos.index(ph)).setIcon(QIcon(pm))
            self.view.update()
        if self._pending or self._preview_rev != self._rev or self._tile_wanted():
            QTimer.singleShot(0, self._render_now)

    # ------------------------------------------------------------------ grain sample (100%)
    def _render_swatch(self, pad=None):
        """pad: how many windows wide to render. A settings change renders just the window first
        (quickest feedback), then a wider area in the background so it can be dragged smoothly."""
        if pad is None:
            pad = 2.6 if self.grain_swatch._last is not None else 1.0    # mid-drag: go wide at once
        if self._swatch_busy:
            self._swatch_pending = True
            self._swatch_pending_pad = max(pad, getattr(self, "_swatch_pending_pad", 1.0))
            return
        ph = self.current
        dpr = self.grain_swatch.devicePixelRatioF()
        w = max(60, int(self.grain_swatch.width() * dpr) - 4)
        h = max(40, int(self.grain_swatch.height() * dpr) - 4)
        s = self.s.copy()
        center = self._swatch_center
        if not self._swatch_manual and self.view.zoom is not None and self.view.center and self.view.full_size:
            W, H = self.view.full_size
            center = (self.view.center[0] / W, self.view.center[1] / H)
        self._swatch_busy = True
        self._swatch_pending = False
        job = Job(("swatch", ph), self._swatch_job, ph, s, w, h, center, pad)
        job.signals.done.connect(self._swatch_done)
        job.signals.failed.connect(lambda t, tb: (setattr(self, "_swatch_busy", False), print(tb, file=sys.stderr)))
        self.swatch_pool.start(job)

    @staticmethod
    def _swatch_job(ph, s, w, h, center, pad=1.0):
        s = s.copy()
        s.border = "none"
        if ph is None or ph.preview is None:
            # no photo yet: a soft grey ramp developed on the current stock
            x = np.exp2(np.linspace(-3.5, 3.0, w, dtype=np.float32)) * 0.18
            img = np.repeat(np.repeat(x[None, :, None], h, 0), 3, 2)
            return engine.render(img, s, scene_referred=True, out_dtype=np.uint8, meter_ev=0.0), "film sample"
        src = ph.full if ph.full is not None else ph.preview
        full = ph.full is not None
        cache = ph.__dict__.setdefault("_low_full" if full else "_low_prev", {})
        H0, W0 = src.linear.shape[:2]
        a, b, c, d = engine.crop_box(s, W0, H0)
        W, H = c - a, d - b
        cx, cy = center if center else (0.5, 0.45)
        w, h = min(w, W), min(h, H)
        # the visible window, kept inside the photo
        vx = float(np.clip(cx * W, w / 2, W - w / 2))
        vy = float(np.clip(cy * H, h / 2, H - h / 2))
        # render a few windows wide and tall around it, so a drag has pixels to slide over
        rw, rh = min(W, int(w * pad)), min(H, int(h * pad))
        x0 = int(np.clip(vx - rw / 2, 0, W - rw))
        y0 = int(np.clip(vy - rh / 2, 0, H - rh))
        arr = engine.render(src.linear, s, scene_referred=src.scene_referred, out_dtype=np.uint8,
                            meter_ev=ph.meter_ev, meta=ph.preview.meta, region=(x0, y0, x0 + rw, y0 + rh),
                            low_cache=cache)
        geom = dict(cx=vx / W, cy=vy / H, W=W, H=H, w=w, h=h, x0=x0, y0=y0, rw=rw, rh=rh)
        return arr, ("100% sample" if full else "sample (loading full resolution…)"), geom

    def _swatch_done(self, token, result):
        self._swatch_busy = False
        if token[1] is not self.current:          # finished after switching photos
            self._render_swatch()
            return
        arr, caption = result[0], result[1]
        g = result[2] if len(result) > 2 else None
        dpr = self.grain_swatch.devicePixelRatioF()
        img = to_qimage(arr)
        img.setDevicePixelRatio(dpr)
        self._swatch_geom = g
        if g is None:                               # the grey ramp shown before a photo is open
            self.grain_swatch.set_image(img)
        else:
            cx, cy = self._swatch_center if (self._swatch_manual and self._swatch_center) else (g["cx"], g["cy"])
            self.grain_swatch.set_image(img, (g["x0"], g["y0"]), self._swatch_px(cx, cy))
        self.swatch_caption.setText(caption)
        if self._swatch_pending:
            pad = getattr(self, "_swatch_pending_pad", 1.0)
            self._swatch_pending_pad = 1.0
            self._render_swatch(pad)
        elif g is not None and (g["rw"] < min(g["W"], g["w"] * 2.5) or g["rh"] < min(g["H"], g["h"] * 2.5)):
            self._render_swatch(2.6)                 # widen it in the background, ready for dragging

    def _swatch_px(self, cx, cy):
        """Normalised centre -> source px, kept so the window stays inside the photo."""
        g = self._swatch_geom
        return (float(np.clip(cx * g["W"], g["w"] / 2, g["W"] - g["w"] / 2)),
                float(np.clip(cy * g["H"], g["h"] / 2, g["H"] - g["h"] / 2)))

    def _swatch_dragged(self, dx, dy):
        g = self._swatch_geom
        if not g:
            return
        dpr = self.grain_swatch.devicePixelRatioF()
        cx, cy = self._swatch_center if (self._swatch_manual and self._swatch_center) else (g["cx"], g["cy"])
        px, py = self._swatch_px(cx, cy)
        px, py = self._swatch_px((px - dx * dpr) / g["W"], (py - dy * dpr) / g["H"])
        self._swatch_center = (px / g["W"], py / g["H"])
        self._swatch_manual = True
        self.grain_swatch.set_centre((px, py))      # instant: slide over what's already rendered
        # only render again once the window gets near the edge of the rendered area
        mx, my = g["w"] * 0.25, g["h"] * 0.25
        inside = (px - g["w"] / 2 >= g["x0"] + (mx if g["x0"] > 0 else 0) and
                  px + g["w"] / 2 <= g["x0"] + g["rw"] - (mx if g["x0"] + g["rw"] < g["W"] else 0) and
                  py - g["h"] / 2 >= g["y0"] + (my if g["y0"] > 0 else 0) and
                  py + g["h"] / 2 <= g["y0"] + g["rh"] - (my if g["y0"] + g["rh"] < g["H"] else 0))
        if not inside:
            self._render_swatch(2.6)

    def _swatch_released(self):
        g = self._swatch_geom
        if not g or not self._swatch_center:
            return
        px, py = self._swatch_px(*self._swatch_center)
        # recentre the rendered area in the background if the window wandered off its middle
        if abs(px - (g["x0"] + g["rw"] / 2)) > g["w"] * 0.6 or abs(py - (g["y0"] + g["rh"] / 2)) > g["h"] * 0.6:
            self._render_swatch(2.6)

    def _update_histogram(self, arr):
        self.histogram.set_image(arr)
        h = self.histogram
        bits = []
        if h.clip_lo > 0.002:
            bits.append(f"shadows clipped {h.clip_lo * 100:.1f}%")
        if h.clip_hi > 0.002:
            bits.append(f"highlights clipped {h.clip_hi * 100:.1f}%")
        self.hist_info.setText("  ·  ".join(bits) if bits else "no clipping")

    def _render_failed(self, token, tb):
        self._busy = False
        self.statusBar().showMessage("Render failed: " + tb.strip().splitlines()[-1], 10000)
        print(tb, file=sys.stderr)

    def _before_key(self, ph):
        crop_mode = self.view.mode == "crop"
        return (None if crop_mode else tuple(self.s.crop or ()), crop_mode, self.s.auto_meter)

    def _ensure_before(self, ph):
        """Render the untouched original once in the background, so \\ can show it instantly."""
        key = self._before_key(ph)
        if ph.before_key == key or getattr(ph, "_before_pending", None) == key:
            return
        ph._before_pending = key
        s0 = Settings(crop=list(self.s.crop) if self.s.crop else None, auto_meter=self.s.auto_meter)
        job = Job(("before", ph, key), self._render_preview, ph, s0, True, self.view.mode == "crop", False)
        job.signals.done.connect(self._before_done)
        job.signals.failed.connect(lambda t, tb: print(tb, file=sys.stderr))
        self.pool.start(job)

    def _before_done(self, token, arr):
        _, ph, key = token
        ph._before_pending = None
        ph.before_img, ph.before_key = to_qimage(arr), key
        ph.before_arr = arr
        if ph is self.current and self._before:
            self._show_before(True)

    def _show_before(self, on):
        ph = self.current
        if ph is None:
            return
        if on and ph.before_img is not None and ph.before_key == self._before_key(ph):
            self.view.set_image(ph.before_img)
            self.view.show_tile = False
            self.view.badge = "BEFORE"
            self.histogram.set_image(ph.before_arr)
        elif not on and ph.after_img is not None:
            self.view.set_image(ph.after_img)
            self.view.show_tile = True
            self.view.badge = ""
        self.view.update()

    def _set_before(self, on):
        if self._before == on:
            return
        self._before = on
        ph = self.current
        if ph is None:
            return
        if on and (ph.before_img is None or ph.before_key != self._before_key(ph)):
            self._ensure_before(ph)            # not ready yet: shows the moment it is
        self._show_before(on)
        if not on and getattr(ph, "after_arr", None) is not None:
            self._update_histogram(ph.after_arr)

    # ------------------------------------------------------------------ zoom
    def _load_full(self, ph):
        if getattr(ph, "_loading_full", False):
            return
        ph._loading_full = True
        self.statusBar().showMessage("Loading full resolution…")
        job = Job(("full", ph), lambda: imageio.load(ph.path))
        job.signals.done.connect(self._full_loaded)
        job.signals.failed.connect(self._failed)
        self.pool.start(job)

    def _full_loaded(self, token, src):
        _, ph = token
        ph.full = src
        ph._loading_full = False
        self.statusBar().clearMessage()
        if ph is self.current:
            self.swatch_timer.start()
        if ph is self.current:
            self.render_timer.start()

    def _update_full_size(self):
        ph = self.current
        if ph is None or ph.preview is None:
            return
        W0, H0 = ph.preview.full_size
        if self.view.mode != "crop" and self.s.crop:
            a, b, c, d = engine.crop_box(self.s, W0, H0)
            size = (c - a, d - b)
        else:
            size = (W0, H0)
        if size != self.view.full_size:
            self._tile_info = None
            self.view.set_full_size(size)

    def _view_moved(self):
        self._tile_gen += 1          # a tile for the old view is wasted work: stop it
        self._swatch_manual = False
        self._update_zoom_label()
        self.swatch_timer.start()
        self.zoom_timer.start()

    def _update_zoom_label(self):
        v = self.view
        self.zoom_label.setText("Fit" if v.zoom is None else f"{v.zoom_percent()}%")

    def zoom_fit(self):
        self.view.set_zoom(None)

    def zoom_100(self):
        self.view.set_zoom(1.0)

    def zoom_in(self):
        self.view.zoom_by(1.5)

    def zoom_out(self):
        self.view.zoom_by(1 / 1.5)

    def _close_loupe(self, render=True):
        """Back to Fit (used when switching photos or entering crop)."""
        self.view.set_zoom(None)
        self.view.clear_tile()
        self._tile_info = None

    # stock thumbnails rendered from the current photo
    # settings that don't show at thumbnail size: changing them doesn't re-render all the stocks
    THUMB_IGNORED = {"stock", "strength", "grain", "grain_size", "grain_roughness", "seed", "dust",
                     "scratches", "texture", "texture_amount", "sharpen", "softness", "denoise",
                     "halation", "bloom", "border", "leak", "leak_amount", "leak_seed", "aspect"}

    def _render_stock_thumbs(self):
        ph = self.current
        if ph is None or ph.preview is None:
            return
        big = self.stack.currentIndex() == 1          # the Compare view shows them large
        edge = 660 if big else 380
        base = self.s.copy()
        key = (id(ph), edge, repr(sorted((k, v) for k, v in base.to_dict().items()
                                         if k not in self.THUMB_IGNORED)))
        if key == getattr(self, "_thumb_key", None):
            return                                    # nothing visible in them changed
        self._thumb_key = key
        self._thumb_gen += 1
        gen = self._thumb_gen
        srcs = ph.__dict__.setdefault("_thumb_srcs", {})
        if edge not in srcs:
            srcs[edge] = imageio.resize_linear(ph.preview.linear, edge)
        small = srcs[edge]
        # current stock first, then the ones on screen, then the rest
        visible = [sid for sid, it in self._stock_items.items() if not it.isHidden()]
        order = [base.stock] + [sid for sid in visible if sid != base.stock] + \
                [sid for sid in stocks.ORDER if sid != base.stock and sid not in visible]
        for sid in order:
            s = base.copy()
            s.stock, s.halation, s.bloom, s.border = sid, None, None, "none"
            s.strength = 100
            s.grain, s.sharpen = 0, 0
            s.dust, s.scratches, s.texture, s.leak = 0, 0, "none", "none"
            job = Job(("thumb", gen, ph, sid), self._thumb_job, small, s, ph, gen, lambda: self._thumb_gen)
            job.signals.done.connect(self._thumb_done)
            job.signals.failed.connect(lambda t, tb: print(tb, file=sys.stderr))
            self.thumb_pool.start(job)

    @staticmethod
    def _thumb_job(small, s, ph, gen, current_gen):
        if current_gen() != gen:
            return None                               # superseded before it started
        return engine.render(small, s, scene_referred=ph.preview.scene_referred, out_dtype=np.uint8,
                             meter_ev=ph.meter_ev, meta=ph.preview.meta)

    def _thumb_done(self, token, arr):
        _, gen, ph, sid = token
        if arr is None or gen != self._thumb_gen or ph is not self.current:
            return
        icon = QIcon(QPixmap.fromImage(to_qimage(arr)))
        for table in (self._stock_items, self._big_items):
            it = table.get(sid)
            if it is not None:
                it.setIcon(icon)

    # ------------------------------------------------------------------ export
    def _targets(self, which):
        if which == "current":
            return [self.current] if self.current else []
        rows = sorted({i.row() for i in self.photo_list.selectedIndexes()})
        return [self.photos[r] for r in rows] or ([self.current] if self.current else [])

    def export(self, which="current"):
        self._store_current()
        targets = self._targets(which)
        if not targets:
            return
        start = self.prefs.get("export_dir") or os.path.dirname(targets[0].path)
        d = QFileDialog.getExistingDirectory(self, "Export to folder", start)
        if not d:
            return
        self.prefs["export_dir"] = d
        gs.save_prefs(self.prefs)
        fmt = self.c_format.currentData()
        space = imageio.OUTPUT_SPACES[self.c_space.currentData()]
        size = self.c_size.currentData()
        quality = int(self.c_quality.value())
        jobs = []
        for ph in targets:
            s = (ph.settings or self.s).copy()
            stem = Path(ph.path).stem
            out = os.path.join(d, f"{stem}_{s.stock}.{fmt}")
            jobs.append((ph, s, out))
        self._run_batch(jobs, fmt, space, size, quality)

    def _run_batch(self, jobs, fmt, space, size, quality, lightroom=False, close_after=True):
        dlg = QProgressDialog("Developing…", "Cancel", 0, 1000, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        state = {"cancel": False}
        dlg.canceled.connect(lambda: state.__setitem__("cancel", True))
        n = len(jobs)

        def work(sig):
            done = []
            for i, (ph, s, out) in enumerate(jobs):
                if state["cancel"]:
                    break
                export_one(ph, s, out, fmt=fmt, space=space, size=size, quality=quality, lightroom=lightroom,
                           progress=lambda f, i=i: sig.progress.emit(None, (i + f) / n),
                           cancel=lambda: state["cancel"])
                done.append(out)
            return done

        job = Job(("export",), lambda: work(job.signals))
        job.signals.progress.connect(lambda _, f: dlg.setValue(int(f * 1000)))

        def finished(_, done):
            dlg.setValue(1000)
            dlg.close()
            if lightroom:
                self.statusBar().showMessage("Saved to Lightroom ✓  (⌘S again any time)", 6000)
                if close_after and len(done) == len(jobs) and self.prefs.get("quit_after_lr", True):
                    QTimer.singleShot(400, self.close)
            else:
                self.statusBar().showMessage(f"Exported {len(done)} file(s)", 6000)

        job.signals.done.connect(finished)
        job.signals.failed.connect(lambda t, tb: (dlg.close(), self._failed(t, tb)))
        self.pool.start(job)

    def quick_save(self):
        """⌘S: back to Lightroom when the photo came from Lightroom, otherwise a quick export."""
        if self.current is None:
            return
        if self.lightroom:
            self.save_to_lightroom(close_after=False)
            return
        d = self.prefs.get("export_dir")
        if not d or not os.path.isdir(d):
            self.export(which="current")
            return
        self._store_current()
        ph = self.current
        s = (ph.settings or self.s).copy()
        fmt = self.c_format.currentData()
        out = os.path.join(d, f"{Path(ph.path).stem}_{s.stock}.{fmt}")
        self._run_batch([(ph, s, out)], fmt, imageio.OUTPUT_SPACES[self.c_space.currentData()],
                        self.c_size.currentData(), int(self.c_quality.value()))
        self.statusBar().showMessage(f"Saving to {out}…", 4000)

    def save_to_lightroom(self, close_after=True):
        """Write the look back into the current photo's Lightroom TIFF (never touches other files)."""
        self._store_current()
        ph = self.current
        if ph is None or not ph.lightroom:
            return
        self._run_batch([(ph, (ph.settings or self.s).copy(), ph.path)], "tif", None, 0, 100,
                        lightroom=True, close_after=close_after)

    def export_lut(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save LUT", os.path.join(
            self.prefs.get("export_dir", str(Path.home())), f"GrainHause {stocks.get(self.s.stock).name}.cube"),
            "Cube LUT (*.cube)")
        if path:
            engine.write_cube(path, self.s)
            self.statusBar().showMessage(f"LUT saved: {path}", 6000)

    def _lightroom_help(self):
        QMessageBox.information(self, "Using GrainHause with Lightroom", LIGHTROOM_HELP)

    def closeEvent(self, e):
        self._save_session()
        self._store_current()
        self._save_sidecar()
        super().closeEvent(e)


LIGHTROOM_HELP = """Lightroom Classic → Settings/Preferences → External Editing.

Under “Additional External Editor”, choose the GrainHause app, then set:
  • File Format: TIFF
  • Color Space: ProPhoto RGB
  • Bit Depth: 16 bits/component
  • Resolution: 300, Compression: None or ZIP
Click “Preset → Save Current Settings as New Preset…” and name it GrainHause.

To use it: right-click a photo → Edit In → GrainHause. Lightroom makes an -Edit.tif with your
Lightroom edits and opens it here. Pick a stock, adjust, then press “Save & return to Lightroom”.
The film look is written into that TIFF and it appears in your catalog next to the original.

Tip: do exposure/white balance in Lightroom or here — but keep Lightroom's own film-style
presets off so the film model starts from a clean, neutral edit."""


def export_one(ph: Photo, s: Settings, out_path: str, *, fmt: str, space, size: int, quality: int,
               lightroom: bool = False, progress=None, cancel=None):
    src = ph.full or imageio.load(ph.path)
    lin = src.linear
    if size and max(lin.shape[:2]) > size:
        lin = imageio.resize_linear(lin, size)
    if ph.meter_ev is None:
        ph.meter_ev = engine.scene_meter(lin, src.scene_referred)
    if lightroom:
        space, icc = src.space, src.icc
        dtype = np.uint16 if src.bit_depth >= 16 else np.uint8
    else:
        icc = None
        dtype = np.uint16 if fmt == "tif" else np.uint8
    arr = engine.render(lin, s, scene_referred=src.scene_referred, out_space=space, out_dtype=dtype,
                        meter_ev=ph.meter_ev, meta=src.meta, progress=progress, cancel=cancel)
    if arr is None:
        return
    imageio.save(out_path, arr, space=space, icc=icc, fmt=fmt, quality=quality,
                 exif=src.exif if fmt == "jpg" else None)


# ====================================================================== app

class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.window: MainWindow | None = None
        self._early = []
        self.installEventFilter(self)

    def eventFilter(self, obj, e):
        t = e.type()
        if (t in (QEvent.KeyPress, QEvent.KeyRelease) and self.window is not None
                and e.key() == Qt.Key_Backslash and self.activeWindow() is self.window):
            if not e.isAutoRepeat():
                self.window._set_before(t == QEvent.KeyPress)
            return True
        return super().eventFilter(obj, e)

    def event(self, e):
        if e.type() == QEvent.FileOpen:   # macOS: files sent by Lightroom / Finder
            path = e.file()
            if self.window is None:
                self._early.append(path)
            else:
                self.window.open_paths([path], lightroom=_looks_like_lightroom([path]))
            return True
        return super().event(e)


def _looks_like_lightroom(paths) -> bool:
    """Lightroom names the files it hands to external editors "<name>-Edit.tif" (or -Edit-2.tif …)."""
    import re
    return bool(paths) and all(Path(p).suffix.lower() in (".tif", ".tiff")
                               and re.search(r"-Edit(-\d+)?$", Path(p).stem) for p in paths)


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.Window, "#0c0c0e"), (QPalette.WindowText, "#e8e9ec"), (QPalette.Base, "#111113"),
                    (QPalette.AlternateBase, "#16161a"), (QPalette.Text, "#e8e9ec"), (QPalette.Button, "#18181c"),
                    (QPalette.ButtonText, "#e8e9ec"), (QPalette.Highlight, "#d9dde3"), (QPalette.HighlightedText, "#0b0b0c"),
                    (QPalette.ToolTipBase, "#18181c"), (QPalette.ToolTipText, "#e8e9ec")):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QMainWindow, QSplitter {{ background:#0c0c0e; }}
        QSplitter::handle {{ background:#0c0c0e; width:1px; }}
        QMenuBar {{ background:#0c0c0e; }}
        QStatusBar {{ background:#0c0c0e; color:{ACCENT_DIM}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:#0c0c0e; border:none; }}
        QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
        QScrollBar::handle:vertical {{ background:#2c2d33; border-radius:3px; min-height:24px; }}
        QScrollBar::handle:vertical:hover {{ background:#4a4c54; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
        QSlider::groove:horizontal {{ height:2px; background:#2a2b30; border-radius:1px; }}
        QSlider::sub-page:horizontal {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6f747d, stop:1 #e9ecf1); border-radius:1px; }}
        QSlider::handle:horizontal {{ background:{CHROME}; width:13px; height:13px; margin:-6px 0; border-radius:7px;
                                     border:1px solid #0c0c0e; }}
        QListWidget {{ border:none; background:#0c0c0e; }}
        QListWidget::item {{ padding:3px; }}
        QPushButton, QToolButton {{ padding:5px 10px; border:1px solid #2a2b30; border-radius:6px; background:#141417;
                                   color:#e8e9ec; }}
        QPushButton:hover, QToolButton:hover {{ border-color:#7d828b; background:#18191d; }}
        QPushButton:checked, QToolButton:checked {{ background:#22242a; border-color:#d9dde3; }}
        QPushButton#primary {{ background:{CHROME}; color:#0b0b0c; border:none; font-weight:700; padding:9px; }}
        QPushButton#primary:hover {{ background:#ffffff; }}
        QComboBox {{ background:#141417; border:1px solid #2a2b30; border-radius:6px; padding:4px 8px; }}
        QComboBox:hover {{ border-color:#7d828b; }}
        QDoubleSpinBox {{ background:#141417; border:1px solid #222328; border-radius:5px; padding:1px 4px; }}
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width:0px; height:0px; border:none; subcontrol-origin:border; }}
        QDoubleSpinBox QLineEdit {{ background:transparent; color:#e8e9ec; border:none; padding:0; }}
        QDoubleSpinBox:focus {{ border-color:#9ea3ab; }}
        QListWidget#stockGrid {{ background:#0f0f11; border:1px solid #1d1e22; border-radius:8px; padding:4px; }}
        QListWidget#stockGrid::item {{ color:#c9ccd2; border-radius:6px; padding:4px 2px; }}
        QListWidget#stockGrid::item:hover {{ background:#18191d; }}
        QListWidget#stockGrid::item:selected {{ background:#1e2025; color:#ffffff; border:1px solid #d9dde3; }}
        QListWidget#filmstrip {{ background:#0c0c0e; font-size:10px; color:{ACCENT_DIM}; }}
        QListWidget#filmstrip::item {{ color:{ACCENT_DIM}; border-radius:6px; padding:5px 3px 3px 3px; border:1px solid transparent; }}
        QListWidget#filmstrip::item:hover {{ background:#141417; }}
        QListWidget#filmstrip::item:selected {{ background:#18191d; color:#f2f3f5; border:1px solid #9ea3ab; }}
        QToolButton#filterBtn {{ padding:3px 8px; border:1px solid #26272c; border-radius:10px; font-size:11px; background:transparent; }}
        QToolButton#filterBtn:checked {{ background:{CHROME}; color:#0b0b0c; border-color:#d9dde3; font-weight:600; }}
        QToolButton#resetBtn {{ padding:0; border:none; background:transparent; color:#7d828b; font-size:13px; }}
        QToolButton#resetBtn:hover {{ color:#ffffff; }}
        QToolButton#resetBtn:disabled {{ color:#2a2b30; }}
        QToolButton#sectionReset {{ padding:1px 6px; border:none; background:transparent; color:#6f747d; font-size:11px; }}
        QToolButton#sectionReset:hover {{ color:#ffffff; }}
        QLabel#brand {{ font-family:'{BRAND_FONT}'; font-weight:700; font-size:13px; letter-spacing:3px;
                        color:#e4e7ec; }}
        QLabel#swatch {{ background:#0a0a0b; border:1px solid #1d1e22; border-radius:8px; }}
        QLabel#swatchCaption {{ color:{ACCENT_DIM}; font-size:10px; }}
    """)


def _asset(name):
    base = getattr(sys, "_MEIPASS", None) or os.path.join(os.path.dirname(__file__), "..")
    p = os.path.join(base, "assets", name)
    return p if os.path.exists(p) else None


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = App(argv)
    app.setApplicationName(APP_NAME)
    icon = _asset("icon.png")
    if icon:
        app.setWindowIcon(QIcon(icon))
    font = _asset(os.path.join("fonts", "Unbounded.ttf"))
    if font:
        from PySide6.QtGui import QFontDatabase
        QFontDatabase.addApplicationFont(font)
    apply_theme(app)
    paths = [a for a in argv[1:] if not a.startswith("-") and os.path.exists(a)]
    lr = "--lightroom" in argv or _looks_like_lightroom(paths)
    win = MainWindow([], lightroom=False)
    app.window = win
    win.show()
    app.processEvents()
    win.restore_session()               # photos from earlier sessions come back, edits and all
    if paths:
        win.open_paths(paths, lightroom=lr)
    if app._early:
        win.open_paths(app._early, lightroom=_looks_like_lightroom(app._early))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
