import { useCallback, useEffect, useState, type ReactNode } from "react";
import { failureText } from "./api";
import { formatDate } from "./format";
import { urlParam } from "./navigation";
import { PurchasingUpdCard } from "./PurchasingUpdCard";
import {
  EDO_STATUS_LABELS, EMPTY_FILTERS, PDF_STATUS_LABELS, UPD_ORIGIN_FILTERS, UPD_ORIGIN_HINTS, UPD_ORIGIN_LABELS, UPD_STATUS_LABELS, UPD_STATUS_ORDER, UPD_STATUS_TONES,
  UPD_KIND_GROUPS, buyerLabel, fileToBase64, purchasingUpdApi as updApi, rub,
  type EdoFileResult, type PdfFileResult, type SellersImportResult, type UpdCard, type UpdFilters, type UpdList, type UpdSellers,
} from "./purchasingUpdApi";
import { StatusChip } from "./StatusChip";
import "./purchasingDocs.css";

/** `&tab=upd&status=needs_review` (a link from «Сегодня»): the registry opens on that status. */
function filtersFromUrl(): UpdFilters {
  if (urlParam("tab") !== "upd") return EMPTY_FILTERS;
  const wanted = urlParam("status");
  const status = UPD_STATUS_ORDER.find((key) => key === wanted);
  return status ? { ...EMPTY_FILTERS, status } : EMPTY_FILTERS;
}

/** «Реестр УПД»: every closing document — PDFs read by the Google parser and EDO XML from Diadoc —
 *  with filters, the card (PurchasingUpdCard), «Загрузить из ЭДО» (dry run first, then apply) and «Продавцы без
 *  поставщика». `extraBlocks` is mounted at the bottom of the card: the slot for «Счета по этому УПД» from the
 *  documents.chain branch. */
export function PurchasingUpd({ readOnly = false, focusId, extraBlocks }: {
  readOnly?: boolean; focusId?: string; extraBlocks?: (documentId: string) => ReactNode;
} = {}) {
  const [filters, setFilters] = useState<UpdFilters>(filtersFromUrl);
  const [list, setList] = useState<UpdList | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(focusId ?? null);
  const [uploading, setUploading] = useState(false);
  const [pdfUploading, setPdfUploading] = useState(false);
  const [sellersOpen, setSellersOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);

  useEffect(() => { if (focusId) setOpenId(focusId); }, [focusId]);
  useEffect(() => {
    if (openId) return undefined;
    let live = true;
    const timer = setTimeout(() => {
      updApi.list(filters)
        .then((next) => { if (live) { setList(next); setError(""); } })
        .catch((reason) => { if (live) setError(failureText(reason, "Реестр УПД не загрузился")); });
    }, filters.q ? 200 : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [filters, reload, openId]);

  const set = (patch: Partial<UpdFilters>) => setFilters((current) => ({ ...current, page: 1, ...patch }));

  if (openId) {
    return <PurchasingUpdCard id={openId} readOnly={readOnly} extraBlocks={extraBlocks} onOpen={setOpenId}
      onBack={() => { setOpenId(null); refresh(); }} />;
  }

  const inForce = list ? Object.entries(list.counts).filter(([key]) => key !== "duplicate").reduce((sum, [, n]) => sum + n, 0) : 0;
  const pages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;
  const statusChip = (key: UpdFilters["status"], label: string, count: number | undefined) => {
    const active = filters.status === key && !filters.duplicates && !filters.review;
    return (
      <button key={key || "all"} type="button" className={`calendar-chip${active ? " active" : ""}`} aria-pressed={active}
        onClick={() => set({ status: key, duplicates: false, review: false })}>
        {label} {list ? count ?? 0 : ""}
      </button>
    );
  };
  return (
    <section className="panel table-panel upd-registry">
      <div className="panel-heading"><h2>Реестр УПД</h2><span>закрывающие документы: PDF, прочитанные разборщиком, и XML из ЭДО</span></div>
      <div className="inline-action purchasing-toolbar">
        <input type="search" aria-label="Поиск по реестру УПД" placeholder="Продавец, №, ИНН, имя файла" value={filters.q}
          onChange={(event) => set({ q: event.target.value })} />
        {!readOnly && <button type="button" className="secondary" onClick={() => setSellersOpen(true)}>Продавцы без поставщика</button>}
        {!readOnly && <button type="button" className="secondary" onClick={() => setPdfUploading(true)}>Загрузить PDF</button>}
        {!readOnly && <button type="button" className="primary" onClick={() => setUploading(true)}>Загрузить из ЭДО</button>}
      </div>
      <div className="pdoc-tabs">
        <span className="seg-tabs upd-kind" role="group" aria-label="Вид документа">
          <button type="button" className={filters.kind === "" ? "on" : undefined} aria-pressed={filters.kind === ""} onClick={() => set({ kind: "" })}>
            Все виды{list ? ` ${Object.values(list.kinds ?? {}).reduce((sum, n) => sum + n, 0)}` : ""}
          </button>
          {UPD_KIND_GROUPS.map((group) => (
            <button key={group.key} type="button" className={filters.kind === group.key ? "on" : undefined} aria-pressed={filters.kind === group.key}
              title={group.hint} onClick={() => set({ kind: group.key })}>
              {group.label}{list ? ` ${list.kinds?.[group.key] ?? 0}` : ""}
            </button>
          ))}
        </span>
      </div>
      <div className="upd-filters">
        <span className="seg-tabs upd-origin" role="group" aria-label="Источник">
          {UPD_ORIGIN_FILTERS.map(({ key: origin, label }) => (
            <button key={origin || "all"} type="button" className={filters.origin === origin ? "on" : undefined} aria-pressed={filters.origin === origin}
              title={origin ? UPD_ORIGIN_HINTS[origin] : undefined} onClick={() => set({ origin })}>
              {label}{origin && list ? ` ${list.origins[origin] ?? 0}` : ""}
            </button>
          ))}
        </span>
        <label>Покупатель
          <select aria-label="Покупатель" value={filters.buyer} onChange={(event) => set({ buyer: event.target.value })}>
            <option value="">все</option>
            <option value="other">{buyerLabel("other")}</option>
            {(list?.buyers ?? []).map((buyer) => (
              <option key={buyer.key} value={buyer.key}>{buyerLabel(buyer.key)} ({buyer.count})</option>
            ))}
          </select>
        </label>
        <label>Поставщик
          <select aria-label="Поставщик" value={filters.noSupplier ? "none" : filters.supplierId}
            onChange={(event) => set(event.target.value === "none" ? { supplierId: "", noSupplier: true } : { supplierId: event.target.value, noSupplier: false })}>
            <option value="">все поставщики</option>
            <option value="none">без поставщика{list ? ` (${list.no_supplier})` : ""}</option>
            {(list?.suppliers ?? []).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name} ({supplier.count})</option>)}
          </select>
        </label>
        <label>С<input type="date" aria-label="Дата с" value={filters.dateFrom} onChange={(event) => set({ dateFrom: event.target.value })} /></label>
        <label>по<input type="date" aria-label="Дата по" value={filters.dateTo} onChange={(event) => set({ dateTo: event.target.value })} /></label>
      </div>
      <div className="calendar-chips" role="group" aria-label="Статус">
        {statusChip("", "Все", inForce)}
        {UPD_STATUS_ORDER.map((status) => statusChip(status, UPD_STATUS_LABELS[status], list?.counts[status]))}
        <button type="button" className={`calendar-chip${filters.review ? " active" : ""}`} aria-pressed={filters.review}
          title="Сомнения разборщика, а также документы, которые разборщик перечитал после вашей правки или проверки"
          onClick={() => set({ review: !filters.review, duplicates: false, status: "" })}>
          Требует проверки {list ? list.review : ""}
        </button>
        <button type="button" className={`calendar-chip${filters.duplicates ? " active" : ""}`} aria-pressed={filters.duplicates}
          title="Копии документов, которые уже есть в реестре: в общем списке и суммах не участвуют"
          onClick={() => set({ duplicates: !filters.duplicates, review: false, status: "" })}>
          Дубликаты {list ? list.counts.duplicate : ""}
        </button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !list && <div className="empty">Загружаю…</div>}
      {list && filters.duplicates && list.items.length > 0 && (
        <p className="page-sub upd-note">
          Дубликаты — второй экземпляр документа, который уже есть в реестре: PDF того же документа, пришедшего XML из ЭДО;
          повторная отправка; отдельный PDF счёта-фактуры к накладной или акту на ту же дату и сумму. Они не входят в общий
          список и суммы; в карточке видно, копией чего является документ.
        </p>
      )}
      {list && (list.items.length === 0 ? (
        <div className="empty">{list.total === 0 && inForce === 0 && !filters.duplicates
          ? "Реестр пуст. Нажмите «Загрузить из ЭДО» и выберите XML или ZIP из Диадока."
          : "Ничего не найдено — смените фильтр или строку поиска."}</div>
      ) : (
        <ul className="pdoc-grid" aria-label="Реестр УПД">
          {list.items.map((card) => <li key={card.id}><UpdCardTile card={card} onOpen={() => setOpenId(card.id)} /></li>)}
        </ul>
      ))}
      {list && (
        <div className="inline-action upd-summary">
          <span role="status">{list.total} док. · {rub(list.sum_gross)}</span>
          {list.total > list.page_size && <>
            <button type="button" className="secondary small" disabled={filters.page <= 1} onClick={() => setFilters({ ...filters, page: filters.page - 1 })}>Назад</button>
            <span>Страница {filters.page} из {pages}</span>
            <button type="button" className="secondary small" disabled={filters.page >= pages} onClick={() => setFilters({ ...filters, page: filters.page + 1 })}>Вперёд</button>
          </>}
        </div>
      )}
      {uploading && <EdoUploadDialog onClose={() => setUploading(false)} onApplied={refresh} />}
      {pdfUploading && <PdfUploadDialog onClose={() => setPdfUploading(false)} onApplied={refresh} />}
      {sellersOpen && <SellersDialog onClose={() => setSellersOpen(false)} onApplied={refresh} />}
    </section>
  );
}

function UpdCardTile({ card, onOpen }: { card: UpdCard; onOpen: () => void }) {
  const seller = card.supplier_name ?? (card.seller_name || card.file_name);
  const marks = [
    card.manual && "своя версия",
    card.changed_in_parser && "изменён разборщиком",
    card.twin_of && (card.origin === "google" ? "копия — есть XML из ЭДО" : "копия"),
    card.copies > 0 && `копий: ${card.copies}`,
    !card.has_file && card.status !== "duplicate" && "файла нет",
    card.supplier_id === null && card.status !== "duplicate" && "без поставщика",
  ].filter(Boolean);
  return (
    <article className={`pdoc-card clickable${card.status === "duplicate" ? " is-dim" : ""}`} onClick={onOpen}>
      <div className="pdoc-top">
        <span className="pdoc-title">
          <button type="button" className="link-button table-link" onClick={(event) => { event.stopPropagation(); onOpen(); }}>{seller}</button>
        </span>
        <span className="pdoc-chips">
          <StatusChip tone={UPD_STATUS_TONES[card.status]}>{UPD_STATUS_LABELS[card.status]}</StatusChip>
          <StatusChip tone={card.origin === "edo" ? "info" : "neutral"} title={UPD_ORIGIN_HINTS[card.origin]}>{UPD_ORIGIN_LABELS[card.origin]}</StatusChip>
          {card.buyer_kind === "other" && <StatusChip tone="warning" title={card.buyer_note || "документ на другую организацию"}>ИНН {card.buyer_org_inn}</StatusChip>}
          {card.project_reference && <StatusChip tone="neutral">{card.project_reference}</StatusChip>}
        </span>
      </div>
      <div className="pdoc-meta">
        <span>{card.kind || "—"}</span>
        <span>№ {card.number || "—"}</span>
        <span>{formatDate(card.date) || "—"}</span>
        <span className="pdoc-sum">{rub(card.total_gross)}</span>
      </div>
      <div className="pdoc-sub">
        <span>{card.line_count} поз.</span>
        {marks.map((mark) => <span key={String(mark)}>{mark}</span>)}
      </div>
      {card.seller_note && <div className="pdoc-note">{card.seller_note}{card.seller_org_inn ? ` → ${card.seller_org_inn}` : ""}</div>}
      {card.issues.length > 0 && card.status !== "parsed" && <div className="pdoc-issues">{card.issues.join("; ")}</div>}
      <div className="pdoc-file" title={card.display_name || card.file_name}>{card.display_name || card.file_name}</div>
    </article>
  );
}

type Picked = { file: File; result?: EdoFileResult[]; error?: string };

function EdoUploadDialog({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [picked, setPicked] = useState<Picked[]>([]);
  const [stage, setStage] = useState<"pick" | "checking" | "checked" | "applying" | "done">("pick");

  // One file per request, one after another: each body stays under the server's ceiling.
  async function run(files: Picked[], dryRun: boolean): Promise<Picked[]> {
    const out: Picked[] = [];
    for (const item of files) {
      try {
        const content = await fileToBase64(item.file);
        const answer = await updApi.importEdo({ file_name: item.file.name, content_base64: content }, dryRun);
        out.push({ file: item.file, result: answer.files });
      } catch (reason) {
        out.push({ file: item.file, error: failureText(reason, "Файл не принят") });
      }
    }
    return out;
  }

  async function check(files: File[]) {
    setPicked([]);
    setStage("checking");
    setPicked(await run(files.map((file) => ({ file })), true));
    setStage("checked");
  }

  // Only files the check found something to write in go again; refused and unchanged ones keep their verdict.
  const writes = (item: Picked) => !item.error && (item.result ?? []).some((row) => ["created", "updated", "twin"].includes(row.status));

  async function apply() {
    setStage("applying");
    const kept = picked.filter((item) => !writes(item));
    const applied = await run(picked.filter(writes), false);
    setPicked([...applied, ...kept]);
    setStage("done");
    onApplied();
  }

  const rows = picked.flatMap((item) => item.error
    ? [{ name: item.file.name, status: "error" as const, message: item.error }]
    : (item.result ?? []).map((result) => ({ ...result })));
  const writable = rows.filter((row) => row.status === "created" || row.status === "updated" || row.status === "twin").length;
  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Загрузить из ЭДО">
        <h2>Загрузить из ЭДО</h2>
        <p className="page-sub">
          XML документов (ON_NSCHFDOPPR…, ON_NKORSCHFDOPPR…) или ZIP из Диадока («Скачать → документооборот целиком»):
          подписи, карточки и PDF внутри архива пропускаются. Сначала проверка — в реестр ничего не пишется.
        </p>
        <label className="upd-file-pick">Файлы
          <input type="file" aria-label="Файлы из ЭДО" accept=".xml,.zip" multiple disabled={stage === "checking" || stage === "applying"}
            onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) void check(files); }} />
        </label>
        {(stage === "checking" || stage === "applying") && <div className="empty" role="status">{stage === "checking" ? "Проверяю…" : "Загружаю…"}</div>}
        {rows.length > 0 && (
          <>
            <p className="page-sub" role="status">
              {stage === "done" ? "Готово: " : "Будет: "}
              {(["created", "updated", "twin", "unchanged", "skipped", "error"] as const)
                .map((status) => [status, rows.filter((row) => row.status === status).length] as const)
                .filter(([, count]) => count > 0).map(([status, count]) => `${EDO_STATUS_LABELS[status]} — ${count}`).join(", ")}.
            </p>
            <div className="table-wrap history-table">
              <table className="grid" aria-label="Документы из ЭДО">
                <thead><tr><th>Файл</th><th>Итог</th><th>№</th><th>Продавец</th><th>Пояснение</th></tr></thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={`${row.name}-${index}`}>
                      <td className="text">{row.name}</td>
                      <td><StatusChip tone={row.status === "error" ? "danger" : row.status === "skipped" || row.status === "twin" ? "warning" : row.status === "unchanged" ? "neutral" : "success"}>{EDO_STATUS_LABELS[row.status]}</StatusChip></td>
                      <td>{"number" in row ? row.number || "—" : "—"}</td>
                      <td className="text">{"seller_name" in row ? row.seller_name || "—" : "—"}</td>
                      <td className="text">{row.message ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose} disabled={stage === "applying"}>Закрыть</button>
          {stage === "checked" && <button type="button" className="primary" disabled={writable === 0} onClick={() => void apply()}>Загрузить в реестр</button>}
        </div>
      </div>
    </div>
  );
}

/** «Загрузить PDF»: scans and electronic PDFs go into the registry «В очереди»; the server's model reads each one
 *  (money included) in the background, the cards fill in as it answers. */
function PdfUploadDialog({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [rows, setRows] = useState<PdfFileResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [modelReady, setModelReady] = useState(true);

  // One file per request, one after another: each body stays under the server's ceiling.
  async function send(files: File[]) {
    setRows([]);
    setBusy(true);
    const out: PdfFileResult[] = [];
    for (const file of files) {
      try {
        const answer = await updApi.importPdf({ file_name: file.name, content_base64: await fileToBase64(file) });
        out.push(...answer.files);
        if (!answer.model_ready) setModelReady(false);
      } catch (reason) {
        out.push({ name: file.name, status: "error", message: failureText(reason, "Файл не принят") });
      }
      setRows([...out]);
    }
    setBusy(false);
    onApplied();
  }

  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Загрузить PDF">
        <h2>Загрузить PDF</h2>
        <p className="page-sub">
          УПД, счёт-фактура, ТОРГ-12, акт или М-15 — скан или электронный PDF. Документ встаёт в очередь, разборщик читает
          его на сервере (шапку, строки, цены и суммы) и проверяет арифметику; результат появится в карточке через минуту-две.
          Метка в имени файла главнее мнения модели: «М15» — накладная М-15, «ТН» — ТОРГ-12 вместе со счётом-фактурой.
        </p>
        <label className="upd-file-pick">Файлы
          <input type="file" aria-label="Файлы PDF" accept=".pdf,application/pdf" multiple disabled={busy}
            onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) void send(files); }} />
        </label>
        {busy && <div className="empty" role="status">Загружаю…</div>}
        {!modelReady && <div className="error-banner" role="alert">Ключ модели не задан («Склад» → «Распознавание УПД»): документы ждут в очереди.</div>}
        {rows.length > 0 && (
          <div className="table-wrap history-table">
            <table className="grid" aria-label="Загруженные PDF">
              <thead><tr><th className="grow">Файл</th><th>Итог</th><th>Пояснение</th></tr></thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={`${row.name}-${index}`}>
                    <td className="text">{row.name}</td>
                    <td><StatusChip tone={row.status === "error" ? "danger" : row.status === "unchanged" ? "neutral" : "success"}>{PDF_STATUS_LABELS[row.status]}</StatusChip></td>
                    <td className="text">{row.message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose} disabled={busy}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}

function SellersDialog({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [sellers, setSellers] = useState<UpdSellers | null>(null);
  const [plan, setPlan] = useState<SellersImportResult | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<SellersImportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    Promise.all([updApi.sellers(), updApi.importSellers(true)])
      .then(([list, preview]) => {
        if (!live) return;
        setSellers(list);
        setPlan(preview);
        setChosen(new Set(list.sellers.map((seller) => seller.inn)));
      })
      .catch((reason) => { if (live) setError(failureText(reason, "Продавцы не загрузились")); });
    return () => { live = false; };
  }, []);
  const toggle = (inn: string) => setChosen((current) => {
    const next = new Set(current);
    if (next.has(inn)) next.delete(inn); else next.add(inn);
    return next;
  });
  const apply = async () => {
    setBusy(true);
    setError("");
    try {
      setDone(await updApi.importSellers(false, [...chosen]));
      onApplied();
    } catch (reason) {
      setError(failureText(reason, "Поставщики не заведены"));
    } finally {
      setBusy(false);
    }
  };
  const nameOf = (inn: string, fallback: string) => plan?.created.find((item) => item.inn === inn)?.name ?? fallback;
  const skippedOf = (inn: string) => plan?.skipped.find((item) => item.inn === inn)?.reason;
  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Продавцы без поставщика">
        <h2>Продавцы без поставщика</h2>
        <p className="page-sub">
          Продавцы действующих документов с верным ИНН, которых нет в справочнике поставщиков: копии, М-15, «Не документ
          закупки» и документы в очереди не считаются, ИНН с ошибкой распознавания засчитан настоящему.
          {sellers && sellers.skipped_invalid_inn > 0 ? ` Ещё ${sellers.skipped_invalid_inn} — с ИНН, который не проходит проверку: исправьте шапку документа.` : ""}
        </p>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!sellers && !error && <div className="empty">Собираю продавцов…</div>}
        {done && <div className="success-banner" role="status">Заведено поставщиков: {done.created.length}, документов привязано: {done.suppliers_changed}.{done.skipped.length ? ` Пропущено: ${done.skipped.length}.` : ""}</div>}
        {sellers && !done && (sellers.sellers.length === 0 ? <div className="empty">Все продавцы уже в справочнике.</div> : (
          <div className="table-wrap history-table">
            <table className="grid" aria-label="Продавцы без поставщика">
              <thead><tr><th /><th>Будет заведён как</th><th>ИНН</th><th className="num">Документов</th><th className="num">Сумма с НДС</th><th>Последний</th><th className="num">На проверке</th></tr></thead>
              <tbody>
                {sellers.sellers.map((seller) => (
                  <tr key={seller.inn}>
                    <td><input type="checkbox" aria-label={`Завести ${seller.name || seller.inn}`} checked={chosen.has(seller.inn)} onChange={() => toggle(seller.inn)} /></td>
                    <td className="text">{nameOf(seller.inn, seller.name)}{skippedOf(seller.inn) && <small className="upd-issues">{skippedOf(seller.inn)}</small>}</td>
                    <td>{seller.inn}</td>
                    <td className="num">{seller.documents}</td>
                    <td className="num">{rub(seller.total_gross)}</td>
                    <td>{formatDate(seller.last_date) || "—"}</td>
                    <td className="num">{seller.needs_review}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
          {!done && <button type="button" className="primary" disabled={busy || chosen.size === 0} onClick={() => void apply()}>Завести поставщиков ({chosen.size})</button>}
        </div>
      </div>
    </div>
  );
}
