import { Chip, Seg } from "./GenControls";
import {
  LASER_END_MM, LASER_MODES, LASER_STEP_MM, LASER_TEXT_MAX, mmRu, WIRE_MARK_DIST_MM, WIRE_MARK_KINDS, WIRE_MARK_LEN_MM, WIRE_MARK_TAPE_WIDTHS_MM,
  WIRE_MARK_TUBE_SIZES_MM, laserTextWarnings, markTubeMmOf, wireMarkCoverOn, wireMarkKindOf, withTubeMm, type LaserMode, type WireMarkCfg, type WireMarkKind,
  type WireMarkRow, type WireMarkSkip,
} from "./tcGenWireMarks";

export const wireMarksOff = (): WireMarkCfg => ({ enabled: false, mat: "", len: "", dist: "" });

const pluralWires = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? "провод" : "проводов");

/** «Метка на проводе» (владелец 26.10, 20.11): нет / трубка / лента маркиратора / лазер ОБ.019. Надписи — из распиновки, по
 *  две на провод; под переключателем — что будет напечатано (провод, надпись конца А, конца В), надпись можно поправить. */
export function WireMarks({ cfg, onChange, rows, skipped, tubes, autoTube, note, wireDia = 0, cover = "", printedTubeMm }: {
  cfg: WireMarkCfg | undefined;
  onChange: (cfg: WireMarkCfg) => void;
  rows: WireMarkRow[];
  skipped: WireMarkSkip[];
  /** Трубки на выбор (состав, затем БД.МАТ). */
  tubes: string[];
  /** Трубка, что выбрана по Ø провода (или по выбранному Ø), пока не выбрана вручную. */
  autoTube: string;
  /** Предупреждение над таблицей (концы, обжатые при резке). */
  note?: string;
  /** Ø изоляции самого толстого маркируемого провода: трубка «по Ø провода» и запомненный за ним выбор Ø. */
  wireDia?: number;
  /** Прозрачная трубка поверх метки, что подобрал генератор («ТУТ(HF) 4/2 прозрачная, 25 мм»). */
  cover?: string;
  /** Ø трубки, что уйдёт на печать при «по Ø провода» (по подобранной трубке состава или БД.МАТ); нет — по Ø провода. */
  printedTubeMm?: number;
}) {
  const own = cfg ?? wireMarksOff();
  const kind = wireMarkKindOf(own);
  const laser = own.laser ?? { mode: "ends" as LaserMode };
  const patch = (next: Partial<WireMarkCfg>) => onChange({ ...own, ...next });
  const patchLaser = (next: Partial<NonNullable<WireMarkCfg["laser"]>>) => patch({ laser: { ...laser, ...next } });
  const setKind = (next: "none" | WireMarkKind) => patch(next === "none" ? { enabled: false } : { enabled: true, kind: next });
  const setText = (row: WireMarkRow, end: "A" | "B", text: string) => {
    const key = `${row.key}:${end}`;
    const texts = { ...(own.texts || {}) };
    if (text.trim() && text.trim() !== (end === "A" ? row.autoA : row.autoB)) texts[key] = text; else delete texts[key];
    patch({ texts });
  };
  const full = kind === "laser" && laser.mode === "full";
  const warnings = kind === "laser" ? laserTextWarnings(own, rows) : {};
  const marks = full ? rows.length : rows.length * 2;
  const hint = kind === "none" ? "две метки на провод — «X1.1 to X2.2» в начале и «X2.2 to X1.1» в конце"
    : !rows.length ? "нет проводов с известными концами — задайте распиновку"
    : kind === "laser" ? `${rows.length} ${pluralWires(rows.length)}${full ? "" : ` · ${marks} меток`} на изделие`
    : `${rows.length} ${pluralWires(rows.length)} · ${marks} меток на изделие`;
  const mmInput = (label: string, value: string | undefined, placeholder: number | string, set: (value: string) => void, caption = label) =>
    <label className="gfield"><span className="gfield-label">{caption}</span>
      <span className="gunit" data-unit="мм"><input aria-label={label} inputMode="decimal" value={value ?? ""} placeholder={String(placeholder)} onChange={(event) => set(event.target.value)} /></span></label>;
  return <div className="gen-marks gen-wmarks" aria-label="Маркировка проводов">
    <div className="gen-marks-bar">
      <div className="gfield"><span className="gfield-label">Метка на проводе <small>{hint}</small></span>
        <Seg label="Метка на проводе" value={kind} options={WIRE_MARK_KINDS} onChange={setKind} /></div>
      {kind === "tube" && <label className="gfield"><span className="gfield-label">Ø трубки</span>
        <select aria-label="Диаметр трубки маркировки" value={own.tubeMm || ""} onChange={(event) => onChange(withTubeMm(own, event.target.value, wireDia))}>
          <option value="">по Ø провода — Ø {mmRu(!own.tubeMm && printedTubeMm ? printedTubeMm : markTubeMmOf({ ...own, tubeMm: "" }, wireDia))} мм</option>
          {WIRE_MARK_TUBE_SIZES_MM.map((mm) => <option key={mm} value={String(mm)}>Ø {mmRu(mm)} мм</option>)}
        </select></label>}
      {kind === "tape" && <label className="gfield"><span className="gfield-label">Ширина ленты</span>
        <select aria-label="Ширина ленты маркировки" value={own.tapeMm || String(WIRE_MARK_TAPE_WIDTHS_MM[0])} onChange={(event) => patch({ tapeMm: event.target.value })}>
          {WIRE_MARK_TAPE_WIDTHS_MM.map((mm) => <option key={mm} value={String(mm)}>{mmRu(mm)} мм</option>)}
        </select></label>}
      {(kind === "tube" || kind === "tape") && <div className="gfield"><span className="gfield-label">Прозрачная трубка{cover ? <small>{cover}</small> : null}</span>
        <Chip checked={wireMarkCoverOn(own)} onChange={(on) => patch({ cover: on })}>Покрыть прозрачной трубкой</Chip></div>}
      {kind === "laser" && <div className="gfield"><span className="gfield-label">Как маркировать <small>на станке латиница, до {LASER_TEXT_MAX} знаков</small></span>
        <Seg label="Режим лазерной маркировки" value={laser.mode} options={LASER_MODES} onChange={(mode) => patchLaser({ mode })} /></div>}
    </div>
    {kind !== "none" && <>
      <div className="gen-wmarks-params">
        {kind === "tube" && <label className="gfield"><span className="gfield-label">Трубка</span>
          <select aria-label="Трубка маркировки проводов" value={own.mat} onChange={(event) => patch({ mat: event.target.value })}>
            <option value="">{autoTube ? `${own.tubeMm ? "по Ø" : "по Ø провода"} — ${autoTube}` : "— нет трубки в составе и БД.МАТ —"}</option>
            {tubes.map((name) => <option key={name} value={name}>{name}</option>)}
            {own.mat && !tubes.includes(own.mat) && <option value={own.mat}>{own.mat}</option>}
          </select></label>}
        {(kind === "tube" || kind === "tape") && <>
          {mmInput("Длина метки провода", own.len, WIRE_MARK_LEN_MM, (len) => patch({ len }), "Длина метки")}
          {mmInput("Расстояние метки от конца провода", own.dist, WIRE_MARK_DIST_MM, (dist) => patch({ dist }), "От конца провода")}
        </>}
        {kind === "laser" && !full && <>
          {mmInput("Лазер: от торца А", laser.frontMm, LASER_END_MM, (frontMm) => patchLaser({ frontMm }), "От торца А")}
          {mmInput("Лазер: от торца В", laser.backMm, LASER_END_MM, (backMm) => patchLaser({ backMm }), "От торца В")}
        </>}
        {full && <>
          <label className="gfield"><span className="gfield-label">Надпись по всей длине</span>
            <input aria-label="Лазер: надпись по всей длине" maxLength={LASER_TEXT_MAX} value={laser.middle ?? ""} placeholder="как на конце А" onChange={(event) => patchLaser({ middle: event.target.value })} /></label>
          {mmInput("Лазер: шаг надписи", laser.stepMm, LASER_STEP_MM, (stepMm) => patchLaser({ stepMm }), "Шаг")}
          {mmInput("Лазер: от торцов", laser.frontMm, LASER_END_MM, (mm) => patchLaser({ frontMm: mm, backMm: mm }), "От торцов")}
        </>}
      </div>
      {note && <div className="warning-inline">{note}</div>}
      {rows.length > 0 && <div className="gtable-wrap"><table className="gtable">
        <colgroup><col className="c-n" /><col className="c-wm" /><col className="c-wm" /></colgroup>
        <thead><tr><th title="Номер провода в строке «Кабели»">№</th><th>Метка на конце А</th><th>Метка на конце В</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.key}>
          <td className="num" title={warnings[row.key]?.join("; ")}>{row.num}{warnings[row.key] && <span className="warning-inline" role="alert"> ⚠ {warnings[row.key].join("; ")}</span>}</td>
          <td><input aria-label={`Метка конца А, провод ${row.num}`} maxLength={kind === "laser" ? LASER_TEXT_MAX : undefined} value={row.textA} onChange={(event) => setText(row, "A", event.target.value)} /></td>
          <td><input aria-label={`Метка конца В, провод ${row.num}`} maxLength={kind === "laser" ? LASER_TEXT_MAX : undefined} value={row.textB} onChange={(event) => setText(row, "B", event.target.value)} /></td>
        </tr>)}</tbody>
      </table></div>}
      {skipped.length > 0 && <div className="gempty">Без метки: {skipped.map((item) => `№ ${item.num} — ${item.reason}`).join("; ")}</div>}
    </>}
  </div>;
}
