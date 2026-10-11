export interface DrawingCamera { x:number; y:number; zoom:number }
/** Fixed-size point grips, compensated for camera and object scale. */
export function drawingHandleRadii(screenScale:number, scaleX:number, scaleY:number, nearestWorldDistance=Infinity) {
  const pixels=Math.max(2,Math.min(5,nearestWorldDistance*screenScale/4));
  return {rx:pixels/Math.max(.0001,screenScale*Math.abs(scaleX)),ry:pixels/Math.max(.0001,screenScale*Math.abs(scaleY))};
}
export function zoomDrawingCamera(camera:DrawingCamera, point:{x:number;y:number}, delta:number, limits={min:.1,max:32}):DrawingCamera {
  const zoom=Math.max(limits.min,Math.min(limits.max,camera.zoom*Math.exp(-Math.max(-300,Math.min(300,delta))*.002)));
  return {zoom,x:point.x-(point.x-camera.x)*camera.zoom/zoom,y:point.y-(point.y-camera.y)*camera.zoom/zoom};
}

/** Fit rendered geometry (including repeated/transformed objects) with screen-space padding. */
export function fitDrawingCamera(bounds:{x:number;y:number;width:number;height:number}, viewport:{width:number;height:number}, padding=16):DrawingCamera {
  if (![bounds.x,bounds.y,bounds.width,bounds.height,viewport.width,viewport.height].every(Number.isFinite) || viewport.width<=0 || viewport.height<=0)
    return {x:0,y:0,zoom:1};
  const zoom=Math.min(Math.max(1,viewport.width-2*padding)/Math.max(1,bounds.width),Math.max(1,viewport.height-2*padding)/Math.max(1,bounds.height));
  return {zoom,x:bounds.x+bounds.width/2-viewport.width/(2*zoom),y:bounds.y+bounds.height/2-viewport.height/(2*zoom)};
}

export function panDrawingCamera(camera:DrawingCamera, delta:{x:number;y:number}, screenScale:number):DrawingCamera {
  return screenScale>0 && Number.isFinite(screenScale)
    ? {...camera,x:camera.x-delta.x/screenScale,y:camera.y-delta.y/screenScale}
    : camera;
}
/** Keep grip anchors on the contour; callers shrink handles for compact geometry. */
export function spacedHandleBounds(left:number,top:number,right:number,bottom:number,pixelsX:number,pixelsY=pixelsX) {
  return {left:Math.min(left,right),right:Math.max(left,right),top:Math.min(top,bottom),bottom:Math.max(top,bottom),compact:Math.abs(right-left)*pixelsX<56||Math.abs(bottom-top)*pixelsY<56};
}

/** Small figures need smaller grips, not a larger invisible selection frame. */
export function boxHandleRadius(widthPixels:number,heightPixels:number):number {
  return Math.max(.5, Math.min(5.5,Math.abs(widthPixels)/4,Math.abs(heightPixels)/4));
}
