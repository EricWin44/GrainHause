"""Batch / command-line developing.

  python -m grainhouse.cli photos/*.CR3 --stock portra400 --exposure 0.5 --grain 120 -o out/
  python -m grainhouse.cli --list
  python -m grainhouse.cli --lut portra400 -o "Portra 400.cube"
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import engine, imageio, settings as gs, stocks
from .settings import Settings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="grainhouse", description="Develop photos like film.")
    ap.add_argument("inputs", nargs="*", help="RAW / TIFF / JPEG files or folders")
    ap.add_argument("-o", "--out", default=None, help="output folder (or .cube path with --lut)")
    ap.add_argument("--list", action="store_true", help="list film stocks")
    ap.add_argument("--lut", metavar="STOCK", help="export a stock as a .cube LUT for video")
    ap.add_argument("--stock", default=None)
    ap.add_argument("--preset", help="name of a look saved in the app")
    ap.add_argument("--use-sidecars", action="store_true", help="use each photo's saved app settings")
    ap.add_argument("--format", choices=["jpg", "tif", "png"], default="jpg")
    ap.add_argument("--space", choices=list(imageio.OUTPUT_SPACES), default="sRGB")
    ap.add_argument("--size", type=int, default=0, help="long edge in px (0 = full)")
    ap.add_argument("--quality", type=int, default=95)
    for name, kind in (("strength", float), ("exposure", float), ("push", float), ("highlights", float),
                       ("shadows", float), ("contrast", float), ("wb-temp", float), ("wb-tint", float),
                       ("dust", float), ("scratches", float), ("skin-warmth", float), ("skin-saturation", float), ("depth", float), ("texture-amount", float), ("softness", float), ("sharpen", float),
                       ("saturation", float), ("fade", float), ("grain", float), ("grain-size", float),
                       ("grain-roughness", float), ("denoise", float), ("halation", float), ("bloom", float),
                       ("vignette", float), ("leak-amount", float), ("leak-seed", int), ("seed", int)):
        ap.add_argument(f"--{name}", type=kind)
    ap.add_argument("--leak", choices=gs.LEAK_STYLES)
    ap.add_argument("--grain-preset", choices=list(gs.GRAIN_PRESETS))
    ap.add_argument("--border", choices=gs.BORDERS)
    ap.add_argument("--lab", choices=["frontier", "noritsu", "neutral", "print"],
                    help="how the film is finished: lab scan or optical RA-4 print")
    ap.add_argument("--texture", choices=["none", "matte", "fiber", "expired", "emulsion"])
    ap.add_argument("--aspect", choices=["1:1", "4:5", "9:16", "3:2", "16:9", "1.91:1"],
                    help="centre-crop to this aspect ratio")
    ap.add_argument("--no-meter", action="store_true", help="don't auto-meter RAW files")
    a = ap.parse_args(argv)

    if a.list:
        for fam, items in stocks.families().items():
            print(fam)
            for st in items:
                print(f"  {st.id:<16} {st.name:<28} {st.blurb}")
        return 0

    base = Settings()
    if a.preset:
        presets = gs.load_presets()
        if a.preset not in presets:
            ap.error(f"no saved look called {a.preset!r}")
        base = presets[a.preset].copy()
    overrides = {}
    for k, v in vars(a).items():
        key = k.replace("-", "_")
        if key in Settings.__dataclass_fields__ and v is not None:
            overrides[key] = v
    if a.stock:
        if a.stock not in stocks.STOCKS:
            ap.error(f"unknown stock {a.stock!r}; see --list")
    if a.no_meter:
        overrides["auto_meter"] = False
    if a.grain_preset:
        g, size, rough = gs.GRAIN_PRESETS[a.grain_preset]
        overrides.setdefault("grain", float(g))
        overrides.setdefault("grain_size", float(size))
        overrides.setdefault("grain_roughness", float(rough))

    def settings_for(path):
        s = (gs.load_sidecar(path) if a.use_sidecars else None) or base.copy()
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    if a.lut:
        s = settings_for("")
        s.stock = a.lut
        out = a.out or f"{stocks.get(a.lut).name}.cube"
        engine.write_cube(out, s)
        print(f"wrote {out}")
        return 0

    files = []
    for p in a.inputs:
        P = Path(p)
        if P.is_dir():
            files += sorted(str(f) for f in P.iterdir() if f.is_file() and imageio.is_supported(str(f)))
        elif P.is_file() and imageio.is_supported(p):
            files.append(p)
    if not files:
        ap.error("no supported input files")

    out_dir = a.out or "."
    os.makedirs(out_dir, exist_ok=True)
    space = imageio.OUTPUT_SPACES[a.space]
    for f in files:
        t = time.time()
        s = settings_for(f)
        src = imageio.load(f)
        lin = src.linear
        if a.size and max(lin.shape[:2]) > a.size:
            lin = imageio.resize_linear(lin, a.size)
        dtype = np.uint16 if a.format == "tif" else np.uint8
        if a.aspect and not s.crop:
            from .widgets import ASPECT_RATIOS, fit_aspect
            s.crop = fit_aspect(ASPECT_RATIOS[a.aspect], lin.shape[1] / lin.shape[0])
        arr = engine.render(lin, s, scene_referred=src.scene_referred, out_space=space, out_dtype=dtype,
                            meta=src.meta)
        out = os.path.join(out_dir, f"{Path(f).stem}_{s.stock}.{a.format}")
        imageio.save(out, arr, space=space, fmt=a.format, quality=a.quality,
                     exif=src.exif if a.format == "jpg" else None)
        print(f"{f} -> {out}  ({time.time() - t:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
