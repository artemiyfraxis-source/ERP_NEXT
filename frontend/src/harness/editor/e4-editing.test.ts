// @vitest-environment node
import {expect,it} from "vitest";
import {moveE4Ends,movedE4Junctions,resolveE4JunctionMoves} from "./e4-editing";
import {createEmptyHarnessDesign} from "./model";
import {createWire} from "./commands";
it("moves a shared shoulder once when both ends move together",()=>{
 const points=[{x:0,y:0},{x:50,y:50},{x:100,y:0}];
 expect(moveE4Ends(points,{x:20,y:30},{x:120,y:30},"carry")).toEqual([{x:20,y:30},{x:70,y:80},{x:120,y:30}]);
 expect(moveE4Ends(points,{x:20,y:30},{x:100,y:0},"carry")[1]).toEqual({x:70,y:80});
 expect(moveE4Ends(points,{x:20,y:30},{x:120,y:30},"adjacent")[1]).toEqual({x:50,y:50});
});
it("resolves conflicting carrier proposals by junction order independently of wire array order",()=>{
 const a=createWire("a",{connectorId:"x",contactId:"1"},{connectorId:"y",contactId:"1"});
 const b=createWire("b",{connectorId:"x",contactId:"2"},{connectorId:"y",contactId:"2"});
 const branch=createWire("branch",{connectorId:"x",contactId:"3"},{connectorId:"",contactId:"",junctionId:"j"});
 const d={...createEmptyHarnessDesign(),wires:[branch,b,a],junctions:[{id:"j",position:{x:80,y:50},wireIds:["branch","a","b"]}]};
 const proposals=new Map([["b",new Map([["j",{x:110,y:90}]])],["branch",new Map([["j",{x:500,y:500}]])],["a",new Map([["j",{x:100,y:70}]])]]);
 expect(resolveE4JunctionMoves(d,proposals).get("j")).toEqual({x:100,y:70});
 expect(resolveE4JunctionMoves({...d,wires:[a,b,branch]},proposals)).toEqual(resolveE4JunctionMoves(d,proposals));
 expect(resolveE4JunctionMoves(d,new Map([["branch",proposals.get("branch")!]]))).toEqual(new Map());
});
it("maps junctions by their original segment and excludes unrelated crossings",()=>{
 const d={...createEmptyHarnessDesign(),junctions:[{id:"attached",position:{x:25,y:0},wireIds:["w","branch"]},{id:"other",position:{x:25,y:0},wireIds:["unrelated"]}]};
 expect([...movedE4Junctions(d,"w",[{x:0,y:0},{x:100,y:0}],[{x:0,y:0},{x:100,y:80}])]).toEqual([["attached",{x:25,y:20}]]);
});
