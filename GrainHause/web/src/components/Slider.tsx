import { useEffect, useState } from "react";

type Props = {
  label: string;
  lo: number;
  hi: number;
  value: number;
  def: number;
  step?: number;
  decimals?: number;
  suffix?: string;
  title?: string;
  onChange: (v: number) => void;
  onReset: () => void;
};

const fmt = (v: number, d: number) => v.toFixed(d);

/** Label + slider + number box + ↺. Double-click the label (or ↺) to reset, as in the desktop app. */
export function Slider({ label, lo, hi, value, def, step = 1, decimals = 0, suffix = "", title, onChange, onReset }: Props) {
  const v = Math.min(hi, Math.max(lo, value));
  const [text, setText] = useState(fmt(v, decimals));
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setText(fmt(v, decimals));
  }, [v, decimals, editing]);
  const changed = Math.abs(v - def) > step * 0.5;
  const commitText = () => {
    setEditing(false);
    const n = parseFloat(text.replace(",", "."));
    if (Number.isFinite(n)) onChange(Math.min(hi, Math.max(lo, n)));
    else setText(fmt(v, decimals));
  };
  const pct = ((v - lo) / (hi - lo)) * 100;
  return (
    <div className="slider" title={title}>
      <div className="slider-top">
        <span className="slider-label" onDoubleClick={onReset} title="Double-click to reset">{label}</span>
        <span className="slider-box">
          <input
            className="num"
            value={editing ? text : `${fmt(v, decimals)}${suffix}`}
            onFocus={(e) => { setEditing(true); setText(fmt(v, decimals)); requestAnimationFrame(() => e.target.select()); }}
            onChange={(e) => setText(e.target.value)}
            onBlur={commitText}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") { setEditing(false); (e.target as HTMLInputElement).blur(); }
              e.stopPropagation();
            }}
            inputMode="decimal"
            aria-label={label}
          />
        </span>
        <button className="reset-btn" disabled={!changed} onClick={onReset} title={`Reset ${label.toLowerCase()}`}
                aria-label={`Reset ${label}`}>↺</button>
      </div>
      <input
        type="range"
        min={lo}
        max={hi}
        step={step}
        value={v}
        style={{ "--pct": `${pct}%` } as React.CSSProperties}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label={label}
      />
    </div>
  );
}

export function Section({ title, onReset, children }: { title: string; onReset?: () => void; children: React.ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h3>{title}</h3>
        {onReset && <button className="section-reset" onClick={onReset} title={`Reset everything in ${title}`}>Reset</button>}
      </div>
      {children}
    </section>
  );
}

export function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="labeled">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Select<T extends string>({ value, options, onChange, disabled, title }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; disabled?: boolean; title?: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as T)} disabled={disabled} title={title}
            onKeyDown={(e) => e.stopPropagation()}>
      {options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );
}
