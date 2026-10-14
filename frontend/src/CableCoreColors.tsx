import { useEffect, useRef, useState } from "react";
import { SWATCHES, colorBackground } from "./wireColors";

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

export function CableCoreColors({ label, colors, onSet }: {
  /** Подпись кабеля для доступных имён: «кабель 1». */
  label: string;
  /** Цвет жил одного кабеля по порядку ("" — не задан). */
  colors: string[];
  onSet: (patch: Record<number, string | null>) => void;
}) {
  const [open, setOpen] = useState(false);
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
        {colors.map((current, k) => <div className="cable-colors-row" role="group" aria-label={`Жила ${k + 1}`} key={k}>
          <span className="pin-num">{k + 1}</span><Dot name={current} />
          <span className={current ? "cable-colors-name" : "cable-colors-name muted"}>{current || "не задан"}</span>
          <div className="swatch-row">{SWATCHES.map((color) => <button type="button" key={color.name} className="swatch" aria-pressed={current === color.name}
            aria-label={`Жила ${k + 1}: ${color.name}`} title={color.name}
            style={{ background: color.hex2 ? `linear-gradient(135deg, ${color.hex} 50%, ${color.hex2} 50%)` : color.hex }}
            onClick={() => onSet({ [k]: color.name })} />)}</div>
          <button type="button" className="btn-link" aria-label={`Сбросить цвет жилы ${k + 1}`} title="Сбросить цвет" disabled={!current} onClick={() => onSet({ [k]: null })}>×</button>
        </div>)}
      </div>
      <div className="pin-color-foot">
        <span className="muted">задано {set} из {colors.length}</span>
        <button type="button" className="btn-link" disabled={!set} onClick={() => onSet(Object.fromEntries(colors.map((_, k) => [k, null])))}>Сбросить все</button>
        <button type="button" className="btn-link" onClick={() => setOpen(false)}>Закрыть</button>
      </div>
    </div>}
  </div>;
}
