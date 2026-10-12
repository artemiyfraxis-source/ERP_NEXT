import type { NumericExpressionV2, TemplateNodeV2 } from "./template-model-v2";

export interface DrawingPoint { x: number; y: number }
export interface DrawingSnaps { corners: boolean; contours: boolean; tangents: boolean }
export const defaultDrawingSnaps: DrawingSnaps = { corners: true, contours: true, tangents: true };
type Evaluate = (value: NumericExpressionV2) => number | null;
export interface DrawingOutline { id: string; points: DrawingPoint[]; closed: boolean; corners?: DrawingPoint[]; curved?: boolean; circle?: { center: DrawingPoint; radius: number } }

export function drawingOutline(node: TemplateNodeV2, evaluate: Evaluate): DrawingOutline | null {
  const values = Object.fromEntries(Object.entries(node.transform).map(([key, value]) => [key, evaluate(value)]));
  if (Object.values(values).some(value => value === null)) return null;
  const angle = values.rotationDegrees! * Math.PI / 180;
  const world = (x: number, y: number): DrawingPoint => ({
    x: values.translateX! + x * values.scaleX! * Math.cos(angle) - y * values.scaleY! * Math.sin(angle),
    y: values.translateY! + x * values.scaleX! * Math.sin(angle) + y * values.scaleY! * Math.cos(angle),
  });
  const n = (value: NumericExpressionV2) => { const result = evaluate(value); if (result === null) throw new Error(); return result; };
  try {
    if (node.kind === "group") return null;
    if (node.kind === "bezier") {
      const control = node.geometry.points.map(p => world(n(p.x), n(p.y)));
      const points: DrawingPoint[] = [];
      for (let i = 0; i + 3 < control.length; i += 3) for (let step = 0; step <= 64; step++) {
        const t = step / 64, u = 1 - t, a = control[i]!, b = control[i+1]!, c = control[i+2]!, d = control[i+3]!;
        points.push({x:u*u*u*a.x+3*u*u*t*b.x+3*u*t*t*c.x+t*t*t*d.x,y:u*u*u*a.y+3*u*u*t*b.y+3*u*t*t*c.y+t*t*t*d.y});
      }
      return {id:node.id,points,closed:node.geometry.closed,corners:control.filter((_,i) => i % 3 === 0),curved:true};
    }
    if ("points" in node.geometry) return { id: node.id, points: node.geometry.points.map(p => world(n(p.x), n(p.y))), closed: node.kind === "closedContour" };
    if (node.kind === "ellipse") {
      const cx = n(node.geometry.centerX), cy = n(node.geometry.centerY), rx = n(node.geometry.radiusX), ry = n(node.geometry.radiusY);
      const radius = Math.abs(rx * values.scaleX!);
      return { id: node.id, closed: true, corners: [], curved: true,
        points: Array.from({ length: 96 }, (_, i) => world(cx + rx * Math.cos(i * Math.PI / 48), cy + ry * Math.sin(i * Math.PI / 48))),
        ...(Math.abs(radius - Math.abs(ry * values.scaleY!)) < 1e-6 ? { circle: { center: world(cx, cy), radius } } : {}),
      };
    }
    const x = n(node.geometry.x), y = n(node.geometry.y) - (node.kind === "text" ? n(node.geometry.fontSize) : 0);
    const w = node.kind === "text" ? n(node.geometry.fontSize) * node.geometry.text.length * 0.6 : n(node.geometry.width);
    const h = node.kind === "text" ? n(node.geometry.fontSize) : n(node.geometry.height);
    return { id: node.id, closed: true, points: [world(x, y), world(x + w, y), world(x + w, y + h), world(x, y + h)] };
  } catch { return null; }
}

export function drawingCenter(outlines: readonly DrawingOutline[]): DrawingPoint | null {
  const points = outlines.flatMap(outline => outline.points);
  if (!points.length) return null;
  return { x: (Math.min(...points.map(p => p.x)) + Math.max(...points.map(p => p.x))) / 2,
    y: (Math.min(...points.map(p => p.y)) + Math.max(...points.map(p => p.y))) / 2 };
}

/** Walks the ownership tree so grouped contours use their actual world position. */
export function drawingLayerOutlines(nodes: readonly TemplateNodeV2[], evaluate: Evaluate): DrawingOutline[] {
  const owned = new Set(nodes.flatMap(node => node.kind === "group" ? node.geometry.childIds : []));
  const byId = new Map(nodes.map(node => [node.id, node]));
  const walk = (node: TemplateNodeV2, ancestors: Set<string>): DrawingOutline[] => {
    if (!node.visible || ancestors.has(node.id)) return [];
    if (node.kind !== "group") { const outline = drawingOutline(node, evaluate); return outline ? [outline] : []; }
    const values = Object.fromEntries(Object.entries(node.transform).map(([key,value]) => [key,evaluate(value)]));
    if (Object.values(values).some(value => value === null)) return [];
    const angle = values.rotationDegrees! * Math.PI / 180, sx=values.scaleX!, sy=values.scaleY!;
    const world = (point:DrawingPoint):DrawingPoint => ({x:values.translateX!+point.x*sx*Math.cos(angle)-point.y*sy*Math.sin(angle),y:values.translateY!+point.x*sx*Math.sin(angle)+point.y*sy*Math.cos(angle)});
    return node.geometry.childIds.flatMap(id => { const child = byId.get(id); return child ? walk(child,new Set([...ancestors,node.id])) : []; }).map(outline => ({
      ...outline,id:node.id,points:outline.points.map(world),corners:outline.corners?.map(world),
      circle:outline.circle && Math.abs(Math.abs(sx)-Math.abs(sy))<1e-8 ? {center:world(outline.circle.center),radius:outline.circle.radius*Math.abs(sx)} : undefined,
    }));
  };
  return nodes.filter(node => !owned.has(node.id)).flatMap(node => walk(node,new Set()));
}

export function circleTangents(anchor: DrawingPoint, center: DrawingPoint, radius: number): DrawingPoint[] {
  const dx = anchor.x - center.x, dy = anchor.y - center.y, distance = Math.hypot(dx, dy);
  if (radius <= 0 || distance < radius) return [];
  const angle = Math.atan2(dy, dx), offset = Math.acos(radius / distance);
  return [angle - offset, angle + offset].map(a => ({ x: center.x + radius * Math.cos(a), y: center.y + radius * Math.sin(a) }));
}

export function snapDrawingPoint(point: DrawingPoint, targets: readonly DrawingOutline[], settings: DrawingSnaps,
  tolerance: number, anchor?: DrawingPoint | null): DrawingPoint {
  const candidates: DrawingPoint[] = [];
  for (const target of targets) {
    if (settings.tangents && anchor && target.circle) candidates.push(...circleTangents(anchor, target.circle.center, target.circle.radius));
    if (settings.corners && !target.circle) candidates.push(...(target.corners ?? target.points));
  }
  const nearest = (values: DrawingPoint[]) => values.map(p => ({ p, d: Math.hypot(point.x - p.x, point.y - p.y) }))
    .filter(item => item.d <= tolerance).sort((a, b) => a.d - b.d)[0]?.p;
  const feature = nearest(candidates);
  if (feature) return feature;
  if (settings.contours) for (const target of targets) {
    if (target.circle) {
      const { center, radius } = target.circle, d = Math.hypot(point.x - center.x, point.y - center.y);
      if (d > 0) candidates.push({ x: center.x + (point.x - center.x) * radius / d, y: center.y + (point.y - center.y) * radius / d });
    } else for (let i = 0; i < target.points.length - (target.closed ? 0 : 1); i++) {
      const a = target.points[i]!, b = target.points[(i + 1) % target.points.length]!;
      const dx = b.x - a.x, dy = b.y - a.y, norm = dx * dx + dy * dy;
      const t = norm === 0 ? 0 : Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / norm));
      candidates.push({ x: a.x + t * dx, y: a.y + t * dy });
    }
  }
  return nearest(candidates) ?? point;
}

/** Translates a straight side onto a circle tangent without changing its angle. */
export function snapDrawingTranslation(moving: DrawingOutline, delta: DrawingPoint, targets: readonly DrawingOutline[], settings: DrawingSnaps, tolerance: number): DrawingPoint {
  let correction: DrawingPoint | null = null, best = tolerance;
  let correctionX: number | undefined, correctionY: number | undefined;
  const accept = (x: number, y: number) => {
    const d = Math.hypot(x, y);
    // Coincident horizontal edges must not suppress a nearby vertical edge.
    if (d < 1e-8 || d > tolerance) return;
    if (Math.abs(y) < 1e-8 && (correctionX === undefined || Math.abs(x) < Math.abs(correctionX))) correctionX = x;
    else if (Math.abs(x) < 1e-8 && (correctionY === undefined || Math.abs(y) < Math.abs(correctionY))) correctionY = y;
    else if (d < best) { best = d; correction = { x, y }; }
  };
  const points = moving.points.map(p => ({ x: p.x + delta.x, y: p.y + delta.y }));
  // Compare parallel sides separately: nearest-point projection can return an
  // exact horizontal hit and hide the equally relevant vertical alignment.
  if (settings.contours && !moving.curved) for (const target of targets) if (!target.curved) {
    for (let i=0;i<points.length-(moving.closed?0:1);i++) for(let j=0;j<target.points.length-(target.closed?0:1);j++) {
      const a=points[i]!, b=points[(i+1)%points.length]!, c=target.points[j]!, d=target.points[(j+1)%target.points.length]!;
      const overlap=(a:number,b:number,c:number,d:number)=>Math.max(Math.min(a,b),Math.min(c,d))<=Math.min(Math.max(a,b),Math.max(c,d))+tolerance;
      if(Math.abs(a.x-b.x)<1e-8 && Math.abs(c.x-d.x)<1e-8 && overlap(a.y,b.y,c.y,d.y))accept(c.x-a.x,0);
      if(Math.abs(a.y-b.y)<1e-8 && Math.abs(c.y-d.y)<1e-8 && overlap(a.x,b.x,c.x,d.x))accept(0,c.y-a.y);
    }
  }
  for (const point of points) { const snapped = snapDrawingPoint(point, targets, settings, tolerance); if (snapped !== point) accept(snapped.x - point.x, snapped.y - point.y); }
  // The target's endpoint may lie inside the moving side even when neither moving
  // endpoint is near the target segment (short line against a long rectangle side).
  if (settings.contours) for (const target of targets) for (const point of target.corners ?? target.points) {
    const projected = snapDrawingPoint(point, [{...moving,points,circle:moving.circle ? {...moving.circle,center:{x:moving.circle.center.x+delta.x,y:moving.circle.center.y+delta.y}} : undefined}], {corners:false,contours:true,tangents:false}, tolerance);
    if (projected !== point) accept(point.x-projected.x,point.y-projected.y);
  }
  if (settings.tangents && !moving.curved) for (const target of targets) if (target.circle) {
    for (let i = 0; i < points.length - (moving.closed ? 0 : 1); i++) {
      const a = points[i]!, b = points[(i + 1) % points.length]!, dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
      if (!length) continue;
      const nx = -dy / length, ny = dx / length, { center, radius } = target.circle;
      const along = ((center.x - a.x) * dx + (center.y - a.y) * dy) / length;
      if (along < 0 || along > length) continue;
      const distance = (a.x - center.x) * nx + (a.y - center.y) * ny;
      for (const sign of [-1, 1]) accept(nx * (sign * radius - distance), ny * (sign * radius - distance));
    }
  }
  const result = correctionX !== undefined || correctionY !== undefined ? {x:correctionX ?? 0,y:correctionY ?? 0} : correction as DrawingPoint | null;
  return result ? { x: delta.x + result.x, y: delta.y + result.y } : delta;
}

/** Snap active box edges in the node's rotated frame, keeping the opposite edges fixed. */
export function snapDrawingResizeDelta(
  moving: DrawingOutline, delta: DrawingPoint, handle: string,
  targets: readonly DrawingOutline[], settings: DrawingSnaps, tolerance: number, rotationDegrees = 0,
): DrawingPoint {
  const angle = rotationDegrees * Math.PI / 180, cos = Math.cos(angle), sin = Math.sin(angle);
  const local = (p:DrawingPoint):DrawingPoint => ({x:p.x*cos+p.y*sin,y:-p.x*sin+p.y*cos});
  const world = (p:DrawingPoint):DrawingPoint => ({x:p.x*cos-p.y*sin,y:p.x*sin+p.y*cos});
  const transform = (o:DrawingOutline):DrawingOutline => ({...o,points:o.points.map(local),corners:o.corners?.map(local),circle:o.circle?{...o.circle,center:local(o.circle.center)}:undefined});
  const box=transform(moving), candidates=targets.map(transform), d=local(delta);
  if(handle === "start" || handle === "end") {
    const origin=box.points[handle === "start" ? 0 : 1];
    if(!origin)return delta;
    const snap=snapDrawingPoint({x:origin.x+d.x,y:origin.y+d.y},candidates,settings,tolerance);
    return world({x:snap.x-origin.x,y:snap.y-origin.y});
  }
  if(box.points.length!==4 || box.curved)return delta;
  const xSide=handle.includes("e")?"e":handle.includes("w")?"w":null;
  const ySide=handle.includes("s")?"s":handle.includes("n")?"n":null;
  const points=box.points.map((p,i)=>({x:p.x+((xSide==="e"&&(i===1||i===2)||xSide==="w"&&(i===0||i===3))?d.x:0),
    y:p.y+((ySide==="s"&&(i===2||i===3)||ySide==="n"&&(i===0||i===1))?d.y:0)}));
  const edgeSnap=(a:number,b:number)=>snapDrawingTranslation({id:box.id,closed:false,points:[points[a]!,points[b]!]},{x:0,y:0},candidates,settings,tolerance);
  const cx=xSide?edgeSnap(xSide==="e"?1:3,xSide==="e"?2:0).x:0;
  const cy=ySide?edgeSnap(ySide==="s"?2:0,ySide==="s"?3:1).y:0;
  return world({x:d.x+cx,y:d.y+cy});
}
