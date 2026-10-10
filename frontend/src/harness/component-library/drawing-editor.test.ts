import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { addBasicNodeV3, newTemplateContentV3, setRootNodeRotationAroundCenterV3, rootNodeRotationCenterV3, resizeNodeV3, constantExpressionV3 as c, addArticleVariantsV3, addContactTypeGroupV3, setArticleVariantContactGroupV3, addContactPointV3, editContactPointV3, projectTemplateContentV3CoreToV2 } from "./template-commands-v3";
import { drawingOutline, circleTangents, snapDrawingPoint, snapDrawingResizeDelta, snapDrawingTranslation } from "./drawing-geometry";
import { TemplateCanvasV2, templateDeltaToNodeDeltaV2 } from "./TemplateCanvasV2";
import { drawingSelection, drawingContactContent } from "./drawing-bindings";
import { createE4ConnectorSeriesTableFromV3 } from "./e4-connector-series-table";
import { createTemplateContentV5FromEditor, validateTemplateContentV5 } from "./template-model-v5";
import { createConnectorInstanceFromComponentTemplateV3 } from "../editor/component-template-placement";
import { projectComponentTemplateView } from "../editor/component-template-view-renderer";

const evaluate = (value: {kind:string;value?:number}) => value.kind === "constant" ? value.value! : null;
const off = {corners:false,contours:false,tangents:false};

describe("M4-15 drawing editor", () => {
  it.each(["rectangle","ellipse"] as const)("matches the %s pick contour and grips to tiny rotated/scaled geometry", kind=>{
    let core=newTemplateContentV3(); const view=core.views[1]!,layer=view.layers[0]!;
    let id:string; [core,id]=addBasicNodeV3(core,view.id,layer.id,kind);
    const node=core.views[1]!.layers[0]!.nodes[0]!;
    node.stroke.width=c(.5);
    node.transform={translateX:c(20),translateY:c(30),rotationDegrees:c(15),scaleX:c(.1),scaleY:c(.1)};
    if(node.kind==="rectangle")node.geometry={x:c(0),y:c(0),width:c(8),height:c(8),cornerRadii:[c(0),c(0),c(0),c(0)]};
    if(node.kind==="ellipse")node.geometry={centerX:c(4),centerY:c(4),radiusX:c(4),radiusY:c(4)};
    const html=renderToStaticMarkup(createElement(TemplateCanvasV2,{content:projectTemplateContentV3CoreToV2(core),viewId:view.id,selectedId:id,onSelect:()=>{},onNodeResize:()=>{},resolveAssetUrl:()=>""}));
    const pick=html.match(/<(?:path|ellipse) data-shape-hit-region="true"[^>]*>/)![0];
    expect(pick).toContain('stroke-width="0.5"');
    const overlay=html.slice(html.indexOf('data-selection-kind="box"'));
    expect(overlay).toContain('<rect x="0" y="0" width="8" height="8"');
    expect(overlay).toMatch(/cx="0" cy="0"[^>]+data-resize-handle="nw"/);
    expect(overlay).toMatch(/cx="8" cy="8"[^>]+data-resize-handle="se"/);
  });
  it("aligns a long moving edge to a shorter target segment and honors disabled contours",()=>{
    const moving={id:"long",closed:false,points:[{x:0,y:0},{x:100,y:0}]};
    const target={id:"short",closed:false,points:[{x:40,y:12},{x:60,y:12}]};
    expect(snapDrawingTranslation(moving,{x:0,y:10},[target],{...off,contours:true},3)).toEqual({x:0,y:12});
    expect(snapDrawingTranslation(moving,{x:0,y:10},[target],off,3)).toEqual({x:0,y:10});
    const point={id:"contact",closed:false,points:[{x:102,y:10}]};
    expect(snapDrawingTranslation(moving,{x:0,y:10},[point],{...off,corners:true},3)).toEqual({x:2,y:10});
  });
  it("snaps both endpoints of a resized vertical edge", () => {
    const moving={id:"moving",closed:true,points:[{x:0,y:0},{x:20,y:0},{x:20,y:20},{x:0,y:20}]};
    const target={id:"target",closed:true,points:[{x:40,y:20},{x:60,y:20},{x:60,y:50},{x:40,y:50}]};
    expect(snapDrawingResizeDelta(moving,{x:20,y:10},"e",[target],{...off,contours:true},2)).toEqual({x:20,y:10});
    expect(snapDrawingResizeDelta(moving,{x:19,y:10},"e",[target],{...off,contours:true},2)).toEqual({x:20,y:10});
  });
  it("aligns touching rectangles on both axes, even with an already aligned horizontal edge (C1)", () => {
    const upper={id:"upper",closed:true,points:[{x:0,y:0},{x:100,y:0},{x:100,y:50},{x:0,y:50}]};
    const lower={id:"lower",closed:true,points:[{x:0,y:50},{x:98,y:50},{x:98,y:100},{x:0,y:100}]};
    expect(snapDrawingResizeDelta(lower,{x:0,y:0},"e",[upper],{...off,contours:true},3)).toEqual({x:2,y:0});
    expect(snapDrawingTranslation(lower,{x:0,y:0},[upper],{...off,contours:true},3)).toEqual({x:2,y:0});
    expect(snapDrawingResizeDelta(lower,{x:0,y:0},"e",[upper],off,3)).toEqual({x:0,y:0});
    const transpose=(o:typeof lower)=>({...o,points:o.points.map(p=>({x:p.y,y:p.x}))});
    expect(snapDrawingTranslation(transpose(lower),{x:0,y:0},[transpose(upper)],{...off,contours:true},3)).toEqual({x:0,y:2});
  });
  it.each([13,45,90,137,270])("resizes rotated rectangle at %s° with fixed opposite corner", angle => {
    let content = newTemplateContentV3(); const view=content.views[1]!,layer=view.layers[0]!;
    let id:string; [content,id] = addBasicNodeV3(content,view.id,layer.id,"rectangle");
    content=setRootNodeRotationAroundCenterV3(content,view.id,layer.id,id,angle);
    const before=content.views[1]!.layers[0]!.nodes[0]!;
    const original=drawingOutline(before,evaluate)!;
    const delta=templateDeltaToNodeDeltaV2(30,20,angle,1,1)!;
    content=resizeNodeV3(content,view.id,layer.id,id,"se",delta.deltaX,delta.deltaY);
    const after=drawingOutline(content.views[1]!.layers[0]!.nodes[0]!,evaluate)!;
    expect(after.points[0]!.x).toBeCloseTo(original.points[0]!.x);
    expect(after.points[0]!.y).toBeCloseTo(original.points[0]!.y);
    expect(after.points[2]!.x-original.points[2]!.x).toBeCloseTo(30);
    expect(after.points[2]!.y-original.points[2]!.y).toBeCloseTo(20);
    const markup=renderToStaticMarkup(createElement(TemplateCanvasV2,{content:projectTemplateContentV3CoreToV2(content),viewId:view.id,selectedId:id,onSelect:()=>{},onNodeResize:()=>{},onNodeRotate:()=>{},resolveAssetUrl:()=>""}));
    expect(markup).toContain('data-resize-handle="se"');
    expect(markup).toContain('data-rotation-handle="true"');
  });

  it.each(["rectangle","ellipse","closedContour","bezier"] as const)("keeps %s geometric center fixed when rotating", kind => {
    let content=newTemplateContentV3(); const view=content.views[1]!,layer=view.layers[0]!;
    let id:string; [content,id]=addBasicNodeV3(content,view.id,layer.id,kind);
    const nodes=content.views[1]!.layers[0]!.nodes;
    const center=rootNodeRotationCenterV3(nodes[0]!,nodes);
    content=setRootNodeRotationAroundCenterV3(content,view.id,layer.id,id,137);
    const changed=content.views[1]!.layers[0]!.nodes;
    const after=rootNodeRotationCenterV3(changed[0]!,changed);
    expect(after.x).toBeCloseTo(center.x); expect(after.y).toBeCloseTo(center.y);
  });

  it("snaps circle tangents analytically and toggles features independently", () => {
    const circle={id:"c",closed:true,points:[],circle:{center:{x:0,y:0},radius:10}};
    const anchor={x:30,y:0},tangent=circleTangents(anchor,circle.circle.center,10)[0]!;
    expect(Math.hypot(tangent.x,tangent.y)).toBeCloseTo(10);
    expect(tangent.x*(anchor.x-tangent.x)+tangent.y*(anchor.y-tangent.y)).toBeCloseTo(0);
    const near={x:tangent.x+0.2,y:tangent.y+0.2};
    expect(snapDrawingPoint(near,[circle],{...off,tangents:true},1,anchor)).toEqual(tangent);
    expect(snapDrawingPoint(near,[circle],off,1,anchor)).toEqual(near);
    const line={id:"l",closed:false,points:[{x:-20,y:11},{x:20,y:11}]};
    expect(snapDrawingTranslation(line,{x:0,y:0},[circle],{...off,tangents:true},2)).toEqual({x:0,y:-1});
    expect(snapDrawingPoint({x:10.5,y:0},[circle],{...off,contours:true},1)).toEqual({x:10,y:0});
    const box={id:"b",closed:true,points:[{x:0,y:0},{x:10,y:0},{x:10,y:10},{x:0,y:10}]};
    expect(snapDrawingPoint({x:0.2,y:0.2},[box],{...off,corners:true},1)).toEqual({x:0,y:0});
    expect(snapDrawingPoint({x:5,y:0.2},[box],{...off,corners:true},1)).toEqual({x:5,y:0.2});
  });

  it("round trips article drawings, hatches and moved contacts with electrical row identity", () => {
    let core=newTemplateContentV3(); const view=core.views[1]!,layer=view.layers[0]!;
    let group:string; [core,group]=addContactTypeGroupV3(core,"Signal");
    core=addArticleVariantsV3(core,[{sourceId:"test",entityType:"connector",articleKey:"A"},{sourceId:"test",entityType:"connector",articleKey:"B"}]);
    for(const article of core.articleVariants) core=setArticleVariantContactGroupV3(core,article.id,group,2,[]);
    const table=createE4ConnectorSeriesTableFromV3(core,true);
    let rectangle:string,ellipse:string,pointId:string;
    [core,rectangle]=addBasicNodeV3(core,view.id,layer.id,"rectangle");
    [core,ellipse]=addBasicNodeV3(core,view.id,layer.id,"ellipse");
    [core,pointId]=addContactPointV3(core,view.id,{number:"9",name:"Old",contactTypeGroupId:group});
    core=editContactPointV3(core,view.id,pointId,{x:c(123),y:c(234)});
    core.views[1]!.layers[0]!.nodes[0]!.fill={color:"#123456",hatch:{kind:"cross",spacing:7,angle:30,backgroundColor:"#ffffff"}};
    const binding={logicalContactId:core.logicalContacts[0]!.id,seriesRowId:table.articles[0]!.rows[1]!.seriesRowId};
    table.seriesDefaults[1]!.values.number="20"; table.seriesDefaults[1]!.values.name="DATA";
    const drawings=[drawingSelection(core.views[1]!,[rectangle,pointId],core.articleVariants[0]!.id),drawingSelection(core.views[1]!,[ellipse],core.articleVariants[1]!.id)];
    const content=JSON.parse(JSON.stringify(createTemplateContentV5FromEditor(core,table,[],[],undefined,drawings,[binding]).content));
    expect(validateTemplateContentV5(content).valid).toBe(true);
    const preview=drawingContactContent(core,table,[binding],core.articleVariants[0]!.id);
    expect(preview.logicalContacts[0]).toMatchObject({number:"20",name:"DATA"});
    const envelope={templateId:"test-template",version:3,versionSha256:"a".repeat(64),code:"T",name:"Test",articleBindings:core.articleVariants.map(({sourceId,entityType,articleKey})=>({sourceId,entityType,articleKey})),assets:[],content};
    const placed=createConnectorInstanceFromComponentTemplateV3(envelope,{id:"connector-test",designation:"X1",articleVariantId:core.articleVariants[0]!.id,e4Position:{x:10,y:20}});
    expect(placed.contacts).toHaveLength(2);
    expect(JSON.stringify(placed)).toContain("20");
    expect(placed.libraryBinding?.mode).toBe("template");
    if(placed.libraryBinding?.mode === "template") { expect(placed.libraryBinding.snapshot.contacts[0]!.representations).toHaveLength(0); expect(placed.libraryBinding.snapshot.contacts[1]!.representations[0]).toMatchObject({x:123,y:234}); }
    const a=projectComponentTemplateView({content,objectId:"X1",articleVariantId:core.articleVariants[0]!.id,snapshotId:"test"},"drawing",{x:0,y:0});
    const b=projectComponentTemplateView({content,objectId:"X1",articleVariantId:core.articleVariants[1]!.id,snapshotId:"test"},"drawing",{x:0,y:0});
    expect(a?.commands.map(command=>command.kind)).toEqual(["rectangle"]);
    expect(b?.commands.map(command=>command.kind)).toEqual(["ellipse"]);
    expect(a?.commands[0]?.hatch).toEqual({kind:"cross",spacing:7,angle:30,backgroundColor:"#ffffff"});
    content.articleDrawings[0].nodeIds.push("missing");
    expect(validateTemplateContentV5(content).valid).toBe(false);
  });
});
