import {describe,it,expect} from "vitest";
import {boxHandleRadius,drawingHandleRadii,spacedHandleBounds,zoomDrawingCamera,fitDrawingCamera,panDrawingCamera} from "./drawing-viewport";
describe("drawing viewport and compact handles",()=>{
  it.each([{x:-250,y:180,width:100,height:5000},{x:700,y:-100,width:4000,height:20}])("fits displaced tall and wide drawings, then pans in screen pixels",bounds=>{
    const viewport={width:400,height:220}, camera=fitDrawingCamera(bounds,viewport);
    expect((bounds.x-camera.x)*camera.zoom).toBeGreaterThanOrEqual(15.99);
    expect((bounds.y-camera.y)*camera.zoom).toBeGreaterThanOrEqual(15.99);
    expect((bounds.x+bounds.width-camera.x)*camera.zoom).toBeLessThanOrEqual(384.01);
    expect((bounds.y+bounds.height-camera.y)*camera.zoom).toBeLessThanOrEqual(204.01);
    const next=panDrawingCamera(camera,{x:50,y:-30},camera.zoom);
    expect((bounds.x-next.x)*next.zoom-(bounds.x-camera.x)*camera.zoom).toBeCloseTo(50);
    expect((bounds.y-next.y)*next.zoom-(bounds.y-camera.y)*camera.zoom).toBeCloseTo(-30);
    const zoomed=zoomDrawingCamera(camera,{x:bounds.x,y:bounds.y},-120,{min:camera.zoom/20,max:camera.zoom*40});
    expect(zoomed.zoom).toBeGreaterThan(camera.zoom);
    expect((bounds.x-zoomed.x)*zoomed.zoom).toBeCloseTo((bounds.x-camera.x)*camera.zoom);
  });
  it.each([.1,1,8,32])("keeps grips small at camera scale %s and compensates stretched geometry",zoom=>{
    const radii=drawingHandleRadii(zoom,20,.25,1000);
    expect(radii.rx*zoom*20).toBeCloseTo(5);
    expect(radii.ry*zoom*.25).toBeCloseTo(5);
    const tiny=drawingHandleRadii(zoom,20,.25,8/zoom);
    expect(tiny.rx*zoom*20).toBeCloseTo(2);
  });
  it("anchors wheel zoom at the same screen point and clamps extremes",()=>{
    const camera={x:100,y:-40,zoom:2},point={x:160,y:70};
    const next=zoomDrawingCamera(camera,point,-120);
    expect((point.x-next.x)*next.zoom).toBeCloseTo((point.x-camera.x)*camera.zoom);
    expect((point.y-next.y)*next.zoom).toBeCloseTo((point.y-camera.y)*camera.zoom);
    expect(zoomDrawingCamera({...camera,zoom:32},point,-100).zoom).toBe(32);
  });
  it("keeps compact grips on the real contour and shrinks their screen footprint",()=>{
    const compact=spacedHandleBounds(10,10,12,12,.2,.1);
    expect(compact).toMatchObject({left:10,right:12,top:10,bottom:12});
    expect(boxHandleRadius(8,8)).toBe(2);
    expect(boxHandleRadius(100,100)).toBe(5.5);
    expect(compact.compact).toBe(true);
    expect(spacedHandleBounds(0,0,100,90,1)).toEqual({left:0,top:0,right:100,bottom:90,compact:false});
  });
});
