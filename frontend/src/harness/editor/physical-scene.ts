import type { HarnessDesignDocument } from "./model";
import type { EditorPoint, EditorSceneObject } from "./editor-types";
import { physicalSegmentControls, physicalSegmentPoints } from "./physical-geometry";
import { physicalNodePoint, physicalNodeFacingDirection } from "./physical-ports";
import { drawingPipeWidth } from "./drawing-thickness";
import { defaultLayerIds } from "./model";
import { physicalEditablePoints } from "./physical-editing";
import { drawingBendRadius, drawingRouteSamples, projectOntoDrawingRoute } from "./drawing-route-path";
import { hasPipeBundleProjection, pipeBundleDisplaySamples, projectPipeBundleControls, pipeBundleNodePoint,pipeBundleDepth } from "./pipe-bundle-projection";
import { joiningPipePoints, joiningPipeEndpointId, joiningPipeExitId, joiningPipeExitPoint } from "./physical-joining-pipes";
import { joiningPipeDisplaySamples,joiningPipeMemberControls,joiningPipeWidth,joiningPipeControlsMemberStation,joiningPipeAuthoredHandle,joiningPipeMidpointRegion } from "./physical-joining-pipe-projection";
import {projectOntoPolyline} from "./physical-coverings";
import type { JoiningPipeBendRegion } from "./physical-topology-model";

function pointAtRouteFraction(points:readonly EditorPoint[],fraction:number):EditorPoint {
  const lengths=points.slice(1).map((point,index)=>Math.hypot(point.x-points[index]!.x,point.y-points[index]!.y));
  let remaining=fraction*lengths.reduce((sum,length)=>sum+length,0);
  for(let index=0;index<lengths.length;index++){
    const length=lengths[index]!;
    if(remaining<=length||index===lengths.length-1){const t=length?Math.max(0,Math.min(1,remaining/length)):0;
      return {x:points[index]!.x+(points[index+1]!.x-points[index]!.x)*t,y:points[index]!.y+(points[index+1]!.y-points[index]!.y)*t};}
    remaining-=length;
  }
  return points[0]!;
}

function joiningPipeDepth(document:HarnessDesignDocument,id:string,visited= new Set<string>()):number {
  const pipe=document.physicalTopology?.joiningPipes?.find(item=>item.id===id);
  if(!pipe||visited.has(id))return 0;
  const next=new Set(visited);next.add(id);
  const children=pipe.members.flatMap(member=>member.segmentIds.filter(segmentId=>document.physicalTopology?.joiningPipes?.some(item=>item.id===segmentId)));
  return children.length ? 1+Math.max(...children.map(child=>joiningPipeDepth(document,child,next))) : 0;
}

/** Stack order inside the drawing wires layer. The numeric bands leave room
 * for nested OPs and coverings while keeping the electrical wire at the base. */
export function physicalSceneStackOrder(document:HarnessDesignDocument,object:EditorSceneObject):number {
  if(object.kind==="wire")return 0;
  if(object.kind==="physical-segment"){
    return object.pipe?.role==="joining-pipe" ? 200+joiningPipeDepth(document,object.id)*100 : 100;
  }
  if(object.kind==="physical-covering"){
    const covering=document.physicalTopology?.coverings?.find(item=>item.id===object.id);
    const joiningDepths=covering?.spans.flatMap(span=>document.physicalTopology?.joiningPipes?.filter(pipe=>pipe.id===span.segmentId).map(pipe=>joiningPipeDepth(document,pipe.id))??[])??[];
    return joiningDepths.length ? 250+Math.max(...joiningDepths)*100 : 150;
  }
  return 0;
}

export function orderPhysicalScene(document:HarnessDesignDocument,objects:readonly EditorSceneObject[]):EditorSceneObject[] {
  return objects.map((object,index)=>({object,index})).sort((a,b)=>physicalSceneStackOrder(document,a.object)-physicalSceneStackOrder(document,b.object)||a.index-b.index).map(item=>item.object);
}

/** One boundary between physical topology and presentation. */
export function physicalTopologyScene(document: HarnessDesignDocument): EditorSceneObject[] {
  const topology = document.physicalTopology;
  if (!topology) return [];
  const segments: EditorSceneObject[] = topology.segments.map((segment, i) => {
    // Controls and grips use the same projection as the painted pipe so a
    // grouped member remains directly editable on its visible path.
    const controls = projectPipeBundleControls(document, segment.id, physicalSegmentControls(document, segment));
    const memberOfOp=topology.joiningPipes?.some(pipe=>pipe.members.some(member=>member.segmentIds.includes(segment.id)))??false;
    const authored = memberOfOp?physicalSegmentControls(document,segment):physicalEditablePoints(document, segment);
    const editable = projectPipeBundleControls(document, segment.id, authored);
    const display = pipeBundleDisplaySamples(document, segment.id);
    const route=physicalSegmentPoints(document,segment);
    const controlled=(point:EditorPoint)=>joiningPipeControlsMemberStation(document,segment.id,projectOntoPolyline(route,point).fraction);
    const generatedControls=joiningPipeMemberControls(document,segment.id);
    type SceneControl={fraction:number;point:EditorPoint;controlled:boolean;authoredIndex?:number;authoredRegion?:JoiningPipeBendRegion;connection?:boolean;boundary?:"outerEnter"|"axisEnter"|"axisExit"|"outerExit";lead?:"enter"|"exit";memberIndex?:number;transition?:{readonly memberIndex:number;readonly side:"enter"|"exit"}};
    const member=topology.joiningPipes?.flatMap(pipe=>pipe.members).find(item=>item.segmentIds.includes(segment.id));
    const authoredHandles:SceneControl[]=editable.slice(1,-1).map((point,index)=>{
      const fixed=joiningPipeAuthoredHandle(document,segment.id,index),fraction=fixed?.fraction??projectOntoPolyline(route,authored[index+1]!).fraction;
      const authoredRegion=member?.authoredBendRegions?.find(entry=>entry.segmentId===segment.id&&entry.bendIndex===index)?.region;
      return {fraction,point:fixed?.point??point,authoredIndex:index+1,authoredRegion,controlled:fixed?.controlled??controlled(authored[index+1]!)};
    });
    const generated:SceneControl[]=generatedControls?.map(control=>({fraction:control.fraction,point:control.point,controlled:control.controlled,connection:control.connection,boundary:control.boundary,lead:control.lead,memberIndex:control.memberIndex,transition:control.transition}))??[];
    const mergedHandles:SceneControl[]=[...authoredHandles,...generated].sort((a,b)=>a.fraction-b.fraction)
      .reduce<SceneControl[]>((handles,handle)=>{
        const previous=handles.at(-1);
        if(previous&&Math.abs(handle.fraction-previous.fraction)<=1e-7&&
           (previous.authoredIndex===undefined||handle.authoredIndex===undefined)){
          // The visible member route has one station per fraction. If an
          // authored bend lands on an OP boundary, the generated OP station
          // owns it and prevents a zero-length artificial kink.
          if(handle.authoredIndex===undefined)handles[handles.length-1]=handle;
          return handles;
        }
        handles.push(handle);return handles;
      },[]);
    const handles=mergedHandles.map(handle=>handle.point);
    const authoredHandleIndices=mergedHandles.map(handle=>handle.authoredIndex??-1);
    const authoredHandleRegions=mergedHandles.map(handle=>handle.authoredRegion);
    const joiningTransitionHandleData=mergedHandles.flatMap((handle,index)=>handle.transition?[{index,memberIndex:handle.transition.memberIndex,side:handle.transition.side}]:[]);
    const joiningBoundaryHandleData=mergedHandles.flatMap((handle,index)=>handle.boundary?[{index,memberIndex:handle.memberIndex!,boundary:handle.boundary}]:[]);
    const controlledHandleIndices=mergedHandles.flatMap((handle,index)=>handle.controlled?[index]:[]);
    const routeControls:SceneControl[]=[{fraction:0,point:editable[0]!,controlled:false},...mergedHandles,{fraction:1,point:editable.at(-1)!,controlled:false}];
    const painted=display?.map(sample=>sample.point)??route;
    const radius=display&&!joiningPipeDisplaySamples(document,segment.id)?0:drawingBendRadius(document);
    const rounded=drawingRouteSamples(painted,radius);
    const pointAtDistance=(distance:number):EditorPoint=>{
      const next=rounded.findIndex(sample=>sample.distance>=distance);
      const at=next<0?rounded.length-1:next;
      const b=rounded[at]!,a=rounded[Math.max(0,at-1)]!;
      const t=b.distance===a.distance?0:Math.max(0,Math.min(1,(distance-a.distance)/(b.distance-a.distance)));
      return {x:a.point.x+(b.point.x-a.point.x)*t,y:a.point.y+(b.point.y-a.point.y)*t};
    };
    const midpointFractions=routeControls.slice(1).map((control,index)=>(control.fraction+routeControls[index]!.fraction)/2);
    const midpointRegions=midpointFractions.map(fraction=>joiningPipeMidpointRegion(document,segment.id,fraction));
    const midpoints=generatedControls
      ? routeControls.slice(1).map((control,index)=>{
          const previous=routeControls[index]!,a=projectOntoDrawingRoute(painted,radius,previous.point),b=projectOntoDrawingRoute(painted,radius,control.point);
          return pointAtDistance((a+b)/2);
        })
      : projectPipeBundleControls(document,segment.id,authored.slice(1).map((point,index)=>({
          x:(point.x+authored[index]!.x)/2,y:(point.y+authored[index]!.y)/2})));
    const midpointSources=midpoints.map((_,index)=>{
      const sourcePoint=generatedControls?pointAtRouteFraction(route,midpointFractions[index]!):{
        x:(authored[index]!.x+authored[index+1]!.x)/2,y:(authored[index]!.y+authored[index+1]!.y)/2};
      const insertion=projectOntoPolyline(authored,sourcePoint).index;
      return {index:insertion-1,point:sourcePoint};
    });
    const controlledMidpoints=generatedControls
      ? routeControls.slice(1).flatMap((point,index)=>{
          const previous=routeControls[index]!;
          const removedTransition=previous.boundary==="outerEnter"&&point.boundary==="axisEnter"||previous.boundary==="axisExit"&&point.boundary==="outerExit";
          const authoredShoulder=previous.authoredIndex!==undefined||point.authoredIndex!==undefined;
          const transitionShoulder=previous.transition||point.transition||removedTransition;
          const isTransition=previous.boundary==="outerEnter"&&point.lead==="enter"||previous.lead==="exit"&&point.boundary==="outerExit";
          return (point.controlled||previous.controlled)&&!authoredShoulder&&!transitionShoulder&&!isTransition?[index]:[];
        })
      : authored.slice(1).flatMap((p,i)=>controlled({x:(p.x+authored[i]!.x)/2,y:(p.y+authored[i]!.y)/2})?[i]:[]);
    const joiningTransitionMidpoints=generatedControls
      ? routeControls.slice(1).flatMap((point,index)=>{
          const previous=routeControls[index]!;
          const directSide=previous.boundary==="outerEnter"&&(point.lead==="enter"||point.boundary==="axisEnter")?"enter":(previous.lead==="exit"||previous.boundary==="axisExit")&&point.boundary==="outerExit"?"exit":undefined;
          const transition=point.transition??previous.transition??(directSide?{memberIndex:point.memberIndex!,side:directSide}:undefined);
          return transition&&!(point.transition&&routeControls[index]!.transition)?[{index,memberIndex:transition.memberIndex,side:transition.side}]:[];
        })
      : [];
    return {
    id: segment.id, kind: "physical-segment", label: `S${i + 1}`, layerId: "wires",
    x: 0, y: 0, width: drawingPipeWidth(document, segment), height: 0,
    color: segment.color ?? "#aebfc9", points: display?.map(s => s.point) ?? physicalSegmentPoints(document, segment),
    // The transition contains its authored corner and no extra collinear
    // stations, so the global radius can round the full adjacent legs.
    routeRadius:joiningPipeDisplaySamples(document,segment.id) ? drawingBendRadius(document) : display ? 0 : drawingBendRadius(document),
    pipe: {
      joiningMember:memberOfOp,
      fromNodeId: segment.from,
      toNodeId: segment.to,
      authoredPoints: authored,
      controls: controls,
      handles,
      authoredHandleIndices,
      authoredHandleRegions,
      midpoints,
      midpointSources,
      midpointRegions,
      controlledHandles:controlledHandleIndices,
      controlledMidpoints,
      joiningTransitionHandles:joiningTransitionHandleData,
      joiningBoundaryHandles:joiningBoundaryHandleData,
      joiningTransitionMidpoints,
      wireIds: topology.routes.filter(route => route.steps.some(step => step.segmentId === segment.id)).map(route => route.wireId),
    },
    metadata:{opacity:String(document.drawingDocuments?.pipeOpacity??.72),volumeShading:String(segment.volumeShading??document.drawingDocuments?.volumeShading!==false)},
    };
  });
  const joining: EditorSceneObject[] = (topology.joiningPipes??[]).map((pipe,i)=>{
    const authored=joiningPipePoints(pipe), display=authored;
    const controls=authored,handles=pipe.path.points;
    const midpoints=authored.slice(1).map((p,j)=>({x:(p.x+authored[j]!.x)/2,y:(p.y+authored[j]!.y)/2}));
    const wireIds=[...new Set(pipe.members.flatMap(m=>m.segmentIds).flatMap(id=>topology.routes.filter(route=>route.steps.some(step=>step.segmentId===id)).map(route=>route.wireId)))];
    return {id:pipe.id,kind:"physical-segment" as const,label:`ОП${i+1}`,layerId:"wires",x:0,y:0,width:joiningPipeWidth(document,pipe),height:0,color:pipe.color??"#aebfc9",points:display,paths:[display],routeRadius:drawingBendRadius(document),
      pipe:{role:"joining-pipe" as const,authoredPoints:authored,fromNodeId:joiningPipeEndpointId(pipe.id,"from"),toNodeId:joiningPipeEndpointId(pipe.id,"to"),controls,handles,midpoints,wireIds,memberSegmentIds:pipe.members.flatMap(m=>m.segmentIds)},
      metadata:{joiningPipe:"true",opacity:String(document.drawingDocuments?.pipeOpacity??.72),volumeShading:String(pipe.volumeShading??document.drawingDocuments?.volumeShading!==false)}};
  });
  const nodes: EditorSceneObject[] = topology.nodes.map((node, i) => {
    const point = pipeBundleNodePoint(document,node.id,physicalNodePoint(document, node));
    return { id: node.id, kind: "physical-node", label: node.connectorId ? "Выход" : `Узел ${i + 1}`,
      layerId: defaultLayerIds.connectionPoints, x: point.x - 5, y: point.y - 5, width: 10, height: 10,
      color: node.connectorId ? "#f59e0b" : "#1179ac",
      metadata: { nodeRole: node.connectorId ? "connector-exit" : "junction",
        bundleMember: String(topology.segments.some(segment =>
          (segment.from === node.id || segment.to === node.id) && hasPipeBundleProjection(document,segment.id))) },
      port: { connectorId: node.connectorId, direction: physicalNodeFacingDirection(document, node) } };
  });
  const joiningEnds:EditorSceneObject[]=(topology.joiningPipes??[]).flatMap((p,i)=>(["from","to"] as const).map(side=>{
    const point=side==="from"?p.start:p.end;
    return {id:joiningPipeEndpointId(p.id,side),kind:"physical-node",label:`ОП${i+1} · ${side==="from"?"начало":"конец"}`,layerId:defaultLayerIds.connectionPoints,
      x:point.x-5,y:point.y-5,width:10,height:10,color:"#8555ad",metadata:{joiningPipe:p.id,bundleMember:"true"},port:{direction:null}};
  }));
  const joiningExits:EditorSceneObject[]=(topology.joiningPipes??[]).flatMap((p,i)=>(["from","to"] as const).map(side=>{
    const point=joiningPipeExitPoint(p,side);
    return {id:joiningPipeExitId(p.id,side),kind:"physical-node",label:`ОП${i+1} · выход`,layerId:defaultLayerIds.connectionPoints,
      x:point.x-5,y:point.y-5,width:10,height:10,color:"#a05cce",metadata:{joiningPipeExit:p.id,bundleMember:"true"},port:{direction:null}} as EditorSceneObject;
  }));
  return [...segments.sort((a,b)=>pipeBundleDepth(document,a.id)-pipeBundleDepth(document,b.id)), ...joining.sort((a,b)=>joiningPipeDepth(document,a.id)-joiningPipeDepth(document,b.id)), ...nodes,...joiningEnds,...joiningExits];
}

export function pipeSceneControls(object: EditorSceneObject): readonly EditorPoint[] {
  return object.pipe?.controls ?? [];
}
export function pipeSceneHandles(object: EditorSceneObject): readonly EditorPoint[] {
  return object.pipe?.handles ?? [];
}
export function pipeSceneEditablePoints(object: EditorSceneObject): readonly EditorPoint[] {
  return object.points?.length ? [object.points[0]!,...pipeSceneHandles(object),object.points.at(-1)!] : [];
}
export function pipeSceneWireIds(object: EditorSceneObject | undefined): readonly string[] {
  return object?.pipe?.wireIds ?? [];
}
