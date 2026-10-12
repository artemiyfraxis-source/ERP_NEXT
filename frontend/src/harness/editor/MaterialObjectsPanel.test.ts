// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MaterialObjectsPanel, materialObjectGroup } from "./MaterialObjectsPanel";
import type { EditorSceneObject } from "./editor-types";

const object = (id: string, kind: EditorSceneObject["kind"], label = id): EditorSceneObject => ({
  id, kind, label, layerId: "layer", x: 0, y: 0, width: 0, height: 0, color: "#333333",
});

describe("material objects panel", () => {
  it("groups material objects and excludes dimensions, exits, pipes, and other drawing structure", () => {
    const objects = [
      object("XS1", "connector"), object("W1", "wire"), object("cover", "physical-covering"),
      object("mark", "graphic-text"), object("dim", "dimension"), object("exit", "physical-node", "Выход"),
      object("pipe", "physical-segment"), object("line", "graphic-line"),
    ];
    const markup = renderToStaticMarkup(createElement(MaterialObjectsPanel, {
      objects, hiddenObjectIds: ["W1"], selectedObjectIds: ["XS1"], onVisibilityChange: vi.fn(),
      onObjectSelect: vi.fn(),
    }));

    expect(markup).toContain("Соединители");
    expect(markup).toContain("Провода");
    expect(markup).toContain("Оболочки");
    expect(markup).toContain("Маркировка");
    expect(markup).toContain("XS1");
    expect(markup).toContain("cover");
    expect(markup).toContain("mark");
    expect(markup).not.toContain("Выход");
    expect(markup).not.toContain("dim");
    expect(markup).not.toContain("pipe");
    expect(markup).not.toContain("Изолировать");
    expect(markup).toContain('aria-label="Показать W1"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('aria-label="Выбрать XS1"');
  });

  it("exposes classification for shared drawing and E4 scene object kinds", () => {
    expect(materialObjectGroup("connector")).toBe("connectors");
    expect(materialObjectGroup("wire")).toBe("wires");
    expect(materialObjectGroup("physical-covering")).toBe("coverings");
    expect(materialObjectGroup("text")).toBe("markings");
    expect(materialObjectGroup("dimension")).toBeNull();
    expect(materialObjectGroup("physical-segment")).toBeNull();
  });
});
