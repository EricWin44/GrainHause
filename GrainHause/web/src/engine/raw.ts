// RAW decoding with LibRaw (WebAssembly), set up the way the desktop app calls rawpy:
// 16-bit linear ProPhoto RGB, daylight-balanced, no auto brightening, blended highlights.
import LibRaw from "libraw-wasm";

export const RAW_EXTS = new Set([
  "3fr", "ari", "arw", "bay", "cr2", "cr3", "crw", "dcr", "dng", "erf", "fff", "iiq", "k25", "kdc", "mef",
  "mos", "mrw", "nef", "nrw", "orf", "pef", "raf", "raw", "rw2", "rwl", "sr2", "srf", "srw", "x3f",
]);
export const OTHER_EXTS = new Set(["tif", "tiff", "jpg", "jpeg", "png", "webp", "heic", "heif", "hif"]);

export const ext = (name: string) => (name.split(".").pop() || "").toLowerCase();
export const isRaw = (name: string) => RAW_EXTS.has(ext(name));
export const isSupported = (name: string) => RAW_EXTS.has(ext(name)) || OTHER_EXTS.has(ext(name));

export type RawDecoded = {
  data: Uint8Array; // uint16 RGB, interleaved
  width: number;
  height: number;
  meta: { pre_mul: number[]; cam_mul: number[]; cam_xyz: number[][] };
};

const BASE = {
  outputBps: 16,
  outputColor: 4, // ProPhoto
  gamm: [1, 1],
  noAutoBright: true,
  highlight: 2, // blend
  userQual: 3,
};

async function decode(bytes: Uint8Array, settings: Record<string, unknown>) {
  const raw = new LibRaw();
  try {
    await raw.open(bytes.slice(), settings as any);
    const meta: any = await raw.metadata(true);
    const img: any = await raw.imageData();
    return { meta, img };
  } finally {
    raw.dispose();
  }
}

export async function decodeRaw(bytes: Uint8Array): Promise<RawDecoded> {
  let { meta, img } = await decode(bytes, BASE);
  const c = meta?.color_data ?? {};
  const day: number[] = (c.pre_mul ?? []).map(Number);
  if (!(day.length >= 3 && day.slice(0, 3).every((v) => v > 0))) {
    // no daylight multipliers for this camera: fall back to the camera's own white balance
    ({ meta, img } = await decode(bytes, { ...BASE, useCameraWb: true }));
  }
  if (!img || !img.data) throw new Error("LibRaw could not decode this RAW file");
  if (img.bits !== 16 || img.colors !== 3) throw new Error(`unexpected RAW output (${img.colors} colours, ${img.bits} bit)`);
  const d: Uint16Array = img.data instanceof Uint16Array ? img.data : new Uint16Array(img.data.buffer);
  const cc = meta?.color_data ?? {};
  return {
    data: new Uint8Array(d.buffer, d.byteOffset, d.byteLength),
    width: img.width,
    height: img.height,
    meta: {
      pre_mul: (cc.pre_mul ?? []).map(Number),
      cam_mul: (cc.cam_mul ?? []).map(Number),
      cam_xyz: cc.cam_xyz ?? [],
    },
  };
}
