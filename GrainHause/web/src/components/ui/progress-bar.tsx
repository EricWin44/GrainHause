import { useState } from "react";
import { cn } from "@/lib/utils";

const SEGMENTS = 20;

/** Segmented slider track. Drag or click a segment to set the value; hover lifts the nearby segments. */
export function SegmentedSlider({
  value,
  lo,
  hi,
  step = 1,
  label,
  onChange,
}: {
  value: number;
  lo: number;
  hi: number;
  step?: number;
  label: string;
  onChange: (value: number) => void;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const span = hi - lo || 1;
  const pct = Math.min(1, Math.max(0, (value - lo) / span));
  const filled = Math.round(pct * SEGMENTS);

  const apply = (clientX: number, el: HTMLElement) => {
    const rect = el.getBoundingClientRect();
    const t = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const places = Math.max(0, (String(step).split(".")[1] ?? "").length);
    const snapped = lo + Math.round((lo + t * span - lo) / step) * step;
    const next = Math.min(hi, Math.max(lo, Number(snapped.toFixed(places))));
    onChange(next);
    setHovered(Math.min(SEGMENTS - 1, Math.floor(t * SEGMENTS)));
  };

  const nudge = (dir: number) => {
    const places = Math.max(0, (String(step).split(".")[1] ?? "").length);
    const next = Math.min(hi, Math.max(lo, Number((value + dir * step).toFixed(places))));
    onChange(next);
  };

  return (
    <div
      role="slider"
      tabIndex={0}
      aria-label={label}
      aria-valuemin={lo}
      aria-valuemax={hi}
      aria-valuenow={value}
      className="flex gap-[3px] py-1 touch-none select-none"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        apply(e.clientX, e.currentTarget);
      }}
      onPointerMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const t = (e.clientX - rect.left) / rect.width;
        if (t >= 0 && t <= 1) setHovered(Math.min(SEGMENTS - 1, Math.floor(t * SEGMENTS)));
        if (e.currentTarget.hasPointerCapture(e.pointerId)) apply(e.clientX, e.currentTarget);
      }}
      onPointerUp={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      onPointerLeave={() => setHovered(null)}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); nudge(1); }
        if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); nudge(-1); }
      }}
    >
      {Array.from({ length: SEGMENTS }, (_, index) => {
        const isFilled = index < filled;
        const distance = hovered == null ? 99 : Math.abs(hovered - index);
        let scale = 1;
        let translateY = 0;
        if (distance === 0) {
          scale = 1.3;
          translateY = -1;
        } else if (distance <= 3) {
          const falloff = Math.cos((distance / 3) * (Math.PI / 2));
          scale = 1 + 0.2 * falloff;
          translateY = -0.5 * falloff;
        }
        return (
          <div
            key={index}
            className={cn(
              "h-3 flex-1 origin-center rounded-[4px]",
              "transition-all duration-500 ease-[cubic-bezier(0.34,1.56,0.64,1)]",
              isFilled ? "bg-primary" : "bg-[#2a2b30]",
              hovered === index && isFilled && "brightness-110 shadow-[0_0_16px_color-mix(in_oklch,var(--primary)_55%,transparent)]",
              hovered === index && !isFilled && "bg-[#3a3b42]",
              hovered != null && !isFilled && hovered !== index && "bg-[#222328]",
            )}
            style={{ transform: `scaleY(${scale}) translateY(${translateY}px)` }}
          />
        );
      })}
    </div>
  );
}
