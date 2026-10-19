import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";

export type Mode = "view" | "crop" | "eyedropper";
type Crop = [number, number, number, number];

export type ViewState = { zoom: number | null; center: [number, number] | null };

export type PreviewHandle = {
  setZoom: (z: number | null, anchor?: [number, number]) => void;
  zoomBy: (f: number) => void;
  zoomDevice: () => number;
  visibleRegion: (margin?: number) => [number, number, number, number];
  view: () => ViewState;
};

type Props = {
  image: ImageBitmap | null;
  tile: { bmp: ImageBitmap; rect: [number, number, number, number] } | null;
  showTile: boolean;
  fullSize: [number, number] | null; // W, H of the (cropped) full image
  mode: Mode;
  crop: Crop | null;
  ratio: number | null;
  imgAspect: number;
  badge: string;
  message: string;
  onView: (v: ViewState) => void;
  onCrop: (c: Crop, final: boolean) => void;
  onPick: (nx: number, ny: number) => void;
  onSampleAt: (nx: number, ny: number) => void;
};

const MAX_ZOOM = 8;

/**
 * The developed photo with zoom and pan (zoom = device px per full-resolution px, null = Fit),
 * a crop mode with aspect lock and rule-of-thirds guides, and an eyedropper mode.
 */
export const PreviewView = forwardRef<PreviewHandle, Props>(function PreviewView(p, ref) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const st = useRef<ViewState>({ zoom: null, center: null });
  const props = useRef(p);
  props.current = p;
  const cropLocal = useRef<Crop | null>(null);
  const drag = useRef<{ kind: string; x: number; y: number; orig: Crop } | null>(null);
  const pan = useRef<{ x: number; y: number; c: [number, number] } | null>(null);
  const pointers = useRef(new Map<number, [number, number]>());
  const pinch = useRef<{ d: number; z: number } | null>(null);

  const dpr = () => window.devicePixelRatio || 1;
  const box = () => canvas.current!.getBoundingClientRect();
  const size = (): [number, number] => {
    const { fullSize, image } = props.current;
    if (fullSize) return fullSize;
    if (image) return [image.width, image.height];
    return [1, 1];
  };
  const fitScale = () => {
    const [W, H] = size();
    const r = box();
    return Math.max(1e-6, Math.min((r.width - 32) / W, (r.height - 32) / H));
  };
  const scale = () => (st.current.zoom === null ? fitScale() : st.current.zoom / dpr());
  const zoomDevice = () => scale() * dpr();
  const centre = (): [number, number] => {
    const [W, H] = size();
    if (st.current.zoom === null || !st.current.center) return [W / 2, H / 2];
    return st.current.center;
  };
  const toScreen = (x: number, y: number): [number, number] => {
    const [cx, cy] = centre();
    const z = scale();
    const r = box();
    return [(x - cx) * z + r.width / 2, (y - cy) * z + r.height / 2];
  };
  const toImage = (px: number, py: number): [number, number] => {
    const [cx, cy] = centre();
    const z = scale();
    const r = box();
    return [(px - r.width / 2) / z + cx, (py - r.height / 2) / z + cy];
  };
  const imageRect = () => {
    const [W, H] = size();
    const [x0, y0] = toScreen(0, 0);
    const [x1, y1] = toScreen(W, H);
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  };
  const clamp = () => {
    const s = st.current;
    if (s.zoom === null || !s.center) return;
    const [W, H] = size();
    const z = scale();
    const r = box();
    const hw = r.width / (2 * z), hh = r.height / (2 * z);
    const cx = hw * 2 >= W ? W / 2 : Math.min(W - hw, Math.max(hw, s.center[0]));
    const cy = hh * 2 >= H ? H / 2 : Math.min(H - hh, Math.max(hh, s.center[1]));
    s.center = [cx, cy];
  };
  const emit = () => props.current.onView({ ...st.current });

  const draw = useCallback(() => {
    const c = canvas.current;
    if (!c) return;
    const r = c.getBoundingClientRect();
    const k = dpr();
    if (c.width !== Math.round(r.width * k) || c.height !== Math.round(r.height * k)) {
      c.width = Math.round(r.width * k);
      c.height = Math.round(r.height * k);
    }
    const g = c.getContext("2d")!;
    g.setTransform(k, 0, 0, k, 0, 0);
    g.fillStyle = "#111113";
    g.fillRect(0, 0, r.width, r.height);
    const { image, tile, showTile, mode, badge, message } = props.current;
    if (image) {
      const ir = imageRect();
      g.imageSmoothingEnabled = true;
      g.imageSmoothingQuality = "high";
      g.drawImage(image, ir.x, ir.y, ir.w, ir.h);
      if (showTile && tile && st.current.zoom !== null) {
        const [x0, y0, x1, y1] = tile.rect;
        const [sx0, sy0] = toScreen(x0, y0);
        const [sx1, sy1] = toScreen(x1, y1);
        g.imageSmoothingEnabled = zoomDevice() < 2.0; // past 200% show real pixels, not a blur
        g.drawImage(tile.bmp, sx0, sy0, sx1 - sx0, sy1 - sy0);
        g.imageSmoothingEnabled = true;
      }
      if (mode === "crop") paintCrop(g, ir);
    } else {
      g.fillStyle = "#777";
      g.font = "13px Inter, system-ui, sans-serif";
      g.textAlign = "center";
      g.fillText(message, r.width / 2, r.height / 2);
      g.textAlign = "left";
    }
    g.font = "12px Inter, system-ui, sans-serif";
    if (badge) {
      g.fillStyle = "#e4e7ec";
      g.fillText(badge, 14, 22);
    }
    if (mode === "eyedropper") {
      g.fillStyle = "#e4e7ec";
      g.fillText("Click something that should be neutral grey or white  ·  Esc to cancel", 14, r.height - 14);
    }
  }, []);

  const cropNow = (): Crop => cropLocal.current ?? props.current.crop ?? [0, 0, 1, 1];
  const cropPx = () => {
    const ir = imageRect();
    const [x0, y0, x1, y1] = cropNow();
    return { x: ir.x + x0 * ir.w, y: ir.y + y0 * ir.h, w: (x1 - x0) * ir.w, h: (y1 - y0) * ir.h };
  };
  const paintCrop = (g: CanvasRenderingContext2D, ir: { x: number; y: number; w: number; h: number }) => {
    const c = cropPx();
    g.fillStyle = "rgba(0,0,0,0.59)";
    g.fillRect(ir.x, ir.y, ir.w, c.y - ir.y);
    g.fillRect(ir.x, c.y + c.h, ir.w, ir.y + ir.h - (c.y + c.h));
    g.fillRect(ir.x, c.y, c.x - ir.x, c.h);
    g.fillRect(c.x + c.w, c.y, ir.x + ir.w - (c.x + c.w), c.h);
    g.strokeStyle = "rgba(255,255,255,0.43)";
    g.lineWidth = 1;
    for (const i of [1, 2]) {
      g.beginPath();
      g.moveTo(c.x + (c.w * i) / 3, c.y); g.lineTo(c.x + (c.w * i) / 3, c.y + c.h);
      g.moveTo(c.x, c.y + (c.h * i) / 3); g.lineTo(c.x + c.w, c.y + (c.h * i) / 3);
      g.stroke();
    }
    g.strokeStyle = "#e4e7ec";
    g.lineWidth = 2;
    g.strokeRect(c.x, c.y, c.w, c.h);
    g.fillStyle = "#e4e7ec";
    for (const [x, y] of [[c.x, c.y], [c.x + c.w, c.y], [c.x, c.y + c.h], [c.x + c.w, c.y + c.h]]) g.fillRect(x - 5, y - 5, 10, 10);
    const cr = cropNow();
    const ratio = ((cr[2] - cr[0]) * props.current.imgAspect) / (cr[3] - cr[1]);
    g.font = "12px Inter, system-ui, sans-serif";
    g.fillText(`${ratio.toFixed(2)} : 1`, c.x + 6, c.y - 7);
  };

  const setZoom = (z: number | null, anchor?: [number, number]) => {
    const s = st.current;
    const pr = props.current;
    if (pr.mode === "crop" || !pr.image) {
      if (s.zoom !== null) {
        s.zoom = null;
        s.center = null;
        draw();
        emit();
      }
      return;
    }
    const fitDev = fitScale() * dpr();
    if (z !== null) {
      z = Math.min(MAX_ZOOM, Math.max(Math.min(fitDev, 1) * 0.999, z));
      if (z <= fitDev * 1.001) z = null;
    }
    const r = box();
    const a = anchor ?? [r.width / 2, r.height / 2];
    const [ix, iy] = toImage(a[0], a[1]);
    s.zoom = z;
    if (z !== null) {
      const zl = z / dpr();
      s.center = [ix - (a[0] - r.width / 2) / zl, iy - (a[1] - r.height / 2) / zl];
      clamp();
    } else s.center = null;
    draw();
    emit();
  };

  useImperativeHandle(ref, () => ({
    setZoom,
    zoomBy: (f) => setZoom(zoomDevice() * f),
    zoomDevice,
    visibleRegion: (margin = 0.08) => {
      const [W, H] = size();
      const r = box();
      let [x0, y0] = toImage(0, 0);
      let [x1, y1] = toImage(r.width, r.height);
      const mx = (x1 - x0) * margin, my = (y1 - y0) * margin;
      x0 = Math.max(0, Math.floor(x0 - mx)); y0 = Math.max(0, Math.floor(y0 - my));
      x1 = Math.min(W, Math.ceil(x1 + mx)); y1 = Math.min(H, Math.ceil(y1 + my));
      return [x0, y0, Math.max(x0 + 1, x1), Math.max(y0 + 1, y1)];
    },
    view: () => ({ ...st.current }),
  }));

  useEffect(() => {
    if (p.mode === "crop" && st.current.zoom !== null) setZoom(null);
    if (!drag.current) cropLocal.current = null;
    clamp();
    draw();
  });

  useEffect(() => {
    const c = canvas.current!;
    const ro = new ResizeObserver(() => {
      clamp();
      draw();
      if (st.current.zoom !== null) emit();
    });
    ro.observe(c);
    const onWheel = (e: WheelEvent) => {
      const pr = props.current;
      if (!pr.image || pr.mode === "crop") return;
      e.preventDefault();
      const r = box();
      const a: [number, number] = [e.clientX - r.left, e.clientY - r.top];
      if (e.ctrlKey || e.metaKey) {
        // trackpad pinch arrives as ctrl+wheel
        const dy = -e.deltaY * (e.deltaMode === 1 ? 16 : 1);
        setZoom(zoomDevice() * 2 ** (dy / (e.ctrlKey && !e.metaKey ? 120 : 360)), a);
      } else if (st.current.zoom !== null) {
        const z = scale();
        const [cx, cy] = centre();
        st.current.center = [cx + e.deltaX / z, cy + e.deltaY / z];
        clamp();
        draw();
        emit();
      }
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      ro.disconnect();
      c.removeEventListener("wheel", onWheel);
    };
  }, []);

  const local = (e: React.PointerEvent | React.MouseEvent): [number, number] => {
    const r = box();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  const onDown = (e: React.PointerEvent) => {
    const pr = props.current;
    if (!pr.image) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const pos = local(e);
    pointers.current.set(e.pointerId, pos);
    if (pointers.current.size === 2 && pr.mode !== "crop") {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: zoomDevice() };
      pan.current = null;
      return;
    }
    if (pr.mode === "eyedropper") {
      const [W, H] = size();
      const [x, y] = toImage(...pos);
      if (x >= 0 && x <= W && y >= 0 && y <= H) pr.onPick(x / W, y / H);
      return;
    }
    if (pr.mode === "crop") {
      const c = cropPx();
      const corners: Record<string, [number, number]> = {
        tl: [c.x, c.y], tr: [c.x + c.w, c.y], bl: [c.x, c.y + c.h], br: [c.x + c.w, c.y + c.h],
      };
      const tol = e.pointerType === "touch" ? 24 : 14;
      for (const [k, [x, y]] of Object.entries(corners)) {
        if (Math.abs(x - pos[0]) < tol && Math.abs(y - pos[1]) < tol) {
          drag.current = { kind: k, x: pos[0], y: pos[1], orig: [...cropNow()] as Crop };
          return;
        }
      }
      if (pos[0] >= c.x && pos[0] <= c.x + c.w && pos[1] >= c.y && pos[1] <= c.y + c.h)
        drag.current = { kind: "move", x: pos[0], y: pos[1], orig: [...cropNow()] as Crop };
      return;
    }
    if (st.current.zoom !== null) pan.current = { x: pos[0], y: pos[1], c: centre() };
  };

  const onMove = (e: React.PointerEvent) => {
    const pos = local(e);
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, pos);
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      setZoom(pinch.current.z * (d / Math.max(1, pinch.current.d)), [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
      return;
    }
    if (pan.current) {
      const z = scale();
      st.current.center = [pan.current.c[0] - (pos[0] - pan.current.x) / z, pan.current.c[1] - (pos[1] - pan.current.y) / z];
      clamp();
      draw();
      return;
    }
    const d = drag.current;
    if (!d || props.current.mode !== "crop") return;
    const ir = imageRect();
    const dx = (pos[0] - d.x) / ir.w, dy = (pos[1] - d.y) / ir.h;
    const [x0, y0, x1, y1] = d.orig;
    let next: Crop;
    if (d.kind === "move") {
      const w = x1 - x0, h = y1 - y0;
      const nx0 = Math.min(1 - w, Math.max(0, x0 + dx));
      const ny0 = Math.min(1 - h, Math.max(0, y0 + dy));
      next = [nx0, ny0, nx0 + w, ny0 + h];
    } else {
      const L = d.kind.includes("l"), T = d.kind.includes("t");
      const ax = L ? x1 : x0, ay = T ? y1 : y0;
      let mx = Math.min(1, Math.max(0, (L ? x0 : x1) + dx));
      let my = Math.min(1, Math.max(0, (T ? y0 : y1) + dy));
      let w = Math.abs(mx - ax), h = Math.abs(my - ay);
      const ratio = props.current.ratio, ia = props.current.imgAspect;
      if (ratio) {
        const wantH = (w * ia) / ratio;
        if (wantH <= h) h = wantH;
        else w = (h * ratio) / ia;
        const sx = L ? -1 : 1, sy = T ? -1 : 1;
        const maxw = sx < 0 ? ax : 1 - ax, maxh = sy < 0 ? ay : 1 - ay;
        const k = Math.min(1, maxw / Math.max(w, 1e-6), maxh / Math.max(h, 1e-6));
        w *= k; h *= k;
        mx = ax + sx * w; my = ay + sy * h;
      }
      w = Math.abs(mx - ax); h = Math.abs(my - ay);
      if (w < 0.03 || h < 0.03) return;
      next = [Math.min(ax, mx), Math.min(ay, my), Math.max(ax, mx), Math.max(ay, my)];
    }
    cropLocal.current = next;
    draw();
    props.current.onCrop(next, false);
  };

  const onUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pan.current) {
      pan.current = null;
      emit();
    }
    if (drag.current) {
      drag.current = null;
      const c = cropLocal.current;
      if (c) props.current.onCrop(c, true);
    }
  };

  const onDouble = (e: React.MouseEvent) => {
    const pr = props.current;
    if (pr.mode !== "view" || !pr.image) return;
    const [W, H] = size();
    const pos = local(e);
    const [x, y] = toImage(...pos);
    if (x >= 0 && x <= W && y >= 0 && y <= H) pr.onSampleAt(x / W, y / H);
    setZoom(st.current.zoom !== null ? null : 1.0, pos);
  };

  const cursor = p.mode === "eyedropper" ? "crosshair" : p.mode === "view" && st.current.zoom !== null ? "grab" : "default";
  return (
    <canvas
      ref={canvas}
      className="preview"
      style={{ cursor, touchAction: "none" }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onDoubleClick={onDouble}
    />
  );
});
