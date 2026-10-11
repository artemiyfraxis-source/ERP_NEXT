import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TemplateLayersPanelV2, normalizeTemplateLayerNameV2 } from "./TemplateLayersPanelV2";
import type { LayerV2 } from "./template-model-v2";

const layers: readonly LayerV2[] = [
  { id: "00000000-0000-4000-8000-000000000001", name: "Подложка", visible: false, locked: true, nodes: [] },
  { id: "00000000-0000-4000-8000-000000000002", name: "Основной", visible: true, locked: false, nodes: [] },
  { id: "00000000-0000-4000-8000-000000000003", name: "Подписи", visible: true, locked: true, nodes: [] },
];

const callbacks = {
  onActivate: () => undefined,
  onAdd: () => undefined,
  onRename: () => undefined,
  onMove: () => undefined,
  onToggleVisible: () => undefined,
  onToggleLocked: () => undefined,
  onDelete: () => undefined,
};

function render(value: readonly LayerV2[] = layers, activeLayerId: string | null = layers[1]!.id): string {
  return renderToStaticMarkup(createElement(TemplateLayersPanelV2, {
    layers: value,
    activeLayerId,
    ...callbacks,
  }));
}

describe("TemplateLayersPanelV2", () => {
  it("renders a controlled active layer and exposes hidden and locked states", () => {
    const markup = render();

    expect(markup).toContain('aria-label="Слои шаблона"');
    expect(markup).toContain(`data-layer-id="${layers[1]!.id}"`);
    expect(markup).toContain('class="template-layer-v2 active"');
    expect(markup).toContain('data-visible="false" data-locked="true"');
    expect(markup).toContain("скрыт, заблокирован");
    expect(markup).toContain('aria-label="Показать слой Подложка"');
    expect(markup).toContain('aria-label="Разблокировать слой Подложка"');
    expect(markup).toContain('aria-label="Скрыть слой Основной"');
    expect(markup).toContain('aria-label="Заблокировать слой Основной"');
  });

  it("offers add and inline rename entry points", () => {
    const markup = render();

    expect(markup).toContain('aria-label="Добавить слой"');
    expect(markup).toContain('aria-label="Переименовать слой Основной"');
    expect(markup).toContain('title="Переименовать слой"');
    expect(markup).toContain('aria-label="Переименовать слой Подложка" title="Сначала разблокируйте слой" disabled=""');
  });

  it("maps visual up/down controls to valid adjacent target positions", () => {
    const markup = render();

    expect(markup).toContain('aria-label="Переместить слой Подложка ниже" title="Ниже" disabled=""');
    expect(markup).toContain('aria-label="Переместить слой Подложка выше" title="Выше" disabled=""');
    expect(markup).toContain('aria-label="Переместить слой Подписи выше" title="Выше" disabled=""');
    expect(markup).toContain('aria-label="Переместить слой Основной ниже" title="Ниже"');
    expect(markup).toContain('aria-label="Переместить слой Основной выше" title="Выше"');
  });

  it("disables delete only when the last layer remains", () => {
    const unlockedSingle = { ...layers[0]!, locked: false };
    const single = render([unlockedSingle], unlockedSingle.id);
    const several = render();

    expect(single).toContain('aria-label="Удалить слой Подложка"');
    expect(single).toContain('title="В виде должен остаться хотя бы один слой" disabled=""');
    expect(several).toContain('aria-label="Удалить слой Основной" title="Удалить слой"');
    expect(several).toContain('aria-label="Удалить слой Подложка" title="Сначала разблокируйте слой" disabled=""');
    expect(several).not.toContain('title="Удалить слой" disabled=""');
  });

  it("normalizes inline rename values before dispatch", () => {
    expect(normalizeTemplateLayerNameV2("  Силуэт  ")).toBe("Силуэт");
    expect(normalizeTemplateLayerNameV2("   ")).toBeNull();
  });

  it("shows an explicit empty state while the owner creates the required first layer", () => {
    const markup = render([], null);

    expect(markup).toContain("Добавьте первый слой.");
    expect(markup).toContain('role="status"');
  });
});
