import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { referencesApi, type TechOperationRecord, type TechOperationTab, type WarehouseLot } from "./api";
import { STOCK_LINK_EVENT_LABELS, stockLinksApi, type StockLinkEntry } from "./stockLinksApi";
import "./StockLinkDialog.css";

/* «В базу данных…»: партия склада привязывается к записи «Базы данных» или из неё создаётся запись. */

interface BaseDef {
  tab: TechOperationTab;
  label: string;
  /** Ключи полей базы, куда ложатся артикул, название и производитель партии. */
  article?: string;
  name: string;
  maker: string;
}

export const LINK_BASES: BaseDef[] = [
  { tab: "ter", label: "Терминалы", article: "terArticle", name: "terComponent", maker: "terManufacturer" },
  { tab: "coax", label: "Коаксиальные разъёмы", article: "coaxArticle", name: "coaxType", maker: "coaxMfr" },
  { tab: "prov", label: "Монтажные провода", name: "Марка", maker: "Производители" },
  { tab: "mat", label: "Материалы", name: "Наименование", maker: "Производитель" },
];

const FIELD_LABELS = { article: "Артикул", name: "Название", maker: "Производитель" } as const;
type Slot = keyof typeof FIELD_LABELS;

/** Поля новой записи из партии; пустое не передаётся. */
export function prefillFields(base: BaseDef, lot: WarehouseLot): Record<string, string> {
  const fields: Record<string, string> = {};
  const maker = (lot.manufacturer ?? "").trim();
  if (base.article && lot.article.trim()) fields[base.article] = lot.article.trim();
  if (lot.name.trim()) fields[base.name] = lot.name.trim();
  if (maker) fields[base.maker] = maker;
  return fields;
}

/** База по названию партии: провод — в провода, терминал — в терминалы и т. д.; иначе терминалы. */
export function guessBase(lot: WarehouseLot): TechOperationTab {
  const text = `${lot.name} ${lot.article}`.toLowerCase();
  if (/коакс|\b(sma|bnc|mcx|mmcx)\b/.test(text)) return "coax";
  if (/провод|кабел/.test(text)) return "prov";
  if (/термоусад|трубк|лент|стяжк|оплёт|оплет|гофр|флюс|припой/.test(text)) return "mat";
  return "ter";
}

export function StockLinkDialog({ lot, onClose }: { lot: WarehouseLot; onClose: () => void }) {
  const [mode, setMode] = useState<"link" | "create">("link");
  const [tab, setTab] = useState<TechOperationTab>(() => guessBase(lot));
  const base = LINK_BASES.find((item) => item.tab === tab) ?? LINK_BASES[0];
  const [query, setQuery] = useState(lot.article || lot.name);
  const [records, setRecords] = useState<TechOperationRecord[] | null>(null);
  const [picked, setPicked] = useState("");
  const [fields, setFields] = useState<Record<string, string>>(() => prefillFields(LINK_BASES.find((item) => item.tab === guessBase(lot)) ?? LINK_BASES[0], lot));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ record: TechOperationRecord; entry: StockLinkEntry | null; created: boolean } | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  useEffect(() => {
    if (mode !== "link") return;
    const mine = ++latest.current;
    setRecords(null);
    setPicked("");
    referencesApi.techOperations(tab, query, 30).then((result) => {
      if (latest.current === mine) setRecords(result.records ?? []);
    }, (reason) => {
      if (latest.current !== mine) return;
      setRecords([]);
      setError(reason instanceof Error ? reason.message : "Не удалось найти записи");
    });
  }, [mode, tab, query]);

  function chooseTab(next: TechOperationTab) {
    setTab(next);
    setError("");
    setFields(prefillFields(LINK_BASES.find((item) => item.tab === next) ?? LINK_BASES[0], lot));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const request = mode === "link"
      ? stockLinksApi.link({ lot_id: lot.id, record_id: picked })
      : stockLinksApi.link({ lot_id: lot.id, tab, fields: Object.fromEntries(Object.entries(fields).filter(([, value]) => value.trim())) });
    request.then((result) => setDone({ ...result, created: mode === "create" }),
      (reason) => setError(reason instanceof Error ? reason.message : "Не удалось записать в базу"))
      .finally(() => setBusy(false));
  }

  const slots = (Object.keys(FIELD_LABELS) as Slot[]).filter((slot) => slot !== "article" || base.article);
  const keyOf = (slot: Slot) => (slot === "article" ? base.article ?? "" : slot === "name" ? base.name : base.maker);
  const canSubmit = mode === "link" ? Boolean(picked) : Object.values(fields).some((value) => value.trim());

  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="panel move-dialog stock-link-dialog" role="dialog" aria-label="В базу данных">
      <h2>В базу данных</h2>
      <div className="move-lot" aria-label="Партия">
        <strong>ГРН {lot.grn}{lot.package ? ` / пакет ${lot.package}` : ""}</strong>
        <span>{[lot.article, lot.name, lot.manufacturer].filter(Boolean).join(" · ")}</span>
      </div>
      {done ? <div className="dialog-form stock-link-done" role="status">
        <div className="success-banner">
          {done.created ? "Создана запись" : done.entry ? "Партия привязана к записи" : "Партия уже была привязана к этой записи"}: {done.record.display_text}
        </div>
        <span>База: {LINK_BASES.find((item) => item.tab === done.record.tab)?.label ?? "—"}{done.entry ? ` · ${STOCK_LINK_EVENT_LABELS[done.entry.event]}` : ""}</span>
        {done.entry && done.entry.changes.length > 0 && <ul className="stock-link-changes">
          {done.entry.changes.map((change) => <li key={change.field}>{change.field}: {change.old || "—"} → {change.new}</li>)}
        </ul>}
        <div className="move-actions"><button type="button" className="primary" autoFocus onClick={onClose}>Закрыть</button></div>
      </div> : <form className="dialog-form" aria-label="В базу данных" onSubmit={submit}>
        <div className="seg-tabs" role="tablist" aria-label="Что сделать">
          <button type="button" role="tab" aria-selected={mode === "link"} className={mode === "link" ? "on" : undefined} onClick={() => { setMode("link"); setError(""); }}>Привязать к записи</button>
          <button type="button" role="tab" aria-selected={mode === "create"} className={mode === "create" ? "on" : undefined} onClick={() => { setMode("create"); setError(""); }}>Создать запись</button>
        </div>
        <label>База<select aria-label="База" value={tab} onChange={(event) => chooseTab(event.target.value as TechOperationTab)}>
          {LINK_BASES.map((item) => <option key={item.tab} value={item.tab}>{item.label}</option>)}
        </select></label>
        {mode === "link" ? <>
          <label>Поиск записи<input aria-label="Поиск записи" type="search" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          {records === null ? <div className="empty">Загрузка…</div>
            : records.length === 0 ? <div className="empty">Записей не найдено — измените поиск или создайте запись.</div>
            : <div className="stock-link-results" role="group" aria-label="Найденные записи">
              {records.map((record) => <button key={record.id} type="button" aria-pressed={picked === record.id} onClick={() => setPicked(record.id)}>{record.display_text}</button>)}
            </div>}
        </> : <div className="move-grid two">
          {slots.map((slot) => <label key={slot}>{FIELD_LABELS[slot]}<input aria-label={FIELD_LABELS[slot]} value={fields[keyOf(slot)] ?? ""} onChange={(event) => setFields((current) => ({ ...current, [keyOf(slot)]: event.target.value }))} /></label>)}
        </div>}
        {error && <div className="error-banner" role="alert">{error}</div>}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || !canSubmit}>{busy ? "Записываем…" : mode === "link" ? "Привязать" : "Создать запись"}</button>
        </div>
      </form>}
    </div>
  </div>, document.body);
}
