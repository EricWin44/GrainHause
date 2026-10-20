import { useCallback, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

// A tiny tiled noise texture used to hint at film grain over the "after" image. Swap for a real
// scanned grain plate once you have one — this is just enough to sell the idea on the homepage.
const GRAIN_SVG = `data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter><rect width="100%" height="100%" filter="url(#n)"/></svg>`,
)}`;

/**
 * A Lightroom-style "drag to compare" slider for the marketing site.
 *
 * Pass distinct `beforeSrc`/`afterSrc` once you have real digital → film pairs. Until then,
 * passing the same image with an `afterClassName` filter (see Home.tsx) gives a reasonable
 * stand-in for "what film does to a photo".
 */
export function BeforeAfterSlider({
  beforeSrc,
  afterSrc,
  beforeLabel = "Digital",
  afterLabel = "Film",
  beforeClassName = "",
  afterClassName = "",
  className = "",
  aspectClassName = "aspect-[3/2]",
  initial = 50,
  grainOverlay = false,
}: {
  beforeSrc: string;
  afterSrc: string;
  beforeLabel?: string;
  afterLabel?: string;
  beforeClassName?: string;
  afterClassName?: string;
  className?: string;
  aspectClassName?: string;
  initial?: number;
  grainOverlay?: boolean;
}) {
  const [pct, setPct] = useState(initial);
  const [dragging, setDragging] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const setFromClientX = useCallback((clientX: number) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const next = ((clientX - rect.left) / rect.width) * 100;
    setPct(Math.min(100, Math.max(0, next)));
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setDragging(true);
    setFromClientX(e.clientX);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    setFromClientX(e.clientX);
  };
  const endDrag = () => setDragging(false);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); setPct((p) => Math.max(0, p - 4)); }
    if (e.key === "ArrowRight") { e.preventDefault(); setPct((p) => Math.min(100, p + 4)); }
    if (e.key === "Home") { e.preventDefault(); setPct(0); }
    if (e.key === "End") { e.preventDefault(); setPct(100); }
  };

  return (
    <div
      ref={ref}
      role="slider"
      tabIndex={0}
      aria-label={`Compare ${beforeLabel} and ${afterLabel}`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerLeave={endDrag}
      className={`relative w-full select-none overflow-hidden rounded-2xl border border-border bg-black outline-none ring-primary/60 focus-visible:ring-2 ${aspectClassName} ${className}`}
    >
      {/* after (film) — full-frame base layer */}
      <img src={afterSrc} alt="" draggable={false} className={`pointer-events-none absolute inset-0 h-full w-full object-cover ${afterClassName}`} />
      {grainOverlay && (
        <div
          className="pointer-events-none absolute inset-0 opacity-20 mix-blend-overlay"
          style={{ backgroundImage: `url("${GRAIN_SVG}")`, clipPath: `inset(0 0 0 ${pct}%)` }}
        />
      )}
      {/* before (digital) — same frame, clipped to the left of the handle */}
      <img
        src={beforeSrc}
        alt=""
        draggable={false}
        className={`pointer-events-none absolute inset-0 h-full w-full object-cover ${beforeClassName}`}
        style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}
      />

      <span className="pointer-events-none absolute left-3 top-3 rounded-full bg-black/55 px-3 py-1 text-xs font-medium tracking-wide text-white backdrop-blur-sm">
        {beforeLabel.toUpperCase()}
      </span>
      <span className="pointer-events-none absolute right-3 top-3 rounded-full bg-black/55 px-3 py-1 text-xs font-medium tracking-wide text-white backdrop-blur-sm">
        {afterLabel.toUpperCase()}
      </span>

      {/* divider + handle */}
      <div className="pointer-events-none absolute inset-y-0 w-px bg-white/80" style={{ left: `${pct}%` }} />
      <div
        className="absolute top-1/2 flex h-9 w-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/70 bg-black/60 text-white shadow-lg backdrop-blur-sm"
        style={{ left: `${pct}%` }}
      >
        <ChevronLeft className="-mr-1 h-4 w-4" />
        <ChevronRight className="-ml-1 h-4 w-4" />
      </div>
    </div>
  );
}
