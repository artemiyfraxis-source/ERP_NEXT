import { describe, expect, it } from "vitest";
import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, type DispatchStage } from "./dispatchStage";

describe("dispatchStage", () => {
  it("keeps the ladder order and Russian labels", () => {
    expect(DISPATCH_STAGES.map(([, label]) => label)).toEqual(["В ожидании", "В производстве", "Готово", "Принято", "Упаковано", "На отгрузку", "Отгружено"]);
  });

  it.each<[string, DispatchStage]>([
    ["Новый", "waiting"], ["ТК готова", "waiting"], ["КБ набрана", "waiting"], ["что-то новое", "waiting"], ["", "waiting"],
    ["В работе", "production"], ["Готово", "ready"], ["ОТК", "ready"], ["ОТК пройден", "ready"],
    ["Принято складом", "accepted"], ["Упаковано", "packed"], ["Отгружено", "shipped"],
  ])("project «%s» is %s", (word, stage) => expect(projectDispatchStage(word)).toBe(stage));

  it.each<[{ status: string; ready_to_ship?: boolean }, string, DispatchStage | null]>([
    [{ status: "Отгружена" }, "ОТК", "shipped"], [{ status: "Закрыта" }, "Новый", "shipped"],
    [{ status: "Упакована", ready_to_ship: true }, "ОТК", "to_ship"], [{ status: "Упакована", ready_to_ship: false }, "ОТК", "packed"], [{ status: "Упакована" }, "ОТК", "packed"],
    [{ status: "Новая" }, "Новый", "waiting"], [{ status: "Новая" }, "В работе", "production"], [{ status: "Собрана" }, "ОТК", "ready"],
    [{ status: "Собрана" }, "Готово", "ready"], [{ status: "Новая" }, "ОТК пройден", "ready"],
    [{ status: "Собрана" }, "Принято складом", "accepted"], [{ status: "Собрана" }, "Упаковано", "accepted"], [{ status: "Новая" }, "Отгружено", "accepted"],
    [{ status: "Отменена" }, "ОТК", null],
  ])("shipment %j on project «%s» is %s", (shipment, word, stage) => expect(shipmentDispatchStage(shipment, word)).toBe(stage));
});
