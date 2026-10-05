// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { LayersPanel, resolveLayerDropIndex } from "./LayersPanel";

describe("layers panel", () => {
  it("offers an explicit hide or show action for every layer", () => {
    const markup = renderToStaticMarkup(createElement(LayersPanel, {
      layers: [
        { id: "visible", label: "Провода", visible: true, locked: false },
        { id: "hidden", label: "Размеры", visible: false, locked: false },
      ],
      onVisibilityToggle: vi.fn(),
      onLockToggle: vi.fn(),
      onMove: vi.fn(),
    }));

    expect(markup).toContain('aria-label="Скрыть слой Провода"');
    expect(markup).toContain('aria-label="Показать слой Размеры"');
    expect(markup).toContain('aria-label="Заблокировать слой Провода"');
    expect(markup).toContain('class="he-layer-lock-icon"');
    expect(markup).not.toContain("◌");
    expect(markup).not.toContain("●");
    expect(markup).toContain("Скрыть");
    expect(markup).toContain("Показать");
    expect(markup).toContain('class="he-layer-row is-hidden"');
  });

  it("uses drag handles and explicit insertion slots instead of order arrows", () => {
    const markup = renderToStaticMarkup(createElement(LayersPanel, {
      layers: [
        { id: "first", label: "Провода", visible: true, locked: false },
        { id: "second", label: "Размеры", visible: true, locked: false },
      ],
      onVisibilityToggle: vi.fn(),
      onLockToggle: vi.fn(),
      onMove: vi.fn(),
    }));

    expect(markup).toContain('class="he-drag-handle"');
    expect(markup).toContain('draggable="true"');
    expect(markup.match(/he-layer-drop-slot/g)).toHaveLength(3);
    expect(markup).not.toContain("he-layer-order-controls");
    expect(markup).not.toContain("Поднять слой");
    expect(markup).not.toContain("Опустить слой");
  });

  it("normalizes insertion slots after removing the dragged layer", () => {
    const layers = [
      { id: "first", label: "Первый", visible: true, locked: false },
      { id: "second", label: "Второй", visible: true, locked: false },
      { id: "third", label: "Третий", visible: true, locked: false },
    ];

    expect(resolveLayerDropIndex(layers, "first", 2)).toBe(1);
    expect(resolveLayerDropIndex(layers, "third", 1)).toBe(1);
    expect(resolveLayerDropIndex(layers, "first", 3)).toBe(2);
    expect(resolveLayerDropIndex(layers, "second", 1)).toBe(1);
  });
});
