import { useEffect, useRef, useState } from "react";
import { formatMinutes } from "./etkRoute";
import { formatPerUnit, parsePositive, routeChain, routeLayout, type BlockTotals, type Route, type RouteBlock, type RouteEdge } from "./routeLogic";

/* Рисунок схемы полуфабрикатов — общий у «Схемы полуфабрикатов» типовых вкладок и «Схемы сборки» ручной. Цепочка и
   ветвистая схема рисуются сверху вниз, итог внизу; между блоками «N шт» на стрелке. */

/** Штук полуфабриката на одну штуку следующего блока — число больше нуля; «1» не выделяется. */
export function PerUnit({ value, label, unit, onChange }: { value: number; label: string; unit: string; onChange: (value: number) => void }) {
  const [text, setText] = useState(formatPerUnit(value));
  useEffect(() => { if (parsePositive(text) !== value) setText(formatPerUnit(value)); }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  const bad = text.trim() !== "" && parsePositive(text) === null;
  return <span className={value === 1 && !bad ? "route-per-chip is-one" : "route-per-chip"} title={label}>
    <input className={bad ? "route-per f-empty" : "route-per"} inputMode="decimal" aria-label={label} value={text}
      onChange={(event) => {
        setText(event.target.value);
        const next = parsePositive(event.target.value);
        if (next !== null) onChange(next);
      }}
      onBlur={() => setText(formatPerUnit(value))} />
    <small aria-hidden>{unit}</small>
  </span>;
}

/** Блок: номера его операций, название (до двух строк, целиком — в подсказке), «N оп. · M мин/шт».
    Блок без операций — одна компактная строка: название и «нет операций». */
export function BlockBox({ block, title, numbers, totals, selected, linking, onSelect }: {
  block: RouteBlock; title: string; numbers: string; totals?: BlockTotals; selected: boolean; linking?: boolean; onSelect: () => void;
}) {
  const empty = block.ops.length === 0;
  const classes = ["route-block", block.final ? "is-final" : "", selected ? "is-selected" : "", linking ? "is-linking" : "", empty ? "is-empty" : ""].filter(Boolean).join(" ");
  const time = totals?.unit != null ? ` · ${formatMinutes(totals.unit)} мин/шт${totals.inputs > 0 && totals.rolled != null ? ` · с входами ${formatMinutes(totals.rolled)}` : ""}` : "";
  if (empty) {
    return <button type="button" className={classes} aria-pressed={selected} title={title} onClick={onSelect}>
      <span className="route-block-name">{title}</span>
      <span className="route-block-meta"><span className="tone-warn">нет операций</span></span>
      {block.final && <span className="route-block-tag">итог</span>}
    </button>;
  }
  return <button type="button" className={classes} aria-pressed={selected} title={title} onClick={onSelect}>
    <span className="route-block-side">
      <span className="route-block-no" aria-hidden={!numbers}>{numbers || "—"}</span>
      {block.final && <span className="route-block-tag">итог</span>}
    </span>
    <span className="route-block-body">
      <span className="route-block-name">{title}</span>
      <span className="route-block-meta">{`${block.ops.length} оп.${time}`}</span>
    </span>
  </button>;
}

/** Число штук на стрелке как «N шт»: нажатие открывает маленькое окно с полем числа и «Убрать связь». */
function EdgePill({ edge, label, onEdit, onQuantity, onRemove }: {
  edge: RouteEdge; label: string; onEdit: (edge: RouteEdge) => void; onQuantity?: (edge: RouteEdge, value: number) => void; onRemove?: (edge: RouteEdge) => void;
}) {
  const [open, setOpen] = useState(false);
  const holder = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event: Event) => { if (holder.current && !holder.current.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <span className="route-edge-holder" ref={holder}>
    <button type="button" className={edge.per_unit === 1 ? "route-edge-pill is-one" : "route-edge-pill"} title={label} aria-expanded={open}
      onClick={() => { if (!open) onEdit(edge); setOpen(!open); }}>{formatPerUnit(edge.per_unit)} шт</button>
    {open && <span className="route-edge-pop" role="dialog" aria-label={label}>
      <PerUnit value={edge.per_unit} label={`Штук — ${label}`} unit="шт на 1" onChange={(value) => onQuantity?.(edge, value)} />
      <span className="route-edge-pop-actions">
        {onRemove && <button type="button" className="small btn-link route-edge-remove" onClick={() => { setOpen(false); onRemove(edge); }}>Убрать связь</button>}
        <button type="button" className="small btn-link" onClick={() => setOpen(false)}>Готово</button>
      </span>
    </span>}
  </span>;
}

/* Размеры рисунка: карточка 84 px, пустой блок — строка 34 px, между строками 56 px (в нём «N шт» и ход стрелки). */
const CARD = 84, LINE = 34, GAP = 56;

/** Схема целиком: блоки и стрелки с числом штук. Блок выбирается кликом. Необязательные крючки нужны ручной сборке:
    «+» на блоке, «→» (соединить два блока двумя нажатиями), окно «N шт» на стрелке. Без них рисунок работает как раньше. */
export function RouteDiagram({ route, title, numbers, totals, selected, onSelect, onPerUnit, edgeLabel, onAddInput, onConnect, onEdgeEdit, onEdgeQuantity, onEdgeRemove }: {
  route: Route;
  title: (block: RouteBlock) => string;
  /** Номера операций блока для значка: «1–3». */
  numbers: (block: RouteBlock) => string;
  totals: Map<string, BlockTotals>;
  selected: string;
  onSelect: (key: string) => void;
  onPerUnit: (edge: RouteEdge, value: number) => void;
  edgeLabel: (edge: RouteEdge) => string;
  /** «+» на блоке: добавить ему новый вход. */
  onAddInput?: (blockId: string) => void;
  /** «→» на блоке A, затем нажатие на блок B: полуфабрикат A идёт в B. */
  onConnect?: (from: string, to: string) => void;
  /** Нажатие на «N шт» открывает окно правки стрелки (вызывается при открытии). */
  onEdgeEdit?: (edge: RouteEdge) => void;
  onEdgeQuantity?: (edge: RouteEdge, value: number) => void;
  onEdgeRemove?: (edge: RouteEdge) => void;
}) {
  const [linking, setLinking] = useState<string | null>(null);
  useEffect(() => {
    if (!linking) return undefined;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setLinking(null); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [linking]);
  const chain = routeChain(route);
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const pick = (key: string) => {
    if (linking) {
      if (key !== linking) onConnect?.(linking, key);
      setLinking(null);
      return;
    }
    onSelect(key);
  };
  const node = (item: RouteBlock) => {
    const name = title(item);
    const tools = !!(onAddInput || onConnect);
    return <div className={tools ? "route-node has-tools" : "route-node"}>
      <BlockBox block={item} title={name} numbers={numbers(item)} totals={totals.get(item.key)} selected={selected === item.key}
        linking={linking === item.key} onSelect={() => pick(item.key)} />
      {tools && <span className="route-tools">
        {onAddInput && <button type="button" className="route-tool" aria-label={`Добавить вход в блок «${name}»`} title="Добавить вход" onClick={() => onAddInput(item.key)}>+</button>}
        {onConnect && <button type="button" className={linking === item.key ? "route-tool is-on" : "route-tool"} aria-pressed={linking === item.key}
          aria-label={`Соединить блок «${name}» с другим блоком`} title="Соединить с другим блоком" onClick={() => setLinking(linking === item.key ? null : item.key)}>→</button>}
      </span>}
    </div>;
  };
  const pill = (edge: RouteEdge, unit: string) => (onEdgeEdit
    ? <EdgePill key={`${edge.from}>${edge.to}`} edge={edge} label={edgeLabel(edge)} onEdit={onEdgeEdit} onQuantity={onEdgeQuantity ?? onPerUnit} onRemove={onEdgeRemove} />
    : <PerUnit key={`${edge.from}>${edge.to}`} value={edge.per_unit} label={edgeLabel(edge)} unit={unit} onChange={(value) => onPerUnit(edge, value)} />);
  const hint = linking ? <p className="route-link-hint" role="status">Нажмите блок, куда идёт «{title(byKey.get(linking)!)}». Esc — отмена.</p> : null;

  if (chain || route.blocks.length === 1) {
    const keys = chain ?? [route.blocks[0].key];
    return <>
      {hint}
      <ol className="route-flow" aria-label="Блоки схемы">
        {keys.map((key, index) => {
          const edge = route.edges.find((candidate) => candidate.to === key);
          return <li key={key}>
            {index > 0 && <span className="route-flow-link"><span className="route-flow-arrow" aria-hidden>↓</span>{edge && pill(edge, "шт на 1")}</span>}
            {node(byKey.get(key)!)}
          </li>;
        })}
      </ol>
    </>;
  }

  // Сверху вниз: итог внизу; места по горизонтали — доли ширины, поэтому рисунок всегда во всю доступную ширину без прокрутки.
  const layout = routeLayout(route);
  const compact = (item: RouteBlock) => item.ops.length === 0;
  const rowHeight = Array.from({ length: layout.layers }, () => LINE);
  route.blocks.forEach((item) => { const at = layout.cells.get(item.key)!.layer; if (!compact(item)) rowHeight[at] = CARD; });
  const rowTop: number[] = [];
  rowHeight.reduce((top, height, index) => { rowTop[index] = top; return top + height + GAP; }, 0);
  const height = rowTop[rowTop.length - 1] + rowHeight[rowHeight.length - 1];
  const percent = (x: number) => `${(((x + 0.5) / layout.cols) * 100).toFixed(3)}%`;
  const outgoing = new Map<string, RouteEdge[]>();
  route.edges.forEach((edge) => outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge]));
  return <>
    {hint}
    <div className="route-canvas" role="group" aria-label="Блоки схемы" style={{ height, ["--cols" as string]: layout.cols }}>
      <svg width="100%" height={height} aria-hidden>
        <defs><marker id="route-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse">
          <path className="route-arrowhead" d="M0,0 L8,4 L0,8 z" />
        </marker></defs>
        {route.edges.map((edge) => {
          const from = layout.cells.get(edge.from), to = layout.cells.get(edge.to);
          if (!from || !to) return null;
          const y1 = rowTop[from.layer] + (compact(byKey.get(edge.from)!) ? LINE : rowHeight[from.layer]), y2 = rowTop[to.layer], mid = y2 - GAP / 3;
          const x1 = percent(from.x), x2 = percent(to.x);
          const key = `${edge.from}>${edge.to}`;
          return from.x === to.x
            ? <line key={key} className="route-edge-line" x1={x1} y1={y1} x2={x1} y2={y2} markerEnd="url(#route-arrow)" />
            : <g key={key}>
              <line className="route-edge-line" x1={x1} y1={y1} x2={x1} y2={mid} />
              <line className="route-edge-line" x1={x1} y1={mid} x2={x2} y2={mid} />
              <line className="route-edge-line" x1={x2} y1={mid} x2={x2} y2={y2} markerEnd="url(#route-arrow)" />
            </g>;
        })}
      </svg>
      {route.blocks.map((item) => {
        const cell = layout.cells.get(item.key)!;
        const h = compact(item) ? LINE : CARD;
        return <div key={item.key} className="route-cell" style={{ left: percent(cell.x), top: rowTop[cell.layer] + (rowHeight[cell.layer] - h) / 2, height: h }}>{node(item)}</div>;
      })}
      {route.edges.map((edge) => {
        const from = layout.cells.get(edge.from);
        if (!from || !layout.cells.has(edge.to)) return null;
        const siblings = outgoing.get(edge.from)!, shift = (siblings.indexOf(edge) - (siblings.length - 1) / 2) * 76;
        const y1 = rowTop[from.layer] + (compact(byKey.get(edge.from)!) ? LINE : rowHeight[from.layer]);
        return <span key={`${edge.from}>${edge.to}`} className="route-edge-label" style={{ left: `calc(${percent(from.x)} + ${shift}px)`, top: y1 + 16 }}>{pill(edge, "шт")}</span>;
      })}
    </div>
  </>;
}

/** «1–3», «4», «2, 5» — номера операций блока для значка. */
export function numberRange(numbers: number[]): string {
  const sorted = [...numbers].sort((a, b) => a - b);
  if (!sorted.length) return "";
  if (sorted.length === 1) return String(sorted[0]);
  return sorted.every((value, index) => !index || value === sorted[index - 1] + 1) ? `${sorted[0]}–${sorted[sorted.length - 1]}` : sorted.join(", ");
}
