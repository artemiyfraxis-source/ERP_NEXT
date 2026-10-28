import { useEffect, useState, useSyncExternalStore } from "react";
import { ApiError, failureText, warehouseApi, type ScanAgentInfo, type ScanAgentScanner, type WarehouseScanJob, type WarehouseScanStart } from "./api";
import { PageWindow } from "./PageWindow";
import { loadPersonalViews, resetPersonalView, savePersonalView } from "./personalViews";
import { useCanScan } from "./rights";

/** «Сканер с любого ПК» (владелец 28.10: «везде… универсальность, если вдруг ПК сменять»): «Сканировать» уходит на тот
 *  ПК, за которым сидит человек. Сервер сам находит агент по адресу; нет агента — окно с установкой, несколько — выбор.
 *  Владелец 28.10: каждый, кому доступно сканирование, выбирает свой сканер — выбор лежит в учётке (вид `scan.device`
 *  личных настроек) и действует на любом экране и любом ПК. Нет выбора — сканер этого ПК, первый из его списка. */
const REMEMBERED_KEY = "erp.scanAgent";
const DEVICE_VIEW = "scan.device";
const DEVICE_LOCAL_KEY = "erp.scanDevice";

export function rememberedScanAgent(): string {
  try { return window.localStorage.getItem(REMEMBERED_KEY) ?? ""; } catch { return ""; }
}

function rememberScanAgent(id: string) {
  try { window.localStorage.setItem(REMEMBERED_KEY, id); } catch { /* приватное окно: выбор не запомнится */ }
}

export const scannerId = (scanner: ScanAgentScanner) => (typeof scanner === "string" ? scanner : scanner.id);
export const scannerName = (scanner: ScanAgentScanner) => (typeof scanner === "string" ? scanner : scanner.name);

/** Выбранные ПК и сканер учётки; без `scanner_id` — первый сканер этого ПК. */
export interface ScanDevice { agent_id: string; scanner_id?: string }

function parseDevice(value: unknown): ScanDevice | null {
  if (!value || typeof value !== "object") return null;
  const { agent_id, scanner_id } = value as Record<string, unknown>;
  if (typeof agent_id !== "string" || !agent_id) return null;
  return typeof scanner_id === "string" && scanner_id ? { agent_id, scanner_id } : { agent_id };
}

let device: ScanDevice | null = null;
let deviceLoaded = false;
let deviceState: { device: ScanDevice | null } = { device: null };
const deviceListeners = new Set<() => void>();

function publishDevice() {
  deviceState = { device };
  deviceListeners.forEach((listener) => listener());
}

/** Выбор из учётки (раз на страницу); сервер молчит — копия этого браузера. */
export async function loadScanDevice(): Promise<ScanDevice | null> {
  if (!deviceLoaded) {
    const views = await loadPersonalViews();
    if (views) {
      device = parseDevice(views[DEVICE_VIEW]);
      deviceLoaded = true;
    } else {
      try { device = parseDevice(JSON.parse(window.localStorage.getItem(DEVICE_LOCAL_KEY) ?? "null")); } catch { device = null; }
    }
    publishDevice();
  }
  return device;
}

/** Запоминает выбор в учётке; `null` — «Сканер этого ПК» (по умолчанию). */
export function setScanDevice(next: ScanDevice | null) {
  device = next;
  deviceLoaded = true;
  try {
    if (next) window.localStorage.setItem(DEVICE_LOCAL_KEY, JSON.stringify(next)); else window.localStorage.removeItem(DEVICE_LOCAL_KEY);
  } catch { /* приватное окно */ }
  if (next) savePersonalView(DEVICE_VIEW, next, 0); else resetPersonalView(DEVICE_VIEW);
  publishDevice();
}

/** Tests: a fresh page. */
export function forgetScanDevice() {
  device = null;
  deviceLoaded = false;
  publishDevice();
}

/** Что выбрано на самом деле: ПК и сканер из списка агентов, либо «нет в списке» (отключён / ПК выключен). */
export function resolveScanDevice(chosen: ScanDevice | null, agents: ScanAgentInfo[] | null) {
  if (!chosen || !agents) return { agent: null, scanner: null, missing: false };
  const agent = agents.find((item) => item.id === chosen.agent_id) ?? null;
  const scanner = agent && chosen.scanner_id ? agent.scanners.find((item) => scannerId(item) === chosen.scanner_id) ?? null : null;
  return { agent, scanner, missing: !agent || Boolean(chosen.scanner_id && !scanner) };
}

type View = { kind: "guide"; idle: string[] } | { kind: "choose"; agents: ScanAgentInfo[] } | null;

/** `start` — запрос задания у сервера (ПК и сканер из учётки). `begin()` отдаёт задание или null: тогда открыто окно
 *  установки / выбора, а `dialogs(retry)` рисует его; после выбора и по «Проверить» вызывается `retry` — снова «Сканировать». */
export function useScanAgent(start: (agentId?: string, targetScanner?: string) => Promise<WarehouseScanStart>) {
  const [view, setView] = useState<View>(null);

  async function begin(): Promise<WarehouseScanJob | null> {
    const chosen = await loadScanDevice();
    let agentId = chosen?.agent_id || rememberedScanAgent() || undefined;
    let scanner = chosen?.scanner_id;
    if (chosen) {
      // Выбранного сканера больше нет в списке — сканируем с этого ПК (строка «Сменить сканер» скажет об этом).
      const agents = await warehouseApi.scanAgents().then((answer) => answer.agents, () => null);
      if (resolveScanDevice(chosen, agents).missing) { agentId = undefined; scanner = undefined; }
    }
    const answer = await start(agentId, scanner);
    if (answer.status === "no_agent") { setView({ kind: "guide", idle: answer.idle ?? [] }); return null; }
    if (answer.status === "choose") { setView({ kind: "choose", agents: answer.agents }); return null; }
    return answer;
  }

  function dialogs(retry: () => void) {
    if (!view) return null;
    const done = (again: boolean) => { setView(null); if (again) retry(); };
    return view.kind === "guide"
      ? <ScanAgentGuide idle={view.idle} close={() => done(false)} check={() => done(true)} />
      : <ScanAgentPicker agents={view.agents} close={() => done(false)}
          pick={(id) => { rememberScanAgent(id); setScanDevice({ agent_id: id }); done(true); }}
          install={() => setView({ kind: "guide", idle: [] })} />;
  }

  return { begin, dialogs };
}

const subscribeDevice = (listener: () => void) => { deviceListeners.add(listener); return () => { deviceListeners.delete(listener); }; };
const deviceSnapshot = () => deviceState;

/** «ПК · сканер» этой учётки и «Сменить сканер»: рядом с кнопками «Сканировать». Без права «Сканирование документов»
 *  не рисуется. */
export function ScanDeviceLine() {
  const canScan = useCanScan();
  const { device: chosen } = useSyncExternalStore(subscribeDevice, deviceSnapshot, deviceSnapshot);
  const [agents, setAgents] = useState<ScanAgentInfo[] | null>(null);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    if (!canScan) return;
    let alive = true;
    void loadScanDevice();
    warehouseApi.scanAgents().then((answer) => { if (alive) setAgents(answer.agents); }, () => undefined);
    return () => { alive = false; };
  }, [canScan]);

  if (!canScan) return null;
  const { agent, scanner, missing } = resolveScanDevice(chosen, agents);
  const text = !chosen || missing ? "этот ПК · первый сканер"
    : `${agent ? agent.pc : "выбранный ПК"} · ${scanner ? scannerName(scanner) : "первый сканер"}`;
  return (
    <p className="scan-device" data-testid="scan-device">
      <span>ПК · сканер: <b>{text}</b></span>
      <button type="button" className="link-button" onClick={() => setPicking(true)}>Сменить сканер</button>
      {chosen && missing ? <span className="warning-inline">Выбранный сканер не найден — сканируем с этого ПК</span> : null}
      {picking ? <ScanDevicePicker agents={agents} chosen={chosen} close={() => setPicking(false)}
        pick={(next) => { setScanDevice(next); setPicking(false); }} /> : null}
    </p>
  );
}

function ScanDevicePicker({ agents, chosen, close, pick }: {
  agents: ScanAgentInfo[] | null; chosen: ScanDevice | null; close: () => void; pick: (next: ScanDevice | null) => void;
}) {
  return (
    <PageWindow label="Выбор сканера" close={close} width={560} dismissable>
      <div className="panel scan-agent">
        <h2>Каким сканером сканировать</h2>
        <div className="scan-agent-list">
          <button type="button" className={chosen ? "secondary" : "primary"} aria-pressed={!chosen} onClick={() => pick(null)}>Сканер этого ПК (первый из списка)</button>
          {agents === null ? <p className="empty">Список ПК недоступен — проверьте связь с ERP</p> : null}
          {agents?.length === 0 ? <p className="empty">Ни один ПК со сканером не вышел на связь</p> : null}
          {agents?.map((agent) => agent.scanners.length === 0
            ? <p key={agent.id} className="muted">{agent.pc}: сканер не виден</p>
            : agent.scanners.map((scanner) => {
              const current = chosen?.agent_id === agent.id && chosen.scanner_id === scannerId(scanner);
              return (
                <button key={`${agent.id}/${scannerId(scanner)}`} type="button" className={current ? "primary" : "secondary"}
                  aria-pressed={current} onClick={() => pick({ agent_id: agent.id, scanner_id: scannerId(scanner) })}>
                  {agent.pc} · {scannerName(scanner)}
                </button>
              );
            }))}
        </div>
        <p className="scan-agent-actions"><button type="button" className="secondary small" onClick={close}>Отмена</button></p>
      </div>
    </PageWindow>
  );
}

function ScanAgentPicker({ agents, close, pick, install }: {
  agents: ScanAgentInfo[]; close: () => void; pick: (id: string) => void; install: () => void;
}) {
  return (
    <PageWindow label="Выбор сканера" close={close} width={560} dismissable>
      <div className="panel scan-agent">
        <h2>С какого ПК сканировать</h2>
        <div className="scan-agent-list">
          {agents.map((agent) => (
            <button key={agent.id} type="button" className="secondary" onClick={() => pick(agent.id)}>
              Сканер: {agent.pc} · {agent.scanners.map(scannerName).join(", ")}
            </button>
          ))}
        </div>
        <p className="scan-agent-actions">
          <button type="button" className="secondary small" onClick={install}>Установить на этом ПК</button>
          <button type="button" className="secondary small" onClick={close}>Отмена</button>
        </p>
      </div>
    </PageWindow>
  );
}

function ScanAgentGuide({ idle, close, check }: { idle: string[]; close: () => void; check: () => void }) {
  const [command, setCommand] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [state, setState] = useState<"loading" | "ready" | "admin" | "error">("loading");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    warehouseApi.scanAgentInstallToken().then((issued) => {
      if (cancelled) return;
      setCommand(issued.command);
      setMinutes(issued.valid_minutes);
      setState("ready");
    }).catch((reason: unknown) => {
      if (cancelled) return;
      if (reason instanceof ApiError && reason.status === 403) { setState("admin"); return; }
      setError(failureText(reason, "Не удалось выпустить команду установки"));
      setState("error");
    });
    return () => { cancelled = true; };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setError("Не удалось скопировать — выделите команду и скопируйте вручную");
    }
  }

  return (
    <PageWindow label="Установка сканера" close={close} width={720} dismissable>
      <div className="panel scan-agent">
        <h2>Сканер не найден — установите агент на этот ПК</h2>
        {idle.length ? <p className="muted">Агент работает на {idle.join(", ")}, но сканера там не видно.</p> : null}
        <ul className="scan-agent-points">
          <li>Устанавливается один раз, права администратора Windows не нужны.</li>
          <li>Запускается вместе с Windows и сам обновляется.</li>
          <li>Сканер находит сам; подключённый позже подхватится без переустановки.</li>
        </ul>
        {state === "loading" ? <p className="empty">Готовим команду…</p> : null}
        {state === "admin" ? (
          <p className="warning-inline">Команду установки выпускает администратор ERP — попросите его открыть это окно и отправить вам команду.</p>
        ) : null}
        {state === "ready" ? (
          <>
            <p>Откройте PowerShell на этом ПК и выполните команду (действует {minutes} мин, один раз):</p>
            <code className="scan-agent-cmd" data-testid="scan-agent-command">{command}</code>
          </>
        ) : null}
        {error ? <p className="error-banner" role="alert">{error}</p> : null}
        <p className="scan-agent-actions">
          {state === "ready" ? <button type="button" className="secondary small" onClick={() => void copy()}>{copied ? "Скопировано" : "Копировать"}</button> : null}
          <button type="button" className="primary small" onClick={check}>Проверить</button>
          <button type="button" className="secondary small" onClick={close}>Закрыть</button>
        </p>
      </div>
    </PageWindow>
  );
}
