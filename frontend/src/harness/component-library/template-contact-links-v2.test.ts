import { describe, expect, it } from "vitest";
import { constantExpressionV2, newTemplateContentV2 } from "./template-commands-v2";
import {
  availableLogicalContactsForViewV2,
  collectContactCoverageDiagnosticsV2,
  contactPointsWithLogicalV2,
  logicalContactRepresentationsV2,
} from "./template-contact-links-v2";
import type { TemplateContentV2, ViewContactPointV2 } from "./template-model-v2";

function withContact(): { content: TemplateContentV2; logicalId: string; pointId: string } {
  const content = newTemplateContentV2();
  const logicalId = crypto.randomUUID(), pointId = crypto.randomUUID();
  content.logicalContacts.push({ id: logicalId, number: "1", name: "Питание", contactType: "power" });
  content.views[0]!.contactPoints.push({
    id: pointId,
    logicalContactId: logicalId,
    x: constantExpressionV2(20),
    y: constantExpressionV2(30),
    direction: "right",
  });
  return { content, logicalId, pointId };
}

describe("template contact links v2", () => {
  it("lists only logical contacts not represented in the target view", () => {
    const { content, logicalId, pointId } = withContact();
    const e4 = content.views[0]!, drawing = content.views[1]!;

    expect(availableLogicalContactsForViewV2(content, e4.id)).toEqual([]);
    expect(availableLogicalContactsForViewV2(content, drawing.id)).toEqual([{
      contact: content.logicalContacts[0],
      representedIn: [{
        logicalContactId: logicalId,
        pointId,
        viewId: e4.id,
        viewName: e4.name,
        viewKind: "e4",
      }],
    }]);
    expect(availableLogicalContactsForViewV2(content, crypto.randomUUID())).toEqual([]);
  });

  it("reports missing points in mandatory E4 and drawing views", () => {
    const { content, logicalId } = withContact();
    const diagnostics = collectContactCoverageDiagnosticsV2(content);

    expect(diagnostics).toEqual([expect.objectContaining({
      code: "missing_linked_contact_point",
      logicalContactId: logicalId,
      viewId: content.views[1]!.id,
      viewKind: "drawing",
      message: "Контакт №1 не размещён в виде Чертёж.",
    })]);
  });

  it("does not let a bundle port satisfy contact coverage and ignores additional views", () => {
    const { content, logicalId } = withContact();
    const drawing = content.views[1]!;
    drawing.bundlePorts.push({
      id: crypto.randomUUID(), name: "Общий выход",
      x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "left",
    });
    content.views.push({
      id: crypto.randomUUID(), name: "Вид контактов", kind: "additional",
      layers: [{ id: crypto.randomUUID(), name: "Основной", visible: true, locked: false, nodes: [] }],
      contactPoints: [], bundlePorts: [], repeatPlacements: [],
    });

    const diagnostics = collectContactCoverageDiagnosticsV2(content);
    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({ logicalContactId: logicalId, viewId: drawing.id });
  });

  it("clears diagnostics once the same logical contact is linked into both mandatory views", () => {
    const { content, logicalId } = withContact();
    const drawingPoint: ViewContactPointV2 = {
      id: crypto.randomUUID(), logicalContactId: logicalId,
      x: constantExpressionV2(40), y: constantExpressionV2(50), direction: "left",
    };
    content.views[1]!.contactPoints.push(drawingPoint);

    expect(collectContactCoverageDiagnosticsV2(content)).toEqual([]);
    expect(logicalContactRepresentationsV2(content, logicalId).map(item => item.pointId))
      .toEqual([content.views[0]!.contactPoints[0]!.id, drawingPoint.id]);
  });

  it("keeps malformed point references visible for structural diagnostics", () => {
    const content = newTemplateContentV2();
    const logicalContactId = crypto.randomUUID();
    content.views[0]!.contactPoints.push({
      id: crypto.randomUUID(), logicalContactId,
      x: constantExpressionV2(1), y: constantExpressionV2(2), direction: "up",
    });

    expect(contactPointsWithLogicalV2(content, content.views[0]!.id)).toEqual([{
      point: content.views[0]!.contactPoints[0], contact: null,
    }]);
  });
});
