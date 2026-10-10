import { useEffect, useState } from "react";
import { productionApi, type ProductionFlow, type ProductionFlowMode, type ProductionOperator, type ProductionTask } from "./api";
import { formatDay, formatMoment } from "./format";

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<void>;

/** Панель задания (Цель 2): «План задания» у цеха, «Сдача ЖУК» у задания, отданного площадке целиком. */
export function JobPanel({ task, operators, canEdit, busy, mutate }: {
  task: ProductionTask;
  operators: ProductionOperator[];
  canEdit: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const [flow, setFlow] = useState<ProductionFlow | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setError("");
    productionApi.flow(task.id)
      .then((result) => { if (active) setFlow(result); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "План задания не рассчитан"); });
    return () => { active = false; };
  }, [task.id, task.version]);

  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!flow) return <div className="empty">Загрузка плана задания…</div>;
  return task.handed_to
    ? <SiteDeliveries task={task} flow={flow} canEdit={canEdit} busy={busy} mutate={mutate} />
    : <JobPlan task={task} flow={flow} operators={operators} canEdit={canEdit} busy={busy} mutate={mutate} />;
}

function whole(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isInteger(number) ? number : NaN;
}

function hours(seconds: number) {
  if (seconds < 60) return "—";
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} мин` : `${(minutes / 60).toFixed(1).replace(".", ",")} ч`;
}

function JobPlan({ task, flow, operators, canEdit, busy, mutate }: {
  task: ProductionTask;
  flow: ProductionFlow;
  operators: ProductionOperator[];
  canEdit: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const saved = flow.task;
  const [mode, setMode] = useState<ProductionFlowMode>(saved.flow_mode ?? "mixed");
  const [batch, setBatch] = useState(saved.transfer_batch ? String(saved.transfer_batch) : "");
  const [early, setEarly] = useState(saved.early_quantity ? String(saved.early_quantity) : "");
  const [team, setTeam] = useState<string[]>(saved.team ?? []);
  const editable = canEdit && task.status === "Запущено";
  const batchValue = mode === "sequential" ? null : whole(batch);
  const earlyValue = whole(early);
  const batchError = batchValue !== null && (Number.isNaN(batchValue) || batchValue < 1 || batchValue > task.quantity)
    ? `Партия — от 1 до ${task.quantity} шт` : "";
  const earlyError = earlyValue !== null && (Number.isNaN(earlyValue) || earlyValue < 1 || earlyValue >= task.quantity)
    ? `Первые раньше — от 1 до ${task.quantity - 1} шт` : "";
  const dirty = mode !== (saved.flow_mode ?? "mixed")
    || batchValue !== (mode === "sequential" ? null : saved.transfer_batch ?? null)
    || earlyValue !== (saved.early_quantity ?? null)
    || team.join(",") !== (saved.team ?? []).join(",");
  const names = new Map(operators.map((item) => [item.id, item.name]));
  // В команде могут быть люди другой площадки (их не выгоняют): показываем и их.
  const people = [...operators.map((item) => item.id), ...(saved.team ?? []).filter((id) => !names.has(id))];
  const plan = flow.plan;
  const late = task.deadline && plan.finish.slice(0, 10) > task.deadline;
  const norms = flow.norms;

  function save() {
    void mutate(`flow:${task.id}`, () => productionApi.updateFlow(task.id, task.version, {
      flow_mode: mode, transfer_batch: batchValue, early_quantity: earlyValue, team,
    }));
  }

  return <section className="job-plan" aria-label="План задания">
    <div className="jp-head">
      <h2>План задания</h2>
      <small>расчёт от {formatMoment(plan.anchor)} по нормам, без учёта уже сданного · ЭТК{norms.revision ? `, ревизия ${norms.revision}` : ""}</small>
    </div>
    {norms.outdated && <div className="warning-inline jp-norms" role="status">
      <span>В ЭТК ревизия {norms.current_revision} — нормы задания {norms.revision ? `от ревизии ${norms.revision}` : "взяты до учёта ревизий"}.</span>
      {editable && <button className="secondary small" disabled={busy} onClick={() => void mutate(`norms:${task.id}`, () => productionApi.refreshNorms(task.id, task.version))}>Обновить нормы из ЭТК</button>}
    </div>}
    <div className="jp-controls">
      <label>Передача между этапами
        <select aria-label="Передача между этапами" value={mode} disabled={!editable} onChange={(event) => setMode(event.target.value as ProductionFlowMode)}>
          {flow.modes.map((item) => <option key={item.value} value={item.value}>{item.name}</option>)}
        </select>
      </label>
      <label>Передаточная партия, шт
        <input aria-label="Передаточная партия" inputMode="numeric" value={mode === "sequential" ? "" : batch} placeholder={`вся, ${task.quantity}`} disabled={!editable || mode === "sequential"} onChange={(event) => setBatch(event.target.value)} />
      </label>
      <label>Первые раньше, шт
        <input aria-label="Первые раньше" inputMode="numeric" value={early} placeholder="нет" disabled={!editable} onChange={(event) => setEarly(event.target.value)} />
      </label>
      {editable && <button className="primary small" disabled={busy || !dirty || Boolean(batchError || earlyError)} onClick={save}>Сохранить план</button>}
    </div>
    {(batchError || earlyError) && <div className="jp-error" role="alert">{batchError || earlyError}</div>}
    <div className="jp-team" role="group" aria-label="Команда задания">
      <span className="jp-label">Команда</span>
      {people.length === 0 && <small>монтажников нет</small>}
      {people.map((id) => <label key={id} className={`jp-person${team.includes(id) ? " on" : ""}`}>
        <input type="checkbox" disabled={!editable} checked={team.includes(id)} onChange={(event) => setTeam((current) => event.target.checked ? [...current, id] : current.filter((value) => value !== id))} />
        {names.get(id) ?? "Сотрудник другой площадки"}
      </label>)}
      {team.length === 0 && people.length > 0 && <small>не выбрана — все монтажники</small>}
    </div>
    <div className="jp-summary">
      <div><span>Первые {plan.first_quantity} шт</span><b>{formatMoment(plan.first_ready)}</b>{plan.baseline_early_ready && <small>без отдельной партии — {formatMoment(plan.baseline_early_ready)}</small>}</div>
      <div className={late ? "jp-late" : ""}><span>Всё готово</span><b>{formatMoment(plan.finish)}</b>{task.deadline && <small>срок {formatDay(task.deadline)}</small>}</div>
      <div><span>Передаточная партия</span><b>{plan.transfer_batch >= task.quantity ? `вся, ${task.quantity} шт` : `${plan.transfer_batch} шт`}</b></div>
      <div><span>Длительность</span><b>{hours(plan.work_seconds)}</b><small>чистых часов смены</small></div>
    </div>
    {plan.warnings.length > 0 && <ul className={`jp-warnings${plan.level === "limit" ? " limit" : ""}`}>{plan.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
    <table className="ap-t jp-steps">
      <thead><tr><th>Этап</th><th>Людей</th><th>Начало</th><th>Первая партия</th><th>Конец</th><th>Ждёт входа</th></tr></thead>
      <tbody>{plan.steps.map((step) => <tr key={step.operation_id}>
        <td><b>{step.position}</b> {step.stage}{step.code && <small className="jp-code">{step.code}</small>}</td>
        <td>{step.people.replace(".", ",")}{step.operator_ids.length ? "" : <small> не назначено</small>}</td>
        <td>{formatMoment(step.start)}</td>
        <td>{formatMoment(step.first_out)}</td>
        <td>{formatMoment(step.finish)}</td>
        <td>{hours(step.wait_seconds)}</td>
      </tr>)}</tbody>
    </table>
  </section>;
}

function SiteDeliveries({ task, flow, canEdit, busy, mutate }: {
  task: ProductionTask;
  flow: ProductionFlow;
  canEdit: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const left = task.quantity - flow.delivered;
  // По умолчанию площадка сдаёт всё одним разом: в поле — весь остаток, можно меньше.
  const [quantity, setQuantity] = useState(String(Math.max(left, 0)));
  const value = whole(quantity);
  const invalid = value === null || Number.isNaN(value) || value < 1 || value > left;
  const editable = canEdit && task.status === "Запущено";
  const site = task.handed_to ?? "";
  return <section className="job-plan" aria-label={`Сдача ${site}`}>
    <div className="jp-head">
      <h2>Сдача {site}</h2>
      <small>сдано {flow.delivered} из {task.quantity} шт{left > 0 ? ` · осталось ${left}` : " · весь тираж сдан"}</small>
    </div>
    {editable && left > 0 && <div className="jp-controls">
      <label>Сдано, шт
        <input aria-label="Сдано, шт" inputMode="numeric" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
      </label>
      <button className="primary small" disabled={busy || invalid} onClick={() => void mutate(`deliver:${task.id}`, () => productionApi.deliver(task.id, task.version, value === left ? null : value))}>
        {invalid ? "Сдано" : `Сдано ${value} шт`}
      </button>
      {invalid && quantity.trim() !== "" && <span className="jp-error">от 1 до {left} шт</span>}
    </div>}
    {flow.deliveries.length > 0 ? <table className="ap-t jp-steps">
      <thead><tr><th>Когда</th><th>Шт</th><th>Кто отметил</th><th></th></tr></thead>
      <tbody>{flow.deliveries.map((item) => <tr key={item.id} className={item.cancelled_at ? "jp-cancelled" : ""}>
        <td>{formatMoment(item.delivered_at)}</td>
        <td><b>{item.quantity}</b></td>
        <td>{item.delivered_by}</td>
        <td>{item.cancelled_at
          ? <small>отменено {formatMoment(item.cancelled_at)}, {item.cancelled_by}</small>
          : canEdit && task.status !== "Отменено" && <button className="link-button" disabled={busy} onClick={() => {
            if (!window.confirm(`Отменить сдачу ${item.quantity} шт от ${formatMoment(item.delivered_at)}?`)) return;
            void mutate(`undeliver:${item.id}`, () => productionApi.cancelDelivery(task.id, item.id, task.version));
          }}>Отменить сдачу</button>}</td>
      </tr>)}</tbody>
    </table> : <small className="jp-empty">{site} ещё ничего не сдал.</small>}
  </section>;
}
