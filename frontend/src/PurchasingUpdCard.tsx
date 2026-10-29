import { useEffect, useMemo, useState, type ReactNode } from "react";
import { failureText } from "./api";
import { formatDate, formatNumber } from "./format";
import { purchasingDirectoryApi, type Supplier } from "./purchasingDirectoryApi";
import {
  UPD_ORIGIN_HINTS, UPD_ORIGIN_LABELS, UPD_STATUS_LABELS, UPD_STATUS_TONES, buyerLabel, docIssues, inputToDecimal, inputToKop,
  kopToInput, lineIssues, purchasingUpdApi as updApi, rub,
  type LineFacts, type UpdBrief, type UpdDetail, type UpdHeader, type UpdLine, type UpdLineInput,
} from "./purchasingUpdApi";
import { StatusChip } from "./StatusChip";

/** The card of one closing document (port of Roller_Buyer UpdDetailPage): the header in force beside the parser's,
 *  the lines, «Исправить» (the buyer's own header and lines), «Проверено», the supplier and the project, the twins.
 *  `extraBlocks(documentId)` renders under the lines — the slot for «Счета по этому УПД». */
export function PurchasingUpdCard({ id, readOnly, onBack, onOpen, extraBlocks }: {
  id: string; readOnly: boolean; onBack: () => void; onOpen: (id: string) => void;
  extraBlocks?: (documentId: string) => ReactNode;
}) {
  const [doc, setDoc] = useState<UpdDetail | null>(null);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    let live = true;
    setDoc(null);
    setLoadError("");
    setEditing(false);
    updApi.card(id)
      .then((next) => { if (live) setDoc(next); })
      .catch((reason) => { if (live) setLoadError(failureText(reason, "Документ не загрузился")); });
    return () => { live = false; };
  }, [id]);

  const act = async (run: () => Promise<UpdDetail>, fallback: string) => {
    setBusy(true);
    setError("");
    try {
      setDoc(await run());
      return true;
    } catch (reason) {
      setError(failureText(reason, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const back = <button type="button" className="link-button upd-back" onClick={onBack}>← Реестр УПД</button>;
  if (loadError) return <section className="panel upd-card-view">{back}<div className="error-banner" role="alert">{loadError}</div></section>;
  if (!doc) return <section className="panel upd-card-view">{back}<div className="empty">Загружаю…</div></section>;
  const canAct = !readOnly && !editing && doc.status !== "waiting" && doc.status !== "duplicate";
  return (
    <section className="panel upd-card-view" aria-label="Карточка УПД">
      <div className="upd-card-head">
        {back}
        <h2>{doc.header.seller_name || doc.file_name}</h2>
        <StatusChip tone={UPD_STATUS_TONES[doc.status]}>{UPD_STATUS_LABELS[doc.status]}</StatusChip>
        <StatusChip tone={doc.origin === "edo" ? "info" : "neutral"} title={UPD_ORIGIN_HINTS[doc.origin]}>{UPD_ORIGIN_LABELS[doc.origin]}</StatusChip>
        <span className="upd-spacer" />
        {doc.file && (
          <a className="secondary upd-file-link" href={updApi.fileUrl(doc.id)} target="_blank" rel="noopener noreferrer"
            title={doc.file.kind === "xml" ? "Исходный XML в формате ФНС: откроется в Диадоке или 1С" : "Файл откроется в новой вкладке"}>
            {doc.file.kind === "xml" ? "Скачать XML" : "Открыть файл"}
          </a>
        )}
        {!doc.file && doc.drive_url && !readOnly && <a className="secondary upd-file-link" href={doc.drive_url} target="_blank" rel="noopener noreferrer">Открыть на Диске</a>}
        {canAct && doc.manual && (
          <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => updApi.dropManual(doc.id), "Версия разборщика не вернулась")}>
            Вернуть версию разборщика
          </button>
        )}
        {canAct && <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(true)}>Исправить</button>}
        {!readOnly && !editing && doc.origin === "google" && doc.file && doc.status !== "waiting" && (
          <button type="button" className="secondary" disabled={busy} title="PDF прочитает модель сервера заново; ваши исправления и отметка «Проверено» останутся"
            onClick={() => void act(() => updApi.reparse(doc.id), "Разбор не запустился")}>
            Разобрать заново
          </button>
        )}
        {doc.status === "waiting" && (
          <button type="button" className="secondary" disabled={busy} title="Документ в очереди разборщика — карточка заполнится, когда модель ответит"
            onClick={() => void act(() => updApi.card(doc.id), "Документ не загрузился")}>
            Обновить
          </button>
        )}
        {canAct && (doc.verified ? (
          <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => updApi.setVerified(doc.id, false), "Отметка не снялась")}>
            Снять отметку «Проверено»
          </button>
        ) : (
          <button type="button" className="primary" disabled={busy} onClick={() => void act(() => updApi.setVerified(doc.id, true), "Отметка не поставилась")}>
            Проверено
          </button>
        ))}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {doc.changed_in_parser && (
        <div className="warning-banner" role="status">
          Разборщик перечитал документ после вашей правки или проверки. Сравните с его версией и решите, оставить ли свою.
        </div>
      )}
      <TwinsBlock doc={doc} onOpen={onOpen} />
      {editing ? (
        <UpdEditor doc={doc} onCancel={() => setEditing(false)}
          onSaved={(next) => { setDoc(next); setEditing(false); }} />
      ) : (
        <>
          <div className="upd-card-body">
            <HeaderTable doc={doc} />
            <div className="upd-side">
              <Facts doc={doc} />
              <SupplierField doc={doc} readOnly={readOnly} busy={busy}
                onPick={(supplierId) => act(() => updApi.setSupplier(doc.id, supplierId), "Поставщик не сохранился")} />
              <ProjectField doc={doc} readOnly={readOnly} busy={busy}
                onSave={(reference) => act(() => updApi.setProject(doc.id, reference), "Проект не сохранился")} />
            </div>
          </div>
          {doc.issues.length > 0 && (
            <div className="warning-banner upd-issues-banner" role="status">
              <strong>Замечания</strong>
              <ul>{doc.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
            </div>
          )}
          <LinesTable lines={doc.lines} label={doc.manual ? "Позиции — своя версия" : "Позиции"} />
          {doc.lines.length === 0 && <div className="empty">Позиций нет. Если в документе они есть — «Исправить» и добавьте строки.</div>}
          {extraBlocks?.(doc.id)}
        </>
      )}
    </section>
  );
}

/* ---- header in force beside the parser's ---- */

type HeaderKey = keyof UpdHeader;
const HEADER_FIELDS: { key: HeaderKey; label: string; money?: boolean; date?: boolean }[] = [
  { key: "number", label: "№ документа" },
  { key: "date", label: "Дата", date: true },
  { key: "seller_name", label: "Продавец" },
  { key: "seller_inn", label: "ИНН продавца" },
  { key: "seller_kpp", label: "КПП продавца" },
  { key: "buyer_inn", label: "ИНН покупателя" },
  { key: "total_net", label: "Итого без НДС", money: true },
  { key: "total_vat", label: "НДС", money: true },
  { key: "total_gross", label: "Итого с НДС", money: true },
];

function shown(field: (typeof HEADER_FIELDS)[number], value: UpdHeader[HeaderKey]): string {
  if (field.money) return rub(value as number | null);
  if (field.date) return formatDate(value as string | null) || "—";
  return String(value ?? "") || "—";
}

function HeaderTable({ doc }: { doc: UpdDetail }) {
  return (
    <div className="table-wrap upd-header-table">
      <table className="grid" aria-label="Шапка документа">
        <thead><tr><th>Реквизит</th><th>{doc.manual ? "Своя версия" : "В документе"}</th>{doc.manual && <th>Разборщик</th>}</tr></thead>
        <tbody>
          {HEADER_FIELDS.map((field) => {
            const value = doc.header[field.key];
            const parsed = doc.parser_header[field.key];
            const differs = doc.manual && (value ?? "") !== (parsed ?? "");
            return (
              <tr key={field.key} className={differs ? "upd-differs" : undefined}>
                <th scope="row">{field.label}</th>
                <td className={field.money ? "num" : "text"}>
                  {shown(field, value)}
                  {field.key === "seller_inn" && doc.seller_note && (
                    <small className="upd-note">{doc.seller_note}{doc.seller_org_inn ? ` — продавец: ИНН ${doc.seller_org_inn}` : ""}</small>
                  )}
                  {field.key === "buyer_inn" && (
                    <small className="upd-note">
                      {doc.buyer_kind === "ours" ? "мы" : doc.buyer_kind === "other" ? buyerLabel(doc.buyer_org_inn ?? "unknown") : "покупатель не указан"}
                      {doc.buyer_note ? ` · ${doc.buyer_note}` : ""}
                    </small>
                  )}
                </td>
                {doc.manual && <td className={field.money ? "num" : "text"}>{shown(field, parsed)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Facts({ doc }: { doc: UpdDetail }) {
  const version = doc.manual ? "своя (исправлена вручную)" : doc.origin !== "google" ? doc.model : `Разборщик, ${doc.model || "модель не указана"}`;
  const rows: [string, ReactNode][] = [
    ["Вид", `${doc.kind || "—"}${doc.tag ? ` (метка «${doc.tag}»)` : ""}`],
    ["Источник", `${UPD_ORIGIN_LABELS[doc.origin]} — ${UPD_ORIGIN_HINTS[doc.origin]}`],
    ["Основание", doc.basis || "—"],
    ["Платёжный документ", doc.payment_doc || "—"],
    ["Версия", version],
    ["Проверен", doc.verified_at ? formatDate(doc.verified_at) : "нет"],
    ["Файл", doc.file ? `${doc.file.name} · ${formatNumber(Math.max(1, Math.round(doc.file.size / 1024)))} КБ` : `${doc.file_name} · файла в базе нет`],
  ];
  if (doc.file_amount !== null) rows.push(["Сумма в имени файла", rub(doc.file_amount)]);
  return (
    <dl className="upd-facts">
      {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

function TwinsBlock({ doc, onOpen }: { doc: UpdDetail; onOpen: (id: string) => void }) {
  const link = (brief: UpdBrief) => (
    <button type="button" className="link-button upd-inline-link" onClick={() => onOpen(brief.id)}>
      {UPD_ORIGIN_LABELS[brief.origin]} «{brief.file_name}»{brief.number ? `, № ${brief.number}` : ""}{brief.date ? ` от ${formatDate(brief.date)}` : ""}
    </button>
  );
  if (doc.twins.main) {
    return (
      <div className="warning-banner upd-twins" role="status">
        Этот документ есть в реестре ещё раз — действующая копия: {link(doc.twins.main)}
        {doc.origin === "google" && doc.twins.main.origin === "edo" ? " (точный XML из ЭДО)" : ""}. Эта копия в суммах не учитывается.
      </div>
    );
  }
  if (doc.twins.copies.length === 0) return null;
  return (
    <div className="success-banner upd-twins" role="status">
      Этот экземпляр действует; его копии в реестре:{" "}
      {doc.twins.copies.map((copy, index) => <span key={copy.id}>{index > 0 ? ", " : ""}{link(copy)}</span>)}.
    </div>
  );
}

/* ---- supplier and project ---- */

function SupplierField({ doc, readOnly, busy, onPick }: {
  doc: UpdDetail; readOnly: boolean; busy: boolean; onPick: (supplierId: string | null) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<Supplier[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || options) return undefined;
    let live = true;
    purchasingDirectoryApi.suppliers({ q: "", category: "", key: false, archived: false })
      .then((next) => { if (live) setOptions(next); })
      .catch((reason) => { if (live) setError(failureText(reason, "Справочник поставщиков не загрузился")); });
    return () => { live = false; };
  }, [open, options]);
  const needle = q.trim().toLowerCase();
  const matches = (options ?? [])
    .filter((supplier) => !needle || supplier.name.toLowerCase().includes(needle) || (supplier.inn ?? "").includes(needle))
    .slice(0, 30);
  const how = doc.supplier_id === null ? "" : doc.supplier_manual ? "выбран вручную" : "по ИНН продавца";
  return (
    <div className="upd-field">
      <span className="upd-field-label">Поставщик в справочнике</span>
      <span>{doc.supplier_name ?? "не связан"}{how && <small className="upd-note">{how}</small>}</span>
      {!readOnly && (
        <span className="upd-field-actions">
          <button type="button" className="secondary small" disabled={busy} onClick={() => setOpen(!open)}>{open ? "Свернуть" : "Сменить поставщика"}</button>
          {doc.supplier_manual && (
            <button type="button" className="secondary small" disabled={busy} onClick={() => void onPick(null)}>Связать по ИНН</button>
          )}
        </span>
      )}
      {open && (
        <div className="upd-picker">
          <input type="search" aria-label="Поиск поставщика" placeholder="Название или ИНН" value={q} onChange={(event) => setQ(event.target.value)} />
          {error && <div className="error-banner" role="alert">{error}</div>}
          {!error && !options && <div className="empty">Загружаю справочник…</div>}
          {options && matches.length === 0 && <div className="empty">Не найдено. Заведите поставщика в «Поставщики» или через «Продавцы без поставщика».</div>}
          <ul className="upd-picker-list">
            {matches.map((supplier) => (
              <li key={supplier.id}>
                <button type="button" className="link-button" disabled={busy || supplier.id === doc.supplier_id}
                  onClick={() => { void onPick(supplier.id).then((ok) => { if (ok) setOpen(false); }); }}>
                  {supplier.name}
                </button>
                {supplier.inn && <small className="upd-note">ИНН {supplier.inn}</small>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ProjectField({ doc, readOnly, busy, onSave }: {
  doc: UpdDetail; readOnly: boolean; busy: boolean; onSave: (reference: string | null) => Promise<boolean>;
}) {
  const [value, setValue] = useState(doc.project_reference ?? "");
  useEffect(() => { setValue(doc.project_reference ?? ""); }, [doc.project_reference]);
  const changed = value.trim() !== (doc.project_reference ?? "");
  return (
    <div className="upd-field">
      <span className="upd-field-label">Проект</span>
      {readOnly ? <span>{doc.project_reference ?? "—"}</span> : (
        <span className="upd-field-actions">
          <input aria-label="Проект документа" placeholder="591К.3 или ВП-001" value={value} onChange={(event) => setValue(event.target.value)} />
          <button type="button" className="secondary small" disabled={busy || !changed} onClick={() => void onSave(value.trim() || null)}>Сохранить</button>
        </span>
      )}
    </div>
  );
}

/* ---- lines ---- */

function vatText(rate: string): string {
  if (!rate) return "—";
  return /без/i.test(rate) || rate.includes("%") || rate.includes("/") ? rate : `${rate}%`;
}

function LinesTable({ lines, label }: { lines: UpdLine[]; label: string }) {
  if (lines.length === 0) return null;
  return (
    <div className="table-wrap data-table upd-lines">
      <table className="grid wide" aria-label={label}>
        <thead>
          <tr>
            <th className="num">№</th><th className="grow">Наименование</th><th>Артикул</th><th>Ед.</th><th className="num">Кол-во</th>
            <th className="num">Цена без НДС</th><th className="num">Без НДС</th><th>Ставка</th><th className="num">НДС</th>
            <th className="num">С НДС</th><th>Замечания</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.seq} className={line.issues.length ? "upd-row-bad" : undefined}>
              <td className="num">{line.seq}</td>
              <td className="text">{line.name}{line.is_service && <small className="upd-note">услуга</small>}</td>
              <td>{line.article || "—"}</td>
              <td>{line.unit || "—"}</td>
              <td className="num">{line.quantity === null ? "—" : formatNumber(line.quantity, { digits: 6 })}</td>
              <td className="num">{line.price === null ? "—" : formatNumber(line.price, { digits: 6, minDigits: 2 })}</td>
              <td className="num">{rub(line.net)}</td>
              <td>{vatText(line.vat_rate)}</td>
              <td className="num">{rub(line.vat)}</td>
              <td className="num">{rub(line.gross)}</td>
              <td className="text">
                {line.issues.join("; ")}
                {line.fixes.length > 0 && <small className="upd-note">исправлено разборщиком: {line.fixes.join("; ")}</small>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---- the buyer's version: header and lines ---- */

/** Cells that can be shifted, in the order of the UPD table columns. */
const SLOTS = ["name", "article", "unit", "quantity", "price", "net", "vat_rate", "vat", "gross"] as const;
type Slot = (typeof SLOTS)[number];
const SLOT_LABELS: Record<Slot, string> = {
  name: "Наименование", article: "Артикул", unit: "Ед.", quantity: "Кол-во", price: "Цена без НДС", net: "Без НДС",
  vat_rate: "Ставка", vat: "НДС", gross: "С НДС",
};
const MONEY_SLOTS = ["net", "vat", "gross"] as const;

interface DraftLine { seq: string; cells: Record<Slot, string>; is_service: boolean }

function toDraft(header: UpdHeader, lines: UpdLine[]) {
  const draftHeader = {} as Record<HeaderKey, string>;
  for (const field of HEADER_FIELDS) {
    const value = header[field.key];
    draftHeader[field.key] = field.money ? kopToInput(value as number | null) : String(value ?? "");
  }
  const rows: DraftLine[] = lines.map((line) => ({
    seq: String(line.seq), is_service: line.is_service,
    cells: {
      name: line.name, article: line.article, unit: line.unit, quantity: line.quantity ?? "", price: line.price ?? "",
      net: kopToInput(line.net), vat_rate: line.vat_rate, vat: kopToInput(line.vat), gross: kopToInput(line.gross),
    },
  }));
  return { header: draftHeader, rows };
}

/** Draft → the body of the save, or the first problem in words. */
function fromDraft(header: Record<HeaderKey, string>, rows: DraftLine[]): { header: UpdHeader; lines: UpdLineInput[] } | string {
  const out: Record<string, unknown> = {};
  for (const field of HEADER_FIELDS) {
    const text = header[field.key].trim();
    if (field.money) {
      const value = inputToKop(text);
      if (Number.isNaN(value)) return `«${field.label}»: не число`;
      out[field.key] = value;
    } else if (field.date) {
      out[field.key] = text || null;
    } else {
      out[field.key] = text;
    }
  }
  const lines: UpdLineInput[] = [];
  for (const [index, row] of rows.entries()) {
    const seq = Number(row.seq);
    if (!Number.isInteger(seq) || seq < 1) return `Строка ${index + 1}: № п/п — целое число`;
    const quantity = inputToDecimal(row.cells.quantity);
    const price = inputToDecimal(row.cells.price);
    const money = MONEY_SLOTS.map((slot) => inputToKop(row.cells[slot]));
    if (quantity === false || price === false || money.some((value) => Number.isNaN(value))) return `Строка ${seq}: в числовой ячейке не число`;
    lines.push({
      seq, name: row.cells.name.trim(), article: row.cells.article.trim(), unit: row.cells.unit.trim(), quantity, price,
      net: money[0], vat_rate: row.cells.vat_rate.trim().replace("%", ""), vat: money[1], gross: money[2], is_service: row.is_service,
    });
  }
  return { header: out as unknown as UpdHeader, lines };
}

function rowFacts(row: DraftLine): LineFacts {
  const number = (text: string) => { const value = inputToDecimal(text); return value === null || value === false ? null : Number(value); };
  const kopecks = (text: string) => { const value = inputToKop(text); return value === null || Number.isNaN(value) ? null : value; };
  return {
    quantity: number(row.cells.quantity), price: number(row.cells.price), net: kopecks(row.cells.net),
    vat_rate: row.cells.vat_rate.replace("%", ""), vat: kopecks(row.cells.vat), gross: kopecks(row.cells.gross),
  };
}

/** Fill what can be computed from the neighbours — the same rules as the Google parser. */
function fillRow(row: DraftLine): DraftLine {
  const facts = rowFacts(row);
  const cells = { ...row.cells };
  const rate = /без/i.test(facts.vat_rate) ? 0 : facts.vat_rate === "" ? null : Number(facts.vat_rate);
  let { net, vat, gross } = facts;
  if (net === null && facts.quantity !== null && facts.price !== null) net = Math.round(facts.quantity * facts.price * 100);
  if (net === null && gross !== null && vat !== null) net = gross - vat;
  if (vat === null && net !== null && gross !== null) vat = gross - net;
  if (vat === null && net !== null && rate !== null && Number.isFinite(rate)) vat = Math.round((net * rate) / 100);
  if (gross === null && net !== null && vat !== null) gross = net + vat;
  cells.net = kopToInput(net);
  cells.vat = kopToInput(vat);
  cells.gross = kopToInput(gross);
  if (cells.price === "" && net !== null && facts.quantity) cells.price = String(Math.round((net / facts.quantity) * 100) / 10000);
  return { ...row, cells };
}

function UpdEditor({ doc, onSaved, onCancel }: { doc: UpdDetail; onSaved: (next: UpdDetail) => void; onCancel: () => void }) {
  const initial = useMemo(() => toDraft(doc.header, doc.lines), [doc]);
  const [header, setHeader] = useState(initial.header);
  const [rows, setRows] = useState<DraftLine[]>(initial.rows);
  const [focus, setFocus] = useState<{ row: number; slot: Slot } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const setCell = (row: number, slot: Slot, value: string) =>
    setRows((current) => current.map((item, index) => (index === row ? { ...item, cells: { ...item.cells, [slot]: value } } : item)));

  /** An empty cell at the focused one: everything from it moves one column right (OCR lost a column). */
  function insertGap() {
    if (!focus) return;
    const row = rows[focus.row];
    if (row.cells[SLOTS[SLOTS.length - 1]] !== "") {
      setMessage("Последняя ячейка строки занята — сдвигать вправо некуда. Освободите её.");
      return;
    }
    const cells = { ...row.cells };
    for (let index = SLOTS.length - 1; index > SLOTS.indexOf(focus.slot); index -= 1) cells[SLOTS[index]] = cells[SLOTS[index - 1]];
    cells[focus.slot] = "";
    setRows((current) => current.map((item, index) => (index === focus.row ? { ...item, cells } : item)));
    setMessage("");
  }

  /** The focused cell goes: everything to its right moves one column left (OCR made up a column). */
  function removeCell() {
    if (!focus) return;
    const cells = { ...rows[focus.row].cells };
    for (let index = SLOTS.indexOf(focus.slot); index < SLOTS.length - 1; index += 1) cells[SLOTS[index]] = cells[SLOTS[index + 1]];
    cells[SLOTS[SLOTS.length - 1]] = "";
    setRows((current) => current.map((item, index) => (index === focus.row ? { ...item, cells } : item)));
    setMessage("");
  }

  function addRow() {
    const next = rows.reduce((max, row) => Math.max(max, Number(row.seq) || 0), 0) + 1;
    const cells = Object.fromEntries(SLOTS.map((slot) => [slot, ""])) as Record<Slot, string>;
    setRows((current) => [...current, { seq: String(next), is_service: false, cells }]);
  }

  const facts = rows.map(rowFacts);
  const draft = fromDraft(header, rows);
  const pending = typeof draft === "string" ? [draft] : docIssues(doc.kind, draft.header, facts);

  async function save() {
    if (typeof draft === "string") {
      setMessage(draft);
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      onSaved(await updApi.saveManual(doc.id, { ...draft, parser_updated: doc.parser_updated, manual_saved_at: doc.manual_saved_at }));
    } catch (reason) {
      setMessage(failureText(reason, "Своя версия не сохранилась"));
      setBusy(false);
    }
  }

  return (
    <div className="upd-editor">
      <div className="upd-header-form">
        {HEADER_FIELDS.map((field) => (
          <label key={field.key}>{field.label}
            <input aria-label={field.label} type={field.date ? "date" : "text"} inputMode={field.money ? "decimal" : undefined}
              className={field.money ? "num" : undefined} value={header[field.key]}
              onChange={(event) => setHeader((current) => ({ ...current, [field.key]: event.target.value }))} />
          </label>
        ))}
      </div>
      <div className="inline-action upd-editor-tools">
        <button type="button" className="secondary small" disabled={!focus} onClick={insertGap} title="Всё правее выбранной ячейки сдвигается на графу вправо">
          Вставить пустую ячейку →
        </button>
        <button type="button" className="secondary small" disabled={!focus} onClick={removeCell} title="Всё правее выбранной ячейки сдвигается на графу влево">
          ← Удалить ячейку со сдвигом
        </button>
        <button type="button" className="secondary small" onClick={() => setRows((current) => current.map(fillRow))} title="Досчитать пустые суммы по количеству, цене и ставке">
          Досчитать пустые
        </button>
        <button type="button" className="secondary small" onClick={addRow}>Добавить строку</button>
      </div>
      <div className="table-wrap upd-edit-table">
        <table className="grid wide" aria-label="Позиции — правка">
          <thead>
            <tr><th>№</th>{SLOTS.map((slot) => <th key={slot}>{SLOT_LABELS[slot]}</th>)}<th>Услуга</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => {
              const issues = lineIssues(facts[rowIndex]);
              return (
                <tr key={rowIndex} className={issues.length ? "upd-row-bad" : undefined} title={issues.join("; ")}>
                  <td><input className="upd-seq" aria-label={`Строка ${rowIndex + 1}: № п/п`} value={row.seq}
                    onChange={(event) => setRows((current) => current.map((item, index) => (index === rowIndex ? { ...item, seq: event.target.value } : item)))} /></td>
                  {SLOTS.map((slot) => (
                    <td key={slot} className={focus?.row === rowIndex && focus.slot === slot ? "upd-cell focused" : "upd-cell"}>
                      <input className={slot === "name" ? "upd-name" : "upd-short"} aria-label={`Строка ${rowIndex + 1}: ${SLOT_LABELS[slot]}`}
                        value={row.cells[slot]} onFocus={() => setFocus({ row: rowIndex, slot })}
                        onChange={(event) => setCell(rowIndex, slot, event.target.value)} />
                    </td>
                  ))}
                  <td><input type="checkbox" aria-label={`Строка ${rowIndex + 1}: услуга`} checked={row.is_service}
                    onChange={(event) => setRows((current) => current.map((item, index) => (index === rowIndex ? { ...item, is_service: event.target.checked } : item)))} /></td>
                  <td><button type="button" className="secondary small" aria-label={`Удалить строку ${rowIndex + 1}`}
                    onClick={() => { setRows((current) => current.filter((_, index) => index !== rowIndex)); setFocus(null); }}>✕</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pending.length > 0
        ? <div className="warning-banner" role="status">После сохранения останутся замечания: {pending.join("; ")}. Сохранить можно и так.</div>
        : <div className="success-banner" role="status">Арифметика сходится, реквизиты на месте.</div>}
      {message && <div className="error-banner" role="alert">{message}</div>}
      <div className="move-actions">
        <button type="button" className="secondary" onClick={onCancel} disabled={busy}>Отмена</button>
        <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняю…" : "Сохранить свою версию"}</button>
      </div>
    </div>
  );
}
