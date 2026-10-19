import { useEffect, useRef } from "react";
import type { StockInfo } from "../engine/engine";

export function BitmapCanvas({ bmp, className }: { bmp: ImageBitmap | null | undefined; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const draw = () => {
      const r = c.getBoundingClientRect();
      const k = window.devicePixelRatio || 1;
      c.width = Math.max(1, Math.round(r.width * k));
      c.height = Math.max(1, Math.round(r.height * k));
      const g = c.getContext("2d")!;
      g.fillStyle = "#202024";
      g.fillRect(0, 0, c.width, c.height);
      if (!bmp) return;
      // fit inside, centred
      const s = Math.min(c.width / bmp.width, c.height / bmp.height);
      const w = bmp.width * s, h = bmp.height * s;
      g.fillStyle = "#141417";
      g.fillRect(0, 0, c.width, c.height);
      g.imageSmoothingQuality = "high";
      g.drawImage(bmp, (c.width - w) / 2, (c.height - h) / 2, w, h);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [bmp]);
  return <canvas ref={ref} className={className} />;
}

export const shortName = (st: StockInfo) => st.name.replace("Kodak ", "").replace("Fujifilm ", "");
export const kindLabel = (st: StockInfo) => ({ neg: "colour negative", rev: "slide", bw: "black & white" })[st.kind];

export type Filter = "all" | "Kodak" | "Fujifilm" | "CineStill" | "bw";
export const showStock = (st: StockInfo, f: Filter) =>
  f === "all" || (f === "bw" && st.kind === "bw") || (f === st.family && st.kind !== "bw");

type Props = {
  stocks: StockInfo[];
  current: string;
  filter: Filter;
  thumbs: Map<string, ImageBitmap>;
  big?: boolean;
  onPick: (id: string) => void;
};

export function StockGrid({ stocks, current, filter, thumbs, big, onPick }: Props) {
  const sel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    sel.current?.scrollIntoView({ block: "nearest" });
  }, [filter, big]);
  return (
    <div className={big ? "stock-grid big" : "stock-grid"}>
      {stocks.filter((st) => showStock(st, filter)).map((st) => (
        <button
          key={st.id}
          ref={st.id === current ? sel : undefined}
          className={st.id === current ? "stock selected" : "stock"}
          title={`${st.name} — ${st.blurb}`}
          onClick={() => onPick(st.id)}
        >
          <BitmapCanvas bmp={thumbs.get(st.id)} className="stock-thumb" />
          <span className="stock-name">{shortName(st)}</span>
          {big && <span className="stock-sub">{st.family} · ISO {st.iso}</span>}
        </button>
      ))}
    </div>
  );
}
