import { Toggle } from "./GenControls";
import { WIRE_MARK_DIST_MM, WIRE_MARK_LEN_MM, type WireMarkCfg, type WireMarkRow, type WireMarkSkip } from "./tcGenWireMarks";

export const wireMarksOff = (): WireMarkCfg => ({ enabled: false, mat: "", len: "", dist: "" });

/** «Маркировка проводов: откуда → куда» (владелец 26.10): по две метки на провод, надписи — из распиновки; под переключателем —
 *  что будет напечатано (провод, надпись конца А, надпись конца В), надпись можно поправить. */
export function WireMarks({ cfg, onChange, rows, skipped, tubes, autoTube, note }: {
  cfg: WireMarkCfg | undefined;
  onChange: (cfg: WireMarkCfg) => void;
  rows: WireMarkRow[];
  skipped: WireMarkSkip[];
  /** Трубки на выбор (состав, затем БД.МАТ). */
  tubes: string[];
  /** Трубка, что выбрана по Ø провода, пока не выбрана вручную. */
  autoTube: string;
  /** Предупреждение над таблицей (концы, обжатые при резке). */
  note?: string;
}) {
  const own = cfg ?? wireMarksOff();
  const patch = (next: Partial<WireMarkCfg>) => onChange({ ...own, ...next });
  const setText = (row: WireMarkRow, end: "A" | "B", text: string) => {
    const key = `${row.key}:${end}`;
    const texts = { ...(own.texts || {}) };
    if (text.trim() && text.trim() !== (end === "A" ? row.autoA : row.autoB)) texts[key] = text; else delete texts[key];
    patch({ texts });
  };
  const hint = !own.enabled ? "две метки на провод — «X1.1 to X2.2» в начале и «X2.2 to X1.1» в конце"
    : rows.length ? `${rows.length} ${rows.length % 10 === 1 && rows.length % 100 !== 11 ? "провод" : "проводов"} · ${rows.length * 2} меток на изделие` : "нет проводов с известными концами — задайте распиновку";
  return <div className="gen-marks gen-wmarks" aria-label="Маркировка проводов">
    <div className="gen-marks-bar">
      <Toggle checked={own.enabled} onChange={(enabled) => patch({ enabled })} title="Маркировка проводов: откуда → куда" hint={hint} />
    </div>
    {own.enabled && <>
      <div className="gen-wmarks-params">
        <label className="gfield"><span className="gfield-label">Трубка</span>
          <select aria-label="Трубка маркировки проводов" value={own.mat} onChange={(event) => patch({ mat: event.target.value })}>
            <option value="">{autoTube ? `по Ø провода — ${autoTube}` : "— нет трубки в составе и БД.МАТ —"}</option>
            {tubes.map((name) => <option key={name} value={name}>{name}</option>)}
            {own.mat && !tubes.includes(own.mat) && <option value={own.mat}>{own.mat}</option>}
          </select></label>
        <label className="gfield"><span className="gfield-label">Длина метки</span>
          <span className="gunit" data-unit="мм"><input aria-label="Длина метки провода" inputMode="decimal" value={own.len} placeholder={WIRE_MARK_LEN_MM} onChange={(event) => patch({ len: event.target.value })} /></span></label>
        <label className="gfield"><span className="gfield-label">От конца провода</span>
          <span className="gunit" data-unit="мм"><input aria-label="Расстояние метки от конца провода" inputMode="decimal" value={own.dist} placeholder={WIRE_MARK_DIST_MM} onChange={(event) => patch({ dist: event.target.value })} /></span></label>
      </div>
      {note && <div className="warning-inline">{note}</div>}
      {rows.length > 0 && <div className="gtable-wrap"><table className="gtable">
        <colgroup><col className="c-n" /><col className="c-wm" /><col className="c-wm" /></colgroup>
        <thead><tr><th title="Номер провода в строке «Кабели»">№</th><th>Метка на конце А</th><th>Метка на конце В</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.key}>
          <td className="num">{row.num}</td>
          <td><input aria-label={`Метка конца А, провод ${row.num}`} value={row.textA} onChange={(event) => setText(row, "A", event.target.value)} /></td>
          <td><input aria-label={`Метка конца В, провод ${row.num}`} value={row.textB} onChange={(event) => setText(row, "B", event.target.value)} /></td>
        </tr>)}</tbody>
      </table></div>}
      {skipped.length > 0 && <div className="gempty">Без метки: {skipped.map((item) => `№ ${item.num} — ${item.reason}`).join("; ")}</div>}
    </>}
  </div>;
}
