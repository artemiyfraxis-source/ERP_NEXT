import { useEffect, useState } from "react";
import { operationsApi, type AuditItem, type OutboxHealth } from "./api";
import { AUDIT_MODULES, auditDetails, auditEventName, auditModule } from "./auditLabels";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";

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

export function OperationsPage() {
  const [events, setEvents] = useState<AuditItem[]>([]);
  const [health, setHealth] = useState<OutboxHealth>(EMPTY_HEALTH);
  const [module, setModule] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

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

  useEffect(() => { void load(""); }, []);

  return <section className="page operations-page">
    <header className="page-head"><h1>Системный журнал</h1><span className="page-count">{events.length}</span>
      <span className="operations-queue">
        Фоновые задания: выполнено {health.published} из {health.total} · в очереди {health.pending}
        {" · "}<span className={health.dead ? "tone-danger" : undefined}>остановлено {health.dead}</span>
      </span>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {health.failures.length > 0 && <article className="panel operations-failures"><h2>Ошибки фоновых заданий</h2>{health.failures.map((failure) => <div key={failure.id}><strong>{auditEventName(failure.topic)}</strong><span>{failure.error}</span><small>{failure.attempts} попыток{failure.dead ? " · остановлено" : " · будет повтор"}</small></div>)}</article>}
    <div className="calendar-chips" role="group" aria-label="Раздел">
      {[["", "Все"] as [string, string], ...AUDIT_MODULES].map(([prefix, label]) =>
        <button key={prefix} type="button" className={`calendar-chip${module === prefix ? " active" : ""}`} aria-pressed={module === prefix} onClick={() => void load(prefix)}>{label}</button>)}
    </div>
    {loaded && <DataTable label="Системный журнал" rows={events} columns={COLUMNS} rowKey={(event) => event.id} storageKey="operations-audit" exportName="Системный журнал" emptyText="Событий в этом разделе нет." />}
    {!loaded && <div className="empty">Загрузка…</div>}
  </section>;
}
