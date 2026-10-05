// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { ReferenceCatalogSearchRecord } from "../reference-catalog";
import {
  builtInConnectorItems,
  componentTemplateSummaryToEditorCatalogItem,
  componentTemplateSummaryToEditorCatalogItems,
  filterComponentTemplates,
  filterBuiltInConnectors,
  normalizeCoaxTerminationCatalogCandidate,
  referenceRecordToEditorCatalogItem,
  remoteEditorCatalogSources,
  editorCatalogSources,
} from "./editor-reference-catalog";

function source(id: string) {
  const result = remoteEditorCatalogSources.find((item) => item.id === id);
  if (result) return result;
  if (id === "technology-awg-reference") return {
    id, label: "legacy", description: "legacy", entityTypes: ["awg-reference"], accent: "#356c88",
  };
  throw new Error(`missing test source ${id}`);
}

function record(entityType: string, sourceKey: string, payload: Readonly<Record<string, unknown>>): ReferenceCatalogSearchRecord {
  return { recordId: "a".repeat(64), entityType, sourceKey, payload, sourceLocation: null };
}

describe("editor reference catalog", () => {
  it("hides the old AWG diameter database and exposes the wire catalog", () => {
    expect(editorCatalogSources.some(source => source.id === "technology-awg-reference")).toBe(false);
    expect(editorCatalogSources.some(source => source.id === "technology-wires")).toBe(true);
  });
  it("shows the wire database mark and section in the catalog and pinned material name", () => {
    const item = referenceRecordToEditorCatalogItem(source("technology-wires"), record("wire", "internal-key", {
      Марка: "TEST", Core: "3C", "Сечение C": 0.5, Pair: "2P", "Сечение P": "0,22",
    }));
    expect(item.title).toBe("TEST · 3Cx0,5 | 2Px0,22");
    expect(item.referenceDisplayName).toBe(item.title);
    expect(item.sourceKey).toBe("internal-key");
  });
  it("keeps protective covering mark, size and color in the pinned display name", () => {
    const item = referenceRecordToEditorCatalogItem(source("technology-protection"), record("protective-covering", "HS-3-1", {
      Марка: "ТУТ", Сечение: "3/1 мм", Цвет: "чёрный",
    }));
    expect(item.referenceDisplayName).toBe("ТУТ · 3/1 мм · чёрный");
  });
  it("exposes exact immutable component template versions for placement", () => {
    const template = {
      templateId: "12345678-1234-4123-8123-123456789abc",
      version: 7,
      code: "JST-XH",
      name: "JST XH",
      articleBindings: [],
      createdUtc: "2026-09-14T00:00:00Z",
    };
    expect(componentTemplateSummaryToEditorCatalogItem(template)).toMatchObject({
      id: `component-template:${template.templateId}:7`,
      title: "JST-XH",
      subtitle: "JST XH · версия 7",
      placement: "connector",
      componentTemplateId: template.templateId,
      componentTemplateVersion: 7,
    });
    expect(filterComponentTemplates([template], "xh")).toHaveLength(1);
    expect(filterComponentTemplates([template], "unknown")).toEqual([]);
  });

  it("creates one family card for all article variants and keeps article search", () => {
    const template = {
      templateId: "12345678-1234-4123-8123-123456789abc",
      version: 7,
      code: "JST-XH",
      name: "JST XH",
      articleBindings: [
        { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "B2B-XH-A" },
        { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "B10B-XH-A" },
      ],
      createdUtc: "2026-09-14T00:00:00Z",
    };

    const items = componentTemplateSummaryToEditorCatalogItems(template);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      id: `component-template:${template.templateId}:7`,
      title: "JST-XH",
      subtitle: "JST XH · 2 артикула · версия 7",
      componentTemplateId: template.templateId,
      componentTemplateVersion: 7,
      componentArticles: template.articleBindings,
    });
    expect(filterComponentTemplates([template], "b10b").map((item) => item.title)).toEqual(["JST-XH"]);
    expect(filterComponentTemplates([template], "jst")).toHaveLength(1);
  });

  it("keeps built-in connector cards and searches them locally", () => {
    expect(builtInConnectorItems.map((item) => item.title)).toEqual(["Серия XS", "JST XH", "Свободный соединитель"]);
    expect(filterBuiltInConnectors("XS-10").map((item) => item.title)).toEqual(["Серия XS"]);
    expect(filterBuiltInConnectors("unknown")).toEqual([]);
  });

  it("formats terminal sourceKey, compatible section, insulation and strip length", () => {
    const item = referenceRecordToEditorCatalogItem(source("technology-terminals"), record("terminal", "TER-0007", {
      productName: "Сигнальный контакт",
      sectionFromMm2: 0.35,
      sectionToMm2: 0.5,
      insulationDiameterFromMm: "1,2",
      insulationDiameterToMm: "1,8",
      stripLengthMm: 4.5,
    }));

    expect(item.title).toBe("TER-0007");
    expect(item.subtitle).toBe("Сигнальный контакт · 0,35–0,5 мм² · 1,2–1,8 мм Ø изоляции · зачистка 4,5 мм");
    expect(item).toMatchObject({ sourceId: "technology-terminals", entityType: "terminal", placement: "reference-only" });
  });

  it("formats cable layers and AWG data without inventing missing values", () => {
    const cable = referenceRecordToEditorCatalogItem(source("technology-coax-cables"), record("coax-cable", "RG-316", {
      layers: [{ index: 1, diameterMm: 0.5 }, { index: 2, diameterMm: 1.5 }, { index: 3, diameterMm: 2.5 }],
    }));
    const awg = referenceRecordToEditorCatalogItem(source("technology-awg-reference"), record("awg-reference", "22", {
      sectionMm2: 0.35,
      conductorDiameterMm: 0.67,
    }));

    expect(cable.subtitle).toBe("D1 0,5 мм · D2 1,5 мм · D3 2,5 мм");
    expect(awg.subtitle).toBe("0,35 мм² · Ø жилы 0,67 мм");
  });

  it("keeps an incomplete published record visible and labels missing characteristics", () => {
    const item = referenceRecordToEditorCatalogItem(
      source("technology-terminals"),
      record("terminal", "TER-INCOMPLETE", {}),
    );

    expect(item.title).toBe("TER-INCOMPLETE");
    expect(item.subtitle).toContain("характеристики не заполнены");
  });

  it("carries the exact immutable snapshot identity needed to pin a wire material", () => {
    const snapshotId = "00000000-0000-4000-8000-000000000099";
    const snapshotSha256 = "a".repeat(64);
    const item = referenceRecordToEditorCatalogItem(
      source("technology-database"),
      record("wire", "UL1061-24AWG", { name: "UL1061 24AWG" }),
      { snapshotId, snapshotSha256 },
    );

    expect(item).toMatchObject({
      sourceId: "technology-database", snapshotId, snapshotSha256,
      recordId: "a".repeat(64),
      entityType: "wire", sourceKey: "UL1061-24AWG",
    });
  });

  it("normalizes complete coax termination layers without compacting index gaps", () => {
    const snapshot = {
      snapshotId: "00000000-0000-4000-8000-000000000099",
      snapshotSha256: "b".repeat(64),
    };
    const sourceRecord = record("coax-termination", "BNC · RG58 #1", {
      layers: [
        { index: 1, diameterMm: "1,2", stripLengthMm: "2,5" },
        { index: 3, diameterMm: 3.5, stripLengthMm: 7.5 },
      ],
    });

    const candidate = normalizeCoaxTerminationCatalogCandidate(
      "technology-coax-terminations", sourceRecord, snapshot,
    );

    expect(candidate).toEqual({
      state: "ready",
      diagnostics: [],
      layers: [
        { index: 1, diameterMm: 1.2, stripLengthMm: 2.5 },
        { index: 3, diameterMm: 3.5, stripLengthMm: 7.5 },
      ],
      binding: {
        sourceId: "technology-coax-terminations",
        ...snapshot,
        recordId: "a".repeat(64),
        entityType: "coax-termination",
        sourceKey: "BNC · RG58 #1",
        layers: [
          { index: 1, diameterMm: 1.2, stripLengthMm: 2.5 },
          { index: 3, diameterMm: 3.5, stripLengthMm: 7.5 },
        ],
      },
    });
  });

  it("keeps a partial coax termination visible with diagnostics and no binding", () => {
    const item = referenceRecordToEditorCatalogItem(
      source("technology-coax-terminations"),
      record("coax-termination", "BNC · RG58 incomplete", {
        layers: [
          { index: 1, diameterMm: 1.2, stripLengthMm: 2.5 },
          { index: 3, stripLengthMm: 7.5 },
        ],
      }),
      { snapshotId: "00000000-0000-4000-8000-000000000099", snapshotSha256: "b".repeat(64) },
    );

    expect(item.title).toBe("BNC · RG58 incomplete");
    expect(item.subtitle).toContain("D3 — / L3 7,5 мм");
    expect(item.subtitle).toContain("данные неполные");
    expect(item.coaxTerminationCandidate).toMatchObject({
      state: "incomplete",
      binding: null,
      layers: [{ index: 1 }, { index: 3 }],
      diagnostics: [{ code: "layer-diameter-missing", layerIndex: 3 }],
    });
  });

  it("requires exact snapshot identity before a complete termination can bind", () => {
    const candidate = normalizeCoaxTerminationCatalogCandidate(
      "technology-coax-terminations",
      record("coax-termination", "BNC · RG58", {
        layers: [{ index: 1, diameterMm: 1.2, stripLengthMm: 2.5 }],
      }),
    );

    expect(candidate.state).toBe("incomplete");
    expect(candidate.binding).toBeNull();
    expect(candidate.diagnostics).toEqual([expect.objectContaining({ code: "snapshot-identity-missing" })]);
  });

  it.each([
    [{ index: 1, diameterMm: 2, stripLengthMm: 2.5 }, { index: 2, diameterMm: 1, stripLengthMm: 7.5 }],
    [{ index: 1, diameterMm: 1, stripLengthMm: 7.5 }, { index: 2, diameterMm: 2, stripLengthMm: 2.5 }],
    [{ index: 1, diameterMm: 1, stripLengthMm: 2.5001 }],
  ])("does not advertise a structurally invalid profile as ready", (...layers) => {
    const candidate = normalizeCoaxTerminationCatalogCandidate(
      "technology-coax-terminations",
      record("coax-termination", "invalid-profile", { layers }),
      { snapshotId: "00000000-0000-4000-8000-000000000099", snapshotSha256: "b".repeat(64) },
    );
    expect(candidate.state).toBe("incomplete");
    expect(candidate.binding).toBeNull();
    expect(candidate.diagnostics).toContainEqual(expect.objectContaining({ code: "layer-invalid" }));
  });
});
