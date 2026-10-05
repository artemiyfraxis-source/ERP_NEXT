// @vitest-environment node
import { describe, expect, it } from "vitest";
import type {
  ComponentTemplateContactRepresentationSnapshot,
  ComponentTemplateContactSnapshot,
  ConnectorInstance,
} from "./model";
import {
  connectorContactPosition,
  connectorE4TableGeometry,
  createEmptyHarnessDesign,
  wireEndpointE4Anchor,
} from "./model";
import {
  materializedContactWorldRepresentation,
  selectMaterializedContactRepresentation,
} from "./materialized-contact-representation";
import { designToScene } from "./HarnessDesignEditor";
import { createWire } from "./commands";
import { physicalNodeDirection, physicalNodeFacingDirection, physicalNodePoint } from "./physical-ports";
import { physicalSegmentPoints } from "./physical-geometry";

it("uses current v5 table edges for E4 rendering and routing while retaining drawing points", () => {
  const original = connector();
  const table = { ...original, e4TableMode: true };
  const id = table.contacts[0]!.id;
  const point = connectorE4TableGeometry(table).contactPoints[id]!;
  const expected = { x: table.positions.e4.x + point.x, y: table.positions.e4.y + point.y };
  expect(connectorContactPosition(table, id, "e4")).toEqual(expected);
  expect(materializedContactWorldRepresentation(table, id, "e4")).toBeNull();
  expect(materializedContactWorldRepresentation(table, id, "drawing"))
    .toEqual(materializedContactWorldRepresentation(original, id, "drawing"));
  const wire = createWire("wire", { connectorId: table.id, contactId: id },
    { connectorId: table.id, contactId: table.contacts[1]!.id }, null);
  const document = { ...createEmptyHarnessDesign(), connectors: [table], wires: [wire] };
  expect(wireEndpointE4Anchor(document, wire.from)?.position).toEqual(expected);
  expect(designToScene(document, "e4", new Set(), new Set()).find(item => item.kind === "wire")?.points?.[0]).toEqual(expected);
});

const fixedLogicalId = "fixed-contact";
const repeatedPrototypeId = "repeat-contact";
const repeatedLogicalId = "repeat-domain:1:repeat-contact";

function representation(
  viewKind: "e4" | "drawing" | "additional",
  x: number,
  y: number,
  direction: "left" | "right" | "up" | "down",
  occurrenceKey?: string,
): ComponentTemplateContactRepresentationSnapshot {
  return {
    viewId: `${viewKind}-view`,
    viewName: viewKind,
    viewKind,
    pointId: `${viewKind}-point`,
    occurrenceKey,
    x,
    y,
    direction,
  };
}

function snapshotContact(
  logicalContactId: string,
  prototypeLogicalContactId: string,
  representations: readonly ComponentTemplateContactRepresentationSnapshot[],
): ComponentTemplateContactSnapshot {
  return {
    logicalContactId,
    prototypeLogicalContactId,
    sourceNumber: "1",
    name: "Контакт",
    circuitText: null,
    contactTypeGroupId: null,
    contactType: "",
    allowedTerminalArticleKeys: [],
    representations,
  };
}

function connector(): ConnectorInstance {
  const fixed = snapshotContact(fixedLogicalId, fixedLogicalId, [
    representation("e4", 12, 18, "right"),
    representation("drawing", 50, 60, "up"),
    representation("additional", 5, 6, "left"),
  ]);
  const repeated = snapshotContact(repeatedLogicalId, repeatedPrototypeId, [
    representation("e4", 30, 40, "left", "repeat-domain:0:repeat-contact"),
    representation("e4", 30, 70, "down", repeatedLogicalId),
    representation("drawing", 80, 90, "right", repeatedLogicalId),
  ]);
  return {
    id: "connector-1",
    designation: "XS1",
    libraryCode: "LIB",
    partNumber: "A-1",
    contacts: [
      {
        id: "connector-1:contact:fixed-contact",
        logicalContactId: fixedLogicalId,
        number: 1,
        contactType: "",
        circuit: "",
        terminalArticle: "",
        wire: "",
        color: "",
        connectionStatus: "available",
        customValues: {},
        libraryContact: null,
      },
      {
        id: `connector-1:contact:${repeatedLogicalId}`,
        logicalContactId: repeatedLogicalId,
        number: 2,
        contactType: "",
        circuit: "",
        terminalArticle: "",
        wire: "",
        color: "",
        connectionStatus: "available",
        customValues: {},
        libraryContact: null,
      },
    ],
    schematic: { orientation: "contacts-right", baseColumns: [], customFields: [] },
    positions: { e4: { x: 100, y: 200 }, drawing: { x: -20, y: 50 } },
    layerIds: { e4: "connectors", drawing: "connectors" },
    libraryBinding: {
      mode: "template",
      templateId: "template-1",
      templateVersion: 3,
      versionSha256: "a".repeat(64),
      articleVariantId: "variant-1",
      article: { sourceId: "source", entityType: "connector", articleKey: "A-1" },
      snapshot: {
        templateId: "template-1",
        templateVersion: 3,
        versionSha256: "a".repeat(64),
        code: "LIB",
        name: "Library connector",
        articleVariantId: "variant-1",
        article: { sourceId: "source", entityType: "connector", articleKey: "A-1" },
        articleBindings: [{ sourceId: "source", entityType: "connector", articleKey: "A-1" }],
        assets: [],
        contacts: [fixed, repeated],
      },
    },
  };
}

describe("materialized contact representations", () => {
  it.each([0,37,90,180,271])("flips an inward common-port direction for a new pipe at %s degrees", rotationDegrees => {
    const original=connector(),contact=original.contacts[0]!,binding=original.libraryBinding!;
    if(binding.mode!=="template")throw new Error("Template fixture expected");
    const snapshotContact=binding.snapshot.contacts[0]!;
    const inward={...snapshotContact,representations:snapshotContact.representations.map(r=>r.viewKind==="drawing"?{...r,direction:"right" as const}:r)};
    const instance={...original,contacts:[contact],drawingPlacements:[{drawingId:"view:drawing",offset:{x:0,y:0},visible:true,scale:1.5,rotationDegrees}],libraryBinding:{...binding,snapshot:{...binding.snapshot,contacts:[inward]}}};
    const node={id:"exit",connectorId:instance.id,position:{x:0,y:60}};
    const start=physicalNodePoint({...createEmptyHarnessDesign(),connectors:[instance]},node),a=rotationDegrees*Math.PI/180;
    const outward={x:-Math.cos(a),y:-Math.sin(a)};
    const other={id:"other",position:{x:start.x+outward.x*180,y:start.y+outward.y*180+70}};
    const document={...createEmptyHarnessDesign(),connectors:[instance],physicalTopology:{snap:true,nodes:[node,other],segments:[],routes:[]}};
    const direction=physicalNodeDirection(document,node)!;
    expect(direction.x).toBeCloseTo(outward.x,6);expect(direction.y).toBeCloseTo(outward.y,6);
    const marker=physicalNodeFacingDirection(document,node)!;
    expect(marker.x*outward.x+marker.y*outward.y).toBeLessThan(0);
    const points=physicalSegmentPoints(document,{id:"pipe",from:node.id,to:other.id,path:{kind:"routed",points:[]}});
    const lead={x:points[1]!.x-start.x,y:points[1]!.y-start.y};
    expect(lead.x*outward.x+lead.y*outward.y).toBeGreaterThan(0);
    expect(lead.x*outward.y-lead.y*outward.x).toBeCloseTo(0,6);
  });
  it("selects fixed representations by runtime contact ID or logical contact ID and view kind", () => {
    const instance = connector();

    expect(selectMaterializedContactRepresentation(
      instance,
      "connector-1:contact:fixed-contact",
      "e4",
    )).toMatchObject({ x: 12, y: 18, direction: "right", viewKind: "e4" });
    expect(selectMaterializedContactRepresentation(instance, fixedLogicalId, "drawing"))
      .toMatchObject({ x: 50, y: 60, direction: "up", viewKind: "drawing" });
  });

  it("uses the exact occurrence key for a repeated materialized contact", () => {
    const selected = selectMaterializedContactRepresentation(connector(), repeatedLogicalId, "e4");

    expect(selected).toMatchObject({
      occurrenceKey: repeatedLogicalId,
      x: 30,
      y: 70,
      direction: "down",
    });
  });

  it("translates each view from its own connector origin and preserves direction", () => {
    expect(materializedContactWorldRepresentation(connector(), fixedLogicalId, "e4"))
      .toMatchObject({ position: { x: 112, y: 218 }, direction: "right" });
    expect(materializedContactWorldRepresentation(connector(), fixedLogicalId, "drawing"))
      .toMatchObject({ position: { x: 30, y: 110 }, direction: "up" });
    expect(materializedContactWorldRepresentation(connector(), repeatedLogicalId, "e4"))
      .toMatchObject({ position: { x: 130, y: 270 }, direction: "down" });
  });

  it("drives persisted wire endpoints from the pinned contact geometry", () => {
    const instance = connector();
    const document = { ...createEmptyHarnessDesign(), connectors: [instance] };
    const endpoint = { connectorId: instance.id, contactId: instance.contacts[0]!.id };

    expect(connectorContactPosition(instance, endpoint.contactId, "e4"))
      .toEqual({ x: 112, y: 218 });
    expect(connectorContactPosition(instance, endpoint.contactId, "drawing"))
      .toEqual({ x: 30, y: 110 });
    expect(wireEndpointE4Anchor(document, endpoint)).toEqual({
      position: { x: 112, y: 218 },
      leadDirection: "right",
    });
  });

  it("returns null so the caller can use the ordinary connector fallback", () => {
    const instance = connector();
    expect(selectMaterializedContactRepresentation(instance, "missing-contact", "e4")).toBeNull();
    expect(selectMaterializedContactRepresentation(instance, repeatedLogicalId, "drawing"))
      .not.toBeNull();
    const withoutDrawing = {
      ...instance,
      libraryBinding: instance.libraryBinding?.mode === "template"
        ? {
            ...instance.libraryBinding,
            snapshot: {
              ...instance.libraryBinding.snapshot,
              contacts: instance.libraryBinding.snapshot.contacts.map(contact =>
                contact.logicalContactId === repeatedLogicalId
                  ? { ...contact, representations: contact.representations.filter(item => item.viewKind !== "drawing") }
                  : contact),
            },
          }
        : instance.libraryBinding,
    } satisfies ConnectorInstance;
    expect(materializedContactWorldRepresentation(withoutDrawing, repeatedLogicalId, "drawing")).toBeNull();
    expect(selectMaterializedContactRepresentation(
      { ...instance, libraryBinding: { mode: "free" } },
      fixedLogicalId,
      "e4",
    )).toBeNull();
  });
});

it("rotates drawing endpoints and scene contact markers by the same angle, leaving E4 unchanged",()=>{
 const original=connector(),id=original.contacts[0]!.id;
 const before=materializedContactWorldRepresentation(original,id,"drawing")!;
 const rotated={...original,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},scale:2,rotationDegrees:90}]};
 const after=materializedContactWorldRepresentation(rotated,id,"drawing")!;
 expect(after.position.x).toBeCloseTo(original.positions.drawing.x-2*(before.position.y-original.positions.drawing.y));
 expect(after.position.y).toBeCloseTo(original.positions.drawing.y+2*(before.position.x-original.positions.drawing.x));
 expect(materializedContactWorldRepresentation(rotated,id,"e4")).toEqual(materializedContactWorldRepresentation(original,id,"e4"));
 const scene=designToScene({...createEmptyHarnessDesign(),connectors:[rotated]},"drawing");
 const marker=JSON.parse(scene[0]!.metadata!.materializedContactPoints!)[0];
 expect(marker.x+rotated.positions.drawing.x).toBeCloseTo(after.position.x);
 expect(marker.y+rotated.positions.drawing.y).toBeCloseTo(after.position.y);
 expect(after.direction).toBe(marker.direction);
 expect(marker.directionVector.x).toBeCloseTo(1);
 expect(marker.directionVector.y).toBeCloseTo(0);
 const halfTurn={...original,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},scale:1,rotationDegrees:180}]};
 expect(materializedContactWorldRepresentation(halfTurn,id,"drawing")!.direction).toBe("down");
 const diagonal={...original,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},scale:1,rotationDegrees:45}]};
 const diagonalScene=designToScene({...createEmptyHarnessDesign(),connectors:[diagonal]},"drawing");
 const diagonalMarker=JSON.parse(diagonalScene[0]!.metadata!.materializedContactPoints!)[0];
 expect(diagonalMarker.directionVector.x).toBeCloseTo(Math.SQRT1_2);
 expect(diagonalMarker.directionVector.y).toBeCloseTo(-Math.SQRT1_2);
});
