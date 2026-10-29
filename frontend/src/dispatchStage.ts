/* Путь готовой продукции (владелец 29.10.2026): семь шагов от ожидания производства до отгрузки — одни слова на
   «Приёмке с производства» и в «Данных отправок». Этап проекта — слово сервера (лестница проекта), отправка — её статус. */

export type DispatchStage = "waiting" | "production" | "ready" | "accepted" | "packed" | "to_ship" | "shipped";

export const DISPATCH_STAGES: ReadonlyArray<readonly [DispatchStage, string]> = [
  ["waiting", "В ожидании"], ["production", "В производстве"], ["ready", "Готово"], ["accepted", "Принято"],
  ["packed", "Упаковано"], ["to_ship", "На отгрузку"], ["shipped", "Отгружено"],
];

const BY_PROJECT_STAGE: Record<string, DispatchStage> = {
  "В работе": "production", "ОТК": "ready", "Принято складом": "accepted", "Упаковано": "packed", "Отгружено": "shipped",
};
const ORDER: DispatchStage[] = DISPATCH_STAGES.map(([stage]) => stage);

/** Шаг по этапу проекта; «Новый» и незнакомое слово (ТК готова, КБ набрана…) — ожидание производства. */
export function projectDispatchStage(projectStage: string): DispatchStage {
  return BY_PROJECT_STAGE[projectStage] ?? "waiting";
}

/** Шаг отправки: уехавшая — «Отгружено», упакованная — «На отгрузку», когда всё для отгрузки есть, иначе «Упаковано»;
 *  заявка (Новая, Собрана) — шаг проекта, но не дальше «Принято»; отменённая — ни в одном шаге. */
export function shipmentDispatchStage(shipment: { status: string; ready_to_ship?: boolean }, projectStage: string): DispatchStage | null {
  if (shipment.status === "Отменена") return null;
  if (shipment.status === "Отгружена" || shipment.status === "Закрыта") return "shipped";
  if (shipment.status === "Упакована") return shipment.ready_to_ship ? "to_ship" : "packed";
  const own = projectDispatchStage(projectStage);
  return ORDER.indexOf(own) > ORDER.indexOf("accepted") ? "accepted" : own;
}
