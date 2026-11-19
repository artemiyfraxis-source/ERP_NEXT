import { useId, useMemo, useState } from "react";
import type { WireChoice } from "./machineControlApi";

/** «Провод вручную» в карточке ОБ.019: поиск монтажного провода по базе (Ø жилы) и по записанным настройкам станка;
 *  провода с настройкой — первыми, с пометкой «есть настройка» (владелец 19.11.2026). */

const LIMIT = 40;

function normal(text: string): string {
  return text.toLowerCase().replace(/\./g, ",").replace(/\s+/g, " ").trim();
}

export function matchWires(wires: WireChoice[], query: string): WireChoice[] {
  const words = normal(query).split(" ").filter(Boolean);
  const found = wires.filter((wire) => { const label = normal(wire.label); return words.every((word) => label.includes(word)); });
  return [...found.filter((wire) => wire.has_recipe), ...found.filter((wire) => !wire.has_recipe)];
}

export function Ob019WirePicker({ wires, value, onPick, disabled = false }: {
  wires: WireChoice[] | null; value: string; onPick: (label: string) => void; disabled?: boolean;
}) {
  const list = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => matchWires(wires ?? [], query).slice(0, LIMIT), [wires, query]);
  const pick = (wire: WireChoice) => { onPick(wire.label); setOpen(false); setQuery(""); };
  return <div className="wire-picker">
    <input role="combobox" aria-label="Провод" aria-expanded={open} aria-controls={list} aria-autocomplete="list"
      aria-activedescendant={open && matches[active] ? `${list}-${active}` : undefined} autoComplete="off" disabled={disabled}
      placeholder={wires === null ? "Загрузка…" : "Найти провод: ПВАМ 0,35"} value={open ? query : value}
      onFocus={() => { setOpen(true); setActive(0); }} onBlur={() => setOpen(false)}
      onChange={(event) => { setQuery(event.target.value); setOpen(true); setActive(0); }}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); setActive((index) => Math.min(index + 1, matches.length - 1)); }
        else if (event.key === "ArrowUp") { event.preventDefault(); setActive((index) => Math.max(index - 1, 0)); }
        else if (event.key === "Enter" && open && matches[active]) { event.preventDefault(); pick(matches[active]); }
        else if (event.key === "Escape") setOpen(false);
      }} />
    {open && <ul className="wire-picker-list" role="listbox" id={list} aria-label="Провода">
      {matches.map((wire, index) => <li key={wire.label} id={`${list}-${index}`} role="option" aria-selected={index === active}
        className={index === active ? "is-active" : undefined}
        onMouseDown={(event) => event.preventDefault()} onClick={() => pick(wire)}>
        <span className="wire-picker-name">{wire.label}</span>
        {wire.conductor_mm && <span className="machine-muted">Ø жилы {wire.conductor_mm} мм</span>}
        {wire.has_recipe && <span className="machine-badge">есть настройка</span>}
      </li>)}
      {matches.length === 0 && <li className="machine-muted" aria-disabled="true">Провод не найден</li>}
    </ul>}
  </div>;
}
