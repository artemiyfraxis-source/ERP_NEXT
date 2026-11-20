import { api } from "./api";

/* «Станки» в «Норме и факте» (`production/machine_speed`): скорость станков по истории работы, предложения для циклограммы. */

export interface MachineRef { equipment_id: string; inventory_number: string; name: string }

export interface MachineSetup {
  wire: string;
  length_mm: string;
  strip_front_mm: string;
  strip_back_mm: string;
  pull: string;
  marking: string;
}

export type StopReason = "pause" | "alarm" | "idle" | "offline" | "reset";
export interface MachineStop { reason: StopReason; label: string; seconds: number }

export interface SuggestionCell { field: string; label: string; old: string; new: string }

export interface SuggestionEvidence {
  pieces: number;
  runs: number;
  lengths: string[];
  fit: string;
  since: string;
  until: string;
}

export interface MachineSuggestion {
  record_id: string;
  operation_code: string;
  cells: SuggestionCell[];
  evidence: SuggestionEvidence;
  note: string | null;
}

export interface MachineSpeedRow {
  equipment_id: string;
  inventory_number: string;
  setup_key: string;
  setup: MachineSetup;
  operation: { code: string; name: string; record_id: string | null };
  runs: number;
  pieces: number;
  running_seconds: number;
  fact_seconds_per_piece: string | null;
  norm_seconds_per_piece: string | null;
  ratio: string | null;
  setup_fact_seconds: string | null;
  setup_norm_seconds: string | null;
  stops: MachineStop[];
  suggestion: MachineSuggestion | null;
}

export interface MachineSpeedReport {
  since: string;
  until: string;
  can_accept: boolean;
  machines: MachineRef[];
  rows: MachineSpeedRow[];
}

export interface MachineRun {
  started_at: string;
  ended_at: string;
  kind: "cutting" | "manual" | null;
  pieces: number;
  running_seconds: number;
  fact_seconds_per_piece: string | null;
  setup_fact_seconds: string | null;
}

export interface MachineSpeedScope { equipment_id: string; setup_key: string; since: string; until: string }

function query(values: Record<string, string>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return params.toString();
}

export const machineSpeedApi = {
  report: (since: string, until: string, equipmentId = "") =>
    api<MachineSpeedReport>(`/api/production/machine-speed?${query({ equipment_id: equipmentId, since, until })}`),
  runs: (scope: MachineSpeedScope) =>
    api<{ runs: MachineRun[] }>(`/api/production/machine-speed/runs?${query({ ...scope })}`),
  accept: (suggestion: MachineSuggestion, scope: MachineSpeedScope) =>
    api<{ record_id: string; written: Array<{ field: string; old: string; new: string }> }>(
      "/api/production/machine-speed/norm-suggestions/accept",
      {
        method: "POST",
        body: JSON.stringify({
          record_id: suggestion.record_id,
          cells: suggestion.cells.map(({ field, old, new: next }) => ({ field, old, new: next })),
          ...scope,
        }),
      },
    ),
};
