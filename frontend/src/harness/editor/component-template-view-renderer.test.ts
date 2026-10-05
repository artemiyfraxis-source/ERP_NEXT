// @vitest-environment node
import { addDrawingPositions, buildDrawingBom, drawingDocumentScene, moveDrawingAnnotation } from "./drawing-documents";
import { buildDrawingPerimeters, drawingObjectPerimeter } from "./drawing-object-perimeter";
import { drawingLocalPoint } from "./drawing-scale";
import { createConnectorInstanceFromComponentTemplate } from "./component-template-placement";
import { createEmptyHarnessDesign } from "./model";
import { createTemplateContentV5FromEditor } from "../component-library/template-model-v5";
import { createE4ConnectorSeriesTableFromV3 } from "../component-library/e4-connector-series-table";
import { projectContactSideView, projectE4DrawingCompanions, shortestDrawingLink } from "./component-template-view-renderer";
import { describe, expect, it, vi } from "vitest";
import { newTemplateContentV3 } from "../component-library/template-commands-v3";
import type { TemplateContentV3, TemplateNodeV3 } from "../component-library/template-model-v3";
import {
  detailStrokeWidth,
  ComponentTemplateImageCache,
  drawProjectedComponentTemplateView,
  projectComponentTemplateView,
  type ComponentTemplateViewInstance,
} from "./component-template-view-renderer";
import { drawingConnectorIndexLabel, getEditorSceneBounds, hitTestEditorScene, redrawCanvas } from "./CanvasViewport";
import type { EditorLayer, EditorSceneObject } from "./editor-types";

let nextId = 1;
const id = () => `00000000-0000-4000-8000-${String(nextId++).padStart(12, "0")}`;
const constant = (value: number) => ({ kind: "constant", value } as const);
const parameter = (parameterId: string) => ({ kind: "parameter", parameterId } as const);

function base(kind: TemplateNodeV3["kind"], layerId: string) {
  return {
    id: id(), kind, layerId, visible: true, locked: false, opacity: 1,
    transform: {
      translateX: constant(0), translateY: constant(0), rotationDegrees: constant(0),
      scaleX: constant(1), scaleY: constant(1),
    },
    stroke: { color: "#123456", width: constant(2) }, fill: { color: null },
  };
}

function rectangle(layerId: string): TemplateNodeV3 {
  return {
    ...base("rectangle", layerId), kind: "rectangle",
    geometry: {
      x: constant(3), y: constant(4), width: constant(12), height: constant(8),
      cornerRadii: [constant(0), constant(0), constant(0), constant(0)],
    },
  };
}

function imageNode(layerId: string, assetId: string, underlay: boolean): TemplateNodeV3 {
  return {
    ...base("image", layerId), kind: "image",
    geometry: {
      assetId, x: constant(2), y: constant(3), width: constant(40), height: constant(20),
      cropX: 0, cropY: 0, cropWidth: 1, cropHeight: 1, underlay,
    },
  };
}

function fixture(): { readonly content: TemplateContentV3; readonly instance: ComponentTemplateViewInstance } {
  const content = newTemplateContentV3();
  const variant = { id: id(), sourceId: "test", entityType: "connector", articleKey: "A-1", parameterValues: [], contactGroups: null };
  const withVariant = { ...content, articleVariants: [variant] };
  return {
    content: withVariant,
    instance: { objectId: "connector-1", snapshotId: "project-snapshot-1", articleVariantId: variant.id, content: withVariant },
  };
}

describe("project component template view", () => {
  it("scales drawing geometry uniformly without changing its pinned content",()=>{
    const {content,instance}=fixture();const view=content.views[1]!;view.layers[0]!.nodes.push(rectangle(view.layers[0]!.id));
    const before=projectComponentTemplateView(instance,"drawing",{x:100,y:200})!;
    const after=projectComponentTemplateView({...instance,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},scale:2}]},"drawing",{x:100,y:200})!;
    expect(after.bounds.maxX-after.bounds.minX).toBeCloseTo(2*(before.bounds.maxX-before.bounds.minX));
    expect(after.bounds.maxY-after.bounds.minY).toBeCloseTo(2*(before.bounds.maxY-before.bounds.minY));
    expect(after.bounds.minX-100).toBeCloseTo(2*(before.bounds.minX-100));
    expect(after.commands[0]!.nodeId).toBe(before.commands[0]!.nodeId);
  });

  it("selects only the matching E4 or drawing view and keeps layer paint order at instance origin", () => {
    const { content, instance } = fixture();
    const [e4, drawing] = content.views;
    const hidden = { ...rectangle(e4!.layers[0]!.id), id: id() };
    const e4Back = { ...rectangle(e4!.layers[0]!.id), id: id(), stroke: { color: "#111111", width: constant(2) } };
    const e4Front = { ...rectangle(e4!.layers[0]!.id), id: id(), stroke: { color: "#222222", width: constant(2) } };
    const drawingNode = { ...rectangle(drawing!.layers[0]!.id), id: id(), stroke: { color: "#333333", width: constant(2) } };
    const custom = {
      ...content,
      views: [
        { ...e4!, layers: [
          { ...e4!.layers[0]!, nodes: [e4Back] },
          { id: id(), name: "hidden", visible: false, locked: false, nodes: [hidden] },
          { id: id(), name: "front", visible: true, locked: false, nodes: [e4Front] },
        ] },
        { ...drawing!, layers: [{ ...drawing!.layers[0]!, nodes: [drawingNode] }] },
        { id: id(), kind: "additional" as const, name: "Extra", layers: [{ id: id(), name: "layer", visible: true, locked: false, nodes: [rectangle(id())] }], contactPoints: [], bundlePorts: [], repeatPlacements: [] },
      ],
    };
    const e4Projection = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 100, y: 200 })!;
    const drawingProjection = projectComponentTemplateView({ ...instance, content: custom }, "drawing", { x: 100, y: 200 })!;
    expect(e4Projection.commands.map(command => command.nodeId)).toEqual([e4Back.id, e4Front.id]);
    expect(e4Projection.commands.map(command => command.stroke)).toEqual(["#111111", "#222222"]);
    expect(e4Projection.bounds).toEqual({ minX: 102, minY: 203, maxX: 116, maxY: 213 });
    expect(drawingProjection.commands.map(command => command.nodeId)).toEqual([drawingNode.id]);
    expect(projectComponentTemplateView({ ...instance, content: { ...custom, views: [custom.views[2]!] } }, "e4", { x: 0, y: 0 })).toBeNull();
    expect(projectComponentTemplateView(instance, "e4", { x: 0, y: 0 })).toBeNull();

    const sceneObject: EditorSceneObject = {
      id: instance.objectId, layerId: "scene", kind: "connector", label: "Library",
      x: 100, y: 200, width: 1, height: 1, color: "#000000",
    };
    const sceneLayers: readonly EditorLayer[] = [{ id: "scene", label: "Scene", visible: true, locked: false }];
    const exactInstance = { ...instance, content: custom };
    expect(hitTestEditorScene(
      [sceneObject], sceneLayers, { x: 114, y: 210 }, 1, "e4", [exactInstance],
    )).toBe(instance.objectId);
    expect(getEditorSceneBounds(
      [sceneObject], sceneLayers, "e4", undefined, [exactInstance],
    )).toEqual(e4Projection.bounds);
  });

  it("projects persisted stroke dash styles and keeps older strokes solid by default", () => {
    const { content, instance } = fixture();
    const e4 = content.views[0]!;
    const dashed = {
      ...rectangle(e4.layers[0]!.id),
      id: id(),
      stroke: { color: "#111111", width: constant(2), dash: "dash-dot" as const },
    };
    const legacySolid = { ...rectangle(e4.layers[0]!.id), id: id() };
    const custom = {
      ...content,
      views: [{ ...e4, layers: [{ ...e4.layers[0]!, nodes: [dashed, legacySolid] }] }, content.views[1]!],
    };

    const projection = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 })!;

    expect(projection.commands.map(command => command.strokeDash)).toEqual(["dash-dot", "solid"]);
  });

  it("keeps and paints the visual bend radius of a placed polyline", () => {
    const { content, instance } = fixture();
    const e4 = content.views[0]!, layer = e4.layers[0]!;
    const rounded: TemplateNodeV3 = {
      ...base("polyline", layer.id), kind: "polyline",
      geometry: {
        points: [
          { x: constant(0), y: constant(0) },
          { x: constant(20), y: constant(0) },
          { x: constant(20), y: constant(20) },
        ],
        bendRadius: constant(5),
      },
    };
    const custom = { ...content, views: [{ ...e4, layers: [{ ...layer, nodes: [rounded] }] }, content.views[1]!] };
    const projection = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 })!;
    expect(projection.commands[0]).toMatchObject({ kind: "polyline", bendRadius: 5 });

    const arcTo = vi.fn();
    const context = {
      save: () => undefined, restore: () => undefined, transform: () => undefined,
      globalAlpha: 1, strokeStyle: "", fillStyle: "", lineWidth: 1,
      setLineDash: () => undefined, beginPath: () => undefined, moveTo: () => undefined,
      lineTo: () => undefined, arcTo, closePath: () => undefined, fill: () => undefined,
      stroke: () => undefined,
    } as unknown as CanvasRenderingContext2D;
    drawProjectedComponentTemplateView(context, projection, new ComponentTemplateImageCache(null));
    expect(arcTo).toHaveBeenCalledOnce();
    expect(arcTo.mock.calls[0]![0]).toBe(20);
    expect(arcTo.mock.calls[0]![1]).toBe(0);
    expect(arcTo.mock.calls[0]![2]).toBe(20);
    expect(arcTo.mock.calls[0]![3]).toBeCloseTo(5);
    expect(arcTo.mock.calls[0]![4]).toBeCloseTo(5);
  });

  it("sets and resets Canvas2D dash patterns for every projected command", () => {
    const { content, instance } = fixture();
    const e4 = content.views[0]!;
    const dashed = {
      ...rectangle(e4.layers[0]!.id),
      id: id(),
      stroke: { color: "#111111", width: constant(2), dash: "dash" as const },
    };
    const solid = {
      ...rectangle(e4.layers[0]!.id),
      id: id(),
      stroke: { color: "#222222", width: constant(3), dash: "solid" as const },
    };
    const dotted = {
      ...rectangle(e4.layers[0]!.id),
      id: id(),
      stroke: { color: "#333333", width: constant(0.5), dash: "dot" as const },
    };
    const custom = {
      ...content,
      views: [{ ...e4, layers: [{ ...e4.layers[0]!, nodes: [dashed, solid, dotted] }] }, content.views[1]!],
    };
    const projection = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 })!;
    const setLineDash = vi.fn();
    const context = {
      save: () => undefined, restore: () => undefined, transform: () => undefined,
      globalAlpha: 1, strokeStyle: "", fillStyle: "", lineWidth: 1,
      setLineDash, beginPath: () => undefined, moveTo: () => undefined, lineTo: () => undefined,
      quadraticCurveTo: () => undefined, closePath: () => undefined, fill: () => undefined,
      stroke: () => undefined,
    } as unknown as CanvasRenderingContext2D;

    drawProjectedComponentTemplateView(context, projection, new ComponentTemplateImageCache(null));

    expect(setLineDash.mock.calls).toEqual([
      [[12, 8]],
      [[]],
      [[1, 3]],
    ]);
  });

  it("resolves article dimensions, nested group transform/opacity and repeat occurrence offsets", () => {
    const { content, instance } = fixture();
    const countId = id();
    const widthId = id();
    const domainId = id();
    const groupId = id();
    const logicalId = id();
    const pointId = id();
    const groupTypeId = id();
    const e4 = content.views[0]!;
    const layer = e4.layers[0]!;
    const node = {
      ...rectangle(layer.id),
      geometry: { ...rectangle(layer.id).geometry, width: parameter(widthId) },
    } as TemplateNodeV3;
    const group: TemplateNodeV3 = {
      ...base("group", layer.id), id: groupId, kind: "group",
      opacity: 0.5,
      transform: { ...base("group", layer.id).transform, translateX: constant(7), translateY: constant(9) },
      geometry: { childIds: [node.id] },
    };
    const custom: TemplateContentV3 = {
      ...content,
      views: [{
        ...e4,
        layers: [{ ...layer, nodes: [node, group] }],
        contactPoints: [{ id: pointId, logicalContactId: logicalId, x: constant(0), y: constant(0), direction: "left" }],
        repeatPlacements: [{ repeatDomainId: domainId, prototypeGroupId: groupId, step: { x: constant(20), y: constant(0) }, contactPointIds: [pointId] }],
      }, content.views[1]!],
      parameters: [
        { id: countId, name: "Count", type: "integer", unit: null, defaultValue: 2, minimum: 1, maximum: 10, formula: null },
        { id: widthId, name: "Width", type: "number", unit: null, defaultValue: 5, minimum: 1, maximum: 100, formula: null },
      ],
      repeaters: [{ id: domainId, countParameterId: countId, logicalContactIds: [logicalId] }],
      contactTypeGroups: [{ id: groupTypeId, name: "Signal" }],
      logicalContacts: [{ id: logicalId, number: "1", name: "Contact", circuitText: null, contactTypeGroupId: groupTypeId }],
      articleVariants: [{ ...content.articleVariants[0]!, parameterValues: [{ parameterId: widthId, value: 14 }] }],
    };
    const projection = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 100, y: 200 })!;
    expect(projection.commands).toHaveLength(2);
    expect(projection.commands.map(command => command.transform.e)).toEqual([107, 127]);
    expect(projection.commands.map(command => command.opacity)).toEqual([0.5, 0.5]);
    expect(projection.commands.map(command => command.kind === "rectangle" ? command.width : null)).toEqual([14, 14]);
  });

  it("requests PNG only from the scoped resolver and retains drawable placeholders for missing URLs/assets", () => {
    const { content, instance } = fixture();
    const e4 = content.views[0]!;
    const assetId = id();
    const image: TemplateNodeV3 = {
      ...base("image", e4.layers[0]!.id), kind: "image",
      geometry: {
        assetId, x: constant(2), y: constant(3), width: constant(40), height: constant(20),
        cropX: 0.1, cropY: 0.2, cropWidth: 0.6, cropHeight: 0.4, underlay: false,
      },
    };
    const custom = {
      ...content,
      views: [{ ...e4, layers: [{ ...e4.layers[0]!, nodes: [image] }] }, content.views[1]!],
      assets: [{ assetId, fileName: "part.png", mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 42 }],
    };
    const resolver = vi.fn(() => "/project/snapshot/asset");
    const ready = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 }, resolver)!;
    expect(resolver).toHaveBeenCalledExactlyOnceWith(instance.snapshotId, assetId);
    expect(ready.commands[0]).toMatchObject({ kind: "image", url: "/project/snapshot/asset", error: null });
    expect(projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 }, () => "")!.commands[0]).toMatchObject({ kind: "image", url: null, error: "URL изображения пуст." });
    expect(projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 }, () => { throw Error("network"); })!.commands[0]).toMatchObject({ kind: "image", url: null, error: "URL изображения не удалось получить." });
    resolver.mockClear();
    const missing = projectComponentTemplateView({ ...instance, content: { ...custom, assets: [] } }, "e4", { x: 0, y: 0 }, resolver)!;
    expect(missing.commands[0]).toMatchObject({ kind: "image", url: null, error: "Asset изображения отсутствует в закреплённом шаблоне." });
    expect(resolver).not.toHaveBeenCalled();

    const events: string[] = [];
    const context = {
      save: () => undefined, restore: () => undefined, transform: () => undefined,
      globalAlpha: 1, strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textBaseline: "alphabetic",
      fillRect: () => events.push("fillRect"), strokeRect: () => events.push("strokeRect"),
      beginPath: () => undefined, moveTo: () => undefined, lineTo: () => undefined,
      setLineDash: () => undefined, stroke: () => undefined, fillText: () => undefined,
    } as unknown as CanvasRenderingContext2D;
    drawProjectedComponentTemplateView(context, missing, new ComponentTemplateImageCache(null));
    expect(events).toEqual(["fillRect", "strokeRect"]);
  });

  it("paints PNG underlays first while retaining stable order for underlays and foreground nodes", () => {
    const { content, instance } = fixture();
    const e4 = content.views[0]!;
    const backAssetId = id();
    const secondBackAssetId = id();
    const frontAssetId = id();
    const foregroundRectangle = { ...rectangle(e4.layers[0]!.id), id: id() };
    const firstUnderlay = imageNode(e4.layers[0]!.id, backAssetId, true);
    const secondUnderlay = imageNode(e4.layers[0]!.id, secondBackAssetId, true);
    const foregroundImage = imageNode(e4.layers[0]!.id, frontAssetId, false);
    const custom = {
      ...content,
      views: [{
        ...e4,
        layers: [{
          ...e4.layers[0]!,
          nodes: [foregroundRectangle, firstUnderlay, secondUnderlay, foregroundImage],
        }],
      }, content.views[1]!],
      assets: [backAssetId, secondBackAssetId, frontAssetId].map(assetId => ({
        assetId, fileName: `${assetId}.png`, mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 42,
      })),
    };
    const projection = projectComponentTemplateView(
      { ...instance, content: custom }, "e4", { x: 0, y: 0 }, (_, assetId) => `/${assetId}.png`,
    )!;
    expect(projection.commands.map(command => command.nodeId)).toEqual([
      firstUnderlay.id,
      secondUnderlay.id,
      foregroundRectangle.id,
      foregroundImage.id,
    ]);
    expect(projection.commands.filter(command => command.kind === "image").map(command => command.underlay))
      .toEqual([true, true, false]);

    const images: Array<{ naturalWidth: number; naturalHeight: number; onload: (() => void) | null; onerror: (() => void) | null; src: string }> = [];
    const cache = new ComponentTemplateImageCache(() => {
      const image = { naturalWidth: 100, naturalHeight: 50, onload: null, onerror: null, src: "" };
      images.push(image);
      return image as unknown as HTMLImageElement;
    });
    const events: string[] = [];
    const context = {
      save: () => undefined, restore: () => undefined, transform: () => undefined,
      globalAlpha: 1, strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textBaseline: "alphabetic",
      fillRect: () => undefined, strokeRect: () => undefined, beginPath: () => undefined,
      moveTo: () => undefined, lineTo: () => undefined, quadraticCurveTo: () => undefined,
      closePath: () => undefined, fill: () => undefined, fillText: () => undefined,
      setLineDash: () => undefined, stroke: () => events.push("rectangle"),
      drawImage: (image: HTMLImageElement) => events.push(image.src),
    } as unknown as CanvasRenderingContext2D;
    drawProjectedComponentTemplateView(context, projection, cache);
    images.forEach(image => image.onload?.());
    events.length = 0;
    drawProjectedComponentTemplateView(context, projection, cache);
    expect(events).toEqual([
      `/${backAssetId}.png`,
      `/${secondBackAssetId}.png`,
      "rectangle",
      `/${frontAssetId}.png`,
    ]);
  });

  it("repaints a loaded PNG with the normalized crop and remains stable on load failure", () => {
    const { content, instance } = fixture();
    const e4 = content.views[0]!;
    const assetId = id();
    const image: TemplateNodeV3 = {
      ...base("image", e4.layers[0]!.id), kind: "image",
      geometry: { assetId, x: constant(1), y: constant(2), width: constant(30), height: constant(40), cropX: 0.1, cropY: 0.2, cropWidth: 0.5, cropHeight: 0.25, underlay: false },
    };
    const custom = {
      ...content,
      views: [{ ...e4, layers: [{ ...e4.layers[0]!, nodes: [image] }] }, content.views[1]!],
      assets: [{ assetId, fileName: "part.png", mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 42 }],
    };
    const projection = projectComponentTemplateView({ ...instance, content: custom }, "e4", { x: 0, y: 0 }, () => "/exact.png")!;
    const fakeImage = { naturalWidth: 200, naturalHeight: 100, onload: null as (() => void) | null, onerror: null as (() => void) | null, src: "" };
    const invalidate = vi.fn();
    const cache = new ComponentTemplateImageCache(() => fakeImage as unknown as HTMLImageElement);
    cache.setInvalidate(invalidate);
    const drawImage = vi.fn();
    const context = {
      save: () => undefined, restore: () => undefined, transform: () => undefined,
      globalAlpha: 1, strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textBaseline: "alphabetic",
      fillRect: () => undefined, strokeRect: () => undefined, beginPath: () => undefined,
      moveTo: () => undefined, lineTo: () => undefined, setLineDash: () => undefined, stroke: () => undefined,
      fillText: () => undefined, drawImage,
    } as unknown as CanvasRenderingContext2D;
    drawProjectedComponentTemplateView(context, projection, cache);
    expect(fakeImage.src).toBe("/exact.png");
    expect(drawImage).not.toHaveBeenCalled();
    fakeImage.onload?.();
    expect(invalidate).toHaveBeenCalledOnce();
    drawProjectedComponentTemplateView(context, projection, cache);
    expect(drawImage).toHaveBeenCalledWith(fakeImage, 20, 20, 100, 25, 1, 2, 30, 40);
    fakeImage.onerror?.();
    drawImage.mockClear();
    drawProjectedComponentTemplateView(context, projection, cache);
    expect(drawImage).not.toHaveBeenCalled();
  });
});

describe("independent E4 companion drawings",()=>{
  it("places ungrouped article primitives across layers as one intact drawing",()=>{
    const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
    const a=rectangle(layer.id),otherLayer={...layer,id:id(),nodes:[] as TemplateNodeV3[]};
    const b={...rectangle(otherLayer.id),transform:{...a.transform,translateX:constant(25)}};
    layer.nodes=[a];otherLayer.nodes=[b];view.layers.push(otherLayer);
    const v5=createTemplateContentV5FromEditor(content,createE4ConnectorSeriesTableFromV3(content),[],[],undefined,[{articleVariantId:instance.articleVariantId,nodeIds:[a.id,b.id],contactPointIds:[]}]).content;
    const pinned={...instance,content:v5},origin={x:100,y:200};
    const original=projectE4DrawingCompanions(pinned,origin,300);
    expect(original).toHaveLength(1);
    expect(original[0]!.drawingId).toBe(view.id);
    expect(original[0]!.commands.map(c=>c.nodeId)).toEqual([a.id,b.id]);
    expect(projectE4DrawingCompanions({...pinned,drawingPlacements:[{drawingId:a.id,visible:false,offset:{x:600,y:70},scale:3}]},origin,300)).toEqual(original);
    const moved=projectE4DrawingCompanions({...pinned,drawingPlacements:[{drawingId:view.id,visible:false,offset:{x:80,y:90}}]},origin,300)[0]!;
    expect(moved.visible).toBe(false);
    original[0]!.commands.forEach((command,i)=>{
      expect(moved.commands[i]!.transform.e-command.transform.e).toBeCloseTo(80);
      expect(moved.commands[i]!.transform.f-command.transform.f).toBeCloseTo(90);
    });
    const scaled=projectE4DrawingCompanions({...pinned,drawingPlacements:[{drawingId:view.id,visible:true,offset:{x:0,y:0},scale:2}]},origin,300)[0]!;
    expect(scaled.bounds.maxX-scaled.bounds.minX).toBeCloseTo(2*(original[0]!.bounds.maxX-original[0]!.bounds.minX));
    expect(scaled.commands[1]!.transform.e-scaled.commands[0]!.transform.e).toBeCloseTo(2*(original[0]!.commands[1]!.transform.e-original[0]!.commands[0]!.transform.e));
  });
  it("filters the pinned article, moves only one drawing and hides it without changing its geometry",()=>{
    const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
    const children=Array.from({length:4},()=>rectangle(layer.id)),excluded=rectangle(layer.id);
    const a={...base("group",layer.id),kind:"group" as const,geometry:{childIds:children.slice(0,2).map(n=>n.id)}};
    const b={...base("group",layer.id),kind:"group" as const,geometry:{childIds:children.slice(2).map(n=>n.id)}};
    layer.nodes=[a,b,...children,excluded];
    const v5=createTemplateContentV5FromEditor(content,createE4ConnectorSeriesTableFromV3(content),[],[],undefined,[{articleVariantId:instance.articleVariantId,nodeIds:[a.id,b.id,...children.map(n=>n.id)],contactPointIds:[]}]).content;
    const original=projectE4DrawingCompanions({...instance,content:v5},{x:100,y:200},300);
    expect(original.map(d=>d.drawingId)).toEqual([a.id,b.id]);
    expect(original.map(d=>d.commands.length)).toEqual([2,2]);
    const moved=projectE4DrawingCompanions({...instance,content:v5,drawingPlacements:[{drawingId:a.id,visible:false,offset:{x:750,y:80}}]},{x:100,y:200},300);
    expect(moved[0]!.visible).toBe(false);
    expect(moved[0]!.bounds.minX).toBeCloseTo(original[0]!.bounds.minX+750);
    expect(moved[0]!.bounds.minY).toBeCloseTo(original[0]!.bounds.minY+80);
    expect(moved[1]).toEqual(original[1]);
    const scaled=projectE4DrawingCompanions({...instance,content:v5,drawingPlacements:[{drawingId:a.id,visible:true,offset:{x:0,y:0},scale:2}]},{x:100,y:200},300);
    expect(scaled[0]!.bounds.maxX-scaled[0]!.bounds.minX).toBeCloseTo(2*(original[0]!.bounds.maxX-original[0]!.bounds.minX));
    expect(scaled[0]!.bounds.maxY-scaled[0]!.bounds.minY).toBeCloseTo(2*(original[0]!.bounds.maxY-original[0]!.bounds.minY));
    expect(scaled[1]).toEqual(original[1]);
    const object:EditorSceneObject={id:instance.objectId,layerId:"connectors",kind:"connector",label:"X1",x:100,y:200,width:300,height:80,color:"#000"};
    const layers=[{id:"connectors",label:"Connectors",visible:true,locked:false}];
    const hidden={...instance,content:v5,drawingPlacements:[{drawingId:a.id,visible:false,offset:{x:750,y:80}},{drawingId:b.id,visible:false,offset:{x:0,y:0}}]};
    expect(hitTestEditorScene([object],layers,{x:moved[0]!.bounds.minX+1,y:moved[0]!.bounds.minY+1},1,"e4",[hidden])).toBeNull();
  });
  it("keeps grouped primitives in a single listing entry",()=>{
    const {content,instance}=fixture(),layer=content.views[1]!.layers[0]!;
    const a=rectangle(layer.id),b=rectangle(layer.id),group={...base("group",layer.id),kind:"group" as const,geometry:{childIds:[a.id,b.id]}};
    layer.nodes=[a,b,group];
    const v5=createTemplateContentV5FromEditor(content,createE4ConnectorSeriesTableFromV3(content),[]).content;
    const drawings=projectE4DrawingCompanions({...instance,content:v5},{x:0,y:0},300);
    expect(drawings).toHaveLength(1);expect(drawings[0]!.drawingId).toBe(group.id);expect(drawings[0]!.commands).toHaveLength(2);
  });
  it("uses one shortest segment for side, diagonal and overlapping bounds",()=>{
    const table={minX:0,minY:0,maxX:100,maxY:50};
    expect(shortestDrawingLink(table,{minX:140,minY:10,maxX:200,maxY:40})).toEqual([{x:100,y:25},{x:140,y:25}]);
    expect(shortestDrawingLink(table,{minX:120,minY:80,maxX:200,maxY:100})).toEqual([{x:100,y:50},{x:120,y:80}]);
    const [a,b]=shortestDrawingLink(table,{minX:20,minY:10,maxX:40,maxY:20});expect(a).toEqual(b);
  });
});

describe("contact-side Drawing projection",()=>{
  it("projects the pinned E4 drawing with its contact labels and wire colours as an independently placed companion",()=>{
    const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
    const contact=rectangle(layer.id);
    layer.nodes=[contact];
    const table=createE4ConnectorSeriesTableFromV3(content);
    const v5=createTemplateContentV5FromEditor(content,table,[]).content;
    const source={minX:100,minY:200,maxX:160,maxY:260};
    const placed=projectContactSideView({...instance,content:v5,drawingPlacements:[{drawingId:"view:contact-side",visible:true,offset:{x:220,y:-40},scale:2}]},{x:100,y:200},source)!;
    expect(placed.placementId).toBe("view:contact-side");
    expect(placed.visible).toBe(true);
    expect(placed.drawing.viewKind).toBe("drawing");
    expect(placed.drawing.bounds.minX).toBeGreaterThan(300);
    expect(placed.drawing.bounds.maxX-placed.drawing.bounds.minX).toBeGreaterThan(20);
    expect(placed.link).toEqual(shortestDrawingLink(source,placed.drawing.bounds));
  });

  it("does not project a contact-side view until the reserved placement is created",()=>{
    const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
    view.layers[0]!.nodes.push(rectangle(layer.id));
    const v5=createTemplateContentV5FromEditor(content,createE4ConnectorSeriesTableFromV3(content),[]).content;
    expect(projectContactSideView({...instance,content:v5},{x:0,y:0},{minX:0,minY:0,maxX:10,maxY:10})).toBeNull();
  });

  it("includes the visible contact-side companion in Drawing selection and fit bounds",()=>{
    const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
    const contact=rectangle(layer.id);layer.nodes=[contact];
    const v5=createTemplateContentV5FromEditor(content,createE4ConnectorSeriesTableFromV3(content),[]).content;
    const placed={...instance,content:v5,drawingPlacements:[{drawingId:"view:contact-side",visible:true,offset:{x:220,y:40},scale:1}]};
    const object:EditorSceneObject={id:instance.objectId,layerId:"connectors",kind:"connector",label:"X1",x:100,y:200,width:60,height:60,color:"#000"};
    const layers:EditorLayer[]=[{id:"connectors",label:"Connectors",visible:true,locked:false}];
    const primary=projectComponentTemplateView(placed,"drawing",{x:object.x,y:object.y})!;
    const contactSide=projectContactSideView(placed,{x:object.x,y:object.y},primary.bounds)!;
    const point={x:(contactSide.drawing.bounds.minX+contactSide.drawing.bounds.maxX)/2,y:(contactSide.drawing.bounds.minY+contactSide.drawing.bounds.maxY)/2};
    expect(hitTestEditorScene([object],layers,point,1,"drawing",[placed])).toBe(object.id);
    const bounds=getEditorSceneBounds([object],layers,"drawing",undefined,[placed])!;
    expect(bounds.maxX).toBeGreaterThanOrEqual(contactSide.drawing.bounds.maxX);
    expect(bounds.maxY).toBeGreaterThanOrEqual(contactSide.drawing.bounds.maxY);
  });

  it("omits a hidden contact-side companion from Drawing selection and fit bounds",()=>{
    const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
    const contact=rectangle(layer.id);layer.nodes=[contact];
    const v5=createTemplateContentV5FromEditor(content,createE4ConnectorSeriesTableFromV3(content),[]).content;
    const placed={...instance,content:v5,drawingPlacements:[{drawingId:"view:contact-side",visible:false,offset:{x:220,y:40},scale:1}]};
    const object:EditorSceneObject={id:instance.objectId,layerId:"connectors",kind:"connector",label:"",x:100,y:200,width:60,height:60,color:"#000"};
    const layers:EditorLayer[]=[{id:"connectors",label:"Connectors",visible:true,locked:false}];
    const primary=projectComponentTemplateView(placed,"drawing",{x:object.x,y:object.y})!;
    const contactSide=projectContactSideView(placed,{x:object.x,y:object.y},primary.bounds)!;
    const point={x:(contactSide.drawing.bounds.minX+contactSide.drawing.bounds.maxX)/2,y:(contactSide.drawing.bounds.minY+contactSide.drawing.bounds.maxY)/2};
    expect(hitTestEditorScene([object],layers,point,1,"drawing",[placed])).toBeNull();
    const bounds=getEditorSceneBounds([object],layers,"drawing",undefined,[placed])!;
    expect(bounds.maxX).toBeLessThan(contactSide.drawing.bounds.minX);
  });
});

it("rotates drawing bounds, hit testing and geometry together",()=>{
 const {content,instance}=fixture(),view=content.views[1]!;view.layers[0]!.nodes.push(rectangle(view.layers[0]!.id));
 const rotated={...instance,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},rotationDegrees:90}]};
 const projection=projectComponentTemplateView(rotated,"drawing",{x:100,y:200})!;
 expect(projection.bounds.minX).toBeCloseTo(87);expect(projection.bounds.maxX).toBeCloseTo(97);
 expect(projection.bounds.minY).toBeCloseTo(202);expect(projection.bounds.maxY).toBeCloseTo(216);
 const object:EditorSceneObject={id:instance.objectId,kind:"connector",x:100,y:200,width:118,height:80,layerId:"connectors",label:"X1",color:"#000"};
 const layers:EditorLayer[]=[{id:"connectors",label:"Connectors",visible:true,locked:false}];
 expect(hitTestEditorScene([object],layers,{x:92,y:209},1,"drawing",[rotated])).toBe(instance.objectId);
 expect(hitTestEditorScene([object],layers,{x:200,y:270},1,"drawing",[rotated])).toBeNull();
});

it("draws the connector index outside a rotated library picture after the picture",()=>{
 const {content,instance}=fixture(),view=content.views[1]!;view.layers[0]!.nodes.push(rectangle(view.layers[0]!.id));
 const rotated={...instance,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},rotationDegrees:90}]};
 const object:EditorSceneObject={id:instance.objectId,kind:"connector",x:100,y:200,width:118,height:80,layerId:"connectors",label:"XS1",color:"#123456"};
 const layers:EditorLayer[]=[{id:"connectors",label:"Connectors",visible:true,locked:false}];
 const projection=projectComponentTemplateView(rotated,"drawing",{x:object.x,y:object.y})!;
 const expected=drawingConnectorIndexLabel(object,projection.bounds);
 vi.stubGlobal("window",{devicePixelRatio:1});
 const calls:{method:string;args:unknown[]}[]=[],state:Record<string,unknown>={getTransform:()=>({a:1,b:0,c:0,d:1,e:0,f:0})};
 const context=new Proxy(state,{get(target,key:string){if(key in target)return target[key];return (...args:unknown[])=>{calls.push({method:key,args});};}}) as unknown as CanvasRenderingContext2D;
 const canvas={width:300,height:200,clientWidth:300,clientHeight:200,getContext:()=>context} as unknown as HTMLCanvasElement;
 redrawCanvas(canvas,"drawing",{zoom:1,offsetX:0,offsetY:0},[object],layers,new Set(),[],undefined,undefined,[rotated]);
 const label=[...calls].reverse().find(call=>call.method==="fillText"&&call.args[0]==="XS1")!;
 expect(label.args).toEqual(["XS1",expected.x,expected.y]);
 expect(calls.findIndex(call=>call.method==="fillText"&&call.args[0]==="XS1")).toBeGreaterThan(calls.findIndex(call=>call.method==="quadraticCurveTo"));
 expect(getEditorSceneBounds([object],layers,"drawing",undefined,[rotated])!.minY).toBeLessThan(projection.bounds.minY);
 vi.unstubAllGlobals();
});


it("anchors to the actual rotated connector perimeter, not its bounding box or internal strokes",()=>{
 const {content,instance}=fixture(),view=content.views[1]!,layer=view.layers[0]!;
 const outer={...rectangle(layer.id),kind:"ellipse" as const,geometry:{centerX:constant(60),centerY:constant(40),radiusX:constant(50),radiusY:constant(30)}};
 layer.nodes.push(outer,{...base("rectangle",layer.id),kind:"rectangle",geometry:{x:constant(50),y:constant(35),width:constant(12),height:constant(8),cornerRadii:[constant(0),constant(0),constant(0),constant(0)]}});
 content.logicalContacts.push({id:id(),number:"1",name:"Контакт",circuitText:null,contactTypeGroupId:null});
 const connector=createConnectorInstanceFromComponentTemplate({templateId:"t",version:1,versionSha256:"a".repeat(64),code:"SERIES",name:"Розетка тестовая",articleBindings:content.articleVariants,assets:[],content}, {id:instance.objectId,designation:"XS1",e4Position:{x:100,y:200}});
 const d={...createEmptyHarnessDesign(),connectors:[{...connector,drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},scale:2,rotationDegrees:90}]}]};
 const perimeters=buildDrawingPerimeters([instance]);
 const topLocal={x:60,y:10},offset=drawingLocalPoint(topLocal,d.connectors[0]!.drawingPlacements);
 const point=drawingObjectPerimeter(d,connector.id,{x:100,y:320},perimeters)!;
 expect(point.x).toBeCloseTo(100+offset.x);expect(point.y).toBeCloseTo(200+offset.y);
 const documents=addDrawingPositions(d,perimeters);
 const moving={...d,drawingDocuments:documents};
 const anchor=moveDrawingAnnotation(moving,documents.leaders[0]!.id+":anchor",{x:96,y:316},perimeters)!;
 const scaled={...moving,drawingDocuments:anchor,connectors:[{...d.connectors[0]!,positions:{...connector.positions,drawing:{x:500,y:300}},drawingPlacements:[{drawingId:"view:drawing",visible:true,offset:{x:0,y:0},scale:3,rotationDegrees:180}]}]};
 const p=drawingDocumentScene(scaled,1,perimeters).find(o=>o.kind==="position-leader")!.points![0]!;
 expect(p.x).toBeCloseTo(320);expect(p.y).toBeCloseTo(270);
 const bom=buildDrawingBom(d)[0]!;
 expect(bom).toMatchObject({index:"XS1",designation:"A-1",name:`${connector.contacts.length} конт. — Розетка тестовая`});
});

it("keeps fine vector strokes readable at 45%, including rotated nonuniform transforms",()=>{
 expect(detailStrokeWidth(1,{a:.45,b:0,c:0,d:.45},.9)).toBeCloseTo(2);
 expect(detailStrokeWidth(1,{a:0,b:.45,c:-.225,d:0},.9)).toBeCloseTo(4);
 expect(detailStrokeWidth(2,{a:2,b:0,c:0,d:2},.9)).toBe(2);
 expect(detailStrokeWidth(0,{a:.1,b:0,c:0,d:.1},.9)).toBe(0);
 expect(detailStrokeWidth(1,{a:.9,b:0,c:0,d:.9},1.8)).toBe(2);
});
