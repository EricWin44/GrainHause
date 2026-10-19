/// <reference lib="webworker" />
// One Pyodide (Python) instance running the unchanged GrainHause engine. The app runs several of
// these and gives each a band of rows to develop.

type Msg = { id: number; op: string; args: unknown[] };

let py: any = null;
let app: any = null;
let ready: Promise<void> | null = null;

async function init(role: "main" | "helper") {
  const base = new URL("/", self.location.href).href;
  const mod = await import(/* @vite-ignore */ `${base}pyodide/pyodide.mjs`);
  py = await mod.loadPyodide({ indexURL: `${base}pyodide/` });
  const manifest = await (await fetch(`${base}py/manifest.json`)).json();
  // pillow-heif / tifffile are only needed where files are opened
  const pkgs: string[] = manifest.packages.filter((p: string) => role === "main" || !["pillow-heif", "cffi", "pycparser"].includes(p));
  await py.loadPackage(pkgs, { messageCallback: () => {} });
  if (role === "main") {
    for (const w of manifest.wheels) await py.loadPackage(`${base}pyodide/${w}`, { messageCallback: () => {} });
  }
  py.FS.mkdirTree("/app/grainhouse");
  await Promise.all(manifest.files.map(async (f: string) => {
    const r = await fetch(`${base}py/${f}?v=${manifest.version}`);
    py.FS.writeFile(`/app/${f}`, new Uint8Array(await r.arrayBuffer()));
  }));
  // the film-border edge print looks for this font by name in the working directory
  const font = await fetch(`${base}fonts/DejaVuSansMono-Bold.ttf`);
  py.FS.mkdirTree("/home/pyodide");
  py.FS.writeFile("/home/pyodide/DejaVuSansMono-Bold.ttf", new Uint8Array(await font.arrayBuffer()));
  py.runPython("import os, sys; sys.path.insert(0, '/app'); os.chdir('/home/pyodide')");
  app = py.pyimport("webapp");
}

function out(r: any): any {
  if (r === undefined || r === null) return r;
  if (typeof r !== "object" || !(r instanceof py.ffi.PyProxy)) return r;
  const t = r.type;
  if (t === "memoryview" || t === "bytes" || t === "bytearray") {
    const b = r.getBuffer("u8");
    const c = (b.data as Uint8Array).slice();
    b.release();
    r.destroy();
    return c;
  }
  if (t === "tuple" || t === "list") {
    const items = Array.from({ length: r.length as number }, (_, i) => out(r.get(i)));
    r.destroy();
    return items;
  }
  const v = r.toJs();
  r.destroy();
  return v;
}

function transferables(v: any, acc: Transferable[] = []): Transferable[] {
  if (v instanceof Uint8Array) acc.push(v.buffer);
  else if (Array.isArray(v)) v.forEach((x) => transferables(x, acc));
  return acc;
}

self.onmessage = async (e: MessageEvent<Msg>) => {
  const { id, op, args } = e.data;
  try {
    let result: unknown;
    if (op === "init") {
      ready = ready ?? init(args[0] as "main" | "helper");
      await ready;
      result = true;
    } else {
      await ready;
      const fn = app[op];
      if (!fn) throw new Error(`unknown op ${op}`);
      result = out(fn(...args));
    }
    (self as unknown as Worker).postMessage({ id, ok: true, result }, transferables(result));
  } catch (err: any) {
    const msg = String(err?.message ?? err);
    (self as unknown as Worker).postMessage({ id, ok: false, error: msg });
  }
};
