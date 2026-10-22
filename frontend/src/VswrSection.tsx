import { useEffect, useMemo, useState } from "react";
import {
  productionApi,
  type ProductionInstrument,
  type ProductionTask,
  type VswrMeasurement,
  type VswrNorm,
  type VswrSerialRow,
  type VswrTaskOverview,
} from "./api";
import { printCoaxPassport } from "./coaxPassportPrint";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate, formatNumber } from "./format";
import { PageWindow } from "./PageWindow";
import { StatusChip } from "./StatusChip";
import { VswrChart } from "./VswrChart";

/* «Замеры КСВН» в «ОТК» (T4): задание коаксиальной сборки → таблица изделий с последним замером. Замер грузится файлом
   ВАЦ (.s1p / .csv / .txt) в окне; норму сервер берёт из ЭТК задания, а когда её там нет — вердикт и причину пишет
   контролёр. «Паспорт» печатает паспорт кабельной сборки; «Не годно» ведёт в очередь ОТК с готовым описанием брака. */

const FILE_ACCEPT = ".s1p,.csv,.txt";
const num = (value: number | null | undefined, digits = 2) => (value === null || value === undefined ? "" : formatNumber(value, { digits }));

export function normLine(norm: VswrNorm): string {
  if (norm.status === "ok") {
    const band = norm.band_mhz && norm.band_mhz[0] !== null && norm.band_mhz[1] !== null ? `, полоса ${num(norm.band_mhz[0], 1)} … ${num(norm.band_mhz[1], 1)} МГц` : "";
    return `Норма ≤ ${num(norm.vswr_max)} по комплектующим${norm.source ? `: ${norm.source}` : ""}${band}`;
  }
  return `Норма не определена — ${norm.reason || "причина не указана"}`;
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Файл замера не прочитан"));
    reader.onload = () => resolve(String(reader.result ?? "").split(",")[1] ?? "");
    reader.readAsDataURL(file);
  });
}

const verdictChip = (measurement: VswrMeasurement | null) => measurement
  ? <StatusChip status={measurement.verdict_name} tone={measurement.verdict === "pass" ? "success" : "danger"} />
  : <>—</>;

export interface VswrRejectRequest { taskId: string; serialNumber: string; description: string }

export function VswrSection({ tasks, instruments, canUpload, canReject, onReject }: {
  tasks: ProductionTask[];
  instruments: ProductionInstrument[];
  canUpload: boolean;
  /** Есть ли у задания коробка в очереди ОТК — только тогда «Не годно» ведёт в брак. */
  canReject: (taskId: string) => boolean;
  onReject: (request: VswrRejectRequest) => void;
}) {
  const choices = useMemo(() => tasks.filter((task) => !task.pending), [tasks]);
  const [taskId, setTaskId] = useState("");
  const [overview, setOverview] = useState<VswrTaskOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState<VswrSerialRow | null>(null);

  async function load(id: string) {
    if (!id) { setOverview(null); return; }
    setLoading(true);
    setError("");
    try {
      setOverview(await productionApi.vswrTask(id));
    } catch (reason) {
      setOverview(null);
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить замеры КСВН");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(taskId); }, [taskId]);

  async function openPassport(row: VswrSerialRow) {
    setError("");
    try {
      printCoaxPassport(await productionApi.vswrPassport(taskId, row.position));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Паспорт не получен");
    }
  }

  const columns: DataColumn<VswrSerialRow>[] = [
    { key: "position", title: "№", value: (row) => row.position, numeric: true, width: 48 },
    { key: "serial", title: "Серийный номер", value: (row) => row.serial_number },
    { key: "attempts", title: "Попыток", value: (row) => row.attempts, numeric: true, render: (row) => row.attempts || "—" },
    { key: "max", title: "Макс. КСВН", value: (row) => row.latest?.max_vswr ?? "", numeric: true, render: (row) => num(row.latest?.max_vswr) || "—" },
    { key: "mhz", title: "На частоте, МГц", value: (row) => row.latest?.max_vswr_mhz ?? "", numeric: true, render: (row) => num(row.latest?.max_vswr_mhz, 1) || "—" },
    { key: "verdict", title: "Вердикт", value: (row) => row.latest?.verdict_name ?? "Не измерено", filter: true, render: (row) => verdictChip(row.latest) },
    { key: "inspector", title: "Контролёр", value: (row) => row.latest?.inspector.name ?? "", render: (row) => row.latest?.inspector.name || "—" },
    { key: "date", title: "Дата", value: (row) => row.latest?.measured_at ?? "", render: (row) => (row.latest ? formatDate(row.latest.measured_at) : "—") },
    {
      key: "actions", title: "", value: () => "", width: 330, render: (row) => <span className="vswr-actions">
        {canUpload && <button className="secondary small" onClick={() => setUploading(row)}>Загрузить замер</button>}
        <button className="secondary small" disabled={!row.latest} onClick={() => void openPassport(row)}>Паспорт</button>
        {row.latest && <a className="secondary small button-link" href={productionApi.vswrFileUrl(row.latest.id)} download>Файл</a>}
        {row.latest?.verdict === "fail" && canReject(taskId) && <button className="link-button" onClick={() => onReject({
          taskId, serialNumber: row.serial_number,
          description: `Изделие ${row.serial_number}: макс. КСВН ${num(row.latest?.max_vswr)} на ${num(row.latest?.max_vswr_mhz, 1)} МГц при норме ${num(row.latest?.norm_vswr_max) || "—"}`,
        })}>В брак ОТК</button>}
      </span>,
    },
  ];

  return <div className="panel vswr-section">
    <div className="manual-stock-head"><h2>Замеры КСВН</h2>
      <label className="vswr-task-pick">Задание
        <select value={taskId} onChange={(event) => setTaskId(event.target.value)}>
          <option value="">— выберите задание —</option>
          {choices.map((task) => <option key={task.id} value={task.id}>{task.number} · {task.name || task.item} · {task.quantity} шт</option>)}
        </select>
      </label>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!taskId && <div className="empty">Выберите задание коаксиальной сборки — здесь появятся её изделия и замеры.</div>}
    {taskId && loading && !overview && <div className="empty">Загрузка…</div>}
    {overview && <>
      <p className={`vswr-norm${overview.norm.status === "ok" ? "" : " is-undefined"}`}>{normLine(overview.norm)}</p>
      <DataTable label="Изделия задания" storageKey="quality-vswr" rows={overview.serials} columns={columns} rowKey={(row) => row.serial_id}
        exportName={`Замеры КСВН ${overview.task.number}`} emptyText="У задания нет изделий с серийными номерами." />
    </>}
    {uploading && overview && <VswrUploadWindow
      task={overview.task} norm={overview.norm} row={uploading} instruments={instruments}
      close={() => setUploading(null)} onSaved={() => load(taskId)}
      canReject={canReject(taskId)} onReject={(request) => { setUploading(null); onReject(request); }} />}
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

function VswrUploadWindow({ task, norm, row, instruments, close, onSaved, canReject, onReject }: {
  task: VswrTaskOverview["task"];
  norm: VswrNorm;
  row: VswrSerialRow;
  instruments: ProductionInstrument[];
  close: () => void;
  onSaved: () => Promise<void>;
  canReject: boolean;
  onReject: (request: VswrRejectRequest) => void;
}) {
  const active = instruments.filter((item) => item.status === "Действует");
  const [instrumentId, setInstrumentId] = useState(active[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [bandFrom, setBandFrom] = useState(norm.band_mhz?.[0] != null ? String(norm.band_mhz[0]) : "");
  const [bandTo, setBandTo] = useState(norm.band_mhz?.[1] != null ? String(norm.band_mhz[1]) : "");
  const [verdict, setVerdict] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<VswrMeasurement | null>(null);
  const problems = uploadProblems({ instrumentId, file, bandFrom, bandTo, norm, verdict, reason });
  const undefinedNorm = norm.status !== "ok";

  async function submit() {
    if (!file || busy || problems.length) return;
    setBusy(true);
    setError("");
    try {
      const saved = await productionApi.uploadVswr({
        task_id: task.id, position: row.position, instrument_id: instrumentId, file_name: file.name, file_base64: await readBase64(file),
        band_from_mhz: parseMhz(bandFrom), band_to_mhz: parseMhz(bandTo),
        ...(undefinedNorm ? { verdict: verdict as "pass" | "fail", reason: reason.trim() } : {}),
      });
      setResult(saved.measurement);
      await onSaved();
    } catch (reasonError) {
      setError(reasonError instanceof Error ? reasonError.message : "Замер не загружен");
    } finally {
      setBusy(false);
    }
  }

  return <PageWindow label={`Замер КСВН ${row.serial_number}`} width={860} close={close}><div className="panel vswr-upload">
    <div className="manual-stock-head"><h2>Замер КСВН · {row.serial_number}</h2><span className="page-count">{task.number}</span></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!result && <>
      <p className={`vswr-norm${undefinedNorm ? " is-undefined" : ""}`}>{normLine(norm)}</p>
      <div className="form-grid">
        <label className="half">Прибор (средство измерения)
          <select value={instrumentId} onChange={(event) => setInstrumentId(event.target.value)}>
            {!active.length && <option value="">Нет действующих СИ</option>}
            {active.map((item) => <option key={item.id} value={item.id}>{item.inventory_number} · {item.name} · поверка до {formatDate(item.verification_due)}</option>)}
          </select>
        </label>
        <label className="half">Файл замера
          <input type="file" accept={FILE_ACCEPT} aria-label="Файл замера" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </label>
        <label>Полоса от, МГц<input inputMode="decimal" value={bandFrom} onChange={(event) => setBandFrom(event.target.value)} /></label>
        <label>Полоса до, МГц<input inputMode="decimal" value={bandTo} onChange={(event) => setBandTo(event.target.value)} /></label>
        {undefinedNorm && <>
          <label>Вердикт
            <select value={verdict} onChange={(event) => setVerdict(event.target.value)}>
              <option value="">— выберите —</option><option value="pass">Годно</option><option value="fail">Не годно</option>
            </select>
          </label>
          <label className="wide">Причина вердикта<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        </>}
      </div>
      {problems.length > 0 && <small className="danger-text vswr-problems">{problems.join(". ")}.</small>}
      <div className="form-actions">
        <button className="secondary small" onClick={close}>Отмена</button>
        <button className="primary small" disabled={busy || problems.length > 0} onClick={() => void submit()}>{busy ? "Загрузка…" : "Загрузить замер"}</button>
      </div>
    </>}
    {result && <>
      <p className="vswr-result">
        {verdictChip(result)} Макс. КСВН <b>{num(result.max_vswr)}</b> на {num(result.max_vswr_mhz, 1)} МГц · попытка {result.attempt}
        {result.norm_vswr_max !== null && <> · норма ≤ {num(result.norm_vswr_max)}</>}
      </p>
      {result.verdict_manual && <p className="vswr-norm is-undefined">Вердикт назначен контролёром: {result.verdict_reason}</p>}
      <VswrChart trace={result.trace ?? []} normMax={result.norm_vswr_max} band={result.band_mhz} maxVswr={result.max_vswr} maxMhz={result.max_vswr_mhz} />
      <div className="form-actions">
        {result.verdict === "fail" && canReject && <button className="secondary small" onClick={() => onReject({
          taskId: task.id, serialNumber: row.serial_number,
          description: `Изделие ${row.serial_number}: макс. КСВН ${num(result.max_vswr)} на ${num(result.max_vswr_mhz, 1)} МГц при норме ${num(result.norm_vswr_max) || "—"}`,
        })}>В брак ОТК</button>}
        <button className="primary small" onClick={close}>Закрыть</button>
      </div>
    </>}
  </div></PageWindow>;
}
