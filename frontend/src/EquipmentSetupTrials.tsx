import { useEffect, useRef, useState } from "react";
import { failureText } from "./api";
import { DataTable } from "./DataTable";
import { formatMoment } from "./format";
import { equipmentSetupApi, type SetupTask, type SetupTrial } from "./equipmentSetupApi";

export function EquipmentSetupTrials({ task, writable, changed }: { task: SetupTask; writable: boolean; changed: () => void }) {
  const [rows, setRows] = useState<SetupTrial[]>([]), [loaded, setLoaded] = useState(false), [more, setMore] = useState(false);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [label, setLabel] = useState(""), [value, setValue] = useState(""), [unit, setUnit] = useState("");
  const attempt = useRef({ fingerprint: "", id: "" });
  useEffect(() => {
    let alive = true; setLoaded(false); setError("");
    equipmentSetupApi.trials(task.id).then(result => { if (alive) { setRows(result.items); setMore(result.items.length === result.limit); } }).catch(reason => { if (alive) setError(failureText(reason, "Не удалось загрузить пробы")); }).finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, [task.id, task.version]);
  return <section><h3>Пробные измерения · рецепт, версия {task.recipe.version}</h3>
    {error && <div className="error-banner" role="alert">{error} <button className="secondary small" disabled={busy} onClick={changed}>Обновить задание</button></div>}
    {!loaded ? <p className="empty">Загрузка измерений…</p> : rows.length ? <DataTable rows={rows} rowKey={row => row.id} columns={[
      { key: "label", title: "Параметр", value: row => row.label, grow: true },
      { key: "value", title: "Измерено", value: row => row.value.replace(".", ","), align: "right" },
      { key: "unit", title: "Ед.", value: row => row.unit },
      { key: "date", title: "Записано", value: row => formatMoment(row.measured_at) },
    ]} /> : !error && <p className="empty">Пробные измерения пока не записаны.</p>}
    {more && <button className="secondary" disabled={busy} onClick={async () => {
      setBusy(true); try { const next = await equipmentSetupApi.trials(task.id, rows.length); setRows([...rows, ...next.items]); setMore(next.items.length === next.limit); }
      catch (reason) { setError(failureText(reason, "Не удалось загрузить пробы")); } finally { setBusy(false); }
    }}>Показать ещё измерения</button>}
    {writable && task.status === "InProgress" && <form className="setup-trial-form" onSubmit={async event => {
      event.preventDefault(); if (busy) return; setError("");
      const exact = value.trim().replace(",", ".");
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(exact)) { setError("Введите конечное десятичное число"); return; }
      const payload = { label: label.trim(), value: exact, unit: unit.trim() };
      const fingerprint = JSON.stringify([task.id, payload]);
      if (attempt.current.fingerprint !== fingerprint) attempt.current = { fingerprint, id: crypto.randomUUID() };
      setBusy(true);
      try { await equipmentSetupApi.recordTrial(task, attempt.current.id, payload); setValue(""); attempt.current = { fingerprint: "", id: "" }; changed(); }
      catch (reason) { setError(failureText(reason, "Не удалось записать измерение")); } finally { setBusy(false); }
    }}>
      <label>Параметр измерения<input name="trial-label" autoComplete="off" required maxLength={160} value={label} disabled={busy} onChange={event => setLabel(event.target.value)} /></label>
      <label>Измеренное значение<input name="trial-value" autoComplete="off" inputMode="decimal" required maxLength={128} value={value} disabled={busy} onChange={event => setValue(event.target.value)} /></label>
      <label>Единица измерения<input name="trial-unit" autoComplete="off" required maxLength={160} value={unit} disabled={busy} onChange={event => setUnit(event.target.value)} /></label>
      <button className="primary" disabled={busy}>{busy ? "Записываю…" : "Записать измерение"}</button>
    </form>}
  </section>;
}
