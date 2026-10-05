import { useMemo, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { formatMinutes } from "./etkRoute";
import {
  addCustomOp,
  addLibraryOp,
  cardCategory,
  cardLabel,
  cardNorms,
  moveOp,
  opMinutes,
  opTitle,
  removeOp,
  setLink,
  toggleComponent,
  updateOp,
  type LibraryCard,
  type ManualOp,
  type ManualState,
  type OpLink,
} from "./manualLogic";
import type { Comp, OpRec } from "./tcGenLogic";
import { Field, UnitInput } from "./GenControls";

type Change = (next: (current: ManualState) => ManualState) => void;

function normText(card: LibraryCard, opRecs: OpRec[]): string {
  const norms = cardNorms(card, opRecs);
  if (!norms) return "";
  return [norms.op ? `${formatMinutes(norms.op)} с/шт` : "", norms.prep ? `подг. ${formatMinutes(norms.prep / 60)} мин` : ""].filter(Boolean).join(" · ");
}

/** Выбор карточки из библиотеки операций: категории чипами, поиск по названию и коду; строка открывается — что делает
    карточка и «Добавить». Несколько сразу — отметить и «Добавить выбранные». */
function LibraryPicker({ library, opRecs, added, onAdd, onClose }: {
  library: LibraryCard[]; opRecs: OpRec[]; added: string; onAdd: (cards: LibraryCard[]) => void; onClose: () => void;
}) {
  const categories = useMemo(() => [...new Set(library.map(cardCategory))].sort((a, b) => (a === "Прочее" ? 1 : b === "Прочее" ? -1 : a.localeCompare(b, "ru"))), [library]);
  const columns: DataColumn<LibraryCard>[] = [
    { key: "name", title: "Операция", value: (card) => `${cardLabel(card)} ${card.code}`,
      render: (card) => <span className="manual-card-name"><b>{cardLabel(card)}</b>{card.code && <small> {card.code}</small>}</span> },
    { key: "category", title: "Категория", value: cardCategory, width: 150 },
    { key: "norm", title: "Норма БД.ОП", value: (card) => normText(card, opRecs), width: 190 },
  ];
  return <div className="manual-picker" role="region" aria-label="Библиотека операций">
    <div className="section-hdr"><strong>Операция из библиотеки</strong>
      {added && <span className="ok-text" role="status">{added}</span>}
      <button type="button" className="btn-link manual-close" onClick={onClose}>Закрыть</button></div>
    {library.length ? <DataTable rows={library} columns={columns} rowKey={(card) => card.id} label="Карточки операций"
      presets={categories.map((category) => ({ label: category, test: (card: LibraryCard) => cardCategory(card) === category }))}
      searchLabel="Поиск карточки" searchPlaceholder="Название или код карточки" emptyText="Нет таких карточек — смените категорию или поиск." pageSize={40}
      bulkActions={[{ label: "Добавить выбранные", run: (rows) => onAdd(rows) }]}
      renderDetail={(card) => <div className="manual-card-detail">
        {card.first_step && <span className="muted">Первый шаг: {card.first_step}</span>}
        <button type="button" className="primary small" onClick={() => onAdd([card])}>Добавить «{cardLabel(card)}»</button>
      </div>} />
      : <div className="empty">Библиотека операций не загружена — добавьте свою операцию.</div>}
  </div>;
}

/** Связь с предыдущей: подпись, подсказка; «тот же процесс» — один блок схемы, «одновременно» — своя ветка. */
const LINKS: Array<[OpLink, string, string]> = [
  ["after", "Следом", "Своим процессом после предыдущей операции"],
  ["same", "Тот же процесс", "В одном процессе с предыдущей: один блок схемы, один полуфабрикат"],
  ["parallel", "Одновременно", "В то же время, что и предыдущая, своей веткой; следующая операция соберёт обе"],
];
const LINK_NOTE: Record<OpLink, string> = { after: "", same: "тот же процесс, что у предыдущей", parallel: "одновременно с предыдущей" };

function normLabel(minutes: number) {
  return minutes > 0 ? <b>{formatMinutes(minutes)} мин</b> : <b className="tone-warn">не задана</b>;
}

/** Операция в открытом блоке схемы — строкой: номер, название, код карточки, сколько на изделие и норма. Правится в списке. */
export function ManualOpSummary({ op, position, partQty }: { op: ManualOp; position: number; partQty: number }) {
  return <div className="route-op-row manual-op-row">
    <span className="op-badge">{position}</span>
    <span className="op-title">{opTitle(op, position)}</span>
    <small className="muted">{op.kind === "library" ? op.code : "своя операция"}{position > 1 && op.link !== "after" ? ` · ${LINK_NOTE[op.link]}` : ""} · на изделие {op.qty.trim() || "1"} · норма на партию {normLabel(opMinutes(op, partQty))}</small>
  </div>;
}

/** Операция ручной сборки: название, норма, параметры, результат; у своей — ход операции, контроль, оснастка;
    комплектующие из состава КБ. */
export function ManualOpCard({ op, position, count, partQty, components, onChange }: {
  op: ManualOp; position: number; count: number; partQty: number; components: Comp[]; onChange: Change;
}) {
  const set = (patch: Partial<ManualOp>) => onChange((current) => updateOp(current, op.seq, patch));
  const title = opTitle(op, position);
  const minutes = opMinutes(op, partQty);
  const picked = new Map(op.components.map((item) => [item.id, item]));
  // Подпись короткая, единица — в поле; полное имя поля — в aria-label («Подготовка, мин: Резка»).
  const field = (key: "qty" | "prepMin" | "opSec", label: string, unit: string, name: string, placeholder: string) =>
    <Field label={label}><UnitInput unit={unit} inputMode="decimal" aria-label={`${name}: ${title}`} value={op[key]} placeholder={placeholder}
      onChange={(event) => set({ [key]: event.target.value })} /></Field>;
  const link = position > 1 ? op.link : "after";
  return <div className={link === "after" ? "manual-op" : `manual-op link-${link}`} aria-label={`Операция ${position}: ${title}`}>
    <div className="manual-op-head">
      <span className="op-badge">{position}</span>
      <div className="manual-op-name">
        <input aria-label={`Название операции ${position}`} value={op.label} placeholder={op.kind === "custom" ? "Название своей операции" : ""}
          className={op.kind === "custom" && !op.label.trim() ? "f-empty" : undefined} onChange={(event) => set({ label: event.target.value })} />
        <small>{op.kind === "library" ? <>{op.code && <span className="op-code">{op.code}</span>} из библиотеки</> : "своя операция"}</small>
      </div>
      <div className="manual-op-tools">
        {position > 1 && <div className="manual-link" role="group" aria-label={`Связь с предыдущей: ${title}`}>
          {LINKS.map(([value, label, hint]) => <button key={value} type="button" title={hint} aria-pressed={link === value} className={link === value ? "on" : ""}
            onClick={() => onChange((current) => setLink(current, op.seq, value))}>{label}</button>)}
        </div>}
        <button type="button" className="secondary small" aria-label={`Выше: ${title}`} title="Выше" disabled={position === 1} onClick={() => onChange((current) => moveOp(current, op.seq, -1))}>↑</button>
        <button type="button" className="secondary small" aria-label={`Ниже: ${title}`} title="Ниже" disabled={position === count} onClick={() => onChange((current) => moveOp(current, op.seq, 1))}>↓</button>
        <button type="button" className="btn-remove" aria-label={`Удалить операцию ${position}: ${title}`} title="Удалить" onClick={() => onChange((current) => removeOp(current, op.seq))}>×</button>
      </div>
    </div>
    <div className="manual-grid">
      {field("qty", "На изделие", "шт", "На изделие", "1")}
      {field("prepMin", "Подготовка", "мин", "Подготовка, мин", "0")}
      {field("opSec", "Операция", "с/шт", "Операция, сек/шт", "0")}
      <div className="manual-norm" aria-label={`Норма на партию: ${title}`}><small>На партию {partQty} шт.</small>{normLabel(minutes)}</div>
      <Field label="Параметры" className="wide"><input aria-label={`Параметры: ${title}`} value={op.params} placeholder="напр. L = 300 мм, зачистка 5 мм" onChange={(event) => set({ params: event.target.value })} /></Field>
      <Field label="Результат — полуфабрикат" className="result"><input aria-label={`Результат: ${title}`} value={op.result} placeholder={`Полуфабрикат после «${title}»`} onChange={(event) => set({ result: event.target.value })} /></Field>
      <Field label="Примечание" className="wide"><input aria-label={`Примечание: ${title}`} value={op.note} onChange={(event) => set({ note: event.target.value })} /></Field>
    </div>
    {op.kind === "custom" && <div className="manual-text">
      <label className="gfield"><span className="gfield-label">Ход операции<small>шаг на строку</small></span><textarea aria-label={`Ход операции: ${title}`} rows={4} value={op.steps} onChange={(event) => set({ steps: event.target.value })} /></label>
      <label className="gfield"><span className="gfield-label">Контроль<small>«что проверить | допуск» на строку</small></span><textarea aria-label={`Контроль: ${title}`} rows={4} value={op.controls} placeholder="Пайка без наплывов | IPC-A-620, разд. 4" onChange={(event) => set({ controls: event.target.value })} /></label>
      <label className="gfield"><span className="gfield-label">Оснастка<small>строкой; «Оборудование: …», «Расходники: …»</small></span><textarea aria-label={`Оснастка: ${title}`} rows={4} value={op.tooling} placeholder={"Пинцет\nОборудование: паяльная станция\nРасходники: припой ПОС-61"} onChange={(event) => set({ tooling: event.target.value })} /></label>
    </div>}
    {components.length > 0 && <details className="manual-comps" open={op.components.length > 0}>
      <summary>Комплектующие из КБ{op.components.length ? ` · ${op.components.length}` : ""}</summary>
      <div className="manual-comp-list">{components.map((comp) => {
        const item = picked.get(comp.id);
        return <span className={item ? "manual-comp on" : "manual-comp"} key={comp.id}>
          <label><input type="checkbox" checked={!!item} onChange={() => onChange((current) => toggleComponent(current, op.seq, comp))} /> {comp.art || comp.name}</label>
          {item && <input aria-label={`Количество ${comp.art || comp.name} на изделие: ${title}`} inputMode="decimal" value={item.qty}
            onChange={(event) => set({ components: op.components.map((other) => (other.id === comp.id ? { ...other, qty: event.target.value } : other)) })} />}
          {item && <small>{item.unit}</small>}
        </span>;
      })}</div>
    </details>}
  </div>;
}

/**
 * «Ручная сборка» — кастомные и нетиповые провода, где типовые генераторы не подходят: технолог набирает операции
 * сам — «+ Операция из библиотеки» (любая карточка по категории, названию, коду) и «+ Своя операция».
 */
export function ManualAssembly({ state, onChange, library, opRecs, components, partQty }: {
  state: ManualState; onChange: Change; library: LibraryCard[]; opRecs: OpRec[]; components: Comp[]; partQty: number;
}) {
  const [picking, setPicking] = useState(false);
  const [added, setAdded] = useState("");
  const total = state.ops.reduce((sum, op) => sum + opMinutes(op, partQty), 0);
  return <section className="gsec full manual-assembly" aria-label="Операции — ручная сборка">
    <div className="gsec-head manual-hdr">
      <h3>Операции — ручная сборка</h3>
      {state.ops.length > 0 && <span className="gsec-meta">{state.ops.length} оп. · {formatMinutes(total)} мин на партию {partQty} шт.</span>}
      <span className="manual-add">
        <button type="button" className="secondary small" aria-expanded={picking} onClick={() => { setPicking(!picking); setAdded(""); }}>+ Операция из библиотеки</button>
        <button type="button" className="secondary small" onClick={() => onChange((current) => addCustomOp(current))}>+ Своя операция</button>
      </span>
    </div>
    {picking && <LibraryPicker library={library} opRecs={opRecs} added={added} onClose={() => { setPicking(false); setAdded(""); }} onAdd={(cards) => {
      onChange((current) => cards.reduce((next, card) => addLibraryOp(next, card, opRecs), current));
      setAdded(cards.length === 1 ? `Добавлена операция ${state.ops.length + 1}: «${cardLabel(cards[0])}»` : `Добавлено операций: ${cards.length}`);
    }} />}
    <div className="manual-ops">
      {!state.ops.length && <div className="gempty">Операций нет — добавьте карточку из библиотеки или свою операцию.</div>}
      {state.ops.map((op, index) => <ManualOpCard key={op.seq} op={op} position={index + 1} count={state.ops.length} partQty={partQty} components={components} onChange={onChange} />)}
    </div>
  </section>;
}
