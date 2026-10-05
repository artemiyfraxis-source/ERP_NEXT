import { useCallback, useEffect, useMemo, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import {
  decisionViolations,
  rfqApi,
  type DecisionField,
  type PastedRow,
  type QuoteLineInput,
  type RfqCandidate,
  type RfqCell,
  type RfqLine,
  type RfqMatrix,
  type RfqSummary,
  type RfqSupplier,
  type SuggestResult,
  type SupplierBrief,
} from "./purchasingRfqApi";

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
  const [rfqs, setRfqs] = useState<RfqSummary[] | null>(null);
  const [selected, setSelected] = useState("");
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

  useEffect(() => { void reload(); }, [reload]);

  return (
    <section className="page" style={{ display: "grid", gridTemplateColumns: "210px minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      <aside className="panel" style={{ padding: 12, display: "grid", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <h2 style={{ margin: 0, fontSize: 16 }}>Запросы КП</h2>
          {!readOnly && <button type="button" className="primary small" onClick={() => setCreating(true)}>+ Запрос</button>}
        </div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!error && rfqs === null && <div className="empty">Загрузка…</div>}
        {rfqs?.length === 0 && <div className="empty">Запросов нет — нажмите «Новый запрос» и отметьте строки «В работе».</div>}
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
          ? <RfqMatrixView key={selected} id={selected} readOnly={readOnly} onChanged={() => void reload(selected)} />
          : <div className="panel empty">Выберите запрос слева или создайте новый.</div>}
      </div>
      {creating && (
        <NewRfqDialog
          onClose={() => setCreating(false)}
          onCreated={(id) => { setCreating(false); void reload(id); }}
        />
      )}
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

function NewRfqDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [lines, setLines] = useState<RfqCandidate[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    rfqApi.candidates()
      .then(({ lines: next }) => setLines(next))
      .catch((reason) => setError(failureText(reason, "Строки не загрузились")));
  }, []);

  const toggle = (id: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  async function create() {
    setBusy(true);
    setError("");
    try {
      const created = await rfqApi.create([...picked], title.trim());
      onCreated(created.id);
    } catch (reason) {
      setError(failureText(reason, "Запрос не создан"));
    } finally {
      setBusy(false);
    }
  }

  const all = lines !== null && lines.length > 0 && picked.size === lines.length;
  return (
    <Dialog title="Новый запрос КП" wide onClose={onClose}>
      <label>Название (необязательно)
        <input value={title} maxLength={255} onChange={(event) => setTitle(event.target.value)} placeholder="Например, разъёмы для RND.012" />
      </label>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {lines === null && !error && <div className="empty">Загрузка…</div>}
      {lines?.length === 0 && <div className="empty">Нет строк «В работе» / «Готов к запросу» вне запросов КП — возьмите строки в работу на листе закупки.</div>}
      {lines && lines.length > 0 && (
        <div className="inwork-scroll" style={{ maxHeight: "55vh" }}>
          <table className="inwork-table">
            <thead>
              <tr>
                <th><input type="checkbox" aria-label="Выбрать все" checked={all} onChange={() => setPicked(all ? new Set() : new Set(lines.map((line) => line.id)))} /></th>
                <th>Позиция</th><th>Артикул</th><th>Кол-во</th><th>Поставщик в строке</th><th>Бюджет с НДС</th><th>Плановая дата</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.id} onClick={() => toggle(line.id)} style={{ cursor: "pointer" }}>
                  <td><input type="checkbox" aria-label={`Взять ${line.legacy_id}`} checked={picked.has(line.id)} onChange={() => toggle(line.id)} onClick={(event) => event.stopPropagation()} /></td>
                  <td><strong>{line.name}</strong> <small className="muted">{line.legacy_id} · {line.status}</small></td>
                  <td>{line.article ?? ""}</td>
                  <td>{quantity(line.quantity)} {line.unit}</td>
                  <td>{line.supplier ?? ""}</td>
                  <td>{money(line.budget_unit_gross)}</td>
                  <td>{formatDay(line.need_by)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy || picked.size === 0} onClick={() => void create()}>
          Создать запрос ({picked.size})
        </button>
      </div>
    </Dialog>
  );
}

function RfqMatrixView({ id, readOnly, onChanged }: { id: string; readOnly: boolean; onChanged: () => void }) {
  const [matrix, setMatrix] = useState<RfqMatrix | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [quoteFor, setQuoteFor] = useState<RfqSupplier | null>(null);
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
                    supplier={supplier} editable={editable}
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
                    key={supplier.supplier_id} line={line} supplier={supplier} cell={line.cells[supplier.supplier_id]}
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
              {quote.valid_until ? `, до ${formatDay(quote.valid_until)}` : ""}{quote.source === "paste" ? ", из Excel" : ""}
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

function SupplierHead({ supplier, editable, onSent, onQuote, onDeclined, onRemove }: {
  supplier: RfqSupplier; editable: boolean; onSent: () => void; onQuote: () => void; onDeclined: () => void; onRemove: () => void;
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

function MatrixCell({ line, supplier, cell, onOpen }: { line: RfqLine; supplier: RfqSupplier; cell: RfqCell | undefined; onOpen: () => void }) {
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

  const chosen = line.cells[chosenId];
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
  const offers = matrix.suppliers.filter((supplier) => line.cells[supplier.supplier_id]);

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
            {offers.map((supplier) => {
              const cell = line.cells[supplier.supplier_id];
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
  const [rows, setRows] = useState<Record<string, QuoteRowState>>(() => Object.fromEntries(matrix.lines.map((line) => [line.id, blank()])));
  const [receivedOn, setReceivedOn] = useState(today());
  const [validUntil, setValidUntil] = useState("");
  const [delivery, setDelivery] = useState("");
  const [minOrder, setMinOrder] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [unmatched, setUnmatched] = useState<PastedRow[]>([]);
  const [source, setSource] = useState<"manual" | "paste">("manual");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const patch = (lineId: string, change: Partial<QuoteRowState>) =>
    setRows((current) => ({ ...current, [lineId]: { ...current[lineId], ...change } }));

  async function parsePaste() {
    setError("");
    try {
      const preview = await rfqApi.paste(matrix.rfq.id, pasteText);
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

  async function save() {
    const lines: QuoteLineInput[] = matrix.lines.flatMap((line) => {
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
      </div>
      {pasteOpen && (
        <div style={{ display: "grid", gap: 6 }}>
          <textarea
            aria-label="Таблица из Excel" rows={6} value={pasteText} onChange={(event) => setPasteText(event.target.value)}
            placeholder="Скопируйте строки КП из Excel вместе с заголовком: наименование, артикул, кол-во, цена, НДС, срок"
          />
          <div><button type="button" className="secondary small" disabled={!pasteText.trim()} onClick={() => void parsePaste()}>Разобрать</button></div>
        </div>
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
            {matrix.lines.map((line) => {
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

function SuggestDialog({ rfqId, onClose, onDone }: { rfqId: string; onClose: () => void; onDone: (count: number) => void }) {
  const [result, setResult] = useState<SuggestResult | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    rfqApi.suggest(rfqId)
      .then((next) => {
        setResult(next);
        setPicked(new Set(next.suggestions.filter((item) => item.preselect).map((item) => item.supplier_id)));
      })
      .catch((reason) => setError(failureText(reason, "Подсказка не загрузилась")));
  }, [rfqId]);

  async function add(names: string[] = []) {
    if (!result) return;
    setBusy(true);
    setError("");
    let count = 0;
    try {
      for (const item of result.suggestions.filter((suggestion) => picked.has(suggestion.supplier_id) && !suggestion.in_rfq)) {
        await rfqApi.addSupplier(rfqId, { supplier_id: item.supplier_id }, item.reason);
        count += 1;
      }
      for (const name of names) {
        await rfqApi.addSupplier(rfqId, { name }, "plan");
        count += 1;
      }
      onDone(count);
    } catch (reason) {
      setError(failureText(reason, "Поставщики не добавлены"));
      setBusy(false);
    }
  }

  const toggle = (id: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const fresh = result?.suggestions.filter((item) => picked.has(item.supplier_id) && !item.in_rfq).length ?? 0;
  return (
    <Dialog title="Подобрать поставщиков" onClose={onClose}>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!result && !error && <div className="empty">Загрузка…</div>}
      {result && result.suggestions.length === 0 && result.unmapped_plan.length === 0 && (
        <div className="empty">Подходящих поставщиков нет — у поставщиков в справочнике не указаны категории этих позиций. Добавьте поставщика по названию.</div>
      )}
      {result && result.suggestions.length > 0 && (
        <div style={{ display: "grid", gap: 4 }}>
          {result.suggestions.map((item) => (
            <label key={item.supplier_id} className="check-inline" style={{ color: "var(--foreground)" }}>
              <input type="checkbox" disabled={item.in_rfq} checked={item.in_rfq || picked.has(item.supplier_id)} onChange={() => toggle(item.supplier_id)} />
              <strong style={{ display: "inline" }}>{item.name}{item.is_key ? " ★" : ""}</strong>
              <span className="muted">
                {REASONS[item.reason]}{item.last_asked_at ? ` · спрашивали ${formatDay(item.last_asked_at)}` : item.reason === "rotation" ? " · ещё не спрашивали" : ""}
                {item.in_rfq ? " · уже в запросе" : ""}
              </span>
            </label>
          ))}
        </div>
      )}
      {result && result.unmapped_plan.length > 0 && (
        <div className="warning-inline">
          В строках указан поставщик не из справочника: {result.unmapped_plan.join(", ")}.{" "}
          <button type="button" className="link-button" style={{ padding: 0 }} disabled={busy} onClick={() => void add(result.unmapped_plan)}>
            Добавить в справочник и в запрос
          </button>
        </div>
      )}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy || fresh === 0} onClick={() => void add()}>Добавить выбранных ({fresh})</button>
      </div>
    </Dialog>
  );
}
