import numpy as np
import pytest

from grainhouse import color, engine, imageio, stocks
from grainhouse.settings import Settings

CLEAN = dict(grain=0, halation=0, bloom=0)


def ramp_image():
    x = np.linspace(-6, 6, 256, dtype=np.float32)
    lin = 0.18 * np.exp2(x)
    img = np.repeat(np.repeat(lin[None, :, None], 8, axis=0), 3, axis=2)
    return img


def test_strength_zero_reproduces_display_input():
    rng = np.random.default_rng(0)
    enc = rng.random((64, 64, 3)).astype(np.float32)
    lin = color.apply_matrix(color.srgb_decode(enc), color.conversion_matrix(color.SRGB, color.WORKING))
    out = engine.render(lin, Settings(strength=0, **CLEAN), scene_referred=False, out_dtype=np.float32)
    assert np.abs(out - enc).mean() < 2 / 255


@pytest.mark.parametrize("sid", stocks.ORDER)
def test_every_stock_is_monotonic_and_in_range(sid):
    out = engine.render(ramp_image(), Settings(stock=sid, **CLEAN), scene_referred=True, out_dtype=np.float32,
                        meter_ev=0.0)
    lum = out[0] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    assert np.all(np.diff(lum) >= -1e-4), sid
    assert out.min() >= 0 and out.max() <= 1


def test_neutral_stock_keeps_grey_grey():
    out = engine.render(ramp_image(), Settings(stock="neutral", **CLEAN), scene_referred=True, meter_ev=0.0)
    assert np.abs(out[0, :, 0] - out[0, :, 2]).max() < 1e-3


def test_exposure_brightens():
    a = engine.render(ramp_image(), Settings(exposure=-1, **CLEAN), scene_referred=True, meter_ev=0.0)
    b = engine.render(ramp_image(), Settings(exposure=1, **CLEAN), scene_referred=True, meter_ev=0.0)
    assert b.mean() > a.mean()


def test_chunking_does_not_change_result():
    rng = np.random.default_rng(1)
    img = (rng.random((300, 200, 3)) * 0.5).astype(np.float32)
    s = Settings(halation=60, bloom=30, vignette=40, leak="corner_glow", grain=0)
    a = engine.render(img, s, scene_referred=True, chunk_rows=64, meter_ev=0.0)
    b = engine.render(img, s, scene_referred=True, chunk_rows=1000, meter_ev=0.0)
    assert np.abs(a - b).max() < 1e-5


def test_loupe_region_matches_full_render():
    rng = np.random.default_rng(2)
    img = (rng.random((200, 300, 3)) * 0.5).astype(np.float32)
    s = Settings(halation=60, vignette=30, grain=0)
    full = engine.render(img, s, scene_referred=True, meter_ev=0.0)
    crop = engine.render(img, s, scene_referred=True, meter_ev=0.0, region=(50, 40, 150, 120))
    assert np.abs(full[40:120, 50:150] - crop).max() < 1e-5


@pytest.mark.parametrize("border", ["scan", "white", "strip35", "medium", "instant"])
def test_borders_grow_the_image(border):
    img = np.full((120, 180, 3), 0.1, np.float32)
    out = engine.render(img, Settings(border=border, **CLEAN), scene_referred=True, meter_ev=0.0)
    assert out.shape[0] > 120 and out.shape[1] > 180


def test_icc_profiles_are_readable():
    for name, sp in imageio.OUTPUT_SPACES.items():
        desc = imageio.icc_description(imageio.make_icc(sp))
        assert color.space_from_icc_description(desc).name == sp.name


def test_tiff_roundtrip(tmp_path):
    img = (np.random.default_rng(3).random((40, 50, 3)) * 65535).astype(np.uint16)
    p = tmp_path / "x-Edit.tif"
    imageio.save(str(p), img, space=color.PROPHOTO)
    src = imageio.load(str(p))
    assert src.space.name == "ProPhoto RGB" and src.bit_depth == 16 and src.linear.shape == (40, 50, 3)


def test_lut(tmp_path):
    p = tmp_path / "x.cube"
    engine.write_cube(str(p), Settings(stock="gold200"), size=9)
    lines = p.read_text().splitlines()
    assert sum(1 for l in lines if l[:1].isdigit()) == 9 ** 3


def test_white_balance_roundtrip():
    for T, tint in [(3200, 0), (6500, 10), (2850, -20)]:
        t2, n2 = color.wb_from_gains(color.wb_gains(T, tint))
        assert abs(t2 - T) / T < 0.02 and abs(n2 - tint) < 2


def test_tungsten_wb_cools_the_image():
    img = np.full((40, 40, 3), 0.18, np.float32)
    a = engine.render(img, Settings(stock="neutral", **CLEAN), scene_referred=True, meter_ev=0.0)
    b = engine.render(img, Settings(stock="neutral", wb_temp=2850, wb_tint=0, **CLEAN), scene_referred=True,
                      meter_ev=0.0)
    assert b[..., 2].mean() > a[..., 2].mean() and b[..., 0].mean() < a[..., 0].mean()


def test_tone_curve_and_crop():
    img = np.full((100, 200, 3), 0.18, np.float32)
    flat = engine.render(img, Settings(**CLEAN), scene_referred=True, meter_ev=0.0)
    bright = engine.render(img, Settings(curve_rgb=[[0, 0], [0.5, 0.7], [1, 1]], **CLEAN), scene_referred=True,
                           meter_ev=0.0)
    assert bright.mean() > flat.mean()
    cropped = engine.render(img, Settings(crop=[0.25, 0.0, 0.75, 1.0], **CLEAN), scene_referred=True, meter_ev=0.0)
    assert cropped.shape[:2] == (100, 100)


def test_dust_is_the_same_at_every_resolution():
    from grainhouse import textures
    a = textures.defect_layers(400, 600, 3, 60.0, 40.0, True)[0]
    b = textures.defect_layers(800, 1200, 3, 60.0, 40.0, True)[0]
    small_b = b.reshape(400, 2, 600, 2).mean(axis=(1, 3))
    assert np.corrcoef(a.ravel(), small_b.ravel())[0, 1] > 0.5


@pytest.mark.parametrize("lab", ["frontier", "noritsu", "neutral", "print"])
def test_labs_keep_mid_grey_neutral(lab):
    from grainhouse import film
    m = film.get_model("portra160", lab)
    y = m.lut(m.layer_density(np.zeros((1, 3))).astype(np.float32))[0]
    assert abs(float(y @ color.LUMA_2020) - 0.18) < 0.02
    assert (y.max() - y.min()) / y.mean() < 0.12



def test_sharpen_slider_monotonic():
    from scipy import ndimage
    rng = np.random.default_rng(3)
    img = ndimage.gaussian_filter(rng.random((300, 400, 3)).astype(np.float32), (1.5, 1.5, 0)) * 0.5
    detail = []
    for v in (-100, -40, 0, 40, 100):
        o = engine.render(img, Settings(stock="portra400", grain=0, sharpen=v), scene_referred=True,
                          meter_ev=0.0)
        L = o.mean(axis=2)
        detail.append(float((L - ndimage.gaussian_filter(L, 2)).std()))
    assert all(b > a for a, b in zip(detail, detail[1:])), detail
