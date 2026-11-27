import { QualityChip } from "./GrnFields";
import { useRef, useState } from "react";
import { ApiError, failureText } from "./api";
import { PageWindow } from "./PageWindow";
import { intakeApi, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
import { isoDay } from "./Gantt";
import { formatDay, formatNumber } from "./format";
import { kbKey } from "./intakeStages";

/* Окно приёмки на склад (владелец 22.10): число в колонке «Можно принять» открывает его; та же форма — для
   «Принять всё» проекта. Количество по умолчанию — всё, что можно принять; место — своё у каждой КБ (пусто — прежнее).
   Сверх сданного цехом по заданиям — только через окно подтверждения (20.11: 708К. КБ20 приняли 14 «вне заданий»
   при одной сданной); «Принять досрочно» количество не подставляет — его вписывают. */

const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;
export const keyOf = (row: Pick<IntakeRow, "project" | "item_index">) => `${row.project}\u0000${row.item_index}`;
export const leftOf = (row: IntakeRow) => round2(Math.max(num(row.needed) - num(row.accepted), 0));
/** Сдано цехом по заданиям и ещё не принято (N) — тот же счёт, что у сервера (`handed_over`). */
export const handedOf = (row: IntakeRow) => round2(Math.max(num(row.delivered) - num(row.accepted), 0));

/** Строка сверх сданного: N, M и есть ли у КБ задания в ERP (нет — N = 0 не потому, что цех ничего не сдал). */
type Beyond = { row: IntakeRow; handed: number; quantity: number; hasTasks: boolean };
/** 409 сервера «подтвердите приёмку сверх сданного»: строки с N сервера (`finished_goods_api._guarded`). */
type ServerBeyond = { code?: string; rows?: Array<{ project: string; item_index: string; handed_over: string; quantity: string; has_tasks: boolean }> };

/** `quantities` — сколько предложить по умолчанию (штуки строки, с которой открыли окно); предел — всё, что можно принять
 *  по КБ. `blank` — поле пустое: «Принять досрочно» (сверх сданного) не подставляет остаток до тиража. */
export function IntakeWindow({ rows, quantities, blank = false, close, onDone }: {
  rows: IntakeRow[]; quantities?: Record<string, number | null>; blank?: boolean; close: () => void; onDone: (pieces: number) => Promise<void> | void;
}) {
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(rows.map((row) => [keyOf(row),
    blank || quantities?.[keyOf(row)] === null ? "" : quantities?.[keyOf(row)] !== undefined ? String(quantities[keyOf(row)]) : row.remaining])));
  const [places, setPlaces] = useState<Record<string, string>>({});
  const [day, setDay] = useState(() => isoDay(new Date()));
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<Beyond[] | null>(null);
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже сделанную приёмку, а не примет второй раз.
  // Отказ «подтвердите сверх сданного» ничего не записал — повтор с подтверждением идёт с новым ключом.
  const idempotencyKey = useRef<string>(crypto.randomUUID());
  const confirmedKeys = useRef<Set<string>>(new Set());
  const total = round2(rows.reduce((sum, row) => sum + num(qty[keyOf(row)]), 0));
  const single = rows.length === 1;

  function submit() {
    if (busy) return;
    const bad: Record<string, string> = {};
    for (const row of rows) {
      const value = num(qty[keyOf(row)]);
      if (!(value > 0)) bad[keyOf(row)] = "укажите количество больше 0";
      else if (value > num(row.remaining)) bad[keyOf(row)] = `больше сданного: можно ${row.remaining}`;
    }
    setProblems(bad);
    if (Object.keys(bad).length) return;
    const beyond = rows.map((row) => ({ row, handed: handedOf(row), quantity: round2(num(qty[keyOf(row)])), hasTasks: row.tasks.length > 0 }))
      .filter((item) => item.quantity > item.handed);
    if (beyond.length) setConfirming(beyond);
    else void send(new Set());
  }

  async function send(confirmed: Set<string>) {
    setConfirming(null);
    confirmedKeys.current = confirmed;
    const lines = rows.map((row) => {
      const place = (places[keyOf(row)] ?? "").trim();
      return { project: row.project, item_index: row.item_index, quantity: String(qty[keyOf(row)]).replace(",", "."),
        ...(place ? { location: place } : {}), ...(confirmed.has(keyOf(row)) ? { confirm_beyond_tasks: true } : {}) };
    });
    setBusy(true); setError("");
    try {
      await intakeApi.accept(lines, day || null, "", idempotencyKey.current);
      await onDone(total);
    } catch (reason) {
      // Сервер насчитал сданного меньше, чем видел экран: спросить о его строках с его N и повторить с новым ключом.
      const server = reason instanceof ApiError && reason.status === 409 ? reason.body as ServerBeyond | undefined : undefined;
      const asked = server?.code === "beyond_tasks" ? (server.rows ?? []).flatMap((item): Beyond[] => {
        const row = rows.find((candidate) => kbKey(candidate) === kbKey(item));
        return row ? [{ row, handed: num(item.handed_over), quantity: num(item.quantity), hasTasks: item.has_tasks }] : [];
      }) : [];
      if (asked.length) {
        idempotencyKey.current = crypto.randomUUID();
        setError(""); setProblems({});
        setConfirming(asked);
        return;
      }
      // Пакет — все строки или ни одной: сервер называет строку («591К.3 КБ1: сдано цехом …»), показываем её у строки.
      const text = reason instanceof Error ? reason.message : "";
      const hit = reason instanceof ApiError && [409, 422].includes(reason.status) ? rows.find((row) => text.startsWith(`${row.project} ${row.item_index}:`)) : undefined;
      if (hit) {
        const why = text.slice(`${hit.project} ${hit.item_index}:`.length).trim();
        setProblems({ [keyOf(hit)]: why });
        setError(`Ничего не принято: ${hit.project} ${hit.item_index} — ${why}`);
      } else setError(failureText(reason, "Приёмка не выполнена"));
    } finally { setBusy(false); }
  }

  return <PageWindow label={single ? `Приёмка ${rows[0].project} ${rows[0].item_index}` : `Принять всё ${rows[0].project}`} close={close} width={960}>
    <section className="panel intake-window">
      <header className="page-head"><h2>{single ? `Принять на склад · ${rows[0].project} ${rows[0].item_index}` : `Принять всё · ${rows[0].project}`}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <table className="grid compact intake-window-table">
        <thead><tr><th>КБ</th><th className="grow">Наименование</th><th className="num">Заказано</th><th className="num">Принято</th><th className="num">Сдано, не принято</th><th className="num">Остаток</th><th className="num">Принять, шт</th><th>Место хранения</th></tr></thead>
        <tbody>{rows.map((row) => {
          const key = keyOf(row);
          return <tr key={key}>
            <td><strong>{row.item_index}</strong></td>
            <td className="grow"><span className="intake-clip intake-name" title={row.name}>{row.name}</span></td>
            <td className="num">{row.needed}</td><td className="num">{row.accepted}</td>
            <td className="num" title="Сдано цехом по заданиям и ещё не принято">{handedOf(row)}</td><td className="num">{leftOf(row)}</td>
            <td className="num"><input className={problems[key] ? "ship-qty invalid" : "ship-qty"} type="number" min="0" step="any" max={row.remaining}
              aria-label={`Сколько принять ${row.project} ${row.item_index}`} aria-invalid={problems[key] ? true : undefined}
              value={qty[key] ?? ""} onChange={(event) => setQty({ ...qty, [key]: event.target.value })} />
              {problems[key] && <small className="tone-danger ship-row-error" role="alert">{problems[key]}</small>}</td>
            <td><input className="intake-place" type="text" autoComplete="off" maxLength={255} aria-label={`Место хранения ${row.project} ${row.item_index}`}
              placeholder={row.location || "ячейка"} value={places[key] ?? ""} onChange={(event) => setPlaces({ ...places, [key]: event.target.value })} /></td>
          </tr>;
        })}</tbody>
      </table>
      <div className="intake-window-actions">
        <label className="ship-inline">Дата приёмки<input type="date" aria-label="Дата приёмки" value={day} max={isoDay(new Date())} onChange={(event) => setDay(event.target.value)} /></label>
        <button type="button" className="primary" disabled={busy} onClick={submit}>{busy ? "Принимаем…" : `Принять на склад · ${total} шт`}</button>
      </div>
    </section>
    {confirming && <BeyondConfirm items={confirming} total={total} cancel={() => setConfirming(null)}
      confirm={() => void send(new Set([...confirmedKeys.current, ...confirming.map((item) => keyOf(item.row))]))} />}
  </PageWindow>;
}

/** Подтверждение приёмки сверх сданного цехом по заданиям: по строкам — сдано N, принимаете M, сверх M−N. */
function BeyondConfirm({ items, total, cancel, confirm }: { items: Beyond[]; total: number; cancel: () => void; confirm: () => void }) {
  const beyond = round2(items.reduce((sum, item) => sum + item.quantity - item.handed, 0));
  const title = items.length > 1 ? `Сверх сданного цехом — ${items.length} КБ`
    : items[0].hasTasks ? `Цех сдал по заданиям ${formatNumber(items[0].handed)}, принимаете ${formatNumber(items[0].quantity)}`
      : `У КБ нет заданий в ERP — принимаете ${formatNumber(items[0].quantity)}`;
  return <PageWindow label={title} close={cancel} width={640}>
    <section className="panel intake-window intake-confirm">
      <header className="page-head"><h2>{title}</h2></header>
      <table className="grid compact intake-window-table">
        <thead><tr><th>КБ</th><th className="grow">Наименование</th><th className="num">Сдано по заданиям</th><th className="num">Принимаете</th><th className="num">Сверх сданного</th></tr></thead>
        <tbody>{items.map(({ row, handed, quantity, hasTasks }) => <tr key={keyOf(row)}>
          <td><strong>{row.item_index}</strong></td>
          <td className="grow"><span className="intake-clip intake-name" title={row.name}>{row.name}</span></td>
          <td className="num">{hasTasks ? formatNumber(handed) : <span className="muted">нет заданий</span>}</td><td className="num">{formatNumber(quantity)}</td>
          <td className="num tone-warn">{formatNumber(round2(quantity - handed))}</td>
        </tr>)}</tbody>
      </table>
      <div className="intake-window-actions">
        <button type="button" className="secondary" onClick={cancel}>Отмена</button>
        <button type="button" className="primary" onClick={confirm}>{`Принять ${formatNumber(total)}, из них сверх сданного ${formatNumber(beyond)}`}</button>
      </div>
    </section>
  </PageWindow>;
}

/** «Откатить приёмку» акта (руководитель): сводка акта, причина обязательна; штуки акта снова «В производстве».
 *  `onStock` — сколько штук акта ещё лежит на складе (не набрано в отправки). */
export function RollbackActWindow({ row, act, onStock, close, onDone }: {
  row: IntakeRow; act: IntakeAct; onStock: number; close: () => void; onDone: (message: string) => Promise<void> | void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const acts = row.acts ?? [];
  const later = acts.slice(acts.findIndex((item) => item.grn === act.grn) + 1).find((item) => num(item.quantity) > 0);
  const taken = round2(num(act.quantity) - onStock);
  const blocked = later ? `После этого акта есть приёмка ${later.grn} — сначала откатите её`
    : taken > 0 ? `Из акта уже набрано в отправки ${formatNumber(taken)} шт — сначала верните их на склад` : "";

  async function submit() {
    if (busy || blocked || !reason.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await intakeApi.rollbackAct(act.grn, reason.trim(), idempotencyKey);
      await onDone(`Приёмка откатена: ${result.grn} · ${result.quantity} шт снова в производстве`);
    } catch (failure) { setError(failureText(failure, "Откат приёмки не выполнен")); }
    finally { setBusy(false); }
  }

  return <PageWindow label={`Акт приёмки ${act.grn}`} close={close} width={640}>
    <section className="panel intake-window intake-act">
      <header className="page-head"><h2>{`Акт приёмки ${act.grn}`}</h2><QualityChip passport={act} quietLegacy />
        <button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <dl className="intake-act-facts">
        <div><dt>КБ</dt><dd>{row.project} {row.item_index}</dd></div>
        <div className="wide"><dt>Наименование</dt><dd>{row.name}</dd></div>
        <div><dt>Принято по акту</dt><dd>{formatNumber(num(act.quantity))} {row.unit}</dd></div>
        <div><dt>Дата приёмки</dt><dd>{formatDay(act.received_at)}</dd></div>
        <div><dt>Место хранения</dt><dd>{act.location || "—"}</dd></div>
        <div><dt>Принял</dt><dd>{act.actor || "—"}</dd></div>
        <div><dt>Сдано цехом по заданиям</dt><dd>{formatNumber(num(row.delivered))} {row.unit}</dd></div>
      </dl>
      {blocked && <div className="warning-inline" role="status">{blocked}</div>}
      <label className="intake-act-reason">Причина<textarea aria-label={`Причина отката ${act.grn}`} required rows={2} maxLength={2000}
        placeholder="например: приняли больше, чем сдал цех" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <div className="intake-window-actions">
        <button type="button" className="secondary" onClick={close}>Отмена</button>
        <button type="button" className="danger" disabled={busy || Boolean(blocked) || !reason.trim()} onClick={() => void submit()}>
          {busy ? "Откатываем…" : `Откатить приёмку · ${formatNumber(num(act.quantity))} ${row.unit}`}</button>
      </div>
    </section>
  </PageWindow>;
}
