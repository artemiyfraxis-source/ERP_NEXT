import { api, type VnaTransfer, type VswrTracePoint } from "./api";

/* Клиент «ВАЦ — рабочее место» (план «ВАЦ только через ERP», слайс S5). Все формы экрана — в этом одном файле, по
   контракту сервера `backend/app/workflows/vna_bench_api.py` (S3/S4): входы — его модели SessionInput/Target/CheckInput,
   ответы — `_state`, `_plan`, `resolve`. Пути — под /api/production/vna-bench/*. Команды прибору асинхронны: POST ставит команду агенту и сразу
   возвращает состояние; экран опрашивает `state`, пока что-то выполняется. Тексты отказов — по-русски с сервера;
   известные коды экран переводит сам (`refusalText`), код наружу не показывается. */

export type VnaBenchStepKey = "calibration" | "confidence" | "load_port1" | "load_port2";
export type VnaBenchStatus = "pending" | "running" | "passed" | "failed" | "refused";
export type VnaBenchCheckKind = "flex" | "weekly" | "monthly";
export type VnaBenchVerdictCode = "pass" | "border" | "fail" | "not_assessed";

/** Отказ агента или сервера: `code` — для перевода на экране, `message` — русский текст сервера. */
export interface VnaBenchRefusal { code: string; message: string }

/** Показания датчиков; неизвестное — null (никогда не выдуманный 0 °C). */
export interface VnaBenchTemperatures { vna_c: (number | null)[]; acm_c: number | null }

export interface VnaBenchDevice {
  /** Имя ПК ОТК, где работает агент. */
  pc_name: string | null;
  /** ПК привязки; наличие ВАЦ на другом ПК не меняет владельца сеанса. */
  bound_pc?: string | null;
  bound_agent_id?: string | null;
  agent_online: boolean;
  agent_last_seen?: string | null;
  agent_ip?: string | null;
  /** Свежий отчёт именно о приборе на закреплённом ПК, отдельно от связи с агентом. */
  vna_online?: boolean;
  last_seen_pc?: string | null;
  last_seen_at?: string | null;
  /** Явный перенос доступен администратору, когда другой живой ПК сообщает прибор сейчас. */
  transfer?: VnaTransfer | null;
  transfer_blocked_reason?: string | null;
  agent_version: string | null;
  /** Карточка ВАЦ в реестре СИ: «СИ.002». */
  instrument_number: string;
  instrument_id: string | null;
  /** «Planar S50180». */
  model: string;
  /** Живой заводской номер с прибора; null — прибор не ответил. */
  serial: string | null;
  /** S2VNA в состоянии «Готов». */
  s2vna_ready: boolean | null;
  /** Минут с запуска S2VNA (прогрев ≥ 40 мин). */
  warmup_min: number | null;
  temperatures: VnaBenchTemperatures | null;
  acm: { model: string; serial: string } | null;
  /** Почему прибором сейчас пользоваться нельзя (нет агента, другой серийный номер, не прогрет). */
  refusal: VnaBenchRefusal | null;
  read_at: string | null;
}

export interface VnaBenchCalibration {
  valid: boolean;
  calibrated_at: string | null;
  valid_until: string | null;
  /** Почему недействительна: «прошло 4 ч», «температура ВАЦ ушла на 1,3 °C», «коррекция К?» … */
  reason: string | null;
  temperatures: VnaBenchTemperatures | null;
}

/** Срок проверки; `flex` — шевеление: «ok» — пройдено после текущей калибровки, «due» — ещё нет. */
export interface VnaBenchDue {
  kind: VnaBenchCheckKind;
  due_at: string | null;
  last_at: string | null;
  status: "ok" | "due" | "overdue" | "failed";
}

export interface VnaBenchStep {
  key: VnaBenchStepKey;
  status: VnaBenchStatus;
  /** Итог с числами: «передача 0,031 дБ ≤ 0,1; отражение 0,12 дБ ≤ 0,4». */
  result_text: string | null;
  refusal: VnaBenchRefusal | null;
  finished_at: string | null;
}

/** Что сейчас выполняет агент: экран показывает «Выполняется: …» и опрашивает состояние. */
export interface VnaBenchCommand { label: string; state: "queued" | "running"; since: string }

/** Оснастка перехода (СТО 12.2): своя карточка СИ с файлом .s2p; `reversed` — её сторона 2 обращена к порту 1. */
export interface VnaBenchFixture { instrument_id: string; reversed: boolean }

export type VnaBenchTarget =
  | {
    kind: "instrument"; instrument_id: string; number: string; name: string; serial_number: string | null;
    /** Цепочка оснастки и верхняя частота ниже, чем позволяют серия и норма; без них — «напрямую», по серии. */
    left?: VnaBenchFixture | null; right?: VnaBenchFixture | null; fmax_ghz?: number | null;
  }
  | {
    kind: "assembly"; task_id: string; task_number: string; position: number; serial: string | number | null; name: string;
    item: string; operation_id?: string | null;
  };

export type VnaBenchInstrumentTarget = Extract<VnaBenchTarget, { kind: "instrument" }> & {
  interfaces_text: string;
  last_measured_at: string | null;
};

export interface VnaBenchPlanTrace { label: string; scale: string }

export interface VnaBenchPlan {
  /** Экран прибора: «ОТК — переход», «ОТК — кабельная сборка», «ОТК — один порт». */
  screen_name: string;
  band_mhz: [number, number];
  points: number;
  ifbw_hz: number;
  power_dbm: number;
  traces: VnaBenchPlanTrace[];
  /** Норма КСВН; null — нормы нет, вердикт «не оценивается». */
  norm_vswr: number | null;
  /** Источник нормы: «ЭТК 591К.3: КД заказчика», «по ДИ разъёмов», «ADP1A, изготовитель». */
  norm_source: string | null;
  /** Расширенная неопределённость U для значения нормы. */
  uncertainty: number | null;
  /** Сторона 1 → порт 1 и т. п. — что куда подключить, словами сервера. */
  connection_hint: string | null;
  /** Почему замер по плану невозможен (нет полосы в ЭТК, профиль > 4001 точки, другая калибровка). */
  refusal: VnaBenchRefusal | null;
}

export interface VnaBenchConnection {
  no: number;
  status: VnaBenchStatus;
  max_vswr: number | null;
  max_mhz: number | null;
  measured_at: string | null;
}

export interface VnaBenchVerdict {
  code: VnaBenchVerdictCode;
  worst_vswr: number | null;
  worst_mhz: number | null;
  norm_vswr: number | null;
  uncertainty: number | null;
  /** Причина «не оценивается» или пояснение «на границе». */
  reason: string | null;
  /** Окончательный (после трёх подключений или сразу) — можно записывать. */
  final: boolean;
}

export interface VnaBenchItem {
  id: string;
  version: number;
  target: VnaBenchTarget;
  plan: VnaBenchPlan;
  /** 1 — обычный замер; 3 — «на границе», повтор с поворотом на 120°. */
  connections_total: number;
  connections: VnaBenchConnection[];
  /** График худшего подключения: КСВН max(S11, S22) по частоте. */
  trace: VswrTracePoint[];
  verdict: VnaBenchVerdict | null;
  refusal: VnaBenchRefusal | null;
  recorded_at: string | null;
  /** Куда записано: «карточка СИ.035» / «ПЗ-0001, изделие № 3». */
  recorded_to: string | null;
  /** После записи СИ — его карточка для «Печать этикетки». */
  label_instrument_id?: string | null;
}

export interface VnaBenchCheckPhase {
  /** flex: load_p1/load_p2/thru_memory/thru_flex; weekly: si009; monthly: open_p1…load_p2 и thru, по 3 подключения. */
  key: string;
  label: string;
  status: VnaBenchStatus;
  connection_no: number | null;
  connections_total: number | null;
  result_text: string | null;
}

export interface VnaBenchCheck {
  id: string;
  version: number;
  kind: VnaBenchCheckKind;
  status: VnaBenchStatus;
  phases: VnaBenchCheckPhase[];
  result_text: string | null;
  refusal: VnaBenchRefusal | null;
  started_at: string;
  finished_at: string | null;
}

export interface VnaBenchSession {
  id: string;
  version: number;
  status: "open" | "closed" | "aborted";
  /** Состояние сеанса на сервере (ready, awaiting_standard, …) — только для отладки, на экран не выводится. */
  state?: string;
  /** Сеанс открыл этот пользователь: управлять им может только он. */
  owned?: boolean;
  /** Остановить или восстановить сеанс может открывший его или администратор (чужой сеанс — с записью в журнал). */
  can_recover?: boolean;
  /** Администратор: закрыть сеанс без восстановления настроек S2VNA, когда восстановление не проходит. */
  can_release_unrestored?: boolean;
  error?: string | null;
  started_at: string;
  operator: string | null;
  steps: VnaBenchStep[];
  command: VnaBenchCommand | null;
  /** Сохранённый экран, который оператор вывел на S2VNA («Показать на ВАЦ»), пока его не сменил следующий замер. */
  view_screen_id?: string | null;
  items: VnaBenchItem[];
  checks: VnaBenchCheck[];
}

export interface VnaBenchState {
  device: VnaBenchDevice;
  calibration: VnaBenchCalibration;
  due: VnaBenchDue[];
  session: VnaBenchSession | null;
}

/** Тело «Открыть сеанс»: без `assembly` — сетка «ОТК — переход» (СИ и проверки); с ним — полоса ЭТК задания. */
export interface VnaBenchSessionInput { assembly?: { task_id: string; operation_id?: string | null } }

export interface VnaBenchCheckInput {
  kind: VnaBenchCheckKind;
  instrument_id?: string;
  label?: string;
  /** Выбор меры, когда в наборе их несколько одного типа: {"THRU": "MA6666B"}. */
  selection?: Record<string, string>;
}

/* «Экраны» (S5b): экраны ВАЦ хранятся в ERP и правятся ОТК. Линия допуска — источник, а не числа: норма СИ,
   норма ЭТК, своё число или без линии; сами отрезки сервер рисует для объекта замера. */
export type VnaScreenPurpose = "adapter" | "assembly" | "one_port" | "load" | "flex" | "engineer";
export type VnaScreenParam = "S11" | "S21" | "S12" | "S22";
export type VnaScreenFormat = "SWR" | "MLOG" | "PHAS" | "GDEL" | "SMIT";
export type VnaScreenLimit = "norm" | "etk" | "fixed" | "none";
export interface VnaScreenScale { pdiv: number; rlev: number | null; rpos: number | null }
export interface VnaScreenTrace {
  param: VnaScreenParam;
  format: VnaScreenFormat;
  /** "auto" — автомасштаб после каждой развёртки; "norm" — КСВН 1,00…max(норма×1,15; 1,20); иначе своя шкала. */
  scale: VnaScreenScale | "auto" | "norm";
  limit: VnaScreenLimit;
  fixed: { kind: "upper" | "lower"; value: number } | null;
  hidden: boolean;
  memory: boolean;
}
export interface VnaScreenFields {
  name: string;
  purpose: VnaScreenPurpose;
  /** Код расположения окон S2VNA (DISPlay:WINDow:SPLit), 1…16. */
  split: number;
  traces: VnaScreenTrace[];
  markers_hz: number[];
  note: string;
  /** Основной экран назначения: его рабочее место ставит само. */
  is_default: boolean;
}
export interface VnaScreen extends VnaScreenFields {
  id: string;
  active: boolean;
  version: number;
  updated_by: string | null;
  updated_at: string;
}

const SCREENS = "/api/production/vna-bench/screens";

export const vnaScreensApi = {
  list: () => api<{ screens: VnaScreen[] }>(SCREENS),
  create: (fields: VnaScreenFields) => api<VnaScreen>(SCREENS, { method: "POST", body: JSON.stringify(fields) }),
  /** «Сохранить»: новая версия; устаревшая `version` — отказ 409 (экран уже сохранили в другом месте). */
  save: (id: string, version: number, fields: VnaScreenFields) =>
    api<VnaScreen>(`${SCREENS}/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ ...fields, version }) }),
  setActive: (id: string, version: number, active: boolean) =>
    api<VnaScreen>(`${SCREENS}/${encodeURIComponent(id)}/${active ? "activate" : "deactivate"}`,
      { method: "POST", body: JSON.stringify({ version }) }),
};

const BASE = "/api/production/vna-bench";
const post = <T>(path: string, body: unknown = {}) => api<T>(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });
const id = encodeURIComponent;

// Protocol 3 has partial diagnostic traces, not Planar's ACM/acceptance DTO.
export interface ArinstProfile { start_hz: number; stop_hz: number; points: number; if_bandwidth_hz: number }
export interface ArinstOptions { scan_range_hz: [number, number]; bandwidths_hz: number[]; max_slice_points: number; raw: string }
export interface ArinstStep {
  op: string; human: boolean; label: string;
  args: { standard?: "OPEN" | "SHORT" | "LOAD" | "THRU"; orientation?: "forward" | "reverse" };
}
export interface ArinstSession {
  id: string; version: number; state: string; step: number; status: string; owned: boolean;
  operator: string | null; error: string | null; steps: ArinstStep[];
  command: { id: string; op: string; status: string } | null;
  artifacts: { id: string; command_id: string; file_name: string; sha256: string }[];
}
export interface ArinstTrace {
  command_id: string; artifact_id: string; sha256: string; profile: ArinstProfile; acceptance_eligible: false;
  acquisition: {
    schema: "arinst.partial.v1"; points: number; orientation: "forward" | "reverse";
    measured_channels: ("S11" | "S21")[]; data: Partial<Record<"S11" | "S21", [number, number][]>>;
    grid_confirmed: boolean; frequencies_hz: number[] | null; correction_origin: string;
  };
}
export interface ArinstBenchState {
  source: "arinst";
  device: {
    instrument_id: string; inventory_number: string; serial: string; printed_serial: string | null;
    hardware_uid: string | null; firmware: string | null; machine_id: string | null;
    connection_ready: boolean; control_ready: boolean; reason: string | null; supported_ops: string[];
    license: { state: "not_required" | "UNKNOWN"; message: string };
    options?: ArinstOptions | null;
  };
  profile: ArinstProfile | null;
  calibration: { valid: boolean; status: string; reason?: string | null };
  acceptance: { eligible: boolean; code: string; message: string };
  session: ArinstSession | null;
  latest_trace: ArinstTrace | null;
}

const arinstPost = (path: string, body: unknown) => api<ArinstBenchState>(`${BASE}${path}`,
  { method: "POST", body: JSON.stringify(body), timeoutMs: 20_000 });

export const arinstBenchApi = {
  state: (instrumentId: string, signal?: AbortSignal) =>
    api<ArinstBenchState>(`${BASE}/state?instrument_id=${id(instrumentId)}`, { signal, timeoutMs: 15_000 }),
  start: (instrumentId: string) => arinstPost("/sessions", { instrument_id: instrumentId }),
  connect: (sessionId: string, version: number) =>
    arinstPost(`/sessions/${id(sessionId)}/arinst/connect`, { version }),
  profile: (sessionId: string, version: number, profile: ArinstProfile) =>
    arinstPost(`/sessions/${id(sessionId)}/arinst/profile`, { version, profile }),
  calibration: (sessionId: string, version: number, kitId: string, referencePlane: string, transmission: boolean) =>
    arinstPost(`/sessions/${id(sessionId)}/arinst/calibration`,
      { version, kit_id: kitId, reference_plane: referencePlane, transmission, orientation: "forward" }),
  capture: (sessionId: string, version: number, orientation: "forward" | "reverse") =>
    arinstPost(`/sessions/${id(sessionId)}/arinst/capture`, { version, orientation }),
  confirm: (sessionId: string, version: number) =>
    arinstPost(`/sessions/${id(sessionId)}/arinst/confirm`, { version }),
  close: (sessionId: string, version: number) => arinstPost(`/sessions/${id(sessionId)}/close`, { version }),
  abort: (sessionId: string, version: number) => arinstPost(`/sessions/${id(sessionId)}/abort`, { version }),
  artifactUrl: (sessionId: string, artifactId: string) =>
    `/api/production/vna-sessions/${id(sessionId)}/artifacts/${id(artifactId)}`,
};

export const vnaBenchApi = {
  /** Состояние рабочего места: прибор, калибровка, сроки проверок, открытый сеанс. */
  state: () => api<VnaBenchState>(`${BASE}/state`),
  /** Открыть сеанс рабочего места (purpose='bench'). */
  startSession: (input: VnaBenchSessionInput = {}) => post<VnaBenchState>("/sessions", input),
  /** Подтвердить шаг после подключения: калибровка АКМ (и повтор), нагрузка на порт 1 / 2. Доверительный тест идёт сам. */
  runStep: (sessionId: string, version: number, step: VnaBenchStepKey) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/steps/${step}`, { version }),
  /** Найти объект по скану или номеру: СИ («СИ.035»), задание и позиция («ПЗ-0001/3»), серийный номер изделия. */
  resolve: (query: string) => post<{ candidates: VnaBenchTarget[] }>("/resolve", { query }),
  targets: () => api<{ targets: VnaBenchInstrumentTarget[] }>(`${BASE}/targets`),
  /** План замера объекта в этом сеансе: экран, полоса, точки, ПЧ, мощность, графики, шкалы, норма. */
  plan: (sessionId: string, target: VnaBenchTarget) => post<VnaBenchPlan>(`/sessions/${id(sessionId)}/plan`, { target }),
  /** Начать замер объекта (подключение 1). */
  measure: (sessionId: string, version: number, target: VnaBenchTarget) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/items`, { version, target }),
  /** Следующее подключение «на границе» (поворот на 120°). */
  nextConnection: (sessionId: string, itemId: string, version: number) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/items/${id(itemId)}/connections`, { version }),
  /** Записать окончательный результат в карточку СИ / в замеры КСВН изделия. */
  record: (sessionId: string, itemId: string, version: number) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/items/${id(itemId)}/record`, { version }),
  /** Начать проверку: шевеление кабелей, еженедельная (СИ.009), ежемесячная (6650F27). */
  startCheck: (sessionId: string, version: number, input: VnaBenchCheckInput) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/checks`, { version, ...input }),
  /** Выполнить текущую фазу проверки (после того, как оператор подключил то, что сказано). */
  checkNext: (sessionId: string, checkId: string, version: number) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/checks/${id(checkId)}/next`, { version }),
  /** Прервать текущую команду и вернуть прибор оператору. */
  abort: (sessionId: string, version: number) => post<VnaBenchState>(`/sessions/${id(sessionId)}/abort`, { version }),
  /** Администратор: освободить ВАЦ без загрузки резервной копии оператора (восстановление не проходит). */
  releaseUnrestored: (sessionId: string, version: number) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/release-unrestored`, { version }),
  /** «Показать на ВАЦ»: сохранённый экран — на S2VNA в откалиброванном сеансе, без развёртки; калибровка остаётся. */
  showScreen: (sessionId: string, version: number, screenId: string) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/screen`, { version, screen_id: screenId }),
  /** Закрыть сеанс: экран ERP и калибровка остаются на приборе для просмотра. */
  close: (sessionId: string, version: number) => post<VnaBenchState>(`/sessions/${id(sessionId)}/close`, { version }),
};

/** Выключатель экрана: вкладка видна, только если сервер отвечает на `state` (до S3/S4 на main — 404, вкладки нет). */
export async function vnaBenchAvailable(): Promise<boolean> {
  try {
    const state = await vnaBenchApi.state();
    return Boolean(state && typeof state === "object" && "device" in state && "calibration" in state);
  } catch {
    return false;
  }
}

/** Отказы агента и сервера — понятной русской причиной с тем, что делать. Неизвестный код — текст сервера. */
const REFUSALS: Record<string, string> = {
  agent_offline: "Агент на закреплённом ПК не отвечает — проверьте, что ПК включён и запущен агент ERP.",
  vna_not_connected: "ВАЦ не подключён — включите ВАЦ, подключите USB и запустите S2VNA.",
  agent_capability_missing: "Агент не подтвердил метод калибровки — проверьте подключение АКМ и настройки управления ВАЦ.",
  agent_old: "Агент на ПК ОТК старый и не умеет рабочее место ВАЦ — он обновится сам в течение 5 минут.",
  device_busy: "ВАЦ занят другой командой — дождитесь её окончания или нажмите «Прервать» в строке «Выполняется».",
  recovery_required: "Ответ ВАЦ потерян — нажмите «Восстановить» над вкладками: прибор остановится и вернёт настройки оператора.",
  serial_changed: "Заводской номер ВАЦ не совпал с карточкой СИ.002 — подключён другой прибор. Замеры запрещены.",
  not_ready: "S2VNA не в состоянии «Готов» — дождитесь прогрева (не меньше 40 минут).",
  screen_changed: "Экран S2VNA изменили вручную — замер остановлен. S2VNA только для просмотра; экран ERP вернётся при следующем замере.",
  correction_lost: "Коррекция не «Кор» (К? или К!) — калибровка не действует для этой сетки. Откалибруйте заново.",
  smoothing_on: "На приборе включено сглаживание или усреднение — для замера оно запрещено. Выключите и повторите.",
  profile_changed: "Профиль стимула изменён после калибровки — калибровка недействительна. Откалибруйте заново.",
  temperature_drift: "Температура ВАЦ ушла от калибровочной больше чем на 1 °C — откалибруйте заново.",
  temperature_unknown: "Прибор не сообщил температуру — без неё замер не принимается. Проверьте связь с ВАЦ.",
  calibration_expired: "Калибровка истекла (4 часа или конец смены) — откалибруйте заново.",
  acm_missing: "АКМ не подключён по USB — подключите модуль и дождитесь зелёного индикатора.",
  points_over_limit: "Профиль больше 4001 точки — доверительный тест по нему невозможен. Нужен другой профиль.",
  no_norm: "Нормы КСВН для этого объекта нет — вердикт ставит контролёр с указанием причины.",
  no_band: "В ЭТК нет полосы для замера КСВН — технолог должен задать её в операции «Контроль КСВН».",
  other_calibration: "Этому объекту нужна другая калибровка (другая полоса или число точек) — откройте новый сеанс.",
  stale: "Состояние уже изменилось (другая вкладка или контролёр) — экран обновлён, повторите действие.",
  calibration_required: "Калибровка не подтверждена (доверительный тест или нагрузка не пройдены) — проверьте соединения и откалибруйте заново.",
  control_failed: "Еженедельная или ежемесячная проверка не пройдена — замеры СИ и КБ остановлены до выяснения причины.",
  not_final: "Решение ещё не окончательное — выполните все подключения, затем записывайте.",
  stopped: "Прервано — начат следующий объект или проверка.",
  station_setup: "Рабочее место не собрано в реестре СИ: у карточки ВАЦ нужны АКМ и два тестовых кабеля.",
  no_fixture_file: "У оснастки нет своего файла .s2p — замер с ней не выполняют (СТО п. 12.2).",
  no_reference: "У контрольной меры нет эталонной кривой — отметьте файл в её карточке СИ.",
  kit_choice: "В наборе несколько мер одного типа — укажите, какую подключаете.",
  conflict: "Действие не выполнено: сеанс в другом состоянии. Экран обновлён.",
  invalid: "Данные не приняты сервером.",
  not_found: "Не найдено — возможно, сеанс уже закрыт.",
  refused: "Команда не выполнена.",
};
/** Коды, у которых текст сервера точнее общего: в нём номер СИ, список мер, имя поля. */
const DETAILED = new Set(["vna_not_connected", "station_setup", "no_fixture_file", "no_reference", "kit_choice", "conflict", "invalid", "not_found", "refused"]);

/** Ни кода в словаре, ни текста сервера — всё равно понятная строка, никогда не пустой баннер. */
export const REFUSAL_FALLBACK = "Команда ВАЦ не выполнена";
/** Отказы, после которых калибровка недействительна и нужна новая (СТО 11.4, 11.5); прочие — повтор того же шага. */
export const CALIBRATION_REFUSALS = new Set(["calibration_required", "calibration_expired", "correction_lost", "profile_changed",
  "temperature_drift", "temperature_unknown", "acm_missing"]);

export function refusalText(refusal: VnaBenchRefusal | null | undefined): string {
  if (!refusal) return "";
  if (DETAILED.has(refusal.code) && refusal.message) return refusal.message;
  return REFUSALS[refusal.code] || refusal.message || REFUSAL_FALLBACK;
}
