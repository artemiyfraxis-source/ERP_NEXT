import { describe, expect, it, vi } from "vitest";
import { editDimension, nodeDimensions } from "./ComponentLibrary";
import { addBasicNodeV3, editNodeV3, newTemplateContentV3, constantExpressionV3 as c, resizeNodeV3 } from "./template-commands-v3";
import { stretchDrawingSelection } from "./drawing-selection";
import { templateDeltaToNodeDeltaV2 } from "./TemplateCanvasV2";

describe("drawing size fields after stretching", () => {
  it.each(["rectangle", "ellipse"] as const)("round trips actual dimensions for a stretched %s and later handle resize", kind => {
    let content = newTemplateContentV3();
    const viewId = content.views[1]!.id, layerId = content.views[1]!.layers[0]!.id;
    let id: string; [content, id] = addBasicNodeV3(content, viewId, layerId, kind);
    const node = () => content.views[1]!.layers[0]!.nodes[0]! as Extract<typeof content.views[number]["layers"][number]["nodes"][number], {kind: "rectangle" | "ellipse"}>;
    const setSize = (key: "width" | "height", value: number) => editDimension(node(), key, value, changes => { content = editNodeV3(content, viewId, layerId, id, changes); });
    setSize("width", 10); setSize("height", 10);
    const before = structuredClone(content);
    content = stretchDrawingSelection(content, viewId, [id], 2, {x:0,y:0});
    expect(nodeDimensions(node())).toEqual({width:20,height:20});
    expect(nodeDimensions(before.views[1]!.layers[0]!.nodes[0]! as ReturnType<typeof node>)).toEqual({width:10,height:10});
    setSize("width", 30); setSize("height", 14);
    expect(nodeDimensions(node())).toEqual({width:30,height:14});
    const delta = templateDeltaToNodeDeltaV2(6,4,0,2,2)!;
    content = resizeNodeV3(content,viewId,layerId,id,"se",delta.deltaX,delta.deltaY);
    expect(nodeDimensions(node())).toEqual({width:36,height:18});
    content = JSON.parse(JSON.stringify(content));
    expect(nodeDimensions(node())).toEqual({width:36,height:18});
  });

  it("uses positive side lengths for rotated, mirrored and nonuniformly scaled images", () => {
    const node = {
      kind:"image" as const, geometry:{x:c(0),y:c(0),width:c(10),height:c(10),assetId:"asset",cropX:0,cropY:0,cropWidth:1,cropHeight:1,underlay:false},
      id:"image",layerId:"layer",visible:true,locked:false,opacity:1,
      stroke:{color:"#000000",width:c(1)},fill:{color:null},
      transform:{translateX:c(5),translateY:c(8),rotationDegrees:c(37),scaleX:c(-2),scaleY:c(.5)},
    };
    expect(nodeDimensions(node)).toEqual({width:20,height:5});
    editDimension(node,"height",12,changes=>Object.assign(node,changes));
    expect(nodeDimensions(node)).toEqual({width:20,height:12});
    expect(node.transform.scaleX).toEqual(c(-2));
    expect(node.transform.rotationDegrees).toEqual(c(37));
    const edit=vi.fn();
    for(const value of [0,-1,NaN,Infinity])editDimension(node,"width",value,edit);
    editDimension({...node,transform:{...node.transform,scaleX:c(0)}},"width",10,edit);
    expect(edit).not.toHaveBeenCalled();
  });
});
