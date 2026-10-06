export type E4CrossingStyle = "none" | "bridge";

export type E4DifferentialPairVariant = 1 | 2;

export interface E4DifferentialPairState {
  readonly variant: E4DifferentialPairVariant;
  readonly twistPitchMm: number;
}

export interface E4ScreenState {
  readonly positionPercent: number;
  /** Omitted in legacy callers and interpreted as `above`. */
  readonly terminalSide?: E4ScreenTerminalSide;
}

export type E4ScreenTerminalSide = "above" | "below" | "both";

export interface E4WireSelectionCapabilities {
  readonly selectedCount: number;
  readonly canSetCrossing: boolean;
  readonly canCreateDifferentialPair: boolean;
  readonly canCreateScreen: boolean;
  readonly canClearGroup: boolean;
}

export function e4WireSelectionCapabilities(
  selectedWireIds: readonly string[],
): E4WireSelectionCapabilities {
  const selectedCount = new Set(selectedWireIds.filter((id) => id.trim().length > 0)).size;
  return {
    selectedCount,
    canSetCrossing: selectedCount >= 1,
    canCreateDifferentialPair: selectedCount === 2,
    canCreateScreen: selectedCount >= 1,
    canClearGroup: selectedCount >= 1,
  };
}

export function clampScreenPosition(value: number): number {
  if (!Number.isFinite(value)) return 50;
  return Math.min(100, Math.max(0, Math.round(value)));
}

export function normalizeTwistPitchMm(value: number): number {
  if (!Number.isFinite(value)) return 25;
  return Math.min(10_000, Math.max(0.1, Math.round(value * 10) / 10));
}
