import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { ApiError, failureText, productionApi, vnaCaptureApi, type ProductionInstrument, type VnaAgent } from "./api";
import { formatMoment, formatNumber, glueUnits, toNumber } from "./format";
import { VswrChart } from "./VswrChart";
import { VnaTargetList } from "./VnaTargetList";
import { ScreenSwitcher, VnaScreensEditor, lossTitle } from "./VnaScreensEditor";
import {
  CALIBRATION_REFUSALS,
  REFUSAL_FALLBACK,
  refusalText,
  vnaBenchApi,
  type VnaBenchCheck,
  type VnaBenchCheckKind,
  type VnaBenchCheckPhase,
  type VnaBenchConnection,
  type VnaBenchDue,
  type VnaBenchDevice,
  type VnaBenchFixture,
  type VnaBenchItem,
  type VnaBenchPlan,
  type VnaBenchSession,
  type VnaBenchState,
  type VnaBenchStatus,
  type VnaBenchStep,
  type VnaBenchStepKey,
  type VnaBenchTarget,
  type VnaBenchVerdict,
} from "./vnaBenchApi";

/* «ОТК → ВАЦ — рабочее место» (план «ВАЦ только через ERP», S5; СТО ФРАК-002-2026 разд. 10–14). ВАЦ Planar S50180
   (СИ.002) управляется только отсюда, S2VNA — для просмотра графиков. Порядок сеанса: калибровка АКМ (доверительный
   тест идёт сам сразу после неё) → нагрузка на порт 1 и порт 2 → объект (СИ или КБ). Сеанс для СИ и проверок — на сетке
   «ОТК — переход»; для КБ — отдельный сеанс на полосе ЭТК задания. Каждый шаг говорит словами, что куда подключить. */

const POLL_MS = 2000;
/** КБ «на границе»: после первого подключения — три повторных (правило record_vswr). */
const ASSEMBLY_REPEATS = 3;

const num = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? "—" : formatNumber(value, { digits, minDigits: digits });
/** КСВН: две-три цифры после запятой — 1,06 и 1,065 различимы у нормы 1,06. */
const vswr = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : formatNumber(value, { digits: 3, minDigits: 2 });
const clock = (value: string | null) => value
  ? new Date(value).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "";

export const STEP_TITLES: Record<VnaBenchStepKey, string> = {
  calibration: "Калибровка АКМ",
  confidence: "Доверительный тест",
  load_port1: "Нагрузка: порт 1",
  load_port2: "Нагрузка: порт 2",
};
const STEP_ORDER: VnaBenchStepKey[] = ["calibration", "confidence", "load_port1", "load_port2"];
const STEP_HINTS: Record<VnaBenchStepKey, string> = {
  calibration: "Подключите АКМ к концам обоих кабелей: порт 1 и порт 2. Кабели закрепите — после калибровки их не двигать. Калибровка идёт около 30 секунд, сразу за ней — доверительный тест.",
  confidence: "Тест идёт сам сразу после калибровки — АКМ с кабелей не снимайте. Норма: передача ±0,1 дБ, отражение ±0,4 дБ до 10 ГГц и ±0,5 дБ выше.",
  load_port1: "Отключите АКМ, не сдвигая кабели. Наверните нагрузку LOAD 6650F27 на кабель порта 1 и затяните тарированным ключом. Норма — КСВН не больше 1,06 во всей полосе.",
  load_port2: "Перенесите нагрузку LOAD на кабель порта 2 и затяните ключом. Норма та же — КСВН не больше 1,06.",
};
const STEP_ACTIONS: Partial<Record<VnaBenchStepKey, string>> = {
  calibration: "Калибровать АКМ",
  load_port1: "Измерить нагрузку на порту 1",
  load_port2: "Измерить нагрузку на порту 2",
};
const RECALIBRATE = "Откалибровать заново";

const STATUS: Record<VnaBenchStatus, { label: string; tone: string }> = {
  pending: { label: "Ждёт", tone: "" },
  running: { label: "Выполняется", tone: "info" },
  passed: { label: "Годен", tone: "success" },
  failed: { label: "Не годен", tone: "danger" },
  refused: { label: "Отказ", tone: "warning" },
};

export const VERDICTS: Record<VnaBenchVerdict["code"], { label: string; tone: string }> = {
  pass: { label: "Соответствует", tone: "success" },
  border: { label: "На границе допуска", tone: "warning" },
  fail: { label: "Не соответствует", tone: "danger" },
  not_assessed: { label: "Не оценивается", tone: "" },
};

const CHECKS: { kind: VnaBenchCheckKind; title: string; what: string }[] = [
  { kind: "flex", title: "Шевеление кабелей", what: "При каждой калибровке. LOAD на кабеле: КСВН остаётся ≤ 1,06; перемычка: S21 меняется не больше ±0,05 дБ." },
  { kind: "weekly", title: "Еженедельная — контрольная мера СИ.009", what: "Переход ADP1A СИ.009 сравнивается с эталонной кривой из его карточки: отклонение КСВН и S21 не больше U." },
  { kind: "monthly", title: "Ежемесячная — меры 6650F27", what: "OPEN, SHORT, LOAD на каждом порту и перемычка: 0 дБм, ПЧ 1 кГц, три подключения с поворотом на 120°." },
];
const CHECK_TITLES = Object.fromEntries(CHECKS.map((check) => [check.kind, check.title])) as Record<VnaBenchCheckKind, string>;

/** Что сделать руками на фазе проверки — словами владельца; ключи фаз задаёт сервер, незнакомая — его название. */
export function phaseHint(kind: VnaBenchCheckKind, phase: VnaBenchCheckPhase): string {
  const turn = phase.connection_no && phase.connections_total && phase.connections_total > 1
    ? ` Подключение ${phase.connection_no} из ${phase.connections_total}${phase.connection_no > 1 ? " — отключите, поверните меру на 120° и подключите снова" : ""}.` : "";
  if (kind === "flex") {
    return {
      load_p1: "Наверните LOAD на кабель порта 1 и изогните кабель в рабочих пределах — КСВН должен остаться не больше 1,06.",
      load_p2: "Перенесите LOAD на кабель порта 2 и изогните кабель в рабочих пределах — КСВН должен остаться не больше 1,06.",
      thru_memory: "Соедините концы кабелей перемычкой и держите кабели в рабочем положении — прибор запомнит графики.",
      thru_flex: "Изогните кабели в рабочих пределах — S21 должен меняться не больше чем на ±0,05 дБ.",
    }[phase.key] ?? phase.label;
  }
  if (kind === "weekly") {
    return "Подключите контрольный переход СИ.009: сторона 1 — к кабелю порта 1, сторона 2 — к кабелю порта 2. Кабели не двигайте.";
  }
  const port = /^(open|short|load)_p([12])$/.exec(phase.key);
  if (port) return `Наверните меру ${port[1].toUpperCase()} из набора 6650F27 на кабель порта ${port[2]} и затяните ключом.${turn}`;
  if (phase.key === "thru") return `Соедините концы кабелей перемычкой из набора (${phase.label}).${turn}`;
  return `${phase.label}.${turn}`;
}

function Chip({ status }: { status: VnaBenchStatus }) {
  const item = STATUS[status];
  return <span className="status-chip" data-tone={item.tone || undefined}>{item.label}</span>;
}

function sessionBusy(state: VnaBenchState | null): boolean {
  const session = state?.session;
  if (!session) return false;
  return Boolean(session.command) || session.steps.some((step) => step.status === "running")
    || session.items.some((item) => item.connections.some((connection) => connection.status === "running"))
    || session.checks.some((check) => check.phases.some((phase) => phase.status === "running"));
}

const targetKey = (target: VnaBenchTarget) => target.kind === "instrument"
  ? `si:${target.instrument_id}` : `kb:${target.task_id}:${target.position}`;
export const targetTitle = (target: VnaBenchTarget) => target.kind === "instrument"
  ? `${target.number} · ${target.name}${target.serial_number ? `, зав. № ${target.serial_number}` : ""}`
  : `${target.task_number} · ${target.item}, изделие № ${target.position}${target.serial !== null && target.serial !== undefined ? ` (${target.serial})` : ""} · ${target.name}`;

type Act = (key: string, operation: () => Promise<VnaBenchState>) => Promise<void>;
type Tab = "measure" | "checks" | "screens";
const TABS: { id: Tab; label: string }[] = [{ id: "measure", label: "Замер" }, { id: "checks", label: "Проверки" },
  { id: "screens", label: "Экраны" }];

export function VnaBench({ canEdit, connection }: { canEdit: boolean; connection?: (device: VnaBenchDevice) => ReactNode }) {
  const [state, setState] = useState<VnaBenchState | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<Tab>("measure");
  const [attempt, setAttempt] = useState(0);
  const [notice, setNotice] = useState("");
  const stateRequest = useRef<Promise<VnaBenchState> | null>(null);

  useEffect(() => {
    let active = true;
    const request = stateRequest.current ?? vnaBenchApi.state();
    stateRequest.current = request;
    request.then((next) => { if (active) { setState(next); setLoadError(""); } })
      .catch((reason) => { if (active) setLoadError(failureText(reason, "Не удалось прочитать состояние ВАЦ")); })
      .finally(() => { if (stateRequest.current === request) stateRequest.current = null; });
    return () => { active = false; };
  }, [attempt]);

  const commandBusy = sessionBusy(state);
  useEffect(() => {
    let timer: number | undefined;
    let active = true;
    const schedule = () => {
      if (active) timer = window.setTimeout(() => setAttempt((value) => value + 1), commandBusy ? POLL_MS : 10_000);
    };
    const request = stateRequest.current;
    if (request) void request.then(schedule, schedule);
    else schedule();
    return () => { active = false; window.clearTimeout(timer); };
  }, [commandBusy, attempt]);

  const refusalOf = (reason: unknown): string => {
    if (reason instanceof ApiError && reason.status === 409) setAttempt((value) => value + 1);
    const detail = reason instanceof ApiError ? reason.detail as { code?: unknown; message?: unknown } | undefined : undefined;
    return (detail && typeof detail.code === "string"
      ? refusalText({ code: detail.code, message: typeof detail.message === "string" ? detail.message : "" })
      : failureText(reason, "")) || REFUSAL_FALLBACK;
  };
  const act: Act = async (key, operation) => {
    if (busy) return;
    setBusy(key); setError("");
    try {
      setState(await operation());
    } catch (reason) {
      setError(refusalOf(reason));
    } finally {
      setBusy("");
    }
  };
  /** «Показать на ВАЦ»: отказ возвращается строкой — его показывает тот, кто вызвал (окно экрана, строка вида). */
  const showScreen = async (screenId: string): Promise<string> => {
    const open = state?.session?.status === "open" ? state.session : null;
    if (!open) return "Сеанс не открыт — откройте его на вкладке «Замер»";
    if (busy) return "Дождитесь окончания предыдущего действия и нажмите ещё раз";
    setBusy("show-screen");
    try {
      setState(await vnaBenchApi.showScreen(open.id, open.version, screenId));
      return "";
    } catch (reason) {
      return refusalOf(reason);
    } finally {
      setBusy("");
    }
  };

  if (!state) {
    return <section className="vna-bench" aria-label="ВАЦ — рабочее место">
      {loadError ? <>
        <div className="error-banner" role="alert">{loadError}</div>
        <button type="button" className="secondary small" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
      </> : <p className="empty" role="status">Загрузка…</p>}
    </section>;
  }
  const session = state.session?.status === "open" ? state.session : null;
  // Чужой сеанс виден, но управлять им может только тот, кто его открыл.
  const canAct = canEdit && (!session || session.owned !== false);
  // Остановить и восстановить чужой сеанс может администратор: открывший мог уйти, а прибор нужен.
  const canRecover = canEdit && Boolean(session) && (session?.owned !== false || Boolean(session?.can_recover));
  const notReady = state.device.s2vna_ready === false || Boolean(state.device.refusal);
  /** Открыть сеанс (для КБ — на полосе ЭТК задания); открытый сеанс сначала закрывается. */
  // Стрелки, Home и End переключают вкладки (как «Разделы ОТК»).
  const tabKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = TABS.findIndex((item) => item.id === tab);
    const next = event.key === "ArrowRight" ? (index + 1) % TABS.length : event.key === "ArrowLeft" ? (index + TABS.length - 1) % TABS.length
      : event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    setTab(TABS[next].id);
    document.getElementById(`vna-bench-tab-${TABS[next].id}`)?.focus();
  };
  const open = (assembly?: { task_id: string; operation_id?: string | null }) => act(assembly ? "open-kb" : "open", async () => {
    if (session) await vnaBenchApi.close(session.id, session.version);
    return vnaBenchApi.startSession(assembly ? { assembly } : {});
  });
  const transfer = (target: VnaAgent) => act("transfer", async () => {
    const offer = state.device.transfer;
    if (!offer || !state.device.instrument_id) return state;
    setNotice("");
    await vnaCaptureApi.transfer(state.device.instrument_id, offer.serial, offer.owner_id, target.id);
    setNotice(`ВАЦ перенесён на ${target.pc}`);
    return vnaBenchApi.state();
  });
  return <section className="vna-bench" aria-label="ВАЦ — рабочее место">
    <BenchHeader state={state} />
    {state.device.transfer && canEdit && <div className="form-actions">
      {state.device.transfer.agents.map((target) => <button type="button" className="secondary" key={target.id}
        disabled={Boolean(busy)} onClick={() => void transfer(target)}>
        {busy === "transfer" ? "Переносим ВАЦ…" : `Перенести ВАЦ на ${target.pc}`}
      </button>)}
    </div>}
    {notice && <p role="status" className="success-banner">{notice}</p>}
    {state.device.transfer_blocked_reason && <p className="warning-inline">{state.device.transfer_blocked_reason}</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {loadError && <div className="warning-inline" role="status">{loadError} Показано последнее известное состояние.</div>}
    {session && session.owned === false && <div className="warning-inline" role="status">
      Сеанс открыл {session.operator ?? "другой сотрудник"} — управлять им может только он.</div>}
    {session?.command && <div className="vna-bench-command" role="status">
      <span>Выполняется: {session.command.label}{session.command.state === "queued" ? " — ждёт агента" : ""}, с {clock(session.command.since)}</span>
      {canRecover && <button type="button" className="secondary small" disabled={Boolean(busy)}
        onClick={() => { if (window.confirm(`Прервать «${session.command?.label}»? Измерение остановится, результат не сохранится.`)) void act("abort", () => vnaBenchApi.abort(session.id, session.version)); }}>Прервать</button>}
    </div>}
    {session?.state === "recovery_required" && !session.command && <div className="vna-bench-command" role="alert">
      <span>Ответ ВАЦ потерян — восстановите сеанс: прибор остановится и вернёт настройки оператора, результат текущего шага не сохранится.
        {session.error ? ` Причина: ${session.error}` : ""}</span>
      {canRecover && <button type="button" className="primary small" disabled={Boolean(busy)}
        onClick={() => void act("recover", () => vnaBenchApi.abort(session.id, session.version))}>
        {busy === "recover" ? "Восстанавливаем…" : "Восстановить"}</button>}
      {canEdit && session.can_release_unrestored && <button type="button" className="secondary small" disabled={Boolean(busy)}
        onClick={() => {
          if (window.confirm("Закрыть сеанс без восстановления? Настройки оператора на S2VNA не вернутся — проверьте их на приборе вручную. Действие записывается в журнал."))
            void act("release-unrestored", () => vnaBenchApi.releaseUnrestored(session.id, session.version));
        }}>{busy === "release-unrestored" ? "Закрываем…" : "Закрыть без восстановления"}</button>}
    </div>}
    <div className="seg-tabs sub" role="tablist" aria-label="Разделы рабочего места ВАЦ" onKeyDown={tabKeys}>
      {TABS.map((item) => <button key={item.id} type="button" role="tab" id={`vna-bench-tab-${item.id}`} aria-controls="vna-bench-panel"
        aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} className={tab === item.id ? "on" : ""}
        onClick={() => setTab(item.id)}>{item.label}</button>)}
    </div>
    <div className="vna-bench-body" role="tabpanel" id="vna-bench-panel" aria-labelledby={`vna-bench-tab-${tab}`}>
      {!session && tab === "measure" && <div className="panel vna-bench-start">
        <p>Сеанс не открыт. Перед началом: ВАЦ включён и прогрет не меньше 40 минут, S2VNA показывает «Готов», АКМ подключён по USB
          и горит зелёным, разъёмы осмотрены и почищены. Сеанс для СИ и проверок калибруется на сетке «ОТК — переход»
          (10 МГц – 18 ГГц); для КБ найдите изделие ниже — сеанс откроется на полосе его ЭТК.</p>
        {canAct && state.device.s2vna_ready === false && <p className="tone-warn">Начать можно, когда S2VNA покажет «Готов».</p>}
        {canAct && <button type="button" className="primary" disabled={Boolean(busy) || notReady}
          onClick={() => void open()}>Открыть сеанс для СИ и проверок</button>}
      </div>}
      {session && tab === "measure" && <StepList session={session} calibrationValid={state.calibration.valid}
        calibrationReason={state.calibration.reason} canEdit={canAct} busy={Boolean(busy)}
        run={(step) => void act(`step:${step}`, () => vnaBenchApi.runStep(session.id, session.version, step))} />}
      {session && tab === "measure" && canAct && state.calibration.valid && <ScreenSwitcher session={session}
        disabled={Boolean(busy) || Boolean(session.command)} show={showScreen} />}
      {tab === "measure" && <ObjectPanel session={session} calibrationValid={state.calibration.valid} canEdit={canAct}
        busy={Boolean(busy)} blockedStart={notReady} act={act} open={(assembly) => void open(assembly)} />}
      {session && tab === "measure" && canAct && <div className="form-actions vna-bench-close">
        <button type="button" className="secondary small" disabled={Boolean(busy) || Boolean(session.command)}
          onClick={() => { if (window.confirm("Закрыть сеанс? Калибровка и шаги текущего сеанса будут сброшены.")) void act("close", () => vnaBenchApi.close(session.id, session.version)); }}>Закрыть сеанс</button>
      </div>}
      {tab === "checks" && <ChecksPanel state={state} session={session} canEdit={canAct} busy={Boolean(busy)} act={act} />}
      {tab === "screens" && <VnaScreensEditor session={session} calibrationValid={state.calibration.valid} canEdit={canEdit}
        show={showScreen} />}
    </div>
    {connection?.(state.device)}
  </section>;
}

function temperatureText(state: VnaBenchState): string {
  const t = state.device.temperatures;
  if (!t) return "нет данных";
  const vna = t.vna_c.map((value) => value === null ? "—" : `${num(value, 1)} °C`).join(" / ");
  return `ВАЦ ${vna} · АКМ ${t.acm_c === null ? "—" : `${num(t.acm_c, 1)} °C`}`;
}

export function dueText(due: VnaBenchDue | undefined): { text: string; tone: string } {
  if (!due) return { text: "нет данных", tone: "tone-neutral" };
  if (due.kind === "flex") {
    return due.status === "ok" ? { text: "пройдена после калибровки", tone: "tone-ok" }
      : due.status === "failed" ? { text: "не пройдена — замените кабель", tone: "tone-danger" }
        : { text: "нужна после калибровки", tone: "tone-warn" };
  }
  const day = due.due_at ? formatMoment(due.due_at).slice(0, 5) : "";
  if (due.status === "ok") return { text: `до ${day}`, tone: "tone-ok" };
  if (due.status === "failed") return { text: "не пройдена — замеры остановлены", tone: "tone-danger" };
  if (due.status === "due") return { text: day ? `сегодня, ${day}` : "ещё не выполнялась", tone: "tone-warn" };
  return { text: day ? `просрочена с ${day}` : "просрочена", tone: "tone-danger" };
}

function BenchHeader({ state }: { state: VnaBenchState }) {
  const { device, calibration } = state;
  const ready = device.s2vna_ready === true;
  const weekly = dueText(state.due.find((due) => due.kind === "weekly"));
  const monthly = dueText(state.due.find((due) => due.kind === "monthly"));
  return <div className="panel vna-bench-head">
    <dl className="vna-bench-facts">
      <div><dt>ПК</dt><dd>{device.bound_pc ?? device.pc_name ?? "—"} <span className={device.agent_online ? "tone-ok" : "tone-danger"}>{device.agent_online ? "Агент на связи" : "Агент не на связи"}</span></dd></div>
      <div><dt>ВАЦ</dt><dd>{device.instrument_number} {device.model}{device.serial ? `, зав. № ${device.serial}` : ""}
        {device.vna_online !== undefined && <span className={device.vna_online ? "tone-ok" : "tone-warn"}> · {device.vna_online ? "ВАЦ на связи" : "ВАЦ не подключён"}</span>}</dd></div>
      <div><dt>S2VNA</dt><dd className={ready ? "tone-ok" : "tone-warn"}>{ready ? "Готов" : device.s2vna_ready === false
        ? `Не готов${device.warmup_min !== null ? ` — прогрев ${device.warmup_min} из 40 мин` : ""}` : "нет данных"}</dd></div>
      <div><dt>Температура</dt><dd className="num">{temperatureText(state)}</dd></div>
      <div><dt>Калибровка</dt><dd className={calibration.valid ? "tone-ok" : "tone-warn"}>{calibration.valid
        ? `действует до ${clock(calibration.valid_until)}` : calibration.reason ?? "нет"}</dd></div>
      <div><dt>Еженедельная</dt><dd className={weekly.tone}>{weekly.text}</dd></div>
      <div><dt>Ежемесячная</dt><dd className={monthly.tone}>{monthly.text}</dd></div>
    </dl>
    {device.refusal && <div className="warning-inline" role="alert">{refusalText(device.refusal)}</div>}
    {device.last_seen_pc && device.last_seen_pc !== (device.bound_pc ?? device.pc_name)
      && device.refusal?.code !== "vna_not_connected" && <p className="warning-inline">
        Последний раз ВАЦ виден {formatMoment(device.last_seen_at)} на {device.last_seen_pc}.
      </p>}
  </div>;
}

function StepList({ session, calibrationValid, calibrationReason, canEdit, busy, run }: {
  session: VnaBenchSession; calibrationValid: boolean; calibrationReason: string | null; canEdit: boolean; busy: boolean;
  run: (step: VnaBenchStepKey) => void;
}) {
  const byKey = new Map(session.steps.map((step) => [step.key, step]));
  const current = STEP_ORDER.find((key) => byKey.get(key)?.status !== "passed");
  const currentStep = current ? byKey.get(current) : undefined;
  // «Не годен» или отказ из-за калибровки (К?, температура, 4 ч) — только новая калибровка (СТО 11.4, 11.5).
  // Отказ по другой причине (прибор занят, агент не на связи) — причина и «Повторить» того же шага.
  const failed = currentStep?.status === "failed"
    || (currentStep?.status === "refused" && CALIBRATION_REFUSALS.has(currentStep.refusal?.code ?? ""));
  const refused = currentStep?.status === "refused" && !failed;
  const running = currentStep?.status === "running" || Boolean(session.command);
  const expired = !current && !calibrationValid;
  const action: { label: string; step: VnaBenchStepKey } | null = failed || expired ? { label: RECALIBRATE, step: "calibration" }
    : refused && current ? { label: "Повторить", step: current }
      : current && STEP_ACTIONS[current] ? { label: STEP_ACTIONS[current] as string, step: current } : null;
  return <div className="panel vna-bench-steps-panel">
    <ol className="vna-bench-steps" aria-label="Шаги сеанса">
      {STEP_ORDER.map((key, index) => {
        const step: VnaBenchStep = byKey.get(key) ?? { key, status: "pending", result_text: null, refusal: null, finished_at: null };
        const isCurrent = key === current;
        return <li key={key} className={isCurrent ? "current" : ""} aria-current={isCurrent ? "step" : undefined}>
          <div className="vna-bench-step-title"><span className="vna-bench-step-no">{index + 1}</span><strong>{STEP_TITLES[key]}</strong>
            <Chip status={step.status} />{step.finished_at && <span className="muted-text num">{formatMoment(step.finished_at)}</span>}</div>
          {step.result_text && <div className="num">{step.result_text}</div>}
          {step.refusal && <div className="tone-danger">{refusalText(step.refusal)}</div>}
        </li>;
      })}
    </ol>
    {(current || expired) && <div className="vna-bench-prompt" role="status">
      <span>{expired ? <><strong>Калибровка недействительна.</strong> {calibrationReason ?? ""} {STEP_HINTS.calibration}</>
        : failed ? <><strong>{STEP_TITLES[current as VnaBenchStepKey]}: не пройдено.</strong> Проверьте соединения и затяжку, затем
          откалибруйте заново. {STEP_HINTS.calibration}</>
          : refused ? <><strong>{STEP_TITLES[current as VnaBenchStepKey]}: команда не выполнена.</strong>{" "}
            {refusalText(currentStep?.refusal ?? { code: "refused", message: "" })} Устраните причину и повторите.</>
          : <><strong>{STEP_TITLES[current as VnaBenchStepKey]}.</strong> {STEP_HINTS[current as VnaBenchStepKey]}</>}</span>
      {canEdit && action && <button type="button" className="primary small" disabled={busy || running}
        onClick={() => run(action.step)}>{action.label}</button>}
    </div>}
  </div>;
}

/** Подпись графика плана: сервер шлёт «Модуль S21» — на экране «Передача (S21), дБ» (только показ). */
const planTraceTitle = (label: string) => {
  const match = /^Модуль (S11|S21|S12|S22)$/.exec(label);
  return match ? lossTitle(match[1] as "S11" | "S21" | "S12" | "S22") : label;
};

function PlanCard({ plan }: { plan: VnaBenchPlan }) {
  return <dl className="vna-bench-plan">
    <div><dt>Экран</dt><dd>{plan.screen_name || "—"}</dd></div>
    <div><dt>Полоса</dt><dd className="num">{formatNumber(plan.band_mhz[0], { digits: 0 })} – {formatNumber(plan.band_mhz[1], { digits: 0 })} МГц</dd></div>
    <div><dt>Точек</dt><dd className="num">{formatNumber(plan.points, { digits: 0 })}</dd></div>
    <div><dt>ПЧ</dt><dd className="num">{formatNumber(plan.ifbw_hz >= 1000 ? plan.ifbw_hz / 1000 : plan.ifbw_hz, { digits: 1 })} {plan.ifbw_hz >= 1000 ? "кГц" : "Гц"}</dd></div>
    <div><dt>Мощность</dt><dd className="num">{formatNumber(plan.power_dbm, { digits: 1 })} дБм</dd></div>
    <div className="wide"><dt>Графики</dt><dd>{plan.traces.map((trace) => glueUnits(`${planTraceTitle(trace.label)} (${trace.scale})`)).join(" · ") || "—"}</dd></div>
    <div className="wide"><dt>Норма</dt><dd>{plan.norm_vswr === null ? "не установлена — не оценивается"
      : `КСВН ≤ ${vswr(plan.norm_vswr)}${plan.uncertainty !== null ? `, U ${num(plan.uncertainty, 3)}` : ""}`}{plan.norm_source ? ` · ${glueUnits(plan.norm_source)}` : ""}</dd></div>
  </dl>;
}

/** Оснастка — карточки СИ со своим файлом .s2p (СТО 12.2: без своего файла замер с оснасткой не выполняют). */
const isFixture = (instrument: ProductionInstrument, exclude: string) => instrument.id !== exclude
  && instrument.files.some((file) => /\.s2p$/i.test(file.file_name));

function FixturePicker({ side, fixtures, value, onChange, disabled }: {
  side: "left" | "right"; fixtures: ProductionInstrument[]; value: VnaBenchFixture | null;
  onChange: (value: VnaBenchFixture | null) => void; disabled: boolean;
}) {
  const label = side === "left" ? "Оснастка слева (к порту 1)" : "Оснастка справа (к порту 2)";
  return <div className="vna-bench-fixture">
    <label>{label}
      <select value={value?.instrument_id ?? ""} disabled={disabled}
        onChange={(event) => onChange(event.target.value ? { instrument_id: event.target.value, reversed: value?.reversed ?? false } : null)}>
        <option value="">нет — напрямую</option>
        {fixtures.map((fixture) => <option key={fixture.id} value={fixture.id}>{fixture.inventory_number} · {fixture.name}</option>)}
      </select>
    </label>
    {value && <label className="vna-bench-check"><input type="checkbox" checked={value.reversed} disabled={disabled}
      onChange={(event) => onChange({ ...value, reversed: event.target.checked })} /> сторона 2 к ВАЦ</label>}
  </div>;
}

function ObjectPanel({ session, calibrationValid, canEdit, busy, blockedStart, act, open }: {
  session: VnaBenchSession | null; calibrationValid: boolean; canEdit: boolean; busy: boolean; blockedStart: boolean; act: Act;
  open: (assembly?: { task_id: string; operation_id?: string | null }) => void;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<VnaBenchTarget[] | null>(null);
  const [target, setTarget] = useState<VnaBenchTarget | null>(null);
  const [plan, setPlan] = useState<VnaBenchPlan | null>(null);
  const [error, setError] = useState("");
  const [looking, setLooking] = useState(false);
  const [fixtures, setFixtures] = useState<ProductionInstrument[] | null>(null);
  const [fmaxText, setFmaxText] = useState("");
  const [fmaxError, setFmaxError] = useState("");
  // Только последний запрос плана: ответ на устаревший (сменили оснастку, объект, сеанс) отбрасывается.
  const planRequest = useRef(0);
  const sessionId = session?.id ?? null;
  const stepsDone = session ? STEP_ORDER.every((key) => session.steps.find((step) => step.key === key)?.status === "passed") : false;
  const ready = stepsDone && calibrationValid;

  async function loadPlan(next: VnaBenchTarget) {
    if (!sessionId) return;
    const request = ++planRequest.current;
    setPlan(null); setError("");
    try {
      const result = await vnaBenchApi.plan(sessionId, next);
      if (request === planRequest.current) setPlan(result);
    } catch (reason) {
      if (request === planRequest.current) setError(failureText(reason, "Не удалось составить план замера"));
    }
  }

  async function pick(next: VnaBenchTarget) {
    setTarget(next); setFmaxText(""); setFmaxError("");
    if (next.kind === "instrument" && fixtures === null) {
      productionApi.instruments().then((result) => setFixtures(result.instruments)).catch(() => setFixtures([]));
    }
    await loadPlan(next);
  }

  // Сеанс сменился (открыт сеанс для КБ) — план пересчитывается по новой калибровке.
  useEffect(() => { if (target) void loadPlan(target); }, [sessionId]);

  async function find(event: FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text || looking) return;
    planRequest.current += 1;
    setLooking(true); setError(""); setCandidates(null); setTarget(null); setPlan(null);
    try {
      const result = await vnaBenchApi.resolve(text);
      setCandidates(result.candidates);
      if (result.candidates.length === 1) await pick(result.candidates[0]);
    } catch (reason) {
      setError(failureText(reason, "Объект не найден"));
    } finally {
      setLooking(false);
    }
  }

  /** Оснастка или верхняя частота изменились — тот же объект, новый план. */
  function retarget(change: Partial<{ left: VnaBenchFixture | null; right: VnaBenchFixture | null; fmax_ghz: number | null }>) {
    if (!target || target.kind !== "instrument") return;
    const next = { ...target, ...change };
    setTarget(next);
    void loadPlan(next);
  }

  function applyFmax() {
    if (!target || target.kind !== "instrument") return;
    const text = fmaxText.trim();
    const value = text ? toNumber(text) : null;
    if (text && (value === null || value <= 0)) {
      // В плане остаётся прежнее значение; поле говорит, что не так.
      setFmaxError("Введите число гигагерц больше нуля, например 6 или 12,4");
      return;
    }
    setFmaxError("");
    if ((value ?? null) !== (target.fmax_ghz ?? null)) retarget({ fmax_ghz: value });
  }

  const item = target && session ? [...session.items].reverse().find((entry) => targetKey(entry.target) === targetKey(target)) ?? null : null;
  // Замер объекта идёт (не окончательный и не отказ) — цепочку и частоту не меняют.
  const inProgress = item !== null && !itemDone(item);
  const blocked = !session ? "" : !ready ? (stepsDone ? "Калибровка недействительна — откалибруйте заново."
    : "Сначала пройдите калибровку и нагрузку на обоих портах.") : "";
  const wrongCalibration = plan?.refusal?.code === "other_calibration";
  const assemblyOpen = target?.kind === "assembly" ? { task_id: target.task_id, operation_id: target.operation_id ?? null } : undefined;
  const suggestedGhz = plan && !plan.refusal ? plan.band_mhz[1] / 1000 : null;
  return <div className="panel vna-bench-object">
    <h2>Объект</h2>
    <form className="vna-bench-find" onSubmit={(event) => void find(event)}>
      <label>СИ или КБ — скан или номер
        <input name="vna-object" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="СИ.035, ПЗ-0001/3 или скан этикетки…" autoComplete="off" spellCheck={false} />
      </label>
      <button type="submit" className="secondary" disabled={looking || !query.trim()}>{looking ? "Ищу…" : "Найти"}</button>
    </form>
    <VnaTargetList sessionId={sessionId} query={query} visible={!target && !looking} onPick={(next) => void pick(next)} />
    {error && <div className="error-banner" role="alert">{error}</div>}
    {candidates && candidates.length === 0 && <p className="muted-text">Ничего не найдено — проверьте номер СИ или задание и позицию.</p>}
    {candidates && candidates.length > 1 && <ul className="vna-bench-candidates" aria-label="Найденные объекты">
      {candidates.map((candidate) => <li key={targetKey(candidate)}>
        <button type="button" className={target && targetKey(target) === targetKey(candidate) ? "secondary small on" : "secondary small"}
          aria-pressed={Boolean(target && targetKey(target) === targetKey(candidate))} onClick={() => void pick(candidate)}>{targetTitle(candidate)}</button>
      </li>)}
    </ul>}
    {target && <div className="vna-bench-target">
      <h3>{targetTitle(target)}</h3>
      <button type="button" className="secondary small" disabled={busy || looking || inProgress || Boolean(session?.command)}
        onClick={() => {
          planRequest.current += 1;
          setTarget(null); setPlan(null); setCandidates(null); setQuery(""); setError("");
        }}>Выбрать другой объект</button>
      {!session && <>
        <p className="vna-bench-hint">{target.kind === "assembly"
          ? `Для КБ калибровка идёт на полосе ЭТК задания ${target.task_number} — откроется отдельный сеанс.`
          : "Переход измеряют в сеансе на сетке «ОТК — переход» — откройте его кнопкой выше."}</p>
        {canEdit && target.kind === "assembly" && <button type="button" className="primary" disabled={busy || blockedStart}
          onClick={() => open(assemblyOpen)}>Открыть сеанс для КБ</button>}
      </>}
      {session && !plan && !error && <p className="empty" role="status">Составляю план замера…</p>}
      {session && plan && <>
        {target.kind === "instrument" && !wrongCalibration && <div className="vna-bench-chain">
          <FixturePicker side="left" fixtures={(fixtures ?? []).filter((f) => isFixture(f, target.instrument_id) && f.id !== target.right?.instrument_id)}
            value={target.left ?? null} disabled={busy || inProgress} onChange={(left) => retarget({ left })} />
          <FixturePicker side="right" fixtures={(fixtures ?? []).filter((f) => isFixture(f, target.instrument_id) && f.id !== target.left?.instrument_id)}
            value={target.right ?? null} disabled={busy || inProgress} onChange={(right) => retarget({ right })} />
          <label className="vna-bench-fmax">Верхняя частота, ГГц
            <input name="vna-fmax" autoComplete="off" spellCheck={false} inputMode="decimal" value={fmaxText} disabled={busy || inProgress} aria-invalid={Boolean(fmaxError)}
              aria-describedby={fmaxError ? "vna-bench-fmax-error" : undefined}
              placeholder={suggestedGhz !== null ? formatNumber(suggestedGhz, { digits: 2 }) : "12,4…"}
              onChange={(event) => setFmaxText(event.target.value)} onBlur={applyFmax}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); applyFmax(); } }} />
            {fmaxError ? <span id="vna-bench-fmax-error" className="tone-danger" role="alert">{fmaxError}</span>
              : <span className="muted-text">{suggestedGhz !== null ? `по серии и норме: до ${formatNumber(suggestedGhz, { digits: 2 })} ГГц` : ""}</span>}
          </label>
        </div>}
        {!wrongCalibration && <PlanCard plan={plan} />}
        {plan.refusal && <div className="warning-inline" role="alert">{refusalText(plan.refusal)}{wrongCalibration && target.kind === "assembly"
          ? ` Для КБ калибровка идёт на полосе ЭТК задания ${target.task_number}: текущий сеанс закроется, откроется новый.` : ""}</div>}
        {wrongCalibration && canEdit && <button type="button" className="primary" disabled={busy || Boolean(session.command)}
          onClick={() => open(assemblyOpen)}>{target.kind === "assembly" ? "Открыть сеанс для КБ" : "Открыть сеанс для СИ и проверок"}</button>}
        {!item && !plan.refusal && <>
          <p className="vna-bench-hint">{plan.connection_hint ?? "Подключите объект через кабели, не к портам ВАЦ напрямую."}</p>
          {blocked && <p className="tone-warn">{blocked}</p>}
          {canEdit && <button type="button" className="primary" disabled={busy || Boolean(blocked) || Boolean(session.command)}
            onClick={() => void act("measure", () => vnaBenchApi.measure(session.id, session.version, target))}>Измерить</button>}
        </>}
      </>}
      {session && item && <ItemResult item={item} session={session} canEdit={canEdit} busy={busy || Boolean(blocked)} act={act}
        remeasure={plan && !plan.refusal ? () => void act("measure", () => vnaBenchApi.measure(session.id, session.version, target)) : null} />}
    </div>}
  </div>;
}

/** Замер объекта закончен: решение окончательное или отказ без идущей команды — можно мерить заново. */
const itemDone = (item: VnaBenchItem) => Boolean(item.verdict?.final)
  || (Boolean(item.refusal) && !item.connections.some((connection) => connection.status === "running"));

/** Подпись подключения: у СИ — «2 из 3», у КБ — первое и три повтора «на границе». */
const connectionName = (item: VnaBenchItem, no: number) => item.target.kind === "assembly"
  ? (no === 1 ? "первое" : `повтор ${no - 1} из ${ASSEMBLY_REPEATS}`) : `${no} из ${item.connections_total}`;

export function connectionPrompt(item: VnaBenchItem): string | null {
  if (item.verdict?.final || item.connections.some((connection) => connection.status === "running")) return null;
  // Первое подключение описывает план («сторона 1 — к порту 1»); здесь — только повторы «на границе».
  const next = item.connections.find((connection) => connection.status === "pending");
  if (!next || next.no === 1) return null;
  const what = item.target.kind === "assembly" ? "изделие" : "объект";
  const name = item.target.kind === "assembly" ? `Повтор ${next.no - 1} из ${ASSEMBLY_REPEATS}` : `Подключение ${next.no} из ${item.connections_total}`;
  return `${name} — отключите ${what}, поверните разъём на 120° и подключите снова. Кабели не двигайте.`;
}

/** Подключение выше нормы, но в пределах нормы + U — «в пределах нормы + U», не «Годен»: не спорит с «на границе». */
function ConnectionChip({ item, connection }: { item: VnaBenchItem; connection: VnaBenchConnection }) {
  const norm = item.plan?.norm_vswr ?? item.verdict?.norm_vswr ?? null;
  if (connection.status === "passed" && norm !== null && connection.max_vswr !== null && connection.max_vswr > norm) {
    return <span className="status-chip" data-tone="warning">в пределах нормы + U</span>;
  }
  return <Chip status={connection.status} />;
}

function verdictNumbers(verdict: VnaBenchVerdict, item: VnaBenchItem): string {
  const parts: string[] = [];
  if (verdict.worst_vswr !== null) parts.push(`наибольший КСВН ${vswr(verdict.worst_vswr)}${verdict.worst_mhz !== null ? ` на ${formatNumber(verdict.worst_mhz, { digits: 0 })} МГц` : ""}`);
  if (verdict.norm_vswr !== null) parts.push(`норма ≤ ${vswr(verdict.norm_vswr)}`);
  if (verdict.uncertainty !== null) parts.push(`U ${num(verdict.uncertainty, 3)}`);
  const measured = item.connections.filter((connection) => connection.status === "passed" || connection.status === "failed").length;
  if (measured > 1) parts.push(`худшее из ${measured} подключений`);
  return parts.join(" · ");
}

function ItemResult({ item, session, canEdit, busy, act, remeasure }: {
  item: VnaBenchItem; session: VnaBenchSession; canEdit: boolean; busy: boolean; act: Act; remeasure: (() => void) | null;
}) {
  const [printing, setPrinting] = useState(false);
  const [printed, setPrinted] = useState("");
  const [printError, setPrintError] = useState("");
  const verdict = item.verdict;
  const prompt = connectionPrompt(item);
  const next = item.connections.find((connection) => connection.status === "pending");

  async function printLabel(id: string) {
    setPrinting(true); setPrinted(""); setPrintError("");
    try {
      await productionApi.printInstrumentLabel(id, 1);
      setPrinted("Этикетка отправлена на принтер.");
    } catch (reason) {
      setPrintError(failureText(reason, "Этикетка не напечатана"));
    } finally {
      setPrinting(false);
    }
  }

  return <div className="vna-bench-result">
    {item.refusal && <div className="error-banner" role="alert">{refusalText(item.refusal)}</div>}
    {verdict && <div className="vna-bench-verdict">
      <span className="status-chip" data-tone={VERDICTS[verdict.code].tone || undefined}>{VERDICTS[verdict.code].label}</span>
      <span className="num">{verdictNumbers(verdict, item)}</span>
      {verdict.reason && <span className="muted-text">{verdict.reason}</span>}
    </div>}
    {prompt && next && <div className="vna-bench-prompt" role="status">
      <strong>{prompt}</strong>
      {canEdit && <button type="button" className="primary small" disabled={busy || Boolean(session.command)}
        onClick={() => void act("connection", () => vnaBenchApi.nextConnection(session.id, item.id, item.version))}>
        Измерить: {connectionName(item, next.no)}</button>}
    </div>}
    {item.connections.length > 1 && <table className="vna-bench-connections" aria-label="Подключения">
      <thead><tr><th scope="col">Подключение</th><th scope="col" className="num">Наибольший КСВН</th><th scope="col" className="num">Частота, МГц</th><th scope="col">Состояние</th></tr></thead>
      <tbody>{item.connections.map((connection) => <tr key={connection.no}>
        <td>{connectionName(item, connection.no)}</td><td className="num">{vswr(connection.max_vswr)}</td>
        <td className="num">{connection.max_mhz != null ? formatNumber(connection.max_mhz, { digits: 0 }) : "—"}</td>
        <td>{connection.status === "pending" ? <span className="muted-text">не измерено</span> : <ConnectionChip item={item} connection={connection} />}</td>
      </tr>)}</tbody>
    </table>}
    {item.trace.length > 0 && <VswrChart trace={item.trace} normMax={item.plan?.norm_vswr ?? null} fineScale
      maxVswr={verdict?.worst_vswr ?? null} maxMhz={verdict?.worst_mhz ?? null} uncertainty={item.plan?.uncertainty ?? null} />}
    {item.recorded_at
      ? <div className="vna-bench-recorded">
        <span className="tone-ok">Записано{item.recorded_to ? `: ${item.recorded_to}` : ""}, {formatMoment(item.recorded_at)}</span>
        {item.label_instrument_id && <button type="button" className="secondary small" disabled={printing}
          onClick={() => void printLabel(item.label_instrument_id as string)}>{printing ? "Печать…" : "Печать этикетки"}</button>}
      </div>
      : canEdit && verdict?.final && item.target.kind === "instrument" && <button type="button" className="primary" disabled={busy}
        onClick={() => void act("record", () => vnaBenchApi.record(session.id, item.id, item.version))}>Записать в карточку</button>}
    {canEdit && remeasure && itemDone(item) && <button type="button" className="secondary" disabled={busy || Boolean(session.command)}
      onClick={remeasure}>Измерить заново</button>}
    {verdict?.final && item.target.kind === "assembly" && !item.recorded_at
      && <p className="muted-text">Каждый замер КБ уже записан в «Замеры КСВН» изделия.</p>}
    {printed && <div className="success-banner" role="status">{printed}</div>}
    {printError && <div className="error-banner" role="alert">{printError}</div>}
  </div>;
}

function ChecksPanel({ state, session, canEdit, busy, act }: {
  state: VnaBenchState; session: VnaBenchSession | null; canEdit: boolean; busy: boolean; act: Act;
}) {
  const active = session?.checks.find((check) => check.status === "pending" || check.status === "running") ?? null;
  const lastOf = (kind: VnaBenchCheckKind) => [...(session?.checks ?? [])].reverse().find((check) => check.kind === kind && check !== active);
  const stepsDone = session ? STEP_ORDER.every((key) => session.steps.find((step) => step.key === key)?.status === "passed") : false;
  const canStart = Boolean(session) && stepsDone && state.calibration.valid && !active;
  return <div className="panel vna-bench-checks">
    <h2>Проверки</h2>
    {!session && <p className="muted-text">Проверки идут внутри сеанса для СИ и проверок — откройте его на вкладке «Замер».</p>}
    {session && !stepsDone && <p className="tone-warn">Сначала пройдите калибровку и нагрузку на обоих портах.</p>}
    <table className="vna-bench-check-list" aria-label="Проверки ВАЦ">
      <thead><tr><th scope="col">Проверка</th><th scope="col">Что и критерий</th><th scope="col">Срок</th><th scope="col">В этом сеансе</th><th scope="col" aria-label="Действие" /></tr></thead>
      <tbody>{CHECKS.map((check) => {
        const due = dueText(state.due.find((entry) => entry.kind === check.kind));
        const last = lastOf(check.kind);
        return <tr key={check.kind}>
          <td><strong>{check.title}</strong></td>
          <td className="wrap">{check.what}</td>
          <td className={due.tone}>{due.text}</td>
          <td className="result">{last ? <><Chip status={last.status} /> {last.result_text && <span className="num">{last.result_text}</span>}
            {last.refusal && <span className="tone-danger"> {refusalText(last.refusal)}</span>}</> : <span className="muted-text">—</span>}</td>
          <td>{canEdit && <button type="button" className="secondary small" disabled={busy || !canStart}
            onClick={() => session && void act(`check:${check.kind}`, () => vnaBenchApi.startCheck(session.id, session.version, { kind: check.kind }))}>Начать</button>}</td>
        </tr>;
      })}</tbody>
    </table>
    {active && session && <ActiveCheck check={active} session={session} canEdit={canEdit} busy={busy} act={act} />}
  </div>;
}

function ActiveCheck({ check, session, canEdit, busy, act }: {
  check: VnaBenchCheck; session: VnaBenchSession; canEdit: boolean; busy: boolean; act: Act;
}) {
  const current = check.phases.find((phase) => phase.status === "pending" || phase.status === "running");
  return <div className="vna-bench-active-check">
    <h3>{CHECK_TITLES[check.kind]}</h3>
    {check.phases.length === 0 && <p className="muted-text">Агент читает описания мер из S2VNA…</p>}
    {check.phases.length > 0 && <table className="vna-bench-phases" aria-label="Ход проверки">
      <thead><tr><th scope="col">№</th><th scope="col">Что подключено</th><th scope="col">Подключение</th><th scope="col">Состояние</th><th scope="col">Итог</th></tr></thead>
      <tbody>{check.phases.map((phase, index) => <tr key={`${phase.key}:${phase.connection_no ?? 0}:${index}`} className={phase === current ? "current" : ""}>
        <td className="num">{index + 1}</td><td>{phase.label}</td>
        <td className="num">{phase.connection_no && phase.connections_total ? `${phase.connection_no} из ${phase.connections_total}` : "—"}</td>
        <td><Chip status={phase.status} /></td><td className="num result">{phase.result_text ?? "—"}</td>
      </tr>)}</tbody>
    </table>}
    {check.refusal && <div className="error-banner" role="alert">{refusalText(check.refusal)}</div>}
    {current && <div className="vna-bench-prompt" role="status">
      <strong>{phaseHint(check.kind, current)}</strong>
      {canEdit && current.status === "pending" && <button type="button" className="primary small" disabled={busy || Boolean(session.command)}
        onClick={() => void act("check-next", () => vnaBenchApi.checkNext(session.id, check.id, check.version))}>Измерить</button>}
    </div>}
  </div>;
}
