import { useEffect, useRef, useState } from "react";
import { ApiError, failureText } from "./api";
import { DataTable } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { formatNumber } from "./format";
import { useCanAct, useCanPrint, useCurrentUserId } from "./rights";
import { boundPrinter, markingApi, type MarkingBatch, type MarkingBatchRequest, type MarkingCatalog, type MarkingLabel, type MarkingPreview, type MarkingRequest, type MarkingSource } from "./markingApi";
import "./marking.css";

type BatchHistory = { id: string; request: MarkingBatchRequest; total: number };
type PrintRecord = { request: MarkingBatchRequest; pending: boolean; batch: MarkingBatch | null; usedKeys?: string[]; previousRequest?: MarkingBatchRequest; previousKeys?: string[]; history?: BatchHistory[] };

export function MarkingPrintWindow({ source, storageKey, title, close }: {
  source: MarkingSource | (() => Promise<MarkingSource>); storageKey?: string; title: string; close: () => void;
}) {
  const userId = useCurrentUserId();
  const identity = storageKey ?? (typeof source === "function" ? title : "group_id" in source ? `group:${source.group_id}` : `task:${source.task_id}:${source.operation_id}`);
  const storageName = `marking-print:${userId ?? "local"}:${identity}`;
  const [restored] = useState(() => {
    try { return { record: JSON.parse(sessionStorage.getItem(storageName) || "null") as PrintRecord | null, error: "" }; }
    catch { return { record: null, error: "Не удалось восстановить очередь печати — проверьте доступность хранилища браузера" }; }
  });
  const [record, setRecord] = useState(restored.record);
  const [outstanding, setOutstanding] = useState(restored.record?.pending ?? false);
  const [catalog, setCatalog] = useState<MarkingCatalog | null>(null);
  const [machineId, setMachineId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [preview, setPreview] = useState<MarkingPreview | null>(null);
  const [approved, setApproved] = useState<MarkingRequest | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [labelIndex, setLabelIndex] = useState(0);
  const [batch, setBatch] = useState<MarkingBatch | null>(restored.record?.batch ?? null);
  const [reprinting, setReprinting] = useState(false);
  const [creatingNew, setCreatingNew] = useState(false);
  const [reprintId, setReprintId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(restored.error);
  const [reload, setReload] = useState(0);
  const inFlight = useRef(false);
  const retry = useRef({ payload: "", key: "" });
  const canPrint = useCanPrint();
  useEffect(() => {
    const controller = new AbortController();
    if (!restored.error) setError("");
    markingApi.machines(controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setCatalog(result);
      // An inventory number is a display label; the choice and binding use the equipment UUID.
      const candidates = result.machines.filter((machine) => machine.binding && machine.profiles.some((profile) => profile.calibrated));
      if (candidates.length === 1) {
        setMachineId(candidates[0].equipment_id);
        const profiles = candidates[0].profiles.filter((profile) => profile.calibrated);
        setProfileId(profiles.length === 1 ? profiles[0].id : "");
      }
    }).catch((problem) => { if (!controller.signal.aborted) setError(failureText(problem, "Не удалось загрузить станки")); });
    return () => controller.abort();
  }, [reload]);
  const machine = catalog?.machines.find((item) => item.equipment_id === machineId);
  const profile = machine?.profiles.find((item) => item.id === profileId);
  const printer = catalog ? boundPrinter(catalog, machine) : undefined;
  const online = Boolean(printer?.present && printer.online);
  const ready = Boolean(profile?.calibrated && machine?.binding);
  const choosing = (!batch || reprinting || creatingNew) && !outstanding && !restored.error;
  const usedKeys = new Set(record?.usedKeys ?? record?.request.selected ?? []);
  const overlapsPrevious = creatingNew && Boolean(preview?.labels.some((label) => usedKeys.has(label.key)));
  const history = record?.history?.length ? record.history : batch && record ? [{ id: batch.id, request: record.pending ? record.previousRequest ?? record.request : record.request, total: batch.total }] : [];
  const reprint = history.find((item) => item.id === reprintId);
  const approvedKeys = new Set(approved?.selected.length ? approved.selected : preview?.labels.map((row) => row.key));
  const selectionChanged = selected.length !== approvedKeys.size || selected.some((key) => !approvedKeys.has(key));

  function invalidate() { setPreview(null); setApproved(null); setSelected([]); setLabelIndex(0); setError(""); }
  async function inspect(keys: string[]) {
    if (inFlight.current || !profile) return;
    inFlight.current = true; setBusy(true); setError(""); setPreview(null);
    try {
      const resolved = typeof source === "function" ? await source() : source;
      const request: MarkingRequest = { profile_id: profile.id, profile_version: profile.version, source: resolved, selected: keys };
      const result = await markingApi.preview(request);
      setPreview(result); setApproved(request);
      setSelected([...new Set(result.labels.map((row) => row.key))].filter((key) => !creatingNew || !usedKeys.has(key))); setLabelIndex(0);
    } catch (problem) { setApproved(null); setError(failureText(problem, "Предпросмотр не получен")); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function submit(request: MarkingBatchRequest) {
    if (inFlight.current || !canPrint || restored.error) return;
    inFlight.current = true; setBusy(true); setError("");
    const previousKeys = record?.pending ? record.previousKeys ?? [] : record?.usedKeys ?? record?.request.selected ?? [];
    const previousRequest = record?.pending ? record.previousRequest : record?.request;
    const requestedKeys = record?.pending ? record.usedKeys ?? [] : preview?.labels.map((label) => label.key) ?? [];
    const pending: PrintRecord = { request, pending: true, batch, previousKeys, previousRequest, history, usedKeys: [...new Set([...previousKeys, ...requestedKeys])] };
    try {
      // Persist before the write: a lost response or a closed window must reuse this exact intent.
      sessionStorage.setItem(storageName, JSON.stringify(pending));
      setRecord(pending); setOutstanding(true);
    } catch {
      setError("Не удалось сохранить запрос печати — проверьте доступность хранилища браузера");
      inFlight.current = false; setBusy(false); return;
    }
    try {
      const result = await markingApi.enqueue(request);
      const completed: PrintRecord = { ...pending, pending: false, batch: result, history: [...history.filter((item) => item.id !== result.id), { id: result.id, request, total: result.total }] };
      sessionStorage.setItem(storageName, JSON.stringify(completed));
      setRecord(completed); setBatch(result); setOutstanding(false); setReprinting(false); setCreatingNew(false); setReason("");
      retry.current = { payload: "", key: "" };
    } catch (problem) {
      if (problem instanceof ApiError && problem.status >= 400 && problem.status < 500) {
        const rejected: PrintRecord = { request: batch && previousRequest ? previousRequest : request, pending: false, batch, usedKeys: previousKeys, history };
        try { sessionStorage.setItem(storageName, JSON.stringify(rejected)); setRecord(rejected); setOutstanding(false); }
        catch { setError("Не удалось сохранить результат запроса — повторите запрос печати"); return; }
      }
      setError(failureText(problem, "Результат постановки неизвестен — повторите запрос"));
    }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function print() {
    if (!preview || !approved || selectionChanged || overlapsPrevious || !online || (reprinting && (!reprint || !reason.trim()))) return;
    const payload = { ...approved, manifest_hash: preview.manifest_hash,
      ...(reprinting && reprint ? { reprint_of: reprint.id, reason: reason.trim() } : {}) };
    const fingerprint = JSON.stringify(payload);
    if (retry.current.payload !== fingerprint) retry.current = { payload: fingerprint, key: crypto.randomUUID() };
    await submit({ ...payload, idempotency_key: retry.current.key });
  }
  const label = preview?.labels[labelIndex];
  const selectedKeys = new Set(selected);
  const selectable = preview ? [...new Map(preview.labels.map((row) => [row.key, row])).values()] : [];
  return <PageWindow label="Печать надписей" width={1280} close={() => { if (!inFlight.current) close(); }}>
    <div className="panel marking-window">
      <div className="marking-head"><div><h2>Печать надписей</h2><p className="muted">{title}</p></div>
        <button type="button" className="secondary" disabled={busy} onClick={close}>Закрыть</button></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {outstanding && record && <div className="warning-inline" role="status">Проверьте постановку предыдущей партии перед новой печатью.
        <button type="button" className="secondary" disabled={busy || !canPrint} onClick={() => void submit(record.request)}>Повторить запрос</button></div>}
      {!catalog && !error && <div className="empty" role="status">Загрузка…</div>}
      {catalog && !catalog.machines.length && <div className="empty">Станки маркировки не найдены — проверьте оборудование ОБ.101 и ОБ.102.</div>}
      {catalog && catalog.machines.length > 0 && <>
        <div className="marking-controls">
          <label>Станок<select name="marking-machine" autoComplete="off" disabled={busy || !choosing} value={machineId} onChange={(event) => {
            setMachineId(event.target.value); setProfileId(""); invalidate();
          }}><option value="">Выберите станок…</option>{catalog.machines.map((item) =>
            <option key={item.equipment_id} value={item.equipment_id}>{item.inventory_number} · {item.name}</option>)}</select></label>
          <label>Материал / профиль<select name="marking-profile" autoComplete="off" disabled={busy || !choosing || !machine} value={profileId}
            onChange={(event) => { setProfileId(event.target.value); invalidate(); }}><option value="">Выберите профиль…</option>
            {machine?.profiles.map((item) => <option key={item.id} value={item.id}>{item.name}{item.calibrated ? "" : " · Не откалиброван"}</option>)}</select></label>
          <div className="marking-target"><span className="muted">Принтер</span><b>{machine?.binding?.printer || "Не привязан"}</b>
            {machine?.binding && <span className={online ? "tone-ok" : "tone-warn"}>{printer?.pc ? `${printer.pc} · ` : ""}{online ? "На связи" : "Не на связи"}</span>}</div>
        </div>
        {profile && !profile.calibrated && <p className="warning-inline">Профиль не откалиброван — выполните пробную печать в карточке оборудования.</p>}
        {machine && !machine.binding && <p className="warning-inline">Привяжите принтер в карточке оборудования.</p>}
        {reprinting && history.length > 1 && <label>Партия для повторной печати<select name="marking-reprint-batch" autoComplete="off" disabled={busy} value={reprintId}
          onChange={(event) => { setReprintId(event.target.value); invalidate(); }}>
          {history.map((item, index) => <option key={item.id} value={item.id}>Партия {index + 1} · {formatNumber(item.total)} шт.</option>)}</select></label>}
        {choosing && <div className="form-actions">
          <button type="button" className="secondary" disabled={busy || !ready} onClick={() => void inspect(reprinting ? reprint?.request.selected ?? [] : [])}>{busy ? "Обработка…" : "Обновить предпросмотр"}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => { invalidate(); setCatalog(null); setMachineId(""); setProfileId(""); setReload(reload + 1); }}>Обновить станки</button>
          {preview && <strong className="tnum" role="status">К печати: {formatNumber(preview.total)} шт.</strong>}
        </div>}
        {preview && <div className="marking-preview">
          <div>
            <DataTable rows={preview.labels} rowKey={(row: MarkingLabel) => `${row.key}:${row.serial ?? "fixed"}`} label="Надписи к печати"
              compactBelow={Infinity} pageSize={20} phoneCards={false} onRowClick={(row) => setLabelIndex(preview.labels.indexOf(row))} columns={[
                { key: "text", title: "Надпись", value: (row) => row.text, grow: true, wrap: true },
                { key: "material", title: "Материал", value: (row) => row.material, wrap: true },
                { key: "quantity", title: "Шт.", numeric: true, value: (row) => row.quantity },
              ]} />
            {choosing && !reprinting && selectable.length > 1 && <details className="marking-selection"><summary>Выбрать надписи</summary>
              {selectable.map((row) => <label className="check-inline" key={row.key}><input type="checkbox" name={`mark-${row.key}`} checked={selectedKeys.has(row.key)} disabled={busy || (creatingNew && usedKeys.has(row.key))}
                onChange={(event) => { const keys = new Set(selectedKeys); if (event.target.checked) keys.add(row.key); else keys.delete(row.key); setSelected([...keys]); }} />{row.text}</label>)}
              <button type="button" className="secondary" disabled={busy || !selected.length} onClick={() => void inspect(selected)}>Показать выбранные</button>
            </details>}
          </div>
          {label && <figure className="marking-raster"><figcaption>Предпросмотр этикетки · <span className="tnum">{formatNumber(preview.width_mm)} × {formatNumber(preview.height_mm)} мм</span></figcaption>
            <img src={`data:image/png;base64,${label.png_b64}`} alt={`Этикетка: ${label.text}`}
              width={Math.round(preview.width_mm * preview.dpi / 25.4)} height={Math.round(preview.height_mm * preview.dpi / 25.4)} />
            <p className="muted tnum">{preview.dpi} точек/дюйм · {labelIndex + 1} из {preview.labels.length}</p>
            <div className="form-actions"><button type="button" className="secondary small" disabled={labelIndex === 0} onClick={() => setLabelIndex(labelIndex - 1)}>Предыдущая</button>
              <button type="button" className="secondary small" disabled={labelIndex === preview.labels.length - 1} onClick={() => setLabelIndex(labelIndex + 1)}>Следующая</button></div>
          </figure>}
        </div>}
        {reprinting && <label>Причина повторной печати<textarea name="marking-reprint-reason" autoComplete="off" maxLength={1000} disabled={busy} value={reason} onChange={(event) => setReason(event.target.value)} /></label>}
        {selectionChanged && preview && <p className="warning-inline">Обновите предпросмотр выбранных надписей.</p>}
        {overlapsPrevious && <p className="warning-inline">В новую партию можно выбрать только оставшиеся надписи. Для уже поставленных в очередь используйте повторную печать.</p>}
        {choosing && <div className="form-actions marking-submit"><button type="button" className="primary" disabled={busy || !canPrint || !online || !preview?.total || !approved || selectionChanged || overlapsPrevious || (reprinting && !reason.trim())}
          onClick={() => void print()}>{busy ? "Обработка…" : reprinting ? "Повторить печать" : "Поставить в очередь"}</button>
          {creatingNew && <button type="button" className="secondary" disabled={busy} onClick={() => { setCreatingNew(false); invalidate(); }}>Отменить новую партию</button>}
          {reprinting && <button type="button" className="secondary" disabled={busy} onClick={() => setReprinting(false)}>Отменить повторную печать</button>}</div>}
      </>}
      {!catalog && error && <button type="button" className="secondary" onClick={() => setReload(reload + 1)}>Повторить загрузку</button>}
      {batch && <>
        <MarkingBatchProgress key={batch.id} initial={batch} onChange={setBatch} />
        {!reprinting && !creatingNew && canPrint && <div className="form-actions">
          <button type="button" className="secondary" disabled={busy || outstanding || batch.jobs.some((job) => ["pending", "taken", "unknown"].includes(job.status))}
            onClick={() => { setReprinting(true); setReprintId(batch.id); setReason(""); invalidate(); }}>Повторная печать…</button>
          <button type="button" className="secondary" disabled={busy || outstanding || batch.jobs.some((job) => ["pending", "taken", "unknown"].includes(job.status))}
            onClick={() => { setCreatingNew(true); invalidate(); }}>Новая партия…</button>
        </div>}
      </>}
    </div>
  </PageWindow>;
}

export function MarkingBatchProgress({ initial, onChange }: { initial: MarkingBatch; onChange?: (batch: MarkingBatch) => void }) {
  const [batch, setBatch] = useState(initial);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [resolution, setResolution] = useState<{ job: string; outcome: "spooled" | "failed"; reason: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const canResolve = useCanAct("production");
  const callback = useRef(onChange); callback.current = onChange;
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await markingApi.batch(initial.id, controller.signal);
        if (controller.signal.aborted) return;
        setBatch(result); callback.current?.(result); setError("");
        if (result.jobs.some((job) => ["pending", "taken", "unknown"].includes(job.status))) timer = setTimeout(() => void poll(), 2000);
      } catch (problem) { if (!controller.signal.aborted) setError(failureText(problem, "Не удалось получить состояние печати")); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [initial.id, reload]);
  async function resolve() {
    if (busy || !resolution?.reason.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await markingApi.resolve(batch.id, resolution.job, resolution.outcome, resolution.reason.trim());
      setBatch(result); callback.current?.(result); setResolution(null); setReload((value) => value + 1);
    } catch (problem) { setError(failureText(problem, "Результат проверки не сохранён")); }
    finally { setBusy(false); }
  }
  const spooled = batch.jobs.filter((job) => job.status === "spooled").length;
  return <section className="marking-progress" aria-label="Очередь печати">
    <div className="marking-head"><h3>{batch.status_label}</h3><strong className="tnum" role="status">Передано принтеру: {spooled} из {batch.total}</strong></div>
    <progress max={batch.total} value={spooled} aria-label="Передано принтеру" />
    <p className="muted">Проверьте напечатанные этикетки перед нанесением.</p>
    {batch.status === "unknown" && <p className="warning-inline">Результат неизвестен. Очередь приостановлена — проверьте этикетки и подтвердите результат в «Производстве».</p>}
    {error && <div className="error-banner" role="alert">{error} <button type="button" className="secondary small" onClick={() => setReload(reload + 1)}>Обновить состояние</button></div>}
    <DataTable rows={batch.jobs} rowKey={(job) => job.id} label="Задания печати" compactBelow={Infinity} pageSize={10} phoneCards={false} columns={[
      { key: "sequence", title: "№", numeric: true, value: (job) => job.sequence },
      { key: "status", title: "Состояние", value: (job) => job.status_label },
      { key: "message", title: "Сообщение", value: (job) => job.message, grow: true, wrap: true },
      { key: "resolve", title: "Проверка", value: () => "", render: (job) => job.status === "unknown" && canResolve
        ? <button type="button" className="secondary small" disabled={busy} onClick={() => setResolution({ job: job.id, outcome: "failed", reason: "" })}>Указать результат</button> : "—" },
    ]} />
    {resolution && <div className="marking-resolution">
      <label>Результат проверки<select name="marking-outcome" autoComplete="off" disabled={busy} value={resolution.outcome}
        onChange={(event) => setResolution({ ...resolution, outcome: event.target.value as "spooled" | "failed" })}><option value="failed">Не напечатано</option><option value="spooled">Передано принтеру</option></select></label>
      <label>Комментарий проверки<input name="marking-resolution-reason" autoComplete="off" maxLength={1000} disabled={busy} value={resolution.reason} onChange={(event) => setResolution({ ...resolution, reason: event.target.value })} /></label>
      <div className="form-actions"><button type="button" className="primary" disabled={busy || !resolution.reason.trim()} onClick={() => void resolve()}>Подтвердить проверку</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setResolution(null)}>Отмена</button></div>
    </div>}
  </section>;
}
