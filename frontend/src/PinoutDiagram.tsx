import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { techcardsApi } from "./api";
import { connectorPicture, isDark, type ConnectorHousing } from "./connectorArt";
import type { AutoFill, FreeSide, PinoutEnd, PinUnit } from "./pinoutCheck";
import type { Connection } from "./tcGenLogic";
import "./pinout.css";

/* Распиновка в генераторе ТК: панель контактов в карточке разъёма, отметка «проверена», схема соединений
   А → В и вопрос перед «Создать», если что-то не проверено (владелец 10.10). Редактор на схеме (владелец 12.10):
   провода палитрой — щелчок берёт провод, щелчок по контакту ставит; автозаполнение; свободные концы с обработкой.
   Логика — pinoutCheck.ts. */

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
export function usePinHousings(arts: string[]): Record<string, ConnectorHousing | null> {
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
const W = 760, LABEL = 200, CELL = 32, HEAD = 20, ART = 46, GAP = 18;
/** Разъём только с одной стороны: отступ разъёма от края и колонка свободных концов (начало кончика провода). */
const ONE_X = 160, TIP_X = 560, TIP_LEN = 22;
const NEUTRAL = "#8a94a6", OUTLINE = "#3b4652";

type Cell = { pin: number; y: number; units: PinUnit[]; dup: boolean };
type Block = { end: PinoutEnd; x: number; y: number; h: number; cellH: number; cells: Cell[]; art: string | null; artW: number };

function clip(text: string, max = 22) { return text.length > max ? `${text.slice(0, max - 1)}…` : text; }
const unitText = (u: PinUnit) => `${u.num} · ${u.name || "провод"}`;

const coarsePointer = () => { try { return !!window.matchMedia?.("(pointer: coarse)").matches; } catch { return false; } };

function layout(ends: PinoutEnd[], units: PinUnit[], housings: Record<string, ConnectorHousing | null>, free: FreeSide | null, coarse: boolean) {
  const blocks: Block[] = [];
  const bottom: Record<string, number> = { A: 0, B: 0 };
  ends.forEach((end) => {
    const hs = housings[end.conn] ?? null;
    const mine = units.filter((u) => end.id in u.at);
    const used = mine.map((u) => u.at[end.id]).filter((p): p is number => p != null);
    // Число контактов — из базы разъёмов; нет в базе — по числу проводов, чтобы каждому было куда встать.
    const n = Math.min(80, Math.max(1, hs?.n || Math.ceil(mine.length / end.per), ...used));
    // Свободные концы: над линией провода — его подпись, строке нужно больше места; планшет — контакт под палец.
    const cellH = n > 20 ? 18 : coarse ? 34 : free ? 28 : 22;
    const byPin: Record<number, PinUnit[]> = {};
    mine.forEach((u) => { const p = u.at[end.id]; if (p != null) (byPin[p] = byPin[p] || []).push(u); });
    const pic = hs ? connectorPicture(hs, 150, ART - 6, Object.fromEntries(Object.entries(byPin).map(([p, list]) => [p, list[0].name]))) : null;
    const x = free ? (end.side === "A" ? ONE_X : W - ONE_X - CELL) : end.side === "A" ? LABEL : W - LABEL - CELL;
    const y = bottom[end.side];
    const top = y + HEAD + (pic ? ART : 0);
    const cells = Array.from({ length: n }, (_, i) => ({ pin: i + 1, y: top + i * cellH, units: byPin[i + 1] || [], dup: end.dups.includes(i + 1) }));
    const h = top - y + n * cellH;
    blocks.push({ end, x, y, h, cellH, cells, art: pic ? pic.body : null, artW: pic ? pic.w : 0 });
    bottom[end.side] = y + h + GAP;
  });
  return { blocks, height: Math.max(bottom.A, bottom.B, 40) - GAP + 4 };
}

/** Провод «в руке»: из палитры (только key) или взятый с контакта (epId + pin). */
export type PinHand = { key: string; epId?: string; pin?: number };

/** Схема соединений: разъёмы А слева, В справа, контакты по порядку; линия на провод от контакта А к контакту В
 *  цветом жилы (белая — с тёмной обводкой), подпись провода у контакта, свободные контакты серые, занятый дважды —
 *  красный. Разъём с одной стороны — провод тянется к свободному концу, на конце видно его обработку (лужение,
 *  зачистка или без обработки). Контакты — кнопки: щелчок и Enter/пробел ставят провод из руки. */
export function PinoutDiagram({ ends, links, units, housings, free = null, hand = null, onCell, onGrab }: {
  ends: PinoutEnd[];
  links: Connection[];
  units: PinUnit[];
  housings: Record<string, ConnectorHousing | null>;
  free?: FreeSide | null;
  hand?: PinHand | null;
  onCell?: (epId: string, pin: number) => void;
  onGrab?: (epId: string, pin: number, event: ReactPointerEvent) => void;
}) {
  if (!ends.length) return null;
  const { blocks, height } = layout(ends, units, housings, free, coarsePointer());
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
  const colorOf = (wire: string) => units.find((u) => u.name === wire)?.color ?? NEUTRAL;
  // Контакт с проводом без пары на другой стороне (разъёмы с обеих сторон) — короткий хвост внутрь.
  const stubs = free ? [] : blocks.flatMap((b) => b.cells.flatMap((c) => c.units.filter(() => !linked.has(`${b.end.key}.${c.pin}`)).map((u) => {
    const pt = at.get(`${b.end.key}.${c.pin}`)!;
    const x2 = pt.side === "A" ? pt.x + 26 : pt.x - 26;
    return { key: `${b.end.key}.${c.pin}.${u.key}`, d: `M${pt.x},${pt.y} L${x2},${pt.y}`, x2, y: pt.y, wire: u.name, end: b.end.key, pin: c.pin };
  })));
  // Разъём с одной стороны: каждый провод с контактом — линия до свободного конца и его обработка.
  const dir = free?.side === "A" ? -1 : 1;
  const tipX = dir > 0 ? TIP_X : W - TIP_X;
  const freeLines = !free ? [] : blocks.flatMap((b) => b.cells.flatMap((c) => c.units.map((u, i) => {
    const pt = at.get(`${b.end.key}.${c.pin}`)!;
    const y = pt.y + i * 4;
    const end = free.ends.find((e) => e.idx === u.idx);
    return { key: `${b.end.key}.${c.pin}.${u.key}`, unit: u, end: b.end.key, pin: c.pin, x0: pt.x, y, kind: end?.kind ?? "none", text: end?.text ?? "без обработки" };
  })));
  const wireEl = (key: string, d: string, color: string, attrs: Record<string, string>, title: string) => {
    const light = color === NEUTRAL || !isDark(color);
    return <g key={key} className="pin-wire" {...attrs}>
      <title>{title}</title>
      {light && <path d={d} fill="none" stroke={OUTLINE} strokeWidth={5} strokeLinecap="round" opacity={0.75} />}
      <path className="pin-line" d={d} fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" strokeDasharray={color === NEUTRAL ? "6 4" : undefined} />
    </g>;
  };
  const handUnit = hand ? units.find((u) => u.key === hand.key) ?? null : null;
  const keyFor = (event: ReactKeyboardEvent, epId: string, pin: number) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onCell?.(epId, pin);
  };
  return <figure className={handUnit ? "pin-diagram has-hand" : "pin-diagram"} aria-label="Схема соединений">
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" style={{ maxWidth: W * 1.15 }} role="group" aria-label="Схема соединений разъёмов А и В">
      {free && <text className="pin-head pin-free-head" x={tipX} y={14} textAnchor={dir > 0 ? "start" : "end"}>
        {`Сторона ${free.side === "B" ? "В" : "А"} — свободные концы`}</text>}
      {lines.map(({ key, d, link }) => wireEl(key, d, colorOf(link.wire), { "data-wire": link.wire, "data-from": `${link.from.end}.${link.from.pin}`, "data-to": `${link.to.end}.${link.to.pin}` },
        `${link.wire || "Провод"}: ${link.from.end} конт. ${link.from.pin} → ${link.to.end} конт. ${link.to.pin}`))}
      {stubs.map((s) => <g key={s.key}>
        {wireEl(s.key, s.d, colorOf(s.wire), { "data-wire": s.wire, "data-from": `${s.end}.${s.pin}` }, `${s.wire || "Провод"}: ${s.end} конт. ${s.pin} — на другой стороне контакт не задан`)}
        <circle cx={s.x2} cy={s.y} r={3} className="pin-stub-end" />
      </g>)}
      {freeLines.map((f) => {
        const color = f.unit.color ?? NEUTRAL;
        const x1 = tipX;
        const label = `${unitText(f.unit)}${f.unit.length ? ` · длина ${f.unit.length} мм` : ""}`;
        const tip = f.kind === "tin" ? <rect className="pin-tip-tin" x={dir > 0 ? x1 : x1 - TIP_LEN} y={f.y - 3.5} width={TIP_LEN} height={7} rx={3.5} />
          : f.kind === "strip" ? <rect className="pin-tip-cu" x={dir > 0 ? x1 : x1 - 16} y={f.y - 2} width={16} height={4} rx={1} />
            : <line className="pin-tip-cut" x1={x1} x2={x1} y1={f.y - 5} y2={f.y + 5} />;
        const textX = dir > 0 ? x1 + TIP_LEN + 8 : x1 - TIP_LEN - 8;
        return <g key={f.key} className="pin-free" data-from={`${f.end}.${f.pin}`} data-free={f.kind}>
          {wireEl(`${f.key}.line`, `M${f.x0},${f.y} L${x1},${f.y}`, color, { "data-wire": f.unit.name }, `${f.unit.name || "Провод"}: ${f.end} конт. ${f.pin} → свободный конец, ${f.text}`)}
          {tip}
          <text className="pin-free-name" x={f.x0 + dir * 12} y={f.y - 6} textAnchor={dir > 0 ? "start" : "end"}><title>{label}</title>{clip(label, 56)}</text>
          <text className={`pin-free-label is-${f.kind}`} x={textX} y={f.y + 4} textAnchor={dir > 0 ? "start" : "end"}>{f.text}</text>
        </g>;
      })}
      {blocks.map((b) => {
        const left = b.end.side === "A";
        const labelX = left ? b.x - 8 : b.x + CELL + 8;
        const target = !!handUnit && b.end.id in handUnit.at;
        return <g key={b.end.id} className={target ? "pin-block is-target" : "pin-block"} data-end={b.end.key}>
          <text x={left ? b.x + CELL : b.x} y={b.y + 14} textAnchor={left ? "end" : "start"} className="pin-head">
            <tspan className="pin-head-key">{b.end.key}</tspan>{` · ${clip(b.end.conn || "разъём не выбран", 30)}`}</text>
          {b.art && <g transform={`translate(${b.x + CELL / 2 - b.artW / 2},${b.y + HEAD})`} dangerouslySetInnerHTML={{ __html: b.art }} />}
          {b.cells.map((c) => {
            const used = c.units.length > 0;
            const color = used ? c.units[0].color ?? NEUTRAL : null;
            const held = !!hand && hand.epId === b.end.id && hand.pin === c.pin;
            const cls = ["pin-cell", used && "is-used", c.dup && "is-dup", held && "is-held", color && !isDark(color) && "is-light"].filter(Boolean).join(" ");
            const names = c.units.map(unitText).join("; ");
            const tip = `${b.end.key}, контакт ${c.pin}: ${used ? names : "свободен"}${c.dup ? " — занят дважды" : ""}`;
            return <g key={c.pin} className={cls} data-pin={c.pin} data-ep={b.end.id} role="button" tabIndex={0} aria-label={tip} aria-pressed={held}
              onClick={() => onCell?.(b.end.id, c.pin)} onKeyDown={(event) => keyFor(event, b.end.id, c.pin)}
              onPointerDown={used && onGrab ? (event) => onGrab(b.end.id, c.pin, event) : undefined}>
              <title>{tip}</title>
              <rect x={b.x} y={c.y + 1} width={CELL} height={b.cellH - 2} rx={3} style={color ? { fill: color } : undefined} />
              <text x={b.x + CELL / 2} y={c.y + b.cellH / 2 + 4} textAnchor="middle" style={color ? { fill: isDark(color) ? "#fff" : "#17272b" } : undefined}>{c.pin}</text>
              {used && !free && <text className="pin-label" x={labelX} y={c.y + b.cellH / 2 + 4} textAnchor={left ? "end" : "start"}>{clip(names, 28)}</text>}
            </g>;
          })}
        </g>;
      })}
    </svg>
    {!free && !links.length && <figcaption className="muted">Линии появятся, когда у проводов будут контакты на обеих сторонах.</figcaption>}
  </figure>;
}

// ── Редактор распиновки над схемой ─────────────────────────────────────────────────────────────────────────────
const Swatch = ({ color }: { color: string | null }) =>
  <span className={color ? "pin-swatch" : "pin-swatch is-unknown"} style={color ? { background: color } : undefined} aria-hidden="true" />;

/** Палитра проводов, рука и схема: щелчок по проводу берёт его, щелчок по контакту ставит; щелчок по занятому
 *  контакту берёт его провод — следующий щелчок по контакту того же разъёма меняет их местами (свободный — перенос).
 *  Перетаскивание провода или занятого контакта делает то же. Esc или повторный щелчок — отпустить. */
export function PinoutBoard({ ends, links, units, housings, free, wireOptions, warnings, onAssign, onUnassign, onSwap, onRecolor, onAutoFill }: {
  ends: PinoutEnd[];
  links: Connection[];
  units: PinUnit[];
  housings: Record<string, ConnectorHousing | null>;
  free: FreeSide | null;
  wireOptions: Array<{ id: string; label: string }>;
  /** Распиновка не помещается в разъём (pinFitWarnings). */
  warnings: string[];
  onAssign: (epId: string, idx: number, unit: number, pin: number) => void;
  onUnassign: (epId: string, idx: number, unit: number) => void;
  onSwap: (epId: string, a: number, b: number) => void;
  onRecolor: (idx: number, compId: string) => void;
  onAutoFill: () => AutoFill;
}) {
  const [hand, setHand] = useState<PinHand | null>(null);
  const [fill, setFill] = useState<{ notes: string[]; warnings: string[] } | null>(null);
  const [dragging, setDragging] = useState(false);
  const handUnit = hand ? units.find((u) => u.key === hand.key) ?? null : null;
  const keyOf = (epId: string) => ends.find((end) => end.id === epId)?.key ?? "";

  useEffect(() => {
    if (!hand) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setHand(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hand]);
  // Провод пропал из формы (удалили кабель) — из руки тоже.
  useEffect(() => { if (hand && !handUnit) setHand(null); }, [hand, handUnit]);

  /** Провод из руки `from` — в контакт `pin` разъёма `epId`. */
  function place(from: PinHand, epId: string, pin: number) {
    const unit = units.find((u) => u.key === from.key);
    if (!unit) return setHand(null);
    setFill(null);
    if (from.epId === epId && from.pin === pin) return setHand(null);
    if (from.epId === epId && from.pin != null) { onSwap(epId, from.pin, pin); return setHand(null); }
    if (!(epId in unit.at)) return; // провод не заведён в этот разъём
    onAssign(epId, unit.idx, unit.unit, pin);
    // Провод ещё без контакта на другом разъёме — остаётся в руке: следующий щелчок — туда.
    const rest = Object.entries(unit.at).some(([id, p]) => id !== epId && p == null);
    setHand(rest ? { key: unit.key } : null);
  }
  function clickCell(epId: string, pin: number) {
    if (suppress.current) return;
    if (hand) return place(hand, epId, pin);
    const occupant = units.find((u) => u.at[epId] === pin);
    if (occupant) setHand({ key: occupant.key, epId, pin });
  }

  // Перетаскивание: указатель отпущен над контактом — то же, что щелчок по нему с проводом в руке.
  const drag = useRef<{ from: PinHand; x: number; y: number; moved: boolean } | null>(null);
  const suppress = useRef(false);
  const placeRef = useRef(place);
  placeRef.current = place;
  function grab(from: PinHand, event: ReactPointerEvent) {
    if (event.button !== 0) return;
    drag.current = { from, x: event.clientX, y: event.clientY, moved: false };
  }
  useEffect(() => {
    const move = (event: PointerEvent) => {
      const d = drag.current;
      if (d && !d.moved && Math.hypot(event.clientX - d.x, event.clientY - d.y) > 6) { d.moved = true; setDragging(true); }
    };
    const up = (event: PointerEvent) => {
      const d = drag.current;
      drag.current = null;
      if (!d?.moved) return;
      setDragging(false);
      suppress.current = true;
      window.setTimeout(() => { suppress.current = false; }, 0);
      const cell = document.elementFromPoint?.(event.clientX, event.clientY)?.closest("[data-ep][data-pin]");
      if (cell) placeRef.current(d.from, cell.getAttribute("data-ep") || "", Number(cell.getAttribute("data-pin")));
    };
    const cancel = () => { drag.current = null; setDragging(false); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", cancel); };
  }, []);

  function autoFill() {
    const manual = units.some((u) => Object.values(u.at).some((p) => p != null));
    if (manual && !window.confirm("Заменить распиновку автоматической? Контакты, заданные вручную, будут перезаписаны.")) return;
    setHand(null);
    const result = onAutoFill();
    setFill({ notes: result.notes, warnings: result.warnings });
  }

  const place_ = (u: PinUnit) => Object.entries(u.at).map(([id, p]) => `${keyOf(id)}·${p ?? "—"}`).join(" → ");
  return <div className={dragging ? "pin-board is-dragging" : "pin-board"}>
    <div className="pin-tools">
      <div className="pin-palette" role="group" aria-label="Провода">
        {units.map((u) => {
          const open = Object.values(u.at).some((p) => p == null);
          const held = hand?.key === u.key;
          return <button type="button" key={u.key} className={["pin-chip-wire", held && "is-held", open && "is-open"].filter(Boolean).join(" ")} aria-pressed={held}
            title={`${unitText(u)}: ${place_(u) || "не заведён в разъём"}`}
            onClick={() => { if (!suppress.current) { setFill(null); setHand(held && !hand?.epId ? null : { key: u.key }); } }}
            onPointerDown={(event) => grab({ key: u.key }, event)}>
            <Swatch color={u.color} /><span className="pin-num">{u.num}</span><span className="pin-chip-name">{u.name || "провод"}</span>
            <small>{place_(u) || "без разъёма"}</small>
          </button>;
        })}
      </div>
      <button type="button" className="secondary small" onClick={autoFill}>Заполнить автоматически</button>
    </div>
    {handUnit ? <div className="pin-hand" role="status">
      <span className="pin-hand-what"><Swatch color={handUnit.color} /><span className="pin-num">{handUnit.num}</span> {handUnit.name || "провод"}
        {hand?.epId && hand.pin != null && <span className="muted"> · {keyOf(hand.epId)}, конт. {hand.pin}</span>}</span>
      <span className="muted">{hand?.epId ? `другой контакт ${keyOf(hand.epId)} — поменять местами` : "щёлкните контакт"} · Esc — отпустить</span>
      <label className="pin-hand-color">Провод
        <select aria-label="Провод на контакте" value={handUnit.compId} onChange={(event) => onRecolor(handUnit.idx, event.target.value)}>
          {!wireOptions.some((o) => o.id === handUnit.compId) && <option value={handUnit.compId}>{handUnit.name || "—"}</option>}
          {wireOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select></label>
      {hand?.epId && hand.pin != null && <button type="button" className="secondary small"
        onClick={() => { onUnassign(hand.epId!, handUnit.idx, handUnit.unit); setHand(null); }}>Снять с контакта</button>}
      <button type="button" className="btn-link" onClick={() => setHand(null)}>Отпустить</button>
    </div> : <small className="muted pin-hint">Щёлкните провод, затем контакт (или перетащите). Щелчок по занятому контакту — перенести или поменять местами.</small>}
    <PinoutDiagram ends={ends} links={links} units={units} housings={housings} free={free} hand={hand}
      onCell={clickCell} onGrab={(epId, pin, event) => {
        const occupant = units.find((u) => u.at[epId] === pin);
        if (occupant) grab({ key: occupant.key, epId, pin }, event);
      }} />
    <div className="pin-board-notes" aria-live="polite">
      {fill?.notes.map((item) => <small className="muted pin-note" key={item}>{item}</small>)}
      {[...new Set([...(fill?.warnings ?? []), ...warnings])].map((item) => <div className="warning-inline" key={item}>{item}</div>)}
    </div>
  </div>;
}
