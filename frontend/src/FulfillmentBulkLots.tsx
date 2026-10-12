import { useEffect, useMemo, useState } from "react";
import { api, ApiError, failureText, type FulfillmentLine, type FulfillmentLot, type FulfillmentRequest } from "./api";
import { matchesWords } from "./DataTable";
import { fifoOrder, fifoPick } from "./FulfillmentPage";

/* Комплектация: «ГРН на все КБ» и «Есть, не заведено» (owner 05.10.2026). One position of a project is closed for every
   КБ at once: the person ticks several lots, the server spreads them over the КБ in natural order, lots by ГРН (FIFO).
   A lot picked here goes to reserve. «Есть, не заведено»: the part is physically there but not yet entered on the
   warehouse — the line passes as closed without a lot. The API of this block is local to the file. */

export type BulkBound = { request_id: string; number: string; kb: string; line_id: string; lot_id: string; grn: string; quantity: string };
export type BulkResult = { bound: BulkBound[]; short: string | number; message?: string };

export const bulkApi = {
  /** available = free for this project + its own reserve, so a re-pick sees what the project already holds. */
  lots: (project: string) => api<{ lots: FulfillmentLot[] }>(`/api/issues/lots?project=${encodeURIComponent(project)}`),
  bind: (project: string, body: { article: string; name: string; lot_ids: string[]; off_book: boolean; versions: Record<string, number> }) =>
    api<BulkResult>(`/api/issues/projects/${encodeURIComponent(project)}/bind-lots`, { method: "POST", body: JSON.stringify(body) }),
  offBook: (requestId: string, lineId: string, version: number, on: boolean) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/lines/${lineId}/off-book`, {
      method: on ? "POST" : "DELETE", body: JSON.stringify({ version }),
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

/** One call of (a) for a position; a 409 says the requests moved on. Result is shaped for the page's mutate. */
export async function bindPosition(project: string, position: BulkPosition, lotIds: string[], offBook: boolean) {
  const versions = Object.fromEntries(position.kbs.map((kb) => [kb.request.id, kb.request.version]));
  try {
    const result = await bulkApi.bind(project, { article: position.article, name: position.name, lot_ids: offBook ? [] : lotIds, off_book: offBook, versions });
    return { message: bulkBanner(result, position.unit, offBook) };
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) throw new ApiError(STALE_TEXT, 409, reason.detail);
    throw reason;
  }
}

/** Detail row under a position of «Весь заказ». `run` goes through the page's mutate (busy, notice, reload). */
export function BulkLotsPanel({ project, position, versionKey, busy, run, onClose }: {
  project: string;
  position: BulkPosition;
  versionKey: string;
  busy: boolean;
  run: (operation: () => Promise<{ message: string }>) => void;
  onClose: () => void;
}) {
  const [lots, setLots] = useState<FulfillmentLot[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState(position.name);
  const [chosen, setChosen] = useState<string[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setError("");
    bulkApi.lots(project).then((result) => { if (alive) setLots(result.lots); })
      .catch((reason) => { if (alive) setError(failureText(reason, "Не удалось загрузить партии")); });
    return () => { alive = false; };
  }, [project, versionKey, attempt]);

  const found = (lots ?? []).filter((lot) => chosen.includes(lot.id) || matchesWords([lot.article, lot.name, lot.grn, lot.package, lot.storage_location, lot.warehouse, lot.unit], query));
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
          <td className="text"><strong>{lot.article || "—"}</strong> <span>{lot.name}</span><small>{lot.storage_location || "без места"}</small></td>
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
