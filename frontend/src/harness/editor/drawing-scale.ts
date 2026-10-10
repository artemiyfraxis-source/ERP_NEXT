import type { ConnectorDrawingPlacement, ConnectorInstance, HarnessDesignDocument } from "./model";
export const DRAWING_VIEW_PLACEMENT_ID="view:drawing";
/**
 * Reserved per-connector placement for the copy of its contact-side template
 * drawing shown on the harness Drawing.  It deliberately is not a template
 * view ID: template authors may rename or replace their source E4 drawing
 * without invalidating a placement saved in a harness document.
 */
export const CONTACT_SIDE_VIEW_PLACEMENT_ID="view:contact-side";
export type DrawingDirection = "left" | "right" | "up" | "down";
const drawingDirectionVectors: Record<DrawingDirection,{x:number;y:number}> = {
 left:{x:-1,y:0}, right:{x:1,y:0}, up:{x:0,y:-1}, down:{x:0,y:1},
};
export function drawingScale(placements:readonly ConnectorDrawingPlacement[]|undefined,id=DRAWING_VIEW_PLACEMENT_ID){return placements?.find(p=>p.drawingId===id)?.scale??1;}
export function validDrawingScale(value:number){return Number.isFinite(value)&&value>=.05&&value<=20;}
export function scaleFromDrag(scale:number,vx:number,vy:number,dx:number,dy:number){return Math.max(.05,Math.min(20,scale*(1+(dx*vx+dy*vy)/Math.max(1,vx*vx+vy*vy))));}

export function drawingRotation(placements:readonly ConnectorDrawingPlacement[]|undefined,id=DRAWING_VIEW_PLACEMENT_ID){return placements?.find(p=>p.drawingId===id)?.rotationDegrees??0;}
export function drawingLocalPoint(point:{x:number;y:number},placements:readonly ConnectorDrawingPlacement[]|undefined,id=DRAWING_VIEW_PLACEMENT_ID){
 const a=drawingRotation(placements,id)*Math.PI/180,scale=drawingScale(placements,id);
 return {x:scale*(point.x*Math.cos(a)-point.y*Math.sin(a)),y:scale*(point.x*Math.sin(a)+point.y*Math.cos(a))};
}

/** Rotates a template cardinal direction into the drawing's cardinal direction.
 * Contact snapshots intentionally keep cardinal directions, so a non-axial
 * rotation is represented by the nearest axis after the vector is rotated. */
export function drawingLocalDirection(direction:DrawingDirection,placements:readonly ConnectorDrawingPlacement[]|undefined):DrawingDirection {
 const vector=drawingLocalDirectionVector(direction,placements);
 if(Math.abs(vector.x)>=Math.abs(vector.y))return vector.x>=0?"right":"left";
 return vector.y>=0?"down":"up";
}

/** Exact unit direction for contact marks at any drawing rotation angle. */
export function drawingLocalDirectionVector(direction:DrawingDirection,placements:readonly ConnectorDrawingPlacement[]|undefined){
 const vector=drawingLocalPoint(drawingDirectionVectors[direction],placements),length=Math.hypot(vector.x,vector.y);
 return {x:vector.x/length,y:vector.y/length};
}

export function drawingPointToLocal(point:{x:number;y:number},placements:readonly ConnectorDrawingPlacement[]|undefined,id=DRAWING_VIEW_PLACEMENT_ID){
 const a=-drawingRotation(placements,id)*Math.PI/180,scale=drawingScale(placements,id);
 return {x:(point.x*Math.cos(a)-point.y*Math.sin(a))/scale,y:(point.x*Math.sin(a)+point.y*Math.cos(a))/scale};
}

/** «Реальные размеры, мм» at М 1:N: a library connector's drawing is shown at
 * its template size (1 template unit = 1 mm), i.e. at scale 1/N, whatever
 * per-connector scale the drawing stores. This is a view of the document —
 * the stored scales are never rewritten (see `withStoredDrawingScales`). */
export function realSizeConnectorScale(document:HarnessDesignDocument):number|null {
 const realSize=document.drawingDocuments?.realSize;
 return realSize?.enabled?1/realSize.scaleDenominator:null;
}
const hasRealSizeDrawing=(connector:ConnectorInstance)=>connector.libraryBinding?.mode==="template";
/** Connectors drawn without a real size (no library template) in real-size mode. */
export function connectorsWithoutRealSize(document:HarnessDesignDocument):number {
 return document.connectors.filter(connector=>!hasRealSizeDrawing(connector)).length;
}
const defaultPlacement=(id:string):ConnectorDrawingPlacement=>({drawingId:id,visible:true,offset:{x:0,y:0}});
export function realSizeView(document:HarnessDesignDocument):HarnessDesignDocument {
 const scale=realSizeConnectorScale(document);
 if(scale===null||!document.connectors.some(hasRealSizeDrawing))return document;
 return {...document,connectors:document.connectors.map(connector=>{
  if(!hasRealSizeDrawing(connector))return connector;
  const placements=connector.drawingPlacements??[],previous=placements.find(p=>p.drawingId===DRAWING_VIEW_PLACEMENT_ID);
  if(previous?.scale===scale)return connector;
  const next={...(previous??defaultPlacement(DRAWING_VIEW_PLACEMENT_ID)),scale};
  return {...connector,drawingPlacements:previous?placements.map(p=>p===previous?next:p):[...placements,next]};
 })};
}
/** Brings an edit made on `realSizeView(stored)` back to the stored scales: a
 * connector the edit did not touch is the stored one; a placement still at the
 * real-size scale gets its stored scale back (or loses the placement the view
 * had to add); a scale the edit set itself is kept. */
export function withStoredDrawingScales(edited:HarnessDesignDocument,stored:HarnessDesignDocument,view=realSizeView(stored)):HarnessDesignDocument {
 const scale=realSizeConnectorScale(stored);
 if(scale===null||view===stored)return edited;
 const viewed=new Map(view.connectors.map(c=>[c.id,c])),originals=new Map(stored.connectors.map(c=>[c.id,c]));
 let changed=false;
 const connectors=edited.connectors.map(connector=>{
  const original=originals.get(connector.id);
  if(!original||!hasRealSizeDrawing(original))return connector;
  changed=true;
  if(connector===viewed.get(connector.id))return original;
  const placements=connector.drawingPlacements??[],placement=placements.find(p=>p.drawingId===DRAWING_VIEW_PLACEMENT_ID);
  if(!placement||placement.scale!==scale)return connector;
  const before=original.drawingPlacements?.find(p=>p.drawingId===DRAWING_VIEW_PLACEMENT_ID);
  const {scale:_view,...rest}=placement;
  const added=!before&&rest.visible&&rest.offset.x===0&&rest.offset.y===0&&Object.keys(rest).length===3;
  if(!added)return {...connector,drawingPlacements:placements.map(p=>p===placement?(before?.scale===undefined?rest:{...rest,scale:before.scale}):p)};
  const others=placements.filter(p=>p!==placement);
  if(!others.length&&original.drawingPlacements===undefined){const {drawingPlacements:_none,...bare}=connector;return bare as ConnectorInstance;}
  return {...connector,drawingPlacements:others};
 });
 return changed?{...edited,connectors}:edited;
}
/** Runs an edit on the real-size view of the drawing and stores the result with the stored scales. */
export function applyInRealSizeView(stored:HarnessDesignDocument,apply:(document:HarnessDesignDocument)=>HarnessDesignDocument):HarnessDesignDocument {
 const view=realSizeView(stored);
 if(view===stored)return apply(stored);
 const edited=apply(view);
 return edited===view?stored:withStoredDrawingScales(edited,stored,view);
}
