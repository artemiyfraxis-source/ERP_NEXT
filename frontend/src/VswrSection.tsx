import { useEffect, useRef, useState } from "react";
import {
  productionApi,
  type ProductionInstrument,
  type VswrMeasurement,
  type VswrControl,
  type VswrNorm,
  type VswrSerialRow,
  type VswrTaskListItem,
  type VswrTaskOverview,
  type VnaCaptureSettings,
} from "./api";
import { useCanPrint } from "./rights";
import { printCoaxPassport } from "./coaxPassportPrint";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate, formatNumber } from "./format";
import { PageWindow } from "./PageWindow";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { VswrChart } from "./VswrChart";
import { VnaCapture, VnaSettings, captureRefusals } from "./vnaCapture";

/* «Замеры КСВН» в «ОТК» (редизайн 19.11, владелец: «очень слабо и неудобно»): сверху задания коаксиальных сборок —
   проект, КБ, ревизия ЭТК, норма и полоса, «померено N из M · брак K»; под выбранным заданием — его изделия с вердиктом
   цветом (годен / на границе / брак). «Снять с ВАЦ» в строке изделия сразу открывает съём трассы; режим «Поток» — скан
   серийного номера → замер → следующий. Норму сервер берёт из ЭТК задания; паспорт годного изделия открывается сразу
   после замера; «В брак ОТК» ведёт в окно ОТК коробки с готовой причиной. */

const FILE_ACCEPT = ".s1p,.csv,.txt";
const num = (value: number | null | undefined, digits = 2) => (value === null || value === undefined ? "" : formatNumber(value, { digits }));

/** Справочная норма по комплектующим при норме заказчика: «по комплектующим ≤ 2,06 — справочно». */
export function componentsText(norm: VswrNorm): string {
  const components = norm.components;
  if (!components) return "";
  return components.status === "ok" && components.vswr_max !== null
    ? `по комплектующим ≤ ${num(components.vswr_max)} — справочно`
    : `по комплектующим не определена${components.reason ? ` (${components.reason})` : ""} — справочно`;
}

export function normLine(norm: VswrNorm): string {
  if (norm.status === "ok" && norm.source === "customer") {
    // Норма заказчика (технолог, ЭТК): вердикт — по её полосе; основание — ТУ / КД / заказ (и ревизия ЭТК, если новее).
    const band = norm.band_mhz ? ` в полосе ${num(norm.band_mhz[0], 1)} … ${num(norm.band_mhz[1], 1)} МГц` : "";
    const reference = componentsText(norm);
    return `Норма заказчика ≤ ${num(norm.vswr_max)}${band}${norm.basis ? ` (${norm.basis})` : ""}${reference ? `; ${reference}` : ""}`;
  }
  if (norm.status === "ok") {
    const band = norm.band_mhz && norm.band_mhz[0] !== null && norm.band_mhz[1] !== null ? `, полоса ${num(norm.band_mhz[0], 1)} … ${num(norm.band_mhz[1], 1)} МГц` : "";
    // source = "computed": норма рассчитана на лету для ЭТК до нормы — вместо служебного слова показываем основание.
    const computed = norm.source === "computed";
    const source = computed ? (norm.basis ? ` (${norm.basis})` : "") : norm.source ? `: ${norm.source}` : "";
    return `Норма ≤ ${num(norm.vswr_max)} по комплектующим${source}${band}`;
  }
  return `Норма не определена — ${norm.reason || "причина не указана"}`;
}

/** Режим контроля для строки нормы: «контроль: каждое изделие» / «выборочно 1 из 50» / «контроль не проводится». */
export function controlText(control: VswrControl | undefined): string {
  if (control?.mode === "sample") return `выборочно 1 из ${control.every}`;
  if (control?.mode === "none") return "контроль не проводится";
  return "контроль: каждое изделие";
}

/** Полоса нормы одной строкой: «1 000 … 3 000»; не задана — пусто. */
export function bandText(band: [number | null, number | null] | null | undefined): string {
  if (!band || band[0] == null || band[1] == null) return "";
  return `${num(band[0], 1)} … ${num(band[1], 1)}`;
}

/** «Померено 2 из 8 · брак 1»; контроль не проводится — так и сказано. */
export function progressText(control: VswrControl, required: { total: number; measured: number; failed?: number }): string {
  if (control.mode === "none") return "контроль не проводится";
  return `померено ${required.measured} из ${required.total}${required.failed ? ` · брак ${required.failed}` : ""}`;
}

/* «На границе» (владелец 19.11: «годен / брак / на границе») — решение сервера по правилу СТО ФРАК-002-2026 п. 13.3, как
   у паспорта СИ: наибольший КСВН в пределах ±U ВАЦ от нормы — на границе допуска, повторить замер в 3 подключениях.
   Экран границу не вычисляет; нет решения сервера — вердикт как записан. */
export type VswrTone = "pass" | "edge" | "fail" | "none";
export const VSWR_TONES: Record<VswrTone, { label: string; tone: Tone; hint: string }> = {
  pass: { label: "Годен", tone: "success", hint: "в норме" },
  edge: { label: "На границе", tone: "warning", hint: "в пределах погрешности ВАЦ от нормы — повторить 3 подключения" },
  fail: { label: "Брак", tone: "danger", hint: "не соответствует норме" },
  none: { label: "Не измерено", tone: "neutral", hint: "замера нет" },
};

export function vswrTone(measurement: Pick<VswrMeasurement, "verdict" | "decision"> | null): VswrTone {
  if (!measurement) return "none";
  if (measurement.decision?.status === "border") return "edge";
  return measurement.verdict === "fail" ? "fail" : "pass";
}

const verdictChip = (measurement: VswrMeasurement | null) => {
  const tone = VSWR_TONES[vswrTone(measurement)];
  return <StatusChip status={tone.label} tone={tone.tone} title={measurement?.decision?.text || measurement?.verdict_reason || tone.hint} />;
};

const LOOKALIKE: Record<string, string> = { A: "А", B: "В", C: "С", E: "Е", H: "Н", K: "К", M: "М", O: "О", P: "Р", T: "Т", X: "Х", Y: "У" };
const scanKey = (text: string) => text.trim().toUpperCase().replace(/[A-Z]/g, (char) => LOOKALIKE[char] ?? char).replace(/\s+/g, "");

/** Скан серийного номера → задание и позиция. Понимает номер ERP «ПЗ-0003-0004», заводской «FRX.<проект>.<PN>.<n>»
    (n — позиция в задании этого проекта; выбранное задание первым) и просто номер позиции в выбранном задании. */
export function resolveScan(text: string, tasks: VswrTaskListItem[], selected: string): { taskId: string; position: number } | null {
  const key = scanKey(text);
  if (!key) return null;
  const own = /^(.+)-(\d{1,6})$/.exec(key);
  if (own) {
    const task = tasks.find((item) => scanKey(item.number) === own[1]);
    const position = Number(own[2]);
    if (task && position >= 1 && position <= task.quantity) return { taskId: task.id, position };
  }
  // «X» латиницей в ключе уже кириллическая «Х» (двойники выше).
  const factory = /^FR[XХ]\.(.+)\.(\d{1,6})$/.exec(key);
  if (factory) {
    const position = Number(factory[2]);
    const fits = (task: VswrTaskListItem) => Boolean(task.project) && factory[1].startsWith(`${scanKey(task.project ?? "")}.`)
      && position >= 1 && position <= task.quantity;
    const current = tasks.find((task) => task.id === selected && fits(task));
    const candidates = tasks.filter(fits);
    const task = current ?? (candidates.length === 1 ? candidates[0] : undefined);
    if (task) return { taskId: task.id, position };
  }
  if (/^\d{1,6}$/.test(key)) {
    const task = tasks.find((item) => item.id === selected);
    const position = Number(key);
    if (task && position >= 1 && position <= task.quantity) return { taskId: task.id, position };
  }
  return null;
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Файл замера не прочитан"));
    reader.onload = () => resolve(String(reader.result ?? "").split(",")[1] ?? "");
    reader.readAsDataURL(file);
  });
}

export interface VswrRejectRequest { taskId: string; serialNumber: string; description: string }

const rejectDescription = (serial: string, measurement: VswrMeasurement) =>
  `Изделие ${serial}: макс. КСВН ${num(measurement.max_vswr)} на ${num(measurement.max_vswr_mhz, 1)} МГц при норме ${num(measurement.norm_vswr_max) || "не определена"}`;

/** Нужен ли изделию замер, которого ещё нет: в выборке (или контроль каждого) и не мерено. */
const awaiting = (row: VswrSerialRow, control: VswrControl) => control.mode !== "none" && row.in_sample && !row.latest;

export function VswrSection({ instruments, choices, taskId, onTaskChange, onOverview, canUpload, canReject, onReject }: {
  instruments: ProductionInstrument[];
  choices: VswrTaskListItem[];
  taskId: string;
  onTaskChange: (id: string) => void;
  onOverview?: (overview: VswrTaskOverview) => void;
  canUpload: boolean;
  /** Есть ли у задания коробка в очереди ОТК — только тогда «Не годно» ведёт в брак. */
  canReject: (taskId: string) => boolean;
  onReject: (request: VswrRejectRequest) => void;
}) {
  const canPrint = useCanPrint();
  const [overview, setOverview] = useState<VswrTaskOverview | null>(null);
  const request = useRef(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [measuring, setMeasuring] = useState<{ row: VswrSerialRow; capture: boolean } | null>(null);
  const [flow, setFlow] = useState(false);
  const [scan, setScan] = useState("");
  const [scanError, setScanError] = useState("");
  const [pendingScan, setPendingScan] = useState<{ taskId: string; position: number } | null>(null);
  const scanInput = useRef<HTMLInputElement>(null);

  async function load(id: string) {
    const stamp = ++request.current;
    if (!id) { setOverview(null); setLoading(false); return; }
    setLoading(true);
    setError("");
    try {
      const result = await productionApi.vswrTask(id);
      if (stamp !== request.current) return;
      setOverview(result);
      onOverview?.(result);
    } catch (reason) {
      if (stamp !== request.current) return;
      setOverview(null);
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить замеры КСВН");
    } finally {
      if (stamp === request.current) setLoading(false);
    }
  }

  useEffect(() => {
    setOverview(null); setMeasuring(null);
    void load(taskId);
    return () => { request.current += 1; };
  }, [taskId]);

  // Скан из другого задания: сначала грузим его изделия, затем открываем замер нужного.
  useEffect(() => {
    if (!pendingScan || overview?.task.id !== pendingScan.taskId) return;
    const row = overview.serials.find((item) => item.position === pendingScan.position);
    setPendingScan(null);
    if (row) setMeasuring({ row, capture: true });
    else setScanError(`Изделия № ${pendingScan.position} в задании ${overview.task.number} ещё нет в реестре серийных номеров.`);
  }, [overview, pendingScan]);

  useEffect(() => { if (flow && !measuring) scanInput.current?.focus(); }, [flow, measuring]);

  async function openPassport(row: VswrSerialRow, id = taskId) {
    setError("");
    try {
      printCoaxPassport(await productionApi.vswrPassport(id, row.position));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Паспорт не получен");
    }
  }

  function submitScan() {
    const found = resolveScan(scan, choices, taskId);
    setScan("");
    if (!found) { setScanError(`«${scan.trim()}» — не серийный номер заданий на замер КСВН.`); return; }
    setScanError("");
    if (found.taskId !== taskId) { setPendingScan(found); onTaskChange(found.taskId); return; }
    const row = overview?.serials.find((item) => item.position === found.position);
    if (row) setMeasuring({ row, capture: true });
    else setScanError(`Изделия № ${found.position} ещё нет в реестре серийных номеров задания.`);
  }

  const selected = choices.find((item) => item.id === taskId);
  const control = overview?.control ?? selected?.control ?? { mode: "all", every: null };
  const sample = control.mode === "sample";
  const measuredAny = Boolean(overview?.serials.some((row) => row.latest));
  const failed = overview?.serials.filter((row) => row.latest?.verdict === "fail").length ?? 0;
  const reasons = Boolean(overview?.serials.some((row) => row.latest?.verdict === "fail" && row.latest.verdict_reason));
  const anyMax = choices.some((item) => item.required.max_vswr != null);
  const anyFailed = choices.some((item) => item.required.failed);

  const taskColumns: DataColumn<VswrTaskListItem>[] = [
    { key: "project", title: "Проект", value: (row) => row.project ?? "", filter: true },
    { key: "kb", title: "КБ", value: (row) => row.item },
    { key: "number", title: "Задание", value: (row) => row.number },
    { key: "name", title: "Изделие", value: (row) => row.name, grow: true, wrap: true },
    { key: "etk", title: "ЭТК", value: (row) => (row.etk_revision != null ? `ред. ${row.etk_revision}` : "") },
    { key: "norm", title: "Норма КСВН", value: (row) => row.norm.vswr_max ?? "", numeric: true,
      render: (row) => (row.norm.status === "ok" && row.norm.vswr_max != null ? `≤ ${num(row.norm.vswr_max)}`
        : <span className="vswr-norm-missing" title="Вердикт ставит контролёр">не определена</span>) },
    { key: "band", title: "Полоса, МГц", value: (row) => bandText(row.norm.band_mhz) },
    { key: "control", title: "Контроль", value: (row) => (row.control.mode === "sample" ? `1 из ${row.control.every}` : row.control.mode === "none" ? "не проводится" : "каждое"), filter: true },
    { key: "measured", title: "Померено", value: (row) => (row.control.mode === "none" ? "" : `${row.required.measured} из ${row.required.total}`), numeric: true,
      render: (row) => (row.control.mode === "none" ? <span className="muted">не требуется</span>
        : <span className={row.required.measured >= row.required.total ? "tone-ok" : undefined}>{row.required.measured} из {row.required.total}</span>) },
    ...(anyFailed ? [{ key: "failed", title: "Брак", numeric: true, value: (row: VswrTaskListItem) => row.required.failed ?? 0,
      render: (row: VswrTaskListItem) => (row.required.failed ? <span className="tone-danger">{row.required.failed}</span> : <span className="muted">0</span>) }] : []),
    ...(anyMax ? [{ key: "max", title: "Макс. КСВН", numeric: true, value: (row: VswrTaskListItem) => row.required.max_vswr ?? "",
      render: (row: VswrTaskListItem) => num(row.required.max_vswr) || <span className="muted">—</span> }] : []),
  ];

  const columns: DataColumn<VswrSerialRow>[] = [
    { key: "position", title: "№", value: (row) => row.position, numeric: true, width: 48 },
    { key: "serial", title: "Серийный номер", value: (row) => row.serial_number },
    ...(sample ? [{ key: "sample", title: "Выборка", filter: true, value: (row: VswrSerialRow) => (row.in_sample ? "в выборке" : "вне выборки"),
      render: (row: VswrSerialRow) => (row.in_sample ? <StatusChip status="в выборке" tone="info" /> : <span className="muted">вне выборки</span>) }] : []),
    { key: "verdict", title: "Вердикт", value: (row) => VSWR_TONES[vswrTone(row.latest)].label, filter: true, render: (row) => verdictChip(row.latest) },
    // Почему брак — как записал сервер («Не измерен конец Б», «Конец А: КСВН выше нормы»); колонка — только когда есть брак с причиной.
    ...(reasons ? [{ key: "reason", title: "Причина", wrap: true, value: (row: VswrSerialRow) => (row.latest?.verdict === "fail" ? row.latest.verdict_reason : ""),
      render: (row: VswrSerialRow) => (row.latest?.verdict === "fail" && row.latest.verdict_reason ? row.latest.verdict_reason : <span className="muted">—</span>) }] : []),
    // Колонки замера — только когда в задании есть хоть один замер: до замеров не рисуем столбцы из одних прочерков.
    ...(measuredAny ? [
      { key: "max", title: "Макс. КСВН", value: (row: VswrSerialRow) => row.latest?.max_vswr ?? "", numeric: true,
        render: (row: VswrSerialRow) => num(row.latest?.max_vswr) || <span className="muted">—</span> },
      { key: "mhz", title: "На частоте, МГц", value: (row: VswrSerialRow) => row.latest?.max_vswr_mhz ?? "", numeric: true,
        render: (row: VswrSerialRow) => num(row.latest?.max_vswr_mhz, 1) || <span className="muted">—</span> },
      { key: "attempts", title: "Попыток", value: (row: VswrSerialRow) => row.attempts, numeric: true,
        render: (row: VswrSerialRow) => row.attempts || <span className="muted">—</span> },
      { key: "inspector", title: "Контролёр", value: (row: VswrSerialRow) => row.latest?.inspector.name ?? "",
        render: (row: VswrSerialRow) => row.latest?.inspector.name || <span className="muted">—</span> },
      { key: "date", title: "Дата", value: (row: VswrSerialRow) => row.latest?.measured_at ?? "",
        render: (row: VswrSerialRow) => (row.latest ? formatDate(row.latest.measured_at) : <span className="muted">—</span>) },
    ] : []),
    {
      key: "actions", title: "Действия", value: () => "", render: (row) => <span className="vswr-actions">
        {canUpload && <button type="button" className="secondary small"
          aria-label={`Снять с ВАЦ ${row.serial_number}`} onClick={() => setMeasuring({ row, capture: true })}>Снять с ВАЦ</button>}
        {canUpload && <button type="button" className="link-button" aria-label={`Замер из файла ${row.serial_number}`}
          onClick={() => setMeasuring({ row, capture: false })}>Из файла…</button>}
        {canPrint && (row.latest || (sample && !row.in_sample)) && <button type="button" className="link-button" onClick={() => void openPassport(row)}>Паспорт</button>}
        {row.latest && <a className="link-button" href={productionApi.vswrFileUrl(row.latest.id)} download>Файл</a>}
        {row.latest?.verdict === "fail" && canReject(taskId) && <button type="button" className="link-button danger-text" onClick={() => onReject({
          taskId, serialNumber: row.serial_number, description: rejectDescription(row.serial_number, row.latest!),
        })}>В брак ОТК</button>}
      </span>,
    },
  ];

  const next = overview?.serials.find((row) => awaiting(row, control) && row.position !== measuring?.row.position);

  return <div className="panel vswr-section">
    <div className="manual-stock-head"><h2>Замеры КСВН</h2>
      {canUpload && <button type="button" className={`secondary small vswr-flow-toggle${flow ? " on" : ""}`} aria-pressed={flow}
        onClick={() => { setFlow((value) => !value); setScanError(""); }}>Поток: скан → замер → следующий</button>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <DataTable label="Задания коаксиальных сборок" storageKey="quality-vswr-tasks" rows={choices} columns={taskColumns} rowKey={(row) => row.id}
      presets={[{ label: "Не померены", test: (row) => row.control.mode !== "none" && row.required.measured < row.required.total },
        { label: "С браком", test: (row) => Boolean(row.required.failed) }]}
      searchPlaceholder="Проект, КБ, задание или изделие…"
      onRowClick={(row) => onTaskChange(row.id)} rowTitle={() => "Показать изделия задания"}
      rowClassName={(row) => (row.id === taskId ? "selected" : "")}
      emptyText="Нет заданий на коаксиальные сборки — они появятся, когда мастер запустит задание по ЭТК коаксиальной сборки." />
    {!taskId && choices.length > 0 && <div className="empty">Выберите задание в таблице — ниже появятся его изделия и замеры.</div>}
    {taskId && loading && !overview && <div className="empty">Загрузка изделий…</div>}
    {overview && <section className="vswr-group" aria-label={`Изделия задания ${overview.task.number}`}>
      <div className="vswr-group-head">
        <h3>{[overview.task.project ?? selected?.project, overview.task.item ?? selected?.item, overview.task.number].filter(Boolean).join(" · ")}</h3>
        <span className="vswr-group-name">{overview.task.name}</span>
        {overview.etk && <span className="muted">ЭТК {overview.etk.item_index || "задания"}{overview.etk.revision != null ? `, ред. ${overview.etk.revision}` : ""}</span>}
        <span className={`vswr-count${failed ? " has-reject" : ""}`} role="status">{progressText(control, { ...overview.required, failed })}</span>
      </div>
      <p className={`vswr-norm${overview.norm.status === "ok" ? "" : " is-undefined"}`}><span>{normLine(overview.norm)}</span> <span className="vswr-control">· {controlText(overview.control)}</span></p>
      <p className="vswr-legend" aria-label="Цвета вердикта">{(["pass", "edge", "fail", "none"] as VswrTone[]).map((key) =>
        <span key={key}><StatusChip status={VSWR_TONES[key].label} tone={VSWR_TONES[key].tone} /> {VSWR_TONES[key].hint}</span>)}</p>
      {flow && canUpload && <form className="vswr-flow" onSubmit={(event) => { event.preventDefault(); submitScan(); }}>
        <label>Серийный номер<input ref={scanInput} name="serial-scan" autoComplete="off" value={scan} placeholder="Сканируйте наклейку изделия…"
          onChange={(event) => setScan(event.target.value)} /></label>
        <button type="submit" className="primary small" disabled={!scan.trim()}>Замерить</button>
        {next && <button type="button" className="secondary small" onClick={() => setMeasuring({ row: next, capture: true })}>Следующий без скана: {next.serial_number}</button>}
        {scanError && <small className="danger-text" role="alert">{scanError}</small>}
      </form>}
      <DataTable label="Изделия задания" storageKey="quality-vswr" rows={overview.serials} columns={columns} rowKey={(row) => row.serial_id}
        presets={[{ label: "Не померены", test: (row) => awaiting(row, control) }, { label: "Брак", test: (row) => row.latest?.verdict === "fail" },
          { label: "На границе", test: (row) => vswrTone(row.latest) === "edge" }]}
        rowClassName={(row) => `vswr-row-${vswrTone(row.latest)}`}
        exportName={`Замеры КСВН ${overview.task.number}`} emptyText="У задания нет изделий с серийными номерами." />
    </section>}
    {measuring && overview && <VswrUploadWindow key={`${overview.task.id}:${measuring.row.position}`}
      task={overview.task} norm={overview.norm} row={measuring.row} instruments={instruments} capture={measuring.capture} flow={flow}
      close={() => setMeasuring(null)} onSaved={() => load(taskId)}
      onPassport={canPrint ? () => openPassport(measuring.row, overview.task.id) : undefined}
      next={flow ? () => setMeasuring(null) : next ? () => setMeasuring({ row: next, capture: true }) : undefined}
      nextLabel={flow ? "Сканировать следующий" : next ? `Следующий: ${next.serial_number}` : ""}
      canReject={canReject(taskId)} onReject={(rejected) => { setMeasuring(null); onReject(rejected); }} />}
  </div>;
}

const parseMhz = (text: string) => (text.trim() === "" ? null : Number(text.replace(",", ".")));

/** Ошибки заполнения формы замера; пустой список — форму можно отправлять. */
export function uploadProblems(input: { instrumentId: string; file: File | null; bandFrom: string; bandTo: string; norm: VswrNorm; verdict: string; reason: string }): string[] {
  const problems: string[] = [];
  if (!input.instrumentId) problems.push("Выберите прибор");
  if (!input.file) problems.push("Приложите файл замера");
  const from = parseMhz(input.bandFrom);
  const to = parseMhz(input.bandTo);
  if ((from !== null && !Number.isFinite(from)) || (to !== null && !Number.isFinite(to))) problems.push("Полоса — числа в МГц");
  else if (from !== null && to !== null && from >= to) problems.push("Полоса «от» должна быть меньше «до»");
  if (input.norm.status !== "ok") {
    if (!input.verdict) problems.push("Норма не определена — выберите вердикт");
    if (!input.reason.trim()) problems.push("Норма не определена — укажите причину вердикта");
  }
  return problems;
}

function VswrUploadWindow({ task, norm, row, instruments, capture, flow, close, onSaved, onPassport, next, nextLabel, canReject, onReject }: {
  task: VswrTaskOverview["task"];
  norm: VswrNorm;
  row: VswrSerialRow;
  instruments: ProductionInstrument[];
  /** Окно открыто кнопкой «Снять с ВАЦ»: съём трассы открывается сразу. */
  capture: boolean;
  /** Поток: трасса снимается сразу и замер загружается без лишнего щелчка, если ВАЦ и форма в порядке. */
  flow: boolean;
  close: () => void;
  onSaved: () => Promise<void>;
  onPassport?: () => Promise<void>;
  next?: () => void;
  nextLabel: string;
  canReject: boolean;
  onReject: (request: VswrRejectRequest) => void;
}) {
  const active = instruments.filter((item) => item.kind === "Средство измерений" && item.status === "Действует");
  const [instrumentId, setInstrumentId] = useState(active[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [capturing, setCapturing] = useState(false);
  const [capturedInstrument, setCapturedInstrument] = useState(false);
  const [captureSettings, setCaptureSettings] = useState<VnaCaptureSettings | null>(null);
  const [captureJobId, setCaptureJobId] = useState("");
  const [bandFrom, setBandFrom] = useState(norm.band_mhz?.[0] != null ? String(norm.band_mhz[0]) : "");
  const [bandTo, setBandTo] = useState(norm.band_mhz?.[1] != null ? String(norm.band_mhz[1]) : "");
  const [verdict, setVerdict] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<VswrMeasurement | null>(null);
  const nextButton = useRef<HTMLButtonElement>(null);
  const savedCaptureSettings = result?.file_meta?.capture_settings ?? captureSettings;
  const savedCaptureWarnings = result?.file_meta?.capture_warnings ?? [];
  const refusals = captureSettings ? captureRefusals(captureSettings) : [];
  const problems = uploadProblems({ instrumentId, file, bandFrom, bandTo, norm, verdict, reason });
  const undefinedNorm = norm.status !== "ok";
  const customer = norm.status === "ok" && norm.source === "customer";

  async function submit() {
    if (!file || busy || capturing || problems.length || refusals.length) return;
    setBusy(true);
    setError("");
    try {
      const saved = await productionApi.uploadVswr({
        task_id: task.id, position: row.position, instrument_id: instrumentId, file_name: file.name, file_base64: await readBase64(file),
        ...(captureSettings ? { capture_settings: captureSettings } : {}),
        ...(captureJobId ? { capture_job_id: captureJobId } : {}),
        band_from_mhz: parseMhz(bandFrom), band_to_mhz: parseMhz(bandTo),
        ...(undefinedNorm ? { verdict: verdict as "pass" | "fail", reason: reason.trim() } : {}),
      });
      setResult(saved.measurement);
      await onSaved();
      // Годное изделие — паспорт сразу (владелец 19.11); брак паспорта не получает, а идёт в брак ОТК.
      if (saved.measurement.verdict === "pass" && onPassport) await onPassport();
    } catch (reasonError) {
      setError(reasonError instanceof Error ? reasonError.message : "Замер не загружен");
    } finally {
      setBusy(false);
    }
  }

  // Поток: снятая с ВАЦ трасса без отказов загружается сама — контролёр только сканирует и смотрит вердикт.
  useEffect(() => {
    if (flow && captureJobId && file && !result && !busy && !problems.length && !refusals.length) void submit();
  }, [flow, captureJobId, file]);
  useEffect(() => { if (result) nextButton.current?.focus(); }, [result]);

  const tone = result ? VSWR_TONES[vswrTone(result)] : null;
  return <PageWindow label={`Замер КСВН ${row.serial_number}`} width={860} close={close}><div className="panel vswr-upload">
    <div className="manual-stock-head"><h2>Замер КСВН · {row.serial_number}</h2><span className="page-count">{task.number}</span></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!result && <>
      <p className={`vswr-norm${undefinedNorm ? " is-undefined" : ""}`}>{normLine(norm)}</p>
      <div className="form-grid">
        <label className="half">Прибор (средство измерения)
          <select value={instrumentId} disabled={busy || capturing || capturedInstrument} onChange={(event) => setInstrumentId(event.target.value)}>
            <option value="">{active.length ? "— выберите прибор —" : "Нет действующих СИ"}</option>
            {active.map((item) => <option key={item.id} value={item.id}>{item.inventory_number} · {item.name} · поверка до {formatDate(item.verification_due)}</option>)}
          </select>
        </label>
        <div className="half">
          <label htmlFor="vswr-measurement-file">Файл замера</label>
          <input ref={fileInput} id="vswr-measurement-file" type="file" hidden accept={FILE_ACCEPT} aria-label="Файл замера"
            disabled={busy || capturing} onChange={(event) => {
              setFile(event.target.files?.[0] ?? null); setCapturedInstrument(false); setCaptureSettings(null); setCaptureJobId("");
            }} />
          <VnaCapture instruments={active} disabled={busy} onBusy={setCapturing} autoOpen={capture} autoStart={capture && flow} onFile={(captured, selectedId, settings, jobId) => {
            if (fileInput.current) fileInput.current.value = "";
            setFile(captured); setInstrumentId(selectedId); setCapturedInstrument(true); setCaptureSettings(settings); setCaptureJobId(jobId); setError("");
          }}>
            <button type="button" className="secondary small" disabled={busy || capturing} onClick={() => fileInput.current?.click()}>Выбрать файл…</button>
          </VnaCapture>
          {file && <small role="status">Выбран файл: {file.name}</small>}
        </div>
        {/* Норма заказчика: полоса — её, вердикт сервер выносит только по её точкам. */}
        <label>Полоса от, МГц<input inputMode="decimal" value={bandFrom} readOnly={customer} title={customer ? "Полоса нормы заказчика" : undefined} onChange={(event) => setBandFrom(event.target.value)} /></label>
        <label>Полоса до, МГц<input inputMode="decimal" value={bandTo} readOnly={customer} title={customer ? "Полоса нормы заказчика" : undefined} onChange={(event) => setBandTo(event.target.value)} /></label>
        {undefinedNorm && <>
          <label>Вердикт
            <select value={verdict} onChange={(event) => setVerdict(event.target.value)}>
              <option value="">— выберите —</option><option value="pass">Годно</option><option value="fail">Не годно</option>
            </select>
          </label>
          <label className="wide">Причина вердикта<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        </>}
      </div>
      {captureSettings && <VnaSettings settings={captureSettings} norm={norm} />}
      {problems.length > 0 && <small className="danger-text vswr-problems">{problems.join(". ")}.</small>}
      <div className="form-actions">
        <button className="secondary small" onClick={close}>Отмена</button>
        <button className="primary small" disabled={busy || capturing || problems.length > 0 || refusals.length > 0} onClick={() => void submit()}>{busy ? "Загрузка…" : "Загрузить замер"}</button>
      </div>
    </>}
    {result && tone && <>
      {savedCaptureSettings ? <VnaSettings settings={savedCaptureSettings} norm={norm} warnings={savedCaptureWarnings} />
        : savedCaptureWarnings.map((warning) => <p key={warning} className="warning-inline" role="status">{warning}</p>)}
      <p className="vswr-result">
        <StatusChip status={tone.label} tone={tone.tone} title={tone.hint} /> Макс. КСВН <b>{num(result.max_vswr)}</b> на {num(result.max_vswr_mhz, 1)} МГц · попытка {result.attempt}
        {result.norm_vswr_max !== null && <> · норма ≤ {num(result.norm_vswr_max)}</>}
      </p>
      {result.verdict_manual && <p className="vswr-norm is-undefined">Вердикт назначен контролёром: {result.verdict_reason}</p>}
      {result.decision?.status === "border" && <p className="warning-inline" role="status">{result.decision.text}{result.decision.uncertainty != null ? ` (погрешность ВАЦ ±${num(result.decision.uncertainty, 3)})` : ""}.</p>}
      {!result.verdict_manual && result.verdict === "fail" && result.verdict_reason && result.decision?.status !== "border" && <p className="danger-text">{result.verdict_reason}</p>}
      <VswrChart trace={result.trace ?? []} normMax={result.norm_vswr_max} band={result.band_mhz} maxVswr={result.max_vswr} maxMhz={result.max_vswr_mhz} />
      <div className="form-actions">
        {result.verdict === "fail" && canReject && <button className="secondary small" onClick={() => onReject({
          taskId: task.id, serialNumber: row.serial_number, description: rejectDescription(row.serial_number, result),
        })}>В брак ОТК</button>}
        {onPassport && <button className="secondary small" onClick={() => void onPassport()}>Паспорт</button>}
        <button className="secondary small" onClick={close}>Закрыть</button>
        {next && nextLabel && <button ref={nextButton} className="primary small" onClick={next}>{nextLabel}</button>}
      </div>
    </>}
  </div></PageWindow>;
}
