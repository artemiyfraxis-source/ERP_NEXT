// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { EditorToolbar } from "./EditorToolbar";

describe("EditorToolbar contextual ribbon", () => {
  it("starts on drawing and exposes the four accessible context tabs", () => {
    const markup = renderToStaticMarkup(createElement(EditorToolbar, {
      view: "drawing",
      activeTool: "select",
      zoom: 1,
      onToolChange: vi.fn(),
      onZoomIn: vi.fn(),
      onZoomOut: vi.fn(),
      onZoomChange: vi.fn(),
      onFitView: vi.fn(),
      onSnapsChange: vi.fn(),
      snaps: { corners: true, contours: false, tangents: true },
    }));

    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('aria-label="Контекст инструментов"');
    expect(markup).toContain('id="he-toolbar-tab-drawing"');
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain("Рисование");
    expect(markup).toContain("Правка");
    expect(markup).toContain("Вид");
    expect(markup).toContain("Привязки");
    expect(markup).toContain('id="he-toolbar-panel-drawing"');
    expect(markup).toContain('aria-label="Соединитель, клавиша C"');
    expect(markup).toContain('aria-label="Линия"');
    expect(markup).toContain('id="he-toolbar-panel-edit"');
    expect(markup).toContain('id="he-toolbar-panel-view"');
    expect(markup).toContain('id="he-toolbar-panel-snap"');
  });
});
