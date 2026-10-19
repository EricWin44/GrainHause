// Main-thread side of the engine: opens photos, splits every render into bands of rows across
// the worker pool and stitches the results back together.
import { EngineWorker, Pool, Stale } from "./pool";
import { decodeRaw, isRaw } from "./raw";
import type { Settings } from "../types";

export { Stale };

export type StockInfo = {
  id: string; name: string; family: string; iso: number; blurb: string; kind: "neg" | "rev" | "bw";
  halation: number; bloom: number; default_lab: string;
};

export type Info = {
  version: string;
  stocks: StockInfo[];
  defaults: Settings;
  labs: string[]; lab_labels: Record<string, string>;
  textures: string[]; texture_labels: Record<string, string>;
  leaks: string[]; leak_labels: Record<string, string>;
  borders: string[]; border_labels: Record<string, string>;
  aspects: string[]; aspect_labels: Record<string, string>;
  wb_presets: Record<string, [number, number] | null>;
  grain_presets: Record<string, [number, number, number]>;
  output_spaces: string[];
  preview_edge: number; draft_edge: number; lowres: number; wb_reference: number;
};

export type Header = {
  id: string;
  kind: "raw" | "tiff" | "image";
  full_size: [number, number]; // W, H
  full_shape: [number, number]; // H, W of the decoded image
  space: string;
  bit_depth: number;
  scene_referred: boolean;
  meta: { as_shot_wb?: [number, number]; as_shot_gains?: number[] };
  meter_ev: number;
  preview_shape: [number, number]; // H, W
};

export type RGB = { w: number; h: number; data: Uint8Array; bits?: 8 | 16 };

// ---------------------------------------------------------------- python-compatible geometry

/** Python's round(): halves go to the even neighbour. */
export function pyRound(x: number) {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** imageio.resize_linear output size (H, W). */
export function resizeDims([h, w]: [number, number], edge: number): [number, number] {
  if (Math.max(h, w) <= edge) return [h, w];
  const s = edge / Math.max(h, w);
  return [Math.max(1, pyRound(h * s)), Math.max(1, pyRound(w * s))];
}

/** engine.crop_box: (a, b, c, d) = x0, y0, x1, y1 in px. */
export function cropBox(crop: number[] | null, W: number, H: number): [number, number, number, number] {
  if (!crop) return [0, 0, W, H];
  const [x0, y0, x1, y1] = crop;
  let a = Math.trunc(pyRound(Math.min(x0, x1) * W));
  let b = Math.trunc(pyRound(Math.min(y0, y1) * H));
  let c = Math.trunc(pyRound(Math.max(x0, x1) * W));
  let d = Math.trunc(pyRound(Math.max(y0, y1) * H));
  a = Math.max(0, Math.min(a, W - 2));
  b = Math.max(0, Math.min(b, H - 2));
  c = Math.max(a + 2, Math.min(c, W));
  d = Math.max(b + 2, Math.min(d, H));
  return [a, b, c, d];
}

function splitRows(y0: number, y1: number, n: number): [number, number][] {
  const rows = y1 - y0;
  n = Math.max(1, Math.min(n, rows));
  const out: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const a = y0 + Math.round((rows * i) / n);
    const b = y0 + Math.round((rows * (i + 1)) / n);
    if (b > a) out.push([a, b]);
  }
  return out;
}

export const PRIO = { open: 0, preview: 1, tile: 2, swatch: 3, export: 3, before: 4, thumb: 6 };

// ---------------------------------------------------------------- engine

type Held = { header: Header; preview: Uint8Array };

export class Engine {
  pool: Pool;
  info!: Info;
  private held = new Map<string, Held>();
  private opening = new Map<string, Promise<Header>>();
  /** gives the engine a photo's file again when worker 0 has let go of it */
  fileFor: (pid: string) => Promise<{ name: string; bytes: Uint8Array }> = async () => {
    throw new Error("no file source");
  };
  onStatus: (msg: string) => void = () => {};

  constructor(n: number) {
    this.pool = new Pool(n);
  }

  get w0(): EngineWorker {
    return this.pool.workers[0];
  }

  get size() {
    return this.pool.workers.length;
  }

  async start(onProgress?: (ready: number, total: number) => void): Promise<Info> {
    const [w0, ...rest] = this.pool.workers;
    const bump = () => onProgress?.(this.pool.readyCount, this.pool.workers.length);
    await w0.call("init", ["main"]);
    this.info = JSON.parse(await w0.call("info"));
    await w0.call("warm");
    w0.ready = true;
    bump();
    this.pool.pump();
    for (const w of rest) {
      w.call("init", ["helper"])
        .then(() => w.call("warm"))
        .then(() => {
          w.ready = true;
          bump();
          this.pool.pump();
        })
        .catch((e) => console.error(`worker ${w.index} failed to start`, e));
    }
    return this.info;
  }

  header(pid: string) {
    return this.held.get(pid)?.header;
  }

  forget(pid: string) {
    this.held.delete(pid);
  }

  // -------------------------------------------------------------- opening (worker 0)

  /** Decode the full file on worker 0 and make the preview every worker renders from. */
  open(pid: string, name: string, bytes: Uint8Array): Promise<Header> {
    const cur = this.opening.get(pid);
    if (cur) return cur;
    const p = this.pool.submit(PRIO.open, async () => {
      const w0 = this.w0;
      let hd: Header;
      if (isRaw(name)) {
        this.onStatus("Decoding RAW…");
        const raw = await decodeRaw(bytes);
        hd = JSON.parse(await w0.call("open_raw", [pid, name, raw.data, raw.width, raw.height,
          JSON.stringify(raw.meta)], [raw.data.buffer]));
      } else {
        hd = JSON.parse(await w0.call("open_file", [pid, name, bytes]));
      }
      w0.touch(pid);
      // other workers that held an older copy must take the new one
      for (const w of this.pool.workers.slice(1)) w.forget(pid);
      const preview: Uint8Array = await w0.call("preview_bytes", [pid]);
      this.held.set(pid, { header: hd, preview });
      while (this.held.size > 3) this.held.delete(this.held.keys().next().value!);
      return hd;
    }, { worker: 0 }).finally(() => {
      this.opening.delete(pid);
      this.onStatus("");
    });
    this.opening.set(pid, p);
    return p;
  }

  /** worker 0 keeps the full-resolution file: open it again if it was let go */
  private async ensureFull(pid: string) {
    if (this.w0.held.includes(pid) && this.held.has(pid)) return;
    const pending = this.opening.get(pid);
    if (pending) {
      await pending;
      return;
    }
    const { name, bytes } = await this.fileFor(pid);
    this.onStatus("Loading full resolution…");
    await this.openDirect(pid, name, bytes);
    this.onStatus("");
  }

  /** like open(), but runs right away (called from inside jobs, so it must not queue behind them) */
  private openDirect(pid: string, name: string, bytes: Uint8Array): Promise<Header> {
    const p = (async () => {
      const w0 = this.w0;
      let hd: Header;
      if (isRaw(name)) {
        const raw = await decodeRaw(bytes);
        hd = JSON.parse(await w0.call("open_raw", [pid, name, raw.data, raw.width, raw.height,
          JSON.stringify(raw.meta)], [raw.data.buffer]));
      } else {
        hd = JSON.parse(await w0.call("open_file", [pid, name, bytes]));
      }
      w0.touch(pid);
      if (!this.held.has(pid)) {
        const preview: Uint8Array = await w0.call("preview_bytes", [pid]);
        this.held.set(pid, { header: hd, preview });
      }
      return hd;
    })().finally(() => this.opening.delete(pid));
    this.opening.set(pid, p);
    return p;
  }

  private async ensurePreview(w: EngineWorker, pid: string) {
    if (w.index === 0) return this.ensureFull(pid);
    if (w.held.includes(pid)) return;
    const h = this.held.get(pid);
    if (!h) throw new Stale();
    const copy = h.preview.slice();
    await w.call("set_preview", [JSON.stringify(h.header), copy], [copy.buffer]);
    w.touch(pid);
  }

  // -------------------------------------------------------------- previews

  previewDims(pid: string, which: "preview" | "draft"): [number, number] {
    const hd = this.held.get(pid)!.header;
    return which === "preview" ? hd.preview_shape : resizeDims(hd.preview_shape, this.info.draft_edge);
  }

  /** Develop the screen preview (or the quick draft used while dragging) in bands across the pool. */
  async renderPreview(pid: string, which: "preview" | "draft", s: Settings,
                      o: { before?: boolean; ignoreCrop?: boolean; prio?: number; stale?: () => boolean } = {}): Promise<RGB> {
    if (!this.held.has(pid)) throw new Stale();
    const [H0, W0] = this.previewDims(pid, which);
    const [a, b, c, d] = s.crop && !o.ignoreCrop ? cropBox(s.crop, W0, H0) : [0, 0, W0, H0];
    const W = c - a, H = d - b;
    const n = Math.max(1, Math.min(this.pool.readyCount, Math.ceil(H / 48)));
    const sj = JSON.stringify(s);
    const parts = await Promise.all(splitRows(0, H, n).map(([y0, y1]) =>
      this.pool.submit(o.prio ?? PRIO.preview, async (w) => {
        await this.ensurePreview(w, pid);
        const rgb: Uint8Array = await w.call("render", [pid, which, sj, !!o.before, !!o.ignoreCrop,
          JSON.stringify([0, y0, W, y1])]);
        return { y0, rgb };
      }, { stale: o.stale })));
    let out: RGB = { w: W, h: H, data: assemble(parts, W * 3, H) };
    if (!o.before && !o.ignoreCrop && s.border !== "none") out = await this.border(out, s, o.prio ?? PRIO.preview, o.stale);
    return out;
  }

  private async border(img: RGB, s: Settings, prio: number, stale?: () => boolean): Promise<RGB> {
    const bits = img.bits ?? 8;
    return this.pool.submit(prio, async (w) => {
      const [shape, data] = await w.call("add_border", [img.data, img.h, img.w, JSON.stringify(s), bits], [img.data.buffer]);
      const [h, wd] = JSON.parse(shape);
      return { w: wd, h, data, bits };
    }, { stale });
  }

  thumb(pid: string, edge: number, s: Settings, stale?: () => boolean): Promise<RGB> {
    return this.pool.submit(PRIO.thumb, async (w) => {
      await this.ensurePreview(w, pid);
      const [shape, data] = await w.call("thumb", [pid, edge, JSON.stringify(s)]);
      const [h, wd] = JSON.parse(shape);
      return { w: wd, h, data };
    }, { stale });
  }

  ramp(w: number, h: number, s: Settings, stale?: () => boolean): Promise<RGB> {
    return this.pool.submit(PRIO.swatch, async (wk) => {
      const data = await wk.call("ramp", [w, h, JSON.stringify(s)]);
      return { w, h, data };
    }, { stale });
  }

  pick(pid: string, nx: number, ny: number, s: Settings, cropMode: boolean): Promise<[number, number]> {
    return this.pool.submit(PRIO.preview, async (w) => {
      await this.ensurePreview(w, pid);
      return JSON.parse(await w.call("pick", [pid, nx, ny, JSON.stringify(s), cropMode]));
    });
  }

  cube(s: Settings): Promise<string> {
    return this.pool.submit(PRIO.export, (w) => w.call("cube", [JSON.stringify(s)]));
  }

  // -------------------------------------------------------------- full resolution

  /** Long edge of the full decoded image. */
  fullEdge(pid: string) {
    const hd = this.held.get(pid)!.header;
    return Math.max(hd.full_shape[0], hd.full_shape[1]);
  }

  async levelShape(pid: string, edge: number): Promise<[number, number]> {
    await this.ensureFull(pid);
    return JSON.parse(await this.w0.call("level_shape", [pid, edge]));
  }

  /**
   * Develop `region` (x0, y0, x1, y1 in the cropped image at long edge `edge`) from the full file:
   * zoomed-in views, the 100% grain sample and exports. Worker 0 hands each worker just the rows
   * its band needs, plus the shared small copy of the frame.
   */
  async renderFull(pid: string, edge: number, s: Settings, region: [number, number, number, number] | null,
                   o: { bits?: 8 | 16; space?: string; prio?: number; stale?: () => boolean;
                        bands?: number; progress?: (f: number) => void } = {}): Promise<RGB> {
    await this.ensureFull(pid);
    if (o.stale?.()) throw new Stale();
    const hd = this.held.get(pid)!.header;
    const w0 = this.w0;
    const sj = JSON.stringify(s);
    const [H, W] = JSON.parse(await w0.call("level_shape", [pid, edge])) as [number, number];
    const [a, b, c, d] = s.crop ? cropBox(s.crop, W, H) : [0, 0, W, H];
    const [x0, y0, x1, y1] = region ?? [0, 0, c - a, d - b];
    const [lowShape, low] = await w0.call("low_for", [pid, edge, sj]);
    const [lh, lw] = JSON.parse(lowShape);
    const bits = o.bits ?? 8;
    const hdr = JSON.stringify({ ...hd, out_space: o.space ?? "sRGB" });
    const L = Math.max(H, W);
    const n = o.bands ?? Math.max(1, Math.min(Math.max(1, this.pool.readyCount - 1), Math.ceil((y1 - y0) / 64)));
    const bands = splitRows(y0, y1, n);
    let done = 0;
    const parts = await Promise.all(bands.map(([ya, yb]) =>
      this.pool.submit(o.prio ?? PRIO.tile, async (w) => {
        let margin = Math.ceil(64 * Math.max(1, L / 9000)) + 8;
        for (;;) {
          const r0 = Math.max(0, b + ya - margin), r1 = Math.min(H, b + yb + margin);
          const data: Uint8Array = await w0.call("band", [pid, edge, r0, r1]);
          const lowCopy = low.slice();
          try {
            const rgb: Uint8Array = await w.call("render_band", [`${pid}:${edge}`, data, r0, r1 - r0, H, W,
              lowCopy, lh, lw, sj, hdr, JSON.stringify([x0, ya, x1, yb]), bits], [data.buffer, lowCopy.buffer]);
            done += yb - ya;
            o.progress?.(done / (y1 - y0));
            return { y0: ya - y0, rgb };
          } catch (e: any) {
            if (String(e?.message).includes("BAND_TOO_SMALL") && margin < H) {
              margin *= 2;
              continue;
            }
            throw e;
          }
        }
      }, { stale: o.stale, avoid0: true })));
    const bpp = bits === 16 ? 6 : 3;
    let out: RGB = { w: x1 - x0, h: y1 - y0, data: assemble(parts, (x1 - x0) * bpp, y1 - y0), bits };
    if (region === null && s.border !== "none") out = await this.border(out, s, o.prio ?? PRIO.export, o.stale);
    return out;
  }

  /** JPEG / TIFF / PNG bytes, with an ICC profile for `space` and the original's EXIF. */
  async encode(pid: string, img: RGB, fmt: string, space: string, quality: number): Promise<Uint8Array> {
    return this.pool.submit(PRIO.export, (w) =>
      w.call("encode", [img.data, img.h, img.w, img.bits ?? 8, fmt, space, quality, pid], [img.data.buffer]),
    { worker: 0 });
  }
}

function assemble(parts: { y0: number; rgb: Uint8Array }[], rowBytes: number, h: number) {
  const out = new Uint8Array(rowBytes * h);
  for (const p of parts) out.set(p.rgb, p.y0 * rowBytes);
  return out;
}

export function workerCount() {
  const hc = navigator.hardwareConcurrency || 4;
  const mem = (navigator as any).deviceMemory as number | undefined; // GB, Chromium only
  let n = Math.max(2, Math.min(6, hc - 1));
  if (mem !== undefined && mem <= 4) n = Math.min(n, 2);
  if (/iPhone|iPad|Android/i.test(navigator.userAgent)) n = Math.min(n, 2);
  return n;
}
