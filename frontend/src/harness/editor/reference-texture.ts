/** Pixel identities of the six original 64px library seeds (including project copies).
 * Never select by covering kind: a user may have replaced that kind's image. */
const seeds = new Map<number, ReferenceTexture>([
  [3606816276, "thread"], [1296187641, "fleece"], [2432396909, "pvc"],
  [1529575019, "shrink"], [406990667, "nylon"], [350775237, "metal"],
]);
export type ReferenceTexture = "thread" | "fleece" | "pvc" | "shrink" | "nylon" | "metal";

export function referenceTexture(pixels: Uint8ClampedArray, width: number, height: number): ReferenceTexture | undefined {
  if (width !== 64 || height !== 64) return undefined;
  let hash = 2166136261;
  for (const byte of pixels) hash = Math.imul(hash ^ byte, 16777619) >>> 0;
  return seeds.get(hash);
}

/** The tile covers 32 world units. Zoom/DPR affect sampling, never weave spacing. */
export function referenceTextureResolution(context: CanvasRenderingContext2D, scale: number): number {
  const m = context.getTransform();
  const density = Math.max(Math.hypot(m.a, m.b), Math.hypot(m.c, m.d));
  return Math.min(4096, 2 ** Math.ceil(Math.log2(Math.max(256, 32 * scale * density))));
}

/** Repeatable vector construction in a 64-unit tile, rasterized at display density.
 * Bundles alternate over/under at every crossing; fine parallel filaments retain
 * the material reference's detail when the camera or texture scale increases. */
export function drawReferenceTexture(ctx: CanvasRenderingContext2D, kind: ReferenceTexture, tint: string, size: number): void {
  ctx.scale(size / 64, size / 64);
  ctx.fillStyle = tint; ctx.strokeStyle = tint;
  const stroke = (x1: number, y1: number, x2: number, y2: number, width: number, alpha: number) => {
    ctx.globalAlpha = alpha; ctx.lineWidth = width;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  };
  if (kind === "nylon" || kind === "metal") {
    const metal = kind === "metal", half = metal ? 3.65 : 3.05;
    ctx.translate(32, 32); ctx.rotate(Math.PI / 4); ctx.translate(-32, -32);
    // The diagonal cell pitch gives an exact 64-unit repeat in both world axes.
    const pitch = 64 / (8 * Math.SQRT2);
    for (let row = -8; row < 20; row++) for (let col = -8; col < 20; col++) {
      const x = col * pitch, y = row * pitch;
      const horizontal = (row + col) % 2 === 0;
      ctx.save(); ctx.translate(x, y); if (!horizontal) ctx.rotate(Math.PI / 2);
      const gradient = ctx.createLinearGradient(0, -half, 0, half);
      // Alpha relief keeps the independently selected background visible.
      gradient.addColorStop(0, "transparent"); gradient.addColorStop(.12, tint);
      gradient.addColorStop(.45, "transparent"); gradient.addColorStop(1, tint);
      ctx.globalAlpha = metal ? .65 : .82; ctx.fillStyle = gradient;
      ctx.fillRect(-pitch / 2, -half, pitch, half * 2);
      const count = metal ? 7 : 5;
      for (let f = 0; f <= count; f++) {
        const y0 = -half + .25 + f * (2 * half - .5) / count;
        stroke(-pitch / 2, y0, pitch / 2, y0, metal ? .16 : .23, metal ? .72 : .9);
      }
      stroke(-pitch / 2 + .12, -half, -pitch / 2 + .12, half, .3, .65);
      ctx.restore();
    }
  } else if (kind === "thread") {
    for (let x = -4; x < 68; x += 4) {
      stroke(x, 0, x, 64, 2.8, .18);
      for (let f = 0; f < 4; f++) stroke(x + f * .65, 0, x + f * .65, 64, .19, .65);
      for (let y = -4; y < 68; y += 2) stroke(x, y, x + 2.6, y + 1.4, .15, .4);
    }
  } else {
    // Deterministic fine fibres/grain, identical at every rendering resolution.
    let random = 173;
    const next = () => { random = (Math.imul(random, 1664525) + 1013904223) >>> 0; return random / 4294967296; };
    for (let i = 0; i < (kind === "fleece" ? 2200 : 1600); i++) {
      const x = next() * 64, y = next() * 64;
      const length = kind === "fleece" ? .5 + next() * 1.8 : .12;
      const dy = kind === "fleece" ? (next() - .5) * .6 : 0;
      const alpha = kind === "fleece" ? .2 + next() * .5 : .07 + next() * .14;
      for (const dx of [-64, 0, 64]) for (const offsetY of [-64, 0, 64])
        stroke(x + dx, y + offsetY, x + length + dx, y + dy + offsetY, .16, alpha);
    }
  }
  ctx.globalAlpha = 1;
}
