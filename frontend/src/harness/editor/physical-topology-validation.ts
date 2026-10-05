import type { HarnessDesignDocument, Point } from "./model";
import type { PhysicalTopology, PhysicalDirection } from "./physical-topology-model";
import { validateCoverings } from "./physical-coverings";
import { readPhysicalSegmentPath } from "./physical-path-codec";
import { validatePipeContinuations } from "./pipe-bundle-model";
import { validateJoiningPipes } from "./physical-joining-pipe-validation";

export function parsePhysicalTopology(value: unknown, document: HarnessDesignDocument): PhysicalTopology | undefined {
  if (value === undefined) return undefined;
  const fail = (): never => { throw new Error("Некорректная физическая трасса: проверьте узлы, участки и порядок маршрутов."); };
  if (!value || typeof value !== "object") return fail();
  let t = value as PhysicalTopology;
  if (!Array.isArray(t.nodes) || !Array.isArray(t.segments) || !Array.isArray(t.routes) || typeof t.snap !== "boolean" ||
      t.nodes.length > 10000 || t.segments.length > 20000 || t.routes.length > 20000) return fail();
  const segments = t.segments.map(segment => readPhysicalSegmentPath(segment, fail));
  if (segments.some((segment, i) => segment !== t.segments[i])) t = { ...t, segments };
  const text = (s: unknown): s is string => typeof s === "string" && s.trim().length > 0 && s.length <= 128;
  const direction = (value: unknown): value is PhysicalDirection => value === "left" || value === "right" || value === "up" || value === "down";
  const point = (p: Point) => p && Number.isFinite(p.x) && Number.isFinite(p.y) && Math.abs(p.x) <= 1e7 && Math.abs(p.y) <= 1e7;
  const ids = new Set([...document.connectors.map(c => c.id), ...document.wires.map(w => w.id)]);
  const unique = (id: unknown) => { if (!text(id) || ids.has(id)) fail(); ids.add(id as string); };
  for (const n of t.nodes) {
    if (!n) return fail(); unique(n.id);
    if(n.wireIds!==undefined&&(!n.connectorId||!Array.isArray(n.wireIds)||new Set(n.wireIds).size!==n.wireIds.length||n.wireIds.some((id:string)=>!document.wires.some(w=>w.id===id&&(w.from.connectorId===n.connectorId||w.to.connectorId===n.connectorId)))))return fail();
    if(n.direction!==undefined&&!direction(n.direction))return fail();
    if(n.contactDirections!==undefined&&(!n.connectorId||!n.contactDirections||typeof n.contactDirections!=="object"||Array.isArray(n.contactDirections)||Object.entries(n.contactDirections).some(([id,value])=>!document.connectors.find(c=>c.id===n.connectorId)?.contacts.some(c=>c.id===id)||!direction(value))))return fail();
    if (!point(n.position) || n.connectorId !== undefined && !document.connectors.some(c => c.id === n.connectorId)) return fail();
  }

  for (const s of t.segments) {
    if (!s) return fail(); unique(s.id);
    if(s.mode!==undefined&&s.mode!=="flat"&&s.mode!=="round"||s.width!==undefined&&(!Number.isFinite(s.width)||s.width<0||s.width>1e7)||s.color!==undefined&&!/^#[0-9a-f]{6}$/i.test(s.color)||s.opacity!==undefined&&(!Number.isFinite(s.opacity)||s.opacity<0||s.opacity>1)||s.showWires!==undefined&&typeof s.showWires!=="boolean"||s.volumeShading!==undefined&&typeof s.volumeShading!=="boolean"||s.specificationItemId!==undefined&&!text(s.specificationItemId))return fail();
    if (s.from === s.to || !t.nodes.some(n => n.id === s.from) || !t.nodes.some(n => n.id === s.to)) return fail();
  }
  const wireIds = new Set<string>();
  for (const r of t.routes) {
    if (!r || wireIds.has(r.wireId) || !document.wires.some(w => w.id === r.wireId) || !Array.isArray(r.steps) || !r.steps.length || r.steps.length > 20000) return fail();
    if(r.automatic!==undefined&&typeof r.automatic!=="boolean")return fail();
    wireIds.add(r.wireId);
    let previous: string | undefined;
    const visited = new Set<string>();
    for (const step of r.steps) {
      const s = t.segments.find(s => s.id === step?.segmentId);
      if (!s || typeof step.reverse !== "boolean" || visited.has(s.id)) return fail();
      visited.add(s.id);
      const from = step.reverse ? s.to : s.from, to = step.reverse ? s.from : s.to;
      if (previous && previous !== from) return fail();
      previous = to;
    }
    const first = t.segments.find(s => s.id === r.steps[0]!.segmentId)!;
    const last = t.segments.find(s => s.id === r.steps.at(-1)!.segmentId)!;
    const from = t.nodes.find(n => n.id === (r.steps[0]!.reverse ? first.to : first.from))!;
    const to = t.nodes.find(n => n.id === (r.steps.at(-1)!.reverse ? last.from : last.to))!;
    const w = document.wires.find(w => w.id === r.wireId)!;
    if(from.wireIds&&!from.wireIds.includes(r.wireId)||to.wireIds&&!to.wireIds.includes(r.wireId))return fail();
    if (from.connectorId && from.connectorId !== w.from.connectorId || to.connectorId && to.connectorId !== w.to.connectorId) return fail();
  }
  validateJoiningPipes(t,ids);
  const joiningIds = new Set((t.joiningPipes??[]).map(p=>p.id));
  validateCoverings(t.coverings, new Set([...t.segments.map(s => s.id),...joiningIds]), ids);
  validatePipeContinuations(t.coverings??[],t.segments);
  for(const c of t.coverings??[])for(const span of c.spans){const count=(t.segments.find(s=>s.id===span.segmentId)??t.joiningPipes?.find(s=>s.id===span.segmentId))!.path.points.length+2;
    if([span.fromAnchor,span.toAnchor].some(i=>i!==undefined&&i>=count)||span.fromAnchor!==undefined&&span.toAnchor!==undefined&&span.fromAnchor>=span.toAnchor)return fail();}
  return t;
}
