import type { PhysicalCovering } from "./physical-coverings";
import type { BundlePackingMode } from "./pipe-bundle-packing";
import type { Point } from "./model";

export type PipeBundleMember =
  | { readonly kind: "segment"; readonly id: string; readonly continuationIds?: readonly string[] }
  | { readonly kind: "covering"; readonly id: string };
/** A covering's spans define its longitudinal support. Membership describes
 * the cross-section; it never changes the physical or electrical route graph. */
export interface PipeBundle {
  readonly mode: BundlePackingMode;
  readonly transitionStart?:number; readonly transitionEnd?:number;
  /** Shared bend offset of every entering member, in drawing coordinates. */
  readonly transitionBendStart?: {readonly x:number;readonly y:number};
  readonly transitionBendEnd?: {readonly x:number;readonly y:number};
  /** Free translation of the common sleeve axis in drawing coordinates. */
  readonly bodyOffset?: Point;
  readonly members: readonly PipeBundleMember[];
}
export const maximumBundleMembers = 128;
export const maximumBundleDepth = 16;

/** Coatings on the same ordered bundle and support intervals share an axis.
 * Paint order and object property insertion order are not geometry. */
export function pipeBundleCoatingKey(c:PhysicalCovering):string {
 return JSON.stringify([c.bundle?.mode,c.bundle?.members.map(m=>m.kind==="segment"?[m.kind,m.id,m.continuationIds??[]]:[m.kind,m.id]),
  [...c.spans].sort((a,b)=>a.segmentId.localeCompare(b.segmentId)||a.from-b.from||a.to-b.to)
   .map(s=>[s.segmentId,s.from,s.to,s.fromAnchor,s.toAnchor]),c.bundle?.bodyOffset?.x??0,c.bundle?.bodyOffset?.y??0]);
}

/** Split fragments are consecutive lengths of one cross-section, not extra disks. */
export const pipeMemberSegments = (member: Extract<PipeBundleMember, {kind:"segment"}>): readonly string[] =>
  [member.id, ...member.continuationIds ?? []];

/** Ordered longitudinal paths. A nested group contributes its existing paths,
 * never another parallel leaf for each fragment created by a split. */
export function pipeBundlePaths(covering: PhysicalCovering, coverings: readonly PhysicalCovering[]): readonly (readonly string[])[] {
  return covering.bundle?.members.flatMap(member => member.kind === "segment"
    ? [pipeMemberSegments(member)]
    : pipeBundlePaths(coverings.find(c => c.id === member.id)!, coverings)) ?? [];
}

/** First covered member in authored membership order owns the common display
 * axis. Reordering spans during motion must not move the shell to another pipe. */
export function pipeBundleAxisPath(covering: PhysicalCovering, coverings: readonly PhysicalCovering[]): readonly string[] {
  return pipeBundlePaths(covering,coverings).find(ids=>covering.spans.some(s=>ids.includes(s.segmentId)))??[];
}

/** Returns ordered unique leaves. Used by validation, selection and layout so
 * nesting cannot silently duplicate a pipe or invent a missing participant. */
export function resolvePipeBundles(coverings: readonly PhysicalCovering[], segmentIds: ReadonlySet<string>): ReadonlyMap<string, readonly string[]> {
  const byId = new Map(coverings.map(c => [c.id, c]));
  const result = new Map<string, readonly string[]>();
  const active = new Set<string>();
  const heights = new Map<string, number>();
  const fail = (): never => { throw new Error("Некорректное объединение: проверьте состав, повторные пайпы и вложенность групп."); };
  const visit = (id: string, depth: number): readonly string[] => {
    if (active.has(id) || depth > maximumBundleDepth) return fail();
    const cached = result.get(id);
    if (cached) {
      if (depth + heights.get(id)! - 1 > maximumBundleDepth) return fail();
      return cached;
    }
    const covering = byId.get(id), bundle = covering?.bundle;
    if (!bundle || typeof bundle !== "object" || Array.isArray(bundle) ||
      bundle.mode !== "flat" && bundle.mode !== "round" || !Array.isArray(bundle.members) ||
      bundle.members.length < 2 || bundle.members.length > maximumBundleMembers) return fail();
    if([bundle.transitionStart,bundle.transitionEnd].some(n=>n!==undefined&&(!Number.isFinite(n)||n<.001||n>.5)))return fail();
    if([bundle.transitionBendStart,bundle.transitionBendEnd,bundle.bodyOffset].some(p=>p!==undefined&&
      (!p||!Number.isFinite(p.x)||!Number.isFinite(p.y)||Math.abs(p.x)>10000||Math.abs(p.y)>10000)))return fail();
    active.add(id);
    const leaves: string[] = [], seen = new Set<string>();
    let height = 1;
    for (const member of bundle.members) {
      if (!member || typeof member !== "object" || typeof member.id !== "string" ||
        !member.id.trim() || member.id.length > 128 || member.kind !== "segment" && member.kind !== "covering") return fail();
      if (member.kind === "segment" && !segmentIds.has(member.id)) return fail();
      if ("continuationIds" in member && (member.kind !== "segment" || !Array.isArray(member.continuationIds) ||
        !member.continuationIds.length || member.continuationIds.length >= maximumBundleMembers ||
        member.continuationIds.some((id: unknown) => typeof id !== "string" || !segmentIds.has(id)))) return fail();
      const children = member.kind === "segment" ? pipeMemberSegments(member) : visit(member.id, depth + 1);
      if (member.kind === "covering") height = Math.max(height, 1 + heights.get(member.id)!);
      for (const leaf of children) {
        if (seen.has(leaf) || leaves.length >= maximumBundleMembers) return fail();
        seen.add(leaf); leaves.push(leaf);
      }
    }
    // The sleeve must be attached to one of its own pipes, never an unrelated axis.
    if (covering!.spans.some(span => !seen.has(span.segmentId))) return fail();
    active.delete(id); heights.set(id, height); result.set(id, leaves);
    return leaves;
  };
  for (const covering of coverings) if (covering.bundle !== undefined) visit(covering.id, 1);
  return result;
}

export function validatePipeContinuations(coverings: readonly PhysicalCovering[], segments: readonly {id:string;from:string;to:string}[]): void {
  const byId = new Map(segments.map(s => [s.id,s]));
  for (const c of coverings) for (const member of c.bundle?.members ?? []) if (member.kind === "segment") {
    const ids = pipeMemberSegments(member);
    const nodes = new Set<string>();
    for (let i=0;i<ids.length;i++) {
      const s=byId.get(ids[i]!);
      if (!s || i>0 && byId.get(ids[i-1]!)?.to !== s.from || nodes.has(s.to))
        throw new Error("Продолжения пайпа должны образовывать непрерывный путь без возвратов.");
      if(i===0)nodes.add(s.from);
      if(nodes.has(s.to))throw new Error("Замкнутое продолжение пайпа.");
      nodes.add(s.to);
    }
  }
}
