import { useEffect, useRef } from "react";
import type { RGB } from "../engine/engine";

export type Hist = { h: Float32Array[]; clipLo: number; clipHi: number };

/** RGB + luminance histogram with clipping amounts (as the desktop app computes them). */
export function histogram(img: RGB): Hist {
  const d = img.data;
  const n = img.w * img.h;
  const step = n > 400_000 ? 2 : 1;
  const hs = [0, 1, 2, 3].map(() => new Float32Array(256));
  let lo = 0, hi = 0, count = 0;
  for (let y = 0; y < img.h; y += step) {
    let i = y * img.w * 3;
    for (let x = 0; x < img.w; x += step, i += 3 * step) {
      const r = d[i], g = d[i + 1], b = d[i + 2];
      hs[0][r]++; hs[1][g]++; hs[2][b]++;
      hs[3][Math.floor(r * 0.2126 + g * 0.7152 + b * 0.0722)]++;
      if (r <= 1 && g <= 1 && b <= 1) lo++;
      if (r >= 254 || g >= 254 || b >= 254) hi++;
      count++;
    }
  }
  const sm = hs.map((h) => {
    const o = new Float32Array(256);
    for (let i = 0; i < 256; i++) o[i] = 0.5 * h[i] + 0.25 * (h[i - 1] ?? 0) + 0.25 * (h[i + 1] ?? 0);
    return o;
  });
  const vals: number[] = [];
  for (const h of sm) for (let i = 2; i < 254; i++) vals.push(h[i]);
  vals.sort((a, b) => a - b);
  const top = vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.995))] || 1;
  for (const h of sm) for (let i = 0; i < 256; i++) h[i] = Math.min(1, h[i] / (top * 1.15));
  return { h: sm, clipLo: lo / count, clipHi: hi / count };
}

export function HistogramView({ hist }: { hist: Hist | null }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const r = c.getBoundingClientRect();
      c.width = Math.round(r.width * dpr);
      c.height = Math.round(r.height * dpr);
      const g = c.getContext("2d")!;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      const W = r.width, H = r.height;
      g.fillStyle = "#141416";
      g.fillRect(0, 0, W, H);
      g.strokeStyle = "#26262b";
      for (const i of [1, 2, 3]) {
        g.beginPath(); g.moveTo(Math.round((W * i) / 4) + 0.5, 0); g.lineTo(Math.round((W * i) / 4) + 0.5, H); g.stroke();
      }
      if (hist) {
        const path = (h: Float32Array, closed: boolean) => {
          g.beginPath();
          if (closed) g.moveTo(0, H);
          for (let i = 0; i < 256; i++) {
            const x = (i / 255) * W, y = H - 2 - h[i] * (H - 6);
            if (!closed && i === 0) g.moveTo(x, y); else g.lineTo(x, y);
          }
          if (closed) { g.lineTo(W, H); g.closePath(); }
        };
        g.globalCompositeOperation = "lighter";
        [[210, 60, 55], [60, 180, 75], [60, 105, 220]].forEach(([r0, g0, b0], ci) => {
          g.fillStyle = `rgba(${r0},${g0},${b0},0.59)`;
          path(hist.h[ci], true);
          g.fill();
        });
        g.globalCompositeOperation = "source-over";
        g.strokeStyle = "rgba(235,235,235,0.67)";
        path(hist.h[3], false);
        g.stroke();
        for (const [on, left] of [[hist.clipLo > 0.002, true], [hist.clipHi > 0.002, false]] as const) {
          g.fillStyle = on ? "#e4e7ec" : "#3a3a40";
          g.beginPath();
          if (left) { g.moveTo(3, 3); g.lineTo(13, 3); g.lineTo(3, 13); }
          else { g.moveTo(W - 3, 3); g.lineTo(W - 13, 3); g.lineTo(W - 3, 13); }
          g.fill();
        }
      }
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [hist]);
  const info = !hist ? "" : [
    hist.clipLo > 0.002 ? `shadows clipped ${(hist.clipLo * 100).toFixed(1)}%` : "",
    hist.clipHi > 0.002 ? `highlights clipped ${(hist.clipHi * 100).toFixed(1)}%` : "",
  ].filter(Boolean).join("  ·  ") || "no clipping";
  return (
    <div className="hist-wrap">
      <canvas ref={ref} className="hist" title="Histogram of the developed image (red, green, blue and brightness). Corner triangles light up when shadows or highlights clip." />
      <div className="hist-info">{info}</div>
    </div>
  );
}
