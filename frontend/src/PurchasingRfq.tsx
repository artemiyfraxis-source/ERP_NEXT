import { useCallback, useEffect, useMemo, useState, type CSSProperties, type FormEvent } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import {
  decisionViolations,
  quoteCell,
  rfqApi,
  unasked,
  type DecisionField,
  type RfqCell,
  type RfqLine,
  type RfqMatrix,
  type RfqSummary,
  type RfqSupplier,
  type SuggestResult,
  type SupplierBrief,
} from "./purchasingRfqApi";
import { picksOf, RfqPool, suggestionKey, SuggestionList } from "./RfqPool";
import { rfqLinkState, syncRfqUrl } from "./rfqLinks";
import { QuoteDialog } from "./RfqQuoteDialog";
import { Dialog, money, number, quantity } from "./rfqUi";
import { RfqLetters } from "./RfqLetters";

/** «Запросы КП» (stage option procurement.rfq, as Roller_Buyer RfqPage): the buyer's lines → a request to several
 *  suppliers → their quotes in a matrix compared with VAT by what covering the need costs → the choice with the
 *  reasons the gate asks for → supplier and quantity go into the procurement line. */

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

function RfqMatrixView({ id, readOnly, onChanged, openLetters = false, openInvoices = false }: {
  id: string; readOnly: boolean; onChanged: () => void; openLetters?: boolean; openInvoices?: boolean;
}) {
  const [letters, setLetters] = useState(openLetters);
  const [invoiceLetters, setInvoiceLetters] = useState(openInvoices);
  useEffect(() => { syncRfqUrl(id, { letters, invoices: invoiceLetters }); }, [id, letters, invoiceLetters]);
  const [matrix, setMatrix] = useState<RfqMatrix | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // «+ КП» of a supplier column, or «Внести КП» from anyone (null supplier — chosen in the dialog).
  const [quoteFor, setQuoteFor] = useState<{ supplier: RfqSupplier | null } | null>(null);
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
        <div className="rfq-head-actions">
          {!letters && suppliers.length > 0 && (
            <button type="button" className="secondary small" onClick={() => setLetters(true)}>Письма поставщикам</button>
          )}
        </div>
        {editable && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <button type="button" className="secondary small" onClick={() => setQuoteFor({ supplier: null })}>Внести КП</button>
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
      {rfq.note && <p className="page-sub">{rfq.note}</p>}
      {letters && suppliers.length > 0 && (
        <RfqLetters matrix={matrix} editable={editable} onClose={() => setLetters(false)} onSent={(message) => void refresh(message)} />
      )}
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
                    onQuote={() => setQuoteFor({ supplier })}
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
              {supplierName(quote.supplier_id)}{quote.supplier_is_key ? " ★" : ""}{quote.number ? ` № ${quote.number}` : ""} от {formatDay(quote.received_on)}
              {quote.valid_until ? `, до ${formatDay(quote.valid_until)}` : ""}{quote.source === "paste" ? ", из Excel" : quote.source === "file" ? ", из PDF" : quote.source === "invoice" ? ", из счёта" : ""}
              {quote.note ? <span className="muted"> — {quote.note}</span> : null}
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
          matrix={matrix} supplier={quoteFor.supplier} directory={directory} onClose={() => setQuoteFor(null)}
          onSaved={(name) => { setQuoteFor(null); void refresh(`КП «${name}» внесено`); }}
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
