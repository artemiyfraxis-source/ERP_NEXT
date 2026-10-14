import { useId, useRef } from "react";
import { AutoTextarea } from "./AutoTextarea";
import { Toggle } from "./GenControls";
import {
  MARK_KIND_DEFAULT, MARK_KINDS, MARK_PLACES, markIsTube, markMaterialSource, markRowActive, newMarkRow, productMarkRow, type MarkMat, type MarkPlace, type MarkRow,
} from "./tcGenMarks";
import { expandMarkSerial, hasMarkSerial, insertMarkSerial, MARK_SERIAL_WIDTHS, markSerialWidth, withMarkSerialWidth } from "./markSerial";

type CompLike = { art?: string; name?: string };

/** Штук меток на изделие (строка без «Кол-во» — одна). */
export function markTotal(rows: MarkRow[]): string {
  return String(rows.filter(markRowActive).reduce((sum, row) => sum + (Number(row.qty.replace(",", ".")) || 1), 0)).replace(".", ",");
}

/** «Маркировка жгута» генератора: таблица меток (что, надпись, материал, где, сколько на изделие) и быстрое
 *  заполнение — по разъёмам формы и из чертежа жгута позиции. Материал — из состава или БД.МАТ; чужой помечен. */
export function MarkTable({ enabled, onEnabled, hint, rows, onChange, targets, kitMarks, comps, mats, productDesignation, onByConnectors, onFromDrawing, drawingBusy, note }: {
  enabled: boolean;
  onEnabled: (on: boolean) => void;
  /** Серая подсказка под переключателем: счёт меток или бирки из состава. */
  hint: string;
  rows: MarkRow[];
  onChange: (rows: MarkRow[]) => void;
  targets: string[];
  /** Маркировочные позиции состава: в выборе вида после Термотрубки и Хлястика, первая — по умолчанию у новых меток. */
  kitMarks: CompLike[];
  /** Обозначение изделия для «Маркировать изделие»; пусто — кнопки нет. */
  productDesignation?: string;
  comps: CompLike[];
  mats: MarkMat[];
  onByConnectors?: () => void;
  onFromDrawing?: () => void;
  drawingBusy?: boolean;
  note?: string;
}) {
  const id = useId();
  // Поле «Надпись» строки и где в нём стоял курсор: «№ изделия» встаёт туда, иначе в конец.
  const textBoxes = useRef(new Map<number, HTMLTextAreaElement>());
  const carets = useRef(new Map<number, number>());
  const set = (index: number, patch: Partial<MarkRow>) => onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const kitNames = kitMarks.map((comp) => comp.art || comp.name || "").filter((name) => name && !(MARK_KINDS as readonly string[]).includes(name));
  const options = [...MARK_KINDS as readonly string[], ...kitNames];
  const hasProductRow = rows.some((row) => row.place === "item");
  function insertSerial(index: number) {
    const inserted = insertMarkSerial(rows[index].text, carets.current.get(index));
    set(index, { text: inserted.text });
    carets.current.set(index, inserted.caret);
    requestAnimationFrame(() => {
      const box = textBoxes.current.get(index);
      box?.focus();
      box?.setSelectionRange(inserted.caret, inserted.caret);
    });
  }
  return <div className="gen-marks" aria-label="Маркировка по КД">
    <div className="gen-marks-bar">
      <Toggle checked={enabled} onChange={onEnabled} title="Маркировка жгута" hint={hint} />
      {enabled && <span className="gen-marks-actions">
        {onByConnectors && <button type="button" className="secondary small" onClick={onByConnectors}>По разъёмам</button>}
        {onFromDrawing && <button type="button" className="secondary small" disabled={drawingBusy} onClick={onFromDrawing}>{drawingBusy ? "Читаю чертёж…" : "Из чертежа жгута"}</button>}
        <button type="button" className="secondary small" onClick={() => onChange([...rows, newMarkRow({ material: kitNames[0] || MARK_KIND_DEFAULT })])}>+ Метка</button>
        {productDesignation !== undefined && <button type="button" className="secondary small" disabled={hasProductRow} title={hasProductRow ? "Метка изделия уже в таблице" : undefined}
          onClick={() => onChange([...rows, productMarkRow(productDesignation)])}>Маркировать изделие</button>}
      </span>}
    </div>
    {enabled && note && <div className="warning-inline">{note}</div>}
    {enabled && <>
    <datalist id={`${id}-targets`}>{targets.map((item) => <option key={item} value={item} />)}</datalist>
    {rows.length ? <div className="gtable-wrap"><table className="gtable">
      <colgroup><col className="c-n" /><col className="c-target" /><col className="c-text" /><col className="c-mat" /><col className="c-place" /><col className="c-qty" /><col className="c-len" /><col className="c-x" /></colgroup>
      <thead><tr><th>№</th><th>Что маркируем</th><th>Надпись</th><th>Вид / материал</th><th>Где</th><th className="num" title="Штук на изделие">Кол-во</th><th className="num" title="Длина отрезка трубки, мм">Длина, мм</th><th aria-label="Удалить" /></tr></thead>
      <tbody>{rows.map((row, index) => {
        const n = index + 1;
        const missing = markMaterialSource(row.material, mats, comps) === null;
        return <tr key={index}>
          <td className="num">{n}</td>
          <td><input aria-label={`Что маркируем ${n}`} list={`${id}-targets`} value={row.target} title={row.target || undefined} placeholder="жгут, разъём, провод" onChange={(event) => set(index, { target: event.target.value })} /></td>
          <td><AutoTextarea aria-label={`Надпись ${n}`} value={row.text} placeholder="как в КД" onChange={(text) => set(index, { text })}
              ref={(node) => { if (node) textBoxes.current.set(index, node); else textBoxes.current.delete(index); }}
              onSelect={(event) => carets.current.set(index, event.currentTarget.selectionStart)} />
            {/* «№ изделия»: у надписи с номером — разрядность и пример всегда; у остальных кнопка видна в строке с фокусом. */}
            {hasMarkSerial(row.text) ? <span className="gen-marks-serial on">
              <select aria-label={`Цифр в номере ${n}`} value={markSerialWidth(row.text)} onChange={(event) => set(index, { text: withMarkSerialWidth(row.text, Number(event.target.value)) })}>
                {MARK_SERIAL_WIDTHS.map((width) => <option key={width} value={width}>{width ? `${width} цифры, ${"1".padStart(width, "0")}` : "без нулей, 1"}</option>)}
              </select>
              <small title="Номер изделия ставит задание: на каждом изделии свой">{expandMarkSerial(row.text, 1)}, {expandMarkSerial(row.text, 2)} …</small>
            </span> : <span className="gen-marks-serial">
              <button type="button" className="gen-marks-serial-add" aria-label={`Вставить № изделия в надпись ${n}`}
                title="Номер изделия в партии: на каждом изделии задания свой — 1, 2, 3…" onClick={() => insertSerial(index)}>+ № изделия</button>
            </span>}</td>
          <td><select aria-label={`Материал ${n}`} className={missing ? "is-warn" : undefined} value={row.material} title={missing ? "Нет в справочнике БД.МАТ и в составе" : undefined}
            onChange={(event) => set(index, { material: event.target.value })}>
            {options.map((name) => <option key={name} value={name}>{name}</option>)}
            {!options.includes(row.material) && <option value={row.material}>{row.material || "— не задан —"}</option>}
          </select>
            {missing && <small className="tone-warn">нет в справочнике</small>}</td>
          <td><span className="gen-marks-place">
            <select aria-label={`Где ${n}`} value={row.place} onChange={(event) => set(index, { place: event.target.value as MarkPlace })}>
              {MARK_PLACES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            {row.place === "dist" && <span className="gunit" data-unit="мм"><input aria-label={`Расстояние от конца ${n}`} inputMode="decimal" value={row.mm} placeholder="—" onChange={(event) => set(index, { mm: event.target.value })} /></span>}
            {row.place === "free" && <AutoTextarea aria-label={`Место ${n}`} value={row.where} placeholder="где по КД" onChange={(where) => set(index, { where })} />}
          </span></td>
          <td><input aria-label={`На изделие ${n}`} title="Штук на изделие" className="num" inputMode="decimal" value={row.qty} onChange={(event) => set(index, { qty: event.target.value })} /></td>
          <td>{markIsTube(row.material) && <input aria-label={`Длина трубки ${n}`} title="Длина отрезка трубки, мм" className="num" inputMode="decimal" value={row.len ?? ""} placeholder="—"
            onChange={(event) => set(index, { len: event.target.value })} />}</td>
          <td><button type="button" className="btn-x" aria-label={`Удалить метку ${n}`} onClick={() => onChange(rows.filter((_, i) => i !== index))}>×</button></td>
        </tr>;
      })}</tbody>
    </table></div> : <div className="gempty">Метки не заданы — в маркировку пойдут бирки из состава. Заполните по разъёмам, из чертежа жгута или добавьте метку.</div>}
  </>}
  </div>;
}
