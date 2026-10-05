import { useEffect, useMemo, useRef, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { formatMinutes, pluralBlocks } from "./etkRoute";
import { RouteDiagram, numberRange } from "./RouteDiagram";
import { blockTitle, routeTotals, type RouteBlock, type RouteEdge } from "./routeLogic";
import {
  addBlock,
  addCustomOp,
  addInput,
  addInputBlock,
  addLibraryOp,
  blockOrder,
  cardCategory,
  cardLabel,
  cardNorms,
  inputCandidates,
  manualRoute,
  moveOp,
  nameBlockAfter,
  opMinutes,
  opTitle,
  orderedOps,
  removeBlock,
  removeInput,
  removeOp,
  renameBlock,
  selectBlock,
  setPerUnit,
  toggleComponent,
  updateOp,
  type EdgeEdit,
  type LibraryCard,
  type ManualOp,
  type ManualState,
} from "./manualLogic";
import type { Comp, OpRec } from "./tcGenLogic";
import { Field, UnitInput } from "./GenControls";

type Change = (next: (current: ManualState) => ManualState) => void;

function normText(card: LibraryCard, opRecs: OpRec[]): string {
  const norms = cardNorms(card, opRecs);
  if (!norms) return "";
  return [norms.op ? `${formatMinutes(norms.op)} с/шт` : "", norms.prep ? `подг. ${formatMinutes(norms.prep / 60)} мин` : ""].filter(Boolean).join(" · ");
}

/** Выбор карточки из библиотеки операций: категории чипами, поиск по названию и коду; клик по строке — карточка сразу
    в блок. Несколько сразу — отметить и «Добавить выбранные». */
function LibraryPicker({ library, opRecs, target, added, onAdd, onClose }: {
  library: LibraryCard[]; opRecs: OpRec[]; target: string; added: string; onAdd: (cards: LibraryCard[]) => void; onClose: () => void;
}) {
  const categories = useMemo(() => [...new Set(library.map(cardCategory))].sort((a, b) => (a === "Прочее" ? 1 : b === "Прочее" ? -1 : a.localeCompare(b, "ru"))), [library]);
  const columns: DataColumn<LibraryCard>[] = [
    { key: "name", title: "Операция", value: (card) => `${cardLabel(card)} ${card.code}`,
      render: (card) => <span className="manual-card-name"><b>{cardLabel(card)}</b>{card.code && <small>{card.code}</small>}</span> },
    { key: "category", title: "Категория", value: cardCategory, width: 100 },
    { key: "norm", title: "Норма БД.ОП", value: (card) => normText(card, opRecs), render: (card) => normText(card, opRecs) || "—", width: 120 },
    { key: "step", title: "Первый шаг", value: (card) => card.first_step || "", width: "28%",
      render: (card) => <span className="manual-card-step" title={card.first_step || undefined}>{card.first_step}</span> },
  ];
  return <div className="manual-picker" role="region" aria-label="Библиотека операций">
    <div className="section-hdr"><strong>Операция из библиотеки → в блок «{target}»</strong>
      {added && <span className="ok-text" role="status">{added}</span>}
      <button type="button" className="btn-link manual-close" onClick={onClose}>Закрыть</button></div>
    {library.length ? <DataTable rows={library} columns={columns} rowKey={(card) => card.id} label="Карточки операций"
      presets={categories.map((category) => ({ label: category, test: (card: LibraryCard) => cardCategory(card) === category }))}
      searchLabel="Поиск карточки" searchPlaceholder="Название или код карточки" emptyText="Нет таких карточек — смените категорию или поиск." pageSize={40}
      bulkActions={[{ label: "Добавить выбранные", run: (rows) => onAdd(rows) }]}
      onRowClick={(card) => onAdd([card])} rowLabel={cardLabel} rowTitle={(card) => `Добавить «${cardLabel(card)}» в блок «${target}»${card.first_step ? `\nПервый шаг: ${card.first_step}` : ""}`} />
      : <div className="empty">Библиотека операций не загружена — добавьте свою операцию.</div>}
  </div>;
}

function normLabel(minutes: number) {
  return minutes > 0 ? <b>{formatMinutes(minutes)} мин</b> : <b className="tone-warn">не задана</b>;
}

/** Операция ручной сборки: название, норма, параметры, результат; у своей — ход операции, контроль, оснастка;
    комплектующие из состава КБ. ↑/↓ — внутри блока. */
export function ManualOpCard({ op, position, first, last, partQty, components, onChange, open: openProp, onToggle }: {
  op: ManualOp; position: number; first: boolean; last: boolean; partQty: number; components: Comp[]; onChange: Change;
  open?: boolean; onToggle?: () => void;
}) {
  const [openOwn, setOpenOwn] = useState(false);
  const open = openProp ?? openOwn;
  const toggle = onToggle ?? (() => setOpenOwn(!openOwn));
  const set = (patch: Partial<ManualOp>) => onChange((current) => updateOp(current, op.seq, patch));
  const title = opTitle(op, position);
  const minutes = opMinutes(op, partQty);
  const picked = new Map(op.components.map((item) => [item.id, item]));
  // Подпись короткая, единица — в поле; полное имя поля — в aria-label («Подготовка, мин: Резка»).
  const field = (key: "qty" | "prepMin" | "opSec", label: string, unit: string, name: string, placeholder: string) =>
    <Field label={label}><UnitInput unit={unit} inputMode="decimal" aria-label={`${name}: ${title}`} value={op[key]} placeholder={placeholder}
      onChange={(event) => set({ [key]: event.target.value })} /></Field>;
  return <div className={open ? "manual-op is-open" : "manual-op"} aria-label={`Операция ${position}: ${title}`}>
    <div className="manual-op-head">
      <button type="button" className="manual-op-fold" aria-expanded={open} aria-label={`${open ? "Свернуть" : "Развернуть"} операцию ${position}: ${title}`}
        title={open ? "Свернуть" : "Открыть: норма, параметры, результат"} onClick={toggle}><span className="op-badge">{position}</span></button>
      <div className="manual-op-name">
        <input aria-label={`Название операции ${position}`} value={op.label} placeholder={op.kind === "custom" ? "Название своей операции" : ""}
          className={op.kind === "custom" && !op.label.trim() ? "f-empty" : undefined} onChange={(event) => set({ label: event.target.value })} />
        <small>{op.kind === "library" ? <>из библиотеки{op.code && <> · <span className="op-code">{op.code}</span></>}</> : "своя операция"}</small>
      </div>
      <div className="manual-op-tools">
        {!open && <button type="button" className="manual-op-norm" aria-label={`Норма: ${title}`} title="Открыть" onClick={toggle}>{normLabel(minutes)}</button>}
        <button type="button" className="secondary small" aria-label={`Выше: ${title}`} title="Выше в блоке" disabled={first} onClick={() => onChange((current) => moveOp(current, op.seq, -1))}>↑</button>
        <button type="button" className="secondary small" aria-label={`Ниже: ${title}`} title="Ниже в блоке" disabled={last} onClick={() => onChange((current) => moveOp(current, op.seq, 1))}>↓</button>
        <button type="button" className="btn-remove" aria-label={`Удалить операцию ${position}: ${title}`} title="Удалить" onClick={() => onChange((current) => removeOp(current, op.seq))}>×</button>
      </div>
    </div>
    {open && <>
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
    </>}
  </div>;
}

/**
 * «Ручная сборка» — схема первой (владелец 05.10): технолог рисует блоки (итог и полуфабрикаты, которые в него входят),
 * выбирает блок и кладёт в него операции — «+ Операция из библиотеки» (клик по строке) и «+ Своя операция».
 * Номера операций идут по схеме: входы раньше блоков, которые их берут.
 */
export function ManualAssembly({ state, onChange, library, opRecs, components, partQty, finalName, error }: {
  state: ManualState; onChange: Change; library: LibraryCard[]; opRecs: OpRec[]; components: Comp[]; partQty: number;
  finalName: string; error?: string;
}) {
  const [picking, setPicking] = useState(false);
  const [added, setAdded] = useState("");
  const [problem, setProblem] = useState("");
  const [nameOnPick, setNameOnPick] = useState(false);
  const [opened, setOpened] = useState<Set<number>>(new Set());
  const panel = useRef<HTMLDivElement>(null);
  const seen = useRef(state.selected);
  const { route, ops: routeOps } = useMemo(() => manualRoute(state, partQty), [state, partQty]);
  const totals = useMemo(() => routeTotals(route, routeOps, partQty), [route, routeOps, partQty]);
  const ordered = useMemo(() => orderedOps(state), [state]);
  const title = (block: RouteBlock) => blockTitle(block, routeOps, finalName);
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const block = byKey.get(state.selected) ?? route.blocks.find((item) => item.final)!;
  const own = state.blocks.find((item) => item.key === block.key)!;
  const bySeq = new Map(state.ops.map((op) => [op.seq, op]));
  const position = new Map(ordered.map((op, index) => [op.seq, index + 1]));
  const total = ordered.reduce((sum, op) => sum + opMinutes(op, partQty), 0);
  const blockName = title(block);

  const select = (key: string) => { onChange((current) => selectBlock(current, key)); setProblem(""); setAdded(""); setNameOnPick(false); };
  // Выбрали блок (на схеме или в «Итоге») — панель блока подъезжает в видимую часть.
  useEffect(() => {
    if (seen.current === block.key) return;
    seen.current = block.key;
    panel.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [block.key]);
  const toggleOp = (seq: number) => setOpened((current) => { const next = new Set(current); if (!next.delete(seq)) next.add(seq); return next; });
  const addInputTo = (to: string) => {
    onChange((current) => addInputBlock(current, to));
    setProblem(""); setAdded(""); setPicking(true); setNameOnPick(true);
  };
  const connect = (from: string, to: string) => {
    if (state.edges.some((edge) => edge.from === from && edge.to === to)) return;
    edgeEdit((current) => addInput(current, from, to, finalName));
  };
  const edgeEdit = (edit: (current: ManualState) => EdgeEdit) => {
    const result = edit(state);
    setProblem(result.problem ? `Так нельзя: ${result.problem}` : "");
    if (!result.problem) onChange(() => result.state);
    return !result.problem;
  };
  const edgeLabel = (edge: RouteEdge) => {
    const from = byKey.get(edge.from), to = byKey.get(edge.to);
    return `Штук из «${from ? title(from) : edge.from}» на один «${to ? title(to) : edge.to}»`;
  };
  const deleteBlock = () => {
    const count = own.ops.length;
    if (count && !window.confirm(`Удалить блок «${blockName}» и его операции (${count})?`)) return;
    onChange((current) => removeBlock(current, block.key));
    setProblem(""); setAdded("");
  };
  const inputs = state.edges.filter((edge) => edge.to === block.key);
  const candidates = inputCandidates(state, block.key);
  const blockOps = own.ops.map((seq) => bySeq.get(seq)).filter((op): op is ManualOp => !!op);

  return <section className="gsec full manual-assembly" aria-label="Схема сборки">
    <div className="gsec-head manual-hdr">
      <h3>Схема сборки</h3>
      <span className="gsec-meta">{pluralBlocks(route.blocks.length)} · {ordered.length} оп.{ordered.length > 0 && <> · {formatMinutes(total)} мин на партию {partQty} шт.</>}</span>
      <span className="manual-add">
        <button type="button" className="secondary small" title={`Новый полуфабрикат — вход блока «${blockName}»`}
          onClick={() => { onChange((current) => addBlock(current)); setProblem(""); setAdded(""); setNameOnPick(false); }}>+ Блок</button>
      </span>
    </div>
    <RouteDiagram route={route} title={title} numbers={(item) => numberRange(item.ops)} totals={totals} selected={block.key}
      onSelect={select} edgeLabel={edgeLabel} onPerUnit={(edge, value) => onChange((current) => setPerUnit(current, edge.from, edge.to, value))}
      onAddInput={addInputTo} onConnect={connect} onEdgeEdit={() => setProblem("")}
      onEdgeQuantity={(edge, value) => onChange((current) => setPerUnit(current, edge.from, edge.to, value))}
      onEdgeRemove={(edge) => edgeEdit((current) => removeInput(current, edge.from, edge.to, finalName))} />
    {error && <div className="error-banner" role="alert">{error}</div>}
    {problem && <div className="warning-inline" role="alert">{problem}</div>}

    <div className="manual-block" ref={panel} role="region" aria-label={`Блок «${blockName}»`}>
      <div className="manual-block-head">
        <Field label={block.final ? "Название блока · итог — изделие" : "Название блока"} className="manual-block-name">
          <input aria-label="Название блока" value={own.name} placeholder={title({ ...block, name: "" })}
            onChange={(event) => onChange((current) => renameBlock(current, block.key, event.target.value))} /></Field>
        <div className="manual-inputs">
          <span className="gfield-label">Входы</span>
          <div className="manual-input-list">
            {inputs.map((edge) => {
              const from = byKey.get(edge.from);
              const name = from ? title(from) : edge.from;
              return <span className="manual-input" key={edge.from}>
                <button type="button" className="btn-link" onClick={() => select(edge.from)}>{name}</button>
                <button type="button" className="btn-remove" title="Убрать вход" aria-label={`Убрать вход «${name}»`}
                  onClick={() => edgeEdit((current) => removeInput(current, edge.from, block.key, finalName))}>×</button>
              </span>;
            })}
            {!inputs.length && <span className="muted">нет — блок начинает сборку</span>}
            {candidates.length > 0 && <span className="manual-input-add">
              <select aria-label="Добавить вход из блока" value="" onChange={(event) => { if (event.target.value) edgeEdit((current) => addInput(current, event.target.value, block.key, finalName)); }}>
                <option value="">+ вход из блока…</option>
                {candidates.map((other) => <option key={other.key} value={other.key}>{title(byKey.get(other.key)!)}</option>)}
              </select>
            </span>}
          </div>
        </div>
        {!block.final && <button type="button" className="secondary small manual-block-delete" onClick={deleteBlock}>Удалить блок</button>}
      </div>

      <div className="manual-ops-head">
        <strong>Операции блока «{blockName}»{blockOps.length ? ` · ${blockOps.length}` : ""}</strong>
        <span className="manual-add">
          <button type="button" className="secondary small" aria-expanded={picking} onClick={() => { setPicking(!picking); setAdded(""); setNameOnPick(false); }}>+ Операция из библиотеки</button>
          <button type="button" className="secondary small" onClick={() => { const seq = state.nextSeq; setOpened((current) => new Set(current).add(seq)); onChange((current) => addCustomOp(current, block.key)); }}>+ Своя операция</button>
        </span>
      </div>
      {picking && <LibraryPicker library={library} opRecs={opRecs} target={blockName} added={added} onClose={() => { setPicking(false); setAdded(""); }} onAdd={(cards) => {
        onChange((current) => {
          const next = cards.reduce((acc, card) => addLibraryOp(acc, card, opRecs, block.key), current);
          return nameOnPick ? nameBlockAfter(next, block.key, cardLabel(cards[0])) : next;
        });
        setAdded(`Добавлено в «${blockName}»: ${cards.length === 1 ? `«${cardLabel(cards[0])}»` : `${cards.length} оп.`}`);
      }} />}
      <div className="manual-ops">
        {!blockOps.length && <div className="gempty">{ordered.length
          ? `В блоке «${blockName}» нет операций — добавьте операцию из библиотеки или свою.`
          : "Выберите блок и добавьте операцию из библиотеки или свою."}</div>}
        {blockOps.map((op, index) => <ManualOpCard key={op.seq} op={op} position={position.get(op.seq) ?? index + 1} first={index === 0} last={index === blockOps.length - 1}
          partQty={partQty} components={components} onChange={onChange}
          open={opened.has(op.seq)} onToggle={() => toggleOp(op.seq)} />)}
      </div>
    </div>
  </section>;
}

/** Итог ручной сборки (правая панель): операции по блокам в порядке номеров, норма на партию, всего. */
export function ManualSummary({ state, partQty, finalName, onSelect }: { state: ManualState; partQty: number; finalName: string; onSelect?: (key: string) => void }) {
  const { route, ops } = manualRoute(state, partQty);
  const ordered = orderedOps(state);
  const position = new Map(ordered.map((op, index) => [op.seq, index + 1]));
  const bySeq = new Map(state.ops.map((op) => [op.seq, op]));
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const total = ordered.reduce((sum, op) => sum + opMinutes(op, partQty), 0);
  if (!ordered.length) return <div className="gempty">Добавьте блок и операции в него — здесь появится порядок операций.</div>;
  return <div className="manual-summary" role="group" aria-label="Порядок операций">
    {blockOrder(state).map((key) => {
      const block = state.blocks.find((item) => item.key === key)!;
      const shown = byKey.get(key)!;
      return <div className="manual-summary-block" key={key}>
        <div className={state.selected === key ? "manual-summary-name is-selected" : "manual-summary-name"}>
          {onSelect ? <button type="button" className="manual-summary-pick" aria-pressed={state.selected === key} title="Показать блок на схеме" onClick={() => onSelect(key)}>{blockTitle(shown, ops, finalName)}</button>
            : blockTitle(shown, ops, finalName)}{block.final && <span className="route-block-tag">итог</span>}</div>
        {block.ops.length ? <ol>{block.ops.map((seq) => {
          const op = bySeq.get(seq);
          if (!op) return null;
          const number = position.get(seq)!;
          return <li key={seq}><span className="op-badge">{number}</span><span className="manual-summary-op">{opTitle(op, number)}</span>
            <span className="manual-summary-norm">{normLabel(opMinutes(op, partQty))}</span></li>;
        })}</ol> : <div className="tone-warn manual-summary-empty">нет операций</div>}
      </div>;
    })}
    <div className="manual-summary-total">Всего на партию {partQty} шт.: <b>{formatMinutes(total)} мин</b></div>
  </div>;
}
