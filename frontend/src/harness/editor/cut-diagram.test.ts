// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CutDiagramSvg } from "./CutDiagramPanel";
import { describe, expect, it } from "vitest";
import { applyEditorCommand } from "./commands";
import { physicalFixture } from "./physical-topology-fixture";
import { buildCutDiagram, cutBlankIds } from "./cut-diagram";
import { calculateWireCutLength, type WireStripProfileBinding } from "./model";

const profile=(id:string):WireStripProfileBinding=>({sourceId:"s",snapshotId:"00000000-0000-4000-8000-000000000001",snapshotSha256:"a".repeat(64),recordId:id.padEnd(64,"b"),entityType:"coax-termination",sourceKey:id,displayName:id,layers:[{index:1,diameterMm:2,stripLengthMm:10},{index:2,diameterMm:4,stripLengthMm:20}]});
describe("cut and strip diagram",()=>{
  it("uses exact blank calculation and exposes both ends/layers",()=>{
    let d=physicalFixture();d={...d,wires:d.wires.map((w,i)=>i===0?{...w,lengthMm:100.125,endCorrectionFromMm:-1.25,endCorrectionToMm:2,cutRoundingStepMm:1,stripProfiles:{from:profile("A"),to:profile("B")}}:w)};
    d={...d,wires:d.wires.map(w=>w.id==="W1"?{...w,materialBinding:{sourceId:"s",snapshotId:"00000000-0000-4000-8000-000000000001",snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"wire" as const,sourceKey:"WIRE",displayName:"Wire"}}:w)};
    const diagram=buildCutDiagram(d,"W1",2)!;
    expect(diagram.cutLengthMm).toBe(101);expect(diagram.unroundedMm).toBe(100.875);expect(diagram.totalMetres).toBe(.202);
    expect(diagram.a.label).toContain("A:1");expect(diagram.b.label).toContain("B:1");expect(diagram.a.steps.map(s=>s.stepLengthMm)).toEqual([10,10]);expect(diagram.warnings).toEqual([]);
  });
  it("opens a cable as one blank and shows member conductor profiles without double material",()=>{
    let d=physicalFixture();d={...d,wires:d.wires.map((w,i)=>i<2?{...w,lengthMm:200,stripProfiles:{from:profile(`A${i}`),to:profile(`B${i}`)}}:w)};
    d=applyEditorCommand(d,{type:"add-cable",cable:{id:"CABLE",memberWireIds:["W1","W2"],lengthMm:200,endCorrectionFromMm:-1,endCorrectionToMm:2,cutRoundingStepMm:1,sheathStrip:{fromMm:20,toMm:30}}});
    const diagram=buildCutDiagram(d,"W1",3)!;expect(diagram.objectId).toBe("CABLE");expect(diagram.kind).toBe("cable");expect(diagram.memberWireIds).toEqual(["W1","W2"]);expect(diagram.conductors).toHaveLength(2);expect(diagram.totalMetres).toBe(.603);expect(cutBlankIds(d)).toEqual(["W3","CABLE"]);
  });
  it("keeps unknown length unknown and diagnoses missing profiles/material",()=>{
    const d={...physicalFixture(),wires:physicalFixture().wires.map(w=>w.id==="W1"?{...w,lengthMm:null}:w)},diagram=buildCutDiagram(d,"W1",1)!;expect(diagram.cutLengthMm).toBeNull();expect(diagram.totalMetres).toBeNull();expect(diagram.warnings).toEqual(["Исходная физическая длина не задана.","Материал заготовки не закреплён.","W1: профиль разделки A/B не задан."]);
  });
  it("rejects invalid quantity and strips exceeding the blank",()=>{
    expect(()=>buildCutDiagram(physicalFixture(),"W1",0)).toThrow();
    let d=physicalFixture();d={...d,wires:d.wires.map(w=>w.id==="W1"?{...w,lengthMm:10,stripProfiles:{from:profile("longA"),to:profile("longB")}}:w)};expect(buildCutDiagram(d,"W1",1)!.warnings).toContain("W1: сумма разделки A/B превышает длину заготовки.");
  });
  it("does not derive production length from drawing geometry",()=>{
    const d=physicalFixture(),diagram=buildCutDiagram(d,"W1",1)!;
    const moved=applyEditorCommand(d,{type:"move-connector",connectorId:"A",view:"drawing",position:{x:1234,y:-789}});
    expect(calculateWireCutLength(d.wires[0]!).sourceLengthMm).toBe(100);
    expect(buildCutDiagram(moved,"W1",1)).toEqual(diagram);
  });
  it("preserves explicit zero sheath stripping and diagnoses unknown strip boundaries",()=>{
    const d=physicalFixture();const cable={id:"K",memberWireIds:["W1","W2"],lengthMm:200,endCorrectionFromMm:0,endCorrectionToMm:0,cutRoundingStepMm:1,sheathStrip:{fromMm:0,toMm:0}};
    const result=buildCutDiagram({...d,cables:[cable]},"K",1)!;
    expect(result.sheath).toMatchObject({fromMm:0,toMm:0,isComplete:true});
    const html=renderToStaticMarkup(createElement(CutDiagramSvg,{diagram:result}));
    expect(html).toContain('x="60" y="98" width="640"');
    const unknown=buildCutDiagram({...d,cables:[{...cable,sheathStrip:{fromMm:null,toMm:10}}]},"K",1)!;
    expect(unknown.sheath!.fromMm).toBeNull();expect(unknown.warnings).toContain("Снятие общей оболочки заполнено не полностью.");
  });
  it("renders A/B, millimetre dimensions and independent layer annotations",()=>{
    const d=physicalFixture(),doc={...d,wires:d.wires.map(w=>w.id==="W1"?{...w,stripProfiles:{from:profile("A"),to:profile("B")}}:w)};
    const html=renderToStaticMarkup(createElement(CutDiagramSvg,{diagram:buildCutDiagram(doc,"W1",1)!}));
    expect(html).toContain("A · A:1");expect(html).toContain("B · B:1");expect(html).toContain("Заготовка 100 мм");expect(html).toContain("L2=20 мм");expect(html).toContain("Условное изображение");
  });
});
