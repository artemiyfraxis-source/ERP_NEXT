import { useEffect, useRef, useState } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import { PageWindow } from "./PageWindow";
import { EquipmentRecipeEditor } from "./EquipmentRecipeEditor";
import { equipmentSetupApi, type SetupExecutor, type SetupMachine, type SetupOperation, type SetupRecipe, type SetupTask } from "./equipmentSetupApi";

export interface SetupOperationChoice { key: string; label: string; operation: SetupOperation }
function nextWorkingDay() {
  const day = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const date = new Date(`${day}T12:00:00+03:00`);
  do { date.setUTCDate(date.getUTCDate() + 1); } while ([0, 6].includes(date.getUTCDay()));
  return date.toISOString().slice(0, 10);
}

export function EquipmentSetupForm({ choices, saved, close, moreChoices, loadingChoices = false, choicesError = "" }: {
  choices: SetupOperationChoice[]; saved: (task: SetupTask) => void; close: () => void;
  moreChoices?: () => void; loadingChoices?: boolean; choicesError?: string;
}) {
  const [operationKey, setOperationKey] = useState(""), [machineId, setMachineId] = useState(""), [executorId, setExecutorId] = useState(""), [recipeKey, setRecipeKey] = useState("");
  const [machines, setMachines] = useState<SetupMachine[]>([]), [executors, setExecutors] = useState<SetupExecutor[]>([]), [recipes, setRecipes] = useState<SetupRecipe[]>([]);
  const [machineMore, setMachineMore] = useState(false), [executorMore, setExecutorMore] = useState(false), [recipeMore, setRecipeMore] = useState(false);
  const [start, setStart] = useState(() => `${nextWorkingDay()}T08:00`), [finish, setFinish] = useState(() => `${nextWorkingDay()}T09:00`);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [dirty, setDirty] = useState(false), [editor, setEditor] = useState(false);
  const attempt = useRef({ fingerprint: "", key: "" });
  const selected = choices.find(item => item.key === operationKey);
  const machine = machines.find(item => item.id === machineId);
  const recipe = recipes.find(item => `${item.recipe_id}:${item.version}` === recipeKey);
  useEffect(() => {
    let alive = true; setMachineId(""); setMachines([]); setMachineMore(false); setError("");
    if (!selected) { setLoading(false); return; }
    setLoading(true);
    equipmentSetupApi.assignmentContext(selected.operation).then(result => { if (alive) { setMachines(result.items); setMachineMore(result.items.length === result.limit); } }).catch(reason => { if (alive) setError(failureText(reason, "Не удалось проверить выданную операцию")); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [selected?.key, selected?.operation.card_id, selected?.operation.card_revision, selected?.operation.production_task_id]);
  useEffect(() => {
    let alive = true; setExecutors([]); setRecipes([]); setExecutorId(""); setRecipeKey(""); setExecutorMore(false); setRecipeMore(false);
    if (!machineId) return;
    setLoading(true); setError("");
    Promise.all([equipmentSetupApi.executors(machineId), equipmentSetupApi.recipes(machineId)]).then(([people, versions]) => {
      if (alive) { setExecutors(people.items); setRecipes(versions.items); setExecutorMore(people.items.length === people.limit); setRecipeMore(versions.items.length === versions.limit); }
    }).catch(reason => { if (alive) setError(failureText(reason, "Не удалось загрузить исполнителей и рецепты")); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [machineId]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [dirty]);
  const dismiss = () => { if (!busy && (!dirty || window.confirm("Закрыть без назначения наладки?"))) close(); };
  async function more(kind: "machines" | "executors" | "recipes") {
    setLoading(true); setError("");
    try {
      if (kind === "machines" && selected) { const result = await equipmentSetupApi.assignmentContext(selected.operation, machines.length); setMachines([...machines, ...result.items]); setMachineMore(result.items.length === result.limit); }
      if (kind === "executors") { const result = await equipmentSetupApi.executors(machineId, executors.length); setExecutors([...executors, ...result.items]); setExecutorMore(result.items.length === result.limit); }
      if (kind === "recipes") { const result = await equipmentSetupApi.recipes(machineId, recipes.length); setRecipes([...recipes, ...result.items]); setRecipeMore(result.items.length === result.limit); }
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить варианты")); } finally { setLoading(false); }
  }
  return <PageWindow label="Назначить наладку" close={dismiss}><form className="panel equipment-setup" onChange={() => setDirty(true)} onSubmit={async event => {
    event.preventDefault(); if (busy) return;
    if (!selected || !machine || !recipe || !executorId) { setError("Выберите операцию, станок, исполнителя и версию рецепта"); return; }
    const planned_start = `${start}:00+03:00`, planned_finish = `${finish}:00+03:00`;
    if (!Number.isFinite(Date.parse(planned_start)) || Date.parse(planned_finish) <= Date.parse(planned_start)) { setError("Конец наладки должен быть позже начала"); return; }
    const payload = { expected_version: 0 as const, operation: selected.operation, machine_id: machine.id, technician_id: executorId, recipe: { recipe_id: recipe.recipe_id, version: recipe.version }, planned_start, planned_finish };
    const fingerprint = JSON.stringify(payload);
    if (attempt.current.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try { const task = await equipmentSetupApi.assign({ ...payload, idempotency_key: attempt.current.key }); setDirty(false); saved(task); }
    catch (reason) { setError(failureText(reason, "Не удалось назначить наладку")); } finally { setBusy(false); }
  }}>
    <div className="page-head"><h2>Назначить наладку</h2><button type="button" className="secondary" disabled={busy} onClick={dismiss}>Закрыть</button></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {choicesError && <p className="error-banner" role="alert">{choicesError}</p>}
    {moreChoices && <button type="button" className="secondary" disabled={busy || loadingChoices} onClick={moreChoices}>{loadingChoices ? "Загрузка заданий…" : "Ещё производственные задания"}</button>}
    <div className="setup-grid">
      <label className="setup-wide">Выданная операция ЭТК<select name="operation" required value={operationKey} disabled={busy || loading} onChange={event => setOperationKey(event.target.value)}><option value="">Выберите операцию</option>{choices.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
      <label>Физический станок<select name="machine" required value={machineId} disabled={busy || loading || !selected} onChange={event => setMachineId(event.target.value)}><option value="">Выберите экземпляр</option>{machines.map(item => <option key={item.id} value={item.id}>{item.id} · {item.name} · {item.site}</option>)}</select></label>
      <label>Наладчик<select name="executor" required value={executorId} disabled={busy || loading || !machine} onChange={event => setExecutorId(event.target.value)}><option value="">Выберите сотрудника</option>{executors.map(item => <option key={item.id} value={item.id}>{item.name} · {item.site}</option>)}</select></label>
      <label className="setup-wide">Ручной рецепт и версия<select name="recipe" required value={recipeKey} disabled={busy || loading || !machine} onChange={event => setRecipeKey(event.target.value)}><option value="">Выберите сохранённую версию</option>{recipes.map(item => <option key={`${item.recipe_id}:${item.version}`} value={`${item.recipe_id}:${item.version}`}>{item.title} · версия {item.version}</option>)}</select></label>
      <label>Начало наладки · МСК<input name="planned-start" type="datetime-local" required value={start} disabled={busy} onChange={event => setStart(event.target.value)} /></label>
      <label>Окончание наладки · МСК<input name="planned-finish" type="datetime-local" required value={finish} disabled={busy} onChange={event => setFinish(event.target.value)} /></label>
    </div>
    {loading && <p className="empty" role="status">Загрузка вариантов…</p>}
    {selected && !loading && !error && !machines.length && <p className="warning-inline">Для этой выданной операции нет подтверждённых станков.</p>}
    {machine && !loading && !error && !recipes.length && <p className="empty">Нет ручного рецепта — запишите проверенные значения панели.</p>}
    <div className="setup-actions">
      {machineMore && <button type="button" className="secondary" disabled={busy || loading} onClick={() => void more("machines")}>Ещё станки</button>}
      {executorMore && <button type="button" className="secondary" disabled={busy || loading} onClick={() => void more("executors")}>Ещё сотрудники</button>}
      {recipeMore && <button type="button" className="secondary" disabled={busy || loading} onClick={() => void more("recipes")}>Ещё рецепты</button>}
      {machine && <button type="button" className="secondary" disabled={busy || loading} onClick={() => setEditor(true)}>Создать ручной рецепт</button>}
      <button className="primary" disabled={busy || loading}>{busy ? "Назначаю…" : `Назначить на ${formatDay(start.slice(0, 10))}`}</button>
    </div>
    {editor && machine && <EquipmentRecipeEditor machine={machine} close={() => setEditor(false)} saved={item => { setRecipes([item, ...recipes]); setRecipeKey(`${item.recipe_id}:${item.version}`); setEditor(false); }} />}
  </form></PageWindow>;
}
