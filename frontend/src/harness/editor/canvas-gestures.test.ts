// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { CanvasViewport, freeWireEndAxes, freeWireEndpointAxis, objectIntersectsSelectionRectangle, selectionRectangleObjectIds, snapFreeWireEndpointX } from "./CanvasViewport";
import { DrawingResizeGrip } from "./DrawingResizeGrip";
import type { EditorSceneObject } from "./editor-types";

vi.mock("./component-template-view-renderer", async importOriginal => ({
  ...await importOriginal<typeof import("./component-template-view-renderer")>(),
  projectComponentTemplateView: vi.fn((_instance: unknown, _view: unknown, origin: {x:number;y:number}) => ({
    objectId: "X", snapshotId: "snapshot", viewId: "drawing", viewKind: "drawing",
    commands: [], bounds: {minX: origin.x, minY: origin.y, maxX: origin.x + 118, maxY: origin.y + 100},
  })),
  projectContactSideView: vi.fn((_instance: unknown, origin: {x:number;y:number}) => ({
    placementId: "view:contact-side", visible: true,
    drawing: {
      objectId: "X", snapshotId: "snapshot", viewId: "contact", viewKind: "drawing",
      commands: [], bounds: {minX: origin.x + 150, minY: origin.y, maxX: origin.x + 250, maxY: origin.y + 100},
    },
    link: [{x: origin.x + 118, y: origin.y + 50}, {x: origin.x + 150, y: origin.y + 50}],
  })),
}));

// Exercise the actual event handlers without a browser renderer. Effects install
// the real Escape listener; pointer capture and native events are modeled below.
const hooks=vi.hoisted(()=>({effects:[] as (()=>unknown)[], states:[] as unknown[], index:0}));
vi.mock("react",async importOriginal=>({
  ...await importOriginal<typeof import("react")>(),
  useRef:(current:unknown)=>({current}),
  useState:(value:unknown)=>{const index=hooks.index++;if(!(index in hooks.states))hooks.states[index]=typeof value==="function"?value():value;return [hooks.states[index],(next:unknown)=>{hooks.states[index]=next;}];},
  useMemo:(factory:()=>unknown)=>factory(),
  useEffect:(effect:()=>unknown)=>{hooks.effects.push(effect);},
}));

const connector:EditorSceneObject={id:"X",kind:"connector",layerId:"connectors",label:"X",x:100,y:80,width:118,height:100,color:"#222",metadata:{view:"e4",orientation:"right",rows:"[]"}};
let keydown:(event:{key:string})=>void;
beforeEach(()=>{
  hooks.effects=[];
  hooks.states=[];hooks.index=0;
  vi.stubGlobal("window",{addEventListener:(type:string,fn:typeof keydown)=>{if(type==="keydown")keydown=fn;},removeEventListener:vi.fn()});
});

it.each([110,500])('isolates the full canvas selection from a right click at x=%s',x=>{
  const isolate=vi.fn(),select=vi.fn(),move=vi.fn();
  const second={...connector,id:'Y',x:300};
  const props={view:'drawing' as const,tool:'select' as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[connector,second],layers:[{id:'connectors',label:'Соединители',visible:true,locked:false}],selectedObjectId:'X',selectedObjectIds:['X','Y'],onObjectsIsolate:isolate,onObjectSelect:select,onObjectMove:move,onCatalogDrop:vi.fn(),onCameraChange:vi.fn()};
  const render=()=>{hooks.index=0;return CanvasViewport(props);};
  const tree=render();
  const canvas=(tree.props as {children:ReactElement[]}).children.find(c=>c?.type==='canvas')!;
  const event={button:2,clientX:x,clientY:90,preventDefault:vi.fn()};
  (canvas.props as any).onPointerDown(event);
  (canvas.props as any).onContextMenu(event);
  expect(select).not.toHaveBeenCalled();
  const findButton=(node:any):any=>{if(!node||typeof node!=='object')return undefined;if(node.type==='button'&&node.props.children==='Изолировать')return node;return [node.props?.children].flat(Infinity).map(findButton).find(Boolean);};
  const button=findButton(render());
  expect(button).toBeDefined();
  button.props.onClick();
  expect(isolate).toHaveBeenCalledExactlyOnceWith(['X','Y']);
  expect(move).not.toHaveBeenCalled();
});

it('does not offer isolation for service geometry alone', () => {
  const node: EditorSceneObject = { ...connector, id: 'N', kind: 'physical-node', label: 'Выход', port: { direction: null } };
  const isolate = vi.fn();
  const props = { view: 'drawing' as const, tool: 'select' as const, camera: { zoom: 1, offsetX: 0, offsetY: 0 }, objects: [node], layers: [{ id: 'connectors', label: 'Соединители', visible: true, locked: false }], selectedObjectId: 'N', selectedObjectIds: ['N'], onObjectsIsolate: isolate, onObjectSelect: vi.fn(), onCatalogDrop: vi.fn(), onCameraChange: vi.fn() };
  const render = () => { hooks.index = 0; return CanvasViewport(props); };
  const tree = render();
  const canvas = (tree.props as { children: ReactElement[] }).children.find(child => child?.type === 'canvas')!;
  (canvas.props as any).onContextMenu({ clientX: 110, clientY: 90, preventDefault: vi.fn() });
  expect(JSON.stringify(render())).not.toContain('Изолировать');
  expect(isolate).not.toHaveBeenCalled();
});
afterEach(()=>vi.unstubAllGlobals());

it("selects every graphic touched by the selection perimeter, including ellipse and text",()=>{
 const line:EditorSceneObject={id:"line",kind:"graphic-line",layerId:"connectors",label:"line",x:0,y:0,width:100,height:0,color:"#222",points:[{x:0,y:0},{x:100,y:0}],metadata:{graphicKind:"line",graphicWidth:"2"}};
 const ellipse:EditorSceneObject={id:"ellipse",kind:"graphic-ellipse",layerId:"connectors",label:"ellipse",x:40,y:20,width:80,height:60,color:"#222",points:[{x:40,y:20},{x:120,y:80}],metadata:{graphicKind:"ellipse",graphicWidth:"2"}};
 const text:EditorSceneObject={id:"text",kind:"graphic-text",layerId:"connectors",label:"Э4",x:150,y:50,width:1,height:1,color:"#222",points:[{x:150,y:50}],metadata:{graphicKind:"text",graphicFontSize:"24"}};
 const selection={start:{x:90,y:-5},end:{x:160,y:55}};
 expect(objectIntersectsSelectionRectangle(line,selection)).toBe(true);
 expect(selectionRectangleObjectIds([line,ellipse,text],[{id:"connectors",label:"Графика",visible:true,locked:false}],selection)).toEqual(["line","ellipse","text"]);
});

it("does not create graphic objects until its first canvas click and completes a line on right click",()=>{
 const create=vi.fn(),props={view:"e4" as const,tool:"graphic-line" as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[],layers:[{id:"connectors",label:"Графика",visible:true,locked:false}],selectedObjectId:null,onObjectSelect:vi.fn(),onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onGraphicCreate:create};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 expect(create).not.toHaveBeenCalled();
 const handlers=()=>{const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==="canvas")!;return canvas.props as Record<string,(event:any)=>void>;};
 const target={style:{cursor:""},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 handlers().onPointerDown!({button:0,pointerId:1,clientX:20,clientY:30,currentTarget:target,preventDefault:vi.fn()});
 expect(create).not.toHaveBeenCalled();
 handlers().onPointerDown!({button:0,pointerId:1,clientX:80,clientY:30,currentTarget:target,preventDefault:vi.fn()});
 expect(create).not.toHaveBeenCalled();
 handlers().onPointerDown!({button:2,pointerId:1,clientX:80,clientY:30,currentTarget:target,preventDefault:vi.fn()});
 expect(create).not.toHaveBeenCalled();
 handlers().onContextMenu!({clientX:80,clientY:30,currentTarget:target,preventDefault:vi.fn()});
 expect(create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({kind:"line",points:[{x:20,y:30},{x:80,y:30}]}));
});

it("clears an unfinished one-point line on right click and closes a later path at its first point",()=>{
 const create=vi.fn(),props={view:"e4" as const,tool:"graphic-line" as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[],layers:[{id:"connectors",label:"Графика",visible:true,locked:false}],selectedObjectId:null,onObjectSelect:vi.fn(),onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onGraphicCreate:create};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 const handlers=()=>{const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==="canvas")!;return canvas.props as Record<string,(event:any)=>void>;},target={style:{cursor:""},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 handlers().onPointerDown!({button:0,pointerId:1,clientX:10,clientY:10,currentTarget:target,preventDefault:vi.fn()});
 handlers().onContextMenu!({clientX:10,clientY:10,currentTarget:target,preventDefault:vi.fn()});
 expect(create).not.toHaveBeenCalled();
 handlers().onPointerDown!({button:0,pointerId:1,clientX:20,clientY:20,currentTarget:target,preventDefault:vi.fn()});
 handlers().onPointerDown!({button:0,pointerId:1,clientX:80,clientY:20,currentTarget:target,preventDefault:vi.fn()});
 handlers().onPointerDown!({button:0,pointerId:1,clientX:20,clientY:20,currentTarget:target,preventDefault:vi.fn()});
 expect(create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({kind:"closedContour",points:[{x:20,y:20},{x:80,y:20},{x:20,y:20}]}));
});

it("creates a rectangle only on its second click",()=>{
 const create=vi.fn(),props={view:"e4" as const,tool:"graphic-rectangle" as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[],layers:[{id:"connectors",label:"Графика",visible:true,locked:false}],selectedObjectId:null,onObjectSelect:vi.fn(),onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onGraphicCreate:create};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 const handlers=()=>{const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==="canvas")!;return canvas.props as Record<string,(event:any)=>void>;},target={style:{cursor:""},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 handlers().onPointerDown!({button:0,pointerId:1,clientX:20,clientY:30,currentTarget:target,preventDefault:vi.fn()});
 handlers().onPointerMove!({pointerId:1,clientX:80,clientY:90,currentTarget:target,preventDefault:vi.fn()});
 expect(create).not.toHaveBeenCalled();
 handlers().onPointerDown!({button:0,pointerId:1,clientX:80,clientY:90,currentTarget:target,preventDefault:vi.fn()});
 expect(create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({kind:"rectangle",points:[{x:20,y:30},{x:80,y:90}]}));
});

it("applies additive and toggle modes when selecting by a frame",()=>{
 const selectGroup=vi.fn(),line:EditorSceneObject={id:"line",kind:"graphic-line",layerId:"connectors",label:"line",x:20,y:20,width:50,height:0,color:"#222",points:[{x:20,y:20},{x:70,y:20}],metadata:{graphicKind:"line",graphicWidth:"2"}};
 const props={view:"e4" as const,tool:"select" as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[line],layers:[{id:"connectors",label:"Графика",visible:true,locked:false}],selectedObjectId:null,onObjectSelect:vi.fn(),onObjectGroupSelect:selectGroup,onCatalogDrop:vi.fn(),onCameraChange:vi.fn()};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==="canvas")!,handlers=canvas.props as Record<string,(event:any)=>void>,target={style:{cursor:""},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 const event={button:0,pointerId:1,clientX:0,clientY:0,currentTarget:target,preventDefault:vi.fn()};
 handlers.onPointerDown!({...event,shiftKey:true});handlers.onPointerMove!({...event,clientX:90,clientY:40,shiftKey:true});handlers.onPointerUp!({...event,clientX:90,clientY:40,shiftKey:true});
 handlers.onPointerDown!({...event,pointerId:2,ctrlKey:true});handlers.onPointerMove!({...event,pointerId:2,clientX:90,clientY:40,ctrlKey:true});handlers.onPointerUp!({...event,pointerId:2,clientX:90,clientY:40,ctrlKey:true});
 expect(selectGroup).toHaveBeenNthCalledWith(1,["line"],"add");
 expect(selectGroup).toHaveBeenNthCalledWith(2,["line"],"toggle");
});

it("stretches a Ctrl-constrained circle from its clicked center",()=>{
 const create=vi.fn(),props={view:"e4" as const,tool:"graphic-ellipse" as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[],layers:[{id:"connectors",label:"Графика",visible:true,locked:false}],selectedObjectId:null,onObjectSelect:vi.fn(),onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onGraphicCreate:create};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 const handlers=()=>{const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==="canvas")!;return canvas.props as Record<string,(event:any)=>void>;},target={style:{cursor:""},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 handlers().onPointerDown!({button:0,pointerId:1,clientX:50,clientY:50,currentTarget:target,preventDefault:vi.fn()});
 handlers().onPointerDown!({button:0,pointerId:1,clientX:80,clientY:90,ctrlKey:true,currentTarget:target,preventDefault:vi.fn()});
 const graphic=create.mock.calls[0]![0];expect(graphic.kind).toBe("ellipse");expect(Math.abs(graphic.points[1].x-graphic.points[0].x)).toBeCloseTo(Math.abs(graphic.points[1].y-graphic.points[0].y));
});

it('opens the paired wire menu at its free endpoint despite a truncated painted stroke and overlapping dimension',()=>{
 const action=vi.fn(),select=vi.fn();
 const wire:EditorSceneObject={id:'W1',kind:'wire',layerId:'wires',label:'W1',x:0,y:0,width:0,height:0,color:'#222',points:[{x:20,y:50},{x:200,y:50}],visibleWireStrokes:[{points:[{x:20,y:50},{x:150,y:50}],width:2}],metadata:{detachedDrawing:'true',freeTo:'true',drawingPairId:'pair',drawingPairWireIds:JSON.stringify(['W1','W2'])}};
 const other:EditorSceneObject={...wire,id:'W2',points:[{x:20,y:70},{x:200,y:70}],visibleWireStrokes:[]};
 const dimension:EditorSceneObject={id:'D',kind:'dimension',layerId:'dimensions',label:'180',x:190,y:40,width:20,height:20,color:'#333',points:[{x:190,y:50},{x:210,y:50}]};
 const props={view:'drawing' as const,tool:'select' as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[wire,other,dimension],layers:[{id:'dimensions',label:'Размеры',visible:true,locked:false},{id:'wires',label:'Провода',visible:true,locked:false}],selectedObjectId:'W1',selectedObjectIds:['W1'],onObjectSelect:select,onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onDetachedPairAction:action};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==='canvas')!;
 (canvas.props as any).onContextMenu({clientX:200,clientY:50,preventDefault:vi.fn()});
 const find=(node:any):any=>{if(!node||typeof node!=='object')return undefined;if(node.type==='button'&&node.props.children==='Распрямить пару')return node;return [node.props?.children].flat(Infinity).map(find).find(Boolean);};
 const button=find(render());
 expect(button).toBeDefined();button.props.onClick();
 expect(action).toHaveBeenCalledExactlyOnceWith(['W1','W2'],'straighten');
 expect(select).not.toHaveBeenCalled();
});

it("selects a dimension before an overlapping pipe gesture",()=>{
 const select=vi.fn(),move=vi.fn();
 const pipe:EditorSceneObject={id:"P",kind:"physical-segment",layerId:"wires",label:"P",x:0,y:0,width:20,height:0,color:"#333",points:[{x:0,y:50},{x:200,y:50}],pipe:{controls:[{x:0,y:50},{x:200,y:50}],handles:[],midpoints:[],wireIds:[]}};
 const dimension:EditorSceneObject={id:"D",kind:"dimension",layerId:"dimensions",label:"200 мм",x:100,y:43,width:0,height:0,color:"#333",points:[{x:0,y:50},{x:0,y:50},{x:200,y:50},{x:200,y:50}],metadata:{boundDimension:"true"}};
 const props={view:"drawing" as const,tool:"select" as const,camera:{zoom:1,offsetX:0,offsetY:0},objects:[pipe,dimension],layers:[{id:"dimensions",label:"Размеры",visible:true,locked:false},{id:"wires",label:"Провода",visible:true,locked:false}],selectedObjectId:null,onObjectSelect:select,onObjectMove:move,onCatalogDrop:vi.fn(),onCameraChange:vi.fn()};
 const render=()=>{hooks.index=0;return CanvasViewport(props);};
 const canvas=(render().props as {children:ReactElement[]}).children.find(c=>c?.type==="canvas")!;
 const target={style:{cursor:""},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 (canvas.props as any).onPointerDown({button:0,pointerId:1,clientX:100,clientY:50,currentTarget:target,preventDefault:vi.fn()});
 expect(select).toHaveBeenCalledWith("D",undefined);
 expect(target.setPointerCapture).toHaveBeenCalled();
});

it.each([false,true])('inserts a detached midpoint with independent anchors and Escape=%s rollback',cancel=>{
 const move=vi.fn(),preview=vi.fn();
 const points=[{x:20,y:50},{x:200,y:50}];
 const wire:EditorSceneObject={id:'W',kind:'wire',layerId:'wires',label:'W',x:0,y:0,width:0,height:0,color:'#222',points,metadata:{detachedDrawing:'true'}};
 const tree=CanvasViewport({view:'drawing',tool:'select',camera:{zoom:1,offsetX:0,offsetY:0},objects:[wire],layers:[{id:'wires',label:'Провода',visible:true,locked:false}],selectedObjectId:'W',onObjectSelect:vi.fn(),onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onWireRoutePointMove:move,onWireRoutePointPreview:preview});
 hooks.effects.forEach(effect=>effect());
 const canvas=(tree.props as {children:ReactElement[]}).children.find(c=>c?.type==='canvas')!;
 const h=canvas.props as Record<string,(event:any)=>void>;
 const target={style:{cursor:''},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 const e={pointerId:1,button:0,clientX:110,clientY:50,currentTarget:target,preventDefault:vi.fn()};
 h.onPointerDown!(e);h.onPointerMove!({...e,clientY:80});
 expect(preview).toHaveBeenLastCalledWith('W',0,{x:110,y:80},'adjacent',true);
 if(cancel)keydown({key:'Escape'});
 h.onPointerUp!({...e,clientY:80});
 if(cancel)expect(move).not.toHaveBeenCalled();else expect(move).toHaveBeenCalledExactlyOnceWith('W',0,{x:110,y:80},'adjacent',true);
 expect(points).toEqual([{x:20,y:50},{x:200,y:50}]);
});

it.each([true,false])('only drags explicitly free drawing endpoints (free=%s)',free=>{
 const move=vi.fn(),preview=vi.fn();
 const wire:EditorSceneObject={id:'W',kind:'wire',layerId:'wires',label:'W',x:0,y:0,width:0,height:0,color:'#222',points:[{x:20,y:50},{x:200,y:50}],metadata:{freeFrom:String(free)}};
 const tree=CanvasViewport({view:'drawing',tool:'select',camera:{zoom:1,offsetX:0,offsetY:0},objects:[wire],layers:[{id:'wires',label:'Провода',visible:true,locked:false}],selectedObjectId:'W',onObjectSelect:vi.fn(),onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onFreeWireEndpointMove:move,onFreeWireEndpointPreview:preview});
 const canvas=(tree.props as {children:ReactElement[]}).children.find(c=>c?.type==='canvas')!;
 const h=canvas.props as Record<string,(event:any)=>void>;
 const target={style:{cursor:''},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()};
 const e={pointerId:1,button:0,clientX:20,clientY:50,currentTarget:target,preventDefault:vi.fn()};
 h.onPointerDown!(e);h.onPointerMove!({...e,clientX:35,clientY:70});h.onPointerUp!({...e,clientX:35,clientY:70});
 if(free){expect(move).toHaveBeenCalledExactlyOnceWith('W','from',{x:35,y:70});expect(preview).toHaveBeenLastCalledWith('W','from',null);}else expect(move).not.toHaveBeenCalled();
});

it("aligns compatible selected free ends on one vertical axis while preserving Y", () => {
  const refs = [
    { wireId: "W1", end: "from" as const, point: { x: 100, y: 30 } },
    { wireId: "W2", end: "from" as const, point: { x: 102, y: 90 } },
  ];
  expect(freeWireEndpointAxis(refs)).toBe(101);
  expect(freeWireEndAxes(refs)[0]).toMatchObject({ end: "from", wireIds: ["W1", "W2"], minY: 30, maxY: 90 });
  expect(snapFreeWireEndpointX({ x: 106, y: 30 }, refs, "W1", "from", 8)).toEqual({ x: 102, y: 30 });
  expect(snapFreeWireEndpointX({ x: 140, y: 30 }, refs, "W1", "from", 8)).toEqual({ x: 140, y: 30 });
});

it("drags the shared vertical handle with one atomic X update", () => {
  const move = vi.fn(), moveGroup = vi.fn();
  const wires: EditorSceneObject[] = [30, 90].map((y, index) => ({
    id: `W${index + 1}`, kind: "wire", layerId: "wires", label: `W${index + 1}`,
    x: 0, y: 0, width: 0, height: 0, color: "#222",
    points: [{ x: 20, y }, { x: 100, y }], metadata: { freeTo: "true", detachedDrawing: "true" },
  }));
  const tree = CanvasViewport({ view: "drawing", tool: "select", camera: { zoom: 1, offsetX: 0, offsetY: 0 },
    objects: wires, layers: [{ id: "wires", label: "Провода", visible: true, locked: false }],
    selectedObjectId: "W2", selectedObjectIds: ["W1", "W2"], onObjectSelect: vi.fn(),
    onCatalogDrop: vi.fn(), onCameraChange: vi.fn(), onFreeWireEndpointMove: move,
    onFreeWireEndpointsXChange: moveGroup });
  const svg = (tree.props as { children: ReactElement[] }).children.find(child => child?.type === "svg" && (child as any).props["aria-label"] === "Свободные концы проводов")!;
  expect(JSON.stringify(svg)).toContain('strokeDasharray');
  const canvas = (tree.props as { children: ReactElement[] }).children.find(child => child?.type === "canvas")!;
  const handlers = canvas.props as Record<string, (event: any) => void>;
  const target = { setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn() };
  const event = { pointerId: 1, button: 0, clientX: 100, clientY: 60, currentTarget: target, preventDefault: vi.fn() };
  handlers.onPointerDown!(event);
  handlers.onPointerUp!({ ...event, clientX: 170 });
  expect(moveGroup).toHaveBeenCalledExactlyOnceWith(["W1", "W2"], "to", 170);
  expect(move).not.toHaveBeenCalled();
});

it('drags the contact-side companion independently at the canvas zoom',()=>{
  const move=vi.fn(),select=vi.fn();
  const placement={drawingId:'view:contact-side',visible:true,offset:{x:150,y:0},scale:1};
  const instance={objectId:'X',snapshotId:'snapshot',articleVariantId:'variant',content:{} as never,drawingPlacements:[placement]};
  const object={...connector,x:100,y:80};
  const props={view:'drawing' as const,tool:'select' as const,camera:{zoom:2,offsetX:0,offsetY:0},objects:[object],layers:[{id:'connectors',label:'Соединители',visible:true,locked:false}],selectedObjectId:'X',componentTemplateViewInstances:[instance],onObjectSelect:select,onDrawingMove:move,onCatalogDrop:vi.fn(),onCameraChange:vi.fn()};
  const tree=CanvasViewport(props);
  const canvas=(tree.props as {children:ReactElement[]}).children.find(c=>c?.type==='canvas')!;
  const handlers=canvas.props as Record<string,(event:any)=>void>;
  let captured=false;
  const target={setPointerCapture:vi.fn(()=>{captured=true;}),hasPointerCapture:()=>captured,releasePointerCapture:vi.fn(()=>{captured=false;})};
  const start={pointerId:7,button:0,clientX:550,clientY:170,currentTarget:target,preventDefault:vi.fn()};
  handlers.onPointerDown!(start);
  handlers.onPointerMove!({...start,clientX:590,clientY:210});
  handlers.onPointerUp!({...start,clientX:590,clientY:210});
  expect(target.setPointerCapture).toHaveBeenCalledWith(7);
  expect(move).toHaveBeenCalledExactlyOnceWith('X','view:contact-side',{x:170,y:20});
  expect(object.x).toBe(100);
  expect(object.y).toBe(80);
});

it('routes the contact-side resize grip to its reserved placement only',()=>{
  const scale=vi.fn();
  const placement={drawingId:'view:contact-side',visible:true,offset:{x:150,y:0},scale:1};
  const instance={objectId:'X',snapshotId:'snapshot',articleVariantId:'variant',content:{} as never,drawingPlacements:[placement]};
  const tree=CanvasViewport({view:'drawing',tool:'select',camera:{zoom:1,offsetX:0,offsetY:0},objects:[connector],layers:[{id:'connectors',label:'Соединители',visible:true,locked:false}],selectedObjectId:'X',componentTemplateViewInstances:[instance],onObjectSelect:vi.fn(),onDrawingScale:scale,onCatalogDrop:vi.fn(),onCameraChange:vi.fn()});
  const find=(node:any):any[]=>!node||typeof node!=='object'?[]:node.type===DrawingResizeGrip?[node]:[node.props?.children].flat(Infinity).flatMap(find);
  const grip=find(tree).find(candidate=>candidate.props.x===350);
  expect(grip).toBeDefined();
  expect(grip.props.scale).toBe(1);
  grip.props.commit(1.5);
  expect(scale).toHaveBeenCalledExactlyOnceWith('X','view:contact-side',1.5);
});

function fixture(){
  const move=vi.fn(),preview=vi.fn();
  const tree=CanvasViewport({view:"e4",tool:"select",camera:{zoom:1,offsetX:0,offsetY:0},objects:[connector],layers:[{id:"connectors",label:"Соединители",visible:true,locked:false}],selectedObjectId:"X",inlineEditor:"Table",onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onObjectSelect:vi.fn(),onObjectMove:move,onObjectMovePreview:preview});
  hooks.effects.forEach(effect=>effect());
  const children=(tree.props as {children:ReactElement[]}).children.filter(Boolean);
  const inline=children.find(c=>(c.props as {className?:string}).className?.startsWith("he-e4-inline-editor"))!;
  const canvas=children.find(c=>c.type==="canvas")!;
  type Handlers=Record<string,(event:unknown)=>void>;
  const handlers=inline.props as Handlers,canvasHandlers=canvas.props as Handlers;
  let captured=false;
  const currentTarget={setPointerCapture:()=>{captured=true;},hasPointerCapture:()=>captured,releasePointerCapture:vi.fn(()=>{captured=false;})};
  const event=(shiftKey=false,x=100,y=90)=>({pointerId:1,button:0,detail:1,clientX:x,clientY:y,shiftKey,currentTarget,target:{closest:(selector:string)=>selector.includes("is-readonly")?{}:null},stopPropagation:vi.fn(),preventDefault:vi.fn()});
  return {handlers,canvasHandlers,event,move,preview,currentTarget};
}

it.each([false,true])("passes the initial Shift=%s mode through inline preview and commit",shift=>{
  const f=fixture();
  f.handlers.onPointerDown!(f.event(shift));
  f.handlers.onPointerMove!(f.event(!shift,130,110));
  expect(f.preview).toHaveBeenLastCalledWith("X",{x:130,y:100},shift?"adjacent":"carry");
  f.handlers.onPointerUp!(f.event(!shift,130,110));
  expect(f.move).toHaveBeenCalledExactlyOnceWith("X",{x:130,y:100},shift?"adjacent":"carry");
  f.handlers.onLostPointerCapture!(f.event());
  expect(f.move).toHaveBeenCalledTimes(1);
});

it.each(["Escape","onPointerCancel","onLostPointerCapture"])("cancels inline preview on %s without a later commit",cancel=>{
  const f=fixture();
  f.handlers.onPointerDown!(f.event(true));
  f.handlers.onPointerMove!(f.event(true,130,110));
  if(cancel==="Escape")keydown({key:"Escape"});else f.handlers[cancel]!(f.event());
  expect(f.preview).toHaveBeenLastCalledWith("X",null);
  expect(f.currentTarget.releasePointerCapture).toHaveBeenCalledTimes(1);
  f.handlers.onPointerUp!(f.event(true,150,130));
  expect(f.move).not.toHaveBeenCalled();
});

it("cancels Canvas object capture loss and ignores its later pointerup",()=>{
  const f=fixture();
  f.canvasHandlers.onPointerDown!(f.event(false,110,90));
  f.canvasHandlers.onPointerMove!(f.event(false,140,110));
  f.canvasHandlers.onLostPointerCapture!(f.event());
  expect(f.preview).toHaveBeenLastCalledWith("X",null);
  f.canvasHandlers.onPointerUp!(f.event(false,160,130));
  expect(f.move).not.toHaveBeenCalled();
});

it('picks a bundle participant without creating a pipe drag or changing selection',()=>{
 const pick=vi.fn(),move=vi.fn(),select=vi.fn(),double=vi.fn(),cancel=vi.fn();
 const pipe:EditorSceneObject={id:'pipe',kind:'physical-segment',layerId:'pipes',label:'S1',color:'#333333',x:0,y:0,width:10,height:0,points:[{x:20,y:50},{x:300,y:50}]};
 const tree=CanvasViewport({view:'drawing',tool:'select',camera:{zoom:1,offsetX:0,offsetY:0},objects:[pipe],layers:[{id:'pipes',label:'Pipes',visible:true,locked:false}],selectedObjectId:null,
  onCatalogDrop:vi.fn(),onCameraChange:vi.fn(),onObjectSelect:select,onObjectMove:move,onObjectPick:pick,onObjectPickCancel:cancel,onCanvasDoubleClick:double});
 const canvas=(tree.props as {children:ReactElement[]}).children.find(c=>c?.type==='canvas')!;
 const handlers=canvas.props as Record<string,(event:any)=>void>,capture=vi.fn();
 const e={pointerId:1,button:0,clientX:100,clientY:50,currentTarget:{setPointerCapture:capture},preventDefault:vi.fn(),stopPropagation:vi.fn()};
 handlers.onPointerDown!(e);handlers.onDoubleClick!(e);
 expect(pick).toHaveBeenCalledExactlyOnceWith('pipe');expect(capture).not.toHaveBeenCalled();expect(select).not.toHaveBeenCalled();expect(move).not.toHaveBeenCalled();expect(double).not.toHaveBeenCalled();
 handlers.onKeyDown!({...e,key:'Escape'});expect(cancel).toHaveBeenCalledOnce();
});

it.each([false, true])("view mode=%s: a press on a connector selects it, a drag moves it only outside view mode", readOnly => {
  const move = vi.fn(), preview = vi.fn(), select = vi.fn();
  const object = { ...connector, x: 100, y: 80 };
  const props = { view: "e4" as const, tool: "select" as const, camera: { zoom: 1, offsetX: 0, offsetY: 0 }, objects: [object],
    layers: [{ id: "connectors", label: "Соединители", visible: true, locked: false }], selectedObjectId: null,
    onObjectSelect: select, onObjectMove: move, onObjectMovePreview: preview, onCatalogDrop: vi.fn(), onCameraChange: vi.fn(), readOnly };
  const render = () => { hooks.index = 0; return CanvasViewport(props); };
  const canvas = (render().props as { children: ReactElement[] }).children.find(child => child?.type === "canvas")!;
  const handlers = canvas.props as Record<string, (event: any) => void>;
  let captured = false;
  const target = { style: {}, setPointerCapture: vi.fn(() => { captured = true; }), hasPointerCapture: () => captured, releasePointerCapture: vi.fn(() => { captured = false; }) };
  const start = { pointerId: 3, button: 0, clientX: 150, clientY: 120, currentTarget: target, preventDefault: vi.fn() };
  handlers.onPointerDown!(start);
  handlers.onPointerMove!({ ...start, clientX: 250, clientY: 220 });
  handlers.onPointerUp!({ ...start, clientX: 250, clientY: 220 });
  expect(select).toHaveBeenCalledWith("X", false);
  if (readOnly) { expect(move).not.toHaveBeenCalled(); expect(preview).not.toHaveBeenCalled(); }
  else expect(move).toHaveBeenCalled();
});
