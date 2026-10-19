"""Develop settings, sidecar files and user presets."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import stocks

LEAK_STYLES = ["none", "edge_fog", "corner_glow", "frame_burn", "full_veil", "streak"]
LEAK_LABELS = {"none": "None", "edge_fog": "Edge fog", "corner_glow": "Corner glow",
               "frame_burn": "Frame burn", "full_veil": "Full veil", "streak": "Light streak"}
BORDERS = ["none", "scan", "strip35", "medium", "instant", "white"]
BORDER_LABELS = {"none": "None", "scan": "Scan edge", "white": "White print",
                 "strip35": "35mm strip", "medium": "Medium format",
                 "instant": "Instant print"}
ASPECTS = ["original", "free", "1:1", "4:5", "9:16", "3:2", "16:9", "1.91:1"]
ASPECT_LABELS = {"original": "Original", "free": "Free", "1:1": "1:1 Square", "4:5": "4:5 Instagram portrait",
                 "9:16": "9:16 Story / Reel", "3:2": "3:2 Film frame", "16:9": "16:9 Wide",
                 "1.91:1": "1.91:1 Instagram landscape"}
WB_PRESETS = {"As shot": None, "Daylight": (5500, 0), "Cloudy": (6500, 8), "Shade": (7500, 10),
              "Tungsten": (2850, 0), "Fluorescent": (3800, 20), "Flash": (5500, 0)}
IDENTITY_CURVE = [[0.0, 0.0], [1.0, 1.0]]
GRAIN_PRESETS = {   # amount %, size %, roughness
    "Minimal": (35, 80, 25),
    "Fine": (65, 90, 40),
    "Classic": (100, 100, 50),
    "Pronounced": (145, 118, 62),
    "Heavy": (200, 140, 78),
}


@dataclass
class Settings:
    stock: str = "portra400"
    strength: float = 100.0      # 0..100, how much of the stock's colour/tone
    exposure: float = 0.0        # stops, on top of metering
    auto_meter: bool = True      # meter RAW files like a film camera would
    push: float = 0.0            # -2..+2 stops of push/pull processing
    denoise: float = 0.0         # 0..100, removes digital colour noise before the film
    wb_temp: float | None = None # Kelvin; None = as shot
    wb_tint: float | None = None # -100..100 (+ = magenta); None = as shot
    highlights: float = 0.0      # -100..100
    shadows: float = 0.0         # -100..100
    contrast: float = 0.0        # -100..100
    saturation: float = 0.0      # -100..100
    fade: float = 0.0            # 0..100
    skin_warmth: float = 0.0     # -100 rosy .. +100 golden
    skin_saturation: float = 0.0 # -100..100
    grain: float | None = None   # 0..200 (% of the stock's grain); None = 100
    grain_size: float | None = None  # 50..200 (%); None = 100
    grain_roughness: float = 50.0    # 0..100, how clumpy / irregular the grain is
    halation: float | None = None    # 0..100; None = stock default
    bloom: float | None = None       # 0..100; None = stock default
    vignette: float = 0.0        # -100..100
    leak: str = "none"
    leak_amount: float = 50.0
    leak_seed: int = 1
    border: str = "none"
    lab: str | None = None       # lab process; None = the stock's usual one
    softness: float | None = None  # % of the stock's film softness; None = 100
    sharpen: float = 0.0         # -100 (softer) .. +100 (sharper), on top of the lab scan's sharpening
    depth: float = 100.0         # % of the film's development (adjacency) effects
    dust: float = 0.0
    scratches: float = 0.0
    texture: str = "none"
    texture_amount: float = 50.0
    curve_rgb: list = field(default_factory=lambda: [[0.0, 0.0], [1.0, 1.0]])
    curve_r: list = field(default_factory=lambda: [[0.0, 0.0], [1.0, 1.0]])
    curve_g: list = field(default_factory=lambda: [[0.0, 0.0], [1.0, 1.0]])
    curve_b: list = field(default_factory=lambda: [[0.0, 0.0], [1.0, 1.0]])
    crop: list | None = None     # [x0, y0, x1, y1] normalised
    aspect: str = "original"
    seed: int = 7

    # ---- resolved values (stock defaults applied)
    def grain_pct(self):
        return 100.0 if self.grain is None else self.grain

    def grain_size_pct(self):
        return 100.0 if self.grain_size is None else self.grain_size

    def halation_value(self):
        return stocks.get(self.stock).halation if self.halation is None else self.halation

    def lab_value(self):
        return self.lab or stocks.get(self.stock).default_lab

    def softness_pct(self):
        return 100.0 if self.softness is None else self.softness

    def bloom_value(self):
        return stocks.get(self.stock).bloom if self.bloom is None else self.bloom

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def copy(self) -> "Settings":
        import copy as _c
        return _c.deepcopy(self)


# ------------------------------------------------------------------ sidecars

SIDECAR_SUFFIX = ".grainhouse.json"


def sidecar_path(image_path: str) -> str:
    return image_path + SIDECAR_SUFFIX


def load_sidecar(image_path: str) -> Settings | None:
    p = sidecar_path(image_path)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return Settings.from_dict(json.load(f).get("settings", {}))
        except Exception:
            return None
    return None


def save_sidecar(image_path: str, s: Settings):
    try:
        with open(sidecar_path(image_path), "w", encoding="utf-8") as f:
            json.dump({"app": "GrainHause", "version": 1, "settings": s.to_dict()}, f, indent=2)
    except OSError:
        pass  # read-only folder: editing still works, it just isn't remembered


# ------------------------------------------------------------------ presets

def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "GrainHause"
    old = base / "Grainhouse"
    if not d.exists() and old.exists():
        import shutil
        try:
            shutil.copytree(old, d)        # carry saved looks and the photo list over from the old name
        except OSError:
            pass
    d.mkdir(parents=True, exist_ok=True)
    return d


def presets_path() -> Path:
    return config_dir() / "presets.json"


def load_presets() -> dict[str, Settings]:
    p = presets_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {k: Settings.from_dict(v) for k, v in data.items()}
    except Exception:
        return {}


def save_presets(presets: dict[str, Settings]):
    presets_path().write_text(json.dumps({k: v.to_dict() for k, v in presets.items()}, indent=2),
                              encoding="utf-8")


def load_prefs() -> dict:
    p = config_dir() / "prefs.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_prefs(prefs: dict):
    (config_dir() / "prefs.json").write_text(json.dumps(prefs, indent=2), encoding="utf-8")
