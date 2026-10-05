import type { HarnessDesignDocument, WireInstance } from "./model";
import type { PhysicalSegment } from "./physical-topology-model";
import { packPipeBundle } from "./pipe-bundle-packing";

/** Catalogue diameters are millimetres; drawing widths are deliberately relative. */
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
export const drawingPhysicalScale=(document:HarnessDesignDocument)=>document.drawingDocuments?.physicalScale??1;
/** Global 1:x relationship used when adjacent protective layers grow. */
export const coveringDiameterRatio=(document:HarnessDesignDocument)=>document.drawingDocuments?.coveringDiameterRatio??2;
export function drawingWireWidth(document:HarnessDesignDocument,wire:WireInstance):number {
  const reference=drawingReferenceDiameter(document);
  return 2.5*drawingPhysicalScale(document)*(wireOuterDiameter(document,wire)??reference)/reference;
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
  const gap=.25*drawingPhysicalScale(document),widths=members.map(id=>drawingWireWidth(document,document.wires.find(w=>w.id===id)!));
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
  const bundle=section.diameter;
  return Math.max((segment.width??0)*scale,bundle+.5*scale);
}
