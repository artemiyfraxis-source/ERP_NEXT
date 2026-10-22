import { useState } from "react";
import { ApiError, failureText } from "./api";
import { PageWindow } from "./PageWindow";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
import { isoDay } from "./Gantt";

/* Окно приёмки на склад (владелец 22.10): число в колонке «Можно принять» открывает его; та же форма — для
   «Принять всё» проекта. Количество по умолчанию — всё, что можно принять; место — своё у каждой КБ (пусто — прежнее). */

const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;
const keyOf = (row: IntakeRow) => `${row.project}\u0000${row.item_index}`;
export const leftOf = (row: IntakeRow) => round2(Math.max(num(row.needed) - num(row.accepted), 0));

export function IntakeWindow({ rows, close, onDone }: { rows: IntakeRow[]; close: () => void; onDone: (pieces: number) => Promise<void> | void }) {
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(rows.map((row) => [keyOf(row), row.remaining])));
  const [places, setPlaces] = useState<Record<string, string>>({});
  const [day, setDay] = useState(() => isoDay(new Date()));
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже сделанную приёмку, а не примет второй раз.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const total = round2(rows.reduce((sum, row) => sum + num(qty[keyOf(row)]), 0));
  const single = rows.length === 1;

  async function submit() {
    if (busy) return;
    const bad: Record<string, string> = {};
    for (const row of rows) {
      const value = num(qty[keyOf(row)]);
      if (!(value > 0)) bad[keyOf(row)] = "укажите количество больше 0";
      else if (value > num(row.remaining)) bad[keyOf(row)] = `больше сданного: можно ${row.remaining}`;
    }
    setProblems(bad);
    if (Object.keys(bad).length) return;
    const lines = rows.map((row) => {
      const place = (places[keyOf(row)] ?? "").trim();
      return { project: row.project, item_index: row.item_index, quantity: String(qty[keyOf(row)]).replace(",", "."), ...(place ? { location: place } : {}) };
    });
    setBusy(true); setError("");
    try {
      await intakeApi.accept(lines, day || null, "", idempotencyKey);
      await onDone(total);
    } catch (reason) {
      // Пакет — все строки или ни одной: сервер называет строку («591К.3 КБ1: сдано цехом …»), показываем её у строки.
      const text = reason instanceof Error ? reason.message : "";
      const hit = reason instanceof ApiError && reason.status === 422 ? rows.find((row) => text.startsWith(`${row.project} ${row.item_index}:`)) : undefined;
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
        <thead><tr><th>КБ</th><th className="grow">Наименование</th><th className="num">Заказано</th><th className="num">Принято</th><th className="num">Остаток</th><th className="num">Принять, шт</th><th>Место хранения</th></tr></thead>
        <tbody>{rows.map((row) => {
          const key = keyOf(row);
          return <tr key={key}>
            <td><strong>{row.item_index}</strong></td>
            <td className="grow"><span className="intake-clip intake-name" title={row.name}>{row.name}</span></td>
            <td className="num">{row.needed}</td><td className="num">{row.accepted}</td><td className="num">{leftOf(row)}</td>
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
        <button type="button" className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "Принимаем…" : `Принять на склад · ${total} шт`}</button>
      </div>
    </section>
  </PageWindow>;
}
