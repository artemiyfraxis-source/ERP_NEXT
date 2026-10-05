/** Cross-section layout in drawing units, independent of routing and wire IDs.
 * Depth is the projection axis; it never creates an electrical connection. */
export interface BundleDisk { readonly id: string; readonly diameter: number }
export interface PackedBundleDisk extends BundleDisk { readonly offset: number; readonly depth: number }
export interface BundlePacking {
  readonly members: readonly PackedBundleDisk[];
  readonly width: number;
  readonly depth: number;
}
export type BundlePackingMode = "flat" | "round";

interface Disk { id: string; radius: number; x: number; y: number }
const epsilon = 1e-8;

/** Deterministic tangent-circle heuristic. Flat mode preserves input order;
 * round mode places large members first, then restores IDs/input order.
 * This is a compact feasible packing, not a claim of global optimality. */
export function packPipeBundle(members: readonly BundleDisk[], mode: BundlePackingMode): BundlePacking {
  if (mode !== "flat" && mode !== "round") throw new Error("Неизвестный режим укладки пайпов.");
  if (new Set(members.map(m => m.id)).size !== members.length || members.some(m =>
    !m.id || !Number.isFinite(m.diameter) || m.diameter <= 0 || m.diameter > 1e7)) {
    throw new Error("Пайпы должны иметь уникальные ID и положительную ширину.");
  }
  if (!members.length) return { members: [], width: 0, depth: 0 };
  if (mode === "flat") {
    const width = members.reduce((sum, m) => sum + m.diameter, 0);
    let start = -width / 2;
    return { width, depth: Math.max(...members.map(m => m.diameter)), members: members.map(m => {
      const offset = start + m.diameter / 2; start += m.diameter;
      return { ...m, offset, depth: 0 };
    }) };
  }
  // Normalize before intersection arithmetic so small/large drawing scales
  // produce the same result and collision tolerance remains relative.
  const scale = Math.max(...members.map(m => m.diameter));
  const sorted = members.map((m, order) => ({ ...m, order })).sort((a, b) => b.diameter - a.diameter || a.order - b.order);
  const placed: Disk[] = [];
  for (const member of sorted) {
    const radius = member.diameter / scale / 2;
    if (!placed.length) { placed.push({ id: member.id, radius, x: 0, y: 0 }); continue; }
    const candidates: { x: number; y: number }[] = [];
    for (let i = 0; i < placed.length; i++) {
      const a = placed[i]!, ar = a.radius + radius;
      // Cardinal tangencies provide a fallback for a one-member packing.
      candidates.push({ x: a.x + ar, y: a.y }, { x: a.x - ar, y: a.y },
        { x: a.x, y: a.y + ar }, { x: a.x, y: a.y - ar });
      for (let j = i + 1; j < placed.length; j++) {
        const b = placed[j]!, br = b.radius + radius, dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy);
        if (d < epsilon || d > ar + br + epsilon || d < Math.abs(ar - br) - epsilon) continue;
        const along = (ar * ar - br * br + d * d) / (2 * d);
        const height = Math.sqrt(Math.max(0, ar * ar - along * along));
        const x = a.x + along * dx / d, y = a.y + along * dy / d;
        candidates.push({ x: x - height * dy / d, y: y + height * dx / d },
          { x: x + height * dy / d, y: y - height * dx / d });
      }
    }
    let best: { x: number; y: number } | undefined, score = Infinity;
    for (const candidate of candidates) {
      if (placed.some(p => Math.hypot(p.x - candidate.x, p.y - candidate.y) + epsilon < p.radius + radius)) continue;
      // Recenter for every candidate; keeping the first disk at the centre
      // makes the third disk prefer a row over a triangular packing.
      const trial = [...placed, { id: member.id, radius, ...candidate }];
      const cx = (Math.min(...trial.map(p => p.x - p.radius)) + Math.max(...trial.map(p => p.x + p.radius))) / 2;
      const cy = (Math.min(...trial.map(p => p.y - p.radius)) + Math.max(...trial.map(p => p.y + p.radius))) / 2;
      const next = Math.max(...trial.map(p => Math.hypot(p.x - cx, p.y - cy) + p.radius));
      if (next < score - epsilon) { score = next; best = candidate; }
    }
    if (!best) {
      best = { x: Math.max(...placed.map(p => p.x + p.radius)) + radius, y: 0 };
    }
    placed.push({ id: member.id, radius, ...best });
  }
  const left = Math.min(...placed.map(p => p.x - p.radius)), right = Math.max(...placed.map(p => p.x + p.radius));
  const top = Math.min(...placed.map(p => p.y - p.radius)), bottom = Math.max(...placed.map(p => p.y + p.radius));
  const byId = new Map(placed.map(p => [p.id, p]));
  return { width: (right - left) * scale, depth: (bottom - top) * scale,
    members: members.map(m => { const p = byId.get(m.id)!; return { ...m,
      offset: (p.x - (left + right) / 2) * scale, depth: (p.y - (top + bottom) / 2) * scale }; }) };
}
