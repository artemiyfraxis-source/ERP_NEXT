import { useMemo, useState, type ReactNode } from "react";
import { formatMinutes, pluralBlocks } from "./etkRoute";
import { RouteDiagram, numberRange } from "./RouteDiagram";
import {
  addEdge,
  blockTitle,
  deriveRoute,
  inputCandidates,
  mergeBlocks,
  moveOp,
  neighbours,
  removeEdge,
  renameBlock,
  routeChain,
  routeProblem,
  routeTotals,
  setPerUnit,
  splitBlock,
  type Route,
  type RouteBlock,
  type RouteEdge,
  type RouteOp,
} from "./routeLogic";

/**
 * Схема полуфабрикатов сборки (ARCH-TRACK-1): блоки-квадратики слева направо и стрелки между ними, число штук на
 * стрелке правится, блок открывается — его операции настраиваются как в генераторе. Без правок схема собирается
 * автоматически (`value` = null); любая правка делает её ручной, «Сбросить к автоматической» возвращает null.
 */
export function RouteEditor({ ops, finalName = "", quantity = 1, value, onChange, error, notice, skipped, renderOp, derive = deriveRoute, defaultOpen = false, timeSource = "время из последней сборки ЭТК" }: {
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
  /** Схема без правок (`value` = null). По умолчанию — по правилам ЭТК; ручной сборке — цепочка по списку. */
  derive?: (ops: RouteOp[]) => Route;
  /** Показать схему сразу, даже цепочкой. */
  defaultOpen?: boolean;
  /** Откуда время в итоге «на изделие». */
  timeSource?: string;
}) {
  const [selected, setSelected] = useState("");
  const [shown, setShown] = useState<boolean | null>(null);
  const [problem, setProblem] = useState("");
  const [mergeWith, setMergeWith] = useState("");
  const [splitAfter, setSplitAfter] = useState("");
  const [addFrom, setAddFrom] = useState("");

  const route = useMemo(() => value ?? derive(ops), [value, ops, derive]);
  const totals = useMemo(() => routeTotals(route, ops, quantity), [route, ops, quantity]);
  const title = (block: RouteBlock) => blockTitle(block, ops, finalName);
  const numberOf = (position: number) => ops.find((op) => op.position === position)?.number ?? position;
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const finalBlock = route.blocks.find((block) => block.final);
  const total = finalBlock ? totals.get(finalBlock.key) : undefined;
  const open = shown ?? (defaultOpen || !routeChain(route) || value !== null || !!error);
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
  const diagram = () => <RouteDiagram route={route} title={title} numbers={(item) => numberRange(item.ops.map(numberOf))} totals={totals}
    selected={selected} onSelect={select} edgeLabel={edgeLabel} onPerUnit={(edge, next) => edit(setPerUnit(route, edge.from, edge.to, next))} />;

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
      {total?.rolled != null && <> · на изделие <strong>{formatMinutes(total.rolled)} мин</strong> ({timeSource})</>}
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
