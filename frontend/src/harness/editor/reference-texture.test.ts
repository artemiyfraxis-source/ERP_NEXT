// @vitest-environment node
import {afterEach,expect,it,vi} from "vitest";
import {referenceTexture,referenceTextureResolution} from "./reference-texture";
import {materialTexture,textureOverlay} from "./texture-tint";

afterEach(()=>vi.unstubAllGlobals());

it("allocates enough texels for camera zoom, material scale and Retina without changing the repeat",()=>{
 const context=(a:number,b=0,c=0,d=a)=>({getTransform:()=>({a,b,c,d})}) as CanvasRenderingContext2D;
 expect(referenceTextureResolution(context(1),1)).toBe(256);
 expect(referenceTextureResolution(context(8),10)).toBe(4096);
 expect(referenceTextureResolution(context(0,8,-8,0),10)).toBe(4096);
 for(const zoom of [.25,1,2,4])for(const scale of [.1,1,4,10]){
  const size=referenceTextureResolution(context(zoom*2),scale);
  expect(size).toBeGreaterThanOrEqual(32*scale*zoom*2);
  expect(size*(32*scale/size)).toBeCloseTo(32*scale);
 }
});

it("does not reinterpret arbitrary 64px uploads as a built-in material",()=>{
 expect(referenceTexture(new Uint8ClampedArray(64*64*4).fill(128),64,64)).toBeUndefined();
 expect(referenceTexture(new Uint8ClampedArray(64*64*4),32,128)).toBeUndefined();
});

it("preserves original resolution and aspect ratio for recoloured raster uploads",()=>{
 const drawImage=vi.fn(),pixels=new Uint8ClampedArray([0,0,0,255,255,255,255,255]);
 const ctx={drawImage,getImageData:()=>({data:pixels}),putImageData:vi.fn()};
 vi.stubGlobal("document",{createElement:()=>({width:0,height:0,getContext:()=>ctx})});
 const image={src:"custom-resolution-regression",naturalWidth:2048,naturalHeight:1024} as HTMLImageElement;
 const tile=materialTexture(image,"#123456",{} as CanvasRenderingContext2D,10);
 expect([tile.width,tile.height]).toEqual([2048,1024]);
 expect(drawImage).toHaveBeenCalledWith(image,0,0,2048,1024);
 expect(textureOverlay(image,"#123456")).toBe(tile);
 expect([...pixels]).toEqual([18,52,86,255,18,52,86,0]);
});
