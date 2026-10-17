import {drawReferenceTexture,referenceTexture,referenceTextureResolution,type ReferenceTexture} from "./reference-texture";
/** Recolour luminance, not opacity: even black/white retain visible material tones. */
export function tintTexturePixels(pixels:Uint8ClampedArray,tint:string):void {
 const rgb=[1,3,5].map(i=>parseInt(tint.slice(i,i+2),16));
 const base=rgb.map(c=>24+c*207/255);
 for(let i=0;i<pixels.length;i+=4){
  const tone=(.2126*pixels[i]!+.7152*pixels[i+1]!+.0722*pixels[i+2]!)/255;
  for(let c=0;c<3;c++)pixels[i+c]=Math.round(tone<.5?base[c]!*(.45+1.1*tone):base[c]!+(255-base[c]!)*(tone-.5)*1.1);
 }
}
const tiles=new Map<string,HTMLCanvasElement>();
export function tintedTexture(image:HTMLImageElement,tint:string):HTMLCanvasElement {
 const key=`${image.src}|${tint}`; const cached=tiles.get(key); if(cached)return cached;
 const tile=document.createElement("canvas");
 tile.width=image.naturalWidth;tile.height=image.naturalHeight;
 const ctx=tile.getContext("2d")!;ctx.drawImage(image,0,0,tile.width,tile.height);
 const pixels=ctx.getImageData(0,0,tile.width,tile.height);tintTexturePixels(pixels.data,tint);ctx.putImageData(pixels,0,0);
 cacheTile(key,tile);return tile;
}

/** Turns a white/neutral source into a transparent texture layer. The selected
 * background remains visible below it, while dark marks retain their density. */
export function textureOverlay(image:HTMLImageElement,tint:string):HTMLCanvasElement {
 const key=`overlay|${image.src}|${tint}`;const cached=tiles.get(key);if(cached)return cached;
 const tile=document.createElement("canvas");
 tile.width=image.naturalWidth;tile.height=image.naturalHeight;
 const ctx=tile.getContext("2d")!;ctx.drawImage(image,0,0,tile.width,tile.height);
 const pixels=ctx.getImageData(0,0,tile.width,tile.height);
 textureOverlayPixels(pixels.data,tint);
 ctx.putImageData(pixels,0,0);cacheTile(key,tile);return tile;
}

export function textureOverlayPixels(pixels:Uint8ClampedArray,tint:string):void {
 const rgb=[1,3,5].map(i=>parseInt(tint.slice(i,i+2),16));
 for(let i=0;i<pixels.length;i+=4){
  const luminance=(.2126*pixels[i]!+.7152*pixels[i+1]!+.0722*pixels[i+2]!)/255;
  const alpha=Math.round(pixels[i+3]!*(1-luminance)**1.35);
  for(let c=0;c<3;c++)pixels[i+c]=rgb[c]!;
  pixels[i+3]=alpha;
 }
}

// Bound memory by pixels as well as entry count now that sources retain detail.
const maxCachedPixels=16*1024*1024;
let cachedPixels=0;
function cacheTile(key:string,tile:HTMLCanvasElement):void {
 const area=tile.width*tile.height;
 if(area>maxCachedPixels)return;
 while(tiles.size&&(tiles.size>=24||cachedPixels+area>maxCachedPixels)){
  const oldest=tiles.keys().next().value!,old=tiles.get(oldest)!;
  cachedPixels-=old.width*old.height;tiles.delete(oldest);
 }
 tiles.set(key,tile);cachedPixels+=area;
}
const referenceKinds=new WeakMap<HTMLImageElement,ReferenceTexture|null>();
/** Shared by the drawing and library preview, including legacy project images. */
export function materialTexture(image:HTMLImageElement,tint:string,context:CanvasRenderingContext2D,scale:number):HTMLCanvasElement {
 if(!referenceKinds.has(image)){
  let kind:ReferenceTexture|undefined;
  if(image.naturalWidth===64&&image.naturalHeight===64){
   const source=document.createElement("canvas");source.width=source.height=64;
   const ctx=source.getContext("2d")!;ctx.drawImage(image,0,0);
   kind=referenceTexture(ctx.getImageData(0,0,64,64).data,64,64);
  }
  referenceKinds.set(image,kind??null);
 }
 const kind=referenceKinds.get(image);
 if(!kind)return textureOverlay(image,tint);
 const size=referenceTextureResolution(context,scale),key=`reference|${kind}|${tint}|${size}`;
 const cached=tiles.get(key);if(cached)return cached;
 const tile=document.createElement("canvas");tile.width=tile.height=size;
 drawReferenceTexture(tile.getContext("2d")!,kind,tint,size);cacheTile(key,tile);return tile;
}
