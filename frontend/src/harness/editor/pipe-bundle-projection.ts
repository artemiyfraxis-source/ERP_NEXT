import { bundleTransitionPoint } from "./pipe-bundle-transition";
import type { HarnessDesignDocument, Point } from "./model";
import { physicalSegmentPoints } from "./physical-geometry";
import { pathLength, projectOntoPolyline, resolvedCoveringSpan } from "./physical-coverings";
import { drawingBendRadius, drawingRouteSamples } from "./drawing-route-path";
import { pipeMemberSegments, pipeBundleAxisPath, pipeBundleCoatingKey, type PipeBundleMember } from "./pipe-bundle-model";
import { pipeBundleSections } from "./pipe-bundle-section";
import {hasJoiningPipeProjection,joiningPipeDisplaySamples,joiningPipeProjectionStops,projectJoiningPipePoint} from "./physical-joining-pipe-projection";

interface Sample { readonly fraction: number; readonly point: Point }
interface Chain { readonly ids: readonly string[]; readonly lengths: readonly number[]; readonly length: number; readonly samples: readonly Sample[] }
interface Placement {
  readonly coveringId: string;
  readonly coatingIds: ReadonlySet<string>;
  readonly transitionStart?:number; readonly transitionEnd?:number;
  readonly transitionBendStart?:Point; readonly transitionBendEnd?:Point;
  readonly ancestors: ReadonlySet<string>;
  readonly axis: Chain;
  readonly chain: Chain;
  readonly start: number;
  readonly end: number;
  readonly reverse: boolean;
  readonly offset: number;
  readonly bodyOffset?: Point;
  readonly depth: number;
  readonly leafCount: number;
  readonly groupOffsets: ReadonlyMap<string, number>;
}
interface Projection {
  readonly source: readonly Sample[];
  readonly length: number;
  readonly placements: readonly Placement[];
}

const cache = new WeakMap<HarnessDesignDocument, ReadonlyMap<string, Projection>>();
const nodeCache = new WeakMap<HarnessDesignDocument, ReadonlyMap<string,Point>>();
// Each immutable preview owns one sampled centreline per pipe. All its wires,
// hit tests and handles share it; replacing the document invalidates the cache.
const displayCache = new WeakMap<HarnessDesignDocument, Map<string, readonly Sample[] | undefined>>();
const coatingParentCache = new WeakMap<HarnessDesignDocument, Map<string,string|undefined>>();
/** A short coating over the same member set uses its supporting group's
 * convergence instead of pulling every member through a second transition. */
function parentCoating(document:HarnessDesignDocument,id:string):string|undefined {
 let cached=coatingParentCache.get(document);
 if(cached?.has(id))return cached.get(id);
 const coverings=document.physicalTopology?.coverings??[],child=coverings.find(c=>c.id===id);
 if(!child?.bundle)return undefined;
 const members=JSON.stringify([child.bundle.mode,child.bundle.members]);
 const contains=(parent:typeof child)=>child.spans.every(span=>parent.spans.some(other=>
  other.segmentId===span.segmentId&&other.from<=span.from+1e-7&&other.to>=span.to-1e-7));
 const parent=coverings.filter(c=>c.id!==id&&c.bundle&&JSON.stringify([c.bundle.mode,c.bundle.members])===members&&
   (c.bundle.bodyOffset?.x??0)===(child.bundle?.bodyOffset?.x??0)&&(c.bundle.bodyOffset?.y??0)===(child.bundle?.bodyOffset?.y??0)&&
   contains(c)&&c.spans.reduce((n,s)=>n+s.to-s.from,0)>child.spans.reduce((n,s)=>n+s.to-s.from,0)+1e-7)
  .sort((a,b)=>a.spans.reduce((n,s)=>n+s.to-s.from,0)-b.spans.reduce((n,s)=>n+s.to-s.from,0))[0]?.id;
 if(!cached){cached=new Map();coatingParentCache.set(document,cached);}cached.set(id,parent);
 return parent;
}
const mix = (a: Point, b: Point, t: number): Point => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);

function at(samples: readonly Sample[], fraction: number): Point {
  if (fraction <= samples[0]!.fraction) return samples[0]!.point;
  const index = samples.findIndex(p => p.fraction >= fraction);
  if (index < 0) return samples.at(-1)!.point;
  const a = samples[index - 1]!, b = samples[index]!;
  return mix(a.point, b.point, (fraction - a.fraction) / (b.fraction - a.fraction || 1));
}

function offsetAt(samples: readonly Sample[], fraction: number, offset: number): Point {
  const point = at(samples, fraction);
  const a = at(samples, Math.max(0, fraction - .00001)), b = at(samples, Math.min(1, fraction + .00001));
  const length = distance(a, b) || 1;
  return { x: point.x - (b.y - a.y) / length * offset, y: point.y + (b.x - a.x) / length * offset };
}

/** An immutable document owns its display cache. No pixel-to-mm or
 * display-to-topology write occurs here. Split continuations share one axis. */
function projections(document: HarnessDesignDocument): ReadonlyMap<string, Projection> {
  const cached = cache.get(document); if (cached) return cached;
  const result = new Map<string, Projection>();
  const topology = document.physicalTopology, coverings = topology?.coverings ?? [];
  if (!topology || !coverings.some(c => c.bundle)) { cache.set(document, result); return result; }
  const sections = pipeBundleSections(document), radius = drawingBendRadius(document);
  const routes = new Map(topology.segments.map(segment => {
    const points = physicalSegmentPoints(document, segment), length = pathLength(points);
    return [segment.id, { length, samples: drawingRouteSamples(points, radius).map(s => ({ point: s.point, fraction: length ? s.distance / length : 0 })) }] as const;
  }));
  const chains = new Map<string, Chain>();
  function chain(ids: readonly string[]): Chain {
    const key = JSON.stringify(ids), existing = chains.get(key); if (existing) return existing;
    const lengths = ids.map(id => routes.get(id)!.length), length = lengths.reduce((a, b) => a + b, 0);
    let before = 0;
    const samples = ids.flatMap((id, i) => {
      const points = routes.get(id)!.samples.map(s => ({ point: s.point, fraction: (before + s.fraction * lengths[i]!) / (length || 1) }));
      before += lengths[i]!; return i ? points.slice(1) : points;
    });
    const value = { ids, lengths, length, samples }; chains.set(key, value); return value;
  }
  function memberChains(members: readonly PipeBundleMember[]): Chain[] {
    return members.flatMap(m => m.kind === "segment" ? [chain(pipeMemberSegments(m))]
      : memberChains(coverings.find(c => c.id === m.id)!.bundle!.members));
  }
  function ancestors(id: string): Set<string> {
    const source=coverings.find(c=>c.id===id)!;
    const aliases=new Set(coverings.filter(c=>c.bundle&&coatingKey(c)===coatingKey(source)).map(c=>c.id));
    const parents = coverings.filter(c => c.bundle?.members.some(m => m.kind === "covering" && aliases.has(m.id)));
    return new Set(parents.flatMap(p => [p.id, ...ancestors(p.id)]));
  }
  // Coincident layers around the same bundle share one convergence geometry.
  // A copied coating must not independently pull the same pipes a second time.
  const coatingKey = pipeBundleCoatingKey;
  const coatingOwners = new Map<string, string>();
  for (const c of [...coverings].sort((a,b)=>a.id.localeCompare(b.id)))
    if (c.bundle && !coatingOwners.has(coatingKey(c))) coatingOwners.set(coatingKey(c), c.id);
  for (const covering of coverings) {
    if (!covering.bundle) continue;
    if (parentCoating(document,covering.id)) continue;
    if (coatingOwners.get(coatingKey(covering)) !== covering.id) continue;
    const coatingIds = new Set(coverings.filter(c => c.bundle && coatingKey(c) === coatingKey(covering)).map(c => c.id));
    const section = sections.get(covering.id)!, members = memberChains(covering.bundle.members);
    // One common sleeve has one display axis, even when saved intervals refer
    // to several members. Use the same stable choice as its renderer.
    const axisIds=pipeBundleAxisPath(covering,coverings);
    const axes=members.filter(c=>c.ids[0]===axisIds[0]);
    for (const axis of axes) {
      if (!axis.length) continue;
      const ranges = covering.spans.filter(s => axis.ids.includes(s.segmentId)).map(s => {
        const index = axis.ids.indexOf(s.segmentId), before = axis.lengths.slice(0, index).reduce((a, b) => a + b, 0);
        const resolved = resolvedCoveringSpan(document, s);
        return { start: (before + resolved.from * axis.lengths[index]!) / axis.length, end: (before + resolved.to * axis.lengths[index]!) / axis.length };
      }).sort((a, b) => a.start - b.start);
      const intervals: { start: number; end: number }[] = [];
      for (const range of ranges) {
        const previous = intervals.at(-1);
        if (previous && range.start <= previous.end + 1e-7) previous.end = Math.max(previous.end, range.end);
        else intervals.push({ ...range });
      }
      for (const member of members) {
        if (!member.length) continue;
        const direct = distance(at(axis.samples, 0), at(member.samples, 0)) + distance(at(axis.samples, 1), at(member.samples, 1));
        const reverse = member !== axis && distance(at(axis.samples, 0), at(member.samples, 1)) + distance(at(axis.samples, 1), at(member.samples, 0)) < direct;
        for (const id of member.ids) {
          const own = section.leafOffsets.get(id)!;
          // A nested shell uses its group centre, never the axis of one leaf.
          const groupOffsets = new Map([...sections].flatMap(([groupId, child]) => {
            const leaf = child.leafOffsets.get(id);
            return leaf && ancestors(groupId).has(covering.id) ? [[groupId, own.offset - leaf.offset] as const] : [];
          }));
          const source = routes.get(id)!, previous = result.get(id);
          const additions = intervals.map(interval => ({ coveringId: covering.id, coatingIds, transitionStart:covering.bundle!.transitionStart, transitionEnd:covering.bundle!.transitionEnd,
            transitionBendStart:covering.bundle!.transitionBendStart, transitionBendEnd:covering.bundle!.transitionBendEnd, ancestors: ancestors(covering.id), axis, chain: member,
            ...interval, reverse, offset: own.offset, bodyOffset:covering.bundle!.bodyOffset, depth: own.depth, leafCount: section.leafOffsets.size, groupOffsets }));
          result.set(id, { source: source.samples, length: source.length, placements: [...previous?.placements ?? [], ...additions] });
        }
      }
    }
  }
  for (const [id, value] of result) result.set(id, { ...value, placements: [...value.placements].sort((a, b) => a.leafCount - b.leafCount || a.coveringId.localeCompare(b.coveringId)) });
  cache.set(document, result); return result;
}

function chainFraction(p: Placement, id: string, fraction: number): number {
  const index = p.chain.ids.indexOf(id), before = p.chain.lengths.slice(0, index).reduce((a, b) => a + b, 0);
  const t = (before + fraction * p.chain.lengths[index]!) / p.chain.length;
  return p.reverse ? 1 - t : t;
}
function localFraction(p: Placement, id: string, fraction: number): number {
  const index = p.chain.ids.indexOf(id), before = p.chain.lengths.slice(0, index).reduce((a, b) => a + b, 0);
  return ((p.reverse ? 1 - fraction : fraction) * p.chain.length - before) / p.chain.lengths[index]!;
}
const transition = (p: Placement,side:"start"|"end"="start") => side==="start"?p.transitionStart??Math.min(.08,(p.end-p.start)/3):p.transitionEnd??Math.min(.08,(p.end-p.start)/3);
function weight(p: Placement, t: number): number {
  // Preserve endpoints while converging before, not inside, the sleeve.
  const lo = Math.max(0, p.start - transition(p)), hi = Math.min(1, p.end + transition(p,"end"));
  if (t <= lo || t >= hi) return 0;
  return Math.max(0, Math.min(1, (t - lo) / (p.start - lo || .001), (hi - t) / (hi - p.end || .001)));
}
function eligible(p: Placement, coveringId?: string, document?:HarnessDesignDocument): boolean {
  const id=coveringId&&document?parentCoating(document,coveringId)??coveringId:coveringId;
  return !id || !p.coatingIds.has(id) && !p.ancestors.has(id);
}

export function hasPipeBundleProjection(document: HarnessDesignDocument, segmentId: string): boolean {
  if(hasJoiningPipeProjection(document,segmentId))return true;
  if(projections(document).has(segmentId))return true;
  const segment=document.physicalTopology?.segments.find(s=>s.id===segmentId);
  return !!segment&&(nodeDisplacements(document).has(segment.from)||nodeDisplacements(document).has(segment.to));
}

export function pipeBundleDepth(document:HarnessDesignDocument,segmentId:string):number {
  return projections(document).get(segmentId)?.placements.at(-1)?.depth??0;
}

/** Sampling stations include sleeve edges and axis bends even on straight pipes.
 * Fractions refer to the original segment, including after a split. */
export function pipeBundleProjectionStops(document: HarnessDesignDocument, segmentId: string, coveringId?: string): number[] {
  if(hasJoiningPipeProjection(document,segmentId))return joiningPipeProjectionStops(document,segmentId);
  const projection = projections(document).get(segmentId); if (!projection) return [];
  return [...new Set(projection.placements.filter(p => eligible(p, coveringId, document)).flatMap(p =>
    [p.start - transition(p), p.start-transition(p)/2, Math.max(.001, p.start), Math.min(.999, p.end), p.end+transition(p,"end")/2, p.end + transition(p,"end"), ...p.axis.samples.map(s => s.fraction),
      ...(drawingBendRadius(document)>0?Array.from({length:65},(_,i)=>[p.start-transition(p)*i/64,p.end+transition(p,"end")*i/64]).flat():[])]
      .filter(t => t >= p.start - transition(p) && t <= p.end + transition(p,"end"))
      .map(t => localFraction(p, segmentId, t))).filter(t => t > 0 && t < 1))].sort((a, b) => a - b);
}

function rawProjectedPoint(document: HarnessDesignDocument, segmentId: string, fraction: number, point: Point, coveringId?: string): Point {
  const all = projections(document), projection = all.get(segmentId); if (!projection) return point;
  const radius = drawingBendRadius(document), memo = new Map<string, Point>();
  // A parent transition starts on the geometry produced by its children. Using
  // the authored route here jumps back out of an already packed inner sleeve.
  // Prefixes are memoized and strictly decrease, including across split chains.
  function projected(id:string, local:number, before?:Placement):Point {
   const current=all.get(id)!;
   const key=JSON.stringify([id,local,before?.coveringId,before?.start,before?.end]);
   const cached=memo.get(key);if(cached)return cached;
   const ownOffset=coveringId?document.physicalTopology?.coverings?.find(c=>c.id===coveringId)?.bundle?.bodyOffset:undefined;
   const source=at(current.source,local);
   let result=ownOffset?{x:source.x+ownOffset.x,y:source.y+ownOffset.y}:source;
   for (const placement of current.placements) {
    if(before&&(placement.leafCount>before.leafCount||placement.leafCount===before.leafCount&&placement.coveringId.localeCompare(before.coveringId)>=0))break;
    if (!eligible(placement, coveringId, document)) continue;
    const t = chainFraction(placement, id, local), blend = weight(placement, t);
    if (!blend) continue;
    const offset = coveringId && placement.groupOffsets.has(coveringId) ? placement.groupOffsets.get(coveringId)! : placement.offset;
    const body=placement.bodyOffset??{x:0,y:0};
    const target=offsetAt(placement.axis.samples,t,offset);
    const shiftedTarget={x:target.x+body.x,y:target.y+body.y};
    if(t<placement.start||t>placement.end){
      const entering=t<placement.start,edge=entering?placement.start:placement.end;
      const outer=entering?Math.max(0,edge-transition(placement)):Math.min(1,edge+transition(placement,"end"));
      let station=(placement.reverse?1-outer:outer)*placement.chain.length,index=0;
      while(index<placement.chain.ids.length-1&&station>placement.chain.lengths[index]!){station-=placement.chain.lengths[index]!;index++;}
      const sourcePoint=projected(placement.chain.ids[index]!,station/(placement.chain.lengths[index]||1),placement);
      const edgePoint=offsetAt(placement.axis.samples,edge,offset);
      const shiftedEdge={x:edgePoint.x+body.x,y:edgePoint.y+body.y};
      const a=at(placement.axis.samples,Math.max(0,edge-.00001)),b=at(placement.axis.samples,Math.min(1,edge+.00001));
      const progress=entering?(t-outer)/(edge-outer):(t-edge)/(outer-edge);
      const bend=entering?placement.transitionBendStart:placement.transitionBendEnd;
      result=entering?bundleTransitionPoint(sourcePoint,shiftedEdge,{x:b.x-a.x,y:b.y-a.y},progress,radius,bend)
        :bundleTransitionPoint(sourcePoint,shiftedEdge,{x:a.x-b.x,y:a.y-b.y},1-progress,radius,bend);
    }else result = mix(result, shiftedTarget, blend);
   }
   memo.set(key,result);return result;
  }
  const source = at(projection.source, fraction), result=projected(segmentId,fraction);
  return { x: point.x + (result.x - source.x), y: point.y + (result.y - source.y) };
}

/** A split creates an actual node at the join. Its visual anchor, and any
 * attached branch, follow the same displacement as the consecutive fragments. */
function nodeDisplacements(document:HarnessDesignDocument):ReadonlyMap<string,Point>{
  const cached=nodeCache.get(document);if(cached)return cached;
  const candidates=new Map<string,Point[]>(),result=new Map<string,Point>();
  for(const segment of document.physicalTopology?.segments??[]){
    const joining=joiningPipeDisplaySamples(document,segment.id);
    if(joining){
      const raw=physicalSegmentPoints(document,segment);
      for(const [nodeId,point,projected] of [[segment.from,raw[0]!,joining[0]!.point],[segment.to,raw.at(-1)!,joining.at(-1)!.point]] as const){
        const delta={x:projected.x-point.x,y:projected.y-point.y};
        if(Math.hypot(delta.x,delta.y)>1e-7)candidates.set(nodeId,[...candidates.get(nodeId)??[],delta]);
      }
      continue;
    }
    const projection=projections(document).get(segment.id);if(!projection)continue;
    for(const [nodeId,fraction] of [[segment.from,0],[segment.to,1]] as const){
      const point=at(projection.source,fraction),projected=rawProjectedPoint(document,segment.id,fraction,point);
      const delta={x:projected.x-point.x,y:projected.y-point.y};
      if(Math.hypot(delta.x,delta.y)>1e-7)candidates.set(nodeId,[...candidates.get(nodeId)??[],delta]);
    }
  }
  for(const [id,deltas] of candidates)if(deltas.every(d=>distance(d,deltas[0]!)<1e-6))result.set(id,deltas[0]!);
  nodeCache.set(document,result);return result;
}

export function pipeBundleNodePoint(document:HarnessDesignDocument,nodeId:string,point:Point):Point{
  const delta=nodeDisplacements(document).get(nodeId);return delta?{x:point.x+delta.x,y:point.y+delta.y}:point;
}

export function projectPipeBundlePoint(document:HarnessDesignDocument,segmentId:string,fraction:number,point:Point,coveringId?:string):Point{
  if(hasJoiningPipeProjection(document,segmentId))return projectJoiningPipePoint(document,segmentId,fraction,point);
  const projected=rawProjectedPoint(document,segmentId,fraction,point,coveringId);
  if(coveringId)return projected;
  const segment=document.physicalTopology?.segments.find(s=>s.id===segmentId);if(!segment)return projected;
  const shifts=nodeDisplacements(document),source=projections(document).get(segmentId)?.source;
  let result=projected;
  for(const [nodeId,end,blend] of [[segment.from,0,Math.max(0,1-fraction/.08)],[segment.to,1,Math.max(0,1-(1-fraction)/.08)]] as const){
    const shift=shifts.get(nodeId);if(!shift||!blend)continue;
    const own=source?at(source,end):{x:0,y:0},raw=source?rawProjectedPoint(document,segmentId,end,own):own;
    result={x:result.x+(shift.x-raw.x+own.x)*blend,y:result.y+(shift.y-raw.y+own.y)*blend};
  }
  return result;
}

/** Consumers use radius=0: samples already contain circular tangent joins. */
export function pipeBundleDisplaySamples(document: HarnessDesignDocument, segmentId: string): readonly Sample[] | undefined {
  const joining=joiningPipeDisplaySamples(document,segmentId);if(joining)return joining;
  let samples = displayCache.get(document);
  if (!samples) { samples = new Map(); displayCache.set(document, samples); }
  if (samples.has(segmentId)) return samples.get(segmentId);
  const result = buildDisplaySamples(document, segmentId);
  samples.set(segmentId, result);
  return result;
}

function buildDisplaySamples(document: HarnessDesignDocument, segmentId: string): readonly Sample[] | undefined {
  if(!hasPipeBundleProjection(document,segmentId))return undefined;
  const projection = projections(document).get(segmentId);
  const segment=document.physicalTopology!.segments.find(s=>s.id===segmentId)!;
  const raw=projection?undefined:physicalSegmentPoints(document,segment),length=raw?pathLength(raw):0;
  const source=projection?.source??drawingRouteSamples(raw!,drawingBendRadius(document)).map(s=>({point:s.point,fraction:length?s.distance/length:0}));
  const fractions = [...new Set([...source.map(s => s.fraction), ...pipeBundleProjectionStops(document, segmentId),
    ...(nodeDisplacements(document).has(segment.from)?[.08]:[]),...(nodeDisplacements(document).has(segment.to)?[.92]:[])])].sort((a, b) => a - b);
  return fractions.map(fraction => ({ fraction, point: projectPipeBundlePoint(document, segmentId, fraction, at(source, fraction)) }));
}

/** Keep authored handle ordinals; display sampling vertices are never handles. */
export function projectPipeBundleControls(document: HarnessDesignDocument, segmentId: string, points: readonly Point[]): Point[] {
  if (!hasPipeBundleProjection(document, segmentId)) return [...points];
  const route = physicalSegmentPoints(document, document.physicalTopology!.segments.find(s => s.id === segmentId)!);
  return points.map(point => projectPipeBundlePoint(document, segmentId, projectOntoPolyline(route, point).fraction, point));
}

/** Pointer deltas act on authored vertices, not on the generated convergence
 * vertices. Keeping the original displacement prevents the first drag jumping. */
export function unprojectPipeBundleEdit(document: HarnessDesignDocument, segmentId: string, original: Point, target: Point): Point {
  if (!hasPipeBundleProjection(document, segmentId)) return target;
  const displayedOrigin=projectPipeBundleControls(document,segmentId,[original])[0]!;
  return { x: original.x + target.x - displayedOrigin.x, y: original.y + target.y - displayedOrigin.y };
}

/** Context actions target the segment under the pointer. Recover its authored
 * station, not the nearest point of an unrelated, unshifted centreline. */
export function unprojectPipeBundlePoint(document:HarnessDesignDocument,segmentId:string,point:Point):Point {
  // A joining pipe is already expressed in the displayed coordinate space.
  // Context actions on an OP (for example adding a shell) must not run the
  // ordinary member unprojection below: an OP has no authored segment path,
  // and looking it up as one dereferences `undefined`.
  if(document.physicalTopology?.joiningPipes?.some(pipe=>pipe.id===segmentId))return point;
  const samples=pipeBundleDisplaySamples(document,segmentId);if(!samples)return point;
  let nearest=Infinity,fraction=0;
  for(let i=1;i<samples.length;i++){
    const a=samples[i-1]!,b=samples[i]!,dx=b.point.x-a.point.x,dy=b.point.y-a.point.y;
    const t=Math.max(0,Math.min(1,((point.x-a.point.x)*dx+(point.y-a.point.y)*dy)/(dx*dx+dy*dy||1)));
    const delta=Math.hypot(point.x-a.point.x-t*dx,point.y-a.point.y-t*dy);
    if(delta<nearest){nearest=delta;fraction=a.fraction+(b.fraction-a.fraction)*t;}
  }
  const raw=physicalSegmentPoints(document,document.physicalTopology!.segments.find(s=>s.id===segmentId)!);
  const length=pathLength(raw);let distance=0;
  return at(raw.map((p,i)=>{if(i)distance+=Math.hypot(p.x-raw[i-1]!.x,p.y-raw[i-1]!.y);return {fraction:length?distance/length:0,point:p};}),fraction);
}

/** Generated convergence controls are kept separate from authored route bends. */
export function pipeBundleTransitionHandles(document:HarnessDesignDocument,coveringId:string){
 const covering=document.physicalTopology?.coverings?.find(c=>c.id===coveringId);if(!covering?.bundle)return [];
 const result:{objectId:string;spanIndex:number;part:"transition-from"|"transition-to";point:Point;normal:Point;halfWidth:number;bound:boolean;tangent:Point;axisLength:number;fraction:number}[]=[];
 const seen = new Set<string>();
 for(const [id,projection] of projections(document))for(const p of projection.placements){
  if(p.coveringId!==coveringId)continue;
  const spanIndex=covering.spans.findIndex(s=>p.axis.ids.includes(s.segmentId));if(spanIndex<0)continue;
  for(const side of ["start","end"] as const){
   const value=side==="start"?Math.max(0,p.start-transition(p)/2):Math.min(1,p.end+transition(p,"end")/2);
   const fraction=localFraction(p,id,value);if(fraction<=0||fraction>=1)continue;
   const a=at(p.axis.samples,Math.max(0,value-.00001)),b=at(p.axis.samples,Math.min(1,value+.00001)),length=distance(a,b)||1;
   const tangent={x:(b.x-a.x)/length,y:(b.y-a.y)/length};
   const bend=side==="start"?covering.bundle.transitionBendStart:covering.bundle.transitionBendEnd;
   const axisPoint=at(p.axis.samples,value),offset=p.bodyOffset??{x:0,y:0};
   const base={x:axisPoint.x+offset.x,y:axisPoint.y+offset.y};
   const point=bend?{x:base.x+bend.x,y:base.y+bend.y}:base;
   const part=side==="start"?"transition-from":"transition-to",key=`${part}:${point.x.toFixed(6)}:${point.y.toFixed(6)}`;
   if(seen.has(key))continue; seen.add(key);
   result.push({objectId:coveringId,spanIndex,part,point,normal:{x:-tangent.y,y:tangent.x},halfWidth:0,bound:false,tangent,axisLength:p.axis.length,fraction:transition(p,side)});
  }
 }
 return result;
}
