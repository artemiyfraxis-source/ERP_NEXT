import type { EditorCommand } from "./commands";
import type { HarnessDesignDocument, Point } from "./model";
import type { PhysicalDragMode } from "./physical-editing";
import { physicalEditablePoints } from "./physical-editing";
import { physicalTopologyScene } from "./physical-scene";
import { unprojectPipeBundleEdit } from "./pipe-bundle-projection";

/** Resolve against the gesture's saved document. Preview and commit must use
 * the same scene station even after the preview projects a new route. */
export function resolvePhysicalRoutePointCommand(
  document: HarnessDesignDocument,
  id: string,
  index: number,
  target: Point,
  mode: PhysicalDragMode = "carry",
  insert = false,
): EditorCommand | null {
  const topology=document.physicalTopology;
  if(!topology)return null;
  const op=topology.joiningPipes?.find(pipe=>pipe.id===id);
  if(op)return {type:"edit-joining-pipe-bend",pipeId:id,index,position:target,mode,insert};
  const segment=topology.segments.find(item=>item.id===id);
  if(!segment)return null;
  const pipe=physicalTopologyScene(document).find(object=>object.id===id)?.pipe;
  if(!pipe)return null;
  if(!insert){
    const boundary=pipe.joiningBoundaryHandles?.find(handle=>handle.index===index);
    if(boundary){
      const memberPipe=topology.joiningPipes?.find(item=>item.members[boundary.memberIndex]?.segmentIds.includes(id));
      return memberPipe?{type:"update-joining-pipe-member-boundary",pipeId:memberPipe.id,memberIndex:boundary.memberIndex,boundary:boundary.boundary,origin:pipe.handles[index]!,position:target}:null;
    }
    const transition=pipe.joiningTransitionHandles?.find(handle=>handle.index===index);
    if(transition){
      const memberPipe=topology.joiningPipes?.find(item=>item.members[transition.memberIndex]?.segmentIds.includes(id));
      const outer=pipe.handles[index+(transition.side==="enter"?-1:1)];
      return memberPipe?{type:"update-joining-pipe-member-bend",pipeId:memberPipe.id,memberIndex:transition.memberIndex,side:transition.side,position:target,mode,origin:pipe.handles[index],outerOrigin:outer}:null;
    }
    const authoredIndex=pipe.authoredHandleIndices?.[index];
    if(authoredIndex===-1)return null;
    // The displayed handle may be a generated corner of a newly created P in
    // an OP.  Resolve it against the complete automatic source route so the
    // first edit materializes that exact corner instead of treating the grip
    // as a fixed OP station.
    const points=physicalEditablePoints(document,segment,true),at=authoredIndex??index+1;
    if(at<=0||at>=points.length-1)return null;
    // A grip is painted on the rounded display route, while the model retains
    // the sharp authored vertex. Preserve the pointer delta from that display
    // grip so the first drag neither jumps nor writes display coordinates into
    // the physical route. Bundled members use the same rule via their own
    // projection helper.
    const displayOrigin=pipe.handles[index] ?? points[at]!;
    const initial=unprojectPipeBundleEdit(document,id,points[at]!,target);
    const position=initial===target
      ? {x:points[at]!.x+target.x-displayOrigin.x,y:points[at]!.y+target.y-displayOrigin.y}
      : initial;
    return {type:"edit-physical-bend",segmentId:id,index:at-1,position,mode,insert:false,displayPosition:target};
  }
  const transition=pipe.joiningTransitionMidpoints?.find(handle=>handle.index===index);
  if(transition){
    const memberPipe=topology.joiningPipes?.find(item=>item.members[transition.memberIndex]?.segmentIds.includes(id));
    if(memberPipe&&memberPipe.members[transition.memberIndex]?.[transition.side==="enter"?"enterBend":"exitBend"]==null)
      return {type:"update-joining-pipe-member-bend",pipeId:memberPipe.id,memberIndex:transition.memberIndex,side:transition.side,position:target};
  }
  if(pipe.controlledMidpoints?.includes(index))return null;
  const station=pipe.midpointSources?.[index],origin=pipe.midpoints?.[index];
  if(!station||!origin)return null;
  return {type:"edit-physical-bend",segmentId:id,index:station.index,
    position:{x:station.point.x+target.x-origin.x,y:station.point.y+target.y-origin.y},mode,insert:true,displayPosition:target,
    displayOrigin:origin,region:pipe.midpointRegions?.[index]};
}
