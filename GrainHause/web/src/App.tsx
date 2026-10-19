import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { cropBox, Engine, pyRound, PRIO, Stale, workerCount, type Header, type Info, type RGB } from "./engine/engine";
import { isSupported } from "./engine/raw";
import { clone, type Curve, type Settings, type SettingKey } from "./types";
import { kv, photosDb, type StoredPhoto } from "./db";
import { download, thumbBlob, toBitmap } from "./img";
import { Labeled, Section, Select, Slider } from "./components/Slider";
import { CurveEditor, type Channel } from "./components/CurveEditor";
import { histogram, HistogramView, type Hist } from "./components/Histogram";
import { PreviewView, type Mode, type PreviewHandle } from "./components/PreviewView";
import { kindLabel, StockGrid, type Filter } from "./components/StockGrid";
import { Swatch, type SwatchHandle, type SwatchImage } from "./components/Swatch";

// ------------------------------------------------------------------ constants (as the desktop app)

const ASPECT_RATIOS: Record<string, number> = { "1:1": 1, "4:5": 4 / 5, "9:16": 9 / 16, "3:2": 3 / 2, "16:9": 16 / 9, "1.91:1": 1.91 };
const SECTION_FIELDS: Record<string, SettingKey[]> = {
  "Film stock": ["strength"],
  Exposure: ["auto_meter", "exposure", "push", "highlights", "shadows", "contrast"],
  "White balance": ["wb_temp", "wb_tint"],
  "Colour & lab": ["lab", "depth", "saturation", "fade"],
  "Skin tones": ["skin_warmth", "skin_saturation"],
  "Tone curve": ["curve_rgb", "curve_r", "curve_g", "curve_b"],
  Crop: ["crop", "aspect"],
  Grain: ["grain", "grain_size", "grain_roughness", "softness", "sharpen", "denoise"],
  Light: ["halation", "bloom", "vignette"],
  "Light leaks": ["leak", "leak_amount"],
  "Dust, scratches & texture": ["dust", "scratches", "texture", "texture_amount"],
  "Frame & border": ["border"],
};
const NO_DRAFT_FIELDS = new Set(["grain", "grain_size", "grain_roughness", "softness", "sharpen", "dust", "scratches", "texture_amount", "depth"]);
const THUMB_IGNORED = new Set(["stock", "strength", "grain", "grain_size", "grain_roughness", "seed", "dust", "scratches", "texture",
  "texture_amount", "sharpen", "softness", "denoise", "halation", "bloom", "border", "leak", "leak_amount", "leak_seed", "aspect"]);
const TILE_LEVELS = [0.25, 0.3, 0.35, 0.42, 0.5, 0.6, 0.71, 0.85, 1.0];
const FIELD_NAMES: Record<string, string> = {
  stock: "film stock", strength: "film strength", auto_meter: "auto-meter", grain_roughness: "grain roughness",
  grain_size: "grain size", wb_temp: "temperature", wb_tint: "tint", leak_amount: "leak amount",
  leak_seed: "leak shuffle", texture_amount: "texture amount", curve_rgb: "tone curve", curve_r: "red curve",
  curve_g: "green curve", curve_b: "blue curve", seed: "grain pattern", lab: "lab process", denoise: "digital noise cleanup",
};
const CURVE_PRESETS: [string, Curve][] = [
  ["Linear", [[0, 0], [1, 1]]],
  ["Soft contrast", [[0, 0], [0.25, 0.21], [0.75, 0.79], [1, 1]]],
  ["Matte", [[0, 0.07], [0.25, 0.26], [0.75, 0.77], [1, 0.97]]],
  ["Punchy", [[0, 0], [0.25, 0.17], [0.75, 0.84], [1, 1]]],
];
const FORMATS: [string, string][] = [["jpg", "JPEG"], ["tif", "TIFF 16-bit"], ["png", "PNG"]];
const SIZES: [string, string][] = [["0", "Full size"], ["4096", "4096 px"], ["2048", "2048 px"], ["1080", "Instagram 1080 px"]];
const MIME: Record<string, string> = { jpg: "image/jpeg", tif: "image/tiff", png: "image/png" };
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = IS_MAC ? "⌘" : "Ctrl+";

function describeChange(a: Settings, b: Settings) {
  const diff = (Object.keys(a) as SettingKey[]).filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
  if (!diff.length) return "change";
  if (diff.length > 3) return `${diff.length} changes`;
  return diff.map((k) => FIELD_NAMES[k] ?? k.replace(/_/g, " ")).join(", ");
}

function fitAspect(ratio: number, imgAspect: number, around?: [number, number]): [number, number, number, number] {
  let w = 1, h = imgAspect / ratio;
  if (h > 1) { w = ratio / imgAspect; h = 1; }
  const [cx, cy] = around ?? [0.5, 0.5];
  const x0 = Math.min(1 - w, Math.max(0, cx - w / 2));
  const y0 = Math.min(1 - h, Math.max(0, cy - h / 2));
  return [x0, y0, x0 + w, y0 + h];
}

const stem = (name: string) => name.replace(/\.[^.]+$/, "");
const clampN = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

// ------------------------------------------------------------------ photo model

type Photo = {
  id: string;
  name: string;
  file: Blob;
  size: number;
  lastModified: number;
  settings: Settings | null;
  committed: Settings | null;
  undo: Settings[];
  redo: Settings[];
  thumbUrl: string | null;
  header?: Header;
  after?: { bmp: ImageBitmap; rgb: RGB };
  before?: { bmp: ImageBitmap; rgb: RGB; key: string };
  beforePending?: string | null;
  error?: string;
};

type Prefs = { format: string; space: string; size: string; quality: number; compareSize: number; filter: Filter };
const DEFAULT_PREFS: Prefs = { format: "jpg", space: "sRGB", size: "0", quality: 95, compareSize: 320, filter: "all" };

type Progress = { label: string; frac: number; cancel?: () => void } | null;

// ------------------------------------------------------------------ app

export default function App() {
  const engine = useRef<Engine | null>(null);
  const [info, setInfo] = useState<Info | null>(null);
  const infoRef = useRef<Info | null>(null);
  const [boot, setBoot] = useState<{ msg: string; ready: number; total: number; error?: string }>({ msg: "Starting the film engine…", ready: 0, total: 0 });
  const [, bump] = useReducer((x: number) => x + 1, 0);

  const photos = useRef<Photo[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const curId = useRef<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const anchor = useRef<string | null>(null);

  const [s, setS] = useState<Settings | null>(null);
  const sRef = useRef<Settings | null>(null);

  const [mode, setModeState] = useState<Mode>("view");
  const modeRef = useRef<Mode>("view");
  const [image, setImage] = useState<ImageBitmap | null>(null);
  const [tile, setTile] = useState<{ bmp: ImageBitmap; rect: [number, number, number, number] } | null>(null);
  const [beforeOn, setBeforeOn] = useState(false);
  const beforeRef = useRef(false);
  const [hist, setHist] = useState<Hist | null>(null);
  const [message, setMessage] = useState("Open a photo to start  ·  RAW, TIFF, JPEG, HEIC");
  const [status, setStatusText] = useState("");
  const statusTimer = useRef<number>(0);
  const [zoomLabel, setZoomLabel] = useState("Fit");
  const [compare, setCompare] = useState(false);
  const compareRef = useRef(false);
  const [thumbs, setThumbs] = useState<Map<string, ImageBitmap>>(new Map());
  const [channel, setChannel] = useState<Channel>("rgb");
  const [swatch, setSwatch] = useState<SwatchImage | null>(null);
  const [swatchCaption, setSwatchCaption] = useState("100% sample");
  const [presets, setPresets] = useState<Record<string, Settings>>({});
  const [presetSel, setPresetSel] = useState("");
  const [prefs, setPrefsState] = useState<Prefs>(DEFAULT_PREFS);
  const prefsRef = useRef(prefs);
  const clipboard = useRef<Settings | null>(null);
  const aspectFlip = useRef(false);
  const [progress, setProgress] = useState<Progress>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; ids: string[] } | null>(null);
  const [help, setHelp] = useState(false);
  const [panelTab, setPanelTab] = useState<"edit" | "photos">("edit");
  const exportDir = useRef<FileSystemDirectoryHandle | null>(null);

  const view = useRef<PreviewHandle>(null);
  const swatchView = useRef<SwatchHandle>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  // render bookkeeping (refs: it changes far faster than React should re-render)
  const R = useRef({
    rev: 0, previewRev: -1, draftRev: -1, busy: false, pending: false, dragging: false, gesture: false,
    lastField: null as string | null, tileGen: 0, tileInfo: null as null | { rev: number; zoom: number; rect: [number, number, number, number] },
    thumbGen: 0, thumbKey: "",
    swatchBusy: false, swatchPending: false, swatchPad: 1, swatchCenter: null as [number, number] | null, swatchManual: false,
    swatchGeom: null as null | { cx: number; cy: number; W: number; H: number; w: number; h: number; x0: number; y0: number; rw: number; rh: number },
    swatchGen: 0,
  });
  const timers = useRef<Record<string, number>>({});
  const later = (name: string, ms: number, fn: () => void) => {
    clearTimeout(timers.current[name]);
    timers.current[name] = window.setTimeout(fn, ms);
  };

  const say = useCallback((msg: string, ms = 4000) => {
    setStatusText(msg);
    clearTimeout(statusTimer.current);
    if (ms) statusTimer.current = window.setTimeout(() => setStatusText(""), ms);
  }, []);

  const current = () => photos.current.find((p) => p.id === curId.current) ?? null;
  const defaults = () => clone(infoRef.current!.defaults);
  const stockOf = (id: string) => infoRef.current!.stocks.find((x) => x.id === id) ?? infoRef.current!.stocks.find((x) => x.id === "portra400")!;

  const setPrefs = (p: Partial<Prefs>) => {
    const next = { ...prefsRef.current, ...p };
    prefsRef.current = next;
    setPrefsState(next);
    kv.set("prefs", next);
  };

  const setMode = (m: Mode) => {
    modeRef.current = m;
    setModeState(m);
  };

  // ------------------------------------------------------------------ start-up
  useEffect(() => {
    if (engine.current) return;
    if (!self.crossOriginIsolated) console.warn("page is not cross-origin isolated: RAW decoding will fail");
    const eng = new Engine(workerCount());
    engine.current = eng;
    eng.onStatus = (m) => (m ? say(m, 0) : setStatusText(""));
    eng.fileFor = async (pid) => {
      const p = photos.current.find((x) => x.id === pid);
      if (!p) throw new Stale();
      return { name: p.name, bytes: new Uint8Array(await p.file.arrayBuffer()) };
    };
    eng.start((ready, total) => setBoot((b) => ({ ...b, ready, total })))
      .then(async (inf) => {
        infoRef.current = inf;
        setInfo(inf);
        const [stored, session, pre, pf] = await Promise.all([
          photosDb.all(), kv.get<{ ids: string[]; current: string | null }>("session", { ids: [], current: null }),
          kv.get<Record<string, Settings>>("presets", {}), kv.get<Prefs>("prefs", DEFAULT_PREFS),
        ]);
        exportDir.current = await kv.get<FileSystemDirectoryHandle | null>("exportDir", null);
        setPresets(pre);
        prefsRef.current = { ...DEFAULT_PREFS, ...pf };
        setPrefsState(prefsRef.current);
        const order = new Map(session.ids.map((id, i) => [id, i]));
        stored.sort((a, b) => (order.get(a.id) ?? 1e9) - (order.get(b.id) ?? 1e9) || a.added - b.added);
        photos.current = stored.map(fromStored);
        const s0 = clone(inf.defaults);
        sRef.current = s0;
        setS(s0);
        bump();
        const cur = session.current && photos.current.some((p) => p.id === session.current) ? session.current : photos.current[0]?.id;
        if (cur) selectPhoto(cur);
        else renderSwatch();
        navigator.storage?.persist?.().catch(() => {});
      })
      .catch((e) => setBoot((b) => ({ ...b, error: String(e?.message ?? e) })));
  }, []);

  const fromStored = (p: StoredPhoto): Photo => ({
    id: p.id, name: p.name, file: p.file, size: p.size, lastModified: p.lastModified, settings: p.settings,
    committed: null, undo: [], redo: [], thumbUrl: p.thumb ? URL.createObjectURL(p.thumb) : null,
  });

  const saveSession = () => kv.set("session", { ids: photos.current.map((p) => p.id), current: curId.current });

  // ------------------------------------------------------------------ files
  const addFiles = async (files: File[]) => {
    const ok = files.filter((f) => isSupported(f.name));
    if (!ok.length) {
      if (files.length) say("Those files aren't photos GrainHause can open (RAW, TIFF, JPEG, PNG, HEIC, WebP).", 6000);
      return;
    }
    ok.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    let first: string | null = null;
    for (const f of ok) {
      const dup = photos.current.find((p) => p.name === f.name && p.size === f.size && p.lastModified === f.lastModified);
      if (dup) {
        first ??= dup.id;
        continue;
      }
      const id = crypto.randomUUID();
      const sp: StoredPhoto = { id, name: f.name, type: f.type, size: f.size, lastModified: f.lastModified, file: f, settings: null, thumb: null, added: Date.now() };
      await photosDb.put(sp);
      photos.current.push(fromStored(sp));
      first ??= id;
    }
    bump();
    saveSession();
    if (first) selectPhoto(first);
    say(`Added ${ok.length} photo${ok.length === 1 ? "" : "s"}`, 3000);
  };

  const removePhotos = async (ids: string[]) => {
    const wasCurrent = ids.includes(curId.current ?? "");
    const idx = photos.current.findIndex((p) => p.id === curId.current);
    for (const id of ids) {
      const p = photos.current.find((x) => x.id === id);
      if (p?.thumbUrl) URL.revokeObjectURL(p.thumbUrl);
      engine.current?.forget(id);
      await photosDb.remove(id);
    }
    photos.current = photos.current.filter((p) => !ids.includes(p.id));
    setSelected(new Set());
    bump();
    if (wasCurrent) {
      const next = photos.current[Math.min(Math.max(0, idx), photos.current.length - 1)];
      if (next) selectPhoto(next.id);
      else {
        curId.current = null;
        setCurrentId(null);
        setImage(null);
        setTile(null);
        setHist(null);
        setThumbs(new Map());
        setMessage("Open a photo to start  ·  RAW, TIFF, JPEG, HEIC");
        renderSwatch();
      }
    }
    saveSession();
  };

  // ------------------------------------------------------------------ selecting a photo
  const selectPhoto = async (id: string) => {
    const eng = engine.current!;
    if (curId.current === id && current()?.header) return;
    commit();
    storeCurrent();
    flushSave();
    const ph = photos.current.find((p) => p.id === id);
    if (!ph) return;
    curId.current = id;
    setCurrentId(id);
    setSelected(new Set([id]));
    anchor.current = id;
    R.current.swatchCenter = null;
    R.current.swatchManual = false;
    R.current.swatchGeom = null;
    R.current.tileInfo = null;
    R.current.thumbKey = "";
    R.current.previewRev = -1;
    R.current.draftRev = -1;
    setTile(null);
    setSwatch(null);
    setSwatchCaption("sample");
    setThumbs(new Map());
    view.current?.setZoom(null);
    if (modeRef.current !== "view") setMode("view");
    const s0 = clone(ph.settings ?? (clipboard.current ? clipboard.current : { ...defaults(), stock: sRef.current?.stock ?? infoRef.current!.defaults.stock }));
    sRef.current = s0;
    setS(s0);
    ph.committed ??= clone(s0);
    aspectFlip.current = false;
    setImage(ph.after?.bmp ?? null);
    setHist(ph.after ? histogram(ph.after.rgb) : null);
    setMessage(`Developing ${ph.name}…`);
    saveSession();
    try {
      if (!ph.header || !eng.header(id)) {
        const bytes = new Uint8Array(await ph.file.arrayBuffer());
        ph.header = await eng.open(id, ph.name, bytes);
        ph.error = undefined;
      }
    } catch (e: any) {
      if (e instanceof Stale) return;
      ph.error = String(e?.message ?? e);
      if (curId.current === id) {
        setMessage(`Couldn't open or develop ${ph.name}.`);
        say(`Couldn't open ${ph.name}: ${ph.error}`, 10000);
      }
      bump();
      return;
    }
    if (curId.current !== id) return;
    bump();
    previewLoaded(ph);
  };

  const previewLoaded = (ph: Photo) => {
    const hd = ph.header!;
    const kind = { raw: "RAW", tiff: "TIFF", image: "image" }[hd.kind];
    setInfoLine(`${kind} · ${hd.full_size[0]}×${hd.full_size[1]} · ${hd.space}` + (hd.scene_referred ? ` · metered ${hd.meter_ev >= 0 ? "+" : ""}${hd.meter_ev.toFixed(1)} EV` : ""));
    setS(clone(sRef.current!)); // as-shot white balance and frame shape are known now
    requestRender();
    later("swatch", 15, () => renderSwatch());
    later("thumbs", 700, renderStockThumbs);
  };
  const [infoLine, setInfoLine] = useState("");

  // ------------------------------------------------------------------ settings changes & history
  const storeCurrent = () => {
    const ph = current();
    if (ph && sRef.current) ph.settings = clone(sRef.current);
  };
  const flushSave = () => {
    const ph = current();
    clearTimeout(timers.current.save);
    if (ph?.settings) photosDb.patch(ph.id, { settings: ph.settings });
  };
  const beginEdit = () => {
    const ph = current();
    if (!ph || R.current.gesture) return;
    ph.committed ??= clone(sRef.current!);
    ph.undo.push(ph.committed);
    if (ph.undo.length > 200) ph.undo.splice(0, ph.undo.length - 200);
    ph.redo = [];
    R.current.gesture = true;
  };
  const commit = () => {
    const ph = current();
    if (ph && R.current.gesture) ph.committed = clone(sRef.current!);
    R.current.gesture = false;
  };
  const settled = () => {
    R.current.dragging = false;
    commit();
    requestRender();
    later("thumbs", 700, renderStockThumbs);
    bump();
  };
  const changed = (field?: string) => {
    if (field) R.current.lastField = field;
    later("swatch", 15, () => renderSwatch());
    beginEdit();
    R.current.dragging = true;
    later("idle", 320, settled);
    storeCurrent();
    later("save", 600, flushSave);
    requestRender();
  };
  const update = (fn: (s: Settings) => void, field?: string) => {
    if (!sRef.current) return;
    const n = clone(sRef.current);
    fn(n);
    sRef.current = n;
    setS(n);
    changed(field);
  };
  const set = <K extends SettingKey>(k: K, v: Settings[K]) => update((n) => { n[k] = v; }, k);

  const resetFields = (fields: SettingKey[]) => {
    const d = defaults();
    const n = clone(sRef.current!);
    let any = false;
    for (const f of fields) {
      if (JSON.stringify(n[f]) !== JSON.stringify(d[f])) {
        (n as any)[f] = clone(d[f]);
        any = true;
      }
    }
    if (fields.includes("crop") || fields.includes("aspect")) aspectFlip.current = false;
    if (any) {
      sRef.current = n;
      setS(n);
      changed(fields[0]);
    }
  };

  const stepHistory = (back: boolean) => {
    const ph = current();
    if (!ph) return;
    if (R.current.gesture) commit();
    const [src, dst] = back ? [ph.undo, ph.redo] : [ph.redo, ph.undo];
    const prev = src.pop();
    if (!prev) return;
    const before = sRef.current!;
    dst.push(clone(before));
    sRef.current = prev;
    setS(prev);
    ph.committed = clone(prev);
    ph.settings = clone(prev);
    aspectFlip.current = false;
    later("save", 600, flushSave);
    requestRender();
    later("swatch", 15, () => renderSwatch());
    later("thumbs", 700, renderStockThumbs);
    say(`${back ? "Undid" : "Redid"} ${describeChange(before, prev)}`, 2500);
    bump();
  };

  // ------------------------------------------------------------------ rendering
  const fullDims = (ph: Photo): [number, number] => [ph.header!.full_shape[1], ph.header!.full_shape[0]];

  const viewFullSize = (): [number, number] | null => {
    const ph = current();
    const st = sRef.current;
    if (!ph?.header || !st) return null;
    const [W0, H0] = fullDims(ph);
    if (modeRef.current === "crop") return [W0, H0];
    if (st.border !== "none") return null; // a border changes the frame: zoom the preview itself
    const [a, b, c, d] = cropBox(st.crop, W0, H0);
    return [c - a, d - b];
  };

  const requestRender = () => {
    const ph = current();
    if (!ph?.header) return;
    R.current.rev++;
    const z = view.current?.view().zoom ?? null;
    if (z === null || view.current!.zoomDevice() < 1.0) {
      R.current.tileGen++;
      if (tileShown.current) {
        setTile(null);
        tileShown.current = false;
        R.current.tileInfo = null;
      }
    }
    engine.current?.pool.sweep();
    later("render", 40, renderNow);
  };
  const tileShown = useRef(false);

  const tileNeeded = () => {
    const v = view.current;
    const ph = current();
    const fs = viewFullSize();
    if (!v || !ph?.after || modeRef.current === "crop" || !fs || v.view().zoom === null) return false;
    const previewScale = ph.after.bmp.width / Math.max(1, fs[0]);
    return v.zoomDevice() > previewScale * 1.12;
  };
  const tileWanted = () => {
    if (!tileNeeded()) return false;
    const inf = R.current.tileInfo;
    if (!inf) return true;
    if (inf.rev !== R.current.rev || Math.abs(inf.zoom - view.current!.zoomDevice()) / inf.zoom > 0.01) return true;
    const [vx0, vy0, vx1, vy1] = view.current!.visibleRegion(0);
    const [x0, y0, x1, y1] = inf.rect;
    return !(vx0 >= x0 && vy0 >= y0 && vx1 <= x1 && vy1 <= y1);
  };

  const renderNow = async () => {
    const r = R.current;
    if (r.busy) {
      r.pending = true;
      return;
    }
    const ph = current();
    const eng = engine.current!;
    if (!ph?.header || !sRef.current) return;
    const [ph_h, ph_w] = ph.header.preview_shape;
    const draftOk = r.dragging && ph_h * ph_w > 900_000 && !NO_DRAFT_FIELDS.has(r.lastField ?? "");
    const wantPreview = r.previewRev !== r.rev && !(draftOk && r.draftRev === r.rev);
    const wantTile = tileWanted();
    if (!wantPreview && !wantTile) return;
    r.busy = true;
    r.pending = false;
    const st = clone(sRef.current);
    const rev = r.rev;
    const pid = ph.id;
    try {
      if (wantTile && (r.dragging || !wantPreview)) {
        const region = view.current!.visibleRegion();
        const zoom = view.current!.zoomDevice();
        const gen = r.tileGen;
        const res = await renderTile(ph, st, region, zoom, () => r.tileGen !== gen || curId.current !== pid);
        if (curId.current === pid && (rev === r.rev || zoom >= 1.0)) {
          setTile({ bmp: await toBitmap(res.img), rect: res.rect });
          tileShown.current = true;
          r.tileInfo = { rev, zoom, rect: res.rect };
        }
      } else {
        const cropMode = modeRef.current === "crop";
        const draft = draftOk;
        const img = await eng.renderPreview(pid, draft ? "draft" : "preview", st, {
          ignoreCrop: cropMode, stale: () => curId.current !== pid,
        });
        const bmp = await toBitmap(img);
        if (curId.current === pid) {
          ph.after = { bmp, rgb: img };
          if (!beforeRef.current) {
            setImage(bmp);
            setHist(histogram(img));
          }
          if (draft) r.draftRev = rev;
          else {
            r.previewRev = rev;
            ensureBefore(ph);
            updateListThumb(ph, bmp);
          }
        }
      }
    } catch (e: any) {
      if (!(e instanceof Stale)) {
        console.error(e);
        say(`Render failed: ${String(e?.message ?? e).split("\n").filter(Boolean).pop()}`, 10000);
        r.previewRev = rev;
      }
    } finally {
      r.busy = false;
      if (r.pending || (r.previewRev !== r.rev && !(r.dragging && r.draftRev === r.rev)) || tileWanted()) setTimeout(renderNow, 0);
    }
  };

  const renderTile = async (ph: Photo, st: Settings, region: [number, number, number, number], zoom: number, stale: () => boolean) => {
    const eng = engine.current!;
    const [Wf, Hf] = fullDims(ph);
    const L = Math.max(Wf, Hf);
    const lvl = TILE_LEVELS.find((l) => l >= zoom * 0.98) ?? 1.0;
    const edge = lvl >= 1 ? L : Math.trunc(pyRound(L * lvl));
    const [H0, W0] = await eng.levelShape(ph.id, edge);
    const sx = W0 / Wf, sy = H0 / Hf;
    const [a, b, c, d] = cropBox(st.crop, W0, H0);
    const W = c - a, H = d - b;
    let [x0, y0, x1, y1] = region;
    x0 = Math.floor(x0 * sx); x1 = Math.ceil(x1 * sx);
    y0 = Math.floor(y0 * sy); y1 = Math.ceil(y1 * sy);
    x0 = Math.max(0, Math.min(x0, W - 1)); y0 = Math.max(0, Math.min(y0, H - 1));
    x1 = Math.max(x0 + 1, Math.min(x1, W)); y1 = Math.max(y0 + 1, Math.min(y1, H));
    const img = await eng.renderFull(ph.id, edge, st, [x0, y0, x1, y1], { prio: PRIO.tile, stale });
    return { img, rect: [x0 / sx, y0 / sy, x1 / sx, y1 / sy] as [number, number, number, number] };
  };

  const listThumbSave = useRef(new Map<string, Blob>());
  const updateListThumb = async (ph: Photo, bmp: ImageBitmap) => {
    const blob = await thumbBlob(bmp);
    if (ph.thumbUrl) URL.revokeObjectURL(ph.thumbUrl);
    ph.thumbUrl = URL.createObjectURL(blob);
    bump();
    listThumbSave.current.set(ph.id, blob);
    later("thumbsave", 1500, () => {
      for (const [id, b] of listThumbSave.current) photosDb.patch(id, { thumb: b });
      listThumbSave.current.clear();
    });
  };

  // before / after
  const beforeKey = () => {
    const cm = modeRef.current === "crop";
    return JSON.stringify([cm ? null : sRef.current!.crop, cm, sRef.current!.auto_meter]);
  };
  const ensureBefore = async (ph: Photo) => {
    const key = beforeKey();
    if (ph.before?.key === key || ph.beforePending === key) return;
    ph.beforePending = key;
    const cm = modeRef.current === "crop";
    const s0 = { ...defaults(), crop: sRef.current!.crop ? [...sRef.current!.crop] as Settings["crop"] : null, auto_meter: sRef.current!.auto_meter };
    try {
      const img = await engine.current!.renderPreview(ph.id, "preview", s0, {
        before: true, ignoreCrop: cm, prio: PRIO.before, stale: () => curId.current !== ph.id,
      });
      ph.before = { bmp: await toBitmap(img), rgb: img, key };
      if (ph.id === curId.current && beforeRef.current) showBefore(true);
    } catch (e) {
      if (!(e instanceof Stale)) console.error(e);
    } finally {
      ph.beforePending = null;
    }
  };
  const showBefore = (on: boolean) => {
    const ph = current();
    if (!ph) return;
    if (on && ph.before && ph.before.key === beforeKey()) {
      setImage(ph.before.bmp);
      setHist(histogram(ph.before.rgb));
    } else if (!on && ph.after) {
      setImage(ph.after.bmp);
      setHist(histogram(ph.after.rgb));
    }
  };
  const setBefore = (on: boolean) => {
    if (beforeRef.current === on) return;
    beforeRef.current = on;
    setBeforeOn(on);
    const ph = current();
    if (!ph) return;
    if (on && (!ph.before || ph.before.key !== beforeKey())) ensureBefore(ph);
    showBefore(on);
  };

  // ------------------------------------------------------------------ 100% grain sample
  const renderSwatch = async (pad?: number) => {
    const r = R.current;
    const eng = engine.current;
    if (!eng || !sRef.current || !swatchView.current) return;
    if (pad === undefined) pad = r.swatchGeom ? 2.6 : 1.0;
    if (r.swatchBusy) {
      r.swatchPending = true;
      r.swatchPad = Math.max(pad, r.swatchPad);
      return;
    }
    const [w0, h0] = swatchView.current.deviceSize();
    const w = Math.max(60, w0 - 4), h = Math.max(40, h0 - 4);
    const st = clone(sRef.current);
    st.border = "none";
    const ph = current();
    r.swatchBusy = true;
    r.swatchPending = false;
    const gen = ++r.swatchGen;
    try {
      if (!ph?.header) {
        const img = await eng.ramp(w, h, st);
        setSwatch({ bmp: await toBitmap(img), origin: null, centre: null });
        setSwatchCaption("film sample");
        r.swatchGeom = null;
        return;
      }
      const [Wf, Hf] = fullDims(ph);
      const L = Math.max(Wf, Hf);
      const [a, b, c, d] = cropBox(st.crop, Wf, Hf);
      const W = c - a, H = d - b;
      let center = r.swatchCenter;
      const v = view.current?.view();
      const fs = viewFullSize();
      if (!r.swatchManual && v && v.zoom !== null && v.center && fs) center = [v.center[0] / fs[0], v.center[1] / fs[1]];
      const [cx, cy] = center ?? [0.5, 0.45];
      const ww = Math.min(w, W), hh = Math.min(h, H);
      const vx = clampN(cx * W, ww / 2, W - ww / 2), vy = clampN(cy * H, hh / 2, H - hh / 2);
      const rw = Math.min(W, Math.trunc(ww * pad)), rh = Math.min(H, Math.trunc(hh * pad));
      const x0 = Math.trunc(clampN(vx - rw / 2, 0, W - rw)), y0 = Math.trunc(clampN(vy - rh / 2, 0, H - rh));
      const pid = ph.id;
      const img = await eng.renderFull(pid, L, st, [x0, y0, x0 + rw, y0 + rh], {
        prio: PRIO.swatch, stale: () => curId.current !== pid || R.current.swatchGen !== gen,
      });
      if (curId.current !== pid) return;
      const g = { cx: vx / W, cy: vy / H, W, H, w: ww, h: hh, x0, y0, rw, rh };
      r.swatchGeom = g;
      const c2 = r.swatchManual && r.swatchCenter ? r.swatchCenter : [g.cx, g.cy] as [number, number];
      setSwatch({ bmp: await toBitmap(img), origin: [x0, y0], centre: swatchPx(c2[0], c2[1]) });
      setSwatchCaption("100% sample");
    } catch (e) {
      if (!(e instanceof Stale)) console.error(e);
    } finally {
      r.swatchBusy = false;
      const g = r.swatchGeom;
      if (r.swatchPending) {
        const p = r.swatchPad;
        r.swatchPad = 1;
        renderSwatch(p);
      } else if (g && current()?.header && (g.rw < Math.min(g.W, g.w * 2.5) || g.rh < Math.min(g.H, g.h * 2.5))) {
        renderSwatch(2.6); // widen it in the background, ready for dragging
      }
    }
  };
  const swatchPx = (cx: number, cy: number): [number, number] => {
    const g = R.current.swatchGeom!;
    return [clampN(cx * g.W, g.w / 2, g.W - g.w / 2), clampN(cy * g.H, g.h / 2, g.H - g.h / 2)];
  };
  const swatchDragged = (dx: number, dy: number) => {
    const r = R.current;
    const g = r.swatchGeom;
    if (!g) return;
    const dpr = window.devicePixelRatio || 1;
    const [cx, cy] = r.swatchManual && r.swatchCenter ? r.swatchCenter : [g.cx, g.cy];
    let [px, py] = swatchPx(cx, cy);
    [px, py] = swatchPx((px - dx * dpr) / g.W, (py - dy * dpr) / g.H);
    r.swatchCenter = [px / g.W, py / g.H];
    r.swatchManual = true;
    swatchView.current?.setCentre([px, py]);
    const mx = g.w * 0.25, my = g.h * 0.25;
    const inside = px - g.w / 2 >= g.x0 + (g.x0 > 0 ? mx : 0) && px + g.w / 2 <= g.x0 + g.rw - (g.x0 + g.rw < g.W ? mx : 0)
      && py - g.h / 2 >= g.y0 + (g.y0 > 0 ? my : 0) && py + g.h / 2 <= g.y0 + g.rh - (g.y0 + g.rh < g.H ? my : 0);
    if (!inside) renderSwatch(2.6);
  };
  const swatchReleased = () => {
    const r = R.current;
    const g = r.swatchGeom;
    if (!g || !r.swatchCenter) return;
    const [px, py] = swatchPx(...r.swatchCenter);
    if (Math.abs(px - (g.x0 + g.rw / 2)) > g.w * 0.6 || Math.abs(py - (g.y0 + g.rh / 2)) > g.h * 0.6) renderSwatch(2.6);
  };

  // ------------------------------------------------------------------ stock thumbnails
  const renderStockThumbs = () => {
    const ph = current();
    const eng = engine.current;
    if (!ph?.header || !eng || !sRef.current) return;
    const big = compareRef.current;
    const edge = big ? 660 : 380;
    const base = clone(sRef.current);
    const key = JSON.stringify([ph.id, edge, Object.entries(base).filter(([k]) => !THUMB_IGNORED.has(k))]);
    if (key === R.current.thumbKey) return;
    R.current.thumbKey = key;
    const gen = ++R.current.thumbGen;
    const pid = ph.id;
    const visible = infoRef.current!.stocks.filter((x) => showStockF(x.id)).map((x) => x.id);
    const order = [base.stock, ...visible.filter((x) => x !== base.stock),
      ...infoRef.current!.stocks.map((x) => x.id).filter((x) => x !== base.stock && !visible.includes(x))];
    eng.pool.sweep();
    for (const sid of order) {
      const st: Settings = { ...clone(base), stock: sid, halation: null, bloom: null, border: "none", strength: 100,
        grain: 0, sharpen: 0, dust: 0, scratches: 0, texture: "none", leak: "none" };
      eng.thumb(pid, edge, st, () => R.current.thumbGen !== gen || curId.current !== pid)
        .then(toBitmap)
        .then((bmp) => {
          if (R.current.thumbGen !== gen) return;
          setThumbs((m) => new Map(m).set(sid, bmp));
        })
        .catch((e) => { if (!(e instanceof Stale)) console.error(e); });
    }
  };
  const showStockF = (sid: string) => {
    const st = infoRef.current!.stocks.find((x) => x.id === sid)!;
    const f = prefsRef.current.filter;
    return f === "all" || (f === "bw" && st.kind === "bw") || (f === st.family && st.kind !== "bw");
  };

  // ------------------------------------------------------------------ white balance
  const asShot = (): [number, number] => {
    const ph = current();
    const ref = infoRef.current?.wb_reference ?? 5500;
    if (!ph?.header || !ph.header.scene_referred) return [ref, 0];
    return (ph.header.meta.as_shot_wb as [number, number]) ?? [ref, 0];
  };
  const wbValues = (): [number, number] => {
    const [T0, t0] = asShot();
    const st = sRef.current!;
    return [st.wb_temp ?? T0, st.wb_tint ?? t0];
  };
  const wbPresetName = () => {
    const st = sRef.current!;
    if (st.wb_temp === null && st.wb_tint === null) return "As shot";
    for (const [k, v] of Object.entries(infoRef.current!.wb_presets)) {
      if (v && Math.abs(v[0] - (st.wb_temp ?? 0)) < 1 && Math.abs(v[1] - (st.wb_tint ?? 0)) < 0.5) return k;
    }
    return "Custom";
  };
  const setWb = (temp?: number, tint?: number) => {
    const [T, t] = wbValues();
    update((n) => {
      n.wb_temp = temp ?? T;
      n.wb_tint = tint ?? t;
    }, temp !== undefined ? "wb_temp" : "wb_tint");
  };
  const picked = async (nx: number, ny: number) => {
    const ph = current();
    if (!ph?.header) return;
    setMode("view");
    try {
      const [T, tint] = await engine.current!.pick(ph.id, nx, ny, sRef.current!, false);
      update((n) => { n.wb_temp = T; n.wb_tint = tint; }, "wb_temp");
      say(`White balance set to ${T.toFixed(0)} K, tint ${tint >= 0 ? "+" : ""}${tint.toFixed(0)}`);
    } catch (e: any) {
      say(`Eyedropper failed: ${e?.message ?? e}`);
    }
  };

  // ------------------------------------------------------------------ crop
  const imgAspect = () => {
    const ph = current();
    if (!ph?.header) return 1.5;
    const [h, w] = ph.header.preview_shape;
    return w / h;
  };
  const ratio = (): number | null => {
    const k = sRef.current!.aspect;
    if (k === "free") return null;
    let r = k === "original" ? imgAspect() : ASPECT_RATIOS[k];
    if (aspectFlip.current) r = 1 / r;
    return r;
  };
  const applyRatio = (aspect: string) => {
    update((n) => {
      n.aspect = aspect;
      const cur = sRef.current!;
      const rr = (() => {
        if (aspect === "free") return null;
        let r = aspect === "original" ? imgAspect() : ASPECT_RATIOS[aspect];
        if (aspectFlip.current) r = 1 / r;
        return r;
      })();
      if (rr === null) n.crop = cur.crop ?? [0, 0, 1, 1];
      else {
        const c = cur.crop ?? [0, 0, 1, 1];
        n.crop = fitAspect(rr, imgAspect(), [(c[0] + c[2]) / 2, (c[1] + c[3]) / 2]);
      }
      if (aspect === "original" && !aspectFlip.current) n.crop = null;
    }, "crop");
    if (modeRef.current !== "crop") toggleCrop(true);
  };
  const toggleCrop = (on: boolean) => {
    setMode(on ? "crop" : "view");
    view.current?.setZoom(null);
    setTile(null);
    tileShown.current = false;
    R.current.tileInfo = null;
    R.current.previewRev = -1;
    requestRender();
  };
  const cropDragged = (c: [number, number, number, number], final: boolean) => {
    if (!final) return;
    const full = c[0] <= 0.001 && c[1] <= 0.001 && c[2] >= 0.999 && c[3] >= 0.999;
    beginEdit();
    const n = clone(sRef.current!);
    n.crop = full ? null : c.map(Number) as Settings["crop"];
    sRef.current = n;
    setS(n);
    storeCurrent();
    later("save", 600, flushSave);
    later("idle", 320, settled);
  };
  const clearCrop = () => {
    aspectFlip.current = false;
    update((n) => { n.crop = null; n.aspect = "original"; }, "crop");
  };

  // ------------------------------------------------------------------ looks, copy/paste, reset
  const reseed = () => set("seed", 1 + Math.floor(Math.random() * 1e6));
  const shuffleLeak = () => update((n) => {
    n.leak_seed = 1 + Math.floor(Math.random() * 1e6);
    if (n.leak === "none") n.leak = "edge_fog";
  }, "leak_seed");
  const resetAdjustments = () => {
    const stock = sRef.current!.stock;
    aspectFlip.current = false;
    update((n) => Object.assign(n, defaults(), { stock }), "stock");
  };
  const resetPhoto = () => {
    if (!current()) return;
    aspectFlip.current = false;
    if (modeRef.current !== "view") toggleCrop(false);
    update((n) => Object.assign(n, defaults()), "stock");
    say(`All edits removed from this photo — ${MOD}Z to bring them back`, 5000);
  };
  const resetPhotos = (ids: string[]) => {
    for (const id of ids) {
      const ph = photos.current.find((p) => p.id === id);
      if (!ph) continue;
      if (id === curId.current) {
        resetPhoto();
        continue;
      }
      ph.undo.push(clone(ph.settings ?? defaults()));
      ph.redo = [];
      ph.settings = defaults();
      ph.committed = clone(ph.settings);
      photosDb.patch(id, { settings: ph.settings });
    }
    say(`Reset ${ids.length} photo${ids.length === 1 ? "" : "s"}`);
  };
  const copySettings = () => {
    clipboard.current = clone(sRef.current!);
    say("Settings copied", 3000);
  };
  const pasteSettings = () => {
    if (!clipboard.current) return;
    const c = clone(clipboard.current);
    update((n) => Object.assign(n, c), "stock");
  };
  const syncSelected = () => {
    const ids = [...selected].filter((id) => id !== curId.current);
    for (const id of ids) {
      const ph = photos.current.find((p) => p.id === id);
      if (!ph) continue;
      ph.settings = clone(sRef.current!);
      photosDb.patch(id, { settings: ph.settings });
    }
    say(`Applied this look to ${ids.length} photo(s)`);
  };
  const savePreset = () => {
    const name = window.prompt("Name this look:")?.trim();
    if (!name) return;
    const next = { ...presets, [name]: clone(sRef.current!) };
    setPresets(next);
    kv.set("presets", next);
    setPresetSel(name);
  };
  const deletePreset = () => {
    if (!presetSel || !presets[presetSel]) return;
    const next = { ...presets };
    delete next[presetSel];
    setPresets(next);
    kv.set("presets", next);
    setPresetSel("");
  };
  const applyPreset = (name: string) => {
    setPresetSel(name);
    const p = presets[name];
    if (!p) return;
    update((n) => Object.assign(n, clone(p)), "stock");
  };

  // ------------------------------------------------------------------ export
  const pickExportDir = async (): Promise<FileSystemDirectoryHandle | null | "download"> => {
    const w = window as any;
    if (!w.showDirectoryPicker) return "download";
    try {
      const d: FileSystemDirectoryHandle = await w.showDirectoryPicker({ id: "grainhause-export", mode: "readwrite", startIn: exportDir.current ?? "pictures" });
      exportDir.current = d;
      kv.set("exportDir", d);
      return d;
    } catch {
      return null;
    }
  };
  const writeOut = async (dir: FileSystemDirectoryHandle | "download", name: string, data: Uint8Array, type: string) => {
    if (dir === "download") {
      download(data as BlobPart, name, type);
      return;
    }
    const fh = await dir.getFileHandle(name, { create: true });
    const wr = await fh.createWritable();
    await wr.write(data as BlobPart);
    await wr.close();
  };
  const runExport = async (targets: Photo[], dir: FileSystemDirectoryHandle | "download") => {
    const eng = engine.current!;
    const { format: fmt, space, size, quality } = prefsRef.current;
    const sizeN = parseInt(size, 10);
    let cancelled = false;
    const done: string[] = [];
    const n = targets.length;
    setProgress({ label: "Developing…", frac: 0, cancel: () => { cancelled = true; eng.pool.sweep(); } });
    try {
      for (let i = 0; i < n && !cancelled; i++) {
        const ph = targets[i];
        const st = clone(ph.id === curId.current ? sRef.current! : (ph.settings ?? sRef.current!));
        setProgress((p) => p && { ...p, label: `Developing ${ph.name}${n > 1 ? ` (${i + 1} of ${n})` : ""}…`, frac: i / n });
        if (!ph.header || !eng.header(ph.id)) {
          ph.header = await eng.open(ph.id, ph.name, new Uint8Array(await ph.file.arrayBuffer()));
        }
        const L = eng.fullEdge(ph.id);
        const edge = sizeN && L > sizeN ? sizeN : L;
        const bits = fmt === "tif" ? 16 : 8;
        const img = await eng.renderFull(ph.id, edge, st, null, {
          bits, space, prio: PRIO.export, bands: eng.size * 3, stale: () => cancelled,
          progress: (f) => setProgress((p) => p && { ...p, frac: (i + f * 0.9) / n }),
        });
        if (cancelled) break;
        setProgress((p) => p && { ...p, label: `Saving ${ph.name}…` });
        const bytes = await eng.encode(ph.id, img, fmt, space, quality);
        const name = `${stem(ph.name)}_${st.stock}.${fmt}`;
        await writeOut(dir, name, bytes, MIME[fmt]);
        done.push(name);
      }
      say(cancelled ? `Export cancelled (${done.length} saved)` : `Exported ${done.length} file(s)${dir !== "download" ? ` to ${dir.name}` : ""}`, 6000);
    } catch (e: any) {
      if (!(e instanceof Stale)) {
        console.error(e);
        say(`Export failed: ${String(e?.message ?? e).split("\n").filter(Boolean).pop()}`, 10000);
      }
    } finally {
      setProgress(null);
    }
  };
  const exportPhotos = async (which: "current" | "selected") => {
    storeCurrent();
    const cur = current();
    const targets = which === "current" ? (cur ? [cur] : []) : photos.current.filter((p) => selected.has(p.id));
    if (!targets.length && cur) targets.push(cur);
    if (!targets.length) return;
    const dir = await pickExportDir();
    if (dir === null) return;
    runExport(targets, dir);
  };
  const quickSave = async () => {
    const cur = current();
    if (!cur) return;
    const d = exportDir.current;
    if (!d) return exportPhotos("current");
    try {
      const perm = await (d as any).requestPermission?.({ mode: "readwrite" });
      if (perm && perm !== "granted") return exportPhotos("current");
    } catch {
      return exportPhotos("current");
    }
    storeCurrent();
    say(`Saving to ${d.name}…`, 4000);
    runExport([cur], d);
  };
  const exportLut = async () => {
    try {
      const txt = await engine.current!.cube(sRef.current!);
      download(txt, `GrainHause ${stockOf(sRef.current!.stock).name}.cube`, "text/plain");
      say("LUT saved", 4000);
    } catch (e: any) {
      say(`LUT export failed: ${e?.message ?? e}`);
    }
  };

  // ------------------------------------------------------------------ view / compare
  const onView = () => {
    const v = view.current;
    if (!v) return;
    const st = v.view();
    setZoomLabel(st.zoom === null ? "Fit" : `${Math.round(v.zoomDevice() * 100)}%`);
    R.current.tileGen++;
    R.current.swatchManual = false;
    later("swatch", 15, () => renderSwatch());
    later("zoom", 140, renderNow);
  };
  const showCompare = (on: boolean) => {
    if (on && !current()?.header) return;
    compareRef.current = on;
    setCompare(on);
    setTimeout(renderStockThumbs, 0);
  };
  const pickStock = (sid: string) => {
    if (sid === sRef.current?.stock) return;
    set("stock", sid);
    later("thumbs", 700, renderStockThumbs);
  };

  // ------------------------------------------------------------------ keyboard & drag-and-drop
  const keys = useRef<(e: KeyboardEvent, down: boolean) => void>(() => {});
  keys.current = (e, down) => {
    if (!info || !sRef.current) return;
    const t = e.target as HTMLElement;
    const typing = t && (t.tagName === "INPUT" && (t as HTMLInputElement).type !== "range" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
    if (e.key === "\\") {
      if (typing) return;
      e.preventDefault();
      if (!e.repeat) setBefore(down);
      return;
    }
    if (!down || typing) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key.toLowerCase();
    if (mod) {
      const act: Record<string, () => void> = {
        s: quickSave, o: () => (e.shiftKey ? folderInput : fileInput).current?.click(),
        e: () => exportPhotos(e.shiftKey ? "selected" : "current"),
        z: () => stepHistory(!e.shiftKey), y: () => stepHistory(false),
        c: () => e.shiftKey && copySettings(), v: () => e.shiftKey && pasteSettings(),
        r: () => (e.shiftKey ? resetPhoto() : resetAdjustments()),
        "=": () => view.current?.zoomBy(1.5), "+": () => view.current?.zoomBy(1.5), "-": () => view.current?.zoomBy(1 / 1.5),
        "0": () => view.current?.setZoom(null), "1": () => view.current?.setZoom(1.0),
      };
      const fn = act[k];
      if (fn && !((k === "c" || k === "v") && !e.shiftKey)) {
        e.preventDefault();
        fn();
      }
      return;
    }
    if (e.key === "Escape") {
      if (compareRef.current) return showCompare(false);
      if (modeRef.current !== "view") toggleCrop(false);
      return;
    }
    if (e.key === "Enter" && modeRef.current === "crop") return toggleCrop(false);
    if (k === "c" && current()) return showCompare(!compareRef.current);
    if (k === "z" && current()) return view.current?.setZoom(view.current.view().zoom !== null ? null : 1.0);
    if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.key) && photos.current.length && t?.tagName !== "INPUT") {
      e.preventDefault();
      const i = photos.current.findIndex((p) => p.id === curId.current);
      const j = e.key === "ArrowRight" || e.key === "ArrowDown" ? Math.min(i + 1, photos.current.length - 1) : Math.max(i - 1, 0);
      if (photos.current[j]) selectPhoto(photos.current[j].id);
    }
  };
  useEffect(() => {
    const kd = (e: KeyboardEvent) => keys.current(e, true);
    const ku = (e: KeyboardEvent) => keys.current(e, false);
    const over = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) {
        e.preventDefault();
        document.body.classList.add("dropping");
      }
    };
    const leave = (e: DragEvent) => {
      if (!e.relatedTarget) document.body.classList.remove("dropping");
    };
    const drop = async (e: DragEvent) => {
      e.preventDefault();
      document.body.classList.remove("dropping");
      const items = e.dataTransfer?.items;
      const files: File[] = [];
      if (items) {
        const walk = async (en: any): Promise<void> => {
          if (!en) return;
          if (en.isFile) files.push(await new Promise<File>((res, rej) => en.file(res, rej)));
          else if (en.isDirectory) {
            const rd = en.createReader();
            for (;;) {
              const batch: any[] = await new Promise((res, rej) => rd.readEntries(res, rej));
              if (!batch.length) break;
              for (const x of batch) await walk(x);
            }
          }
        };
        const entries = [...items].map((it) => (it as any).webkitGetAsEntry?.());
        if (entries.some(Boolean)) for (const en of entries) await walk(en);
        else files.push(...[...(e.dataTransfer?.files ?? [])]);
      }
      if (files.length) addFiles(files);
    };
    const blur = () => keys.current({ key: "\\", repeat: false, target: document.body } as any, false);
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    window.addEventListener("blur", blur);
    const bye = () => { storeCurrent(); flushSave(); saveSession(); };
    window.addEventListener("pagehide", bye);
    return () => {
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
      window.removeEventListener("blur", blur);
      window.removeEventListener("pagehide", bye);
    };
  }, []);

  // the grain sample shows a developed grey ramp until a photo is open
  useEffect(() => {
    if (info && !curId.current) renderSwatch();
  }, [info]);

  // re-render when the crop mode toggles or the frame changes shape
  useEffect(() => {
    if (current()?.header) {
      R.current.previewRev = -1;
      requestRender();
    }
  }, [mode]);

  // ------------------------------------------------------------------ list interaction
  const clickPhoto = (e: React.MouseEvent, id: string) => {
    if (e.shiftKey && anchor.current) {
      const ids = photos.current.map((p) => p.id);
      const [i, j] = [ids.indexOf(anchor.current), ids.indexOf(id)].sort((a, b) => a - b);
      setSelected(new Set(ids.slice(i, j + 1)));
      return;
    }
    if (e.metaKey || e.ctrlKey) {
      const n = new Set(selected);
      if (n.has(id)) n.delete(id); else n.add(id);
      setSelected(n);
      anchor.current = id;
      return;
    }
    selectPhoto(id);
  };

  // ------------------------------------------------------------------ render
  const stocksList = info?.stocks ?? [];
  const st = s;
  const ph = currentId ? photos.current.find((p) => p.id === currentId) ?? null : null;
  const stock = info && st ? stockOf(st.stock) : null;
  const fullSize = info && st ? viewFullSize() : null;
  const [T0, t0] = info && st ? asShot() : [5500, 0];
  const [wbT, wbt] = info && st ? wbValues() : [5500, 0];
  const grainBtn = useMemo(() => {
    if (!info || !st) return "";
    const cur = [st.grain ?? 100, st.grain_size ?? 100, st.grain_roughness];
    return Object.entries(info.grain_presets).find(([, v]) => v.every((x, i) => Math.abs(cur[i] - x) < 0.5))?.[0] ?? "";
  }, [info, st]);

  if (!info || !st || !stock) {
    return (
      <div className="boot">
        <img src="/icon.png" alt="" width={72} height={72} />
        <div className="brand big">GRAINHAUSE</div>
        {boot.error ? (
          <p className="boot-error">The film engine couldn't start: {boot.error}<br />Try reloading, or use a current version of Chrome, Edge, Safari or Firefox.</p>
        ) : (
          <>
            <p>{boot.msg}</p>
            <div className="boot-bar"><div style={{ width: `${boot.total ? 15 + 85 * (boot.ready / boot.total) : 8}%` }} /></div>
            <p className="dim">Everything runs on your computer — your photos are never uploaded.<br />The first visit downloads about 35 MB; after that it starts from cache.</p>
          </>
        )}
      </div>
    );
  }

  const sl = (label: string, k: SettingKey, lo: number, hi: number, o: { def?: number; step?: number; decimals?: number; suffix?: string; value?: number; title?: string; onChange?: (v: number) => void } = {}) => (
    <Slider label={label} lo={lo} hi={hi} step={o.step} decimals={o.decimals} suffix={o.suffix} title={o.title}
            value={o.value ?? (st[k] as number)} def={o.def ?? ((info.defaults[k] as number) ?? 0)}
            onChange={o.onChange ?? ((v) => set(k, v as never))} onReset={() => resetFields([k])} />
  );
  const sec = (title: string) => SECTION_FIELDS[title] ? () => resetFields(SECTION_FIELDS[title]) : undefined;
  const busyCount = engine.current?.pool.readyCount ?? 0;

  return (
    <div className={`app ${panelTab}`}>
      {/* ---------------- left: photos */}
      <aside className="left">
        <div className="brand-row">
          <img src="/icon.png" alt="" width={34} height={34} />
          <span className="brand">GRAINHAUSE</span>
          <button className="icon-btn" onClick={() => setHelp(true)} title="Help & shortcuts" aria-label="Help">?</button>
        </div>
        <div className="row">
          <button onClick={() => fileInput.current?.click()} title={`Open photos (${MOD}O)`}>Open…</button>
          <button onClick={() => folderInput.current?.click()} title={`Open a folder (${MOD}⇧O)`}>Folder…</button>
        </div>
        <input ref={fileInput} type="file" multiple hidden
               accept="image/*,.dng,.cr2,.cr3,.nef,.arw,.raf,.orf,.rw2,.pef,.srw,.3fr,.iiq,.rwl,.nrw,.erf,.kdc,.dcr,.mos,.mrw,.x3f,.heic,.heif,.hif,.tif,.tiff"
               onChange={(e) => { addFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
        <input ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: "", directory: "" } as any}
               onChange={(e) => { addFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
        <div className="filmstrip" onContextMenu={(e) => e.preventDefault()}>
          {photos.current.length === 0 && <div className="empty">Drop photos or a folder anywhere, or use Open…</div>}
          {photos.current.map((p) => (
            <button key={p.id}
                    className={`strip-item${p.id === currentId ? " current" : ""}${selected.has(p.id) ? " selected" : ""}`}
                    onClick={(e) => clickPhoto(e, p.id)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      const ids = selected.has(p.id) ? [...selected] : [p.id];
                      if (!selected.has(p.id)) setSelected(new Set([p.id]));
                      setMenu({ x: e.clientX, y: e.clientY, ids });
                    }}
                    title={p.error ? `${p.name} — ${p.error}` : p.name}>
              <div className="strip-thumb">
                {p.thumbUrl ? <img src={p.thumbUrl} alt="" /> : <span className="ph">{p.error ? "!" : ""}</span>}
              </div>
              <span className="strip-name">{p.name}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* ---------------- centre: preview */}
      <main className="centre">
        <div className="stack">
          <div className={compare ? "view hidden" : "view"}>
            <PreviewView ref={view} image={image} tile={tile} showTile={!beforeOn}
                         fullSize={fullSize} mode={mode} crop={st.crop} ratio={ratio()} imgAspect={imgAspect()}
                         badge={beforeOn ? "BEFORE" : ""} message={message}
                         onView={onView} onCrop={cropDragged} onPick={picked}
                         onSampleAt={(x, y) => { R.current.swatchCenter = [x, y]; R.current.swatchManual = true; later("swatch", 15, () => renderSwatch()); }} />
          </div>
          {compare && (
            <div className="compare">
              <div className="compare-top">
                <span className="compare-title">COMPARE STOCKS</span>
                <span className="dim">Click a stock to use it · Esc to go back</span>
                <span className="grow" />
                <label className="dim">Size <input type="range" min={200} max={520} value={prefs.compareSize}
                                                   onChange={(e) => setPrefs({ compareSize: +e.target.value })} /></label>
                <button onClick={() => showCompare(false)}>Back to photo</button>
              </div>
              <div style={{ "--cmp": `${prefs.compareSize}px` } as React.CSSProperties} className="compare-scroll">
                <StockGrid stocks={stocksList} current={st.stock} filter="all" thumbs={thumbs} big
                           onPick={(id) => { pickStock(id); showCompare(false); }} />
              </div>
            </div>
          )}
        </div>
        <div className="bar">
          <button onPointerDown={() => setBefore(true)} onPointerUp={() => setBefore(false)} onPointerLeave={() => setBefore(false)}
                  title="Hold to see the original (or hold \)">Hold for before ( \ )</button>
          <button onClick={resetPhoto} title={`Remove every change on this photo (${MOD}⇧R). ${MOD}Z brings them back.`}>Reset photo</button>
          <span className="sp" />
          <button className="tb" onClick={() => view.current?.setZoom(null)} title={`Fit to window (${MOD}0)`}>Fit</button>
          <button className="tb" onClick={() => view.current?.setZoom(1)} title={`Actual pixels (${MOD}1)`}>100%</button>
          <button className="tb" onClick={() => view.current?.zoomBy(1 / 1.5)} title={`Zoom out (${MOD}−)`}>−</button>
          <button className="tb" onClick={() => view.current?.zoomBy(1.5)} title={`Zoom in (${MOD}+)`}>+</button>
          <span className="zoom-label">{zoomLabel}</span>
          <span className="hint">pinch or {MOD}scroll to zoom · drag to pan</span>
          <span className="grow" />
          <span className="info">{ph?.header ? infoLine : ""}</span>
        </div>
      </main>

      {/* ---------------- right: controls */}
      <aside className="right">
        <div className="tabs">
          <button className={panelTab === "photos" ? "on" : ""} onClick={() => setPanelTab("photos")}>Photos</button>
          <button className={panelTab === "edit" ? "on" : ""} onClick={() => setPanelTab("edit")}>Develop</button>
        </div>
        <HistogramView hist={hist} />
        <div className="panel">
          <Section title="Film stock" onReset={sec("Film stock")}>
            <div className="filters">
              {([["all", "All"], ["Kodak", "Kodak"], ["Fujifilm", "Fujifilm"], ["CineStill", "CineStill"], ["bw", "B&W"]] as [Filter, string][]).map(([k, l]) => (
                <button key={k} className={prefs.filter === k ? "chip on" : "chip"} onClick={() => { setPrefs({ filter: k }); R.current.thumbKey = ""; later("thumbs", 50, renderStockThumbs); }}>{l}</button>
              ))}
            </div>
            <div className="stock-panel">
              <StockGrid stocks={stocksList} current={st.stock} filter={prefs.filter} thumbs={thumbs} onPick={pickStock} />
            </div>
            <button className="wide" onClick={() => showCompare(true)} disabled={!ph?.header}>⊞  Compare all stocks on this photo  (C)</button>
            <p className="blurb"><b>{stock.name}</b> — {stock.blurb} <span className="dim">({kindLabel(stock)}, ISO {stock.iso})</span></p>
            {sl("Film strength", "strength", 0, 100, { suffix: " %" })}
          </Section>

          <Section title="Exposure" onReset={sec("Exposure")}>
            <label className="check"><input type="checkbox" checked={st.auto_meter} onChange={(e) => set("auto_meter", e.target.checked)} /> Auto-meter RAW files (like a film camera)</label>
            {sl("Exposure", "exposure", -4, 4, { step: 0.05, decimals: 2, suffix: " EV" })}
            {sl("Push / pull", "push", -2, 2, { step: 0.5, decimals: 1, suffix: " stops" })}
            {sl("Highlights", "highlights", -100, 100)}
            {sl("Shadows", "shadows", -100, 100)}
            {sl("Contrast", "contrast", -100, 100)}
          </Section>

          <Section title="White balance" onReset={sec("White balance")}>
            <div className="row">
              <Select value={wbPresetName()} options={[...Object.keys(info.wb_presets).map((k) => [k, k] as [string, string]), ["Custom", "Custom"]]}
                      onChange={(k) => {
                        if (k === "Custom") return;
                        const v = info.wb_presets[k];
                        update((n) => { [n.wb_temp, n.wb_tint] = v ? [v[0], v[1]] : [null, null]; }, "wb_temp");
                      }} />
              <button className={mode === "eyedropper" ? "on" : ""} onClick={() => setMode(mode === "eyedropper" ? "view" : "eyedropper")}
                      disabled={!ph?.header} title="Click something in the photo that should be neutral grey or white">Eyedropper</button>
            </div>
            {sl("Temperature", "wb_temp", 2000, 12000, { step: 50, suffix: " K", value: wbT, def: T0, onChange: (v) => setWb(v, undefined) })}
            {sl("Tint", "wb_tint", -150, 150, { value: wbt, def: t0, onChange: (v) => setWb(undefined, v) })}
            <p className="note">Set as light hitting the film — the stock still adds its own colour.</p>
          </Section>

          <Section title="Colour & lab" onReset={sec("Colour & lab")}>
            <Labeled label="Process">
              <Select value={st.lab ?? stock.default_lab} options={info.labs.map((k) => [k, info.lab_labels[k]] as [string, string])}
                      disabled={stock.kind === "rev" || stock.id === "neutral"}
                      title={stock.kind === "rev" ? "Slide film is viewed directly — no scan or print step" : ""}
                      onChange={(v) => set("lab", v)} />
            </Labeled>
            {sl("Film depth", "depth", 0, 200, { suffix: " %", title: "How strongly each dye layer's development carves edges and separates tones" })}
            {sl("Saturation", "saturation", -100, 100)}
            {sl("Fade", "fade", 0, 100)}
          </Section>

          <Section title="Skin tones" onReset={sec("Skin tones")}>
            {sl("Skin warmth  (rosy ↔ golden)", "skin_warmth", -100, 100)}
            {sl("Skin saturation", "skin_saturation", -100, 100)}
            <p className="note">Only affects skin — works for every complexion.</p>
          </Section>

          <Section title="Tone curve" onReset={sec("Tone curve")}>
            <div className="seg">
              {(["rgb", "r", "g", "b"] as Channel[]).map((c) => (
                <button key={c} className={channel === c ? "on" : ""} onClick={() => setChannel(c)}>{{ rgb: "RGB", r: "Red", g: "Green", b: "Blue" }[c]}</button>
              ))}
            </div>
            <CurveEditor channel={channel} curves={{ rgb: st.curve_rgb, r: st.curve_r, g: st.curve_g, b: st.curve_b }}
                         onChange={(ch, pts) => set(`curve_${ch}` as SettingKey, pts as never)} />
            <div className="seg">
              {CURVE_PRESETS.map(([l, pts]) => <button key={l} onClick={() => set(`curve_${channel}` as SettingKey, clone(pts) as never)}>{l}</button>)}
            </div>
            <p className="note">Click to add a point · drag to move · double-click a point to remove</p>
          </Section>

          <Section title="Crop" onReset={sec("Crop")}>
            <Labeled label="Aspect">
              <Select value={st.aspect} options={info.aspects.map((k) => [k, info.aspect_labels[k]] as [string, string])}
                      onChange={(k) => { aspectFlip.current = false; applyRatio(k); }} />
            </Labeled>
            <div className="row">
              <button className={mode === "crop" ? "grow on" : "grow"} disabled={!ph?.header} onClick={() => toggleCrop(mode !== "crop")}>{mode === "crop" ? "Done" : "Crop…"}</button>
              <button onClick={() => { aspectFlip.current = !aspectFlip.current; applyRatio(st.aspect); }} disabled={!ph?.header}>↻ Rotate ratio</button>
              <button onClick={clearCrop}>Clear</button>
            </div>
          </Section>

          <Section title="Grain" onReset={sec("Grain")}>
            <Swatch ref={swatchView} image={swatch} onDrag={swatchDragged} onRelease={swatchReleased} />
            <div className="swatch-caption">{swatchCaption}</div>
            <div className="filters even">
              {Object.keys(info.grain_presets).map((name) => (
                <button key={name} className={grainBtn === name ? "chip on" : "chip"} onClick={() => {
                  const [g, size, rough] = info.grain_presets[name];
                  update((n) => { n.grain = g; n.grain_size = size; n.grain_roughness = rough; }, "grain");
                }}>{name}</button>
              ))}
            </div>
            {sl("Amount", "grain", 0, 200, { suffix: " %", value: st.grain ?? 100, def: 100 })}
            {sl("Size", "grain_size", 50, 250, { suffix: " %", value: st.grain_size ?? 100, def: 100 })}
            {sl("Roughness", "grain_roughness", 0, 100)}
            {sl("Film softness", "softness", 0, 200, { suffix: " %", value: st.softness ?? 100, def: 100 })}
            {sl("Sharpening", "sharpen", -100, 100, { title: "Drag right to sharpen edges and grain, left to soften" })}
            {sl("Clean digital noise", "denoise", 0, 100)}
            <button className="wide" onClick={reseed}>New grain pattern</button>
          </Section>

          <Section title="Light" onReset={sec("Light")}>
            {sl("Halation", "halation", 0, 100, { value: st.halation ?? stock.halation, def: stock.halation })}
            {sl("Bloom", "bloom", 0, 100, { value: st.bloom ?? stock.bloom, def: stock.bloom })}
            {sl("Vignette", "vignette", -100, 100)}
          </Section>

          <Section title="Light leaks" onReset={sec("Light leaks")}>
            <Labeled label="Style">
              <Select value={st.leak} options={info.leaks.map((k) => [k, info.leak_labels[k]] as [string, string])} onChange={(v) => set("leak", v)} />
            </Labeled>
            {sl("Leak amount", "leak_amount", 0, 100)}
            <button className="wide" onClick={shuffleLeak}>Shuffle leak</button>
          </Section>

          <Section title="Dust, scratches & texture" onReset={sec("Dust, scratches & texture")}>
            {sl("Dust", "dust", 0, 100)}
            {sl("Scratches", "scratches", 0, 100)}
            <Labeled label="Texture">
              <Select value={st.texture} options={info.textures.map((k) => [k, info.texture_labels[k]] as [string, string])} onChange={(v) => set("texture", v)} />
            </Labeled>
            {sl("Texture amount", "texture_amount", 0, 100)}
            <button className="wide" onClick={reseed}>Shuffle dust & scratches</button>
          </Section>

          <Section title="Frame & border" onReset={sec("Frame & border")}>
            <Labeled label="Border">
              <Select value={st.border} options={info.borders.map((k) => [k, info.border_labels[k]] as [string, string])} onChange={(v) => set("border", v)} />
            </Labeled>
          </Section>

          <Section title="Your looks">
            <div className="row">
              <Select value={presetSel} options={[["", "Choose a saved look…"], ...Object.keys(presets).sort().map((k) => [k, k] as [string, string])]}
                      onChange={applyPreset} />
              <button onClick={savePreset}>Save</button>
              <button onClick={deletePreset} disabled={!presetSel}>Delete</button>
            </div>
            <div className="grid2">
              <button onClick={copySettings} title={`${MOD}⇧C`}>Copy</button>
              <button onClick={pasteSettings} title={`${MOD}⇧V`}>Paste</button>
              <button onClick={syncSelected} title="Apply this look to the photos selected in the list (⌘/Ctrl-click or Shift-click to select)">Sync to selected</button>
              <button onClick={resetAdjustments} title={`${MOD}R — keeps the film stock`}>Reset adjustments</button>
            </div>
          </Section>

          <Section title="Export">
            <Labeled label="Format">
              <Select value={prefs.format} options={FORMATS} onChange={(v) => setPrefs({ format: v })} />
            </Labeled>
            <Labeled label="Colour space">
              <Select value={prefs.space} options={info.output_spaces.map((k) => [k, k] as [string, string])} onChange={(v) => setPrefs({ space: v })} />
            </Labeled>
            <Labeled label="Long edge">
              <Select value={prefs.size} options={SIZES} onChange={(v) => setPrefs({ size: v })} />
            </Labeled>
            <Slider label="JPEG quality" lo={60} hi={100} value={prefs.quality} def={95} onChange={(v) => setPrefs({ quality: v })} onReset={() => setPrefs({ quality: 95 })} />
            <div className="row">
              <button className="grow primary" onClick={() => exportPhotos("current")} disabled={!ph?.header} title={`${MOD}E  ·  ${MOD}S saves again to the last folder`}>Export this</button>
              <button className="grow" onClick={() => exportPhotos("selected")} disabled={!photos.current.length} title={`${MOD}⇧E`}>Export selected</button>
            </div>
            <button className="wide" onClick={exportLut}>Export look as .cube LUT (for video)</button>
            <p className="note">{(window as any).showDirectoryPicker ? "You choose a folder; exports are written straight into it." : "Exports download to your Downloads folder."}</p>
          </Section>
        </div>
      </aside>

      {/* ---------------- status, menus, dialogs */}
      <div className="status">{status}{busyCount && engine.current && busyCount < engine.current.size ? <span className="dim">  ·  warming up film engine ({busyCount}/{engine.current.size})</span> : null}</div>

      {menu && (
        <div className="menu-scrim" onClick={() => setMenu(null)} onContextMenu={(e) => { e.preventDefault(); setMenu(null); }}>
          <div className="menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
            <button onClick={() => { resetPhotos(menu.ids); setMenu(null); }}>Reset {menu.ids.length > 1 ? `${menu.ids.length} photos` : "photo"}</button>
            <button onClick={() => { exportPhotos("selected"); setMenu(null); }}>Export {menu.ids.length > 1 ? `${menu.ids.length} photos` : "photo"}…</button>
            <button onClick={() => { removePhotos(menu.ids); setMenu(null); }}>Remove from list</button>
          </div>
        </div>
      )}

      {progress && (
        <div className="modal-scrim">
          <div className="modal">
            <p>{progress.label}</p>
            <div className="boot-bar"><div style={{ width: `${Math.round(progress.frac * 100)}%` }} /></div>
            {progress.cancel && <button onClick={progress.cancel}>Cancel</button>}
          </div>
        </div>
      )}

      {help && (
        <div className="modal-scrim" onClick={() => setHelp(false)}>
          <div className="modal help" onClick={(e) => e.stopPropagation()}>
            <h2>GrainHause {info.version}</h2>
            <p>Film emulation for RAW, TIFF, JPEG and HEIC — the same film engine as the desktop app, running entirely in your browser. Your photos never leave this computer; the photo list and your edits are kept in this browser.</p>
            <table>
              <tbody>
                {[
                  [`${MOD}O / ${MOD}⇧O`, "Open photos / a folder (or drop them anywhere)"],
                  ["\\ (hold)", "See the original"],
                  ["C", "Compare all stocks on this photo"],
                  [`Z, double-click`, "Toggle Fit / 100%"],
                  [`${MOD}+ ${MOD}− ${MOD}0 ${MOD}1`, "Zoom in, out, Fit, 100%"],
                  [`${MOD}Z / ${MOD}⇧Z`, "Undo / redo"],
                  [`${MOD}⇧C / ${MOD}⇧V`, "Copy / paste settings"],
                  [`${MOD}E / ${MOD}⇧E`, "Export this / selected"],
                  [`${MOD}S`, "Save again to the last export folder"],
                  ["← →", "Previous / next photo"],
                  ["Esc / Enter", "Leave crop or eyedropper"],
                ].map(([k, v]) => <tr key={k}><td className="kbd">{k}</td><td>{v}</td></tr>)}
              </tbody>
            </table>
            <p className="dim">Stock parameters are tuned from each film's datasheet curves and known character; they are not lab measurements of real film, and film names are used only to describe the look.</p>
            <button onClick={() => setHelp(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
