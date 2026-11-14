import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, failureText, vnaCaptureApi, warehouseApi, type ProductionInstrument, type VnaAgent, type VnaCaptureSettings, type VnaTransfer, type VswrNorm } from "./api";
import { formatNumber } from "./format";
import { PageWindow } from "./PageWindow";
import { useCurrentUserId } from "./rights";

const PC_STORAGE = "erp.vna.pc";
type RememberedPc = Pick<VnaAgent, "id" | "pc">;
function rememberedPc(userId: string | null): RememberedPc | null {
  try {
    localStorage.removeItem(PC_STORAGE);
    if (!userId) return null;
    const value = JSON.parse(localStorage.getItem(`${PC_STORAGE}:${userId}`) ?? "null");
    return value && typeof value.id === "string" && value.id && value.id.length <= 80
      && typeof value.pc === "string" && value.pc ? { id: value.id, pc: value.pc } : null;
  } catch { return null; } // Storage may be unavailable in private/managed browsers.
}

function readiness(vna: VnaAgent["vna"]): string {
  if (vna?.ready) return "готов";
  if (vna?.reason === "s2vna_not_running") return "S2VNA не запущена";
  if (vna?.reason === "device_not_connected") return "прибор не подключён";
  return "готовность не подтверждена";
}

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = setTimeout(done, 1000);
    signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}

export function captureWarnings(settings: VnaCaptureSettings, norm: VswrNorm): string[] {
  const warnings: string[] = [];
  if (settings.smoothing_enabled === true) warnings.push("Сглаживание включено — пики КСВН занижаются, выключите");
  if (settings.correction_enabled === false || ["NONE", "C?", "C!", "RC?", "RC!"].includes(settings.correction_status ?? "")) {
    warnings.push("Нет калибровки на этом диапазоне");
  }
  const [from, to] = norm.band_mhz ?? [];
  if ((from != null && settings.start_hz != null && settings.start_hz > from * 1e6)
    || (to != null && settings.stop_hz != null && settings.stop_hz < to * 1e6)) {
    warnings.push("Диапазон ВАЦ не покрывает норму");
  }
  // Use the largest actual frequency gap; a mean would hide sparse logarithmic/segment sweeps.
  if (norm.length_m != null && norm.length_m > 0 && settings.frequency_step_hz != null
    && settings.frequency_step_hz > 299792458 * 0.7 / (2 * norm.length_m) / 10) {
    warnings.push("Мало точек для этой длины");
  }
  return warnings;
}

export function VnaSettings({ settings, norm, warnings = [] }: { settings: VnaCaptureSettings; norm: VswrNorm; warnings?: string[] }) {
  const number = (value: number | undefined, divisor = 1) => value == null ? "не прочитано" : formatNumber(value / divisor, { digits: 3 });
  const enabled = (value: boolean | undefined) => value == null ? "не прочитано" : value ? "включено" : "выключено";
  const calibration: Record<string, string> = { NONE: "нет", COR: "на текущей сетке", RC: "относительная", "C?": "интерполяция", "C!": "вне диапазона", "RC?": "интерполяция", "RC!": "вне диапазона" };
  return <section aria-label="Настройки ВАЦ">
    <h3>Настройки ВАЦ</h3>
    <div className="form-grid" style={{ fontVariantNumeric: "tabular-nums" }}>
      <div>Трасса: <b>{settings.s_parameter ?? "не прочитано"}</b></div>
      <div>Точек: <b>{number(settings.points)}</b></div>
      <div>Начало, МГц: <b>{number(settings.start_hz, 1e6)}</b></div>
      <div>Конец, МГц: <b>{number(settings.stop_hz, 1e6)}</b></div>
      <div>Полоса ПЧ, Гц: <b>{number(settings.if_bandwidth_hz)}</b></div>
      <div>Усреднение: <b>{enabled(settings.averaging_enabled)}</b> · коэффициент {number(settings.averaging_factor)}</div>
      <div>Сглаживание: <b>{enabled(settings.smoothing_enabled)}</b> · апертура {number(settings.smoothing_aperture_percent)} %</div>
      <div>Коррекция: <b>{enabled(settings.correction_enabled)}</b> · калибровка: {calibration[settings.correction_status ?? ""] ?? "не прочитано"}</div>
    </div>
    {settings.unavailable?.length ? <p className="muted-text">Часть настроек ВАЦ не удалось прочитать.</p> : null}
    {[...new Set([...captureWarnings(settings, norm), ...warnings])].map((warning) => <p key={warning} className="warning-inline" role="status">{warning}</p>)}
  </section>;
}

type VnaCaptureProps = {
  instruments: ProductionInstrument[];
  onFile: (file: File, instrumentId: string, settings: VnaCaptureSettings | null, jobId: string) => void;
  onBusy: (busy: boolean) => void; disabled?: boolean; children?: ReactNode;
};

export function VnaCapture(props: VnaCaptureProps) {
  const userId = useCurrentUserId();
  return <UserVnaCapture key={userId ?? ""} {...props} userId={userId} />;
}

function UserVnaCapture({ instruments, onFile, onBusy, disabled = false, children, userId }: VnaCaptureProps & { userId: string | null }) {
  const configured = instruments.filter((item) => item.kind === "Средство измерений" && item.status === "Действует" && item.vna_app);
  const [opened, setOpened] = useState(false);
  const [instrumentId, setInstrumentId] = useState("");
  const instrument = configured.find((item) => item.id === instrumentId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [transfer, setTransfer] = useState<VnaTransfer | null>(null);
  const [transferring, setTransferring] = useState(false);
  const [notice, setNotice] = useState("");
  const [agents, setAgents] = useState<VnaAgent[] | null>(null);
  const [remembered, setRemembered] = useState(() => rememberedPc(userId));
  const [agentId, setAgentId] = useState("");
  const [pc, setPc] = useState("");
  const [guide, setGuide] = useState(false);
  const [command, setCommand] = useState("");
  const [installing, setInstalling] = useState(false);
  const run = useRef<AbortController | null>(null);
  const jobId = useRef("");
  const mounted = useRef(true);
  const viewEpoch = useRef(0);

  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false;
    run.current?.abort();
    if (jobId.current) void vnaCaptureApi.cancel(jobId.current).catch(() => { /* Server expiry closes a disconnected job. */ });
  }; }, []);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);

  function close() {
    viewEpoch.current++;
    run.current?.abort();
    if (jobId.current) void vnaCaptureApi.cancel(jobId.current).catch(() => { /* Server expiry remains authoritative. */ });
    setOpened(false);
  }

  async function begin(chosen = agents ? agentId : "", chooseDevice = false) {
    if (run.current || disabled || transferring || !instrument) return;
    const selectedId = instrument.id;
    const controller = new AbortController();
    run.current = controller;
    const { signal } = controller;
    setBusy(true); setError(""); setGuide(false); setAgents(null); setTransfer(null); setNotice("");
    try {
      // Let create settle even after close so a newly created job can still be cancelled by id.
      const answer = await vnaCaptureApi.start(selectedId, instrument.vna_app === "Arinst VL" ? chosen : "", chooseDevice);
      if (signal.aborted) {
        if ("id" in answer) await vnaCaptureApi.cancel(answer.id);
        return;
      }
      if (answer.status === "choose") {
        // Only ambiguous devices need a picker; the server routes a known instrument directly.
        setAgentId(answer.agents.some((agent) => agent.id === answer.preferred_agent_id) ? answer.preferred_agent_id! : "");
        setPc(""); setAgents(answer.agents); return;
      }
      if (answer.status === "no_agent") {
        setAgentId(""); setPc(""); setError(answer.message ?? "ВАЦ не в сети");
        setTransfer(answer.transfer ?? null); setGuide(!answer.transfer); return;
      }
      jobId.current = answer.id;
      setPc(answer.agent?.pc ?? "");
      if (answer.agent && instrument.vna_app === "Arinst VL") {
        const selected = { id: answer.agent.id, pc: answer.agent.pc };
        setRemembered(selected); setAgentId(selected.id);
        try { if (userId) localStorage.setItem(`${PC_STORAGE}:${userId}`, JSON.stringify(selected)); }
        catch { /* Keep the confirmed choice for this mounted window when storage is unavailable. */ }
      }
      const deadline = Date.now() + 240_000;
      let job = answer;
      while (!signal.aborted) {
        if (job.status === "failed") throw new Error(job.error || "ВАЦ не прислал трассу");
        if (job.status === "done") {
          const blob = await vnaCaptureApi.file(job.id, signal);
          if (!signal.aborted) {
            onFile(new File([blob], job.file_name, { type: "application/octet-stream" }), selectedId, job.capture_settings ?? null, job.id);
            setOpened(false);
          }
          return;
        }
        if (Date.now() >= deadline) throw new Error("ВАЦ не ответил — проверьте агент на выбранном ПК");
        await pause(signal);
        if (!signal.aborted) job = await vnaCaptureApi.status(job.id, signal);
      }
    } catch (reason) {
      if (!signal.aborted && mounted.current) setError(failureText(reason, "Не удалось снять трассу ВАЦ"));
      if (jobId.current) {
        try { await vnaCaptureApi.cancel(jobId.current); }
        catch { /* Keep the original error; server expiry remains authoritative. */ }
      }
    } finally {
      if (run.current === controller) {
        run.current = null; jobId.current = "";
        if (mounted.current) setBusy(false);
      }
    }
  }

  async function moveDevice(target: VnaAgent) {
    if (!instrument || !transfer || transferring) return;
    const epoch = viewEpoch.current;
    setTransferring(true); setNotice("");
    try {
      await vnaCaptureApi.transfer(instrument.id, transfer.serial, transfer.owner_id, target.id);
      if (mounted.current && epoch === viewEpoch.current) {
        setNotice(`ВАЦ № ${transfer.serial} перенесён на ПК ${target.pc}`);
        setTransfer(null); setError(""); setPc(target.pc); setGuide(false);
      }
    } catch (reason) {
      if (mounted.current && epoch === viewEpoch.current) setError(failureText(reason, "Не удалось перенести ВАЦ"));
    } finally { if (mounted.current) setTransferring(false); }
  }

  async function install() {
    setInstalling(true); setError("");
    try { const issued = await warehouseApi.scanAgentInstallToken(); if (mounted.current) setCommand(issued.command); }
    catch (reason) { if (mounted.current) setError(reason instanceof ApiError && reason.status === 403
      ? "Попросите администратора ERP выдать команду установки агента для этого ПК."
      : failureText(reason, "Не удалось получить команду установки")); }
    finally { if (mounted.current) setInstalling(false); }
  }

  return <div className="vna-capture">
    <div className="form-actions">
      {children}
      <button type="button" className="secondary" disabled={disabled || busy} onClick={() => {
        setInstrumentId(""); setAgentId(""); setAgents(null); setError(""); setGuide(false); setPc(""); setTransfer(null); setNotice(""); setOpened(true);
      }}>Снять с ВАЦ</button>
    </div>
    {opened && <PageWindow label="Снять с ВАЦ" width={680} close={close}><div className="panel">
      <h2>Снять с ВАЦ</h2>
      {configured.length ? <label>Средство измерений<select name="vna_instrument_id" value={instrumentId} disabled={busy || transferring} onChange={(event) => {
        const arinst = configured.find((item) => item.id === event.target.value)?.vna_app === "Arinst VL";
        setInstrumentId(event.target.value); setAgentId(arinst ? remembered?.id ?? "" : ""); setAgents(null); setError(""); setGuide(false); setPc(arinst ? remembered?.pc ?? "" : ""); setTransfer(null); setNotice("");
      }}>
        <option value="">— выберите прибор —</option>
        {configured.map((item) => <option key={item.id} value={item.id}>{item.inventory_number} · {item.name} — {item.vna_app}</option>)}
      </select></label> : <p className="empty">Нет действующих СИ с программой ВАЦ. Укажите «Программу ВАЦ» в карточке прибора.</p>}
      {pc && <div className="form-actions"><span>ПК: {pc}</span>
        {instrument?.vna_app === "Arinst VL" && <button type="button" className="secondary" disabled={busy || disabled} onClick={() => void begin("", true)}>Сменить устройство</button>}
      </div>}
      {busy && <p role="status">ВАЦ: снимаю трассу…</p>}
      {error && <p className="error-banner" role="alert">{error}</p>}
      {notice && <p className="success-banner" role="status">{notice}</p>}
      {transfer && <div className="form-actions">{transfer.agents.map((target) =>
        <button type="button" className="secondary" key={target.id} disabled={busy || disabled || transferring} onClick={() => void moveDevice(target)}>
          {transferring ? "Переносим ВАЦ…" : `Перенести ВАЦ № ${transfer.serial} на ПК ${target.pc}`}
        </button>)}
      </div>}
      {agents && <label>Устройство ВАЦ<select name="vna_agent_id" value={agentId} disabled={busy} onChange={(event) => setAgentId(event.target.value)}>
        <option value="">— выберите устройство —</option>
        {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.vna?.name || "Arinst VNA-DL"} · {agent.pc}{instrument?.vna_app === "S2VNA" ? ` · ${readiness(agent.vna)}` : ""}</option>)}
      </select></label>}
      {guide && <div className="scan-agent">
        <p>Попросите администратора проверить агент на ПК с прибором. Команду установки выполните в PowerShell на ПК с ВАЦ.</p>
        {command && <code className="scan-agent-cmd">{command}</code>}
        <button type="button" className="secondary" disabled={installing} onClick={() => void install()}>{installing ? "Готовим команду…" : "Получить команду установки"}</button>
      </div>}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={close}>{busy ? "Отменить снятие" : "Отмена"}</button>
        <button type="button" className="primary" disabled={!instrument || disabled || busy || transferring || Boolean(agents && !agentId)} onClick={() => void begin()}>
          {guide ? "Проверить" : "Снять трассу"}
        </button>
      </div>
    </div></PageWindow>}
  </div>;
}
