// The first templates of the component library (backend/data/harness-library/seed.json, migration 0091 inserts them):
// each is a template the editor accepts and places — strict v3 validation, assets matching the envelope, every
// contact on both views, contact 1 on the right of the drawing (JST SH, wire side).
import { describe, expect, it } from "vitest";
import seedFile from "../../../../backend/data/harness-library/seed.json";
import { parseComponentTemplateContent } from "../component-library/template-content";
import { createConnectorInstanceFromComponentTemplateV3 } from "../editor/component-template-placement";
import type { TemplateContentV3 } from "../component-library/template-model-v3";

interface SeedTemplate {
  templateId: string; revision: number; code: string; name: string; connectorRef: string;
  articleBindings: { sourceId: string; entityType: string; articleKey: string }[];
  assets: { assetId: string; fileName: string; mediaType: "image/png"; sha256: string; sizeBytes: number }[];
  content: TemplateContentV3; picture: string;
}

const seed = seedFile as unknown as { templates: SeedTemplate[] };

describe("component library seed", () => {
  it("holds JST SH 2…20", () => {
    expect(seed.templates.map((item) => item.connectorRef)).toEqual(
      Array.from({ length: 19 }, (_, index) => `SHR-${String(index + 2).padStart(2, "0")}V-S`));
  });

  it.each(seed.templates.map((item) => [item.code, item] as const))("%s is a placeable template", (_code, item) => {
    const content = parseComponentTemplateContent(item.content);
    expect(content.schemaVersion).toBe(3);
    const connector = createConnectorInstanceFromComponentTemplateV3({
      templateId: item.templateId, version: item.revision, versionSha256: "a".repeat(64), code: item.code,
      name: item.name, articleBindings: item.articleBindings, assets: item.assets, content: content as TemplateContentV3,
    }, { id: "00000000-0000-4000-8000-000000000001", designation: "XS1", e4Position: { x: 0, y: 0 } });
    const count = item.content.logicalContacts.length;
    expect(connector.contacts.map((contact) => contact.number)).toEqual(Array.from({ length: count }, (_, i) => i + 1));
    expect(connector.partNumber).toBe(item.code);
    const binding = connector.libraryBinding;
    if (binding?.mode !== "template") throw new Error("no template binding");
    const drawingX = (number: number) => binding.snapshot.contacts.find((contact) => contact.sourceNumber === String(number))!
      .representations.find((representation) => representation.viewKind === "drawing")!.x;
    expect(drawingX(1)).toBeGreaterThan(drawingX(count));
    for (const contact of binding.snapshot.contacts) {
      expect(contact.representations.map((representation) => representation.viewKind).sort()).toEqual(["drawing", "e4"]);
    }
  });
});
