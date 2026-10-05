// @vitest-environment node
import { describe, expect, it } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { addDrawingPositions, setDrawingPositionVisibility, setDrawingPositionsVisibility, buildDrawingBom, drawingDocumentScene, emptyDrawingDocuments, moveDrawingAnnotation, connectionEndLabel, reconcileDrawingDocuments } from "./drawing-documents";
import { applyEditorCommand } from "./commands";
import { createJunctionEndpoint, createScreenEndpoint, parseHarnessDesignDocument, type WireMaterialBinding } from "./model";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";

const material:WireMaterialBinding={sourceId:"source",snapshotId:"00000000-0000-4000-8000-000000000001",snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"wire",sourceKey:"SAME",displayName:"Провод"};
import { dimensionRouteKey, measuredWireLength, validateDrawingDimensions } from "./drawing-dimensions";
import { tableWindowPosition, tableWindowStyle, resizeTableWindow } from "./DrawingTableWindows";
import { snapDrawingPoint, type DrawingOutline } from "../component-library/drawing-geometry";
import { buildDrawingObjectIndices } from "./drawing-object-indices";

describe("authored canvas graphics",()=>{
 it("applies angular, corner, contour, and circle tangent snaps according to enabled settings",()=>{
  const corner:DrawingOutline={id:"poly",points:[{x:0,y:0},{x:20,y:0}],closed:false,corners:[{x:0,y:0},{x:20,y:0}]};
  const circle:DrawingOutline={id:"circle",points:[{x:-10,y:0},{x:10,y:0}],closed:true,circle:{center:{x:0,y:0},radius:10}};
  expect(snapDrawingPoint({x:19,y:1},[corner],{corners:true,contours:false,tangents:false},3)).toEqual({x:20,y:0});
  expect(snapDrawingPoint({x:0,y:11},[circle],{corners:false,contours:true,tangents:false},3)).toEqual({x:0,y:10});
  const tangent=snapDrawingPoint({x:5,y:9},[circle],{corners:false,contours:false,tangents:true},2,{x:20,y:0});
  expect(tangent.x).toBeCloseTo(5);expect(tangent.y).toBeCloseTo(8.660254,5);
 });
 it("round trips and isolates graphics by view, then moves them as document geometry",()=>{
  const base=physicalFixture(),graphics=[{id:"g1",view:"drawing" as const,kind:"line" as const,points:[{x:1,y:2},{x:10,y:20}]},{id:"g2",view:"e4" as const,kind:"contact" as const,points:[{x:30,y:40}]}];
  const document=parseHarnessDesignDocument({...base,drawingDocuments:{...emptyDrawingDocuments(),graphics}});
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(document))).drawingDocuments?.graphics).toEqual(graphics);
  expect(drawingDocumentScene(document,1,undefined,"drawing").map(o=>o.id)).toContain("g1");
  expect(drawingDocumentScene(document,1,undefined,"drawing").map(o=>o.id)).not.toContain("g2");
  expect(drawingDocumentScene(document,1,undefined,"e4").map(o=>o.id)).toEqual(["g2"]);
  expect(moveDrawingAnnotation(document,"g1",{x:11,y:12})?.graphics?.[0]?.points).toEqual([{x:11,y:12},{x:20,y:30}]);
 });
 it("persists editable text family and size without widening the graphic contract",()=>{
  const base=physicalFixture(),graphics=[{id:"label",view:"e4" as const,kind:"text" as const,points:[{x:30,y:40}],text:"Э4",fontFamily:"Times New Roman",fontSize:24}];
  const document=parseHarnessDesignDocument({...base,drawingDocuments:{...emptyDrawingDocuments(),graphics}});
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(document))).drawingDocuments?.graphics).toEqual(graphics);
  expect(()=>parseHarnessDesignDocument({...base,drawingDocuments:{...emptyDrawingDocuments(),graphics:[{...graphics[0],fontSize:145}]}})).toThrow();
  expect(()=>parseHarnessDesignDocument({...base,drawingDocuments:{...emptyDrawingDocuments(),graphics:[{...graphics[0],fontFamily:"<script>"}]}})).toThrow();
 });
 it("rejects malformed authored graphics",()=>{
  const base=physicalFixture(),docs={...emptyDrawingDocuments(),graphics:[{id:"g",view:"drawing",kind:"line",points:[{x:0,y:0}]}]};
  expect(()=>parseHarnessDesignDocument({...base,drawingDocuments:docs})).toThrow();
 });
});

describe("drawing tables and position leaders",()=>{
 it("persists and validates the OP covering edge allowance",()=>{
   const d=physicalFixture(), documents={...emptyDrawingDocuments(),opCoveringEdgePx:6};
   const saved=parseHarnessDesignDocument(JSON.parse(JSON.stringify({...d,drawingDocuments:documents})));
   expect(saved.drawingDocuments?.opCoveringEdgePx).toBe(6);
   expect(()=>parseHarnessDesignDocument({...d,drawingDocuments:{...documents,opCoveringEdgePx:-1}})).toThrow();
   expect(()=>parseHarnessDesignDocument({...d,drawingDocuments:{...documents,opCoveringEdgePx:25}})).toThrow();
 });
 it.each([true,false])("preserves volume switch %s through command, JSON and undo",enabled=>{
   const original=physicalFixture();
   const history=executeEditorCommand(createEditorHistory(original),{type:"set-drawing-documents",documents:{...emptyDrawingDocuments(),volumeShading:enabled}});
   expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present))).drawingDocuments?.volumeShading).toBe(enabled);
   expect(undoEditorCommand(history).present).toEqual(original);
 });
 it.each([null,0,"false",{},[]])("rejects invalid volume switch %j",volumeShading=>{
   expect(()=>parseHarnessDesignDocument({...physicalFixture(),drawingDocuments:{...emptyDrawingDocuments(),volumeShading}})).toThrow();
 });
  it("uses readable placeholders instead of internal endpoint identifiers",()=>{
    const d=physicalFixture();
    expect(connectionEndLabel(d,createScreenEndpoint("2c7e77f8-2dad-4d26-bfcd-e905e7bc730b","above"))).toBe("Экран");
    expect(connectionEndLabel(d,createJunctionEndpoint("2c7e77f8-2dad-4d26-bfcd-e905e7bc730b"))).toBe("Узел");
    expect(connectionEndLabel(d,{connectorId:"missing",contactId:"missing"})).toBe("Соединитель:Контакт");
  });

  it("preserves resized windows through serialization and a single Undo",()=>{
    const table={id:"T",kind:"bom" as const,position:{x:10,y:20}};
    const d={...physicalFixture(),drawingDocuments:{...emptyDrawingDocuments(),tables:[table]}};
    const size=resizeTableWindow({width:760,height:380},120,60);
    const h=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents:{...d.drawingDocuments,tables:[{...table,...size}]}});
    const saved=parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present)));
    expect(saved.drawingDocuments!.tables[0]).toEqual({...table,width:880,height:440});
    expect(tableWindowStyle(saved.drawingDocuments!.tables[0]!,{offsetX:2,offsetY:3,zoom:2})).toEqual({left:22,top:43,width:880,height:440});
    expect(undoEditorCommand(h).present).toBe(d);
    expect(tableWindowStyle(table,{offsetX:0,offsetY:0,zoom:1})).toMatchObject({width:760,height:380});
  });
  it("resizes away from the pinned edge and limits window size",()=>{
    expect(resizeTableWindow({width:760,height:380},-100,80,"right")).toEqual({width:860,height:460});
    expect(resizeTableWindow({width:760,height:380},100,-80,"bottom")).toEqual({width:860,height:460});
    expect(resizeTableWindow({width:760,height:380},-9999,-9999)).toEqual({width:280,height:160});
    expect(resizeTableWindow({width:760,height:380},9999,9999)).toEqual({width:4000,height:4000});
  });
  it.each([{width:279},{height:159},{width:4001},{height:4001},{width:null},{width:"760"},{height:NaN}])("rejects invalid window size %j",size=>{
    expect(()=>parseHarnessDesignDocument({...physicalFixture(),drawingDocuments:{...emptyDrawingDocuments(),tables:[{id:"T",kind:"bom",position:{x:0,y:0},...size}]}})).toThrow();
  });
  it("keeps source versions separate, sums exactly and excludes material cable members",()=>{
    const d=physicalFixture();
    const doc={...d,wires:d.wires.map((w,i)=>({...w,lengthMm:100.1,cutRoundingStepMm:.001,materialBinding:i===2?{...material,snapshotSha256:"c".repeat(64)}:material}))};
    const rows=buildDrawingBom(doc,3).filter(r=>r.unit==="м");
    expect(rows).toHaveLength(2);expect(rows[0]).toMatchObject({amount:.6006,objectIds:["W1","W2"]});expect(rows[1]!.amount).toBe(.3003);
    const cable={id:"K",memberWireIds:["W1","W2"],materialBinding:{...material,entityType:"cable" as const},lengthMm:200,endCorrectionFromMm:0,endCorrectionToMm:0,cutRoundingStepMm:1};
    expect(buildDrawingBom({...doc,cables:[cable]},2).filter(r=>r.unit==="м").map(r=>r.objectIds)).toEqual([["W3"],["K"]]);
  });
  it("uses the measured length plus both signed terminal corrections in the specification",()=>{
    const d=physicalFixture(),wire={...d.wires[0]!,lengthMm:100.1,endCorrectionFromMm:1.25,endCorrectionToMm:-.35,cutRoundingStepMm:.001,materialBinding:material};
    const row=buildDrawingBom({...d,wires:[wire]},2).find(item=>item.objectIds.includes(wire.id));
    expect(row).toMatchObject({amount:.202,unit:"м"});
  });
  it("retains unknown length and never counts graphics as additional material",()=>{
    const d=physicalFixture();
    const rows=buildDrawingBom({...d,wires:d.wires.map(w=>({...w,lengthMm:null})),physicalTopology:{...d.physicalTopology!,coverings:[{id:"cover",name:"shell",width:14,color:"#123456",lengthMm:100,spans:[{segmentId:"S0",from:0,to:1}]}]}});
    expect(rows.filter(r=>r.unit==="м"&&!r.objectIds.includes("cover")).every(r=>r.amount===null)).toBe(true);expect(rows.find(r=>r.objectIds.includes("cover"))).toMatchObject({amount:.1,name:"shell"});
  });
  it("moves circle and anchor independently, follows object moves and renumbers from BOM",()=>{
    const d=physicalFixture(),rows=buildDrawingBom(d),key=rows.find(r=>r.objectIds.includes("A"))!.key;
    const docs={...emptyDrawingDocuments(),leaders:[{id:"L",objectId:"A",rowKey:key,anchorOffset:{x:10,y:20},circle:{x:300,y:100}}]};
    let doc=applyEditorCommand(d,{type:"set-drawing-documents",documents:docs});
    const moved=moveDrawingAnnotation(doc,"L",{x:400,y:200})!;
    expect(moved.leaders[0]!.anchorOffset).toEqual({x:10,y:20});expect(moved.leaders[0]!.circle).toEqual({x:412,y:212});
    doc={...doc,drawingDocuments:moved};
    const anchor=moveDrawingAnnotation(doc,"L:anchor",{x:36,y:46})!;
    expect(anchor.leaders[0]!.circle).toEqual({x:412,y:212});expect(anchor.leaders[0]!.anchorOffset.x).toBeCloseTo(0);expect(anchor.leaders[0]!.anchorOffset.y).toBeCloseTo(75.2631578947);
    doc=applyEditorCommand({...doc,drawingDocuments:anchor},{type:"move-connector",connectorId:"A",view:"drawing",position:{x:100,y:150}});
    expect(drawingDocumentScene(doc).find(o=>o.id==="L")!.points![0]!.x).toBeCloseTo(100);expect(drawingDocumentScene(doc).find(o=>o.id==="L")!.points![0]!.y).toBeCloseTo(225.2631578947);
    const reverse={...doc,drawingDocuments:{...anchor,bomOrder:rows.map(r=>r.key).reverse()}};
    expect(drawingDocumentScene(reverse).find(o=>o.id==="L")!.label).toBe(String(rows.length));
  });
  it("round trips annotations, supports Undo and diagnoses deleted targets",()=>{
    const d=physicalFixture(),key=buildDrawingBom(d)[0]!.key;
    const documents={tables:[{id:"T",kind:"bom" as const,position:{x:100,y:200}}],leaders:[{id:"L",objectId:"A",rowKey:key,anchorOffset:{x:0,y:0},circle:{x:300,y:100}}],bomOrder:[key]};
    const h=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents});
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).drawingDocuments).toEqual(documents);
    expect(undoEditorCommand(h).present).toBe(d);
    expect(JSON.parse(drawingDocumentScene(h.present)[0]!.metadata!.headers!)).toEqual(["Поз.","Индекс","Обозначение","Наименование","Кол-во","Примечание"]);
    expect(drawingDocumentScene({...h.present,connectors:[]}).find(o=>o.id==="L")!.label).toBe("?");
    expect(()=>parseHarnessDesignDocument({...h.present,drawingDocuments:{...documents,tables:[{...documents.tables[0],position:{x:NaN,y:0}}]}})).toThrow();
  });

  it("drops orphan leaders and rail memberships after a component replacement",()=>{
    const before=physicalFixture(), row=buildDrawingBom(before)[0]!;
    const docs={...emptyDrawingDocuments(), leaders:[{id:"stale",objectId:"A",rowKey:row.key,anchorOffset:{x:0,y:0},circle:{x:10,y:10}}], rails:[{id:"rail",start:{x:0,y:0},end:{x:100,y:100},leaderIds:["stale"]}], bomOrder:[row.key]};
    const after={...before,connectors:before.connectors.filter(c=>c.id!=="A"),drawingDocuments:docs};
    const repaired=reconcileDrawingDocuments(before,after);
    expect(repaired.drawingDocuments?.leaders).toEqual([]);
    expect(repaired.drawingDocuments?.rails).toEqual([]);
  });
  it("keeps floating coordinates in drawing space and docking independent of camera",()=>{
    const table={id:"cut",kind:"cut" as const,position:{x:100,y:200}};
    expect(tableWindowPosition(table,{offsetX:10,offsetY:20,zoom:2})).toEqual({left:210,top:420});
    expect(tableWindowPosition({...table,dock:"right"},{offsetX:-1000,offsetY:-2000,zoom:.2})).toEqual({right:8,top:8});
    const d=physicalFixture(),key=buildDrawingBom(d)[0]!.key;
    const documents={...emptyDrawingDocuments(),tables:[{...table,dock:"bottom" as const}],bomText:{[key]:{name:"Edited",note:"Note"}}};
    const h=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents});
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).drawingDocuments).toEqual(documents);
    expect(buildDrawingBom(h.present)[0]).toMatchObject({name:"Edited",note:"Note"});
    expect(undoEditorCommand(h).present).toBe(d);
    expect(()=>parseHarnessDesignDocument({...h.present,drawingDocuments:{...documents,tables:[{...table,dock:"bad"}]}})).toThrow();
  });

  it("calculates a total dimension or a complete sum of non-overlapping sections",()=>{
    const d=physicalFixture(),wire=d.wires[0]!,key=dimensionRouteKey(d,wire);
    expect(measuredWireLength([{id:"a",wireId:wire.id,from:0,to:3,pointCount:4,routeKey:key,mode:"aligned",offset:20,lengthMm:350}])).toBe(350);
    expect(measuredWireLength([
      {id:"a",wireId:wire.id,from:0,to:1,pointCount:4,routeKey:key,mode:"horizontal",offset:20,lengthMm:100},
      {id:"b",wireId:wire.id,from:1,to:3,pointCount:4,routeKey:key,mode:"vertical",offset:20,lengthMm:250},
    ])).toBe(350);
    expect(measuredWireLength([{id:"a",wireId:wire.id,from:0,to:1,pointCount:4,routeKey:key,mode:"aligned",offset:20,lengthMm:100}])).toBeNull();
    expect(()=>measuredWireLength([{id:"a",wireId:wire.id,from:0,to:2,pointCount:4,routeKey:key,mode:"aligned",offset:20,lengthMm:100},{id:"b",wireId:wire.id,from:1,to:3,pointCount:4,routeKey:key,mode:"aligned",offset:20,lengthMm:250}])).toThrow();
    expect(validateDrawingDimensions([{id:"a",wireId:wire.id,from:0,to:3,pointCount:4,routeKey:key,mode:"aligned",offset:20,lengthMm:350}],d)).toHaveLength(1);
    expect(()=>validateDrawingDimensions([{id:"a",wireId:wire.id,from:0,to:3,pointCount:4,routeKey:"stale",mode:"aligned",offset:20,lengthMm:350}],d)).toThrow();
  });

});

it("omits abstract specification objects and pipeline segments while retaining material rows and Undo",()=>{
 const d=physicalFixture(),item={id:"glue",kind:"manual" as const,type:"Клей",designation:"",name:"Клей",amount:2.5,unit:"г" as const,note:""};
 const documents={...emptyDrawingDocuments(),specificationItems:[item,{...item,id:"clamp",kind:"abstract" as const,type:"Хомут",name:"Хомут",unit:"шт." as const,amount:2,position:{x:40,y:60}}]};
 const h=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents});
 const bom=buildDrawingBom(h.present,3);
 expect(bom.find(r=>r.objectIds.includes("glue"))).toMatchObject({amount:7.5,unit:"г"});
 expect(bom.some(r=>r.objectIds.includes("clamp"))).toBe(false);
 expect(bom.some(r=>r.designation.startsWith("S")&&r.name==="Канал")).toBe(false);
 expect(bom.every(r=>!r.name.includes("Абстрактный канал"))).toBe(true);
 expect(drawingDocumentScene(h.present).filter(o=>o.kind==="specification-item").map(o=>o.id)).toEqual(["clamp"]);
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).drawingDocuments).toEqual(documents);
 const before=buildDrawingBom(h.present).find(r=>r.objectIds.includes("glue"))!;
 const changed={...h.present,drawingDocuments:{...documents,specificationItems:[{...item,designation:"GL-1",sourceIdentity:"versioned-record"},documents.specificationItems[1]!]}};
 expect(buildDrawingBom(changed).find(r=>r.objectIds.includes("glue"))!.key).toBe(before.key);
 expect(undoEditorCommand(h).present).toEqual(d);
 expect(()=>parseHarnessDesignDocument({...d,drawingDocuments:{...documents,specificationItems:[item,item]}})).toThrow();
});

it("uses readable wire and covering indices and carries material attributes into BOM",()=>{
 const base=physicalFixture();
 const connectors=base.connectors.map(c=>({...c,contacts:c.contacts.map((contact,index)=>({...contact,terminalArticle:index===0?"3:JST|14:SPH-002T-P0.5S|0:|3:PHR":"",...(index===0?{terminalDetails:{manufacturer:"JST",series:"PHR",description:"Клемма обжимная",article:"SPH-002T-P0.5S"}}:{}),wire:"UL1061",wireSection:"24 AWG",color:"красный"}))}));
 const wires=base.wires.map((wire,index)=>({...wire,circuit:`W${index+1}`,materialBinding:{...material,sourceKey:"UL1061-24AWG",displayName:"UL1061 24AWG"}}));
 const document={...base,connectors,wires,physicalTopology:{...base.physicalTopology!,coverings:[{id:"cover-1",name:"Термоусадка",kind:"heat-shrink" as const,width:8,color:"#123456",lengthMm:50,spans:[{segmentId:"S0",from:0,to:1}]}]}};
 const rows=buildDrawingBom(document);
 expect(rows.find(row=>row.objectIds.includes("W1"))).toMatchObject({index:"W1, W2, W3",designation:"UL1061-24AWG — UL1061, 24 AWG, красный",name:"UL1061 24AWG, цвет красный"});
 expect(rows.find(row=>row.objectIds.includes("cover-1"))?.index).toBe("ТУ1");
 expect(rows.find(row=>row.designation.includes("SPH-002T-P0.5S"))).toMatchObject({designation:"SPH-002T-P0.5S",name:"JST · PHR · Клемма обжимная"});
 expect(buildDrawingObjectIndices([{id:"W1",kind:"wire",metadata:{circuit:"W1"}},{id:"cover-1",kind:"physical-covering",metadata:{coveringKind:"heat-shrink"}}])).toEqual(new Map([["W1","W1"],["cover-1","ТУ1"]]));
});

it("keeps differently colored wires on the same material in separate BOM rows",()=>{
 const base=physicalFixture();
 const connectors=base.connectors.map(connector=>({...connector,contacts:connector.contacts.map(contact=>({...contact,color:({"A:contact:1":"красный","B:contact:1":"красный","A:contact:2":"синий","C:contact:1":"синий"} as Record<string,string>)[contact.id]??""}))}));
 const wires=base.wires.slice(0,2).map(wire=>({...wire,materialBinding:material,lengthMm:100}));
 const rows=buildDrawingBom({...base,connectors,wires}).filter(row=>row.unit==="м");
 expect(rows).toHaveLength(2);
 expect(rows.map(row=>[row.index,row.amount,row.designation])).toEqual([
  ["W1",.1,"SAME — красный"],
  ["W2",.1,"SAME — синий"],
 ]);
 expect(rows.every(row=>JSON.parse(row.key)[0]==="material")).toBe(true);
});

it("keeps plain covering article keys and hides unbound internal IDs",()=>{
 const base=physicalFixture();
 const id="f0f0f0f0-f0f0-40f0-80f0-f0f0f0f0f0f0";
 const doc={...base,wires:[{...base.wires[0]!,id,materialBinding:undefined}],physicalTopology:{...base.physicalTopology!,coverings:[
  {id:"plain-cover",name:"Термоусадка",width:8,color:"#123456",lengthMm:50,spans:[{segmentId:"S0",from:0,to:1}],material:{sourceId:"source",snapshotId:"00000000-0000-4000-8000-000000000001",snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"protective-covering" as const,sourceKey:"HS-ARTICLE",displayName:"Термоусадка 3/1"}},
 ]}};
 const wireRow=buildDrawingBom(doc).find(row=>row.objectIds.includes(id));
 expect(wireRow).toMatchObject({designation:"—",index:"W1"});
 expect(wireRow?.designation).not.toContain(id);
 expect(buildDrawingBom(doc).find(row=>row.objectIds.includes("plain-cover"))).toMatchObject({designation:"HS-ARTICLE — Термоусадка 3/1",index:"ТУ1"});
});

it("uses saved material display names when a length-prefixed article key is malformed",()=>{
 const base=physicalFixture();
 const binding={...material,sourceKey:"6:UL1007 #10",displayName:"UL1007 28AWG"};
 const wire={...base.wires[0]!,materialBinding:binding};
 const covering={id:"cover",name:"Термоусадка",kind:"heat-shrink" as const,width:8,color:"#123456",lengthMm:50,spans:[{segmentId:"S0",from:0,to:1}],material:{...material,entityType:"protective-covering" as const,sourceKey:"6:HS-301",displayName:"HS-301, 3/1 мм"}};
 const doc={...base,wires:[wire],physicalTopology:{...base.physicalTopology!,coverings:[covering]}};
 expect(buildDrawingBom(doc).find(row=>row.objectIds.includes(wire.id))?.designation).toBe("UL1007 28AWG");
 expect(buildDrawingBom(doc).find(row=>row.objectIds.includes("cover"))?.designation).toBe("HS-301 — 3/1 мм");
});


it("adds all positions in one undo, is idempotent and places additional numbers horizontally tangent",()=>{
 const base=physicalFixture();const doc={...base,connectors:base.connectors.map(c=>({...c,partNumber:"PART",contacts:c.contacts.map(p=>({...p,terminalArticle:"PIN"}))}))};
 const added=addDrawingPositions(doc);const h=executeEditorCommand(createEditorHistory(doc),{type:"set-drawing-documents",documents:added});
 expect(added.leaders.filter(l=>l.objectId==="A")).toHaveLength(3);
 const [a,b,c]=added.leaders.filter(l=>l.objectId==="A");
 expect(b!.circle).toEqual({x:a!.circle.x+24,y:a!.circle.y});expect(c!.circle).toEqual({x:a!.circle.x+48,y:a!.circle.y});
 expect(addDrawingPositions(h.present)).toEqual(added);
 const rearranged={...added,leaders:added.leaders.filter(l=>l.id!==c!.id).map(l=>l.id===a!.id?{...l,circle:{x:800,y:900}}:l)};
 const extra=addDrawingPositions({...doc,drawingDocuments:rearranged}).leaders.filter(l=>l.objectId==="A").at(-1)!;
 expect(extra.circle).toEqual({x:824,y:900});
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).drawingDocuments).toEqual(added);
 expect(undoEditorCommand(h).present).toBe(doc);
 const firstOnly=addDrawingPositions(doc,undefined,[buildDrawingBom(doc)[0]!.key]);
 const moved={...firstOnly,leaders:firstOnly.leaders.map(l=>({...l,circle:{x:900,y:250}}))};
 const complete=addDrawingPositions({...doc,drawingDocuments:moved});
 expect(complete.leaders.filter(l=>l.objectId==="A").map(l=>l.circle)).toEqual([{x:900,y:250},{x:924,y:250},{x:948,y:250}]);
});

it("shows and hides every designation of a shared position together without losing layout",()=>{
 const base=physicalFixture();
 const binding={mode:"series" as const,seriesId:"xs-demo-series",partNumber:"XS-04"};
 const doc={...base,connectors:base.connectors.map(c=>({...c,partNumber:"XS-04",libraryBinding:binding}))};
 const row=buildDrawingBom(doc)[0]!;
 expect(row.index).toBe("A, B, C");expect(row.designation).toBe("XS-04");
 const visible=setDrawingPositionVisibility(doc,row.key,true);
 expect(visible.leaders).toHaveLength(3);
 const hidden=setDrawingPositionVisibility({...doc,drawingDocuments:visible},row.key,false);
 expect(hidden.leaders.every(l=>l.hidden)).toBe(true);
 expect(drawingDocumentScene({...doc,drawingDocuments:hidden}).filter(o=>o.kind==="position-leader")).toHaveLength(0);
 const again=setDrawingPositionVisibility({...doc,drawingDocuments:hidden},row.key,true);
 expect(again.leaders.map(l=>l.circle)).toEqual(visible.leaders.map(l=>l.circle));
 expect(again.leaders.every(l=>!l.hidden)).toBe(true);
});

it("toggles all position annotations together and restores missing positions",()=>{
 const doc=physicalFixture();
 const visible=setDrawingPositionsVisibility(doc,true);
 expect(visible.leaders.length).toBeGreaterThan(0);
 const hidden=setDrawingPositionsVisibility({...doc,drawingDocuments:visible},false);
 expect(hidden.leaders.every(l=>l.hidden)).toBe(true);
 expect(drawingDocumentScene({...doc,drawingDocuments:hidden}).filter(o=>o.kind==="position-leader")).toHaveLength(0);
 const shown=setDrawingPositionsVisibility({...doc,drawingDocuments:hidden},true);
 expect(shown.leaders.map(l=>l.circle)).toEqual(visible.leaders.map(l=>l.circle));
 expect(shown.leaders.every(l=>!l.hidden)).toBe(true);
});

it.each([{hidden:"yes"},{anchorLocal:{x:null,y:1}},{anchorLocal:{x:1e8,y:0}}])("rejects invalid persisted leader attachment %j",patch=>{
 const doc=physicalFixture(),docs=addDrawingPositions(doc);
 expect(()=>parseHarnessDesignDocument({...doc,drawingDocuments:{...docs,leaders:[{...docs.leaders[0],...patch}]}})).toThrow();
});

it("migrates legacy material keys without losing order, edits, leaders or rails when colors split",()=>{
 const base=physicalFixture();
 const connectors=base.connectors.map(c=>({...c,contacts:c.contacts.map(p=>({...p,color:p.id.endsWith(':1')?'red':'blue'}))}));
 const raw={...base,connectors,wires:base.wires.slice(0,2).map(w=>({...w,materialBinding:material}))};
 const rows=buildDrawingBom(raw).filter(row=>row.unit==='м');
 const legacyKey=JSON.stringify(JSON.parse(rows[0]!.key).slice(0,6));
 const documents={...emptyDrawingDocuments(),bomOrder:[legacyKey],bomText:{[legacyKey]:{name:'Authored',note:'Keep me',index:'Custom'}},leaders:raw.wires.map((w,i)=>({id:`L${i}`,objectId:w.id,rowKey:legacyKey,anchorOffset:{x:0,y:0},circle:{x:40+i*30,y:50}})),rails:[{id:'rail',start:{x:0,y:50},end:{x:200,y:50},leaderIds:['L0','L1']}]};
 const before={...raw,drawingDocuments:documents},next=reconcileDrawingDocuments(before,before);
 expect(next.drawingDocuments!.bomOrder).toEqual(rows.map(row=>row.key));
 expect(next.drawingDocuments!.leaders.map(l=>l.rowKey)).toEqual(rows.map(row=>row.key));
 expect(next.drawingDocuments!.rails).toEqual(documents.rails);
 const migrated=buildDrawingBom(next).filter(row=>row.unit==='м');
 expect(migrated.map(row=>row.name)).toEqual(['Authored','Authored']);
 expect(migrated.map(row=>row.index)).toEqual(['Custom','W2']);
 expect(reconcileDrawingDocuments(next,next)).toBe(next);
});
