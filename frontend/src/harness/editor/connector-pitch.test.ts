// @vitest-environment node
// «Реальные размеры, мм»: how many template units a library drawing has per mm, measured from its contact centres
// against the pitch of its ERP connector family (harness catalog «connector-family»).
import { describe, expect, it } from "vitest";
import seedFile from "../../../../backend/data/harness-library/seed.json";
import catalogFixture from "../erpCatalog.fixture.json";
import type { ReferenceCatalogRecord } from "../reference-catalog";
import type { TemplateContentV3 } from "../component-library/template-model-v3";
import { parseComponentTemplateContent } from "../component-library/template-content";
import { createConnectorInstanceFromComponentTemplateV3 } from "./component-template-placement";
import { connectorPitchMm, drawingContactCentres, templateUnitsPerMm, templateUnitsPerMmByConnector } from "./connector-pitch";
import { connectorsWithoutRealSize, drawingScale, realSizeView } from "./drawing-scale";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "./model";

interface SeedTemplate {
  templateId: string; revision: number; code: string; name: string;
  articleBindings: { sourceId: string; entityType: string; articleKey: string }[];
  assets: { assetId: string; fileName: string; mediaType: "image/png"; sha256: string; sizeBytes: number }[];
  content: TemplateContentV3;
}
const seed = seedFile as unknown as { templates: SeedTemplate[] };
const fixtureRecords = (catalogFixture as unknown as { snapshots: { sourceId: string; records: ReferenceCatalogRecord[] }[] })
  .snapshots.find(snapshot => snapshot.sourceId === "technology-connectors")!.records;
// As the ERP publishes JST SH (`connector_housings` «jst-sh», test_harness_catalog checks it).
const jstSh: ReferenceCatalogRecord = { recordId: "sh", entityType: "connector-family", sourceKey: "JST SH", sourceLocation: null,
  payload: { familyId: "jst-sh", pattern: "^SHR-?0*(\\d+)V-S", pitchMm: 1 } };
const records = [...fixtureRecords, jstSh];

function placed(code: string, id = "XS1") {
  const item = seed.templates.find(template => template.code === code)!;
  const content = parseComponentTemplateContent(item.content) as TemplateContentV3;
  const connector = createConnectorInstanceFromComponentTemplateV3({
    templateId: item.templateId, version: item.revision, versionSha256: "a".repeat(64), code: item.code,
    name: item.name, articleBindings: item.articleBindings, assets: item.assets, content,
  }, { id, designation: id, e4Position: { x: 0, y: 0 } });
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template") throw new Error("no template binding");
  return { connector, instance: { objectId: id, snapshotId: "s", articleVariantId: binding.articleVariantId, content } };
}
/** Width of the template's drawing picture in template units. */
const drawingWidth = (content: TemplateContentV3) => {
  const view = content.views.find(candidate => candidate.kind === "drawing")!;
  const node = view.layers.flatMap(layer => layer.nodes).find(candidate => candidate.kind === "image")!;
  return ((node.geometry as unknown as { width: { value: number } }).width).value;
};

describe("connector pitch from the ERP base of connectors", () => {
  it("matches the family after the analogues, unknown or pitchless → null", () => {
    expect(connectorPitchMm("SHR-04V-S", records)).toBe(1);
    expect(connectorPitchMm("JST xhp-4", records)).toBe(2.5);
    expect(connectorPitchMm("BL2018P-04", records)).toBe(2.5); // analogue of JST SMP-04V-BC
    expect(connectorPitchMm("LP-12-C04PE", records)).toBeNull(); // the family has no single pitch
    expect(connectorPitchMm("ABC-123", records)).toBeNull();
    expect(connectorPitchMm("", records)).toBeNull();
  });

  it("units per mm = median nearest-neighbour distance of contact centres / pitch", () => {
    expect(templateUnitsPerMm([{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }, { x: 15, y: 0 }], 2.5)).toBe(2);
    // A two-row grid: the nearest neighbour is one pitch in either direction.
    expect(templateUnitsPerMm([{ x: 0, y: 0 }, { x: 6, y: 0 }, { x: 0, y: 6 }, { x: 6, y: 6 }], 3)).toBe(2);
    expect(templateUnitsPerMm([{ x: 0, y: 0 }], 1)).toBeNull();
    expect(templateUnitsPerMm([{ x: 0, y: 0 }, { x: 0, y: 0 }], 1)).toBeNull();
    expect(templateUnitsPerMm([{ x: 0, y: 0 }, { x: 4, y: 0 }], null)).toBeNull();
  });
});

describe("JST SH SHR-04V-S of the component library in real size", () => {
  it("measures 13,6 units per mm, so its drawing comes out at the housing's real width", () => {
    const { connector, instance } = placed("SHR-04V-S");
    expect(drawingContactCentres(connector, instance)).toHaveLength(4);
    const document: HarnessDesignDocument = { ...createEmptyHarnessDesign(), connectors: [connector] };
    const units = templateUnitsPerMmByConnector(document, [instance], records);
    expect(units.get("XS1")).toBeCloseTo(13.6, 6);
    // JST eSH housing: A = (4 − 1) × 1,0 = 3 mm; B = A + 2 … A + 4 (with the protrusions) = 5…7 mm.
    const widthMm = drawingWidth(instance.content) / units.get("XS1")!;
    expect(widthMm).toBeGreaterThanOrEqual(5);
    expect(widthMm).toBeLessThanOrEqual(7);
    // At М 1:2 the field draws it at 1/(2 × 13,6) of its template size; nothing is counted as «без размеров».
    const real = { ...document, drawingDocuments: { tables: [], leaders: [], bomOrder: [], realSize: { enabled: true, scaleDenominator: 2 } } };
    expect(drawingScale(realSizeView(real, units).connectors[0]!.drawingPlacements)).toBeCloseTo(1 / 27.2, 9);
    expect(connectorsWithoutRealSize(real, units)).toBe(0);
  });

  it("an unknown pitch falls back to 1 unit = 1 mm and is counted", () => {
    const { connector, instance } = placed("SHR-04V-S");
    const document: HarnessDesignDocument = { ...createEmptyHarnessDesign(), connectors: [connector],
      drawingDocuments: { tables: [], leaders: [], bomOrder: [], realSize: { enabled: true, scaleDenominator: 2 } } };
    const units = templateUnitsPerMmByConnector(document, [instance], fixtureRecords);
    expect(units.size).toBe(0);
    expect(drawingScale(realSizeView(document, units).connectors[0]!.drawingPlacements)).toBe(.5);
    expect(connectorsWithoutRealSize(document, units)).toBe(1);
  });
});
