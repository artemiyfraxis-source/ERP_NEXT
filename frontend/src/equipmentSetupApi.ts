import { api, type ProductionTask } from "./api";

export interface SetupOperation { card_id: string; card_revision: number; production_task_id: string; production_operation_id: string; etk_position: number }
export interface SetupMachine { id: string; name: string; model: string; site: string }
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
};
