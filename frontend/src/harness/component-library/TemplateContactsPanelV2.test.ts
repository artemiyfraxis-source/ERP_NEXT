import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { TemplateContactsPanelV2, type TemplateContactsPanelV2Props } from "./TemplateContactsPanelV2";
import { constantExpressionV2, newTemplateContentV2 } from "./template-commands-v2";
import type { TemplateContentV2 } from "./template-model-v2";

function fixture(linkDrawing = false): TemplateContentV2 {
  const content = newTemplateContentV2();
  const firstId = crypto.randomUUID(), secondId = crypto.randomUUID();
  content.logicalContacts.push(
    { id: firstId, number: "1", name: "Питание", contactType: "power" },
    { id: secondId, number: "2", name: "Сигнал", contactType: "signal" },
  );
  content.views[0]!.contactPoints.push({
    id: crypto.randomUUID(), logicalContactId: firstId,
    x: constantExpressionV2(20), y: constantExpressionV2(30), direction: "right",
  });
  if (linkDrawing) content.views[1]!.contactPoints.push({
    id: crypto.randomUUID(), logicalContactId: firstId,
    x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "left",
  });
  content.views[1]!.bundlePorts.push({
    id: crypto.randomUUID(), name: "Кабель",
    x: constantExpressionV2(80), y: constantExpressionV2(90), direction: "down",
  });
  return content;
}

function render(content: TemplateContentV2, activeViewId: string, overrides: Partial<TemplateContactsPanelV2Props> = {}): string {
  return renderToStaticMarkup(createElement(TemplateContactsPanelV2, {
    content,
    activeViewId,
    onCreateLogicalContact: vi.fn(),
    onPlaceLinkedContact: vi.fn(),
    onAddBundlePort: vi.fn(),
    onNavigateToMissingPoint: vi.fn(),
    ...overrides,
  }));
}

describe("TemplateContactsPanelV2", () => {
  it("shows explicit create, linked placement and bundle actions", () => {
    const content = fixture();
    const markup = render(content, content.views[0]!.id);

    expect(markup).toContain('aria-label="Контакты вида Схема Э4"');
    expect(markup).toContain('aria-label="Создать логический контакт"');
    expect(markup).toContain('aria-label="Логический контакт для размещения"');
    expect(markup).toContain('aria-label="Разместить связанную точку"');
    expect(markup).toContain('aria-label="Добавить общий выход пучка"');
    expect(markup).toContain("№2 · Сигнал · ещё не размещён");
  });

  it("names source views for contacts available to the drawing", () => {
    const content = fixture();
    const markup = render(content, content.views[1]!.id);

    expect(markup).toContain("№1 · Питание · Схема Э4");
    expect(markup).toContain("№2 · Сигнал · ещё не размещён");
    expect(markup).toContain('aria-label="Выбрать общий выход пучка: Кабель"');
    expect(markup).toContain("◆");
    expect(markup).toMatch(/<button(?=[^>]*aria-label="Создать логический контакт")(?=[^>]*disabled="")[^>]*>/);
  });

  it("renders advisory coverage navigation with addressable labels", () => {
    const content = fixture(true);
    const markup = render(content, content.views[0]!.id);

    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('aria-label="Диагностика точек подключения"');
    expect(markup).toContain("Контакт №2 не размещён в виде Схема Э4.");
    expect(markup).toContain("Контакт №2 не размещён в виде Чертёж.");
    expect(markup).toContain('aria-label="Перейти к отсутствующей точке контакта 2 в виде Чертёж"');
  });

  it("shows active-view points separately from bundle ports", () => {
    const content = fixture(true);
    const drawing = content.views[1]!;
    const markup = render(content, drawing.id);

    expect(markup).toContain('aria-label="Выбрать точку контакта 1: Питание"');
    expect(markup).toContain('aria-label="Выбрать общий выход пучка: Кабель"');
    expect(markup).toContain("Точки контактов");
    expect(markup).toContain("Общие выходы пучка");
  });

  it("handles an absent active view without exposing mutation actions", () => {
    const content = fixture();
    const markup = render(content, crypto.randomUUID());

    expect(markup).toContain("Активный вид не найден.");
    expect(markup).not.toContain("Разместить связанную точку");
  });
});
