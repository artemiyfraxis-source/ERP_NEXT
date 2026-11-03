import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, productionApi, type Principal, type ProductionTask } from "./api";
import { DataTable } from "./DataTable";
import { formatMoment } from "./format";
import { PageWindow } from "./PageWindow";
import { StatusChip } from "./StatusChip";
import { EquipmentSetupForm, type SetupOperationChoice } from "./EquipmentSetupForm";
import { EquipmentRecipeEditor } from "./EquipmentRecipeEditor";
import { EquipmentSetupTrials } from "./EquipmentSetupTrials";
import { equipmentSetupApi, type SetupAction, type SetupExecutor, type SetupMachine, type SetupRecipe, type SetupStatus, type SetupTask, type SetupViewer } from "./equipmentSetupApi";
import "./equipmentSetup.css";

const statuses: Record<SetupStatus, string> = { New: "Назначена", InProgress: "В работе", Blocked: "Заблокирована", Ready: "Готова", Cancelled: "Отменена" };
const tones = { New: "neutral", InProgress: "info", Blocked: "warning", Ready: "success", Cancelled: "neutral" } as const;
export function setupOperationChoices(tasks: ProductionTask[]): SetupOperationChoice[] {
  return tasks.flatMap(task => !task.pending && task.techcard_id && task.techcard_revision ? task.operations.flatMap(operation =>
    (operation.etk_ops?.length ? operation.etk_ops : [{ position: operation.position, stage: operation.stage }]).map(item => ({
      key: `${operation.id}:${item.position}`,
      label: `${task.project} · ${task.item} · ${item.stage || "Название операции не задано"} · ЭТК ${item.position}, рев. ${task.techcard_revision}`,
      operation: { card_id: task.techcard_id!, card_revision: task.techcard_revision!, production_task_id: task.id, production_operation_id: operation.id, etk_position: item.position },
    }))) : []);
}

export function EquipmentSetupPage({ principal }: { principal?: Principal }) {
  const [tasks, setTasks] = useState<SetupTask[]>([]), [machines, setMachines] = useState<SetupMachine[]>([]), [production, setProduction] = useState<ProductionTask[]>([]);
  const [viewer, setViewer] = useState<SetupViewer | null>(null), [loaded, setLoaded] = useState(false), [error, setError] = useState(""), [contextError, setContextError] = useState("");
  const [more, setMore] = useState(false), [busy, setBusy] = useState(false), [create, setCreate] = useState(false), [selected, setSelected] = useState<SetupTask | null>(null);
  const [productionCursor, setProductionCursor] = useState<string | null>(null), [productionBusy, setProductionBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true; setLoaded(false); setError(""); setContextError(""); setViewer(null); setSelected(null); setCreate(false);
    Promise.all([equipmentSetupApi.viewer(), equipmentSetupApi.tasks(), equipmentSetupApi.machines()]).then(([who, rows, catalog]) => {
      if (alive) { setViewer(who); setTasks(rows.items); setMore(rows.items.length === rows.limit); setMachines(catalog.items); }
    }).catch(reason => { if (alive) setError(failureText(reason, "Не удалось загрузить наладку")); }).finally(() => { if (alive) setLoaded(true); });
    productionApi.list().then(result => { if (alive) { setProduction(result.tasks); setProductionCursor(result.next_cursor); } }).catch(reason => { if (alive) { setProduction([]); setProductionCursor(null); setContextError(failureText(reason, "Не удалось загрузить названия операций")); } });
    return () => { alive = false; };
  }, [refresh, principal?.user.id, principal?.viewing, principal?.admin_mode]);
  const choices = useMemo(() => setupOperationChoices(production), [production]);
  const operationLabel = (task: SetupTask) => choices.find(item => item.key === `${task.operation.production_operation_id}:${task.operation.etk_position}`
    && item.operation.card_id === task.operation.card_id && item.operation.card_revision === task.operation.card_revision)?.label
    ?? `Данные операции недоступны · ЭТК ${task.operation.etk_position}, рев. ${task.operation.card_revision}`;
  const changed = (task: SetupTask) => { setTasks(rows => rows.some(item => item.id === task.id) ? rows.map(item => item.id === task.id ? task : item) : [task, ...rows]); setSelected(task); setCreate(false); };
  async function moreProduction() {
    if (!productionCursor || productionBusy) return;
    setProductionBusy(true); setContextError("");
    try {
      const next = await equipmentSetupApi.moreProduction(productionCursor);
      setProduction(rows => [...rows, ...next.tasks.filter(task => !rows.some(row => row.id === task.id))]);
      setProductionCursor(next.next_cursor);
    } catch (reason) { setContextError(failureText(reason, "Не удалось загрузить следующие задания")); }
    finally { setProductionBusy(false); }
  }
  return <div className="equipment-setup">
    <div className="page-head"><div><h1>Наладка оборудования</h1><span className="muted">{viewer?.site || "Все доступные площадки"}</span></div><div className="setup-actions">
      <button className="secondary" disabled={!loaded || busy} onClick={() => setRefresh(value => value + 1)}>Обновить</button>
      {viewer?.can_assign && viewer.writable && <button className="primary" onClick={() => setCreate(true)}>Назначить наладку</button>}
    </div></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {contextError && <p className="warning-inline" role="status">{contextError}</p>}
    {!loaded ? <p className="empty">Загрузка заданий наладки…</p> : !error && <section className="panel">
      {!tasks.length ? <p className="empty">Нет доступных заданий наладки. Мастер может назначить выданную операцию.</p> : <DataTable rows={tasks} rowKey={task => task.id} storageKey="equipment-setup" personalKey="equipment-setup" label="Поиск по станку или операции" onRowClick={setSelected} rowTitle={() => "Открыть наладку"} columns={[
        { key: "machine", title: "Станок", value: task => task.machine_id, render: task => <strong>{task.machine_id}</strong> },
        { key: "operation", title: "Проект · операция · ревизия", value: operationLabel, grow: true, wrap: true },
        { key: "window", title: "Окно наладки", value: task => `${formatMoment(task.planned_start)} — ${formatMoment(task.planned_finish)}` },
        { key: "status", title: "Статус", value: task => statuses[task.status], render: task => <StatusChip tone={tones[task.status]}>{statuses[task.status]}</StatusChip>, filter: true },
        { key: "recipe", title: "Версия рецепта", value: task => task.recipe.version, numeric: true },
      ]} />}
      {more && <button className="secondary" disabled={busy} onClick={async () => { setBusy(true); try { const next = await equipmentSetupApi.tasks(tasks.length); setTasks([...tasks, ...next.items]); setMore(next.items.length === next.limit); } catch (reason) { setError(failureText(reason, "Не удалось загрузить задания")); } finally { setBusy(false); } }}>Показать ещё задания</button>}
    </section>}
    {create && <EquipmentSetupForm choices={choices} close={() => setCreate(false)} saved={changed}
      moreChoices={productionCursor ? () => void moreProduction() : undefined} loadingChoices={productionBusy} choicesError={contextError} />}
    {selected && viewer && <SetupDetail key={selected.id} task={selected} viewer={viewer} machine={machines.find(item => item.id === selected.machine_id)} label={operationLabel(selected)} changed={changed} close={() => setSelected(null)} />}
  </div>;
}

function SetupDetail({ task, viewer, machine, label, changed, close }: { task: SetupTask; viewer: SetupViewer; machine?: SetupMachine; label: string; changed: (task: SetupTask) => void; close: () => void }) {
  const [recipe, setRecipe] = useState<SetupRecipe | null>(null), [people, setPeople] = useState<SetupExecutor[]>([]), [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [reason, setReason] = useState(""), [available, setAvailable] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"block" | "cancel" | null>(null), [editor, setEditor] = useState(false);
  const attempt = useRef({ fingerprint: "", key: "" });
  const own = viewer.writable && task.technician_id === viewer.actor_id;
  const canManage = viewer.writable && viewer.can_assign;
  useEffect(() => {
    let alive = true; setLoaded(false);
    Promise.all([equipmentSetupApi.recipe(task.recipe.recipe_id, task.recipe.version), equipmentSetupApi.executors(task.machine_id)])
      .then(([version, executors]) => { if (alive) { setRecipe(version); setPeople(executors.items); } }).catch(value => { if (alive) setError(failureText(value, "Не удалось загрузить рецепт и исполнителя")); }).finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, [task.recipe.recipe_id, task.recipe.version, task.machine_id]);
  async function reload() { try { changed(await equipmentSetupApi.task(task.id)); } catch (value) { setError(failureText(value, "Не удалось обновить задание")); } }
  async function transition(action: SetupAction) {
    if (busy) return;
    if (action === "block" && (!reason.trim() || available === "")) { setError("Укажите причину и доступность станка"); return; }
    if (action === "cancel" && !reason.trim()) { setError("Укажите причину отмены"); return; }
    const fingerprint = JSON.stringify([task.id, task.version, action, reason, available]);
    if (attempt.current.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try { changed(await equipmentSetupApi.transition(task, action, attempt.current.key, action === "block" || action === "cancel" ? reason : "", action === "block" ? available === "yes" : null)); setMode(null); setReason(""); }
    catch (value) { setError(failureText(value, "Не удалось изменить статус")); } finally { setBusy(false); }
  }
  return <PageWindow label="Задание наладки" close={() => { if (!busy) close(); }} width={1160}><div className="panel equipment-setup">
    <div className="page-head"><h2>{task.machine_id} · {machine?.name || "Наладка оборудования"}</h2><button className="secondary" disabled={busy} onClick={close}>Закрыть</button></div>
    <p className="setup-operation">{label}</p>
    <div className="setup-detail-meta"><StatusChip tone={tones[task.status]}>{statuses[task.status]}</StatusChip><span>{formatMoment(task.planned_start)} — {formatMoment(task.planned_finish)}</span><span>{people.find(person => person.id === task.technician_id)?.name || (loaded ? "Исполнитель недоступен в текущем списке" : "Загрузка исполнителя…")}</span></div>
    {task.status === "Blocked" && <p className="warning-inline">{task.blocked_reason} · {task.blocked_machine_available ? "Станок освобождён" : "Станок остаётся занят"}. Наладчик освобождён.</p>}
    {error && <div className="error-banner" role="alert">{error} <button className="secondary small" disabled={busy} onClick={() => void reload()}>Обновить задание</button></div>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    <div className="setup-actions">
      {own && task.status === "New" && <button className="primary" disabled={busy} onClick={() => void transition("start")}>Начать наладку</button>}
      {own && task.status === "Blocked" && <button className="primary" disabled={busy} onClick={() => void transition("resume")}>Продолжить наладку</button>}
      {own && task.status === "InProgress" && <><button className="primary" disabled={busy} onClick={() => void transition("ready")}>Подтвердить готовность наладки</button><button className="secondary" disabled={busy} onClick={() => setMode("block")}>Заблокировать</button></>}
      {canManage && !["Ready", "Cancelled"].includes(task.status) && <button className="secondary" disabled={busy} onClick={() => setMode("cancel")}>Отменить наладку</button>}
    </div>
    {mode && <form className="setup-grid" onSubmit={event => { event.preventDefault(); void transition(mode); }}>
      <label>Причина {mode === "block" ? "блокировки" : "отмены"}<input name="reason" autoComplete="off" required maxLength={2000} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
      {mode === "block" && <label>Станок доступен другим работам?<select name="available" required value={available} disabled={busy} onChange={event => setAvailable(event.target.value)}><option value="">Укажите явно</option><option value="yes">Да, освободить станок</option><option value="no">Нет, оставить занятым</option></select></label>}
      <div className="setup-actions"><button className="primary" disabled={busy}>{mode === "block" ? "Подтвердить блокировку" : "Подтвердить отмену"}</button><button type="button" className="secondary" onClick={() => setMode(null)}>Не менять</button></div>
    </form>}
    {!loaded ? <p className="empty">Загрузка рецепта…</p> : recipe && <section><div className="page-head"><h3>{recipe.title} · версия {recipe.version}</h3>{canManage && machine && <button className="secondary small" onClick={() => setEditor(true)}>Записать новую версию</button>}</div>
      <div className="setup-recipe-read">{([['Геометрия кабеля', recipe.geometry], ['Ручные настройки панели', recipe.panel_settings]] as const).map(([title, fields]) => <section key={title}><h3>{title}</h3>{fields.length ? <dl>{fields.map(field => <div key={field.label}><dt>{field.label}</dt><dd>{field.value} {field.unit}</dd></div>)}</dl> : <p className="muted">Не записана</p>}</section>)}</div>
      <p className="setup-source">Источник: {recipe.source}</p>
    </section>}
    <EquipmentSetupTrials task={task} writable={own} changed={() => void reload()} />
    {editor && machine && recipe && <EquipmentRecipeEditor machine={machine} initial={recipe} close={() => setEditor(false)} saved={() => { setEditor(false); setNotice("Новая версия сохранена. Это задание остаётся на закреплённой версии рецепта."); }} />}
  </div></PageWindow>;
}
