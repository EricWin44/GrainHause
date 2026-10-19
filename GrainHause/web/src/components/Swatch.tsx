import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

export type SwatchImage = { bmp: ImageBitmap; origin: [number, number] | null; centre: [number, number] | null };

export type SwatchHandle = { deviceSize: () => [number, number]; setCentre: (c: [number, number]) => void };

/**
 * The 100% grain sample: one device pixel per image pixel. Drag it to look around — it slides
 * over what's already rendered and asks for more when it nears the edge.
 */
export const Swatch = forwardRef<SwatchHandle, {
  image: SwatchImage | null;
  onDrag: (dx: number, dy: number) => void;
  onRelease: () => void;
}>(function Swatch({ image, onDrag, onRelease }, ref) {
  const c = useRef<HTMLCanvasElement>(null);
  const img = useRef(image);
  img.current = image;
  const centre = useRef<[number, number] | null>(null);
  const last = useRef<[number, number] | null>(null);

  const draw = () => {
    const cv = c.current;
    if (!cv) return;
    const r = cv.getBoundingClientRect();
    const k = window.devicePixelRatio || 1;
    cv.width = Math.max(1, Math.round(r.width * k));
    cv.height = Math.max(1, Math.round(r.height * k));
    const g = cv.getContext("2d")!;
    g.fillStyle = "#0a0a0b";
    g.fillRect(0, 0, cv.width, cv.height);
    const im = img.current;
    if (!im) return;
    g.imageSmoothingEnabled = false;
    if (!im.origin) {
      g.drawImage(im.bmp, (cv.width - im.bmp.width) / 2, (cv.height - im.bmp.height) / 2);
      return;
    }
    const [cx, cy] = centre.current ?? im.centre ?? [im.origin[0] + im.bmp.width / 2, im.origin[1] + im.bmp.height / 2];
    g.drawImage(im.bmp, Math.round(cv.width / 2 - (cx - im.origin[0])), Math.round(cv.height / 2 - (cy - im.origin[1])));
  };

  useImperativeHandle(ref, () => ({
    deviceSize: () => {
      const r = c.current!.getBoundingClientRect();
      const k = window.devicePixelRatio || 1;
      return [Math.round(r.width * k), Math.round(r.height * k)];
    },
    setCentre: (p) => {
      centre.current = p;
      draw();
    },
  }));

  useEffect(() => {
    centre.current = null;
    draw();
  }, [image]);
  useEffect(() => {
    const ro = new ResizeObserver(draw);
    ro.observe(c.current!);
    return () => ro.disconnect();
  }, []);

  return (
    <canvas
      ref={c}
      className="swatch"
      title={"A 100% sample of your photo with the current grain — updates instantly.\nDrag it to look at another part of the photo (or double-click the photo)."}
      style={{ touchAction: "none" }}
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
        last.current = [e.clientX, e.clientY];
      }}
      onPointerMove={(e) => {
        if (!last.current) return;
        const [x, y] = last.current;
        last.current = [e.clientX, e.clientY];
        onDrag(e.clientX - x, e.clientY - y);
      }}
      onPointerUp={() => {
        if (last.current) onRelease();
        last.current = null;
      }}
    />
  );
});
