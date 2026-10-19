"""Physically based film grain (Boolean model).

Real grain isn't noise: it's millions of developed particles (silver grains in black & white,
dye clouds in colour film), each a little disc of random size dropped at a random place. Where
the film is dense there are more of them; they overlap, clump and leave gaps. That's what gives
grain its irregular, crisp, "organic" texture — something no blurred noise field reproduces.

We simulate exactly that, once, on a small tileable patch of film:
  * a Poisson process of discs with log-normal radii (a nested process, so a denser level
    contains every grain of the thinner levels — grains *appear* as density rises)
  * snapshots of the covered fraction at many density levels, rendered supersampled and
    softened by the optics (crisp for silver, diffuse for dye clouds)
  * a mip pyramid of each, so a downsized preview sees the correct, averaged-out grain

At render time every pixel looks up its film position in the patch (so the grain is locked to
the film, not the screen), picks the level matching its density, and reads how much of it is
covered by grains. Patches are built on first use (a couple of seconds) and cached on disk.
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

VERSION = 1
TILE = 512                 # patch size (tile px)
R_TILE = 2.0               # mean grain radius in tile px
SIGMA_R = 0.38             # log-normal spread of grain sizes
LEVELS = np.concatenate([[0.0], np.linspace(0.03, 0.985, 19)])   # target covered fractions
SS = 4                     # supersampling while drawing
KINDS = {"dye": 0.85, "silver": 0.35}    # optical softness (tile px) of each grain type
_lock = threading.Lock()
NEAREST = True
NEAREST_BIAS = 0.4


def _cache_path(kind):
    from .settings import config_dir
    return os.path.join(str(config_dir()), f"grain_{kind}_v{VERSION}.npz")


def _simulate(kind, seed=11):
    """Nested Boolean model -> (levels, coverage stack (K, TILE, TILE) float32)."""
    rng = np.random.default_rng(seed)
    big = TILE * SS
    mean_area = np.pi * (R_TILE * SS) ** 2 * np.exp(2 * SIGMA_R ** 2)
    lam_max = -np.log(1 - LEVELS[-1]) / mean_area                 # grains per supersampled px
    n = rng.poisson(lam_max * big * big)
    xs, ys = rng.uniform(0, big, n), rng.uniform(0, big, n)
    rs = R_TILE * SS * np.exp(rng.normal(-0.5 * SIGMA_R ** 2, SIGMA_R, n))
    birth = rng.uniform(0, 1, n)                                   # nesting: each grain's threshold
    order = np.argsort(birth)
    lam_frac = (-np.log(1 - LEVELS)) / (-np.log(1 - LEVELS[-1]))
    canvas = Image.new("L", (big, big), 0)
    dr = ImageDraw.Draw(canvas)
    out = np.zeros((len(LEVELS), TILE, TILE), np.float32)
    i = 0
    soft = KINDS[kind]
    for k, f in enumerate(lam_frac):
        while i < n and birth[order[i]] <= f:
            j = order[i]
            x, y, r = xs[j], ys[j], rs[j]
            for ox in (-big, 0, big):                                # wrap: the patch tiles seamlessly
                if -r <= x + ox <= big + r:
                    for oy in (-big, 0, big):
                        if -r <= y + oy <= big + r:
                            dr.ellipse([x + ox - r, y + oy - r, x + ox + r, y + oy + r], fill=255)
            i += 1
        a = np.asarray(canvas, np.float32).reshape(TILE, SS, TILE, SS).mean(axis=(1, 3)) / 255.0
        if soft > 0:
            a = ndimage.gaussian_filter(a, soft, mode="wrap")
        out[k] = a
    levels = out.reshape(len(LEVELS), -1).mean(axis=1)              # actual mean coverage of each level
    return levels.astype(np.float32), out


@lru_cache(maxsize=4)
def tiles(kind):
    """(levels, pyramid) for a grain kind. pyramid[L] is (K, TILE >> L, TILE >> L)."""
    with _lock:
        path = None
        try:
            path = _cache_path(kind)
            if os.path.exists(path):
                z = np.load(path)
                levels, stack = z["levels"], z["stack"].astype(np.float32) / 65535.0
            else:
                raise FileNotFoundError
        except Exception:
            levels, stack = _simulate(kind)
            if path:
                try:
                    np.savez_compressed(path, levels=levels, stack=np.round(stack * 65535).astype(np.uint16))
                except OSError:
                    pass
        pyr = [stack.astype(np.float32)]
        while pyr[-1].shape[1] > 8:
            p = pyr[-1]
            k, h, w = p.shape
            pyr.append(p.reshape(k, h // 2, 2, w // 2, 2).mean(axis=(2, 4)))
        return levels, pyr


def _sample(arr, fy, fx):
    """Bilinear, wrapping lookup in a (K?, T, T) or (T, T) array; arr indexed [..., y, x]."""
    T = arr.shape[-1]
    y0 = np.floor(fy).astype(np.int32)
    x0 = np.floor(fx).astype(np.int32)
    ty = (fy - y0).astype(np.float32)
    tx = (fx - x0).astype(np.float32)
    y0 %= T
    x0 %= T
    y1 = (y0 + 1) % T
    x1 = (x0 + 1) % T
    a = arr[..., y0, x0] * (1 - tx) + arr[..., y0, x1] * tx
    b = arr[..., y1, x0] * (1 - tx) + arr[..., y1, x1] * tx
    return a * (1 - ty) + b * ty


class Lookup:
    """Precomputed sampling positions for one grain field (one size, one seed) over a block of
    pixels; can then be read at any coverage per pixel, cheaply, many times.

    When the grains are smaller than a pixel the patch is read from its pre-averaged (mip)
    levels, blending smoothly between the two nearest so grain changes continuously with size
    and resolution instead of in steps."""

    def __init__(self, ys, xs, r_img, kind, layer_seed):
        levels, pyr = tiles(kind)
        self.levels = levels
        scale = R_TILE / max(r_img, 1e-3)          # tile px per image px
        # grains well below a pixel (zoomed-out previews): the pre-averaged patch is read with one
        # lookup per pixel instead of four; NEAREST_BIAS picks a mip level whose texels match a pixel
        self.nearest = NEAREST and scale > 3.0
        lf_raw = np.log2(scale) + (NEAREST_BIAS if self.nearest else 0.0)
        lf = float(np.clip(lf_raw, 0.0, len(pyr) - 1.001)) if scale > 1.0 else 0.0
        L0 = int(np.floor(lf))
        frac = lf - L0
        rng = np.random.default_rng(layer_seed)
        oy, ox = rng.uniform(0, 1, 2)
        th = rng.uniform(0, 2 * np.pi)              # rotate per field so fields don't line up
        ct, st = np.float32(np.cos(th)), np.float32(np.sin(th))
        ry = ys * ct - xs * st
        rx = ys * st + xs * ct
        self.parts = []
        for L, wgt in ((L0, 1.0 - frac), (L0 + 1, frac)):
            if wgt <= 1e-3 or L >= len(pyr):
                continue
            arr = pyr[L]
            T = arr.shape[-1]
            sc = np.float32(scale / (2 ** L))
            fy = ry * sc + np.float32(oy * T)
            fx = rx * sc + np.float32(ox * T)
            if self.nearest:
                iy = np.floor(fy + 0.5).astype(np.int32) % T
                ix = np.floor(fx + 0.5).astype(np.int32) % T
                self.parts.append((arr.reshape(-1), T * T, (iy * T + ix,), None, np.float32(wgt)))
                continue
            y0 = np.floor(fy)
            x0 = np.floor(fx)
            ty = (fy - y0).astype(np.float32)
            tx = (fx - x0).astype(np.float32)
            y0 = y0.astype(np.int32) % T
            x0 = x0.astype(np.int32) % T
            y1 = (y0 + 1) % T
            x1 = (x0 + 1) % T
            idx = (y0 * T + x0, y0 * T + x1, y1 * T + x0, y1 * T + x1)
            w = ((1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty)
            self.parts.append((arr.reshape(-1), T * T, idx, w, np.float32(wgt)))

    def _at(self, k):
        out = None
        for flat, TT, idx, ws, wgt in self.parts:
            base = k * TT
            if ws is None:
                v = flat[base + idx[0]]
                out = v * wgt if out is None else out + v * wgt
                continue
            i00, i01, i10, i11 = idx
            w00, w01, w10, w11 = ws
            v = flat[base + i00] * w00 + flat[base + i01] * w01 + flat[base + i10] * w10 + flat[base + i11] * w11
            out = v * wgt if out is None else out + v * wgt
        return out

    def __call__(self, c):
        """Local grain coverage for target coverage c (n,). Mean ≈ c."""
        levels = self.levels
        k1 = np.clip(_LEVEL_LUT[np.clip((c * 4095).astype(np.int32), 0, 4095)], 1, len(levels) - 1)
        k0 = k1 - 1
        l0, l1 = levels[k0], levels[k1]
        w = np.clip((c - l0) / np.maximum(l1 - l0, 1e-6), 0, 1).astype(np.float32)
        v = self._at(k0) * (1 - w) + self._at(k1) * w
        return np.clip(v + (c - (l0 * (1 - w) + l1 * w)), 0.0, 1.0)


def _level_lut():
    # coverage -> index of the first level above it (levels are near LEVELS, refined after load)
    return np.searchsorted(LEVELS, np.linspace(0, 1, 4096)).astype(np.int32)


_LEVEL_LUT = _level_lut()


def coverage(c, ys, xs, r_img, kind, layer_seed):
    """One-off convenience wrapper around Lookup."""
    return Lookup(ys, xs, r_img, kind, layer_seed)(c)


def warm(kinds=("dye", "silver")):
    """Build the patches in the background so the first grainy render doesn't wait."""
    def run():
        for k in kinds:
            try:
                tiles(k)
            except Exception:
                pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
