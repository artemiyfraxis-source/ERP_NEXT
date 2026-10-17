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

it("names the journal wipe of a deleted project", () => {
  expect(auditEventName("production.plan_events_deleted")).toBe("Журнал проекта удалён");
  expect(auditDetails({ project: "708К.4", events: 4 })).toBe("проект: 708К.4 · записей: 4");
});

it("names the automatic fill after «Взять в работу» and its fields in Russian", () => {
  expect(auditEventName("procurement.autofilled")).toBe("Заполнено автоматически");
  expect(auditDetails({ legacy_id: "SM-20261004-01", fields: ["supplier", "site_name", "manufacturer", "country"] })).toBe(
    "поля: поставщик, наименование (сайт), изготовитель, страна",
  );
  // Audit U09: where the fill came from, and a site read without a certificate check.
  expect(auditDetails({ fields: ["site_name"], source: "www.chipdip.ru", tls: "не проверен" })).toBe(
    "поля: наименование (сайт) · сайт: www.chipdip.ru · сертификат: не проверен",
  );
});

it("names the background retry and the login lock release in Russian", () => {
  expect(auditEventName("operations.outbox_retried")).toBe("Фоновое задание запущено повторно");
  expect(auditDetails({ topic: "warehouse.received", attempts: "5", error: "invalid payload" })).toBe(
    "задание: Приход на склад · попыток: 5 · ошибка: invalid payload",
  );
  expect(auditEventName("identity.login_lock_released")).toBe("Блокировка входа снята");
  expect(auditEventName("procurement.autofill_requested")).toBe("Автозаполнение строки закупки");
});

it("names the access-level events and the level and flag details in Russian", () => {
  expect(auditEventName("identity.level_access_saved")).toBe("Доступы уровней сохранены");
  expect(auditEventName("identity.access_levels_migrated")).toBe("Роли переведены в уровни доступа");
  expect(auditEventName("identity.level_view_defaults_seeded")).toBe("Уровням открыт просмотр склада, закупки и приёмки");
  expect(auditEventName("identity.dormant_access_archived")).toBe("Исключения прежней роли с полным доступом убраны в архив");
  expect(auditDetails({ level: "tech_lead", changes: "3" })).toBe("уровень: Технический руководитель · изменения: 3");
  expect(auditDetails({ login: "petrov", access_level: "foreman", shop_worker: "да", site_master: "да" })).toBe(
    "логин: petrov · уровень доступа: Бригадир · монтажник: да · мастер площадки: да",
  );
  expect(auditDetails({ access_level: "unknown_level" })).toBe("уровень доступа: unknown_level");
});
