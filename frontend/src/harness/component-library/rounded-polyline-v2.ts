export type RoundedPolylinePointV2 = readonly [number, number];

export type RoundedPolylineCommandV2 =
  | { readonly kind: "move"; readonly x: number; readonly y: number }
  | { readonly kind: "line"; readonly x: number; readonly y: number }
  | { readonly kind: "arc"; readonly cornerX: number; readonly cornerY: number; readonly x: number; readonly y: number; readonly radius: number; readonly sweep: 0 | 1 };

export function roundedPolylineCommandsV2(
  points: readonly RoundedPolylinePointV2[],
  bendRadius: number,
): readonly RoundedPolylineCommandV2[] | null {
  if (points.length < 2 || !Number.isFinite(bendRadius) || bendRadius <= 0 ||
      points.some(point => point.length !== 2 || !point.every(Number.isFinite))) return null;
  const commands: RoundedPolylineCommandV2[] = [{ kind: "move", x: points[0]![0], y: points[0]![1] }];
  const epsilon = 1e-9;
  for (let index = 1; index < points.length - 1; index++) {
    const previous = points[index - 1]!, corner = points[index]!, next = points[index + 1]!;
    const incomingX = corner[0] - previous[0], incomingY = corner[1] - previous[1];
    const outgoingX = next[0] - corner[0], outgoingY = next[1] - corner[1];
    const incomingLength = Math.hypot(incomingX, incomingY), outgoingLength = Math.hypot(outgoingX, outgoingY);
    if (incomingLength <= epsilon || outgoingLength <= epsilon) {
      commands.push({ kind: "line", x: corner[0], y: corner[1] }); continue;
    }
    const incomingUnitX = incomingX / incomingLength, incomingUnitY = incomingY / incomingLength;
    const outgoingUnitX = outgoingX / outgoingLength, outgoingUnitY = outgoingY / outgoingLength;
    const cross = incomingUnitX * outgoingUnitY - incomingUnitY * outgoingUnitX;
    const dot = Math.max(-1, Math.min(1, incomingUnitX * outgoingUnitX + incomingUnitY * outgoingUnitY));
    const tangentFactor = Math.tan(Math.acos(dot) / 2);
    if (Math.abs(cross) <= epsilon || tangentFactor <= epsilon || !Number.isFinite(tangentFactor)) {
      commands.push({ kind: "line", x: corner[0], y: corner[1] }); continue;
    }
    const tangentDistance = Math.min(bendRadius * tangentFactor, incomingLength / 2, outgoingLength / 2);
    const effectiveRadius = tangentDistance / tangentFactor;
    if (tangentDistance <= epsilon || effectiveRadius <= epsilon || !Number.isFinite(effectiveRadius)) {
      commands.push({ kind: "line", x: corner[0], y: corner[1] }); continue;
    }
    commands.push(
      { kind: "line", x: corner[0] - incomingUnitX * tangentDistance, y: corner[1] - incomingUnitY * tangentDistance },
      {
        kind: "arc", cornerX: corner[0], cornerY: corner[1],
        x: corner[0] + outgoingUnitX * tangentDistance, y: corner[1] + outgoingUnitY * tangentDistance,
        radius: effectiveRadius, sweep: cross > 0 ? 1 : 0,
      },
    );
  }
  const last = points[points.length - 1]!;
  commands.push({ kind: "line", x: last[0], y: last[1] });
  return commands;
}

const format = (value: number) => {
  const normalized = Math.abs(value) < 1e-12 ? 0 : Math.round(value * 1e12) / 1e12;
  return Number.isInteger(normalized) ? String(normalized) : normalized.toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
};

export function roundedPolylinePathV2(points: readonly RoundedPolylinePointV2[], bendRadius: number): string | null {
  const commands = roundedPolylineCommandsV2(points, bendRadius);
  return commands?.map(command => command.kind === "move"
    ? `M ${format(command.x)} ${format(command.y)}`
    : command.kind === "line"
      ? `L ${format(command.x)} ${format(command.y)}`
      : `A ${format(command.radius)} ${format(command.radius)} 0 0 ${command.sweep} ${format(command.x)} ${format(command.y)}`,
  ).join(" ") ?? null;
}
