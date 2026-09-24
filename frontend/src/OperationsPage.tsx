import { useEffect, useState } from "react";
import { operationsApi, type AuditItem, type OutboxHealth } from "./api";

const EMPTY_HEALTH: OutboxHealth = { total: 0, published: 0, pending: 0, dead: 0, failures: [] };

export function OperationsPage() {
  const [events, setEvents] = useState<AuditItem[]>([]);
  const [health, setHealth] = useState<OutboxHealth>(EMPTY_HEALTH);
  const [eventType, setEventType] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  async function load(filter = eventType) {
    setError("");
    try {
      const [audit, outbox] = await Promise.all([operationsApi.audit(filter), operationsApi.outbox()]);
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
    <header className="page-head"><h1>Системный журнал</h1><span className="page-count">{events.length}</span></header>
    <p className="page-sub">Неизменяемые бизнес-события и состояние фоновой очереди без минутного owner-тиковика.</p>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="operations-metrics">
      <Metric label="Всего сообщений" value={health.total} />
      <Metric label="Обработано" value={health.published} />
      <Metric label="В очереди" value={health.pending} />
      <Metric label="В dead-letter" value={health.dead} danger={health.dead > 0} />
    </div>
    {health.failures.length > 0 && <article className="panel operations-failures"><h2>Ошибки фоновых заданий</h2>{health.failures.map((failure) => <div key={failure.id}><strong>{failure.topic}</strong><span>{failure.error}</span><small>{failure.attempts} попыток{failure.dead ? " · остановлено" : " · будет повтор"}</small></div>)}</article>}
    <div className="filters reference-filters"><input className="search" aria-label="Фильтр типа события" placeholder="Например: shipping или request.created" value={eventType} onChange={(event) => setEventType(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void load(); }} /><button className="secondary" onClick={() => void load()}>Найти</button></div>
    <div className="table-wrap"><table className="grid wide"><thead><tr><th>Время</th><th>Событие</th><th>Сотрудник</th><th>Объект</th><th>Детали</th></tr></thead><tbody>{events.map((event) => <tr key={event.id}><td>{new Date(event.occurred_at).toLocaleString("ru-RU")}</td><td><strong>{event.event_type}</strong></td><td>{event.actor}<small>{event.department}</small></td><td>{event.aggregate_id}</td><td className="text">{Object.entries(event.details).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") || "—"}</td></tr>)}{loaded && !events.length && <tr><td colSpan={5} className="empty">Событий по выбранному фильтру нет.</td></tr>}</tbody></table></div>
  </section>;
}

function Metric({ label, value, danger = false }: { label: string; value: number; danger?: boolean }) {
  return <div className={danger ? "metric danger" : "metric"}><span>{label}</span><strong>{value}</strong></div>;
}
