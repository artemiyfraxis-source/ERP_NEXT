import type {PhysicalCovering} from "./physical-coverings";
import type {PhysicalSegment} from "./physical-topology-model";
import {pipeMemberSegments, type PipeBundleMember} from "./pipe-bundle-model";

/** Resolve deletions from the inside out. Never leave an unsavable dangling group.
 * A broken longitudinal member leaves the group; its surviving pipes stay in the document. */
export function prunePipeBundles(coverings: readonly PhysicalCovering[] | undefined, segments: readonly PhysicalSegment[], additionalIds:readonly string[]=[]): readonly PhysicalCovering[] | undefined {
  if (!coverings) return undefined;
  const byId=new Map(coverings.map(c=>[c.id,c])), pipes=new Map(segments.map(s=>[s.id,s]));
  const done=new Map<string,PhysicalCovering|null>(), active=new Set<string>();
  const resolvedMembers=new Map<string,readonly PipeBundleMember[]>();
  const visit=(id:string):PhysicalCovering|null=>{
    if(done.has(id))return done.get(id)!;
    const c=byId.get(id);if(!c||active.has(id))return null;
    active.add(id);
    const members:PipeBundleMember[]=[];
    for(const m of c.bundle?.members??[]) {
      if(m.kind==="segment") {
        const ids=pipeMemberSegments(m).filter(id=>pipes.has(id));
        if(ids.length&&ids.every((id,i)=>i===0||pipes.get(ids[i-1]!)!.to===pipes.get(id)!.from))
          members.push({kind:"segment",id:ids[0]!,...(ids.length>1?{continuationIds:ids.slice(1)}:{})});
      } else {
        const child=visit(m.id);
        if(child?.bundle)members.push(m);
        else if(child)members.push(...resolvedMembers.get(child.id)??[]);
      }
    }
    const leaves=(ms:readonly PipeBundleMember[]):string[]=>ms.flatMap(m=>m.kind==="segment"?[...pipeMemberSegments(m)]:leaves(visit(m.id)?.bundle?.members??[]));
    const allowed=c.bundle?new Set(leaves(members)):new Set([...pipes.keys(),...additionalIds]);
    const spans=c.spans.filter(s=>allowed.has(s.segmentId));
    const result=spans.length?{...c,spans,...(c.bundle?{bundle:members.length>=2?{...c.bundle,members}:undefined}:{})}:null;
    active.delete(id);done.set(id,result);resolvedMembers.set(id,members);return result;
  };
  return coverings.flatMap(c=>{const next=visit(c.id);return next?[next]:[];});
}

export function splitPipeBundleMembers(covering:PhysicalCovering,id:string,nextId:string):PhysicalCovering {
  if(!covering.bundle)return covering;
  return {...covering,bundle:{...covering.bundle,members:covering.bundle.members.map(m=>{
    if(m.kind!=="segment"||!pipeMemberSegments(m).includes(id))return m;
    const ids=pipeMemberSegments(m).flatMap(part=>part===id?[part,nextId]:[part]);
    return {kind:"segment",id:ids[0]!,continuationIds:ids.slice(1)};
  })}};
}
