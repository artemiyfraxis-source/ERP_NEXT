export interface WidthSample { readonly at: number; readonly halfWidth: number }

/** Envelop a stepped support with short ramps on the thinner side. Both ends
 * of every ramp are explicit: flat / diagonal / flat, never one long taper.
 * `at` is distance along the pipe, not manufacturing length. */
export function coveringWidthProfile(
  from: number, to: number, boundaries: readonly number[], halfAt: (at: number) => number,
): readonly WidthSample[] {
  if (to <= from) return [];
  const stops = [...new Set([from, ...boundaries.filter(n => n > from && n < to), to])].sort((a,b) => a-b);
  const widths = stops.slice(1).map((end,i) => halfAt((stops[i]! + end)/2));
  const result: WidthSample[] = [{ at: from, halfWidth: widths[0]! }];
  for (let i=1; i<stops.length-1; i++) {
    const at=stops[i]!, before=widths[i-1]!, after=widths[i]!;
    if (Math.abs(after-before)<1e-9) continue;
    // A steep ramp (at least 2:1 radial/axial), capped before neighbouring ramps.
    const run=Math.min(Math.abs(after-before)/2,(at-stops[i-1]!)/4,(stops[i+1]!-at)/4);
    result.push({at:after>before?at-run:at,halfWidth:before});
    result.push({at:after>before?at:at+run,halfWidth:after});
  }
  result.push({ at: to, halfWidth: widths.at(-1)! });
  return result;
}

export function profileHalfWidth(profile: readonly WidthSample[], at: number): number {
  if (!profile.length) return 0;
  const i=profile.findIndex(p=>p.at>=at);
  if(i===0)return profile[0]!.halfWidth;
  if(i<0)return profile.at(-1)!.halfWidth;
  const a=profile[i-1]!,b=profile[i]!;
  return a.halfWidth+(b.halfWidth-a.halfWidth)*(at-a.at)/(b.at-a.at);
}

export interface WidthSupport {
  readonly from: number;
  readonly to: number;
  readonly profile: readonly WidthSample[];
}

/** Enclose actual lower ramps, splitting at their intersections. */
export function encloseWidthProfiles(base: readonly WidthSample[], supports: readonly WidthSupport[], clearance: number): readonly WidthSample[] {
  if (base.length < 2 || !supports.length) return base;
  const from = base[0]!.at, to = base.at(-1)!.at;
  const stops = [...new Set([...base.map(p => p.at), ...supports.flatMap(s =>
    [s.from, s.to, ...s.profile.map(p => p.at)]).filter(at => at > from && at < to),
  ])].sort((a, b) => a - b);
  const result: WidthSample[] = [];
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1]!, b = stops[i]!, mid = (a + b) / 2;
    const lines = [
      [profileHalfWidth(base, a), profileHalfWidth(base, b)],
      ...supports.filter(s => mid > s.from && mid < s.to).map(s =>
        [profileHalfWidth(s.profile, a) + clearance, profileHalfWidth(s.profile, b) + clearance]),
    ];
    const fractions = new Set([0, 1]);
    for (let j = 0; j < lines.length; j++) for (let k = j + 1; k < lines.length; k++) {
      const u = lines[j]!, v = lines[k]!, delta = (u[1]! - u[0]!) - (v[1]! - v[0]!);
      if (Math.abs(delta) < 1e-9) continue;
      const t = (v[0]! - u[0]!) / delta;
      if (t > 0 && t < 1) fractions.add(t);
    }
    for (const t of [...fractions].sort((a, b) => a - b)) {
      const sample = { at: a + (b - a) * t, halfWidth: Math.max(...lines.map(l => l[0]! + (l[1]! - l[0]!) * t)) };
      const last = result.at(-1);
      if (last && Math.abs(last.at - sample.at) < 1e-9) {
        result[result.length - 1] = { at: last.at, halfWidth: Math.max(last.halfWidth, sample.halfWidth) };
      } else result.push(sample);
    }
  }
  return result.filter((p, i) => {
    const a = result[i - 1], b = result[i + 1];
    return !a || !b || Math.abs((p.halfWidth - a.halfWidth) * (b.at - p.at) -
      (b.halfWidth - p.halfWidth) * (p.at - a.at)) > 1e-8;
  });
}
