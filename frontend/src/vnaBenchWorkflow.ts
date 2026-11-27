import { CALIBRATION_REFUSALS, type KitBand, type KitCaptureInput, type KitPiece, type KitView, type VnaBenchItem, type VnaBenchPlan, type VnaBenchSession, type VnaBenchState, type VnaBenchStepKey, type VnaBenchTarget } from "./vnaBenchApi";

/** Шаги калибровки, которые прислал сервер: с 26.11.2026 — одна «Калибровка АКМ» (доверительный тест и нагрузку
 *  владелец отменил); старый сеанс — свои четыре шага. */
export const benchSteps = (session: VnaBenchSession): VnaBenchStepKey[] =>
  session.steps.length ? session.steps.map((step) => step.key) : ["calibration"];
export const calibrationDone = (session: VnaBenchSession): boolean =>
  benchSteps(session).every((key) => session.steps.find((step) => step.key === key)?.status === "passed");
export type BenchAction = {
  kind: "open" | "step" | "measure" | "connection" | "record" | "wait" | "kit-capture" | "kit-band" | "kit-save" | "kit-activate";
  label: string;
  reason?: string;
  step?: "calibration";
  /** «Характеристика мер»: что снять (мера или оснастка), какую меру сохранить, на какой проход перейти. */
  capture?: KitCaptureInput;
  pieceId?: string;
  band?: KitBand;
};

export const benchTargetKey = (target: VnaBenchTarget) => target.kind === "instrument"
  ? `si:${target.instrument_id}` : `kb:${target.task_id}:${target.position}`;

export const benchItem = (session: VnaBenchSession | null, target: VnaBenchTarget | null): VnaBenchItem | null =>
  session && target ? [...session.items].reverse().find((item) => benchTargetKey(item.target) === benchTargetKey(target)) ?? null : null;

export function benchStepAction(session: VnaBenchSession, calibrationValid: boolean): BenchAction | null {
  const current = benchSteps(session).map((key) => session.steps.find((step) => step.key === key) ?? { key, status: "pending", refusal: null })
    .find((step) => step.status !== "passed");
  if (current?.status === "running") return { kind: "wait", label: "Дождитесь калибровки", reason: "Калибровка АКМ выполняется" };
  // Старый сеанс, ждущий нагрузку или доверительный тест: эти шаги отменены — сразу новая калибровка.
  if (current?.status === "failed" || (current?.status === "refused" && CALIBRATION_REFUSALS.has(current.refusal?.code ?? ""))
    || (current && current.key !== "calibration") || (!current && !calibrationValid)) {
    return { kind: "step", label: "Откалибровать заново", step: "calibration" };
  }
  if (!current) return null;
  if (current.status === "refused") return { kind: "step", step: "calibration", label: "Повторить" };
  return { kind: "step", step: "calibration", label: "Калибровать АКМ" };
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

/** Оснастка розетка–розетка на порт 1 и порт 2 (её номер СИ), выбранная оператором; null — не выбрана. */
export type KitFixtures = [string | null, string | null];
/** «Оснастка развёрнута» на порту 1 и 2: к кабелю обращена не та сторона, что при её съёмке. */
export type KitReversed = [boolean, boolean];
const NOT_REVERSED: KitReversed = [false, false];

const fixtureNumber = (view: KitView, instrumentId: string) =>
  view.fixture_choices.find((choice) => choice.instrument_id === instrumentId)?.number ?? "оснастку";

/** Что нужно, чтобы снять меру сейчас: выбранная оснастка, снятая на текущей калибровке этого прохода. */
export function kitPieceCapture(view: KitView, piece: KitPiece, fixtures: KitFixtures, reversed: KitReversed = NOT_REVERSED): BenchAction {
  if (!piece.in_band) {
    return { kind: "kit-capture", label: `Снять ${piece.label}`, reason: `${piece.name} снимают только в проходе «до 3 ГГц»` };
  }
  const slots = fixtures.slice(0, piece.fixtures_needed);
  const missing = slots.findIndex((value) => !value);
  if (missing >= 0) {
    return { kind: "kit-capture", label: `Снять ${piece.label}`, reason: `Выберите оснастку розетка–розетка для порта ${missing + 1}` };
  }
  if (piece.fixtures_needed === 2 && slots[0] === slots[1]) {
    return { kind: "kit-capture", label: `Снять ${piece.label}`, reason: "На порты 1 и 2 нужны разные оснастки" };
  }
  for (const instrumentId of slots as string[]) {
    const fixture = view.fixtures.find((item) => item.instrument_id === instrumentId);
    if (!fixture?.bands[view.band]?.current) {
      return { kind: "kit-capture", label: `Снять оснастку ${fixtureNumber(view, instrumentId)}`, capture: { fixture_id: instrumentId } };
    }
  }
  return {
    kind: "kit-capture", label: `Снять ${piece.label}`, pieceId: piece.id,
    capture: { standard_id: piece.id, fixtures: (slots as string[]).map((instrumentId, slot) => ({ instrument_id: instrumentId, reversed: reversed[slot] })) },
  };
}

/** Одно главное действие «Характеристики мер»: калибровка → меры прохода по порядку → второй проход → сохранить →
 *  активировать. Выбранная оператором мера (selected) идёт вне очереди. Решает сервер; здесь только навигация. */
export function kitMainAction(state: VnaBenchState, selected: string | null, fixtures: KitFixtures, reversed: KitReversed = NOT_REVERSED): BenchAction {
  const session = state.session?.status === "open" ? state.session : null;
  const view = session?.characterization;
  if (!session || !view) return { kind: "open", label: "Открыть сеанс «Характеристика мер»" };
  if (session.command || session.steps.some((step) => step.status === "running")) {
    return { kind: "wait", label: "Выполняется…", reason: "Дождитесь завершения текущего действия" };
  }
  if (view.missing.length) {
    return { kind: "wait", label: "Внесите меры набора", reason: `Не внесено мер набора: ${view.missing.length} — укажите их маркировку ниже` };
  }
  const preparation = benchStepAction(session, state.calibration.valid);
  const chosen = view.pieces.find((piece) => piece.id === selected);
  if (chosen) return preparation ?? kitPieceCapture(view, chosen, fixtures, reversed);
  const next = view.pieces.find((piece) => piece.in_band && !piece.captures[view.band]);
  if (next) return preparation ?? kitPieceCapture(view, next, fixtures, reversed);
  const other: KitBand = view.band === "low" ? "high" : "low";
  if (view.pieces.some((piece) => piece.bands.includes(other) && !piece.captures[other])) {
    const name = view.bands.find((band) => band.key === other)?.label ?? other;
    return { kind: "kit-band", label: `Перейти к проходу «${name}»`, band: other };
  }
  const unsaved = view.pieces.find((piece) => piece.can_save);
  if (unsaved) return { kind: "kit-save", label: `Сохранить ${unsaved.label}`, pieceId: unsaved.id };
  if (view.can_activate && view.activation) return { kind: "kit-activate", label: "Активировать набор" };
  if (view.pieces.length && view.pieces.every((piece) => piece.active)) {
    return { kind: "wait", label: "Набор действует", reason: `Поколение ${view.kit.generation}: все восемь мер опубликованы` };
  }
  const failed = view.pieces.find((piece) => piece.latest_revision && !piece.latest_revision.valid);
  if (failed) return { kind: "wait", label: "Переснимите меру", reason: `${failed.label}: проверка не пройдена — выберите меру и снимите заново` };
  return preparation ?? { kind: "wait", label: "Выберите меру", reason: "Выберите меру в списке" };
}

/** Выбор оснастки по умолчанию — снятые в этом проходе (на текущей калибровке — первыми); выбор оператора важнее. */
export function kitDefaultFixtures(view: KitView | null | undefined, chosen: KitFixtures): KitFixtures {
  if (!view) return chosen;
  const captured = [...view.fixtures].sort((a, b) => Number(Boolean(b.bands[view.band]?.current)) - Number(Boolean(a.bands[view.band]?.current)))
    .filter((item) => item.bands[view.band]).map((item) => item.instrument_id);
  const first = chosen[0] ?? captured.find((id) => id !== chosen[1]) ?? null;
  const second = chosen[1] ?? captured.find((id) => id !== first) ?? null;
  return [first, second];
}
