import { api, type ProductionTask } from "./api";

export interface SetupOperation { card_id: string; card_revision: number; production_task_id: string; production_operation_id: string; etk_position: number }
export interface SetupMachine { id: string; name: string; model: string; site: string; inventory_number?: string; equipment_model_id?: string | null; family?: string }
export interface SetupExecutor { id: string; name: string; site: string }
export interface SetupViewer { actor_id: string; site: string; can_assign: boolean; writable: boolean }
export type SetupStatus = "New" | "InProgress" | "Blocked" | "Ready" | "Cancelled";
export interface SetupTask {
  id: string; version: number; operation: SetupOperation; machine_id: string; technician_id: string;
  recipe: { recipe_id: string; version: number }; planned_start: string; planned_finish: string;
  created_at: string; updated_at: string; status: SetupStatus; blocked_reason: string; blocked_machine_available: boolean | null;
}
export interface RecipeField { label: string; value: string; unit: string }
export interface SetupRecipe {
  recipe_id: string; version: number; machine_id: string; title: string; geometry: RecipeField[];
  panel_settings: RecipeField[]; source: string; created_by: string; created_at: string;
}
export interface SetupTrial { id: string; task_id: string; recipe_id: string; recipe_version: number; label: string; value: string; unit: string; measured_by: string; measured_at: string }
export interface SetupPage<T> { items: T[]; limit: number; offset: number }
export type SetupAction = "start" | "block" | "resume" | "ready" | "cancel";
export interface SetupAssignment { idempotency_key: string; expected_version: 0; operation: SetupOperation; machine_id: string; technician_id: string; recipe: { recipe_id: string; version: number }; planned_start: string; planned_finish: string }
// Наладка ОБ.019 (владелец 18.11.2026): предложение = последний рецепт станка для провода, иначе номинал ЭТК;
// живые значения ПЛК — из «Станков» (`GET /api/production/machines/{id}`), каждая правка — в журнал.
export type Ob019Key = "length_mm" | "strip_front_mm" | "pull_front_mm" | "strip_back_mm" | "pull_back_mm" | "diameter_mm" | "knife_back_mm";
export interface Ob019Parameter { key: Ob019Key; label: string; unit: string; register: string; in_recipe: boolean }
export interface Ob019Wire { label: string; conductor_mm: string | null; has_recipe: boolean }
export interface Ob019Job { key: string; group_id: string; number: string; project: string; cable: string; wire: string; length_mm: string; quantity: string; strip_front_mm: string | null; strip_back_mm: string | null }
export interface Ob019Context { machine: { id: string; inventory_number: string; site: string }; parameters: Ob019Parameter[]; wires: Ob019Wire[]; groups: Ob019Job[] }
export interface Ob019ProposedValue { key: Ob019Key; label: string; unit: string; register: string; value: string | null; source: string }
export interface Ob019Proposal {
  wire: string; values: Ob019ProposedValue[];
  recipe: { recipe_id: string; version: number; title: string; created_at: string; source: string } | null;
  publish: { recipe_id: string; expected_version: number };
  etk: { strip_front_mm: string | null; strip_back_mm: string | null; partial_front: boolean; partial_back: boolean } | null;
}
export interface Ob019Correction { parameter: Ob019Key; actual: string; proposed: string | null; live: string | null; wire: string; context: string; quantity: number | null }
export interface Ob019VersionDraft { recipe_id: string; expected_version: number; wire: string; values: Partial<Record<Ob019Key, string>>; etk_strip_front_mm: string | null; etk_strip_back_mm: string | null; context: string }
type Tone = "ok" | "warn" | "danger" | "info" | "neutral";
/** Подмножество живой карточки «Станков» (часть 1); пока её нет на сервере, панель работает без живых значений. */
export interface Ob019LiveCard {
  link: { code: string; text: string; tone: Tone }; reported_at: string | null; age_seconds: number | null;
  live: { state: { text: string; tone: Tone }; wire: Partial<Record<Ob019Key, number | null>> } | null;
}
const ob019 = (machine: string) => `/api/production/equipment-setup/ob019/${encodeURIComponent(machine)}`;
const root = "/api/production/equipment-setup";
const page = (offset: number) => `limit=100&offset=${offset}`;
const body = (value: unknown, method = "POST") => ({ method, body: JSON.stringify(value) });
export const equipmentSetupApi = {
  moreProduction: (cursor: string) => api<{ tasks: ProductionTask[]; next_cursor: string | null }>(`/api/production/tasks?limit=100&after=${encodeURIComponent(cursor)}`),
  viewer: () => api<SetupViewer>(`${root}/viewer`),
  tasks: (offset = 0) => api<SetupPage<SetupTask>>(`${root}/tasks?${page(offset)}`),
  task: (id: string) => api<SetupTask>(`${root}/tasks/${encodeURIComponent(id)}`),
  machines: (offset = 0) => api<SetupPage<SetupMachine>>(`${root}/machines?${page(offset)}`),
  assignmentContext: (operation: SetupOperation, offset = 0) => api<SetupPage<SetupMachine>>(`${root}/assignment-context?${new URLSearchParams(Object.entries(operation).map(([key, value]) => [key, String(value)]))}&${page(offset)}`),
  executors: (machine: string, offset = 0) => api<SetupPage<SetupExecutor>>(`${root}/executors?machine_id=${encodeURIComponent(machine)}&${page(offset)}`),
  recipes: (machine: string, offset = 0) => api<SetupPage<SetupRecipe>>(`${root}/recipes?machine_id=${encodeURIComponent(machine)}&${page(offset)}`),
  recipe: (id: string, version: number) => api<SetupRecipe>(`${root}/recipes/${encodeURIComponent(id)}/versions/${version}`),
  trials: (task: string, offset = 0) => api<SetupPage<SetupTrial>>(`${root}/tasks/${encodeURIComponent(task)}/trials?${page(offset)}`),
  assign: (value: SetupAssignment) => api<SetupTask>(`${root}/tasks`, body(value)),
  transition: (task: SetupTask, action: SetupAction, key: string, reason = "", machineAvailable: boolean | null = null) => api<SetupTask>(`${root}/tasks/${encodeURIComponent(task.id)}/transition`, body({ expected_version: task.version, action, idempotency_key: key, reason, machine_available: machineAvailable })),
  publish: (id: string, value: Omit<SetupRecipe, "recipe_id" | "version" | "created_by" | "created_at"> & { expected_version: number }) => api<SetupRecipe>(`${root}/recipes/${encodeURIComponent(id)}/versions`, body(value)),
  recordTrial: (task: SetupTask, id: string, value: { label: string; value: string; unit: string }) => api<SetupTrial>(`${root}/tasks/${encodeURIComponent(task.id)}/trials/${encodeURIComponent(id)}`, body({ ...value, expected_version: task.version }, "PUT")),
  ob019Context: (machine: string) => api<Ob019Context>(`${ob019(machine)}/context`),
  ob019Proposal: (machine: string, query: { wire?: string; group_id?: string; length_mm?: string }) =>
    api<Ob019Proposal>(`${ob019(machine)}/proposal?${new URLSearchParams(Object.entries(query).filter(([, value]) => value) as [string, string][])}`),
  ob019Correct: (machine: string, value: Ob019Correction) => api<Record<string, string>>(`${ob019(machine)}/corrections`, body(value)),
  ob019SaveVersion: (machine: string, value: Ob019VersionDraft) => api<{ recipe_id: string; version: number; title: string; created_at: string }>(`${ob019(machine)}/versions`, body(value)),
  ob019Live: (machine: string, signal?: AbortSignal) => api<Ob019LiveCard>(`/api/production/machines/${encodeURIComponent(machine)}`, { signal }),
};
