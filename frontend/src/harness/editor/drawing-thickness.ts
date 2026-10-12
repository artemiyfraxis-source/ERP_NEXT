import type { HarnessDesignDocument, WireInstance } from "./model";
import type { PhysicalSegment } from "./physical-topology-model";
import { packPipeBundle } from "./pipe-bundle-packing";

/** Catalogue diameters are millimetres. Drawing widths are relative to the
 * thinnest catalogue wire unless the drawing is in «Реальные размеры, мм»:
 * then 1 drawing unit is N mm (М 1:N) and every cross-section is drawn at its
 * catalogue size. Harness lengths are never scaled — they stay the typed
 * nominal dimensions, like a drawing marked «не в масштабе» for lengths. */
export function catalogOuterDiameter(payload:Readonly<Record<string,unknown>>):number|undefined {
  const fields=Object.entries(payload).map(([key,value])=>[key.toLowerCase().replace(/[\s_,.()-]/g,""),value] as const);
  for(const names of [["внешнийдиаметр","наружныйдиаметр","outerdiametermm","outerdiameter","externaldiameter"],["диаметризоляции","диаметрпоизоляции","insulationdiameter","insulationdiametermm"]]) {
    for(const [key,value] of fields)if(names.some(name=>key===name||key===name+"мм")) {
      const number=typeof value==="number"?value:typeof value==="string"?Number(value.trim().replace(/\s*(?:mm|мм)$/i,"").replace(",",".")):NaN;
      if(Number.isFinite(number)&&number>0&&number<=1000)return number;
    }
  }
  return undefined;
}
export function wireOuterDiameter(document:HarnessDesignDocument,wire:WireInstance):number|undefined {
  return wire.materialBinding?.outerDiameterMm ?? [wire.from,wire.to].map(e=>document.connectors.find(c=>c.id===e.connectorId)?.contacts.find(c=>c.id===e.contactId)?.wireDiameterMm).find(d=>d!==undefined);
}
export function drawingReferenceDiameter(document:HarnessDesignDocument):number {
  const values=document.wires.map(w=>wireOuterDiameter(document,w)).filter((d):d is number=>!!d&&d>0);
  return values.length?Math.min(...values):1;
}
/** М 1:N when «Реальные размеры, мм» is on, otherwise null (today's relative look). */
export function drawingRealSizeScale(document:HarnessDesignDocument):number|null {
  const realSize=document.drawingDocuments?.realSize;
  return realSize?.enabled?realSize.scaleDenominator:null;
}
/** Drawing units per millimetre of cross-section: the «Толщина» factor, or 1/N in real size. */
export const drawingPhysicalScale=(document:HarnessDesignDocument)=>{const n=drawingRealSizeScale(document);return n===null?document.drawingDocuments?.physicalScale??1:1/n;};
/** Global 1:x relationship used when adjacent protective layers grow. */
export const coveringDiameterRatio=(document:HarnessDesignDocument)=>document.drawingDocuments?.coveringDiameterRatio??2;
export function drawingWireWidth(document:HarnessDesignDocument,wire:WireInstance):number {
  const reference=drawingReferenceDiameter(document),n=drawingRealSizeScale(document);
  // Real size: the catalogue diameter at М 1:N. A wire without one is drawn as
  // the thinnest catalogue wire of the drawing (or 1 mm) and counted in the scale bar.
  if(n!==null)return (wireOuterDiameter(document,wire)??reference)/n;
  return 2.5*drawingPhysicalScale(document)*(wireOuterDiameter(document,wire)??reference)/reference;
}
/** Wires drawn without a catalogue diameter in real size — the scale bar names their count. */
export function wiresWithoutDiameter(document:HarnessDesignDocument):number {
  return document.wires.filter(wire=>wireOuterDiameter(document,wire)===undefined).length;
}
/** A segment carrying a cable item is drawn at that cable's catalogue outer diameter in real size. */
function segmentCableDiameter(document:HarnessDesignDocument,segment:PhysicalSegment):number|undefined {
  return segment.specificationItemId?document.cables.find(cable=>cable.id===segment.specificationItemId)?.materialBinding?.outerDiameterMm:undefined;
}
export interface ProjectedWireStrip { readonly id:string; readonly offset:number; readonly width:number }
export interface SegmentWireProjection {
  readonly mode:"flat"|"round";
  readonly lanes:readonly {readonly id:string;readonly width:number;readonly offset:number;readonly depth?:number}[];
  readonly strips:readonly ProjectedWireStrip[];
  readonly diameter:number;
}
const sectionCache=new WeakMap<HarnessDesignDocument,Map<string,SegmentWireProjection>>();
/** Cross-section coordinates stay in the drawing projection. Depth affects
 * occlusion only; it never changes the electrical path or cut length. */
export function segmentWireProjection(document:HarnessDesignDocument,segmentId:string):SegmentWireProjection {
  let sections=sectionCache.get(document);if(!sections){sections=new Map();sectionCache.set(document,sections);}
  const cached=sections.get(segmentId);if(cached)return cached;
  const mode=document.physicalTopology?.segments.find(s=>s.id===segmentId)?.mode??"flat";
  const members=(document.physicalTopology?.routes??[]).filter(r=>r.steps.some(s=>s.segmentId===segmentId)).map(r=>r.wireId).sort();
  // Real wires touch inside a bundle; the relative look keeps a small lane gap.
  const gap=drawingRealSizeScale(document)===null?.25*drawingPhysicalScale(document):0,widths=members.map(id=>drawingWireWidth(document,document.wires.find(w=>w.id===id)!));
  const total=widths.reduce((a,b)=>a+b,0)+Math.max(0,members.length-1)*gap;
  let x=-total/2;
  const flatLanes=members.map((id,i)=>{const width=widths[i]!,offset=x+width/2;x+=width+gap;return {id,width,offset};});
  if(mode==="flat"||!members.length){const result={mode,lanes:flatLanes,strips:flatLanes.map(lane=>({...lane})),diameter:total};sections.set(segmentId,result);return result;}
  // The v5 cross-section looks along the first packing axis. Transposing the
  // packed coordinates makes that axis depth and the second axis the visible
  // lateral coordinate on the drawing.
  const packed=packPipeBundle(members.map((id,i)=>({id,diameter:widths[i]!})),"round");
  const lanes=packed.members.map(member=>({id:member.id,width:member.diameter,offset:member.depth,depth:member.offset}));
  const left=Math.min(...lanes.map(lane=>lane.offset-lane.width/2));
  const right=Math.max(...lanes.map(lane=>lane.offset+lane.width/2));
  const count=Math.max(300,Math.min(1024,Math.ceil((right-left)*32))),step=(right-left)/count;
  const strips:ProjectedWireStrip[]=[];let visible:string|null=null,begin=0;
  for(let i=0;i<=count;i++){
    const lateral=left+(i+.5)*step;
    let next:string|null=null,nearest=Infinity;
    if(i<count)for(const lane of lanes){
      const delta=lateral-lane.offset,radius=lane.width/2;
      if(Math.abs(delta)>radius)continue;
      const surface=lane.depth-Math.sqrt(Math.max(0,radius*radius-delta*delta));
      if(surface<nearest-1e-9){nearest=surface;next=lane.id;}
    }
    if(next!==visible){if(visible)strips.push({id:visible,offset:left+(begin+i)*step/2,width:(i-begin)*step});visible=next;begin=i;}
  }
  const diameter=2*Math.max(...lanes.map(lane=>Math.hypot(lane.offset,lane.depth)+lane.width/2));
  const result={mode,lanes,strips,diameter};sections.set(segmentId,result);return result;
}
export function segmentWireLanes(document:HarnessDesignDocument,segmentId:string) {
  return segmentWireProjection(document,segmentId).lanes;
}
export function drawingPipeWidth(document:HarnessDesignDocument,segment:PhysicalSegment):number {
  const scale=drawingPhysicalScale(document),section=segmentWireProjection(document,segment.id);
  const bundle=section.diameter,n=drawingRealSizeScale(document);
  if(n!==null){const cable=segmentCableDiameter(document,segment);return Math.max((segment.width??0)*scale,cable!==undefined?Math.max(cable/n,bundle):bundle);}
  return Math.max((segment.width??0)*scale,bundle+.5*scale);
}
