import { useState } from "react";
import { COLOR_GROUPS, PALETTE, SWATCHES } from "./wireColors";

/* Палитра жилы одним куском для окна «Цвет жилы» и «Цвета жил» (владелец 15.10: «думаешь, мне хватит этих цветов?»):
   цвета магазина рядами — основные · тёмные · светлые · металл/натуральный/прозрачный, ниже свои цвета карты и «Свой цвет…»
   (название + выбор цвета). Свой цвет хранится именем, hex запоминает карта генератора. */

const EARTH_SWATCH = SWATCHES.find((swatch) => swatch.hex2);

export function PaletteGroups({ label, value, onPick, customs = {}, onCustom, withEarth = false, tag = label }: {
  /** Подпись ряда для кнопок: «Цвет», «Жила 2». */
  label: string;
  /** Выбранное название цвета. */
  value: string;
  onPick: (name: string) => void;
  /** Свои цвета карты: название → hex. */
  customs?: Record<string, string>;
  /** Есть — под палитрой «Свой цвет…»: запомнить название и hex; выбор цвета ставит родитель через onPick. */
  onCustom?: (name: string, hex: string) => void;
  /** «жёлто-зелёный» (PE) отдельной точкой — для окна «Цвета жил». */
  withEarth?: boolean;
  tag?: string;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [hex, setHex] = useState("#6b7a3a");
  const own = Object.entries(customs);
  const dot = (title: string, background: string) => <button type="button" key={title} className="swatch" aria-pressed={value.toLowerCase() === title.toLowerCase()}
    aria-label={`${tag}: ${title}`} title={title} style={{ background }} onClick={() => onPick(title)} />;
  const save = () => {
    const clean = name.trim().toLowerCase();
    if (!clean || !onCustom) return;
    onCustom(clean, hex);
    onPick(clean);
    setAdding(false);
    setName("");
  };
  return <div className="palette-groups" aria-label={label}>
    {COLOR_GROUPS.map((group) => <div className="palette-group" key={group.id}>
      <small>{group.title}</small>
      <div className="swatch-row">
        {PALETTE.filter((color) => color.group === group.id).map((color) => dot(color.name, color.hex))}
        {withEarth && group.id === "other" && EARTH_SWATCH && dot(EARTH_SWATCH.name, `linear-gradient(135deg, ${EARTH_SWATCH.hex} 50%, ${EARTH_SWATCH.hex2} 50%)`)}
      </div>
    </div>)}
    {(own.length > 0 || onCustom) && <div className="palette-group">
      <small>Свои</small>
      <div className="swatch-row">
        {own.map(([title, color]) => dot(title, color))}
        {onCustom && !adding && <button type="button" className="btn-link" onClick={() => setAdding(true)}>Свой цвет…</button>}
      </div>
    </div>}
    {adding && <div className="palette-custom" role="group" aria-label="Свой цвет">
      <input type="text" aria-label="Название цвета" placeholder="болотный" value={name} maxLength={40} autoFocus
        onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); save(); } }} />
      <input type="color" aria-label="Цвет (hex)" value={hex} onChange={(event) => setHex(event.target.value)} />
      <code>{hex}</code>
      <button type="button" className="secondary small" disabled={!name.trim()} onClick={save}>Добавить</button>
      <button type="button" className="btn-link" onClick={() => setAdding(false)}>Отмена</button>
    </div>}
  </div>;
}
