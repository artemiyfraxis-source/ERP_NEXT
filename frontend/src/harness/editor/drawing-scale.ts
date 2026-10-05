import type { ConnectorDrawingPlacement } from "./model";
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
