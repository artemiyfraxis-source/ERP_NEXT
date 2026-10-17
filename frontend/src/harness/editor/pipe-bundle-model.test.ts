// @vitest-environment node
import {expect,it} from "vitest";
import {resolvePipeBundles,type PipeBundle} from "./pipe-bundle-model";
import type {PhysicalCovering} from "./physical-coverings";
import {physicalFixture} from "./physical-topology-fixture";
import {parseHarnessDesignDocument} from "./model";
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from "./history";

const segment=(id:string)=>({kind:"segment" as const,id});
const group=(id:string)=>({kind:"covering" as const,id});
const cover=(id:string,members:PipeBundle["members"],axis="S0"):PhysicalCovering=>({id,name:id,width:0,color:"#8899aa",lengthMm:null,
 spans:[{segmentId:axis,from:.2,to:.8}],bundle:{mode:"flat",members}});
const ids=new Set(["S0","S1","S2"]);

it("resolves ordered nested leaves independent of paint order",()=>{
 const inner=cover("inner",[segment("S0"),segment("S1")]);
 const outer=cover("outer",[group("inner"),segment("S2")]);
 expect([...resolvePipeBundles([outer,inner],ids)]).toEqual([["inner",["S0","S1"]],["outer",["S0","S1","S2"]]]);
});
it("preserves a bundle through command, serialization and undo without changing wires",()=>{
 const before=physicalFixture(),inner=cover("inner",[segment("S0"),segment("S1")]),outer=cover("outer",[group("inner"),segment("S2")]);
 const history=executeEditorCommand(createEditorHistory(before),{type:"set-physical-topology",topology:{...before.physicalTopology!,coverings:[outer,inner]}});
 const loaded=parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present)));
 expect(loaded.physicalTopology!.coverings).toEqual([outer,inner]);
 expect(loaded.wires).toEqual(parseHarnessDesignDocument(JSON.parse(JSON.stringify(before))).wires);
 expect(undoEditorCommand(history).present).toEqual(before);
});
it.each([null,[],{}, {mode:"wrong",members:[segment("S0"),segment("S1")]},
 {mode:"flat",members:[]},{mode:"flat",members:[segment("S0")]},
 {mode:"round",members:[segment("S0"),segment("S0")]},
 {mode:"flat",members:[segment("S0"),segment("missing")]},
 {mode:"flat",members:[segment("S0"),group("missing")]},
 {mode:"flat",members:[segment("S0"),{kind:"wire",id:"S1"}]},
 {mode:"flat",members:[segment("S0"),null]},
])("rejects malformed bundle %j at document boundary",bundle=>{
 const d=physicalFixture();
 expect(()=>parseHarnessDesignDocument({...d,physicalTopology:{...d.physicalTopology!,coverings:[{...cover("c",[]),bundle}]}})).toThrow();
});
it("rejects direct and indirect cycles and duplicate leaves across branches",()=>{
 const a=cover("a",[segment("S0"),group("a")]);expect(()=>resolvePipeBundles([a],ids)).toThrow();
 const b=cover("b",[segment("S1"),group("a")],"S1");
 expect(()=>resolvePipeBundles([{...a,bundle:{mode:"flat",members:[segment("S0"),group("b")]}},b],ids)).toThrow();
 const inner=cover("inner",[segment("S0"),segment("S1")]);
 expect(()=>resolvePipeBundles([inner,cover("outer",[group("inner"),segment("S0")])],ids)).toThrow();
});
it("rejects an unrelated axis or a reference to an ordinary sleeve",()=>{
 expect(()=>resolvePipeBundles([cover("c",[segment("S0"),segment("S1")],"S2")],ids)).toThrow();
 expect(()=>resolvePipeBundles([{...cover("plain",[]),bundle:undefined},cover("outer",[group("plain"),segment("S1")])],ids)).toThrow();
});
it("caps nesting even when a previously resolved group is referenced",()=>{
 const pipes=new Set(Array.from({length:20},(_,i)=>`s${i}`));
 const covers=[cover("g1",[segment("s0"),segment("s1")],"s0")];
 for(let i=2;i<=17;i++)covers.push(cover(`g${i}`,[group(`g${i-1}`),segment(`s${i}`)],"s0"));
 expect(()=>resolvePipeBundles(covers.slice(0,16),pipes)).not.toThrow();
 expect(()=>resolvePipeBundles(covers,pipes)).toThrow();
 expect(()=>resolvePipeBundles([...covers].reverse(),pipes)).toThrow();
});
it("caps total unique leaves after expanding nested groups",()=>{
 const pipes=new Set(Array.from({length:130},(_,i)=>`s${i}`));
 const inner=cover("inner",Array.from({length:128},(_,i)=>segment(`s${i}`)),"s0");
 expect(resolvePipeBundles([inner],pipes).get("inner")).toHaveLength(128);
 expect(()=>resolvePipeBundles([inner,cover("outer",[group("inner"),segment("s129")],"s0")],pipes)).toThrow();
});
