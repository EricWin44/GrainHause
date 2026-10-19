// A small pool of engine workers with a priority queue. Each job runs on one worker; jobs that
// have gone stale (the photo or settings changed) are dropped before they start.

export class Stale extends Error {
  constructor() {
    super("stale");
  }
}

type Pending = { resolve: (v: any) => void; reject: (e: Error) => void };

export class EngineWorker {
  readonly worker: Worker;
  readonly index: number;
  busy = false;
  ready = false;
  /** photo ids this worker holds, oldest first (mirrors webapp.KEEP) */
  held: string[] = [];
  private seq = 0;
  private pending = new Map<number, Pending>();

  constructor(index: number) {
    this.index = index;
    this.worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module", name: `grainhause-${index}` });
    this.worker.onmessage = (e) => {
      const { id, ok, result, error } = e.data;
      const p = this.pending.get(id);
      if (!p) return;
      this.pending.delete(id);
      if (ok) p.resolve(result);
      else p.reject(new Error(error));
    };
    this.worker.onerror = (e) => {
      for (const p of this.pending.values()) p.reject(new Error(e.message || "worker crashed"));
      this.pending.clear();
    };
  }

  call<T = any>(op: string, args: unknown[] = [], transfer: Transferable[] = []): Promise<T> {
    const id = ++this.seq;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, op, args }, transfer);
    });
  }

  touch(pid: string) {
    this.held = this.held.filter((p) => p !== pid);
    this.held.push(pid);
    while (this.held.length > 2) this.held.shift();
  }

  forget(pid: string) {
    this.held = this.held.filter((p) => p !== pid);
  }
}

type Job = {
  prio: number;
  seq: number;
  worker?: number;
  /** keep worker 0 free: it serves the full-resolution rows the other workers need */
  avoid0?: boolean;
  stale?: () => boolean;
  run: (w: EngineWorker) => Promise<any>;
  resolve: (v: any) => void;
  reject: (e: Error) => void;
};

export class Pool {
  readonly workers: EngineWorker[];
  private queue: Job[] = [];
  private seq = 0;

  constructor(n: number) {
    this.workers = Array.from({ length: n }, (_, i) => new EngineWorker(i));
  }

  get readyCount() {
    return this.workers.filter((w) => w.ready).length;
  }

  submit<T>(prio: number, run: (w: EngineWorker) => Promise<T>,
            opts: { worker?: number; avoid0?: boolean; stale?: () => boolean } = {}): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.queue.push({ prio, seq: ++this.seq, run, resolve, reject, ...opts });
      this.queue.sort((a, b) => a.prio - b.prio || a.seq - b.seq);
      this.pump();
    });
  }

  /** drop queued (not yet running) jobs that are stale */
  sweep() {
    const keep: Job[] = [];
    for (const j of this.queue) {
      if (j.stale?.()) j.reject(new Stale());
      else keep.push(j);
    }
    this.queue = keep;
  }

  pump() {
    for (const w of this.workers) {
      if (!w.ready || w.busy) continue;
      const i = this.queue.findIndex((j) => j.worker === w.index ||
        (j.worker === undefined && !(j.avoid0 && w.index === 0 && this.readyCount > 1)));
      if (i < 0) continue;
      const job = this.queue.splice(i, 1)[0];
      if (job.stale?.()) {
        job.reject(new Stale());
        this.pump();
        return;
      }
      w.busy = true;
      job.run(w).then(job.resolve, job.reject).finally(() => {
        w.busy = false;
        this.pump();
      });
    }
  }
}
