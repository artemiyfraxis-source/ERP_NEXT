import type {HarnessDesignDocument} from "./model";
import {drawingPhysicalScale,drawingPipeWidth} from "./drawing-thickness";
import {pipeMemberSegments,resolvePipeBundles,type PipeBundleMember} from "./pipe-bundle-model";
import {packPipeBundle,type BundlePacking} from "./pipe-bundle-packing";

export interface PipeBundleSection extends BundlePacking {
 readonly diameter:number;
 readonly leafOffsets:ReadonlyMap<string,{offset:number;depth:number}>;
}

/** Cross-sections only. Authored routes, measured lengths and electrical IDs
 * remain untouched. A continuation uses one disk with the widest fragment. */
export function pipeBundleSections(document:HarnessDesignDocument):ReadonlyMap<string,PipeBundleSection> {
 const t=document.physicalTopology,result=new Map<string,PipeBundleSection>();if(!t)return result;
 const coverings=t.coverings??[],byId=new Map(coverings.map(c=>[c.id,c]));
 const segments=new Map(t.segments.map(s=>[s.id,s])),scale=drawingPhysicalScale(document);
 const resolved=resolvePipeBundles(coverings,new Set(segments.keys()));
 const pipeDiameter=(id:string):number=>{
  let width=drawingPipeWidth(document,segments.get(id)!);
  for(const c of coverings)if(!c.bundle&&c.spans.some(s=>s.segmentId===id))width=Math.max(c.width*scale,width+.5*scale);
  return width;
 };
 const visit=(id:string):PipeBundleSection=>{
  const existing=result.get(id);if(existing)return existing;
  const c=byId.get(id)!,bundle=c.bundle!;
  const key=(m:PipeBundleMember)=>`${m.kind}:${m.id}`;
  const disks=bundle.members.map(m=>({id:key(m),diameter:m.kind==='segment'
   ?Math.max(...pipeMemberSegments(m).map(pipeDiameter)):visit(m.id).diameter}));
  const packed=packPipeBundle(disks,bundle.mode),leafOffsets=new Map<string,{offset:number;depth:number}>();
  for(const [i,m] of bundle.members.entries()) {
   const placement=packed.members[i]!;
   if(m.kind==='segment')for(const leaf of pipeMemberSegments(m))leafOffsets.set(leaf,{offset:placement.offset,depth:placement.depth});
   else for(const [leaf,p] of visit(m.id).leafOffsets)leafOffsets.set(leaf,{offset:placement.offset+p.offset,depth:placement.depth+p.depth});
  }
  const radial=2*Math.max(...packed.members.map(m=>Math.hypot(m.offset,m.depth)+m.diameter/2));
  const section={...packed,width:Math.max(c.width*scale,packed.width+.5*scale),depth:Math.max(c.width*scale,packed.depth+.5*scale),
   diameter:Math.max(c.width*scale,radial+.5*scale),leafOffsets};
  result.set(id,section);return section;
 };
 for(const id of resolved.keys())visit(id);
 return result;
}
