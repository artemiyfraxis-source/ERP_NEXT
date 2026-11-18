import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  failureText, instrumentMatingsApi, type InstrumentMatingEnd, type InstrumentMatingEvent,
  type InstrumentMatingsSnapshot, type MatingStatus,
} from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDateTime, formatNumber } from "./format";

export const matingStatusLabels: Record<MatingStatus, string> = {
  unknown: "Ресурс не указан", ok: "Ресурс в норме", warning: "Ресурс почти исчерпан", exhausted: "Ресурс исчерпан — заменить",
};
export const matingTone = (status: MatingStatus) => status === "exhausted" ? "tone-danger" : status === "warning" ? "tone-warn" : "";
const kindLabels: Record<InstrumentMatingEvent["kind"], string> = {
  measurement: "Измерение", calibration: "Калибровка", manual: "Вручную", initial: "Начальное значение", correction: "Исправление",
};
const historyColumns: DataColumn<InstrumentMatingEvent>[] = [
  { key: "created_at", title: "Дата", value: (event) => event.created_at, render: (event) => formatDateTime(event.created_at), width: 148 },
  { key: "end", title: "Сторона", value: (event) => event.end, width: 68 },
  { key: "delta", title: "Изменение", value: (event) => event.delta, numeric: true, width: 100,
    render: (event) => `${event.delta > 0 ? "+" : ""}${formatNumber(event.delta)}` },
  { key: "kind", title: "Действие", value: (event) => kindLabels[event.kind], width: 152 },
  { key: "note", title: "Примечание", value: (event) => event.note, grow: true, minWidth: 160 },
  { key: "actor", title: "Кто", value: (event) => event.actor, width: 128 },
];
type Adjustment = { end: InstrumentMatingEnd["end"]; kind: "initial" | "correction"; value: string; note: string };

export function InstrumentMatings({ instrumentId, locked, refreshKey, onChanged, onDirty, onBusy }: {
  instrumentId: string | undefined; locked: boolean; refreshKey: number; onChanged: () => Promise<void>;
  onDirty: (value: boolean) => void; onBusy: (value: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<InstrumentMatingsSnapshot | null>(null);
  const [loading, setLoading] = useState(Boolean(instrumentId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [adjustment, setAdjustment] = useState<Adjustment | null>(null);
  const inFlight = useRef(false);
  const valueInput = useRef<HTMLInputElement>(null);
  const noteInput = useRef<HTMLInputElement>(null);
  const actionButtons = useRef<Partial<Record<string, HTMLButtonElement | null>>>({});

  useEffect(() => { onDirty(Boolean(adjustment && (adjustment.value || adjustment.note))); }, [adjustment, onDirty]);
  useEffect(() => {
    if (!instrumentId) return;
    let active = true;
    setLoading(true); setError("");
    instrumentMatingsApi.get(instrumentId).then((result) => {
      if (!Array.isArray(result.ends) || !Array.isArray(result.events)) throw new Error("Сведения о сочленениях не получены");
      if (active) setSnapshot(result);
    }).catch((reason) => { if (active) setError(failureText(reason, "Не удалось загрузить сочленения")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instrumentId, refreshKey, attempt]);

  function closeAdjustment() {
    const key = adjustment ? `${adjustment.end}-${adjustment.kind}` : "";
    setAdjustment(null);
    actionButtons.current[key]?.focus();
  }
  function openAdjustment(end: InstrumentMatingEnd["end"], kind: Adjustment["kind"]) {
    if (adjustment && (adjustment.value || adjustment.note) && !window.confirm("Заменить несохранённое исправление?")) return;
    setAdjustment({ end, kind, value: "", note: "" }); setError(""); setNotice("");
    // Focus follows an explicit action; opening the card itself never steals focus.
    requestAnimationFrame(() => valueInput.current?.focus());
  }
  async function append(end: InstrumentMatingEnd["end"], delta: number, kind: "manual" | Adjustment["kind"], note = "") {
    if (!instrumentId || locked || loading || inFlight.current) return;
    inFlight.current = true; setBusy(true); onBusy(true); setError(""); setNotice("");
    try {
      const result = await instrumentMatingsApi.append(instrumentId, { end, delta, kind, note });
      setSnapshot(result); setAdjustment(null);
      setNotice(`Сочленения стороны ${end} записаны`);
      // The append is committed already: a registry refresh failure must not invite a duplicate write.
      try { await onChanged(); }
      catch (reason) { setError(failureText(reason, "Сочленения записаны, но реестр не обновлён — обновите страницу")); }
    } catch (reason) { setError(failureText(reason, "Не удалось записать сочленения")); }
    finally { inFlight.current = false; setBusy(false); onBusy(false); }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!adjustment) return;
    const delta = Number(adjustment.value);
    if (!Number.isSafeInteger(delta) || !delta || (adjustment.kind === "initial" && delta < 0)) {
      setError("Укажите целое ненулевое число сочленений"); valueInput.current?.focus(); return;
    }
    if (!adjustment.note.trim()) { setError("Укажите причину или источник начального значения"); noteInput.current?.focus(); return; }
    const current = snapshot?.ends.find((end) => end.end === adjustment.end);
    if (current && current.total + delta < 0) { setError("Сумма сочленений не может быть отрицательной"); valueInput.current?.focus(); return; }
    void append(adjustment.end, delta, adjustment.kind, adjustment.note.trim());
  }

  return <section className="instrument-matings" aria-label="Сочленения" aria-busy={loading || busy}>
    <h3>Сочленения</h3>
    {!instrumentId && <p className="muted-text">Сохраните прибор, чтобы вести учёт сочленений.</p>}
    {loading && <p className="empty">Загрузка сочленений…</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {error && instrumentId && !loading && <button type="button" className="secondary small" disabled={locked || busy}
      onClick={() => setAttempt((value) => value + 1)}>Обновить сочленения</button>}
    {snapshot && !loading && <>
      <div className="instrument-mating-ends">
        {snapshot.ends.map((end) => <div className="instrument-mating-end" key={end.end}>
          <div className="instrument-mating-count">
            <strong className={matingTone(end.status)}>{end.end}: {formatNumber(end.total)}
              {snapshot.resource !== null && <> / {formatNumber(snapshot.resource)} ({formatNumber(end.percent_used, { digits: 1 })} %)</>}
            </strong>
            <span className={matingTone(end.status) || "muted-text"}>{matingStatusLabels[end.status]}</span>
          </div>
          <div className="header-actions">
            <button type="button" className="secondary small" disabled={locked || busy || Boolean(adjustment)} aria-label={`+1 сочленение стороны ${end.end}`}
              onClick={() => void append(end.end, 1, "manual")}>+1 сочленение</button>
            {!end.initial_recorded && end.total === 0 && <button type="button" className="secondary small" disabled={locked || busy}
              ref={(element) => { actionButtons.current[`${end.end}-initial`] = element; }} aria-label={`Задать начальное значение стороны ${end.end}`}
              onClick={() => openAdjustment(end.end, "initial")}>Задать начальное значение</button>}
            <button type="button" className="secondary small" disabled={locked || busy}
              ref={(element) => { actionButtons.current[`${end.end}-correction`] = element; }} aria-label={`Исправить сочленения стороны ${end.end}`}
              onClick={() => openAdjustment(end.end, "correction")}>Исправить</button>
          </div>
        </div>)}
      </div>
      {adjustment && <form className="instrument-mating-adjustment" onSubmit={submit} aria-label={`Сочленения стороны ${adjustment.end}`}>
        <fieldset className="instrument-fields" disabled={locked || busy}>
          <h4>{adjustment.kind === "initial" ? "Начальное значение" : "Исправление"} · сторона {adjustment.end}</h4>
          <div className="form-grid">
            <label>{adjustment.kind === "initial" ? "Начальное число сочленений" : "Изменение числа сочленений"}
              <input ref={valueInput} name="mating_delta" type="number" step="1" required min={adjustment.kind === "initial" ? 1 : undefined} max="2147483647"
                autoComplete="off" value={adjustment.value} onChange={(event) => setAdjustment({ ...adjustment, value: event.target.value })} /></label>
            <label className="wide">{adjustment.kind === "initial" ? "Источник начального значения" : "Причина исправления"}
              <input ref={noteInput} name="mating_note" autoComplete="off" required maxLength={2000} value={adjustment.note}
                onChange={(event) => setAdjustment({ ...adjustment, note: event.target.value })} /></label>
          </div>
          {adjustment.kind === "correction" && <p className="muted-text">Для уменьшения укажите число со знаком минус.</p>}
          <div className="form-actions"><button type="button" className="secondary small" onClick={closeAdjustment}>Отменить исправление</button>
            <button type="submit" className="primary small">{busy ? "Запись…" : "Записать сочленения"}</button></div>
        </fieldset>
      </form>}
      <details className="instrument-mating-history"><summary>История сочленений · последние {snapshot.events.length}</summary>
        <DataTable label="История сочленений" rows={snapshot.events} columns={historyColumns} rowKey={(event) => event.id} hideSearch pageSize={50}
          emptyText="Сочленений ещё нет — запишите первое или задайте начальное значение." />
      </details>
    </>}
  </section>;
}
