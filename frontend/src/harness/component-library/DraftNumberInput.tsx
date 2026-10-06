import { useEffect, useRef, useState, type InputHTMLAttributes } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  value: number | "";
  onValueChange: (value: number) => void;
  onEmpty?: () => void;
  /** Apply valid numeric edits as they are typed while retaining partial input locally. */
  immediate?: boolean;
};

export function parseDraftNumber(text: string, min?: number, max?: number): number | null {
  if (!text.trim()) return null;
  const value = Number(text);
  return Number.isFinite(value) && (min === undefined || value >= min) && (max === undefined || value <= max) ? value : null;
}

/** Keep partial input local; optionally publish valid edits before blur/Enter. */
export function DraftNumberInput({ value, onValueChange, onEmpty, onBlur, onKeyDown, immediate = false, ...props }: Props) {
  const [draft, setDraft] = useState(String(value));
  const pending = useRef(false);
  const emitted = useRef<number | null>(null);
  useEffect(() => {
    if (immediate && emitted.current === value) { emitted.current = null; return; }
    setDraft(String(value)); pending.current = false;
  }, [value, immediate]);
  const commit = () => {
    if (!pending.current) return;
    pending.current = false;
    const parsed = parseDraftNumber(draft, props.min === undefined ? undefined : Number(props.min), props.max === undefined ? undefined : Number(props.max));
    if (draft === "" && onEmpty) onEmpty();
    else if (parsed !== null) { setDraft(String(parsed)); if (parsed !== value) onValueChange(parsed); }
    else setDraft(String(value));
  };
  return <input {...props} type="number" value={draft} onChange={event => {
    pending.current = true;
    const next = event.target.value;
    setDraft(next);
    if (immediate) {
      const parsed = parseDraftNumber(next, props.min === undefined ? undefined : Number(props.min), props.max === undefined ? undefined : Number(props.max));
      if (parsed !== null && parsed !== value) { emitted.current = parsed; onValueChange(parsed); }
    }
  }}
    onBlur={event => { commit(); onBlur?.(event); }} onKeyDown={event => {
      if (event.key === "Enter") { event.preventDefault(); commit(); }
      if (event.key === "Escape") { pending.current = false; setDraft(String(value)); }
      onKeyDown?.(event);
    }} />;
}
