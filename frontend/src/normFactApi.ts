import { api } from "./api";

/* «Норма и факт» (`production/norm_fact.py`): факт против нормы ЭТК по шаблонам операций и монтажникам. Только отчёт. */

interface SpreadFields {
  /** Фактов в сводке (с нормой, штуками и временем). */
  facts: number;
  no_norm: number;
  /** Фактов хватает для предложения (≥ thresholds.min_facts). */
  enough: boolean;
  verdict: "" | "в норме" | "медленнее" | "быстрее";
  median: number | null;
  low: number | null;
  high: number | null;
  actual_seconds: number;
  norm_seconds: number;
}

export interface NormFactTemplate extends SpreadFields {
  template: string;
  operation_code: string;
  stage: string;
  seconds_per_unit: number | null;
  suggestion: string;
  suggested_seconds_per_unit: number | null;
}

export interface NormFactOperator extends SpreadFields {
  operator_id: string;
  operator_name: string;
  /** Медиана к обычному для шаблона (1 — как все). */
  relative: number | null;
  coefficient: number | null;
  suggestion: string;
}

export interface NormFactFact {
  task_id: string;
  task_number: string;
  project: string;
  item: string;
  operation_id: string;
  position: number;
  template: string;
  operation_code: string;
  stage: string;
  operator_id: string;
  operator_name: string;
  started_at: string;
  finished_at: string;
  submissions: number;
  actual_seconds: number;
  quantity: string;
  norm_seconds: number;
  actual_per_piece: number | null;
  norm_per_piece: number | null;
  ratio: number | null;
}

export interface NormFactReport {
  date_from: string;
  date_to: string;
  thresholds: { min_facts: number; slow: number; fast: number };
  templates: NormFactTemplate[];
  operators: NormFactOperator[];
  facts: NormFactFact[];
}

export const normFactApi = {
  report: (dateFrom: string, dateTo: string) =>
    api<NormFactReport>(`/api/production/norm-fact?date_from=${dateFrom}&date_to=${dateTo}`),
};
