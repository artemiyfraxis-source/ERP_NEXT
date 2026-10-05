import type { Point } from "./model";
import type { PhysicalCovering } from "./physical-coverings";

/** Persisted topology only. Generated routing vertices never enter this model. */
export type PhysicalDirection = "left" | "right" | "up" | "down";
export interface PhysicalNode { readonly id: string; readonly position: Point; readonly connectorId?: string; readonly wireIds?: readonly string[]; readonly direction?: PhysicalDirection; readonly contactDirections?: Readonly<Record<string, PhysicalDirection>> }
/** Routed paths return to automatic routing when their last authored point is removed.
 * Polylines retain their authored shape even with no internal points (split fragments). */
export type PhysicalPath =
  | { readonly kind: "routed"; readonly points: readonly Point[] }
  | { readonly kind: "polyline"; readonly points: readonly Point[] };
export interface PhysicalSegment { readonly id: string; readonly from: string; readonly to: string; readonly path: PhysicalPath; readonly mode?:"flat"|"round"; readonly width?:number; readonly color?:string; readonly opacity?:number; readonly showWires?:boolean; readonly volumeShading?:boolean; readonly specificationItemId?:string }
/** A first-class common pipe (ОП). It owns its axis; member segments are
 * projected onto it for presentation while their electrical routes remain
 * unchanged. */
export type JoiningPipeBendRegion = "before-enter"|"enter"|"axis"|"exit"|"after-exit";
export interface JoiningPipeMember {
  /** Consecutive fragments of one pipe after an explicit split. */
  readonly segmentIds: readonly string[];
  readonly from: number; readonly to: number; readonly reverse: boolean;
  /** Undefined uses the generated bend; null explicitly removes it. */
  readonly enterBend?: Point | null;
  readonly exitBend?: Point | null;
  readonly enterOuter?: Point;
  readonly exitOuter?: Point;
  /** Stable ownership of authored route bends across OP projection edits. */
  readonly authoredBendRegions?: readonly {readonly segmentId:string;readonly bendIndex:number;readonly region:JoiningPipeBendRegion;readonly displayPoint?:Point}[];
}
export interface PhysicalJoiningPipe {
  readonly id: string;
  readonly start: Point; readonly end: Point;
  readonly path: PhysicalPath;
  readonly members: readonly JoiningPipeMember[];
  /** Shared straight lead lengths at the two OP ends (drawing units). */
  readonly enterLength?: number;
  readonly exitLength?: number;
  readonly mode: "flat" | "round";
  readonly width?: number; readonly color?: string; readonly opacity?:number; readonly volumeShading?: boolean;
}
export interface PhysicalStep { readonly segmentId: string; readonly reverse: boolean }
export interface PhysicalRoute { readonly wireId: string; readonly steps: readonly PhysicalStep[]; readonly automatic?:boolean }
export interface PhysicalTopology {
  readonly coverings?: readonly PhysicalCovering[];
  readonly nodes: readonly PhysicalNode[];
  readonly segments: readonly PhysicalSegment[];
  readonly joiningPipes?: readonly PhysicalJoiningPipe[];
  readonly routes: readonly PhysicalRoute[];
  readonly snap: boolean;
}
export const emptyPhysicalTopology = (): PhysicalTopology => ({ nodes: [], segments: [], routes: [], snap: true });
