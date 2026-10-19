"""The film engine.

Per pixel (linear Rec.2020):

  1. light on the film   white balance (as a colour temperature), exposure and
                         metering, vignette, bloom, halation and light leaks are
                         all light reaching the emulsion; the film's limited
                         resolution softens it (MTF)
  2. emulsion            the light is split into the three layers by their
                         spectral sensitivities and each layer develops dye
                         through its own characteristic curve
  3. grain               dye clouds: per-layer density noise, coarser and
                         clumpier where the negative is thin
  4. dye -> image        the dyes' spectral densities are read by a lab scanner,
                         printed onto RA-4 paper, or (slides) viewed directly —
                         all precomputed into a 3-D density lookup (dye.py/film.py)
  5. finishing           user saturation, fade, paper / expired textures, dust,
                         scratches, tone curve, output colour space

Everything streams through row chunks so large RAWs stay within a few GB.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
from scipy import ndimage
from scipy.interpolate import PchipInterpolator

from . import color, effects, film, grain, stocks, textures
from .settings import Settings

MID = 0.18
NEUTRAL_C = 1.5
NEUTRAL_W = 1.03
NEUTRAL_M = NEUTRAL_W / MID - 1.0
LUMA = color.LUMA_2020


# ------------------------------------------------------------------ curves

def display_to_scene(y: np.ndarray) -> np.ndarray:
    """Invert the neutral rendering, so already-edited (display) images can be re-exposed onto film."""
    y = np.clip(y, 0.0, NEUTRAL_W * 0.9995)
    tc = NEUTRAL_M * y / (NEUTRAL_W - y)
    return (MID * np.power(tc, 1.0 / NEUTRAL_C)).astype(np.float32)


def neutral_tone(p, c=NEUTRAL_C):
    return NEUTRAL_W / (1.0 + NEUTRAL_M * np.exp2(-c * p))


def meter(x_low: np.ndarray) -> float:
    """Centre-weighted average metering, like an in-camera meter. Returns stops to add."""
    L = color.luminance(x_low)
    lo, hi = np.percentile(L, [1, 99.5])
    L = np.clip(L, max(lo, 1e-6), max(hi, 1e-5))
    h, w = L.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    wgt = np.exp(-(((xx / w - 0.5) / 0.35) ** 2 + ((yy / h - 0.5) / 0.35) ** 2))
    logmean = float(np.sum(wgt * np.log2(L)) / np.sum(wgt))
    return float(np.clip(np.log2(MID) - logmean, -6.0, 9.0))


def _is_identity(pts):
    return pts is None or (len(pts) == 2 and abs(pts[0][0]) < 1e-6 and abs(pts[0][1]) < 1e-6
                           and abs(pts[1][0] - 1) < 1e-6 and abs(pts[1][1] - 1) < 1e-6)


@lru_cache(maxsize=32)
def _curve_lut(pts: tuple) -> np.ndarray:
    xs = np.array([p[0] for p in pts], np.float64)
    ys = np.array([p[1] for p in pts], np.float64)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    xs, idx = np.unique(xs, return_index=True)
    ys = ys[idx]
    if len(xs) < 2:
        return np.linspace(0, 1, 4096, dtype=np.float32)
    f = PchipInterpolator(xs, ys, extrapolate=False)
    g = np.linspace(0, 1, 4096)
    out = f(np.clip(g, xs[0], xs[-1]))
    out = np.where(g < xs[0], ys[0], np.where(g > xs[-1], ys[-1], out))
    return np.clip(out, 0, 1).astype(np.float32)


def _apply_lut(v, lut):
    i = np.clip(v, 0, 1) * 4095.0
    i0 = np.floor(i).astype(np.int32)
    i1 = np.minimum(i0 + 1, 4095)
    t = i - i0
    return lut[i0] * (1 - t) + lut[i1] * t


def apply_tone_curve(y, s: Settings):
    """Tone curve on display values (sRGB-style encoding on the working primaries), like Lightroom's."""
    curves = [s.curve_rgb, s.curve_r, s.curve_g, s.curve_b]
    if all(_is_identity(c) for c in curves):
        return y
    v = color.srgb_encode(np.clip(y, 0, 1))
    if not _is_identity(s.curve_rgb):
        v = _apply_lut(v, _curve_lut(tuple(map(tuple, s.curve_rgb))))
    for ch, pts in enumerate(curves[1:]):
        if not _is_identity(pts):
            v[..., ch] = _apply_lut(v[..., ch], _curve_lut(tuple(map(tuple, pts))))
    return color.srgb_decode(v)


# ------------------------------------------------------------------ white balance

def wb_gains_for(s: Settings, meta: dict | None) -> np.ndarray:
    meta = meta or {}
    if s.wb_temp is None and s.wb_tint is None:
        g = meta.get("as_shot_gains")
        return np.asarray(g, np.float32) if g is not None else np.ones(3, np.float32)
    T0, t0 = meta.get("as_shot_wb", (color.WB_REFERENCE_K, 0.0))
    T = T0 if s.wb_temp is None else s.wb_temp
    tint = t0 if s.wb_tint is None else s.wb_tint
    return color.wb_gains(T, tint)


def as_shot_wb(meta: dict | None):
    return (meta or {}).get("as_shot_wb", (color.WB_REFERENCE_K, 0.0))


# ------------------------------------------------------------------ resolved look

class Look:
    def __init__(self, s: Settings, *, before: bool = False):
        st = stocks.get(s.stock)
        self.stock = st
        self.before = before
        self.is_film = st.id != "neutral" and not before
        self.strength = 0.0 if before else float(np.clip(s.strength, 0, 100)) / 100.0
        push = float(np.clip(s.push, -2, 2))
        sat = s.saturation / 100.0
        self.model = film.get_model(st.id, s.lab_value(), round(0.35 * max(sat, 0) * 0.5, 2)) if self.is_film else None
        self.contrast = 2 ** (s.contrast / 100.0 * 0.55) * (1 + 0.1 * push)
        self.push = push
        self.hl = s.highlights / 100.0 * 1.3
        self.sh = s.shadows / 100.0 * 1.3
        self.post_sat = 1.0 + 0.45 * sat if not before else 1.0
        gfac = 1 + (0.35 * push if push > 0 else 0.15 * push)
        # coarser grain is also stronger grain (granularity grows with grain size), so Size reads naturally
        size_f = max(0.2, s.grain_size_pct() / 100.0)
        self.grain = 0.0 if before else st.grain * 0.62 * grain_curve(s.grain_pct()) * gfac * size_f ** 0.6
        self.grain_size = st.grain_size * s.grain_size_pct() / 100.0 * (1 + 0.12 * max(push, 0))
        self.grain_colour = st.grain_colour
        self.grain_rough = float(np.clip(s.grain_roughness, 0, 100)) / 100.0
        self.softness = 0.0 if before else st.softness * s.softness_pct() / 100.0
        self.depth = 0.0 if (before or st.id == "neutral") else st.adjacency * float(np.clip(s.depth, 0, 200)) / 100.0
        self.halation = 0.0 if before else s.halation_value()
        self.bloom = 0.0 if before else s.bloom_value()
        self.leak = "none" if before else s.leak
        self.leak_amount, self.leak_seed = s.leak_amount, s.leak_seed
        self.vignette = 0.0 if before else s.vignette
        self.fade = 0.0 if before else s.fade / 100.0 * 0.07
        self.seed = s.seed
        self.dust = 0.0 if before else s.dust
        self.scratches = 0.0 if before else s.scratches
        self.texture = "none" if before else s.texture
        self.texture_amount = s.texture_amount
        # dust on a negative prints white; on a slide it stays dark
        self.defects_white = st.kind != "rev"

    def _tone_mods(self, e):
        """Exposure-side adjustments: development contrast, dodge (shadows) and burn (highlights)."""
        if self.contrast != 1.0:
            e = e * self.contrast
        if self.hl or self.sh:
            eb = e.mean(axis=-1, keepdims=True)
            whl = 1 / (1 + np.exp(-(eb - 1.0) * 1.3))
            wsh = 1 / (1 + np.exp((eb + 1.0) * 1.3))
            e = e + self.hl * whl + self.sh * wsh
        return e

    def develop(self, x: np.ndarray, fine=None, coarse=None, vis=1.0, E=None, amp=None,
                shape=None, adj_px=0.0, D_regional=None, pos=None, r_img=1.0) -> np.ndarray:
        """x: (n, 3) scene light on the film. E: optional (n, 3) layer exposures (already blurred per
        layer by the film's MTF). Returns display-linear Rec.2020 (n, 3)."""
        x = np.maximum(x, 0.0)
        e_n = self._tone_mods(np.log2(np.maximum(x, 2 ** -20) / MID))
        s = self.strength
        if self.model is not None and s > 0:
            m = self.model
            if E is None:
                E = m.exposures(x)
            e = self._tone_mods(np.log2(np.maximum(E, 1e-9) / MID))
            if self.push:
                e = e + 0.12 * self.push * np.minimum(e, 0)  # push blocks up the shadows
            D = m.layer_density(e)
            if self.depth > 0:
                D = self._adjacency(D, shape, adj_px, D_regional)
            if pos is not None and self.grain > 0:
                D = self._film_grain(D, pos, r_img, m.kind)
            y_film = m.lut(D.astype(np.float32))
            y = y_film if s >= 1.0 else neutral_tone(e_n) * (1 - s) + y_film * s
        else:
            if fine is not None and self.grain > 0:
                F = fine * 0.75 + coarse * 0.66
                prof = 0.3 + 0.7 * np.exp(-((e_n.mean(axis=-1, keepdims=True) + 0.5) / 2.4) ** 2)
                e_n = e_n + F * prof * self.grain * 4.0 * vis * (1.0 if amp is None else amp)
            y = neutral_tone(e_n)
        if self.post_sat != 1.0:
            L = y @ LUMA
            y = L[:, None] + (y - L[:, None]) * self.post_sat
        if self.fade > 0:
            y = self.fade * np.array([1.02, 1.0, 0.97], np.float32) + y * (1 - self.fade * 1.25)
        return np.maximum(y, 0).astype(np.float32)

    # development effects ------------------------------------------------------
    ADJ_EDGE = 0.42      # edge (Eberhard) effect, per unit of local density difference
    ADJ_REGION = 0.24    # regional (developer exhaustion / Mackie) effect

    def _adjacency(self, D, shape, edge_px, D_regional):
        """Each dye layer develops harder next to thinner areas and weaker next to denser ones —
        developer is used up locally. This carves edges and separates neighbouring tones *inside
        each layer*, which is much of why film colour looks embedded rather than laid on top."""
        a = self.depth
        if shape is not None and edge_px >= 0.5:
            h, w = shape
            D3 = D.reshape(h, w, 3)
            out = np.empty_like(D3)
            for k, f in enumerate(LAYER_MTF):          # deeper layers diffuse further
                blur = ndimage.gaussian_filter(D3[..., k], edge_px * f, mode="reflect", truncate=3.0)
                out[..., k] = D3[..., k] + a * self.ADJ_EDGE * (D3[..., k] - blur)
            D = out.reshape(-1, 3)
        if D_regional is not None:
            D = D + a * self.ADJ_REGION * (D - D_regional)
        return np.maximum(D, 0.0)

    # Boolean-model grain ---------------------------------------------------------
    COVER_K = 0.35       # density -> covered fraction (Nutting: D = -log10(1 - coverage) / k)
    GRAIN_CAL = 4.3      # calibrated so granularity at density 1 matches each stock's grain value

    def _film_grain(self, D, pos, r_img, kind):
        """Re-form each dye layer's density from simulated grains (see grain.py)."""
        ys, xs = pos
        k = self.COVER_K
        gk = "silver" if kind == "bw" else "dye"
        c = np.clip(1.0 - np.power(10.0, -k * D), 0.0, 0.985)
        # colour mottle: kept modest, and smaller as grain gets heavier, so big grain stays mostly
        # brightness texture (as in real coarse colour film) rather than coloured blotches
        heavy = max(1.0, self.grain_size) * max(1.0, self.grain / max(self.stock.grain * 0.62, 1e-6))
        a = float(np.clip(self.grain_colour * 0.75 / heavy ** 0.6, 0, 1)) if kind != "bw" else 0.0
        rough = self.grain_rough
        # where the negative is thin, only the big fast grains developed: weight toward coarse clumps
        out = np.empty_like(D)
        g = float(np.clip(self.grain * self.GRAIN_CAL * (0.8 if kind == "bw" else 1.0), 0, 0.9))
        # the shared part is the same grain field in every layer (reads as brightness grain);
        # each layer's own grains, at its own size, add the colour mottle
        fine = grain.Lookup(ys, xs, r_img, gk, self.seed * 7 + 0)
        coarse = grain.Lookup(ys, xs, r_img * 2.1, gk, self.seed * 7 + 1)
        for li in range(3 if kind != "bw" else 1):
            cl = c[:, li]
            wc = np.clip(0.12 + 0.4 * rough + 0.3 * np.exp(-D[:, li] / 0.35), 0, 0.85)
            cov = fine(cl) * (1 - wc) + coarse(cl) * wc
            if a > 0:
                own = grain.Lookup(ys, xs, r_img * LAYER_SIZE_GRAIN[li], gk, self.seed * 7 + 11 + li)
                cov = cov * (1 - a) + own(cl) * a
            T = 1.0 - cl
            T2 = np.clip(T + g * ((1.0 - cov) - T), 10 ** (-k * 4.5), 1.0)
            dD = -np.log10(T2 / T) / k
            # Where the negative is very dense, a coverage change is a huge *relative* change in
            # transmitted light, which would explode into speckle. Real granularity only grows like
            # sqrt(density) (Selwyn), so softly hold the excursions to that envelope.
            env = 3.2 * (0.42 * g) * np.sqrt(np.clip(D[:, li], 0.05, None)) + 1e-4
            out[:, li] = D[:, li] + env * np.tanh(dD / env)
        if kind == "bw":
            out[:, 1] = out[:, 0]
            out[:, 2] = out[:, 0]
        return out

    # dye-cloud coverage model ------------------------------------------------
    COVER_K = 0.35  # density -> covered fraction (Nutting); tuned so granularity follows Selwyn (~sqrt D)

    def _grain(self, D, fine, coarse, vis, amp=None):
        """Grain as dye clouds covering the film.

        Each layer's density says what fraction of the area its dye clouds cover. A smooth random
        field is thresholded at exactly that fraction, so thin areas get sparse separate clumps and
        dense areas get merging clumps with small gaps; the density is then read back from the
        local coverage. The grain's strength, size and shape therefore change with exposure the way
        film does, instead of being noise added on top."""
        from scipy.special import ndtr, ndtri
        k = self.COVER_K
        c = np.clip(1.0 - np.power(10.0, -k * D), 1e-4, 1 - 1e-4)
        # thin areas: only the big, fast grains developed -> coarser clumps
        wc = np.clip(0.04 + 0.22 * self.grain_rough + 0.22 * np.exp(-D / 0.35), 0.03, 0.75)
        F = np.sqrt(1 - wc) * fine + np.sqrt(wc) * coarse
        # soft edge: crisp clouds when they span pixels, averaged when they're sub-pixel
        wt = 0.6 + 0.9 * (1.0 - vis)
        q = ndtri(1.0 - c) * np.sqrt(1 + wt * wt)       # keeps the mean coverage exactly c
        c_loc = ndtr((F - q) / wt)
        # a scan pixel averages many dye clouds: g is how much of the single-cloud contrast survives
        g = float(np.clip(self.grain * 1.15, 0, 0.3)) * (0.35 + 0.65 * vis)
        if amp is not None:
            g = np.clip(g * amp, 0, 0.6)
        T = 1.0 - c
        T2 = np.clip(T + g * ((1.0 - c_loc) - T), 10 ** (-k * 4.5), 1.0)
        return -np.log10(T2) / k


# ------------------------------------------------------------------ full render

def prepare_scene(rows: np.ndarray, scene_referred: bool) -> np.ndarray:
    return rows if scene_referred else display_to_scene(rows)


def crop_box(s: Settings, W: int, H: int):
    if not s.crop:
        return 0, 0, W, H
    x0, y0, x1, y1 = s.crop
    a, b = int(round(min(x0, x1) * W)), int(round(min(y0, y1) * H))
    c, d = int(round(max(x0, x1) * W)), int(round(max(y0, y1) * H))
    a, b = max(0, min(a, W - 2)), max(0, min(b, H - 2))
    c, d = max(a + 2, min(c, W)), max(b + 2, min(d, H))
    return a, b, c, d


def render(src: np.ndarray, s: Settings, *, scene_referred: bool, out_space: color.RGBSpace = color.SRGB,
           out_dtype=np.float32, before: bool = False, region=None, meter_ev: float | None = None,
           meta: dict | None = None, ignore_crop: bool = False, chunk_rows: int = 192, progress=None,
           cancel=None, with_border: bool = True, low_cache: dict | None = None) -> np.ndarray | None:
    """Develop `src` (linear Rec.2020) into an encoded image in `out_space`.

    region: optional (x0, y0, x1, y1) in (cropped) image pixels, for the 1:1 loupe."""
    film_landscape = src.shape[1] >= src.shape[0]   # scratches run along the film, whatever the crop
    if not ignore_crop and s.crop:
        H0, W0 = src.shape[:2]
        a, b, c, d = crop_box(s, W0, H0)
        src = src[b:d, a:c]
    look = Look(s, before=before)
    H, W = src.shape[:2]
    long_edge = max(H, W)

    # ---- low-res scene for metering and large-radius light effects
    # the small copy of the frame is the same for every render of this crop: reuse it when we can
    key = (src.shape, tuple(s.crop) if (s.crop and not ignore_crop) else None)
    if low_cache is not None and key in low_cache:
        low = low_cache[key].copy()
    else:
        low = effects.resize_float(src, _low_size(W, H), resample=effects.Image.BOX) if long_edge > effects.LOWRES else src.copy()
        if low_cache is not None:
            low_cache.clear()          # new frame/crop: everything derived from the old one is stale
            low_cache[key] = low.copy()
    low = prepare_scene(low, scene_referred)
    if meter_ev is None:
        meter_ev = meter(low) if (scene_referred and s.auto_meter) else 0.0
    gain = (np.float32(2 ** (meter_ev + s.exposure)) * wb_gains_for(s, meta if scene_referred else None)
            ).astype(np.float32)
    low = low * gain

    aux = None
    if low_cache is not None:
        fk = _frame_key(s, meter_ev, before, scene_referred, H, W)
        aux = low_cache.get("_aux")
        if aux is None or aux.get("key") != fk:
            aux = {"key": fk}
            low_cache["_aux"] = aux
    if aux is not None and "light" in aux:
        add, keep = aux["light"]
    else:
        add, keep = effects.light_maps(
            low, long_edge, halation=look.halation, halation_radius=look.stock.halation_radius,
            halation_colour=look.stock.halation_colour, bloom=look.bloom, leak=look.leak,
            leak_amount=look.leak_amount, leak_seed=look.leak_seed)
        if aux is not None:
            aux["light"] = (add, keep)
    up = effects.Upsampler(add, H, W) if bool(np.any(add)) else None
    tex_low = textures.low_texture_maps(H, W, look.seed, look.texture)
    tex_up = effects.Upsampler(tex_low, H, W) if tex_low is not None else None
    defects = None
    if look.dust > 0 or look.scratches > 0:
        defects = textures.defect_layers(H, W, int(look.seed), float(look.dust), float(look.scratches), film_landscape)

    x0, y0, x1, y1 = region if region else (0, 0, W, H)
    out_h, out_w = y1 - y0, x1 - x0
    out = np.empty((out_h, out_w, 3), dtype=out_dtype)
    to_out = color.conversion_matrix(color.WORKING, out_space)

    sigma_px = 1.45 * look.grain_size * (long_edge / 6000.0)
    r_img = 0.72 * sigma_px            # mean grain radius in image px
    soft_px = look.softness * (long_edge / 6000.0)
    # lab scanner sharpening (what makes scanned grain crisp) and scanner / enlarger veiling glare
    lab = look.model.lab if look.model is not None else None
    kind = look.stock.kind
    if before or look.model is None:
        sharp_amt, glare_k = 0.0, 0.0
    elif kind == "rev":
        sharp_amt, glare_k = 0.3, 0.008
    else:
        sharp_amt, glare_k = SCAN_SHARPEN.get(lab, 0.25), (0.02 if lab == "print" else 0.012)
    sharp_amt *= look.strength          # part of the film look, so it follows film strength
    glare_k *= look.strength
    sharp_r_px = 0.95 * (long_edge / 6000.0)
    # your sharpening on top of the lab's: + crisps edges and grain, - softens like a gentler scan
    user_sharp = 0.0 if before else float(np.clip(getattr(s, "sharpen", 0.0), -100, 100)) / 100.0
    if user_sharp > 0:
        sharp_amt += 1.6 * user_sharp
    elif user_sharp < 0:
        sharp_amt = sharp_amt * (1 + user_sharp) + 0.95 * user_sharp     # down to an outright blur
        sharp_r_px *= 1 - 1.2 * user_sharp                               # and a wider one
    sharp_r = max(0.5, sharp_r_px)
    sharp_amt *= min(1.0, sharp_r_px / 0.5)    # small previews: match what the full-size file will show
    glare_up = None
    if glare_k > 0:
        if aux is not None and "glare" in aux:
            G = aux["glare"]
        else:
            ylow = look.develop(low.reshape(-1, 3)).reshape(low.shape)
            Lg = effects.blur((ylow @ LUMA).astype(np.float32), 0.05 * max(low.shape[:2]), truncate=4.0)
            G = (glare_k * np.repeat(Lg[..., None], 3, axis=2)).astype(np.float32)
            if aux is not None:
                aux["glare"] = G
        glare_up = effects.Upsampler(G, H, W)

    # development effects: regional density field from the small frame
    edge_px = 0.045 * long_edge / 36.0 * (1 if look.depth > 0 else 0)   # ~0.045 mm on a 35 mm frame
    reg_up = None
    if look.depth > 0 and look.model is not None:
        if aux is not None and "regional" in aux:
            D_low = aux["regional"]
        else:
            e_low = look._tone_mods(np.log2(np.maximum(look.model.exposures(low.reshape(-1, 3)), 1e-9) / MID))
            D_low = look.model.layer_density(e_low).reshape(low.shape).astype(np.float32)
            sig = 0.7 / 36.0 * max(low.shape[:2])                              # ~0.7 mm
            D_low = effects.blur(D_low, sig, truncate=4.0)
            if aux is not None:
                aux["regional"] = D_low
        reg_up = effects.Upsampler(D_low, H, W)

    margin = 0
    if edge_px >= 0.5:
        margin = int(np.ceil(3 * edge_px * max(LAYER_MTF))) + 1
    if s.denoise > 0:
        margin = max(margin, 12)
    if soft_px >= 0.3:
        margin = max(margin, int(np.ceil(3 * soft_px * max(LAYER_MTF))) + 1)
    if sharp_amt != 0:
        margin = max(margin, int(np.ceil(3 * sharp_r)) + 1)
    margin = max(margin, 4)

    npx = max(1, x1 - x0)
    target = 110_000 if H * W < 5_000_000 else 380_000
    if region is not None:     # small region (zoom tile, grain sample): split it so every core helps
        target = min(target, max(12_000, (out_w * out_h) // (_workers() * 2)))
    rows_per = int(np.clip(target // npx, 24, chunk_rows if chunk_rows != 192 else 512))
    starts = list(range(y0, y1, rows_per))
    done = [0]
    lock = threading.Lock()

    def work(ci, ys):
        if cancel is not None and cancel():
            return
        ye = min(ys + rows_per, y1)
        a0, a1 = max(0, ys - margin), min(H, ye + margin)
        b0, b1 = max(0, x0 - margin), min(W, x1 + margin)
        blk = src[a0:a1, b0:b1]
        if s.denoise > 0:
            blk = effects.chroma_denoise(blk, s.denoise)
        blk = prepare_scene(blk, scene_referred) * gain
        vig = effects.vignette_rows(a0, a1, H, W, look.vignette, b0, b1)
        if vig is not None:
            blk = blk * vig
        if keep != 1.0:
            blk = blk * np.float32(keep)
        if up is not None:
            blk = blk + up.rows(a0, a1, b0, b1)
        bh, bw = blk.shape[:2]
        nb = bh * bw
        E = None
        if soft_px >= 0.3:
            # film resolution: the red-sensitive layer sits deepest in the emulsion and is softest
            if look.model is not None:
                E = look.model.exposures(blk.reshape(nb, 3)).reshape(bh, bw, 3)
                for k, f in enumerate(LAYER_MTF):
                    E[..., k] = ndimage.gaussian_filter(E[..., k], soft_px * f, mode="reflect", truncate=3.0)
                E = E.reshape(nb, 3)
            blk = ndimage.gaussian_filter(blk, (soft_px, soft_px, 0), mode="reflect", truncate=3.0)
        fine = coarse = None
        vis = 1.0
        pos = None
        if look.grain > 0 and look.model is not None:
            yy = np.arange(a0, a1, dtype=np.float32)[:, None]
            xx = np.arange(b0, b1, dtype=np.float32)[None, :]
            pos = (np.broadcast_to(yy, (bh, bw)).reshape(-1), np.broadcast_to(xx, (bh, bw)).reshape(-1))
            amp = None
        elif look.grain > 0:
            # seeded by absolute position, so the grain doesn't change with chunking or threads
            rng = np.random.default_rng([look.seed, ys, x0, bh, bw])
            fine, coarse, amp, vis = effects.grain_fields(bh, bw, sigma_px, look.grain_colour, rng)
            fine, coarse, amp = fine.reshape(nb, 3), coarse.reshape(nb, 3), amp.reshape(nb, 1)
        else:
            amp = None
        D_reg = reg_up.rows(a0, a1, b0, b1).reshape(nb, 3) if reg_up is not None else None
        y = look.develop(blk.reshape(nb, 3), fine, coarse, vis, E=E, amp=amp, shape=(bh, bw),
                         adj_px=edge_px, D_regional=D_reg, pos=pos, r_img=r_img).reshape(bh, bw, 3)
        if not before:
            y = apply_skin(y, s.skin_warmth, s.skin_saturation)
        if glare_up is not None:
            y = y + glare_up.rows(a0, a1, b0, b1)
        if sharp_amt != 0:
            y = _scanner_sharpen(y, sharp_r, sharp_amt)
        y = y[ys - a0:ys - a0 + (ye - ys), x0 - b0:x0 - b0 + (x1 - x0)]
        h, w = y.shape[:2]
        n = h * w
        if look.texture != "none":
            y = textures.apply_texture(y, look.texture, look.texture_amount, ys, x0, H, W, look.seed,
                                       low_up=tex_up, rng=np.random.default_rng([look.seed, 99, ys, x0]))
        if defects is not None:
            y = textures.apply_defects(y, slice(ys, ye), slice(x0, x1), defects, look.defects_white)
        if not before:
            y = apply_tone_curve(y, s)
        y = np.clip(y.reshape(n, 3) @ to_out.T, 0.0, 1.0)
        enc = out_space.encode(y).reshape(h, w, 3)
        out[ys - y0:ye - y0] = _quantize(enc, out_dtype)
        if progress:
            with lock:
                done[0] += ye - ys
                progress(done[0] / out_h)

    if len(starts) == 1:
        work(0, starts[0])
    else:
        with ThreadPoolExecutor(max_workers=_workers()) as ex:
            list(ex.map(lambda a: work(*a), enumerate(starts)))
    if cancel is not None and cancel():
        return None

    if with_border and not before and region is None and s.border != "none":
        f = out.astype(np.float32) / _scale(out_dtype)
        f = effects.add_border(f, s.border, look.stock.name, look.stock.iso, s.seed)
        out = _quantize(f, out_dtype)
    return out


LAYER_MTF = (1.45, 1.0, 0.8)
LAYER_SIZE_GRAIN = (0.9, 1.0, 1.3)   # red, green, blue layer grain size (fast blue layer coarsest)    # relative blur of the red, green and blue layers
SCAN_SHARPEN = {"frontier": 0.62, "noritsu": 0.4, "neutral": 0.2, "print": 0.0}


def _scanner_sharpen(y, r, amt):
    """Unsharp mask on lightness, the way lab scanners sharpen: it crisps up grain and edges."""
    v = np.sqrt(np.maximum(y, 0))
    L = v @ LUMA
    d = L - ndimage.gaussian_filter(L, r, mode="reflect", truncate=3.0)
    v = v + (amt * d)[..., None]
    return np.maximum(v, 0) ** 2


SKIN_HUE = np.radians(135.0)   # skin sits here in the (B-Y, R-Y) plane, for every complexion


def apply_skin(y, warmth, sat):
    """Target skin tones only: rotate them rosy <-> golden and change their saturation.

    Skin of every complexion falls in a narrow band of hue (it differs mostly in lightness and
    saturation), so a soft mask on that hue band, moderate chroma and mid lightness picks it out."""
    if abs(warmth) < 0.5 and abs(sat) < 0.5:
        return y
    v = color.srgb_encode(np.clip(y, 0, 1))
    Y = v @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    cr = v[..., 0] - Y
    cb = v[..., 2] - Y
    ang = np.arctan2(cr, cb)
    chroma = np.hypot(cr, cb)
    d = np.angle(np.exp(1j * (ang - SKIN_HUE)))
    m = (np.exp(-(d / np.radians(24)) ** 2)
         * np.clip((chroma - 0.015) / 0.05, 0, 1) * np.clip((0.45 - chroma) / 0.15, 0, 1)
         * np.clip((Y - 0.08) / 0.12, 0, 1) * np.clip((0.97 - Y) / 0.08, 0, 1)).astype(np.float32)
    if not np.any(m > 0.01):
        return y
    rot = np.radians(12.0) * (warmth / 100.0) * m
    k = 1.0 + 0.55 * (sat / 100.0) * m
    a2 = ang + rot
    c2 = chroma * k
    cr2, cb2 = c2 * np.sin(a2), c2 * np.cos(a2)
    r = Y + cr2
    b = Y + cb2
    g = (Y - 0.2126 * r - 0.0722 * b) / 0.7152
    out = np.stack([r, g, b], axis=-1)
    return color.srgb_decode(np.clip(out, 0, 1))


def grain_curve(pct):
    """Grain amount slider (0..200 %) -> strength. 100 % = the stock's own grain. Gentle at the
    bottom for fine control, up to ~2.4x at 200 %, so the whole slider does something visible."""
    v = max(0.0, float(pct)) / 100.0
    return v ** 1.25


_PER_PIXEL_FIELDS = {"grain", "grain_size", "grain_roughness", "seed", "dust", "scratches", "texture",
                     "texture_amount", "sharpen", "curve_rgb", "curve_r", "curve_g", "curve_b", "skin_warmth",
                     "skin_saturation", "border", "softness", "denoise", "fade", "aspect"}


def _frame_key(s, meter_ev, before, scene_referred, H, W):
    d = {k: v for k, v in s.to_dict().items() if k not in _PER_PIXEL_FIELDS}
    return repr((sorted(d.items()), round(float(meter_ev), 4), before, scene_referred, H, W))


def _workers():
    return max(1, min(12, (os.cpu_count() or 2) - 2))


def _low_size(W, H):
    s = effects.LOWRES / max(W, H)
    return max(1, round(W * s)), max(1, round(H * s))


def _scale(dtype):
    if dtype == np.uint8:
        return 255.0
    if dtype == np.uint16:
        return 65535.0
    return 1.0


def _quantize(enc: np.ndarray, dtype) -> np.ndarray:
    if dtype == np.uint8:
        return np.round(enc * 255.0).astype(np.uint8)
    if dtype == np.uint16:
        return np.round(enc * 65535.0).astype(np.uint16)
    return enc.astype(np.float32)


def scene_meter(src: np.ndarray, scene_referred: bool, s: Settings | None = None) -> float:
    if s is not None and s.crop:
        H0, W0 = src.shape[:2]
        a, b, c, d = crop_box(s, W0, H0)
        src = src[b:d, a:c]
    H, W = src.shape[:2]
    low = effects.resize_float(src, _low_size(W, H), resample=effects.Image.BOX) if max(H, W) > effects.LOWRES else src
    return meter(prepare_scene(low, scene_referred))


# ------------------------------------------------------------------ LUT export

def write_cube(path: str, s: Settings, size: int = 33, space: color.RGBSpace = color.SRGB):
    """Export the stock's dye colour and tone (no grain/halation/textures) as a .cube LUT for video."""
    s = s.copy()
    s.grain, s.halation, s.bloom, s.leak, s.vignette, s.border = 0, 0, 0, "none", 0, "none"
    s.dust, s.scratches, s.texture, s.softness = 0, 0, "none", 0
    look = Look(s)
    g = np.linspace(0, 1, size, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    enc = np.stack([r, gg, b], axis=-1).reshape(-1, 3)  # red changes fastest
    lin = space.decode(enc)
    to_w = color.conversion_matrix(color.RGBSpace(space.name, space.primaries, space.white, color.linear, color.linear),
                                   color.WORKING)
    x = display_to_scene(lin @ to_w.T) * np.float32(2 ** s.exposure)
    if not (s.wb_temp is None and s.wb_tint is None):
        x = x * wb_gains_for(s, None)
    y = look.develop(x)
    y = apply_skin(y, s.skin_warmth, s.skin_saturation)
    y = apply_tone_curve(y, s)
    y = np.clip(y @ color.conversion_matrix(color.WORKING, space).T, 0, 1)
    out = space.encode(y)
    with open(path, "w", encoding="ascii") as f:
        f.write(f'TITLE "GrainHause {look.stock.name}"\n')
        f.write(f"LUT_3D_SIZE {size}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n")
        for v in out:
            f.write(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
