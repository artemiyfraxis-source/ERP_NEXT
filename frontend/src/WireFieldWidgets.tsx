import { useId, useState, type KeyboardEvent } from "react";
import { SWATCHES, colorBackground, joinColors, parseColors } from "./wireColors";
import { joinMakers, parseMakers } from "./wireMakers";

/* Two list fields of a БД.ПРОВ record: «Цвета» (swatches from the shared palette) and «Производители» (chips with
   suggestions). Both store a comma-separated text, as the cell always held. */

function Chip({ label, onRemove, dot }: { label: string; onRemove: () => void; dot?: string | null }) {
  return <span className="chip-item">
    {dot && <i className="wire-dot" style={{ background: dot }} aria-hidden="true" />}
    {label}
    <button type="button" aria-label={`Убрать ${label}`} onClick={onRemove}>×</button>
  </span>;
}

export function ColorPicker({ label = "Цвета", value, onChange, seriesColors = "" }: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  /** The colours the other sizes of the series have; «Все цвета серии» copies them. */
  seriesColors?: string;
}) {
  const chosen = parseColors(value);
  const [other, setOther] = useState<string | null>(null);
  const toggle = (name: string) => onChange(joinColors(chosen.includes(name) ? chosen.filter((item) => item !== name) : [...chosen, name]));
  const commit = () => {
    if (other && other.trim()) onChange(joinColors(parseColors(joinColors(chosen) + "," + other)));
    setOther(null);
  };
  const series = parseColors(seriesColors);
  return <div className="field-group color-picker" role="group" aria-label={label}>
    <small>{label}</small>
    <div className="swatch-row">
      {SWATCHES.map((swatch) => <button type="button" key={swatch.name} className="swatch" aria-pressed={chosen.includes(swatch.name)}
        aria-label={swatch.name} title={swatch.name} onClick={() => toggle(swatch.name)}
        style={{ background: swatch.hex2 ? `linear-gradient(135deg, ${swatch.hex} 50%, ${swatch.hex2} 50%)` : swatch.hex }} />)}
      {other === null
        ? <button type="button" className="secondary small" onClick={() => setOther("")}>+ другой</button>
        : <input aria-label="Другой цвет" autoFocus value={other} onChange={(event) => setOther(event.target.value)} onBlur={commit}
          onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } if (event.key === "Escape") setOther(null); }} />}
      {series.length > 0 && <button type="button" className="secondary small" onClick={() => onChange(joinColors(series))}>Все цвета серии</button>}
    </div>
    <div className="chip-row">
      {chosen.length === 0 && <span className="muted">—</span>}
      {chosen.map((name) => <Chip key={name} label={name} dot={colorBackground(name)} onRemove={() => onChange(joinColors(chosen.filter((item) => item !== name)))} />)}
    </div>
  </div>;
}

export function MakersInput({ label = "Производители", value, onChange, suggestions }: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  suggestions: string[];
}) {
  const names = parseMakers(value);
  const [draft, setDraft] = useState("");
  const listId = useId();
  const add = (text: string) => {
    const fresh = parseMakers(text);
    if (fresh.length) onChange(joinMakers([...names, ...fresh]));
    setDraft("");
  };
  const keys = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(draft); }
    else if (event.key === "Backspace" && !draft && names.length) onChange(joinMakers(names.slice(0, -1)));
  };
  return <div className="field-group makers-input" role="group" aria-label={label}>
    <small>{label}</small>
    <div className="chip-row">
      {names.map((name) => <Chip key={name} label={name} onRemove={() => onChange(joinMakers(names.filter((item) => item !== name)))} />)}
      <input aria-label={`${label}: добавить`} placeholder="+ добавить" list={listId} value={draft} onChange={(event) => setDraft(event.target.value)}
        onKeyDown={keys} onBlur={() => add(draft)} />
      <datalist id={listId}>{suggestions.filter((name) => !names.includes(name)).slice(0, 60).map((name) => <option key={name} value={name} />)}</datalist>
    </div>
  </div>;
}
