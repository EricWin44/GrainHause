import { useEffect, useRef } from "react";
import type { Curve } from "../types";

export type Channel = "rgb" | "r" | "g" | "b";
export const CURVE_COLOURS: Record<Channel, string> = { rgb: "#e6e6e6", r: "#ff6b5e", g: "#6be08a", b: "#6aa8ff" };

/** Same monotone (PCHIP) interpolation the engine uses for the tone curve (scipy's PchipInterpolator). */
export function pchip(pts: Curve, n = 128): [number[], number[]] {
  const sorted = [...pts].sort((a, b) => a[0] - b[0]);
  const xs: number[] = [], ys: number[] = [];
  for (const [x, y] of sorted) {
    if (xs.length && Math.abs(x - xs[xs.length - 1]) < 1e-12) continue;
    xs.push(x);
    ys.push(y);
  }
  const gx: number[] = [], gy: number[] = [];
  for (let i = 0; i < n; i++) gx.push(i / (n - 1));
  if (xs.length < 2) return [gx, gx.slice()];
  const k = xs.length;
  const h = xs.slice(1).map((x, i) => x - xs[i]);
  const m = h.map((hh, i) => (ys[i + 1] - ys[i]) / hh);
  const d = new Array(k).fill(0);
  if (k === 2) {
    d[0] = d[1] = m[0];
  } else {
    for (let i = 1; i < k - 1; i++) {
      if (m[i - 1] * m[i] <= 0) d[i] = 0;
      else {
        const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1];
        d[i] = (w1 + w2) / (w1 / m[i - 1] + w2 / m[i]);
      }
    }
    const edge = (h0: number, h1: number, m0: number, m1: number) => {
      let dd = ((2 * h0 + h1) * m0 - h0 * m1) / (h0 + h1);
      if (Math.sign(dd) !== Math.sign(m0)) dd = 0;
      else if (Math.sign(m0) !== Math.sign(m1) && Math.abs(dd) > 3 * Math.abs(m0)) dd = 3 * m0;
      return dd;
    };
    d[0] = edge(h[0], h[1], m[0], m[1]);
    d[k - 1] = edge(h[k - 2], h[k - 3], m[k - 2], m[k - 3]);
  }
  for (const x of gx) {
    let y: number;
    if (x <= xs[0]) y = ys[0];
    else if (x >= xs[k - 1]) y = ys[k - 1];
    else {
      let i = 0;
      while (i < k - 2 && x > xs[i + 1]) i++;
      const t = (x - xs[i]) / h[i];
      const t2 = t * t, t3 = t2 * t;
      y = (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * d[i] + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * d[i + 1];
    }
    gy.push(Math.min(1, Math.max(0, y)));
  }
  return [gx, gy];
}

const isIdentity = (p: Curve) => p.length === 2 && p[0][0] === 0 && p[0][1] === 0 && p[1][0] === 1 && p[1][1] === 1;

type Props = {
  curves: Record<Channel, Curve>;
  channel: Channel;
  onChange: (ch: Channel, pts: Curve, final: boolean) => void;
};

/** Drag points to shape the curve; click empty space to add a point, double-click a point to remove it. */
export function CurveEditor({ curves, channel, onChange }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ i: number; pts: Curve } | null>(null);

  const geom = () => {
    const c = ref.current!;
    const r = c.getBoundingClientRect();
    const m = 10;
    const side = Math.min(r.width, r.height) - 2 * m;
    return { left: (r.width - side) / 2, top: m, side, rect: r };
  };

  const draw = (local?: Curve) => {
    const c = ref.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const r = c.getBoundingClientRect();
    if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) {
      c.width = Math.round(r.width * dpr);
      c.height = Math.round(r.height * dpr);
    }
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, r.width, r.height);
    const { left, top, side } = geom();
    const X = (x: number) => left + x * side, Y = (y: number) => top + side - y * side;
    g.fillStyle = "#141416";
    g.fillRect(left, top, side, side);
    g.strokeStyle = "#2c2c31";
    g.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      g.beginPath();
      g.moveTo(X(i / 4) + 0.5, top); g.lineTo(X(i / 4) + 0.5, top + side);
      g.moveTo(left, Y(i / 4) + 0.5); g.lineTo(left + side, Y(i / 4) + 0.5);
      g.stroke();
    }
    g.setLineDash([4, 4]);
    g.strokeStyle = "#3a3a40";
    g.beginPath(); g.moveTo(X(0), Y(0)); g.lineTo(X(1), Y(1)); g.stroke();
    g.setLineDash([]);
    const all = { ...curves, ...(local ? { [channel]: local } : {}) } as Record<Channel, Curve>;
    for (const ch of Object.keys(CURVE_COLOURS) as Channel[]) {
      const pts = all[ch];
      const active = ch === channel;
      if (!active && isIdentity(pts)) continue;
      const [xs, ys] = pchip(pts);
      g.beginPath();
      xs.forEach((x, i) => (i ? g.lineTo(X(x), Y(ys[i])) : g.moveTo(X(x), Y(ys[i]))));
      g.globalAlpha = active ? 1 : 0.28;
      g.strokeStyle = CURVE_COLOURS[ch];
      g.lineWidth = active ? 2 : 1;
      g.stroke();
      g.globalAlpha = 1;
    }
    g.fillStyle = CURVE_COLOURS[channel];
    g.strokeStyle = "#111";
    for (const [x, y] of all[channel]) {
      g.beginPath(); g.arc(X(x), Y(y), 5, 0, Math.PI * 2); g.fill(); g.stroke();
    }
  };

  useEffect(() => {
    draw();
    const ro = new ResizeObserver(() => draw());
    if (ref.current) ro.observe(ref.current);
    return () => ro.disconnect();
  });

  const toCurve = (e: React.PointerEvent) => {
    const { left, top, side, rect } = geom();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    return [Math.min(1, Math.max(0, (px - left) / side)), Math.min(1, Math.max(0, (top + side - py) / side))] as [number, number];
  };
  const hit = (e: React.PointerEvent, pts: Curve) => {
    const { left, top, side, rect } = geom();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    return pts.findIndex(([x, y]) => (left + x * side - px) ** 2 + (top + side - y * side - py) ** 2 < 100);
  };

  return (
    <canvas
      ref={ref}
      className="curve"
      onPointerDown={(e) => {
        const pts = curves[channel].map((p) => [...p] as [number, number]);
        let i = hit(e, pts);
        if (i < 0) {
          const p = toCurve(e);
          pts.push(p);
          pts.sort((a, b) => a[0] - b[0]);
          i = pts.indexOf(p);
          onChange(channel, pts, false);
        }
        drag.current = { i, pts };
        (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
        draw(pts);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const pts = d.pts;
        let [x, y] = toCurve(e);
        const i = d.i;
        const lo = i > 0 ? pts[i - 1][0] + 0.01 : 0;
        const hi = i < pts.length - 1 ? pts[i + 1][0] - 0.01 : 1;
        if (i === 0) x = Math.min(x, hi);
        else if (i === pts.length - 1) x = Math.max(x, lo);
        else x = Math.min(hi, Math.max(lo, x));
        pts[i] = [x, y];
        draw(pts);
        onChange(channel, pts.map((p) => [...p] as [number, number]), false);
      }}
      onPointerUp={() => {
        const d = drag.current;
        drag.current = null;
        if (d) onChange(channel, d.pts.map((p) => [...p] as [number, number]), true);
      }}
      onDoubleClick={(e) => {
        const pts = curves[channel].map((p) => [...p] as [number, number]);
        const i = hit(e as unknown as React.PointerEvent, pts);
        if (i > 0 && i < pts.length - 1) {
          pts.splice(i, 1);
          onChange(channel, pts, true);
        }
      }}
    />
  );
}
