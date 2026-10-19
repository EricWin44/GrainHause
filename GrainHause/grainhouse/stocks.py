"""Film stock library.

Every stock is described physically, the way its datasheet would:

  sens_centers / sens_widths  spectral sensitivity of the red-, green- and
                              blue-sensitive layers (nm)
  cyan_layer                  strength of Fuji's 4th, cyan-sensitive layer
  dyes                        which dye set forms the image (Kodak / Fuji /
                              cine negative dyes, slide dyes, Kodachrome dyes)
  gamma, toe, shoulder_at     per-layer characteristic curve (density per
                              log10 exposure; toe and shoulder in stops)
  interimage                  DIR-coupler interlayer effect (colour saturation
                              formed in the dyes)
  lab_balance                 how the lab printed / scanned it (stops, R G B)
  scan_black                  the lab's black point (tinted, per channel)

Values come from the published character of each film (contrast, latitude,
palette, grain, halation) and its datasheet curves, tuned by eye against known
looks. They are not lab measurements, and names are used only to describe the
look each stock emulates.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

KODAK_SENS = ((646, 549, 452), (27, 30, 26))
FUJI_SENS = ((648, 543, 450), (25, 29, 27))
CINE_SENS = ((652, 551, 456), (25, 29, 26))


@dataclass(frozen=True)
class Stock:
    id: str
    name: str
    family: str
    iso: int
    blurb: str
    kind: str = "neg"                  # "neg", "rev" (slide) or "bw"

    sens_centers: tuple = KODAK_SENS[0]
    sens_widths: tuple = KODAK_SENS[1]
    cyan_layer: float = 0.0
    taking: tuple | None = None        # explicit 3x3 layer-sensitivity matrix (e.g. infrared film)
    dyes: str = "kodak_neg"

    gamma: tuple = (0.62, 0.62, 0.62)
    toe: tuple = (-4.6, -4.6, -4.6)
    shoulder_at: tuple = (8.0, 8.0, 8.0)
    toe_softness: float = 1.2
    shoulder_softness: float = 0.7
    dmax: float = 3.3                  # slides only
    interimage: float = 0.3

    lab_balance: tuple = (0.0, 0.0, 0.0)
    shadow_tint: tuple = (0.0, 0.0, 0.0)
    highlight_tint: tuple = (0.0, 0.0, 0.0)
    scan_black: tuple = (0.008, 0.008, 0.008)
    scan_white: float = 0.975
    scan_contrast: float = 1.45
    paper_gamma: float = 2.7
    default_lab: str = "frontier"
    post_saturation: float = 1.0

    bw_weights: tuple = (0.27, 0.62, 0.11)

    grain: float = 0.035               # density std of the dye clouds at density 1
    grain_size: float = 1.0
    grain_colour: float = 0.36         # 0 = layers share their grain, 1 = independent
    softness: float = 0.7              # film MTF blur, px at 24 MP
    adjacency: float = 1.0             # strength of development edge / regional effects
    halation: float = 22.0
    halation_radius: float = 1.0
    halation_colour: tuple = (1.0, 0.24, 0.05)
    bloom: float = 10.0

    tags: tuple = field(default_factory=tuple)


S = Stock
_STOCKS = [
    # ===================================================================== Kodak
    S("portra160", "Kodak Portra 160", "Kodak", 160,
      "Fine-grain portrait negative. Low contrast, true skin, huge latitude.",
      gamma=(0.56, 0.57, 0.6), toe=(-5.0, -5.0, -4.9), interimage=0.22,
      lab_balance=(0.03, 0.0, -0.04), scan_contrast=1.36, scan_black=(0.010, 0.011, 0.013),
      highlight_tint=(0.02, 0.01, -0.03), grain=0.026, grain_size=0.75, softness=0.6, halation=16),
    S("portra400", "Kodak Portra 400", "Kodak", 400,
      "The wedding standard. Warm peachy skin, olive greens, cyan-leaning skies, loves overexposure.",
      gamma=(0.56, 0.58, 0.62), toe=(-4.8, -4.8, -4.6), interimage=0.26,
      sens_centers=(644, 551, 452), sens_widths=(28, 31, 27),
      lab_balance=(0.05, 0.0, -0.06), scan_contrast=1.38, scan_black=(0.011, 0.012, 0.015),
      shadow_tint=(-0.02, 0.01, 0.04), highlight_tint=(0.04, 0.015, -0.05),
      grain=0.042, grain_size=0.95, softness=0.75, halation=20),
    S("portra800", "Kodak Portra 800", "Kodak", 800,
      "Fast portrait film. Warmer, richer and grainier than 400; greenish shadows.",
      gamma=(0.6, 0.6, 0.64), toe=(-4.3, -4.3, -4.1), interimage=0.32,
      lab_balance=(0.07, 0.01, -0.07), scan_contrast=1.44, scan_black=(0.010, 0.014, 0.014),
      shadow_tint=(-0.03, 0.03, 0.01), highlight_tint=(0.04, 0.01, -0.05),
      grain=0.058, grain_size=1.2, softness=0.85, halation=26),
    S("gold200", "Kodak Gold 200", "Kodak", 200,
      "Golden-hour in a box. Strong yellow-warm cast, saturated oranges, brown blacks.",
      sens_centers=(640, 546, 450), sens_widths=(30, 32, 30), gamma=(0.62, 0.64, 0.66),
      toe=(-4.2, -4.2, -4.0), interimage=0.46, lab_balance=(0.1, 0.05, -0.17),
      scan_contrast=1.5, scan_black=(0.018, 0.013, 0.009), highlight_tint=(0.05, 0.035, -0.07),
      grain=0.046, grain_size=1.0, softness=0.8, halation=22),
    S("ektar100", "Kodak Ektar 100", "Kodak", 100,
      "The most saturated negative. Vivid reds, deep blue skies, reddish skin, fine grain.",
      sens_centers=(650, 550, 454), sens_widths=(23, 26, 24), gamma=(0.68, 0.7, 0.72),
      toe=(-4.0, -4.0, -4.0), interimage=0.72, lab_balance=(0.03, 0.0, -0.02),
      scan_contrast=1.62, scan_black=(0.005, 0.005, 0.007), grain=0.02, grain_size=0.6,
      softness=0.5, halation=12, post_saturation=1.05),
    S("ultramax400", "Kodak UltraMax 400", "Kodak", 400,
      "Drugstore classic. Warm, contrasty, punchy reds and yellows, green-cast shadows.",
      sens_centers=(640, 546, 450), sens_widths=(30, 32, 30), gamma=(0.64, 0.65, 0.68),
      toe=(-4.0, -4.0, -3.8), interimage=0.5, lab_balance=(0.07, 0.03, -0.09),
      scan_contrast=1.56, scan_black=(0.010, 0.015, 0.012),
      shadow_tint=(-0.03, 0.035, 0.0), highlight_tint=(0.03, 0.02, -0.04),
      grain=0.06, grain_size=1.15, softness=0.9, halation=24),
    S("colorplus200", "Kodak ColorPlus 200", "Kodak", 200,
      "Budget film: sunny, slightly faded and soft, warm brown shadows.",
      sens_centers=(638, 545, 450), sens_widths=(31, 33, 31), gamma=(0.6, 0.61, 0.63),
      toe=(-3.9, -3.9, -3.7), interimage=0.22, lab_balance=(0.1, 0.045, -0.12),
      scan_contrast=1.4, scan_black=(0.026, 0.02, 0.014), highlight_tint=(0.03, 0.02, -0.03),
      grain=0.058, grain_size=1.1, softness=1.0, halation=24, post_saturation=0.92),
    S("vision500t", "Kodak Vision3 500T", "Kodak", 500,
      "Hollywood night stock, printed: teal shadows, warm skin, wide and soft.",
      sens_centers=CINE_SENS[0], sens_widths=CINE_SENS[1], dyes="cine_neg",
      gamma=(0.6, 0.62, 0.64), toe=(-5.0, -5.0, -4.8), interimage=0.3, lab_balance=(0.0, 0.0, 0.03),
      scan_contrast=1.38, scan_black=(0.006, 0.012, 0.016),
      shadow_tint=(-0.06, 0.02, 0.06), highlight_tint=(0.05, 0.015, -0.04), default_lab="print",
      grain=0.05, grain_size=1.05, softness=0.85, halation=18),
    S("vision250d", "Kodak Vision3 250D", "Kodak", 250,
      "Daylight motion-picture negative through a print: rich, natural, cinematic.",
      sens_centers=CINE_SENS[0], sens_widths=CINE_SENS[1], dyes="cine_neg",
      gamma=(0.6, 0.62, 0.64), toe=(-5.2, -5.2, -5.0), interimage=0.3, lab_balance=(0.03, 0.0, -0.02),
      scan_contrast=1.4, scan_black=(0.007, 0.01, 0.013), shadow_tint=(-0.03, 0.01, 0.03),
      highlight_tint=(0.03, 0.01, -0.03), default_lab="print",
      grain=0.036, grain_size=0.85, softness=0.7, halation=16),
    S("ektachrome", "Kodak Ektachrome E100", "Kodak", 100,
      "Kodak's slide film. Clean, cool, saturated blues, neutral greys, deep blacks.",
      kind="rev", dyes="kodak_rev", sens_centers=(645, 548, 450), sens_widths=(25, 27, 25),
      gamma=(1.75, 1.75, 1.78), toe=(-3.4, -3.4, -3.5), shoulder_at=(3.0, 3.0, 3.0),
      toe_softness=1.8, shoulder_softness=1.6, dmax=3.3, interimage=0.35,
      lab_balance=(-0.02, 0.0, 0.03), scan_black=(0.002, 0.002, 0.004),
      grain=0.016, grain_size=0.6, softness=0.45, halation=8, bloom=6, default_lab="neutral"),
    S("kodachrome64", "Kodak Kodachrome 64", "Kodak", 64,
      "The legend. Deep reds, golden yellows, dense blues, rich shadows.",
      kind="rev", dyes="kodachrome", sens_centers=(640, 545, 446), sens_widths=(24, 28, 26),
      gamma=(1.85, 1.8, 1.82), toe=(-3.1, -3.1, -3.2), shoulder_at=(2.7, 2.7, 2.7),
      toe_softness=1.8, shoulder_softness=1.5, dmax=3.5, interimage=0.45,
      lab_balance=(0.05, 0.01, -0.04), scan_black=(0.003, 0.002, 0.002),
      grain=0.02, grain_size=0.6, softness=0.5, halation=8, bloom=6, default_lab="neutral"),
    S("trix400", "Kodak Tri-X 400", "Kodak", 400,
      "Gritty photojournalist classic. Punchy mids, crisp, real grain.",
      kind="bw", gamma=(0.68, 0.68, 0.68), toe=(-4.2,) * 3, bw_weights=(0.25, 0.58, 0.17),
      scan_contrast=1.65, scan_black=(0.004,) * 3, grain=0.06, grain_size=1.3, grain_colour=0.0,
      softness=0.7, halation=10, bloom=8),
    S("tmax100", "Kodak T-Max 100", "Kodak", 100,
      "Ultra-fine T-grain. Smooth, sharp, modern.",
      kind="bw", gamma=(0.7,) * 3, toe=(-4.4,) * 3, bw_weights=(0.26, 0.6, 0.14),
      scan_contrast=1.6, scan_black=(0.003,) * 3, grain=0.026, grain_size=0.7, grain_colour=0.0,
      softness=0.45, halation=6, bloom=5),
    S("tmax3200", "Kodak T-Max P3200", "Kodak", 3200,
      "Low-light monster. Heavy grain, lifted blacks, moody.",
      kind="bw", gamma=(0.62,) * 3, toe=(-3.8,) * 3, bw_weights=(0.3, 0.55, 0.15),
      scan_contrast=1.5, scan_black=(0.016,) * 3, grain=0.12, grain_size=1.9, grain_colour=0.0,
      softness=1.1, halation=12, bloom=12),
    S("proimage100", "Kodak ProImage 100", "Kodak", 100,
      "Warm, saturated everyday film made for hot climates. Golden skin, punchy reds, fine grain.",
      sens_centers=(642, 548, 451), sens_widths=(28, 30, 28), gamma=(0.64, 0.65, 0.68),
      toe=(-4.3, -4.3, -4.1), interimage=0.42, lab_balance=(0.07, 0.03, -0.09),
      scan_contrast=1.52, scan_black=(0.012, 0.011, 0.009), highlight_tint=(0.035, 0.02, -0.045),
      grain=0.03, grain_size=0.8, softness=0.65, halation=16),
    S("max800", "Kodak Max 800", "Kodak", 800,
      "Point-and-shoot party film. Warm, contrasty, chunky grain, deep orange-brown shadows.",
      sens_centers=(640, 546, 450), sens_widths=(31, 33, 31), gamma=(0.66, 0.66, 0.7),
      toe=(-3.7, -3.7, -3.5), interimage=0.42, lab_balance=(0.09, 0.03, -0.1),
      scan_contrast=1.6, scan_black=(0.018, 0.013, 0.01),
      shadow_tint=(0.01, 0.015, -0.02), highlight_tint=(0.04, 0.02, -0.04),
      grain=0.075, grain_size=1.4, softness=1.1, halation=26, bloom=12),
    S("vision50d", "Kodak Vision3 50D", "Kodak", 50,
      "The finest-grained cine negative, printed. Clean, rich and natural with big latitude.",
      sens_centers=CINE_SENS[0], sens_widths=CINE_SENS[1], dyes="cine_neg",
      gamma=(0.6, 0.62, 0.64), toe=(-5.2, -5.2, -5.0), interimage=0.32, lab_balance=(0.02, 0.0, -0.015),
      scan_contrast=1.42, scan_black=(0.006, 0.009, 0.012), shadow_tint=(-0.02, 0.01, 0.02),
      highlight_tint=(0.025, 0.01, -0.025), default_lab="print",
      grain=0.02, grain_size=0.6, softness=0.5, halation=12),
    S("elitechrome100", "Kodak Elite Chrome 100", "Kodak", 100,
      "Kodak's consumer slide film. Bright, slightly cool, vivid blues and greens.",
      kind="rev", dyes="kodak_rev", sens_centers=(646, 549, 451), sens_widths=(25, 27, 25),
      gamma=(1.8, 1.8, 1.82), toe=(-3.3, -3.3, -3.4), shoulder_at=(2.9, 2.9, 2.9),
      toe_softness=1.8, shoulder_softness=1.6, dmax=3.3, interimage=0.4,
      lab_balance=(-0.025, 0.0, 0.035), scan_black=(0.002, 0.002, 0.004),
      grain=0.017, grain_size=0.6, softness=0.45, halation=8, bloom=6, default_lab="neutral",
      post_saturation=1.04),
    S("doublex5222", "Kodak Double-X 5222", "Kodak", 250,
      "The classic black & white motion-picture stock. Contrasty, gritty, cinematic.",
      kind="bw", gamma=(0.72,) * 3, toe=(-4.0,) * 3, bw_weights=(0.3, 0.56, 0.14),
      scan_contrast=1.72, scan_black=(0.005,) * 3, grain=0.07, grain_size=1.25, grain_colour=0.0,
      softness=0.8, halation=12, bloom=8),
    S("aerochrome", "Kodak Aerochrome III", "Kodak", 400,
      "Infrared false-colour film. Foliage turns red and magenta, skies deep blue, skin pale and odd.",
      taking=((0.95, 0.9, -0.62), (1.0, 0.0, 0.0), (0.05, 0.9, 0.05)),
      gamma=(0.66, 0.64, 0.66), toe=(-4.0, -4.0, -4.0), interimage=0.45, lab_balance=(0.0, 0.0, 0.0),
      scan_contrast=1.6, scan_black=(0.008, 0.006, 0.012),
      grain=0.05, grain_size=1.1, softness=0.9, halation=14, post_saturation=1.05),
    # ===================================================================== Fujifilm
    S("superia400", "Fujifilm Superia X-TRA 400", "Fujifilm", 400,
      "Cool consumer Fuji. Teal-leaning greens, magenta-red, green-cyan shadows.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.2, dyes="fuji_neg",
      gamma=(0.63, 0.66, 0.64), toe=(-4.1, -4.1, -4.0), interimage=0.52,
      lab_balance=(-0.02, 0.03, 0.02), scan_contrast=1.52, scan_black=(0.008, 0.016, 0.015),
      shadow_tint=(-0.04, 0.04, 0.03), highlight_tint=(0.01, 0.0, -0.01),
      grain=0.058, grain_size=1.1, softness=0.9, halation=22),
    S("pro400h", "Fujifilm Pro 400H", "Fujifilm", 400,
      "Airy pastels, mint greens, pink skin, cool lifted shadows. Shoot it a stop over.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.27, dyes="fuji_neg",
      gamma=(0.55, 0.58, 0.56), toe=(-4.8, -4.8, -4.7), interimage=0.16,
      lab_balance=(-0.03, 0.025, 0.035), scan_contrast=1.3, scan_black=(0.014, 0.019, 0.022),
      shadow_tint=(-0.03, 0.01, 0.04), highlight_tint=(-0.03, 0.015, 0.0),
      grain=0.045, grain_size=1.0, softness=0.8, halation=18, post_saturation=0.88),
    S("fujic200", "Fujifilm C200", "Fujifilm", 200,
      "Everyday Fuji: green cast, gentle contrast, honest colour.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.15, dyes="fuji_neg",
      gamma=(0.62, 0.65, 0.63), toe=(-4.1, -4.1, -4.0), interimage=0.3,
      lab_balance=(-0.015, 0.04, 0.0), scan_contrast=1.48, scan_black=(0.009, 0.016, 0.012),
      shadow_tint=(-0.03, 0.035, 0.01), grain=0.052, grain_size=1.05, softness=0.9, halation=20),
    S("fuji400", "Fujifilm 400", "Fujifilm", 400,
      "Fuji's current 400: warm-neutral, punchy, a little contrasty.",
      sens_centers=(642, 547, 451), sens_widths=(29, 31, 29), dyes="kodak_neg",
      gamma=(0.63, 0.64, 0.67), toe=(-4.1, -4.1, -3.9), interimage=0.38,
      lab_balance=(0.05, 0.025, -0.06), scan_contrast=1.52, scan_black=(0.010, 0.013, 0.012),
      shadow_tint=(-0.02, 0.025, 0.01), grain=0.056, grain_size=1.1, softness=0.9, halation=22),
    S("velvia50", "Fujifilm Velvia 50", "Fujifilm", 50,
      "Landscape slide. Extreme greens, deep magenta-blue skies, dense saturated shadows.",
      kind="rev", dyes="fuji_rev", sens_centers=(652, 545, 448), sens_widths=(21, 23, 23),
      gamma=(1.95, 2.0, 1.95), toe=(-3.0, -3.0, -3.1), shoulder_at=(2.6, 2.6, 2.6),
      toe_softness=2.0, shoulder_softness=1.7, dmax=3.6, interimage=0.6,
      lab_balance=(0.0, -0.02, 0.03), scan_black=(0.002, 0.001, 0.003),
      grain=0.013, grain_size=0.5, softness=0.4, halation=6, bloom=5, default_lab="neutral",
      post_saturation=1.06),
    S("provia100f", "Fujifilm Provia 100F", "Fujifilm", 100,
      "Neutral professional slide. Clean colour, crisp contrast.",
      kind="rev", dyes="fuji_rev", sens_centers=(646, 546, 450), sens_widths=(25, 27, 26),
      gamma=(1.75, 1.75, 1.75), toe=(-3.3, -3.3, -3.3), shoulder_at=(2.9, 2.9, 2.9),
      toe_softness=1.8, shoulder_softness=1.6, dmax=3.4, interimage=0.32,
      lab_balance=(-0.005, 0.0, 0.01), scan_black=(0.002, 0.002, 0.003),
      grain=0.014, grain_size=0.55, softness=0.4, halation=6, bloom=5, default_lab="neutral"),
    S("acros100", "Fujifilm Acros 100 II", "Fujifilm", 100,
      "Fine-grain Fuji mono. Smooth, silvery, deep skies.",
      kind="bw", gamma=(0.7,) * 3, toe=(-4.4,) * 3, bw_weights=(0.22, 0.6, 0.18),
      scan_contrast=1.6, scan_black=(0.003,) * 3, grain=0.03, grain_size=0.75, grain_colour=0.0,
      softness=0.5, halation=6, bloom=5),
    S("pro160ns", "Fujifilm Pro 160NS", "Fujifilm", 160,
      "Soft, cool-neutral studio film. Clean whites, gentle greens, very flattering skin.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.25, dyes="fuji_neg",
      gamma=(0.56, 0.58, 0.57), toe=(-4.9, -4.9, -4.8), interimage=0.18,
      lab_balance=(-0.02, 0.015, 0.025), scan_contrast=1.32, scan_black=(0.011, 0.015, 0.017),
      shadow_tint=(-0.02, 0.01, 0.03), grain=0.03, grain_size=0.8, softness=0.65, halation=14,
      post_saturation=0.9),
    S("natura1600", "Fujifilm Natura 1600", "Fujifilm", 1600,
      "Cult low-light film. Soft, cool-green shadows, gentle colour, visible grain.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.22, dyes="fuji_neg",
      gamma=(0.58, 0.61, 0.6), toe=(-3.8, -3.8, -3.7), interimage=0.22,
      lab_balance=(-0.03, 0.035, 0.02), scan_contrast=1.38, scan_black=(0.014, 0.022, 0.02),
      shadow_tint=(-0.04, 0.035, 0.025), grain=0.085, grain_size=1.5, softness=1.1, halation=24,
      bloom=12, post_saturation=0.9),
    S("superia200", "Fujifilm Superia 200", "Fujifilm", 200,
      "Bright consumer Fuji. Vivid greens and magentas, clean blues, fine grain.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.18, dyes="fuji_neg",
      gamma=(0.63, 0.66, 0.64), toe=(-4.2, -4.2, -4.1), interimage=0.48,
      lab_balance=(-0.015, 0.025, 0.015), scan_contrast=1.52, scan_black=(0.008, 0.014, 0.013),
      shadow_tint=(-0.03, 0.035, 0.02), grain=0.045, grain_size=0.95, softness=0.8, halation=20),
    S("velvia100", "Fujifilm Velvia 100", "Fujifilm", 100,
      "Velvia's faster sibling. Very saturated, a touch warmer and less magenta than 50.",
      kind="rev", dyes="fuji_rev", sens_centers=(650, 545, 449), sens_widths=(22, 24, 24),
      gamma=(1.9, 1.95, 1.9), toe=(-3.1, -3.1, -3.2), shoulder_at=(2.7, 2.7, 2.7),
      toe_softness=2.0, shoulder_softness=1.7, dmax=3.5, interimage=0.55,
      lab_balance=(0.02, -0.01, 0.0), scan_black=(0.002, 0.002, 0.003),
      grain=0.014, grain_size=0.55, softness=0.4, halation=6, bloom=5, default_lab="neutral",
      post_saturation=1.04),
    S("astia100f", "Fujifilm Astia 100F", "Fujifilm", 100,
      "Soft-contrast portrait slide. Gentle skin, restrained saturation, open shadows.",
      kind="rev", dyes="fuji_rev", sens_centers=(646, 546, 450), sens_widths=(26, 28, 27),
      gamma=(1.55, 1.55, 1.55), toe=(-3.6, -3.6, -3.6), shoulder_at=(3.1, 3.1, 3.1),
      toe_softness=1.7, shoulder_softness=1.5, dmax=3.2, interimage=0.22,
      lab_balance=(0.01, 0.0, -0.005), scan_black=(0.004, 0.004, 0.005),
      grain=0.014, grain_size=0.55, softness=0.45, halation=6, bloom=6, default_lab="neutral",
      post_saturation=0.93),
    S("eterna500t", "Fujifilm Eterna 500T", "Fujifilm", 500,
      "Fuji's cine negative: low saturation, soft contrast, cyan-green shadows. Very 2000s cinema.",
      sens_centers=FUJI_SENS[0], sens_widths=FUJI_SENS[1], cyan_layer=0.2, dyes="fuji_neg",
      gamma=(0.56, 0.58, 0.58), toe=(-5.0, -5.0, -4.8), interimage=0.15,
      lab_balance=(-0.02, 0.015, 0.03), scan_contrast=1.32, scan_black=(0.006, 0.013, 0.015),
      shadow_tint=(-0.05, 0.03, 0.035), highlight_tint=(0.02, 0.01, -0.02), default_lab="print",
      grain=0.048, grain_size=1.0, softness=0.85, halation=14, post_saturation=0.82),
    S("neopan400", "Fujifilm Neopan 400", "Fujifilm", 400,
      "Fuji's classic 400 black & white. Clean mid-tones, moderate contrast, tight grain.",
      kind="bw", gamma=(0.66,) * 3, toe=(-4.3,) * 3, bw_weights=(0.24, 0.6, 0.16),
      scan_contrast=1.55, scan_black=(0.004,) * 3, grain=0.05, grain_size=1.1, grain_colour=0.0,
      softness=0.65, halation=8, bloom=6),
    # ===================================================================== CineStill
    S("cinestill800t", "CineStill 800T", "CineStill", 800,
      "Tungsten cine film without remjet: teal nights, cyan skies, glowing red halos.",
      sens_centers=CINE_SENS[0], sens_widths=CINE_SENS[1], dyes="cine_neg",
      gamma=(0.6, 0.62, 0.66), toe=(-4.3, -4.3, -4.1), interimage=0.32,
      lab_balance=(-0.06, 0.0, 0.09), scan_contrast=1.46, scan_black=(0.006, 0.014, 0.02),
      shadow_tint=(-0.07, 0.03, 0.07), highlight_tint=(0.06, 0.0, -0.05),
      grain=0.064, grain_size=1.25, softness=1.0,
      halation=82, halation_radius=1.4, halation_colour=(1.0, 0.16, 0.04), bloom=18),
    S("cinestill50d", "CineStill 50D", "CineStill", 50,
      "Daylight cine film. Clean, cool, very fine grain, red-glowing highlights.",
      sens_centers=CINE_SENS[0], sens_widths=CINE_SENS[1], dyes="cine_neg",
      gamma=(0.6, 0.62, 0.64), toe=(-4.8, -4.8, -4.6), interimage=0.32,
      lab_balance=(-0.015, 0.0, 0.025), scan_contrast=1.46, scan_black=(0.006, 0.009, 0.012),
      highlight_tint=(0.02, 0.0, -0.01), grain=0.026, grain_size=0.7, softness=0.6,
      halation=62, halation_radius=1.25, halation_colour=(1.0, 0.2, 0.05), bloom=12),
    S("cinestill400d", "CineStill 400D", "CineStill", 400,
      "Dynamic daylight cine stock. Warm, saturated, glowing highlights with red halos.",
      sens_centers=CINE_SENS[0], sens_widths=CINE_SENS[1], dyes="cine_neg",
      gamma=(0.62, 0.63, 0.66), toe=(-4.5, -4.5, -4.3), interimage=0.4,
      lab_balance=(0.04, 0.01, -0.04), scan_contrast=1.5, scan_black=(0.008, 0.011, 0.013),
      shadow_tint=(-0.03, 0.01, 0.03), highlight_tint=(0.04, 0.015, -0.03),
      grain=0.048, grain_size=1.0, softness=0.85,
      halation=70, halation_radius=1.3, halation_colour=(1.0, 0.18, 0.05), bloom=14),
    S("bwxx", "CineStill BwXX", "CineStill", 250,
      "Double-X cine stock for still cameras. Punchy, gritty, classic Hollywood black & white.",
      kind="bw", gamma=(0.7,) * 3, toe=(-4.1,) * 3, bw_weights=(0.3, 0.56, 0.14),
      scan_contrast=1.68, scan_black=(0.005,) * 3, grain=0.068, grain_size=1.25, grain_colour=0.0,
      softness=0.8, halation=16, bloom=10),
]

NEUTRAL = Stock("neutral", "Digital (no film)", "Digital", 100,
                "Clean digital rendering. Useful as a base for grain, halation and textures only.",
                grain=0.03, halation=0, bloom=0, softness=0.0)

STOCKS = {s.id: s for s in _STOCKS}
STOCKS[NEUTRAL.id] = NEUTRAL
ORDER = [s.id for s in _STOCKS] + [NEUTRAL.id]


def get(stock_id: str) -> Stock:
    return STOCKS.get(stock_id, STOCKS["portra400"])


def families():
    out: dict[str, list[Stock]] = {}
    for sid in ORDER:
        s = STOCKS[sid]
        out.setdefault(s.family, []).append(s)
    return out


def with_overrides(stock: Stock, **kw) -> Stock:
    return replace(stock, **kw)
