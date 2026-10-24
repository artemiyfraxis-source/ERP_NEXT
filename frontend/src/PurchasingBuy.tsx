import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError, api, failureText, manualRatePrompt, procurementApi, type ApplyOrderDocumentInput, type ManualRatePrompt, type ProcurementLine } from "./api";
import { formatDay, formatQty } from "./format";
import { projectLabel, useInternalProjects } from "./internalProjects";
import { openSection } from "./navigation";
import { PageWindow } from "./PageWindow";
import { formatAmount } from "./ProcurementSheetColumns";
import { OrderDocumentDialog, groupByOrder, type OrderGroup } from "./ProcurementStages";
import { ordersApi, type Order } from "./purchasingOrdersApi";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import "./purchasingBuy.css";

/* «Закупка» (владелец 23.10, правка Вити 13): позиции с решением по КП, по поставщикам. Карточка — поставщик, число
   позиций и сумма по КП; внутри — позиции. Метка карточки — этап её строк закупки: «Готов к запросу» → «Запросить
   счёт» (письмо о закупке по КП) → «Жду счёт» → счёт с распознаванием и проверкой → «Ожидает оплаты» → «Внести ПП» или
   «Оплачено» → архив; дальше заказ живёт в «Счетах и поставках». Каждая запись идёт с версиями строк (409 — обновить). */

export type BuyStage = "draft" | "ready" | "awaiting" | "to_pay" | "paid";

export type BuyLine = {
  line_id: string; legacy_id: string; version: number; status: string; stage: BuyStage; name: string;
  article: string | null; project: string | null; quantity: string | null; unit: string; unit_gross: string | null;
  total_gross: string | null; currency: string; vat_included: boolean; order_number: string | null;
  invoice_number: string | null; rfq_id: string; rfq_number: string; quote_number: string; quote_date: string;
  delivery_point: string;
};

export type BuyCard = {
  key: string; supplier_id: string; supplier: string; email: string | null; mine: boolean; order_number: string | null;
  stage: BuyStage; tag: string; archived: boolean; count: number; sums: { currency: string; sum: string | null }[];
  quotes: { number: string; date: string; rfq_id: string; rfq_number: string }[]; invoices: string[];
  decided_at: string | null; stage_counts: Partial<Record<BuyStage, number>>; lines: BuyLine[];
};

export type BuyBoard = { scope: "mine" | "all"; stages: { key: BuyStage; title: string }[]; cards: BuyCard[]; archived_total: number };

export type BuyLetter = {
  supplier_id: string; supplier_name: string; email: string | null; subject: string; body: string; cart: string;
  total: string | null; orders: string[]; quotes: { number: string; date: string }[];
};

type RequestResult = {
  orders: { order_number: string }[];
  lines: unknown[];
  skipped: { line_id: string; legacy_id: string; reason: string }[];
};

const BASE = "/api/purchasing/buy";

export const buyApi = {
  board: (scope: "mine" | "all") => api<BuyBoard>(`${BASE}?scope=${scope}`),
  letters: (lineIds: string[]) =>
    api<{ letters: BuyLetter[]; skipped: string[] }>(`${BASE}/letters?${lineIds.map((id) => `line_ids=${encodeURIComponent(id)}`).join("&")}`),
  /** «Сформировать запрос» по позициям карточки с версиями, которые показал экран (409 — строку уже изменили). */
  request: (lines: Pick<BuyLine, "line_id" | "version">[]) =>
    api<RequestResult>("/api/procurement/supplier-requests", {
      method: "POST",
      body: JSON.stringify({ line_ids: lines.map((line) => line.line_id), versions: Object.fromEntries(lines.map((line) => [line.line_id, line.version])) }),
    }),
  /** ПП к строке заказа (вид «payment»): файл хранится, не распознаётся и ничего в строке не меняет. */
  uploadPayment: (lineId: string, file: { name: string; type: string; base64: string }) =>
    api<{ file: { id: string; file_name: string; url: string } }>(`/api/procurement/${lineId}/files`, {
      method: "POST",
      body: JSON.stringify({ kind: "payment", file_name: file.name, content_type: file.type, content_base64: file.base64 }),
    }),
};

type Filter = "active" | "ready" | "awaiting" | "to_pay" | "archive";
const FILTERS: { key: Filter; label: string; test: (card: BuyCard) => boolean }[] = [
  { key: "active", label: "Активные", test: (card) => !card.archived },
  { key: "ready", label: "Готов к запросу", test: (card) => card.stage === "ready" },
  { key: "awaiting", label: "Жду счёт", test: (card) => card.stage === "awaiting" },
  { key: "to_pay", label: "Ожидает оплаты", test: (card) => card.stage === "to_pay" },
  { key: "archive", label: "Архив", test: (card) => card.archived },
];

const STAGE_TONE: Record<BuyStage, Tone> = { draft: "neutral", ready: "info", awaiting: "neutral", to_pay: "warning", paid: "success" };

const currencyMark = (currency: string) => (currency === "RUB" ? "₽" : currency);

export function sumText(sums: BuyCard["sums"]): string {
  const parts = sums.filter((item) => item.sum !== null).map((item) => `${formatAmount(item.sum)} ${currencyMark(item.currency)}`);
  return parts.join(" + ") || "—";
}

export function quotesText(card: Pick<BuyCard, "quotes">): string {
  const shown = card.quotes.map((quote) => (/\d/.test(quote.number) ? `КП № ${quote.number}` : "КП") + ` от ${formatDay(quote.date)}`);
  return shown.length > 2 ? `${shown.slice(0, 2).join(", ")} и ещё ${shown.length - 2}` : shown.join(", ");
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

type Notice = { text: string; tone: "ok" | "error" };

export function PurchasingBuy({ readOnly = false, onOpenRfq, onOpenOrder }: {
  readOnly?: boolean; onOpenRfq?: (rfqId: string) => void; onOpenOrder?: (orderNumber: string) => void;
} = {}) {
  useInternalProjects(); // «ВП-001 · Название»
  const [scope, setScope] = useState<"mine" | "all">(readOnly ? "all" : "mine");
  const [filter, setFilter] = useState<Filter>("active");
  const [data, setData] = useState<BuyBoard | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [letterFor, setLetterFor] = useState<BuyCard | null>(null);
  const [invoice, setInvoice] = useState<{ card: BuyCard; group: OrderGroup; lines: ProcurementLine[] } | null>(null);
  const [pay, setPay] = useState<{ card: BuyCard; withFile: boolean } | null>(null);
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const next = await buyApi.board(scope);
      if (!live.current) return;
      setData(next);
      setError("");
    } catch (reason) {
      if (live.current) setError(failureText(reason, "Закупка не загрузилась"));
    }
  }, [scope]);

  useEffect(() => {
    live.current = true;
    void reload();
    return () => { live.current = false; };
  }, [reload]);

  const cards = useMemo(() => data?.cards ?? [], [data]);
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((item) => [item.key, cards.filter(item.test).length])), [cards]);
  const test = FILTERS.find((item) => item.key === filter)!.test;
  const shown = cards.filter(test);
  const opened = openKey ? cards.find((card) => card.key === openKey) ?? null : null;
  const say = (text: string, tone: Notice["tone"] = "ok") => setNotice({ text, tone });
  const openOrder = (number: string) => (onOpenOrder ? onOpenOrder(number) : openSection("procurement", { tab: "orders", order: number }));

  async function openInvoice(card: BuyCard) {
    if (!card.order_number) return;
    try {
      const { lines } = await procurementApi.kpOrder(card.order_number);
      const [group] = groupByOrder(lines);
      if (!group) throw new Error(`У заказа ${card.order_number} нет строк «Жду счёт»`);
      setInvoice({ card, group, lines });
    } catch (reason) {
      say(failureText(reason, "Окно счёта не открылось"), "error");
      void reload();
    }
  }

  async function applyInvoice(group: OrderGroup, input: ApplyOrderDocumentInput) {
    try {
      const result = await procurementApi.applyOrderDocument(group.number, input);
      const warnings = result.warnings ?? [];
      say(`${group.number}: счёт внесён — «Ожидает оплаты», позиций: ${input.lines.length}${warnings.length ? ` · ${warnings.join("; ")}` : ""}`);
      void reload();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        void reload();
        throw new Error("Строки заказа изменились — данные обновлены. Закройте окно и загрузите счёт ещё раз");
      }
      throw reason;
    }
  }

  const act = (card: BuyCard) => {
    if (readOnly || !card.mine) return null;
    if (card.stage === "ready") return <button type="button" className="primary small" onClick={() => setLetterFor(card)}>Запросить счёт</button>;
    if (card.stage === "awaiting") return (
      <>
        <button type="button" className="primary small" onClick={() => void openInvoice(card)}>Загрузить счёт</button>
        <button type="button" className="secondary small" onClick={() => setLetterFor(card)}>Письмо</button>
      </>
    );
    if (card.stage === "to_pay") return (
      <>
        <button type="button" className="primary small" onClick={() => setPay({ card, withFile: true })}>Внести ПП</button>
        <button type="button" className="secondary small" onClick={() => setPay({ card, withFile: false })}>Оплачено</button>
      </>
    );
    return null;
  };

  const actions = (card: BuyCard) => {
    const own = act(card);
    if (own) return own;
    if (card.archived && card.order_number) {
      return <button type="button" className="link-button" onClick={() => openOrder(card.order_number!)}>Открыть в «Счета и поставки»</button>;
    }
    if (card.stage === "draft") return <span className="muted">строки ещё «В работе» — заполните их в листе закупки</span>;
    if (card.stage !== "ready") return <button type="button" className="link-button" onClick={() => setLetterFor(card)}>Письмо</button>;
    return null;
  };

  return (
    <section className="page purchasing-buy">
      <div className="orders-toolbar">
        <div className="seg-tabs" role="group" aria-label="Этап">
          {FILTERS.map((item) => (
            <button key={item.key} type="button" className={filter === item.key ? "on" : undefined} aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>
              {item.label} <span className="buy-count">{counts[item.key] ?? 0}</span>
            </button>
          ))}
        </div>
        {!readOnly && (
          <div className="seg-tabs" role="group" aria-label="Чьи позиции">
            <button type="button" className={scope === "mine" ? "on" : undefined} aria-pressed={scope === "mine"} onClick={() => setScope("mine")}>Мои</button>
            <button type="button" className={scope === "all" ? "on" : undefined} aria-pressed={scope === "all"} onClick={() => setScope("all")}>Все закупщики</button>
          </div>
        )}
        {filter === "archive" && data && data.archived_total > counts.archive && (
          <span className="muted">показаны последние {counts.archive} из {data.archived_total}</span>
        )}
      </div>

      {notice && (
        <div className={notice.tone === "error" ? "error-banner" : "success-banner"} role={notice.tone === "error" ? "alert" : "status"}>
          <span>{notice.text}</span>
          <button type="button" className="link-button" aria-label="Скрыть" onClick={() => setNotice(null)}>✕</button>
        </div>
      )}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!data && !error && <div className="empty">Загрузка…</div>}
      {data && shown.length === 0 && (
        <div className="empty">
          {filter === "archive" ? "В архиве пока пусто: сюда уходят оплаченные заказы."
            : cards.length === 0 ? "Решений по КП нет: позиции появятся здесь, когда в «Запросах КП» выбран поставщик."
              : "Нет карточек на этом этапе."}
        </div>
      )}

      {shown.length > 0 && (
        <div className="buy-cards" role="list" aria-label="Поставщики">
          {shown.map((card) => (
            <article key={card.key} role="listitem" className={`buy-card${card.mine ? "" : " is-foreign"}`} data-stage={card.stage}>
              <button type="button" className="buy-card-open" onClick={() => setOpenKey(card.key)} aria-label={`${card.supplier} · ${card.tag} · ${card.count} поз.`}>
                <span className="buy-card-top">
                  <b className="buy-card-supplier">{card.supplier || "поставщик не указан"}</b>
                  <StatusChip tone={STAGE_TONE[card.stage]}>{card.tag}</StatusChip>
                </span>
                <span className="buy-card-figures">
                  <span>{card.count}&nbsp;поз.</span>
                  <span><b className="buy-card-sum">{sumText(card.sums)}</b> <span className="buy-card-meta">по КП</span></span>
                </span>
                <span className="buy-card-meta">
                  {card.order_number ? `Заказ ${card.order_number}` : "заказа ещё нет"}
                  {card.quotes.length > 0 && ` · ${quotesText(card)}`}
                  {card.invoices.length > 0 && ` · счёт ${card.invoices.join(", ")}`}
                </span>
              </button>
              <div className="buy-card-actions">{actions(card)}</div>
            </article>
          ))}
        </div>
      )}

      {opened && (
        <PageWindow label={`${opened.supplier} · ${opened.tag}`} close={() => setOpenKey(null)} width={1180} dismissable>
          <div className="panel buy-window">
            <div className="buy-window-head">
              <h2>{opened.supplier}</h2>
              <StatusChip tone={STAGE_TONE[opened.stage]}>{opened.tag}</StatusChip>
              {opened.order_number && <span className="muted">Заказ {opened.order_number}</span>}
              <span className="buy-spacer" />
              <button type="button" className="link-button" onClick={() => setOpenKey(null)}>Закрыть</button>
            </div>
            <p className="buy-window-meta">
              {opened.count}&nbsp;поз. · <b>{sumText(opened.sums)}</b> по КП, с НДС
              {opened.email && <> · {opened.email}</>}
              {opened.invoices.length > 0 && <> · счёт {opened.invoices.join(", ")}</>}
            </p>
            <div className="table-wrap">
              <table className="grid buy-lines" aria-label="Позиции">
                <thead>
                  <tr>
                    <th>Строка</th><th>Позиция</th><th>Проект</th><th className="num">Кол-во</th><th className="num">Цена с НДС</th>
                    <th className="num">Сумма</th><th>Запрос КП</th><th>Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {opened.lines.map((line) => (
                    <tr key={line.line_id}>
                      <td><strong>{line.legacy_id}</strong></td>
                      <td className="text">{line.name}{line.article && <small className="muted"> {line.article}</small>}</td>
                      <td>{line.project ? projectLabel(line.project) : "—"}</td>
                      <td className="num">{formatQty(line.quantity, line.unit)}</td>
                      <td className="num">{formatAmount(line.unit_gross) || "—"}</td>
                      <td className="num">{line.total_gross ? `${formatAmount(line.total_gross)} ${currencyMark(line.currency)}` : "—"}</td>
                      <td>
                        {onOpenRfq ? <button type="button" className="link-button" onClick={() => onOpenRfq(line.rfq_id)}>{line.rfq_number}</button> : line.rfq_number}
                        <small className="muted"> {/\d/.test(line.quote_number) ? `КП № ${line.quote_number}` : "КП"} от {formatDay(line.quote_date)}</small>
                      </td>
                      <td><StatusChip status={line.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="move-actions">{actions(opened)}</div>
          </div>
        </PageWindow>
      )}

      {letterFor && (
        <LetterWindow
          card={letterFor}
          canRequest={!readOnly && letterFor.mine && letterFor.stage === "ready"}
          onClose={() => setLetterFor(null)}
          onRequested={(text) => { say(text); void reload(); }}
          onConflict={(text) => { setLetterFor(null); say(text, "error"); void reload(); }}
        />
      )}
      {invoice && (
        <OrderDocumentDialog
          group={invoice.group}
          lines={invoice.lines}
          kind="invoice"
          replace={false}
          onRecognize={(file) => procurementApi.recognizeOrderDocument(invoice.group.number, file, "invoice")}
          onApply={(input) => applyInvoice(invoice.group, input)}
          onClose={() => setInvoice(null)}
        />
      )}
      {pay && (
        <PayWindow
          card={pay.card}
          withFile={pay.withFile}
          onClose={() => setPay(null)}
          onPaid={(text) => { setPay(null); setOpenKey(null); say(text); void reload(); }}
          onConflict={(text) => { setPay(null); say(text, "error"); void reload(); }}
        />
      )}
    </section>
  );
}

/** Письмо о закупке по КП. На «Готов к запросу» — с кнопкой «Запросить счёт»: запрос формируется (строки «Жду счёт»),
 *  письмо перечитывается уже с номером нашего заказа. */
function LetterWindow({ card, canRequest, onClose, onRequested, onConflict }: {
  card: BuyCard; canRequest: boolean; onClose: () => void; onRequested: (text: string) => void; onConflict: (text: string) => void;
}) {
  const asked = useMemo(
    () => card.lines.filter((line) => (card.stage === "ready" ? line.stage === "ready" : line.stage === card.stage)),
    [card],
  );
  const [letters, setLetters] = useState<BuyLetter[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [requested, setRequested] = useState(false);
  const [skipped, setSkipped] = useState<RequestResult["skipped"]>([]);
  const [copied, setCopied] = useState("");

  const load = useCallback(async () => {
    try {
      setLetters((await buyApi.letters(asked.map((line) => line.line_id))).letters);
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Письмо не собралось"));
    }
  }, [asked]);

  useEffect(() => { void load(); }, [load]);

  async function request() {
    setBusy(true);
    setError("");
    try {
      const result = await buyApi.request(asked);
      setRequested(true);
      setSkipped(result.skipped);
      const numbers = result.orders.map((order) => order.order_number);
      onRequested(numbers.length
        ? `Запрос сформирован: ${numbers.join(", ")} — «Жду счёт», позиций: ${result.lines.length}${result.skipped.length ? ` · не вошли: ${result.skipped.length}` : ""}`
        : `Запрос не сформирован: ${result.skipped.map((item) => `${item.legacy_id} — ${item.reason}`).join("; ")}`);
      await load();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) onConflict(failureText(reason, "Позиции изменились — данные обновлены"));
      else setError(failureText(reason, "Запрос не сформирован"));
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
    } catch {
      setCopied("");
      setError("Не удалось скопировать — выделите текст и скопируйте вручную");
    }
  }

  return (
    <PageWindow label={`Запрос счёта · ${card.supplier}`} close={onClose} width={900}>
      <div className="panel buy-window">
        <div className="buy-window-head">
          <h2>Запрос счёта · {card.supplier}</h2>
          <span className="muted">{asked.length} поз.</span>
          <span className="buy-spacer" />
          <button type="button" className="link-button" onClick={onClose}>Закрыть</button>
        </div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!letters && !error && <div className="empty">Загрузка…</div>}
        {letters && letters.length === 0 && <div className="empty">Для этих позиций письма нет: решения по КП сняты или счёт уже внесён.</div>}
        {letters?.map((letter) => (
          <div key={letter.supplier_id} className="buy-letter">
            <label>Кому<input readOnly value={letter.email ?? ""} placeholder="адрес не указан в карточке поставщика" /></label>
            <label>Тема<input readOnly value={letter.subject} /></label>
            <label>Текст<textarea readOnly rows={16} value={letter.body} /></label>
            <div className="buy-letter-actions">
              <button type="button" className="secondary small" onClick={() => void copy(letter.subject, "тема")}>Копировать тему</button>
              <button type="button" className="secondary small" onClick={() => void copy(letter.body, "текст")}>Копировать текст</button>
              <a className="secondary small" href={`mailto:${letter.email ?? ""}?subject=${encodeURIComponent(letter.subject)}&body=${encodeURIComponent(letter.body)}`}>Открыть в почте</a>
              {copied && <span className="muted" role="status">{copied} скопирован{copied === "тема" ? "а" : ""}</span>}
            </div>
          </div>
        ))}
        {skipped.length > 0 && (
          <div className="warning-inline" role="alert">
            Не вошли в запрос: {skipped.map((item) => `${item.legacy_id} — ${item.reason}`).join("; ")}
          </div>
        )}
        <div className="move-actions">
          {canRequest && !requested && (
            <button type="button" className="primary" disabled={busy || !letters?.length} onClick={() => void request()}>
              {busy ? "Формируем…" : `Запросить счёт (${asked.length} поз.)`}
            </button>
          )}
        </div>
      </div>
    </PageWindow>
  );
}

/** «Внести ПП» / «Оплачено»: строки заказа «В оплату» из карточки заказа (версии и чего не хватает для оплаты), ПП —
 *  файл к первой строке (не распознаётся), затем оплата всего заказа. ЦБ молчит — спрашиваем курс, как в «Счетах и
 *  поставках»; ПП второй раз не загружается. */
function PayWindow({ card, withFile, onClose, onPaid, onConflict }: {
  card: BuyCard; withFile: boolean; onClose: () => void; onPaid: (text: string) => void; onConflict: (text: string) => void;
}) {
  const number = card.order_number ?? "";
  const [order, setOrder] = useState<Order | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [stored, setStored] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<ManualRatePrompt | null>(null);
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    ordersApi.card(number)
      .then((next) => { if (alive) setOrder(next.order); })
      .catch((reason) => { if (alive) setError(failureText(reason, "Заказ не загрузился")); });
    return () => { alive = false; };
  }, [number]);

  const lines = (order?.lines ?? []).filter((line) => line.status === "В оплату");
  const missing = lines.filter((line) => line.pay_missing.length > 0);
  const total = lines.reduce((sum, line) => sum + Number(line.actual_sum ?? line.calculated_sum ?? 0), 0);
  const first = [...lines].sort((a, b) => a.legacy_id.localeCompare(b.legacy_id))[0];
  const ready = Boolean(order) && lines.length > 0 && !missing.length && (!withFile || Boolean(file) || Boolean(stored)) && (!prompt || Boolean(rate.trim()));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !ready || !first) return;
    setBusy(true);
    setError("");
    try {
      let name = stored;
      if (withFile && !name && file) {
        const saved = await buyApi.uploadPayment(first.id, { name: file.name, type: file.type || "application/octet-stream", base64: await readBase64(file) });
        name = saved.file.file_name;
        setStored(name);
      }
      await procurementApi.orderPaid(number, lines.map((line) => ({ line_id: line.id, version: line.version })), prompt ? rate.trim() : undefined);
      onPaid(`${number}: оплачено${name ? `, ПП «${name}» приложено` : ""} — заказ ушёл в архив, дальше он в «Счетах и поставках»`);
    } catch (reason) {
      const asked = manualRatePrompt(reason);
      if (asked) setPrompt(asked);
      else if (reason instanceof ApiError && reason.status === 409) onConflict("Строки заказа изменились — данные обновлены. Проверьте заказ и проведите оплату ещё раз");
      else setError(failureText(reason, "Оплата не проведена"));
    } finally {
      setBusy(false);
    }
  }

  const title = withFile ? "Внести ПП" : "Оплачено";
  return (
    <PageWindow label={`${title} · ${number}`} close={() => { if (!busy) onClose(); }} width={820}>
      <form className="panel buy-window" onSubmit={(event) => void submit(event)}>
        <div className="buy-window-head">
          <h2>{title} · {number}</h2>
          <span className="muted">{card.supplier}</span>
          <span className="buy-spacer" />
          <button type="button" className="link-button" disabled={busy} onClick={onClose}>Закрыть</button>
        </div>
        {!order && !error && <div className="empty">Загрузка…</div>}
        {order && (
          <>
            <p className="buy-window-meta">{lines.length} стр. к оплате · <b>{formatAmount(total)} ₽</b></p>
            <div className="table-wrap">
              <table className="grid" aria-label="Строки к оплате">
                <thead><tr><th>Позиция</th><th className="num">Кол-во</th><th className="num">Цена с НДС, ₽</th><th className="num">Сумма, ₽</th></tr></thead>
                <tbody>
                  {lines.map((line) => (
                    <tr key={line.id}>
                      <td className="text"><strong>{line.legacy_id}</strong> {line.name}
                        {line.pay_missing.length > 0 && <small className="tone-warn"> не хватает: {line.pay_missing.join(", ")}</small>}
                      </td>
                      <td className="num">{formatQty(line.quantity, line.unit)}</td>
                      <td className="num">{formatAmount(line.price_with_vat) || "—"}</td>
                      <td className="num">{formatAmount(line.actual_sum ?? line.calculated_sum) || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {withFile && (
              <div className="buy-file">
                <span>Платёжное поручение *</span>
                {stored ? <span className="buy-file-name">«{stored}» приложено</span> : (
                  <span className="buy-file-pick">
                    <label className="secondary small">
                      {file ? "Другой файл" : "Выбрать файл"}
                      <input type="file" accept=".pdf,image/*" disabled={busy} aria-label="Файл платёжного поручения" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
                    </label>
                    <span className="buy-file-name">{file ? file.name : "файл не выбран"}</span>
                  </span>
                )}
              </div>
            )}
            {missing.length > 0 ? (
              <div className="warning-inline" role="alert">
                Оплата закрыта: у {missing.length === 1 ? "строки" : "строк"} {missing.map((line) => line.legacy_id).join(", ")} не заполнено нужное для оплаты — откройте заказ в «Счетах и поставках».
              </div>
            ) : (
              <p className="warning-inline">Оплату нельзя отменить: строки уйдут «В пути», их начнёт ждать приёмка. Дата оплаты — сегодня.</p>
            )}
            {prompt && (
              <label className="inline-rate">{prompt.message}. Курс, ₽ за 1 {prompt.currency} *
                <input name="manual_rate" inputMode="decimal" autoComplete="off" required value={rate} onChange={(event) => setRate(event.target.value)} />
              </label>
            )}
          </>
        )}
        {error && <div className="error-banner" role="alert">{error}</div>}
        <div className="move-actions">
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || !ready}>{busy ? "Проводим…" : `${withFile ? "Внести ПП и оплатить" : "Оплачено"} (${lines.length})`}</button>
        </div>
      </form>
    </PageWindow>
  );
}
