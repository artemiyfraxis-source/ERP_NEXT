import { describe, expect, it } from "vitest";
import { STATUS_WORDS, TONES, deadlineTone, isKnownStatus, statusLabel, statusTone } from "./statusTone";

/* Статусы, которые отдаёт сервер — перечисления и словари backend (копия на 12.10.2026; Vite не читает файлы вне
   frontend, поэтому список здесь). Новое слово статуса на сервере — добавить сюда и в словарь statusTone.ts. */
const BACKEND_STATUSES: Record<string, string[]> = {
  "fulfillment/domain.py FulfillmentStatus": ["Новая", "Недоукомплектован", "Укомплектован", "Готов к выдаче", "КБ Выдана", "КБ Возвращена", "Готово", "Отменена"],
  "procurement/domain.py ProcurementStatus": ["Новый", "В работе", "Готов к запросу", "Жду счет", "В оплату", "В пути", "Карантин", "Готово", "Согласование", "Отменено"],
  "production/domain.py ProductionStatus": ["Запущено", "Отменено", "Выполнено"],
  "production/traceability.py BoxStatus": ["Открыта", "Закрыта оператором", "Подтверждена", "Возвращена", "Израсходована"],
  "production/traceability.py WorkStatus": ["В работе", "Пауза", "Завершена", "Отменена"],
  "production/traceability.py DefectStatus": ["Ожидает решения", "Решение принято"],
  "production/traceability.py DefectDisposition": ["Доработка", "Списание", "Использовать как есть"],
  "production/traceability.py SerialStatus": ["В работе", "Принято ОТК", "Сдано на ГП", "Брак"],
  "production/traceability.py InstrumentStatus": ["Действует", "Не используется"],
  "receiving/domain.py ReceivingStatus": ["В пути", "Ожидание", "Принято", "Карантин", "Возврат", "Архив ВК"],
  "requests/domain.py RequestStatus": ["Новый", "В работе", "Согласование", "В пути", "Закрыто", "Отменено"],
  "shipping/domain.py ShipmentStatus": ["Новая", "Собрана", "Упакована", "Отгружена", "Закрыта", "Отменена"],
  "techcards/project_cards.py TechcardStatus": ["Черновик", "Выдана"],
  "production/plan.py PLAN_BLOCKERS": ["СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ"],
  "production/plan.py KIT_STAGES": ["Новый", "В работе", "Готово", "ОТК", "ОТК пройден", "Принято складом", "Упаковано", "Отгружено"],
  "production/plan.py SHOP_STATUSES": ["СТОП", "ПРВ КОМПЛ", "К ЗАПУСК", "В РАБОТЕ", "ОЖИД КОМПЛ", "ОЖИД ИНФ", "ОТК", "НА ОТГР", "ГОТОВО", "УПАК", "ОТГРУЖЕН"],
  "production/plan.py TC_REVIEW_VALUES": ["не проверена", "проверена", "с погрешностями"],
  "production/plan.py PROJECT_TERMINALS": ["Отменён"],
  "identity/leave.py STATUS_LABELS": ["Черновик", "На согласовании у руководителя", "Согласовано руководителем", "Зарегистрировано кадрами", "Отклонено", "Отозвано"],
  "operations/domain.py PROJECT_TASK_STATUS_LABELS": ["Открыта", "Закрыта", "Отменена"],
};

describe("statusTone", () => {
  it("gives every status word of the backend a tone", () => {
    const missing = Object.entries(BACKEND_STATUSES).flatMap(([source, words]) =>
      words.filter((word) => !isKnownStatus(word)).map((word) => `${source}: ${word}`));
    expect(missing).toEqual([]);
  });

  it("keeps one tone per word", () => {
    const keys = STATUS_WORDS.map((word) => word.toLowerCase().replace(/ё/g, "е"));
    const twice = keys.filter((key, index) => keys.indexOf(key) !== index);
    // «Пауза» и «ПАУЗА» — одно слово листа и ERP, один тон.
    expect(twice.every((key) => key === "пауза")).toBe(true);
  });

  it("colours the kitting stages as the shop reads them", () => {
    expect(statusTone("Новая")).toBe("neutral");
    expect(statusTone("Недоукомплектован")).toBe("warning");
    expect(statusTone("Укомплектован")).toBe("success");
    expect(statusTone("Готов к выдаче")).toBe("info");
    expect(statusTone("КБ Выдана")).toBe("success");
    expect(statusTone("КБ Возвращена")).toBe("info");
    expect(statusTone("Отменена")).toBe("neutral");
  });

  it("marks blockers and defects", () => {
    expect(statusTone("СТОП")).toBe("danger");
    expect(statusTone("Брак")).toBe("danger");
    expect(statusTone("ХОЛД")).toBe("neutral"); // слова ХОЛД больше нет: не блокер
    expect(statusTone("ОЖИД КОМПЛ")).toBe("warning");
    expect(statusTone("Карантин")).toBe("warning");
  });

  it("ignores case, ё and extra spaces", () => {
    expect(statusTone("  в   пути ")).toBe("info");
    expect(statusTone("Жду счет")).toBe("warning");
    expect(statusTone("ЖДУ СЧЁТ")).toBe("warning");
  });

  it("shows the ERP word instead of the shop sheet word", () => {
    expect(statusLabel("В РАБОТЕ")).toBe("В работе");
    expect(statusLabel("ОТГРУЖЕН")).toBe("Отгружено");
    expect(statusLabel("К ЗАПУС")).toBe("КБ набрана");
    expect(statusLabel("НА ОТГР")).toBe("Упаковано");
    expect(statusLabel("УПАК")).toBe("Упаковано");
    expect(statusLabel("РАСПЛАН")).toBe("РАСПЛАН"); // этапа «Распланировано» больше нет
    expect(statusLabel("Жду счет")).toBe("Жду счёт");
    expect(statusTone("В РАБОТЕ")).toBe(statusTone("В работе"));
    expect(statusTone("ГОТОВО")).toBe("success");
    // Блокеры — слова производства, остаются как есть.
    expect(statusLabel("ОЖИД ИНФ")).toBe("ОЖИД ИНФ");
    expect(statusLabel("ОЖД ИНФ")).toBe("ОЖИД ИНФ");
    expect(statusLabel(" Новый ")).toBe("Новый");
  });

  it("colours the project stage ladder from new to shipped", () => {
    // «Готово» (производство закончено) и «ОТК пройден» — спокойный зелёный, «ОТК» (на проверке) — идёт.
    expect(["Новый", "В работе", "Готово", "ОТК", "ОТК пройден", "Принято складом", "Упаковано", "Отгружено"]
      .map(statusTone)).toEqual(["neutral", "info", "success", "info", "success", "success", "info", "success"]);
    // Слова листа «К ЗАПУСК» больше нет на экране — это этап «КБ набрана».
    expect(statusLabel("К ЗАПУСК")).toBe("КБ набрана");
  });

  it("keeps the tones of the two project marks", () => {
    expect(statusTone("ТК готова")).toBe("info");
    expect(statusTone("КБ набрана")).toBe("info");
    expect(statusTone("ТК не готова")).toBe("neutral");
    expect(statusTone("КБ не набрана")).toBe("neutral");
    expect(statusTone("Пауза")).toBe("warning");
  });

  it("leaves an unknown word neutral", () => {
    expect(statusTone("Что-то новое")).toBe("neutral");
    expect(isKnownStatus("Что-то новое")).toBe(false);
    expect(statusTone("")).toBe("neutral");
    expect(statusTone(null)).toBe("neutral");
    expect(statusLabel(undefined)).toBe("");
  });

  it("uses only the five tones", () => {
    for (const word of STATUS_WORDS) expect(TONES).toContain(statusTone(word));
  });
});

describe("deadlineTone", () => {
  it("is red only when the deadline burns", () => {
    expect(deadlineTone(-1)).toBe("danger");
    expect(deadlineTone(0)).toBe("warning");
    expect(deadlineTone(3)).toBe("warning");
    expect(deadlineTone(4)).toBe("neutral");
    expect(deadlineTone(20)).toBe("neutral");
    expect(deadlineTone(null)).toBe("neutral");
    expect(deadlineTone(Number.NaN)).toBe("neutral");
  });
});
