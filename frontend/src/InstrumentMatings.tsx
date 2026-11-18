import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError, failureText, instrumentMatingsApi, type InstrumentMatingEnd, type InstrumentMatingEvent,
  type InstrumentMatingsSnapshot, type MatingStatus,
} from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDateTime, formatNumber } from "./format";
import { PageWindow } from "./PageWindow";

export const matingStatusLabels: Record<MatingStatus, string> = {
  unknown: "Ресурс не указан", ok: "Ресурс в норме", warning: "Ресурс почти исчерпан", exhausted: "Ресурс исчерпан — заменить",
};
export const matingTone = (status: MatingStatus) => status === "exhausted" ? "tone-danger" : status === "warning" ? "tone-warn" : "";
const kindLabels: Record<InstrumentMatingEvent["kind"], string> = {
  measurement: "Измерение", calibration: "Калибровка", manual: "Вручную", initial: "Начальное значение", correction: "Исправление",
};
const historyColumns: DataColumn<InstrumentMatingEvent>[] = [
  { key: "created_at", title: "Дата", value: (event) => event.created_at, render: (event) => formatDateTime(event.created_at), width: 148 },
  { key: "end", title: "Сторона", value: (event) => event.end, width: 72 },
  { key: "delta", title: "Изменение", value: (event) => event.delta, numeric: true, width: 96,
    render: (event) => `${event.delta > 0 ? "+" : ""}${formatNumber(event.delta)}` },
  { key: "total_after", title: "Итого", value: (event) => event.total_after, numeric: true, width: 88,
    render: (event) => formatNumber(event.total_after) },
  { key: "kind", title: "Действие", value: (event) => kindLabels[event.kind], width: 152 },
  { key: "note", title: "Примечание", value: (event) => event.note, grow: true, minWidth: 160 },
  { key: "actor", title: "Кто", value: (event) => event.actor, width: 140 },
];
type Adjustment = { end: InstrumentMatingEnd["end"]; version: number; kind: "initial" | "correction"; value: string; note: string };

/** Счётчик сочленений по сторонам A/B в карточке СИ; начальное значение и исправление — в отдельном окне. */
export function InstrumentMatings({ instrumentId, locked, refreshKey, onChanged, onDirty, onBusy }: {
  instrumentId: string | undefined; locked: boolean; refreshKey: number; onChanged: () => Promise<void>;
  onDirty: (value: boolean) => void; onBusy: (value: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<InstrumentMatingsSnapshot | null>(null);
  const [loading, setLoading] = useState(Boolean(instrumentId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [adjustment, setAdjustment] = useState<Adjustment | null>(null);
  const inFlight = useRef(false);
  const valueInput = useRef<HTMLInputElement>(null);
  const noteInput = useRef<HTMLInputElement>(null);
  const adjustmentDirty = Boolean(adjustment && (adjustment.value || adjustment.note));

  useEffect(() => { onDirty(adjustmentDirty); }, [adjustmentDirty, onDirty]);
  useEffect(() => {
    if (!instrumentId) return;
    let active = true;
    setLoading(true); setError("");
    instrumentMatingsApi.get(instrumentId).then((result) => {
      if (!Array.isArray(result?.ends) || !Array.isArray(result?.events)) throw new Error("Сведения о сочленениях не получены");
      if (active) setSnapshot(result);
    }).catch((reason) => { if (active) setError(failureText(reason, "Не удалось загрузить сочленения")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instrumentId, refreshKey, attempt]);

  function closeAdjustment() {
    if (busy || (adjustmentDirty && !window.confirm("Закрыть без записи?"))) return;
    setAdjustment(null); setFormError("");
  }
  function openAdjustment(end: InstrumentMatingEnd, kind: Adjustment["kind"]) {
    setAdjustment({ end: end.end, version: end.version, kind, value: "", note: "" }); setFormError(""); setError(""); setNotice("");
    requestAnimationFrame(() => valueInput.current?.focus());
  }
  async function append(end: InstrumentMatingEnd["end"], delta: number, kind: "manual" | Adjustment["kind"], note = "", version?: number) {
    if (!instrumentId || locked || loading || inFlight.current) return;
    inFlight.current = true; setBusy(true); onBusy(true); setError(""); setFormError(""); setNotice("");
    try {
      const result = await instrumentMatingsApi.append(instrumentId, { end, delta, kind, note, ...(version === undefined ? {} : { version }) });
      setSnapshot(result); setAdjustment(null);
      setNotice(`Сочленения стороны ${end} записаны`);
      // The append is committed already: a registry refresh failure must not invite a duplicate write.
      try { await onChanged(); }
      catch (reason) { setError(failureText(reason, "Сочленения записаны, но реестр не обновлён — обновите страницу")); }
    } catch (reason) {
      const text = failureText(reason, "Не удалось записать сочленения");
      if (kind === "manual") setError(text); else setFormError(text);
      if (reason instanceof ApiError && reason.status === 409) setAttempt((value) => value + 1);
    } finally { inFlight.current = false; setBusy(false); onBusy(false); }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!adjustment) return;
    const delta = Number(adjustment.value);
    if (!adjustment.value.trim() || !Number.isSafeInteger(delta) || !delta || (adjustment.kind === "initial" && delta < 0)) {
      setFormError("Укажите целое ненулевое число сочленений"); valueInput.current?.focus(); return;
    }
    if (!adjustment.note.trim()) {
      setFormError(adjustment.kind === "initial" ? "Укажите источник начального значения" : "Укажите причину исправления");
      noteInput.current?.focus(); return;
    }
    const current = snapshot?.ends.find((end) => end.end === adjustment.end);
    if (current && current.total + delta < 0) { setFormError("Число сочленений не может быть отрицательным"); valueInput.current?.focus(); return; }
    void append(adjustment.end, delta, adjustment.kind, adjustment.note.trim(), adjustment.version);
  }

  if (snapshot && !snapshot.has_connectors && !loading && !error) return null;
  return <section className="instrument-matings" aria-label="Сочленения" aria-busy={loading || busy}>
    <h3>Сочленения</h3>
    {!instrumentId && <p className="muted-text">Сохраните запись, чтобы вести учёт сочленений.</p>}
    {loading && <p className="empty">Загрузка сочленений…</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {error && instrumentId && !loading && <button type="button" className="secondary small" disabled={locked || busy}
      onClick={() => setAttempt((value) => value + 1)}>Обновить сочленения</button>}
    {snapshot && !loading && <>
      <div className="instrument-mating-ends">
        {snapshot.ends.map((end) => <div className="instrument-mating-end" key={end.end}>
          <div className="instrument-mating-count">
            <strong className={matingTone(end.status) || undefined}>Сторона {end.end}: {formatNumber(end.total)}
              {snapshot.resource !== null && <> / {formatNumber(snapshot.resource)} ({formatNumber(end.percent_used, { digits: 1 })} %)</>}
            </strong>
            <span className={matingTone(end.status) || "muted-text"}>{matingStatusLabels[end.status]}</span>
          </div>
          <div className="header-actions">
            <button type="button" className="secondary small" disabled={locked || busy} aria-label={`+1 сочленение стороны ${end.end}`}
              onClick={() => void append(end.end, 1, "manual")}>+1 сочленение</button>
            {end.can_initialize && <button type="button" className="secondary small" disabled={locked || busy}
              aria-label={`Задать начальное значение стороны ${end.end}`} onClick={() => openAdjustment(end, "initial")}>Задать начальное значение</button>}
            {!end.can_initialize && <button type="button" className="secondary small" disabled={locked || busy}
              aria-label={`Исправить сочленения стороны ${end.end}`} onClick={() => openAdjustment(end, "correction")}>Исправить</button>}
          </div>
        </div>)}
      </div>
      <details className="instrument-mating-history"><summary>История сочленений · последние {snapshot.events.length}</summary>
        <DataTable label="История сочленений" rows={snapshot.events} columns={historyColumns} rowKey={(event) => event.id} hideSearch pageSize={50}
          emptyText="Сочленений ещё нет — запишите первое или задайте начальное значение." />
      </details>
    </>}
    {adjustment && <PageWindow label={`${adjustment.kind === "initial" ? "Начальное значение" : "Исправление"} сочленений · сторона ${adjustment.end}`}
      width={640} close={closeAdjustment}>
      <form className="panel instrument-window instrument-mating-adjustment" onSubmit={submit} aria-label={`Сочленения стороны ${adjustment.end}`}>
        <header className="page-head"><h2>{adjustment.kind === "initial" ? "Начальное значение" : "Исправление"} · сторона {adjustment.end}</h2></header>
        {formError && <div className="error-banner" role="alert">{formError}</div>}
        <fieldset className="instrument-fields" disabled={locked || busy}>
          <div className="form-grid instrument-mating-fields">
            <label>{adjustment.kind === "initial" ? "Начальное число сочленений" : "Изменение числа сочленений"}
              <input ref={valueInput} name="mating_delta" type="number" step="1" min={adjustment.kind === "initial" ? 1 : undefined} max="2147483647"
                autoComplete="off" value={adjustment.value} onChange={(event) => setAdjustment({ ...adjustment, value: event.target.value })} /></label>
            <label className="wide">{adjustment.kind === "initial" ? "Источник начального значения" : "Причина исправления"}
              <input ref={noteInput} name="mating_note" autoComplete="off" maxLength={2000} value={adjustment.note}
                onChange={(event) => setAdjustment({ ...adjustment, note: event.target.value })} /></label>
          </div>
          {adjustment.kind === "correction" && <p className="muted-text">Для уменьшения укажите число со знаком минус.</p>}
          <div className="form-actions"><button type="button" className="secondary small" onClick={closeAdjustment}>Отменить</button>
            <button type="submit" className="primary small">{busy ? "Запись…" : "Записать сочленения"}</button></div>
        </fieldset>
      </form>
    </PageWindow>}
  </section>;
}
