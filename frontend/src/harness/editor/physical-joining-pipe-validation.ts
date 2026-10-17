import type {PhysicalTopology} from "./physical-topology-model";
import type {Point} from "./model";

/** Persistence validation shared by loads, edits and previews. One P has one OP. */
export function validateJoiningPipes(t:PhysicalTopology,existingIds:Set<string>):void {
  if(t.joiningPipes===undefined)return;
  const fail=():never=>{throw new Error("Некорректный ОП: проверьте ось, границы и уникальный состав пайпов.");};
  if(!Array.isArray(t.joiningPipes)||t.joiningPipes.length>10000)return fail();
  const segments=new Map(t.segments.map(s=>[s.id,s])),owned=new Set<string>();
  const point=(p:Point)=>p&&Number.isFinite(p.x)&&Number.isFinite(p.y)&&Math.abs(p.x)<=1e7&&Math.abs(p.y)<=1e7;
  for(const p of t.joiningPipes){
    if(!p||typeof p.id!=="string"||!p.id.trim()||p.id.length>120||existingIds.has(p.id)||existingIds.has(`${p.id}:from`)||existingIds.has(`${p.id}:to`))return fail();
    for(const id of [p.id,`${p.id}:from`,`${p.id}:to`])existingIds.add(id);
    if("bends" in p||"routing" in p||!point(p.start)||!point(p.end)||!p.path||p.path.kind!=="polyline"||!Array.isArray(p.path.points)||p.path.points.length>1000||!p.path.points.every(point))return fail();
    const axis=[p.start,...p.path.points,p.end];
    if(axis.slice(1).every((q,i)=>Math.hypot(q.x-axis[i]!.x,q.y-axis[i]!.y)<1e-7))return fail();
    if(p.mode!=="flat"&&p.mode!=="round"||p.width!==undefined&&(!Number.isFinite(p.width)||p.width<0||p.width>1e7)||p.color!==undefined&&!/^#[\da-f]{6}$/i.test(p.color)||p.opacity!==undefined&&(!Number.isFinite(p.opacity)||p.opacity<0||p.opacity>1)||p.volumeShading!==undefined&&typeof p.volumeShading!=="boolean"||[p.enterLength,p.exitLength].some(value=>value!==undefined&&(!Number.isFinite(value)||value<0||value>1e7)))return fail();
    if(!Array.isArray(p.members)||p.members.length<2||p.members.length>128)return fail();
    for(const m of p.members){
      if(!m||!Array.isArray(m.segmentIds)||!m.segmentIds.length||m.segmentIds.length>128||!Number.isFinite(m.from)||!Number.isFinite(m.to)||m.from<=0||m.to>=1||m.from>=m.to||typeof m.reverse!=="boolean"||m.enterBend!==undefined&&m.enterBend!==null&&!point(m.enterBend)||m.exitBend!==undefined&&m.exitBend!==null&&!point(m.exitBend)||m.enterOuter!==undefined&&!point(m.enterOuter)||m.exitOuter!==undefined&&!point(m.exitOuter))return fail();
      let previous:string|undefined;const visited=new Set<string>();
      for(const id of m.segmentIds){
        const s=segments.get(id);if(!s||owned.has(id)||previous!==undefined&&s.from!==previous)return fail();
        if(previous===undefined)visited.add(s.from);
        if(visited.has(s.to))return fail();visited.add(s.to);
        owned.add(id);previous=s.to;
      }
      if(m.authoredBendRegions!==undefined){
        if(!Array.isArray(m.authoredBendRegions)||m.authoredBendRegions.length>m.segmentIds.reduce((sum:number,id:string)=>sum+(segments.get(id)?.path.points.length??0),0))return fail();
        const seen=new Set<string>();
        for(const entry of m.authoredBendRegions){
          const segment=segments.get(entry?.segmentId),key=`${entry?.segmentId}:${entry?.bendIndex}`;
          if(!segment||!m.segmentIds.includes(entry.segmentId)||!Number.isInteger(entry.bendIndex)||entry.bendIndex<0||entry.bendIndex>=segment.path.points.length||
            !["before-enter","enter","axis","exit","after-exit"].includes(entry.region)||entry.displayPoint!==undefined&&!point(entry.displayPoint)||seen.has(key))return fail();
          seen.add(key);
        }
      }
    }
  }
}
