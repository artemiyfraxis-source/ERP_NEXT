// @vitest-environment node
import {expect,it} from "vitest";
import {textureOverlayPixels} from "./texture-tint";

it.each(["#000000","#ffffff","#16a34a","#dc2626"])("keeps background separate from the %s texture colour",tint=>{
 const pixels=new Uint8ClampedArray([0,0,0,255,128,128,128,255,255,255,255,255,0,0,0,0]);
 textureOverlayPixels(pixels,tint);
 const rgb=[1,3,5].map(i=>parseInt(tint.slice(i,i+2),16));
 expect([...pixels.slice(0,3)]).toEqual(rgb);
 expect(pixels[3]).toBe(255);expect(pixels[7]).toBeGreaterThan(0);expect(pixels[7]).toBeLessThan(255);
 expect(pixels[11]).toBe(0);expect(pixels[15]).toBe(0);
 const composite=(bg:number,index:number)=>Math.round(pixels[index]!*(pixels[index+3]!/255)+bg*(1-pixels[index+3]!/255));
 expect(composite(25,8)).toBe(25);expect(composite(210,8)).toBe(210);
 expect(composite(25,4)).not.toBe(composite(210,4));
});

it("preserves partial source transparency when extracting the pattern",()=>{
 const pixels=new Uint8ClampedArray([0,0,0,71,255,255,255,71]);
 textureOverlayPixels(pixels,"#123456");
 expect([...pixels]).toEqual([18,52,86,71,18,52,86,0]);
});
