import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, ApiError, failureText, type FulfillmentCandidate, type FulfillmentLine, type FulfillmentLot, type FulfillmentRequest } from "./api";
import { matchesWords } from "./DataTable";
import { closedLine, fifoOrder, fifoPick, positionKey } from "./FulfillmentPage";
import { matchText } from "./stockCandidates";

/* Комплектация: «ГРН на все КБ» и «Есть, не заведено» (owner 05.10.2026). One position of a project is closed for every
   КБ at once: the person ticks several lots, the server spreads them over the КБ in natural order, lots by ГРН (FIFO).
   A lot picked here goes to reserve. «Есть, не заведено»: the part is physically there but not yet entered on the
   warehouse — the line passes as closed without a lot. The API of this block is local to the file. */

export type BulkBound = { request_id: string; number: string; kb: string; line_id: string; lot_id: string; grn: string; quantity: string };
export type BulkResult = { bound: BulkBound[]; short: string | number; message?: string };

export const bulkApi = {
  /** available = free for this project + its own reserve, so a re-pick sees what the project already holds. */
  lots: (project: string) => api<{ lots: FulfillmentLot[] }>(`/api/issues/lots?project=${encodeURIComponent(project)}`),
  bind: (project: string, body: { article: string; name: string; lot_ids: string[]; off_book: boolean; off_book_quantity?: string | null; versions: Record<string, number> }) =>
    api<BulkResult>(`/api/issues/projects/${encodeURIComponent(project)}/bind-lots`, { method: "POST", body: JSON.stringify(body) }),
  /** quantity — «сколько есть» below the line's need (the server splits the line); null — the whole need. */
  offBook: (requestId: string, lineId: string, version: number, on: boolean, quantity: string | null = null) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/lines/${lineId}/off-book`, {
      method: on ? "POST" : "DELETE", body: JSON.stringify(on && quantity !== null ? { version, quantity } : { version }),
    }),
  /** «Нет на складе» (owner 12.10.2026): the line stays open as a shortage; a repeat on a marked line changes nothing. */
  absent: (requestId: string, lineId: string, version: number, on: boolean) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/lines/${lineId}/absent`, {
      method: on ? "POST" : "DELETE", body: JSON.stringify({ version }),
    }),
};

/** «Есть, не заведено»: the line is marked, not bound to a lot. */
export function isOffBook(line: FulfillmentLine) {
  return Boolean((line as FulfillmentLine & { off_book?: boolean }).off_book);
}

/** «Нет на складе»: who confirmed the part is absent and when; null — no mark. */
export function absentMark(line: FulfillmentLine): { by: string; at: string } | null {
  const mark = line as FulfillmentLine & { absent_at?: string | null; absent_by?: string | null };
  return mark.absent_at ? { by: mark.absent_by ?? "", at: mark.absent_at } : null;
}

// «Возможно есть»: the tones, legend and wording live in stockCandidates.ts (the project's «Снабжение» uses them too).
export { CANDIDATE_LEGEND, candidateTone, matchText, type CandidateTone } from "./stockCandidates";

export const STALE_TEXT = "Заявки изменились, пока выбирали ГРН. Список обновлён — повторите.";

export type BulkPosition = {
  article: string;
  name: string;
  unit: string;
  /** КБ in natural order; remaining = still not closed (no lot, not marked). */
  kbs: Array<{ request: FulfillmentRequest; label: string; remaining: number }>;
};

const round = (value: number) => Math.round(value * 100) / 100;

/** Client estimate of the server split: КБ in order, lots by ГРН, a short lot is split between КБ. */
export function previewSplit(position: BulkPosition, lots: FulfillmentLot[]) {
  const free = new Map(lots.map((lot) => [lot.id, Number(lot.available)]));
  const ordered = [...lots].sort(fifoOrder);
  return position.kbs.map((kb) => {
    const result = fifoPick(ordered.map((lot) => ({ lot, free: free.get(lot.id) ?? 0 })), kb.remaining);
    for (const pick of result.picks) free.set(pick.lot.id, round((free.get(pick.lot.id) ?? 0) - pick.qty));
    return { kb, picks: result.picks, short: round(result.short) };
  });
}

/** «Назначено: КБ1 — ГРН … N шт; …; не хватает M». */
export function bulkBanner(result: BulkResult, unit: string, offBook: boolean) {
  if (offBook) return result.message || "Отмечено «есть, не заведено»";
  const byKb = new Map<string, string[]>();
  for (const item of result.bound) byKb.set(item.kb, [...(byKb.get(item.kb) ?? []), `ГРН ${item.grn} ${round(Number(item.quantity))} ${unit}`]);
  const parts = [...byKb].map(([kb, items]) => `${kb} — ${items.join(", ")}`);
  const short = round(Number(result.short) || 0);
  if (!parts.length) return result.message || "Ничего не назначено";
  return `Назначено: ${parts.join("; ")}${short > 0 ? `; не хватает ${short}` : ""}`;
}

/** The search box of a lot picker opens filled with the position's name (its article when the name is empty): the list is
    already narrowed to that item, the person erases or edits it. «Возможно есть» lots stay on the list whatever it says. */
export const searchSeed = (name: string, article: string) => name.trim() || article.trim();

/** «Нет на складе» for a position: every still open, unmarked line of it in the КБ of the position. */
export function absentTargets(position: BulkPosition) {
  const key = positionKey(position.article, position.name);
  return position.kbs.flatMap((kb) => (kb.request.lines ?? [])
    .filter((line) => positionKey(line.article, line.name) === key && !closedLine(line) && absentMark(line) === null)
    .map((line) => ({ request: kb.request, line })));
}

/** One «Нет на складе» call per КБ line, the same endpoint as the per-КБ button; a request's version moves on after
    each call, so the next line of the same request takes the version the server returned. */
export async function absentPosition(position: BulkPosition) {
  const versions = new Map<string, number>();
  const targets = absentTargets(position);
  for (const { request, line } of targets) {
    const result = await bulkApi.absent(request.id, line.id, versions.get(request.id) ?? request.version, true);
    versions.set(request.id, result.request?.version ?? (versions.get(request.id) ?? request.version) + 1);
  }
  return { message: `Отмечено «нет на складе»: ${position.name || position.article}, строк ${targets.length}` };
}

/** One call of (a) for a position; a 409 says the requests moved on. Result is shaped for the page's mutate. */
export async function bindPosition(project: string, position: BulkPosition, lotIds: string[], offBook: boolean, offBookQuantity: string | null = null) {
  const versions = Object.fromEntries(position.kbs.map((kb) => [kb.request.id, kb.request.version]));
  try {
    const body = offBook
      ? { article: position.article, name: position.name, lot_ids: [], off_book: true, off_book_quantity: offBookQuantity, versions }
      : { article: position.article, name: position.name, lot_ids: lotIds, off_book: false, versions };
    const result = await bulkApi.bind(project, body);
    return { message: bulkBanner(result, position.unit, offBook) };
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) throw new ApiError(STALE_TEXT, 409, reason.detail);
    throw reason;
  }
}

/** Detail row under a position of «Весь заказ». `run` goes through the page's mutate (busy, notice, reload). */
export function BulkLotsPanel({ project, position, versionKey, busy, run, onClose, suggested = [] }: {
  project: string;
  position: BulkPosition;
  /** «Возможно есть»: the lots the rules offered for this position, shown first and marked. */
  suggested?: FulfillmentCandidate[];
  versionKey: string;
  busy: boolean;
  run: (operation: () => Promise<{ message: string }>) => void;
  onClose: () => void;
}) {
  const [lots, setLots] = useState<FulfillmentLot[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState(searchSeed(position.name, position.article));
  const [chosen, setChosen] = useState<string[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setError("");
    bulkApi.lots(project).then((result) => { if (alive) setLots(result.lots); })
      .catch((reason) => { if (alive) setError(failureText(reason, "Не удалось загрузить партии")); });
    return () => { alive = false; };
  }, [project, versionKey, attempt]);

  const offered = useMemo(() => new Map(suggested.map((candidate, index) => [candidate.lot_id, { candidate, index }])), [suggested]);
  const found = (lots ?? [])
    .filter((lot) => chosen.includes(lot.id) || offered.has(lot.id) || matchesWords([lot.article, lot.name, lot.grn, lot.package, lot.storage_location, lot.warehouse, lot.unit], query))
    .sort((a, b) => (offered.get(a.id)?.index ?? 1e9) - (offered.get(b.id)?.index ?? 1e9));
  const selected = useMemo(() => (lots ?? []).filter((lot) => chosen.includes(lot.id)), [lots, chosen]);
  const preview = useMemo(() => previewSplit(position, selected), [position, selected]);
  const total = round(position.kbs.reduce((sum, kb) => sum + kb.remaining, 0));
  const anyPick = preview.some((row) => row.picks.length > 0);
  const toggle = (id: string) => setChosen((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);

  return <div className="lot-picker bulk-lots" role="group" aria-label={`ГРН на все КБ: ${position.name}`}>
    <p className="page-sub">Нужно ещё {total} {position.unit} по КБ: {position.kbs.map((kb) => `${kb.label} ${round(kb.remaining)}`).join(", ")}. Отмеченные ГРН уйдут в резерв проекта.</p>
    {error && <div className="error-banner" role="alert">{error} <button type="button" className="secondary small" onClick={() => setAttempt((value) => value + 1)}>Повторить</button></div>}
    {!error && lots === null && <div className="empty">Загрузка…</div>}
    {lots !== null && <>
      <input className="search" type="search" aria-label={`Поиск партий для ${position.name}`} placeholder="поиск: вилка usb · 3B4 · ГРН-2026 …" value={query} onChange={(event) => setQuery(event.target.value)} />
      {!found.length && <div className="empty">{lots.length ? `По запросу ничего нет. Сотри слова — покажется весь склад (${lots.length} строк).` : "На складе нет ни одной строки с остатком."}</div>}
      {found.length > 0 && <table className="grid wide"><thead><tr><th /><th>Наименование</th><th>ГРН</th><th className="num">Доступно</th></tr></thead><tbody>
        {found.slice(0, 200).map((lot) => <tr key={lot.id}>
          <td><input type="checkbox" aria-label={`ГРН ${lot.grn}`} checked={chosen.includes(lot.id)} onChange={() => toggle(lot.id)} /></td>
          <td className="text"><strong>{lot.article || "—"}</strong> <span>{lot.name}</span><small>{lot.storage_location || "без места"}</small>{offered.has(lot.id) && <small className="cand-tag">возможно есть · {matchText(offered.get(lot.id)!.candidate)}</small>}</td>
          <td>{lot.grn}<small>{lot.warehouse}{lot.package ? ` · пакет ${lot.package}` : ""}</small></td>
          <td className="num">{lot.available} {lot.unit}</td>
        </tr>)}
      </tbody></table>}
      {found.length > 200 && <p className="page-sub">Показаны первые 200 из {found.length} — уточни поиск.</p>}
      {selected.length > 0 && <table className="grid wide" aria-label="Предварительная раскладка"><caption>Предварительно, окончательно разложит сервер</caption>
        <thead><tr><th>КБ</th><th className="num">Нужно</th><th>ГРН</th><th className="num">Не хватает</th></tr></thead><tbody>
          {preview.map((row) => <tr key={row.kb.request.id}>
            <td>{row.kb.label}</td><td className="num">{round(row.kb.remaining)} {position.unit}</td>
            <td>{row.picks.map((pick) => `${pick.lot.grn} ${pick.qty} ${position.unit}`).join("; ") || "—"}</td>
            <td className="num">{row.short > 0 ? row.short : "—"}</td>
          </tr>)}
        </tbody></table>}
    </>}
    <div className="fulfillment-actions">
      <button type="button" className="primary" disabled={busy || !anyPick} onClick={() => run(() => bindPosition(project, position, chosen, false))}>Назначить ГРН на все КБ</button>
      <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
    </div>
  </div>;
}

/** A quantity as the input shows it: up to 6 decimals, no trailing zeros. */
export const qtyText = (value: number) => String(Math.round(value * 1e6) / 1e6);

/** «Сколько есть?»: the error for what was typed, "" — fine. A comma is a decimal point; шт of a whole need — whole. */
export function offBookQtyError(text: string, max: number, unit: string) {
  const raw = text.trim().replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(raw) || Number(raw) <= 0) return "Укажи число больше нуля";
  const value = Number(raw);
  if (value > Math.round(max * 1e6) / 1e6) return `Больше, чем ждут: не больше ${qtyText(max)} ${unit}`.trimEnd();
  if (/^шт/i.test(unit.trim()) && Number.isInteger(max) && !Number.isInteger(value)) return "В штуках — целое число";
  return "";
}

/* «Есть, не заведено» → «Сколько есть?» (owner 14.10.2026). A small popover under the button: the default is everything
   that still waits, Enter or «Отметить» confirms, Esc, «Отмена» or a click outside closes. Less than waits — the server
   marks that much (КБ in order) and splits the line where it runs out; the rest keeps waiting. The default untouched
   sends null (the whole wait), so a need with more decimals than the screen shows is never left a crumb short. */
export function OffBookQtyPopover({ anchor, name, unit, max, scope, busy, onConfirm, onCancel }: {
  anchor: HTMLElement | null;
  name: string;
  unit: string;
  /** Everything that still waits: the default and the most that can be marked. */
  max: number;
  /** Where it waits, e.g. «по 3 КБ»; empty for one line. */
  scope?: string;
  busy: boolean;
  /** null — the whole wait; otherwise the quantity as the server takes it ("2.5"). */
  onConfirm: (quantity: string | null) => void;
  onCancel: () => void;
}) {
  const initial = qtyText(max);
  const [text, setText] = useState(initial);
  const box = useRef<HTMLFormElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);
  const error = offBookQtyError(text, max, unit);
  const hintId = useId();

  useLayoutEffect(() => {
    const locate = () => {
      if (!anchor || !box.current) return;
      const at = anchor.getBoundingClientRect();
      const width = box.current.offsetWidth || 300;
      const height = box.current.offsetHeight || 160;
      const left = Math.max(8, Math.min(at.right - width, window.innerWidth - width - 8));
      const below = at.bottom + 4;
      setPlace({ left, top: below + height > window.innerHeight - 8 && at.top - height - 4 > 8 ? at.top - height - 4 : below });
    };
    locate();
    window.addEventListener("resize", locate);
    window.addEventListener("scroll", locate, true);
    return () => { window.removeEventListener("resize", locate); window.removeEventListener("scroll", locate, true); };
  }, [anchor]);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (box.current?.contains(target) || anchor?.contains(target)) return;
      onCancel();
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [anchor, onCancel]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy || error) return;
    const raw = text.trim().replace(",", ".");
    onConfirm(raw === initial ? null : String(Number(raw)));
  };

  return <form ref={box} className="offbook-pop" role="dialog" aria-label={`Сколько есть: ${name}`} noValidate
    style={place ? { top: place.top, left: place.left } : { visibility: "hidden" }}
    onSubmit={submit} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onCancel(); } }}>
    <div className="offbook-pop-head"><strong>Сколько есть?</strong><span>{name}</span></div>
    <label className="offbook-pop-qty">
      <input ref={input} type="text" inputMode="decimal" autoComplete="off" aria-label="Сколько есть" aria-invalid={Boolean(error)}
        aria-describedby={hintId} value={text} onChange={(event) => setText(event.target.value)} />
      <span>{unit}</span>
    </label>
    <small id={hintId} className={error ? "offbook-pop-error" : undefined} role={error ? "alert" : undefined}>
      {error || `Ждут ${initial} ${unit}${scope ? ` ${scope}` : ""}. Меньше — остальное останется в ожидании поставки.`}
    </small>
    <div className="offbook-pop-acts">
      <button type="submit" className="primary small" disabled={busy || Boolean(error)}>Отметить</button>
      <button type="button" className="secondary small" onClick={onCancel}>Отмена</button>
    </div>
  </form>;
}
