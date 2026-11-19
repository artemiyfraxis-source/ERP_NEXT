import { api, type VswrTracePoint } from "./api";

/* Клиент «ВАЦ — рабочее место» (план «ВАЦ только через ERP», слайс S5). Все формы, которые ждёт экран, — в этом
   одном файле: сервер (S3/S4, `workflows/vna_bench_api.py`) пишется параллельно, и при выравнивании правится только
   он. Пути — под /api/production/vna-bench/*. Команды прибору асинхронны: POST ставит команду агенту и сразу
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
  agent_online: boolean;
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

export type VnaBenchTarget =
  | { kind: "instrument"; instrument_id: string; number: string; name: string; serial_number: string | null }
  | { kind: "assembly"; task_id: string; task_number: string; position: number; serial: number | null; name: string; item: string };

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
}

export interface VnaBenchCheckPhase {
  /** flex: load_memory/load_flex/thru_memory/thru_flex; weekly: si009; monthly: open_p1…thru_c. */
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
  started_at: string;
  operator: string | null;
  steps: VnaBenchStep[];
  command: VnaBenchCommand | null;
  items: VnaBenchItem[];
  checks: VnaBenchCheck[];
}

export interface VnaBenchState {
  device: VnaBenchDevice;
  calibration: VnaBenchCalibration;
  due: VnaBenchDue[];
  session: VnaBenchSession | null;
}

const BASE = "/api/production/vna-bench";
const post = <T>(path: string, body: unknown = {}) => api<T>(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });
const id = encodeURIComponent;

export const vnaBenchApi = {
  /** Состояние рабочего места: прибор, калибровка, сроки проверок, открытый сеанс. */
  state: () => api<VnaBenchState>(`${BASE}/state`),
  /** Открыть сеанс рабочего места (purpose='bench'). */
  startSession: () => post<VnaBenchState>("/sessions"),
  /** Выполнить шаг сеанса: калибровка АКМ, доверительный тест, нагрузка на порт 1 / 2. */
  runStep: (sessionId: string, version: number, step: VnaBenchStepKey) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/steps/${step}`, { version }),
  /** Найти объект по скану или номеру: СИ («СИ.035»), задание и позиция («ПЗ-0001/3»), серийный номер изделия. */
  resolve: (query: string) => post<{ candidates: VnaBenchTarget[] }>("/resolve", { query }),
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
  startCheck: (sessionId: string, version: number, kind: VnaBenchCheckKind) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/checks`, { version, kind }),
  /** Выполнить текущую фазу проверки (после того, как оператор подключил то, что сказано). */
  checkNext: (sessionId: string, checkId: string, version: number) =>
    post<VnaBenchState>(`/sessions/${id(sessionId)}/checks/${id(checkId)}/next`, { version }),
  /** Прервать текущую команду и вернуть прибор оператору. */
  abort: (sessionId: string, version: number) => post<VnaBenchState>(`/sessions/${id(sessionId)}/abort`, { version }),
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
  agent_offline: "Агент на ПК ОТК не отвечает — проверьте, что ПК включён и программа печати запущена.",
  agent_old: "Агент на ПК ОТК старый и не умеет рабочее место ВАЦ — он обновится сам в течение 5 минут.",
  device_busy: "ВАЦ занят другой командой — дождитесь её окончания или нажмите «Прервать».",
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
};

export function refusalText(refusal: VnaBenchRefusal | null | undefined): string {
  if (!refusal) return "";
  return REFUSALS[refusal.code] ?? refusal.message;
}
