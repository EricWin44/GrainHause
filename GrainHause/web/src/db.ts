// Everything the desktop app keeps on disk lives in the browser's IndexedDB instead: the photo
// list (with the files themselves, so they come back next time), each photo's edit (the
// "sidecar"), saved looks and preferences. Nothing is uploaded anywhere.
import type { Settings } from "./types";

export type StoredPhoto = {
  id: string;
  name: string;
  type: string;
  size: number;
  lastModified: number;
  file: Blob;
  settings: Settings | null;
  thumb: Blob | null;
  added: number;
};

const DB = "grainhause";
const VERSION = 1;
let dbp: Promise<IDBDatabase> | null = null;

function db() {
  if (!dbp) {
    dbp = new Promise((resolve, reject) => {
      const r = indexedDB.open(DB, VERSION);
      r.onupgradeneeded = () => {
        const d = r.result;
        if (!d.objectStoreNames.contains("photos")) d.createObjectStore("photos", { keyPath: "id" });
        if (!d.objectStoreNames.contains("kv")) d.createObjectStore("kv");
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  return dbp;
}

function req<T>(r: IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function store(name: "photos" | "kv", mode: IDBTransactionMode = "readonly") {
  return (await db()).transaction(name, mode).objectStore(name);
}

async function safe<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    console.warn("storage unavailable", e);
    return fallback;
  }
}

export const photosDb = {
  all: () => safe(async () => req((await store("photos")).getAll()) as Promise<StoredPhoto[]>, []),
  put: (p: StoredPhoto) => safe(async () => { await req((await store("photos", "readwrite")).put(p)); }, undefined),
  get: (id: string) => safe(async () => req((await store("photos")).get(id)) as Promise<StoredPhoto | undefined>, undefined),
  remove: (id: string) => safe(async () => { await req((await store("photos", "readwrite")).delete(id)); }, undefined),
  async patch(id: string, fields: Partial<StoredPhoto>) {
    const p = await photosDb.get(id);
    if (p) await photosDb.put({ ...p, ...fields });
  },
};

export const kv = {
  get: <T,>(k: string, fallback: T) => safe(async () => {
    const v = await req((await store("kv")).get(k));
    return (v === undefined ? fallback : v) as T;
  }, fallback),
  set: (k: string, v: unknown) => safe(async () => { await req((await store("kv", "readwrite")).put(v, k)); }, undefined),
};
