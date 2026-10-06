import type {CoveringSurface} from "./covering-layout";
import type {Point} from "./model";

/** Dense transverse windings follow the pipe, including vertical and curved
 * spans. They are clipped to the exact sleeve polygon, with no blurred ends. */
export function threadBandStrokes(surface:CoveringSurface,scale:number,rotation:number):readonly (readonly [Point,Point])[] {
 const points=surface.path,step=Math.max(.3,3*scale),angle=rotation*Math.PI/180;
 const strokes:[Point,Point][]=[];let before=0,next=step/2;
 // The cross section may vary on stepped lower coatings. A long cross-stroke
 // is clipped against the sleeve boundary by the caller.
 const width=surface.polygon.reduce((max,p,i)=>{
  const q=surface.polygon[surface.polygon.length-1-i]!;
  return Math.max(max,Math.hypot(p.x-q.x,p.y-q.y));
 },0);
 for(let i=1;i<points.length;i++){
  const a=points[i-1]!,b=points[i]!,length=Math.hypot(b.x-a.x,b.y-a.y);if(!length)continue;
  const ux=(b.x-a.x)/length,uy=(b.y-a.y)/length;
  const nx=-uy*Math.cos(angle)+ux*Math.sin(angle),ny=ux*Math.cos(angle)+uy*Math.sin(angle);
  while(next<before+length&&strokes.length<10000){const t=(next-before)/length,x=a.x+(b.x-a.x)*t,y=a.y+(b.y-a.y)*t;
   strokes.push([{x:x-nx*width,y:y-ny*width},{x:x+nx*width,y:y+ny*width}]);next+=step;
  }
  before+=length;
 }
 return strokes;
}

export function drawThreadBand(context:CanvasRenderingContext2D,surface:CoveringSurface,scale:number,rotation:number,color:string):void {
 context.save();context.beginPath();surface.polygon.forEach((p,i)=>i?context.lineTo(p.x,p.y):context.moveTo(p.x,p.y));context.closePath();context.clip();
 context.beginPath();for(const [a,b] of threadBandStrokes(surface,scale,rotation)){context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);}
 const width=Math.max(.15,scale*.65);
 // Keep the weave visible on the default light tint. The reference band has
 // a fine dark relief under the selected thread colour; without this pass a
 // white tint on a white sleeve renders as an empty rectangle.
 context.strokeStyle="#263640";context.lineWidth=width*1.9;context.globalAlpha=.28;context.lineCap="butt";context.stroke();
 context.strokeStyle=color;context.lineWidth=width;context.globalAlpha=.85;context.stroke();context.restore();
}
