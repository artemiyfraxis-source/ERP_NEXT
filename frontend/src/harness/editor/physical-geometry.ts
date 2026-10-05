import type { HarnessDesignDocument, Point } from "./model";
import type { PhysicalNode, PhysicalSegment } from "./physical-topology-model";
import { physicalNodePoint, physicalNodeDirection, physicalNodeFacingDirection, physicalNodeContactDirection } from "./physical-ports";
import { automaticPipeRoute, computePipeRoute, directionTowards, type PipeRouteInput } from "./pipe-routing";
export { physicalNodePoint, physicalNodeLocalPoint, physicalNodeDirection, physicalNodeFacingDirection, physicalNodeContactDirection } from "./physical-ports";
export { automaticPipeRoute, constrainedPolyline } from "./pipe-routing";

export function physicalSegmentControls(document: HarnessDesignDocument, segment: PhysicalSegment): Point[] {
  const t = document.physicalTopology!;
  return [physicalNodePoint(document, t.nodes.find(n => n.id === segment.from)!), ...segment.path.points,
    physicalNodePoint(document, t.nodes.find(n => n.id === segment.to)!)];
}

/** Generated vertices are presentation only; a polyline remains authored even without corners. */
export function physicalSegmentRouteInput(document: HarnessDesignDocument, segment: PhysicalSegment): PipeRouteInput {
  const topology = document.physicalTopology!;
  if (segment.path.points.length || segment.path.kind === "polyline" || !topology.snap) {
    return { kind: "authored", points: physicalSegmentControls(document, segment) };
  }
  const from = topology.nodes.find(n => n.id === segment.from)!;
  const to = topology.nodes.find(n => n.id === segment.to)!;
  const start = physicalNodePoint(document, from), end = physicalNodePoint(document, to);
  return { kind: "automatic", start, end,
    from: physicalNodeDirection(document, from, end), to: physicalNodeDirection(document, to, start) };
}

export function physicalSegmentPoints(document: HarnessDesignDocument, segment: PhysicalSegment): Point[] {
  return computePipeRoute(physicalSegmentRouteInput(document, segment));
}

export function physicalContactTail(document: HarnessDesignDocument, node: PhysicalNode, contactId: string, start: Point, end: Point): Point[] {
  const outward = physicalNodeContactDirection(document, node, contactId) ?? directionTowards(start, end);
  return automaticPipeRoute(start, end, outward, null);
}
