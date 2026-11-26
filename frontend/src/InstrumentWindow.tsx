import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError, failureText, productionApi, referencesApi, warehouseApi,
  type InstrumentFileKind, type InstrumentInput, type InstrumentKind, type ProductionInstrument, type WarehouseLot,
} from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDay, formatMoment, formatNumber } from "./format";
import { PageWindow } from "./PageWindow";
import { InstrumentRfInterfaces } from "./InstrumentRfInterfaces";
import { InstrumentMatings } from "./InstrumentMatings";
import { InstrumentVnaChecks } from "./InstrumentVnaChecks";
import { InstrumentLabelPrint } from "./InstrumentLabelPrint";

const fileKinds: InstrumentFileKind[] = ["Паспорт", "Свидетельство о поверке", "Руководство", "Прочее"];
const conflictText = "Запись изменил другой пользователь — обновите";
const errorText = (reason: unknown) => reason instanceof ApiError && reason.status === 409
  ? conflictText : failureText(reason, "Не удалось сохранить запись");

function initialDraft(instrument: ProductionInstrument | null, kind: InstrumentKind, parentId: string | null): InstrumentInput {
  return {
    inventory_number: instrument?.inventory_number ?? "", name: instrument?.name ?? "", instrument_type: instrument?.instrument_type ?? "",
    kind: instrument?.kind ?? kind, parent_id: instrument?.parent_id ?? parentId,
    vna_app: instrument?.vna_app ?? "",
    serial_number: instrument?.serial_number ?? "", manufacturer: instrument?.manufacturer ?? "", doc_designation: instrument?.doc_designation ?? "",
    quantity: Number(instrument?.quantity ?? 1), storage_location: instrument?.storage_location ?? "ОТК", warehouse_grn: instrument?.warehouse_grn ?? "",
    verification_number: instrument?.verification_number ?? null, verified_at: instrument?.verified_at?.slice(0, 10) ?? null,
    verification_due: instrument?.verification_due?.slice(0, 10) ?? null,
    status: instrument?.status ?? (kind === "Принадлежность" ? "Действует" : "Поверка не подтверждена"),
    custom_fields: instrument?.custom_fields ?? {},
    article: instrument?.article ?? "", notes: instrument?.notes ?? "", measurement_category: instrument?.measurement_category ?? "",
  };
}

function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.onabort = () => reject(new Error("Чтение файла отменено"));
    reader.readAsDataURL(file);
  });
}

export function InstrumentWindow({ instrument, kind, parentId, instruments, close, onChanged, onAccessory,
  source = "production", canEditIdentity = true, canEditCustom = false, customColumns = [], referenceLink,
}: {
  instrument: ProductionInstrument | null;
  kind: InstrumentKind;
  parentId: string | null;
  instruments: ProductionInstrument[];
  close: () => void;
  onChanged: () => Promise<void>;
  onAccessory: (parentId: string) => void;
  source?: "production" | "references";
  canEditIdentity?: boolean;
  canEditCustom?: boolean;
  customColumns?: string[];
  referenceLink?: string;
}) {
  const [record, setRecord] = useState(instrument);
  const [draft, setDraft] = useState(() => initialDraft(instrument, kind, parentId));
  const [baseline, setBaseline] = useState(draft);
  const [numberLoading, setNumberLoading] = useState(!instrument);
  const [busy, setBusy] = useState(false);
  const [rfDirty, setRfDirty] = useState(false);
  const [rfBusy, setRfBusy] = useState(false);
  const [matingsDirty, setMatingsDirty] = useState(false);
  const [matingsBusy, setMatingsBusy] = useState(false);
  const [rfRevision, setRfRevision] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [verification, setVerification] = useState(false);
  const [stock, setStock] = useState(false);
  const [labels, setLabels] = useState(false);
  const [fileKind, setFileKind] = useState<InstrumentFileKind>("Паспорт");
  const [numberAttempt, setNumberAttempt] = useState(0);
  const [history, setHistory] = useState<Awaited<ReturnType<typeof referencesApi.instrumentHistory>> | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const productionActions = source === "production" && canEditIdentity;
  const identityReason = "Паспорт СИ изменяет сотрудник с правом правки ОТК";
  const verificationInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const dirty = rfDirty || matingsDirty || JSON.stringify(draft) !== JSON.stringify(baseline);
  const editingIdentity = canEditIdentity && (!record || Object.entries(draft).some(([key, value]) =>
    key !== "custom_fields" && value !== baseline[key as keyof InstrumentInput]));
  const accessory = draft.kind === "Принадлежность";
  const showVnaApp = !accessory && (Boolean(draft.vna_app) || /ВАЦ|VNA|векторн.*анализ|анализ.*(?:векторн|цеп)/i.test(`${draft.instrument_type} ${draft.name}`));
  const parents = instruments.filter((item) => item.kind === "Средство измерений" && item.id !== record?.id && !isDescendant(item, record?.id, instruments));

  useEffect(() => {
    if (instrument) return;
    let active = true;
    setNumberLoading(true);
    setError("");
    productionApi.nextInstrumentNumber().then(({ inventory_number }) => {
      if (active) {
        setDraft((current) => ({ ...current, inventory_number: current.inventory_number || inventory_number }));
        setBaseline((current) => ({ ...current, inventory_number }));
      }
    }).catch((reason) => { if (active) setError(failureText(reason, "Не удалось получить инвентарный номер")); })
      .finally(() => { if (active) setNumberLoading(false); });
    return () => { active = false; };
  }, [instrument, numberAttempt]);

  useEffect(() => { if (verification) verificationInput.current?.focus(); }, [verification]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function requestClose() {
    if (!busy && !rfBusy && !matingsBusy && (!dirty || window.confirm("Закрыть без сохранения изменений?"))) close();
  }
  const field = <K extends keyof InstrumentInput>(key: K, value: InstrumentInput[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const text = (key: "inventory_number" | "name" | "instrument_type" | "serial_number" | "manufacturer" | "doc_designation" | "storage_location" | "warehouse_grn" | "article" | "notes" | "measurement_category", label: string, required = false) =>
    <label>{label}<input name={key} autoComplete="off" required={required} value={draft[key]} onChange={(event) => field(key, event.target.value)} /></label>;

  function accept(saved: ProductionInstrument) {
    setRecord(saved);
    const next = initialDraft(saved, saved.kind, saved.parent_id);
    setDraft(next);
    setBaseline(next);
    setVerification(false);
    setConflict(false);
  }

  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await operation(); }
    catch (reason) { setError(errorText(reason)); if (reason instanceof ApiError && reason.status === 409) setConflict(true); }
    finally { setBusy(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canEditIdentity && !canEditCustom) return;
    const quantity = Number(draft.quantity);
    const payload: InstrumentInput = { ...draft, quantity, name: draft.name.trim(), inventory_number: draft.inventory_number.trim() };
    if (editingIdentity && accessory && !payload.instrument_type.trim()) payload.instrument_type = "Принадлежность";
    if (editingIdentity && (accessory || payload.status === "Поверка не подтверждена")) {
      payload.verification_number = null; payload.verified_at = null; payload.verification_due = null;
    }
    if (editingIdentity && (!payload.inventory_number || !payload.name || !Number.isFinite(quantity) || quantity <= 0 ||
      (!accessory && !payload.instrument_type.trim()))) {
      setError("Заполните инвентарный номер, наименование, тип и количество."); return;
    }
    if (editingIdentity && !accessory && payload.status === "Действует" && (!payload.verification_number?.trim() || !payload.verified_at || !payload.verification_due)) {
      setError("Укажите номер свидетельства и даты поверки."); return;
    }
    if (editingIdentity && payload.verified_at && payload.verification_due && payload.verification_due < payload.verified_at) {
      setError("Срок поверки не может быть раньше даты поверки."); return;
    }
    await run(async () => {
      const changes = Object.fromEntries(Object.entries(payload).filter(([key, value]) => value !== baseline[key as keyof InstrumentInput]));
      const editable = canEditIdentity ? changes : { custom_fields: payload.custom_fields };
      const result = record ? await (source === "references" ? referencesApi : productionApi).updateInstrument(record.id, { version: record.version, ...editable }) : await productionApi.registerInstrument(payload);
      accept(result.instrument);
      setNotice("Запись сохранена");
      await onChanged();
    });
  }

  async function refreshFiles() {
    const result = await productionApi.instruments();
    const fresh = result.instruments.find((item) => item.id === record?.id);
    if (!fresh) throw new Error("Запись не найдена — обновите реестр");
    // A file operation must not overwrite unsaved form fields or silently rebase their version.
    setRecord((current) => current ? { ...current, files: fresh.files } : current);
    await onChanged();
  }

  return <PageWindow label={record ? `Средство измерений: ${record.name}` : `Добавить: ${kind.toLocaleLowerCase("ru")}`} width={1120} close={requestClose}>
    <div className="panel instrument-window" aria-busy={busy}>
      <header className="page-head"><h2>{record ? `${record.inventory_number} · ${record.name}` : `Новое: ${kind.toLocaleLowerCase("ru")}`}</h2>
        <div className="header-actions">{referenceLink && <a className="secondary small" href={referenceLink}>Открыть в базе данных</a>}
          {record && source === "references" && canEditIdentity && <a className="secondary small" href={`?section=quality&tab=instruments&si=${encodeURIComponent(record.inventory_number)}`}>Открыть в ОТК</a>}
          {record && source === "production" && <a className="primary small instrument-passport" href={productionApi.instrumentPassportUrl(record.id)}
          target="_blank" rel="noopener noreferrer">Паспорт (PDF)</a>}
          {record && source === "production" && <button type="button" className="secondary small" disabled={busy || rfBusy || dirty || conflict}
          title={dirty ? "Сначала сохраните изменения" : undefined} onClick={() => setLabels(true)}>Печать этикетки</button>}
          <button type="button" className="secondary small" disabled={busy || rfBusy} onClick={requestClose}>Закрыть</button></div></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      {conflict && <button className="secondary small" disabled={busy} onClick={() => {
        if (dirty && !window.confirm("Обновить запись и заменить несохранённые изменения?")) return;
        void run(async () => {
          if (!record) return;
          const fresh = source === "references" ? (await referencesApi.instrument(record.id)).instrument
            : (await productionApi.instruments()).instruments.find((item) => item.id === record.id);
          if (!fresh) throw new Error("Запись не найдена — обновите реестр");
          accept(fresh); await onChanged();
        });
      }}>Обновить запись</button>}
      {numberLoading && <div className="empty">Получение инвентарного номера…</div>}
      {!record && error && !draft.inventory_number && !numberLoading && <button className="secondary small" onClick={() => setNumberAttempt((value) => value + 1)}>Повторить получение номера</button>}
      {!canEditIdentity && <p className="warning-inline" id="instrument-identity-reason">{identityReason}</p>}
      <form noValidate={!editingIdentity} onSubmit={(event) => void save(event)}>
        <fieldset disabled={busy || rfBusy || !canEditIdentity} aria-describedby={!canEditIdentity ? "instrument-identity-reason" : undefined} className="instrument-fields">
          <section><h3>Прибор</h3><div className="form-grid">
            {text("inventory_number", "Инв. №", true)}{text("name", "Наименование", true)}{text("instrument_type", "Тип", !accessory)}
            <label>Вид<input name="kind" readOnly value={draft.kind} /></label>
            {showVnaApp && <label>Программа ВАЦ<select name="vna_app" value={draft.vna_app ?? ""} onChange={(event) => field("vna_app", event.target.value as InstrumentInput["vna_app"])}>
              <option value="">Не указана</option><option>S2VNA</option><option>Arinst VL</option>
            </select></label>}
            {text("doc_designation", "Обозначение для документов")}
            {source === "references" && <>{text("article", "Артикул")}{text("measurement_category", "Категория измерений")}</>}
            <div className="instrument-serial"><label>Зав. №<input name="serial_number" autoComplete="off" value={draft.serial_number} onChange={(event) => field("serial_number", event.target.value)} /></label>
              {record && productionActions && !record.serial_number.trim() && !draft.serial_number.trim() && record.suggested_serial_number && <button type="button" className="secondary small"
                disabled={busy || rfBusy || dirty || conflict} title={dirty ? "Сначала сохраните изменения" : undefined} onClick={() => void run(async () => {
                  const result = await productionApi.assignInstrumentSerial(record.id, record.version);
                  accept(result.instrument); setNotice("Номер присвоен"); await onChanged();
                })}>Присвоить номер {record.suggested_serial_number}</button>}
            </div>{text("manufacturer", "Изготовитель")}
            <label>Количество, шт<input name="quantity" type="number" min="1" step="1" required disabled={record?.kind === "Средство измерений" || Boolean(record?.warehouse_writeoff_id)} value={draft.quantity} onChange={(event) => field("quantity", event.target.value)} /></label>
            <label>В составе прибора<select name="parent_id" value={draft.parent_id ?? ""} onChange={(event) => field("parent_id", event.target.value || null)}>
              <option value="">{accessory ? "Без прибора (общая)" : "Самостоятельный прибор"}</option>{parents.map((item) => <option value={item.id} key={item.id}>{item.inventory_number} · {item.name}</option>)}
            </select></label>
            <label>Статус<select name="status" value={draft.status} onChange={(event) => field("status", event.target.value as InstrumentInput["status"])}>
              <option>Действует</option><option>Не используется</option>{!accessory && <option>Поверка не подтверждена</option>}
            </select></label>
          </div></section>
          {!accessory && <section><div className="instrument-verification-head"><h3>Поверка</h3>
            {!verification && <button type="button" className="secondary small" onClick={() => { setVerification(true); field("status", "Действует"); }}>Добавить поверку</button>}
          </div>
            {(draft.status !== "Поверка не подтверждена" || verification) ? <div className="form-grid">
              <label>№ свидетельства о поверке<input ref={verificationInput} name="verification_number" autoComplete="off" value={draft.verification_number ?? ""} onChange={(event) => field("verification_number", event.target.value)} /></label>
              <label>Поверено<input name="verified_at" type="date" value={draft.verified_at ?? ""} onChange={(event) => field("verified_at", event.target.value || null)} /></label>
              <label>Поверка до<input name="verification_due" type="date" min={draft.verified_at ?? undefined} value={draft.verification_due ?? ""} onChange={(event) => field("verification_due", event.target.value || null)} /></label>
            </div> : <p className="muted-text">Поверка не подтверждена</p>}
          </section>}
          <section><h3>Хранение</h3><div className="form-grid">{text("storage_location", "Место хранения")}{text("warehouse_grn", "ГРН")}</div></section>
          {source === "references" && <section><h3>Примечание</h3>{text("notes", "Примечание")}</section>}
        </fieldset>
        {customColumns.length > 0 && <section><h3>Дополнительные сведения</h3>
          {!canEditCustom && !canEditIdentity && <p className="warning-inline">Собственные столбцы изменяет сотрудник с правом правки базы данных</p>}
          <div className="form-grid">{customColumns.map((column) => <label key={column}>{column}<input name={`custom:${column}`} autoComplete="off"
            disabled={busy || (!canEditCustom && !canEditIdentity)} value={draft.custom_fields?.[column] ?? ""}
            onChange={(event) => field("custom_fields", { ...draft.custom_fields, [column]: event.target.value })} /></label>)}</div>
        </section>}
          <div className="form-actions"><button className="primary small" type="submit" disabled={busy || rfBusy || numberLoading || conflict || (!canEditIdentity && !canEditCustom)}>{busy ? "Сохранение…" : "Сохранить"}</button>
            {productionActions && record?.kind === "Принадлежность" && record.warehouse_writeoff_id && record.status === "Действует" &&
              <button type="button" className="secondary" disabled={busy || rfBusy || conflict} onClick={() => {
                if (!window.confirm("Вернуть принадлежность на склад и отметить «Не используется»?")) return;
                if (dirty && !window.confirm("Несохранённые изменения будут потеряны. Продолжить?")) return;
                void run(async () => {
                  const result = await productionApi.returnInstrumentToStock(record.id, record.version);
                  accept(result.instrument); setNotice("Принадлежность возвращена на склад"); await onChanged();
                });
              }}>Вернуть на склад</button>}
            {productionActions && record && record.status !== "Не используется" && <button type="button" className="danger small" disabled={busy || rfBusy || conflict} onClick={() => {
              if (!window.confirm("Списать прибор / отметить «Не используется»?")) return;
              if (dirty && !window.confirm("Несохранённые изменения будут потеряны. Продолжить?")) return;
              void run(async () => { const result = await productionApi.updateInstrument(record.id, { version: record.version, status: "Не используется" }); accept(result.instrument); setNotice("Прибор не используется"); await onChanged(); });
            }}>Списать / Не используется</button>}
          </div>
      </form>
      {source === "production" && <InstrumentRfInterfaces instrumentId={record?.id} locked={busy || !canEditIdentity} onDirty={setRfDirty} onBusy={setRfBusy}
        onSaved={async (suggestion) => {
          if (suggestion !== undefined) setRecord((current) => current ? { ...current, suggested_serial_number: suggestion } : current);
          setRfRevision((value) => value + 1);
          await onChanged();
        }} />}
      {record && source === "production" && <InstrumentMatings instrumentId={record.id} locked={busy || rfBusy || !canEditIdentity} refreshKey={rfRevision} onChanged={onChanged}
        onDirty={setMatingsDirty} onBusy={setMatingsBusy} />}
      {record && source === "production" && <InstrumentVnaChecks instrumentId={record.id} refreshKey={rfRevision} />}
      <section className="instrument-files"><h3>Файлы</h3>
        {record ? <>
          {source === "references" && <p className="muted-text">Документы СИ изменяются в ОТК.</p>}
          {productionActions && <div className="instrument-upload"><label>Вид файла<select disabled={busy} value={fileKind} onChange={(event) => setFileKind(event.target.value as InstrumentFileKind)}>{fileKinds.map((value) => <option key={value}>{value}</option>)}</select></label>
            <div><button type="button" className="secondary small" disabled={busy} onClick={() => fileInput.current?.click()}>Загрузить файл…</button>
              <input ref={fileInput} type="file" hidden aria-label="Загрузить файл" disabled={busy} onChange={(event) => {
              const file = event.target.files?.[0]; event.target.value = "";
              if (file) void run(async () => { await productionApi.uploadInstrumentFile(record.id, { kind: fileKind, file_name: file.name, content_base64: await fileBase64(file) }); setNotice("Файл загружен"); await refreshFiles(); });
            }} /></div>
          </div>}
          {fileKinds.map((value) => <div className="instrument-file-group" key={value}><h4>{value}</h4>
            {!(record.files ?? []).some((file) => file.kind === value) && <span className="muted-text">Файлов нет</span>}
            {(record.files ?? []).filter((file) => file.kind === value).map((file) => <div className="instrument-file" key={file.id}>
              <a href={(source === "references" ? referencesApi : productionApi).instrumentFileUrl(record.id, file.id)} target="_blank" rel="noopener noreferrer">{file.file_name}</a>
              <span>{formatNumber(file.size)} байт · {formatDay(file.uploaded_at)} · {file.uploaded_by_name}</span>
              {file.is_reference && <span className="tone-ok instrument-file-reference">Эталонная кривая</span>}
              {productionActions && !file.is_reference && /\.s2p$/i.test(file.file_name) && <button type="button" className="secondary small" disabled={busy}
                aria-label={`Сделать эталонной кривой: ${file.file_name}`} onClick={() => {
                  const current = (record.files ?? []).find((item) => item.is_reference);
                  if (current && !window.confirm(`Эталонной кривой сейчас назначен «${current.file_name}». Заменить на «${file.file_name}»?`)) return;
                  void run(async () => { await productionApi.setInstrumentReferenceCurve(record.id, file.id); setNotice("Эталонная кривая назначена"); await refreshFiles(); });
                }}>Сделать эталонной кривой</button>}
              {productionActions && <button className="danger small" disabled={busy} aria-label={`Удалить файл ${file.file_name}`} onClick={() => {
                if (window.confirm(`Удалить файл «${file.file_name}»?`)) void run(async () => { await productionApi.deleteInstrumentFile(record.id, file.id); setNotice("Файл удалён"); await refreshFiles(); });
              }}>Удалить</button>}
            </div>)}
          </div>)}
        </> : <p className="muted-text">Сохраните запись, чтобы прикрепить файлы.</p>}
      </section>
      {record && source === "references" && <section><h3>История</h3>
        <button type="button" className="secondary small" disabled={historyLoading} onClick={() => {
          setHistoryLoading(true); setHistoryError("");
          void referencesApi.instrumentHistory(record.id).then(setHistory)
            .catch((reason) => setHistoryError(failureText(reason, "Не удалось загрузить историю")))
            .finally(() => setHistoryLoading(false));
        }}>{historyLoading ? "Загрузка…" : history ? "Обновить историю" : "Показать историю"}</button>
        {historyError && <p className="error-banner" role="alert">{historyError}</p>}
        {history && <div aria-live="polite">{history.events.length ? history.events.map((event) => <p key={event.id}>
          <span className="tnum">{formatMoment(event.occurred_at)}</span> · {event.actor_name || "Сотрудник"} · Изменение записи
        </p>) : <p className="muted-text">Изменений нет</p>}</div>}
      </section>}
      {record && productionActions && !accessory && <div className="form-actions"><button className="secondary small" disabled={busy} onClick={() => {
        if (!dirty || window.confirm("Открыть принадлежность без сохранения изменений?")) onAccessory(record.id);
      }}>+ Принадлежность</button><button className="secondary small" disabled={busy} onClick={() => setStock(true)}>+ Со склада</button></div>}
    </div>
    {stock && record && <InstrumentStockWindow parent={record} close={() => setStock(false)} onChanged={onChanged} />}
    {labels && record && <InstrumentLabelPrint instruments={[record]} close={() => setLabels(false)} />}
  </PageWindow>;
}

function isDescendant(item: ProductionInstrument, id: string | undefined, instruments: ProductionInstrument[]): boolean {
  if (!id) return false;
  const seen = new Set<string>();
  let parent = item.parent_id;
  while (parent && !seen.has(parent)) {
    if (parent === id) return true;
    seen.add(parent); parent = instruments.find((candidate) => candidate.id === parent)?.parent_id ?? null;
  }
  return false;
}

const lotColumns: DataColumn<WarehouseLot>[] = [
  { key: "grn", title: "ГРН", value: (lot) => lot.grn },
  { key: "article", title: "Артикул", value: (lot) => lot.article },
  { key: "name", title: "Наименование", value: (lot) => lot.name, wrap: true, grow: true },
  { key: "warehouse", title: "Склад", value: (lot) => lot.warehouse },
  { key: "balance", title: "Остаток", value: (lot) => Number(lot.balance), numeric: true },
  { key: "available", title: "Доступно", value: (lot) => Number(lot.available), numeric: true },
  { key: "unit", title: "Ед.", value: (lot) => lot.unit },
];

function InstrumentStockWindow({ parent, close, onChanged }: { parent: ProductionInstrument; close: () => void; onChanged: () => Promise<void> }) {
  const [lots, setLots] = useState<WarehouseLot[]>([]);
  const [selected, setSelected] = useState<WarehouseLot | null>(null);
  const [query, setQuery] = useState("");
  const [searched, setSearched] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [name, setName] = useState("");
  const [number, setNumber] = useState("");
  const [designation, setDesignation] = useState("");
  const [location, setLocation] = useState("ОТК");
  const [unknownResult, setUnknownResult] = useState(false);
  const request = useRef(0);
  const numericQuantity = Number(quantity);
  async function search(after?: string) {
    const stamp = ++request.current;
    setLoading(true); setError(""); setSelected(null);
    if (!after) { setLots([]); setCursor(null); }
    const value = after ? searched : query;
    try {
      const result = await warehouseApi.list({ query: value, after });
      if (stamp !== request.current) return;
      setLots((current) => after ? [...current, ...result.lots] : result.lots); setCursor(result.next_cursor); setSearched(value);
    } catch (reason) { if (stamp === request.current) setError(failureText(reason, "Не удалось загрузить складские партии")); }
    finally { if (stamp === request.current) setLoading(false); }
  }
  useEffect(() => { void search(); return () => { request.current += 1; }; }, []);
  async function transfer(event: FormEvent) {
    event.preventDefault();
    if (!selected || busy || unknownResult || !Number.isFinite(numericQuantity) || numericQuantity <= 0 || numericQuantity > Number(selected.available)) return;
    if (!window.confirm(`Передать со склада ${formatNumber(numericQuantity)} ${selected.unit} «${selected.name}» в принадлежности прибора «${parent.name}»?`)) return;
    setBusy(true); setError("");
    try {
      await productionApi.instrumentFromStock({ parent_id: parent.id, lot_id: selected.id, lot_version: selected.version, quantity: numericQuantity,
        ...(number.trim() ? { inventory_number: number.trim() } : {}), ...(name.trim() ? { name: name.trim() } : {}),
        ...(designation.trim() ? { doc_designation: designation.trim() } : {}), storage_location: location.trim() });
      await onChanged(); close();
    } catch (reason) {
      setError(errorText(reason));
      // Network ambiguity must never offer a second stock write-off in this window.
      if (!(reason instanceof ApiError)) setUnknownResult(true);
      else if (reason.status === 409) { setSelected(null); setLots([]); setCursor(null); }
    } finally { setBusy(false); }
  }
  return <PageWindow label="Принадлежность со склада" width={1080} close={() => { if (!busy) close(); }}>
    <div className="panel instrument-window"><header className="page-head"><h2>Принадлежность со склада</h2><button className="secondary small" disabled={busy} onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <form className="instrument-stock-search" onSubmit={(event) => { event.preventDefault(); void search(); }}>
        <label>Найти партию<input name="lot_query" autoComplete="off" placeholder="ГРН, артикул или наименование…" value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} /></label>
        <button className="secondary small" disabled={busy || loading}>Найти</button>
      </form>
      <DataTable label="Складские партии" rows={lots} columns={lotColumns} rowKey={(lot) => lot.id} hideSearch loading={loading} pageSize={100}
        onRowClick={(lot) => { if (!busy && !loading && !unknownResult) { setSelected(lot); setName(lot.name); setQuantity("1"); } }} rowClassName={(lot) => selected?.id === lot.id ? "instrument-lot-selected" : ""}
        emptyText="Доступных партий нет — измените поиск." />
      {cursor && <button className="secondary small" disabled={loading || busy} onClick={() => void search(cursor)}>Показать ещё</button>}
      {selected && <form onSubmit={(event) => void transfer(event)}><h3>{selected.grn} · {selected.name}</h3>
        <p>Остаток: {formatNumber(selected.balance)} {selected.unit} · Доступно: {formatNumber(selected.available)} {selected.unit}</p>
        <fieldset disabled={busy || unknownResult} className="instrument-fields"><div className="form-grid">
          <label>Количество со склада<input name="stock_quantity" type="number" min="1" step="1" max={Math.floor(Number(selected.available))} required value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
          <label>Инв. № принадлежности<input name="stock_inventory_number" autoComplete="off" value={number} onChange={(event) => setNumber(event.target.value)} /></label>
          <label>Наименование принадлежности<input name="stock_name" autoComplete="off" value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Обозначение для документов<input name="stock_doc_designation" autoComplete="off" value={designation} onChange={(event) => setDesignation(event.target.value)} /></label>
          <label>Место хранения<input name="stock_location" autoComplete="off" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
        </div><div className="form-actions"><button className="primary small" disabled={loading || numericQuantity <= 0 || !Number.isFinite(numericQuantity) || numericQuantity > Number(selected.available)}>{busy ? "Передача…" : "Передать со склада"}</button></div></fieldset>
      </form>}
    </div>
  </PageWindow>;
}
