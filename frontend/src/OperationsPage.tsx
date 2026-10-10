import { useEffect, useState } from "react";
import { failureText, operationsApi, type AuditItem, type BackgroundGroup, type BackgroundStatus, type OutboxHealth } from "./api";
import { AUDIT_MODULES, auditDetails, auditEventName, auditModule } from "./auditLabels";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate, formatMoment } from "./format";

const EMPTY_HEALTH: OutboxHealth = { total: 0, published: 0, pending: 0, dead: 0, failures: [] };

const COLUMNS: DataColumn<AuditItem>[] = [
  { key: "at", title: "Время", value: (event) => event.occurred_at, render: (event) => formatDate(event.occurred_at) },
  {
    key: "event", title: "Событие", value: (event) => `${auditEventName(event.event_type)} ${event.event_type}`,
    render: (event) => <><strong>{auditEventName(event.event_type)}</strong><small className="muted">{event.event_type}</small></>,
  },
  { key: "module", title: "Раздел", value: (event) => auditModule(event.event_type), filter: true },
  { key: "actor", title: "Сотрудник", value: (event) => event.actor, filter: true, render: (event) => <>{event.actor}<small className="muted">{event.department}</small></> },
  { key: "details", title: "Подробности", value: (event) => auditDetails(event.details) || "—", wrap: true },
  { key: "object", title: "Объект", value: (event) => event.aggregate_id, hidden: true },
];

const STATUS: Record<BackgroundStatus, [label: string, tone: string]> = {
  ok: ["Работает", "tone-ok"],
  warn: ["Сбои", "tone-warn"],
  red: ["Стоит", "tone-danger"],
};

/** «3 с», «4 мин», «2 ч 05 мин» — сколько прошло. */
export function ago(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} с`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} мин`;
  return `${Math.floor(minutes / 60)} ч ${String(minutes % 60).padStart(2, "0")} мин`;
}

function runTime(ms: number): string {
  return ms < 1000 ? `${ms} мс` : `${(ms / 1000).toFixed(1).replace(".", ",")} с`;
}

function lastSuccess(group: BackgroundGroup): string {
  if (!group.success_at) return "ни разу";
  return `${formatMoment(group.success_at)} · ${ago((Date.now() - new Date(group.success_at).getTime()) / 1000)} назад`;
}

function BackgroundPanel({ health }: { health: OutboxHealth }) {
  const groups = health.groups ?? [];
  if (!groups.length) return null;
  const late = (health.topics ?? []).filter((topic) => topic.status === "red");
  const stopped = groups.filter((group) => group.status === "red");
  return <article className="panel operations-background" aria-label="Фоновые процессы">
    <h2>Фоновые процессы</h2>
    {(stopped.length > 0 || late.length > 0) && <div className="error-banner" role="alert">
      {stopped.length > 0 && <>Не работает: {stopped.map((group) => group.label).join(", ")}. </>}
      {late.map((topic) => `«${auditEventName(topic.topic)}» ждёт ${ago(topic.age_seconds)}`).join(", ")}
    </div>}
    <div className="operations-bg-row operations-bg-head" aria-hidden="true">
      <span>Процесс</span><span>Состояние</span><span>Последний успех</span><span>Прогон</span><span>Последняя ошибка</span>
    </div>
    {groups.map((group) => {
      const [label, tone] = STATUS[group.status];
      return <div key={group.key} className="operations-bg-row">
        <strong>{group.label}</strong>
        <span className={tone}>{label}{group.failures > 0 ? ` · ${group.failures} подряд` : ""}</span>
        <span>{lastSuccess(group)}</span>
        <span>{group.success_at || group.failure_at ? runTime(group.duration_ms) : "—"}</span>
        <span className="operations-bg-error">{group.error ? <>{group.failure_at ? `${formatMoment(group.failure_at)} · ` : ""}{group.error}</> : "—"}</span>
      </div>;
    })}
    <p className="operations-bg-queue">
      {health.oldest_pending_seconds == null
        ? "Очередь пуста."
        : <>Дольше всех в очереди ждёт: <span className={late.length ? "tone-danger" : undefined}>{ago(health.oldest_pending_seconds)}</span>
          {" · "}{(health.topics ?? []).map((topic) => `${auditEventName(topic.topic)} — ${topic.count}`).join(", ")}</>}
    </p>
    {(health.tls_exceptions ?? []).length > 0 && <div className="warning-inline">
      Сайты поставщиков без проверки сертификата: {(health.tls_exceptions ?? []).join(", ")}
    </div>}
  </article>;
}

export function OperationsPage() {
  const [events, setEvents] = useState<AuditItem[]>([]);
  const [health, setHealth] = useState<OutboxHealth>(EMPTY_HEALTH);
  const [module, setModule] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState("");

  async function load(prefix: string) {
    setError("");
    setModule(prefix);
    try {
      const [audit, outbox] = await Promise.all([operationsApi.audit(prefix ? `${prefix}.` : ""), operationsApi.outbox()]);
      setEvents(audit.events);
      setHealth(outbox);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить системный журнал");
    } finally {
      setLoaded(true);
    }
  }

  async function retry(id: string) {
    setRetrying(id);
    setError("");
    try {
      await operationsApi.retryOutbox(id);
      await load(module);
    } catch (reason) {
      setError(failureText(reason, "Не удалось повторить задание"));
    } finally {
      setRetrying("");
    }
  }

  useEffect(() => { void load(""); }, []);

  return <section className="page operations-page">
    <header className="page-head"><h1>Системный журнал</h1><span className="page-count">{events.length}</span>
      <span className="operations-queue">
        Фоновые задания: выполнено {health.published} из {health.total} · в очереди {health.pending}
        {" · "}<span className={health.dead ? "tone-danger" : undefined}>остановлено {health.dead}</span>
      </span>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <BackgroundPanel health={health} />
    {health.failures.length > 0 && <article className="panel operations-failures"><h2>Ошибки фоновых заданий</h2>{health.failures.map((failure) => <div key={failure.id}>
      <strong>{auditEventName(failure.topic)}</strong>
      <span>{failure.error}</span>
      <small>{failure.attempts} попыток{failure.dead ? " · остановлено" : " · будет повтор"}</small>
      {failure.dead && health.can_retry
        ? <button type="button" className="secondary small" disabled={retrying === failure.id} onClick={() => void retry(failure.id)}>Повторить</button>
        : <span />}
    </div>)}</article>}
    <div className="calendar-chips" role="group" aria-label="Раздел">
      {[["", "Все"] as [string, string], ...AUDIT_MODULES].map(([prefix, label]) =>
        <button key={prefix} type="button" className={`calendar-chip${module === prefix ? " active" : ""}`} aria-pressed={module === prefix} onClick={() => void load(prefix)}>{label}</button>)}
    </div>
    {loaded && <DataTable label="Системный журнал" rows={events} columns={COLUMNS} rowKey={(event) => event.id} storageKey="operations-audit" exportName="Системный журнал" emptyText="Событий в этом разделе нет." />}
    {!loaded && <div className="empty">Загрузка…</div>}
  </section>;
}
