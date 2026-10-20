import { useEffect, useRef, type MouseEvent } from "react";

export type SortMode = "newest" | "oldest" | "az" | "rating";

export const SORTS: [SortMode, string][] = [
  ["newest", "Newest"],
  ["oldest", "Oldest"],
  ["az", "A–Z"],
  ["rating", "Rating"],
];

export type StripPhoto = {
  id: string;
  name: string;
  thumbUrl: string | null;
  error?: string;
  rating: number;
};

export function Filmstrip({
  photos,
  currentId,
  selected,
  sort,
  onSort,
  onPhotoClick,
  onPhotoContextMenu,
  onRate,
}: {
  photos: StripPhoto[];
  currentId: string | null;
  selected: Set<string>;
  sort: SortMode;
  onSort: (sort: SortMode) => void;
  onPhotoClick: (e: MouseEvent, id: string) => void;
  onPhotoContextMenu: (e: MouseEvent, id: string) => void;
  onRate: (id: string, rating: number) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = scroller.current;
    const el = root?.querySelector<HTMLElement>("[data-current='true']");
    if (!root || !el) return;
    const left = el.offsetLeft - root.clientWidth / 2 + el.clientWidth / 2;
    root.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }, [currentId, sort, photos.length]);

  return (
    <div className="lr-strip">
      <div className="lr-strip-top">
        <span className="dim">{photos.length === 0 ? "No photos yet" : `${photos.length} photo${photos.length === 1 ? "" : "s"}`}</span>
        <span className="grow" />
        <span className="dim">Sort</span>
        <div className="filters">
          {SORTS.map(([id, label]) => (
            <button key={id} type="button" className={sort === id ? "chip on" : "chip"} onClick={() => onSort(id)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="lr-film" ref={scroller} onContextMenu={(e) => e.preventDefault()}>
        {photos.length === 0 && <div className="empty">No photos yet. Use New Image to add one.</div>}
        {photos.map((p) => (
          <div
            key={p.id}
            data-current={p.id === currentId ? "true" : undefined}
            className={`strip-item lr${p.id === currentId ? " current" : ""}${selected.has(p.id) ? " selected" : ""}`}
            onClick={(e) => onPhotoClick(e, p.id)}
            onContextMenu={(e) => onPhotoContextMenu(e, p.id)}
            title={p.error ? `${p.name} — ${p.error}` : p.name}
          >
            <div className="strip-thumb">
              {p.thumbUrl ? <img src={p.thumbUrl} alt="" /> : <span className="ph">{p.error ? "!" : ""}</span>}
            </div>
            <span className="strip-name">{p.name}</span>
            <div
              className="rate"
              onMouseLeave={(e) => e.currentTarget.querySelectorAll(".rate-btn").forEach((btn) => btn.classList.remove("preview"))}
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  className={n <= p.rating ? "rate-btn on" : "rate-btn"}
                  aria-label={`Rate ${p.name} ${n} of 5`}
                  aria-pressed={p.rating === n}
                  onMouseEnter={(e) => {
                    e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(".rate-btn").forEach((btn, i) => {
                      btn.classList.toggle("preview", i < n);
                    });
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!e.shiftKey && !e.metaKey && !e.ctrlKey) onPhotoClick(e, p.id);
                    onRate(p.id, n);
                  }}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
