import type { RGB } from "./engine/engine";

export function toImageData(img: RGB): ImageData {
  const n = img.w * img.h;
  const out = new Uint8ClampedArray(n * 4);
  const d = img.data;
  for (let i = 0, j = 0; i < n; i++, j += 3) {
    const k = i * 4;
    out[k] = d[j];
    out[k + 1] = d[j + 1];
    out[k + 2] = d[j + 2];
    out[k + 3] = 255;
  }
  return new ImageData(out, img.w, img.h);
}

export const toBitmap = (img: RGB) => createImageBitmap(toImageData(img));

/** Small JPEG of a developed image, for the photo list. */
export async function thumbBlob(bmp: ImageBitmap, edge = 420): Promise<Blob> {
  const s = Math.min(1, edge / Math.max(bmp.width, bmp.height));
  const w = Math.max(1, Math.round(bmp.width * s)), h = Math.max(1, Math.round(bmp.height * s));
  const c = new OffscreenCanvas(w, h);
  const g = c.getContext("2d")!;
  g.imageSmoothingQuality = "high";
  g.drawImage(bmp, 0, 0, w, h);
  return c.convertToBlob({ type: "image/jpeg", quality: 0.82 });
}

export function download(data: BlobPart, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
