// Puts everything the in-browser engine needs under public/, so the site serves it itself
// (the page is cross-origin isolated, which LibRaw's threads need):
//   public/pyodide/   Python runtime + the numpy / scipy / Pillow / pillow-heif / tifffile wheels
//   public/py/        the grainhouse engine (copied from ../grainhouse, unchanged) + webapp.py
//   public/fonts/     brand font, and the font the film-border edge print uses
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "..");
const repoApp = path.resolve(web, "..");
const pub = path.join(web, "public");
const cache = path.join(web, ".cache", "wheels");

const PYODIDE_VERSION = JSON.parse(fs.readFileSync(path.join(web, "node_modules/pyodide/package.json"), "utf8")).version;
const CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PACKAGES = ["numpy", "scipy", "pillow", "pillow-heif"];
const EXTRA_WHEELS = [
  {
    file: "tifffile-2026.9.20-py3-none-any.whl",
    url: "https://files.pythonhosted.org/packages/05/bf/04f3e61cb20a03678ca43f29bae9a7d0b7d9f563b86f5f600b2d8fba9712/tifffile-2026.9.20-py3-none-any.whl",
    sha256: "9b913167b8f66a57f2e7c0454486c4c4607196d494797461226166bd0755c0e6",
  },
];
// the desktop GUI, its widgets and the CLI entry point aren't used in the browser
const SKIP_PY = new Set(["gui.py", "widgets.py", "__main__.py"]);

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

async function fetchCached(file, url, hash) {
  fs.mkdirSync(cache, { recursive: true });
  const p = path.join(cache, file);
  if (fs.existsSync(p) && sha256(fs.readFileSync(p)) === hash) return fs.readFileSync(p);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download failed ${res.status}: ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (sha256(buf) !== hash) throw new Error(`checksum mismatch for ${file}`);
  fs.writeFileSync(p, buf);
  return buf;
}

function copyDirPy(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  const files = [];
  for (const f of fs.readdirSync(src)) {
    if (!f.endsWith(".py") || f.startsWith("._") || SKIP_PY.has(f)) continue;
    fs.copyFileSync(path.join(src, f), path.join(dst, f));
    files.push(f);
  }
  return files;
}

// ---- runtime
const pyo = path.join(pub, "pyodide");
fs.mkdirSync(pyo, { recursive: true });
for (const f of ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]) {
  fs.copyFileSync(path.join(web, "node_modules/pyodide", f), path.join(pyo, f));
}
const lock = JSON.parse(fs.readFileSync(path.join(pyo, "pyodide-lock.json"), "utf8")).packages;
const need = new Set();
const stack = [...PACKAGES];
while (stack.length) {
  const n = stack.pop();
  if (need.has(n)) continue;
  if (!lock[n]) throw new Error(`package not in pyodide-lock.json: ${n}`);
  need.add(n);
  stack.push(...lock[n].depends);
}
for (const n of need) {
  const { file_name, sha256: hash } = lock[n];
  const buf = await fetchCached(file_name, CDN + file_name, hash);
  fs.writeFileSync(path.join(pyo, file_name), buf);
}
const extra = [];
for (const w of EXTRA_WHEELS) {
  fs.writeFileSync(path.join(pyo, w.file), await fetchCached(w.file, w.url, w.sha256));
  extra.push(w.file);
}

// ---- engine
const py = path.join(pub, "py");
fs.rmSync(py, { recursive: true, force: true });
const engineFiles = copyDirPy(path.join(repoApp, "grainhouse"), path.join(py, "grainhouse"));
fs.copyFileSync(path.join(web, "py", "webapp.py"), path.join(py, "webapp.py"));
const fileList = [...engineFiles.map((f) => `grainhouse/${f}`), "webapp.py"];

// ---- fonts
const fonts = path.join(pub, "fonts");
fs.mkdirSync(fonts, { recursive: true });
fs.copyFileSync(path.join(repoApp, "assets/fonts/Unbounded.ttf"), path.join(fonts, "Unbounded.ttf"));
fs.copyFileSync(path.join(web, "node_modules/dejavu-fonts-ttf/ttf/DejaVuSansMono-Bold.ttf"),
  path.join(fonts, "DejaVuSansMono-Bold.ttf"));
fs.copyFileSync(path.join(repoApp, "assets/icon.png"), path.join(pub, "icon.png"));

// a content hash, so browsers re-fetch the engine whenever it changes
const h = createHash("sha256");
for (const f of fileList) h.update(fs.readFileSync(path.join(py, f)));
fs.writeFileSync(path.join(py, "manifest.json"), JSON.stringify({
  version: h.digest("hex").slice(0, 12),
  files: fileList,
  packages: [...need].sort(),
  wheels: extra,
}, null, 2));
console.log(`prepared pyodide ${PYODIDE_VERSION} (${need.size} packages + ${extra.length} wheels), ${fileList.length} engine files`);
