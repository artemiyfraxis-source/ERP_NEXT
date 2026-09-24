import { useEffect, useState, type FormEvent } from "react";
import { procurementApi, type ProcurementLine } from "./api";

type FactsDraft = {
  supplier: string;
  site_name: string;
  link: string;
  design_article: string;
  calculated_name: string;
  actual_quantity: string;
  actual_unit: string;
};

function draftFrom(line: ProcurementLine): FactsDraft {
  return {
    supplier: line.supplier ?? "",
    site_name: line.site_name ?? "",
    link: line.link ?? "",
    design_article: line.design_article ?? "",
    calculated_name: line.calculated_name,
    actual_quantity: line.actual_quantity ?? line.calculated_quantity,
    actual_unit: line.actual_unit ?? line.calculated_unit,
  };
}

export function ProcurementPage() {
  const [available, setAvailable] = useState<ProcurementLine[]>([]);
  const [mine, setMine] = useState<ProcurementLine[]>([]);
  const [availableCursor, setAvailableCursor] = useState<string | null>(null);
  const [mineCursor, setMineCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState<"available" | "mine" | "">("");
  const loadingQueue = refreshing || Boolean(loadingMore);
  const interactionLocked = Boolean(busyId) || loadingQueue;

  async function reload(afterMutation = false) {
    if (loadingQueue || (busyId && !afterMutation)) return;
    setRefreshing(true);
    setError("");
    try {
      const [availableResult, mineResult] = await Promise.all([
        procurementApi.available(),
        procurementApi.mine(),
      ]);
      setAvailable(availableResult.lines);
      setMine(mineResult.lines);
      setAvailableCursor(availableResult.next_cursor);
      setMineCursor(mineResult.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка загрузки закупки");
    } finally {
      setRefreshing(false);
    }
  }

  async function loadMore(queue: "available" | "mine", cursor: string) {
    if (interactionLocked) return;
    setLoadingMore(queue);
    setError("");
    try {
      const result = await procurementApi[queue](cursor);
      if (queue === "available") {
        setAvailable((current) => [
          ...current,
          ...result.lines.filter((line) => !current.some((item) => item.id === line.id)),
        ]);
        setAvailableCursor(result.next_cursor);
      } else {
        setMine((current) => [
          ...current,
          ...result.lines.filter((line) => !current.some((item) => item.id === line.id)),
        ]);
        setMineCursor(result.next_cursor);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка загрузки закупки");
    } finally {
      setLoadingMore("");
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function take(line: ProcurementLine) {
    if (interactionLocked) return;
    setBusyId(line.id);
    setError("");
    try {
      await procurementApi.take([line.id]);
      setNotice(`${line.legacy_id} взята в работу`);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось взять строку");
    } finally {
      setBusyId("");
    }
  }

  async function save(line: ProcurementLine, draft: FactsDraft) {
    if (interactionLocked) return;
    setBusyId(line.id);
    setError("");
    try {
      const facts = Object.fromEntries(
        Object.entries(draft).map(([key, value]) => [key, value.trim() || null]),
      );
      await procurementApi.save(line.id, { version: line.version, ...facts });
      setNotice(`${line.legacy_id} сохранена`);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить строку");
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  async function advance(
    line: ProcurementLine,
    operation: () => Promise<unknown>,
    message: string,
  ) {
    if (interactionLocked) return;
    setBusyId(line.id);
    setError("");
    try {
      await operation();
      setNotice(message);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Переход закупки не выполнен");
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Рабочая очередь</span>
          <h1>Закупка</h1>
          <p>Разберите новые позиции и заполните фактические данные поставки.</p>
        </div>
        <button className="secondary" onClick={() => void reload()} disabled={interactionLocked}>
          {refreshing ? "Обновляем…" : "Обновить"}
        </button>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="queue-summary">
        <span><strong>{available.length}</strong> доступно</span>
        <span><strong>{mine.length}</strong> у меня</span>
      </div>

      <section className="panel table-panel">
        <div className="panel-heading"><h2>Неразобранные</h2><span>Берите только то, что будете вести</span></div>
        <div className="table-scroll">
          <table>
            <thead><tr><th>ID</th><th>Позиция</th><th>Проект</th><th>Срок</th><th>Кол-во</th><th /></tr></thead>
            <tbody>
              {available.map((line) => (
                <tr key={line.id}>
                  <td><strong>{line.legacy_id}</strong><small>рев. {line.revision}</small></td>
                  <td>{line.calculated_name}<small>{line.designation}</small></td>
                  <td>{line.project_reference || "—"}<small>{line.delivery_point}</small></td>
                  <td>{line.planned_date}<small>{line.priority}</small></td>
                  <td>{line.calculated_quantity} {line.calculated_unit}</td>
                  <td><button className="secondary small" disabled={interactionLocked} onClick={() => take(line)}>{busyId === line.id ? "Берём…" : "Взять"}</button></td>
                </tr>
              ))}
              {!available.length && <tr><td className="empty" colSpan={6}>Свободных позиций нет</td></tr>}
            </tbody>
          </table>
        </div>
        {availableCursor && (
          <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("available", availableCursor)}>
            {loadingMore === "available" ? "Загружаем…" : "Ещё неразобранные"}
          </button>
        )}
      </section>

      <section className="work-list">
        <div className="panel-heading"><h2>Мои позиции</h2><span>Изменения защищены версией строки</span></div>
        {mine.map((line) => (
          <ProcurementCard
            key={`${line.id}:${line.version}`}
            line={line}
            busy={busyId === line.id}
            disabled={interactionLocked}
            onSave={save}
            onAdvance={advance}
          />
        ))}
        {!mine.length && <div className="panel empty">Пока ничего не взято в работу.</div>}
        {mineCursor && (
          <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("mine", mineCursor)}>
            {loadingMore === "mine" ? "Загружаем…" : "Ещё мои"}
          </button>
        )}
      </section>
    </section>
  );
}

function ProcurementCard({
  line,
  busy,
  disabled,
  onSave,
  onAdvance,
}: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onSave: (line: ProcurementLine, draft: FactsDraft) => Promise<void>;
  onAdvance: (
    line: ProcurementLine,
    operation: () => Promise<unknown>,
    message: string,
  ) => Promise<void>;
}) {
  const [draft, setDraft] = useState(() => draftFrom(line));
  const [requestReference, setRequestReference] = useState(line.supplier_request_reference ?? "");
  const [invoiceUrl, setInvoiceUrl] = useState(line.invoice_url ?? "");
  const [expectedArrival, setExpectedArrival] = useState(line.expected_arrival ?? "");

  function field(name: keyof FactsDraft, value: string) {
    setDraft((current) => ({ ...current, [name]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void onSave(line, draft).catch(() => undefined);
  }

  return (
    <article className="panel procurement-card">
      <div className="procurement-title">
        <div><strong>{line.legacy_id} · {line.calculated_name}</strong><small>{line.designation} · {line.calculated_quantity} {line.calculated_unit}</small></div>
        <span className="status">{line.status}</span>
      </div>
      {line.status === "В работе" && <form onSubmit={submit}><div className="facts-grid">
        <label>Поставщик *<input value={draft.supplier} onChange={(e) => field("supplier", e.target.value)} required /></label>
        <label>Площадка<input value={draft.site_name} onChange={(e) => field("site_name", e.target.value)} /></label>
        <label>Фактическое количество *<input type="number" min="0.000001" step="any" value={draft.actual_quantity} onChange={(e) => field("actual_quantity", e.target.value)} required /></label>
        <label>Единица *<input value={draft.actual_unit} onChange={(e) => field("actual_unit", e.target.value)} required /></label>
        <label>Артикул<input value={draft.design_article} onChange={(e) => field("design_article", e.target.value)} /></label>
        <label>Уточнённое наименование<input value={draft.calculated_name} onChange={(e) => field("calculated_name", e.target.value)} /></label>
        <label className="wide">Ссылка<input type="url" value={draft.link} onChange={(e) => field("link", e.target.value)} placeholder="https://…" /></label>
      </div>
      <div className="card-footer"><small>Версия {line.version} · план {line.planned_date}</small><button className="primary small" disabled={disabled}>{busy ? "Сохраняем…" : "Сохранить"}</button></div></form>}
      {line.status === "Готов к запросу" && (
        <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onAdvance(line, () => procurementApi.sendSupplierRequest(line.id, line.version, requestReference), `${line.legacy_id}: запрос поставщику зафиксирован`).catch(() => undefined); }}>
          <label>Номер или ссылка запроса поставщику<input required value={requestReference} onChange={(event) => setRequestReference(event.target.value)} /></label>
          <button className="primary small" disabled={disabled}>{busy ? "Отправляем…" : "Запрос отправлен"}</button>
        </form>
      )}
      {line.status === "Жду счет" && (
        <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onAdvance(line, () => procurementApi.recordInvoice(line.id, line.version, invoiceUrl, expectedArrival), `${line.legacy_id}: счёт передан в оплату`).catch(() => undefined); }}>
          <label>Ссылка на счёт<input type="url" required value={invoiceUrl} onChange={(event) => setInvoiceUrl(event.target.value)} /></label>
          <label>Ожидаемая дата<input type="date" required value={expectedArrival} onChange={(event) => setExpectedArrival(event.target.value)} /></label>
          <button className="primary small" disabled={disabled}>{busy ? "Сохраняем…" : "В оплату"}</button>
        </form>
      )}
      {line.status === "В оплату" && (
        <div className="card-footer"><small>{line.invoice_url} · ожидается {line.expected_arrival}</small><button className="primary small" disabled={disabled} onClick={() => void onAdvance(line, () => procurementApi.markPaid(line.id, line.version), `${line.legacy_id}: оплачено, передано в приёмку`).catch(() => undefined)}>{busy ? "Проводим…" : "Оплачено → в пути"}</button></div>
      )}
      {line.status === "В пути" && <div className="success-banner">Позиция передана в приёмку · ожидается {line.expected_arrival}</div>}
    </article>
  );
}
