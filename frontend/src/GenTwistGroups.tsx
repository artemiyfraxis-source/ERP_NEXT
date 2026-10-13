import { addTwistMember, rowMembers, setTwistMember, type TwistProblems, type TwistRow } from "./twistGroups";

/** «Пары и группы для свивки»: строка — пара проводов, «+ провод» делает её тройкой и больше; провод, убранный из
 *  тройки («— убрать —»), освобождает место. Под строками — проверки (twistGroups.ts): стоп красным, вопросы — жёлтым. */
export function TwistGroups({ rows, wires, problems, onChange, onAuto }: {
  rows: TwistRow[];
  /** Кабели формы по позиции: подпись в списке выбора. */
  wires: Array<{ pos: number; label: string }>;
  problems: TwistProblems;
  onChange: (rows: TwistRow[]) => void;
  onAuto: () => void;
}) {
  return <div className="twist-pairs">
    <span className="gfield-label">Пары и группы для свивки</span>
    {rows.map((row, i) => {
      const members = rowMembers(row);
      return <div className="twist-pair-row" key={i} role="group" aria-label={`Свивка ${i + 1}`}
        style={{ gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr)) auto 28px`, maxWidth: 170 * members.length + 110 }}>
        {members.map((value, k) => <select key={k} aria-label={`Свивка ${i + 1}, провод ${k + 1}`} value={value}
          onChange={(event) => onChange(setTwistMember(rows, i, k, event.target.value))}>
          <option value="">{k < 2 ? "—" : "— убрать —"}</option>
          {wires.map((wire) => <option key={wire.pos} value={wire.pos}>{wire.label}</option>)}
        </select>)}
        <button type="button" className="btn-link" disabled={members.length >= wires.length} aria-label={`Добавить провод в свивку ${i + 1}`}
          title="Свить три провода и больше" onClick={() => onChange(addTwistMember(rows, i))}>+ провод</button>
        <button type="button" className="btn-remove" title="Убрать свивку" aria-label={`Убрать свивку ${i + 1}`}
          onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
      </div>;
    })}
    <div className="row-actions">
      <button type="button" className="btn-add" onClick={() => onChange([...rows, { a: "", b: "" }])}>+ Добавить пару</button>
      <button type="button" className="btn-add" title="Определить пары по запасу длины из спецификации" onClick={onAuto}>Авто</button>
    </div>
    {problems.error && <small className="opt-note tone-danger" role="alert">{problems.error}</small>}
    {problems.warnings.length > 0 && <div className="twist-pairs" aria-live="polite">
      {problems.warnings.map((text) => <small className="opt-note tone-warn" key={text}>{text}</small>)}</div>}
  </div>;
}
