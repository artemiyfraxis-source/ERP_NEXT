import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { ApiError, failureText, productionApi, rfInterfacesApi, vnaCaptureApi, type ProductionInstrument, type VnaAgent } from "./api";
import { formatMoment, formatNumber, glueUnits, toNumber } from "./format";
import { VswrChart } from "./VswrChart";
import { ArinstPartialChart } from "./vnaCapture";
import { VnaTargetList } from "./VnaTargetList";
import { ScreenSwitcher, VnaScreensEditor, lossTitle } from "./VnaScreensEditor";
import { benchItem, benchMainAction } from "./vnaBenchWorkflow";
import {
  CALIBRATION_REFUSALS,
  arinstBenchApi,
  type ArinstBenchState,
  type ArinstOptions,
  type ArinstProfile,
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

type ArinstAct = (key: string, operation: () => Promise<ArinstBenchState>) => Promise<void>;
const ARINST_SAVE_REASON = "Сохранение в КБ/СИ — после стендового сравнения Arinst";
const ARINST_STANDARDS = { OPEN: "открытый конец", SHORT: "короткое замыкание (КЗ)", LOAD: "согласованная нагрузка", THRU: "перемычка для S21" };
const arinstProfileText = (profile: ArinstProfile | null) => profile
  ? `${num(profile.start_hz / 1e6, 3)}–${num(profile.stop_hz / 1e6, 3)} МГц · ${formatNumber(profile.points)} точек · ПЧ ${formatNumber(profile.if_bandwidth_hz)} Гц`
  : "Профиль сеанса не задан";

/** Separate protocol-3 workplace: no ACM, automatic verdict or full two-port claims. */
export function ArinstBench({ instrumentId, pc, agentOnline, lastSeen, connection }: {
  instrumentId: string; pc: string; agentOnline: boolean; lastSeen: string | null; connection?: ReactNode;
}) {
  const [state, setState] = useState<ArinstBenchState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState<{ key: string; text: string; failed: boolean } | null>(null);
  const [orientation, setOrientation] = useState<"forward" | "reverse">("forward");
  const [retry, setRetry] = useState(0);
  const pending = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const calibrationPanel = useRef<HTMLDetailsElement>(null);
  const accept = (next: ArinstBenchState) => {
    if (next.source !== "arinst" || next.device.instrument_id !== instrumentId) throw new Error("Ответ относится к другому прибору — обновите карточку");
    return next;
  };
  useEffect(() => {
    mounted.current = true;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    async function poll() {
      if (pending.current) { if (active) timer = setTimeout(poll, POLL_MS); return; }
      controller = new AbortController();
      const stamp = generation.current;
      try {
        const next = accept(await arinstBenchApi.state(instrumentId, controller.signal));
        if (active && stamp === generation.current) { setState(next); setLoadError(""); }
      } catch (reason) {
        if (active && stamp === generation.current) setLoadError(failureText(reason, "Не удалось прочитать состояние Arinst — проверьте связь"));
      } finally {
        if (active) timer = setTimeout(poll, POLL_MS);
      }
    }
    void poll();
    return () => { active = false; mounted.current = false; clearTimeout(timer); controller?.abort(); };
    // The instrument is keyed by its registry UUID in AnalyserCard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrumentId, retry]);
  useEffect(() => {
    if (feedback?.text === "Отправлено агенту — ожидается результат" && state && !state.session?.command) {
      setFeedback({ ...feedback, text: state.session?.error ? `Отказ: ${state.session.error}` : !state.session
        ? "Сеанс завершён; состояние обновлено" : "Команда завершена", failed: Boolean(state.session?.error) });
    }
  }, [state, feedback]);

  const act: ArinstAct = async (key, operation) => {
    if (pending.current) return;
    pending.current = true; generation.current += 1;
    setBusy(key); setFeedback({ key, text: "Отправлено…", failed: false });
    try {
      const next = accept(await operation());
      if (mounted.current) { setState(next); setLoadError(""); setFeedback({ key, text: next.session?.command ? "Отправлено агенту — ожидается результат" : "Принято сервером", failed: false }); }
    } catch (reason) {
      if (mounted.current) {
        setFeedback({ key, text: `Отказ: ${failureText(reason, "Команда Arinst не выполнена")}`, failed: true });
        setLoadError("Обновляем состояние после отказа — дождитесь связи с сервером");
      }
    } finally { pending.current = false; if (mounted.current) { setBusy(""); setRetry((value) => value + 1); } }
  };
  const result = (key: string) => feedback?.key === key
    ? <span role="status" className={feedback.failed ? "tone-danger" : "machine-muted"}>{feedback.text}</span> : null;
  if (!state) return <div className="vna-bench">{loadError
    ? <p className="error-banner" role="alert">{loadError} <button type="button" className="secondary" onClick={() => setRetry((value) => value + 1)}>Повторить</button></p>
    : <p className="empty" role="status">Загрузка состояния Arinst…</p>}</div>;

  const { device, session, profile, calibration } = state;
  const open = session?.status === "open";
  const owned = open && session.owned === true;
  const ready = owned && session.state === "ready" && !session.command;
  const connectionReason = loadError || (!device.connection_ready ? "Проверьте привязку прибора и связь с агентом закреплённого ПК" : "");
  const sessionConnectionReason = connectionReason || (!ready ? "Завершите текущий шаг или прервите сеанс" : busy ? "Дождитесь ответа сервера" : "");
  const reconnect = ready && !device.control_ready && device.connection_ready;
  const refreshHint = !device.control_ready && device.connection_ready ? "Подключите прибор через ERP — обновите данные о нём" : "";
  const unavailable = loadError ? "Нет свежего состояния — проверьте связь" : !device.control_ready ? device.reason || "Прибор не готов к управлению через ERP"
    : !open ? "Сначала откройте сеанс" : !owned ? `Сеанс ведёт ${session.operator || "другой сотрудник"}`
      : !ready ? "Завершите текущий шаг или прервите сеанс" : busy ? "Дождитесь ответа сервера" : "";
  const operationReason = (op: string) => unavailable || (!device.supported_ops.includes(op) ? "Операция не подтверждена агентом для этого прибора" : "");
  const current = session?.steps[session.step];
  const awaiting = owned && (session.state === "awaiting_standard" || session.state === "awaiting_dut");
  const standard = current?.args.standard;
  const prompt = standard ? `Подключите ${ARINST_STANDARDS[standard]} в плоскости измерения и подтвердите.`
    : current?.label || "Подключите изделие в выбранной ориентации и подтвердите.";
  const appOpen = Boolean(device.reason?.includes("Закройте ArinstVL"));
  const nextStep = loadError ? "Восстановите связь с ERP" : appOpen ? "Закройте ArinstVL на ПК прибора"
    : refreshHint || (!device.control_ready ? device.reason || "Проверьте подключение прибора" : !open ? "Откройте сеанс через ERP"
      : awaiting ? `Подключите ${standard ? ARINST_STANDARDS[standard] : "изделие"} по инструкции ниже` : session.command ? "Дождитесь выполнения команды" : !profile ? "Задайте профиль сеанса"
        : "Выберите калибровку или снимите диагностические S11/S21");
  const primaryLabel = !open ? "Открыть сеанс через ERP" : reconnect ? "Переподключить через ERP" : awaiting ? "Подключено — измерить"
    : session.command || !ready ? "Выполняется…" : !calibration.valid ? "Калибровать" : "Замерить";
  const primaryReason = !open ? connectionReason : reconnect ? sessionConnectionReason
    : awaiting ? loadError || (!device.control_ready || !current?.human
      || !device.supported_ops.includes(current?.op === "measure_standard" ? "measure_standard" : "capture") ? nextStep : "")
      : operationReason(!calibration.valid ? "measure_standard" : "capture") || (!profile ? "Сначала задайте профиль сеанса" : "");
  function primaryAction() {
    if (busy || primaryReason) return;
    if (!open) void act("session", () => arinstBenchApi.start(instrumentId));
    else if (reconnect) void act("session", () => arinstBenchApi.connect(session.id, session.version));
    else if (awaiting) void act("confirm", () => arinstBenchApi.confirm(session.id, session.version));
    else if (!calibration.valid && calibrationPanel.current) {
      calibrationPanel.current.open = true;
      calibrationPanel.current.querySelector("summary")?.focus();
      calibrationPanel.current.scrollIntoView?.({ block: "nearest" });
    } else void act("capture", () => arinstBenchApi.capture(session.id, session.version, orientation));
  }
  return <div className="vna-bench" style={{ fontVariantNumeric: "tabular-nums" }}>
    <section className="panel vna-bench-head" aria-label="Состояние Arinst">
      <div className="vna-bench-status-row">
        <div className="vna-bench-status-strip" role="status">
          <strong className={loadError || !device.control_ready ? "tone-warn" : ""}>{loadError ? "Нет свежего состояния" : appOpen ? "Закройте ArinstVL на ПК прибора" : refreshHint || (device.control_ready ? "ВАЦ готов" : "ВАЦ не готов")}</strong>
          <span>{calibration.valid ? "Калибровка подтверждена" : "Нужна калибровка"}</span>
          <span>{open ? `Сеанс: ${session.operator || "оператор не указан"}` : "Сеанс не открыт"}</span>
        </div>
        <div className="vna-bench-main-action"><button type="button" className="primary" disabled={Boolean(busy || primaryReason)}
          aria-describedby={primaryReason ? "arinst-action-reason" : undefined} onClick={primaryAction}>{primaryLabel}</button>
          {primaryReason && <span className="machine-muted" id="arinst-action-reason">{primaryReason}</span>}
        </div>
      </div>
      <details className="vna-bench-connection"><summary>Подключение · {pc || "ПК не подтверждён"}{lastSeen ? ` · ${formatMoment(lastSeen)}` : ""}</summary>
      <p className={`analyser-status ${loadError || !device.control_ready ? "tone-warn" : ""}`}>{nextStep}</p>
      <p className="machine-muted">ПК последнего обнаружения: {pc || "не подтверждён"} · Агент этого ПК: {agentOnline ? "на связи" : "связь не подтверждена"}
        {lastSeen ? ` · сигнал ${formatMoment(lastSeen)}` : ""} · Прибор: {loadError ? "данные устарели" : device.control_ready ? "готов к управлению" : "не готов"}
        {` · ArinstVL ${device.control_ready && !loadError ? "закрыт" : appOpen ? "открыт" : "— состояние не подтверждено"}`}</p>
      <p>Для управления через ERP программа ArinstVL должна быть закрыта.</p>
      {connection}
      </details>
      <p className="machine-muted">Связь: протокол ArinstVL</p>
      {loadError && <p role="alert" className="error-banner">{loadError}</p>}
      <div className="analyser-command" aria-label="Сеанс Arinst">
        {open && <>
            {!reconnect && <button type="button" className="secondary" disabled={Boolean(sessionConnectionReason)}
              onClick={() => void act("session", () => arinstBenchApi.connect(session.id, session.version))}>Переподключить через ERP</button>}
            <button type="button" className="secondary" disabled={Boolean(sessionConnectionReason)}
              onClick={() => void act("session", () => arinstBenchApi.close(session.id, session.version))}>Закрыть сеанс</button>
            <button type="button" className="secondary" disabled={Boolean(!owned || busy || loadError || session.state === "releasing")}
              onClick={() => void act("session", () => arinstBenchApi.abort(session.id, session.version))}>Прервать сеанс</button>
          </>}
        {result("session")}
      </div>
      {unavailable && <p className="machine-muted">{unavailable}</p>}
      {session?.error && <p role="alert" className="error-banner">{session.error}</p>}
      {session?.command && <p role="status">Команда выполняется агентом — дождитесь результата. Повторно отправлять её не нужно.</p>}
      {awaiting && <div className="vna-bench-active-check">
        <h3>Подключение: {standard ? ARINST_STANDARDS[standard] : "изделие"}</h3><p>{prompt}</p>
        <div className="analyser-command">{result("confirm")}</div>
      </div>}
    </section>
    <ArinstProfileEditor profile={profile} options={device.options ?? null} reason={operationReason("apply_profile")} act={act} session={session} result={result("profile")} />
    <ArinstCalibration panelRef={calibrationPanel} reason={operationReason("measure_standard") || (!profile ? "Сначала задайте профиль сеанса" : "")} session={session} act={act} result={result("calibration")} />
    <section className="machine-facts" aria-label="Диагностический замер">
      <h3>Диагностический замер S11 / S21</h3>
      <p className={calibration.valid ? "" : "warning-inline"}>{calibration.valid ? "Калибровка подтверждена" : calibration.status === "blocked"
        ? `Калибровка не подтверждена: ${calibration.reason || "нужна независимая проверка"}`
        : calibration.status === "acquiring" ? "Снимаются меры — следуйте инструкции подключения выше" : calibration.reason || "Калибровка не выполнена"}</p>
      <div className="analyser-command">
        <label>Ориентация <select name="arinst-orientation" value={orientation} onChange={(event) => setOrientation(event.target.value as "forward" | "reverse")}>
          <option value="forward">Прямая</option><option value="reverse">Обратная</option>
        </select></label>
        <button type="button" className="secondary" disabled={Boolean(operationReason("capture") || !profile)}
          onClick={() => session && void act("capture", () => arinstBenchApi.capture(session.id, session.version, orientation))}>Снять S11 / S21</button>{result("capture")}
      </div>
      {(operationReason("capture") || !profile) && <p className="machine-muted">{operationReason("capture") || "Сначала задайте профиль сеанса"}</p>}
      <div className="analyser-command"><button type="button" className="secondary" disabled>Сохранить в КБ/СИ</button>
        <span>{state.acceptance.eligible ? "Диагностическая трасса не предназначена для записи в КБ/СИ" : ARINST_SAVE_REASON}</span></div>
      {!state.acceptance.eligible && <p className="machine-muted">{state.acceptance.message}</p>}
      {state.latest_trace ? <ArinstPartialChart trace={state.latest_trace} /> : <p className="machine-muted">Трасс ещё нет — подключите изделие и снимите S11/S21.</p>}
    </section>
    {session && session.artifacts.length > 0 && <details className="machine-facts"><summary>Квитанции прибора · {session.artifacts.length}</summary>
      <ul>{session.artifacts.map((artifact, index) => <li key={artifact.id}><a href={arinstBenchApi.artifactUrl(session.id, artifact.id)} download>
        Скачать квитанцию {index + 1}</a> <span className="machine-muted" style={{ overflowWrap: "anywhere" }}>SHA-256: {artifact.sha256}</span></li>)}</ul>
    </details>}
  </div>;
}

function ArinstProfileEditor({ profile, options, reason, act, session, result }: {
  profile: ArinstProfile | null; options: ArinstOptions | null; reason: string; act: ArinstAct; session: ArinstBenchState["session"]; result: ReactNode;
}) {
  const unavailable = reason || (!options ? "Переподключите прибор, чтобы получить доступные диапазон и полосу ПЧ" : "");
  const [draft, setDraft] = useState(() => profile
    ? { start: String(profile.start_hz / 1e6), stop: String(profile.stop_hz / 1e6), points: String(profile.points), ifbw: String(profile.if_bandwidth_hz) }
    : { start: "", stop: "", points: "", ifbw: "" });
  const [review, setReview] = useState<ArinstProfile | null>(null);
  const [error, setError] = useState("");
  const signature = JSON.stringify(profile);
  const readback = useRef(signature);
  useEffect(() => {
    if (readback.current === signature) return;
    readback.current = signature;
    setDraft(profile ? { start: String(profile.start_hz / 1e6), stop: String(profile.stop_hz / 1e6), points: String(profile.points), ifbw: String(profile.if_bandwidth_hz) }
      : { start: "", stop: "", points: "", ifbw: "" });
    setReview(null);
    setError("");
    // Reset only on actual readback changes, not on each state poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
  function submit(event: FormEvent) {
    event.preventDefault();
    const frequencyHz = (text: string) => {
      const normalized = text.trim().replace(",", ".");
      if (!/^\d+(?:\.\d{1,6})?$/.test(normalized)) return NaN;
      const [whole, fraction = ""] = normalized.split(".");
      return Number(whole) * 1e6 + Number(fraction.padEnd(6, "0"));
    };
    const next = { start_hz: frequencyHz(draft.start), stop_hz: frequencyHz(draft.stop), points: Number(draft.points), if_bandwidth_hz: Number(draft.ifbw) };
    if (!options || ![next.start_hz, next.stop_hz].every(Number.isSafeInteger) || next.start_hz < options.scan_range_hz[0] || next.stop_hz > options.scan_range_hz[1] || next.stop_hz <= next.start_hz
      || !Number.isInteger(next.points) || next.points < 2 || next.points > 1000 || next.stop_hz - next.start_hz < next.points - 1 || !options.bandwidths_hz.includes(next.if_bandwidth_hz)) {
      setError("Укажите возрастающий диапазон прибора с целыми частотами в Гц, 2–1000 разных точек и полосу ПЧ из списка"); return;
    }
    setError(""); setReview(next);
  }
  return <details className="machine-facts"><summary>Профиль · {arinstProfileText(profile)}</summary>
    <form onSubmit={submit}>
      <p>В сеансе: {arinstProfileText(profile)}</p>
      {options && <p className="machine-muted">Диапазон прибора: {num(options.scan_range_hz[0] / 1e6, 3)}–{num(options.scan_range_hz[1] / 1e6, 3)} МГц</p>}
      <div className="form-grid">
        {([['start', 'Начало, МГц'], ['stop', 'Конец, МГц'], ['points', 'Число точек']] as const).map(([key, label]) => <label key={key}>{label}
          <input name={`arinst-${key}`} autoComplete="off" inputMode={key === "points" ? "numeric" : "decimal"} required value={draft[key]}
            onChange={(event) => { setDraft({ ...draft, [key]: event.target.value }); setReview(null); }} /></label>)}
        <label>Полоса ПЧ, Гц<select name="arinst-ifbw" required value={draft.ifbw} onChange={(event) => { setDraft({ ...draft, ifbw: event.target.value }); setReview(null); }}>
          <option value="">Выберите полосу</option>{(options?.bandwidths_hz ?? []).map((value) => <option key={value} value={value}>{formatNumber(value)}</option>)}
        </select></label>
      </div>
      {error && <p role="alert" className="error-banner">{error}</p>}
      <div className="analyser-command"><button type="submit" className="secondary" disabled={Boolean(unavailable)}>Проверить изменения</button>{result}</div>
      {unavailable && <p className="machine-muted">{unavailable}</p>}
      {review && <div className="vna-bench-active-check"><p>В сеансе: {arinstProfileText(profile)} → Применить: {arinstProfileText(review)}. Калибровка станет недействительной.</p>
        <div className="analyser-command"><button type="button" className="primary" disabled={Boolean(unavailable)} onClick={() => {
          if (session) { void act("profile", () => arinstBenchApi.profile(session.id, session.version, review)); setReview(null); }
        }}>Применить профиль сеанса</button><button type="button" className="secondary" onClick={() => setReview(null)}>Отменить изменения</button></div>
      </div>}
    </form>
  </details>;
}

function ArinstCalibration({ reason, session, act, result, panelRef }: { reason: string; session: ArinstBenchState["session"]; act: ArinstAct; result: ReactNode;
  panelRef: React.RefObject<HTMLDetailsElement | null> }) {
  const [instruments, setInstruments] = useState<ProductionInstrument[] | null>(null);
  const [opened, setOpened] = useState(false);
  const [kit, setKit] = useState("");
  const [kitReady, setKitReady] = useState(false);
  const [kitReason, setKitReason] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [plane, setPlane] = useState("");
  const [transmission, setTransmission] = useState(true);
  useEffect(() => {
    if (!opened || instruments) return;
    let active = true;
    productionApi.instruments().then(({ instruments: rows }) => { if (active) { setInstruments(rows.filter((row) => row.status === "Действует")); setError(""); } })
      .catch((reason) => { if (active) setError(failureText(reason, "Не удалось загрузить реестр мер")); });
    return () => { active = false; };
  }, [opened, attempt, instruments]);
  useEffect(() => {
    setKitReady(false);
    if (!kit) { setKitReason("Выберите зарегистрированный калибровочный набор"); return; }
    let active = true;
    setKitReason("Проверяем роль набора…");
    rfInterfacesApi.get(kit).then(({ rf }) => { if (active) { setKitReady(rf.role === "solt_kit"); setKitReason(rf.role === "solt_kit" ? "" : "Это не набор SOL: набор нагрузок SMA-MF06 не заменяет открытый конец и КЗ"); } })
      .catch((reason) => { if (active) setKitReason(failureText(reason, "Не удалось проверить роль набора")); });
    return () => { active = false; };
  }, [kit]);
  const disabledReason = reason || kitReason || (!kitReady ? "Выберите набор SOL" : !plane.trim() ? "Укажите плоскость измерения" : "");
  return <details ref={panelRef} className="machine-facts" onToggle={(event) => setOpened(event.currentTarget.open)}><summary>Калибровка по мерам · открытый конец → КЗ → нагрузка → перемычка</summary>
    <p>Меры SMA-MF06: открытый конец, КЗ и нагрузка; для S21 — перемычка. Используйте набор с зарегистрированной ролью SOL, а не только набор нагрузок.</p>
    <p className="warning-inline">Снятие мер сохраняет исходные данные. Для калибровки нужны подтверждённые характеристики мер; для сохранения результата в КБ/СИ — стендовое сравнение.</p>
    {error && <p role="alert" className="error-banner">{error} <button type="button" className="secondary" onClick={() => setAttempt((value) => value + 1)}>Повторить загрузку мер</button></p>}
    {!instruments && !error && <p role="status">Загрузка реестра мер…</p>}
    {instruments?.length === 0 && <p className="machine-muted">В реестре нет действующих мер — добавьте калибровочный набор.</p>}
    <div className="form-grid"><label>Калибровочный набор<select name="arinst-kit" value={kit} onChange={(event) => { setKitReady(false); setKit(event.target.value); }}>
      <option value="">Выберите набор из реестра</option>{instruments?.map((row) => <option key={row.id} value={row.id}>{row.inventory_number} · {row.name}</option>)}
    </select></label>
      <label>Плоскость измерения<input name="arinst-plane" autoComplete="off" maxLength={300} value={plane} onChange={(event) => setPlane(event.target.value)} /></label>
      <label><span>Передача S21</span><span style={{ display: "flex", alignItems: "center", gap: 8 }}><input type="checkbox" style={{ width: "auto" }} name="arinst-transmission" checked={transmission} onChange={(event) => setTransmission(event.target.checked)} /> С перемычкой</span></label>
    </div>
    <div className="analyser-command"><button type="button" className="secondary" disabled={Boolean(disabledReason)}
      onClick={() => session && void act("calibration", () => arinstBenchApi.calibration(session.id, session.version, kit, plane.trim(), transmission))}>Начать калибровку по мерам</button>{result}</div>
    {disabledReason && <p className="machine-muted">{disabledReason}</p>}
  </details>;
}

export function VnaBench({ canEdit, connection }: { canEdit: boolean; connection?: (device: VnaBenchDevice) => ReactNode }) {
  const [state, setState] = useState<VnaBenchState | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<Tab>("measure");
  const [attempt, setAttempt] = useState(0);
  const [notice, setNotice] = useState("");
  const stateRequest = useRef<Promise<VnaBenchState> | null>(null);
  const operationPending = useRef(false);
  const stateGeneration = useRef(0);

  useEffect(() => {
    let active = true;
    if (operationPending.current) return;
    const stamp = stateGeneration.current;
    const request = stateRequest.current ?? vnaBenchApi.state();
    stateRequest.current = request;
    request.then((next) => { if (active && stamp === stateGeneration.current) { setState(next); setLoadError(""); } })
      .catch((reason) => { if (active && stamp === stateGeneration.current) setLoadError(failureText(reason, "Не удалось прочитать состояние ВАЦ")); })
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
    if (operationPending.current) return;
    operationPending.current = true;
    stateGeneration.current += 1;
    stateRequest.current = null;
    setBusy(key); setError("");
    try {
      setState(await operation());
      setLoadError("");
    } catch (reason) {
      setError(refusalOf(reason));
      setAttempt((value) => value + 1);
    } finally {
      operationPending.current = false;
      setBusy("");
    }
  };
  /** «Показать на ВАЦ»: отказ возвращается строкой — его показывает тот, кто вызвал (окно экрана, строка вида). */
  const showScreen = async (screenId: string): Promise<string> => {
    const open = state?.session?.status === "open" ? state.session : null;
    if (!open) return "Сеанс не открыт — откройте его на вкладке «Замер»";
    if (operationPending.current) return "Дождитесь окончания предыдущего действия и нажмите ещё раз";
    operationPending.current = true;
    stateGeneration.current += 1;
    stateRequest.current = null;
    setBusy("show-screen");
    try {
      setState(await vnaBenchApi.showScreen(open.id, open.version, screenId));
      return "";
    } catch (reason) {
      return refusalOf(reason);
    } finally {
      operationPending.current = false;
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
  const notReady = !state.device.agent_online || state.device.vna_online === false
    || state.device.s2vna_ready !== true || Boolean(state.device.refusal) || Boolean(loadError);
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
    <ObjectPanel state={state} session={session} calibrationValid={state.calibration.valid} canEdit={canAct}
      busy={Boolean(busy) || commandBusy} blockedStart={notReady} act={act} open={open}
      freshReadings={!loadError && state.device.agent_online && state.device.vna_online !== false}
      visible={tab === "measure"} header={(action) => <>
    <BenchHeader state={state} action={tab === "measure" ? action : !session && canAct
      ? <button type="button" className="primary" disabled={Boolean(busy) || notReady} onClick={() => void open()}>Открыть сеанс</button> : null}
      connection={connection?.(state.device)} />
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
      {canAct && <button type="button" className="secondary small" disabled={Boolean(busy)}
        onClick={() => { if (window.confirm(`Прервать «${session.command?.label}»? Измерение остановится, результат не сохранится.`)) void act("abort", () => vnaBenchApi.abort(session.id, session.version)); }}>Прервать</button>}
    </div>}
    <div className="seg-tabs sub vna-bench-tools" role="tablist" aria-label="Разделы рабочего места ВАЦ" onKeyDown={tabKeys}>
      {TABS.map((item) => <button key={item.id} type="button" role="tab" id={`vna-bench-tab-${item.id}`} aria-controls="vna-bench-panel"
        aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} className={tab === item.id ? "on" : ""}
        onClick={() => setTab(item.id)}>{item.label}</button>)}
    </div>
    </>} />
    <div className="vna-bench-body" role={tab === "measure" ? undefined : "tabpanel"}
      id={tab === "measure" ? undefined : "vna-bench-panel"} aria-labelledby={`vna-bench-tab-${tab}`}>
      {session && tab === "measure" && canAct && state.calibration.valid && <details className="vna-bench-view"><summary>Вид графиков на приборе</summary><ScreenSwitcher session={session}
        disabled={Boolean(busy) || Boolean(session.command)} show={showScreen} /></details>}
      {session && tab === "measure" && canAct && <div className="form-actions vna-bench-close">
        <button type="button" className="secondary small" disabled={Boolean(busy) || Boolean(session.command)}
          onClick={() => { if (window.confirm("Закрыть сеанс? Калибровка и шаги текущего сеанса будут сброшены.")) void act("close", () => vnaBenchApi.close(session.id, session.version)); }}>Закрыть сеанс</button>
      </div>}
      {tab === "checks" && <ChecksPanel state={state} session={session} canEdit={canAct} busy={Boolean(busy)} act={act} />}
      {tab === "screens" && <VnaScreensEditor session={session} calibrationValid={state.calibration.valid} canEdit={canEdit}
        show={showScreen} />}
    </div>
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

function BenchHeader({ state, action, connection }: { state: VnaBenchState; action: ReactNode; connection?: ReactNode }) {
  const { device, calibration } = state;
  const ready = device.s2vna_ready === true;
  const weekly = dueText(state.due.find((due) => due.kind === "weekly"));
  const monthly = dueText(state.due.find((due) => due.kind === "monthly"));
  const nextDue = [...state.due].filter((due) => due.kind !== "flex").sort((a, b) =>
    (a.status === "ok" ? 1 : 0) - (b.status === "ok" ? 1 : 0) || (a.due_at ?? "").localeCompare(b.due_at ?? ""))[0];
  const nextDueText = dueText(nextDue);
  const status = !device.agent_online ? "Нет связи с ПК" : device.vna_online === false ? "ВАЦ не подключён"
    : device.refusal ? "ВАЦ не готов" : ready ? "ВАЦ готов" : "ВАЦ не готов";
  return <div className="panel vna-bench-head">
    <div className="vna-bench-status-row">
      <div className="vna-bench-status-strip" role="status">
        <strong className={!device.agent_online || device.vna_online === false ? "tone-danger" : !ready || device.refusal ? "tone-warn" : ""}>{status}</strong>
        <span>Прогрев {device.warmup_min === null ? "не подтверждён" : `${formatNumber(device.warmup_min)} / 40 мин`}</span>
        <span className={calibration.valid ? "" : "tone-warn"}>{calibration.valid ? `Калибровка до ${clock(calibration.valid_until)}` : "Нужна калибровка"}</span>
        <span className={nextDue?.status === "ok" ? "" : nextDueText.tone}>{nextDue ? `${nextDue.kind === "weekly" ? "Еженедельная" : "Ежемесячная"}: ${nextDueText.text}` : "Срок проверок неизвестен"}</span>
      </div>
      <div className="vna-bench-main-action">{action}</div>
    </div>
    <details className="vna-bench-connection"><summary><b>Подключение</b> · связь с агентом: {device.agent_online ? "На связи" : "Нет связи"} · ПК {device.bound_pc ?? device.pc_name ?? "не указан"}{device.read_at ? ` · ${clock(device.read_at)}` : ""}</summary>
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
    {connection}
    </details>
    {device.refusal && <div className="warning-inline" role="alert">{refusalText(device.refusal)}</div>}
    {device.last_seen_pc && device.last_seen_pc !== (device.bound_pc ?? device.pc_name)
      && device.refusal?.code !== "vna_not_connected" && <p className="warning-inline">
        Последний раз ВАЦ виден {formatMoment(device.last_seen_at)} на {device.last_seen_pc}.
      </p>}
  </div>;
}

function StepList({ session, calibrationValid, calibrationReason }: {
  session: VnaBenchSession; calibrationValid: boolean; calibrationReason: string | null;
}) {
  const byKey = new Map(session.steps.map((step) => [step.key, step]));
  const current = STEP_ORDER.find((key) => byKey.get(key)?.status !== "passed");
  const currentStep = current ? byKey.get(current) : undefined;
  // «Не годен» или отказ из-за калибровки (К?, температура, 4 ч) — только новая калибровка (СТО 11.4, 11.5).
  // Отказ по другой причине (прибор занят, агент не на связи) — причина и «Повторить» того же шага.
  const failed = currentStep?.status === "failed"
    || (currentStep?.status === "refused" && CALIBRATION_REFUSALS.has(currentStep.refusal?.code ?? ""));
  const refused = currentStep?.status === "refused" && !failed;
  const expired = !current && !calibrationValid;
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
    </div>}
  </div>;
}

/** Подпись графика плана: сервер шлёт «Модуль S21» — на экране «Передача (S21), дБ» (только показ). */
const planTraceTitle = (label: string) => {
  const match = /^Модуль (S11|S21|S12|S22)$/.exec(label);
  return match ? lossTitle(match[1] as "S11" | "S21" | "S12" | "S22") : label;
};

function PlanCard({ plan }: { plan: VnaBenchPlan }) {
  return <>
    <dl className="vna-bench-plan">
      <div><dt>Полоса</dt><dd className="num">{formatNumber(plan.band_mhz[0], { digits: 0 })} – {formatNumber(plan.band_mhz[1], { digits: 0 })} МГц</dd></div>
      <div><dt>Норма</dt><dd>{plan.norm_vswr === null ? "не установлена — не оценивается"
        : `КСВН ≤ ${vswr(plan.norm_vswr)}${plan.uncertainty !== null ? `, U ${num(plan.uncertainty, 3)}` : ""}`}{plan.norm_source ? ` · ${glueUnits(plan.norm_source)}` : ""}</dd></div>
    </dl>
    <details className="vna-bench-settings"><summary>Параметры замера</summary><dl className="vna-bench-plan">
    <div><dt>Экран</dt><dd>{plan.screen_name || "—"}</dd></div>
    <div><dt>Точек</dt><dd className="num">{formatNumber(plan.points, { digits: 0 })}</dd></div>
    <div><dt>ПЧ</dt><dd className="num">{formatNumber(plan.ifbw_hz >= 1000 ? plan.ifbw_hz / 1000 : plan.ifbw_hz, { digits: 1 })} {plan.ifbw_hz >= 1000 ? "кГц" : "Гц"}</dd></div>
    <div><dt>Мощность</dt><dd className="num">{formatNumber(plan.power_dbm, { digits: 1 })} дБм</dd></div>
    <div className="wide"><dt>Графики</dt><dd>{plan.traces.map((trace) => glueUnits(`${planTraceTitle(trace.label)} (${trace.scale})`)).join(" · ") || "—"}</dd></div>
  </dl></details></>;
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

function ObjectPanel({ state, session, calibrationValid, canEdit, busy, blockedStart, act, open, header, visible, freshReadings }: {
  state: VnaBenchState; header: (action: ReactNode) => ReactNode; visible: boolean;
  freshReadings: boolean;
  session: VnaBenchSession | null; calibrationValid: boolean; canEdit: boolean; busy: boolean; blockedStart: boolean; act: Act;
  open: (assembly?: { task_id: string; operation_id?: string | null }) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<VnaBenchTarget[] | null>(null);
  const [target, setTarget] = useState<VnaBenchTarget | null>(() => session?.items.at(-1)?.target ?? null);
  const [plan, setPlan] = useState<VnaBenchPlan | null>(null);
  const [error, setError] = useState("");
  const [looking, setLooking] = useState(false);
  const [fixtures, setFixtures] = useState<ProductionInstrument[] | null>(null);
  const [fixtureError, setFixtureError] = useState("");
  const [fixtureAttempt, setFixtureAttempt] = useState(0);
  const [fmaxText, setFmaxText] = useState("");
  const [fmaxError, setFmaxError] = useState("");
  const [cleaned, setCleaned] = useState(false);
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
    if (!session && canEdit && !busy && !blockedStart) {
      await open(next.kind === "assembly" ? { task_id: next.task_id, operation_id: next.operation_id ?? null } : undefined);
    } else await loadPlan(next);
  }

  // Сеанс сменился (открыт сеанс для КБ) — план пересчитывается по новой калибровке.
  useEffect(() => { if (target) void loadPlan(target); }, [sessionId]);
  const instrumentSelected = target?.kind === "instrument";
  useEffect(() => {
    if (!instrumentSelected || fixtures !== null) return;
    let active = true;
    productionApi.instruments().then((result) => { if (active) { setFixtures(result.instruments); setFixtureError(""); } })
      .catch((reason) => { if (active) setFixtureError(failureText(reason, "Не удалось загрузить оснастку")); });
    return () => { active = false; };
  }, [instrumentSelected, fixtures, fixtureAttempt]);

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

  const item = benchItem(session, target);
  // Замер объекта идёт (не окончательный и не отказ) — цепочку и частоту не меняют.
  const inProgress = item !== null && !itemDone(item);
  const blocked = !session ? "" : !ready ? (stepsDone ? "Калибровка недействительна — откалибруйте заново."
    : "Сначала пройдите калибровку и нагрузку на обоих портах.") : "";
  const wrongCalibration = plan?.refusal?.code === "other_calibration";
  const assemblyOpen = target?.kind === "assembly" ? { task_id: target.task_id, operation_id: target.operation_id ?? null } : undefined;
  const suggestedGhz = plan && !plan.refusal ? plan.band_mhz[1] / 1000 : null;
  const action = benchMainAction(state, target, plan);
  const calibrationAction = action.kind === "step" && action.step === "calibration";
  const actionReason = blockedStart ? state.device.refusal ? refusalText(state.device.refusal)
    : state.device.agent_online && state.device.vna_online !== false && state.device.warmup_min !== null && state.device.warmup_min < 40
    ? `Дождитесь прогрева: ещё ${formatNumber(40 - state.device.warmup_min)} мин` : "Проверьте связь с ВАЦ и готовность прибора" : busy ? "Дождитесь завершения текущего действия"
    : calibrationAction && !state.device.acm ? "Подключите АКМ по USB" : calibrationAction && !cleaned ? "Подтвердите очистку разъёмов" : action.reason;
  async function execute() {
    if (!canEdit || busy || actionReason) return;
    if (action.kind === "open") { await open(assemblyOpen); return; }
    if (!session) return;
    if (action.kind === "step" && action.step) await act(`step:${action.step}`, () => vnaBenchApi.runStep(session.id, session.version, action.step!));
    else if (action.kind === "measure" && target) await act("measure", () => vnaBenchApi.measure(session.id, session.version, target));
    else if (action.kind === "connection" && item) await act("connection", () => vnaBenchApi.nextConnection(session.id, item.id, item.version));
    else if (action.kind === "record" && item) await act("record", () => vnaBenchApi.record(session.id, item.id, item.version));
  }
  return <>{header(canEdit && <>
    <button type="button" className="primary" disabled={Boolean(actionReason) || action.kind === "wait"} aria-describedby={actionReason ? "vna-action-reason" : undefined}
      onClick={() => void execute()}>{action.label}</button>
    {actionReason && <span className="machine-muted" id="vna-action-reason">{actionReason}</span>}
  </>)}
  <div className="vna-bench-body" hidden={!visible} role="tabpanel" id={visible ? "vna-bench-panel" : undefined} aria-labelledby="vna-bench-tab-measure">
  <div className="panel vna-bench-object">
    <h2>Что меряем</h2>
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
    {!target && !candidates && <p className="muted-text">Отсканируйте СИ или КБ — сеанс откроется на нужной полосе.</p>}
    {target && <h3 className="vna-bench-selected">{targetTitle(target)}</h3>}
    {target && <button type="button" className="secondary small" disabled={busy || looking || inProgress || Boolean(session?.command)}
      onClick={() => {
        planRequest.current += 1;
        setTarget(null); setPlan(null); setCandidates(null); setQuery(""); setError("");
      }}>Выбрать другой объект</button>}
  </div>
  <div className={`vna-bench-readiness${!freshReadings ? " machine-muted" : ""}`} role="group" aria-label="Готовность к калибровке">
    <span>{freshReadings && state.device.warmup_min !== null && state.device.warmup_min >= 40 ? "✓" : "○"} Прогрев ≥ 40 мин</span>
    <span>{!freshReadings ? "○ АКМ: нет свежих данных" : state.device.acm ? "✓ АКМ подключён" : "○ Подключите АКМ по USB"}</span>
    <label><input type="checkbox" name="vna-cleaned" checked={cleaned} disabled={!canEdit || busy}
      onChange={(event) => setCleaned(event.target.checked)} /> Разъёмы осмотрены и почищены</label>
  </div>
  {session && (ready ? <details className="vna-bench-settings vna-bench-completed"><summary>Калибровка пройдена · 4 шага</summary>
    <StepList session={session} calibrationValid={calibrationValid} calibrationReason={state.calibration.reason} /></details>
    : <StepList session={session} calibrationValid={calibrationValid} calibrationReason={state.calibration.reason} />)}
    {target && <div className="panel vna-bench-target">
      <h3>Замер и результат</h3>
      {!session && <>
        <p className="vna-bench-hint">{target.kind === "assembly"
          ? `Для КБ калибровка идёт на полосе ЭТК задания ${target.task_number}.`
          : "СИ — полоса «ОТК — переход», 10 МГц – 18 ГГц."}</p>
      </>}
      {session && !plan && !error && <p className="empty" role="status">Составляю план замера…</p>}
      {session && plan && <>
        {target.kind === "instrument" && !wrongCalibration && <details className="vna-bench-settings"><summary>Оснастка и частота · {target.left || target.right ? "с оснасткой" : "напрямую"}</summary><div className="vna-bench-chain">
          {fixtureError && <p className="error-banner" role="alert">{fixtureError} <button type="button" className="secondary" onClick={() => setFixtureAttempt((value) => value + 1)}>Повторить загрузку оснастки</button></p>}
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
        </div></details>}
        {!wrongCalibration && <PlanCard plan={plan} />}
        {plan.refusal && <div className="warning-inline" role="alert">{refusalText(plan.refusal)}{wrongCalibration && target.kind === "assembly"
          ? ` Для КБ калибровка идёт на полосе ЭТК задания ${target.task_number}: текущий сеанс закроется, откроется новый.` : ""}</div>}
        {!item && !plan.refusal && <>
          <p className="vna-bench-hint">{plan.connection_hint ?? "Подключите объект через кабели, не к портам ВАЦ напрямую."}</p>
          {blocked && <p className="tone-warn">{blocked}</p>}
        </>}
      </>}
      {session && item && <ItemResult item={item} remeasure={action.kind === "record" && canEdit && plan && !plan.refusal
        ? () => void act("measure", () => vnaBenchApi.measure(session.id, session.version, target)) : undefined} busy={busy || blockedStart} />}
    </div>}
  </div></>;
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

function ItemResult({ item, remeasure, busy }: { item: VnaBenchItem; remeasure?: () => void; busy: boolean }) {
  const [printing, setPrinting] = useState(false);
  const [printed, setPrinted] = useState("");
  const [printError, setPrintError] = useState("");
  const verdict = item.verdict;
  const prompt = connectionPrompt(item);

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
    {prompt && <div className="vna-bench-prompt" role="status">
      <strong>{prompt}</strong>
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
      </div> : null}
    {remeasure && <button type="button" className="secondary" disabled={busy} onClick={remeasure}>Измерить заново</button>}
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
