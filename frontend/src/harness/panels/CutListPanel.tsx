// «Карта резки»: заготовки жгута (провода вне кабелей, кабели, оболочки) с длиной резки и метражом на заданное число
// жгутов и лист резки и разделки выбранной заготовки. Всё считает сервер (`rules.cut_length` — та же длина, что в
// «Ручной сборке»); здесь только показ и правка исходных величин провода или кабеля командами редактора.
// Рисунок листа — по схеме резки TECHMAP-GRAPHER (CutDiagramPanel.tsx, © Василий, SaveOurSouls, с разрешения автора).
import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "../../DataTable";
import type { HarnessCutEnd, HarnessCutItem, HarnessCutSheet } from "../../api";
import type { EditorCommand } from "../editor/commands";
import type { HarnessDesignDocument } from "../editor/model";
import { formatNumber as mm, useHarnessDocument } from "./documents-source";

function Dimension({ x1, x2, y, label }: { x1: number; x2: number; y: number; label: string }) {
  return <g className="he-cut-dimension"><path d={`M${x1} ${y - 5}v10m0 -5H${x2}m0 -5v10`} fill="none" stroke="currentColor" /><text x={(x1 + x2) / 2} y={y - 7} textAnchor="middle">{label}</text></g>;
}

function EndSteps({ end, side }: { end: HarnessCutEnd; side: "a" | "b" }) {
  const steps = end.steps;
  const maximum = steps.at(-1)?.length_mm || 1, maxDiameter = steps.at(-1)?.diameter_mm || 1;
  let previous = 0;
  return <g>{steps.map((step, i) => {
    const from = previous / maximum * 150, to = step.length_mm / maximum * 150;
    previous = step.length_mm;
    const x = side === "a" ? 60 + from : 700 - to, half = 8 + (step.diameter_mm ?? maxDiameter) / maxDiameter * 14;
    return <g key={step.index}><rect x={x} y={125 - half} width={Math.max(1, to - from)} height={half * 2} fill={i % 2 ? "#b4c6d1" : "#e2b362"} stroke="#426477" />
      <Dimension x1={side === "a" ? 60 : 700 - to} x2={side === "a" ? 60 + to : 700} y={175 + i * 25} label={`L${step.index}=${mm(step.length_mm)} мм`} /></g>;
  })}</g>;
}

/** Условный рисунок заготовки: концы A и Б, длина резки, слои разделки или снятие общей оболочки кабеля. */
export function CutSheetSvg({ sheet, cutMm, a = sheet.a, b = sheet.b }: { sheet: HarnessCutSheet; cutMm: number | null; a?: HarnessCutEnd; b?: HarnessCutEnd }) {
  const withSheath = sheet.kind === "cable" && a === sheet.a;
  const from = sheet.sheath?.a_mm, to = sheet.sheath?.b_mm;
  const sheathStart = 60 + (from === 0 ? 0 : from !== null && from !== undefined && cutMm ? Math.min(640, 640 * from / cutMm) : 105);
  const sheathEnd = 700 - (to === 0 ? 0 : to !== null && to !== undefined && cutMm ? Math.min(640, 640 * to / cutMm) : 105);
  const height = 210 + Math.max(a.steps.length, b.steps.length) * 25;
  return <svg className="he-cut-svg" viewBox={`0 0 760 ${height}`} role="img" aria-label={`Схема резки ${sheet.title}: A ${a.label}, Б ${b.label}, длина ${mm(cutMm)} мм`}>
    <text x="60" y="28">A · {a.label}</text><text x="700" y="28" textAnchor="end">Б · {b.label}</text>
    <Dimension x1={60} x2={700} y={70} label={`Заготовка ${mm(cutMm)} мм`} />
    <path d="M60 125H700" stroke="#597789" strokeWidth="6" />
    <rect x={a.steps.length ? 210 : 60} y="102" width={640 - (a.steps.length ? 150 : 0) - (b.steps.length ? 150 : 0)} height="46" fill="#e1e9ee" stroke="#426477" />
    {withSheath
      ? <><rect x={sheathStart} y="98" width={Math.max(0, sheathEnd - sheathStart)} height="54" fill="#8da4b2" stroke="#426477" />
        <Dimension x1={60} x2={sheathStart} y={177} label={`A: ${mm(from ?? null)} мм`} /><Dimension x1={sheathEnd} x2={700} y={177} label={`Б: ${mm(to ?? null)} мм`} />
        <text x="380" y="130" textAnchor="middle">Общая оболочка</text></>
      : <><EndSteps side="a" end={a} /><EndSteps side="b" end={b} />
        {!a.steps.length && <text x="65" y="178">Разделка A не задана</text>}{!b.steps.length && <text x="695" y="178" textAnchor="end">Разделка Б не задана</text>}</>}
    <text x="380" y={height - 8} textAnchor="middle">Условное изображение · размеры в мм</text>
  </svg>;
}

function LayerTable({ label, end }: { label: string; end: HarnessCutEnd }) {
  return <section><strong>{label} · {end.profile ?? "Профиль не задан"}</strong>{end.steps.length > 0 && <table className="grid he-cut-layers"><thead><tr><th>Слой</th><th>Ø, мм</th><th>L от торца, мм</th><th>Ступень, мм</th></tr></thead>
    <tbody>{end.steps.map(s => <tr key={s.index}><td>L{s.index}</td><td className="num">{mm(s.diameter_mm)}</td><td className="num">{mm(s.length_mm)}</td><td className="num">{mm(s.step_mm)}</td></tr>)}</tbody></table>}</section>;
}

type CutField = "lengthMm" | "endCorrectionFromMm" | "endCorrectionToMm" | "cutRoundingStepMm";

function CutInput({ label, value, nullable, onCommit }: { label: string; value: number | null; nullable: boolean; onCommit: (value: string) => boolean }) {
  const [draft, setDraft] = useState(value === null ? "" : String(value)), [invalid, setInvalid] = useState(false);
  useEffect(() => { setDraft(value === null ? "" : String(value)); setInvalid(false); }, [value]);
  const commit = () => {
    const v = draft.trim().replace(",", ".");
    if (v === String(value ?? "")) return;
    if ((v === "" && !nullable) || (v !== "" && !Number.isFinite(Number(v)))) { setInvalid(true); return; }
    setInvalid(!onCommit(v));
  };
  return <label>{label}<input type="text" inputMode="decimal" aria-label={label} aria-invalid={invalid} value={draft}
    onChange={e => { setDraft(e.target.value); setInvalid(false); }} onBlur={commit} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />
    {invalid && <small role="alert">Проверьте значение и допустимую длину.</small>}</label>;
}

const STATUS: Record<string, [string, string]> = {
  "length-missing": ["Нет длины", "tone-danger"], "material-missing": ["Нет материала", "tone-warn"], ready: ["Готово", "tone-ok"],
};

function itemStatus(item: HarnessCutItem): [string, string] {
  return STATUS[item.warnings.includes("length-missing") ? "length-missing" : item.warnings.includes("material-missing") ? "material-missing" : "ready"]!;
}

/** Таблица заготовок. `onSelect` — щелчок по строке. */
export function CutItemsTable({ items, activeId, onSelect }: { items: HarnessCutItem[]; activeId?: string; onSelect: (item: HarnessCutItem) => void }) {
  const columns: DataColumn<HarnessCutItem>[] = [
    { key: "title", title: "Цепь / провод", value: row => row.title || "Без цепи", render: row => <span className="he-cut-title"><strong>{row.title || "Без цепи"}</strong><small>{row.material ?? "Материал не закреплён"}</small></span> },
    { key: "length", title: "Исходная длина, мм", numeric: true, value: row => row.length_mm, render: row => mm(row.length_mm) },
    { key: "corrections", title: "Поправки A / Б, мм", value: row => `${mm(row.correction_a_mm)} / ${mm(row.correction_b_mm)}` },
    { key: "cut", title: "Длина резки, мм", numeric: true, value: row => row.cut_mm, render: row => <strong>{mm(row.cut_mm)}</strong> },
    { key: "pieces", title: "Шт.", numeric: true, value: row => row.pieces },
    { key: "total", title: "Метраж, м", numeric: true, value: row => row.total_m, render: row => mm(row.total_m, 6) },
    { key: "status", title: "Статус", filter: true, value: row => itemStatus(row)[0], render: row => <span className={itemStatus(row)[1]}>{itemStatus(row)[0]}</span> },
  ];
  return <DataTable label="Карта резки" rows={items} columns={columns} rowKey={row => row.id} emptyText="В жгуте пока нет проводов."
    onRowClick={onSelect} rowTitle={() => "Открыть лист резки"} rowClassName={row => row.id === activeId ? "open" : ""} compactBelow={8} />;
}

/** «Карта резки» в окне чертежа: заготовки и лист выбранной. */
export function CutListPanel({ document, quantity, readOnly, relatedIds, onReveal, onCommand }: {
  document: HarnessDesignDocument; quantity: number; readOnly: boolean; relatedIds: readonly string[];
  onReveal: (id: string) => void; onCommand: (command: EditorCommand) => boolean;
}) {
  const { data, error } = useHarnessDocument("cut", document, quantity);
  const [selected, setSelected] = useState("");
  const items = data?.items ?? [];
  const sheets = data?.sheets ?? [];
  // Выбранная на поле жила кабеля открывает лист своего кабеля.
  const related = sheets.find(sheet => relatedIds.some(id => sheet.member_wire_ids.includes(id) || sheet.object_id === id))?.object_id;
  const activeId = items.some(item => item.id === selected) ? selected : related ?? items[0]?.id ?? "";
  const item = items.find(entry => entry.id === activeId);
  const sheet = sheets.find(entry => entry.object_id === activeId);
  const local = sheet?.kind === "cable" ? document.cables.find(c => c.id === activeId) : document.wires.find(w => w.id === activeId);
  const commit = (field: CutField, value: string) => {
    if (!sheet) return false;
    const patch = { [field]: value === "" && field === "lengthMm" ? null : Number(value) };
    return onCommand(sheet.kind === "cable" ? { type: "update-cable", cableId: sheet.object_id, ...patch } : { type: "update-wire", wireId: sheet.object_id, ...patch });
  };
  const fields: [CutField, string, number | null | undefined][] = [
    ["lengthMm", "Исходная длина, мм", local?.lengthMm], ["endCorrectionFromMm", "Поправка A, мм", local?.endCorrectionFromMm],
    ["endCorrectionToMm", "Поправка Б, мм", local?.endCorrectionToMm], ["cutRoundingStepMm", "Шаг округления, мм", local?.cutRoundingStepMm],
  ];
  if (!data) return error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загрузка…</div>;
  return <section className="he-cut-embedded" aria-label="Схема резки и разделки">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <CutItemsTable items={items} activeId={activeId} onSelect={entry => setSelected(entry.id)} />
    {item && <div className="he-cut-sheet">
      <header className="he-cut-sheet-head"><strong>{item.title || "Без цепи"} · {item.material ?? "Материал не закреплён"}</strong>
        <button className="secondary small" type="button" onClick={() => onReveal(item.id)}>На чертеже</button></header>
      {sheet ? <>
        {!readOnly && local && <div className="he-cut-fields">{fields.map(([key, label, value]) =>
          <CutInput key={`${sheet.object_id}:${key}`} label={label} value={value ?? null} nullable={key === "lengthMm"} onCommit={v => commit(key, v)} />)}</div>}
        <p className="he-cut-formula">{mm(item.length_mm)} + ({mm(item.correction_a_mm)}) + ({mm(item.correction_b_mm)}) = {mm(item.unrounded_mm)} мм → <strong>{mm(item.cut_mm)} мм</strong> · {item.pieces} шт. · {mm(item.total_m, 6)} м</p>
        {sheet.warnings.length > 0 && <ul className="warning-inline he-cut-warnings">{sheet.warnings.map(w => <li key={w}>{w}</li>)}</ul>}
        <CutSheetSvg sheet={sheet} cutMm={item.cut_mm} />
        {sheet.kind === "wire"
          ? <div className="he-cut-end-tables"><LayerTable label="A" end={sheet.a} /><LayerTable label="Б" end={sheet.b} /></div>
          : sheet.conductors.map((c, index) => <details key={c.id}><summary>Жила {index + 1} · A {c.a.label} → Б {c.b.label}</summary>
            <CutSheetSvg sheet={sheet} cutMm={item.cut_mm} a={c.a} b={c.b} /><div className="he-cut-end-tables"><LayerTable label="A" end={c.a} /><LayerTable label="Б" end={c.b} /></div></details>)}
      </> : <p>Оболочка · {item.title}. Длина — по размерам на чертеже в мм; разделка проводов к оболочке не относится.</p>}
    </div>}
  </section>;
}
