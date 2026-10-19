"""Loading and saving images: RAW (via LibRaw/rawpy), TIFF (8/16/32-bit), JPEG, PNG.

Everything is decoded into linear Rec.2020 float32 (the engine's working space).
"""
from __future__ import annotations

import io
import os
import struct
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageCms, ImageOps

from . import color

try:        # iPhone photos (HEIC / HEIF)
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_OK = True
except Exception:
    HEIF_OK = False

RAW_EXTS = {
    ".3fr", ".ari", ".arw", ".bay", ".cr2", ".cr3", ".crw", ".dcr", ".dng", ".erf", ".fff",
    ".iiq", ".k25", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".pef", ".raf",
    ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw", ".x3f",
}
TIFF_EXTS = {".tif", ".tiff"}
OTHER_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".hif"}
SUPPORTED_EXTS = RAW_EXTS | TIFF_EXTS | OTHER_EXTS


@dataclass
class Source:
    path: str
    kind: str                     # "raw" | "tiff" | "image"
    linear: np.ndarray            # (H, W, 3) float32, linear Rec.2020
    space: color.RGBSpace         # colour space the file was encoded in
    icc: bytes | None = None      # embedded ICC profile, kept for round trips
    bit_depth: int = 8
    scene_referred: bool = False  # True for RAW (no tone curve applied yet)
    full_size: tuple = (0, 0)     # (W, H) of the full-resolution image
    exif: bytes | None = None
    meta: dict = field(default_factory=dict)

    @property
    def height(self):
        return self.linear.shape[0]

    @property
    def width(self):
        return self.linear.shape[1]


def is_raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RAW_EXTS


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


def icc_description(icc: bytes | None) -> str | None:
    if not icc:
        return None
    try:
        prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return prof.profile.profile_description
    except Exception:
        return None


# ------------------------------------------------------------------ resizing

def resize_linear(img: np.ndarray, long_edge: int) -> np.ndarray:
    """Area-average downscale of a float image so its long edge <= long_edge."""
    h, w = img.shape[:2]
    if max(h, w) <= long_edge:
        return img
    s = long_edge / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    chans = [np.asarray(Image.fromarray(np.ascontiguousarray(img[..., c])).resize((nw, nh), Image.BOX))
             for c in range(img.shape[2])]
    return np.stack(chans, axis=-1).astype(np.float32)


# ------------------------------------------------------------------ loading

def _load_raw(path: str, half_size: bool):
    """Decode a RAW file daylight-balanced (like daylight film) and estimate the camera's as-shot WB."""
    import rawpy

    with rawpy.imread(path) as raw:
        full = (raw.sizes.width, raw.sizes.height)
        if raw.sizes.flip in (5, 6):
            full = (full[1], full[0])
        day = [float(v) for v in raw.daylight_whitebalance]
        cam = [float(v) for v in raw.camera_whitebalance]
        usable_day = len(day) >= 3 and all(v > 0 for v in day[:3])
        kwargs = dict(
            no_auto_bright=True, output_bps=16, gamma=(1, 1),
            output_color=rawpy.ColorSpace.ProPhoto, half_size=half_size,
            highlight_mode=rawpy.HighlightMode.Blend,
        )
        if usable_day:
            kwargs["user_wb"] = day[:3] + [day[3] if len(day) > 3 and day[3] > 0 else day[1]]
        else:
            kwargs["use_camera_wb"] = True
        try:
            rgb = raw.postprocess(**kwargs)
        except Exception:
            kwargs.pop("highlight_mode")
            rgb = raw.postprocess(**kwargs)
        as_shot = None
        if usable_day and len(cam) >= 3 and all(v > 0 for v in cam[:3]):
            # camera-space ratio of the as-shot multipliers to daylight, carried into the working space
            r = np.array([cam[0] / day[0], cam[1] / day[1], cam[2] / day[2]])
            r = r / r[1]
            try:
                xyz_to_cam = np.asarray(raw.rgb_xyz_matrix, np.float64)[:3]
                cam_to_xyz = np.linalg.inv(xyz_to_cam)
                to_w = np.linalg.inv(color.REC2020_LINEAR.to_xyz) @ cam_to_xyz
                white_cam = 1.0 / r                    # the light the camera thought was white
                white_w = to_w @ white_cam
                base_w = to_w @ np.ones(3)
                g = base_w / white_w
                if np.all(np.isfinite(g)) and np.all(g > 0):
                    as_shot = g / float(g @ color.LUMA_2020)
            except Exception:
                as_shot = None
    lin = rgb.astype(np.float32) / 65535.0
    lin = color.apply_matrix(lin, color.conversion_matrix(color.PROPHOTO_LINEAR, color.WORKING))
    meta = {}
    if as_shot is not None:
        meta["as_shot_gains"] = as_shot.astype(np.float32)
        meta["as_shot_wb"] = color.wb_from_gains(as_shot)
    elif not usable_day:
        meta["as_shot_wb"] = (color.WB_REFERENCE_K, 0.0)
    return lin, full, meta


def load(path: str, preview_long_edge: int | None = None) -> Source:
    """Load any supported file. With preview_long_edge, returns a fast, downscaled decode."""
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXTS:
        lin, full, meta = _load_raw(path, half_size=preview_long_edge is not None)
        if preview_long_edge:
            lin = resize_linear(lin, preview_long_edge)
        if not meta.get("as_shot_gains") is not None and "as_shot_wb" not in meta:
            meta["as_shot_wb"] = (color.WB_REFERENCE_K, 0.0)
        return Source(path, "raw", lin, color.PROPHOTO, None, 16, True, full, meta=meta)

    if ext in TIFF_EXTS:
        return _load_tiff(path, preview_long_edge)

    return _load_pillow(path, preview_long_edge)


def _to_float(arr: np.ndarray) -> tuple[np.ndarray, int]:
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 65535.0, 16
    if arr.dtype == np.uint8:
        return arr.astype(np.float32) / 255.0, 8
    if arr.dtype in (np.float32, np.float64, np.float16):
        return arr.astype(np.float32), 32
    if arr.dtype == np.uint32:
        return arr.astype(np.float32) / 4294967295.0, 32
    raise ValueError(f"Unsupported sample type {arr.dtype}")


def _rgbify(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    if arr.shape[-1] > 3:
        arr = arr[..., :3]
    return arr


def _load_tiff(path: str, preview_long_edge: int | None) -> Source:
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        icc_tag = page.tags.get(34675)
        icc = bytes(icc_tag.value) if icc_tag is not None else None
        arr = page.asarray()
        if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[-1] not in (3, 4):
            arr = np.moveaxis(arr, 0, -1)  # planar -> interleaved
    f, depth = _to_float(arr)
    f = _rgbify(f)
    space = color.space_from_icc_description(icc_description(icc), default=color.SRGB)
    full = (f.shape[1], f.shape[0])
    if preview_long_edge:
        # downscale in encoded space is fine for preview speed but do it linear for accuracy
        lin = space.decode(f)
        lin = resize_linear(lin, preview_long_edge)
    else:
        lin = space.decode(f)
    lin = color.apply_matrix(lin, color.conversion_matrix(
        color.RGBSpace(space.name, space.primaries, space.white, color.linear, color.linear), color.WORKING))
    return Source(path, "tiff", lin, space, icc, depth, False, full)


def _load_pillow(path: str, preview_long_edge: int | None) -> Source:
    try:
        im = Image.open(path)
    except Exception as e:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".heic", ".heif", ".hif") and not HEIF_OK:
            raise ValueError("This copy of GrainHause can't read HEIC photos. Please update to the latest version.") from e
        raise ValueError(f"Couldn't open {os.path.basename(path)} — the file may be damaged or in a format "
                         "GrainHause doesn't read.") from e
    icc = im.info.get("icc_profile")
    exif = im.info.get("exif")
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "I;16", "I;16B", "I"):
        im = im.convert("RGB")
    full = im.size
    if preview_long_edge and max(im.size) > preview_long_edge * 1.5:
        im.draft("RGB", (preview_long_edge, preview_long_edge))  # fast JPEG decode
    arr = np.asarray(im)
    if arr.dtype == np.int32:
        arr = arr.astype(np.uint16)
    f, depth = _to_float(arr)
    f = _rgbify(f)
    space = color.space_from_icc_description(icc_description(icc), default=color.SRGB)
    lin = space.decode(f)
    if preview_long_edge:
        lin = resize_linear(lin, preview_long_edge)
    lin = color.apply_matrix(lin, color.conversion_matrix(
        color.RGBSpace(space.name, space.primaries, space.white, color.linear, color.linear), color.WORKING))
    return Source(path, "image", lin, space, icc, depth, False, full, exif)


# ------------------------------------------------------------------ ICC writer

def _s15(v: float) -> bytes:
    return struct.pack(">i", int(round(v * 65536)))


def _xyz_tag(xyz) -> bytes:
    return b"XYZ " + b"\0" * 4 + b"".join(_s15(v) for v in xyz)


def _curv_tag(fn) -> bytes:
    n = 1024
    x = np.linspace(0, 1, n, dtype=np.float32)
    y = np.clip(fn(x), 0, 1)
    vals = np.round(y * 65535).astype(">u2").tobytes()
    return b"curv" + b"\0" * 4 + struct.pack(">I", n) + vals


def _desc_tag(text: str) -> bytes:
    a = text.encode("ascii", "replace") + b"\0"
    body = b"desc" + b"\0" * 4 + struct.pack(">I", len(a)) + a
    body += struct.pack(">II", 0, 0)            # unicode lang + count
    body += struct.pack(">HB", 0, 0) + b"\0" * 67  # scriptcode
    return body


def _text_tag(text: str) -> bytes:
    return b"text" + b"\0" * 4 + text.encode("ascii", "replace") + b"\0"


def make_icc(space: color.RGBSpace) -> bytes:
    """Build a small ICC v2 matrix/TRC display profile for an RGB space."""
    if space.name == "sRGB":
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    # colorants adapted to D50 (Bradford)
    m = color.bradford(space.white, color.D50) @ space.to_xyz
    trc = _curv_tag(space.decode)
    tags = [
        (b"desc", _desc_tag(space.name)),
        (b"cprt", _text_tag("No copyright, use freely")),
        (b"wtpt", _xyz_tag((0.9642, 1.0, 0.8249))),
        (b"rXYZ", _xyz_tag(m[:, 0])),
        (b"gXYZ", _xyz_tag(m[:, 1])),
        (b"bXYZ", _xyz_tag(m[:, 2])),
        (b"rTRC", trc), (b"gTRC", trc), (b"bTRC", trc),
    ]
    table_size = 4 + 12 * len(tags)
    offset = 128 + table_size
    data = b""
    table = struct.pack(">I", len(tags))
    shared = {}
    for sig, body in tags:
        key = body
        if key in shared:
            off, size = shared[key]
        else:
            while (offset + len(data)) % 4:
                data += b"\0"
            off, size = offset + len(data), len(body)
            shared[key] = (off, size)
            data += body
        table += sig + struct.pack(">II", off, size)
    while (offset + len(data)) % 4:
        data += b"\0"
    total = 128 + table_size + len(data)
    header = struct.pack(">I", total) + b"lcms" + struct.pack(">I", 0x02100000)
    header += b"mntrRGB XYZ "
    header += struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
    header += b"acsp" + b"APPL" + struct.pack(">I", 0) + b"\0" * 8 + b"\0" * 8 + struct.pack(">I", 0)
    header += _s15(0.9642) + _s15(1.0) + _s15(0.8249)
    header += b"grnh" + b"\0" * 16 + b"\0" * 28
    assert len(header) == 128, len(header)
    return header + table + data


OUTPUT_SPACES = {
    "sRGB": color.SRGB,
    "Display P3": color.DISPLAY_P3,
    "Adobe RGB": color.ADOBE_RGB,
    "ProPhoto RGB": color.PROPHOTO,
}


# ------------------------------------------------------------------ saving

def save(path: str, encoded: np.ndarray, *, space: color.RGBSpace, icc: bytes | None = None,
         fmt: str | None = None, quality: int = 95, exif: bytes | None = None):
    """encoded: (H, W, 3) uint8/uint16 or float in [0, 1], already in `space` encoding."""
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    if icc is None:
        icc = make_icc(space)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".grainhouse-tmp"
    if fmt in ("tif", "tiff"):
        import tifffile
        arr = encoded
        if arr.dtype != np.uint16:
            arr = np.round(np.clip(arr.astype(np.float32) if arr.dtype != np.uint8 else arr / 255.0, 0, 1) * 65535).astype(np.uint16)
        tifffile.imwrite(tmp, arr, photometric="rgb", compression="zlib",
                         extratags=[(34675, "B", len(icc), icc, True)], metadata=None)
    else:
        arr = encoded
        if arr.dtype == np.uint16:
            arr = (arr.astype(np.uint32) * 255 + 32767) // 65535
            arr = arr.astype(np.uint8)
        elif arr.dtype != np.uint8:
            arr = np.round(np.clip(arr, 0, 1) * 255).astype(np.uint8)
        im = Image.fromarray(arr, "RGB")
        kw = {"icc_profile": icc}
        if fmt in ("jpg", "jpeg"):
            kw.update(quality=quality, subsampling=0 if quality >= 90 else 2)
            if exif:
                kw["exif"] = exif
            im.save(tmp, "JPEG", **kw)
        elif fmt == "png":
            im.save(tmp, "PNG", **kw)
        else:
            raise ValueError(f"Unsupported output format: {fmt}")
    os.replace(tmp, path)  # atomic: Lightroom never sees a half-written file
