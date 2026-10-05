import { materializeE4ConnectorArticle } from "../component-library/e4-connector-series-table";
import { contactShapeLabel, contactLabelColor } from "../component-library/contact-shape";
import { CONTACT_SIDE_VIEW_PLACEMENT_ID, drawingScale, drawingRotation } from "./drawing-scale";
import { materializeGenerator } from "../component-library/drawing-generator";
import { projectTemplateContentV5TableToV1 } from "../component-library/template-model-v5";
import type { ConnectorDrawingPlacement } from "./model";
import { articleDrawingView, findArticleDrawing, type DrawingTarget } from "../component-library/drawing-bindings";
import { evaluateNumericExpressionV3 } from "../component-library/template-commands-v3";
import { materializeArticleVariantV3 } from "../component-library/template-article-materialization-v3";
import { hatchTile, type DrawingHatch } from "../component-library/drawing-hatch";
import type {
  NumericExpressionV3,
  TemplateContentV3,
  TemplateNodeV3,
} from "../component-library/template-model-v3";
import type { TemplateContentV4 } from "../component-library/template-model-v4";
import { projectTemplateContentV5ToV3, type TemplateContentV5 } from "../component-library/template-model-v5";
import {
  expandTemplateViewRepeatsV2,
  resolveTemplateParameterValuesV2,
  type RepeatOccurrenceDescriptorV2,
} from "../component-library/template-repeat-v2";
import type { HarnessEditorView } from "./editor-types";
import { roundedPolylineCommandsV2 } from "../component-library/rounded-polyline-v2";

export interface ComponentTemplateViewInstance {
  readonly contactWireColors?: Readonly<Record<string,string>>;
  readonly drawingPlacements?: readonly ConnectorDrawingPlacement[];
  /** Placement id; for editor connectors this is the EditorSceneObject id. */
  readonly objectId: string;
  readonly snapshotId: string;
  readonly articleVariantId: string;
  readonly content: TemplateContentV3 | TemplateContentV4 | TemplateContentV5;
}

export type ResolveComponentTemplateAssetUrl = (snapshotId: string, assetId: string) => string;

export interface ComponentTemplateProjectionOrigin {
  readonly x: number;
  readonly y: number;
}

export interface ComponentTemplateViewBounds {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

export interface ComponentTemplateTransform {
  readonly a: number;
  readonly b: number;
  readonly c: number;
  readonly d: number;
  readonly e: number;
  readonly f: number;
}

interface ProjectedCommandBase {
  readonly contactLabel?: {text:string;x:number;y:number;fontSize:number;color:string};
  readonly nodeId: string;
  readonly layerId: string;
  readonly transform: ComponentTemplateTransform;
  readonly opacity: number;
  readonly stroke: string;
  readonly strokeWidth: number;
  readonly strokeDash: "solid" | "dash" | "dot" | "dash-dot";
  readonly fill: string | null;
  readonly hatch?: DrawingHatch;
}

export type ProjectedComponentTemplateCommand =
  | ProjectedCommandBase & { readonly kind: "polyline"; readonly points: readonly ComponentTemplateProjectionOrigin[]; readonly closed: boolean; readonly bendRadius: number }
  | ProjectedCommandBase & { readonly kind: "rectangle"; readonly x: number; readonly y: number; readonly width: number; readonly height: number; readonly radii: readonly [number, number, number, number] }
  | ProjectedCommandBase & { readonly kind: "ellipse"; readonly centerX: number; readonly centerY: number; readonly radiusX: number; readonly radiusY: number }
  | ProjectedCommandBase & { readonly kind: "bezier"; readonly points: readonly ComponentTemplateProjectionOrigin[]; readonly closed: boolean }
  | ProjectedCommandBase & { readonly kind: "text"; readonly x: number; readonly y: number; readonly text: string; readonly fontSize: number }
  | ProjectedCommandBase & { readonly kind: "image"; readonly x: number; readonly y: number; readonly width: number; readonly height: number; readonly cropX: number; readonly cropY: number; readonly cropWidth: number; readonly cropHeight: number; readonly underlay: boolean; readonly url: string | null; readonly error: string | null };

export interface ProjectedComponentTemplateView {
  readonly objectId: string;
  readonly snapshotId: string;
  readonly viewId: string;
  readonly viewKind: HarnessEditorView;
  readonly commands: readonly ProjectedComponentTemplateCommand[];
  readonly bounds: ComponentTemplateViewBounds;
}

/**
 * A separately selectable contact-side copy on the Drawing and the shortest
 * thin companion link back to its source connector.  `visible` is kept
 * separate from the projection so the properties card can describe a hidden
 * existing placement without drawing or hit-testing it.
 */
export interface ContactSideViewProjection {
  readonly placementId: typeof CONTACT_SIDE_VIEW_PLACEMENT_ID;
  readonly visible: boolean;
  readonly drawing: ProjectedComponentTemplateView;
  readonly link: readonly [ComponentTemplateProjectionOrigin, ComponentTemplateProjectionOrigin];
}

const identity = (): ComponentTemplateTransform => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });

function multiply(
  parent: ComponentTemplateTransform,
  child: ComponentTemplateTransform,
): ComponentTemplateTransform {
  return {
    a: parent.a * child.a + parent.c * child.b,
    b: parent.b * child.a + parent.d * child.b,
    c: parent.a * child.c + parent.c * child.d,
    d: parent.b * child.c + parent.d * child.d,
    e: parent.a * child.e + parent.c * child.f + parent.e,
    f: parent.b * child.e + parent.d * child.f + parent.f,
  };
}

function translation(x: number, y: number): ComponentTemplateTransform {
  return { a: 1, b: 0, c: 0, d: 1, e: x, f: y };
}

function nodeTransform(
  node: TemplateNodeV3,
  evaluate: (expression: NumericExpressionV3) => number,
): ComponentTemplateTransform {
  const x = evaluate(node.transform.translateX);
  const y = evaluate(node.transform.translateY);
  const radians = evaluate(node.transform.rotationDegrees) * Math.PI / 180;
  const scaleX = evaluate(node.transform.scaleX);
  const scaleY = evaluate(node.transform.scaleY);
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  return {
    a: cosine * scaleX,
    b: sine * scaleX,
    c: -sine * scaleY,
    d: cosine * scaleY,
    e: x,
    f: y,
  };
}

function finitePositive(value: number): boolean {
  return Number.isFinite(value) && value > 0;
}

function evaluatedPoints(
  points: readonly { readonly x: NumericExpressionV3; readonly y: NumericExpressionV3 }[],
  evaluate: (expression: NumericExpressionV3) => number,
): readonly ComponentTemplateProjectionOrigin[] {
  return points.map(point => ({ x: evaluate(point.x), y: evaluate(point.y) }));
}

function commonCommand(
  node: TemplateNodeV3,
  layerId: string,
  transform: ComponentTemplateTransform,
  opacity: number,
  evaluate: (expression: NumericExpressionV3) => number,
): ProjectedCommandBase | null {
  const strokeWidth = evaluate(node.stroke.width);
  if (!Number.isFinite(strokeWidth) || strokeWidth < 0) return null;
  return {
    nodeId: node.id,
    layerId,
    transform,
    opacity,
    stroke: node.stroke.color,
    strokeWidth,
    strokeDash: node.stroke.dash ?? "solid",
    fill: node.fill.color,
    hatch: node.fill.hatch,
  };
}

function commandForNode(
  node: Exclude<TemplateNodeV3, { readonly kind: "group" }>,
  layerId: string,
  transform: ComponentTemplateTransform,
  opacity: number,
  evaluate: (expression: NumericExpressionV3) => number,
  assetIds: ReadonlySet<string>,
  snapshotId: string,
  resolveAssetUrl: ResolveComponentTemplateAssetUrl | undefined,
): ProjectedComponentTemplateCommand | null {
  const common = commonCommand(node, layerId, transform, opacity, evaluate);
  if (!common) return null;
  if (node.kind === "line" || node.kind === "polyline") {
    const points = evaluatedPoints(node.geometry.points, evaluate);
    const bendRadius = evaluate(node.geometry.bendRadius);
    if (points.length < 2 || !Number.isFinite(bendRadius) || bendRadius < 0) return null;
    return { ...common, kind: "polyline", points, closed: false, bendRadius };
  }
  if (node.kind === "rectangle") {
    const x = evaluate(node.geometry.x);
    const y = evaluate(node.geometry.y);
    const width = evaluate(node.geometry.width);
    const height = evaluate(node.geometry.height);
    const radii = node.geometry.cornerRadii.map(evaluate) as [number, number, number, number];
    if (!finitePositive(width) || !finitePositive(height) || radii.some(value => !Number.isFinite(value))) return null;
    return { ...common, kind: "rectangle", x, y, width, height, radii };
  }
  if (node.kind === "ellipse") {
    const centerX = evaluate(node.geometry.centerX);
    const centerY = evaluate(node.geometry.centerY);
    const radiusX = evaluate(node.geometry.radiusX);
    const radiusY = evaluate(node.geometry.radiusY);
    if (!finitePositive(radiusX) || !finitePositive(radiusY)) return null;
    return { ...common, kind: "ellipse", centerX, centerY, radiusX, radiusY };
  }
  if (node.kind === "bezier") {
    const points = evaluatedPoints(node.geometry.points, evaluate);
    if (points.length < 4 || (points.length - 1) % 3 !== 0) return null;
    return { ...common, kind: "bezier", points, closed: node.geometry.closed };
  }
  if (node.kind === "closedContour") {
    const points = evaluatedPoints(node.geometry.points, evaluate);
    if (points.length < 3) return null;
    return { ...common, kind: "polyline", points, closed: true, bendRadius: 0 };
  }
  if (node.kind === "text") {
    const x = evaluate(node.geometry.x);
    const y = evaluate(node.geometry.y);
    const fontSize = evaluate(node.geometry.fontSize);
    if (!finitePositive(fontSize)) return null;
    return { ...common, kind: "text", x, y, text: node.geometry.text, fontSize };
  }

  const x = evaluate(node.geometry.x);
  const y = evaluate(node.geometry.y);
  const width = evaluate(node.geometry.width);
  const height = evaluate(node.geometry.height);
  if (!finitePositive(width) || !finitePositive(height)) return null;
  let url: string | null = null;
  let error: string | null = null;
  if (!assetIds.has(node.geometry.assetId)) {
    error = "Asset изображения отсутствует в закреплённом шаблоне.";
  } else if (node.geometry.cropWidth <= 0 || node.geometry.cropHeight <= 0) {
    error = "Область обрезки изображения имеет нулевой размер.";
  } else if (!resolveAssetUrl) {
    error = "URL изображения не настроен.";
  } else {
    try {
      url = resolveAssetUrl(snapshotId, node.geometry.assetId).trim() || null;
      if (!url) error = "URL изображения пуст.";
    } catch {
      error = "URL изображения не удалось получить.";
    }
  }
  return {
    ...common,
    kind: "image",
    x,
    y,
    width,
    height,
    cropX: node.geometry.cropX,
    cropY: node.geometry.cropY,
    cropWidth: node.geometry.cropWidth,
    cropHeight: node.geometry.cropHeight,
    underlay: node.geometry.underlay,
    url,
    error,
  };
}

function transformedPoint(transform: ComponentTemplateTransform, x: number, y: number): ComponentTemplateProjectionOrigin {
  return {
    x: transform.a * x + transform.c * y + transform.e,
    y: transform.b * x + transform.d * y + transform.f,
  };
}

function commandBounds(command: ProjectedComponentTemplateCommand): ComponentTemplateViewBounds {
  let points: readonly ComponentTemplateProjectionOrigin[];
  if (command.kind === "polyline" || command.kind === "bezier") {
    points = command.points.map(point => transformedPoint(command.transform, point.x, point.y));
  } else if (command.kind === "ellipse") {
    const center = transformedPoint(command.transform, command.centerX, command.centerY);
    const dx = Math.hypot(command.transform.a * command.radiusX, command.transform.c * command.radiusY);
    const dy = Math.hypot(command.transform.b * command.radiusX, command.transform.d * command.radiusY);
    points = [{ x: center.x - dx, y: center.y - dy }, { x: center.x + dx, y: center.y + dy }];
  } else {
    const x = command.x;
    const y = command.kind === "text" ? command.y - command.fontSize : command.y;
    const width = command.kind === "text" ? Math.max(command.fontSize * 0.62, command.text.length * command.fontSize * 0.62) : command.width;
    const height = command.kind === "text" ? command.fontSize * 1.2 : command.height;
    points = [
      transformedPoint(command.transform, x, y),
      transformedPoint(command.transform, x + width, y),
      transformedPoint(command.transform, x, y + height),
      transformedPoint(command.transform, x + width, y + height),
    ];
  }
  const strokeScale = Math.max(
    Math.hypot(command.transform.a, command.transform.b),
    Math.hypot(command.transform.c, command.transform.d),
  );
  const padding = command.strokeWidth * strokeScale / 2;
  return {
    minX: Math.min(...points.map(point => point.x)) - padding,
    minY: Math.min(...points.map(point => point.y)) - padding,
    maxX: Math.max(...points.map(point => point.x)) + padding,
    maxY: Math.max(...points.map(point => point.y)) + padding,
  };
}

function combinedBounds(commands: readonly ProjectedComponentTemplateCommand[]): ComponentTemplateViewBounds {
  const first = commandBounds(commands[0]!);
  return commands.slice(1).reduce((bounds, command) => {
    const next = commandBounds(command);
    return {
      minX: Math.min(bounds.minX, next.minX),
      minY: Math.min(bounds.minY, next.minY),
      maxX: Math.max(bounds.maxX, next.maxX),
      maxY: Math.max(bounds.maxY, next.maxY),
    };
  }, first);
}

/**
 * Materializes one immutable article snapshot into paint-order canvas commands.
 * A null result means the caller should use its legacy connector renderer.
 */
export function projectComponentTemplateView(
  instance: ComponentTemplateViewInstance,
  viewKind: HarnessEditorView,
  origin: ComponentTemplateProjectionOrigin,
  resolveAssetUrl?: ResolveComponentTemplateAssetUrl,
  drawingTarget: DrawingTarget = viewKind,
  placementId: string | undefined = drawingTarget === "drawing" ? "view:drawing" : undefined,
): ProjectedComponentTemplateView | null {
  // A v5 E4 table is always rendered by the schematic editor. Its drawing is
  // projected separately as a companion, never as a replacement for that table.
  if (viewKind === "e4" && instance.content.schemaVersion === 5) return null;
  if (instance.content.schemaVersion === 5) {
    const content = instance.content;
    const generator = content.drawingGenerators?.find(g => g.target === drawingTarget && g.articles.some(a => a.articleId === instance.articleVariantId));
    if (generator) {
      const generated = materializeGenerator(projectTemplateContentV5ToV3(content), projectTemplateContentV5TableToV1(content), content.drawingContactBindings ?? [], generator, instance.articleVariantId);
      const runtime = { ...content, drawingGenerators: undefined, views: generated.content.views, logicalContacts: generated.content.logicalContacts,
        articleDrawings: [...(content.articleDrawings ?? []).filter(d => !(d.articleVariantId === instance.articleVariantId && d.target === drawingTarget)),
          { articleVariantId: instance.articleVariantId, target: drawingTarget, viewId: generated.view.id, nodeIds: generated.view.layers.flatMap(l => l.nodes.map(n => n.id)), contactPointIds: generated.view.contactPoints.map(p => p.id) }] };
      return projectComponentTemplateView({ ...instance, content: runtime }, viewKind, origin, resolveAssetUrl, drawingTarget, placementId);
    }
  }
  const binding=instance.content.schemaVersion===5 ? findArticleDrawing(instance.content.articleDrawings,instance.articleVariantId,drawingTarget) : undefined;
  const sourceView = instance.content.views.find(candidate => binding?.viewId ? candidate.id===binding.viewId : candidate.kind === viewKind);
  if (!sourceView) return null;
  const view = articleDrawingView(sourceView, instance.content.schemaVersion === 5 ? instance.content.articleDrawings : undefined, instance.articleVariantId,drawingTarget);
  try {
    const v3Content: TemplateContentV3 = instance.content.schemaVersion === 3
      ? instance.content
      : instance.content.schemaVersion === 5 ? projectTemplateContentV5ToV3(instance.content)
      : (() => {
          const { e4ConnectorTable: _table, ...core } = instance.content;
          return { ...core, schemaVersion: 3 };
        })();
    const graphic = instance.content.schemaVersion === 5 ? { ...v3Content, articleVariants: v3Content.articleVariants.map(a=>({...a,contactGroups:null})) } : v3Content;
    const materialized = materializeArticleVariantV3(graphic, instance.articleVariantId);
    const values = resolveTemplateParameterValuesV2(materialized.repeatContent, materialized.repeatOptions);
    const evaluate = (expression: NumericExpressionV3) => evaluateNumericExpressionV3(expression, values);
    const expansions = expandTemplateViewRepeatsV2(materialized.repeatContent, view.id, materialized.repeatOptions);
    const occurrences = new Map<string, readonly RepeatOccurrenceDescriptorV2[]>(
      expansions.map(expansion => [expansion.prototypeGroupId, expansion.occurrences]),
    );
    const repeatedGroupIds = new Set(occurrences.keys());
    const assetIds = new Set(instance.content.assets.map(asset => asset.assetId));
    const commands: ProjectedComponentTemplateCommand[] = [];
    const scale=placementId === undefined ? 1 : drawingScale(instance.drawingPlacements,placementId);
    const angle=placementId === undefined ? 0 : drawingRotation(instance.drawingPlacements,placementId)*Math.PI/180;
    const worldOrigin = {...translation(origin.x, origin.y),a:scale*Math.cos(angle),b:scale*Math.sin(angle),c:-scale*Math.sin(angle),d:scale*Math.cos(angle)};

    for (const layer of view.layers) {
      if (!layer.visible) continue;
      const nodesById = new Map(layer.nodes.map(node => [node.id, node]));
      const ownedIds = new Set(layer.nodes.flatMap(node => node.kind === "group" ? node.geometry.childIds : []));

      const appendNode = (
        node: TemplateNodeV3,
        parentTransform: ComponentTemplateTransform,
        parentOpacity: number,
        ancestors: ReadonlySet<string>,
        occurrenceNumber?: number,
      ): void => {
        if (!node.visible || ancestors.has(node.id)) return;
        const transform = multiply(parentTransform, nodeTransform(node, evaluate));
        const opacity = parentOpacity * node.opacity;
        if (node.kind !== "group") {
          let command = commandForNode(
            node, layer.id, transform, opacity, evaluate, assetIds, instance.snapshotId, resolveAssetUrl,
          );
          const point = drawingTarget==="e4" ? view.contactPoints.find(p=>p.shape?.nodeId===node.id) : undefined;
          if(command && point && (command.kind==="rectangle" || command.kind==="ellipse")) {
            const content=instance.content;
            const rowId=point.logicalContactId.startsWith("generator-row:") ? point.logicalContactId.slice("generator-row:".length)
              : content.schemaVersion===5 ? content.drawingContactBindings?.find(b=>b.logicalContactId===point.logicalContactId)?.seriesRowId : undefined;
            const number=(content.schemaVersion===5 && rowId ? materializeE4ConnectorArticle(projectTemplateContentV5TableToV1(content),instance.articleVariantId).rows.find(r=>r.seriesRowId===rowId)?.number : undefined) ?? content.logicalContacts.find(c=>c.id===point.logicalContactId)?.number ?? "";
            const fill=point.shape?.fillFromWire ? instance.contactWireColors?.[point.logicalContactId] ?? (rowId ? instance.contactWireColors?.[rowId] : undefined) ?? command.fill : command.fill;
            const label=command.kind==="rectangle" ? contactShapeLabel(number,command.x+command.width/2,command.y+command.height/2,command.width,command.height,command.strokeWidth)
              : contactShapeLabel(number,command.centerX,command.centerY,command.radiusX*2,command.radiusY*2,command.strokeWidth,true);
            command={...command,fill,...(fill!==command.fill?{hatch:undefined}:{}),contactLabel:{...label,color:contactLabelColor(fill)}};
          }
          if (command) commands.push(command.kind === "text" && occurrenceNumber !== undefined ? {...command,text:command.text.replaceAll("{{n}}",String(occurrenceNumber))} : command);
          return;
        }
        if (node.geometry.childIds.some(childId => !nodesById.has(childId))) return;
        const childIds = new Set(node.geometry.childIds);
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(node.id);
        for (const child of layer.nodes) {
          if (childIds.has(child.id)) appendNode(child, transform, opacity, nextAncestors, occurrenceNumber);
        }
      };

      for (const node of layer.nodes) {
        if (ownedIds.has(node.id)) continue;
        const repeated = repeatedGroupIds.has(node.id) ? occurrences.get(node.id) : undefined;
        if (repeated) {
          for (const occurrence of repeated) {
            appendNode(node, multiply(worldOrigin, translation(occurrence.offset.x, occurrence.offset.y)), 1, new Set(), occurrence.index + 1);
          }
        } else {
          appendNode(node, worldOrigin, 1, new Set());
        }
      }
    }
    if (commands.length === 0) return null;
    const paintOrderedCommands = [
      ...commands.filter(command => command.kind === "image" && command.underlay),
      ...commands.filter(command => command.kind !== "image" || !command.underlay),
    ];
    return {
      objectId: instance.objectId,
      snapshotId: instance.snapshotId,
      viewId: view.id,
      viewKind,
      commands: paintOrderedCommands,
      bounds: combinedBounds(paintOrderedCommands),
    };
  } catch {
    return null;
  }
}

/**
 * Projects the exact pinned E4 contact drawing onto the harness Drawing.
 * The E4 target preserves template contact labels and `fillFromWire` colours;
 * the reserved placement owns only position, scale, rotation and visibility.
 */
export function projectContactSideView(
  instance: ComponentTemplateViewInstance,
  origin: ComponentTemplateProjectionOrigin,
  connectorBounds: ComponentTemplateViewBounds,
  resolveAssetUrl?: ResolveComponentTemplateAssetUrl,
): ContactSideViewProjection | null {
  const placement = instance.drawingPlacements?.find(item => item.drawingId === CONTACT_SIDE_VIEW_PLACEMENT_ID);
  if (!placement) return null;
  const drawing = projectComponentTemplateView(
    instance, "drawing", {
      x: origin.x + (placement?.offset.x ?? 0),
      y: origin.y + (placement?.offset.y ?? 0),
    }, resolveAssetUrl, "e4", CONTACT_SIDE_VIEW_PLACEMENT_ID,
  );
  if (!drawing) return null;
  return {
    placementId: CONTACT_SIDE_VIEW_PLACEMENT_ID,
    visible: placement.visible,
    drawing,
    link: shortestDrawingLink(connectorBounds, drawing.bounds),
  };
}

export interface E4DrawingCompanion extends ProjectedComponentTemplateView {
  readonly drawingId:string;
  readonly label:string;
  readonly visible:boolean;
  readonly offset:{x:number;y:number};
}

/** Loose primitives form one drawing. Explicit groups can represent several whole drawings. */
export function projectE4DrawingCompanions(instance:ComponentTemplateViewInstance,origin:ComponentTemplateProjectionOrigin,tableWidth:number,resolveAssetUrl?:ResolveComponentTemplateAssetUrl):E4DrawingCompanion[] {
  if(instance.content.schemaVersion!==5) return [];
  const drawing=projectComponentTemplateView(instance,"drawing",{x:0,y:0},resolveAssetUrl,"e4");
  const generator = instance.content.drawingGenerators?.find(g => g.target === "e4" && g.articles.some(a => a.articleId === instance.articleVariantId));
  if (drawing && generator) {
    const placement = instance.drawingPlacements?.find(p => p.drawingId === generator.id), offset = placement?.offset ?? {x:0,y:0};
    const scale = Math.min(1, Math.max(40,tableWidth)/Math.max(1,drawing.bounds.maxX-drawing.bounds.minX),140/Math.max(1,drawing.bounds.maxY-drawing.bounds.minY)) * drawingScale(instance.drawingPlacements,generator.id);
    const transform = {a:scale,b:0,c:0,d:scale,e:origin.x-drawing.bounds.minX*scale+offset.x,f:origin.y-20-drawing.bounds.maxY*scale+offset.y};
    const commands = drawing.commands.map(command=>({...command,transform:multiply(transform,command.transform)}));
    return [{...drawing,viewKind:"e4",drawingId:generator.id,label:"Рисунок",visible:placement?.visible!==false,offset,commands,bounds:combinedBounds(commands)}];
  }
  const binding=findArticleDrawing(instance.content.articleDrawings,instance.articleVariantId,"e4");
  const source=instance.content.views.find(v=>binding?.viewId ? v.id===binding.viewId : v.kind==="drawing");
  if(!drawing || !source) return [];
  const view=articleDrawingView(source,instance.content.articleDrawings,instance.articleVariantId,"e4");
  const nodes=view.layers.flatMap(l=>l.nodes),owned=new Set(nodes.flatMap(n=>n.kind==="group" ? n.geometry.childIds : []));
  const scale=Math.min(1,Math.max(40,tableWidth)/Math.max(1,drawing.bounds.maxX-drawing.bounds.minX),140/Math.max(1,drawing.bounds.maxY-drawing.bounds.minY));
  const roots=nodes.filter(n=>!owned.has(n.id) && n.visible && view.layers.some(l=>l.id===n.layerId && l.visible));
  const separate=roots.every(n=>n.kind==="group" || n.kind==="image");
  const parts=separate ? roots.map(node=>({id:node.id,roots:[node]})) : [{id:roots.length===1 ? roots[0]!.id : view.id,roots}];
  return parts.flatMap((part,index)=>{
    const ids=new Set<string>();
    const visit=(id:string)=>{if(ids.has(id))return;ids.add(id);const n=nodes.find(n=>n.id===id);if(n?.kind==="group")n.geometry.childIds.forEach(visit);};part.roots.forEach(node=>visit(node.id));
    const commands=drawing.commands.filter(c=>ids.has(c.nodeId));if(!commands.length)return [];
    // Old per-primitive offsets must not tear apart an assembled drawing.
    const placement=instance.drawingPlacements?.find(p=>p.drawingId===part.id),offset=placement?.offset ?? {x:0,y:0};
    const placedScale=scale*drawingScale(instance.drawingPlacements,part.id);
    const transform={a:placedScale,b:0,c:0,d:placedScale,e:origin.x-drawing.bounds.minX*placedScale+offset.x,f:origin.y-20-drawing.bounds.maxY*placedScale+offset.y};
    const projected=commands.map(command=>({...command,transform:multiply(transform,command.transform)}));
    const node=part.roots.length===1 ? part.roots[0] : undefined;
    const label=node?.kind==="image" ? (instance.content.assets.find(a=>a.assetId===node.geometry.assetId)?.fileName ?? `Изображение ${index+1}`) : `Рисунок ${index+1}`;
    return [{...drawing,viewKind:"e4" as const,drawingId:part.id,label,visible:placement?.visible!==false,offset,commands:projected,bounds:combinedBounds(projected)}];
  });
}

/** Compatibility aggregate for fit-to-view and hit testing. */
export function projectE4DrawingCompanion(instance:ComponentTemplateViewInstance,origin:ComponentTemplateProjectionOrigin,tableWidth:number,resolveAssetUrl?:ResolveComponentTemplateAssetUrl):ProjectedComponentTemplateView|null {
  const drawings=projectE4DrawingCompanions(instance,origin,tableWidth,resolveAssetUrl).filter(d=>d.visible);
  const commands=drawings.flatMap(d=>d.commands);
  return commands.length ? {...drawings[0]!,commands,bounds:combinedBounds(commands)} : null;
}

/** Closest points of two axis-aligned bounds. Overlap needs no visible link. */
export function shortestDrawingLink(a:ComponentTemplateViewBounds,b:ComponentTemplateViewBounds):readonly [ComponentTemplateProjectionOrigin,ComponentTemplateProjectionOrigin] {
  const axis=(amin:number,amax:number,bmin:number,bmax:number):[number,number]=>amax<bmin ? [amax,bmin] : bmax<amin ? [amin,bmax] : [(Math.max(amin,bmin)+Math.min(amax,bmax))/2,(Math.max(amin,bmin)+Math.min(amax,bmax))/2];
  const [ax,bx]=axis(a.minX,a.maxX,b.minX,b.maxX),[ay,by]=axis(a.minY,a.maxY,b.minY,b.maxY);
  return [{x:ax,y:ay},{x:bx,y:by}];
}

type ImageCacheEntry =
  | { readonly state: "loading" }
  | { readonly state: "loaded"; readonly image: HTMLImageElement }
  | { readonly state: "error" };

export class ComponentTemplateImageCache {
  private readonly entries = new Map<string, ImageCacheEntry>();
  private invalidate: (() => void) | null = null;

  constructor(private readonly createImage: (() => HTMLImageElement) | null =
    typeof Image === "undefined" ? null : () => new Image()) {}

  setInvalidate(invalidate: (() => void) | null): void {
    this.invalidate = invalidate;
  }

  get(url: string): ImageCacheEntry {
    const cached = this.entries.get(url);
    if (cached) return cached;
    if (!this.createImage) {
      const failed = { state: "error" } as const;
      this.entries.set(url, failed);
      return failed;
    }
    const image = this.createImage();
    const loading = { state: "loading" } as const;
    this.entries.set(url, loading);
    image.onload = () => {
      this.entries.set(url, { state: "loaded", image });
      this.invalidate?.();
    };
    image.onerror = () => {
      this.entries.set(url, { state: "error" });
      this.invalidate?.();
    };
    image.src = url;
    return loading;
  }
}

const hatchPatterns = new WeakMap<CanvasRenderingContext2D, Map<string, CanvasPattern>>();

/** Preserve fine strokes in device pixels without rasterizing the vector drawing. */
export function detailStrokeWidth(width:number, transform:Pick<ComponentTemplateTransform,"a"|"b"|"c"|"d">, minimumPixels:number):number {
  if(width<=0||minimumPixels<=0)return width;
  const {a,b,c,d}=transform, sum=a*a+b*b+c*c+d*d, determinant=a*d-b*c;
  const largest=Math.sqrt((sum+Math.sqrt(Math.max(0,sum*sum-4*determinant*determinant)))/2);
  const smallest=largest>0?Math.abs(determinant)/largest:0;
  return smallest>1e-8?Math.max(width,minimumPixels/smallest):width;
}

function applyCommandTransform(context: CanvasRenderingContext2D, command: ProjectedComponentTemplateCommand, minimumPixels:number): void {
  const { a, b, c, d, e, f } = command.transform;
  context.transform(a, b, c, d, e, f);
  context.globalAlpha *= Math.min(1, Math.max(0, command.opacity));
  context.strokeStyle = command.stroke;
  context.lineWidth = minimumPixels>0 && context.getTransform ? detailStrokeWidth(command.strokeWidth,context.getTransform(),minimumPixels) : command.strokeWidth;
  const unit = Math.max(command.strokeWidth, 1);
  context.setLineDash(command.strokeDash === "dash" ? [6 * unit, 4 * unit]
    : command.strokeDash === "dot" ? [unit, 3 * unit]
      : command.strokeDash === "dash-dot" ? [6 * unit, 3 * unit, unit, 3 * unit]
        : []);
  context.fillStyle = command.fill ?? "rgba(0, 0, 0, 0)";
  if (command.fill && command.hatch && typeof document !== "undefined") {
    const key = JSON.stringify([command.fill,command.hatch]);
    const cache = hatchPatterns.get(context) ?? new Map<string,CanvasPattern>();
    if (!hatchPatterns.has(context)) hatchPatterns.set(context,cache);
    const cached = cache.get(key);
    if (cached) { context.fillStyle = cached; return; }
    const hatch = command.hatch, tile = hatchTile(hatch), canvas = document.createElement("canvas");
    canvas.width = canvas.height = Math.ceil(hatch.spacing);
    const brush = canvas.getContext("2d");
    if (brush) {
      brush.scale(canvas.width / hatch.spacing, canvas.height / hatch.spacing);
      if (hatch.backgroundColor) { brush.fillStyle = hatch.backgroundColor; brush.fillRect(0,0,hatch.spacing,hatch.spacing); }
      brush.strokeStyle = brush.fillStyle = command.fill; brush.lineWidth = 1;
      for (const [x1, y1, x2, y2] of tile.lines) { brush.beginPath(); brush.moveTo(x1!, y1!); brush.lineTo(x2!, y2!); brush.stroke(); }
      for (const [x, y, r] of tile.dots) { brush.beginPath(); brush.arc(x!, y!, r!, 0, 2 * Math.PI); brush.fill(); }
      const pattern = context.createPattern(canvas, "repeat");
      if (pattern) { pattern.setTransform(new DOMMatrix().rotate(hatch.angle).scale(hatch.spacing / canvas.width)); context.fillStyle = pattern; if(cache.size>=128) cache.clear(); cache.set(key,pattern); }
    }
  }
}

function paintPath(context: CanvasRenderingContext2D, command: ProjectedComponentTemplateCommand): void {
  if (command.fill) context.fill();
  if (command.strokeWidth > 0) context.stroke();
}

function roundedRectanglePath(
  context: CanvasRenderingContext2D,
  command: Extract<ProjectedComponentTemplateCommand, { readonly kind: "rectangle" }>,
): void {
  const maximum = Math.min(command.width, command.height) / 2;
  const [topLeft, topRight, bottomRight, bottomLeft] = command.radii.map(radius =>
    Math.min(maximum, Math.max(0, radius)),
  );
  context.moveTo(command.x + topLeft!, command.y);
  context.lineTo(command.x + command.width - topRight!, command.y);
  context.quadraticCurveTo(command.x + command.width, command.y, command.x + command.width, command.y + topRight!);
  context.lineTo(command.x + command.width, command.y + command.height - bottomRight!);
  context.quadraticCurveTo(command.x + command.width, command.y + command.height, command.x + command.width - bottomRight!, command.y + command.height);
  context.lineTo(command.x + bottomLeft!, command.y + command.height);
  context.quadraticCurveTo(command.x, command.y + command.height, command.x, command.y + command.height - bottomLeft!);
  context.lineTo(command.x, command.y + topLeft!);
  context.quadraticCurveTo(command.x, command.y, command.x + topLeft!, command.y);
  context.closePath();
}

function drawImagePlaceholder(
  context: CanvasRenderingContext2D,
  command: Extract<ProjectedComponentTemplateCommand, { readonly kind: "image" }>,
): void {
  context.setLineDash([]);
  context.fillStyle = "#fff7e6";
  context.strokeStyle = "#a86519";
  context.lineWidth = Math.max(1, command.strokeWidth);
  context.fillRect(command.x, command.y, command.width, command.height);
  context.strokeRect(command.x, command.y, command.width, command.height);
  context.beginPath();
  context.moveTo(command.x, command.y);
  context.lineTo(command.x + command.width, command.y + command.height);
  context.moveTo(command.x + command.width, command.y);
  context.lineTo(command.x, command.y + command.height);
  context.stroke();
  if (command.width >= 36 && command.height >= 18) {
    context.fillStyle = "#7b4c16";
    context.font = `${Math.min(10, command.height / 3)}px Segoe UI, sans-serif`;
    context.textBaseline = "middle";
    context.fillText("PNG", command.x + 4, command.y + command.height / 2);
  }
}

export function drawProjectedComponentTemplateView(
  context: CanvasRenderingContext2D,
  projection: ProjectedComponentTemplateView,
  imageCache: ComponentTemplateImageCache,
  selected = false,
  minimumStrokePixels = 0,
): void {
  for (const command of projection.commands) {
    context.save();
    applyCommandTransform(context, command, minimumStrokePixels);
    if (command.kind === "polyline") {
      context.beginPath();
      const rounded = !command.closed && command.bendRadius > 0
        ? roundedPolylineCommandsV2(command.points.map(point => [point.x, point.y] as const), command.bendRadius)
        : null;
      if (rounded) rounded.forEach(item => {
        if (item.kind === "move") context.moveTo(item.x, item.y);
        else if (item.kind === "line") context.lineTo(item.x, item.y);
        else context.arcTo(item.cornerX, item.cornerY, item.x, item.y, item.radius);
      });
      else command.points.forEach((point, index) => index === 0
        ? context.moveTo(point.x, point.y)
        : context.lineTo(point.x, point.y));
      if (command.closed) context.closePath();
      paintPath(context, command);
    } else if (command.kind === "rectangle") {
      context.beginPath();
      roundedRectanglePath(context, command);
      paintPath(context, command);
    } else if (command.kind === "ellipse") {
      context.beginPath();
      context.ellipse(command.centerX, command.centerY, command.radiusX, command.radiusY, 0, 0, Math.PI * 2);
      paintPath(context, command);
    } else if (command.kind === "bezier") {
      context.beginPath();
      context.moveTo(command.points[0]!.x, command.points[0]!.y);
      for (let index = 1; index < command.points.length; index += 3) {
        const first = command.points[index]!;
        const second = command.points[index + 1]!;
        const end = command.points[index + 2]!;
        context.bezierCurveTo(first.x, first.y, second.x, second.y, end.x, end.y);
      }
      if (command.closed) context.closePath();
      paintPath(context, command);
    } else if (command.kind === "text") {
      context.fillStyle = command.fill ?? command.stroke;
      context.font = `${command.fontSize}px Segoe UI, sans-serif`;
      context.fillText(command.text, command.x, command.y);
    } else if (command.url) {
      const entry = imageCache.get(command.url);
      if (entry.state === "loaded") {
        const sourceX = command.cropX * entry.image.naturalWidth;
        const sourceY = command.cropY * entry.image.naturalHeight;
        const sourceWidth = command.cropWidth * entry.image.naturalWidth;
        const sourceHeight = command.cropHeight * entry.image.naturalHeight;
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.drawImage(entry.image, sourceX, sourceY, sourceWidth, sourceHeight, command.x, command.y, command.width, command.height);
      } else {
        drawImagePlaceholder(context, command);
      }
    } else {
      drawImagePlaceholder(context, command);
    }
    if(command.contactLabel && command.contactLabel.fontSize>0) {
      const label=command.contactLabel; context.fillStyle=label.color; context.font=label.fontSize+"px Arial";
      context.textAlign="center";context.textBaseline="middle";context.fillText(label.text,label.x,label.y);
    }
    context.restore();
  }
  if (selected) {
    const { minX, minY, maxX, maxY } = projection.bounds;
    context.save();
    context.strokeStyle = "#087bb4";
    context.lineWidth = 2;
    context.setLineDash([5, 4]);
    context.strokeRect(minX - 4, minY - 4, maxX - minX + 8, maxY - minY + 8);
    context.restore();
  }
}
