import { useEffect, useRef, useState } from "react";
import { colorBackground } from "./wireColors";
import { PaletteGroups } from "./ColorPalette";

/* «Цвета жил» в строке «Кабели» (владелец 14.10: «цвета многожильного так и не добавил»): цвет каждой жилы кабеля видно
   в строке, а окно рядом задаёт их все разом — по жиле или типовым порядком. Цвета — этой карты (у партий кабеля они
   разные); пусто — из «Цвета» записи БД.ПРОВ или из наименования. */

/** Типовой порядок жил — подставляется только по щелчку технолога, не сам. */
export const CORE_COLOR_PRESETS: Record<number, string[]> = {
  2: ["красный", "чёрный"],
  3: ["красный", "чёрный", "белый"],
  4: ["красный", "чёрный", "белый", "зелёный"],
};

const Dot = ({ name }: { name: string }) => {
  const bg = name ? colorBackground(name) : null;
  return <span className={bg ? "pin-swatch" : "pin-swatch is-ask"} style={bg ? { background: bg } : undefined} aria-hidden="true">{bg ? null : "?"}</span>;
};

export function CableCoreColors({ label, colors, customs, onCustom, onSet }: {
  /** Свои цвета карты и «Свой цвет…» (название → hex). */
  customs?: Record<string, string>;
  onCustom?: (name: string, hex: string) => void;
  /** Подпись кабеля для доступных имён: «кабель 1». */
  label: string;
  /** Цвет жил одного кабеля по порядку ("" — не задан). */
  colors: string[];
  onSet: (patch: Record<number, string | null>) => void;
}) {
  const [open, setOpen] = useState(false);
  /** Жила, которой сейчас выбирают цвет (одна палитра на все жилы окна). */
  const [active, setPick] = useState(0);
  const pick = Math.min(active, Math.max(0, colors.length - 1));
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const down = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", down);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("mousedown", down); window.removeEventListener("keydown", key); };
  }, [open]);
  const set = colors.filter(Boolean).length;
  const preset = CORE_COLOR_PRESETS[colors.length];
  const named = colors.length <= 6;
  return <div className="cable-colors" ref={box}>
    <button type="button" className="cable-colors-btn" aria-expanded={open} aria-label={`Цвета жил: ${label}`}
      title={colors.map((c, k) => `${k + 1} — ${c || "цвет не задан"}`).join("\n")} onClick={() => setOpen(!open)}>
      <span className="cable-colors-cap">Цвета жил</span>
      {colors.map((c, k) => <span className="cable-colors-core" key={k}><span className="pin-num">{k + 1}</span><Dot name={c} />{named && c && <span>{c}</span>}</span>)}
      {!named && <span className="muted">задано {set} из {colors.length}</span>}
    </button>
    {open && <div className="pin-color-pop cable-colors-pop" role="dialog" aria-label={`Цвета жил ${label}`}>
      {preset && <div className="cable-colors-presets">
        <button type="button" className="secondary small" onClick={() => onSet(Object.fromEntries(preset.map((c, k) => [k, c])))}>
          Типовой порядок: {preset.join(", ")}</button>
      </div>}
      <div className="cable-colors-list">
        {colors.map((current, k) => <div className={k === pick ? "cable-colors-row is-active" : "cable-colors-row"} role="group" aria-label={`Жила ${k + 1}`} key={k}>
          <span className="pin-num">{k + 1}</span><Dot name={current} />
          <button type="button" className={current ? "cable-colors-name" : "cable-colors-name muted"} aria-pressed={k === pick} aria-label={`Выбрать жилу ${k + 1}`}
            onClick={() => setPick(k)}>{current || "не задан"}</button>
          <button type="button" className="btn-link" aria-label={`Сбросить цвет жилы ${k + 1}`} title="Сбросить цвет" disabled={!current} onClick={() => onSet({ [k]: null })}>×</button>
        </div>)}
      </div>
      <PaletteGroups label={`Жила ${pick + 1}`} value={colors[pick] ?? ""} customs={customs} onCustom={onCustom} withEarth onPick={(name) => onSet({ [pick]: name })} />
      <div className="pin-color-foot">
        <span className="muted">задано {set} из {colors.length}</span>
        <button type="button" className="btn-link" disabled={!set} onClick={() => onSet(Object.fromEntries(colors.map((_, k) => [k, null])))}>Сбросить все</button>
        <button type="button" className="btn-link" onClick={() => setOpen(false)}>Закрыть</button>
      </div>
    </div>}
  </div>;
}
