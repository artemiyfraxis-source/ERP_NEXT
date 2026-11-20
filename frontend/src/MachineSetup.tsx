import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { failureText } from "./api";
import { EMPTY, formatMoment } from "./format";
import { machinesApi, type UnitSetup } from "./machinesApi";

/** Часть «Наладка» карточки станка (владелец 19.11.2026): что требует задание или что стоит сейчас — параметры модели
 *  наладки с источником, текущая настройка станка и последний эталон; наладчик выставляет станок и нажимает «Настроено
 *  по заданию» (снимает «Ждёт наладки») или «Настроено вручную» (с причиной). У станка без модели наладки — свободные
 *  строки «что настроено». Станок с управлением получает значения из ERP (`extra` — его собственная панель). */

type FreeRow = { name: string; value: string };
const NO_TASK = "";

export function MachineSetupPart({ inventory, canWrite, extra, saved }: {
  inventory: string; canWrite: boolean; extra?: ReactNode; saved?: () => void;
}) {
  const [setup, setSetup] = useState<UnitSetup | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [task, setTask] = useState(NO_TASK), [reason, setReason] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [free, setFree] = useState<FreeRow[]>([{ name: "", value: "" }]);
  const load = useCallback(async () => {
    try {
      const next = await machinesApi.setup(inventory);
      setSetup(next);
      setError("");
      setTask(next.open_tasks.find((item) => item.mine)?.id ?? next.open_tasks.find((item) => item.can_confirm)?.id ?? NO_TASK);
      setValues({});
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить наладку станка")); }
  }, [inventory]);
  useEffect(() => { void load(); }, [load]);

  const latest = useMemo(() => {
    const newest = [...(setup?.references ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    return newest ?? null;
  }, [setup]);
  if (error && !setup) return <section className="machine-facts machine-setup" aria-label="Наладка"><h3>Наладка</h3><p className="error-banner" role="alert">{error}</p></section>;
  if (!setup) return <section className="machine-facts machine-setup" aria-label="Наладка"><h3>Наладка</h3><p className="empty">Загрузка наладки…</p></section>;

  const params = setup.parameters;
  const now = setup.configuration?.values ?? {};
  const proposed = (key: string) => now[key] ?? latest?.values[key] ?? "";
  const value = (key: string) => values[key] ?? proposed(key);
  const byTask = task !== NO_TASK;
  const draftValues = params.length
    ? Object.fromEntries(params.map((item) => [item.key, value(item.key).trim()]).filter(([, text]) => text))
    : Object.fromEntries(free.filter((row) => row.name.trim() && row.value.trim()).map((row) => [row.name.trim(), row.value.trim()]));
  const ready = Object.keys(draftValues).length > 0 && (byTask || reason.trim().length > 0);
  const label = (key: string) => params.find((item) => item.key === key)?.label ?? key;

  async function save() {
    if (!setup || busy || !ready) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const chosen = setup.open_tasks.find((item) => item.id === task);
      await machinesApi.configure(inventory, { expected_version: setup.configuration?.version ?? 0, task_id: byTask ? task : null,
        ...(byTask ? { task_version: chosen?.version ?? null } : {}), values: draftValues, reason: reason.trim() });
      setNotice(byTask ? "Настройка записана по заданию — задание наладки готово" : "Настройка записана");
      setReason(""); setFree([{ name: "", value: "" }]);
      await load();
      saved?.();
    } catch (reason) { setError(failureText(reason, "Не удалось записать настройку станка")); }
    finally { setBusy(false); }
  }

  return <section className="machine-facts machine-setup" aria-label="Наладка">
    <header className="machine-setup-head">
      <h3>Наладка</h3>
      <span className="machine-muted">{setup.requires_setup.code ? `Требует наладки: ${setup.requires_setup.label}` : "Модель наладки не задана"}</span>
      {extra}
    </header>
    {setup.reason && <p className="machine-muted machine-setup-note">{setup.reason}</p>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    <div className="machine-setup-tasks" role="radiogroup" aria-label="Для чего настройка">
      {setup.open_tasks.map((item) => <label key={item.id} className={task === item.id ? "is-on" : ""}>
        <input type="radio" name={`setup-task-${inventory}`} checked={task === item.id} disabled={!canWrite || !item.can_confirm} onChange={() => setTask(item.id)} />
        <span><strong>{item.operation}</strong>
          <span className="machine-muted"> · {item.status}{item.planned_start ? ` · ${formatMoment(item.planned_start)}` : ""}{item.can_confirm ? " · ждёт наладки" : " · назначено другому наладчику"}</span></span>
      </label>)}
      <label className={task === NO_TASK ? "is-on" : ""}>
        <input type="radio" name={`setup-task-${inventory}`} checked={task === NO_TASK} disabled={!canWrite} onChange={() => setTask(NO_TASK)} />
        <span><strong>Без задания</strong><span className="machine-muted"> · настроено вручную, нужна причина</span></span>
      </label>
    </div>
    {params.length > 0
      ? <table className="machine-table machine-setup-table">
        <thead><tr><th scope="col">Параметр</th><th scope="col">Откуда значение</th><th scope="col">Стоит сейчас</th><th scope="col">Последний эталон</th><th scope="col">Настроено</th></tr></thead>
        <tbody>{params.map((item) => <tr key={item.key}>
          <th scope="row">{item.label}{item.unit && <small>{item.unit}</small>}</th>
          <td>{item.source}{item.source_field && <small className="machine-muted"> · {item.source_field}</small>}</td>
          <td className="machine-mono">{now[item.key] ?? EMPTY}</td>
          <td className="machine-mono">{latest?.values[item.key] ?? EMPTY}</td>
          <td><input aria-label={`${item.label}, настроено`} value={value(item.key)} disabled={!canWrite || busy} inputMode={item.unit ? "decimal" : "text"}
            onChange={(event) => setValues({ ...values, [item.key]: event.target.value })} /></td>
        </tr>)}</tbody>
      </table>
      : <div className="machine-setup-free">
        <p className="machine-muted">Параметры наладки для этого станка не заданы — запишите, что настроено.</p>
        {free.map((row, index) => <div key={index} className="machine-setup-free-row">
          <input aria-label={`Параметр ${index + 1}`} placeholder="Параметр" value={row.name} disabled={!canWrite || busy}
            onChange={(event) => setFree(free.map((item, at) => at === index ? { ...item, name: event.target.value } : item))} />
          <input aria-label={`Значение ${index + 1}`} placeholder="Значение" value={row.value} disabled={!canWrite || busy}
            onChange={(event) => setFree(free.map((item, at) => at === index ? { ...item, value: event.target.value } : item))} />
        </div>)}
        {canWrite && <button type="button" className="secondary small" disabled={busy} onClick={() => setFree([...free, { name: "", value: "" }])}>+ Параметр</button>}
      </div>}
    {canWrite && <div className="machine-setup-actions">
      {!byTask && <label className="machine-setup-reason"><span>Причина</span>
        <input value={reason} maxLength={500} disabled={busy} placeholder="например, смена ножа" onChange={(event) => setReason(event.target.value)} /></label>}
      <button type="button" className="primary" disabled={busy || !ready} onClick={() => void save()}>
        {byTask ? "Настроено по заданию" : "Настроено вручную"}</button>
    </div>}
    {setup.configuration && <p className="machine-muted machine-setup-note">
      Сейчас на станке — записал {setup.configuration.set_by || EMPTY}{setup.configuration.set_at ? `, ${formatMoment(setup.configuration.set_at)}` : ""}</p>}
    {setup.history.length > 0 && <details className="machine-setup-history">
      <summary>Журнал наладки <span className="machine-muted">· {setup.history.length}</span></summary>
      <ul>{setup.history.map((item, index) => <li key={index}>
        <span className="machine-mono">{formatMoment(item.at)}</span> {item.by}
        {" · "}{item.operation ? `по заданию: ${item.operation}` : `вручную: ${item.reason}`}
        {" · "}{Object.entries(item.values).map(([key, text]) => `${label(key)} ${text}`).join(", ")}
      </li>)}</ul>
    </details>}
  </section>;
}
