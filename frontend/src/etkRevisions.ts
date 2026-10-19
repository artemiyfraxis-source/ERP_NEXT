/** Журнал ревизий ЭТК: кто выпустил, и список «Что изменилось» у автоматической ревизии. */

export interface RevisionChange { operation_key: string; label: string; field: string; old: unknown; new: unknown }

/** Список изменений ревизии: массив {operation_key, label, field, old, new} (или обёртка {changes: [...]}). */
export function revisionChanges(diff: unknown): RevisionChange[] {
  const raw = Array.isArray(diff) ? diff : diff && typeof diff === "object" ? (diff as { changes?: unknown }).changes : null;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object").map((item) => ({
    operation_key: String(item.operation_key ?? ""), label: String(item.label ?? ""), field: String(item.field ?? ""),
    old: item.old, new: item.new,
  }));
}

/** Значение в таблице изменений: пусто — тире, список — через «; », остальное как есть. */
export function changeValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(changeValue).join("; ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function revisionActor(revision: { source?: string; actor_name: string }): string {
  return revision.source === "auto" ? "автоматически" : revision.actor_name;
}

/** Изменения по операциям, в порядке появления. */
export function changesByOperation(changes: RevisionChange[]): { key: string; label: string; items: RevisionChange[] }[] {
  const groups = new Map<string, { key: string; label: string; items: RevisionChange[] }>();
  for (const change of changes) {
    const key = change.operation_key || change.label;
    const group = groups.get(key) ?? { key, label: change.label || change.operation_key, items: [] };
    group.items.push(change);
    groups.set(key, group);
  }
  return [...groups.values()];
}
