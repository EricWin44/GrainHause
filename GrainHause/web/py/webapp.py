"""Browser bridge for the GrainHause engine (runs inside Pyodide, one copy per Web Worker).

The film engine itself (grainhouse/*) is used unchanged. This module only:
  * runs the engine's row chunks serially (a browser worker has no threads; the web app gets its
    parallelism from several workers each rendering a band of rows instead)
  * keeps the photos this worker has been given, and the per-photo caches the desktop app keeps
  * moves pixels in and out as raw bytes
"""
from __future__ import annotations

import dataclasses
import json
import os
import struct

import numpy as np

from grainhouse import color, effects, engine, film, imageio, settings as gs, stocks, textures
from grainhouse.settings import Settings


class _Serial:
    """Stand-in for ThreadPoolExecutor: runs the engine's row chunks one after another."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, it):
        return [fn(x) for x in it]


engine.ThreadPoolExecutor = _Serial
engine._workers = lambda: 1


# Pyodide's Pillow is built without LittleCMS (PIL.ImageCms). The engine only uses it to read an
# embedded profile's name and to write an sRGB profile, so do both in plain Python.
def _icc_description(icc):
    if not icc or len(icc) < 132:
        return None
    try:
        n = struct.unpack(">I", icc[128:132])[0]
        for i in range(n):
            sig, off, size = struct.unpack(">4sII", icc[132 + 12 * i:144 + 12 * i])
            if sig != b"desc":
                continue
            tag = icc[off:off + size]
            if tag[:4] == b"desc":           # ICC v2 textDescriptionType
                ln = struct.unpack(">I", tag[8:12])[0]
                return tag[12:12 + ln].split(b"\0")[0].decode("latin-1").strip() or None
            if tag[:4] == b"mluc":           # ICC v4 multiLocalizedUnicodeType: first record
                ln, o = struct.unpack(">II", tag[20:28])
                return tag[o:o + ln].decode("utf-16-be", "replace").strip("\0 ") or None
        return None
    except Exception:
        return None


_make_icc = imageio.make_icc


def _make_icc_no_lcms(space):
    if space.name == "sRGB":                 # same matrix/curve profile the other spaces get
        space = dataclasses.replace(space, name="sRGB IEC61966-2.1")
    return _make_icc(space)


imageio.icc_description = _icc_description
imageio.make_icc = _make_icc_no_lcms

PREVIEW_EDGE = 1800      # same as the desktop app
DRAFT_EDGE = 960

# photos held by this worker: id -> dict
PHOTOS: dict[str, dict] = {}
KEEP = 2


def _touch(pid, ph):
    PHOTOS.pop(pid, None)
    PHOTOS[pid] = ph
    while len(PHOTOS) > KEEP:
        PHOTOS.pop(next(iter(PHOTOS)))


def _settings(js: str) -> Settings:
    return Settings.from_dict(json.loads(js))


# ------------------------------------------------------------------ catalogue

def info() -> str:
    st = []
    for sid in stocks.ORDER:
        s = stocks.STOCKS[sid]
        st.append(dict(id=s.id, name=s.name, family=s.family, iso=s.iso, blurb=s.blurb, kind=s.kind,
                       halation=s.halation, bloom=s.bloom, default_lab=s.default_lab))
    from grainhouse import __version__
    return json.dumps(dict(
        version=__version__,
        stocks=st,
        defaults=Settings().to_dict(),
        labs=film.LABS, lab_labels=film.LAB_LABELS,
        textures=textures.TEXTURES, texture_labels=textures.TEXTURE_LABELS,
        leaks=gs.LEAK_STYLES, leak_labels=gs.LEAK_LABELS,
        borders=gs.BORDERS, border_labels=gs.BORDER_LABELS,
        aspects=gs.ASPECTS, aspect_labels=gs.ASPECT_LABELS,
        wb_presets={k: (list(v) if v else None) for k, v in gs.WB_PRESETS.items()},
        grain_presets={k: list(v) for k, v in gs.GRAIN_PRESETS.items()},
        output_spaces=list(imageio.OUTPUT_SPACES),
        preview_edge=PREVIEW_EDGE, draft_edge=DRAFT_EDGE, lowres=effects.LOWRES,
        wb_reference=color.WB_REFERENCE_K,
    ))


def warm():
    from grainhouse import grain
    for k in ("dye", "silver"):
        grain.tiles(k)


# ------------------------------------------------------------------ loading (worker 0)

def _header(pid, src: imageio.Source, ph) -> str:
    meta = {}
    if "as_shot_wb" in src.meta:
        meta["as_shot_wb"] = [float(v) for v in src.meta["as_shot_wb"]]
    if src.meta.get("as_shot_gains") is not None:
        meta["as_shot_gains"] = [float(v) for v in src.meta["as_shot_gains"]]
    pv = ph["preview"]
    return json.dumps(dict(
        id=pid, kind=src.kind, full_size=[int(src.full_size[0]), int(src.full_size[1])],
        full_shape=[int(src.linear.shape[0]), int(src.linear.shape[1])],
        space=src.space.name, bit_depth=int(src.bit_depth), scene_referred=bool(src.scene_referred),
        meta=meta, meter_ev=float(ph["meter_ev"]), preview_shape=[int(pv.shape[0]), int(pv.shape[1])],
    ))


def _finish_open(pid, src: imageio.Source):
    preview = imageio.resize_linear(src.linear, PREVIEW_EDGE).astype(np.float32)
    ev = engine.scene_meter(preview, src.scene_referred)
    ph = dict(src=src, preview=np.ascontiguousarray(preview), scene=src.scene_referred, meta=src.meta,
              meter_ev=ev, caches={}, levels={})
    _touch(pid, ph)
    return _header(pid, src, ph)


def open_file(pid: str, name: str, data) -> str:
    """JPEG / PNG / TIFF / HEIC / WebP bytes -> full Source (kept here) + header."""
    d = "/tmp/in"
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.path.basename(name) or "photo")
    with open(path, "wb") as f:
        f.write(data.to_bytes() if hasattr(data, "to_bytes") else bytes(data))
    try:
        src = imageio.load(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return _finish_open(pid, src)


def open_raw(pid: str, name: str, data, w: int, h: int, meta_js: str) -> str:
    """16-bit linear ProPhoto RGB decoded by LibRaw (in the browser), plus LibRaw's colour data.
    Mirrors imageio._load_raw after rawpy's postprocess()."""
    m = json.loads(meta_js)
    buf = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    rgb = np.frombuffer(buf, dtype=np.uint16).reshape(h, w, 3)
    day = [float(v) for v in (m.get("pre_mul") or [])]
    cam = [float(v) for v in (m.get("cam_mul") or [])]
    usable_day = len(day) >= 3 and all(v > 0 for v in day[:3])
    as_shot = None
    if usable_day and len(cam) >= 3 and all(v > 0 for v in cam[:3]):
        r = np.array([cam[0] / day[0], cam[1] / day[1], cam[2] / day[2]])
        r = r / r[1]
        try:
            xyz_to_cam = np.asarray(m["cam_xyz"], np.float64)[:3]
            cam_to_xyz = np.linalg.inv(xyz_to_cam)
            to_w = np.linalg.inv(color.REC2020_LINEAR.to_xyz) @ cam_to_xyz
            white_cam = 1.0 / r
            white_w = to_w @ white_cam
            base_w = to_w @ np.ones(3)
            g = base_w / white_w
            if np.all(np.isfinite(g)) and np.all(g > 0):
                as_shot = g / float(g @ color.LUMA_2020)
        except Exception:
            as_shot = None
    lin = rgb.astype(np.float32) / 65535.0
    del rgb
    lin = color.apply_matrix(lin, color.conversion_matrix(color.PROPHOTO_LINEAR, color.WORKING))
    meta = {}
    if as_shot is not None:
        meta["as_shot_gains"] = as_shot.astype(np.float32)
        meta["as_shot_wb"] = color.wb_from_gains(as_shot)
    else:
        meta["as_shot_wb"] = (color.WB_REFERENCE_K, 0.0)
    src = imageio.Source(name, "raw", lin, color.PROPHOTO, None, 16, True, (w, h), meta=meta)
    return _finish_open(pid, src)


def preview_bytes(pid: str):
    return memoryview(PHOTOS[pid]["preview"].reshape(-1)).cast("B")


# ------------------------------------------------------------------ other workers

def _meta_from_header(hd):
    meta = {}
    if "as_shot_wb" in hd["meta"]:
        meta["as_shot_wb"] = tuple(hd["meta"]["as_shot_wb"])
    if "as_shot_gains" in hd["meta"]:
        meta["as_shot_gains"] = np.asarray(hd["meta"]["as_shot_gains"], np.float32)
    return meta


def set_preview(header_js: str, data):
    hd = json.loads(header_js)
    h, w = hd["preview_shape"]
    buf = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    pv = np.frombuffer(buf, dtype=np.float32).reshape(h, w, 3).copy()
    _touch(hd["id"], dict(src=None, preview=pv, scene=hd["scene_referred"], meta=_meta_from_header(hd),
                          meter_ev=hd["meter_ev"], caches={}, levels={}))


def has(pid: str) -> bool:
    return pid in PHOTOS


def drop(pid: str):
    PHOTOS.pop(pid, None)


# ------------------------------------------------------------------ rendering

def _out(arr):
    return memoryview(np.ascontiguousarray(arr).reshape(-1)).cast("B")


def _lin_for(ph, which):
    if which == "draft":
        if "draft" not in ph:
            ph["draft"] = imageio.resize_linear(ph["preview"], DRAFT_EDGE)
        return ph["draft"]
    if which.startswith("thumb"):
        edge = int(which[5:])
        key = f"thumb{edge}"
        if key not in ph:
            ph[key] = imageio.resize_linear(ph["preview"], edge)
        return ph[key]
    return ph["preview"]


def render(pid: str, which: str, settings_js: str, before: bool, ignore_crop: bool, region_js: str):
    """Develop (a band of) the preview, draft or a stock thumbnail. Returns RGB uint8 bytes."""
    ph = PHOTOS[pid]
    s = _settings(settings_js)
    region = json.loads(region_js) if region_js else None
    lin = _lin_for(ph, which)
    if ignore_crop:
        s.border = "none"
    if before and not ph["scene"]:
        # an already-developed photo's "before" is just the photo (as the desktop app does)
        if s.crop and not ignore_crop:
            H, W = lin.shape[:2]
            a, b, c, d = engine.crop_box(s, W, H)
            lin = lin[b:d, a:c]
        if region:
            x0, y0, x1, y1 = region
            lin = lin[y0:y1, x0:x1]
        out = np.clip(color.apply_matrix(lin, color.conversion_matrix(color.WORKING, color.SRGB)), 0, 1)
        return _out(np.round(color.srgb_encode(out) * 255).astype(np.uint8))
    cache = ph["caches"].setdefault(which, {})
    arr = engine.render(lin, s, scene_referred=ph["scene"], out_space=color.SRGB, out_dtype=np.uint8,
                        before=before, meter_ev=ph["meter_ev"], meta=ph["meta"], ignore_crop=ignore_crop,
                        low_cache=cache, region=tuple(region) if region else None)
    return _out(arr)


def thumb(pid: str, edge: int, settings_js: str):
    ph = PHOTOS[pid]
    s = _settings(settings_js)
    lin = _lin_for(ph, f"thumb{int(edge)}")
    arr = engine.render(lin, s, scene_referred=ph["scene"], out_dtype=np.uint8, meter_ev=ph["meter_ev"],
                        meta=ph["meta"])
    return json.dumps([int(arr.shape[0]), int(arr.shape[1])]), _out(arr)


def ramp(w: int, h: int, settings_js: str):
    """Grey ramp developed on the current stock: the grain sample before a photo is open."""
    s = _settings(settings_js)
    s.border = "none"
    x = np.exp2(np.linspace(-3.5, 3.0, w, dtype=np.float32)) * 0.18
    img = np.repeat(np.repeat(x[None, :, None], h, 0), 3, 2)
    return _out(engine.render(img, s, scene_referred=True, out_dtype=np.uint8, meter_ev=0.0))


def add_border(data, h: int, w: int, settings_js: str, bits: int):
    s = _settings(settings_js)
    st = stocks.get(s.stock)
    buf = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    dtype = np.uint16 if bits == 16 else np.uint8
    arr = np.frombuffer(buf, dtype=dtype).reshape(h, w, 3)
    f = arr.astype(np.float32) / engine._scale(dtype)
    f = effects.add_border(f, s.border, st.name, st.iso, s.seed)
    out = engine._quantize(f, dtype)
    hd = json.dumps([int(out.shape[0]), int(out.shape[1])])
    return hd, _out(out)


# ------------------------------------------------------------------ full resolution (bands)

def _level(ph, edge: int):
    """The full image at a long edge of `edge` px (cached), like the desktop app's tile sources."""
    full = ph["src"].linear
    L = max(full.shape[:2])
    if edge >= L:
        return full
    lv = ph["levels"]
    if edge not in lv:
        if len(lv) >= 2:
            lv.pop(next(iter(lv)))
        lv[edge] = imageio.resize_linear(full, edge)
    return lv[edge]


def level_shape(pid: str, edge: int) -> str:
    a = _level(PHOTOS[pid], int(edge))
    return json.dumps([int(a.shape[0]), int(a.shape[1])])


def low_for(pid: str, edge: int, settings_js: str):
    """The small copy of the (cropped) frame that every band of one render must share."""
    ph = PHOTOS[pid]
    s = _settings(settings_js)
    src = _level(ph, int(edge))
    if s.crop:
        H0, W0 = src.shape[:2]
        a, b, c, d = engine.crop_box(s, W0, H0)
        src = src[b:d, a:c]
    H, W = src.shape[:2]
    if max(H, W) > effects.LOWRES:
        low = effects.resize_float(src, engine._low_size(W, H), resample=effects.Image.BOX)
    else:
        low = src.copy()
    hd = json.dumps([int(low.shape[0]), int(low.shape[1])])
    return hd, _out(low.astype(np.float32))


def band(pid: str, edge: int, r0: int, r1: int):
    src = _level(PHOTOS[pid], int(edge))
    return _out(src[r0:r1])


class BandSource:
    """Looks enough like the full (H, W, 3) image for engine.render, but holds only the rows one
    band needs. Slicing the whole frame (the crop) gives a lazy view; slicing rows (the engine's
    chunks) must land inside the band."""

    def __init__(self, data, row0, H, W, oy=0, ox=0, h=None, w=None, root=True):
        self.data, self.row0, self.H, self.W = data, row0, H, W
        self.oy, self.ox = oy, ox
        self.h = H if h is None else h
        self.w = W if w is None else w
        self.root = root

    @property
    def shape(self):
        return (self.h, self.w, 3)

    def __getitem__(self, key):
        ys, xs = key
        y0, y1, _ = ys.indices(self.h)
        x0, x1, _ = xs.indices(self.w)
        ay0, ay1 = self.oy + y0, self.oy + y1
        ax0, ax1 = self.ox + x0, self.ox + x1
        if self.row0 <= ay0 and ay1 <= self.row0 + self.data.shape[0]:
            return self.data[ay0 - self.row0:ay1 - self.row0, ax0:ax1]
        if self.root:
            return BandSource(self.data, self.row0, self.H, self.W, ay0, ax0, ay1 - ay0, ax1 - ax0, root=False)
        raise IndexError("BAND_TOO_SMALL")


BAND_CACHES: dict = {}


def render_band(pid_key: str, data, row0: int, rows: int, H: int, W: int, low_data, low_h: int, low_w: int,
                settings_js: str, header_js: str, region_js: str, bits: int):
    """Render `region` (cropped full-level px) from a band of rows of the full-level image."""
    hd = json.loads(header_js)
    s = _settings(settings_js)
    buf = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    arr = np.frombuffer(buf, dtype=np.float32).reshape(rows, W, 3)
    src = BandSource(arr, int(row0), int(H), int(W))
    lbuf = low_data.to_bytes() if hasattr(low_data, "to_bytes") else bytes(low_data)
    low = np.frombuffer(lbuf, dtype=np.float32).reshape(low_h, low_w, 3).copy()
    # the key engine.render looks for: shape of the cropped frame, and the crop
    if s.crop:
        a, b, c, d = engine.crop_box(s, W, H)
        shape = (d - b, c - a, 3)
    else:
        shape = (H, W, 3)
    key = (shape, tuple(s.crop) if s.crop else None)
    cache = BAND_CACHES.get(pid_key)
    if cache is None or key not in cache:
        BAND_CACHES.clear()
        cache = {}
        BAND_CACHES[pid_key] = cache
    cache[key] = low
    dtype = np.uint16 if bits == 16 else np.uint8
    out_space = imageio.OUTPUT_SPACES.get(hd.get("out_space") or "sRGB", color.SRGB)
    region = tuple(json.loads(region_js))
    out = engine.render(src, s, scene_referred=hd["scene_referred"], out_space=out_space, out_dtype=dtype,
                        meter_ev=hd["meter_ev"], meta=_meta_from_header(hd), region=region, low_cache=cache)
    return _out(out)


# ------------------------------------------------------------------ tools

def pick(pid: str, nx: float, ny: float, settings_js: str, crop_mode: bool) -> str:
    """Eyedropper: white balance that makes the clicked patch neutral (as the desktop app)."""
    ph = PHOTOS[pid]
    s = _settings(settings_js)
    lin = ph["preview"]
    H, W = lin.shape[:2]
    a, b, c, d = engine.crop_box(s, W, H) if (s.crop and not crop_mode) else (0, 0, W, H)
    x = int(a + nx * (c - a))
    y = int(b + ny * (d - b))
    r = max(2, W // 300)
    patch = lin[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1].reshape(-1, 3)
    rgb = engine.prepare_scene(np.median(patch, axis=0)[None, :], ph["scene"])[0]
    meta = ph["meta"] if ph["scene"] else None
    g_cur = engine.wb_gains_for(s, meta)
    g_new = g_cur / np.maximum(rgb * g_cur, 1e-6)
    T, tint = color.wb_from_gains(g_new)
    return json.dumps([round(T / 10) * 10.0, round(float(tint), 1)])


def encode(data, h: int, w: int, bits: int, fmt: str, space_name: str, quality: int, pid: str):
    """Write the finished pixels as JPEG / TIFF / PNG with an ICC profile (and the photo's EXIF)."""
    dtype = np.uint16 if bits == 16 else np.uint8
    buf = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    arr = np.frombuffer(buf, dtype=dtype).reshape(h, w, 3)
    space = imageio.OUTPUT_SPACES[space_name]
    exif = None
    ph = PHOTOS.get(pid)
    if ph is not None and ph.get("src") is not None and fmt == "jpg":
        exif = ph["src"].exif
    path = f"/tmp/out.{fmt}"
    imageio.save(path, arr, space=space, fmt=fmt, quality=int(quality), exif=exif)
    with open(path, "rb") as f:
        out = f.read()
    os.remove(path)
    return out


def cube(settings_js: str) -> str:
    path = "/tmp/look.cube"
    engine.write_cube(path, _settings(settings_js))
    with open(path, encoding="ascii") as f:
        txt = f.read()
    os.remove(path)
    return txt
