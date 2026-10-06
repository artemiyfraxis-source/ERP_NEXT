// @vitest-environment node
import {expect,it} from "vitest";
import {packPipeBundle,type BundleDisk} from "./pipe-bundle-packing";

it("lays pipes side by side with exact diameter sum and stable identity",()=>{
 const members=[{id:"a",diameter:10},{id:"b",diameter:20},{id:"c",diameter:4}];
 expect(packPipeBundle(members,"flat")).toEqual({width:34,depth:20,members:[
  {...members[0]!,offset:-12,depth:0},{...members[1]!,offset:3,depth:0},{...members[2]!,offset:15,depth:0}]});
});
it("packs equal pipes into a triangular cross section with overlapping projection but no physical overlap",()=>{
 const members=["a","b","c"].map(id=>({id,diameter:10}));
 const packed=packPipeBundle(members,"round");
 expect(packed.width).toBeCloseTo(20);expect(packed.depth).toBeCloseTo(10+5*Math.sqrt(3));
 expect(packed.members.map(m=>m.id)).toEqual(["a","b","c"]);
 for(const a of packed.members)for(const b of packed.members)if(a.id!==b.id)
  expect(Math.hypot(a.offset-b.offset,a.depth-b.depth)).toBeCloseTo(10);
 expect(packed.members.some((a,i)=>packed.members.slice(i+1).some(b=>Math.abs(a.offset-b.offset)<10))).toBe(true);
});
it.each([1,2,8,32])("encloses %s mixed pipes and leaves all disks disjoint, deterministic and immutable",count=>{
 const members=Array.from({length:count},(_,i)=>({id:`p${i}`,diameter:1+(i*7)%13})),before=JSON.stringify(members);
 const packed=packPipeBundle(members,"round");
 expect(packPipeBundle(members,"round")).toEqual(packed);expect(JSON.stringify(members)).toBe(before);
 for(const a of packed.members){
  expect(Math.abs(a.offset)+a.diameter/2).toBeLessThanOrEqual(packed.width/2+1e-7);
  expect(Math.abs(a.depth)+a.diameter/2).toBeLessThanOrEqual(packed.depth/2+1e-7);
  for(const b of packed.members)if(a.id!==b.id)expect(Math.hypot(a.offset-b.offset,a.depth-b.depth)+1e-7).toBeGreaterThanOrEqual((a.diameter+b.diameter)/2);
 }
 expect(packed.width).toBeLessThanOrEqual(members.reduce((n,m)=>n+m.diameter,0)+1e-7);
});
it.each([.001,1000])("keeps proportional layout at scale %s",scale=>{
 const members=[{id:"a",diameter:3},{id:"b",diameter:2},{id:"c",diameter:1}];
 const original=packPipeBundle(members,"round"),scaled=packPipeBundle(members.map(m=>({...m,diameter:m.diameter*scale})),"round");
 expect(scaled.width).toBeCloseTo(original.width*scale);expect(scaled.depth).toBeCloseTo(original.depth*scale);
 scaled.members.forEach((m,i)=>{expect(m.offset).toBeCloseTo(original.members[i]!.offset*scale);expect(m.depth).toBeCloseTo(original.members[i]!.depth*scale);});
});
it("handles an empty bundle and rejects duplicate IDs or invalid diameters",()=>{
 expect(packPipeBundle([],"round")).toEqual({members:[],width:0,depth:0});
 for(const diameter of [0,-1,NaN,Infinity])expect(()=>packPipeBundle([{id:"p",diameter}],"flat")).toThrow();
 expect(()=>packPipeBundle([{id:"p",diameter:1},{id:"p",diameter:2}],"round")).toThrow();
 expect(()=>packPipeBundle([{id:"",diameter:1}] as BundleDisk[],"round")).toThrow();
});
