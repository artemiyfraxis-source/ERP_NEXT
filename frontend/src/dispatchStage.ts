/* Единая лестница этапов отправки (владелец 29.10.2026): одна на «Данные отправок» (менеджер) и «Приёмка с
   производства» (кладовщик). Этап проекта приходит словом сервера, этап отправки считается по её статусу и
   проекту; чего не знаем (Новый, ТК готова, КБ набрана…) — «В ожидании». */

export type DispatchStage = "waiting" | "production" | "ready" | "accepted" | "packed" | "to_ship" | "shipped";

export const DISPATCH_STAGES: ReadonlyArray<readonly [DispatchStage, string]> = [
  ["waiting", "В ожидании"], ["production", "В производстве"], ["ready", "Готово"], ["accepted", "Принято"],
  ["packed", "Упаковано"], ["to_ship", "На отгрузку"], ["shipped", "Отгружено"],
];

// «Готово» (производство закончено), «ОТК» (на проверке) и «ОТК пройден» — уже не в цехе и ждут приёмки складом.
const PROJECT_STAGE: Record<string, DispatchStage> = {
  "В работе": "production", "Готово": "ready", "ОТК": "ready", "ОТК пройден": "ready", "Принято складом": "accepted",
  "Упаковано": "packed", "Отгружено": "shipped",
};

/** Номер этапа на лестнице (для «не дальше принято» и сравнения). */
export const stageRank = (stage: DispatchStage) => DISPATCH_STAGES.findIndex(([key]) => key === stage);
/** Русское название этапа. */
export const stageLabel = (stage: DispatchStage | null) => stage ? DISPATCH_STAGES.find(([key]) => key === stage)?.[1] ?? "" : "Отменена";

export function projectDispatchStage(projectStage: string): DispatchStage {
  return PROJECT_STAGE[projectStage] ?? "waiting";
}

export function shipmentDispatchStage(shipment: { status: string; ready_to_ship?: boolean }, projectStage: string): DispatchStage | null {
  if (shipment.status === "Отменена") return null;
  if (shipment.status === "Отгружена" || shipment.status === "Закрыта") return "shipped";
  if (shipment.status === "Упакована") return shipment.ready_to_ship ? "to_ship" : "packed";
  const own = projectDispatchStage(projectStage);
  return stageRank(own) > stageRank("accepted") ? "accepted" : own;
}
