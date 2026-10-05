// @vitest-environment node
import { expect, it } from "vitest";
import { coveringWidthProfile, encloseWidthProfiles, profileHalfWidth } from "./covering-width-profile";
import { coveringScene, moveCovering } from "./covering-layout";
import { coveringSurfaces } from "./covering-renderer";
import { physicalFixture } from "./physical-topology-fixture";
import type { CoveringKind, PhysicalCovering } from "./physical-coverings";
import type { HarnessDesignDocument } from "./model";

it("has two sharp corners per change and flat supports; ramps never enter the thicker support",()=>{
 const profile=coveringWidthProfile(0,100,[30,70],x=>x>=30&&x<=70?15:5);
 expect(profile).toEqual([{at:0,halfWidth:5},{at:25,halfWidth:5},{at:30,halfWidth:15},{at:70,halfWidth:15},{at:75,halfWidth:5},{at:100,halfWidth:5}]);
 expect(profileHalfWidth(profile,27.5)).toBe(10);
 expect(profileHalfWidth(profile,50)).toBe(15);
 expect(profileHalfWidth(profile,90)).toBe(5);
});
it("caps neighbouring ramps without inversion on short intervals and follows scale",()=>{
 const profile=coveringWidthProfile(0,100,[40,41,42,43],x=>x>=40&&x<41||x>=42&&x<43?50:1);
 expect(profile.every((p,i)=>i===0||p.at>profile[i-1]!.at)).toBe(true);
 const scaled=coveringWidthProfile(0,200,[80,82,84,86],x=>x>=80&&x<82||x>=84&&x<86?100:2);
 expect(scaled).toEqual(profile.map(p=>({at:p.at*2,halfWidth:p.halfWidth*2})));
});

function fixture(kind:CoveringKind):HarnessDesignDocument {
 const doc=physicalFixture();
 const cover=(id:string,width:number,from:number,to:number):PhysicalCovering=>({id,name:id,kind,width,color:"#8899aa",lengthMm:null,spans:[{segmentId:"S0",from,to}]});
 return {...doc,drawingDocuments:{tables:[],leaders:[],bomOrder:[],bendRadius:0},physicalTopology:{...doc.physicalTopology!,snap:false,
 nodes:[{id:"NA",position:{x:0,y:0}},{id:"J",position:{x:300,y:0}}],
 segments:[{id:"S0",from:"NA",to:"J",path:{kind:"routed",points:[]},width:10}],routes:[],
 coverings:[cover("lower",30,.3,.7),cover("upper",0,.1,.9)]}};
}
it.each<CoveringKind>(["heat-shrink","nylon","braid","metal-braid","tape"])("%s follows both lower sleeve boundaries with Z contours and preserved identities",kind=>{
 const doc=fixture(kind),before=JSON.stringify(doc),surface=coveringSurfaces(coveringScene(doc)[1]!)[0]!;
 const left=surface.polygon.slice(0,surface.path.length);
 expect(left).toEqual([{x:30,y:5.25},{x:85,y:5.25},{x:90,y:15.25},{x:210,y:15.25},{x:215,y:5.25},{x:270,y:5.25}]);
 const right=surface.polygon.slice(surface.path.length).reverse();
 expect(right).toEqual(left.map(p=>({x:p.x,y:-p.y})));
 expect(JSON.stringify(doc)).toBe(before);
 // Sleeve movement exposes a different range of the same support profile.
 const moved=moveCovering(doc,"upper",0,"body",{x:50,y:0},{x:65,y:0})!;
 const after={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[doc.physicalTopology!.coverings![0]!,moved]}};
 expect(coveringSurfaces(coveringScene(after)[1]!)[0]!.polygon).toContainEqual({x:90,y:15.25});
 const vertical={...doc,physicalTopology:{...doc.physicalTopology!,nodes:[{id:"NA",position:{x:0,y:0}},{id:"J",position:{x:0,y:300}}]}};
 expect(coveringSurfaces(coveringScene(vertical)[1]!)[0]!.polygon).toEqual(surface.polygon.map(p=>({x:-p.y,y:p.x})));
});
it("retains ramp corners when a sleeve ends within a ramp and when the pipe bends",()=>{
 const doc=fixture("heat-shrink"),coverings=doc.physicalTopology!.coverings!;
 const clipped={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[coverings[0]!,{...coverings[1]!,spans:[{segmentId:"S0",from:87.5/300,to:.9}]}]}};
 const surface=coveringSurfaces(coveringScene(clipped)[1]!)[0]!;
 expect(surface.polygon[0]).toEqual({x:87.5,y:10.25});
 expect(surface.polygon[1]).toEqual({x:90,y:15.25});
 const bent={...doc,physicalTopology:{...doc.physicalTopology!,nodes:[{id:"NA",position:{x:0,y:0}},{id:"J",position:{x:150,y:150}}],segments:[{...doc.physicalTopology!.segments[0]!,path:{kind:"routed" as const,points:[{x:150,y:0}]}}]}};
 const bentSurface=coveringSurfaces(coveringScene(bent)[1]!)[0]!;
 expect(bentSurface.polygon).toContainEqual({x:90,y:15.25});
 expect(bentSurface.polygon).toContainEqual({x:150-15.25,y:60});
 expect(bentSurface.polygon).toContainEqual({x:150-5.25,y:65});
 expect(bentSurface.polygon.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y))).toBe(true);
});

it("encloses intersecting lower ramps with the exact piecewise linear envelope",()=>{
 const base=[{at:0,halfWidth:1},{at:100,halfWidth:1}];
 const supports=[{from:0,to:100,profile:[{at:0,halfWidth:2},{at:100,halfWidth:12}]},
  {from:0,to:100,profile:[{at:0,halfWidth:12},{at:100,halfWidth:2}]}];
 const result=encloseWidthProfiles(base,supports,.25);
 expect(result).toEqual([{at:0,halfWidth:12.25},{at:50,halfWidth:7.25},{at:100,halfWidth:12.25}]);
});

it("keeps a third sleeve outside a lower ramp clipped at its end",()=>{
 const doc=fixture("heat-shrink"),[lower,middle]=doc.physicalTopology!.coverings!;
 const outer={...middle!,id:"outer",spans:[{segmentId:"S0",from:.05,to:.95}]};
 const layered={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[lower!,
  {...middle!,spans:[{segmentId:"S0",from:87.5/300,to:212.5/300}]},outer]}};
 const surfaces=coveringScene(layered).map(c=>coveringSurfaces(c)[0]!);
 const profiles=surfaces.map(s=>s.polygon.slice(0,s.path.length).map(p=>({at:p.x,halfWidth:p.y})));
 for(let x=87.5;x<=212.5;x+=.25) expect(profileHalfWidth(profiles[2]!,x)).toBeGreaterThanOrEqual(profileHalfWidth(profiles[1]!,x)+.25-1e-8);
 expect(profiles[2]!.every((p,i)=>i===0||p.at>profiles[2]![i-1]!.at)).toBe(true);
});

it.each([1.1,2,4])("grows every adjacent diameter at 1:%s without flattening the profile",ratio=>{
 const d=fixture("heat-shrink"),t=d.physicalTopology!;
 const widths=(width:number)=>{const doc={...d,drawingDocuments:{...d.drawingDocuments!,coveringDiameterRatio:ratio},physicalTopology:{...t,coverings:t.coverings!.map(c=>c.id==="upper"?{...c,width}:c)}};const surface=coveringSurfaces(coveringScene(doc)[1]!)[0]!;return surface.polygon.slice(0,surface.path.length);};
 const base=widths(0),a=widths(40),b=widths(42);
 expect(a[0]!.y).toBeGreaterThan(base[0]!.y);
 expect(b[0]!.y-a[0]!.y).toBeCloseTo(1/ratio);
 expect(Math.max(...b.map(p=>p.y))-Math.max(...a.map(p=>p.y))).toBeCloseTo(1);
 expect(Math.max(...b.map(p=>p.y))).toBeGreaterThan(b[0]!.y);
});
