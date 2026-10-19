import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// LibRaw's WebAssembly build uses threads (SharedArrayBuffer), which browsers only allow on a
// cross-origin isolated page. Production sets the same headers in ../../vercel.json.
const isolation = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

export default defineConfig({
  plugins: [react()],
  server: { headers: isolation },
  preview: { headers: isolation },
  worker: { format: "es" },
  optimizeDeps: { exclude: ["libraw-wasm", "pyodide"] },
  build: { target: "es2022", chunkSizeWarningLimit: 2000 },
});
