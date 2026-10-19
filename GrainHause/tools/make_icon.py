"""GrainHause app icon: a chrome GH monogram on black.

python tools/make_icon.py               ->  assets/icon.png, assets/icon.icns, assets/icon.ico
python tools/make_icon.py preview.png   (just writes a preview)
Font: Unbounded (SIL Open Font License), in tools/fonts/.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

S = 4096
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets")
FONT = os.path.join(HERE, "fonts", "Unbounded.ttf")


def squircle(pad):
    w = S - 2 * pad
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([pad, pad, pad + w, pad + w], radius=int(w * 0.225), fill=255)
    return m


def chrome(top, bottom):
    """Polished chrome: bright sky, dark horizon line, bright floor, with a soft horizontal sheen."""
    y = (np.arange(S, dtype=np.float32) - top) / max(1.0, bottom - top)
    stops = [(0.00, 250), (0.30, 222), (0.47, 150), (0.50, 92), (0.53, 128), (0.75, 200), (1.00, 246)]
    v = np.interp(np.clip(y, 0, 1), [s[0] for s in stops], [s[1] for s in stops])[:, None]
    sheen = 1 + 0.08 * np.cos((np.arange(S) / S - 0.35) * np.pi * 2.2)[None, :]
    v = np.clip(v * sheen, 0, 255)
    return Image.fromarray(np.stack([v * 0.985, v * 0.995, v * 1.02], -1).clip(0, 255).astype(np.uint8))


def make(preview=None):
    pad = int(S * 0.098)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # deep black tile with a faint vignette and fine grain
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32) / S
    vign = 1 - 0.55 * ((xx - 0.5) ** 2 + (yy - 0.42) ** 2)
    rng = np.random.default_rng(3)
    g = np.asarray(Image.fromarray(rng.standard_normal((S // 4, S // 4)).astype(np.float32)).resize((S, S), Image.BICUBIC))
    v = np.clip(10 + 14 * vign + 2.2 * g, 0, 255)
    img.paste(Image.fromarray(np.repeat(v[..., None], 3, 2).astype(np.uint8)), (0, 0), squircle(pad))

    f = ImageFont.truetype(FONT, int(S * 0.36))
    try:
        f.set_variation_by_axes([900])
    except Exception:
        pass
    d = ImageDraw.Draw(img)
    bb = d.textbbox((0, 0), "GH", font=f)
    x = (S - (bb[2] - bb[0])) / 2 - bb[0]
    y = (S - (bb[3] - bb[1])) / 2 - bb[1]
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).text((x, y), "GH", font=f, fill=255)
    metal = chrome(y + bb[1], y + bb[3])

    # bevel: light from above on top edges, shadow on bottom edges
    edge = mask.filter(ImageFilter.GaussianBlur(S * 0.004))
    up = ImageChops.subtract(edge, ImageChops.offset(edge, 0, int(S * 0.006)))
    down = ImageChops.subtract(edge, ImageChops.offset(edge, 0, -int(S * 0.006)))
    metal = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), metal, up.point(lambda a: int(a * 0.85)))
    metal = Image.composite(Image.new("RGB", (S, S), (40, 40, 44)), metal, down.point(lambda a: int(a * 0.7)))

    img.paste((150, 170, 200, 255), (0, 0), mask.filter(ImageFilter.GaussianBlur(S * 0.035)).point(lambda a: int(a * 0.22)))
    img.paste(metal, (0, 0), mask)
    ring = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(9)), mask).point(lambda a: int(a * 0.5))
    img.paste((20, 20, 24, 255), (0, 0), ring)

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), squircle(pad))
    final = out.resize((1024, 1024), Image.LANCZOS)
    if preview:
        final.save(preview)
        return final
    os.makedirs(OUT, exist_ok=True)
    final.save(os.path.join(OUT, "icon.png"))
    final.save(os.path.join(OUT, "icon.icns"))
    final.save(os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                     (128, 128), (256, 256)])
    return final


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else None)
    print("ok")
