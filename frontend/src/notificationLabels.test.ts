import { describe, expect, it } from "vitest";
import { notificationKind } from "./notificationLabels";

describe("notificationKind", () => {
  it("names every kind the backend producers send in Russian", () => {
    const labels = ["digest", "disposal", "error", "nonconformity", "quarantine", "ready", "ship", "security", "leave", "status", "info",
      "arrival", "production"]
      .map((type) => notificationKind(type).label);
    expect(labels).toEqual([
      "Сводка", "Утилизация", "Ошибка", "Несоответствие", "Карантин", "Готово к выдаче",
      "Отгрузка", "Безопасность", "Отпуск и отгул", "Статус заявки", "Сообщение", "Приход под проект", "Цех",
    ]);
    for (const label of labels) expect(label).not.toMatch(/[A-Za-z]/);
  });

  it("falls back to «Событие» instead of showing an unknown code", () => {
    expect(notificationKind("brand_new_kind").label).toBe("Событие");
    expect(notificationKind("").label).toBe("Событие");
    expect(notificationKind(undefined).label).toBe("Событие");
    expect(notificationKind(" Digest ").label).toBe("Сводка");
  });
});
