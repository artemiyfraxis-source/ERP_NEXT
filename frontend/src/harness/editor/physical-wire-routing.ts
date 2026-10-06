import type { HarnessDesignDocument } from "./model";
import type { PhysicalTopology, PhysicalRoute, PhysicalStep } from "./physical-topology-model";
import { physicalSegmentPoints } from "./physical-geometry";
import { pathLength } from "./physical-coverings";

interface RouteEdge {
  readonly node: string;
  readonly step: PhysicalStep;
  readonly cost: number;
}
type RouteGraph = ReadonlyMap<string, readonly RouteEdge[]>;

/** Geometry supplies weights once, independently of wire eligibility and path search. */
function buildRouteGraph(document: HarnessDesignDocument, topology: PhysicalTopology): RouteGraph {
  const graph = new Map<string, RouteEdge[]>();
  const routedDocument = { ...document, physicalTopology: topology };
  const measuredLengths=new Map<string,number>();
  const geometricLengths=new Map(topology.segments.map(s=>[s.id,pathLength(physicalSegmentPoints(routedDocument,s))]));
  for (const segment of topology.segments) {
    const routeKey=JSON.stringify([segment.id,segment.from,segment.to,segment.path.points.length]);
    const dimensions=document.drawingDocuments?.dimensions?.filter(d=>d.segmentId===segment.id&&d.routeKey===routeKey)??[];
    const total=dimensions.find(d=>d.from===0&&d.to===segment.path.points.length+1);
    const ordered=[...dimensions].sort((a,b)=>a.from-b.from);
    let next=0,measured=0,complete=ordered.length>0;
    for(const d of ordered){if(d.from!==next||d.lengthMm===null)complete=false;next=d.to;measured+=d.lengthMm??0;}
    const length=total?.lengthMm??(complete&&next===segment.path.points.length+1?measured:null);
    if(length!==null)measuredLengths.set(segment.id,length);
  }
  // Unknown lengths use a drawing estimate in the same scale, never raw pixels
  // competing against measured millimetres. This estimate is not stored as length.
  const references=[...measuredLengths].filter(([id,n])=>n>0&&geometricLengths.get(id)!>1e-7);
  const scale=references.length?references.reduce((sum,[,n])=>sum+n,0)/references.reduce((sum,[id])=>sum+geometricLengths.get(id)!,0):1;
  for (const segment of topology.segments) {
    const cost = Math.max(.001, measuredLengths.get(segment.id)??geometricLengths.get(segment.id)!*scale);
    for (const [from, to, reverse] of [[segment.from, segment.to, false], [segment.to, segment.from, true]] as const) {
      if (!graph.has(from)) graph.set(from, []);
      graph.get(from)!.push({ node: to, step: { segmentId: segment.id, reverse }, cost });
    }
  }
  return graph;
}

/** A connector is a terminal, never an intermediate physical junction. */
function shortestRoute(graph: RouteGraph, sources: readonly string[], targets: ReadonlySet<string>,
  terminals: ReadonlySet<string>): PhysicalStep[] | undefined {
  const distance = new Map(sources.map(id => [id, 0]));
  const previous = new Map<string, { node: string; step: PhysicalStep }>();
  const queue = new Set(sources);
  while (queue.size) {
    const id = [...queue].sort((a, b) => distance.get(a)! - distance.get(b)! || a.localeCompare(b))[0]!;
    queue.delete(id);
    if (targets.has(id)) {
      const steps: PhysicalStep[] = [];
      let current = id;
      while (previous.has(current)) {
        const parent = previous.get(current)!;
        steps.unshift(parent.step);
        current = parent.node;
      }
      return steps;
    }
    if (terminals.has(id) && !sources.includes(id)) continue;
    for (const edge of graph.get(id) ?? []) {
      const cost = distance.get(id)! + edge.cost;
      if (cost < (distance.get(edge.node) ?? Infinity) - 1e-8) {
        distance.set(edge.node, cost);
        previous.set(edge.node, { node: id, step: edge.step });
        queue.add(edge.node);
      }
    }
  }
  return undefined;
}

function exitsAcceptRoute(topology: PhysicalTopology, route: PhysicalRoute): boolean {
  const first = route.steps[0], last = route.steps.at(-1);
  if (!first || !last) return false;
  const start = topology.segments.find(segment => segment.id === first.segmentId);
  const end = topology.segments.find(segment => segment.id === last.segmentId);
  if (!start || !end) return false;
  return [first.reverse ? start.to : start.from, last.reverse ? end.from : end.to].every(id => {
    const node = topology.nodes.find(node => node.id === id);
    return node && (!node.wireIds || node.wireIds.includes(route.wireId));
  });
}

/** Explicit assignment changes only routes. Drawing distances are not manufacturing millimetres. */
export function routePhysicalWires(document: HarnessDesignDocument, topology: PhysicalTopology): PhysicalTopology {
  const routes = topology.routes.filter(route => !route.automatic && exitsAcceptRoute(topology, route));
  const pinned = new Set(routes.map(route => route.wireId));
  const graph = buildRouteGraph(document, topology);
  const terminals = new Set(topology.nodes.filter(node => node.connectorId).map(node => node.id));
  for (const wire of document.wires) {
    if (pinned.has(wire.id) || !wire.from.connectorId || !wire.to.connectorId) continue;
    const eligible = (connectorId: string) => topology.nodes
      .filter(node => node.connectorId === connectorId && (!node.wireIds || node.wireIds.includes(wire.id)))
      .map(node => node.id);
    const steps = shortestRoute(graph, eligible(wire.from.connectorId), new Set(eligible(wire.to.connectorId)), terminals);
    if (steps?.length) routes.push({ wireId: wire.id, steps, automatic: true });
  }
  return { ...topology, routes };
}

/** Refresh on graph/placement changes, never on selection or style-only edits. */
export function refreshAutomaticPhysicalRoutes(before:HarnessDesignDocument,after:HarnessDesignDocument):HarnessDesignDocument {
  const t=after.physicalTopology;if(!t||!before.physicalTopology)return after;
  const key=(d:HarnessDesignDocument)=>JSON.stringify([d.physicalTopology?.snap,d.physicalTopology?.nodes,
    d.physicalTopology?.segments.map(s=>[s.id,s.from,s.to,s.path]),
    d.connectors.map(c=>[c.id,c.positions.drawing,c.drawingPlacements]),
    d.drawingDocuments?.dimensions?.filter(d=>d.segmentId).map(d=>[d.segmentId,d.from,d.to,d.lengthMm])]);
  if(key(before)===key(after))return after;
  return {...after,physicalTopology:routePhysicalWires(after,t)};
}
