import type {CoveringLibrary} from "./covering-library";
import { editedDetachedWireBends } from "./detached-wire-editing";
import { freeTwistedPairPaths } from "./drawing-twisted-pair";
import { drawingPhysicalScale, drawingWireWidth } from "./drawing-thickness";
import { parseOuterDiameter } from "./model";
import { reconcileDrawingDimensions, pipeMeasuredWireLength, segmentDimensionKey, type DimensionMode } from "./drawing-dimensions";
import { validDrawingScale } from "./drawing-scale";
import { CONTACT_SIDE_VIEW_PLACEMENT_ID } from "./drawing-scale";
import { reconcileDrawingDocuments, validateDrawingDocuments, type DrawingDocuments } from "./drawing-documents";
import { parsePhysicalTopology } from "./physical-topology-validation";
import { prunePhysicalTopology, removePhysicalSegment } from "./physical-topology";
import { type PhysicalTopology } from "./physical-topology-model";
import {editJoiningPipeBend,moveJoiningPipe,removeJoiningPipe,remapJoiningPipeMemberBends} from "./physical-joining-pipe-editing";
import { carryPhysicalExits, editPhysicalBend, deletePhysicalBend, materializePhysicalPath, type PhysicalDragMode } from "./physical-editing";
import { physicalNodeLocalPoint } from "./physical-ports";
import { joiningPipePoints, joiningPipeExitVector } from "./physical-joining-pipes";
import { refreshAutomaticPhysicalRoutes } from "./physical-wire-routing";
import {
  connectorBaseColumnKeys,
  connectorE4Contacts,
  connectorE4TableGeometry,
  templateTerminalChoices,
  calculateWireCutLength,
  createOrthogonalE4Route,
  createJunctionEndpoint,
  createDefaultConnectorBaseColumns,
  defaultE4WireLead,
  defaultLayerIds,
  isJunctionEndpoint,
  isScreenEndpoint,
  normalizeCableInstance,
  normalizeWireStripProfileBinding,
  validateConnectorLibraryMetadata,
  validateE4Polyline,
  wireE4PathContainsPoint,
  wireEndpointE4Anchor,
  wireGroupHasCommonE4ParallelSpan,
  wireGroupHasScreenCrossSection,
  type ConnectorBaseColumnKey,
  type CableInstance,
  type ConnectorContact,
  type ConnectorContactStatus,
  type ConnectorCustomField,
  type ConnectorInstance,
  type DiffPairGroup,
  type E4Junction,
  type EditorLayer,
  type EditorView,
  type HarnessDesignDocument,
  type Point,
  type WireEndpoint,
  type WireCrossingStyle,
  type WireColorSource,
  type WireInstance,
  type WireMaterialBinding,
  type WireStripProfileBinding,
  type WireScreenGroup,
} from "./model";
import type { ConnectorLibraryBinding } from "./model";
import { polylineLength, routeE4Wire, routeE4WireThroughWaypoints, validateE4Route, type E4RouterAnchor } from "./e4-router";
import { normalizeE4WireLabelPosition } from "./e4-wire-label";
import { resolveWireColorHex } from "./wire-reference-catalog";
import { editedE4Points, preserveE4Leads, moveE4Ends, movedE4Junctions, followE4Junctions, resolveE4JunctionMoves } from "./e4-editing";
import { commonHorizontalPairSpan } from "./e4-parallel-spans";
import { wireBlankEnds, type WireBlankEnd } from "../WireBlankCatalog";

function isFreeDrawingEnd(document: HarnessDesignDocument, wire: WireInstance, end: "from" | "to"): boolean {
  if (!wire.drawingEndpoints) return false;
  const endpoint = end === "from" ? wire.from : wire.to;
  return !document.connectors.some(connector => connector.id === endpoint.connectorId);
}

function assertNoLockedDrawingWires(document: HarnessDesignDocument, wires: readonly WireInstance[]): void {
  const locked = wires.some(wire => document.views.drawing.layers.some(layer => layer.id === wire.layerIds.drawing && layer.locked));
  if (locked) throw new Error("Слой выбранного провода заблокирован.");
}

function assertEditableFreeDrawingEnd(document: HarnessDesignDocument, wire: WireInstance, end: "from" | "to"): void {
  if (!isFreeDrawingEnd(document, wire, end)) throw new Error("Изменить можно только свободный конец независимого провода.");
  assertNoLockedDrawingWires(document, [wire]);
}

export type EditorCommand =
  | {readonly type:"edit-detached-wire-bend";readonly wireId:string;readonly index:number;readonly position:Point;readonly insert?:boolean}
  | {readonly type:"remove-detached-wire-bend";readonly wireId:string;readonly index:number}
  | {readonly type:"set-detached-wire-pair";readonly wireIds:readonly string[];readonly action:"twist"|"straighten";readonly groupId?:string;readonly step?:number;readonly amplitude?:number;readonly variant?:1|2}
  | {readonly type:"edit-e4-bend";readonly wireId:string;readonly index:number;readonly position:Point;readonly mode:PhysicalDragMode;readonly insert?:boolean}
  | {readonly type:"add-visible-pipe-dimension";readonly id:string;readonly segmentId:string;readonly from:number;readonly to:number;readonly pointCount:number;readonly mode:DimensionMode;readonly auxiliary?:boolean}
  | {readonly type:"remove-physical-bend";readonly segmentId:string;readonly index:number}
  | {readonly type:"edit-physical-bend";readonly segmentId:string;readonly index:number;readonly position:Point;readonly mode:PhysicalDragMode;readonly insert?:boolean;readonly region?:"before-enter"|"enter"|"axis"|"exit"|"after-exit";readonly displayPosition?:Point;readonly displayOrigin?:Point}
  | {readonly type:"move-physical-node";readonly nodeId:string;readonly position:Point;readonly mode:PhysicalDragMode}
  | {readonly type:"set-drawing-documents"; readonly documents:DrawingDocuments}
  | { readonly type: "set-physical-topology"; readonly topology: PhysicalTopology; readonly coveringLibrary?:CoveringLibrary }
  | { readonly type: "remove-physical-segment"; readonly segmentId: string }
  | { readonly type: "edit-joining-pipe-bend"; readonly pipeId:string; readonly index:number; readonly position:Point; readonly mode:PhysicalDragMode; readonly insert?:boolean; readonly remove?:boolean }
  | { readonly type: "move-joining-pipe"; readonly pipeId:string; readonly delta:Point }
  | { readonly type: "update-joining-pipe-exit"; readonly pipeId:string; readonly side:"from"|"to"; readonly position:Point }
  | { readonly type: "update-joining-pipe-member-bend"; readonly pipeId:string; readonly memberIndex:number; readonly side:"enter"|"exit"; readonly position?:Point; readonly clear?:boolean; readonly mode?:PhysicalDragMode; readonly origin?:Point; readonly outerOrigin?:Point }
  | { readonly type: "update-joining-pipe-member-boundary"; readonly pipeId:string; readonly memberIndex:number; readonly boundary:"outerEnter"|"axisEnter"|"axisExit"|"outerExit"; readonly origin:Point; readonly position:Point }
  | { readonly type: "update-joining-pipe"; readonly pipeId:string; readonly start?:Point; readonly end?:Point; readonly mode?:"flat"|"round"; readonly width?:number; readonly color?:string; readonly opacity?:number; readonly volumeShading?:boolean }
  | { readonly type: "add-connector"; readonly connector: ConnectorInstance }
  | { readonly type: "set-drawing-placement"; readonly connectorId:string; readonly drawingId:string; readonly scale?:number; readonly rotationDegrees?:number; readonly rotationCenter?:Point; readonly visible?:boolean; readonly offset?:Point }
  /** Creates, updates or removes the reserved Drawing copy of the E4 contact-side view. */
  | { readonly type: "set-contact-side-view"; readonly connectorId:string; readonly visible?:boolean; readonly offset?:Point; readonly scale?:number; readonly rotationDegrees?:number; readonly remove?:boolean }
  | { readonly type: "use-e4-table"; readonly connectorId: string }
  | { readonly type: "refresh-template-terminals"; readonly connectorId: string; readonly catalog: NonNullable<ConnectorInstance["terminalCatalog"]> }
  | { readonly type: "move-connector"; readonly connectorId: string; readonly view: EditorView; readonly position: Point; readonly physicalDragMode?:PhysicalDragMode }
  | { readonly type: "update-connector"; readonly connectorId: string; readonly designation: string; readonly partNumber?: string; readonly libraryCode?: string }
  | { readonly type: "apply-connector-article"; readonly connectorId: string; readonly partNumber: string; readonly contacts: readonly ConnectorContact[]; readonly libraryBinding: ConnectorLibraryBinding }
  | { readonly type: "apply-template-article"; readonly connectorId: string; readonly connector: ConnectorInstance }
  | { readonly type: "flip-connector-orientation"; readonly connectorId: string }
  | { readonly type: "update-contact"; readonly connectorId: string; readonly contactId: string; readonly nameOverride?: string; readonly number?: number; readonly contactType?: string; readonly circuit?: string; readonly terminalArticle?: string; readonly terminalDetails?: ConnectorContact["terminalDetails"]; readonly wire?: string; readonly wireSection?: string; readonly wireDiameterMm?:number|null; readonly materialBinding?: WireMaterialBinding | null; readonly color?: string; readonly secondaryColor?: string; readonly connectionStatus?: ConnectorContactStatus; readonly customValues?: Readonly<Record<string, string>> }
  | { readonly type: "reset-contact-color-auto"; readonly connectorId: string; readonly contactId: string }
  | { readonly type: "add-contact"; readonly connectorId: string; readonly contact: ConnectorContact }
  | { readonly type: "remove-contact"; readonly connectorId: string; readonly contactId: string }
  | { readonly type: "move-contact-row"; readonly connectorId: string; readonly contactId: string; readonly direction: -1 | 1 }
  | { readonly type: "set-name-column-visibility"; readonly connectorId: string; readonly visible: boolean; readonly scope?: "document" }
  | { readonly type: "toggle-base-column-visibility"; readonly connectorId: string; readonly key: ConnectorBaseColumnKey }
  | { readonly type: "add-custom-field"; readonly connectorId: string; readonly field: ConnectorCustomField }
  | { readonly type: "remove-custom-field"; readonly connectorId: string; readonly fieldId: string }
  | { readonly type: "toggle-custom-field-visibility"; readonly connectorId: string; readonly fieldId: string }
  | { readonly type: "remove-connector"; readonly connectorId: string }
  | { readonly type: "add-wire"; readonly wire: WireInstance; readonly targetWireId?: string }
  | { readonly type: "remove-wire"; readonly wireId: string }
  | { readonly type: "add-cable"; readonly cable: CableInstance }
  | { readonly type: "update-cable"; readonly cableId: string; readonly materialBinding?: WireMaterialBinding | null; readonly lengthMm?: number | null; readonly endCorrectionFromMm?: number; readonly endCorrectionToMm?: number; readonly cutRoundingStepMm?: number; readonly sheathStrip?: CableInstance["sheathStrip"] | null }
  | { readonly type: "set-cable-members"; readonly cableId: string; readonly memberWireIds: readonly string[] }
  | { readonly type: "remove-cable"; readonly cableId: string }
  | { readonly type: "update-wire"; readonly wireId: string; readonly circuit?: string; readonly color?: string; readonly materialBinding?: WireMaterialBinding | null; readonly lengthMm?: number | null; readonly endCorrectionFromMm?: number; readonly endCorrectionToMm?: number; readonly cutRoundingStepMm?: number }
  | { readonly type: "set-wire-strip-profile"; readonly wireId: string; readonly end: "from" | "to"; readonly profile: WireStripProfileBinding | null }
  | { readonly type: "set-wire-drawing-end-style"; readonly wireId: string; readonly end: "from" | "to"; readonly style: WireBlankEnd }
  | { readonly type: "set-wire-drawing-endpoint"; readonly wireId: string; readonly end: "from" | "to"; readonly position: Point }
  | { readonly type: "set-wire-drawing-end-styles"; readonly wireIds: readonly string[]; readonly end: "from" | "to"; readonly style: WireBlankEnd }
  | { readonly type: "set-wire-drawing-endpoints-x"; readonly wireIds: readonly string[]; readonly end: "from" | "to"; readonly x: number }
  | { readonly type: "set-e4-wire-label-position"; readonly wireId: string; readonly position: number }
  | { readonly type: "reconnect-wire"; readonly wireId: string; readonly end: "from" | "to"; readonly endpoint: WireEndpoint }
  | { readonly type: "set-wire-route"; readonly wireId: string; readonly route: readonly Point[] }
  | { readonly type: "set-e4-wire-route"; readonly wireId: string; readonly route: readonly Point[] }
  | { readonly type: "move-e4-wire-route-point"; readonly wireId: string; readonly pointIndex: number; readonly position: Point }
  | { readonly type: "remove-e4-wire-route-point"; readonly wireId: string; readonly pointIndex: number }
  | { readonly type: "move-e4-wire-segment"; readonly wireId: string; readonly segmentIndex: number; readonly position: Point; readonly detached?: boolean }
  | { readonly type: "reroute-e4-wires"; readonly wireIds: readonly string[] }
  | { readonly type: "set-wire-crossing-style"; readonly view: EditorView; readonly style: WireCrossingStyle }
  | { readonly type: "set-e4-routing-mode"; readonly mode: "orthogonal" | "angular" }
  | { readonly type: "create-junction"; readonly junction: E4Junction; readonly branchWire?: WireInstance }
  | { readonly type: "move-junction"; readonly junctionId: string; readonly position: Point }
  | { readonly type: "remove-junction"; readonly junctionId: string }
  | { readonly type: "connect-wire-to-junction"; readonly wireId: string; readonly end: "from" | "to"; readonly junctionId: string }
  | { readonly type: "connect-wire-to-wire"; readonly wireId: string; readonly end: "from" | "to"; readonly targetWireId: string; readonly junctionId: string; readonly position: Point }
  | { readonly type: "create-diff-pair"; readonly group: DiffPairGroup }
  | { readonly type: "update-diff-pair"; readonly groupId: string; readonly variant?: 1 | 2; readonly step?: number; readonly amplitude?: number }
  | { readonly type: "remove-diff-pair"; readonly groupId: string }
  | { readonly type: "create-screen"; readonly screen: WireScreenGroup }
  | { readonly type: "update-screen"; readonly screenId: string; readonly wireIds?: readonly string[]; readonly position?: number; readonly label?: string; readonly width?: number; readonly terminalSide?: WireScreenGroup["terminalSide"] }
  | { readonly type: "remove-screen"; readonly screenId: string }
  | { readonly type: "update-layer"; readonly view: EditorView; readonly layerId: string; readonly visible?: boolean; readonly locked?: boolean }
  | { readonly type: "replace-layers"; readonly view: EditorView; readonly layers: readonly EditorLayer[] }
  | { readonly type: "move-layer"; readonly view: EditorView; readonly layerId: string; readonly direction: -1 | 1 };

export function createConnector(
  id: string,
  designation: string,
  contactCount: number,
  e4Position: Point,
  drawingPosition: Point = e4Position,
  partNumber: string = designation,
): ConnectorInstance {
  if (!Number.isSafeInteger(contactCount) || contactCount < 1 || contactCount > 300) {
    throw new Error("Число контактов должно быть от 1 до 300.");
  }
  const normalizedDesignation = designation.trim();
  requireConnectorText(normalizedDesignation, "Укажите обозначение соединителя.");
  const normalizedPartNumber = partNumber.trim();
  requirePartNumberText(normalizedPartNumber, "Укажите артикул шаблона соединителя.");
  const contacts: ConnectorContact[] = Array.from({ length: contactCount }, (_, index) => ({
    id: `${id}:contact:${index + 1}`,
    number: index + 1,
    contactType: "",
    circuit: "",
    terminalArticle: "",
    wire: "",
    color: "",
    secondaryColor: "",
    connectionStatus: "available",
    customValues: {},
  }));
  return {
    id,
    designation: normalizedDesignation,
    libraryCode: "FREE",
    partNumber: normalizedPartNumber,
    contacts,
    schematic: {
      orientation: "contacts-right",
      baseColumns: createDefaultConnectorBaseColumns(),
      customFields: [],
    },
    positions: { e4: e4Position, drawing: drawingPosition },
    layerIds: { e4: defaultLayerIds.connectors, drawing: defaultLayerIds.connectors },
    libraryBinding: { mode: "free" },
  };
}

export function createWire(
  id: string,
  from: WireEndpoint,
  to: WireEndpoint,
  lengthMm: number | null = 100,
  circuit = "",
  color = "#334155",
  endCorrectionFromMm = 0,
  endCorrectionToMm = 0,
  cutRoundingStepMm = 1,
  colorSource?: WireColorSource,
): WireInstance {
  const wire: WireInstance = {
    id,
    from,
    to,
    circuit: circuit.trim(),
    color,
    colorSource,
    lengthMm,
    endCorrectionFromMm,
    endCorrectionToMm,
    cutRoundingStepMm,
    e4Route: [],
    e4RouteMode: "auto",
    e4LabelPosition: 0.5,
    drawingRoute: [],
    layerIds: { e4: defaultLayerIds.wires, drawing: defaultLayerIds.wires },
  };
  calculateWireCutLength(wire);
  return wire;
}

export function applyEditorCommand(
  document: HarnessDesignDocument,
  command: EditorCommand,
): HarnessDesignDocument {
  if(command.type==="update-wire"&&command.lengthMm!==undefined&&(document.drawingDocuments?.dimensions?.some(d=>d.wireId===command.wireId&&!d.auxiliary)||pipeMeasuredWireLength(document,command.wireId).managed))throw new Error("Длина задана размерами на чертеже. Измените размер либо удалите его для ручного ввода.");
  const changed = reconcileDrawingDimensions(document, refreshAutomaticPhysicalRoutes(document, prunePhysicalTopology(applyCommand(document, command))));
  return reconcileDrawingDocuments(document, changed);
}

function applyCommand(document: HarnessDesignDocument, command: EditorCommand): HarnessDesignDocument {
  switch (command.type) {
    case "edit-e4-bend": {
      const wire=findWire(document,command.wireId);
      if(document.views.e4.layers.some(l=>l.id===wire.layerIds.e4&&l.locked))throw new Error("Слой проводов заблокирован.");
      if(screenJunctionEnds(wire))throw new Error("Подключение экрана к проводу должно быть прямым, без изгибов.");
      const request=createE4RoutingRequest(document,wire,wire.id);
      const original=[request.start.position,...wire.e4Route,request.end.position];
      const authoredPosition=document.views.e4.e4RoutingMode==="angular"
        ? snapE4AngularBend(original,command.index,command.position,!!command.insert)
        : command.position;
      const editMode=document.views.e4.e4RoutingMode==="angular"?"adjacent":command.mode;
      const raw=editedE4Points(original,command.index,authoredPosition,editMode,command.insert,false);
      const baseline=[...original];
      if(command.insert){const a=baseline[command.index]!,b=baseline[command.index+1]!;baseline.splice(command.index+1,0,{x:(a.x+b.x)/2,y:(a.y+b.y)/2});}
      const positions=movedE4Junctions(document,wire.id,baseline,raw);
      const points=editedE4Points(original,command.index,authoredPosition,editMode,command.insert);
      const route=preserveE4Leads(points,request.start,request.end).slice(1,-1);
      let changed=followE4Junctions(document,{...document,wires:document.wires.map(w=>w.id===wire.id?{...w,e4Route:route,e4RouteMode:"manual" as const}:w)},positions,new Set([wire.id]),editMode);
      const branches=new Set(changed.junctions.filter(j=>positions.has(j.id)).flatMap(j=>j.wireIds).filter(id=>id!==wire.id));
      if(changed.wires.some(w=>branches.has(w.id)&&changed.views.e4.layers.some(l=>l.id===w.layerIds.e4&&l.locked)))throw new Error("Слой присоединённой ветви заблокирован.");
      changed=repairE4JunctionBranches(changed,branches);
      return setE4WireRoute(changed,wire.id,route);
    }
    case "add-visible-pipe-dimension": {
      if(document.views.drawing.layers.some(l=>(l.id==="wires"||l.id==="dimensions")&&l.locked))throw new Error("Слой трассы или размеров заблокирован.");
      const changed=materializePhysicalPath(document,command.segmentId);
      const segment=changed.physicalTopology?.segments.find(s=>s.id===command.segmentId);
      if(!segment||segment.path.points.length+2!==command.pointCount)throw new Error("Трасса изменилась. Выберите точки размера заново.");
      const docs=changed.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};
      const dimensions=[...docs.dimensions??[],{id:command.id,segmentId:segment.id,
        from:Math.min(command.from,command.to),to:Math.max(command.from,command.to),
        pointCount:command.pointCount,routeKey:segmentDimensionKey(changed,segment.id),
        mode:command.mode,modeOverride:false,auxiliary:command.auxiliary,offset:40,lengthMm:null}];
      return {...changed,drawingDocuments:validateDrawingDocuments({...docs,showDimensions:true,dimensions},changed)};
    }
    case "remove-physical-bend": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой проводов заблокирован.");
      const changed=deletePhysicalBend(document,command.segmentId,command.index);
      return {...changed,physicalTopology:parsePhysicalTopology(changed.physicalTopology,changed)};
    }
    case "edit-physical-bend": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой проводов заблокирован.");
      const changed=editPhysicalBend(document,command.segmentId,command.index,command.position,command.mode,command.insert,command.region,command.displayPosition,command.displayOrigin);
      return {...changed,physicalTopology:parsePhysicalTopology(changed.physicalTopology,changed)};
    }
    case "edit-joining-pipe-bend": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой трассы заблокирован.");
      if(!document.physicalTopology)return document;
      return {...document,physicalTopology:parsePhysicalTopology(editJoiningPipeBend(document,command.pipeId,command.index,command.position,command.mode,command.insert,command.remove),document)};
    }
    case "move-joining-pipe": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой трассы заблокирован.");
      const t=document.physicalTopology;if(!t)return document;
      return {...document,physicalTopology:parsePhysicalTopology(moveJoiningPipe(t,command.pipeId,command.delta),document)};
    }
    case "update-joining-pipe-exit": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой трассы заблокирован.");
      const t=document.physicalTopology,pipe=t?.joiningPipes?.find(p=>p.id===command.pipeId);if(!t||!pipe)return document;
      const axis=joiningPipePoints(pipe),edge=command.side==="from"?axis[0]!:axis.at(-1)!;
      const vector=joiningPipeExitVector(pipe,command.side);
      const length=Math.max(0,Math.min(1e7,(command.position.x-edge.x)*vector.x+(command.position.y-edge.y)*vector.y));
      if(!Number.isFinite(length))throw new Error("Некорректная длина выхода ОП.");
      const key=command.side==="from"?"enterLength":"exitLength";
      return {...document,physicalTopology:parsePhysicalTopology({...t,joiningPipes:t.joiningPipes?.map(p=>p.id===pipe.id?{...p,[key]:length}:p)},document)};
    }
    case "update-joining-pipe-member-bend": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой трассы заблокирован.");
      const t=document.physicalTopology;if(!t)return document;
      const pipe=t.joiningPipes?.find(p=>p.id===command.pipeId);if(!pipe||!pipe.members[command.memberIndex])return document;
      if(!command.clear&&(!command.position||!Number.isFinite(command.position.x)||!Number.isFinite(command.position.y)))throw new Error("Некорректное положение ручки перехода.");
      const members=pipe.members.map((member,index)=>{
        if(index!==command.memberIndex)return member;
        const bendKey=command.side==="enter"?"enterBend":"exitBend";
        const outerKey=command.side==="enter"?"enterOuter":"exitOuter";
        if(command.clear)return {...member,[bendKey]:null};
        if(command.mode==="carry"){
          const previous=member[bendKey]??command.origin;
          const outer=member[outerKey]??command.outerOrigin;
          if(previous&&outer)return {...member,[bendKey]:command.position,[outerKey]:{
            x:outer.x+command.position!.x-previous.x,y:outer.y+command.position!.y-previous.y,
          }};
        }
        return {...member,[bendKey]:command.position};
      });
      return {...document,physicalTopology:parsePhysicalTopology({...t,joiningPipes:t.joiningPipes?.map(p=>p.id===pipe.id?{...p,members}:p)},document)};
    }
    case "update-joining-pipe-member-boundary": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой трассы заблокирован.");
      const t=document.physicalTopology,pipe=t?.joiningPipes?.find(p=>p.id===command.pipeId);if(!t||!pipe||!pipe.members[command.memberIndex])return document;
      if(!Number.isFinite(command.position.x)||!Number.isFinite(command.position.y))throw new Error("Некорректное положение точки перегиба.");
      const {boundary,position,origin}=command;
      if(boundary==="axisEnter"||boundary==="axisExit"){
        const key=boundary==="axisEnter"?"start":"end",delta={x:position.x-origin.x,y:position.y-origin.y};
        const changed={...pipe,[key]:{x:pipe[key].x+delta.x,y:pipe[key].y+delta.y}};
        const remapped={...changed,members:remapJoiningPipeMemberBends(pipe,changed)};
        return {...document,physicalTopology:parsePhysicalTopology({...t,joiningPipes:t.joiningPipes?.map(p=>p.id===pipe.id?remapped:p)},document)};
      }
      const key=boundary==="outerEnter"?"enterOuter":"exitOuter";
      const members=pipe.members.map((member,index)=>index===command.memberIndex?{...member,[key]:position}:member);
      return {...document,physicalTopology:parsePhysicalTopology({...t,joiningPipes:t.joiningPipes?.map(p=>p.id===pipe.id?{...p,members}:p)},document)};
    }
    case "update-joining-pipe": {
      if(document.views.drawing.layers.some(l=>l.id==="wires"&&l.locked))throw new Error("Слой трассы заблокирован.");
      const t=document.physicalTopology;if(!t)return document;
      const next=t.joiningPipes?.map(p=>{
        if(p.id!==command.pipeId)return p;
        const changed={...p,...(command.start!==undefined?{start:command.start}:{}),...(command.end!==undefined?{end:command.end}:{}),...(command.mode!==undefined?{mode:command.mode}:{}),...(command.width!==undefined?{width:command.width}:{}),...(command.color!==undefined?{color:command.color}:{}),...(command.opacity!==undefined?{opacity:command.opacity}:{}),...(command.volumeShading!==undefined?{volumeShading:command.volumeShading}:{})};
        return command.start!==undefined||command.end!==undefined?{...changed,members:remapJoiningPipeMemberBends(p,changed)}:changed;
      });
      return {...document,physicalTopology:parsePhysicalTopology({...t,joiningPipes:next},document)};
    }
    case "move-physical-node": {
      if(document.views.drawing.layers.some(l=>(l.id==="wires"||l.id===defaultLayerIds.connectionPoints)&&l.locked))throw new Error("Слой трассы заблокирован.");
      const t=document.physicalTopology,node=t?.nodes.find(n=>n.id===command.nodeId);if(!t||!node)return document;
      const changed={...document,physicalTopology:{...t,nodes:t.nodes.map(n=>n.id===node.id?{...n,position:physicalNodeLocalPoint(document,n,command.position)}:n)}};
      const result=carryPhysicalExits(document,changed,new Set([node.id]),command.mode);
      return {...result,physicalTopology:parsePhysicalTopology(result.physicalTopology,result)};
    }
    case "set-drawing-documents":
      if(document.views.drawing.layers.some(l=>l.id==="dimensions"&&l.locked)) throw new Error("Слой размеров заблокирован.");
      return {...document,drawingDocuments:validateDrawingDocuments(command.documents,document)};
    case "set-physical-topology":
      if (document.views.drawing.layers.some(layer => layer.id === "wires" && layer.locked)) throw new Error("Слой проводов заблокирован.");
      return { ...document, ...(command.coveringLibrary?{drawingDocuments:validateDrawingDocuments({...document.drawingDocuments??{tables:[],leaders:[],bomOrder:[]},coveringLibrary:command.coveringLibrary},document)}:{}), physicalTopology: parsePhysicalTopology(command.topology, document) };
    case "remove-physical-segment":
      if (document.views.drawing.layers.some(layer => layer.id === "wires" && layer.locked)) throw new Error("Слой проводов заблокирован.");
      if(document.physicalTopology?.joiningPipes?.some(p=>p.id===command.segmentId))return {...document,physicalTopology:removeJoiningPipe(document.physicalTopology,command.segmentId)};
      return { ...document, physicalTopology: parsePhysicalTopology(removePhysicalSegment(document, command.segmentId), document) };
    case "add-connector":
      if (document.connectors.some((item) => item.id === command.connector.id)) {
        throw new Error("Соединитель с таким ID уже существует.");
      }
      validateConnectorLibraryMetadata(command.connector);
      return rememberCustomWireColors(rebuildConnectorE4Wires(
        { ...document, connectors: [...document.connectors, command.connector] },
        command.connector.id,
      ), command.connector.contacts.flatMap((contact) => [contact.color, contact.secondaryColor ?? ""]));
    case "set-drawing-placement":
      return updateConnector(document,command.connectorId,connector=>{
        if(connector.libraryBinding?.mode!=="template") throw new Error("Рисунок доступен библиотечному компоненту.");
        if(!command.drawingId.trim() || command.offset && (!Number.isFinite(command.offset.x) || !Number.isFinite(command.offset.y))) throw new Error("Некорректное положение рисунка.");
        if(command.rotationDegrees!==undefined && (!Number.isFinite(command.rotationDegrees)||Math.abs(command.rotationDegrees)>360))throw new Error("Угол рисунка: от −360 до 360°.");
        if(command.scale!==undefined&&!validDrawingScale(command.scale)) throw new Error("Масштаб рисунка: 5–2000%.");
        const previous=connector.drawingPlacements?.find(p=>p.drawingId===command.drawingId) ?? {drawingId:command.drawingId,visible:true,offset:{x:0,y:0}};
        let positions=connector.positions;
        if(command.rotationDegrees!==undefined && command.rotationCenter) {
          const center=command.rotationCenter;
          if(!Number.isFinite(center.x)||!Number.isFinite(center.y))throw new Error("Некорректный центр поворота.");
          const angle=(command.rotationDegrees-(previous.rotationDegrees??0))*Math.PI/180;
          const x=connector.positions.drawing.x-center.x,y=connector.positions.drawing.y-center.y;
          positions={...positions,drawing:{x:center.x+x*Math.cos(angle)-y*Math.sin(angle),y:center.y+x*Math.sin(angle)+y*Math.cos(angle)}};
        }
        return {...connector,positions,drawingPlacements:[...(connector.drawingPlacements ?? []).filter(p=>p.drawingId!==command.drawingId),{...previous,...(command.visible===undefined ? {} : {visible:command.visible}),...(command.offset ? {offset:command.offset} : {}),...(command.scale===undefined?{}:{scale:command.scale}),...(command.rotationDegrees===undefined?{}:{rotationDegrees:command.rotationDegrees})}]};
      });
    case "set-contact-side-view":
      return updateConnector(document,command.connectorId,connector=>{
        if(connector.libraryBinding?.mode!=="template") throw new Error("Вид со стороны контактов доступен библиотечному компоненту.");
        if(command.remove) {
          return {...connector,drawingPlacements:(connector.drawingPlacements??[]).filter(placement=>placement.drawingId!==CONTACT_SIDE_VIEW_PLACEMENT_ID)};
        }
        if(command.offset && (!Number.isFinite(command.offset.x) || !Number.isFinite(command.offset.y))) throw new Error("Некорректное положение вида со стороны контактов.");
        if(command.rotationDegrees!==undefined && (!Number.isFinite(command.rotationDegrees)||Math.abs(command.rotationDegrees)>360))throw new Error("Угол вида: от −360 до 360°.");
        if(command.scale!==undefined&&!validDrawingScale(command.scale)) throw new Error("Масштаб вида: 5–2000%.");
        const previous=connector.drawingPlacements?.find(placement=>placement.drawingId===CONTACT_SIDE_VIEW_PLACEMENT_ID);
        const initial={drawingId:CONTACT_SIDE_VIEW_PLACEMENT_ID,visible:true,offset:{x:160,y:0},scale:1};
        const next={...(previous??initial),...(command.visible===undefined?{}:{visible:command.visible}),...(command.offset?{offset:command.offset}:{}),...(command.scale===undefined?{}:{scale:command.scale}),...(command.rotationDegrees===undefined?{}:{rotationDegrees:command.rotationDegrees})};
        return {...connector,drawingPlacements:[...(connector.drawingPlacements??[]).filter(placement=>placement.drawingId!==CONTACT_SIDE_VIEW_PLACEMENT_ID),next]};
      });
    case "move-connector":
      {
      if (!Number.isFinite(command.position.x) || !Number.isFinite(command.position.y)) {
        throw new Error("Положение соединителя задано неверно.");
      }
      const moved = {
        ...document,
        connectors: replaceRequired(document.connectors, command.connectorId, (connector) => ({
          ...connector,
          positions: { ...connector.positions, [command.view]: command.position },
        }), "Соединитель не найден."),
      };
      if (command.view !== "e4") {
        const connector = document.connectors.find(c => c.id === command.connectorId)!;
        const delta = { x: command.position.x - connector.positions.drawing.x, y: command.position.y - connector.positions.drawing.y };
        let shiftedWiresChanged = false;
        const shiftedWires = moved.wires.map(wire => {
          if (!wire.drawingEndpoints) return wire;
          const fromAttached = wire.from.connectorId === command.connectorId;
          const toAttached = wire.to.connectorId === command.connectorId;
          if (!fromAttached && !toAttached) return wire;
          shiftedWiresChanged = true;
          return { ...wire, drawingEndpoints: {
            from: fromAttached ? { x: wire.drawingEndpoints.from.x + delta.x, y: wire.drawingEndpoints.from.y + delta.y } : wire.drawingEndpoints.from,
            to: toAttached ? { x: wire.drawingEndpoints.to.x + delta.x, y: wire.drawingEndpoints.to.y + delta.y } : wire.drawingEndpoints.to,
          } };
        });
        const shifted = shiftedWiresChanged ? { ...moved, wires: shiftedWires } : moved;
        return command.physicalDragMode
          ? carryPhysicalExits(document,shifted,new Set(document.physicalTopology?.nodes.filter(n=>n.connectorId===command.connectorId).map(n=>n.id)),command.physicalDragMode)
          : shifted;
      }
      if(command.physicalDragMode){
        const connector=document.connectors.find(c=>c.id===command.connectorId)!;
        if(document.views.e4.layers.some(l=>l.locked&&(l.id===connector.layerIds.e4||document.wires.some(w=>[w.from,w.to].some(e=>e.connectorId===connector.id)&&w.layerIds.e4===l.id))))throw new Error("Слой соединителя или проводов заблокирован.");
        let changed=moved;
        const proposals=new Map<string,ReadonlyMap<string,Point>>(),owners=new Set<string>();
        for(const wire of document.wires){
          if(![wire.from,wire.to].some(e=>e.connectorId===command.connectorId)||screenJunctionEnds(wire))continue;
          const a=wireEndpointE4Anchor(document,wire.from),b=wireEndpointE4Anchor(document,wire.to);
          const start=wireEndpointE4Anchor(moved,wire.from),end=wireEndpointE4Anchor(moved,wire.to);
          if(!a||!b||!start||!end)continue;
          const original=[a.position,...wire.e4Route,b.position];
          const raw=moveE4Ends(original,start.position,end.position,command.physicalDragMode);
          const baseline=moveE4Ends(original,a.position,b.position,command.physicalDragMode);
          proposals.set(wire.id,movedE4Junctions(document,wire.id,baseline,raw));
          owners.add(wire.id);
          const points=preserveE4Leads(raw,start,end);
          changed={...changed,wires:changed.wires.map(w=>w.id===wire.id?{...w,e4Route:points.slice(1,-1),e4RouteMode:"manual"}:w)};
        }
        const positions=resolveE4JunctionMoves(document,proposals);
        const branches=new Set(document.junctions.filter(j=>positions.has(j.id)).flatMap(j=>j.wireIds.filter(id=>{
          const wire=findWire(changed,id);
          return !owners.has(id)||[wire.from,wire.to].some(e=>e.junctionId===j.id)||!wireE4PathContainsPoint(changed,wire,positions.get(j.id)!);
        })));
        changed=followE4Junctions(document,changed,positions,new Set([...owners].filter(id=>!branches.has(id))),command.physicalDragMode);
        if(changed.wires.some(w=>branches.has(w.id)&&changed.views.e4.layers.some(l=>l.id===w.layerIds.e4&&l.locked)))throw new Error("Слой присоединённой ветви заблокирован.");
        changed=repairE4JunctionBranches(changed,branches);
        for(const id of screenAttachmentWireIds(changed,changed.wires.filter(w=>[w.from,w.to].some(e=>e.connectorId===command.connectorId)).map(w=>w.id)))changed=rerouteWireE4ThroughJunctions(changed,id);
        for(const junction of changed.junctions)validateJunctionAgainstWires(changed,junction);
        validateWireGroups(changed);
        return changed;
      }
      try {
        return rebuildConnectorE4Wires(
          moved,
          command.connectorId,
          document.connectors.find((connector) => connector.id === command.connectorId),
        );
      } catch {
        // A drag must never be rejected because a temporary obstacle layout has
        // no solution. Keep the last valid traces; diagnostics will expose the
        // stale route until the next successful global reflow.
        return retainEditableE4Routes(moved);
      }
      }
    case "update-connector": {
      const designation = command.designation.trim();
      requireConnectorText(designation, "Укажите обозначение соединителя.");
      const partNumber = command.partNumber?.trim();
      if (partNumber !== undefined) requirePartNumberText(partNumber, "Укажите артикул шаблона соединителя.");
      const libraryCode = command.libraryCode?.trim();
      if (libraryCode !== undefined) requireConnectorText(libraryCode, "Укажите код свободного блока.");
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => {
          if (partNumber !== undefined && connector.libraryBinding?.mode === "series" &&
              partNumber !== connector.libraryBinding.partNumber) {
            throw new Error("Артикул библиотечного соединителя можно изменить только выбором артикула серии.");
          }
          if (partNumber !== undefined && connector.libraryBinding?.mode === "template" &&
              partNumber !== connector.partNumber) {
            throw new Error("Артикул закреплённого шаблона определяется выбранным вариантом библиотеки.");
          }
          if (libraryCode !== undefined && (connector.libraryBinding?.mode === "series" || connector.libraryBinding?.mode === "template")) {
            throw new Error("Код библиотечной серии определяется справочником.");
          }
          return {
            ...connector,
            designation,
            libraryCode: libraryCode ?? connector.libraryCode ?? "FREE",
            partNumber: partNumber ?? connector.partNumber,
          };
        });
    }
    case "apply-connector-article": {
      const partNumber = command.partNumber.trim();
      requirePartNumberText(partNumber, "Укажите артикул соединителя.");
      const connector = document.connectors.find((item) => item.id === command.connectorId);
      if (!connector) throw new Error("Соединитель не найден.");
      if (command.libraryBinding.mode !== "series") {
        throw new Error("Команда выбора артикула должна сохранять привязку к серии.");
      }
      if (command.libraryBinding.partNumber !== partNumber) {
        throw new Error("Артикул команды не совпадает с артикулом привязки к серии.");
      }
      if (connector.libraryBinding?.mode === "series" &&
          connector.libraryBinding.seriesId !== command.libraryBinding.seriesId) {
        throw new Error("Нельзя заменить серию уже привязанного библиотечного соединителя.");
      }
      const contacts = command.contacts.map((contact) => normalizeContact(contact, connector.schematic.customFields));
      validateUniqueContacts(contacts);
      const candidate: ConnectorInstance = {
        ...connector,
        partNumber,
        contacts,
        libraryBinding: command.libraryBinding,
      };
      validateConnectorLibraryMetadata(candidate);
      const retainedContactIds = new Set(contacts.map((contact) => contact.id));
      const connectedContactIds = new Set(document.wires.flatMap((wire) => [wire.from, wire.to]
        .filter((endpoint) => !isJunctionEndpoint(endpoint) && !isScreenEndpoint(endpoint) &&
          endpoint.connectorId === command.connectorId)
        .map((endpoint) => endpoint.contactId)));
      if ([...connectedContactIds].some((contactId) => !retainedContactIds.has(contactId))) {
        throw new Error("Выбранный артикул удалит подключённые контакты. Сначала переподключите или удалите их провода.");
      }
      if (contacts.some((contact) => connectedContactIds.has(contact.id) && contact.connectionStatus === "not-connected")) {
        throw new Error("Выбранный артикул помечает подключённый контакт как неподключённый.");
      }
      const updated = updateConnectorE4Geometry(document, command.connectorId, (item) => ({
        ...candidate,
        positions: item.positions,
        drawingPlacements: item.drawingPlacements,
      }));
      return rememberCustomWireColors(updated, contacts.flatMap((contact) => [contact.color, contact.secondaryColor ?? ""]));
    }
    case "use-e4-table": {
      const before = document.connectors.find(item => item.id === command.connectorId);
      if (!before) throw new Error("Соединитель не найден.");
      if (before.e4TableMode) return document;
      const changed = updateConnector(document, command.connectorId, connector => ({ ...connector, e4TableMode: true }));
      try { return rebuildConnectorE4Wires(changed, command.connectorId, before); }
      catch { return retainEditableE4Routes(changed); }
    }
    case "refresh-template-terminals":
      return updateConnector(document, command.connectorId, connector => {
        if (connector.libraryBinding?.mode !== "template" || connector.libraryBinding.templateId !== command.catalog.templateId) {
          throw new Error("Список терминалов принадлежит другому шаблону.");
        }
        const updated = { ...connector, terminalCatalog: command.catalog };
        validateConnectorLibraryMetadata(updated);
        return updated;
      });
    case "apply-template-article": {
      const current = document.connectors.find((item) => item.id === command.connectorId);
      if (!current) throw new Error("Соединитель не найден.");
      const previousBinding = current.libraryBinding;
      const nextBinding = command.connector.libraryBinding;
      if (previousBinding?.mode !== "template" || nextBinding?.mode !== "template" ||
          command.connector.id !== current.id ||
          previousBinding.templateId !== nextBinding.templateId ||
          previousBinding.templateVersion !== nextBinding.templateVersion ||
          previousBinding.versionSha256 !== nextBinding.versionSha256) {
        throw new Error("Артикул можно сменить только внутри закреплённой версии шаблона.");
      }
      validateConnectorLibraryMetadata(command.connector);
      const retainedContactIds = new Set(command.connector.contacts.map((contact) => contact.id));
      const connectedContactIds = new Set(document.wires.flatMap((wire) => [wire.from, wire.to]
        .filter((endpoint) => !isJunctionEndpoint(endpoint) && !isScreenEndpoint(endpoint) &&
          endpoint.connectorId === command.connectorId)
        .map((endpoint) => endpoint.contactId)));
      if ([...connectedContactIds].some((contactId) => !retainedContactIds.has(contactId))) {
        throw new Error("Выбранный артикул удалит подключённые контакты. Сначала переподключите или удалите их провода.");
      }
      const updated = updateConnectorE4Geometry(document, command.connectorId, (item) => ({
        ...command.connector,
        positions: item.positions,
        drawingPlacements: item.drawingPlacements,
      }));
      return rememberCustomWireColors(updated, command.connector.contacts
        .flatMap((contact) => [contact.color, contact.secondaryColor ?? ""]));
    }
    case "flip-connector-orientation":
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => ({
        ...connector,
        schematic: {
          ...connector.schematic,
          orientation: connector.schematic.orientation === "contacts-left" ? "contacts-right" : "contacts-left",
        },
      }));
    case "update-contact": {
      const updated = updateConnectorE4Geometry(document, command.connectorId, (connector) => {
        if ((connector.libraryBinding?.mode === "series" || connector.libraryBinding?.mode === "template") &&
            (command.number !== undefined || command.contactType !== undefined)) {
          throw new Error("Номер и тип библиотечного контакта определяются выбранным артикулом серии.");
        }
        if (command.number !== undefined) requireContactNumber(command.number);
        if (command.connectionStatus !== undefined) requireContactStatus(command.connectionStatus);
        if (command.connectionStatus === "not-connected" && isContactConnected(document, command.connectorId, command.contactId)) {
          throw new Error("Нельзя пометить контакт как неподключённый, пока к нему подключён провод.");
        }
        const customValues = command.customValues === undefined
          ? undefined
          : normalizeCustomValues(command.customValues, connector.schematic.customFields);
        const contacts = replaceRequired(connector.contacts, command.contactId, (contact) => ({
          ...contact,
          ...(command.nameOverride === undefined ? {} : { nameOverride: normalizeValue(command.nameOverride, "Назначение контакта") }),
          number: command.number ?? contact.number,
          contactType: command.contactType === undefined ? contact.contactType : normalizeValue(command.contactType, "Тип контакта"),
          circuit: command.circuit === undefined ? contact.circuit : normalizeValue(command.circuit, "Цепь контакта"),
          terminalArticle: command.terminalArticle === undefined
            ? contact.terminalArticle
            : normalizeTemplateTerminalArticle(
              connector,
              contact,
              normalizeValue(command.terminalArticle, "Артикул терминала"),
            ),
          terminalDetails: command.terminalArticle === undefined ? contact.terminalDetails
            : command.terminalArticle === contact.terminalArticle ? command.terminalDetails ?? contact.terminalDetails
              : command.terminalDetails,
          ...((command.wire!==undefined||command.wireSection!==undefined||command.wireDiameterMm!==undefined)?{wireDiameterMm:command.wireDiameterMm==null?undefined:parseOuterDiameter(command.wireDiameterMm)}:{}),
          wire: command.wire === undefined ? contact.wire : normalizeValue(command.wire, "Провод контакта"),
          ...(command.wireSection === undefined ? {} : { wireSection: normalizeValue(command.wireSection, "Сечение провода") }),
          materialBinding: command.materialBinding === undefined && command.wire === undefined && command.wireSection === undefined
            ? contact.materialBinding : command.materialBinding ?? undefined,
          color: command.color === undefined ? contact.color : normalizeValue(command.color, "Цвет провода контакта"),
          secondaryColor: command.secondaryColor === undefined
            ? contact.secondaryColor ?? ""
            : normalizeValue(command.secondaryColor, "Второй цвет провода контакта"),
          colorMode: command.color === undefined && command.secondaryColor === undefined ? contact.colorMode : "manual",
          connectionStatus: command.connectionStatus ?? contact.connectionStatus,
          customValues: customValues ?? contact.customValues,
        }), "Контакт не найден.");
        validateUniqueContacts(contacts);
        return { ...connector, contacts };
      });
      const synchronized = command.color === undefined && command.secondaryColor === undefined
        ? updated
        : syncDirectWireColors(updated, command.connectorId, command.contactId);
      const materialChanged = command.materialBinding !== undefined || command.wire !== undefined || command.wireSection !== undefined;
      const contactMaterial = contactAtEndpoint(synchronized, { connectorId: command.connectorId, contactId: command.contactId })?.materialBinding;
      const withMaterial = materialChanged ? {
        ...synchronized,
        wires: synchronized.wires.map(wire => [wire.from, wire.to].some(endpoint =>
          sameConnectorEndpoint(endpoint, command.connectorId, command.contactId))
          ? { ...wire, materialBinding: contactMaterial } : wire),
      } : synchronized;
      const diameterChanged=command.wireDiameterMm!==undefined||command.wire!==undefined||command.wireSection!==undefined;
      const endpoints=document.wires.filter(w=>[w.from,w.to].some(e=>e.connectorId===command.connectorId&&e.contactId===command.contactId)).flatMap(w=>[w.from,w.to]);
      const sized=diameterChanged?{...withMaterial,connectors:withMaterial.connectors.map(c=>({...c,contacts:c.contacts.map(p=>endpoints.some(e=>e.connectorId===c.id&&e.contactId===p.id)?{...p,wireDiameterMm:command.wireDiameterMm==null?undefined:parseOuterDiameter(command.wireDiameterMm)}:p)}))}:withMaterial;
      const linked = materialChanged
        ? syncContactWireMaterial(sized, command.connectorId, command.contactId) : sized;
      const rerouted = linked === sized ? linked : rerouteE4WireBatch(linked,
        linked.wires.filter(wire => [wire.from, wire.to].some(endpoint =>
          sameConnectorEndpoint(endpoint, command.connectorId, command.contactId))).map(wire => wire.id));
      return rememberCustomWireColors(rerouted, [command.color ?? "", command.secondaryColor ?? ""]);
    }
    case "reset-contact-color-auto":
      return resetContactColorToAutomatic(document, command.connectorId, command.contactId);
    case "add-contact": {
      const updated = updateConnectorE4Geometry(document, command.connectorId, (connector) => {
        if (connector.libraryBinding?.mode === "series" || connector.libraryBinding?.mode === "template") {
          throw new Error("Число контактов библиотечного соединителя определяется выбранным артикулом серии.");
        }
        const contact = normalizeContact(command.contact, connector.schematic.customFields);
        if (contact.libraryContact !== undefined && contact.libraryContact !== null) {
          throw new Error("Свободная строка не должна ссылаться на позицию библиотечной серии.");
        }
        const contacts = [...connector.contacts, contact];
        validateUniqueContacts(contacts);
        return { ...connector, contacts };
      });
      return rememberCustomWireColors(updated, [command.contact.color, command.contact.secondaryColor ?? ""]);
    }
    case "move-contact-row": {
      const reorder = (connector: ConnectorInstance): ConnectorInstance => {
        const rows = connectorE4Contacts(connector).map(contact => contact.id);
        const index = rows.indexOf(command.contactId);
        if (index < 0) throw new Error("Контакт не найден.");
        const next = index + command.direction;
        if (next < 0 || next >= rows.length) return connector;
        [rows[index], rows[next]] = [rows[next]!, rows[index]!];
        return { ...connector, schematic: { ...connector.schematic, rowOrder: rows } };
      };
      try { return updateConnectorE4Geometry(document, command.connectorId, reorder); }
      catch {
        // A temporary crossing must not prevent the next row move that resolves it.
        // Keep valid contact leads and report clearance conflicts through diagnostics.
        return retainEditableE4Routes(updateConnector(document, command.connectorId, reorder));
      }
    }
    case "remove-contact": {
      const connector = document.connectors.find((item) => item.id === command.connectorId);
      if (connector?.libraryBinding?.mode === "series" || connector?.libraryBinding?.mode === "template") {
        throw new Error("Строки библиотечного соединителя определяются выбранным артикулом серии.");
      }
      if (document.wires.some((wire) =>
        (isConnectorEndpoint(wire.from, command.connectorId, command.contactId)) ||
        (isConnectorEndpoint(wire.to, command.connectorId, command.contactId)))) {
        throw new Error("Нельзя удалить контакт, к которому подключён провод.");
      }
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => ({
        ...connector,
        contacts: removeRequired(connector.contacts, command.contactId, "Контакт не найден."),
      }));
    }
    case "set-name-column-visibility": {
      const targets = command.scope === "document"
        ? document.connectors.filter(connector => connector.libraryBinding?.mode === "template").map(connector => connector.id)
        : [command.connectorId];
      return targets.reduce((current, id) => updateConnectorE4Geometry(current, id, connector => ({
        ...connector, schematic: { ...connector.schematic, showName: command.visible },
      })), document);
    }
    case "toggle-base-column-visibility":
      if (!connectorBaseColumnKeys.includes(command.key)) throw new Error("Базовая колонка не найдена.");
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => ({
        ...connector,
        schematic: {
          ...connector.schematic,
          baseColumns: connector.schematic.baseColumns.map((column) =>
            column.key === command.key ? { ...column, visible: !column.visible } : column),
        },
      }));
    case "add-custom-field":
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => {
        const field = normalizeCustomField(command.field);
        if (connector.schematic.customFields.some((item) => item.id === field.id)) {
          throw new Error("Справочное поле с таким ID уже существует.");
        }
        return {
          ...connector,
          schematic: {
            ...connector.schematic,
            customFields: [...connector.schematic.customFields, field],
          },
        };
      });
    case "remove-custom-field":
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => ({
        ...connector,
        contacts: connector.contacts.map((contact) => {
          const { [command.fieldId]: _removed, ...customValues } = contact.customValues;
          return { ...contact, customValues };
        }),
        schematic: {
          ...connector.schematic,
          customFields: removeRequired(connector.schematic.customFields, command.fieldId, "Справочное поле не найдено."),
        },
      }));
    case "toggle-custom-field-visibility":
      return updateConnectorE4Geometry(document, command.connectorId, (connector) => ({
        ...connector,
        schematic: {
          ...connector.schematic,
          customFields: replaceRequired(connector.schematic.customFields, command.fieldId, (field) => ({
            ...field,
            visible: !field.visible,
          }), "Справочное поле не найдено."),
        },
      }));
    case "remove-connector": {
      if (!document.connectors.some((item) => item.id === command.connectorId)) {
        throw new Error("Соединитель не найден.");
      }
      const removedWireIds = document.wires.filter((wire) =>
        isConnectorEndpoint(wire.from, command.connectorId) || isConnectorEndpoint(wire.to, command.connectorId)).map((wire) => wire.id);
      const changed = cleanupWireReferences({
        ...document,
        connectors: document.connectors.filter((item) => item.id !== command.connectorId),
        wires: document.wires.filter((wire) => !removedWireIds.includes(wire.id)),
      }, removedWireIds);
      return rebuildConnectorE4Wires(changed, command.connectorId, document.connectors.find((item) => item.id === command.connectorId));
    }
    case "add-cable": {
      const cable = normalizeCableInstance(command.cable);
      if (document.cables.some((item) => item.id === cable.id)) {
        throw new Error("Кабель с таким ID уже существует.");
      }
      validateCableMembership(document, cable);
      return { ...document, cables: [...document.cables, cable] };
    }
    case "update-cable": {
      const existing = document.cables.find((item) => item.id === command.cableId);
      if (!existing) throw new Error("Кабель не найден.");
      const cable = normalizeCableInstance({
        ...existing,
        materialBinding: command.materialBinding === undefined
          ? existing.materialBinding : command.materialBinding ?? undefined,
        lengthMm: command.lengthMm === undefined ? existing.lengthMm : command.lengthMm,
        endCorrectionFromMm: command.endCorrectionFromMm ?? existing.endCorrectionFromMm,
        endCorrectionToMm: command.endCorrectionToMm ?? existing.endCorrectionToMm,
        cutRoundingStepMm: command.cutRoundingStepMm ?? existing.cutRoundingStepMm,
        sheathStrip: command.sheathStrip === undefined ? existing.sheathStrip : command.sheathStrip ?? undefined,
      });
      validateCableMembership(document, cable, cable.id);
      return { ...document, cables: document.cables.map((item) => item.id === cable.id ? cable : item) };
    }
    case "set-cable-members": {
      const existing = document.cables.find((item) => item.id === command.cableId);
      if (!existing) throw new Error("Кабель не найден.");
      const cable = normalizeCableInstance({ ...existing, memberWireIds: command.memberWireIds });
      validateCableMembership(document, cable, cable.id);
      return { ...document, cables: document.cables.map((item) => item.id === cable.id ? cable : item) };
    }
    case "remove-cable":
      return { ...document, cables: removeRequired(document.cables, command.cableId, "Кабель не найден.") };
    case "add-wire":
      validateWire(document, command.wire);
      if (document.wires.some((item) => item.id === command.wire.id)) {
        throw new Error("Провод с таким ID уже существует.");
      }
      const selectedMaterial = contactAtEndpoint(document, command.wire.from)?.materialBinding ??
        contactAtEndpoint(document, command.wire.to)?.materialBinding;
      const newWire = command.wire.materialBinding || !selectedMaterial
        ? command.wire : { ...command.wire, materialBinding: selectedMaterial };
      let added = addWireToEndpointJunctions(
        { ...document, wires: [...document.wires, newWire] }, newWire);
      if (command.targetWireId !== undefined) {
        const junctionEndpoint = [command.wire.from, command.wire.to].find(isJunctionEndpoint);
        if (!junctionEndpoint) throw new Error("Новая ветвь должна завершаться в узле целевого провода.");
        added = addTargetWireToJunction(added, junctionEndpoint.junctionId, command.targetWireId);
      }
      let addedAndRouted: HarnessDesignDocument;
      try { addedAndRouted = rerouteWireE4(added, command.wire.id); }
      catch { addedAndRouted = rerouteE4WireBatch(added, added.wires.filter(w=>w.id===command.wire.id || w.e4RouteMode!=="manual").map(w=>w.id)); }
      validateWireGroups(addedAndRouted);
      const normalized = normalizeJunctionCircuits(addedAndRouted);
      const sourceEndpoint = [newWire.from, newWire.to].find(endpoint => contactAtEndpoint(normalized, endpoint)?.materialBinding)
        ?? [newWire.from, newWire.to].find(endpoint => contactAtEndpoint(normalized, endpoint)?.wire.trim());
      const linked = sourceEndpoint
        ? syncContactWireMaterial(normalized, sourceEndpoint.connectorId, sourceEndpoint.contactId, newWire.materialBinding)
        : normalized;
      const withMaterial = linked === normalized ? linked : rerouteE4WireBatch(linked, [newWire.id]);
      return command.wire.colorSource
        ? syncDirectWireColors(withMaterial, command.wire.colorSource.connectorId, command.wire.colorSource.contactId)
        : withMaterial;
    case "remove-wire": {
      const attachedWireIds = screenAttachmentWireIds(document, [command.wireId]);
      const changed = {
        ...document,
        wires: removeRequired(document.wires, command.wireId, "Провод не найден."),
      };
      const cleaned = cleanupWireReferences(changed, [command.wireId]);
      return rerouteE4WireBatch(cleaned, [
        ...cleaned.wires.filter((wire) => wire.e4RouteMode !== "manual").map((wire) => wire.id),
        ...attachedWireIds.filter((wireId) => cleaned.wires.some((wire) => wire.id === wireId)),
      ]);
    }
    case "update-wire": {
      const changed = {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => {
          const updated = {
            ...wire,
            circuit: command.circuit === undefined ? wire.circuit : command.circuit.trim(),
            color: command.color ?? wire.color,
            colorSource: command.color === undefined ? wire.colorSource : null,
            materialBinding: command.materialBinding === undefined
              ? wire.materialBinding : command.materialBinding ?? undefined,
            lengthMm: command.lengthMm === undefined ? wire.lengthMm : command.lengthMm,
            endCorrectionFromMm: command.endCorrectionFromMm ?? wire.endCorrectionFromMm,
            endCorrectionToMm: command.endCorrectionToMm ?? wire.endCorrectionToMm,
            cutRoundingStepMm: command.cutRoundingStepMm ?? wire.cutRoundingStepMm,
          };
          calculateWireCutLength(updated);
          return updated;
        }, "Провод не найден."),
      };
      if (command.materialBinding === null) {
        const wire = changed.wires.find(item => item.id === command.wireId)!;
        return normalizeJunctionCircuits({ ...changed, connectors: changed.connectors.map(connector => ({
          ...connector, contacts: connector.contacts.map(contact => [wire.from, wire.to].some(endpoint =>
            sameConnectorEndpoint(endpoint, connector.id, contact.id))
            ? { ...contact, materialBinding: undefined } : contact),
        })) });
      }
      return normalizeJunctionCircuits(changed);
    }
    case "set-wire-strip-profile":
      return {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => {
          const profile = command.profile === null ? undefined : normalizeWireStripProfileBinding(command.profile);
          const stripProfiles = { ...wire.stripProfiles, [command.end]: profile };
          if (stripProfiles.from === undefined && stripProfiles.to === undefined) {
            const { stripProfiles: _removed, ...withoutProfiles } = wire;
            return withoutProfiles;
          }
          return { ...wire, stripProfiles: Object.freeze(stripProfiles) };
        }, "Провод не найден."),
      };
    case "set-wire-drawing-end-style":
      if (!wireBlankEnds.includes(command.style)) throw new Error("Режим отображения конца провода задан неверно.");
      return {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => {
          assertEditableFreeDrawingEnd(document, wire, command.end);
          return ({
          ...wire,
          drawingEndStyles: {
            from: wire.drawingEndStyles?.from ?? "cut",
            to: wire.drawingEndStyles?.to ?? "cut",
            [command.end]: command.style,
          },
        }); }, "Провод не найден."),
      };
    case "set-wire-drawing-end-styles": {
      if (!wireBlankEnds.includes(command.style)) throw new Error("Режим отображения конца провода задан неверно.");
      const targets = document.wires.filter(wire => command.wireIds.includes(wire.id));
      if (targets.length !== new Set(command.wireIds).size) throw new Error("Один из выбранных проводов не найден.");
      const editable = targets.filter(wire => isFreeDrawingEnd(document, wire, command.end));
      assertNoLockedDrawingWires(document, editable);
      const ids = new Set(editable.map(wire => wire.id));
      return { ...document, wires: document.wires.map(wire => ids.has(wire.id) ? ({ ...wire, drawingEndStyles: { from: wire.drawingEndStyles?.from ?? "cut", to: wire.drawingEndStyles?.to ?? "cut", [command.end]: command.style } }) : wire) };
    }
    case "set-wire-drawing-endpoints-x": {
      if (!Number.isFinite(command.x)) throw new Error("Координата X должна быть конечным числом.");
      const targets = document.wires.filter(wire => command.wireIds.includes(wire.id) && isFreeDrawingEnd(document, wire, command.end));
      assertNoLockedDrawingWires(document, targets);
      return { ...document, wires: document.wires.map(wire => {
        const endpoints = wire.drawingEndpoints;
        if (!targets.includes(wire) || !endpoints) return wire;
        const current = endpoints[command.end];
        return { ...wire, drawingEndpoints: { ...endpoints, [command.end]: { x: command.x, y: current.y } } };
      }) };
    }
    case "set-wire-drawing-endpoint":
      if (!Number.isFinite(command.position.x) || !Number.isFinite(command.position.y)) throw new Error("Координаты конца должны быть конечными числами.");
      return { ...document, wires: replaceRequired(document.wires, command.wireId, wire => {
        assertEditableFreeDrawingEnd(document, wire, command.end);
        const endpoints = wire.drawingEndpoints;
        if (!endpoints) throw new Error("Свободный конец доступен только у независимого провода.");
        return { ...wire, drawingEndpoints: { from: endpoints.from, to: endpoints.to, [command.end]: command.position } };
      }, "Провод не найден.") };
    case "set-e4-wire-label-position": {
      const changed = {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => ({
          ...wire,
          e4LabelPosition: normalizeE4WireLabelPosition(command.position),
        }), "Провод не найден."),
      };
      // Moving an annotation must not move the wire that owns it. Automatic
      // neighboring wires may reroute around the label's new rectangle.
      return reflowWiresAroundLabel(changed, command.wireId);
    }
    case "reconnect-wire": {
      requireEndpoint(document, command.endpoint);
      const original = findWire(document, command.wireId);
      const changed = {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => {
          const updated = { ...wire, [command.end]: { ...command.endpoint } };
          validateWire(document, updated);
          return updated;
        }, "Провод не найден."),
      };
      const membership = updateJunctionMembership(changed, command.wireId, original[command.end], command.endpoint);
      const routed = rerouteWireE4(membership, command.wireId);
      validateWireGroups(routed);
      return normalizeJunctionCircuits(routed);
    }
    case "set-wire-route":
      return {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => ({
          ...wire,
          drawingRoute: command.route.map((point) => ({ ...point })),
        }), "Провод не найден."),
      };
    case "set-e4-wire-route": {
      const wire=findWire(document,command.wireId);
      if(screenJunctionEnds(wire)&&command.route.length>0)throw new Error("Подключение экрана к проводу должно быть прямым, без изгибов.");
      const start=wireEndpointE4Anchor(document,wire.from),end=wireEndpointE4Anchor(document,wire.to);
      if(!start||!end)throw new Error("Точки подключения маршрута Э4 не найдены.");
      requireE4ModeGeometry(document,[start.position,...command.route,end.position]);
      return setE4WireRoute(document, command.wireId, command.route);
    }
    case "move-e4-wire-route-point": {
      const wire = findWire(document, command.wireId);
      if (!Number.isSafeInteger(command.pointIndex) || command.pointIndex < 0 || command.pointIndex >= wire.e4Route.length) {
        throw new Error("Точка маршрута Э4 не найдена.");
      }
      return setE4WireRoute(document, command.wireId, wire.e4Route.map((point, index) =>
        index === command.pointIndex ? { ...command.position } : point));
    }
    case "remove-e4-wire-route-point":
      return removeE4WireRoutePoint(document, command.wireId, command.pointIndex);
    case "reroute-e4-wires": {
      requireWireIds(document, command.wireIds);
      const selected = new Set(command.wireIds);
      const changed = { ...document, wires: document.wires.map(wire => selected.has(wire.id)
        ? { ...wire, e4RouteMode: "auto" as const } : wire) };
      return rerouteE4WireBatch(changed, changed.wires.filter(wire => wire.e4RouteMode !== "manual").map(wire => wire.id));
    }
    case "move-e4-wire-segment": {
      const wire = findWire(document, command.wireId);
      if (!Number.isSafeInteger(command.segmentIndex) || command.segmentIndex < 1 || command.segmentIndex >= wire.e4Route.length) {
        throw new Error("Перемещать можно только внутренний сегмент маршрута Э4.");
      }
      const first = wire.e4Route[command.segmentIndex - 1]!;
      const second = wire.e4Route[command.segmentIndex]!;
      const route = wire.e4Route.map((point) => ({ ...point }));
      if (first.x === second.x) {
        route[command.segmentIndex - 1] = { x: command.position.x, y: first.y };
        route[command.segmentIndex] = { x: command.position.x, y: second.y };
      } else if (first.y === second.y) {
        route[command.segmentIndex - 1] = { x: first.x, y: command.position.y };
        route[command.segmentIndex] = { x: second.x, y: command.position.y };
      } else {
        throw new Error("Сегмент маршрута Э4 должен быть ортогональным.");
      }
      const movedJunctionIds = new Set(document.junctions
        .filter((junction) => junction.wireIds.includes(command.wireId) && pointOnOrthogonalSegment(junction.position, first, second))
        .map((junction) => junction.id));
      let changed: HarnessDesignDocument = {
        ...document,
        wires: document.wires.map((item) => item.id === command.wireId
          ? { ...item, e4Route: route, e4RouteMode: "manual" as const }
          : item),
        junctions: document.junctions.map((junction) => !movedJunctionIds.has(junction.id)
          ? junction
          : first.x === second.x
            ? { ...junction, position: { ...junction.position, x: command.position.x } }
            : { ...junction, position: { ...junction.position, y: command.position.y } }),
      };
      const start = wireEndpointE4Anchor(changed, wire.from);
      const end = wireEndpointE4Anchor(changed, wire.to);
      if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
      validateE4Polyline(start, route, end);
      const otherWireIds = new Set([
        ...changed.junctions
          .filter((junction) => movedJunctionIds.has(junction.id))
          .flatMap((junction) => junction.wireIds)
          .filter((wireId) => wireId !== command.wireId),
        ...screenAttachmentWireIds(changed, [command.wireId]),
      ]);
      // A manual segment drag owns only the selected route.  Automatic
      // neighbours are not an implicit dependency: moving one wire must not
      // silently rebuild an unrelated differential pair or the rest of the
      // scheme.  Junction and screen attachments remain explicit dependencies
      // and are handled through `otherWireIds` above.
      const excludedWireIds = command.detached
        ? new Set(changed.wires.map(item => item.id))
        : new Set([command.wireId, ...otherWireIds]);
      validateE4Route([start.position, ...route, end.position], createE4RoutingRequest(
        changed, wire, command.wireId, undefined, undefined, excludedWireIds,
      ));
      const pendingWireIds = [...otherWireIds];
      for (const wireId of pendingWireIds) {
        otherWireIds.delete(wireId);
        changed = rerouteWireE4ThroughJunctions(changed, wireId, otherWireIds);
      }
      for (const junction of changed.junctions.filter((item) => movedJunctionIds.has(item.id))) {
        validateJunctionAgainstWires(changed, junction);
      }
      validateWireGroups(changed);
      if (!command.detached) {
        // Validate only the selected route and explicit shared attachments.
        // Independent automatic routes deliberately retain their geometry.
        validateE4WireIds(changed, [...new Set([command.wireId, ...otherWireIds])]);
      }
      return changed;
    }
    case "set-wire-crossing-style":
      if (command.style !== "none" && command.style !== "bridge") throw new Error("Режим пересечения проводов задан неверно.");
      return {
        ...document,
        views: { ...document.views, [command.view]: { ...document.views[command.view], wireCrossingStyle: command.style } },
      };
    case "set-e4-routing-mode": {
      const changed = {
        ...document,
        views: { ...document.views, e4: { ...document.views.e4, e4RoutingMode: command.mode } },
      };
      if (command.mode === "orthogonal") return changed;
      return rerouteE4WireBatch(changed, changed.wires.filter(wire => wire.e4RouteMode !== "manual").map(wire => wire.id));
    }
    case "create-junction": {
      if (document.junctions.some((item) => item.id === command.junction.id)) throw new Error("Узел соединения с таким ID уже существует.");
      let changed = document;
      if (command.branchWire) {
        const endpointMatches = [command.branchWire.from, command.branchWire.to].some((endpoint) =>
          isJunctionEndpoint(endpoint) && endpoint.junctionId === command.junction.id);
        if (!endpointMatches) throw new Error("Ветвь должна завершаться в создаваемом узле соединения.");
        validateWire(document, command.branchWire, command.junction.id);
        if (document.wires.some((wire) => wire.id === command.branchWire!.id)) throw new Error("Провод с таким ID уже существует.");
        changed = { ...document, wires: [...document.wires, command.branchWire] };
      }
      const junction = normalizeJunction(command.junction);
      if (command.branchWire && !junction.wireIds.includes(command.branchWire.id)) {
        throw new Error("Создаваемый узел должен содержать ID новой ветви.");
      }
      validateJunctionAgainstWires(changed, junction);
      const result = { ...changed, junctions: [...changed.junctions, junction] };
      return normalizeJunctionCircuits(command.branchWire ? rerouteWireE4(result, command.branchWire.id) : result);
    }
    case "move-junction": {
      const junction = requireJunction(document, command.junctionId);
      let changed: HarnessDesignDocument = { ...document, junctions: document.junctions.map((item) =>
        item.id === junction.id ? { ...item, position: { ...command.position } } : item) };
      // A moved junction changes the routes and therefore the positions of
      // their labels. Reflow every automatic route in the same transaction so
      // a newly positioned label cannot be left over an unrelated conductor.
      changed = rerouteE4WireBatch(changed, changed.wires
        .filter((wire) => wire.e4RouteMode !== "manual" || junction.wireIds.includes(wire.id))
        .map((wire) => wire.id));
      validateJunctionAgainstWires(changed, changed.junctions.find((item) => item.id === junction.id)!);
      validateWireGroups(changed);
      validateAllE4Wires(changed);
      return changed;
    }
    case "remove-junction": {
      const junction = requireJunction(document, command.junctionId);
      if (document.wires.some((wire) => [wire.from, wire.to].some((endpoint) =>
        isJunctionEndpoint(endpoint) && endpoint.junctionId === junction.id))) {
        throw new Error("Нельзя удалить узел соединения, в котором завершается ветвь.");
      }
      return { ...document, junctions: document.junctions.filter((item) => item.id !== junction.id) };
    }
    case "connect-wire-to-junction": {
      const existingJunction = requireJunction(document, command.junctionId);
      const original = findWire(document, command.wireId);
      const changed = {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => ({
          ...wire,
          [command.end]: createJunctionEndpoint(command.junctionId),
        }), "Провод не найден."),
      };
      const result = updateJunctionMembership(changed, command.wireId, original[command.end], createJunctionEndpoint(command.junctionId));
      validateJunctionAgainstWires(result, result.junctions.find((item) => item.id === existingJunction.id)!);
      const routed = rerouteWireE4(result, command.wireId);
      validateWireGroups(routed);
      return normalizeJunctionCircuits(routed);
    }
    case "connect-wire-to-wire": {
      if (command.wireId === command.targetWireId) throw new Error("Провод нельзя подключить к самому себе.");
      const target = findWire(document, command.targetWireId);
      const existingJunction = document.junctions.find((junction) => junction.id === command.junctionId);
      const junctionPosition = existingJunction?.position ?? command.position;
      if (existingJunction && !pointsMatch(existingJunction.position, command.position)) {
        throw new Error("Существующий узел находится в другой точке.");
      }
      if (!wireE4PathContainsPoint(document, target, junctionPosition)) throw new Error("Целевой провод не проходит через точку соединения.");
      const original = findWire(document, command.wireId);
      const changed = {
        ...document,
        wires: replaceRequired(document.wires, command.wireId, (wire) => ({
          ...wire,
          [command.end]: createJunctionEndpoint(command.junctionId),
        }), "Провод не найден."),
      };
      const junction = existingJunction
        ? normalizeJunction({ ...existingJunction, wireIds: [...new Set([...existingJunction.wireIds, command.targetWireId, command.wireId])] })
        : normalizeJunction({ id: command.junctionId, position: command.position, wireIds: [command.targetWireId, command.wireId] });
      const membership = updateJunctionMembership(changed, command.wireId, original[command.end], createJunctionEndpoint(command.junctionId));
      const result = { ...membership, junctions: existingJunction
        ? membership.junctions.map((item) => item.id === junction.id ? junction : item)
        : [...membership.junctions, junction] };
      validateJunctionAgainstWires(result, junction);
      const routed = rerouteWireE4(result, command.wireId);
      validateWireGroups(routed);
      return normalizeJunctionCircuits(routed);
    }
    case "edit-detached-wire-bend": {
      return { ...document, wires: replaceRequired(document.wires, command.wireId, wire => {
        assertNoLockedDrawingWires(document, [wire]);
        return { ...wire, drawingRoute: editedDetachedWireBends(wire, command.index, command.position, command.insert) };
      }, "Провод не найден.") };
    }
    case "remove-detached-wire-bend": {
      return { ...document, wires: replaceRequired(document.wires, command.wireId, wire => {
        assertNoLockedDrawingWires(document, [wire]);
        if (!wire.drawingEndpoints || !Number.isInteger(command.index) || command.index < 0 || command.index >= wire.drawingRoute.length) throw new Error("Изгиб независимого провода не найден.");
        return { ...wire, drawingRoute: wire.drawingRoute.filter((_, index) => index !== command.index) };
      }, "Провод не найден.") };
    }
    case "set-detached-wire-pair": {
      if (command.wireIds.length !== 2 || new Set(command.wireIds).size !== 2) throw new Error("Выберите два разных независимых провода.");
      requireWireIds(document, command.wireIds);
      const wires = document.wires.filter(wire => command.wireIds.includes(wire.id));
      if (wires.some(wire => !wire.drawingEndpoints)) throw new Error("Скрутка фрагмента доступна только для независимых проводов.");
      assertNoLockedDrawingWires(document, wires);
      const existing = document.diffPairs.find(group => command.wireIds.every(id => group.wireIds.includes(id)));
      if (command.action === "straighten") {
        if (!existing) return document;
        return { ...document, diffPairs: document.diffPairs.filter(group => group.id !== existing.id) };
      }
      if (document.diffPairs.some(group => group.id !== existing?.id && group.wireIds.some(id => command.wireIds.includes(id)))) throw new Error("Провод уже входит в другую дифференциальную пару.");
      const group: DiffPairGroup = {
        id: existing?.id ?? command.groupId ?? crypto.randomUUID(),
        wireIds: existing?.wireIds ?? [command.wireIds[0]!, command.wireIds[1]!],
        step: command.step ?? existing?.step ?? 40,
        amplitude: command.amplitude ?? existing?.amplitude ?? 6,
        variant: command.variant ?? existing?.variant ?? 1,
      };
      if (!group.id.trim() || group.id.length > 128 || document.diffPairs.some(candidate => candidate.id === group.id && candidate !== existing)) throw new Error("ID дифференциальной пары задан неверно.");
      requirePositiveParameter(group.step, "Шаг дифференциальной пары");
      requirePositiveParameter(group.amplitude, "Амплитуда дифференциальной пары");
      if (group.variant !== 1 && group.variant !== 2) throw new Error("Вид дифференциальной пары задан неверно.");
      const display = freeTwistedPairPaths(group, wires.map(wire => ({ id: wire.id, points: [wire.drawingEndpoints!.from, ...wire.drawingRoute, wire.drawingEndpoints!.to], width: drawingWireWidth(document, wire) })), drawingPhysicalScale(document), true);
      if (display.size !== 2) throw new Error("Для скрутки нужны два направленных вдоль друг друга провода достаточной длины. Согласуйте их изгибы или уменьшите шаг скрутки.");
      return { ...document, diffPairs: existing ? document.diffPairs.map(candidate => candidate.id === existing.id ? group : candidate) : [...document.diffPairs, group] };
    }
    case "create-diff-pair": {
      const group = normalizeDiffPair(document, command.group);
      if (document.diffPairs.some((item) => item.id === group.id)) throw new Error("Дифференциальная пара с таким ID уже существует.");
      if (document.diffPairs.some((item) => item.wireIds.some((wireId) => group.wireIds.includes(wireId)))) {
        throw new Error("Провод уже входит в другую дифференциальную пару.");
      }
      return compactAutomaticDifferentialPairs(
        { ...document, diffPairs: [...document.diffPairs, group] },
        group.wireIds,
      );
    }
    case "update-diff-pair":
      const changed = {
        ...document,
        diffPairs: replaceRequired(document.diffPairs, command.groupId, (group) => normalizeDiffPair(document, {
          ...group,
          variant: command.variant ?? group.variant,
          step: command.step ?? group.step,
          amplitude: command.amplitude ?? group.amplitude,
        }), "Дифференциальная пара не найдена."),
      };
      const group = changed.diffPairs.find(item => item.id === command.groupId);
      return group ? compactAutomaticDifferentialPairs(changed, group.wireIds) : changed;
    case "remove-diff-pair":
      return { ...document, diffPairs: removeRequired(document.diffPairs, command.groupId, "Дифференциальная пара не найдена.") };
    case "create-screen": {
      const screen = normalizeScreen(document, command.screen);
      if (document.screens.some((item) => item.id === screen.id)) throw new Error("Экран с таким ID уже существует.");
      return { ...document, screens: [...document.screens, screen] };
    }
    case "update-screen": {
      const changed = {
        ...document,
        screens: replaceRequired(document.screens, command.screenId, (screen) => normalizeScreen(document, {
          ...screen,
          wireIds: command.wireIds ?? screen.wireIds,
          position: command.position ?? screen.position,
          label: command.label ?? screen.label,
          width: command.width ?? screen.width,
          terminalSide: command.terminalSide ?? screen.terminalSide,
        }), "Экран не найден."),
      };
      const attachedWires = changed.wires.filter((wire) => [wire.from, wire.to].some((endpoint) =>
        isScreenEndpoint(endpoint) && endpoint.screenId === command.screenId));
      if (attachedWires.some((wire) => [wire.from, wire.to].some((endpoint) =>
        isScreenEndpoint(endpoint) && endpoint.screenId === command.screenId && !wireEndpointE4Anchor(changed, endpoint)))) {
        throw new Error("Выбранная сторона экрана уже используется. Сначала переподключите или удалите провод.");
      }
      const attachedWireIds = attachedWires.map((wire) => wire.id);
      return rerouteE4WireBatch(changed, attachedWireIds);
    }
    case "remove-screen": {
      const attachedWireIds = document.wires.filter((wire) => [wire.from, wire.to].some((endpoint) =>
        isScreenEndpoint(endpoint) && endpoint.screenId === command.screenId)).map((wire) => wire.id);
      const changed = {
        ...document,
        screens: removeRequired(document.screens, command.screenId, "Экран не найден."),
        wires: document.wires.filter((wire) => !attachedWireIds.includes(wire.id)),
      };
      return cleanupWireReferences(changed, attachedWireIds);
    }
    case "update-layer":
      return updateLayers(document, command.view, (layers) =>
        replaceRequired(layers, command.layerId, (layer) => ({
          ...layer,
          visible: command.visible ?? layer.visible,
          locked: command.locked ?? layer.locked,
        }), "Слой не найден."));
    case "replace-layers":
      validateLayers(command.layers);
      return updateLayers(document, command.view, () => command.layers.map((layer) => ({ ...layer })));
    case "move-layer":
      return updateLayers(document, command.view, (layers) => moveLayer(layers, command.layerId, command.direction));
  }
}

function syncDirectWireColors(
  document: HarnessDesignDocument,
  connectorId: string,
  contactId: string,
): HarnessDesignDocument {
  const contact = document.connectors.find((connector) => connector.id === connectorId)
    ?.contacts.find((candidate) => candidate.id === contactId);
  if (!contact) return document;
  const color = resolveWireColorHex(contact.color);
  let changed = false;
  let connectors = document.connectors;
  const wires = document.wires.map((wire) => {
    if (wire.colorSource === null || isJunctionEndpoint(wire.from) || isScreenEndpoint(wire.from) ||
        isJunctionEndpoint(wire.to) || isScreenEndpoint(wire.to) ||
        ![wire.from, wire.to].some((endpoint) => isConnectorEndpoint(endpoint, connectorId, contactId))) return wire;
    const opposite = sameConnectorEndpoint(wire.from, connectorId, contactId) ? wire.to : wire.from;
    const oppositeContact = contactAtEndpoint({ ...document, connectors }, opposite);
    if (oppositeContact && contactColorIsAutomatic(oppositeContact)) {
      connectors = updateContactColor(connectors, opposite, contact.color, contact.secondaryColor ?? "", "auto");
    }
    changed = true;
    return { ...wire, color, colorSource: { connectorId, contactId } };
  });
  return changed ? { ...document, connectors, wires } : document;
}

/** Keep the two contact rows and their direct conductor on one material. */
function syncContactWireMaterial(
  document: HarnessDesignDocument,
  connectorId: string,
  contactId: string,
  materialOverride?: WireMaterialBinding,
): HarnessDesignDocument {
  const source = contactAtEndpoint(document, { connectorId, contactId });
  if (!source) return document;
  if (!document.wires.some(wire => [wire.from, wire.to].some(endpoint =>
    sameConnectorEndpoint(endpoint, connectorId, contactId)))) return document;
  const material = materialOverride ?? source.materialBinding;
  let connectors = document.connectors;
  const wires = document.wires.map(wire => {
    if (![wire.from, wire.to].some(endpoint => sameConnectorEndpoint(endpoint, connectorId, contactId))) return wire;
    const opposite = sameConnectorEndpoint(wire.from, connectorId, contactId) ? wire.to : wire.from;
    if (!isJunctionEndpoint(opposite) && !isScreenEndpoint(opposite)) {
      connectors = connectors.map(connector => connector.id !== opposite.connectorId ? connector : {
        ...connector,
        contacts: connector.contacts.map(contact => contact.id !== opposite.contactId ? contact : {
          ...contact, wire: source.wire, wireSection: source.wireSection,
          wireDiameterMm: source.wireDiameterMm, materialBinding: material,
        }),
      });
    }
    return { ...wire, materialBinding: material };
  });
  return { ...document, connectors, wires };
}

function resetContactColorToAutomatic(
  document: HarnessDesignDocument,
  connectorId: string,
  contactId: string,
): HarnessDesignDocument {
  const endpoint = { connectorId, contactId } as const;
  if (!contactAtEndpoint(document, endpoint)) throw new Error("Контакт не найден.");
  const directWire = document.wires.find((wire) =>
    !isJunctionEndpoint(wire.from) && !isScreenEndpoint(wire.from) &&
    !isJunctionEndpoint(wire.to) && !isScreenEndpoint(wire.to) &&
    (sameConnectorEndpoint(wire.from, connectorId, contactId) || sameConnectorEndpoint(wire.to, connectorId, contactId)));
  if (!directWire) {
    return { ...document, connectors: updateContactColor(document.connectors, endpoint, "", "", "auto") };
  }
  const opposite = sameConnectorEndpoint(directWire.from, connectorId, contactId) ? directWire.to : directWire.from;
  const source = contactAtEndpoint(document, opposite);
  if (!source || isJunctionEndpoint(opposite) || isScreenEndpoint(opposite)) {
    return { ...document, connectors: updateContactColor(document.connectors, endpoint, "", "", "auto") };
  }
  return {
    ...document,
    connectors: updateContactColor(document.connectors, endpoint, source.color, source.secondaryColor ?? "", "auto"),
    wires: directWire.colorSource === null ? document.wires : document.wires.map((wire) => wire.id === directWire.id ? {
      ...wire,
      color: resolveWireColorHex(source.color),
      colorSource: { connectorId: opposite.connectorId, contactId: opposite.contactId },
    } : wire),
  };
}

function contactColorIsAutomatic(contact: ConnectorContact): boolean {
  return contact.colorMode === "auto" ||
    contact.colorMode === undefined && !contact.color.trim() && !(contact.secondaryColor ?? "").trim();
}

function contactAtEndpoint(document: HarnessDesignDocument, endpoint: WireEndpoint): ConnectorContact | null {
  if (isJunctionEndpoint(endpoint) || isScreenEndpoint(endpoint)) return null;
  return document.connectors.find((connector) => connector.id === endpoint.connectorId)
    ?.contacts.find((contact) => contact.id === endpoint.contactId) ?? null;
}

function sameConnectorEndpoint(endpoint: WireEndpoint, connectorId: string, contactId: string): boolean {
  return !isJunctionEndpoint(endpoint) && !isScreenEndpoint(endpoint) &&
    endpoint.connectorId === connectorId && endpoint.contactId === contactId;
}

function updateContactColor(
  connectors: readonly ConnectorInstance[],
  endpoint: WireEndpoint,
  color: string,
  secondaryColor: string,
  colorMode: "auto" | "manual",
): readonly ConnectorInstance[] {
  if (isJunctionEndpoint(endpoint) || isScreenEndpoint(endpoint)) return connectors;
  return connectors.map((connector) => connector.id !== endpoint.connectorId ? connector : {
    ...connector,
    contacts: connector.contacts.map((contact) => contact.id !== endpoint.contactId ? contact : {
      ...contact, color, secondaryColor, colorMode,
    }),
  });
}

function rememberCustomWireColors(
  document: HarnessDesignDocument,
  values: readonly string[],
): HarnessDesignDocument {
  const colors = [...new Set([
    ...(document.customWireColors ?? []),
    ...values.flatMap((value) => {
      const normalized = value.trim().toUpperCase();
      return /^#[0-9A-F]{6}$/.test(normalized) ? [normalized] : [];
    }),
  ])];
  return colors.length === (document.customWireColors?.length ?? 0) &&
    colors.every((value, index) => value === document.customWireColors?.[index])
    ? document
    : { ...document, customWireColors: colors };
}

/** Repairs or upgrades loaded E4 geometry before it is shown or saved. */
export function normalizeE4RoutingDocument(document: HarnessDesignDocument): HarnessDesignDocument {
  return rebuildConnectorE4Wires(document);
}

function validateLayers(layers: readonly EditorLayer[]): void {
  if (layers.length < 1 || new Set(layers.map((layer) => layer.id)).size !== layers.length ||
      new Set(layers.map((layer) => layer.order)).size !== layers.length) {
    throw new Error("Слои должны иметь уникальные ID и порядок.");
  }
}

function updateConnector(
  document: HarnessDesignDocument,
  connectorId: string,
  update: (connector: ConnectorInstance) => ConnectorInstance,
): HarnessDesignDocument {
  return {
    ...document,
    connectors: replaceRequired(document.connectors, connectorId, update, "Соединитель не найден."),
  };
}

function updateConnectorE4Geometry(
  document: HarnessDesignDocument,
  connectorId: string,
  update: (connector: ConnectorInstance) => ConnectorInstance,
): HarnessDesignDocument {
  const before = document.connectors.find((connector) => connector.id === connectorId);
  const changed = updateConnector(document, connectorId, connector => {
    const next = update(connector);
    // Article changes/removal can retire contacts; new rows keep their default order.
    return next.schematic.rowOrder ? { ...next, schematic: { ...next.schematic,
      rowOrder: next.schematic.rowOrder.filter(id => next.contacts.some(contact => contact.id === id)),
    } } : next;
  });
  const after = changed.connectors.find((connector) => connector.id === connectorId);
  if (!before || !after) throw new Error("Соединитель не найден.");
  const oldGeometry = connectorE4TableGeometry(before);
  const newGeometry = connectorE4TableGeometry(after);
  const changedAnchors = before.schematic.orientation !== after.schematic.orientation ||
    oldGeometry.width !== newGeometry.width || oldGeometry.height !== newGeometry.height ||
    before.contacts.some((contact) => {
      const oldPoint = oldGeometry.contactPoints[contact.id];
      const newPoint = newGeometry.contactPoints[contact.id];
      return !oldPoint || !newPoint || oldPoint.x !== newPoint.x || oldPoint.y !== newPoint.y;
    });
  return changedAnchors ? rebuildConnectorE4Wires(changed, connectorId, before) : changed;
}

/** Preserve existing traces where possible, with structurally valid endpoints
 * so a temporary collision can be saved, reopened and repaired by another drag. */
function retainEditableE4Routes(document: HarnessDesignDocument): HarnessDesignDocument {
  let changed = document;
  for (const wire of document.wires) {
    if (screenJunctionEnds(wire)) {
      changed = routeStraightScreenBranch(changed, wire);
      continue;
    }
    const start = wireEndpointE4Anchor(changed, wire.from);
    const end = wireEndpointE4Anchor(changed, wire.to);
    if (!start || !end) continue;
    try {
      validateE4Polyline(start, wire.e4Route, end);
    } catch {
      const junctions = changed.junctions.filter((junction) => junction.wireIds.includes(wire.id) &&
        ![wire.from, wire.to].some((endpoint) => isJunctionEndpoint(endpoint) && endpoint.junctionId === junction.id));
      const route = routeE4WireThroughWaypoints({ start, end }, junctions.map((junction) => junction.position));
      changed = { ...changed, wires: changed.wires.map((item) => item.id === wire.id
        ? { ...item, e4Route: route.intermediate } : item) };
    }
  }
  return changed;
}

/** Collision diagnostics do not reject edits to electrical data or placement. */
export function e4RoutingIssues(document: HarnessDesignDocument): readonly { wireId: string; message: string }[] {
  return document.wires.flatMap((wire) => {
    try {
      validateE4WireIds(document, [wire.id]);
      return [];
    } catch (error) {
      return [{ wireId: wire.id, message: error instanceof Error ? error.message : "Маршрут требует перестроения." }];
    }
  });
}

function rebuildConnectorE4Wires(
  document: HarnessDesignDocument,
  changedConnectorId?: string,
  previousConnector?: ConnectorInstance,
): HarnessDesignDocument {
  // Keep manually arranged routes when they remain valid. A route becomes an
  // affected route when its contact anchor moved or a changed/new table now
  // covers it. Completed reroutes reserve their clearance immediately.
  let working = document;
  const preservedManualWireIds = new Set<string>();
  if (changedConnectorId !== undefined && previousConnector !== undefined) {
    for (const wire of working.wires) {
      if (wire.e4RouteMode !== "manual" ||
          (!isConnectorEndpoint(wire.from, changedConnectorId) && !isConnectorEndpoint(wire.to, changedConnectorId))) continue;
      try {
        const preserved = preserveManualE4WireAfterConnectorMove(working, wire.id);
        if (preserved !== null) {
          working = preserved;
          preservedManualWireIds.add(wire.id);
        }
      } catch {
        // The moved table made these guide points invalid. This wire falls
        // through to the ordinary obstacle-aware reroute below.
      }
    }
  }
  const currentConnector = changedConnectorId
    ? working.connectors.find((connector) => connector.id === changedConnectorId)
    : undefined;
  const affectedWireIds = working.wires.flatMap((wire) => {
    const isAutomatic = wire.e4RouteMode !== "manual";
    const connectedToChanged = changedConnectorId !== undefined &&
      (isConnectorEndpoint(wire.from, changedConnectorId) || isConnectorEndpoint(wire.to, changedConnectorId));
    const closeToChangedObstacle = isAutomatic && changedConnectorId !== undefined &&
      [previousConnector, currentConnector].some((connector) => connector !== undefined &&
        wireRouteTouchesConnector(working, wire, connector));
    if (connectedToChanged && preservedManualWireIds.has(wire.id)) return [];
    if (connectedToChanged || closeToChangedObstacle || isAutomatic) return [wire.id];
    const start = wireEndpointE4Anchor(working, wire.from);
    const end = wireEndpointE4Anchor(working, wire.to);
    if (!start || !end) return [wire.id];
    try {
      validateE4Route(
        [start.position, ...wire.e4Route, end.position],
        createE4RoutingRequest(working, wire, wire.id),
      );
      return [];
    } catch {
      return [wire.id];
    }
  });
  return rerouteE4WireBatch(working, [
    ...affectedWireIds,
    ...screenAttachmentWireIds(working, [...affectedWireIds, ...preservedManualWireIds]),
  ]);
}

function preserveManualE4WireAfterConnectorMove(
  document: HarnessDesignDocument,
  wireId: string,
): HarnessDesignDocument | null {
  const wire = findWire(document, wireId);
  // End points in e4Route are generated contact leads. The points between
  // them are the bends and offsets positioned by the operator.
  const guidePoints = wire.e4Route.slice(1, -1);
  if (guidePoints.length === 0) return null;
  const request = createE4RoutingRequest(document, wire, wireId);
  const routed = routeE4WireThroughWaypoints(request, guidePoints);
  const start = wireEndpointE4Anchor(document, wire.from);
  const end = wireEndpointE4Anchor(document, wire.to);
  if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
  validateE4Polyline(start, routed.intermediate, end);
  validateE4Route(routed.points, request);
  return {
    ...document,
    wires: document.wires.map((item) => item.id === wireId
      ? { ...item, e4Route: routed.intermediate, e4RouteMode: "manual" as const }
      : item),
  };
}

function screenAttachmentWireIds(document: HarnessDesignDocument, changedWireIds: readonly string[]): readonly string[] {
  const changed = new Set(changedWireIds);
  const screenIds = new Set(document.screens.filter((screen) =>
    screen.wireIds.some((wireId) => changed.has(wireId))).map((screen) => screen.id));
  return document.wires.flatMap((wire) => !changed.has(wire.id) && [wire.from, wire.to].some((endpoint) =>
    isScreenEndpoint(endpoint) && screenIds.has(endpoint.screenId)) ? [wire.id] : []);
}

function normalizeContact(
  contact: ConnectorContact,
  customFields: readonly ConnectorCustomField[],
): ConnectorContact {
  requireContactNumber(contact.number);
  requireContactStatus(contact.connectionStatus);
  if (!contact.id.trim() || contact.id.length > 1024) throw new Error("ID контакта задан неверно.");
  return {
    ...contact,
    id: contact.id.trim(),
    contactType: normalizeValue(contact.contactType, "Тип контакта"),
    circuit: normalizeValue(contact.circuit, "Цепь контакта"),
    terminalArticle: normalizeValue(contact.terminalArticle, "Артикул терминала"),
    wire: normalizeValue(contact.wire, "Провод контакта"),
    ...(contact.wireSection === undefined ? {} : { wireSection: normalizeValue(contact.wireSection, "Сечение провода") }),
    ...(contact.materialBinding === undefined ? {} : { materialBinding: contact.materialBinding }),
    color: normalizeValue(contact.color, "Цвет провода контакта"),
    secondaryColor: normalizeValue(contact.secondaryColor ?? "", "Второй цвет провода контакта"),
    colorMode: contact.colorMode === "auto" || contact.colorMode === "manual"
      ? contact.colorMode
      : contact.color.trim() || (contact.secondaryColor ?? "").trim() ? "manual" : "auto",
    customValues: normalizeCustomValues(contact.customValues, customFields),
  };
}

function normalizeCustomField(field: ConnectorCustomField): ConnectorCustomField {
  const id = field.id.trim();
  const label = field.label.trim();
  if (!id || id.length > 1024) throw new Error("ID справочного поля задан неверно.");
  if (!label || label.length > 120) throw new Error("Название справочного поля задано неверно.");
  if (typeof field.visible !== "boolean") throw new Error("Видимость справочного поля задана неверно.");
  return { id, label, visible: field.visible };
}

function normalizeCustomValues(
  values: Readonly<Record<string, string>>,
  customFields: readonly ConnectorCustomField[],
): Readonly<Record<string, string>> {
  const knownIds = new Set(customFields.map((field) => field.id));
  const result: Record<string, string> = {};
  for (const [fieldId, value] of Object.entries(values)) {
    if (!knownIds.has(fieldId)) throw new Error("Справочное поле контакта не найдено.");
    result[fieldId] = normalizeValue(value, "Значение справочного поля");
  }
  return result;
}

function validateUniqueContacts(contacts: readonly ConnectorContact[]): void {
  if (new Set(contacts.map((contact) => contact.id)).size !== contacts.length ||
      new Set(contacts.map((contact) => contact.number)).size !== contacts.length) {
    throw new Error("Контакты соединителя должны иметь уникальные ID и номера.");
  }
}

function requireContactNumber(number: number): void {
  if (!Number.isSafeInteger(number) || number < 1 || number > 300) {
    throw new Error("Номер контакта должен быть от 1 до 300.");
  }
}

function requireContactStatus(status: ConnectorContactStatus): void {
  if (status !== "available" && status !== "not-connected") {
    throw new Error("Статус подключения контакта задан неверно.");
  }
}

function normalizeValue(value: string, name: string): string {
  if (typeof value !== "string" || value.length > 1024) throw new Error(`${name} задано неверно.`);
  return value.trim();
}

function requireConnectorText(value: string, emptyMessage: string): void {
  if (!value) throw new Error(emptyMessage);
  if (value.length > 120) throw new Error("Значение соединителя не должно быть длиннее 120 символов.");
}

function requirePartNumberText(value: string, emptyMessage: string): void {
  if (!value) throw new Error(emptyMessage);
  if (value.length > 512) throw new Error("Артикул соединителя не должен быть длиннее 512 символов.");
}

function normalizeTemplateTerminalArticle(
  connector: ConnectorInstance,
  contact: ConnectorContact,
  value: string,
): string {
  if (connector.libraryBinding?.mode !== "template" || !value) return value;
  const allowed = templateTerminalChoices(connector, contact.logicalContactId);
  if (!allowed.includes(value)) {
    throw new Error("Артикул терминала не входит в список совместимых терминалов закреплённого шаблона.");
  }
  return value;
}

function isConnectorEndpoint(endpoint: WireEndpoint, connectorId: string, contactId?: string): boolean {
  return !isJunctionEndpoint(endpoint) && !isScreenEndpoint(endpoint) && endpoint.connectorId === connectorId &&
    (contactId === undefined || endpoint.contactId === contactId);
}

function findWire(document: HarnessDesignDocument, wireId: string): WireInstance {
  const wire = document.wires.find((item) => item.id === wireId);
  if (!wire) throw new Error("Провод не найден.");
  return wire;
}

function setE4WireRoute(document: HarnessDesignDocument, wireId: string, route: readonly Point[]): HarnessDesignDocument {
  const wire = findWire(document, wireId);
  if (screenJunctionEnds(wire) && route.length > 0) {
    throw new Error("Подключение экрана к проводу должно быть прямым, без изгибов.");
  }
  const start = wireEndpointE4Anchor(document, wire.from);
  const end = wireEndpointE4Anchor(document, wire.to);
  if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
  const copy = route.map(point=>({...point}));
  validateE4Polyline(start, copy, end);
  // A manually moved wire owns the space first. Rebuild automatic neighbours
  // around it so two wires do not deadlock each other as mutual obstacles.
  const changed: HarnessDesignDocument = { ...document, wires: document.wires.map((item) => item.id === wireId
    ? { ...item, e4Route: copy, e4RouteMode: "manual" as const }
    : item) };
  const automaticNeighbours = [...new Set([...changed.wires
    .filter((item) => item.id !== wireId && item.e4RouteMode !== "manual")
    .map((item) => item.id), ...screenAttachmentWireIds(changed, [wireId])])];
  let repaired = changed;
  if (automaticNeighbours.length > 0) {
    try {
      repaired = rerouteE4WireBatch(changed, automaticNeighbours);
    } catch {
      // Keep the explicit manual move visible; the next automatic edit can
      // retry routing when more space becomes available.
    }
  }
  const repairedWire = findWire(repaired, wireId);
  const repairedStart = wireEndpointE4Anchor(repaired, repairedWire.from);
  const repairedEnd = wireEndpointE4Anchor(repaired, repairedWire.to);
  if (!repairedStart || !repairedEnd) throw new Error("Точки подключения маршрута Э4 не найдены.");
  try {
    validateE4Route([repairedStart.position, ...repairedWire.e4Route, repairedEnd.position],
      createE4RoutingRequest(repaired, repairedWire, wireId));
  } catch (error) {
    if (!(error instanceof Error) || !/зазор|пересеч|clearance/i.test(error.message)) throw error;
    const adjusted = nearestValidE4Route(document, wireId, copy);
    if (!adjusted) throw new Error("Рядом с выбранной позицией нет допустимого маршрута Э4 с нужным зазором.");
    repaired = adjusted;
  }
  for (const junction of repaired.junctions.filter((item) => item.wireIds.includes(wireId))) validateJunctionAgainstWires(repaired, junction);
  validateWireGroups(repaired);
  return repaired;
}

function removeE4WireRoutePoint(
  document: HarnessDesignDocument,
  wireId: string,
  pointIndex: number,
): HarnessDesignDocument {
  const wire = findWire(document, wireId);
  if (!Number.isSafeInteger(pointIndex) || pointIndex < 0 || pointIndex >= wire.e4Route.length) {
    throw new Error("Точка маршрута Э4 не найдена.");
  }
  const start = wireEndpointE4Anchor(document, wire.from);
  const end = wireEndpointE4Anchor(document, wire.to);
  if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
  const points = [start.position, ...wire.e4Route, end.position];
  const fullIndex = pointIndex + 1;
  const previous = points[fullIndex - 1]!;
  const selected = points[fullIndex]!;
  const next = points[fullIndex + 1]!;
  const reduced = [...points.slice(1, fullIndex), ...points.slice(fullIndex + 1, -1)];
  if (previous.x !== next.x && previous.y !== next.y) {
    const alternate = selected.x === next.x
      ? { x: previous.x, y: next.y }
      : { x: next.x, y: previous.y };
    const candidates: Point[][] = [];
    // Removing one bend changes the neighboring bend to the other corner of
    // the rectangle. This really removes a stored point instead of replacing
    // it with an indistinguishable copy at the same coordinates.
    if (pointIndex + 1 < wire.e4Route.length) {
      candidates.push(simplifyE4IntermediateRoute(start.position,
        reduced.map((point, index) => index === pointIndex ? alternate : point), end.position));
    }
    if (pointIndex > 0) {
      candidates.push(simplifyE4IntermediateRoute(start.position,
        reduced.map((point, index) => index === pointIndex - 1 ? alternate : point), end.position));
    }
    for (const candidate of candidates) {
      try {
        return setE4WireRoute(document, wireId, candidate);
      } catch {
        // Try adapting the bend on the other side before a full reroute.
      }
    }
    const retainedGuides = reduced.slice(1, -1).filter((_, index) =>
      index !== pointIndex - 1 && index !== pointIndex);
    const routed = routeE4WireThroughWaypoints(
      createE4RoutingRequest(document, wire, wireId),
      retainedGuides,
    );
    return setE4WireRoute(document, wireId, routed.intermediate);
  }
  return setE4WireRoute(document, wireId, reduced);
}

function simplifyE4IntermediateRoute(
  start: Point,
  intermediate: readonly Point[],
  end: Point,
): Point[] {
  const result: Point[] = [{ ...start }];
  for (const point of [...intermediate, end]) {
    const last = result.at(-1)!;
    if (last.x === point.x && last.y === point.y) continue;
    result.push({ ...point });
    while (result.length >= 3) {
      const first = result[result.length - 3]!;
      const middle = result[result.length - 2]!;
      const third = result[result.length - 1]!;
      if (!((first.x === middle.x && middle.x === third.x) ||
          (first.y === middle.y && middle.y === third.y))) break;
      result.splice(result.length - 2, 1);
    }
  }
  return result.slice(1, -1);
}

function rerouteWireE4(document: HarnessDesignDocument, wireId: string): HarnessDesignDocument {
  return rerouteWireE4ThroughJunctions(document, wireId);
}

function screenJunctionEnds(wire: WireInstance) {
  const screen = [wire.from, wire.to].find(isScreenEndpoint);
  const junction = [wire.from, wire.to].find(isJunctionEndpoint);
  return screen && junction ? { screen, junction } : null;
}

function routeStraightScreenBranch(document: HarnessDesignDocument, wire: WireInstance): HarnessDesignDocument {
  const ends = screenJunctionEnds(wire)!;
  const anchor = wireEndpointE4Anchor(document, ends.screen)!;
  const junction = requireJunction(document, ends.junction.junctionId);
  const targets = junction.wireIds.filter((id) => id !== wire.id);
  const candidates: Point[] = [];
  for (const targetId of targets) {
    const target = findWire(document, targetId);
    const start = wireEndpointE4Anchor(document, target.from)?.position;
    const end = wireEndpointE4Anchor(document, target.to)?.position;
    if (!start || !end) continue;
    const points = [start, ...target.e4Route, end];
    for (let index = 1; index < points.length; index += 1) {
      const a = points[index - 1]!;
      const b = points[index]!;
      const vertical = anchor.leadDirection === "up" || anchor.leadDirection === "down";
      const divisor=vertical?b.x-a.x:b.y-a.y;
      if(Math.abs(divisor)<1e-8)continue;
      const t=vertical?(anchor.position.x-a.x)/divisor:(anchor.position.y-a.y)/divisor;
      if(t<0||t>1)continue;
      const point=vertical?{x:anchor.position.x,y:a.y+(b.y-a.y)*t}:{x:a.x+(b.x-a.x)*t,y:anchor.position.y};
      const outward = anchor.leadDirection === "up" ? point.y < anchor.position.y
        : anchor.leadDirection === "down" ? point.y > anchor.position.y
          : anchor.leadDirection === "left" ? point.x < anchor.position.x : point.x > anchor.position.x;
      if (outward && targets.every((id) => wireE4PathContainsPoint(document, findWire(document, id), point))) candidates.push(point);
    }
  }
  candidates.sort((a, b) => Math.hypot(a.x - anchor.position.x, a.y - anchor.position.y) -
    Math.hypot(b.x - anchor.position.x, b.y - anchor.position.y));
  for (const point of candidates) {
    const changed = { ...document,
      junctions: document.junctions.map((item) => item.id === junction.id ? { ...item, position: point } : item),
      wires: document.wires.map((item) => item.id === wire.id ? { ...item, e4Route: [] } : item),
    };
    try {
      validateE4WireIds(changed, junction.wireIds);
      return changed;
    } catch { /* Try the next straight span on the same electrical target. */ }
  }
  throw new Error("Для подключения экрана нужен прямой участок до целевого провода без изгибов.");
}

function rerouteE4WireBatch(document: HarnessDesignDocument, wireIds: readonly string[]): HarnessDesignDocument {
  const baseOrder = [...new Set(wireIds)];
  if (baseOrder.length === 0) return document;
  const byRouteComplexity = [...baseOrder].sort((left, right) => {
    const difference = findWire(document, right).e4Route.length - findWire(document, left).e4Route.length;
    return difference || left.localeCompare(right);
  });
  const candidates = [baseOrder, [...baseOrder].reverse(), byRouteComplexity, [...byRouteComplexity].reverse()];
  // Exhaustive ordering is cheap for the common two-to-four-wire bundle.
  // Larger scenes use bounded deterministic priorities to keep dragging finite.
  if (baseOrder.length <= 4) {
    const visit = (prefix: string[], remaining: string[]) => {
      if (remaining.length === 0) candidates.push(prefix);
      else remaining.forEach((id, index) => visit([...prefix, id], remaining.filter((_, i) => i !== index)));
    };
    visit([], baseOrder);
  }
  for (let offset = 1; offset < Math.min(baseOrder.length, 5); offset += 1) {
    candidates.push([...baseOrder.slice(offset), ...baseOrder.slice(0, offset)]);
  }
  const seen = new Set<string>();
  let lastError: unknown = new Error("Ортогональный маршрут с заданными зазорами не найден.");
  let best: HarnessDesignDocument | null = null;
  let bestScore = Number.POSITIVE_INFINITY;
  for (const candidateOrder of candidates) {
    // Screen ports depend on the final routes covered by their oval.
    const order = [...candidateOrder].sort((a, b) =>
      Number([findWire(document, a).from, findWire(document, a).to].some(isScreenEndpoint)) -
      Number([findWire(document, b).from, findWire(document, b).to].some(isScreenEndpoint)));
    const key = order.join("\u0000");
    if (seen.has(key)) continue;
    seen.add(key);
    try {
      let changed = document;
      for (const [index, wireId] of order.entries()) {
        changed = rerouteWireE4ThroughJunctions(changed, wireId, new Set(order.slice(index + 1)));
      }
      validateE4WireIds(changed, order);
      for (const junction of changed.junctions) validateJunctionAgainstWires(changed, junction);
      validateWireGroups(changed);
      const score = e4RoutingScore(changed);
      if (score < bestScore) {
        best = changed;
        bestScore = score;
      }
    } catch (error) {
      lastError = error;
    }
  }
  if (best) return compactAutomaticDifferentialPairs(best, baseOrder);
  throw lastError;
}

function e4RoutingScore(document: HarnessDesignDocument): number {
  const carriers = document.wires.filter(wire => ![wire.from,wire.to].some(isScreenEndpoint));
  const paths = carriers.map((wire) => [wireEndpointE4Anchor(document, wire.from)!.position,
    ...wire.e4Route, wireEndpointE4Anchor(document, wire.to)!.position]);
  let score = paths.reduce((sum, points) => sum + polylineLength(points) + points.slice(1, -1)
    .filter((point, index) => (points[index]!.x === point.x) !== (point.x === points[index + 2]!.x)).length * 8, 0);
  for (let i = 0; i < paths.length; i += 1) for (let j = i + 1; j < paths.length; j += 1) {
    const crossings = new Set<string>();
    const first = paths[i]!;
    const second = paths[j]!;
    for (let a = 1; a < first.length; a += 1) for (let b = 1; b < second.length; b += 1) {
      const a0 = first[a - 1]!; const a1 = first[a]!;
      const b0 = second[b - 1]!; const b1 = second[b]!;
      const horizontal = a0.y === a1.y;
      if (horizontal === (b0.y === b1.y)) continue;
      const point = horizontal ? { x: b0.x, y: a0.y } : { x: a0.x, y: b0.y };
      if (pointOnOrthogonalSegment(point, a0, a1) && pointOnOrthogonalSegment(point, b0, b1) &&
          !document.junctions.some((junction) => junction.wireIds.includes(carriers[i]!.id) &&
            junction.wireIds.includes(carriers[j]!.id) && pointsMatch(point, junction.position))) {
        crossings.add(`${point.x}:${point.y}`);
      }
    }
    score += crossings.size * 84;
  }
  return score;
}

function validateAllE4Wires(document: HarnessDesignDocument): void {
  validateE4WireIds(document, document.wires.map((wire) => wire.id));
}

function reflowWiresAroundLabel(document: HarnessDesignDocument, labelWireId: string): HarnessDesignDocument {
  const routed = rerouteE4WireBatch(document, document.wires
    .filter((wire) => wire.id !== labelWireId && wire.e4RouteMode !== "manual")
    .map((wire) => wire.id));
  validateAllE4Wires(routed);
  return routed;
}

function validateE4WireIds(document: HarnessDesignDocument, wireIds: readonly string[]): void {
  for (const wireId of wireIds) {
    const wire = findWire(document, wireId);
    const start = wireEndpointE4Anchor(document, wire.from);
    const end = wireEndpointE4Anchor(document, wire.to);
    if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
    // A junction component is one electrical net. Its branches may enter the
    // visual label of another branch; treating that annotation as an obstacle
    // would make a valid T connection impossible after its junction moves.
    const junctionComponent = collectJunctionComponent([wireId], document.junctions);
    const request = createE4RoutingRequest(document, wire, wire.id);
    if (screenJunctionEnds(wire) && wire.e4Route.length !== 0) {
      throw new Error("Подключение экрана к проводу должно быть прямым, без изгибов.");
    }
    const points = [start.position, ...wire.e4Route, end.position];
    validateE4Route(
      points,
      {
        ...request,
        obstacles: request.obstacles?.filter((obstacle) =>
          !obstacle.id?.startsWith("wire-label:") ||
          !junctionComponent.has(obstacle.id.slice("wire-label:".length))),
      },
    );
  }
}

function wireRouteTouchesConnector(
  document: HarnessDesignDocument,
  wire: WireInstance,
  connector: ConnectorInstance,
): boolean {
  const geometry = connectorE4TableGeometry(connector);
  const padding = 4;
  const rect = {
    left: connector.positions.e4.x - padding,
    right: connector.positions.e4.x + geometry.width + padding,
    top: connector.positions.e4.y - padding,
    bottom: connector.positions.e4.y + geometry.height + padding,
  };
  const start = wireEndpointE4Anchor(document, wire.from)?.position;
  const end = wireEndpointE4Anchor(document, wire.to)?.position;
  if (!start || !end) return true;
  const points = [start, ...wire.e4Route, end];
  for (let index = 1; index < points.length; index += 1) {
    const first = points[index - 1]!;
    const second = points[index]!;
    if (first.x === second.x) {
      if (first.x >= rect.left && first.x <= rect.right &&
          Math.max(Math.min(first.y, second.y), rect.top) <= Math.min(Math.max(first.y, second.y), rect.bottom)) return true;
    } else if (first.y === second.y && first.y >= rect.top && first.y <= rect.bottom &&
        Math.max(Math.min(first.x, second.x), rect.left) <= Math.min(Math.max(first.x, second.x), rect.right)) return true;
  }
  return false;
}

/** Keep the author's unaffected corners when the deformed branch is valid.
 * Reflow is a fallback for collisions, not the default for every shared node. */
function repairE4JunctionBranches(document:HarnessDesignDocument,wireIds:ReadonlySet<string>):HarnessDesignDocument {
  let changed=document;
  const pending=[...wireIds];
  for(const [index,id] of pending.entries()){
    try {
      validateE4WireIds(changed,[id]);
      for(const j of changed.junctions.filter(j=>j.wireIds.includes(id)))validateJunctionAgainstWires(changed,j);
    } catch {
      changed=rerouteWireE4ThroughJunctions(changed,id,new Set(pending.slice(index+1)));
    }
  }
  validateE4WireIds(changed,pending);
  return changed;
}

function requireE4ModeGeometry(document: HarnessDesignDocument, points: readonly Point[]): void {
  const step=document.views.e4.e4RoutingMode==="angular"?Math.PI/6:Math.PI/2;
  for(let index=1;index<points.length;index++){
    const a=points[index-1]!,b=points[index]!;
    if(a.x===b.x&&a.y===b.y)continue;
    const turns=Math.atan2(b.y-a.y,b.x-a.x)/step;
    if(Math.abs(turns-Math.round(turns))>1e-6)throw new Error(document.views.e4.e4RoutingMode==="angular"
      ?"Маршрут Э4 допускает только углы, кратные 30°."
      :"При выключенных углах маршрут Э4 должен быть ортогональным.");
  }
}

function nearestValidE4Route(document: HarnessDesignDocument, wireId: string, preferred: readonly Point[]): HarnessDesignDocument | null {
  const wire = findWire(document, wireId);
  const start = wireEndpointE4Anchor(document, wire.from);
  const end = wireEndpointE4Anchor(document, wire.to);
  if (!start || !end) return null;
  const displacement = [0, 8, -8, 16, -16, 24, -24, 32, -32, 48, -48, 64, -64, 96, -96];
  const full = [start.position, ...preferred, end.position];
  const longest = full.slice(1).map((point, index) => ({index,length:Math.abs(point.x-full[index]!.x),horizontal:point.y===full[index]!.y}))
    .filter(item => item.horizontal && item.index>0 && item.index<full.length-2)
    .sort((left,right)=>right.length-left.length)[0];
  const bodyY = longest === undefined ? null : full[longest.index]!.y;
  const candidates = displacement.map(delta => preferred.map((point, index) =>
    bodyY !== null && point.y===bodyY && index>0 && index<preferred.length-1
      ? {x:point.x,y:point.y+delta} : {...point}));
  candidates.push([...wire.e4Route]);
  for (const route of candidates) {
    try {
      validateE4Polyline(start, route, end);
      const candidate = { ...document, wires: document.wires.map(item => item.id === wireId
        ? { ...item, e4Route: route, e4RouteMode: "manual" as const } : item) };
      validateE4Route([start.position, ...route, end.position], createE4RoutingRequest(candidate, wire, wireId));
      return candidate;
    } catch { /* Move to the next nearest legal body position. */ }
  }
  return null;
}


function rerouteWireE4ThroughJunctions(
  document: HarnessDesignDocument,
  wireId: string,
  additionallyExcludedWireIds: ReadonlySet<string> = new Set(),
): HarnessDesignDocument {
  const wire = findWire(document, wireId);
  if (screenJunctionEnds(wire)) return routeStraightScreenBranch(document, wire);
  const start = wireEndpointE4Anchor(document, wire.from);
  const end = wireEndpointE4Anchor(document, wire.to);
  if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
  const endpointJunctionIds = new Set([wire.from, wire.to].filter(isJunctionEndpoint).map((endpoint) => endpoint.junctionId));
  const currentPoints = [start.position, ...wire.e4Route, end.position];
  const waypoints = document.junctions
    .filter((junction) => junction.wireIds.includes(wireId) && !endpointJunctionIds.has(junction.id))
    .map((junction, index) => ({
      junction,
      index,
      distance: orthogonalPathProjectionDistance(currentPoints, junction.position),
    }))
    .sort((left, right) => left.distance - right.distance || left.index - right.index)
    .map(({ junction }) => junction)
    .map((junction): E4RouterAnchor => ({ position: junction.position, leadDirection: null }));
  const anchors: readonly E4RouterAnchor[] = [
    {
      ...start,
      obstacleId: isJunctionEndpoint(wire.from) || isScreenEndpoint(wire.from) ? undefined : wire.from.connectorId,
    },
    ...waypoints,
    {
      ...end,
      obstacleId: isJunctionEndpoint(wire.to) || isScreenEndpoint(wire.to) ? undefined : wire.to.connectorId,
    },
  ];
  if (waypoints.length > 0) {
    try {
      const routed = routeE4WireThroughWaypoints(
        createE4RoutingRequest(document, wire, wireId, anchors[0], anchors.at(-1),
          new Set([wireId, ...additionallyExcludedWireIds])),
        waypoints.map((waypoint) => waypoint.position),
      );
      validateE4Polyline(start, routed.intermediate, end);
      return { ...document, wires: document.wires.map((item) => item.id === wireId
        ? { ...item, e4Route: routed.intermediate } : item) };
    } catch {
      // An unrestricted shortest path can revisit an earlier leg. The
      // sequential builder below reserves completed legs in that case.
    }
  }
  const e4Route: Point[] = [];
  for (let index = 0; index < anchors.length - 1; index += 1) {
    const legStart = anchors[index]!;
    const legEnd = anchors[index + 1]!;
    const occupiedWireIds = new Set([wireId, ...additionallyExcludedWireIds]);
    const routed = routeE4Wire(createE4RoutingRequest(
      document,
      wire,
      wireId,
      legStart,
      legEnd,
      occupiedWireIds,
      e4Route.length > 0 ? [start.position, ...e4Route] : undefined,
    ));
    e4Route.push(...routed.intermediate);
    if (index < anchors.length - 2) e4Route.push({ ...legEnd.position });
  }
  validateE4Polyline(start, e4Route, end);
  validateE4Route(
    [start.position, ...e4Route, end.position],
    createE4RoutingRequest(document, wire, wireId, undefined, undefined, new Set([wireId, ...additionallyExcludedWireIds])),
  );
  return { ...document, wires: document.wires.map((item) => item.id === wireId ? { ...item, e4Route } : item) };
}

function createE4RoutingRequest(
  document: HarnessDesignDocument,
  wire: WireInstance,
  wireId: string,
  explicitStart?: E4RouterAnchor,
  explicitEnd?: E4RouterAnchor,
  excludedWireIds: ReadonlySet<string> = new Set([wireId]),
  partialRoute?: readonly Point[],
) {
  const wireStart = wireEndpointE4Anchor(document, wire.from);
  const wireEnd = wireEndpointE4Anchor(document, wire.to);
  if (!wireStart || !wireEnd) throw new Error("Точки подключения маршрута Э4 не найдены.");
  const start: E4RouterAnchor = explicitStart ?? {
    ...wireStart,
    obstacleId: isJunctionEndpoint(wire.from) || isScreenEndpoint(wire.from) ? undefined : wire.from.connectorId,
  };
  const end: E4RouterAnchor = explicitEnd ?? {
    ...wireEnd,
    obstacleId: isJunctionEndpoint(wire.to) || isScreenEndpoint(wire.to) ? undefined : wire.to.connectorId,
  };
  const fullWirePoints = (candidate: WireInstance): readonly Point[] | null => {
    const candidateStart = wireEndpointE4Anchor(document, candidate.from);
    const candidateEnd = wireEndpointE4Anchor(document, candidate.to);
    if (!candidateStart || !candidateEnd) return null;
    const points = [candidateStart.position, ...candidate.e4Route, candidateEnd.position];
    try {
      validateE4Polyline(candidateStart, candidate.e4Route, candidateEnd, screenJunctionEnds(candidate) ? 0 : defaultE4WireLead);
      return points;
    } catch {
      return null;
    }
  };
  const relatedScreenIds = new Set([
    ...[wire.from, wire.to].flatMap((endpoint) => isScreenEndpoint(endpoint) ? [endpoint.screenId] : []),
  ]);
  const screenRelatedWireIds = new Set([
    ...document.screens.filter((screen) => relatedScreenIds.has(screen.id)).flatMap((screen) => screen.wireIds),
    ...document.wires.filter((candidate) => [candidate.from, candidate.to].some((endpoint) =>
      isScreenEndpoint(endpoint) && relatedScreenIds.has(endpoint.screenId))).map((candidate) => candidate.id),
  ]);
  return {
    start,
    end,
    obstacles: [
      ...document.connectors.map((connector) => {
        const geometry = connectorE4TableGeometry(connector);
        return {
          id: connector.id,
          x: connector.positions.e4.x,
          y: connector.positions.e4.y,
          width: geometry.width,
          height: geometry.height,
        };
      }),
      // Wire strokes and labels are soft geometry. They are represented in
      // occupiedRoutes below so a rerouted wire may cross or move them; only
      // connector tables remain hard obstacles.
    ],
    occupiedRoutes: [
      ...document.wires.flatMap((candidate) => {
      if (excludedWireIds.has(candidate.id)) return [];
      // Shield leads follow the finished carrier routes. They must never push
      // those routes around, while carriers still reserve space for each other.
      if (relatedScreenIds.size === 0 && [candidate.from,candidate.to].some(isScreenEndpoint)) return [];
      if (screenRelatedWireIds.has(candidate.id)) return [];
      const points = fullWirePoints(candidate);
      const allowedTouchPoints = document.junctions
        .filter((junction) => junction.wireIds.includes(wireId) && junction.wireIds.includes(candidate.id))
        .map((junction) => junction.position);
      return points ? [{ id: candidate.id, points, allowedTouchPoints }] : [];
      }),
      ...(partialRoute && partialRoute.length >= 2
        ? [{
          id: `partial:${wireId}`,
          points: partialRoute,
          allowCrossings: false,
          allowedTouchPoint: partialRoute.at(-1),
        }]
        : []),
    ],
    // Leave a visible gap from the table stroke while keeping the mandatory
    // contact lead attached to the table itself.
    options: {
      leadLength: screenJunctionEnds(wire) ? 0 : defaultE4WireLead,
      wireClearance: 8,
      obstacleClearance: 4,
      angleStep: document.views.e4.e4RoutingMode === "angular" ? Math.PI / 6 : undefined,
    },
  };
}

/**
 * Bring an automatically routed differential pair onto the same compact
 * lanes that the E4 motif uses. The transformation is deliberately limited
 * to the common parallel segment and is kept only when the regular route
 * validator accepts the resulting conductors.
 */
function compactAutomaticDifferentialPairs(
  document: HarnessDesignDocument,
  routedWireIds: readonly string[],
): HarnessDesignDocument {
  const routed = new Set(routedWireIds);
  let result = document;
  for (const group of document.diffPairs) {
    if (!group.wireIds.every(id => routed.has(id))) continue;
    if (group.wireIds.some(id => result.wires.find(wire => wire.id === id)?.drawingEndpoints)) continue;
    const planned = planHorizontalDifferentialPair(result, group.wireIds);
    if (planned) { result = planned; continue; }
    const wires = group.wireIds.map(id => result.wires.find(wire => wire.id === id));
    if (wires.some(wire => !wire)) continue;
    const paths = wires.map(wire => {
      const start = wireEndpointE4Anchor(result, wire!.from)?.position;
      const end = wireEndpointE4Anchor(result, wire!.to)?.position;
      return start && end ? { id: wire!.id, points: [start, ...wire!.e4Route, end] } : null;
    });
    if (paths.some(path => !path)) continue;
    const span = commonHorizontalPairSpan(paths as { id: string; points: readonly Point[] }[]);
    // A differential pair is a horizontal bundle. Oblique/vertical spans are
    // valid ordinary wire geometry, but they are not candidates for the pair
    // planner and must not produce a rotated X decoration.
    if (!span || span.direction || span.orientation !== "horizontal" || span.crossMaximum - span.crossMinimum <= 24 + 1e-6) continue;
    const targetGap = 24;
    const center = (span.crossMinimum + span.crossMaximum) / 2;
    const targets = new Map(group.wireIds.map((id, index) => [id, center + (index === 0 ? -targetGap / 2 : targetGap / 2)]));
    const nextWires = result.wires.map(wire => {
      const target = targets.get(wire.id);
      const segment = span.segmentByWireId[wire.id];
      if (target === undefined || !segment) return wire;
      const start = wireEndpointE4Anchor(result, wire.from)?.position;
      const end = wireEndpointE4Anchor(result, wire.to)?.position;
      if (!start || !end) return wire;
      const full = [start, ...wire.e4Route, end];
      const p0 = full[segment.index]!, p1 = full[segment.index + 1]!;
      const along = (point: Point) => span.orientation === "horizontal" ? point.x : point.y;
      const cross = (point: Point) => span.orientation === "horizontal" ? point.y : point.x;
      const axis0 = along(p0), axis1 = along(p1), direction = axis1 >= axis0 ? 1 : -1;
      const lo = Math.max(Math.min(axis0, axis1), span.start), hi = Math.min(Math.max(axis0, axis1), span.end);
      if (hi - lo <= 1e-6) return wire;
      const axisValues = direction > 0 ? [axis0, lo, hi, axis1] : [axis0, hi, lo, axis1];
      const world = (axis: number, crossValue: number): Point => span.orientation === "horizontal"
        ? { x: axis, y: crossValue } : { x: crossValue, y: axis };
      const replacement = axisValues.map((axis, index) => world(axis, index === 1 || index === 2 ? target : cross(p0)));
      const next = [...full.slice(0, segment.index), ...replacement, ...full.slice(segment.index + 2)];
      const compacted = next.filter((point, index) => index === 0 || Math.hypot(point.x - next[index - 1]!.x, point.y - next[index - 1]!.y) > 1e-7);
      try {
        const compactStart = wireEndpointE4Anchor(result, wire.from)!;
        const compactEnd = wireEndpointE4Anchor(result, wire.to)!;
        validateE4Polyline(compactStart, compacted.slice(1, -1), compactEnd,
          screenJunctionEnds(wire) ? 0 : defaultE4WireLead);
        return { ...wire, e4Route: compacted.slice(1, -1) };
      } catch {
        return wire;
      }
    });
    const candidate = { ...result, wires: nextWires };
    // Commit the two conductors as one geometry transaction. Checking each
    // changed lane against the old position of its partner rejects a valid
    // compact pair, while accepting only one lane leaves a wide half-pair.
    try {
      for (const id of group.wireIds) validateE4WireIds(candidate, [id]);
      result = candidate;
    } catch { /* Keep the last valid pair when compact geometry is obstructed. */ }
  }
  return result;
}

function planHorizontalDifferentialPair(
  document: HarnessDesignDocument,
  wireIds: readonly [string, string],
): HarnessDesignDocument | null {
  const wires = wireIds.map(id => document.wires.find(wire => wire.id === id));
  if (wires.some(wire => !wire || wire.e4RouteMode === "manual" ||
      document.junctions.some(junction => junction.wireIds.includes(wire!.id)) ||
      document.screens.some(screen => screen.wireIds.includes(wire!.id)))) return null;
  const anchors = wires.map(wire => ({
    start: wireEndpointE4Anchor(document, wire!.from),
    end: wireEndpointE4Anchor(document, wire!.to),
  }));
  if (anchors.some(pair => !pair.start || !pair.end)) return null;
  const forward = anchors.every(pair => pair.start!.leadDirection === "right" && pair.end!.leadDirection === "left");
  const reverse = anchors.every(pair => pair.start!.leadDirection === "left" && pair.end!.leadDirection === "right");
  if (!forward && !reverse) return null;
  const direction = forward ? 1 : -1;
  const departure = forward
    ? Math.max(...anchors.map(pair => pair.start!.position.x)) + defaultE4WireLead + 4
    : Math.min(...anchors.map(pair => pair.start!.position.x)) - defaultE4WireLead - 4;
  const arrival = forward
    ? Math.min(...anchors.map(pair => pair.end!.position.x)) - defaultE4WireLead - 4
    : Math.max(...anchors.map(pair => pair.end!.position.x)) + defaultE4WireLead + 4;
  if (direction * (arrival - departure) < 48) return null;
  const ys = anchors.map(pair => pair.start!.position.y);
  const firstOnTop = ys[0]! <= ys[1]!;
  const middle = anchors.reduce((sum, pair) => sum + pair.start!.position.y + pair.end!.position.y, 0) / 4;
  const centers = [middle, ...[24, -24, 48, -48, 72, -72].map(delta => middle + delta)];
  for (const center of centers) {
    const lanes = firstOnTop ? [center - 12, center + 12] : [center + 12, center - 12];
    const candidates = wires.map((wire, index) => {
      const { start, end } = anchors[index]!;
      const lane = lanes[index]!;
      const full = [start!.position,
        { x: departure, y: start!.position.y },
        { x: departure, y: lane },
        { x: arrival, y: lane },
        { x: arrival, y: end!.position.y },
        end!.position];
      return { wire: wire!, points: simplifyE4IntermediateRoute(full[0]!, full.slice(1, -1), full.at(-1)!) };
    });
    const candidate: HarnessDesignDocument = { ...document, wires: document.wires.map(wire => {
      const planned = candidates.find(item => item.wire.id === wire.id);
      return planned ? { ...wire, e4Route: planned.points } : wire;
    }) };
    try {
      for (const id of wireIds) validateE4WireIds(candidate, [id]);
      return candidate;
    } catch { /* Try the nearest free parallel lane. */ }
  }
  return null;
}

function orthogonalPathProjectionDistance(points: readonly Point[], point: Point): number {
  let distance = 0;
  let bestDeviation = Number.POSITIVE_INFINITY;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1]!;
    const end = points[index]!;
    const horizontal = start.y === end.y;
    const projected = horizontal
      ? { x: Math.max(Math.min(point.x, Math.max(start.x, end.x)), Math.min(start.x, end.x)), y: start.y }
      : { x: start.x, y: Math.max(Math.min(point.y, Math.max(start.y, end.y)), Math.min(start.y, end.y)) };
    const deviation = Math.abs(point.x - projected.x) + Math.abs(point.y - projected.y);
    const projectedDistance = distance + Math.abs(projected.x - start.x) + Math.abs(projected.y - start.y);
    if (deviation < bestDeviation || (deviation === bestDeviation && projectedDistance < bestDistance)) {
      bestDeviation = deviation;
      bestDistance = projectedDistance;
    }
    distance += Math.abs(end.x - start.x) + Math.abs(end.y - start.y);
  }
  return bestDistance;
}

function normalizeJunction(junction: E4Junction): E4Junction {
  const id = junction.id.trim();
  const wireIds = junction.wireIds.map((wireId) => wireId.trim());
  if (!id || !Number.isFinite(junction.position.x) || !Number.isFinite(junction.position.y)) throw new Error("Узел соединения задан неверно.");
  if (wireIds.length < 2 || wireIds.some((wireId) => !wireId) || new Set(wireIds).size !== wireIds.length) {
    throw new Error("Узел соединения должен объединять не менее двух разных проводов.");
  }
  return { id, position: { ...junction.position }, wireIds };
}

function requireJunction(document: HarnessDesignDocument, junctionId: string): E4Junction {
  const junction = document.junctions.find((item) => item.id === junctionId);
  if (!junction) throw new Error("Узел соединения не найден.");
  return junction;
}

function validateJunctionAgainstWires(document: HarnessDesignDocument, junction: E4Junction): void {
  for (const wireId of junction.wireIds) {
    const wire = findWire(document, wireId);
    const endsAtJunction = [wire.from, wire.to].some((endpoint) => isJunctionEndpoint(endpoint) && endpoint.junctionId === junction.id);
    if (!endsAtJunction && !wireE4PathContainsPoint(document, wire, junction.position)) {
      throw new Error("Линия провода должна проходить через узел соединения.");
    }
  }
}

function addTargetWireToJunction(
  document: HarnessDesignDocument,
  junctionId: string,
  targetWireId: string,
): HarnessDesignDocument {
  const junction = requireJunction(document, junctionId);
  const targetWire = findWire(document, targetWireId);
  if (!wireE4PathContainsPoint(document, targetWire, junction.position)) {
    throw new Error("Целевой провод не проходит через существующий узел соединения.");
  }
  const changed = junction.wireIds.includes(targetWireId) ? document : {
    ...document,
    junctions: document.junctions.map((item) => item.id === junctionId
      ? { ...item, wireIds: [...item.wireIds, targetWireId] }
      : item),
  };
  validateJunctionAgainstWires(changed, changed.junctions.find((item) => item.id === junctionId)!);
  return changed;
}

function pointOnOrthogonalSegment(point: Point, start: Point, end: Point): boolean {
  if (start.x === end.x) return point.x === start.x && point.y >= Math.min(start.y, end.y) && point.y <= Math.max(start.y, end.y);
  if (start.y === end.y) return point.y === start.y && point.x >= Math.min(start.x, end.x) && point.x <= Math.max(start.x, end.x);
  return false;
}

function pointsMatch(left: Point, right: Point): boolean {
  return Math.hypot(left.x - right.x, left.y - right.y) < 0.01;
}

function validateWireGroups(document: HarnessDesignDocument): void {
  for (const group of document.diffPairs) if (!wireGroupHasCommonE4ParallelSpan(document, group.wireIds)) {
    throw new Error("Изменение уберёт общий параллельный участок дифференциальной пары.");
  }
  for (const screen of document.screens) if (!wireGroupHasScreenCrossSection(document, screen.wireIds)) {
    throw new Error("Изменение уберёт общий поперечный охват проводов экрана.");
  }
}

function normalizeJunctionCircuits(document: HarnessDesignDocument): HarnessDesignDocument {
  const byId = new Map(document.wires.map((wire) => [wire.id, wire]));
  for (const junction of document.junctions) {
    const component = collectConductiveComponent(junction.wireIds, document);
    const circuits = new Set([...component].map((wireId) => byId.get(wireId)?.circuit).filter((value): value is string => Boolean(value)));
    if (circuits.size > 1) throw new Error("Нельзя соединить провода с разными непустыми обозначениями цепи.");
    const circuit = circuits.values().next().value as string | undefined;
    if (circuit) for (const wireId of component) {
      const wire = byId.get(wireId);
      if (wire && !wire.circuit) byId.set(wireId, { ...wire, circuit });
    }
  }
  for (const screen of document.screens) {
    const attached = document.wires.filter((wire) => [wire.from, wire.to].some((endpoint) =>
      isScreenEndpoint(endpoint) && endpoint.screenId === screen.id)).map((wire) => wire.id);
    if (attached.length === 0) continue;
    const component = collectConductiveComponent(attached, document);
    const circuits = new Set([...component].map((wireId) => byId.get(wireId)?.circuit).filter((value): value is string => Boolean(value)));
    if (circuits.size > 1) throw new Error("Нельзя соединить с экраном провода с разными непустыми обозначениями цепи.");
    const circuit = circuits.values().next().value as string | undefined;
    if (circuit) for (const wireId of component) {
      const wire = byId.get(wireId);
      if (wire && !wire.circuit) byId.set(wireId, { ...wire, circuit });
    }
  }
  return { ...document, wires: document.wires.map((wire) => byId.get(wire.id)!) };
}

function collectConductiveComponent(initial: readonly string[], document: HarnessDesignDocument): Set<string> {
  const result = collectJunctionComponent(initial, document.junctions);
  let size = -1;
  while (size !== result.size) {
    size = result.size;
    const screenIds = new Set(document.wires.filter((wire) => result.has(wire.id)).flatMap((wire) =>
      [wire.from, wire.to].flatMap((endpoint) => isScreenEndpoint(endpoint) ? [endpoint.screenId] : [])));
    const attached = document.wires.filter((wire) => [wire.from, wire.to].some((endpoint) =>
      isScreenEndpoint(endpoint) && screenIds.has(endpoint.screenId))).map((wire) => wire.id);
    for (const wireId of collectJunctionComponent(attached, document.junctions)) result.add(wireId);
  }
  return result;
}

function collectJunctionComponent(initial: readonly string[], junctions: readonly E4Junction[]): Set<string> {
  const result = new Set(initial);
  let size = -1;
  while (size !== result.size) {
    size = result.size;
    for (const junction of junctions) if (junction.wireIds.some((wireId) => result.has(wireId))) {
      for (const wireId of junction.wireIds) result.add(wireId);
    }
  }
  return result;
}

function cleanupWireReferences(document: HarnessDesignDocument, removedWireIds: readonly string[]): HarnessDesignDocument {
  const removed = new Set(removedWireIds);
  let junctions = document.junctions;
  let screens = document.screens;
  let expanded = true;
  while (expanded) {
    expanded = false;
    junctions = junctions.map((junction) => ({ ...junction, wireIds: junction.wireIds.filter((id) => !removed.has(id)) }));
    const collapsedIds = new Set(junctions.filter((junction) => junction.wireIds.length < 2).map((junction) => junction.id));
    for (const wire of document.wires) if (!removed.has(wire.id) && [wire.from, wire.to].some((endpoint) =>
      isJunctionEndpoint(endpoint) && collapsedIds.has(endpoint.junctionId))) {
      removed.add(wire.id);
      expanded = true;
    }
    junctions = junctions.filter((junction) => !collapsedIds.has(junction.id));
    screens = screens.map((screen) => ({ ...screen, wireIds: screen.wireIds.filter((id) => !removed.has(id)) }));
    const emptyScreenIds = new Set(screens.filter((screen) => screen.wireIds.length === 0).map((screen) => screen.id));
    for (const wire of document.wires) if (!removed.has(wire.id) && [wire.from, wire.to].some((endpoint) =>
      isScreenEndpoint(endpoint) && emptyScreenIds.has(endpoint.screenId))) {
      removed.add(wire.id);
      expanded = true;
    }
    screens = screens.filter((screen) => !emptyScreenIds.has(screen.id));
  }
  return {
    ...document,
    wires: document.wires.filter((wire) => !removed.has(wire.id)),
    cables: document.cables.map((cable) => ({
      ...cable,
      memberWireIds: cable.memberWireIds.filter((wireId) => !removed.has(wireId)),
    })),
    junctions,
    diffPairs: document.diffPairs.filter((group) => !group.wireIds.some((id) => removed.has(id))),
    screens,
  };
}

function validateCableMembership(
  document: HarnessDesignDocument,
  cable: CableInstance,
  replacingCableId?: string,
): void {
  const wireIds = new Set(document.wires.map((wire) => wire.id));
  if (cable.memberWireIds.some((wireId) => !wireIds.has(wireId))) {
    throw new Error("Кабель ссылается на отсутствующий провод.");
  }
  const occupiedWireIds = new Set(document.cables
    .filter((item) => item.id !== replacingCableId)
    .flatMap((item) => item.memberWireIds));
  if (cable.memberWireIds.some((wireId) => occupiedWireIds.has(wireId))) {
    throw new Error("Провод может входить только в один кабель.");
  }
}

function updateJunctionMembership(
  document: HarnessDesignDocument,
  wireId: string,
  previous: WireEndpoint,
  next: WireEndpoint,
): HarnessDesignDocument {
  let junctions = document.junctions;
  if (isJunctionEndpoint(previous) && (!isJunctionEndpoint(next) || previous.junctionId !== next.junctionId)) {
    junctions = junctions.map((junction) => junction.id === previous.junctionId
      ? { ...junction, wireIds: junction.wireIds.filter((id) => id !== wireId) }
      : junction);
    if (junctions.some((junction) => junction.id === previous.junctionId && junction.wireIds.length < 2)) {
      throw new Error("Переподключение оставит прежний узел менее чем с двумя проводами.");
    }
  }
  if (isJunctionEndpoint(next)) {
    junctions = junctions.map((junction) => junction.id === next.junctionId && !junction.wireIds.includes(wireId)
      ? { ...junction, wireIds: [...junction.wireIds, wireId] }
      : junction);
  }
  return { ...document, junctions };
}

function addWireToEndpointJunctions(document: HarnessDesignDocument, wire: WireInstance): HarnessDesignDocument {
  let junctions = document.junctions;
  for (const endpoint of [wire.from, wire.to]) if (isJunctionEndpoint(endpoint)) {
    junctions = junctions.map((junction) => junction.id === endpoint.junctionId && !junction.wireIds.includes(wire.id)
      ? { ...junction, wireIds: [...junction.wireIds, wire.id] }
      : junction);
  }
  return { ...document, junctions };
}

function normalizeDiffPair(document: HarnessDesignDocument, group: DiffPairGroup): DiffPairGroup {
  const id = group.id.trim();
  if (!id || group.wireIds.length !== 2 || group.wireIds[0] === group.wireIds[1]) throw new Error("Дифференциальная пара должна иметь ID и два разных провода.");
  requireWireIds(document, group.wireIds);
  const detachedWires = document.wires.filter(wire => group.wireIds.includes(wire.id) && wire.drawingEndpoints);
  if (detachedWires.length) assertNoLockedDrawingWires(document, detachedWires);
  requirePositiveParameter(group.step, "Шаг дифференциальной пары");
  requirePositiveParameter(group.amplitude, "Амплитуда дифференциальной пары");
  const variant = group.variant ?? 1;
  if (variant !== 1 && variant !== 2) throw new Error("Вид дифференциальной пары задан неверно.");
  if (!wireGroupHasCommonE4ParallelSpan(document, group.wireIds)) {
    throw new Error("Выбранные провода не имеют общего параллельного участка для дифференциальной пары.");
  }
  return { id, wireIds: [group.wireIds[0], group.wireIds[1]], step: group.step, amplitude: group.amplitude, variant };
}

function normalizeScreen(document: HarnessDesignDocument, screen: WireScreenGroup): WireScreenGroup {
  const id = screen.id.trim();
  const label = screen.label.trim();
  if (!id || !label || label.length > 120) throw new Error("ID или обозначение экрана задано неверно.");
  if (screen.wireIds.length < 1 || new Set(screen.wireIds).size !== screen.wireIds.length) throw new Error("Экран должен содержать хотя бы один провод без повторов.");
  requireWireIds(document, screen.wireIds);
  if (screen.wireIds.some((wireId) => {
    const wire = document.wires.find((item) => item.id === wireId);
    return wire && [wire.from, wire.to].some((endpoint) => isScreenEndpoint(endpoint) && endpoint.screenId === id);
  })) throw new Error("Провод, подключённый к экрану, не может одновременно входить в его охват.");
  if (!Number.isFinite(screen.position) || screen.position < 0 || screen.position > 1) throw new Error("Положение экрана должно быть от 0 до 1.");
  requirePositiveParameter(screen.width, "Ширина экрана");
  const terminalSide = screen.terminalSide ?? "above";
  if (terminalSide !== "above" && terminalSide !== "below" && terminalSide !== "both") {
    throw new Error("Сторона вывода экрана задана неверно.");
  }
  if (!wireGroupHasScreenCrossSection(document, screen.wireIds)) {
    throw new Error("Выбранные провода не имеют общего поперечного охвата для экрана.");
  }
  return { id, wireIds: [...screen.wireIds], position: screen.position, label, width: screen.width, terminalSide };
}

function requireWireIds(document: HarnessDesignDocument, wireIds: readonly string[]): void {
  if (wireIds.some((id) => !document.wires.some((wire) => wire.id === id))) throw new Error("Группа ссылается на отсутствующий провод.");
}

function requirePositiveParameter(value: number, name: string): void {
  if (!Number.isFinite(value) || value <= 0) throw new Error(`${name} должен быть положительным числом.`);
}

function updateLayers(
  document: HarnessDesignDocument,
  view: EditorView,
  update: (layers: readonly EditorLayer[]) => readonly EditorLayer[],
): HarnessDesignDocument {
  return {
    ...document,
    views: { ...document.views, [view]: { ...document.views[view], layers: update(document.views[view].layers) } },
  };
}

function moveLayer(layers: readonly EditorLayer[], layerId: string, direction: -1 | 1): readonly EditorLayer[] {
  const ordered = [...layers].sort((left, right) => left.order - right.order);
  const index = ordered.findIndex((item) => item.id === layerId);
  if (index < 0) throw new Error("Слой не найден.");
  const target = index + direction;
  if (target < 0 || target >= ordered.length) return layers;
  const currentLayer = ordered[index]!;
  const targetLayer = ordered[target]!;
  return layers.map((layer) => layer.id === currentLayer.id
    ? { ...layer, order: targetLayer.order }
    : layer.id === targetLayer.id
      ? { ...layer, order: currentLayer.order }
      : layer);
}

function validateWire(document: HarnessDesignDocument, wire: WireInstance, pendingJunctionId?: string): void {
  if (sameEndpoint(wire.from, wire.to)) {
    throw new Error("Провод должен соединять две разные точки.");
  }
  requireEndpoint(document, wire.from, pendingJunctionId);
  requireEndpoint(document, wire.to, pendingJunctionId);
  calculateWireCutLength(wire);
}

function requireEndpoint(document: HarnessDesignDocument, endpoint: WireEndpoint, pendingJunctionId?: string): void {
  if (isJunctionEndpoint(endpoint)) {
    if (endpoint.junctionId !== pendingJunctionId && !document.junctions.some((item) => item.id === endpoint.junctionId)) {
      throw new Error("Узел конца провода не найден.");
    }
    return;
  }
  if (isScreenEndpoint(endpoint)) {
    if (!document.screens.some((screen) => screen.id === endpoint.screenId) ||
        !wireEndpointE4Anchor(document, endpoint)) {
      throw new Error("Точка подключения экрана не найдена.");
    }
    return;
  }
  const connector = document.connectors.find((item) => item.id === endpoint.connectorId);
  const contact = connector?.contacts.find((item) => item.id === endpoint.contactId);
  if (!contact) {
    throw new Error("Точка подключения провода не найдена.");
  }
  if (contact.connectionStatus === "not-connected") {
    throw new Error("Контакт помечен как неподключённый и не может быть соединён проводом.");
  }
}

function isContactConnected(document: HarnessDesignDocument, connectorId: string, contactId: string): boolean {
  return document.wires.some((wire) =>
    isConnectorEndpoint(wire.from, connectorId, contactId) || isConnectorEndpoint(wire.to, connectorId, contactId));
}

function sameEndpoint(left: WireEndpoint, right: WireEndpoint): boolean {
  if (isJunctionEndpoint(left)) return isJunctionEndpoint(right) && left.junctionId === right.junctionId;
  if (isScreenEndpoint(left)) return isScreenEndpoint(right) && left.screenId === right.screenId &&
    (left.screenTerminalSide ?? "above") === (right.screenTerminalSide ?? "above");
  return !isJunctionEndpoint(right) && !isScreenEndpoint(right) &&
    left.connectorId === right.connectorId && left.contactId === right.contactId;
}

function replaceRequired<T extends { readonly id: string }>(
  items: readonly T[],
  id: string,
  update: (item: T) => T,
  message: string,
): readonly T[] {
  let found = false;
  const result = items.map((item) => {
    if (item.id !== id) return item;
    found = true;
    return update(item);
  });
  if (!found) throw new Error(message);
  return result;
}

function removeRequired<T extends { readonly id: string }>(items: readonly T[], id: string, message: string): readonly T[] {
  if (!items.some((item) => item.id === id)) throw new Error(message);
  return items.filter((item) => item.id !== id);
}

function snapE4AngularBend(points: readonly Point[], index: number, target: Point, insert: boolean): Point {
  const previous=points[index]!,next=points[index+(insert?1:2)]!;
  const directions=Array.from({length:12},(_,sector)=>({x:Math.cos(sector*Math.PI/6),y:Math.sin(sector*Math.PI/6)}));
  const candidates:Point[]=[];
  for(const from of directions)for(const to of directions){
    const determinant=from.x*to.y-from.y*to.x;
    if(Math.abs(determinant)<1e-8)continue;
    const dx=next.x-previous.x,dy=next.y-previous.y;
    const distanceFrom=(dx*to.y-dy*to.x)/determinant;
    const distanceTo=(dx*from.y-dy*from.x)/determinant;
    if(distanceFrom<1e-5||distanceTo> -1e-5)continue;
    candidates.push({x:previous.x+from.x*distanceFrom,y:previous.y+from.y*distanceFrom});
  }
  return candidates.sort((left,right)=>Math.hypot(left.x-target.x,left.y-target.y)-Math.hypot(right.x-target.x,right.y-target.y))[0]??target;
}
