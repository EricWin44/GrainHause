// Mirrors grainhouse.settings.Settings (the defaults come from Python at start-up).
export type Curve = [number, number][];

export type Settings = {
  stock: string;
  strength: number;
  exposure: number;
  auto_meter: boolean;
  push: number;
  denoise: number;
  wb_temp: number | null;
  wb_tint: number | null;
  highlights: number;
  shadows: number;
  contrast: number;
  saturation: number;
  fade: number;
  skin_warmth: number;
  skin_saturation: number;
  grain: number | null;
  grain_size: number | null;
  grain_roughness: number;
  halation: number | null;
  bloom: number | null;
  vignette: number;
  leak: string;
  leak_amount: number;
  leak_seed: number;
  border: string;
  lab: string | null;
  softness: number | null;
  sharpen: number;
  depth: number;
  dust: number;
  scratches: number;
  texture: string;
  texture_amount: number;
  curve_rgb: Curve;
  curve_r: Curve;
  curve_g: Curve;
  curve_b: Curve;
  crop: [number, number, number, number] | null;
  aspect: string;
  seed: number;
};

export type SettingKey = keyof Settings;

export const clone = <T,>(v: T): T => structuredClone(v);

export function sameSettings(a: Settings, b: Settings) {
  return JSON.stringify(a) === JSON.stringify(b);
}
