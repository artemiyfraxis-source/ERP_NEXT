import { useCallback, useEffect, useMemo, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import {
  decisionViolations,
  quoteCell,
  rfqApi,
  unasked,
  type DecisionField,
  type ImportedQuoteLine,
  type PastedRow,
  type QuoteLineInput,
  type RfqCell,
  type RfqLine,
  type RfqMatrix,
  type RfqSummary,
  type RfqSupplier,
  type SuggestResult,
  type SupplierBrief,
} from "./purchasingRfqApi";
import { useStageOption } from "./StageOptions";
import { picksOf, RfqPool, suggestionKey, SuggestionList } from "./RfqPool";
import { rfqLinkState, syncRfqUrl } from "./rfqLinks";

/** «Запросы КП» (stage option procurement.rfq, as Roller_Buyer RfqPage): the buyer's lines → a request to several
 *  suppliers → their quotes in a matrix compared with VAT by what covering the need costs → the choice with the
 *  reasons the gate asks for → supplier and quantity go into the procurement line. */

const number = (value: string | null | undefined, digits = 2) =>
  value === null || value === undefined || value === ""
    ? "—"
    : Number(value).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const money = (value: string | null | undefined) => (value === null || value === undefined ? "—" : `${number(value)} ₽`);
const quantity = (value: string | null | undefined) =>
  value === null || value === undefined ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 6 });
const decimalInput = (value: string) => value.replace(/\s/g, "").replace(",", ".");
const today = () => {
  const now = new Date();
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

const REASONS: Record<string, string> = {
  key: "ключевой", category: "по категории", rotation: "давно не спрашивали", plan: "указан в строке", manual: "вручную",
};
const GATE_PRESETS = ["Срочно", "Единственный производитель или поставщик", "Повторная закупка по свежему КП"];
const PRICIER_PRESETS = ["Срок", "Наличие", "Минимальная партия", "Качество или происхождение"];

// Sticky first columns of the matrix: Позиция, Артикул, Кол-во, Бюджет с НДС, КП.
// The article sits under the name: one sticky column less leaves room for the suppliers on a 16" screen.
const STICKY_WIDTHS = [250, 76, 108, 64];
const STICKY_LEFT = STICKY_WIDTHS.map((_, index) => STICKY_WIDTHS.slice(0, index).reduce((sum, width) => sum + width, 0));
const sticky = (index: number, head = false): CSSProperties => ({
  position: "sticky", left: STICKY_LEFT[index], zIndex: head ? 3 : 1, minWidth: STICKY_WIDTHS[index],
  maxWidth: STICKY_WIDTHS[index], background: head ? "var(--accent)" : "var(--card)", whiteSpace: "normal",
  boxShadow: index === STICKY_WIDTHS.length - 1 ? "1px 0 0 var(--border)" : undefined,
});
const groupEdge: CSSProperties = { borderLeft: "1px solid var(--border)" };
const chip = (tone: "ok" | "warn" | "neutral"): CSSProperties => ({
  display: "inline-block", borderRadius: 999, padding: "1px 7px", fontSize: 11, fontWeight: 600,
  color: `var(--tone-${tone === "neutral" ? "ok" : tone})`,
  background: tone === "warn" ? "var(--warn-soft)" : "color-mix(in srgb, var(--tone-ok) 12%, transparent)",
});

function gateText(line: RfqLine) {
  return `КП ${line.gate.priced}/${line.gate.required}`;
}

function gateHint(line: RfqLine) {
  const parts = [`КП с ценой: ${line.gate.priced} из ${line.gate.required} нужных`];
  if (line.gate.require_non_key && !line.gate.has_non_key) parts.push("нет КП от неключевого поставщика");
  return parts.join("; ");
}

export function PurchasingRfq({ readOnly = false }: { readOnly?: boolean } = {}) {
  // `?rfq=<id>[&letters=1|&invoices=1]` (CONTRACTS.md): the request to open and the panel to open with it.
  const [link] = useState(rfqLinkState);
  const [rfqs, setRfqs] = useState<RfqSummary[] | null>(null);
  const [selected, setSelected] = useState(link.rfq);
  // The panel to open once with a request: from the link, or the letters right after «Создать запрос».
  const [panelFor, setPanelFor] = useState<{ id: string; letters: boolean; invoices: boolean } | null>(
    link.rfq ? { id: link.rfq, letters: link.letters, invoices: link.invoices } : null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async (pick?: string) => {
    try {
      const { rfqs: next } = await rfqApi.list(readOnly ? "all" : "mine");
      setRfqs(next);
      setSelected((current) => pick ?? (next.some((rfq) => rfq.id === current) ? current : next[0]?.id ?? ""));
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Запросы КП не загрузились"));
    }
  }, [readOnly]);

  useEffect(() => { void reload(link.rfq || undefined); }, [reload, link.rfq]);
  useEffect(() => { if (selected && panelFor?.id !== selected) syncRfqUrl(selected); }, [selected, panelFor]);

  if (creating) {
    // The pool takes the whole width (a sheet of lines, not a modal list); the new request's matrix opens after it.
    return (
      <section className="page">
        <RfqPool onClose={() => setCreating(false)} onCreated={(id) => {
          setCreating(false); setPanelFor({ id, letters: true, invoices: false }); void reload(id);
        }} />
      </section>
    );
  }

  return (
    <section className="page" style={{ display: "grid", gridTemplateColumns: "210px minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      <aside className="panel" style={{ padding: 12, display: "grid", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <h2 style={{ margin: 0, fontSize: 16 }}>Запросы КП</h2>
          {!readOnly && <button type="button" className="primary small" onClick={() => setCreating(true)}>+ Запрос</button>}
        </div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!error && rfqs === null && <div className="empty">Загрузка…</div>}
        {rfqs?.length === 0 && <div className="empty">Запросов нет — нажмите «+ Запрос» и отметьте строки «В работе».</div>}
        {rfqs?.map((rfq) => (
          <button
            key={rfq.id} type="button" aria-pressed={rfq.id === selected}
            className={`nav-item${rfq.id === selected ? " active" : ""}`}
            style={{ display: "grid", gap: 2, opacity: rfq.closed_at ? 0.7 : 1 }}
            onClick={() => setSelected(rfq.id)}
          >
            <strong>{rfq.number}{rfq.closed_at ? " · закрыт" : ""}</strong>
            {rfq.title && <span>{rfq.title}</span>}
            <small className="muted">
              {formatDay(rfq.created_at)} · строк {rfq.lines} · поставщиков {rfq.suppliers} · КП {rfq.quotes} · решено {rfq.decided}
            </small>
          </button>
        ))}
      </aside>
      <div style={{ minWidth: 0 }}>
        {selected
          ? <RfqMatrixView
              key={selected} id={selected} readOnly={readOnly} onChanged={() => void reload(selected)}
              openLetters={panelFor?.id === selected && panelFor.letters}
              openInvoices={panelFor?.id === selected && panelFor.invoices}
            />
          : <div className="panel empty">Выберите запрос слева или создайте новый.</div>}
      </div>
    </section>
  );
}

function Dialog({ title, wide = false, onClose, children }: { title: string; wide?: boolean; onClose: () => void; children: ReactNode }) {
  return (
    <div className="dialog-overlay" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={`panel move-dialog${wide ? " wide" : ""}`} role="dialog" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

function RfqMatrixView({ id, readOnly, onChanged, openLetters = false, openInvoices = false }: {
  id: string; readOnly: boolean; onChanged: () => void; openLetters?: boolean; openInvoices?: boolean;
}) {
  const [letters, setLetters] = useState(openLetters);
  const [invoiceLetters, setInvoiceLetters] = useState(openInvoices);
  useEffect(() => { syncRfqUrl(id, { letters, invoices: invoiceLetters }); }, [id, letters, invoiceLetters]);
  const [matrix, setMatrix] = useState<RfqMatrix | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [quoteFor, setQuoteFor] = useState<RfqSupplier | null>(null);
  const [linesFor, setLinesFor] = useState<RfqSupplier | null>(null);
  const [decisionFor, setDecisionFor] = useState<{ lineId: string; supplierId: string } | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [directory, setDirectory] = useState<SupplierBrief[]>([]);
  const [newSupplier, setNewSupplier] = useState("");

  const load = useCallback(async () => {
    try {
      setMatrix(await rfqApi.matrix(id));
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Запрос не загрузился"));
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const editable = Boolean(matrix && !readOnly && matrix.rfq.mine && !matrix.rfq.closed_at);
  useEffect(() => {
    if (!editable) return;
    rfqApi.directory().then(({ suppliers }) => setDirectory(suppliers)).catch(() => setDirectory([]));
  }, [editable]);

  const refresh = useCallback(async (message = "") => {
    await load();
    setNotice(message);
    onChanged();
  }, [load, onChanged]);

  async function act(run: () => Promise<unknown>, message: string, fallback: string) {
    setError("");
    try {
      await run();
      await refresh(message);
    } catch (reason) {
      setError(failureText(reason, fallback));
    }
  }

  async function addSupplier(event: FormEvent) {
    event.preventDefault();
    const name = newSupplier.trim();
    if (!name) return;
    const known = directory.find((supplier) => supplier.name.trim().toLowerCase() === name.toLowerCase());
    await act(() => rfqApi.addSupplier(id, known ? { supplier_id: known.supplier_id } : { name }),
      `Поставщик «${known?.name ?? name}» добавлен в запрос`, "Поставщик не добавлен");
    setNewSupplier("");
  }

  if (!matrix) {
    return error ? <div className="error-banner" role="alert">{error}</div> : <div className="panel empty">Загрузка…</div>;
  }
  const { rfq, suppliers, lines } = matrix;
  const supplierName = (supplierId: string) => suppliers.find((supplier) => supplier.supplier_id === supplierId)?.name ?? "";
  const decisionLine = decisionFor ? lines.find((line) => line.id === decisionFor.lineId) : undefined;

  return (
    <section className="panel" style={{ padding: "12px 14px", display: "grid", gap: 10 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>{rfq.number}{rfq.title ? ` · ${rfq.title}` : ""}</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          от {formatDay(rfq.created_at)} · {rfq.closed_at ? `закрыт ${formatDay(rfq.closed_at)}` : "открыт"} · нужно КП: {matrix.settings.gate_min_quotes}
          {matrix.settings.gate_require_non_key ? ", хотя бы одно не от ключевого" : ""}
        </span>
        {editable && (
          <div style={{ marginLeft: "auto", display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <button type="button" className="secondary small" onClick={() => setSuggesting(true)}>Подобрать поставщиков</button>
            <form onSubmit={(event) => void addSupplier(event)} style={{ display: "flex", gap: 6 }}>
              <input
                aria-label="Поставщик" list={`rfq-directory-${id}`} value={newSupplier} placeholder="Поставщик из справочника или новый"
                onChange={(event) => setNewSupplier(event.target.value)} style={{ width: 280 }}
              />
              <datalist id={`rfq-directory-${id}`}>
                {directory.map((supplier) => <option key={supplier.supplier_id} value={supplier.name} />)}
              </datalist>
              <button type="submit" className="secondary small" disabled={!newSupplier.trim()}>Добавить поставщика</button>
            </form>
            <button
              type="button" className="secondary small"
              onClick={() => { if (window.confirm("Закрыть запрос? Строки без решения выйдут из него и снова станут доступны для нового запроса.")) void act(() => rfqApi.close(id), "Запрос закрыт", "Запрос не закрыт"); }}
            >Закрыть запрос</button>
          </div>
        )}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      {suppliers.length === 0 && (
        <div className="empty">Поставщиков в запросе нет{editable ? " — «Подобрать поставщиков» или добавьте по названию" : ""}.</div>
      )}
      <div className="inwork-scroll" style={{ maxHeight: "68vh" }}>
        <table className="inwork-table" aria-label={`Матрица ${rfq.number}`} style={{ borderCollapse: "separate", borderSpacing: 0 }}>
          <thead>
            <tr>
              {["Позиция", "Кол-во", "Бюджет с НДС", "КП"].map((title, index) => (
                <th key={title} rowSpan={2} style={sticky(index, true)}>{title}</th>
              ))}
              {suppliers.map((supplier) => (
                <th key={supplier.supplier_id} colSpan={2} style={{ ...groupEdge, verticalAlign: "top", minWidth: 190, whiteSpace: "normal", fontWeight: 400 }}>
                  <SupplierHead
                    supplier={supplier} editable={editable} total={lines.length} onLines={() => setLinesFor(supplier)}
                    onSent={() => void act(() => rfqApi.markSent(id, supplier.supplier_id), `«${supplier.name}»: запрос отправлен`, "Не отмечено")}
                    onQuote={() => setQuoteFor(supplier)}
                    onDeclined={() => {
                      const note = window.prompt(`«${supplier.name}» отказался отвечать. Комментарий (необязательно):`, "");
                      if (note !== null) void act(() => rfqApi.markDeclined(id, supplier.supplier_id, note), `«${supplier.name}»: отказ отмечен`, "Не отмечено");
                    }}
                    onRemove={() => void act(() => rfqApi.removeSupplier(id, supplier.supplier_id), `«${supplier.name}» убран из запроса`, "Поставщик не убран")}
                  />
                </th>
              ))}
            </tr>
            <tr>
              {suppliers.map((supplier) => [
                <th key={`${supplier.supplier_id}-p`} style={groupEdge}>Цена с НДС</th>,
                <th key={`${supplier.supplier_id}-t`}>Сумма</th>,
              ])}
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id}>
                <td style={sticky(0)}>
                  <strong>{line.name}</strong>
                  <small className="muted" style={{ display: "block" }}>
                    {line.article ? `${line.article} · ` : ""}{line.legacy_id ?? ""}{line.status ? ` · ${line.status}` : ""}{line.need_by ? ` · к ${formatDay(line.need_by)}` : ""}
                  </small>
                </td>
                <td style={sticky(1)}>{quantity(line.quantity)} {line.unit}</td>
                <td style={sticky(2)}>
                  {money(line.budget_unit_gross)}
                  {line.delta_pct !== null && (
                    <small className={Number(line.delta_pct) > 0 ? "tone-danger" : "tone-ok"} style={{ display: "block" }}>
                      Δ {Number(line.delta_pct) > 0 ? "+" : ""}{number(line.delta_pct, 1)}%
                    </small>
                  )}
                </td>
                <td style={sticky(3)}>
                  <span style={chip(line.gate.passes ? "ok" : "warn")} title={gateHint(line)}>{gateText(line)}</span>
                  {line.decision && <small className="tone-ok" style={{ display: "block" }}>✓ решено</small>}
                </td>
                {suppliers.map((supplier) => (
                  <MatrixCell
                    key={supplier.supplier_id} line={line} supplier={supplier}
                    onOpen={() => setDecisionFor({ lineId: line.id, supplierId: supplier.supplier_id })}
                  />
                ))}
              </tr>
            ))}
          </tbody>
          {suppliers.length > 0 && (
            <tfoot>
              <tr>
                <td colSpan={4} style={{ ...sticky(0), maxWidth: undefined }}><strong>Выбрано у поставщика</strong></td>
                {suppliers.map((supplier) => (
                  <td key={supplier.supplier_id} colSpan={2} style={groupEdge}>{supplier.chosen_total ? money(supplier.chosen_total) : ""}</td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {matrix.quotes.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 12 }} aria-label="КП в запросе">
          <span className="muted">КП в запросе:</span>
          {matrix.quotes.map((quote) => (
            <span key={quote.id}>
              {supplierName(quote.supplier_id)}{quote.supplier_is_key ? " ★" : ""} от {formatDay(quote.received_on)}
              {quote.valid_until ? `, до ${formatDay(quote.valid_until)}` : ""}{quote.source === "paste" ? ", из Excel" : quote.source === "file" ? ", из PDF" : ""}
              {editable && !quote.used && (
                <button
                  type="button" className="link-button" style={{ padding: "0 0 0 6px" }}
                  onClick={() => { if (window.confirm("Удалить это КП?")) void act(() => rfqApi.deleteQuote(quote.id), "КП удалено", "КП не удалено"); }}
                >удалить</button>
              )}
            </span>
          ))}
        </div>
      )}
      {quoteFor && (
        <QuoteDialog
          matrix={matrix} supplier={quoteFor} onClose={() => setQuoteFor(null)}
          onSaved={() => { setQuoteFor(null); void refresh(`КП «${quoteFor.name}» внесено`); }}
        />
      )}
      {linesFor && (
        <SupplierLinesDialog
          matrix={matrix} supplier={linesFor} onClose={() => setLinesFor(null)}
          onSaved={() => { setLinesFor(null); void refresh(`«${linesFor.name}»: позиции запроса изменены`); }}
        />
      )}
      {decisionFor && decisionLine && (
        <DecisionPanel
          matrix={matrix} line={decisionLine} supplierId={decisionFor.supplierId} editable={editable}
          onClose={() => setDecisionFor(null)}
          onDone={(message) => { setDecisionFor(null); void refresh(message); }}
        />
      )}
      {suggesting && (
        <SuggestDialog
          rfqId={id} onClose={() => setSuggesting(false)}
          onDone={(count) => { setSuggesting(false); void refresh(count ? `Добавлено поставщиков: ${count}` : ""); }}
        />
      )}
    </section>
  );
}

function SupplierHead({ supplier, editable, total, onLines, onSent, onQuote, onDeclined, onRemove }: {
  supplier: RfqSupplier; editable: boolean; total: number; onLines: () => void; onSent: () => void; onQuote: () => void;
  onDeclined: () => void; onRemove: () => void;
}) {
  const state = supplier.declined_at
    ? `отказался${supplier.decline_note ? `: ${supplier.decline_note}` : ""}`
    : supplier.answered ? "КП получено" : supplier.sent_at ? `отправлено ${formatDay(supplier.sent_at)}` : "не отправлено";
  return (
    <div style={{ display: "grid", gap: 2, textTransform: "none", letterSpacing: 0, fontSize: 12 }}>
      <strong style={{ fontSize: 13, color: "var(--foreground)" }} title={supplier.is_key ? "Ключевой поставщик" : undefined}>
        {supplier.name}{supplier.is_key ? " ★" : ""}
      </strong>
      <span className={supplier.declined_at ? "tone-danger" : supplier.answered ? "tone-ok" : "muted"}>{state} · {REASONS[supplier.reason] ?? ""}</span>
      <span className="muted">
        позиций: {supplier.line_ids?.length ?? total} из {total}
        {editable && <>
          {" · "}
          <button type="button" className="link-button" style={{ padding: 0, fontSize: 12 }} aria-label={`Изменить позиции ${supplier.name}`} onClick={onLines}>изменить</button>
        </>}
      </span>
      {(supplier.delivery_cost || supplier.min_order_sum) && (
        <span className="muted">
          {supplier.delivery_cost ? `доставка ${money(supplier.delivery_cost)}` : ""}
          {supplier.delivery_cost && supplier.min_order_sum ? " · " : ""}
          {supplier.min_order_sum ? `мин. заказ ${money(supplier.min_order_sum)}` : ""}
        </span>
      )}
      {supplier.basket_total && (
        <span title="Все позиции с ценой у этого поставщика, с доставкой">всё у него: <strong style={{ display: "inline" }}>{money(supplier.basket_total)}</strong> ({supplier.basket_lines} поз.)</span>
      )}
      {supplier.min_order_warning && <span className="tone-warn">{supplier.min_order_warning}</span>}
      {editable && (
        <span style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
          {!supplier.sent_at && <button type="button" className="secondary small" style={{ padding: "2px 8px" }} onClick={onSent}>Отправлено</button>}
          <button type="button" className="secondary small" style={{ padding: "2px 8px" }} onClick={onQuote} aria-label={`+ КП ${supplier.name}`}>+ КП</button>
          {!supplier.answered && !supplier.declined_at && <button type="button" className="link-button" style={{ padding: 0 }} onClick={onDeclined}>отказ</button>}
          {!supplier.answered && <button type="button" className="link-button" style={{ padding: 0 }} onClick={onRemove}>убрать</button>}
        </span>
      )}
    </div>
  );
}

function cellHints(cell: RfqCell): string[] {
  const hints: string[] = [];
  if (cell.moq) hints.push(`мин. ${quantity(cell.moq)}`);
  if (cell.pack) hints.push(`уп. ${quantity(cell.pack)}`);
  if (cell.in_stock) hints.push("в наличии");
  else if (cell.lead_days !== null) hints.push(`${cell.lead_days} дн`);
  if (cell.analog) hints.push("аналог");
  if (cell.expired) hints.push("срок КП истёк");
  return hints;
}

function MatrixCell({ line, supplier, onOpen }: { line: RfqLine; supplier: RfqSupplier; onOpen: () => void }) {
  if (unasked(line, supplier.supplier_id)) {
    return (
      <td colSpan={2} style={{ ...groupEdge, color: "var(--muted-foreground)", fontSize: 12 }} title="Эту позицию у поставщика не запрашивали">
        не запрашивали
      </td>
    );
  }
  const cell = quoteCell(line, supplier.supplier_id);
  if (!cell) {
    return <>
      <td style={{ ...groupEdge, color: "var(--muted-foreground)" }}>—</td>
      <td style={{ color: "var(--muted-foreground)" }}>—</td>
    </>;
  }
  const background = cell.decided
    ? "var(--sheet-selected)"
    : cell.cheapest ? "color-mix(in srgb, var(--tone-ok) 10%, transparent)" : undefined;
  const dim = cell.expired || cell.declined;
  const style: CSSProperties = { background, cursor: "pointer", color: dim ? "var(--muted-foreground)" : undefined, whiteSpace: "nowrap" };
  const label = `${line.name} — ${supplier.name}`;
  const open = () => onOpen();
  const raisedQty = cell.cover_qty !== null && Number(cell.cover_qty) !== Number(line.quantity);
  return <>
    <td
      style={{ ...style, ...groupEdge }} role="button" tabIndex={0} aria-label={label}
      onClick={open} onKeyDown={(event) => { if (event.key === "Enter") open(); }}
    >
      {cell.declined ? <span className="tone-danger">отказ</span> : <strong style={{ display: "inline" }}>{number(cell.gross_unit)}</strong>}
      {cell.cheapest && <span style={{ ...chip("ok"), marginLeft: 4 }}>мин</span>}
      {cellHints(cell).length > 0 && <small className="muted" style={{ display: "block" }}>{cellHints(cell).join(" · ")}</small>}
    </td>
    <td style={style} onClick={open}>
      {cell.decided && <span className="tone-ok" aria-label="выбрано">✓ </span>}
      {cell.declined ? "" : number(cell.total)}
      {raisedQty && <small className="muted" style={{ display: "block" }}>за {quantity(cell.cover_qty)} {line.unit}</small>}
    </td>
  </>;
}

function DecisionPanel({ matrix, line, supplierId, editable, onClose, onDone }: {
  matrix: RfqMatrix; line: RfqLine; supplierId: string; editable: boolean; onClose: () => void; onDone: (message: string) => void;
}) {
  const [chosenId, setChosenId] = useState(supplierId);
  const [gateReason, setGateReason] = useState("");
  const [pricierReason, setPricierReason] = useState("");
  const [analogBy, setAnalogBy] = useState("");
  const [serverFields, setServerFields] = useState<DecisionField[]>([]);
  const [messages, setMessages] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const chosen = quoteCell(line, chosenId);
  const chosenSupplier = matrix.suppliers.find((supplier) => supplier.supplier_id === chosenId);
  const decidedSupplier = line.decision ? matrix.suppliers.find((supplier) => supplier.supplier_id === line.decision?.supplier_id) : undefined;
  const choosable = Boolean(chosen && !chosen.declined && !chosen.expired && chosen.gross_unit !== null);
  const required = useMemo(
    () => new Set<DecisionField>([...(chosen?.requires ?? []), ...serverFields]),
    [chosen, serverFields],
  );
  const missing = (required.has("gate_override_reason") && !gateReason.trim())
    || (required.has("not_cheapest_reason") && !pricierReason.trim())
    || (required.has("analog_approved_by") && !analogBy.trim());
  const offers = matrix.suppliers.flatMap((supplier) => {
    const cell = quoteCell(line, supplier.supplier_id);
    return cell ? [{ supplier, cell }] : [];
  });

  async function decide() {
    if (!chosen) return;
    setBusy(true);
    setError("");
    setMessages([]);
    try {
      await rfqApi.decide(matrix.rfq.id, {
        rfq_line_id: line.id, quote_line_id: chosen.quote_line_id, expected_version: line.version,
        ...(required.has("gate_override_reason") ? { gate_override_reason: gateReason.trim() } : {}),
        ...(required.has("not_cheapest_reason") ? { not_cheapest_reason: pricierReason.trim() } : {}),
        ...(required.has("analog_approved_by") ? { analog_approved_by: analogBy.trim() } : {}),
      });
      onDone(`${line.legacy_id ?? line.name}: выбран «${chosenSupplier?.name ?? ""}» — поставщик и количество записаны в строку закупки`);
    } catch (reason) {
      const violations = decisionViolations(reason);
      if (violations.length > 0) {
        setServerFields(violations.map((violation) => violation.field));
        setMessages(violations.map((violation) => violation.message));
      } else {
        setError(failureText(reason, "Решение не сохранено"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!line.decision) return;
    setBusy(true);
    setError("");
    try {
      await rfqApi.undoDecision(matrix.rfq.id, line.decision.id);
      onDone(`${line.legacy_id ?? line.name}: решение отменено, данные строки закупки остались как были`);
    } catch (reason) {
      setError(failureText(reason, "Решение не отменено"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title={`${line.name}: выбор поставщика`} wide onClose={onClose}>
      <p className="page-sub">
        Нужно {quantity(line.quantity)} {line.unit} · бюджет {money(line.budget_unit_gross)} за {line.unit} ·{" "}
        <span style={chip(line.gate.passes ? "ok" : "warn")}>{gateText(line)}</span> {line.gate.passes ? "" : gateHint(line)}
      </p>
      <div className="inwork-scroll">
        <table className="inwork-table">
          <thead>
            <tr><th>Поставщик</th><th>Цена с НДС</th><th>Берём</th><th>Сумма</th><th>Срок</th><th>Отметки</th></tr>
          </thead>
          <tbody>
            {offers.map(({ supplier, cell }) => {
              const marks = [
                cell.cheapest && "самое дешёвое", cell.baseline && "база экономии", cell.decided && "выбрано",
                cell.analog && `аналог${cell.analog_note ? `: ${cell.analog_note}` : ""}`, cell.expired && "срок КП истёк",
                cell.declined && "отказ",
              ].filter(Boolean).join(", ");
              const active = supplier.supplier_id === chosenId;
              return (
                <tr
                  key={supplier.supplier_id} aria-selected={active}
                  style={{ cursor: "pointer", background: active ? "var(--sheet-selected)" : undefined }}
                  onClick={() => { setChosenId(supplier.supplier_id); setServerFields([]); setMessages([]); }}
                >
                  <td><strong>{supplier.name}{supplier.is_key ? " ★" : ""}</strong></td>
                  <td>{cell.declined ? "—" : money(cell.gross_unit)}</td>
                  <td>{cell.cover_qty ? `${quantity(cell.cover_qty)} ${line.unit}` : "—"}</td>
                  <td>{cell.declined ? "—" : money(cell.total)}</td>
                  <td>{cell.in_stock ? "в наличии" : cell.lead_days !== null ? `${cell.lead_days} дн` : ""}</td>
                  <td>{marks}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {line.decision ? (
        <div style={{ display: "grid", gap: 6 }}>
          <p className="page-sub">
            Выбран «{decidedSupplier?.name ?? ""}»: {quantity(line.decision.cover_quantity)} {line.unit} на {money(line.decision.total_gross)}
            {line.decision.applied_at ? `, записано в строку закупки ${formatDay(line.decision.applied_at)}` : ""}.
            {line.decision.gate_override_reason ? ` Обход: ${line.decision.gate_override_reason}.` : ""}
            {line.decision.not_cheapest_reason ? ` Не самое дешёвое: ${line.decision.not_cheapest_reason}.` : ""}
            {line.decision.analog_approved_by ? ` Аналог согласован: ${line.decision.analog_approved_by}.` : ""}
          </p>
          {editable && line.editable && (
            <div className="form-actions">
              <span className="muted" style={{ marginRight: "auto", fontSize: 12 }}>При отмене поставщик и количество в строке закупки останутся как есть.</span>
              <button type="button" className="secondary" disabled={busy} onClick={() => void undo()}>Отменить решение</button>
            </div>
          )}
          {editable && !line.editable && <p className="muted">Строка уже «{line.status}» — решение не отменить.</p>}
        </div>
      ) : !editable ? (
        <p className="muted">Решение принимает закупщик строки в открытом запросе.</p>
      ) : !line.editable ? (
        <p className="muted">Строка уже «{line.status}» — поставщик в неё не пишется.</p>
      ) : !choosable ? (
        <p className="muted">Это предложение выбрать нельзя: {chosen?.declined ? "поставщик отказался" : chosen?.expired ? "срок КП истёк" : "нет цены"}.</p>
      ) : (
        <form className="dialog-form" onSubmit={(event) => { event.preventDefault(); void decide(); }}>
          {required.size === 0 && <p className="page-sub">Причины не нужны: КП достаточно и выбрано самое дешёвое.</p>}
          {required.has("gate_override_reason") && (
            <ReasonField label="Причина обхода" hint={gateHint(line)} presets={GATE_PRESETS} value={gateReason} onChange={setGateReason} />
          )}
          {required.has("not_cheapest_reason") && (
            <ReasonField label="Почему не самое дешёвое" presets={PRICIER_PRESETS} value={pricierReason} onChange={setPricierReason} />
          )}
          {required.has("analog_approved_by") && (
            <label>С кем согласован аналог
              <input value={analogBy} maxLength={255} onChange={(event) => setAnalogBy(event.target.value)} placeholder="ФИО конструктора" />
            </label>
          )}
          {messages.map((message) => <div key={message} className="warning-inline">{message}</div>)}
          <div className="form-actions">
            <button type="button" className="secondary" onClick={onClose}>Отмена</button>
            <button type="submit" className="primary" disabled={busy || missing}>
              Выбрать «{chosenSupplier?.name ?? ""}» — {quantity(chosen?.cover_qty)} {line.unit} на {money(chosen?.total)}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function ReasonField({ label, hint, presets, value, onChange }: {
  label: string; hint?: string; presets: string[]; value: string; onChange: (value: string) => void;
}) {
  return (
    <div style={{ display: "grid", gap: 2 }}>
      <span style={{ fontWeight: 600, fontSize: 13 }}>{label}</span>
      {hint ? <small className="muted">{hint}</small> : null}
      <span style={{ display: "flex", flexWrap: "wrap", gap: 4, margin: "4px 0" }}>
        {presets.map((preset) => (
          <button
            key={preset} type="button" className={`secondary small${value === preset ? " active" : ""}`} aria-pressed={value === preset}
            style={{ padding: "2px 8px" }} onClick={() => onChange(preset)}
          >{preset}</button>
        ))}
      </span>
      <input aria-label={label} value={value} maxLength={2000} onChange={(event) => onChange(event.target.value)} placeholder="Своими словами" />
    </div>
  );
}

type QuoteRowState = {
  price: string; vat_included: boolean; vat_rate: string; moq: string; pack: string; lead: string;
  in_stock: boolean; declined: boolean; analog: boolean; analog_note: string;
};

function QuoteDialog({ matrix, supplier, onClose, onSaved }: {
  matrix: RfqMatrix; supplier: RfqSupplier; onClose: () => void; onSaved: () => void;
}) {
  const blank = (): QuoteRowState => ({
    price: "", vat_included: true, vat_rate: matrix.settings.default_vat_rate, moq: "", pack: "", lead: "",
    in_stock: false, declined: false, analog: false, analog_note: "",
  });
  const asked = supplier.line_ids ? matrix.lines.filter((line) => supplier.line_ids?.includes(line.id)) : matrix.lines;
  const [rows, setRows] = useState<Record<string, QuoteRowState>>(() => Object.fromEntries(asked.map((line) => [line.id, blank()])));
  const [receivedOn, setReceivedOn] = useState(today());
  const [validUntil, setValidUntil] = useState("");
  const [delivery, setDelivery] = useState("");
  const [minOrder, setMinOrder] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [unmatched, setUnmatched] = useState<PastedRow[]>([]);
  const importEnabled = useStageOption("procurement.quote_import");
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importNotes, setImportNotes] = useState<string[]>([]);
  const [importUnmatched, setImportUnmatched] = useState<ImportedQuoteLine[]>([]);
  const [importUnread, setImportUnread] = useState<string[]>([]);
  // The supplier's wording per request line, remembered as an alias when the quote is saved.
  const [wording, setWording] = useState<Record<string, string>>({});
  const [source, setSource] = useState<"manual" | "paste" | "file">("manual");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const patch = (lineId: string, change: Partial<QuoteRowState>) =>
    setRows((current) => ({ ...current, [lineId]: { ...current[lineId], ...change } }));

  async function parsePaste() {
    setError("");
    try {
      const preview = await rfqApi.paste(matrix.rfq.id, pasteText, supplier.supplier_id);
      const next = { ...rows };
      for (const row of preview.rows) {
        if (!row.rfq_line_id || !next[row.rfq_line_id]) continue;
        next[row.rfq_line_id] = {
          ...next[row.rfq_line_id], price: row.price ?? "", vat_included: preview.vat_included,
          vat_rate: row.vat_rate ?? next[row.rfq_line_id].vat_rate, lead: row.lead_days === null ? "" : String(row.lead_days),
          in_stock: row.in_stock, declined: row.declined, analog: row.analog,
        };
      }
      setRows(next);
      setUnmatched(preview.rows.filter((row) => !row.rfq_line_id));
      setSource("paste");
      setPasteOpen(false);
    } catch (reason) {
      setError(failureText(reason, "Вставка не разобрана"));
    }
  }

  /** One proposed line into the form row of its request line; the buyer still reviews and saves. */
  function fillFrom(next: Record<string, QuoteRowState>, row: ImportedQuoteLine, lineId: string) {
    const current = next[lineId];
    if (!current) return;
    next[lineId] = {
      ...current, price: row.declined ? "" : row.price ?? "", vat_rate: row.vat_rate ?? current.vat_rate,
      lead: row.in_stock || row.lead_days === null ? "" : String(row.lead_days), in_stock: row.in_stock,
      declined: row.declined, analog: row.analog, analog_note: row.analog_note, moq: row.moq ?? current.moq,
      pack: row.pack ?? current.pack,
    };
  }

  const wordingOf = (row: ImportedQuoteLine) => `${row.source_article} ${row.source_name}`.trim();

  async function runImport(input: { text: string } | { file_name: string; content_base64: string }) {
    setError("");
    setImportBusy(true);
    try {
      const result = await rfqApi.importQuote(matrix.rfq.id, supplier.supplier_id, input);
      const next = { ...rows };
      const words: Record<string, string> = {};
      for (const row of result.lines) {
        if (!row.rfq_line_id || !next[row.rfq_line_id]) continue;
        fillFrom(next, row, row.rfq_line_id);
        next[row.rfq_line_id].vat_included = result.vat_included;
        if (row.matched_by !== "alias") words[row.rfq_line_id] = wordingOf(row);
      }
      setRows(next);
      setWording((current) => ({ ...current, ...words }));
      setImportNotes(result.lines.filter((row) => row.pack_note).map((row) => `«${row.source_name}»: ${row.pack_note}`));
      setImportUnmatched(result.unmatched);
      setImportUnread(result.unread);
      setSource(result.source);
      setImportOpen(false);
    } catch (reason) {
      setError(failureText(reason, "КП не разобрано"));
    } finally {
      setImportBusy(false);
    }
  }

  function importFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      setError("PDF больше 20 МБ — сожмите файл или вставьте текст");
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setError("Файл не прочитан");
    reader.onload = () => {
      const data = String(reader.result ?? "");
      void runImport({ file_name: file.name, content_base64: data.slice(data.indexOf(",") + 1) });
    };
    reader.readAsDataURL(file);
  }

  /** The buyer puts an unmatched quote line onto a request line by hand. */
  function assignUnmatched(index: number, lineId: string) {
    const row = importUnmatched[index];
    if (!row || !lineId) return;
    const next = { ...rows };
    fillFrom(next, row, lineId);
    setRows(next);
    setWording((current) => ({ ...current, [lineId]: wordingOf(row) }));
    if (row.pack_note) setImportNotes((current) => [...current, `«${row.source_name}»: ${row.pack_note}`]);
    setImportUnmatched((current) => current.filter((_, at) => at !== index));
  }

  async function save() {
    const lines: QuoteLineInput[] = asked.flatMap((line) => {
      const row = rows[line.id];
      if (!row.declined && !decimalInput(row.price)) return [];
      return [{
        rfq_line_id: line.id, unit_price: row.declined ? null : decimalInput(row.price), vat_included: row.vat_included,
        vat_rate: decimalInput(row.vat_rate) || null, moq: decimalInput(row.moq) || null, pack: decimalInput(row.pack) || null,
        lead_days: row.in_stock ? 0 : row.lead.trim() ? Number(row.lead) : null, in_stock: row.in_stock, declined: row.declined,
        analog: row.analog, analog_note: row.analog ? row.analog_note.trim() || null : null,
      }];
    });
    if (lines.length === 0) {
      setError("Заполните цену или отказ хотя бы по одной позиции");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await rfqApi.addQuote(matrix.rfq.id, {
        supplier_id: supplier.supplier_id, received_on: receivedOn || null, valid_until: validUntil || null,
        delivery_cost: decimalInput(delivery) || null, min_order_sum: decimalInput(minOrder) || null, source, lines,
        ...(importEnabled ? { aliases: lines.filter((line) => wording[line.rfq_line_id]).map((line) => ({ rfq_line_id: line.rfq_line_id, text: wording[line.rfq_line_id] })) } : {}),
      });
      onSaved();
    } catch (reason) {
      setError(failureText(reason, "КП не сохранено"));
    } finally {
      setBusy(false);
    }
  }

  const small: CSSProperties = { width: 80 };
  return (
    <Dialog title={`КП от «${supplier.name}»`} wide onClose={onClose}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "end" }}>
        <label>Получено<input type="date" value={receivedOn} onChange={(event) => setReceivedOn(event.target.value)} /></label>
        <label>Действует до<input type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
        <label>Доставка, ₽<input inputMode="decimal" value={delivery} onChange={(event) => setDelivery(event.target.value)} style={{ width: 110 }} /></label>
        <label>Мин. заказ, ₽<input inputMode="decimal" value={minOrder} onChange={(event) => setMinOrder(event.target.value)} style={{ width: 110 }} /></label>
        <button type="button" className="secondary small" onClick={() => setPasteOpen(!pasteOpen)} aria-expanded={pasteOpen}>Вставить из Excel</button>
        {importEnabled && (
          <button type="button" className="secondary small" onClick={() => setImportOpen(!importOpen)} aria-expanded={importOpen}>Вставить из PDF / текста</button>
        )}
      </div>
      {importEnabled && importOpen && (
        <div className="quote-import-panel">
          <textarea
            aria-label="Текст КП" rows={6} value={importText} onChange={(event) => setImportText(event.target.value)}
            placeholder="Вставьте текст письма или КП: в каждой строке наименование, количество, цена и сумма (единица и срок — если есть)"
          />
          <div className="quote-import-actions">
            <button type="button" className="secondary small" disabled={!importText.trim() || importBusy} onClick={() => void runImport({ text: importText })}>Разобрать текст</button>
            <label className="quote-import-file">или PDF с текстовым слоем (до 20 МБ)
              <input type="file" accept="application/pdf,.pdf" aria-label="PDF с КП" disabled={importBusy} onChange={(event) => { importFile(event.target.files?.[0]); event.target.value = ""; }} />
            </label>
          </div>
        </div>
      )}
      {importNotes.length > 0 && (
        <ul className="quote-import-notes" aria-label="Что сделано при разборе">
          {importNotes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      )}
      {importUnmatched.length > 0 && (
        <div className="warning-inline quote-import-unmatched">
          Не сопоставлено со строками запроса — отнесите к позиции или пропустите:
          {importUnmatched.map((row, index) => (
            <div key={`${row.source_name}-${index}`} className="quote-import-unmatched-row">
              <span>{row.source_name || row.source_article}{row.price ? `, ${row.price} ₽` : ""}</span>
              <select aria-label={`Отнести «${row.source_name}»`} value="" onChange={(event) => assignUnmatched(index, event.target.value)}>
                <option value="">— к позиции —</option>
                {asked.map((line) => <option key={line.id} value={line.id}>{line.name}</option>)}
              </select>
            </div>
          ))}
        </div>
      )}
      {importUnread.length > 0 && (
        <div className="warning-inline">Строки с ценой, которые не удалось прочитать: {importUnread.join("; ")}</div>
      )}
      {pasteOpen && (
        <div style={{ display: "grid", gap: 6 }}>
          <textarea
            aria-label="Таблица из Excel" rows={6} value={pasteText} onChange={(event) => setPasteText(event.target.value)}
            placeholder="Скопируйте строки КП из Excel вместе с заголовком: наименование, артикул, кол-во, цена, НДС, срок"
          />
          <div><button type="button" className="secondary small" disabled={!pasteText.trim()} onClick={() => void parsePaste()}>Разобрать</button></div>
        </div>
      )}
      {asked.length < matrix.lines.length && (
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          У поставщика запрошено позиций: {asked.length} из {matrix.lines.length} — остальные в КП не вносятся.
        </p>
      )}
      {unmatched.length > 0 && (
        <div className="warning-inline">Не сопоставлено со строками запроса: {unmatched.map((row) => row.source_name || row.source_article).join("; ")}</div>
      )}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="inwork-scroll" style={{ maxHeight: "50vh" }}>
        <table className="inwork-table">
          <thead>
            <tr>
              <th>Позиция</th><th>Кол-во</th><th>Цена</th><th>НДС в цене</th><th>Ставка, %</th><th>Мин. партия</th><th>Упаковка</th>
              <th>Срок, дн</th><th>В наличии</th><th>Отказ</th><th>Аналог</th>
            </tr>
          </thead>
          <tbody>
            {asked.map((line) => {
              const row = rows[line.id];
              return (
                <tr key={line.id}>
                  <td style={{ whiteSpace: "normal", minWidth: 200 }}><strong>{line.name}</strong> <small className="muted">{line.article ?? ""}</small></td>
                  <td>{quantity(line.quantity)} {line.unit}</td>
                  <td><input aria-label={`Цена ${line.name}`} inputMode="decimal" disabled={row.declined} value={row.price} onChange={(event) => patch(line.id, { price: event.target.value })} style={{ width: 100 }} /></td>
                  <td><input type="checkbox" aria-label={`НДС в цене ${line.name}`} checked={row.vat_included} onChange={(event) => patch(line.id, { vat_included: event.target.checked })} /></td>
                  <td><input aria-label={`Ставка НДС ${line.name}`} inputMode="decimal" value={row.vat_rate} onChange={(event) => patch(line.id, { vat_rate: event.target.value })} style={{ width: 56 }} /></td>
                  <td><input aria-label={`Мин. партия ${line.name}`} inputMode="decimal" value={row.moq} onChange={(event) => patch(line.id, { moq: event.target.value })} style={small} /></td>
                  <td><input aria-label={`Упаковка ${line.name}`} inputMode="decimal" value={row.pack} onChange={(event) => patch(line.id, { pack: event.target.value })} style={small} /></td>
                  <td><input aria-label={`Срок ${line.name}`} inputMode="numeric" disabled={row.in_stock} value={row.lead} onChange={(event) => patch(line.id, { lead: event.target.value.replace(/\D/g, "") })} style={{ width: 56 }} /></td>
                  <td><input type="checkbox" aria-label={`В наличии ${line.name}`} checked={row.in_stock} onChange={(event) => patch(line.id, { in_stock: event.target.checked })} /></td>
                  <td><input type="checkbox" aria-label={`Отказ ${line.name}`} checked={row.declined} onChange={(event) => patch(line.id, { declined: event.target.checked })} /></td>
                  <td>
                    <input type="checkbox" aria-label={`Аналог ${line.name}`} checked={row.analog} onChange={(event) => patch(line.id, { analog: event.target.checked })} />
                    {row.analog && <input aria-label={`Какой аналог ${line.name}`} value={row.analog_note} placeholder="какой" onChange={(event) => patch(line.id, { analog_note: event.target.value })} style={{ width: 120, marginLeft: 4 }} />}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="form-actions">
        <span className="muted" style={{ marginRight: "auto", fontSize: 12 }}>Пустая строка без отказа — позиция не предложена. Повторное КП заменит прежнее по этим позициям.</span>
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy} onClick={() => void save()}>Сохранить КП</button>
      </div>
    </Dialog>
  );
}

/** Which lines of the request a supplier is asked for; a line he has already quoted stays ticked. */
function SupplierLinesDialog({ matrix, supplier, onClose, onSaved }: {
  matrix: RfqMatrix; supplier: RfqSupplier; onClose: () => void; onSaved: () => void;
}) {
  const all = matrix.lines.map((line) => line.id);
  const [picked, setPicked] = useState<Set<string>>(() => new Set(supplier.line_ids ?? all));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const quoted = new Set(matrix.lines.filter((line) => quoteCell(line, supplier.supplier_id)).map((line) => line.id));
  const toggle = (id: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  async function save() {
    setBusy(true);
    setError("");
    try {
      await rfqApi.setSupplierLines(matrix.rfq.id, supplier.supplier_id, all.filter((id) => picked.has(id)));
      onSaved();
    } catch (reason) {
      setError(failureText(reason, "Позиции не сохранены"));
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Позиции для «${supplier.name}»`} onClose={onClose}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 12 }}>
        <span className="muted">Отмечено {picked.size} из {all.length}</span>
        <button type="button" className="link-button" style={{ padding: 0 }} onClick={() => setPicked(new Set(all))}>все</button>
      </div>
      <div className="rfq-lines-editor" role="group" aria-label="Позиции запроса">
        {matrix.lines.map((line) => (
          <label key={line.id} title={quoted.has(line.id) ? "По позиции уже есть КП этого поставщика" : undefined}>
            <input
              type="checkbox" aria-label={`Запросить ${line.legacy_id ?? line.name}`} checked={picked.has(line.id)}
              disabled={quoted.has(line.id)} onChange={() => toggle(line.id)}
            />
            <span>{line.name}</span>
            {line.legacy_id && <small>{line.legacy_id}</small>}
            {quoted.has(line.id) && <small>есть КП</small>}
          </label>
        ))}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy || picked.size === 0} onClick={() => void save()}>Сохранить</button>
      </div>
    </Dialog>
  );
}

function SuggestDialog({ rfqId, onClose, onDone }: { rfqId: string; onClose: () => void; onDone: (count: number) => void }) {
  const [result, setResult] = useState<SuggestResult | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    rfqApi.suggest(rfqId)
      .then((next) => {
        setResult(next);
        setPicked(new Set(next.suggestions.filter((item) => item.preselect).map(suggestionKey)));
      })
      .catch((reason) => setError(failureText(reason, "Подсказка не загрузилась")));
  }, [rfqId]);

  async function add() {
    if (!result) return;
    setBusy(true);
    setError("");
    let count = 0;
    try {
      // One by one: a supplier not in the directory is quick-added by name, as «Добавить поставщика» does.
      for (const pick of picksOf(result.suggestions, picked)) {
        await rfqApi.addSupplier(rfqId, pick.supplier_id ? { supplier_id: pick.supplier_id } : { name: pick.name }, pick.reason);
        count += 1;
      }
      onDone(count);
    } catch (reason) {
      setError(failureText(reason, "Поставщики не добавлены"));
      setBusy(false);
    }
  }

  const toggle = (key: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const fresh = result ? picksOf(result.suggestions, picked).length : 0;
  return (
    <Dialog title="Подобрать поставщиков" wide onClose={onClose}>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!result && !error && <div className="empty">Загрузка…</div>}
      {result && result.suggestions.length === 0 && (
        <div className="empty">Подсказать некого: истории закупок этих позиций нет, в строках поставщик не указан, у поставщиков справочника нет таких категорий. Добавьте поставщика по названию.</div>
      )}
      {result && result.suggestions.length > 0 && <SuggestionList items={result.suggestions} checked={picked} onToggle={toggle} />}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy || fresh === 0} onClick={() => void add()}>Добавить выбранных ({fresh})</button>
      </div>
    </Dialog>
  );
}
