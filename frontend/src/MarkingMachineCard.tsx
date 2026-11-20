import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, failureText, productionApi } from "./api";
import { cuttingLook, cuttingRows, type CuttingGroup } from "./cuttingGroups";
import { DataTable } from "./DataTable";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { MachineSetupPart } from "./MachineSetup";
import { usePolled, type MachineRow } from "./machinesApi";
import { MarkingBatchProgress, MarkingPrintWindow } from "./MarkingPrintWindow";
import { MarkingProfilesWindow } from "./MarkingProfilesWindow";
import {
  boundPrinter, markingApi, type MarkingBatch, type MarkingCatalog, type MarkingManualLine, type MarkingManualRequest,
  type MarkingOrder, type MarkingPreview, type MarkingSource, type MarkingSourceProgress,
} from "./markingApi";
import { PageWindow } from "./PageWindow";
import { useCanAct, useCanPrint, useCurrentUserId } from "./rights";
import "./marking.css";

/** Карточка принтера маркировки (Godex ОБ.101 флажки, ОБ.102 намотка; владелец 20.11.2026: «вбить задание, текст,
 *  порядок»). Сверху — состояние принтера одной строкой; «Что печатаем»: «Из заданий» (группы печати этикеток и
 *  операции маркировки, остаток по каждой) или «Свой текст» (строка — этикетка, × копий, {№} — номера, порядок);
 *  профиль этикетки, предпросмотр первых этикеток, одна кнопка «Печатать N этикеток», ход партии и «Перепечатать».
 *  Наладка (профили, калибровка, привязка принтера, таблица наладки) — свёрнута под кнопкой «Наладка». */

type Tab = "jobs" | "text";
type Job = { key: string; title: string; detail: string; planned: string | null; source: MarkingSource;
  progressOf: { group_id: string } | { task_id: string; operation_id: string }; progress: MarkingSourceProgress | null };

const TOKEN = /\{№(?::[1-6])?\}/;
/** «ПИТАНИЕ +24В × 4» / «… * 4» — четыре копии этой строки. */
const COPIES = /\s+[×*]\s*(\d{1,3})\s*$/;
const MARK_OPERATION = /^(INS_MARK_AUTO|PRINT_MARK_LABEL)/i;
const PREVIEW_SHOWN = 12;

/** Строки поля → надписи с копиями; пустые строки пропускаются. */
export function parseManualLines(text: string, copies: number): MarkingManualLine[] {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const match = COPIES.exec(line);
    return match ? { text: line.slice(0, match.index).trim(), quantity: Math.max(1, Number(match[1])) } : { text: line, quantity: copies };
  }).filter((line) => line.text);
}

/** Сколько этикеток выйдет: строка с {№} — по одной на каждый номер «с»…«по». */
export function manualCount(lines: MarkingManualLine[], first: number, last: number): number {
  const numbers = Number.isInteger(first) && Number.isInteger(last) && last >= first ? last - first + 1 : 0;
  return lines.reduce((sum, line) => sum + line.quantity * (TOKEN.test(line.text) ? numbers : 1), 0);
}

const labelsWord = (count: number) => {
  const tail = count % 100, last = count % 10;
  return tail >= 11 && tail <= 14 ? "этикеток" : last === 1 ? "этикетку" : last >= 2 && last <= 4 ? "этикетки" : "этикеток";
};
const printText = (count: number) => `Печатать ${formatNumber(count)} ${labelsWord(count)}`;
const remember = (key: string, value: string | null) => {
  try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value); } catch { /* только удобство */ }
};
const recall = (key: string) => { try { return sessionStorage.getItem(key); } catch { return null; } };

async function loadJobs(signal: AbortSignal): Promise<Job[]> {
  let groups: CuttingGroup[] | null = null;
  try { groups = (await markingApi.cuttingGroups(signal)).groups; }
  catch (problem) { if (!(problem instanceof ApiError && problem.status === 403)) throw problem; }
  let mine: Awaited<ReturnType<typeof productionApi.mine>> | null = null;
  try { mine = await productionApi.mine(); }
  catch (problem) { if (!(problem instanceof ApiError && problem.status === 403)) throw problem; }
  const jobs: Job[] = [];
  const grouped = new Set<string>();
  for (const group of groups ?? mine?.cutting_groups ?? []) {
    const rows = cuttingRows(group);
    rows.forEach((row) => grouped.add(row.operation_id));
    const look = cuttingLook(group);
    if (group.state !== "open" || !(look === "labels" || (look === "mixed" && rows.some((row) => row.labels?.length)))) continue;
    jobs.push({ key: `group:${group.id}`, title: `${group.project} · ${group.number}`,
      detail: `${group.kind_name || "Печать этикеток"} · КБ: ${formatNumber(rows.length)}`, planned: group.planned_start,
      source: { group_id: group.id, version: group.version }, progressOf: { group_id: group.id }, progress: null });
  }
  for (const task of mine?.tasks ?? []) {
    for (const operation of task.operations.filter((item) => MARK_OPERATION.test(item.code) && !grouped.has(item.id) && item.state !== "выполнена")) {
      jobs.push({ key: `task:${task.id}:${operation.id}`, title: `${task.project} · ${task.item}`,
        detail: `${operation.stage || "Маркировка"} · ${task.name}`, planned: operation.planned_start,
        source: { task_id: task.id, operation_id: operation.id, version: task.version },
        progressOf: { task_id: task.id, operation_id: operation.id }, progress: null });
    }
  }
  const progress = await Promise.allSettled(jobs.slice(0, 40).map((job) => markingApi.progress(job.progressOf, signal)));
  progress.forEach((result, index) => { if (result.status === "fulfilled") jobs[index].progress = result.value; });
  // Сначала то, что осталось напечатать, раньше по плану.
  return jobs.sort((a, b) => Number((b.progress?.remaining_labels ?? 1) > 0) - Number((a.progress?.remaining_labels ?? 1) > 0)
    || (a.planned ?? "9").localeCompare(b.planned ?? "9") || a.title.localeCompare(b.title, "ru", { numeric: true }));
}

export function MarkingMachineCard({ row, close, canWrite }: { row: MachineRow; close: () => void; canWrite: boolean }) {
  const equipmentId = row.equipment_id ?? "";
  const userId = useCurrentUserId();
  const storage = `marking-card:${userId ?? "local"}:${equipmentId}`;
  const canPrint = useCanPrint();
  const canConfigure = useCanAct("erpref");
  const stateLoad = useCallback((signal: AbortSignal) => markingApi.machineState(row.inventory, signal), [row.inventory]);
  const { data: state, error: stateError } = usePolled(stateLoad, 5000, "Нет связи с ERP");
  const [catalog, setCatalog] = useState<MarkingCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [reload, setReload] = useState(0);
  const [tab, setTab] = useState<Tab>("jobs");
  const [jobs, setJobs] = useState<Job[] | null>(null), [jobsError, setJobsError] = useState("");
  const [chosenJob, setChosenJob] = useState("");
  const [printing, setPrinting] = useState<Job | null>(null);
  const [profileId, setProfileId] = useState(() => recall(`${storage}:profile`) ?? "");
  const [text, setText] = useState(""), [copies, setCopies] = useState("1"), [order, setOrder] = useState<MarkingOrder>("listed");
  const [first, setFirst] = useState("1"), [last, setLast] = useState("10");
  const [preview, setPreview] = useState<MarkingPreview | null>(null), [previewError, setPreviewError] = useState("");
  const [batch, setBatch] = useState<MarkingBatch | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [setupOpen, setSetupOpen] = useState(Boolean(row.pending_setup));
  const [profilesOpen, setProfilesOpen] = useState(false);
  const keys = useRef<Record<string, { payload: string; key: string }>>({});

  useEffect(() => {
    const controller = new AbortController();
    setCatalogError("");
    markingApi.machines(controller.signal).then((result) => { if (!controller.signal.aborted) setCatalog(result); })
      .catch((problem) => { if (!controller.signal.aborted) setCatalogError(failureText(problem, "Не удалось загрузить профили принтера")); });
    return () => controller.abort();
  }, [reload]);
  useEffect(() => {
    const controller = new AbortController();
    setJobsError("");
    loadJobs(controller.signal).then((result) => { if (!controller.signal.aborted) setJobs(result); })
      .catch((problem) => { if (!controller.signal.aborted) { setJobs([]); setJobsError(failureText(problem, "Не удалось загрузить задания")); } });
    return () => controller.abort();
  }, [reload]);
  // Партия, начатая в этой карточке, — её ход виден и после повторного открытия.
  useEffect(() => {
    const saved = recall(`${storage}:batch`);
    if (!saved) return;
    const controller = new AbortController();
    markingApi.batch(saved, controller.signal).then((result) => { if (!controller.signal.aborted) setBatch(result); })
      .catch(() => remember(`${storage}:batch`, null));
    return () => controller.abort();
  }, [storage]);

  const machine = catalog?.machines.find((item) => item.equipment_id === equipmentId);
  const profiles = machine?.profiles ?? [];
  const calibrated = profiles.filter((item) => item.calibrated);
  const profile = profiles.find((item) => item.id === profileId) ?? (calibrated.length ? calibrated[0] : undefined);
  const printer = catalog ? boundPrinter(catalog, machine) : undefined;
  const online = Boolean(printer?.present && printer.online);

  const lines = useMemo(() => parseManualLines(text, Math.max(1, Math.trunc(Number(copies)) || 1)), [text, copies]);
  const numbered = lines.some((line) => TOKEN.test(line.text));
  const firstNo = Number(first), lastNo = Number(last);
  const numberingValid = !numbered || (first.trim() !== "" && last.trim() !== "" && Number.isInteger(firstNo) && Number.isInteger(lastNo) && firstNo >= 0 && lastNo >= firstNo);
  const count = manualCount(lines, firstNo, lastNo);
  const request: MarkingManualRequest | null = profile && lines.length && numberingValid ? {
    profile_id: profile.id, profile_version: profile.version, lines, order,
    numbering: numbered ? { first: firstNo, last: lastNo } : null,
  } : null;
  const requestText = request ? JSON.stringify(request) : "";

  // Предпросмотр «Своего текста» — сам, через полсекунды после правки.
  useEffect(() => {
    setPreview(null); setPreviewError("");
    if (!requestText || tab !== "text") return;
    let live = true;
    const timer = setTimeout(() => {
      markingApi.manualPreview(JSON.parse(requestText) as MarkingManualRequest)
        .then((result) => { if (live) setPreview(result); })
        .catch((problem) => { if (live) setPreviewError(failureText(problem, "Предпросмотр не получен")); });
    }, 500);
    return () => { live = false; clearTimeout(timer); };
  }, [requestText, tab]);

  const running = Boolean(batch?.jobs.some((job) => ["pending", "taken"].includes(job.status)));
  const blocker = !canPrint ? "Печать маркировки вам не открыта"
    : !machine?.binding ? "Принтер не привязан — «Наладка» → «Профили и принтер»"
      : !online ? "Принтер не на связи"
        : !profile ? "Нет откалиброванного профиля — «Наладка» → «Профили и принтер»"
          : !profile.calibrated ? "Профиль не откалиброван — «Наладка» → «Профили и принтер»"
            : printer?.hold ? "Маркировка на паузе" : "";

  function chooseProfile(id: string) { setProfileId(id); remember(`${storage}:profile`, id); }

  async function printManual(trial: boolean) {
    if (!request || busy || blocker) return;
    const body = { ...request, ...(trial ? { trial: true } : {}) };
    const payload = JSON.stringify(body);
    const slot = trial ? "trial" : "print";
    // Тот же запрос после потерянного ответа — тот же ключ: сервер вернёт уже созданную партию.
    if (keys.current[slot]?.payload !== payload) keys.current[slot] = { payload, key: crypto.randomUUID() };
    setBusy(true); setError(""); setNotice("");
    try {
      const shown = await markingApi.manualPreview(body);
      const result = await markingApi.manualEnqueue({ ...body, manifest_hash: shown.manifest_hash, idempotency_key: keys.current[slot].key });
      setBatch(result); remember(`${storage}:batch`, result.id);
      keys.current[slot] = { payload: "", key: "" };
      setNotice(trial ? "Пробная этикетка поставлена в очередь" : `${printText(result.total).replace("Печатать", "Поставлено в очередь:")}`);
    } catch (problem) { setError(failureText(problem, "Печать не поставлена в очередь")); }
    finally { setBusy(false); }
  }

  const job = jobs?.find((item) => item.key === chosenJob) ?? null;
  const waitingJobs = jobs?.filter((item) => (item.progress?.remaining_labels ?? 1) > 0).length ?? 0;
  const tone = stateError ? "danger" : state?.state.tone ?? "neutral";
  const lastJob = state?.marking?.last_job;
  const hint = stateError ? "Проверьте сеть — состояние обновится само"
    : !machine?.binding && catalog ? "Привяжите принтер: «Наладка» → «Профили и принтер»"
    : tone === "danger" ? "Проверьте, что ПК принтера включён и агент печати запущен"
      : tone === "warn" ? "Проверьте кабель и питание принтера"
        : "Выберите задание или введите свой текст и нажмите «Печатать»";
  const title = [row.inventory, row.name].filter(Boolean).join(" · ");

  return <PageWindow label={title} close={close} width={1280} dismissable>
    <section className="panel machine-card marking-card">
      <header className="machine-card-head">
        <h2>{title}{row.article && <span className="machine-muted"> · {row.article}</span>}</h2>
        <button type="button" className="secondary small machine-manual" aria-expanded={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>
          Наладка{row.pending_setup ? " · ждёт" : ""}</button>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>

      <div className={`machine-status${tone === "danger" ? " is-danger" : tone === "warn" ? " is-warn" : ""}`} role="status" aria-label="Состояние принтера">
        <span className={`machine-status-title${tone === "danger" || tone === "warn" ? ` tone-${tone}` : ""}`}>{stateError || (state?.state.text ?? "Загрузка…")}</span>
        <span className="machine-status-link">{[state?.marking?.printer || machine?.binding?.printer, state?.connection.pc && `ПК ${state.connection.pc}`,
          state?.last_seen && `сигнал ${formatMoment(state.last_seen)}`].filter(Boolean).join(" · ") || "Принтер не привязан"}</span>
        <span className="machine-status-link tnum">В очереди {formatNumber(state?.marking?.queue ?? 0)} · сегодня напечатано {formatNumber(state?.counter.value ?? 0)}
          {lastJob && <> · последняя {formatMoment(lastJob.finished_at)} {lastJob.status.toLocaleLowerCase("ru")}</>}</span>
        <span className="machine-status-hint">{hint}</span>
      </div>
      {printer?.hold && <p className="warning-inline" role="alert">Маркировка на паузе: {printer.hold}</p>}
      {catalogError && <div className="error-banner" role="alert">{catalogError} <button type="button" className="secondary small" onClick={() => setReload(reload + 1)}>Повторить</button></div>}

      {setupOpen && <section className="marking-card-setup" aria-label="Наладка принтера">
        <header className="machine-setup-head"><h3>Наладка принтера</h3>
          <button type="button" className="secondary small" onClick={() => setProfilesOpen(true)}>Профили и принтер</button>
          <span className="machine-muted">размер этикетки, калибровка, привязка к принтеру, очистка очереди</span></header>
        {row.equipment_id && <MachineSetupPart inventory={row.inventory} canWrite={canWrite} saved={() => undefined} />}
      </section>}

      <section className="marking-card-what" aria-label="Что печатаем">
        <div className="marking-card-what-head">
          <h3>Что печатаем</h3>
          <div className="seg-tabs" role="tablist" aria-label="Что печатаем">
            <button type="button" role="tab" aria-selected={tab === "jobs"} className={tab === "jobs" ? "on" : ""} onClick={() => setTab("jobs")}>
              Из заданий<span className="ship-tab-count">{jobs ? waitingJobs : "…"}</span></button>
            <button type="button" role="tab" aria-selected={tab === "text"} className={tab === "text" ? "on" : ""} onClick={() => setTab("text")}>Свой текст</button>
          </div>
          <label className="marking-card-profile">Профиль этикетки
            <select name="marking-card-profile" autoComplete="off" value={profile?.id ?? ""} disabled={!profiles.length || busy}
              onChange={(event) => chooseProfile(event.target.value)}>
              {!profiles.length && <option value="">{catalog ? "Нет профилей" : "Загрузка…"}</option>}
              {profiles.map((item) => <option key={item.id} value={item.id}>{item.name}{item.calibrated ? "" : " · не откалиброван"}</option>)}
            </select></label>
          {profile && <span className={`marking-card-geometry tnum${profile.calibrated ? " machine-muted" : " tone-warn"}`}>
            {formatNumber(profile.geometry.width_mm)} × {formatNumber(profile.geometry.height_mm)} мм{profile.calibrated ? " · откалиброван" : " · не откалиброван"}</span>}
        </div>

        {tab === "jobs" && <div className="marking-card-jobs">
          {jobsError && <div className="error-banner" role="alert">{jobsError}</div>}
          {!jobs && !jobsError && <p className="empty">Загрузка…</p>}
          {jobs && !jobs.length && !jobsError && <p className="empty">Нет заданий на печать маркировки — введите надписи во вкладке «Свой текст»</p>}
          {jobs && jobs.length > 0 && <DataTable rows={jobs} rowKey={(item) => item.key} label="Задания на печать маркировки" compactBelow={Infinity}
            pageSize={50} phoneCards={false} hideSearch onRowClick={(item) => setChosenJob(item.key)}
            rowClassName={(item) => item.key === chosenJob ? "is-selected" : ""} columns={[
              { key: "pick", title: "", value: () => "", render: (item) => <input type="radio" name="marking-card-job" aria-label={item.title}
                checked={item.key === chosenJob} onChange={() => setChosenJob(item.key)} /> },
              { key: "title", title: "Задание", value: (item) => item.title, render: (item) => <strong>{item.title}</strong> },
              { key: "detail", title: "Что", value: (item) => item.detail, grow: true, wrap: true },
              { key: "planned", title: "План", value: (item) => item.planned ? formatMoment(item.planned) : EMPTY },
              { key: "left", title: "Осталось этикеток", numeric: true, value: (item) => item.progress?.remaining_labels ?? EMPTY },
              { key: "done", title: "Изделий напечатано", numeric: true, value: (item) => item.progress
                ? `${formatNumber(item.progress.printed)} из ${formatNumber(item.progress.printed + item.progress.remaining)}` : EMPTY },
            ]} />}
          <div className="form-actions marking-card-actions">
            <button type="button" className="primary" disabled={!job || Boolean(blocker) || job.progress?.remaining_labels === 0}
              onClick={() => job && setPrinting(job)}>
              {job?.progress ? (job.progress.remaining_labels ? printText(job.progress.remaining_labels) : "Всё напечатано") : "Печатать…"}</button>
            <span className={blocker ? "tone-warn" : "machine-muted"}>{blocker || (job ? job.title : "Выберите задание в списке")}</span>
          </div>
        </div>}

        {tab === "text" && <div className="marking-card-text">
          <label className="marking-card-lines">Надписи — одна строка = одна этикетка
            <textarea name="marking-card-lines" rows={9} spellCheck={false} autoComplete="off" value={text} disabled={busy}
              placeholder={"X1-{№}\nПИТАНИЕ +24В × 4\nGND"} onChange={(event) => setText(event.target.value)} /></label>
          <div className="marking-card-options">
            <label>Копий каждой<input type="number" name="marking-card-copies" inputMode="numeric" min={1} max={500} step={1} value={copies} disabled={busy}
              onChange={(event) => setCopies(event.target.value)} /></label>
            <div role="radiogroup" aria-label="Порядок" className="marking-card-order"><span>Порядок</span>
              <label className="check-inline"><input type="radio" name="marking-card-order" checked={order === "listed"} onChange={() => setOrder("listed")} />как введено</label>
              <label className="check-inline"><input type="radio" name="marking-card-order" checked={order === "number"} onChange={() => setOrder("number")} />по номеру</label>
            </div>
            {numbered && <div className="marking-card-numbers">
              <label>{"{№}"} с<input type="number" name="marking-card-first" inputMode="numeric" min={0} step={1} value={first} disabled={busy} onChange={(event) => setFirst(event.target.value)} /></label>
              <label>по<input type="number" name="marking-card-last" inputMode="numeric" min={0} step={1} value={last} disabled={busy} onChange={(event) => setLast(event.target.value)} /></label>
            </div>}
            <p className="machine-muted">«× 4» в конце строки — 4 копии; {"{№}"} заменяется номером</p>
          </div>
          <div className="marking-card-preview" aria-label="Предпросмотр">
            <div className="marking-card-preview-head"><strong>Предпросмотр</strong>
              <span className="machine-muted tnum">{preview ? `${formatNumber(preview.total)} ${labelsWord(preview.total)} · ${formatNumber(preview.width_mm)} × ${formatNumber(preview.height_mm)} мм` : lines.length ? "…" : EMPTY}</span></div>
            {previewError && <p className="warning-inline">{previewError}</p>}
            {!lines.length && <p className="empty">Введите надписи слева</p>}
            {preview && <ol className="marking-card-strip">
              {preview.labels.slice(0, PREVIEW_SHOWN).map((label, index) => <li key={`${label.key}:${index}`}>
                <img src={`data:image/png;base64,${label.png_b64}`} alt={`Этикетка: ${label.text}`} />
                <span className="tnum">{label.text}{label.quantity > 1 ? ` × ${formatNumber(label.quantity)}` : ""}</span></li>)}
              {preview.labels.length > PREVIEW_SHOWN && <li className="machine-muted">ещё {formatNumber(preview.labels.length - PREVIEW_SHOWN)} надп.</li>}
            </ol>}
          </div>
          <div className="form-actions marking-card-actions">
            <button type="button" className="primary" disabled={busy || !request || Boolean(blocker) || running} onClick={() => void printManual(false)}>
              {busy ? "Обработка…" : printText(preview?.total ?? count)}</button>
            <button type="button" className="secondary" disabled={busy || !request || Boolean(blocker)} onClick={() => void printManual(true)}>Пробная (1 этикетка)</button>
            <span className={blocker || !numberingValid ? "tone-warn" : "machine-muted"}>
              {blocker || (!numberingValid ? "Номера: «с» не больше «по»" : running ? "Дождитесь конца текущей партии" : "")}</span>
          </div>
        </div>}
        {error && <div className="error-banner" role="alert">{error}</div>}
        {notice && <p className="success-banner" role="status">{notice}</p>}
      </section>

      {batch && <MarkingBatchProgress key={batch.id} initial={batch} onChange={setBatch}
        onReprinted={(next) => { setBatch(next); remember(`${storage}:batch`, next.id); }} />}

      <details className="machine-facts marking-card-fold">
        <summary>Журнал печати <span className="machine-muted">· {formatNumber(state?.journal.length ?? 0)}</span></summary>
        {state?.journal.length ? <ul className="marking-card-journal">{state.journal.slice(0, 30).map((item, index) => <li key={index}>
          <span className="machine-mono tnum">{formatMoment(item.at)}</span> <span className={item.tone !== "ok" && item.tone !== "neutral" ? `tone-${item.tone}` : ""}>{item.text}</span></li>)}</ul>
          : <p className="machine-muted">Печати ещё не было</p>}
      </details>
      <details className="machine-facts marking-card-fold">
        <summary>Подключение</summary>
        {state && <dl>{([["ПК", state.connection.pc], ["IP", state.connection.ip], ["Агент", state.connection.agent], ["Версия агента", state.connection.version],
          ["Связь", state.connection.link.text], ["Последний сигнал", state.connection.last_seen ? formatMoment(state.connection.last_seen) : ""],
          ...state.connection.extra] as [string, string][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || EMPTY}</dd></div>)}</dl>}
      </details>
    </section>
    {printing && <MarkingPrintWindow source={printing.source} remaining equipmentId={equipmentId} profileId={profile?.id}
      title={`${printing.title} · ${row.inventory}`} close={() => { setPrinting(null); setReload(reload + 1); }} />}
    {profilesOpen && <MarkingProfilesWindow equipmentIds={[equipmentId]} canEdit={canConfigure}
      close={() => { setProfilesOpen(false); setReload(reload + 1); }} />}
  </PageWindow>;
}
