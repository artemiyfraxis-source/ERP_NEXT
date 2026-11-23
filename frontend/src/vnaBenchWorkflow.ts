import { CALIBRATION_REFUSALS, type VnaBenchItem, type VnaBenchPlan, type VnaBenchSession, type VnaBenchState, type VnaBenchStepKey, type VnaBenchTarget } from "./vnaBenchApi";

export const BENCH_STEPS: VnaBenchStepKey[] = ["calibration", "confidence", "load_port1", "load_port2"];
export type BenchAction = {
  kind: "open" | "step" | "measure" | "connection" | "record" | "wait";
  label: string;
  reason?: string;
  step?: VnaBenchStepKey;
};

export const benchTargetKey = (target: VnaBenchTarget) => target.kind === "instrument"
  ? `si:${target.instrument_id}` : `kb:${target.task_id}:${target.position}`;

export const benchItem = (session: VnaBenchSession | null, target: VnaBenchTarget | null): VnaBenchItem | null =>
  session && target ? [...session.items].reverse().find((item) => benchTargetKey(item.target) === benchTargetKey(target)) ?? null : null;

export function benchStepAction(session: VnaBenchSession, calibrationValid: boolean): BenchAction | null {
  const current = BENCH_STEPS.map((key) => session.steps.find((step) => step.key === key) ?? { key, status: "pending", refusal: null })
    .find((step) => step.status !== "passed");
  if (current?.status === "failed" || (current?.status === "refused" && CALIBRATION_REFUSALS.has(current.refusal?.code ?? ""))
    || (!current && !calibrationValid)) return { kind: "step", label: "Откалибровать заново", step: "calibration" };
  if (!current) return null;
  if (current.status === "refused") return { kind: "step", step: current.key, label: "Повторить" };
  if (current.status === "running" || current.key === "confidence") return { kind: "wait", label: "Дождитесь проверки", reason: "Доверительный тест выполняется автоматически" };
  return { kind: "step", step: current.key, label: current.key === "calibration" ? "Калибровать АКМ" : `Измерить нагрузку на порту ${current.key === "load_port1" ? 1 : 2}` };
}

/** Only UI navigation is derived here; the server remains the authority for commands and verdicts. */
export function benchMainAction(state: VnaBenchState, target: VnaBenchTarget | null, plan: VnaBenchPlan | null): BenchAction {
  const session = state.session?.status === "open" ? state.session : null;
  const running = session && (session.command || session.steps.some((step) => step.status === "running")
    || session.items.some((item) => item.connections.some((connection) => connection.status === "running"))
    || session.checks.some((check) => check.status === "pending" || check.status === "running"));
  if (running) return { kind: "wait", label: "Выполняется…", reason: "Дождитесь завершения текущего действия" };
  if (!session || plan?.refusal?.code === "other_calibration") return { kind: "open", label: target?.kind === "assembly" ? "Открыть сеанс для КБ" : "Открыть сеанс" };
  const preparation = benchStepAction(session, state.calibration.valid);
  if (preparation) return preparation;
  if (!target) return { kind: "measure", label: "Замерить", reason: "Выберите СИ или КБ — сканируйте этикетку или введите номер" };
  const item = benchItem(session, target);
  if (item?.verdict?.final && item.target.kind === "instrument" && !item.recorded_at) return { kind: "record", label: "Сохранить" };
  if (!plan) return { kind: "measure", label: "Замерить", reason: "Дождитесь плана замера" };
  if (plan.refusal) return { kind: "measure", label: "Замерить", reason: "Устраните причину отказа в плане замера" };
  if (item?.verdict?.final) {
    if (item.target.kind === "instrument" && !item.recorded_at) return { kind: "record", label: "Сохранить" };
    return { kind: "measure", label: "Измерить заново" };
  }
  const next = item?.connections.find((connection) => connection.status === "pending");
  if (next && !item?.refusal) return { kind: "connection", label: `Измерить: ${item?.target.kind === "assembly" ? (next.no === 1 ? "первое" : `повтор ${next.no - 1} из 3`) : `${next.no} из ${item?.connections_total}`}` };
  if (item && !item.refusal) return { kind: "wait", label: "Ожидается результат…", reason: "Дождитесь результата прибора" };
  return { kind: "measure", label: "Замерить" };
}
