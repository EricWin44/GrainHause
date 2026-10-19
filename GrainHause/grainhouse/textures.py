"""Physical surface defects and textures: dust, hairs, scratches, paper and expired-film textures.

Defects are generated as vector shapes in normalised coordinates from a seed, then
rasterised at whatever resolution is being rendered, so the preview, the 1:1 loupe
and the full-size export all show the *same* specks in the same places.

Polarity follows the physics: dust on a negative blocks light, so after the
negative is inverted it prints/scans as a *white* speck; on a slide it stays dark.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

TEXTURES = ["none", "matte", "fiber", "expired", "emulsion"]
TEXTURE_LABELS = {"none": "None", "matte": "Matte print paper", "fiber": "Fibre (baryta) paper",
                  "expired": "Expired film", "emulsion": "Emulsion mottle"}


# ------------------------------------------------------------------ shape generation
#
# Modelled on real lab scans with dust correction off:
#   * specks: dust sitting on the negative, in focus — most are tiny (1-3 px at 24 MP), a few
#     are larger irregular clumps; partly transparent at their edges
#   * fibres / hairs: fine, smooth gentle arcs (not squiggles), tapered at the ends, thinner
#     than you'd expect, with opacity varying along their length
#   * scratches: very thin, long and almost perfectly straight, running along the film,
#     faint and broken — base scratches print white, emulsion scratches pick up a colour
# Everything is drawn supersampled and filtered down, so edges are as soft as an optical scan.


def _blob(rng, cx, cy, r, n=None, stretch=1.0, angle=0.0):
    """An irregular but rounded speck: random radii smoothed around the outline."""
    n = max(n or 0, 14)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False) + rng.uniform(0, 2 * np.pi)
    raw = rng.uniform(0.5, 1.3, n)
    rad = r * (np.roll(raw, 1) + 2 * raw + np.roll(raw, -1)) / 4
    xs, ys = rad * np.cos(ang) * stretch, rad * np.sin(ang)
    ca, sa = np.cos(angle), np.sin(angle)
    return list(zip(cx + xs * ca - ys * sa, cy + xs * sa + ys * ca))


def _arc(rng, x, y, length, steps=40):
    """A smooth fibre path: gently varying curvature, occasionally a soft hook at one end."""
    ang = rng.uniform(0, 2 * np.pi)
    k0 = rng.normal(0, 1.2) / length            # base curvature (radians per px)
    k1 = rng.normal(0, 2.5) / length            # slow change of curvature along the fibre
    hook = rng.random() < 0.2
    pts = [(x, y)]
    ds = length / steps
    for i in range(steps):
        t = i / steps
        k = k0 + k1 * np.sin(np.pi * t) + (rng.normal(0, 8) / length if hook and t > 0.85 else 0.0)
        ang += k * ds
        x += np.cos(ang) * ds
        y += np.sin(ang) * ds
        pts.append((x, y))
    return pts


def _smooth_profile(rng, n, base, var, lo=0.05):
    """A slowly-varying random profile of length n (opacity along a fibre or scratch)."""
    knots = rng.uniform(base - var, base + var, max(3, n // 12 + 2))
    return np.clip(np.interp(np.linspace(0, len(knots) - 1, n), np.arange(len(knots)), knots), lo, 1.0)


@lru_cache(maxsize=8)
def defect_layers(H: int, W: int, seed: int, dust: float, scratches: float, along_x: bool):
    """Returns (alpha, tint index) maps at H x W. Shapes are defined relative to the frame, so the
    preview, a zoomed tile and the export all show the same dust in the same places."""
    if dust <= 0 and scratches <= 0:
        return None
    L = max(H, W)
    ss = 3 if H * W <= 4_000_000 else (2 if H * W <= 30_000_000 else 1)   # supersampling
    SW, SH = W * ss, H * ss
    u = L / 6000.0 * ss                 # px per "24 MP pixel" on the supersampled canvas
    alpha = Image.new("L", (SW, SH), 0)
    tint = Image.new("L", (W, H), 0)
    da, dt = ImageDraw.Draw(alpha), ImageDraw.Draw(tint)
    soft = Image.new("L", (SW, SH), 0)
    ds = ImageDraw.Draw(soft)
    rng = np.random.default_rng(90210 + seed)

    if dust > 0:
        a = dust / 100.0
        # --- specks: count fixed per frame (not per pixel), power-law sizes
        for _ in range(int(25 + 520 * a ** 1.5)):
            x, y = rng.uniform(0, SW), rng.uniform(0, SH)
            big = rng.random() < 0.05
            r = (0.45 + rng.pareto(2.8) * 0.7) * (2.6 if big else 1.0) * u
            r = min(r, 9 * u)
            op = float(np.clip(rng.lognormal(-0.25, 0.35), 0.25, 1.0))
            poly = _blob(rng, x, y, r, stretch=rng.uniform(1.0, 2.2), angle=rng.uniform(0, np.pi))
            da.polygon(poly, fill=int(255 * op))
            if big and rng.random() < 0.6:       # clumps often have a few satellites
                for _ in range(int(rng.integers(1, 4))):
                    ox, oy = rng.normal(0, 3 * r, 2)
                    da.polygon(_blob(rng, x + ox, y + oy, r * rng.uniform(0.2, 0.5)), fill=int(255 * op * 0.9))
        # --- fibres and hairs
        for _ in range(int(0.5 + 9 * a ** 1.3)):
            x, y = rng.uniform(0, SW), rng.uniform(0, SH)
            length = float(np.clip(rng.lognormal(np.log(0.022), 0.6), 0.006, 0.12)) * max(SW, SH)
            pts = _arc(rng, x, y, length)
            n = len(pts) - 1
            width = rng.uniform(0.55, 1.25) * u
            prof = _smooth_profile(rng, n, rng.uniform(0.55, 0.85), 0.25)
            taper = np.sin(np.linspace(0.08, np.pi - 0.08, n)) ** 0.5
            for i in range(n):
                wpx = max(1, int(round(width * (0.35 + 0.65 * taper[i]))))
                da.line([pts[i], pts[i + 1]], fill=int(255 * prof[i] * (0.5 + 0.5 * taper[i])), width=wpx)
        # --- a few out-of-focus specks (dust on the scanner glass): soft, irregular, faint
        for _ in range(int(rng.integers(0, 3) + 3 * a)):
            x, y = rng.uniform(0, SW), rng.uniform(0, SH)
            r = rng.uniform(4, 11) * u
            ds.polygon(_blob(rng, x, y, r, n=11, stretch=rng.uniform(1.0, 1.6), angle=rng.uniform(0, np.pi)),
                       fill=int(255 * rng.uniform(0.06, 0.14)))

    if scratches > 0:
        a = scratches / 100.0
        span = SW if along_x else SH
        across = SH if along_x else SW
        for _ in range(int(0.5 + 7 * a ** 1.2)):
            c0 = rng.uniform(0.03, 0.97) * across
            start = rng.uniform(-0.3, 0.5) * span
            length = rng.uniform(0.3, 1.4) * span
            slope = np.tan(np.radians(rng.normal(0, 0.15)))          # almost perfectly straight
            n = 160
            t = np.linspace(0, 1, n + 1)
            d = start + t * length
            off = c0 + slope * (d - start) + np.cumsum(rng.normal(0, 0.08 * u, n + 1))  # tiny jitter
            pts = list(zip(d, off)) if along_x else list(zip(off, d))
            prof = _smooth_profile(rng, n, rng.uniform(0.25, 0.55), 0.3, lo=0.0)
            gaps = _smooth_profile(rng, n, 0.5, 0.6, lo=0.0) > 0.3       # broken in places
            width = max(1, int(round(rng.uniform(0.5, 1.0) * u)))
            emulsion = rng.random() < 0.25
            tint_idx = 1 + int(rng.integers(0, 3))
            for i in range(n):
                if not gaps[i] or prof[i] < 0.03:
                    continue
                da.line([pts[i], pts[i + 1]], fill=int(255 * prof[i]), width=width)
                if emulsion:
                    dt.line([(pts[i][0] / ss, pts[i][1] / ss), (pts[i + 1][0] / ss, pts[i + 1][1] / ss)],
                            fill=tint_idx, width=max(1, width // ss))
        # cinch marks: short, faint, parallel bunches
        for _ in range(int(3 * a)):
            cx, cy = rng.uniform(0, SW), rng.uniform(0, SH)
            ang = rng.normal(0, 0.02) + (0 if along_x else np.pi / 2)
            for k in range(int(rng.integers(3, 8))):
                o = k * rng.uniform(2.5, 6) * u
                ln = rng.uniform(0.01, 0.05) * max(SW, SH)
                px, py = -np.sin(ang) * o, np.cos(ang) * o
                da.line([(cx + px, cy + py), (cx + px + np.cos(ang) * ln, cy + py + np.sin(ang) * ln)],
                        fill=int(255 * rng.uniform(0.08, 0.25)), width=max(1, int(0.6 * u)))

    alpha = alpha.filter(ImageFilter.GaussianBlur(0.45 * ss))    # optical softness of the scan
    soft = soft.filter(ImageFilter.GaussianBlur(4.0 * u))
    if ss > 1:
        alpha = alpha.resize((W, H), Image.BOX)
        soft = soft.resize((W, H), Image.BOX)
    a_arr = np.maximum(np.asarray(alpha, np.float32), np.asarray(soft, np.float32)) / 255.0
    return a_arr, np.asarray(tint, dtype=np.uint8)


TINTS = np.array([[0.0, 0.0, 0.0], [0.62, 0.95, 0.78], [0.95, 0.62, 0.9], [0.6, 0.88, 0.98]], np.float32)


def apply_defects(y: np.ndarray, rows: slice, cols: slice, layers, positive_white: bool):
    """Composite defects onto display-linear rows y (h, w, 3)."""
    if layers is None:
        return y
    a, t = layers
    a = a[rows, cols][..., None]
    if not np.any(a):
        return y
    # dust blocks the scanner's light: from a negative that's the brightest the scan can go
    # (slightly warm, the scanner's lamp), from a slide it's near black
    col = np.array([0.96, 0.95, 0.92], np.float32) if positive_white else np.array([0.012, 0.011, 0.01], np.float32)
    ti = t[rows, cols]
    if np.any(ti):
        tinted = TINTS[ti] * (0.9 if positive_white else 0.15)
        col = np.where(ti[..., None] > 0, tinted, col)
    return y * (1 - a) + col * a


# ------------------------------------------------------------------ textures

@lru_cache(maxsize=8)
def _low_maps(h, w, seed, style):
    rng = np.random.default_rng(4242 + seed)
    def m(cells):
        g = rng.standard_normal((max(2, int(cells * h / max(h, w)) + 2), max(2, int(cells * w / max(h, w)) + 2)))
        g = ndimage.gaussian_filter(g.astype(np.float32), 0.8)
        im = np.asarray(Image.fromarray(g).resize((w, h), Image.BICUBIC))
        return (im - im.mean()) / (im.std() + 1e-6)
    return m(4), m(9), m(22)


def apply_texture(y: np.ndarray, style: str, amount: float, y0: int, x0: int, H: int, W: int, seed: int,
                  low_up=None, rng=None):  # noqa: E501
    """y: display-linear (h, w, 3) rows starting at (y0, x0) of an (H, W) frame."""
    if style == "none" or amount <= 0:
        return y
    a = amount / 100.0
    h, w = y.shape[:2]
    s = max(H, W) / 6000.0
    if style == "matte":
        n = rng.standard_normal((h + 8, w + 8)).astype(np.float32)
        n = ndimage.gaussian_filter(n, 1.1 * max(s, 0.5))[4:4 + h, 4:4 + w]
        n /= n.std() + 1e-6
        return y * (1 + a * 0.045 * n)[..., None] + a * 0.012
    if style == "fiber":
        n = rng.standard_normal((h + 24, w + 24)).astype(np.float32)
        fib = ndimage.gaussian_filter(n, (0.9 * max(s, 0.5), 7.0 * max(s, 0.5)))[12:12 + h, 12:12 + w]
        fib /= fib.std() + 1e-6
        tex = 0.06 * fib
        y = y * (1 + a * tex)[..., None]
        return y * np.array([1.0, 0.99, 0.965], np.float32) ** a + a * 0.008
    lows = low_up.rows(y0, y0 + h, x0, x0 + w) if low_up is not None else None
    if style == "expired":
        m1, m2, m3 = lows[..., 0], lows[..., 1], lows[..., 2]
        shift = (m1[..., None] * np.array([0.18, -0.04, -0.16], np.float32)
                 + m2[..., None] * np.array([-0.08, 0.07, 0.04], np.float32)) * a
        y = y * np.exp2(shift + np.array([0.06, 0.0, -0.1], np.float32) * a)
        fog = np.array([0.05, 0.04, 0.025], np.float32) * a * (1 + 0.3 * m3[..., None])
        return fog + y * (1 - 0.12 * a)
    if style == "emulsion":
        m2, m3 = lows[..., 1], lows[..., 2]
        return y * np.exp2(a * (0.09 * m3 + 0.05 * m2))[..., None]
    return y


def low_texture_maps(H, W, seed, style):
    """Low-res maps (h, w, 3) for the large-scale textures, to be upsampled per chunk."""
    if style not in ("expired", "emulsion"):
        return None
    h, w = max(8, int(256 * H / max(H, W))), max(8, int(256 * W / max(H, W)))
    a, b, c = _low_maps(h, w, seed, style)
    return np.stack([a, b, c], axis=-1).astype(np.float32)
