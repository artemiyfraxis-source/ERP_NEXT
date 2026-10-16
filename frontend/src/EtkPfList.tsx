import { useState, type DragEvent } from "react";
import { techcardsApi, failureText, type ProjectTechcard } from "./api";
import { wireColorKnown } from "./connectorArt";
import { etkCutEnds, etkFigure, type CutEndKind } from "./etkDraw";
import { etkPfGroups, etkRouteOf, formatMinutes, opNumber, type EtkPfGroup, type EtkPfOperation, type EtkPfRow } from "./etkRoute";
import { formatQty } from "./format";
import type { WireBlankEnd } from "./harness/WireBlankCatalog";
import { proposePf } from "./pfPropose";
import { coreColor } from "./pinoutCheck";
import { CableArt } from "./RouteCanvas";
import { coresWord, type WireThumb } from "./routeCards";
import { mergeBlocks, placeOp, routePayload, routeProblem, type Route, type RouteOp } from "./routeLogic";
import { setCustomColors } from "./wireColors";
import "./EtkPfList.css";

/* Список операций ЭТК по ПФ (владелец + Василий 13.10): ЭТК режется на полуфабрикаты, ПФ — блок схемы `document.route`.
   У каждого ПФ — вход (комплектующие и полуфабрикаты других ПФ), выход, норма и эскизы операций. Технолог переносит
   операции между ПФ и внутри ПФ (перетаскиванием или кнопками), выделяет новый ПФ, объединяет ПФ и сохраняет — выданная
   ЭТК получает новую ревизию. Порядок операций внутри ПФ — порядок работы; номера операций ЭТК не меняются.
   «Предложить разбивку на ПФ» кладёт в редактор разбивку по правилу генератора (сервер, pfPropose.ts) — без сохранения. */

export type EtkPfListOperation = EtkPfOperation & { position?: number; type?: string; code?: string };

/** Сколько комплектующих показать в ПФ до «ещё N». */
const SHOWN_PARTS = 4;

function editable(document: ProjectTechcard["document"]): Route | null {
  const route = etkRouteOf(document);
  if (!route) return null;
  return {
    blocks: route.blocks.map(({ key, name, ops, final, components, kit }) => ({
      key, name, ops, final, components, ...(kit ? { kit } : {}),
    })),
    edges: route.edges.map((edge) => ({ ...edge })),
  };
}

function rowQty(row: EtkPfRow): string {
  return row.norm ? formatQty(row.norm, row.unit) || row.norm : "";
}

function rowName(row: EtkPfRow): string {
  return row.art || row.name || "—";
}

type Row = Record<string, unknown>;
const rowsOf = (value: unknown): Row[] => (Array.isArray(value) ? value.filter((item): item is Row => !!item && typeof item === "object") : []);
const END_ART: Record<CutEndKind, WireBlankEnd> = { none: "cut", strip: "copper", tin: "tin", crimp: "terminal" };

/** Кабель в оболочке (жил > 1) в операции резки — рисунком схемы ПФ генератора (RouteCanvas.CableArt): оболочка
 *  посередине, жилы веером в своих цветах, концы — как отдаёт резка. Жилы и их цвета — у провода в конфигурации сборки
 *  (`build_config.wires`: `cores`, `coreColors`), поэтому выданные ЭТК перевыпускать не нужно. Одиночные провода — null
 *  (эскиз резки как был). */
export function etkCutCable(operation: Row, config: Row): WireThumb | null {
  if (!/^cutWire/.test(String(operation.type ?? ""))) return null;
  const fields = (operation.fields && typeof operation.fields === "object" ? operation.fields : {}) as Row;
  const own = rowsOf(config.wires);
  const same = (a: Row, b: Row) => String(a.art ?? "") === String(b.art ?? "") && String(a.name ?? "") === String(b.name ?? "");
  for (const wire of rowsOf(fields.wires)) {
    const source = own.find((item) => same(item, wire)) ?? {};
    const cores = Math.round(Number(wire.cores ?? source.cores) || 0);
    if (cores <= 1) continue;
    setCustomColors(config.colorHex as Record<string, string> | undefined);
    const names = (Array.isArray(wire.coreColors) ? wire.coreColors : Array.isArray(source.coreColors) ? source.coreColors : []) as unknown[];
    const label = String(wire.art || wire.name || "кабель");
    const base = wireColorKnown(wire.name || wire.art) ?? "#6b7c86";
    const colors = Array.from({ length: cores }, (_, k) => coreColor(String(names[k] ?? ""))?.hex ?? base);
    const ends = etkCutEnds(operation);
    return {
      key: label, label, title: `${label} · ${cores} ${coresWord(cores)}`,
      color: base, left: END_ART[ends.a.kind], right: END_ART[ends.b.kind],
      cable: { cores: colors.map((color, k) => ({ color, label: String(names[k] ?? "") })), sub: "" },
    };
  }
  return null;
}

function samePositions(a: number[], b: number[]): boolean {
  return a.length === b.length && [...a].sort((x, y) => x - y).join(",") === [...b].sort((x, y) => x - y).join(",");
}

/** Тело сохранения: блок, чей состав поменялся, теряет старое название — сервер назовёт его по результату последней
 *  операции (название по продукту иначе осталось бы от прежнего состава). */
export function pfPayload(route: Route, original: Route | null): Record<string, unknown> {
  const before = new Map((original?.blocks ?? []).map((block) => [block.key, block.ops]));
  return routePayload({
    ...route,
    blocks: route.blocks.map((block) => (before.has(block.key) && samePositions(before.get(block.key)!, block.ops) ? block : { ...block, name: "" })),
  }, true);
}

export function EtkPfList<T extends EtkPfListOperation>({ card, list, selected, onPick, canEdit, onSaved, title, sub, sketch }: {
  card: ProjectTechcard;
  list: T[];
  /** Индекс открытой операции в `list`. */
  selected: number;
  onPick: (index: number) => void;
  canEdit: boolean;
  onSaved: (updated: ProjectTechcard) => void;
  /** Русское название операции и вторая строка (код карты, разъём, «правлена»). */
  title: (operation: T) => string;
  sub: (operation: T) => string;
  /** Своя картинка эскиза технолога (адрес) или пусто. */
  sketch: (operation: T) => string;
}) {
  const [draft, setDraft] = useState<Route | null>(null);
  // Схема, от которой пошла правка: сохранённая или предложенная («Предложить разбивку на ПФ») — названия ПФ берутся
  // из неё, пока состав ПФ тот же.
  const [base, setBase] = useState<Route | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const saved = editable(card.document);
  const route = draft ?? saved;
  if (!route) return null;
  const kit = (card.document?.kit && typeof card.document.kit === "object" ? card.document.kit : {}) as Record<string, unknown>;
  const config = (card.document?.build_config && typeof card.document.build_config === "object" ? card.document.build_config : {}) as Row;
  const cableOf = (operation: T) => etkCutCable(operation as Row, config);
  const batch = Number(String(kit.part_quantity ?? "").replace(",", ".")) || 1;
  const groups = etkPfGroups(route, list, batch);
  const editing = draft !== null;
  const routeOps: RouteOp[] = list.map((operation, index) => ({
    position: index + 1, key: String(index + 1), type: String(operation.type ?? ""), code: String(operation.code ?? ""),
    label: title(operation), minutes: null,
  }));
  const problem = editing ? routeProblem(route, routeOps, String(kit.name ?? "")) : null;
  const numberOf = (position: number) => opNumber(list[position - 1]?.position || position);

  function edit(next: Route) { setDraft(next); setError(""); setInfo(""); }

  function stop() { setDraft(null); setBase(null); setError(""); setInfo(""); }

  async function propose() {
    if (busy) return;
    setBusy(true); setError(""); setInfo("");
    try {
      const proposal = await proposePf(card.id);
      if (proposal.same || !proposal.route.blocks.length) {
        setInfo("Разбивка уже по ПФ");
      } else {
        setDraft(proposal.route);
        setBase(proposal.route);
        setInfo("Предложена разбивка по этапам генератора — проверьте и сохраните");
      }
    } catch (reason) {
      setError(failureText(reason, "Разбивка на ПФ не предложена"));
    } finally { setBusy(false); }
  }

  function drop(event: DragEvent, to: string | null, at: number) {
    event.preventDefault();
    event.stopPropagation();
    const position = dragging ?? Number(event.dataTransfer.getData("text/plain"));
    setDragging(null);
    if (!(position > 0) || !draft) return;
    // Бросили на операцию своего же ПФ ниже себя — встаёт перед ней (место считается без перенесённой).
    const own = draft.blocks.find((block) => block.ops.includes(position));
    const place = own && own.key === to && own.ops.indexOf(position) < at ? at - 1 : at;
    edit(placeOp(draft, position, to, place));
  }

  async function save() {
    if (!draft || busy || problem) return;
    setBusy(true); setError("");
    try {
      const updated = await techcardsApi.saveRoute(card.id, card.version, pfPayload(draft, base ?? saved));
      stop();
      onSaved(updated);
    } catch (reason) {
      setError(failureText(reason, "ПФ не сохранены"));
    } finally { setBusy(false); }
  }

  const allowDrop = (event: DragEvent) => { if (editing) event.preventDefault(); };

  return <nav className={`etk-pf${editing ? " editing" : ""}`} aria-label="Маршрут ЭТК">
    <div className="etk-pf-bar">
      <strong>{groups.length} ПФ</strong>
      {canEdit && !editing && <button type="button" className="secondary small" onClick={() => edit(route)}>Править ПФ</button>}
      {editing && <small>перетащите операцию в другой ПФ или на её место</small>}
    </div>
    {editing && <div className="etk-pf-propose">
      <button type="button" className="secondary small" disabled={busy} onClick={() => void propose()}>Предложить разбивку на ПФ</button>
      {info && <span role="status">{info}</span>}
    </div>}
    {groups.map((group) => <PfGroup key={group.key} group={group} list={list} selected={selected} onPick={onPick}
      editing={editing} groups={groups} title={title} sub={sub} sketch={sketch} cableOf={cableOf} numberOf={numberOf}
      mergeTarget={editing ? mergeTarget(route, group.key) : null}
      onMerge={(target) => edit(mergeBlocks(route, group.key, target, true))}
      onPlace={(position, to, at) => edit(placeOp(route, position, to, at))}
      onDragStart={(position) => setDragging(position)} onDragEnd={() => setDragging(null)}
      onDrop={drop} allowDrop={allowDrop} dragging={dragging} />)}
    {editing && <>
      <div className={`etk-pf-new${dragging ? " armed" : ""}`} onDragOver={allowDrop} onDrop={(event) => drop(event, null, 0)}>
        Перетащите сюда операцию — новый ПФ
      </div>
      {problem && <div className="warning-inline" role="status">Схема полуфабрикатов: {problem}</div>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="etk-pf-actions">
        <button type="button" className="primary small" disabled={busy || Boolean(problem)} onClick={() => void save()}>
          {card.status === "Выдана" ? `Сохранить ПФ · ревизия ${card.revision + 1}` : "Сохранить ПФ"}
        </button>
        <button type="button" className="secondary small" disabled={busy} onClick={stop}>Отмена</button>
      </div>
    </>}
  </nav>;
}

/** С кем объединить ПФ: с тем единственным ПФ, который берёт его полуфабрикат. */
function mergeTarget(route: Route, key: string): string | null {
  const out = route.edges.filter((edge) => edge.from === key);
  return out.length === 1 ? out[0].to : null;
}

function PfGroup<T extends EtkPfListOperation>({ group, list, selected, onPick, editing, groups, title, sub, sketch, cableOf, numberOf, mergeTarget: target, onMerge, onPlace,
  onDragStart, onDragEnd, onDrop, allowDrop, dragging }: {
  group: EtkPfGroup; list: T[]; selected: number; onPick: (index: number) => void; editing: boolean; groups: EtkPfGroup[];
  title: (operation: T) => string; sub: (operation: T) => string;
  sketch: (operation: T) => string; cableOf: (operation: T) => WireThumb | null; numberOf: (position: number) => string;
  mergeTarget: string | null; onMerge: (target: string) => void;
  onPlace: (position: number, to: string | null, at: number) => void;
  onDragStart: (position: number) => void; onDragEnd: () => void;
  onDrop: (event: DragEvent, to: string | null, at: number) => void; allowDrop: (event: DragEvent) => void; dragging: number | null;
}) {
  const first = list[group.ops[0] - 1];
  const label = `ПФ-${group.number}`;
  const parts = group.components.slice(0, SHOWN_PARTS);
  const more = group.components.length - parts.length;
  const output = group.output[0];
  type Thumb = { position: number; figure: string; image: string; cable: WireThumb | null };
  const thumbs = group.ops.map((position): Thumb | null => {
    const operation = list[position - 1];
    if (!operation) return null;
    const image = sketch(operation), cable = image ? null : cableOf(operation);
    return { position, figure: image || cable ? "" : etkFigure(operation as Record<string, unknown>), image, cable };
  }).filter((item): item is Thumb => !!item && Boolean(item.figure || item.image || item.cable));
  const into = target ? groups.find((item) => item.key === target) : undefined;
  return <section className={`etk-pf-group${group.ops.includes(selected + 1) ? " on" : ""}`} aria-label={`${label} · ${first ? title(first) : ""}`}
    onDragOver={allowDrop} onDrop={(event) => onDrop(event, group.key, group.ops.length)}>
    <header className="etk-pf-head">
      <span className="etk-pf-name"><b>{label}</b>{first ? ` · ${title(first)}` : ""}</span>
      {group.final && <em className="etk-pf-final">изделие</em>}
      {group.minutes > 0 && <span className="etk-pf-norm" title="Норма ПФ на партию">{formatMinutes(group.minutes)} мин</span>}
    </header>
    {(group.inputs.length > 0 || parts.length > 0) && <ul className="etk-pf-in" aria-label={`Вход ${label}`}>
      {group.inputs.map((input) => <li key={`pf:${input.number}`} className="etk-pf-semi">
        <span title={input.name || undefined}>← ПФ-{input.number}{input.name ? ` ${input.name}` : ""}</span><span className="num">{input.times}</span>
      </li>)}
      {parts.map((row, index) => <li key={`${rowName(row)}:${index}`}><span title={row.name && row.art ? row.name : undefined}>{rowName(row)}</span><span className="num">{rowQty(row)}</span></li>)}
      {more > 0 && <li className="muted"><span>ещё {more}</span></li>}
    </ul>}
    <ol className="etk-pf-ops">{group.ops.map((position, at) => {
      const index = position - 1;
      const operation = list[index];
      if (!operation) return null;
      const name = title(operation);
      const second = sub(operation);
      return <li key={position} draggable={editing} className={dragging === position ? "drag" : undefined}
        onDragStart={(event) => { event.dataTransfer.setData("text/plain", String(position)); event.dataTransfer.effectAllowed = "move"; onDragStart(position); }}
        onDragEnd={onDragEnd} onDragOver={allowDrop} onDrop={(event) => onDrop(event, group.key, at)}>
        <button type="button" className={index === selected ? "on" : ""} aria-current={index === selected ? "step" : undefined} onClick={() => onPick(index)}>
          <span className="etk-no">{numberOf(position)}</span>
          <span>{name}{second && <small>{second}</small>}</span>
        </button>
        {editing && <span className="etk-pf-move">
          <button type="button" className="secondary small" aria-label={`Выше в ПФ: ${numberOf(position)} ${name}`} title="Выше в ПФ" disabled={at === 0}
            onClick={() => onPlace(position, group.key, at - 1)}>↑</button>
          <button type="button" className="secondary small" aria-label={`Ниже в ПФ: ${numberOf(position)} ${name}`} title="Ниже в ПФ" disabled={at === group.ops.length - 1}
            onClick={() => onPlace(position, group.key, at + 1)}>↓</button>
          <select aria-label={`Перенести операцию ${numberOf(position)} в ПФ`} value="" onChange={(event) => {
            const value = event.target.value;
            if (!value) return;
            const to = value === "new" ? null : value;
            onPlace(position, to, to ? (groups.find((item) => item.key === to)?.ops.length ?? 0) : 0);
          }}>
            <option value="">в ПФ…</option>
            {groups.filter((item) => item.key !== group.key).map((item) => <option key={item.key} value={item.key}>ПФ-{item.number}</option>)}
            {group.ops.length > 1 && <option value="new">Новый ПФ</option>}
          </select>
        </span>}
      </li>;
    })}</ol>
    {output && <div className="etk-pf-out" aria-label={`Выход ${label}`}>
      <span title={group.output.map((row) => row.name).filter(Boolean).join("; ") || undefined}>→ {output.name || "—"}{group.output.length > 1 ? ` и ещё ${group.output.length - 1}` : ""}</span><span className="num">{rowQty(output)}</span>
    </div>}
    {thumbs.length > 0 && <div className="etk-pf-thumbs">{thumbs.map((thumb) =>
      <button type="button" key={thumb.position} className="etk-pf-thumb" aria-label={`Эскиз операции ${numberOf(thumb.position)}`}
        title={thumb.cable?.title} onClick={() => onPick(thumb.position - 1)}>
        {thumb.image ? <img src={thumb.image} alt="" />
          : thumb.cable ? <span aria-hidden="true"><CableArt wire={thumb.cable} /></span>
          : <span dangerouslySetInnerHTML={{ __html: thumb.figure }} />}
      </button>)}</div>}
    {editing && into && <button type="button" className="secondary small etk-pf-merge" onClick={() => onMerge(into.key)}>Объединить с ПФ-{into.number}</button>}
  </section>;
}
