"""Optical / darkroom effects: halation, bloom, light leaks, vignette, grain, borders."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from . import color

LOWRES = 1024  # long edge of the working map for large-radius effects


# ------------------------------------------------------------------ helpers

def resize_float(img: np.ndarray, size_wh, resample=Image.BILINEAR) -> np.ndarray:
    chans = [np.asarray(Image.fromarray(np.ascontiguousarray(img[..., c], dtype=np.float32)).resize(size_wh, resample))
             for c in range(img.shape[2])]
    return np.stack(chans, axis=-1).astype(np.float32)


def blur(img: np.ndarray, sigma: float, truncate: float = 3.0) -> np.ndarray:
    """Gaussian blur. Wide blurs are done on a smaller copy and scaled back up: the result is
    smooth, so this is visually identical and many times faster."""
    if sigma <= 0.05:
        return img
    f = int(sigma // 4)
    if sigma >= 12 and min(img.shape[:2]) // f >= 8:
        h, w = img.shape[:2]
        hh, ww = -(-h // f), -(-w // f)
        pad = ((0, hh * f - h), (0, ww * f - w)) + ((0, 0),) * (img.ndim - 2)
        src = np.pad(img, pad, mode="symmetric") if (hh * f != h or ww * f != w) else img
        small = src.reshape(hh, f, ww, f, *img.shape[2:]).mean(axis=(1, 3))
        ss = sigma / f
        s2 = (ss, ss, 0) if img.ndim == 3 else (ss, ss)
        small = ndimage.gaussian_filter(small.astype(np.float32), s2, mode="reflect", truncate=truncate)
        # sample positions of the full grid in the small grid (block centres)
        zy = (np.arange(h, dtype=np.float32) + 0.5) / f - 0.5
        zx = (np.arange(w, dtype=np.float32) + 0.5) / f - 0.5
        zy = np.clip(zy, 0, hh - 1)
        zx = np.clip(zx, 0, ww - 1)
        y0 = np.floor(zy).astype(np.int32); x0 = np.floor(zx).astype(np.int32)
        y1 = np.minimum(y0 + 1, hh - 1); x1 = np.minimum(x0 + 1, ww - 1)
        ty = (zy - y0)[:, None]; tx = (zx - x0)[None, :]
        if img.ndim == 3:
            ty = ty[..., None]; tx = tx[..., None]
        a = small[y0][:, x0] * (1 - tx) + small[y0][:, x1] * tx
        b = small[y1][:, x0] * (1 - tx) + small[y1][:, x1] * tx
        return (a * (1 - ty) + b * ty).astype(np.float32)
    s = (sigma, sigma, 0) if img.ndim == 3 else (sigma, sigma)
    return ndimage.gaussian_filter(img, s, mode="reflect", truncate=truncate)


def smooth_noise(h, w, cells, rng) -> np.ndarray:
    """Low-frequency noise in [0, 1], shape (h, w)."""
    gh, gw = max(2, int(cells * h / max(h, w)) + 2), max(2, int(cells * w / max(h, w)) + 2)
    g = rng.random((gh, gw)).astype(np.float32)
    return np.asarray(Image.fromarray(g).resize((w, h), Image.BICUBIC)).clip(0, 1)


class Upsampler:
    """Bilinear upsampling of a low-res map to H x W, computed lazily for just the rows and
    columns asked for (so a small region doesn't pay for the whole frame)."""

    def __init__(self, low: np.ndarray, H: int, W: int):
        self.low = low
        self.H, self.W = H, W
        self.h, self.w = low.shape[:2]

    def rows(self, y0: int, y1: int, x0: int = 0, x1: int | None = None) -> np.ndarray:
        x1 = self.W if x1 is None else x1
        h, w = self.h, self.w
        xs = np.clip((np.arange(x0, x1, dtype=np.float32) + 0.5) * w / self.W - 0.5, 0, w - 1)
        i0 = np.floor(xs).astype(np.int32)
        i1 = np.minimum(i0 + 1, w - 1)
        tx = (xs - i0)[None, :, None]
        ys = np.clip((np.arange(y0, y1, dtype=np.float32) + 0.5) * h / self.H - 0.5, 0, h - 1)
        j0 = np.floor(ys).astype(np.int32)
        j1 = np.minimum(j0 + 1, h - 1)
        ty = (ys - j0)[:, None, None]
        r0 = self.low[j0] * (1 - ty) + self.low[j1] * ty          # (rows, w, C)
        return r0[:, i0] * (1 - tx) + r0[:, i1] * tx


# ------------------------------------------------------------------ low-res light maps

def light_maps(x_low: np.ndarray, long_edge_full: int, *, halation: float, halation_radius: float,
               halation_colour, bloom: float, leak: str, leak_amount: float, leak_seed: int):
    """Returns (additive map, bloom keep factor). Inputs are scene-linear, exposure already applied.

    halation/bloom/leak_amount are 0..100 slider values.
    """
    h, w = x_low.shape[:2]
    scale = max(h, w)  # low-res long edge
    add = np.zeros_like(x_low)

    if halation > 0:
        lum = color.luminance(x_low)
        thr = 0.18 * 2 ** 1.8
        hl = np.maximum(lum - thr, 0.0)
        hl = hl / (1.0 + hl * 0.08)  # very bright sources don't explode
        hl = hl[..., None]
        r = halation_radius
        tight = blur(hl, 0.0028 * scale * r)
        wide = blur(hl, 0.011 * scale * r)
        amt = (halation / 100.0) * 0.55
        hal = (0.7 * tight + 0.45 * wide) * amt
        add += hal * np.asarray(halation_colour, np.float32)[None, None, :]

    keep = 1.0
    if bloom > 0:
        b = (bloom / 100.0) * 0.28
        add += blur(x_low, 0.006 * scale) * b
        keep = 1.0 - b

    if leak != "none" and leak_amount > 0:
        add += leak_map(leak, h, w, leak_seed, leak_amount / 100.0)

    return add, keep


LEAK_COLOURS = [
    (1.0, 0.36, 0.08),   # orange
    (1.0, 0.20, 0.05),   # red
    (1.0, 0.55, 0.16),   # amber
    (1.0, 0.24, 0.30),   # magenta-red
]


def leak_map(style: str, h: int, w: int, seed: int, amount: float) -> np.ndarray:
    rng = np.random.default_rng(1000 + seed)
    col = np.asarray(LEAK_COLOURS[rng.integers(len(LEAK_COLOURS))], np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    m = float(min(h, w))
    noise = smooth_noise(h, w, 5, rng)
    if style == "edge_fog":
        side = rng.integers(4)
        d = [yy, xx, (w - 1 - xx), (h - 1 - yy)][side] / m
        f = np.exp(-d / (0.10 + 0.08 * noise)) * (0.6 + 0.8 * noise)
        k = 2.2
    elif style == "corner_glow":
        cx, cy = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)][rng.integers(4)]
        r = np.hypot(xx - cx, yy - cy) / np.hypot(h, w)
        f = np.exp(-(r / (0.28 + 0.1 * noise)) ** 1.6)
        k = 3.0
    elif style == "frame_burn":
        side = rng.integers(2)
        d = (xx if side == 0 else (w - 1 - xx)) / m
        d = d + (noise - 0.5) * 0.06
        f = np.exp(-np.maximum(d, 0) / 0.05) * 8 + np.exp(-np.maximum(d, 0) / 0.25) * 1.2
        k = 2.5
    elif style == "full_veil":
        g = (xx / w) if rng.random() < 0.5 else (yy / h)
        if rng.random() < 0.5:
            g = 1 - g
        f = 0.35 + 0.65 * g * (0.6 + 0.8 * noise)
        k = 0.9
    elif style == "streak":
        x0 = rng.uniform(0.15, 0.85) * w
        width = (0.03 + 0.04 * rng.random()) * w
        f = np.exp(-((xx - x0) / width) ** 2) * (0.5 + 0.8 * noise) + \
            np.exp(-((xx - x0) / (width * 4)) ** 2) * 0.25
        k = 2.5
    else:
        return np.zeros((h, w, 3), np.float32)
    f = f.astype(np.float32) * amount * k * 0.18
    return f[..., None] * col[None, None, :]


def vignette_rows(y0, y1, H, W, amount: float, x0: int = 0, x1: int | None = None) -> np.ndarray | None:
    """Multiplicative falloff for rows [y0, y1), columns [x0, x1). amount -100..100."""
    if abs(amount) < 0.5:
        return None
    a = amount / 100.0
    x1 = W if x1 is None else x1
    v = ((np.arange(y0, y1, dtype=np.float32) + 0.5) / H * 2 - 1)[:, None]
    u = ((np.arange(x0, x1, dtype=np.float32) + 0.5) / W * 2 - 1)[None, :]
    r2 = (u * u + v * v) / 2.0
    return np.exp2(-a * 1.7 * r2 ** 1.25).astype(np.float32)[..., None]


# ------------------------------------------------------------------ grain

@lru_cache(maxsize=64)
def _blur_norm(sigma: float) -> float:
    rng = np.random.default_rng(0)
    n = rng.standard_normal((256, 256)).astype(np.float32)
    b = ndimage.gaussian_filter(n, sigma, mode="wrap", truncate=3.0)
    return float(1.0 / b.std())


def grain_field(rows: int, cols: int, sigma_px: float, colour: float, rng,
                rough: float = 0.5) -> tuple[np.ndarray, float]:
    """Unit-variance film grain for a block, plus a visibility factor (<1 when grain is sub-pixel)."""
    vis = 1.0
    s = sigma_px
    if s < 0.55:
        vis = max(s / 0.55, 0.12)
        s = 0.55
    s = round(s, 2)
    m = int(np.ceil(3 * s * 2.5)) + 1

    def octave(sig, n_ch):
        n = rng.standard_normal((rows + 2 * m, cols + 2 * m, n_ch)).astype(np.float32)
        n = ndimage.gaussian_filter(n, (sig, sig, 0), mode="reflect", truncate=3.0)
        return n[m:m + rows, m:m + cols] * _blur_norm(sig)

    mono = octave(s, 1)
    coarse = octave(round(s * 2.4, 2), 1)
    # roughness: more energy in the coarse clumps, and a skewed (clumpy) distribution
    wc = 0.2 + 0.8 * rough
    base = (mono + coarse * wc) / np.sqrt(1 + wc * wc)
    if rough > 0:
        base = base + rough * 0.25 * (base * base - 1.0)
    if colour > 0:
        ch = octave(s, 3)
        g = base * np.sqrt(1 - colour) + ch * np.sqrt(colour)
    else:
        g = np.repeat(base, 3, axis=-1)
    return g.astype(np.float32), vis


# ------------------------------------------------------------------ borders

def _font(size: int):
    for name in ("DejaVuSansMono-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf",
                 "Menlo.ttc", "Courier New Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _to_float_rgb(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


def add_border(img: np.ndarray, style: str, label: str = "", iso: int = 400, seed: int = 0) -> np.ndarray:
    """img: encoded float (H, W, 3) in [0, 1]. Returns a new, larger array."""
    if style == "none":
        return img
    H, W = img.shape[:2]
    rng = np.random.default_rng(seed + 77)

    if style == "white":
        m = int(round(0.05 * min(H, W)))
        out = np.full((H + 2 * m, W + 2 * m, 3), 0.965, np.float32)
        out[m:m + H, m:m + W] = img
        return out

    if style == "scan":
        m = int(round(0.04 * min(H, W)))
        out = np.full((H + 2 * m, W + 2 * m, 3), 0.018, np.float32)
        # ragged, slightly soft inner edge like a carrier mask
        d_row = np.minimum(np.arange(H), np.arange(H)[::-1]).astype(np.float32)[:, None]
        d_col = np.minimum(np.arange(W), np.arange(W)[::-1]).astype(np.float32)[None, :]
        d = np.minimum(d_row, d_col)
        band = int(m * 0.6) + 2
        mask = np.ones((H, W), np.float32)
        near = d < band
        noise = smooth_noise(H // 8 + 2, W // 8 + 2, 90, rng)
        noise = np.asarray(Image.fromarray(noise).resize((W, H), Image.BILINEAR))
        off = noise * (0.14 * m) + 0.04 * m
        feather = max(1.0, 0.04 * m)
        mask[near] = np.clip((d[near] - off[near]) / feather, 0, 1)
        out[m:m + H, m:m + W] = img * mask[..., None] + 0.018 * (1 - mask[..., None])
        return out

    if style == "strip35":
        portrait = H > W
        im = np.swapaxes(img, 0, 1) if portrait else img
        h, w = im.shape[:2]
        mm = h / 24.0
        reb = int(round(5.6 * mm))
        gap = int(round(1.8 * mm))
        cw, ch = w + 2 * gap, h + 2 * reb
        canvas = Image.new("RGB", (cw, ch), (8, 7, 7))
        dr = ImageDraw.Draw(canvas)
        pitch = 4.75 * mm
        hw, hh = 1.98 * mm, 2.8 * mm
        rad = max(1, int(0.5 * mm))
        n = int(cw / pitch) + 2
        start = (cw - (n - 1) * pitch) / 2
        for y_c in (reb * 0.42, ch - reb * 0.42):
            for i in range(n):
                xc = start + i * pitch
                dr.rounded_rectangle([xc - hw / 2, y_c - hh / 2, xc + hw / 2, y_c + hh / 2],
                                     radius=rad, fill=(236, 232, 224))
        amber = (238, 150, 44)
        font = _font(max(8, int(1.3 * mm)))
        frame = int(rng.integers(3, 33))
        top_y = int(reb * 0.86 - 1.0 * mm)
        dr.text((gap + 4 * mm, top_y), f"{label.upper()}", fill=amber, font=font, anchor="lm")
        dr.text((gap + w * 0.82, top_y), f"{frame}   {frame}A  >", fill=amber, font=font, anchor="lm")
        bot_y = int(ch - reb * 0.86 + 1.0 * mm)
        dr.text((gap + w * 0.3, bot_y), f"{iso}  {frame + 1}  > {frame}A", fill=amber, font=font, anchor="lm")
        out = _to_float_rgb(canvas)
        out[reb:reb + h, gap:gap + w] = im
        return np.swapaxes(out, 0, 1) if portrait else out

    if style == "instant":
        side = int(round(0.06 * W))
        top = side
        bottom = int(round(0.24 * W))
        out = np.full((H + top + bottom, W + 2 * side, 3), 1.0, np.float32)
        out[...] = np.array([0.955, 0.95, 0.93], np.float32)
        # soft dye-spread at the image edge, like an instant print
        img2 = img.copy()
        e = max(2, int(0.006 * min(H, W)))
        ramp = np.linspace(0.75, 1.0, e, dtype=np.float32)
        img2[:e] = img2[:e] * ramp[:, None, None] + (1 - ramp[:, None, None]) * 0.2
        img2[-e:] = img2[-e:] * ramp[::-1, None, None] + (1 - ramp[::-1, None, None]) * 0.2
        img2[:, :e] = img2[:, :e] * ramp[None, :, None] + (1 - ramp[None, :, None]) * 0.2
        img2[:, -e:] = img2[:, -e:] * ramp[None, ::-1, None] + (1 - ramp[None, ::-1, None]) * 0.2
        out[top:top + H, side:side + W] = img2
        return out

    if style == "medium":
        m = int(round(0.065 * min(H, W)))
        mt = int(m * 1.25)
        canvas = Image.new("RGB", (W + 2 * m, H + mt + m), (10, 9, 9))
        dr = ImageDraw.Draw(canvas)
        amber = (238, 150, 44)
        font = _font(max(8, int(0.32 * m)))
        frame = int(rng.integers(1, 13))
        dr.text((m + 0.06 * W, mt * 0.45), f"{label.upper()}", fill=amber, font=font, anchor="lm")
        dr.text((m + 0.62 * W, mt * 0.45), f"{frame}   {label.split()[0].upper()}   {frame + 1}",
                fill=amber, font=font, anchor="lm")
        # small V-notches on the left edge, like 120 film
        for k in range(3):
            yc = mt + H * (0.25 + 0.04 * k)
            dr.polygon([(m * 0.25, yc - m * 0.12), (m * 0.55, yc), (m * 0.25, yc + m * 0.12)], fill=(30, 28, 28))
        out = _to_float_rgb(canvas)
        out[mt:mt + H, m:m + W] = img
        return out

    return img


def chroma_denoise(rows: np.ndarray, amount: float) -> np.ndarray:
    """Soften digital colour noise (keeps luminance detail). rows: (h, w, 3) linear. amount 0..100."""
    if amount <= 0:
        return rows
    L = color.luminance(rows)[..., None]
    safe = np.maximum(L, 1e-5)
    ratio = rows / safe
    sig = 0.6 + 3.0 * amount / 100.0
    ratio = ndimage.gaussian_filter(ratio, (sig, sig, 0), mode="reflect", truncate=2.5)
    Ls = ndimage.gaussian_filter(L, (0.5 + amount / 100.0, 0.5 + amount / 100.0, 0), mode="reflect", truncate=2.5)
    k = amount / 100.0 * 0.5
    L = L * (1 - k) + Ls * k
    return (ratio * L).astype(np.float32)


# ------------------------------------------------------------------ dye-cloud grain

LAYER_SIZE = (0.85, 1.0, 1.35)   # red-, green-, blue-sensitive layers: the fast top (blue) layer is coarsest


def _crisp(f):
    """Gaussian-filtered noise has soft, digital-looking blobs; dye clouds have firmer edges."""
    f = np.tanh(1.55 * f)
    return f / (f.std() + 1e-6)


def _octave(h, w, sig, m, rng):
    n = rng.standard_normal((h + 2 * m, w + 2 * m)).astype(np.float32)
    n = ndimage.gaussian_filter(n, sig, mode="reflect", truncate=2.5)
    return n[m:m + h, m:m + w] * _blur_norm(round(sig, 2))


def dye_grain(h, w, sigma_px, colour, rough, rng):
    """Per-layer grain fields (h, w, 3) at two scales, plus visibility factors for sub-pixel grain.

    fine   = individual dye clouds
    coarse = clumps and the low-frequency mottle that survives downsizing
    """
    def eff(sig):
        if sig < 0.5:
            return 0.5, max(sig / 0.5, 0.1)
        return sig, 1.0

    fine = np.empty((h, w, 3), np.float32)
    coarse = np.empty((h, w, 3), np.float32)
    s_f, v_f = eff(sigma_px * 0.55)
    s_c, v_c = eff(sigma_px * 1.6)
    s_m, _ = eff(sigma_px * 5.0)
    m = int(np.ceil(2.5 * max(s_c, s_m) * 1.35)) + 1
    shared_f = _octave(h, w, s_f, m, rng)
    shared_c = _octave(h, w, s_c, m, rng)
    mottle = _octave(h, w, s_m, m, rng)
    a = np.sqrt(np.clip(colour, 0, 1))
    b = np.sqrt(1 - np.clip(colour, 0, 1))
    for k, size in enumerate(LAYER_SIZE):
        if colour > 0:
            fk = _octave(h, w, max(0.5, s_f * size), m, rng)
            ck = _octave(h, w, max(0.5, s_c * size), m, rng)
            fine[..., k] = _crisp(shared_f * b + fk * a)
            coarse[..., k] = _crisp(shared_c * b + ck * a) * 0.9 + mottle * (0.15 + 0.25 * rough)
        else:
            fine[..., k] = _crisp(shared_f)
            coarse[..., k] = _crisp(shared_c) * 0.9 + mottle * (0.15 + 0.25 * rough)
    if colour <= 0:
        fine[..., 1:] = fine[..., :1]
        coarse[..., 1:] = coarse[..., :1]
    return fine, coarse, (v_f, v_c)


# ------------------------------------------------------------------ grain fields for the coverage model

def grain_fields(h, w, sigma_px, colour, rng):
    """Random fields for the dye-cloud coverage model.

    What makes scanned grain read as film rather than sensor noise:
      * the fine structure is mostly shared by the three dye layers (it reads as brightness
        grain), and colour only separates at the clump scale, as soft colour mottle — never
        per-pixel RGB speckle
      * clouds come in a spread of sizes (two populations, not one blur radius)
      * grain clusters: it is denser and more contrasty in some patches than others

    Returns (fine, coarse, amp, vis): fine / coarse (h, w, 3) unit-variance fields, amp (h, w, 1)
    local grain-contrast multiplier (mean 1), and vis < 1 when clouds are smaller than a pixel."""
    vis = min(1.0, sigma_px / 0.5) if sigma_px > 0 else 0.0
    s_f = max(0.5, sigma_px * 0.5)
    s_f2 = max(0.5, sigma_px * 0.95)
    s_c = max(0.5, sigma_px * 1.6)
    s_a = max(1.0, sigma_px * 11.0)
    m = int(np.ceil(2.5 * s_a)) + 1
    col_f = float(np.clip(colour * 0.22, 0, 1))
    col_c = float(np.clip(colour * 0.9, 0, 1))
    fine = np.empty((h, w, 3), np.float32)
    coarse = np.empty((h, w, 3), np.float32)

    def two_pop(sf, sf2):
        return (0.8 * _octave(h, w, round(sf, 2), m, rng) + 0.6 * _octave(h, w, round(sf2, 2), m, rng)) / 1.0

    shared_f = two_pop(s_f, s_f2)
    shared_c = _octave(h, w, round(s_c, 2), m, rng)
    for k, size in enumerate(LAYER_SIZE):
        if colour > 0:
            fine[..., k] = (shared_f * np.sqrt(1 - col_f)
                            + two_pop(max(0.5, s_f * size), max(0.5, s_f2 * size)) * np.sqrt(col_f))
            coarse[..., k] = (shared_c * np.sqrt(1 - col_c)
                              + _octave(h, w, round(max(0.5, s_c * size), 2), m, rng) * np.sqrt(col_c))
        else:
            fine[..., k] = shared_f
            coarse[..., k] = shared_c
    z = _octave(h, w, round(s_a, 2), m, rng)
    amp = np.exp(0.16 * z - 0.5 * 0.16 ** 2).astype(np.float32)[..., None]
    return fine, coarse, amp, vis
