import { useCallback, useEffect, useState, type FormEvent } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import {
  quoteCell,
  rfqApi,
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
import { MatrixTable } from "./RfqMatrix";
import { DecisionPanel } from "./RfqDecision";
import { askSplit, RemovedLines, RfqLineToolbar, RfqSelectionBar } from "./RfqLineActions";
import { decidedPicks, RfqBasket } from "./RfqBasket";
import { invoiceAskable, RfqInvoiceRequests } from "./RfqInvoiceRequests";

/** «Запросы КП» (stage option procurement.rfq, as Roller_Buyer RfqPage): the buyer's lines → a request to several
 *  suppliers → their quotes in a matrix compared with VAT by what covering the need costs → the choice with the
 *  reasons the gate asks for → supplier and quantity go into the procurement line. */


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
  // Lines ticked for the selection bar (request line ids) and «показать отменённые и убранные».
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showRemoved, setShowRemoved] = useState(false);
  // «Подбор»: request line → the offer taken for it while the buyer assembles a set; null — the mode is off.
  const [basket, setBasket] = useState<Map<string, string> | null>(null);
  const pick = (ids: string[], on: boolean) => setPicked((current) => {
    const next = new Set(current);
    for (const lineId of ids) { if (on) next.add(lineId); else next.delete(lineId); }
    return next;
  });

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

  /** After the selection bar or a split: the lines changed, the ticks go. */
  async function linesChanged(message: string) {
    setPicked(new Set());
    setError("");
    await refresh(message);
  }

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
          {!invoiceLetters && lines.some(invoiceAskable) && (
            <button
              type="button" className="secondary small"
              title="Письма поставщикам с просьбой выставить счёт по выбранным у них строкам"
              onClick={() => setInvoiceLetters(true)}
            >Запросить счета</button>
          )}
          {editable && !basket && lines.some((line) => !line.cancelled) && (
            <button
              type="button" className="secondary small"
              title="Выбрать по строкам предложения разных поставщиков и посчитать итог с доставкой"
              onClick={() => { setBasket(decidedPicks(matrix)); setDecisionFor(null); setQuoteFor(null); setPicked(new Set()); }}
            >Подбор</button>
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
      {editable && lines.length > 0 && (
        <RfqLineToolbar matrix={matrix} picked={picked} onPick={pick} showRemoved={showRemoved} onShowRemoved={setShowRemoved} />
      )}
      {!editable && (matrix.removed?.length ?? 0) + lines.filter((line) => line.cancelled).length > 0 && (
        <RfqLineToolbar matrix={matrix} picked={picked} onPick={() => undefined} showRemoved={showRemoved} onShowRemoved={setShowRemoved} />
      )}
      {invoiceLetters && !basket && <RfqInvoiceRequests key={matrix.lines.map((line) => line.decision?.id ?? "").join()} rfqId={id} onClose={() => setInvoiceLetters(false)} />}
      {basket && (
        <RfqBasket
          m={matrix} picks={basket} setPicks={setBasket} onClose={() => setBasket(null)}
          onSaved={(message) => { setBasket(null); setInvoiceLetters(true); void refresh(message); }}
        />
      )}
      {editable && picked.size > 0 && !basket && (
        <RfqSelectionBar
          matrix={matrix} picked={picked} onClear={() => setPicked(new Set())}
          onDone={(message) => void linesChanged(message)} onError={setError}
        />
      )}
      <MatrixTable
        matrix={matrix} lines={showRemoved ? lines : lines.filter((line) => !line.cancelled)} editable={editable}
        picked={picked} onPick={pick} basket={basket} actions={{
          onSplit: (line) => {
            const task = askSplit(line);
            if (task) void task().then((message) => linesChanged(message)).catch((reason) => setError(failureText(reason, "Строка не разделена")));
          },
          onOpen: (lineId, supplierId) => setDecisionFor({ lineId, supplierId }),
          onBasket: (lineId, quoteLineId) => setBasket((current) => {
            if (!current) return current;
            const next = new Map(current);
            // A click takes this offer for the line; again — drops it.
            if (next.get(lineId) === quoteLineId) next.delete(lineId); else next.set(lineId, quoteLineId);
            return next;
          }),
          onLines: (supplier) => setLinesFor(supplier),
          onSent: (supplier) => void act(() => rfqApi.markSent(id, supplier.supplier_id), `«${supplier.name}»: запрос отправлен`, "Не отмечено"),
          onQuote: (supplier) => setQuoteFor({ supplier }),
          onDeclined: (supplier) => {
            const note = window.prompt(`«${supplier.name}» отказался отвечать. Комментарий (необязательно):`, "");
            if (note !== null) void act(() => rfqApi.markDeclined(id, supplier.supplier_id, note), `«${supplier.name}»: отказ отмечен`, "Не отмечено");
          },
          onRemove: (supplier) => void act(() => rfqApi.removeSupplier(id, supplier.supplier_id), `«${supplier.name}» убран из запроса`, "Поставщик не убран"),
        }}
      />
      {showRemoved && <RemovedLines matrix={matrix} />}
      {lines.length === 0 && (
        <div className="empty">В запросе нет строк: все убраны или отменены.</div>
      )}
      {matrix.quotes.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 12 }} aria-label="КП в запросе">
          <span className="muted">КП в запросе:</span>
          {matrix.quotes.map((quote) => (
            <span key={quote.id}>
              {supplierName(quote.supplier_id)}{quote.supplier_is_key ? " ★" : ""}{quote.number ? ` № ${quote.number}` : ""} от {formatDay(quote.received_on)}
              {quote.valid_until ? `, до ${formatDay(quote.valid_until)}` : ""}{quote.source === "paste" ? ", из Excel" : quote.source === "file" ? ", из PDF" : quote.source === "invoice" ? ", из счёта" : ""}
              {quote.note ? <span className="muted"> — {quote.note}</span> : null}
              {quote.has_file && <a href={rfqApi.quoteFileUrl(quote.id)} target="_blank" rel="noreferrer" style={{ paddingLeft: 6 }}>PDF ↗</a>}
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
