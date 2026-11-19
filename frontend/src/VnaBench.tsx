import { useEffect, useState, type FormEvent } from "react";
import { ApiError, failureText } from "./api";
import { formatMoment, formatNumber } from "./format";
import { VswrChart } from "./VswrChart";
import {
  refusalText,
  vnaBenchApi,
  type VnaBenchCheck,
  type VnaBenchCheckKind,
  type VnaBenchCheckPhase,
  type VnaBenchDue,
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
   (СИ.002) управляется только отсюда, S2VNA — для просмотра графиков. Порядок сеанса: калибровка АКМ → доверительный
   тест → нагрузка на порт 1 и порт 2 → объект (СИ или КБ). Каждый шаг говорит словами, что куда подключить. */

const POLL_MS = 2000;

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
  calibration: "Подключите АКМ к концам обоих кабелей: порт 1 и порт 2. Кабели закрепите — после калибровки их не двигать. Калибровка идёт около 30 секунд.",
  confidence: "АКМ остаётся на кабелях. Прибор сравнит замер с памятью АКМ: передача в пределах ±0,1 дБ, отражение ±0,4 дБ до 10 ГГц и ±0,5 дБ выше.",
  load_port1: "Отключите АКМ, не сдвигая кабели. Наверните нагрузку LOAD 6650F27 на кабель порта 1 и затяните тарированным ключом. Норма — КСВН не больше 1,06 во всей полосе.",
  load_port2: "Перенесите нагрузку LOAD на кабель порта 2 и затяните ключом. Норма та же — КСВН не больше 1,06.",
};
const STEP_ACTIONS: Record<VnaBenchStepKey, string> = {
  calibration: "Калибровать АКМ",
  confidence: "Запустить доверительный тест",
  load_port1: "Измерить нагрузку на порту 1",
  load_port2: "Измерить нагрузку на порту 2",
};

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
  { kind: "flex", title: "Шевеление кабелей", what: "При каждой калибровке. LOAD на кабеле: КСВН остаётся ≤ 1,06; перемычка СИ.008: S21 меняется не больше ±0,05 дБ." },
  { kind: "weekly", title: "Еженедельная — контрольная мера СИ.009", what: "Переход ADP1A СИ.009 (зав. № N8B140) сравнивается с эталонной кривой от 16.11.2026: отклонение КСВН и S21 не больше U." },
  { kind: "monthly", title: "Ежемесячная — меры 6650F27", what: "OPEN, SHORT, LOAD на каждом порту и перемычки MA6666A/B/C: 0 дБм, ПЧ 1 кГц, три подключения с поворотом на 120°." },
];
const CHECK_TITLES = Object.fromEntries(CHECKS.map((check) => [check.kind, check.title])) as Record<VnaBenchCheckKind, string>;

const STANDARDS: Record<string, string> = { open: "OPEN", short: "SHORT", load: "LOAD" };
const JUMPERS: Record<string, string> = { thru_a: "MA6666A", thru_b: "MA6666B", thru_c: "MA6666C" };

/** Что сделать руками на фазе проверки — словами владельца; неизвестная фаза — её название с сервера. */
export function phaseHint(kind: VnaBenchCheckKind, phase: VnaBenchCheckPhase): string {
  const turn = phase.connection_no && phase.connections_total && phase.connections_total > 1
    ? ` Подключение ${phase.connection_no} из ${phase.connections_total}${phase.connection_no > 1 ? " — отключите, поверните меру на 120° и подключите снова" : ""}.` : "";
  if (kind === "flex") {
    return {
      load_memory: "Наверните LOAD на кабель порта 1 и не трогайте кабель — прибор запомнит график.",
      load_flex: "Пошевелите кабель порта 1 в рабочих пределах и держите изогнутым — КСВН должен остаться не больше 1,06.",
      thru_memory: "Соедините концы кабелей переходом СИ.008 и не трогайте кабели — прибор запомнит S21.",
      thru_flex: "Пошевелите кабели — S21 должен меняться не больше чем на ±0,05 дБ.",
    }[phase.key] ?? phase.label;
  }
  if (kind === "weekly") {
    return "Подключите контрольный переход СИ.009: сторона 1 — к кабелю порта 1, сторона 2 — к кабелю порта 2. Кабели не двигайте.";
  }
  const port = /^(open|short|load)_p([12])$/.exec(phase.key);
  if (port) return `Наверните меру ${STANDARDS[port[1]]} 6650F27 на кабель порта ${port[2]} и затяните ключом.${turn}`;
  if (JUMPERS[phase.key]) return `Соедините концы кабелей перемычкой ${JUMPERS[phase.key]}.${turn}`;
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
  ? `si:${target.instrument_id}` : `kb:${target.task_id}:${target.position}:${target.serial ?? ""}`;
export const targetTitle = (target: VnaBenchTarget) => target.kind === "instrument"
  ? `${target.number} · ${target.name}${target.serial_number ? `, зав. № ${target.serial_number}` : ""}`
  : `${target.task_number} · ${target.item}, позиция ${target.position}${target.serial !== null ? `, изделие № ${target.serial}` : ""} · ${target.name}`;

export function VnaBench({ canEdit }: { canEdit: boolean }) {
  const [state, setState] = useState<VnaBenchState | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<"measure" | "checks">("measure");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    vnaBenchApi.state().then((next) => { if (active) { setState(next); setLoadError(""); } })
      .catch((reason) => { if (active) setLoadError(failureText(reason, "Не удалось прочитать состояние ВАЦ")); });
    return () => { active = false; };
  }, [attempt]);

  // Пока агент выполняет команду — опрос раз в 2 с; без команды экран не дёргает сервер.
  const polling = sessionBusy(state);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setTimeout(() => setAttempt((value) => value + 1), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [polling, state, attempt]);

  async function act(key: string, operation: () => Promise<VnaBenchState>) {
    if (busy) return;
    setBusy(key); setError("");
    try {
      setState(await operation());
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) setAttempt((value) => value + 1);
      const detail = reason instanceof ApiError ? reason.detail as { code?: unknown; message?: unknown } | undefined : undefined;
      setError(detail && typeof detail.code === "string"
        ? refusalText({ code: detail.code, message: typeof detail.message === "string" ? detail.message : failureText(reason, "") })
        : failureText(reason, "Команда ВАЦ не выполнена"));
    } finally {
      setBusy("");
    }
  }

  if (!state) {
    return <section className="vna-bench" aria-label="ВАЦ — рабочее место">
      {loadError ? <>
        <div className="error-banner" role="alert">{loadError}</div>
        <button type="button" className="secondary small" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
      </> : <p className="empty">Загрузка…</p>}
    </section>;
  }
  const session = state.session?.status === "open" ? state.session : null;
  return <section className="vna-bench" aria-label="ВАЦ — рабочее место">
    <BenchHeader state={state} />
    {error && <div className="error-banner" role="alert">{error}</div>}
    {loadError && <div className="warning-inline" role="status">{loadError} Показано последнее известное состояние.</div>}
    {session?.command && <div className="vna-bench-command" role="status">
      <span>Выполняется: {session.command.label}{session.command.state === "queued" ? " — ждёт агента" : ""}, с {clock(session.command.since)}</span>
      {canEdit && <button type="button" className="secondary small" disabled={Boolean(busy)}
        onClick={() => void act("abort", () => vnaBenchApi.abort(session.id, session.version))}>Прервать</button>}
    </div>}
    <div className="seg-tabs sub" role="tablist" aria-label="Разделы рабочего места ВАЦ">
      <button type="button" role="tab" aria-selected={tab === "measure"} className={tab === "measure" ? "on" : ""} onClick={() => setTab("measure")}>Замер</button>
      <button type="button" role="tab" aria-selected={tab === "checks"} className={tab === "checks" ? "on" : ""} onClick={() => setTab("checks")}>Проверки</button>
    </div>
    {!session && <div className="panel vna-bench-start">
      <p>Сеанс не открыт. Перед началом: ВАЦ включён и прогрет не меньше 40 минут, S2VNA показывает «Готов», АКМ подключён по USB
        и горит зелёным, разъёмы осмотрены и почищены.</p>
      {canEdit && state.device.s2vna_ready === false && <p className="tone-warn">Начать можно, когда S2VNA покажет «Готов».</p>}
      {canEdit && <button type="button" className="primary" disabled={Boolean(busy) || Boolean(state.device.refusal) || state.device.s2vna_ready === false}
        onClick={() => void act("start", () => vnaBenchApi.startSession())}>Начать сеанс</button>}
    </div>}
    {session && tab === "measure" && <>
      <StepList session={session} canEdit={canEdit} busy={Boolean(busy)}
        run={(step) => void act(`step:${step}`, () => vnaBenchApi.runStep(session.id, session.version, step))} />
      <ObjectPanel session={session} calibrationValid={state.calibration.valid} canEdit={canEdit} busy={Boolean(busy)} act={act} />
      {canEdit && <div className="form-actions vna-bench-close">
        <button type="button" className="secondary small" disabled={Boolean(busy) || Boolean(session.command)}
          onClick={() => void act("close", () => vnaBenchApi.close(session.id, session.version))}>Закрыть сеанс</button>
      </div>}
    </>}
    {tab === "checks" && <ChecksPanel state={state} session={session} canEdit={canEdit} busy={Boolean(busy)} act={act} />}
  </section>;
}

function temperatureText(state: VnaBenchState): string {
  const t = state.device.temperatures;
  if (!t) return "нет данных";
  const vna = t.vna_c.map((value) => value === null ? "—" : `${num(value, 1)} °C`).join(" / ");
  return `ВАЦ ${vna} · АКМ ${t.acm_c === null ? "—" : `${num(t.acm_c, 1)} °C`}`;
}

function dueText(due: VnaBenchDue | undefined): { text: string; tone: string } {
  if (!due) return { text: "нет данных", tone: "tone-neutral" };
  const day = due.due_at ? formatMoment(due.due_at).slice(0, 5) : "";
  if (due.status === "ok") return { text: `до ${day}`, tone: "tone-ok" };
  if (due.status === "due") return { text: `сегодня, ${day}`, tone: "tone-warn" };
  if (due.status === "failed") return { text: "не пройдена — замеры остановлены", tone: "tone-danger" };
  return { text: `просрочена с ${day}`, tone: "tone-danger" };
}

function BenchHeader({ state }: { state: VnaBenchState }) {
  const { device, calibration } = state;
  const ready = device.s2vna_ready === true;
  const weekly = dueText(state.due.find((due) => due.kind === "weekly"));
  const monthly = dueText(state.due.find((due) => due.kind === "monthly"));
  return <div className="panel vna-bench-head">
    <dl className="vna-bench-facts">
      <div><dt>ПК</dt><dd>{device.pc_name ?? "—"} <span className={device.agent_online ? "tone-ok" : "tone-danger"}>{device.agent_online ? "на связи" : "нет связи"}</span></dd></div>
      <div><dt>ВАЦ</dt><dd>{device.instrument_number} {device.model}{device.serial ? `, зав. № ${device.serial}` : ""}</dd></div>
      <div><dt>S2VNA</dt><dd className={ready ? "tone-ok" : "tone-warn"}>{ready ? "Готов" : device.s2vna_ready === false
        ? `Не готов${device.warmup_min !== null ? ` — прогрев ${device.warmup_min} из 40 мин` : ""}` : "нет данных"}</dd></div>
      <div><dt>Температура</dt><dd className="num">{temperatureText(state)}</dd></div>
      <div><dt>Калибровка</dt><dd className={calibration.valid ? "tone-ok" : "tone-warn"}>{calibration.valid
        ? `действует до ${clock(calibration.valid_until)}` : calibration.reason ?? "нет"}</dd></div>
      <div><dt>Еженедельная</dt><dd className={weekly.tone}>{weekly.text}</dd></div>
      <div><dt>Ежемесячная</dt><dd className={monthly.tone}>{monthly.text}</dd></div>
    </dl>
    {device.refusal && <div className="warning-inline" role="alert">{refusalText(device.refusal)}</div>}
  </div>;
}

function StepList({ session, canEdit, busy, run }: {
  session: VnaBenchSession; canEdit: boolean; busy: boolean; run: (step: VnaBenchStepKey) => void;
}) {
  const byKey = new Map(session.steps.map((step) => [step.key, step]));
  const current = STEP_ORDER.find((key) => byKey.get(key)?.status !== "passed");
  const currentStep = current ? byKey.get(current) : undefined;
  const retry = currentStep?.status === "failed" || currentStep?.status === "refused";
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
    {current && <div className="vna-bench-prompt" role="status">
      <span><strong>{STEP_TITLES[current]}.</strong> {STEP_HINTS[current]}</span>
      {canEdit && <button type="button" className="primary small" disabled={busy || Boolean(session.command)}
        onClick={() => run(current)}>{retry ? `Повторить: ${STEP_ACTIONS[current].toLowerCase()}` : STEP_ACTIONS[current]}</button>}
    </div>}
  </div>;
}

function PlanCard({ plan }: { plan: VnaBenchPlan }) {
  return <dl className="vna-bench-plan">
    <div><dt>Экран</dt><dd>{plan.screen_name}</dd></div>
    <div><dt>Полоса</dt><dd className="num">{formatNumber(plan.band_mhz[0], { digits: 0 })} – {formatNumber(plan.band_mhz[1], { digits: 0 })} МГц</dd></div>
    <div><dt>Точек</dt><dd className="num">{formatNumber(plan.points, { digits: 0 })}</dd></div>
    <div><dt>ПЧ</dt><dd className="num">{formatNumber(plan.ifbw_hz >= 1000 ? plan.ifbw_hz / 1000 : plan.ifbw_hz, { digits: 1 })} {plan.ifbw_hz >= 1000 ? "кГц" : "Гц"}</dd></div>
    <div><dt>Мощность</dt><dd className="num">{formatNumber(plan.power_dbm, { digits: 1 })} дБм</dd></div>
    <div className="wide"><dt>Графики</dt><dd>{plan.traces.map((trace) => `${trace.label} (${trace.scale})`).join(" · ") || "—"}</dd></div>
    <div className="wide"><dt>Норма</dt><dd>{plan.norm_vswr === null ? "не установлена — не оценивается"
      : `КСВН ≤ ${vswr(plan.norm_vswr)}${plan.uncertainty !== null ? `, U ${num(plan.uncertainty, 3)}` : ""}`}{plan.norm_source ? ` · ${plan.norm_source}` : ""}</dd></div>
  </dl>;
}

function verdictNumbers(verdict: VnaBenchVerdict, item: VnaBenchItem): string {
  const parts: string[] = [];
  if (verdict.worst_vswr !== null) parts.push(`наибольший КСВН ${vswr(verdict.worst_vswr)}${verdict.worst_mhz !== null ? ` на ${formatNumber(verdict.worst_mhz, { digits: 0 })} МГц` : ""}`);
  if (verdict.norm_vswr !== null) parts.push(`норма ≤ ${vswr(verdict.norm_vswr)}`);
  if (verdict.uncertainty !== null) parts.push(`U ${num(verdict.uncertainty, 3)}`);
  if (item.connections_total > 1) parts.push(`худшее из ${item.connections_total} подключений`);
  return parts.join(" · ");
}

function ObjectPanel({ session, calibrationValid, canEdit, busy, act }: {
  session: VnaBenchSession; calibrationValid: boolean; canEdit: boolean; busy: boolean;
  act: (key: string, operation: () => Promise<VnaBenchState>) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<VnaBenchTarget[] | null>(null);
  const [target, setTarget] = useState<VnaBenchTarget | null>(null);
  const [plan, setPlan] = useState<VnaBenchPlan | null>(null);
  const [error, setError] = useState("");
  const [looking, setLooking] = useState(false);
  const stepsDone = STEP_ORDER.every((key) => session.steps.find((step) => step.key === key)?.status === "passed");
  const ready = stepsDone && calibrationValid;

  async function pick(next: VnaBenchTarget) {
    setTarget(next); setPlan(null); setError("");
    try {
      setPlan(await vnaBenchApi.plan(session.id, next));
    } catch (reason) {
      setError(failureText(reason, "Не удалось составить план замера"));
    }
  }

  async function find(event: FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text || looking) return;
    setLooking(true); setError(""); setCandidates(null);
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

  const item = target ? [...session.items].reverse().find((entry) => targetKey(entry.target) === targetKey(target)) ?? null : null;
  const blocked = !ready ? (stepsDone ? "Калибровка недействительна — откалибруйте заново." : "Сначала пройдите калибровку, доверительный тест и нагрузку на обоих портах.") : "";
  return <div className="panel vna-bench-object">
    <h2>Объект</h2>
    <form className="vna-bench-find" onSubmit={(event) => void find(event)}>
      <label>СИ или КБ — скан или номер
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="СИ.035, ПЗ-0001/3 или скан этикетки" autoComplete="off" />
      </label>
      <button type="submit" className="secondary" disabled={looking || !query.trim()}>{looking ? "Ищу…" : "Найти"}</button>
    </form>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {candidates && candidates.length === 0 && <p className="muted-text">Ничего не найдено — проверьте номер СИ или задание и позицию.</p>}
    {candidates && candidates.length > 1 && <ul className="vna-bench-candidates" aria-label="Найденные объекты">
      {candidates.map((candidate) => <li key={targetKey(candidate)}>
        <button type="button" className={target && targetKey(target) === targetKey(candidate) ? "secondary small on" : "secondary small"}
          onClick={() => void pick(candidate)}>{targetTitle(candidate)}</button>
      </li>)}
    </ul>}
    {target && <div className="vna-bench-target">
      <h3>{targetTitle(target)}</h3>
      {!plan && !error && <p className="empty">Составляю план замера…</p>}
      {plan && <>
        <PlanCard plan={plan} />
        {plan.refusal && <div className="warning-inline" role="alert">{refusalText(plan.refusal)}</div>}
        {!item && !plan.refusal && <>
          <p className="vna-bench-hint">{plan.connection_hint ?? "Подключите объект через кабели, не к портам ВАЦ напрямую."}</p>
          {blocked && <p className="tone-warn">{blocked}</p>}
          {canEdit && <button type="button" className="primary" disabled={busy || Boolean(blocked) || Boolean(session.command)}
            onClick={() => void act("measure", () => vnaBenchApi.measure(session.id, session.version, target))}>Измерить</button>}
        </>}
      </>}
      {item && <ItemResult item={item} session={session} canEdit={canEdit} busy={busy || Boolean(blocked)} act={act} />}
    </div>}
    {!target && !candidates && <p className="muted-text">Отсканируйте этикетку СИ или изделия, или введите номер — экран покажет план замера.</p>}
  </div>;
}

export function connectionPrompt(item: VnaBenchItem): string | null {
  if (item.verdict?.final) return null;
  const done = item.connections.filter((connection) => connection.status === "passed" || connection.status === "failed").length;
  if (done >= item.connections_total || item.connections.some((connection) => connection.status === "running")) return null;
  const next = done + 1;
  return next === 1 ? `Подключение 1 из ${item.connections_total}.`
    : `Подключение ${next} из ${item.connections_total} — отключите объект, поверните разъём на 120° и подключите снова. Кабели не двигайте.`;
}

function ItemResult({ item, session, canEdit, busy, act }: {
  item: VnaBenchItem; session: VnaBenchSession; canEdit: boolean; busy: boolean;
  act: (key: string, operation: () => Promise<VnaBenchState>) => Promise<void>;
}) {
  const verdict = item.verdict;
  const prompt = connectionPrompt(item);
  const next = item.connections.filter((connection) => connection.status === "passed" || connection.status === "failed").length + 1;
  return <div className="vna-bench-result">
    {item.refusal && <div className="error-banner" role="alert">{refusalText(item.refusal)}</div>}
    {verdict && <div className="vna-bench-verdict">
      <span className="status-chip" data-tone={VERDICTS[verdict.code].tone || undefined}>{VERDICTS[verdict.code].label}</span>
      <span className="num">{verdictNumbers(verdict, item)}</span>
      {verdict.reason && <span className="muted-text">{verdict.reason}</span>}
    </div>}
    {prompt && <div className="vna-bench-prompt" role="status">
      <strong>{prompt}</strong>
      {canEdit && <button type="button" className="primary small" disabled={busy || Boolean(session.command)}
        onClick={() => void act("connection", () => vnaBenchApi.nextConnection(session.id, item.id, item.version))}>Измерить подключение {next}</button>}
    </div>}
    {item.connections_total > 1 && <table className="vna-bench-connections" aria-label="Подключения">
      <thead><tr><th scope="col">Подключение</th><th scope="col" className="num">Наибольший КСВН</th><th scope="col" className="num">Частота, МГц</th><th scope="col">Состояние</th></tr></thead>
      <tbody>{Array.from({ length: item.connections_total }, (_, index) => {
        const connection = item.connections.find((entry) => entry.no === index + 1);
        return <tr key={index}><td>{index + 1} из {item.connections_total}</td><td className="num">{vswr(connection?.max_vswr)}</td>
          <td className="num">{connection?.max_mhz != null ? formatNumber(connection.max_mhz, { digits: 0 }) : "—"}</td>
          <td>{connection ? <Chip status={connection.status} /> : <span className="muted-text">не измерено</span>}</td></tr>;
      })}</tbody>
    </table>}
    {item.trace.length > 0 && <VswrChart trace={item.trace} normMax={item.plan.norm_vswr} fineScale
      maxVswr={verdict?.worst_vswr ?? null} maxMhz={verdict?.worst_mhz ?? null} uncertainty={item.plan.uncertainty} />}
    {item.recorded_at
      ? <p className="tone-ok">Записано{item.recorded_to ? `: ${item.recorded_to}` : ""}, {formatMoment(item.recorded_at)}</p>
      : canEdit && verdict?.final && <button type="button" className="primary" disabled={busy}
        onClick={() => void act("record", () => vnaBenchApi.record(session.id, item.id, item.version))}>Записать в карточку</button>}
  </div>;
}

function ChecksPanel({ state, session, canEdit, busy, act }: {
  state: VnaBenchState; session: VnaBenchSession | null; canEdit: boolean; busy: boolean;
  act: (key: string, operation: () => Promise<VnaBenchState>) => Promise<void>;
}) {
  const active = session?.checks.find((check) => check.status === "pending" || check.status === "running") ?? null;
  const lastOf = (kind: VnaBenchCheckKind) => [...(session?.checks ?? [])].reverse().find((check) => check.kind === kind && check !== active);
  const stepsDone = session ? STEP_ORDER.every((key) => session.steps.find((step) => step.key === key)?.status === "passed") : false;
  const canStart = Boolean(session) && stepsDone && state.calibration.valid && !active;
  return <div className="panel vna-bench-checks">
    <h2>Проверки</h2>
    {!session && <p className="muted-text">Проверки идут внутри сеанса — откройте сеанс на вкладке «Замер».</p>}
    {session && !stepsDone && <p className="tone-warn">Сначала пройдите калибровку, доверительный тест и нагрузку на обоих портах.</p>}
    <table className="vna-bench-check-list" aria-label="Проверки ВАЦ">
      <thead><tr><th scope="col">Проверка</th><th scope="col">Что и критерий</th><th scope="col">Срок</th><th scope="col">В этом сеансе</th><th scope="col" aria-label="Действие" /></tr></thead>
      <tbody>{CHECKS.map((check) => {
        const due = check.kind === "flex" ? null : dueText(state.due.find((entry) => entry.kind === check.kind));
        const last = lastOf(check.kind);
        return <tr key={check.kind}>
          <td><strong>{check.title}</strong></td>
          <td className="wrap">{check.what}</td>
          <td className={due?.tone}>{due ? due.text : "каждая калибровка"}</td>
          <td className="result">{last ? <><Chip status={last.status} /> {last.result_text && <span className="num">{last.result_text}</span>}
            {last.refusal && <span className="tone-danger"> {refusalText(last.refusal)}</span>}</> : <span className="muted-text">—</span>}</td>
          <td>{canEdit && <button type="button" className="secondary small" disabled={busy || !canStart}
            onClick={() => session && void act(`check:${check.kind}`, () => vnaBenchApi.startCheck(session.id, session.version, check.kind))}>Начать</button>}</td>
        </tr>;
      })}</tbody>
    </table>
    {active && session && <ActiveCheck check={active} session={session} canEdit={canEdit} busy={busy} act={act} />}
  </div>;
}

function ActiveCheck({ check, session, canEdit, busy, act }: {
  check: VnaBenchCheck; session: VnaBenchSession; canEdit: boolean; busy: boolean;
  act: (key: string, operation: () => Promise<VnaBenchState>) => Promise<void>;
}) {
  const current = check.phases.find((phase) => phase.status === "pending" || phase.status === "running");
  return <div className="vna-bench-active-check">
    <h3>{CHECK_TITLES[check.kind]}</h3>
    <table className="vna-bench-phases" aria-label="Ход проверки">
      <thead><tr><th scope="col">№</th><th scope="col">Что подключено</th><th scope="col">Подключение</th><th scope="col">Состояние</th><th scope="col">Итог</th></tr></thead>
      <tbody>{check.phases.map((phase, index) => <tr key={`${phase.key}:${phase.connection_no ?? 0}`} className={phase === current ? "current" : ""}>
        <td className="num">{index + 1}</td><td>{phase.label}</td>
        <td className="num">{phase.connection_no && phase.connections_total ? `${phase.connection_no} из ${phase.connections_total}` : "—"}</td>
        <td><Chip status={phase.status} /></td><td className="num result">{phase.result_text ?? "—"}</td>
      </tr>)}</tbody>
    </table>
    {check.refusal && <div className="error-banner" role="alert">{refusalText(check.refusal)}</div>}
    {current && <div className="vna-bench-prompt" role="status">
      <strong>{phaseHint(check.kind, current)}</strong>
      {canEdit && current.status === "pending" && <button type="button" className="primary small" disabled={busy || Boolean(session.command)}
        onClick={() => void act("check-next", () => vnaBenchApi.checkNext(session.id, check.id, check.version))}>Измерить</button>}
    </div>}
  </div>;
}
