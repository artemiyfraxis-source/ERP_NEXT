import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { PALETTE } from "./wireColors";
import { techcardsApi } from "./api";
import { connectorFace, connectorFaceUnits, isDark, type ConnectorHousing, type FaceHit } from "./connectorArt";
import type { AutoFill, FreeSide, PinoutEnd, PinUnit } from "./pinoutCheck";
import type { Branch, Connection } from "./tcGenLogic";
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
/** Пикселей на шаг контактов: гнездо не меньше 28 px под курсор и палец; круглые разъёмы крупнее — контакты стоят по кругу. */
const MIN_STEP = 28, MAX_STEP = 44, FACE_WIDTH = 420, ROUND_STEP = 40, PAD = 10;
const NEUTRAL = "#8a94a6", OUTLINE = "#3b4652";

const unitText = (u: PinUnit) => `${u.num} · ${u.name || "провод"}${u.colorName ? ` · ${u.colorName}` : ""}`;

/** Торец разъёма со стороны проводов — тот же рисунок, что в «База данных → Разъёмы»; гнёзда с координатами для щелчка. */
function housingFace(hs: ConnectorHousing | null, byPin: Record<number, string>) {
  if (!hs) return null;
  const round = hs.kind === "round" || hs.kind === "barrel";
  const step = round ? ROUND_STEP : Math.max(MIN_STEP, Math.min(MAX_STEP, FACE_WIDTH / connectorFaceUnits(hs)));
  const hits: FaceHit[] = [];
  const box = connectorFace(hs, PAD, PAD / 2, step, byPin, hits);
  return hits.length ? { box, hits, w: box.w + PAD * 2, h: box.h + PAD } : null;
}

/** Провод «в руке»: из палитры (только key) или взятый с контакта (epId + pin). */
export type PinHand = { key: string; epId?: string; pin?: number };

type Cell = { pin: number; units: PinUnit[]; dup: boolean };

/** Схема соединений: у каждого разъёма его торец со стороны проводов, как в базе разъёмов (сетка гнёзд с номерами и
 *  защёлкой, у круглых — кольцо контактов). Гнёзда — кнопки: щелчок, Enter или пробел ставит провод из руки; занятое
 *  гнездо залито цветом жилы, на нём номер провода. Разъёма нет в базе — пронумерованные квадраты по порядку.
 *  Разъём с одной стороны — под торцем список свободных концов с обработкой (лужение, зачистка или без неё). */
export function PinoutDiagram({ ends, links, units, housings, free = null, hand = null, branches = [], onCell, onGrab, onBranchMode, onRemoveBranch }: {
  ends: PinoutEnd[];
  links: Connection[];
  units: PinUnit[];
  housings: Record<string, ConnectorHousing | null>;
  free?: FreeSide | null;
  hand?: PinHand | null;
  branches?: Branch[];
  onCell?: (epId: string, pin: number) => void;
  onGrab?: (epId: string, pin: number, event: ReactPointerEvent) => void;
  onBranchMode?: (extra: number, mode: Branch["mode"]) => void;
  onRemoveBranch?: (extra: number) => void;
}) {
  if (!ends.length) return null;
  const handUnit = hand ? units.find((u) => u.key === hand.key) ?? null : null;
  const keyFor = (event: ReactKeyboardEvent, epId: string, pin: number) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onCell?.(epId, pin);
  };

  const cellProps = (end: PinoutEnd, c: Cell) => {
    const used = c.units.length > 0;
    const color = used ? c.units[0].color : null;
    const held = !!hand && hand.epId === end.id && hand.pin === c.pin;
    const cls = ["pin-cell", used && "is-used", c.dup && "is-dup", held && "is-held", color && !isDark(color) && "is-light"].filter(Boolean).join(" ");
    const names = c.units.map(unitText).join("; ");
    const label = `${end.key}, контакт ${c.pin}: ${used ? names : "свободен"}${c.dup ? " — занят дважды" : ""}`;
    const hover = `контакт ${c.pin} · ${used ? c.units.map((u) => u.name || "провод").join(", ") : "свободен"}`;
    return {
      used, cls, hover,
      handlers: {
        role: "button" as const, tabIndex: 0, "aria-label": label, "aria-pressed": held, "data-pin": c.pin, "data-ep": end.id,
        onClick: () => onCell?.(end.id, c.pin),
        onKeyDown: (event: ReactKeyboardEvent) => keyFor(event, end.id, c.pin),
        onPointerDown: used && onGrab ? (event: ReactPointerEvent) => onGrab(end.id, c.pin, event) : undefined,
      },
    };
  };

  const endView = (end: PinoutEnd) => {
    const hs = housings[end.conn] ?? null;
    const mine = units.filter((u) => end.id in u.at);
    const byPin: Record<number, PinUnit[]> = {};
    mine.forEach((u) => { const p = u.at[end.id]; if (p != null) (byPin[p] = byPin[p] || []).push(u); });
    const cellOf = (pin: number): Cell => ({ pin, units: byPin[pin] || [], dup: end.dups.includes(pin) });
    const face = end.conn ? housingFace(hs, Object.fromEntries(Object.entries(byPin).map(([p, list]) => [p, list[0].baseName ?? list[0].name]))) : null;
    const target = !!handUnit && (end.id in handUnit.at || end.multi);
    const head = <div className="pin-end-head"><span className="pin-end-key">{end.key}</span><span className="pin-end-conn">{end.conn || "разъём не выбран"}</span></div>;
    if (face) {
      return <section key={end.id} className={target ? "pin-end is-target" : "pin-end"} data-end={end.key} aria-label={`Схема разъёма ${end.key}`}>
        {head}
        <svg className="pin-face" viewBox={`0 0 ${face.w} ${face.h}`} width={face.w} height={face.h} role="group" aria-label={`Торец разъёма ${end.key}, вид со стороны проводов`}>
          <g dangerouslySetInnerHTML={{ __html: face.box.body }} />
          {face.hits.map((hit) => {
            const c = cellOf(hit.pin), p = cellProps(end, c);
            return <g key={hit.pin} className={p.cls} {...p.handlers}>
              <title>{p.hover}</title>
              <circle className="pin-hit" cx={hit.x} cy={hit.y} r={hit.r} />
              {p.used && <g className="pin-badge" aria-hidden="true">
                <circle cx={hit.x + hit.r * 0.78} cy={hit.y - hit.r * 0.78} r={7} />
                <text x={hit.x + hit.r * 0.78} y={hit.y - hit.r * 0.78 + 3.2} textAnchor="middle">{c.units.length > 1 ? `×${c.units.length}` : c.units[0].num}</text>
              </g>}
            </g>;
          })}
        </svg>
      </section>;
    }
    // Вид разъёма неизвестен (нет в базе или без гнёзд на рисунке): пронумерованные квадраты по порядку.
    const used = Object.keys(byPin).map(Number);
    const n = Math.min(80, Math.max(1, hs?.n || Math.ceil(mine.length / end.per), ...used));
    return <section key={end.id} className={target ? "pin-end is-target" : "pin-end"} data-end={end.key} aria-label={`Схема разъёма ${end.key}`}>
      {head}
      <div className="pin-boxes">{Array.from({ length: n }, (_, i) => {
        const c = cellOf(i + 1), p = cellProps(end, c), color = p.used ? c.units[0].color ?? NEUTRAL : null;
        return <div key={c.pin} className={`${p.cls} pin-box`} {...p.handlers} title={p.hover}
          style={color ? { background: color, color: isDark(color) ? "#fff" : "#17272b" } : undefined}>{c.pin}</div>;
      })}</div>
      <small className="muted pin-face-note">вид разъёма неизвестен — номера по порядку</small>
    </section>;
  };

  const sideOf = (side: "A" | "B") => ends.filter((end) => end.side === side);
  // «А1·4 → В1·4 + В2·2»: источник — первый разъём с контактом; ответвление добавляет свои выводы (у двойного обжима
  // его источник тот же контакт — не повторяем).
  const extraIdx = new Set(branches.map((b) => b.extra));
  const pinned = (u: PinUnit, skip?: string) => ends.filter((e) => e.id !== skip && u.at[e.id] != null).map((e) => `${e.key}·${u.at[e.id]}`);
  const routes = free ? [] : units.filter((u) => !extraIdx.has(u.idx)).flatMap((u) => {
    const own = pinned(u);
    if (!own.length) return [];
    const mine = branches.filter((b) => b.main === u.idx);
    const targets = [...own.slice(1), ...mine.flatMap((b) => {
      const x = units.find((v) => v.idx === b.extra);
      return x ? pinned(x, b.mode === "double" ? b.from : undefined) : [];
    })];
    return targets.length ? [{ unit: u, source: own[0], targets, branches: mine }] : [];
  });
  return <figure className={handUnit ? "pin-diagram has-hand" : "pin-diagram"} aria-label="Схема соединений">
    <div className={free ? "pin-sides is-one" : "pin-sides"}>
      {(["A", "B"] as const).map((side) => sideOf(side).length > 0 && <div className="pin-side" key={side} data-side={side}>{sideOf(side).map(endView)}</div>)}
    </div>
    {free && <div className="pin-free-block">
      <div className="pin-free-head">{`Сторона ${free.side === "B" ? "В" : "А"} — свободные концы`}</div>
      <ul className="pin-free-list">{units.filter((u) => Object.values(u.at).some((p) => p != null)).map((u) => {
        const fe = free.ends.find((e) => e.idx === u.idx);
        const kind = fe?.kind ?? "none";
        return <li className="pin-free" key={u.key} data-free={kind}>
          <Swatch color={u.color} stripe={u.stripe} />
          <span className="pin-free-name">{`${unitText(u)}${u.length ? ` · длина ${u.length} мм` : ""}`}</span>
          <span className="muted pin-free-at">{Object.entries(u.at).map(([id, p]) => `${ends.find((e) => e.id === id)?.key ?? ""}·${p ?? "—"}`).join(" ")}</span>
          <span className={`pin-free-label is-${kind}`}>{fe?.text ?? "без обработки"}</span>
        </li>;
      })}</ul>
    </div>}
    {!free && routes.length > 0 && <ul className="pin-routes" aria-label="Соединения">{routes.map((r) => <li className="pin-route" key={r.unit.key} data-wire={r.unit.num} data-from={r.source}>
      <Swatch color={r.unit.color} stripe={r.unit.stripe} />
      <span className="pin-route-name">{unitText(r.unit)}</span>
      <span className="pin-route-from">{r.source}</span>
      <RouteGlyph color={r.unit.color} stripe={r.unit.stripe} count={r.targets.length} />
      <span className="pin-route-targets">{r.targets.map((t) => <span className="pin-route-to" key={t} data-to={t}>{t}</span>)}</span>
      {r.branches.map((b) => <span className="pin-route-mode" key={b.extra}>
        <span className="muted">ответвление:</span>
        {(["splice", "double"] as const).map((mode) => <button type="button" key={mode} className={b.mode === mode ? "pin-mode is-on" : "pin-mode"} aria-pressed={b.mode === mode}
          onClick={() => onBranchMode?.(b.extra, mode)}>{mode === "splice" ? "сросток" : "двойной обжим"}</button>)}
        <button type="button" className="btn-link" onClick={() => onRemoveBranch?.(b.extra)}>Убрать</button>
      </span>)}
    </li>)}</ul>}
    {!free && !links.length && !routes.length && <figcaption className="muted">Соединения появятся, когда у проводов будут контакты на обеих сторонах.</figcaption>}
  </figure>;
}

/** Линия провода от источника к одному или нескольким выводам: прямая или «Y». */
function RouteGlyph({ color, stripe, count }: { color: string | null; stripe: string | null; count: number }) {
  const H = 20, h = Math.max(1, count) * H, mid = h / 2, c = color ?? NEUTRAL;
  const paths = Array.from({ length: Math.max(1, count) }, (_, i) => `M0,${mid} C20,${mid} 20,${H * i + H / 2} 40,${H * i + H / 2}`);
  return <svg className="pin-glyph" width={40} height={h} viewBox={`0 0 40 ${h}`} aria-hidden="true">
    {paths.map((d, i) => <g key={i}>
      {(c === NEUTRAL || !isDark(c)) && <path d={d} fill="none" stroke={OUTLINE} strokeWidth={4.5} strokeLinecap="round" opacity={0.75} />}
      <path className="pin-line" d={d} fill="none" stroke={c} strokeWidth={2.5} strokeLinecap="round" strokeDasharray={c === NEUTRAL ? "5 3" : undefined} />
      {stripe && <path className="pin-stripe" d={d} fill="none" stroke={stripe} strokeWidth={2.5} strokeDasharray="4 4" />}
    </g>)}
  </svg>;
}

// ── Редактор распиновки над схемой ─────────────────────────────────────────────────────────────────────────────
const Swatch = ({ color, stripe = null }: { color: string | null; stripe?: string | null }) =>
  <span className={color ? "pin-swatch" : "pin-swatch is-unknown"} style={color ? { background: stripe ? `linear-gradient(135deg, ${color} 50%, ${stripe} 50%)` : color } : undefined} aria-hidden="true" />;

/** Цвет жилы с палитры магазина (wireColors.ts): один цвет — щелчок по кружку; два цвета — основной и полоса, название
 *  вида «бело-синий». «Сбросить цвет» возвращает цвет из наименования кабеля. */
function CoreColorPopover({ unit, onPick, onClose }: { unit: PinUnit; onPick: (color: string | null) => void; onClose: () => void }) {
  const [two, setTwo] = useState(!!unit.stripe);
  const [first, setFirst] = useState(unit.baseName ?? "");
  const [second, setSecond] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const down = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node) && !(event.target as Element).closest?.(".pin-color-dot")) onClose(); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("mousedown", down);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("mousedown", down); window.removeEventListener("keydown", key); };
  }, [onClose]);
  const apply = (a: string, b: string) => {
    const head = PALETTE.find((color) => color.name === a), tail = PALETTE.find((color) => color.name === b);
    if (head && tail && head !== tail) onPick(`${head.stem}-${tail.name}`);
  };
  const row = (title: string, value: string, set: (name: string) => void) => <div className="pin-color-row" role="group" aria-label={title}>
    <small>{title}</small>
    <div className="swatch-row">{PALETTE.map((color) => <button type="button" key={color.name} className="swatch" aria-pressed={value === color.name} aria-label={`${title}: ${color.name}`}
      title={color.name} style={{ background: color.hex }} onClick={() => set(color.name)} />)}</div>
  </div>;
  return <div className="pin-color-pop" role="dialog" aria-label={`Цвет жилы ${unit.num}`} ref={box}>
    <label className="pin-color-two"><input type="checkbox" checked={two} onChange={(event) => setTwo(event.target.checked)} /> Два цвета</label>
    {two ? <>
      {row("Основной", first, (name) => { setFirst(name); if (second) apply(name, second); })}
      {row("Полоса", second, (name) => { setSecond(name); if (first) apply(first, name); })}
    </> : row("Цвет", unit.stripe ? "" : unit.baseName ?? "", (name) => { onPick(name); onClose(); })}
    <div className="pin-color-foot">
      <span className="muted">{unit.colorName || "как в наименовании кабеля"}</span>
      <button type="button" className="btn-link" onClick={() => { onPick(null); onClose(); }}>Сбросить цвет</button>
      <button type="button" className="btn-link" onClick={onClose}>Закрыть</button>
    </div>
  </div>;
}

/** Палитра проводов, рука и схема: щелчок по проводу берёт его, щелчок по контакту ставит; щелчок по занятому
 *  контакту берёт его провод — следующий щелчок по контакту того же разъёма меняет их местами (свободный — перенос).
 *  Перетаскивание провода или занятого контакта делает то же. Esc или повторный щелчок — отпустить. */
export function PinoutBoard({ ends, links, units, housings, free, wireOptions, warnings, branches = [], complex = false, onAssign, onUnassign, onSwap, onRecolor, onAutoFill, onBranch, onBranchMode, onRemoveBranch, onCoreColor }: {
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
  /** Ответвления проводов и «Сложная сборка» (ответвление есть только в ней). */
  branches?: Branch[];
  complex?: boolean;
  onBranch?: (from: { epId: string; idx: number; pin: number }, toEpId: string, toPin: number, mode: Branch["mode"]) => void;
  onBranchMode?: (extra: number, mode: Branch["mode"]) => void;
  onRemoveBranch?: (extra: number) => void;
  /** Цвет жилы с палитры: кабель, жила, название цвета (null — сбросить). */
  onCoreColor?: (idx: number, unit: number, color: string | null) => void;
}) {
  const [hand, setHand] = useState<PinHand | null>(null);
  const [fill, setFill] = useState<{ notes: string[]; warnings: string[] } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [palette, setPalette] = useState<string | null>(null);
  const [branch, setBranch] = useState<{ from: PinHand; toEp: string; toPin: number; mode: Branch["mode"] } | null>(null);
  const handUnit = hand ? units.find((u) => u.key === hand.key) ?? null : null;
  const keyOf = (epId: string) => ends.find((end) => end.id === epId)?.key ?? "";

  useEffect(() => {
    if (!hand) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { setHand(null); setBranch(null); } };
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
    const dest = ends.find((end) => end.id === epId), source = ends.find((end) => end.id === from.epId);
    // Провод с контакта — на второй вывод той же стороны или на сторону, где у него уже есть контакт: ответвление.
    if (dest && source && from.pin != null) {
      const second = ends.some((end) => end.id !== epId && end.side === dest.side && end.id in unit.at && unit.at[end.id] != null);
      if (dest.side === source.side || second) {
        if (!complex) { setFill({ notes: [], warnings: ["Ответвление доступно в «Сложной сборке»."] }); return; }
        setBranch({ from, toEp: epId, toPin: pin, mode: "splice" });
        return;
      }
    }
    if (!(epId in unit.at) && !dest?.multi) return; // провод не заведён в этот разъём
    onAssign(epId, unit.idx, unit.unit, pin);
    // Провод ещё без контакта на другом разъёме — остаётся в руке: следующий щелчок — туда.
    const rest = Object.entries(unit.at).some(([id, p]) => id !== epId && p == null);
    setHand(rest ? { key: unit.key } : null);
  }
  function clickCell(epId: string, pin: number) {
    if (suppress.current) return;
    if (hand) return place(hand, epId, pin);
    setBranch(null);
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

  // Только те разъёмы, куда провод идёт: «А1·4 → В1·4 + В2·2» (ответвление — после «+»).
  const extraSet = new Set(branches.map((b) => b.extra));
  const at_ = (u: PinUnit, skip?: string) => ends.filter((e) => e.id in u.at && e.id !== skip).map((e) => `${e.key}·${u.at[e.id] ?? "—"}`);
  const place_ = (u: PinUnit) => {
    const extra = branches.filter((b) => b.main === u.idx).flatMap((b) => {
      const x = units.find((v) => v.idx === b.extra);
      return x ? at_(x, b.mode === "double" ? b.from : undefined) : [];
    });
    return [at_(u).join(" → "), ...extra].filter(Boolean).join(" + ");
  };
  // Сторона с несколькими разъёмами, а у провода там нет контакта — «не назначен» (янтарём).
  const unassigned = (u: PinUnit) => (["A", "B"] as const).filter((side) => {
    const sideEnds = ends.filter((e) => e.side === side);
    return sideEnds.some((e) => e.multi) && !sideEnds.some((e) => e.id in u.at);
  }).map((side) => (side === "A" ? "А" : "В"));
  return <div className={dragging ? "pin-board is-dragging" : "pin-board"}>
    <div className="pin-tools">
      <div className="pin-palette" role="group" aria-label="Провода">
        {units.filter((u) => !extraSet.has(u.idx)).map((u) => {
          const open = Object.values(u.at).some((p) => p == null);
          const free_ = unassigned(u);
          const held = hand?.key === u.key;
          return <span className="pin-chip-wrap" key={u.key}><button type="button" className={["pin-chip-wire", held && "is-held", (open || free_.length > 0) && "is-open"].filter(Boolean).join(" ")} aria-pressed={held}
            title={`${unitText(u)}: ${place_(u) || "не заведён в разъём"}`}
            onClick={() => { if (!suppress.current) { setFill(null); setHand(held && !hand?.epId ? null : { key: u.key }); } }}
            onPointerDown={(event) => grab({ key: u.key }, event)}>
            <span className="pin-swatch is-spacer" aria-hidden="true" /><span className="pin-num">{u.num}</span><span className="pin-chip-name">{u.name || "провод"}{u.colorName ? ` · ${u.colorName}` : ""}</span>
            <small>{[place_(u) || (free_.length ? "" : "без разъёма"), free_.length ? `не назначен: сторона ${free_.join(", ")}` : ""].filter(Boolean).join(" · ")}</small>
          </button>
          <button type="button" className="pin-color-dot" aria-label={`Цвет жилы ${u.num}`} aria-expanded={palette === u.key} title={u.colorName || "выбрать цвет жилы"}
            onClick={() => setPalette(palette === u.key ? null : u.key)}><Swatch color={u.color} stripe={u.stripe} /></button>
          {palette === u.key && <CoreColorPopover unit={u} onClose={() => setPalette(null)} onPick={(color) => onCoreColor?.(u.idx, u.unit, color)} />}
          </span>;
        })}
      </div>
      <button type="button" className="secondary small" onClick={autoFill}>Заполнить автоматически</button>
    </div>
    {handUnit ? <div className="pin-hand" role="status">
      <span className="pin-hand-what"><Swatch color={handUnit.color} stripe={handUnit.stripe} /><span className="pin-num">{handUnit.num}</span> {handUnit.name || "провод"}{handUnit.colorName ? ` · ${handUnit.colorName}` : ""}
        {hand?.epId && hand.pin != null && <span className="muted"> · {keyOf(hand.epId)}, конт. {hand.pin}</span>}</span>
      <span className="muted">{hand?.epId ? `другой контакт ${keyOf(hand.epId)} — поменять местами; другой разъём — добавить ответвление` : "щёлкните контакт"} · Esc — отпустить</span>
      <label className="pin-hand-color">Провод
        <select aria-label="Провод на контакте" value={handUnit.compId} onChange={(event) => onRecolor(handUnit.idx, event.target.value)}>
          {!wireOptions.some((o) => o.id === handUnit.compId) && <option value={handUnit.compId}>{handUnit.name || "—"}</option>}
          {wireOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select></label>
      {hand?.epId && hand.pin != null && <button type="button" className="secondary small"
        onClick={() => { onUnassign(hand.epId!, handUnit.idx, handUnit.unit); setHand(null); }}>Снять с контакта</button>}
      <button type="button" className="btn-link" onClick={() => setHand(null)}>Отпустить</button>
    </div> : <small className="muted pin-hint">Щёлкните провод, затем контакт (или перетащите). Щелчок по занятому контакту — перенести или поменять местами.</small>}
    {handUnit && branch && hand?.epId && hand.pin != null && <div className="pin-branch" role="group" aria-label="Ответвление">
      <span className="pin-hand-what">Ответвление: <Swatch color={handUnit.color} stripe={handUnit.stripe} /><span className="pin-num">{handUnit.num}</span> {keyOf(hand.epId)}, конт. {hand.pin} → {keyOf(branch.toEp)}, конт. {branch.toPin}</span>
      <span className="pin-mode-seg" role="group" aria-label="Как разветвить">
        {(["splice", "double"] as const).map((mode) => <button type="button" key={mode} className={branch.mode === mode ? "pin-mode is-on" : "pin-mode"} aria-pressed={branch.mode === mode}
          onClick={() => setBranch({ ...branch, mode })}>{mode === "splice" ? "сросток" : "двойной обжим"}</button>)}
      </span>
      <button type="button" className="secondary small" onClick={() => {
        onBranch?.({ epId: hand.epId!, idx: handUnit.idx, pin: hand.pin! }, branch.toEp, branch.toPin, branch.mode);
        setBranch(null); setHand(null);
      }}>Добавить ответвление</button>
      <button type="button" className="btn-link" onClick={() => setBranch(null)}>Отмена</button>
    </div>}
    <PinoutDiagram ends={ends} links={links} units={units} housings={housings} free={free} hand={hand} branches={branches}
      onBranchMode={onBranchMode} onRemoveBranch={onRemoveBranch}
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
