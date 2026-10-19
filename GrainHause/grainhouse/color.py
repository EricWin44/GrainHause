"""Colour management: RGB spaces, transfer functions and matrices.

The engine works in *linear Rec.2020* (D65). Every input is converted into
that space and every output is converted out of it, so the film model sees
the same numbers whether the photo came from a RAW, a Lightroom TIFF or a JPEG.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

D65 = (0.3127, 0.3290)
D50 = (0.3457, 0.3585)


def _xy_to_XYZ(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def rgb_to_xyz_matrix(primaries, white):
    """3x3 matrix taking linear RGB (with given primaries/white) to XYZ."""
    P = np.stack([_xy_to_XYZ(p) for p in primaries], axis=1)  # columns = R,G,B
    W = _xy_to_XYZ(white)
    S = np.linalg.solve(P, W)
    return P * S


_BRADFORD = np.array([
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296],
])


def bradford(src_white, dst_white):
    if src_white == dst_white:
        return np.eye(3)
    s = _BRADFORD @ _xy_to_XYZ(src_white)
    d = _BRADFORD @ _xy_to_XYZ(dst_white)
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


# ---------------------------------------------------------------- transfer fns

def srgb_decode(v):
    v = np.asarray(v, dtype=np.float32)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def srgb_encode(l):
    l = np.clip(l, 0.0, 1.0)
    return np.where(l <= 0.0031308, l * 12.92, 1.055 * np.power(l, 1 / 2.4) - 0.055).astype(np.float32)


def gamma_decode(g):
    return lambda v: np.power(np.clip(np.asarray(v, np.float32), 0, None), g).astype(np.float32)


def gamma_encode(g):
    return lambda l: np.power(np.clip(l, 0.0, 1.0), 1.0 / g).astype(np.float32)


def romm_decode(v):
    v = np.asarray(v, dtype=np.float32)
    return np.where(v < 16.0 / 512.0, v / 16.0, np.power(np.clip(v, 0, None), 1.8)).astype(np.float32)


def romm_encode(l):
    l = np.clip(l, 0.0, 1.0)
    return np.where(l < 1.0 / 512.0, l * 16.0, np.power(l, 1 / 1.8)).astype(np.float32)


def linear(v):
    return np.asarray(v, dtype=np.float32)


@dataclass(frozen=True)
class RGBSpace:
    name: str
    primaries: tuple
    white: tuple
    decode: object
    encode: object

    @property
    def to_xyz(self):
        return rgb_to_xyz_matrix(self.primaries, self.white)


SRGB = RGBSpace("sRGB", ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), D65, srgb_decode, srgb_encode)
DISPLAY_P3 = RGBSpace("Display P3", ((0.68, 0.32), (0.265, 0.69), (0.15, 0.06)), D65, srgb_decode, srgb_encode)
ADOBE_RGB = RGBSpace("Adobe RGB (1998)", ((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)), D65,
                     gamma_decode(563 / 256), gamma_encode(563 / 256))
PROPHOTO = RGBSpace("ProPhoto RGB", ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)), D50,
                    romm_decode, romm_encode)
PROPHOTO_LINEAR = RGBSpace("ProPhoto RGB (linear)", PROPHOTO.primaries, D50, linear, linear)
REC2020_LINEAR = RGBSpace("Rec.2020 (linear)", ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)), D65,
                          linear, linear)

WORKING = REC2020_LINEAR


def conversion_matrix(src: RGBSpace, dst: RGBSpace) -> np.ndarray:
    m = np.linalg.inv(dst.to_xyz) @ bradford(src.white, dst.white) @ src.to_xyz
    return m.astype(np.float32)


def apply_matrix(img: np.ndarray, m: np.ndarray) -> np.ndarray:
    """img: (..., 3) float32. Returns img @ m.T without huge temporaries."""
    return np.einsum("...c,dc->...d", img, m.astype(np.float32), optimize=True).astype(np.float32, copy=False)


def space_from_icc_description(desc: str | None, default: RGBSpace = SRGB) -> RGBSpace:
    if not desc:
        return default
    d = desc.lower()
    if "prophoto" in d or "romm" in d:
        return PROPHOTO
    if "adobe" in d and "rgb" in d:
        return ADOBE_RGB
    if "p3" in d:
        return DISPLAY_P3
    if "srgb" in d or "61966" in d:
        return SRGB
    return default


# Rec.2020 luminance weights (linear)
LUMA_2020 = np.array([0.2627, 0.6780, 0.0593], dtype=np.float32)


def luminance(img):
    return apply_matrix(img, LUMA_2020[None, :])[..., 0]


# ---------------------------------------------------------------- white balance

WB_REFERENCE_K = 5500.0


def cct_to_xy(T: float):
    """Planckian locus chromaticity (Kim et al. 2002), 1667–25000 K."""
    T = float(np.clip(T, 1667.0, 25000.0))
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    return x, y


def _white_rgb(T):
    x, y = cct_to_xy(T)
    xyz = np.array([x / y, 1.0, (1 - x - y) / y])
    rgb = np.linalg.inv(REC2020_LINEAR.to_xyz) @ xyz
    return rgb / float(rgb @ LUMA_2020)


def wb_gains(temp: float, tint: float, reference: float = WB_REFERENCE_K) -> np.ndarray:
    """Gains that make light of colour temperature `temp` neutral, relative to `reference`.

    Lower temp = you're telling it the light was warmer, so the image is cooled (like Lightroom).
    tint: + adds magenta, - adds green."""
    g = _white_rgb(reference) / _white_rgb(temp)
    g = g * np.array([1.0, 2 ** (-0.45 * tint / 100.0), 1.0])
    return (g / float(g @ LUMA_2020)).astype(np.float32)


def wb_from_gains(gains) -> tuple[float, float]:
    """Inverse of wb_gains: the (temp, tint) closest to the given channel gains."""
    gains = np.asarray(gains, np.float64)
    target = np.log(gains / gains.mean())
    best, best_err = (WB_REFERENCE_K, 0.0), 1e9
    for T in np.geomspace(2000, 15000, 240):
        g0 = _white_rgb(WB_REFERENCE_K) / _white_rgb(T)
        # tint only moves green: pick it to match the green/magenta balance exactly
        rb = np.log(g0 / g0.mean())
        gr = target[1] - (target[0] + target[2]) / 2 - (rb[1] - (rb[0] + rb[2]) / 2)
        tint = float(np.clip(-gr / (0.45 * np.log(2)) * 100.0, -150, 150))
        g = wb_gains(T, tint)
        err = float(np.sum((np.log(g / g.mean()) - target) ** 2))
        if err < best_err:
            best, best_err = (float(T), tint), err
    return best


def wb_for_neutral(rgb) -> tuple[float, float]:
    """(temp, tint) that would make this (white-balanced, linear) pixel neutral."""
    rgb = np.maximum(np.asarray(rgb, np.float64), 1e-6)
    return wb_from_gains(1.0 / rgb)
