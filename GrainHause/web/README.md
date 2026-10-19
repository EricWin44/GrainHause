# GrainHause for the web

The same film engine as the desktop app (`../grainhouse/`, used unchanged), running in the
browser with Pyodide. Photos never leave the computer: there is no server, and Vercel only
hosts static files.

- `py/webapp.py`: connects the engine to the browser (pixels in and out, row-band rendering, no-LCMS ICC helpers)
- `src/engine/`: worker pool, band splitting, LibRaw (WebAssembly) RAW decoding
- `src/App.tsx`: the interface (mirrors `grainhouse/gui.py`)
- `scripts/prepare-py.mjs`: copies the engine and the Python runtime and packages into `public/` before each build

```
npm install
npm run dev      # http://localhost:5173
npm run build    # -> dist/
```

The page must be cross-origin isolated, because LibRaw's WebAssembly build uses threads. The dev server
sets the headers itself; in production they come from `/vercel.json` at the repo root, which also
holds the build settings, so the Vercel project needs no dashboard configuration.

Not available in the browser: the Lightroom Classic round trip (a web page can't be an external
editor) and the command-line batch tool.
