"""Builds a developed film model (layer curves + dye lookup) for a stock and a lab process."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy.optimize import least_squares

from . import color, dye, stocks

LOG2_10 = 0.30103  # density per stop at gamma 1
LUMA = np.asarray(color.LUMA_2020, np.float64)

# Scanner output is linear sRGB primaries (what lab scanners deliver)
_SRGB_TO_2020 = color.conversion_matrix(
    color.RGBSpace("lin sRGB", color.SRGB.primaries, color.SRGB.white, color.linear, color.linear),
    color.WORKING).astype(np.float64)

LABS = ["frontier", "noritsu", "neutral", "print"]
LAB_LABELS = {"frontier": "Frontier-style scan", "noritsu": "Noritsu-style scan",
              "neutral": "Neutral (camera) scan", "print": "Optical print (RA-4 paper)"}


def _sp(x, k):
    return np.logaddexp(0.0, k * x) / k


def tone(p, c, black, white):
    """Log-logistic scan tone curve (stops in, display-linear out)."""
    m = (white - black) / (0.18 - black) - 1.0
    return black + (white - black) / (1.0 + m * np.exp2(-c * p))


class FilmModel:
    def __init__(self, st: stocks.Stock, lab: str, extra_interimage: float = 0.0):
        self.st, self.lab, self.kind = st, lab, st.kind
        self.extra_interimage = extra_interimage
        if st.kind == "bw":
            self.A = None
            self.bw_w = np.asarray(st.bw_weights, np.float32) / sum(st.bw_weights)
        else:
            if st.taking is not None:
                A = np.asarray(st.taking, np.float32)
                self.A = (A / (A @ np.ones(3))[:, None]).astype(np.float32)   # white exposes layers equally
            else:
                self.A = dye.taking_matrix(st.sens_centers, st.sens_widths, st.cyan_layer)
        self.gamma = np.asarray(st.gamma, np.float64)
        self.toe = np.asarray(st.toe, np.float64)
        self.sh = np.asarray(st.shoulder_at, np.float64)
        self.kt, self.ks = st.toe_softness, st.shoulder_softness
        dye_set = "silver" if st.kind == "bw" else st.dyes
        self.spectra = dye.dye_spectra(dye_set, "rev" if st.kind == "rev" else "neg")
        self.balance = np.zeros(3)
        if st.kind == "rev":
            self.dmin = 0.12
            span = st.dmax - self.dmin
            self.k_rev = 4 * self.gamma * LOG2_10 / span
            q = (1.0 - self.dmin) / span                      # grey card -> density 1.0
            self.e_mid = np.log(q / (1 - q)) / self.k_rev     # logistic midpoint (stops)
            raw = self._slide_raw()
        elif lab == "print":
            raw = self._print_raw()
        else:
            raw = self._scan_raw(lab)
        self.lut = dye.DyeLUT(self._finish(raw), 0.0, (st.dmax + 0.3) if st.kind == "rev" else 3.4, 44)

    # ---------------------------------------------------------- layer curves
    def layer_density(self, e, balance=None):
        """e: (n,3) stops of exposure per layer -> dye density (n,3)."""
        e = e + (self.balance if balance is None else balance)
        if self.kind == "rev":
            span = self.st.dmax - self.dmin
            D = self.dmin + span / (1 + np.exp(self.k_rev * (e - self.e_mid)))
        else:
            D = self.gamma * LOG2_10 * (_sp(e - self.toe, self.kt) - _sp(e - self.sh, self.ks))
        ii = self.st.interimage + self.extra_interimage
        if ii and self.kind != "bw":
            # DIR couplers: each layer's development is held back by its neighbours,
            # which exaggerates the differences between the dye layers (saturation in the dyes)
            D = D + ii * (D - D.mean(axis=-1, keepdims=True))
        return np.maximum(D, 0.0)

    def _T(self, D):
        return np.power(10.0, -(D @ self.spectra.T))

    # ---------------------------------------------------------- lab scan of a negative
    def _scan_raw(self, lab):
        st = self.st
        bands = dye.SCANNERS["neutral" if st.kind == "bw" else lab]
        bnorm = bands / bands.sum(axis=0, keepdims=True)

        def scan_density(D):
            return -np.log10(np.maximum(self._T(D) @ bnorm, 1e-12))

        d_gray = self.layer_density(np.zeros((1, 3)))
        ds_gray = scan_density(d_gray)[0]
        dd = scan_density(self.layer_density(np.array([[-0.5] * 3, [0.5] * 3])))
        slope = dd[1] - dd[0]
        slope = 0.75 * slope + 0.25 * slope.mean()
        c = st.scan_contrast * {"frontier": 1.05, "noritsu": 0.97}.get(lab, 1.0)
        black = float(np.mean(st.scan_black))
        sat = {"frontier": 1.06, "noritsu": 0.98, "neutral": 0.95}.get(lab, 1.0)

        def raw(D):
            P = (scan_density(D) - ds_gray) / slope
            y = tone(P, c, black, st.scan_white)
            if sat != 1.0:
                L = y @ np.array([0.2126, 0.7152, 0.0722])
                y = L[:, None] + (y - L[:, None]) * sat
            return np.maximum(y, 0) @ _SRGB_TO_2020.T

        return raw

    # ---------------------------------------------------------- optical RA-4 print
    def _print_raw(self):
        st = self.st
        paper_dyes = np.stack(dye.DYES["silver" if st.kind == "bw" else "paper"], axis=1)
        if st.kind == "bw":
            paper_dyes = paper_dyes / 3.0
        sens = dye.PAPER_SENS / dye.PAPER_SENS.sum(axis=0, keepdims=True)
        dmin, dmax, pg = 0.06, 2.35, st.paper_gamma
        k = 4 * pg / (dmax - dmin)

        def paper(D, lights):
            H = self._T(D) @ sens
            logH = np.log10(np.maximum(H, 1e-12)) + lights
            Dp = dmin + (dmax - dmin) / (1 + np.exp(-k * logH))
            return dye.spectral_to_rec2020(np.power(10.0, -(Dp @ paper_dyes.T)))

        wY = float(dye.spectral_to_rec2020(np.power(10.0, -(np.full((1, 3), dmin) @ paper_dyes.T)))[0] @ LUMA)
        scale = 0.955 / wY
        d_gray = self.layer_density(np.zeros((1, 3)))

        def resid(lights):
            out = paper(d_gray, lights)[0] * scale
            return np.log(np.maximum(out, 1e-6)) - np.log(0.18)

        best = None
        for x0 in (0.0, 0.6, 1.2, -0.6):
            r = least_squares(resid, x0=np.full(3, x0), bounds=(-4, 4), method="trf")
            if best is None or r.cost < best.cost:
                best = r
        lights = best.x
        return lambda D: np.maximum(paper(D, lights) * scale, 0)

    # ---------------------------------------------------------- slide film, viewed directly
    def _slide_raw(self):
        base = 0.06

        def view(D):
            return dye.spectral_to_rec2020(np.power(10.0, -(D @ self.spectra.T + base)))

        def resid(b):
            out = view(self.layer_density(np.zeros((1, 3)), balance=b))[0]
            return np.log(np.maximum(out, 1e-9)) - np.log(np.mean(np.maximum(out, 1e-9)))

        b = least_squares(resid, x0=np.zeros(3), bounds=(-3, 3), method="trf").x
        self.balance = b - b.mean()
        gain = 0.18 / float(view(self.layer_density(np.zeros((1, 3))))[0] @ LUMA)

        def raw(D):
            y = view(D) * gain
            return y / np.power(1 + np.power(np.maximum(y, 0) / 1.06, 6), 1 / 6)  # projector white roll-off

        return raw

    # ---------------------------------------------------------- lab grey balance + the stock's look
    def _finish(self, raw):
        st = self.st
        e = np.linspace(-10, 10, 161)
        ramp = raw(self.layer_density(np.repeat(e[:, None], 3, axis=1)))
        Y = np.maximum(ramp @ LUMA, 1e-6)
        corr = (Y[:, None] / np.maximum(ramp, 1e-6)) ** 0.75  # labs neutralise ~75% of the drift; the rest is the film
        order = np.argsort(Y)
        logY, corr = np.log2(Y[order]), corr[order]
        logY, idx = np.unique(logY, return_index=True)
        corr = corr[idx]
        bal = np.asarray(st.lab_balance)
        sh_t, hl_t = np.asarray(st.shadow_tint), np.asarray(st.highlight_tint)
        black = np.asarray(st.scan_black) - float(np.mean(st.scan_black))
        mono = st.kind == "bw"

        def fn(D):
            y = raw(D)
            y0 = np.maximum(y @ LUMA, 1e-6)
            ly = np.log2(y0)
            k = np.stack([np.interp(ly, logY, corr[:, i]) for i in range(3)], axis=1)
            y = y * k
            if mono:
                return np.repeat((y @ LUMA)[:, None], 3, axis=1)
            p = (ly - np.log2(0.18))[:, None]
            ws = 1 / (1 + np.exp((p + 1.6) * 1.4))
            wh = 1 / (1 + np.exp(-(p - 1.4) * 1.4))
            y = y * np.exp2(bal + ws * sh_t + wh * hl_t)
            y = y + black * np.exp(-y0 / 0.04)[:, None]   # the lab's tinted black point
            return np.maximum(y, 0)

        return fn

    # ---------------------------------------------------------- develop
    def exposures(self, x):
        if self.kind == "bw":
            L = np.maximum(x @ self.bw_w, 1e-9)
            return np.repeat(L[:, None], 3, axis=1)
        return np.maximum(x @ self.A.T, 1e-9)


@lru_cache(maxsize=64)
def get_model(stock_id: str, lab: str, extra_interimage: float = 0.0) -> FilmModel:
    return FilmModel(stocks.get(stock_id), lab, round(extra_interimage, 2))
