import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { techcardsApi } from "./api";
import { connectorPicture, isDark, type ConnectorHousing } from "./connectorArt";
import type { PinoutEnd } from "./pinoutCheck";
import type { Connection } from "./tcGenLogic";
import "./pinout.css";

/* Распиновка в генераторе ТК: панель контактов в карточке разъёма, отметка «проверена», схема соединений
   А → В и вопрос перед «Создать», если что-то не проверено (владелец 10.10). Логика — pinoutCheck.ts. */

const housingCache = new Map<string, Promise<ConnectorHousing | null>>();
/** Тесты: корпуса читаются заново (каждый тест подменяет ответы API своими). */
export function resetPinoutHousings() { housingCache.clear(); }

function housingOf(art: string): Promise<ConnectorHousing | null> {
  let found = housingCache.get(art);
  if (!found) {
    found = techcardsApi.connectorFamilies(art).then((result) => result?.match ?? null).catch(() => null);
    housingCache.set(art, found);
  }
  return found;
}

/** Корпуса разъёмов из базы разъёмов (рисунок и число контактов); нет в базе — схема рисует без них. */
function useHousings(arts: string[]): Record<string, ConnectorHousing | null> {
  const [map, setMap] = useState<Record<string, ConnectorHousing | null>>({});
  const key = [...new Set(arts.filter(Boolean))].sort().join("\n");
  useEffect(() => {
    let alive = true;
    const list = key ? key.split("\n") : [];
    void Promise.all(list.map(async (art) => [art, await housingOf(art)] as const))
      .then((pairs) => { if (alive) setMap(Object.fromEntries(pairs)); });
    return () => { alive = false; };
  }, [key]);
  return map;
}

/** Чип у разъёма и у «Соединений по распиновке»: не проверена / не задана / проверена. */
export function PinoutChip({ ends, short = false }: { ends: PinoutEnd[]; short?: boolean }) {
  if (!ends.length) return null;
  const open = ends.filter((end) => !end.checked);
  if (!open.length) return <span className="pin-chip is-ok">Распиновка проверена</span>;
  const empty = open.every((end) => !end.wires.some((w) => w.pins.length));
  const text = empty ? "Распиновка не задана" : "Распиновка не проверена";
  return <span className="pin-chip" title={open.map((end) => `${end.key} ${end.conn}: ${end.summary}`).join("\n")}>
    {short || ends.length === 1 ? text : `${text}: ${open.map((end) => end.key).join(", ")}`}</span>;
}

/** Контакты разъёма в его карточке: открыто, пока не проверено; после проверки — строка итога и «Изменить». */
export function PinoutEditor({ end, open, hint, onOpen, onPins, onConfirm }: {
  end: PinoutEnd;
  open: boolean;
  hint?: string;
  onOpen: (open: boolean) => void;
  onPins: (wireIdx: number, value: string) => void;
  onConfirm: () => void;
}) {
  const shown = !end.checked || open;
  return <div className={end.checked ? "pin-panel is-ok" : "pin-panel"} id={`pinout-${end.id}`} aria-label={`Распиновка ${end.key}`} role="group">
    <div className="pin-panel-head">
      <span className="pin-panel-title">Распиновка — контакты разъёма</span>
      {end.checked && <span className="pin-done">✓ Проверена: {end.summary}</span>}
      {end.checked
        ? <button type="button" className="btn-link" onClick={() => onOpen(!open)}>{open ? "Свернуть" : "Изменить"}</button>
        : <button type="button" className="secondary small" onClick={onConfirm}>Подтвердить распиновку</button>}
    </div>
    {shown && <>
      <div className="ep-pins">{end.wires.map((w) => <label className="ep-pin" key={w.idx}>
        <span>{w.name || "провод"}{w.qty > 1 ? ` ×${w.qty}` : ""}</span>
        <input value={w.text} aria-label={`Контакты ${end.key}: ${w.name}`} placeholder={w.qty > 1 ? `1, 3 или 1-${w.qty}` : "конт."}
          className={w.pins.some((p) => end.dups.includes(p)) ? "is-bad" : undefined}
          onChange={(event) => onPins(w.idx, event.target.value.trim())} />
      </label>)}</div>
      {!end.checked && <small className="pin-state">Сейчас: {end.summary}</small>}
      {end.dups.length > 0 && <small className="tone-danger">Контакт {end.dups.join(", ")} занят двумя проводами — проверьте или включите двойной обжим.</small>}
      {hint && <small className="muted">{hint}</small>}
    </>}
  </div>;
}

/** «Создать», а распиновка не проверена: какие разъёмы, перейти к ним или создать всё равно. */
export function PinoutAsk({ ends, onCheck, onCreate, onCancel }: {
  ends: PinoutEnd[];
  onCheck: (id: string) => void;
  onCreate: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <div className="panel move-dialog pin-ask" role="dialog" aria-modal="true" aria-label="Распиновка не проверена">
      <h2>Распиновка не проверена</h2>
      <p className="muted">Проверьте контакты разъёмов на схеме соединений — даже если порядок прямой (1→1, 2→2).</p>
      <ul className="pin-ask-list">{ends.map((end) => <li key={end.id}>
        <b className="ep-badge">{end.key}</b>
        <span className="pin-ask-conn">{end.conn || "разъём не выбран"}</span>
        <span className="pin-ask-sum">{end.summary}</span>
        <button type="button" className="secondary small" onClick={() => onCheck(end.id)}>Проверить</button>
      </li>)}</ul>
      <div className="move-actions">
        <button type="button" className="secondary" onClick={onCancel}>Отмена</button>
        <button type="button" className="primary" onClick={onCreate}>Создать всё равно</button>
      </div>
    </div>
  </div>, document.body);
}

// ── Схема соединений ───────────────────────────────────────────────────────────
const W = 640, LABEL = 150, CELL = 32, HEAD = 20, ART = 46, GAP = 18;
const NEUTRAL = "#8a94a6", OUTLINE = "#3b4652";

type Cell = { pin: number; y: number; wires: string[]; dup: boolean };
type Block = { end: PinoutEnd; x: number; y: number; h: number; cellH: number; cells: Cell[]; art: string | null; artW: number };

function clip(text: string, max = 22) { return text.length > max ? `${text.slice(0, max - 1)}…` : text; }

function layout(ends: PinoutEnd[], housings: Record<string, ConnectorHousing | null>) {
  const blocks: Block[] = [];
  const bottom: Record<string, number> = { A: 0, B: 0 };
  ends.forEach((end) => {
    const hs = housings[end.conn] ?? null;
    const used = end.wires.flatMap((w) => w.pins);
    const n = Math.min(80, Math.max(1, hs?.n || 0, ...used));
    const cellH = n > 20 ? 18 : 22;
    const byPin: Record<number, string[]> = {};
    end.wires.forEach((w) => w.pins.forEach((p) => { (byPin[p] = byPin[p] || []).push(w.name); }));
    const pic = hs ? connectorPicture(hs, 150, ART - 6, Object.fromEntries(Object.entries(byPin).map(([p, names]) => [p, names[0]]))) : null;
    const x = end.side === "A" ? LABEL : W - LABEL - CELL;
    const y = bottom[end.side];
    const top = y + HEAD + (pic ? ART : 0);
    const cells = Array.from({ length: n }, (_, i) => ({ pin: i + 1, y: top + i * cellH, wires: byPin[i + 1] || [], dup: end.dups.includes(i + 1) }));
    const h = top - y + n * cellH;
    blocks.push({ end, x, y, h, cellH, cells, art: pic ? pic.body : null, artW: pic ? pic.w : 0 });
    bottom[end.side] = y + h + GAP;
  });
  return { blocks, height: Math.max(bottom.A, bottom.B, 40) - GAP + 4 };
}

/** Схема соединений: разъёмы А слева, В справа, контакты по порядку; линия на провод от контакта А к контакту В
 *  цветом жилы (белая — с тёмной обводкой), подпись провода у контакта, свободные контакты серые, занятый дважды — красный. */
export function PinoutDiagram({ ends, links }: { ends: PinoutEnd[]; links: Connection[] }) {
  const housings = useHousings(ends.map((end) => end.conn));
  if (!ends.length) return null;
  const { blocks, height } = layout(ends, housings);
  const at = new Map<string, { x: number; y: number; side: "A" | "B" }>();
  blocks.forEach((b) => b.cells.forEach((c) => at.set(`${b.end.key}.${c.pin}`, {
    x: b.end.side === "A" ? b.x + CELL : b.x, y: c.y + b.cellH / 2, side: b.end.side,
  })));
  const linked = new Set<string>();
  const lines = links.flatMap((link, i) => {
    const a = at.get(`${link.from.end}.${link.from.pin}`), b = at.get(`${link.to.end}.${link.to.pin}`);
    if (!a || !b) return [];
    linked.add(`${link.from.end}.${link.from.pin}`); linked.add(`${link.to.end}.${link.to.pin}`);
    const bend = (p: { x: number; side: string }, other: { x: number }) => (a.side === b.side ? (p.side === "A" ? p.x + 110 : p.x - 110) : p.x + (other.x - p.x) * 0.42);
    const d = `M${a.x},${a.y} C${bend(a, b)},${a.y} ${bend(b, a)},${b.y} ${b.x},${b.y}`;
    return [{ key: `${i}`, d, link }];
  });
  // Контакт с проводом без пары на другой стороне — короткий хвост внутрь.
  const stubs = blocks.flatMap((b) => b.end.wires.flatMap((w) => w.pins.filter((p) => !linked.has(`${b.end.key}.${p}`)).map((p) => {
    const pt = at.get(`${b.end.key}.${p}`)!;
    const x2 = pt.side === "A" ? pt.x + 26 : pt.x - 26;
    return { key: `${b.end.key}.${p}.${w.idx}`, d: `M${pt.x},${pt.y} L${x2},${pt.y}`, x2, y: pt.y, wire: w.name, end: b.end.key, pin: p };
  })));
  const stroke = (wire: string) => ends.flatMap((end) => end.wires).find((w) => w.name === wire)?.color ?? NEUTRAL;
  const lineEl = (key: string, d: string, wire: string, attrs: Record<string, string>, title: string) => {
    const color = stroke(wire);
    const light = color === NEUTRAL || !isDark(color);
    return <g key={key} className="pin-wire" data-wire={wire} {...attrs}>
      <title>{title}</title>
      {light && <path d={d} fill="none" stroke={OUTLINE} strokeWidth={5} strokeLinecap="round" opacity={0.75} />}
      <path className="pin-line" d={d} fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" strokeDasharray={color === NEUTRAL ? "6 4" : undefined} />
    </g>;
  };
  return <figure className="pin-diagram" aria-label="Схема соединений">
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" style={{ maxWidth: W * 1.15 }} role="img" aria-label="Схема соединений разъёмов А и В">
      {blocks.map((b) => {
        const left = b.end.side === "A";
        const labelX = left ? b.x - 8 : b.x + CELL + 8;
        return <g key={b.end.id} className="pin-block" data-end={b.end.key}>
          <text x={left ? b.x + CELL : b.x} y={b.y + 14} textAnchor={left ? "end" : "start"} className="pin-head">
            <tspan className="pin-head-key">{b.end.key}</tspan>{` · ${clip(b.end.conn || "разъём не выбран", 30)}`}</text>
          {b.art && <g transform={`translate(${b.x + CELL / 2 - b.artW / 2},${b.y + HEAD})`} dangerouslySetInnerHTML={{ __html: b.art }} />}
          {b.cells.map((c) => {
            const used = c.wires.length > 0;
            const cls = c.dup ? "pin-cell is-dup" : used ? "pin-cell is-used" : "pin-cell";
            return <g key={c.pin} className={cls} data-pin={c.pin}>
              <title>{`${b.end.key}, контакт ${c.pin}: ${used ? c.wires.join("; ") : "свободен"}${c.dup ? " — занят дважды" : ""}`}</title>
              <rect x={b.x} y={c.y + 1} width={CELL} height={b.cellH - 2} rx={3} />
              <text x={b.x + CELL / 2} y={c.y + b.cellH / 2 + 4} textAnchor="middle">{c.pin}</text>
              {used && <text className="pin-label" x={labelX} y={c.y + b.cellH / 2 + 4} textAnchor={left ? "end" : "start"}>{clip(c.wires.join("; "))}</text>}
            </g>;
          })}
        </g>;
      })}
      {lines.map(({ key, d, link }) => lineEl(key, d, link.wire, { "data-from": `${link.from.end}.${link.from.pin}`, "data-to": `${link.to.end}.${link.to.pin}` },
        `${link.wire || "Провод"}: ${link.from.end} конт. ${link.from.pin} → ${link.to.end} конт. ${link.to.pin}`))}
      {stubs.map((s) => <g key={s.key}>
        {lineEl(s.key, s.d, s.wire, { "data-from": `${s.end}.${s.pin}` }, `${s.wire || "Провод"}: ${s.end} конт. ${s.pin} — на другой стороне контакт не задан`)}
        <circle cx={s.x2} cy={s.y} r={3} className="pin-stub-end" />
      </g>)}
    </svg>
    {!links.length && <figcaption className="muted">Линии появятся, когда у проводов будут контакты на обеих сторонах.</figcaption>}
  </figure>;
}
