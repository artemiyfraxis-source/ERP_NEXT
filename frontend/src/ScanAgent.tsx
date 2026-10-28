import { useEffect, useState } from "react";
import { ApiError, failureText, warehouseApi, type ScanAgentInfo, type WarehouseScanJob, type WarehouseScanStart } from "./api";
import { PageWindow } from "./PageWindow";

/** «Сканер с любого ПК» (владелец 28.10: «везде… универсальность, если вдруг ПК сменять»): «Сканировать» уходит на тот
 *  ПК, за которым сидит человек. Сервер сам находит агент по адресу; нет агента — окно с установкой, несколько — выбор. */
const REMEMBERED_KEY = "erp.scanAgent";

export function rememberedScanAgent(): string {
  try { return window.localStorage.getItem(REMEMBERED_KEY) ?? ""; } catch { return ""; }
}

function rememberScanAgent(id: string) {
  try { window.localStorage.setItem(REMEMBERED_KEY, id); } catch { /* приватное окно: выбор не запомнится */ }
}

type View = { kind: "guide"; idle: string[] } | { kind: "choose"; agents: ScanAgentInfo[] } | null;

/** `start` — запрос задания у сервера (с запомненным агентом). `begin()` отдаёт задание или null: тогда открыто окно
 *  установки / выбора, а `dialogs(retry)` рисует его; после выбора и по «Проверить» вызывается `retry` — снова «Сканировать». */
export function useScanAgent(start: (agentId?: string) => Promise<WarehouseScanStart>) {
  const [view, setView] = useState<View>(null);

  async function begin(): Promise<WarehouseScanJob | null> {
    const remembered = rememberedScanAgent();
    const answer = await start(remembered || undefined);
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
          pick={(id) => { rememberScanAgent(id); done(true); }}
          install={() => setView({ kind: "guide", idle: [] })} />;
  }

  return { begin, dialogs };
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
              Сканер: {agent.pc} · {agent.scanners.join(", ")}
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
