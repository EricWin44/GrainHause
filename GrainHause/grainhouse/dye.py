"""Spectral dye-formation model.

The image is *dyed*, not filtered:

  scene light ──► three emulsion layers, each with its own spectral sensitivity
                  (red-, green- and blue-sensitive; Fuji stocks add a 4th
                  cyan-sensitive interlayer that inhibits the red layer)
              ──► each layer develops dye through its characteristic curve
                  (cyan in the red layer, magenta in green, yellow in blue)
              ──► the dyes overlap spectrally, including their unwanted side
                  absorptions; the film's transmittance is 10^-(sum of dye
                  densities) at every wavelength
              ──► that light is either read by a lab scanner's narrow-band
                  sensors, exposed onto RA-4 colour paper (which forms its own
                  three dyes and is viewed by reflected light), or – for slide
                  film – viewed directly

Everything after the dye densities is precomputed into a 3-D lookup table
over (cyan, magenta, yellow) density, so per-pixel cost is a trilinear lookup.
Grain is added to the dye densities before the lookup, so it is dyed too.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import color

LAM = np.arange(380.0, 731.0, 5.0)
DL = 5.0
LN10 = np.log(10.0)


# ---------------------------------------------------------------- spectral helpers

def _g(mu, s1, s2=None):
    s2 = s1 if s2 is None else s2
    s = np.where(LAM < mu, s1, s2)
    return np.exp(-0.5 * ((LAM - mu) / s) ** 2)


def _sig(mu, w):
    return 1.0 / (1.0 + np.exp(-(LAM - mu) / w))


# CIE 1931 2° colour-matching functions (Wyman, Sloan & Shirley 2013 multi-lobe fit)
CMF = np.stack([
    1.056 * _g(599.8, 37.9, 31.0) + 0.362 * _g(442.0, 16.0, 26.7) - 0.065 * _g(501.1, 20.4, 26.2),
    0.821 * _g(568.8, 46.9, 40.5) + 0.286 * _g(530.9, 16.3, 31.1),
    1.217 * _g(437.0, 11.8, 36.0) + 0.681 * _g(459.0, 26.0, 13.8),
], axis=1)  # (n_lam, 3)

# Smooth basis spectra used to turn an RGB triple into a plausible scene spectrum
_BASIS = np.stack([_sig(592.0, 16.0), _g(542.0, 36.0), 1.0 - _sig(488.0, 15.0)], axis=1)
_XYZ_OF_BASIS = CMF.T @ _BASIS * DL
_REC2020_TO_XYZ = color.REC2020_LINEAR.to_xyz
# rgb (linear Rec.2020) -> basis weights -> spectrum
_RGB_TO_SPEC = _BASIS @ np.linalg.inv(_XYZ_OF_BASIS) @ _REC2020_TO_XYZ  # (n_lam, 3)

_E_WHITE = (1 / 3, 1 / 3)
_ADAPT_E_TO_D65 = color.bradford(_E_WHITE, color.D65)
_XYZ_TO_2020 = np.linalg.inv(_REC2020_TO_XYZ)


def spectral_to_rec2020(spec: np.ndarray) -> np.ndarray:
    """(…, n_lam) spectra of light under an equal-energy source -> linear Rec.2020 (white-adapted)."""
    xyz = spec @ CMF * DL
    return xyz @ (_XYZ_TO_2020 @ _ADAPT_E_TO_D65).T


# ---------------------------------------------------------------- dye sets

def _dye(peak, wl, wr, sec=None):
    d = _g(peak, wl, wr)
    if sec:
        for mu, amp, w in sec:
            d = d + amp * _g(mu, w)
    return d / d.max()


DYES = {
    # colour-negative dyes; side absorptions are partly cancelled by the orange mask
    "kodak_neg": (_dye(690, 62, 42, [(470, 0.14, 42)]),
                  _dye(552, 38, 34, [(435, 0.30, 30)]),
                  _dye(448, 32, 30, [(520, 0.06, 30)])),
    "fuji_neg": (_dye(680, 58, 44, [(475, 0.13, 40)]),
                 _dye(546, 36, 33, [(430, 0.28, 30)]),
                 _dye(445, 31, 30, [(515, 0.07, 30)])),
    "cine_neg": (_dye(695, 64, 44, [(470, 0.14, 42)]),
                 _dye(555, 38, 34, [(438, 0.30, 30)]),
                 _dye(450, 33, 30, [(520, 0.06, 30)])),
    # reversal (slide) dyes – no mask, so side absorptions stay
    "kodak_rev": (_dye(652, 50, 48, [(470, 0.12, 40)]),
                  _dye(550, 37, 33, [(430, 0.22, 28)]),
                  _dye(440, 30, 30)),
    "fuji_rev": (_dye(656, 46, 44, [(470, 0.10, 40)]),
                 _dye(545, 33, 31, [(430, 0.18, 28)]),
                 _dye(445, 27, 28)),
    "kodachrome": (_dye(662, 46, 44, [(470, 0.10, 40)]),
                   _dye(538, 40, 34, [(432, 0.25, 28)]),
                   _dye(440, 31, 31)),
    # RA-4 colour paper dyes
    "paper": (_dye(660, 52, 46, [(470, 0.10, 40)]),
              _dye(545, 37, 32, [(432, 0.16, 28)]),
              _dye(446, 32, 30)),
    "silver": (np.ones_like(LAM), np.ones_like(LAM), np.ones_like(LAM)),
}
MASK_FACTOR = 0.38  # how much of a negative dye's side absorption survives masking

PAPER_SENS = np.stack([_g(694, 13), _g(548, 17), _g(468, 18)], axis=1)

SCANNERS = {
    # narrow LED / filter bands read the dye densities almost independently
    "frontier": np.stack([_g(668, 16), _g(546, 15), _g(452, 14)], axis=1),
    "noritsu": np.stack([_g(658, 20), _g(538, 18), _g(456, 16)], axis=1),
    # broad, camera-like bands (a "DSLR scan")
    "neutral": np.stack([_g(615, 32), _g(540, 30), _g(462, 26)], axis=1),
}


def dye_spectra(dye_set: str, kind: str) -> np.ndarray:
    """(n_lam, 3) spectral density per unit peak density for C, M, Y."""
    c, m, y = DYES[dye_set]
    out = np.stack([c, m, y], axis=1)
    if dye_set == "silver":
        return out / 3.0  # one silver image, carried equally by the three channels
    if kind == "neg" and dye_set != "silver":
        primary = np.stack([_dye_main(dye_set, i) for i in range(3)], axis=1)
        out = primary + (out - primary) * MASK_FACTOR
    return out


@lru_cache(maxsize=None)
def _dye_main_cached(dye_set):
    peaks = {"kodak_neg": [(690, 62, 42), (552, 38, 34), (448, 32, 30)],
             "fuji_neg": [(680, 58, 44), (546, 36, 33), (445, 31, 30)],
             "cine_neg": [(695, 64, 44), (555, 38, 34), (450, 33, 30)]}
    return [_g(*p) for p in peaks[dye_set]]


def _dye_main(dye_set, i):
    return _dye_main_cached(dye_set)[i]


# ---------------------------------------------------------------- taking sensitivities

def taking_matrix(centers, widths, cyan_layer: float = 0.0) -> np.ndarray:
    """3x3 matrix: linear Rec.2020 scene RGB -> relative exposure of the R, G, B layers."""
    sens = np.stack([_g(c, w) for c, w in zip(centers, widths)], axis=1)
    A = sens.T @ _RGB_TO_SPEC * DL
    if cyan_layer > 0:
        # Fuji's 4th (cyan-sensitive) layer inhibits the red-sensitive layer in proportion
        # to blue-green light. Scaled to the red layer's own white response, so `cyan_layer`
        # is the fraction of red-layer development it can hold back.
        cyan = (_g(505, 22)[None, :] @ _RGB_TO_SPEC * DL)[0]
        cyan = cyan * (A[0] @ np.ones(3)) / (cyan @ np.ones(3))
        A[0] = A[0] - cyan_layer * cyan
    A = A / (A @ np.ones(3))[:, None]  # white light exposes all layers equally
    # A three-number RGB can't pin down a real spectrum, so the raw projection exaggerates
    # crosstalk. Keep its direction (which comes from where each layer sits in the spectrum)
    # but blend toward identity to the strength of real layer crosstalk.
    A = (1 - TAKING_STRENGTH) * np.eye(3) + TAKING_STRENGTH * A
    return A.astype(np.float32)


TAKING_STRENGTH = 0.35


# ---------------------------------------------------------------- lookup table

class DyeLUT:
    """Maps dye densities (C, M, Y above fog) -> display-linear Rec.2020."""

    def __init__(self, fn, lo, hi, n=40):
        self.lo, self.hi, self.n = float(lo), float(hi), n
        g = np.linspace(lo, hi, n)
        C, M, Y = np.meshgrid(g, g, g, indexing="ij")
        D = np.stack([C, M, Y], axis=-1).reshape(-1, 3)
        self.table = fn(D).reshape(n, n, n, 3).astype(np.float32)
        self.flat = np.ascontiguousarray(self.table.reshape(-1, 3))

    def __call__(self, D: np.ndarray) -> np.ndarray:
        n = self.n
        f = (np.clip(D, self.lo, self.hi) - self.lo) * np.float32((n - 1) / (self.hi - self.lo))
        i0 = np.minimum(f.astype(np.int32), n - 2)
        t = f - i0
        flat = self.flat
        base = (i0[:, 0] * n + i0[:, 1]) * n + i0[:, 2]
        tx, ty, tz = t[:, 0:1], t[:, 1:2], t[:, 2:3]
        sx, sy, sz = n * n, n, 1
        c00 = flat[base] * (1 - tz) + flat[base + sz] * tz
        c01 = flat[base + sy] * (1 - tz) + flat[base + sy + sz] * tz
        c10 = flat[base + sx] * (1 - tz) + flat[base + sx + sz] * tz
        c11 = flat[base + sx + sy] * (1 - tz) + flat[base + sx + sy + sz] * tz
        c0 = c00 * (1 - ty) + c01 * ty
        c1 = c10 * (1 - ty) + c11 * ty
        return c0 * (1 - tx) + c1 * tx
