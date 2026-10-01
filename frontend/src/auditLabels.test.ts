import { expect, it } from "vitest";
import { auditDetails, auditEventName, auditModule } from "./auditLabels";

it("names events in Russian and falls back to the module for unknown ones", () => {
  expect(auditEventName("warehouse.written_off")).toBe("Списание со склада");
  expect(auditEventName("requests.reorder_requested")).toBe("Запрошена дозакупка");
  expect(auditEventName("shipping.something_new")).toBe("Отгрузка: something new");
  expect(auditModule("techcards.project_issued")).toBe("Техкарты");
  expect(auditModule("requests.reorder_requested")).toBe("Заявки");
});

it("shows details without ids, UUIDs or [object Object]", () => {
  const text = auditDetails({
    task_id: "15a1ac70-e92c-4a5f-ad12-4a14d7ee5138",
    reason: "output",
    moves: [{ a: 1 }, { b: 2 }],
    quantity: 60,
    grn: "ГП.26.0000002",
    lot: "15e010b2-8450-4147-adc7-afc26e99b98b",
    inherited: [],
  });
  expect(text).toBe("причина: output · перемещений: 2 · кол-во: 60 · ГРН: ГП.26.0000002");
  expect(text).not.toMatch(/object Object|[0-9a-f]{8}-/);
});

it("folds per-line maps and trims decimal zeros", () => {
  expect(auditDetails({ "15e010b2-8450-4147-adc7-afc26e99b98b": 25 })).toBe("строк: 1");
  expect(auditDetails({ quantity: "39.000000", fields: ["carrier", "shipping_point"] })).toBe("кол-во: 39 · поля: перевозчик, адрес отгрузки");
});
