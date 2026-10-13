import { Component, Suspense, useEffect, useState, type ReactNode } from "react";
import { harnessApi, productionApi } from "./api";
import { wireColor } from "./connectorArt";
import type { EtkTopologyData } from "./etkDraw";
import { HarnessEditorLazy, loadHarnessDrawing, type HarnessDrawingPorts, type HarnessEditorCatalogApi } from "./harness";
import "./etkHarness.css";

/* Схема жгута в задании монтажника — вид «Схема Э4», как в редакторе чертежа Василия (владелец 13.10: «должно быть
   как у Василия в Э4»). Есть сохранённый чертёж КБ — его Э4 только для просмотра (редактор грузится отдельным чанком
   по щелчку на блоке; вид как на телефоне: поле, масштаб и «Схема Э4 / Чертёж», без панелей правки). Чертежа нет
   (или его не прочитать) — плоская схема Э4 по данным ЭТК: разъёмы таблицами контактов, провода линиями своего цвета
   между контактами; без объёмных картинок разъёмов. */

type End = { end?: unknown; conn?: unknown; label?: unknown; pin?: unknown };
type Connection = { wire?: unknown; from?: End; to?: End };

export type WireMapRow = { pin: string; wire: string; color: string; count?: number };
export type WireMapConnector = { id: string; title: string; sub: string; rows: WireMapRow[] };
export type WireMapLink = { from: [string, number]; to: [string, number]; color: string; wire: string };
export type WireMapBundle = { from: string; to: string; label: string; colors: string[] };
export interface WireMapModel { columns: WireMapConnector[][]; links: WireMapLink[]; bundles: WireMapBundle[] }

const text = (value: unknown) => (value == null ? "" : String(value).trim());

function asConnections(value: unknown): Connection[] {
  return Array.isArray(value) ? value.filter((item): item is Connection => Boolean(item) && typeof item === "object") : [];
}

/** Схема по таблице соединений: слева концы «откуда», справа «куда»; строка — контакт, линия — провод. */
function fromConnections(list: Connection[]): WireMapModel | null {
  const usable = list.filter((item) => text(item.from?.end) && text(item.to?.end));
  if (!usable.length) return null;
  const connectors = new Map<string, WireMapConnector>();
  const sides = new Map<string, number>();
  const connector = (end: End, side: number) => {
    const id = text(end.end);
    if (!connectors.has(id)) {
      connectors.set(id, { id, title: [id, text(end.label)].filter(Boolean).join(" "), sub: text(end.conn), rows: [] });
      sides.set(id, side);
    }
    return connectors.get(id)!;
  };
  const rowOf = (end: End, side: number, wire: string) => {
    const target = connector(end, side);
    const pin = text(end.pin);
    let index = target.rows.findIndex((row) => row.pin === pin);
    if (index < 0) { target.rows.push({ pin, wire, color: wireColor(wire) }); index = target.rows.length - 1; }
    return index;
  };
  const raw = usable.map((item) => {
    const wire = text(item.wire);
    return { wire, from: item.from!, to: item.to!, a: rowOf(item.from!, 0, wire), b: rowOf(item.to!, 1, wire) };
  });
  // Контакты по возрастанию номера; ссылки линий — на новые места строк.
  const order = new Map<string, number[]>();
  connectors.forEach((item) => {
    const sorted = item.rows.map((row, index) => ({ row, index }))
      .sort((left, right) => (Number(left.row.pin) || 0) - (Number(right.row.pin) || 0));
    const place: number[] = [];
    sorted.forEach((entry, position) => { place[entry.index] = position; });
    order.set(item.id, place);
    item.rows = sorted.map((entry) => entry.row);
  });
  const links = raw.map((item) => ({
    from: [text(item.from.end), order.get(text(item.from.end))![item.a]] as [string, number],
    to: [text(item.to.end), order.get(text(item.to.end))![item.b]] as [string, number],
    color: wireColor(item.wire), wire: item.wire,
  }));
  const all = [...connectors.values()];
  return { columns: [all.filter((item) => sides.get(item.id) === 0), all.filter((item) => sides.get(item.id) === 1)], links, bundles: [] };
}

/** Схема по топологии ЭТК (распиновки нет): разъём — таблица проводов по цветам, между разъёмами — пучок с числом
 *  проводов и длиной. Контакты по номерам — когда операция монтажа их задала. */
function fromTopology(value: EtkTopologyData | null, pins: Record<string, Record<number, string>>): WireMapModel | null {
  const nodes = value?.nodes ?? [];
  if (nodes.length < 2) return null;
  const edges = value?.edges ?? [];
  const byDepth = new Map<number, WireMapConnector[]>();
  nodes.forEach((node) => {
    const own = pins[`${node.conn}::${node.side || ""}`] ?? {};
    const numbered = Object.entries(own).map(([pin, wire]) => ({ pin, wire, color: wireColor(wire) }))
      .sort((left, right) => Number(left.pin) - Number(right.pin));
    const wires = new Map<string, number>();
    edges.filter((edge) => edge.from === node.id || edge.to === node.id)
      .forEach((edge) => (edge.wires ?? []).forEach((wire) => wires.set(wire, (wires.get(wire) ?? 0) + 1)));
    const rows = numbered.length ? numbered
      : [...wires].map(([wire, count]) => ({ pin: "", wire, color: wireColor(wire), count }));
    const housing = node.housing;
    const side = node.side ? `сторона ${node.side === "B" ? "В" : "А"}` : "";
    const sub = [housing?.title, housing?.n ? `${housing.n} конт.` : "", node.kind === "splice" ? "сросток" : "", side].filter(Boolean).join(" · ");
    const depth = node.depth || 0;
    byDepth.set(depth, [...(byDepth.get(depth) ?? []), { id: node.id, title: text(node.conn) || node.id, sub, rows }]);
  });
  const columns = [...byDepth.keys()].sort((left, right) => left - right).map((depth) => byDepth.get(depth)!);
  const bundles = edges.map((edge) => ({
    from: edge.from, to: edge.to,
    label: `${edge.count || (edge.wires ?? []).length} пров.${edge.length ? ` · ${edge.length} мм` : ""}`,
    colors: [...new Set((edge.wires ?? []).map((wire) => wireColor(wire)))].slice(0, 8),
  }));
  return { columns, links: [], bundles };
}

export function wireMapModel(connections: unknown, topology: EtkTopologyData | null, pins: Record<string, Record<number, string>> = {}): WireMapModel | null {
  return fromConnections(asConnections(connections)) ?? fromTopology(topology, pins);
}

const BOX = 280, GAP = 170, PAD = 8, HEAD = 40, ROW = 22, SPACE = 18, PIN = 34;
const cut = (value: string, size: number) => (value.length > size ? `${value.slice(0, size - 1)}…` : value);
const LIGHT = /#f4f4f4|#f2c200/i;

/** Плоская схема Э4: разъёмы таблицами «контакт · провод», провода — линиями своего цвета. */
export function EtkWireMap({ model }: { model: WireMapModel }) {
  const place = new Map<string, { x: number; y: number; column: number; last: boolean }>();
  const heights = model.columns.map((column) => column.reduce((sum, item) => sum + HEAD + Math.max(1, item.rows.length) * ROW + SPACE, -SPACE));
  const height = Math.max(...heights) + PAD * 2;
  model.columns.forEach((column, index) => {
    let y = PAD + (height - PAD * 2 - heights[index]) / 2;
    column.forEach((item) => {
      place.set(item.id, { x: PAD + index * (BOX + GAP), y, column: index, last: index === model.columns.length - 1 && index > 0 });
      y += HEAD + Math.max(1, item.rows.length) * ROW + SPACE;
    });
  });
  const width = PAD * 2 + model.columns.length * BOX + (model.columns.length - 1) * GAP;
  const anchor = (id: string, row: number, toward: "left" | "right") => {
    const at = place.get(id);
    if (!at) return null;
    return { x: toward === "right" ? at.x + BOX : at.x, y: row < 0 ? at.y + HEAD / 2 : at.y + HEAD + row * ROW + ROW / 2 };
  };
  const curve = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const middle = (a.x + b.x) / 2;
    return `M${a.x},${a.y} C${middle},${a.y} ${middle},${b.y} ${b.x},${b.y}`;
  };
  return <svg className="etk-wiremap" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
    aria-label={`Схема Э4: ${model.columns.flat().map((item) => item.title).join(", ")}`}>
    {model.bundles.map((bundle, index) => {
      const a = anchor(bundle.from, -1, "right"), b = anchor(bundle.to, -1, "left");
      if (!a || !b) return null;
      const label = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      return <g key={`b${index}`}>
        <path d={curve(a, b)} className="etk-wm-bundle" strokeWidth={bundle.colors.length * 3 + 2} />
        {bundle.colors.map((color, strand) => {
          const shift = (strand - (bundle.colors.length - 1) / 2) * 3;
          return <path key={color} d={curve({ x: a.x, y: a.y + shift }, { x: b.x, y: b.y + shift })} fill="none" stroke={color} strokeWidth={2.4} />;
        })}
        <rect className="etk-wm-tag" x={label.x - 52} y={label.y - 10} width={104} height={20} rx={10} />
        <text className="etk-wm-tagtext" x={label.x} y={label.y + 4} textAnchor="middle">{bundle.label}</text>
      </g>;
    })}
    {model.links.map((link, index) => {
      const a = anchor(link.from[0], link.from[1], "right"), b = anchor(link.to[0], link.to[1], "left");
      if (!a || !b) return null;
      return <g key={`l${index}`}>
        <title>{link.wire}</title>
        {LIGHT.test(link.color) && <path d={curve(a, b)} className="etk-wm-edge" strokeWidth={4.4} />}
        <path d={curve(a, b)} fill="none" stroke={link.color} strokeWidth={2.6} />
      </g>;
    })}
    {model.columns.flat().map((item) => {
      const at = place.get(item.id)!;
      const pinLeft = at.last; // справа — номер контакта у левого края, к проводам
      const rows: WireMapRow[] = item.rows.length ? item.rows : [{ pin: "", wire: "контакты не заданы", color: "" }];
      return <g key={item.id}>
        <rect className="etk-wm-box" x={at.x} y={at.y} width={BOX} height={HEAD + rows.length * ROW} rx={6} />
        <rect className="etk-wm-head" x={at.x} y={at.y} width={BOX} height={HEAD} rx={6} />
        <text className="etk-wm-title" x={at.x + 10} y={at.y + 17}><title>{item.title}</title>{cut(item.title, 34)}</text>
        <text className="etk-wm-sub" x={at.x + 10} y={at.y + 32}><title>{item.sub}</title>{cut(item.sub, 46)}</text>
        {rows.map((row, index) => {
          const y = at.y + HEAD + index * ROW;
          const pinX = pinLeft ? at.x : at.x + BOX - PIN;
          const nameX = pinLeft ? at.x + PIN + 8 : at.x + 10;
          return <g key={`${row.pin}:${index}`}>
            <line className="etk-wm-rule" x1={at.x} x2={at.x + BOX} y1={y} y2={y} />
            {row.pin !== "" && <>
              <rect className="etk-wm-pin" x={pinX} y={y} width={PIN} height={ROW} />
              <text className="etk-wm-pintext" x={pinX + PIN / 2} y={y + 15} textAnchor="middle">{row.pin}</text>
            </>}
            {row.color && <rect x={nameX} y={y + 7} width={10} height={8} rx={2} fill={row.color} className="etk-wm-swatch" />}
            <text className="etk-wm-wire" x={nameX + (row.color ? 16 : 0)} y={y + 15}><title>{row.wire}</title>
              {cut(row.wire, row.pin !== "" ? 31 : 36)}{row.count && row.count > 1 ? ` × ${row.count}` : ""}</text>
          </g>;
        })}
      </g>;
    })}
  </svg>;
}

/** Редактор упал при показе — остаётся плоская схема. */
class E4Boundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

type Loaded = { state: "loading" } | { state: "map" } | { state: "e4"; ports: HarnessDrawingPorts; catalog: HarnessEditorCatalogApi };

/** Схема Э4 позиции: сохранённый чертёж — только просмотр; иначе плоская схема по ЭТК. В карточке задания (`taskId`)
 *  чертёж читается маршрутом заданий — раздел «Проекты» монтажнику не нужен; технолог (без `taskId`) — маршрутом проекта. */
export function EtkHarnessE4({ project, index, model, taskId }: { project: string; index: string; model: WireMapModel | null; taskId?: string }) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  useEffect(() => {
    let live = true;
    setLoaded({ state: "loading" });
    const map = () => { if (live) setLoaded({ state: "map" }); };
    try {
      (taskId ? productionApi.taskHarness(taskId) : harnessApi.drawing(project, index)).then((found) => {
        if (!found?.design) return map();
        return loadHarnessDrawing().then(({ harnessDrawing, createErpHarnessEditorCatalog }) => {
          const catalog = createErpHarnessEditorCatalog();
          const ports = harnessDrawing({ templates: catalog.templates, readOnly: true });
          if (live) setLoaded({ state: "e4", ports, catalog });
        });
      }).catch(map);
    } catch { map(); }
    return () => { live = false; };
  }, [project, index, taskId]);

  const flat = model ? <div className="etk-wiremap-wrap"><EtkWireMap model={model} /></div>
    : <div className="empty">Схема жгута не задана: нет ни чертежа, ни таблицы соединений.</div>;
  if (loaded.state === "loading") return <div className="empty">Загрузка схемы…</div>;
  if (loaded.state === "map") return flat;
  return <div className="etk-e4" role="group" aria-label={`Схема Э4 ${index}`}>
    <E4Boundary fallback={flat}>
      <Suspense fallback={<div className="empty">Загрузка схемы…</div>}>
        <HarnessEditorLazy projectId={project} harnessId={index} harnessDesignation={index} initialView="e4"
          api={loaded.ports.api} placements={loaded.ports.placements} catalog={loaded.catalog} readOnly phoneView />
      </Suspense>
    </E4Boundary>
  </div>;
}
