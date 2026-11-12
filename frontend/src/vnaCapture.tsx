import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, failureText, vnaCaptureApi, warehouseApi, type ProductionInstrument, type VnaAgent, type VnaSource } from "./api";

/** Capture provenance wins over the previous instrument cached in the heartbeat. */
export function vnaNameFromHeader(text: string, fallback: string): string {
  const first = text.replace(/^\uFEFF/, "").split(/\r?\n/, 1)[0];
  return /^! S2VNA (.{1,500}) \d{4}-\d{2}-\d{2}T\S+ (?:S11|S22)$/.exec(first)?.[1].trim() || fallback;
}

function readiness(vna: VnaAgent["vna"]): string {
  if (vna?.ready) return "готов";
  if (vna?.reason === "s2vna_not_running") return "S2VNA не запущена";
  if (vna?.reason === "device_not_connected") return "прибор не подключён";
  return "готовность не подтверждена";
}

/** S2VNA matches its IDN serial; saved Arinst files can identify only a unique active Arinst instrument. */
export function instrumentForVna(name: string, instruments: ProductionInstrument[], source: VnaSource = "s2vna"): string {
  if (source === "arinst") {
    const matches = instruments.filter((item) => item.kind === "Средство измерений" && item.status === "Действует"
      && /arinst/i.test(`${item.name} ${item.manufacturer ?? ""}`));
    return matches.length === 1 ? matches[0].id : "";
  }
  const serial = name.split(",")[2]?.trim();
  if (!serial) return "";
  const matches = instruments.filter((item) => item.kind === "Средство измерений" && item.status === "Действует" && item.serial_number.trim() === serial);
  return matches.length === 1 ? matches[0].id : "";
}

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = setTimeout(done, 1000);
    signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}

export function VnaCapture({ onFile, onBusy, disabled = false, children }: {
  onFile: (file: File, name: string, source: VnaSource) => void; onBusy: (busy: boolean) => void; disabled?: boolean; children?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [agents, setAgents] = useState<VnaAgent[] | null>(null);
  const [agentId, setAgentId] = useState("");
  const [source, setSource] = useState<VnaSource>("s2vna");
  const [pc, setPc] = useState("");
  const [guide, setGuide] = useState(false);
  const [command, setCommand] = useState("");
  const [installing, setInstalling] = useState(false);
  const run = useRef<AbortController | null>(null);
  const jobId = useRef("");
  const mounted = useRef(true);
  const options = (agents ?? []).flatMap((agent) => (agent.vna_sources ?? (agent.vna ? ["s2vna" as const] : []))
    .map((source) => ({ ...agent, source, key: `${source}:${agent.id}` })));

  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false;
    run.current?.abort();
    if (jobId.current) void vnaCaptureApi.cancel(jobId.current).catch(() => { /* Server expiry closes a disconnected job. */ });
  }; }, []);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);

  async function begin(chosen = agentId) {
    if (run.current || disabled) return;
    const controller = new AbortController();
    run.current = controller;
    const { signal } = controller;
    setBusy(true); setError(""); setGuide(false); setAgents(null);
    try {
      // Let create settle even after unmount, so a created job can be cancelled by id.
      const answer = await vnaCaptureApi.start(chosen, source);
      if (signal.aborted) {
        if ("id" in answer) await vnaCaptureApi.cancel(answer.id);
        return;
      }
      if (answer.status === "choose") { setAgentId(""); setAgents(answer.agents); return; }
      if (answer.status === "no_agent") { setAgentId(""); setGuide(true); return; }
      jobId.current = answer.id;
      setPc(answer.agent?.pc ?? "");
      const deadline = Date.now() + 240_000;
      let job = answer;
      while (!signal.aborted) {
        if (job.status === "failed") throw new Error(job.error || "ВАЦ не прислал трассу");
        if (job.status === "done") {
          const blob = await vnaCaptureApi.file(job.id, signal);
          const text = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = () => reject(new Error("Не удалось прочитать файл ВАЦ"));
            reader.readAsText(blob);
          });
          if (!signal.aborted) onFile(new File([blob], job.file_name, { type: "application/octet-stream" }),
            job.source === "arinst" ? job.vna.name : vnaNameFromHeader(text, job.vna.name || answer.agent?.vna?.name || ""), job.source);
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
      <button type="button" className="secondary small" disabled={disabled || busy} onClick={() => void begin()}>Снять с ВАЦ</button>
      {pc && !busy && <button type="button" className="link-button" disabled={disabled} onClick={() => { setPc(""); setAgentId(""); void begin(""); }}>Сменить ПК / источник</button>}
    </div>
    {busy && <p role="status">ВАЦ: снимаю трассу…{pc ? ` · ${pc}` : ""}</p>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {agents && <div>
      <label>ПК и источник ВАЦ<select name="vna-source" value={agentId ? `${source}:${agentId}` : ""} onChange={(event) => {
        const selected = options.find((option) => option.key === event.target.value);
        setAgentId(selected?.id ?? ""); setSource(selected?.source ?? "s2vna");
      }}>
        <option value="">— выберите ПК и источник —</option>
        {options.map((option) => <option key={option.key} value={option.key}>{option.pc} · {option.source === "arinst"
          ? "Arinst — последний файл" : `Planar S50180 (S2VNA) · ${readiness(option.vna)}`}</option>)}
      </select></label>
      <button type="button" className="secondary small" disabled={!agentId || disabled || busy} onClick={() => void begin()}>Снять на выбранном ПК</button>
      <button type="button" className="link-button" onClick={() => { setAgents(null); setGuide(true); }}>Моего ПК нет</button>
    </div>}
    {guide && <div className="scan-agent">
      <p>Агент с ВАЦ не найден. Установите S2VNA или Arinst Virtual Lab и агент ERP на этот ПК.</p>
      <p>Команду установки выдаёт администратор ERP. Выполните её в PowerShell на этом ПК.</p>
      {command && <code className="scan-agent-cmd">{command}</code>}
      <div className="form-actions">
        <button type="button" className="secondary small" disabled={installing} onClick={() => void install()}>{installing ? "Готовим команду…" : "Получить команду установки"}</button>
        <button type="button" className="secondary small" disabled={disabled || busy} onClick={() => void begin("")}>Проверить</button>
      </div>
    </div>}
  </div>;
}
