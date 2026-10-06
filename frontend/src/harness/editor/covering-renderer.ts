import {materialTexture} from "./texture-tint";
import {drawCatalogHatch} from "../hatching";
import {drawThreadBand} from "./thread-band-renderer";
import { drawConformalVolumeSurface, drawVolumeSurface } from "./drawing-volume";
import type { CoveringHandle, CoveringSurface } from "./covering-layout";
import type { EditorPoint, EditorSceneObject } from "./editor-types";
import { coveringTextureFile,resolvedCoveringStyle,type CoveringStyle } from "./covering-style";
import type { CoveringKind } from "./physical-coverings";
import rubberTexture from "./covering-textures/Rubber002.jpg";
import fabricTexture from "./covering-textures/Fabric061.jpg";
import metalTexture from "./covering-textures/Metal049A.jpg";

const textures=new Map<string,HTMLImageElement>();
const listeners=new Set<()=>void>();
export const coveringTextureUrls: Readonly<Record<string, string>> = {
  Rubber002: rubberTexture, Fabric061: fabricTexture, Metal049A: metalTexture,
};

export function coveringTextureTransform(style: Required<CoveringStyle>, tile: HTMLCanvasElement, _surface?: CoveringSurface): DOMMatrix {
 const base = .5 * style.textureScale * 64 / tile.width;
 return new DOMMatrix().rotate(style.textureRotation).scale(base);
}
export function warmCoveringTextures(invalidate:()=>void,objects:readonly EditorSceneObject[]=[]):()=>void {
  listeners.add(invalidate);
  if(typeof Image!=="undefined")for(const file of ["Rubber002","Fabric061","Metal049A"])if(!textures.has(file)){
    const image=new Image();textures.set(file,image);
    image.onload=()=>listeners.forEach(fn=>fn());image.src=coveringTextureUrls[file]!;
  }
  if(typeof Image!=="undefined")for(const object of objects){const url=object.metadata?.coveringTextureUrl;if(!url||textures.has(url))continue;
    const image=new Image();textures.set(url,image);image.onload=()=>listeners.forEach(fn=>fn());image.src=url;
    if(textures.size>1030)for(const key of textures.keys())if(!["Rubber002","Fabric061","Metal049A"].includes(key)&&!objects.some(o=>o.metadata?.coveringTextureUrl===key)){textures.delete(key);break;}
  }
  return ()=>{listeners.delete(invalidate);};
}
const hatchTiles=new Map<string,HTMLCanvasElement>();
/** A bounded repeat tile avoids per-line work on very long pipes. */
function hatchTile(style:Required<CoveringStyle>):HTMLCanvasElement|null {
  if(style.hatch==="none"||typeof document==="undefined")return null;
  const key=JSON.stringify([style.hatch,style.hatchColor,style.hatchLineWidth,style.hatchSpacing]);
  const cached=hatchTiles.get(key);if(cached)return cached;
  const tile=document.createElement("canvas");tile.width=tile.height=32;
  const ctx=tile.getContext("2d");if(!ctx)return null;
  drawHatchTile(ctx,style.hatch,style.hatchColor,style.hatchLineWidth*32/style.hatchSpacing);
  if(hatchTiles.size>=64)hatchTiles.delete(hatchTiles.keys().next().value!);
  hatchTiles.set(key,tile);return tile;
}
export function drawHatchTile(ctx:CanvasRenderingContext2D,hatch:Required<CoveringStyle>["hatch"],color:string,width=2):void {
  ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=width;ctx.beginPath();
  if(hatch==="dots"){ctx.arc(16,16,width/2,0,2*Math.PI);ctx.fill();}
  else if(hatch!=="none"){
    ctx.moveTo(0,16);ctx.lineTo(32,16);
    if(hatch==="cross"){ctx.moveTo(16,0);ctx.lineTo(16,32);}
    ctx.stroke();
  }
}
export const coveringGrips=(object:EditorSceneObject):CoveringHandle[]=>JSON.parse(object.metadata?.coveringHandles??"[]");
export const coveringSurfaces=(object:EditorSceneObject):CoveringSurface[]=>JSON.parse(object.metadata?.surfaces??"[]");
/** Even/odd containment supports concave sleeves around bends. */
export function coveringHit(object:EditorSceneObject,p:EditorPoint,tolerance:number):number|null {
  for(const [index,surface] of coveringSurfaces(object).entries()){
    let inside=false;const polygon=surface.polygon;
    for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){
      const a=polygon[j]!,b=polygon[i]!,dx=b.x-a.x,dy=b.y-a.y,den=dx*dx+dy*dy;
      const t=den?Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/den)):0;
      if(Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy)<=tolerance)return surface.spanIndex??index;
      if((a.y>p.y)!==(b.y>p.y)&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)inside=!inside;
    }
    if(inside)return surface.spanIndex??index;
  }
  return null;
}

export function drawCoveringSurface(context:CanvasRenderingContext2D,object:EditorSceneObject,selected:boolean):void {
  const style=resolvedCoveringStyle(JSON.parse(object.metadata?.coveringStyle??"{}"));
  const file=coveringTextureFile((object.metadata?.coveringKind??"braid") as CoveringKind,style);
  for(const surface of coveringSurfaces(object)){
    const polygon=surface.polygon;if(!polygon.length)continue;
    context.save();context.beginPath();polygon.forEach((p,i)=>i?context.lineTo(p.x,p.y):context.moveTo(p.x,p.y));context.closePath();
    context.fillStyle=object.color;context.fill();
    const image=object.metadata?.coveringTextureUrl?textures.get(object.metadata.coveringTextureUrl):file?textures.get(file):undefined;
    if(image?.complete&&image.naturalWidth){
      // The polygon is the opaque background layer. The material image is
      // always an alpha texture so its light/empty pixels reveal that fill;
      // this keeps background and thread/braid colour independently editable.
      const tile=materialTexture(image,style.textureTint,context,style.textureScale);
      const pattern=context.createPattern(tile,"repeat");
      if(pattern){pattern.setTransform(coveringTextureTransform(style,tile));context.save();context.fillStyle=pattern;context.fill();context.restore();}
    }
    const tile=hatchTile(style);
    if(tile){const pattern=context.createPattern(tile,"repeat");if(pattern){pattern.setTransform(new DOMMatrix().rotate(style.hatchRotation).scale(style.hatchSpacing/32));context.fillStyle=pattern;context.fill();}}
    if(style.hatchCode){
      const xs=polygon.map(p=>p.x),ys=polygon.map(p=>p.y),x=Math.min(...xs),y=Math.min(...ys);
      context.save();context.clip();drawCatalogHatch(context,style.hatchCode,style.hatchSpacing/10,style.hatchRotation,style.hatchColor,style.hatchLineWidth,{x,y,width:Math.max(...xs)-x,height:Math.max(...ys)-y});context.restore();
    }
    if(object.metadata?.volumeShading === "true") {
      if(surface.conformal) drawConformalVolumeSurface(context,polygon,surface.path,surface.ridges);
      else drawVolumeSurface(context,polygon,surface.path);
    }
    if(object.metadata?.coveringKind === "band" && style.texture!=="none")
      drawThreadBand(context,surface,style.textureScale,style.textureRotation,style.textureTint);
    context.beginPath();
    if(surface.conformal){polygon.forEach((p,i)=>i?context.lineTo(p.x,p.y):context.moveTo(p.x,p.y));context.closePath();}
    else {polygon.forEach((p,i)=>i&&!(surface.openEnd&&i===polygon.length/2)?context.lineTo(p.x,p.y):context.moveTo(p.x,p.y));
      if(!surface.openStart){const p=polygon[0]!;context.lineTo(p.x,p.y);}}
    context.strokeStyle=selected?"#007fae":style.lineColor;context.lineWidth=selected?Math.max(2,style.lineWidth):style.lineWidth;context.stroke();context.restore();
  }
  for(const grip of coveringGrips(object)){
    if(grip.part.startsWith("transition-")){context.beginPath();context.arc(grip.point.x,grip.point.y,4,0,Math.PI*2);context.fillStyle="white";context.fill();context.strokeStyle="#007fae";context.lineWidth=2;context.stroke();continue;}
    if(!selected)continue;
    if(grip.pointMarker){context.beginPath();context.arc(grip.point.x,grip.point.y,4,0,Math.PI*2);context.fillStyle="white";context.fill();context.strokeStyle=grip.bound?"#21905c":"#007fae";context.lineWidth=2;context.stroke();continue;}
    const {point:p,normal:n,halfWidth:w}=grip,right=grip.rightHalfWidth??w;context.strokeStyle=grip.bound?"#21905c":"#007fae";context.lineWidth=3;
    context.beginPath();context.moveTo(p.x-n.x*right,p.y-n.y*right);context.lineTo(p.x+n.x*w,p.y+n.y*w);context.stroke();
  }
}
