import { useEffect, useState } from "react";
import { formatMinutes } from "./etkRoute";
import { formatPerUnit, parsePositive, routeChain, routeLayout, type BlockTotals, type Route, type RouteBlock, type RouteEdge } from "./routeLogic";

/* Рисунок схемы полуфабрикатов — общий у «Схемы полуфабрикатов» типовых вкладок и «Схемы сборки» ручной (владелец 05.10:
   «визуал слабенький»). Цепочка — сверху вниз строками во всю ширину, между ними «↓» и число штук; ветвистая схема —
   полотном слева направо (блок W×H, между слоями GX, между строками GY), число штук — в последнем промежутке перед целью. */
const W = 208, H = 92, GX = 92, GY = 14;

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

/** Блок: номера его операций, название (до двух строк, целиком — в подсказке), «N оп. · M мин/шт». */
export function BlockBox({ block, title, numbers, totals, selected, onSelect }: {
  block: RouteBlock; title: string; numbers: string; totals?: BlockTotals; selected: boolean; onSelect: () => void;
}) {
  const classes = ["route-block", block.final ? "is-final" : "", selected ? "is-selected" : ""].filter(Boolean).join(" ");
  const time = totals?.unit != null ? ` · ${formatMinutes(totals.unit)} мин/шт${totals.inputs > 0 && totals.rolled != null ? ` · с входами ${formatMinutes(totals.rolled)}` : ""}` : "";
  return <button type="button" className={classes} aria-pressed={selected} title={title} onClick={onSelect}>
    <span className="route-block-side">
      <span className="route-block-no" aria-hidden={!numbers}>{numbers || "—"}</span>
      {block.final && <span className="route-block-tag">итог</span>}
    </span>
    <span className="route-block-body">
      <span className="route-block-name">{title}</span>
      <span className="route-block-meta">
        {block.ops.length ? `${block.ops.length} оп.${time}` : <span className="tone-warn">нет операций</span>}
      </span>
    </span>
  </button>;
}

/** Схема целиком: блоки и стрелки с числом штук. Блок выбирается кликом. */
export function RouteDiagram({ route, title, numbers, totals, selected, onSelect, onPerUnit, edgeLabel }: {
  route: Route;
  title: (block: RouteBlock) => string;
  /** Номера операций блока для значка: «1–3». */
  numbers: (block: RouteBlock) => string;
  totals: Map<string, BlockTotals>;
  selected: string;
  onSelect: (key: string) => void;
  onPerUnit: (edge: RouteEdge, value: number) => void;
  edgeLabel: (edge: RouteEdge) => string;
}) {
  const chain = routeChain(route);
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const box = (item: RouteBlock) => <BlockBox block={item} title={title(item)} numbers={numbers(item)} totals={totals.get(item.key)}
    selected={selected === item.key} onSelect={() => onSelect(item.key)} />;
  const perUnit = (edge: RouteEdge, unit: string) => <PerUnit key={`${edge.from}>${edge.to}`} value={edge.per_unit} label={edgeLabel(edge)} unit={unit}
    onChange={(value) => onPerUnit(edge, value)} />;

  if (chain || route.blocks.length === 1) {
    const keys = chain ?? [route.blocks[0].key];
    return <ol className="route-flow" aria-label="Блоки схемы">
      {keys.map((key, index) => {
        const edge = route.edges.find((candidate) => candidate.to === key);
        return <li key={key}>
          {index > 0 && <span className="route-flow-link"><span className="route-flow-arrow" aria-hidden>↓</span>{edge && perUnit(edge, "шт на 1")}</span>}
          {box(byKey.get(key)!)}
        </li>;
      })}
    </ol>;
  }

  const layout = routeLayout(route);
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
        return <span key={`${edge.from}>${edge.to}`} className="route-edge-label" style={{ left: cx, top: cy }}>{perUnit(edge, "шт")}</span>;
      })}
    </div>
  </div>;
}

/** «1–3», «4», «2, 5» — номера операций блока для значка. */
export function numberRange(numbers: number[]): string {
  const sorted = [...numbers].sort((a, b) => a - b);
  if (!sorted.length) return "";
  if (sorted.length === 1) return String(sorted[0]);
  return sorted.every((value, index) => !index || value === sorted[index - 1] + 1) ? `${sorted[0]}–${sorted[sorted.length - 1]}` : sorted.join(", ");
}
