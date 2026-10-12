import { describe,it,expect } from "vitest";
import { addContactPointV3, addBasicNodeV3, addLayerV3, groupRootNodesV3, moveNodeV3, newTemplateContentV3, projectTemplateContentV3CoreToV2 } from "./template-commands-v3";
import { stretchDrawingSelection, useInternalDrawingClipboard } from "./drawing-selection";
import { copyDrawingSelection,pasteDrawingSelection,deleteDrawingSelection,moveDrawingSelection,rotateDrawingSelection,styleDrawingSelection,nodesInsideSelectionBox,selectionBounds,drawingKeyboardAction,drawingStyleLeaves } from "./drawing-selection";
import { drawingLayerOutlines } from "./drawing-geometry";
import { validateTemplateContentV3Structure } from "./template-model-v3";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TemplateCanvasV2 } from "./TemplateCanvasV2";

function fixture() {
  let content=newTemplateContentV3();const viewId=content.views[1]!.id,layerId=content.views[1]!.layers[0]!.id;
  let a:string,b:string; [content,a]=addBasicNodeV3(content,viewId,layerId,"rectangle");[content,b]=addBasicNodeV3(content,viewId,layerId,"ellipse");
  content=moveNodeV3(content,viewId,layerId,b,230,90);
  return {content,viewId,layerId,a,b};
}
const evaluate=(expression:any)=>expression.kind==="constant" ? expression.value as number : null;
describe("drawing selection commands",()=>{
  it("pastes the copied figure over a stale image, but honors a later external copy",()=>{
    expect(useInternalDrawingClipboard("","token",false)).toBe(true);
    expect(useInternalDrawingClipboard("token","token",true)).toBe(true);
    expect(useInternalDrawingClipboard("","token",true)).toBe(false);
    expect(useInternalDrawingClipboard("",null,false)).toBe(false);
  });
  it("selects, moves and stretches contacts with rotated figures without changing electrical identity",()=>{
    let {content,viewId,a,b}=fixture();let pointId:string;
    [content,pointId]=addContactPointV3(content,viewId);
    const point=content.views[1]!.contactPoints.find(p=>p.id===pointId)!;
    const ids=[a,b,pointId],bounds=selectionBounds(content.views[1]!,ids,evaluate)!;
    expect(nodesInsideSelectionBox(content.views[1]!,bounds,evaluate)).toContain(pointId);
    const rotated=rotateDrawingSelection(content,viewId,ids,33,{x:0,y:0});
    const result=stretchDrawingSelection(rotated,viewId,ids,2,{x:0,y:0});
    const moved=moveDrawingSelection(result,viewId,ids,10,20);
    expect(moved.logicalContacts).toEqual(content.logicalContacts);
    expect(moved.views[1]!.contactPoints[0]!.logicalContactId).toBe(point.logicalContactId);
    expect(evaluate(moved.views[1]!.contactPoints[0]!.x)).toBeCloseTo(evaluate(rotated.views[1]!.contactPoints[0]!.x)!*2+10);
    expect(evaluate(result.views[1]!.layers[0]!.nodes[0]!.transform.scaleX)).toBe(2);
    expect(validateTemplateContentV3Structure(moved).valid).toBe(true);
    const html=renderToStaticMarkup(createElement(TemplateCanvasV2,{content:projectTemplateContentV3CoreToV2(moved),viewId,selectedId:a,selectedIds:ids,onSelect:()=>{},onSelectionStretch:()=>{},resolveAssetUrl:()=>""}));
    expect(html.match(/data-selection-stretch-handle=/g)).toHaveLength(4);
    expect(html).toContain('data-selected="true"');
  });
  it("requires every repeated occurrence inside the window",()=>{
    const {content,a}=fixture(),view=content.views[1]!,bounds=selectionBounds(view,[a],evaluate)!;
    const repeats=new Map([[a,[{offset:{x:0,y:0}},{offset:{x:400,y:0}}]]]);
    expect(nodesInsideSelectionBox(view,bounds,evaluate,repeats)).not.toContain(a);
    expect(nodesInsideSelectionBox(view,{...bounds,right:bounds.right+400},evaluate,repeats)).toContain(a);
  });
  it("reads and styles group leaves without multiplying opacity twice",()=>{
    let {content,viewId,layerId,a,b}=fixture();let group:string;
    [content,group]=groupRootNodesV3(content,viewId,layerId,[a,b]);
    const styled=styleDrawingSelection(content,viewId,[group],{opacity:0.4,stroke:{color:"#dc2626"}});
    const nodes=styled.views[1]!.layers[0]!.nodes,root=nodes.find(n=>n.id===group)!;
    expect(root.opacity).toBe(0.4);
    const leaves=drawingStyleLeaves([root],nodes);expect(leaves).toHaveLength(2);
    expect(leaves.every(n=>n.opacity===1 && n.stroke.color==="#dc2626")).toBe(true);
  });
  it("copies an immutable snapshot, remaps nested IDs, offsets only roots to upper left",()=>{
    let {content,viewId,layerId,a,b}=fixture(); let group:string;
    [content,group]=groupRootNodesV3(content,viewId,layerId,[a,b]);
    const original=structuredClone(content),clipboard=copyDrawingSelection(content,viewId,[group]);
    const [pasted,ids]=pasteDrawingSelection(content,viewId,layerId,clipboard);
    expect(content).toEqual(original);expect(ids).toHaveLength(1);expect(ids[0]).not.toBe(group);
    const nodes=pasted.views[1]!.layers[0]!.nodes,root=nodes.find(node=>node.id===ids[0])!;
    expect(root.transform.translateX).toEqual({kind:"constant",value:-12});
    expect(root.transform.translateY).toEqual({kind:"constant",value:-12});
    expect(root.kind).toBe("group");
    if(root.kind==="group") {expect(root.geometry.childIds).toHaveLength(2);expect(root.geometry.childIds).not.toContain(a);expect(root.geometry.childIds.every(id=>nodes.some(node=>node.id===id))).toBe(true);}
    expect(validateTemplateContentV3Structure(pasted).valid).toBe(true);
    const deleted=deleteDrawingSelection(pasted,viewId,ids);
    expect(deleted).toEqual(original);
  });
  it("window selects only fully contained roots, ignores hidden and locked objects",()=>{
    const {content,a,b}=fixture(),view=content.views[1]!;
    const bounds=selectionBounds(view,[a],evaluate)!;
    expect(nodesInsideSelectionBox(view,bounds,evaluate)).toEqual([a]);
    expect(nodesInsideSelectionBox(view,{...bounds,right:bounds.right-1},evaluate)).toEqual([]);
    const all=selectionBounds(view,[a,b],evaluate)!;
    expect(nodesInsideSelectionBox(view,all,evaluate)).toEqual([a,b]);
    view.layers[0]!.nodes[0]!.locked=true;
    expect(nodesInsideSelectionBox(view,all,evaluate)).toEqual([b]);
    view.layers[0]!.visible=false;
    expect(nodesInsideSelectionBox(view,all,evaluate)).toEqual([]);
  });
  it("moves and rotates roots across layers as one rigid selection",()=>{
    let {content,viewId,layerId,a,b}=fixture();let second:string;
    [content,second]=addLayerV3(content,viewId,"Второй слой");
    const layer=content.views[1]!.layers.find(layer=>layer.id===second)!;
    const node=content.views[1]!.layers[0]!.nodes.pop()!;node.layerId=second;layer.nodes.push(node);
    const before=content.views[1]!.layers.flatMap(layer=>drawingLayerOutlines(layer.nodes,evaluate));
    const moved=moveDrawingSelection(content,viewId,[a,b],20,-30);
    const bounds=selectionBounds(moved.views[1]!,[a,b],evaluate)!;
    const center={x:(bounds.left+bounds.right)/2,y:(bounds.top+bounds.bottom)/2};
    const rotated=rotateDrawingSelection(moved,viewId,[a,b],90,center);
    const after=rotated.views[1]!.layers.flatMap(layer=>drawingLayerOutlines(layer.nodes,evaluate));
    before.forEach((outline,i)=>outline.points.forEach((p,j)=>{const q=after[i]!.points[j]!;expect(q.x).toBeCloseTo(center.x-(p.y-30-center.y));expect(q.y).toBeCloseTo(center.y+(p.x+20-center.x));}));
    const markup=renderToStaticMarkup(createElement(TemplateCanvasV2,{content:projectTemplateContentV3CoreToV2(rotated),viewId,selectedId:a,selectedIds:[a,b],onSelect:()=>{},onSelectionRotate:()=>{},resolveAssetUrl:()=>""}));
    expect(markup).toContain('data-selection-rotation-handle="true"');
    expect(deleteDrawingSelection(rotated,viewId,[a,b]).views[1]!.layers.flatMap(l=>l.nodes)).toEqual([]);
  });
  it("changes only requested common fields and keeps unrelated hatch/geometry intact",()=>{
    const {content,viewId,a,b}=fixture();
    content.views[1]!.layers[0]!.nodes[0]!.fill={color:"#ffffff",hatch:{kind:"cross",spacing:9,angle:45}};
    const before=structuredClone(content);
    const styled=styleDrawingSelection(content,viewId,[a,b],{opacity:0.35,stroke:{color:"#dc2626"},fill:{color:"#2563eb"}});
    styled.views[1]!.layers[0]!.nodes.forEach((node,i)=>{
      expect(node.opacity).toBe(0.35);expect(node.stroke.color).toBe("#dc2626");expect(node.fill.color).toBe("#2563eb");expect(node.geometry).toEqual(before.views[1]!.layers[0]!.nodes[i]!.geometry);
    });
    expect(styled.views[1]!.layers[0]!.nodes[0]!.fill.hatch).toEqual(before.views[1]!.layers[0]!.nodes[0]!.fill.hatch);
    expect(content).toEqual(before);
    expect(()=>styleDrawingSelection(content,viewId,[a],{opacity:2})).toThrow();
  });
  it("rejects locked descendants atomically and preserves the original",()=>{
    let {content,viewId,layerId,a,b}=fixture();let group:string;
    [content,group]=groupRootNodesV3(content,viewId,layerId,[a,b]);
    content.views[1]!.layers[0]!.nodes[0]!.locked=true;
    const before=structuredClone(content);
    expect(()=>deleteDrawingSelection(content,viewId,[group])).toThrow();
    expect(()=>moveDrawingSelection(content,viewId,[group],20,20)).toThrow();
    expect(content).toEqual(before);
  });
  it("keeps native text clipboard/delete shortcuts inside text fields",()=>{
    const event={key:"c",ctrlKey:true,metaKey:false,altKey:false,shiftKey:false};
    expect(drawingKeyboardAction(event)).toBe("copy");
    expect(drawingKeyboardAction({...event,key:"v"})).toBe("paste");
    expect(drawingKeyboardAction({...event,key:"Delete",ctrlKey:false})).toBe("delete");
    for(const tagName of ["INPUT","TEXTAREA","SELECT"]) expect(drawingKeyboardAction({...event,target:{tagName} as unknown as EventTarget})).toBeNull();
    expect(drawingKeyboardAction({...event,target:{isContentEditable:true} as unknown as EventTarget})).toBeNull();
  });
});
