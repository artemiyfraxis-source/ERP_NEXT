// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { EditorSceneObject } from "./editor-types";
import { ObjectInspector, parseWireCorrectionDraft } from "./ObjectInspector";

function wireObject(metadata: Readonly<Record<string, string>>): EditorSceneObject {
  return {
    id: "wire-1",
    layerId: "wires",
    kind: "wire",
    label: "CAN-H",
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    color: "#334155",
    metadata,
  };
}

function inputTag(markup: string, ariaLabel: string): string {
  const marker = `aria-label="${ariaLabel}"`;
  const markerIndex = markup.indexOf(marker);
  expect(markerIndex).toBeGreaterThanOrEqual(0);
  return markup.slice(markup.lastIndexOf("<input", markerIndex), markup.indexOf(">", markerIndex) + 1);
}

describe("wire object inspector", () => {
  it("keeps intermediate signed correction text out of the document until it is a number", () => {
    expect(parseWireCorrectionDraft("")).toBeNull();
    expect(parseWireCorrectionDraft("-")).toBeNull();
    expect(parseWireCorrectionDraft("-2")).toBe(-2);
    expect(parseWireCorrectionDraft("-2.001")).toBe(-2.001);
  });

  it("shows editable source values and the calculated cut length", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "drawing",
      selectedObject: wireObject({
        lengthKnown: "true",
        lengthMm: "1000.1",
        endCorrectionFromMm: "10",
        endCorrectionToMm: "20",
        cutRoundingStepMm: "1",
        cutLengthMm: "1031",
        materialStatus: "included",
      }),
      disabled: false,
      onChange: vi.fn(),
    }));

    expect(inputTag(markup, "Длина задана")).toContain("checked");
    expect(inputTag(markup, "Абсолютная длина, мм")).toContain('value="1000.1"');
    expect(inputTag(markup, "Поправка начала, мм")).toContain('value="10"');
    expect(inputTag(markup, "Поправка начала, мм")).toContain('type="text"');
    expect(inputTag(markup, "Поправка начала, мм")).toContain('inputMode="decimal"');
    expect(inputTag(markup, "Поправка конца, мм")).toContain('value="20"');
    expect(markup).toContain("Расчётная длина резки");
    expect(markup).toContain("1031");
    expect(markup).toContain("Длина готова для карты резки");
  });

  it("shows L+ and L- beside both connected wire ends without disabling manual correction fields", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "drawing",
      selectedObject: wireObject({ lengthKnown: "true", lengthMm: "100", endCorrectionFromMm: "1", endCorrectionToMm: "2", cutLengthMm: "103" }),
      disabled: false,
      onChange: vi.fn(),
      onTerminalCorrectionApply: vi.fn(),
      terminalCorrections: {
        from: { terminalArticle: "T-A", plusMm: 1, minusMm: -0.5 },
        to: { terminalArticle: "T-B", plusMm: 2, minusMm: -0.75 },
      },
    }));

    expect(inputTag(markup, "Поправка начала, мм")).toContain('value="1"');
    expect(inputTag(markup, "Поправка конца, мм")).toContain('value="2"');
    expect(markup).toContain('aria-label="Подставить L+ для начала провода"');
    expect(markup).toContain('aria-label="Подставить L- для начала провода"');
    expect(markup).toContain('aria-label="Подставить L+ для конца провода"');
    expect(markup).toContain('aria-label="Подставить L- для конца провода"');
  });

  it("does not expose drawing length controls on the E4 schematic", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "e4",
      selectedObject: wireObject({
        lengthKnown: "false",
        lengthMm: "",
        endCorrectionFromMm: "0",
        endCorrectionToMm: "0",
        cutRoundingStepMm: "1",
        cutLengthMm: "",
        materialStatus: "excluded",
      }),
      disabled: false,
      onChange: vi.fn(),
    }));

    expect(markup).not.toContain("Длина задана");
    expect(markup).not.toContain("Абсолютная длина, мм");
    expect(markup).not.toContain("Поправка начала, мм");
    expect(markup).not.toContain("Поправка конца, мм");
    expect(markup).not.toContain("Шаг округления длины резки, мм");
    expect(markup).not.toContain("Расчётная длина резки");
    expect(markup).toContain("Цепь / обозначение");
    expect(markup).toContain("Цвет");
  });

  it("offers all standard wire colors and keeps the full custom palette", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "e4", selectedObject: wireObject({ lengthKnown: "false" }), disabled: false, onChange: vi.fn(),
    }));
    expect(markup).toContain('aria-label="Стандартные цвета проводов"');
    for (const color of ["красный", "черный", "белый", "оранжевый", "зеленый", "фиолетовый", "желтый",
      "голубой", "синий", "коричневый", "серый", "розовый", "бирюзовый"]) {
      expect(markup).toContain(`aria-label="${color}"`);
    }
    expect(inputTag(markup, "Цвет объекта")).toContain('type="color"');
  });

  it("keeps the removed wire summary card out of the inspector", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "e4",
      selectedObject: wireObject({
        materialSourceKey: "UL1061-24AWG",
        materialDisplayName: "UL1061 24AWG",
        materialEntityType: "wire",
      }),
      disabled: false,
      onChange: vi.fn(),
      onWireMaterialClear: vi.fn(),
    }));

    expect(markup).not.toContain('aria-label="Материал провода"');
    expect(markup).not.toContain("UL1061 24AWG");
    expect(markup).not.toContain("Очистить материал");
  });

  it("does not render the removed empty material card", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "e4", selectedObject: wireObject({}), disabled: false, onChange: vi.fn(),
    }));
    expect(markup).not.toContain("Материал не выбран");
    expect(markup).not.toContain("нижнем справочнике");
  });

  it("offers mark and exact database position when an E4 wire is selected", () => {
    const markup=renderToStaticMarkup(createElement(ObjectInspector,{
      view:"e4",selectedObject:wireObject({}),disabled:false,onChange:vi.fn(),
      wireMaterialOptions:[{id:"position",mark:"МГТФ",section:"0,35",label:"МГТФ · 0,35",detail:"",
        materialBinding:{sourceId:"technology-wires",snapshotId:"11111111-1111-4111-8111-111111111111",
          snapshotSha256:"a".repeat(64),recordId:"position",entityType:"wire",sourceKey:"МГТФ",displayName:"МГТФ · 0,35"}}],
      onWireMaterialSelect:vi.fn(),
    }));
    expect(markup).toContain('aria-label="Марка материала провода"');
    expect(markup).toContain('value="МГТФ"');
    expect(markup).toContain('aria-label="Позиция материала провода"');
  });

  it("offers an end selector for a drawing wire strip profile", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "drawing",
      selectedObject: wireObject({ lengthKnown: "false" }),
      disabled: false,
      onChange: vi.fn(),
      activeWireStripEnd: "from",
      onActiveWireStripEndChange: vi.fn(),
      onWireStripProfileClear: vi.fn(),
      wireStripProfiles: {
        from: {
          sourceId: "technology-database",
          snapshotId: "00000000-0000-4000-8000-000000000099",
          snapshotSha256: "a".repeat(64),
          recordId: "b".repeat(64),
          entityType: "coax-termination",
          sourceKey: "BNC|RG58|6|9",
          displayName: "BNC / RG58",
          layers: [
            { index: 1, diameterMm: 0.9, stripLengthMm: 6 },
            { index: 2, diameterMm: 3.1, stripLengthMm: 9 },
          ],
        },
      },
    }));

    expect(markup).toContain("Оконцовка свободного конца");
    expect(markup).toContain('aria-label="Начало провода: BNC / RG58"');
    expect(markup).toContain('aria-label="Конец провода: профиль не выбран"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain("Очистить оконцовку");
  });

  it("keeps strip profiles out of E4 and explains an empty drawing end", () => {
    const e4 = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "e4", selectedObject: wireObject({}), disabled: false, onChange: vi.fn(),
    }));
    expect(e4).not.toContain("Профиль разделки провода");

    const drawing = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "drawing", selectedObject: wireObject({ lengthKnown: "false" }), disabled: true, onChange: vi.fn(),
      activeWireStripEnd: "to", onActiveWireStripEndChange: vi.fn(),
    }));
    expect(drawing).toContain("профиль не выбран");
    expect(drawing).toContain("дважды щёлкните профиль полуфабриката");
    expect(drawing).toContain("disabled");
  });

  it("explains why a saved drawing strip profile is not visible", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "drawing",
      selectedObject: wireObject({ lengthKnown: "false", stripProfileDisplayWarning: "from" }),
      disabled: false,
      onChange: vi.fn(),
    }));

    expect(markup).not.toContain("Разделку невозможно показать");
    expect(markup).not.toContain("нет достаточно длинного направленного участка");
  });

  it("omits coordinates and color from connector properties", () => {
    const markup = renderToStaticMarkup(createElement(ObjectInspector, {
      view: "drawing",
      selectedObject: {
        id: "connector-1", layerId: "connectors", kind: "connector", label: "XS5",
        x: 1983.3, y: 316.6, width: 100, height: 80, color: "#416579",
      },
      disabled: false,
      onChange: vi.fn(),
    }));
    expect(markup).toContain("Обозначение");
    expect(markup).not.toContain('aria-label="Цвет объекта"');
    expect(markup).not.toContain(">X<");
    expect(markup).not.toContain(">Y<");
    expect(markup).toContain("Слой");
    expect(markup).toContain("Представление");
  });
});
