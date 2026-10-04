import type { ProductionOperation, ProductionOperationInput, ProductionTask } from "./api";

// Сетевой график задания на экранах цеха: что операция берёт на вход и куда идёт её выход. Вместо арифметики
// по номерам (position - 1, max(position)) — поля ответа сервера inputs / first / final / required_quantity.
// Старый ответ без этих полей читается как цепочка по номерам, как раньше.

/** Начало ветки: у операции нет входов (коробок с прошлого этапа она не берёт). */
export function isFirst(operation: ProductionOperation): boolean {
  if (operation.first !== undefined) return operation.first;
  return operation.inputs ? operation.inputs.length === 0 : operation.position === 1;
}

/** Финальная операция: её выход идёт в ОТК и на склад готовой продукции. */
export function isFinal(task: ProductionTask, operation: ProductionOperation): boolean {
  if (operation.final !== undefined) return operation.final;
  return operation.position === Math.max(...task.operations.map((item) => item.position));
}

/** Сколько штук операция сдаёт по заданию; без поля в ответе — тираж. */
export function requiredOf(task: ProductionTask, operation: ProductionOperation): number {
  const value = Number(operation.required_quantity);
  return operation.required_quantity !== undefined && Number.isFinite(value) ? value : task.quantity;
}

const decimal = (value: number) => String(value).replace(".", ",");

/** « · сдать 20 шт» — только там, где операция сдаёт не тираж (ветка, которой на сборку идёт по 2 на изделие). */
export function requiredNote(task: ProductionTask, operation: ProductionOperation): string {
  const required = requiredOf(task, operation);
  return required === task.quantity ? "" : ` · сдать ${decimal(required)} шт`;
}

/** Вход — ровно предыдущий этап, по штуке на изделие: так выглядит цепочка, и пояснять её не нужно. */
function isChainStep(operation: ProductionOperation): boolean {
  const inputs = operation.inputs;
  if (!inputs || !inputs.length) return true;
  return inputs.length === 1 && inputs[0].position === operation.position - 1 && Number(inputs[0].per_unit) === 1;
}

function stageOf(task: ProductionTask, input: ProductionOperationInput): string {
  return input.stage ?? task.operations.find((item) => item.id === input.operation_id)?.stage ?? "";
}

/** «1 «Нарезка А», 2 «Нарезка Б» ×2» — номера и названия этапов-входов, ×N — штук на одно изделие. */
function stageList(task: ProductionTask, operation: ProductionOperation): string {
  return (operation.inputs ?? []).map((input) => {
    const name = stageOf(task, input);
    const per = Number(input.per_unit);
    return `${input.position ?? "?"}${name ? ` «${name}»` : ""}${per !== 1 ? ` ×${decimal(per)}` : ""}`;
  }).join(", ");
}

/** «из этапов 1 «Нарезка А», 2 «Нарезка Б»» — у операции не по цепочке; у цепочки и у начала ветки — пусто. */
export function feedsText(task: ProductionTask, operation: ProductionOperation): string {
  if (isChainStep(operation)) return "";
  return `из ${(operation.inputs ?? []).length > 1 ? "этапов" : "этапа"} ${stageList(task, operation)}`;
}

/** Задание идёт по схеме с ветками: у какой-то операции входы не по цепочке или вторая ветка начинается не с этапа 1. */
export function isNetwork(task: ProductionTask): boolean {
  return task.operations.some((operation) => operation.inputs !== undefined
    && (operation.inputs.length ? !isChainStep(operation) : operation.position !== 1));
}

/** Номер предыдущего этапа цепочки (для подсказки «с этапа N»). */
export function previousStage(operation: ProductionOperation): number {
  return operation.inputs?.length === 1 ? operation.inputs[0].position ?? operation.position - 1 : operation.position - 1;
}

/** Операции, чей полуфабрикат доходит до этой (входы, их входы, …). Без `inputs` в ответе — null: читать по номерам. */
export function upstreamIds(task: ProductionTask, operation: ProductionOperation): Set<string> | null {
  if (!operation.inputs) return null;
  const byId = new Map(task.operations.map((item) => [item.id, item]));
  const found = new Set<string>();
  const pending = operation.inputs.map((item) => item.operation_id);
  while (pending.length) {
    const id = pending.pop()!;
    if (found.has(id) || id === operation.id) continue;
    found.add(id);
    pending.push(...(byId.get(id)?.inputs ?? []).map((item) => item.operation_id));
  }
  return found;
}
