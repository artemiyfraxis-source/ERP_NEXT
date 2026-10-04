import { useEffect, useMemo, useState, type ReactNode } from "react";
import { formatMinutes, pluralBlocks } from "./etkRoute";
import {
  addEdge,
  blockTitle,
  deriveRoute,
  formatPerUnit,
  inputCandidates,
  mergeBlocks,
  moveOp,
  neighbours,
  parsePositive,
  removeEdge,
  renameBlock,
  routeChain,
  routeLayout,
  routeProblem,
  routeTotals,
  setPerUnit,
  splitBlock,
  type BlockTotals,
  type Route,
  type RouteBlock,
  type RouteEdge,
  type RouteOp,
} from "./routeLogic";

/* Размеры схемы: блок W×H, между слоями GX, между строками GY. Стрелка идёт горизонтально из блока, а в последнем
   промежутке перед целью изгибается; число штук на стрелке стоит по центру этого промежутка. */
const W = 184, H = 92, GX = 66, GY = 14;

/** Штук полуфабриката на одну штуку следующего блока — число больше нуля, правится на стрелке. */
function PerUnit({ value, label, onChange }: { value: number; label: string; onChange: (value: number) => void }) {
  const [text, setText] = useState(formatPerUnit(value));
  useEffect(() => { if (parsePositive(text) !== value) setText(formatPerUnit(value)); }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  const bad = text.trim() !== "" && parsePositive(text) === null;
  return <input className={bad ? "route-per f-empty" : "route-per"} inputMode="decimal" aria-label={label} title={label} value={text}
    onChange={(event) => {
      setText(event.target.value);
      const next = parsePositive(event.target.value);
      if (next !== null) onChange(next);
    }}
    onBlur={() => setText(formatPerUnit(value))} />;
}

function BlockBox({ block, title, totals, selected, onSelect }: {
  block: RouteBlock; title: string; totals?: BlockTotals; selected: boolean; onSelect: () => void;
}) {
  const classes = ["route-block", block.final ? "is-final" : "", selected ? "is-selected" : ""].filter(Boolean).join(" ");
  return <button type="button" className={classes} aria-pressed={selected} onClick={onSelect}>
    {block.final && <span className="route-block-tag">итог</span>}
    <span className="route-block-name">{title}</span>
    <span className="route-block-meta">{block.ops.length} оп. · {totals && totals.inputs > 0 ? `входов ${formatPerUnit(totals.inputs)}` : "без входов"}</span>
    {totals?.unit != null && <span className="route-block-time">{formatMinutes(totals.unit)} мин/шт{totals.inputs > 0 && totals.rolled != null ? ` · с входами ${formatMinutes(totals.rolled)}` : ""}</span>}
  </button>;
}

/**
 * Схема полуфабрикатов сборки (ARCH-TRACK-1): блоки-квадратики слева направо и стрелки между ними, число штук на
 * стрелке правится, блок открывается — его операции настраиваются как в генераторе. Без правок схема собирается
 * автоматически (`value` = null); любая правка делает её ручной, «Сбросить к автоматической» возвращает null.
 */
export function RouteEditor({ ops, finalName = "", quantity = 1, value, onChange, error, notice, skipped, renderOp }: {
  /** Операции, которые попадут в сборку: номер, название, норма на партию (null — ЭТК ещё не собиралась). */
  ops: RouteOp[];
  /** Наименование изделия — название итогового блока. */
  finalName?: string;
  /** Партия, на которую заданы нормы ops. */
  quantity?: number;
  /** Ручная схема; null — автоматическая. */
  value: Route | null;
  onChange: (route: Route | null) => void;
  /** Ответ сервера «Схема полуфабрикатов: …» (422). */
  error?: string;
  notice?: string;
  /** Номера операций формы без шаблона — в сборку и в схему они не входят. */
  skipped?: number[];
  /** Операция так, как её настраивает генератор (шаблон и времена) — показывается в открытом блоке. */
  renderOp?: (position: number) => ReactNode;
}) {
  const [selected, setSelected] = useState("");
  const [shown, setShown] = useState<boolean | null>(null);
  const [problem, setProblem] = useState("");
  const [mergeWith, setMergeWith] = useState("");
  const [splitAfter, setSplitAfter] = useState("");
  const [addFrom, setAddFrom] = useState("");

  const route = useMemo(() => value ?? deriveRoute(ops), [value, ops]);
  const totals = useMemo(() => routeTotals(route, ops, quantity), [route, ops, quantity]);
  const chain = useMemo(() => routeChain(route), [route]);
  const layout = useMemo(() => routeLayout(route), [route]);
  const title = (block: RouteBlock) => blockTitle(block, ops, finalName);
  const numberOf = (position: number) => ops.find((op) => op.position === position)?.number ?? position;
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const finalBlock = route.blocks.find((block) => block.final);
  const total = finalBlock ? totals.get(finalBlock.key) : undefined;
  const open = shown ?? (!chain || value !== null || !!error);
  const block = byKey.get(selected);

  function apply(next: Route): boolean {
    const wrong = routeProblem(next, ops, finalName);
    setProblem(wrong ? `Так нельзя: ${wrong}` : "");
    if (wrong) return false;
    onChange(next);
    return true;
  }
  const edit = (next: Route) => { apply(next); };
  const select = (key: string) => { setSelected(selected === key ? "" : key); setProblem(""); setMergeWith(""); setSplitAfter(""); setAddFrom(""); };
  const edgeLabel = (edge: RouteEdge) => {
    const from = byKey.get(edge.from), to = byKey.get(edge.to);
    return `Штук из «${from ? title(from) : edge.from}» на один «${to ? title(to) : edge.to}»`;
  };
  const perUnit = (edge: RouteEdge) => <PerUnit key={`${edge.from}>${edge.to}`} value={edge.per_unit} label={edgeLabel(edge)}
    onChange={(next) => edit(setPerUnit(route, edge.from, edge.to, next))} />;
  const box = (item: RouteBlock) => <BlockBox block={item} title={title(item)} totals={totals.get(item.key)} selected={selected === item.key} onSelect={() => select(item.key)} />;

  function diagram() {
    if (chain) {
      return <ol className="route-chain" aria-label="Блоки схемы">
        {chain.map((key, index) => {
          const item = byKey.get(key)!;
          const edge = route.edges.find((candidate) => candidate.to === key);
          return <li key={key}>
            {index > 0 && <span className="route-link"><span aria-hidden>→</span>{edge && perUnit(edge)}</span>}
            {box(item)}
          </li>;
        })}
      </ol>;
    }
    const x = (layer: number) => layer * (W + GX);
    const y = (row: number) => row * (H + GY);
    const width = layout.layers * (W + GX) - GX, height = layout.rows * (H + GY) - GY;
    return <div className="route-scroll">
      <div className="route-canvas" role="group" aria-label="Блоки схемы" style={{ width, height }}>
        <svg width={width} height={height} aria-hidden>
          <defs><marker id="route-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse">
            <path className="route-arrowhead" d="M0,0 L8,4 L0,8 z" />
          </marker></defs>
          {route.edges.map((edge) => {
            const from = layout.cells.get(edge.from), to = layout.cells.get(edge.to);
            if (!from || !to) return null;
            const x1 = x(from.layer) + W, y1 = y(from.row) + H / 2, x2 = x(to.layer), y2 = y(to.row) + H / 2, xs = x2 - GX;
            return <path key={`${edge.from}>${edge.to}`} className="route-edge-line" markerEnd="url(#route-arrow)"
              d={`M${x1},${y1} H${Math.max(x1, xs)} C${Math.max(x1, xs) + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`} />;
          })}
        </svg>
        {route.blocks.map((item) => {
          const cell = layout.cells.get(item.key)!;
          return <div key={item.key} className="route-cell" style={{ left: x(cell.layer), top: y(cell.row), width: W, height: H }}>{box(item)}</div>;
        })}
        {route.edges.map((edge) => {
          const from = layout.cells.get(edge.from), to = layout.cells.get(edge.to);
          if (!from || !to) return null;
          const cx = x(to.layer) - GX / 2, cy = (y(from.row) + y(to.row)) / 2 + H / 2;
          return <span key={`${edge.from}>${edge.to}`} className="route-edge-label" style={{ left: cx, top: cy }}>{perUnit(edge)}</span>;
        })}
      </div>
    </div>;
  }

  function panel(item: RouteBlock) {
    const { before, after } = neighbours(route, item.key);
    const adjacent = [...before, ...after];
    const candidates = inputCandidates(route, item.key);
    const others = route.blocks.filter((other) => other.key !== item.key);
    const merge = () => {
      const target = byKey.get(mergeWith);
      if (!target) return;
      const upstream = before.includes(target) ? target.key : item.key;
      const downstream = upstream === item.key ? target.key : item.key;
      if (apply(mergeBlocks(route, upstream, downstream))) { setSelected(downstream); setMergeWith(""); }
    };
    return <div className="gcard route-panel" role="region" aria-label={`Блок «${title(item)}»`}>
      <div className="route-panel-head">
        <label className="field-label">Название блока
          <input value={item.name} placeholder={title({ ...item, name: "" })} onChange={(event) => edit(renameBlock(route, item.key, event.target.value))} /></label>
        <button type="button" className="btn-link" onClick={() => select(item.key)}>Закрыть блок</button>
      </div>
      <div className="route-ops">
        {item.ops.map((position) => <div className="route-op" key={position}>
          {renderOp ? renderOp(position) : <div className="route-op-row"><span className="op-badge">{numberOf(position)}</span><span className="op-title">{ops.find((op) => op.position === position)?.label}</span></div>}
          {item.ops.length > 1 && <label className="field-label route-move">Перенести операцию {numberOf(position)} в блок
            <select aria-label={`Перенести операцию ${numberOf(position)} в блок`} value="" onChange={(event) => { if (event.target.value) edit(moveOp(route, position, event.target.value)); }}>
              <option value="">— выберите —</option>
              {others.map((other) => <option key={other.key} value={other.key}>{title(other)}</option>)}
            </select></label>}
        </div>)}
      </div>
      <div className="route-actions">
        {before.length > 0 && <div className="route-inputs"><span className="field-label">Входы блока</span>
          {route.edges.filter((edge) => edge.to === item.key).map((edge) => <div className="route-input-row" key={edge.from}>
            <span>{title(byKey.get(edge.from)!)}</span>
            <button type="button" className="btn-remove" title="Убрать вход" aria-label={`Убрать вход «${title(byKey.get(edge.from)!)}»`} onClick={() => edit(removeEdge(route, edge.from, edge.to))}>×</button>
          </div>)}</div>}
        {candidates.length > 0 && <div className="route-act"><label className="field-label">Добавить вход из блока
          <select aria-label="Добавить вход из блока" value={addFrom} onChange={(event) => setAddFrom(event.target.value)}>
            <option value="">— выберите —</option>{candidates.map((other) => <option key={other.key} value={other.key}>{title(other)}</option>)}</select></label>
          <button type="button" className="secondary small" disabled={!addFrom} onClick={() => { if (apply(addEdge(route, addFrom, item.key))) setAddFrom(""); }}>Добавить вход</button></div>}
        {adjacent.length > 0 && <div className="route-act"><label className="field-label">Объединить с блоком
          <select aria-label="Объединить с блоком" value={mergeWith} onChange={(event) => setMergeWith(event.target.value)}>
            <option value="">— выберите —</option>{adjacent.map((other) => <option key={other.key} value={other.key}>{title(other)}</option>)}</select></label>
          <button type="button" className="secondary small" disabled={!mergeWith} onClick={merge}>Объединить</button></div>}
        {item.ops.length > 1 && <div className="route-act"><label className="field-label">Разделить после операции
          <select aria-label="Разделить после операции" value={splitAfter} onChange={(event) => setSplitAfter(event.target.value)}>
            <option value="">— выберите —</option>{item.ops.slice(0, -1).map((position) => <option key={position} value={position}>{numberOf(position)}. {ops.find((op) => op.position === position)?.label}</option>)}</select></label>
          <button type="button" className="secondary small" disabled={!splitAfter} onClick={() => { if (apply(splitBlock(route, item.key, Number(splitAfter)))) setSplitAfter(""); }}>Разделить</button></div>}
      </div>
    </div>;
  }

  if (!ops.length) return null;
  const manual = value !== null;
  return <section className="route-editor" aria-label="Схема полуфабрикатов">
    <div className="route-head">
      <h3>Схема полуфабрикатов</h3>
      <span className={manual ? "route-badge manual" : "route-badge"}>{manual ? "правлена вручную" : "автоматическая"}</span>
      {manual && <button type="button" className="btn-link" onClick={() => { setProblem(""); onChange(null); }}>Сбросить к автоматической</button>}
      <button type="button" className="btn-link route-toggle" aria-expanded={open} onClick={() => setShown(!open)}>{open ? "Свернуть схему" : "Показать схему"}</button>
    </div>
    <div className="route-summary">
      {pluralBlocks(route.blocks.length)} · {ops.length} оп.
      {total?.rolled != null && <> · на изделие <strong>{formatMinutes(total.rolled)} мин</strong> (время из последней сборки ЭТК)</>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="warning-inline">{notice}</div>}
    {open && <>
      {diagram()}
      {!!skipped?.length && <div className="route-summary">Без шаблона, в схему не входят: {skipped.join(", ")}</div>}
      {problem && <div className="warning-inline" role="alert">{problem}</div>}
      {block && panel(block)}
    </>}
  </section>;
}
