import { useId } from "react";
import {
  MARK_PLACES, markMaterialSource, markRowActive, newMarkRow, type MarkMat, type MarkPlace, type MarkRow,
} from "./tcGenMarks";

type CompLike = { art?: string; name?: string };

/** «Маркировка по КД» генератора: таблица меток (что, надпись, материал, где, сколько на изделие) и быстрое
 *  заполнение — по разъёмам формы и из чертежа жгута позиции. Материал — из состава или БД.МАТ; чужой помечен. */
export function MarkTable({ rows, onChange, targets, kitMarks, comps, mats, onByConnectors, onFromDrawing, drawingBusy, note }: {
  rows: MarkRow[];
  onChange: (rows: MarkRow[]) => void;
  targets: string[];
  /** Бирки из состава — первыми в выборе материала. */
  kitMarks: CompLike[];
  comps: CompLike[];
  mats: MarkMat[];
  onByConnectors?: () => void;
  onFromDrawing?: () => void;
  drawingBusy?: boolean;
  note?: string;
}) {
  const id = useId();
  const set = (index: number, patch: Partial<MarkRow>) => onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const total = rows.filter(markRowActive).reduce((sum, row) => sum + (Number(row.qty.replace(",", ".")) || 1), 0);
  const kitNames = kitMarks.map((comp) => comp.art || comp.name || "").filter(Boolean);
  return <div className="gen-marks" aria-label="Маркировка по КД">
    <div className="gen-marks-head">
      <h4>Маркировка по КД</h4>
      {rows.some(markRowActive) && <span className="muted">{rows.filter(markRowActive).length} меток · {String(total).replace(".", ",")} шт. на изделие</span>}
      <span className="gen-marks-actions">
        {onByConnectors && <button type="button" className="secondary small" onClick={onByConnectors}>По разъёмам</button>}
        {onFromDrawing && <button type="button" className="secondary small" disabled={drawingBusy} onClick={onFromDrawing}>{drawingBusy ? "Читаю чертёж…" : "Из чертежа жгута"}</button>}
        <button type="button" className="secondary small" onClick={() => onChange([...rows, newMarkRow({ material: kitNames[0] || "" })])}>+ Метка</button>
      </span>
    </div>
    {note && <div className="warning-inline">{note}</div>}
    <datalist id={`${id}-targets`}>{targets.map((item) => <option key={item} value={item} />)}</datalist>
    <datalist id={`${id}-mats`}>
      {kitNames.map((name) => <option key={`kit:${name}`} value={name}>из состава</option>)}
      {mats.filter((mat) => !kitNames.includes(mat.name)).map((mat) => <option key={mat.name} value={mat.name}>{mat.group}</option>)}
    </datalist>
    {rows.length ? <div className="gtable-wrap"><table className="gtable">
      <colgroup><col className="c-n" /><col className="c-target" /><col className="c-text" /><col className="c-mat" /><col className="c-place" /><col className="c-qty" /><col className="c-x" /></colgroup>
      <thead><tr><th>№</th><th>Что маркируем</th><th>Надпись</th><th>Вид / материал</th><th>Где</th><th className="num" title="Штук на изделие">Кол-во</th><th aria-label="Удалить" /></tr></thead>
      <tbody>{rows.map((row, index) => {
        const n = index + 1;
        const missing = markMaterialSource(row.material, mats, comps) === null;
        return <tr key={index}>
          <td className="num">{n}</td>
          <td><input aria-label={`Что маркируем ${n}`} list={`${id}-targets`} value={row.target} title={row.target || undefined} placeholder="жгут, разъём, провод" onChange={(event) => set(index, { target: event.target.value })} /></td>
          <td><input aria-label={`Надпись ${n}`} value={row.text} placeholder="как в КД" onChange={(event) => set(index, { text: event.target.value })} /></td>
          <td><input aria-label={`Материал ${n}`} list={`${id}-mats`} className={missing ? "is-warn" : undefined} value={row.material} placeholder="бирка, трубка, этикетка"
            title={missing ? "Нет в справочнике БД.МАТ и в составе" : row.material || undefined} onChange={(event) => set(index, { material: event.target.value })} />
            {missing && <small className="tone-warn">нет в справочнике</small>}</td>
          <td><span className={row.place === "dist" || row.place === "free" ? "gen-marks-place two" : "gen-marks-place"}>
            <select aria-label={`Где ${n}`} value={row.place} onChange={(event) => set(index, { place: event.target.value as MarkPlace })}>
              {MARK_PLACES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            {row.place === "dist" && <span className="gunit" data-unit="мм"><input aria-label={`Расстояние от конца ${n}`} inputMode="decimal" value={row.mm} placeholder="—" onChange={(event) => set(index, { mm: event.target.value })} /></span>}
            {row.place === "free" && <input aria-label={`Место ${n}`} value={row.where} placeholder="где по КД" onChange={(event) => set(index, { where: event.target.value })} />}
          </span></td>
          <td><input aria-label={`На изделие ${n}`} title="Штук на изделие" className="num" inputMode="decimal" value={row.qty} onChange={(event) => set(index, { qty: event.target.value })} /></td>
          <td><button type="button" className="btn-x" aria-label={`Удалить метку ${n}`} onClick={() => onChange(rows.filter((_, i) => i !== index))}>×</button></td>
        </tr>;
      })}</tbody>
    </table></div> : <div className="gempty">Метки не заданы — в маркировку пойдут бирки из состава. Заполните по разъёмам, из чертежа жгута или добавьте метку.</div>}
  </div>;
}
