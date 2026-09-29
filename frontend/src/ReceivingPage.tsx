import { useEffect, useState, type FormEvent } from "react";
import { receivingApi, type ReceivingLine } from "./api";

const MANUAL_CHECKS = [
  "Механическое повреждение / брак",
  "Не тот товар (несоответствие счету)",
];

export function ReceivingPage() {
  const [lines, setLines] = useState<ReceivingLine[]>([]);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  async function reload(after?: string) {
    if (loading || busy) return;
    setLoading(true);
    setError("");
    try {
      const result = await receivingApi.list(after);
      setLines((current) => after
        ? [...new Map([...current, ...result.lines].map((line) => [line.id, line])).values()]
        : result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить приёмку");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function inspect(line: ReceivingLine, payload: Record<string, unknown>) {
    if (busy || loading) return;
    setBusy(line.id);
    setError("");
    try {
      await receivingApi.inspect(line.id, { version: line.version, ...payload });
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Входной контроль не выполнен");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  async function place(line: ReceivingLine, location: string) {
    if (busy || loading) return;
    setBusy(line.id);
    setError("");
    try {
      await receivingApi.place(line.id, line.version, location);
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Размещение не выполнено");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  async function openVkRevision(line: ReceivingLine) {
    if (busy || loading) return;
    if (!window.confirm(`Строка ${line.legacy_id} уйдёт в «Архив ВК» (акты сохранятся как история). Появится ревизия с контекстом заказа и УПД — по ней проведёте приёмку заново. Продолжить?`)) return;
    setBusy(line.id);
    setError("");
    try {
      await receivingApi.openVkRevision(line.id, line.version);
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Новый ВК не открыт");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div><span className="eyebrow">Входной контроль</span><h1>Приёмка</h1><p>Проверка поставки, ГРН и передача на склад.</p></div>
        <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => void reload()}>{loading ? "Обновляем…" : "Обновить"}</button>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="queue-summary"><span><strong>{lines.length}</strong> позиций</span><span><strong>{lines.filter((line) => line.status === "Принято").length}</strong> принято</span></div>
      <section className="work-list">
        {lines.map((line) => (
          <ReceivingCard
            key={`${line.id}:${line.version}`}
            line={line}
            busy={busy === line.id}
            disabled={Boolean(busy) || loading}
            onInspect={inspect}
            onPlace={place}
            onOpenVkRevision={openVkRevision}
          />
        ))}
        {!lines.length && !loading && <div className="panel empty">Поставок для приёмки пока нет.</div>}
      </section>
      {nextCursor && <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => void reload(nextCursor)}>Показать ещё</button>}
    </section>
  );
}

function ReceivingCard({
  line,
  busy,
  disabled,
  onInspect,
  onPlace,
  onOpenVkRevision,
}: {
  line: ReceivingLine;
  busy: boolean;
  disabled: boolean;
  onInspect: (line: ReceivingLine, payload: Record<string, unknown>) => Promise<void>;
  onPlace: (line: ReceivingLine, location: string) => Promise<void>;
  onOpenVkRevision: (line: ReceivingLine) => Promise<void>;
}) {
  const [fact, setFact] = useState(line.fact_quantity ?? line.ordered_quantity);
  const [documentQty, setDocumentQty] = useState(line.document_quantity ?? line.ordered_quantity);
  const [shippingDocument, setShippingDocument] = useState(line.shipping_document_url ?? "");
  const [qualityDocument, setQualityDocument] = useState(line.quality_document_url ?? "");
  const [expiry, setExpiry] = useState(line.expiry_date ?? "");
  const [disposition, setDisposition] = useState(line.disposition ?? "");
  const [manual, setManual] = useState<string[]>(
    line.nonconformities.filter((item) => MANUAL_CHECKS.includes(item)),
  );
  const [prefix, setPrefix] = useState("");
  const [historical, setHistorical] = useState(false);
  const [documentDate, setDocumentDate] = useState("");
  const [series, setSeries] = useState(line.series);
  // МАССА 1 ШТ: any one of 1/10/100 pieces; the server prefills the one it was likely weighed with.
  const [mass, setMass] = useState(line.mass_fields ?? { mass_1: "", mass_10: "", mass_100: "" });
  const [location, setLocation] = useState(line.storage_location ?? "");

  function submitInspection(event: FormEvent) {
    event.preventDefault();
    void onInspect(line, {
      fact_quantity: fact,
      document_quantity: documentQty,
      shipping_document_url: shippingDocument.trim() || null,
      quality_document_url: qualityDocument.trim() || null,
      expiry_date: expiry || null,
      manual_nonconformities: manual,
      disposition: disposition || null,
      manual_grn_prefix: prefix || null,
      series: series.trim(),
      ...mass,
      historical,
      document_date: historical && documentDate ? documentDate : null,
    }).catch(() => undefined);
  }

  const inspectable = ["В пути", "Ожидание", "Карантин"].includes(line.status);
  const revisable = Boolean(line.verdict) && line.status !== "Архив ВК" && !line.warehouse_event_emitted;
  return (
    <article className="panel receiving-card">
      <div className="procurement-title">
        <div><strong>{line.legacy_id} · {line.article}</strong><small>{line.name ? `${line.name} · ` : ""}{line.supplier} · заказано {line.ordered_quantity} {line.unit} · {line.warehouse}</small></div>
        <span className="status">{line.status}</span>
      </div>
      {line.nonconformities.length > 0 && <p className="nonconformity">{line.nonconformities.join(" · ")}</p>}
      {Number(line.backorder_quantity) > 0 && <p className="nonconformity">Недовоз {line.backorder_quantity} {line.unit} — оплаченный остаток ушёл в закупку дозавозом</p>}
      {inspectable && (
        <form onSubmit={submitInspection}>
          <div className="facts-grid">
            <label>Количество по УПД/М-15<input type="number" min="0.000001" step="any" required value={documentQty} onChange={(event) => setDocumentQty(event.target.value)} /></label>
            <label>Фактическое количество<input type="number" min="0.000001" step="any" required value={fact} onChange={(event) => setFact(event.target.value)} /></label>
            <label>Срок годности<input type="date" value={expiry} onChange={(event) => setExpiry(event.target.value)} /></label>
            <label>Серия изготовителя<input value={series} onChange={(event) => setSeries(event.target.value)} /></label>
            <label title="Вес ОДНОЙ штуки. Для крупного: разъём, катушка, корпус.">Масса 1 шт, г<input inputMode="decimal" value={mass.mass_1} onChange={(event) => setMass((current) => ({ ...current, mass_1: event.target.value }))} /></label>
            <label title="Вес ДЕСЯТИ штук — система поделит на 10. Клеммы, наконечники, крепёж.">Масса 10 шт, г<input inputMode="decimal" value={mass.mass_10} onChange={(event) => setMass((current) => ({ ...current, mass_10: event.target.value }))} /></label>
            <label title="Вес СТА штук — самый точный способ для мелочи. Хватит любого ОДНОГО из трёх полей.">Масса 100 шт, г<input inputMode="decimal" value={mass.mass_100} onChange={(event) => setMass((current) => ({ ...current, mass_100: event.target.value }))} /></label>
            <label>Ссылка на УПД/М-15<input type="url" required value={shippingDocument} onChange={(event) => setShippingDocument(event.target.value)} /></label>
            <label>Документ качества{line.quality_document_required ? " *" : ""}<input type="url" required={line.quality_document_required} value={qualityDocument} onChange={(event) => setQualityDocument(event.target.value)} /></label>
            <label>Решение<select value={disposition} onChange={(event) => setDisposition(event.target.value)}><option value="">Ещё не принято</option><option>Изолятор</option><option>Использование по разрешению</option><option>Возврат поставщику</option></select></label>
            <label>Идентификатор ГРН<select value={prefix} onChange={(event) => setPrefix(event.target.value)}><option value="">Автоматически</option><option>CSM</option><option>PPM</option></select></label>
            <label className="check-field"><input type="checkbox" checked={historical} onChange={(event) => setHistorical(event.target.checked)} />Историческая приёмка: срок годности сверяется с датой УПД</label>
            {historical && <label>Дата УПД/М-15<input type="date" required value={documentDate} onChange={(event) => setDocumentDate(event.target.value)} /></label>}
            <fieldset className="check-field wide"><legend>Несоответствия ручной проверки</legend>{MANUAL_CHECKS.map((item) => <label key={item}><input type="checkbox" checked={manual.includes(item)} onChange={(event) => setManual((current) => event.target.checked ? [...current, item] : current.filter((value) => value !== item))} />{item}</label>)}</fieldset>
          </div>
          <button className="primary small" disabled={disabled}>{busy ? "Проводим…" : "Провести входной контроль"}</button>
        </form>
      )}
      {line.status === "Принято" && !line.warehouse_event_emitted && (
        <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onPlace(line, location).catch(() => undefined); }}>
          <label>Место на складе<input required placeholder="1F4" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
          <button className="primary small" disabled={disabled}>{busy ? "Передаём…" : "Передать на склад"}</button>
        </form>
      )}
      {revisable && (
        <button className="secondary small" disabled={disabled} onClick={() => void onOpenVkRevision(line)}>Провести новый ВК</button>
      )}
      {line.status === "Архив ВК" && <p className="hint">Архив ВК: акты этой строки — история, приёмка идёт по ревизии.</p>}
      {line.warehouse_event_emitted && <div className="success-banner">Передано на склад: {line.grn} · {line.storage_location}</div>}
    </article>
  );
}
