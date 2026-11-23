import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, failureText, productionApi } from "./api";
import { cuttingLook, cuttingRows, type CuttingGroup } from "./cuttingGroups";
import { DataTable } from "./DataTable";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { MachineSetupPart } from "./MachineSetup";
import { usePolled, type MachineRow } from "./machinesApi";
import { MarkingOutsideWindow } from "./MarkingOutsideWindow";
import { MarkingBatchProgress, MarkingPrintWindow } from "./MarkingPrintWindow";
import { MarkingProfilesWindow } from "./MarkingProfilesWindow";
import {
  boundPrinter, formatSize, markingApi, type MarkingBatch, type MarkingCatalog, type MarkingManualLine, type MarkingManualRequest,
  markingProblem, type MarkingOrder, type MarkingPreview, type MarkingRequest, type MarkingSource, type MarkingSourceProgress,
  parseWireMm, sizeMatches,
} from "./markingApi";
import { PageWindow } from "./PageWindow";
import { useCanAct, useCanPrint, useCurrentUserId } from "./rights";
import "./marking.css";

/** Карточка принтера маркировки — один пульт (Godex ОБ.101 флажки, ОБ.102 намотка; владелец 20–21.11.2026). Сверху —
 *  состояние принтера одной строкой; «Что печатаем»: «Из заданий» — выбрал строку, и тут же размер этикетки и Ø провода
 *  из ЭТК, порядок, предпросмотр оставшихся, «Пробная (1 этикетка)» и «Печатать N»; профиль не как в ЭТК — с причиной;
 *  «Выбрать надписи…» — прежнее окно для выборочной печати. «Свой текст» — строка = этикетка, × копий, {№}, Ø провода.
 *  Ход партии и «Перепечатать» — под ним. «Наладка» (профили, калибровка, привязка, таблица наладки) — отдельным окном. */

type Tab = "jobs" | "text";
type Job = { key: string; title: string; detail: string; planned: string | null; source: MarkingSource;
  progressOf: { group_id: string } | { task_id: string; operation_id: string }; progress: MarkingSourceProgress | null;
  /** Группа печати этикеток: из неё «Отметить напечатанные вне ERP». */
  group?: CuttingGroup };

const TOKEN = /\{№(?::[1-6])?\}/;
/** «ПИТАНИЕ +24В × 4» / «… * 4» — четыре копии этой строки. */
const COPIES = /\s+[×*]\s*(\d+)\s*$/;
const MARK_OPERATION = /^(INS_MARK_AUTO|PRINT_MARK_LABEL)/i;
const PREVIEW_SHOWN = 12;

/** Строки поля → надписи с копиями; пустые строки пропускаются. */
export function parseManualLines(text: string, copies: number): MarkingManualLine[] {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const match = COPIES.exec(line);
    return match ? { text: line.slice(0, match.index).trim(), quantity: Number(match[1]) } : { text: line, quantity: copies };
  }).filter((line) => line.text);
}

const MAX_COPIES = 500;
type ManualRow = { id: number; text: string; copies: string; canonical?: boolean };
// Imported/normalized text is literal: a second trailing «× N» belongs to the label.
const manualRowLines = (row: ManualRow): MarkingManualLine[] => row.canonical
  ? row.text.trim() ? [{ text: row.text.trim(), quantity: Number(row.copies) }] : []
  : parseManualLines(row.text, Number(row.copies));
/** Что не так с копиями: «× 0» и выход за 1…500 не округляются молча, а отклоняются с текстом. */
export function copiesProblem(lines: MarkingManualLine[], copies: string): string {
  const common = Number(copies);
  if (copies.trim() === "" || !Number.isInteger(common) || common < 1) return "Копий каждой — целое число не меньше 1";
  if (common > MAX_COPIES) return `Копий каждой — не больше ${MAX_COPIES}`;
  const zero = lines.find((line) => line.quantity < 1);
  if (zero) return `Копий в строке «${zero.text}» должно быть не меньше 1 (× 0 не печатает)`;
  const big = lines.find((line) => line.quantity > MAX_COPIES);
  return big ? `Копий в строке «${big.text}» — не больше ${MAX_COPIES}` : "";
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
const sizeText = (size: { width_mm: number; height_mm: number }) => `${formatSize(size)} мм`;
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
      source: { group_id: group.id, version: group.version }, progressOf: { group_id: group.id }, progress: null, group });
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

export function MarkingMachineCard({ row, close, canWrite, incoming, openJournal }: { row: MachineRow; close: () => void; canWrite: boolean;
  incoming?: import("./machinesApi").MachineTaskContext; openJournal?: () => void }) {
  const incomingSource = incoming?.action.source?.kind === "marking" ? incoming.action.source : null;
  const [incomingError, setIncomingError] = useState("");
  const appliedIncoming = useRef(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
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
  const nextRow = useRef(1);
  const [manualRows, setManualRows] = useState<ManualRow[]>([{ id: 0, text: "", copies: "1" }]);
  const [pasteOpen, setPasteOpen] = useState(false), [pasteText, setPasteText] = useState("");
  const [order, setOrder] = useState<MarkingOrder>("listed");
  const rowInputs = useRef(new Map<number, HTMLInputElement>());
  const [first, setFirst] = useState("1"), [last, setLast] = useState("10");
  // Предпросмотр помнит запрос, для которого получен: «Печатать» уходит с хэшем показанного, а не свежего.
  const [shown, setShown] = useState<{ key: string; result: MarkingPreview } | null>(null), [previewError, setPreviewError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const inflight = useRef("");
  const [batch, setBatch] = useState<MarkingBatch | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  // «Наладка» — окно по кнопке; «· ждёт» на кнопке говорит о наладке, само окно не открывается (владелец 21.11).
  const [setupOpen, setSetupOpen] = useState(false);
  const [profilesOpen, setProfilesOpen] = useState(false);
  const [outside, setOutside] = useState<CuttingGroup | null>(null);
  // «Из заданий»: порядок «по изделию», причина отступления от ЭТК, предпросмотр оставшихся выбранного задания.
  const [interleave, setInterleave] = useState(false), [reason, setReason] = useState("");
  const [jobShown, setJobShown] = useState<{ key: string; result: MarkingPreview } | null>(null), [jobPreviewError, setJobPreviewError] = useState("");
  const [jobTick, setJobTick] = useState(0);
  const [wire, setWire] = useState("");
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

  const lines = useMemo(() => manualRows.flatMap(manualRowLines), [manualRows]);
  const copiesError = lines.some((line) => !Number.isInteger(line.quantity))
    ? "Копий в строке — целое число от 1 до 500" : copiesProblem(lines, "1");
  const numbered = lines.some((line) => TOKEN.test(line.text));
  const firstNo = Number(first), lastNo = Number(last);
  const numberingValid = !numbered || (first.trim() !== "" && last.trim() !== "" && Number.isInteger(firstNo) && Number.isInteger(lastNo) && firstNo >= 0 && lastNo >= firstNo && lastNo <= 999999);
  const count = copiesError || !numberingValid ? 0 : manualCount(lines, firstNo, lastNo);
  const totalError = count > MAX_COPIES ? `За один раз — не более ${MAX_COPIES} этикеток. Уменьшите копии или диапазон номеров` : "";
  const wireMm = parseWireMm(wire);
  const wireError = wireMm === undefined ? "Ø провода — число от 0,1 до 60 мм" : "";
  const request: MarkingManualRequest | null = profile && lines.length && numberingValid && !copiesError && !wireError && !totalError ? {
    profile_id: profile.id, profile_version: profile.version, lines, order,
    numbering: numbered ? { first: firstNo, last: lastNo } : null, ...(wireMm != null ? { wire_mm: wireMm } : {}),
  } : null;
  const requestText = request ? JSON.stringify(request) : "";

  // Предпросмотр «Своего текста» — сам, через полсекунды после правки. Прошлый запрос отменяется (сервер рисует
  // каждую надпись), по тому же тексту второй не стартует, пока первый в пути.
  useEffect(() => {
    setShown(null); setPreviewError("");
    if (!requestText || tab !== "text") return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      if (inflight.current === requestText) return;
      inflight.current = requestText;
      markingApi.manualPreview(JSON.parse(requestText) as MarkingManualRequest, controller.signal)
        .then((result) => { if (!controller.signal.aborted) setShown({ key: requestText, result }); })
        .catch((problem) => { if (!controller.signal.aborted) setPreviewError(markingProblem(problem, "Предпросмотр не получен")); })
        .finally(() => { if (inflight.current === requestText) inflight.current = ""; });
    }, 500);
    return () => { controller.abort(); clearTimeout(timer); if (inflight.current === requestText) inflight.current = ""; };
  }, [requestText, tab, refreshTick]);
  const preview = shown && shown.key === requestText ? shown.result : null;

  const job = jobs?.find((item) => item.key === chosenJob) ?? null;
  const etkSize = job?.progress?.label_size ?? null;
  // Ø провода — только из /progress (предпросмотр его не несёт): общий список задания, иначе — по строкам КБ.
  const wireList = job?.progress ? (job.progress.wire_mm?.length ? job.progress.wire_mm
    : [...new Set((job.progress.rows ?? []).flatMap((item) => item.wire_mm ?? []))].sort((a, b) => a - b)) : [];
  const jobLeft = job?.progress ? job.progress.remaining_labels : null;
  const jobRequest: MarkingRequest | null = job && profile && jobLeft !== 0 ? { profile_id: profile.id, profile_version: profile.version,
    source: job.source, selected: incomingSource?.selected ?? [], remaining: true, ...(interleave ? { interleave: true } : {}) } : null;
  const jobRequestText = jobRequest ? JSON.stringify(jobRequest) : "";
  // Выбрал задание (профиль, порядок) — предпросмотр оставшихся сам; «Печатать» уходит с хэшем показанного.
  useEffect(() => {
    setJobShown(null); setJobPreviewError("");
    if (!jobRequestText || tab !== "jobs") return;
    const controller = new AbortController();
    markingApi.preview(JSON.parse(jobRequestText) as MarkingRequest, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setJobShown({ key: jobRequestText, result }); })
      .catch((problem) => { if (!controller.signal.aborted) setJobPreviewError(markingProblem(problem, "Предпросмотр не получен")); });
    return () => controller.abort();
  }, [jobRequestText, tab, jobTick]);
  const jobPreview = jobShown && jobShown.key === jobRequestText ? jobShown.result : null;
  // Сервер сверяет профиль с размером из ЭТК; не совпал — партия (и пробная) только с причиной.
  const needsReason = jobPreview?.profile_matches === false;
  const reasonMissing = needsReason && !reason.trim();
  // Причина — к выбранному профилю: сменили профиль — причину пишут заново; итог прошлой постановки к новому не относится.
  useEffect(() => { setReason(""); setNotice(""); }, [profile?.id]);

  const running = Boolean(batch?.jobs.some((job) => ["pending", "taken"].includes(job.status)));
  const blocker = !canPrint ? "Печать маркировки вам не открыта"
    : !machine?.binding ? "Принтер не привязан — «Наладка» → «Профили и принтер»"
      : !online ? "Принтер не на связи"
        : !profile ? "Нет откалиброванного профиля — «Наладка» → «Профили и принтер»"
          : !profile.calibrated ? "Профиль не откалиброван — «Наладка» → «Профили и принтер»"
            : printer?.hold ? "Маркировка на паузе" : "";
  const manualHardwareBlocker = blocker === "Принтер не на связи"
    ? "Принтер не на связи — проверьте питание, кабель и агент печати на ПК принтера" : blocker
      || (stateError ? "Нет связи с ERP — проверьте сеть; состояние обновится автоматически"
        : !state ? "Проверяем связь с принтером…"
          : state.connection.link.code !== "ok" ? "Нет связи с ПК принтера — включите ПК и запустите агент печати"
            : state.marking?.present === false ? "Принтер не найден — проверьте питание и кабель принтера" : "");
  const manualBlocker = catalogError || (!catalog ? "Загрузка профилей принтера…" : manualHardwareBlocker)
    || copiesError || (!numberingValid ? "Номера: «с» не больше «по»; укажите целые числа от 0" : "")
    || wireError || totalError || (!lines.length ? "Добавьте надпись или вставьте список" : "")
    || (running ? "Дождитесь конца текущей партии" : "") || previewError
    || (!preview ? "Обновляем предпросмотр…" : "");

  function updateManualRow(id: number, changes: Partial<ManualRow>) {
    setManualRows((rows) => rows.map((item) => item.id === id ? { ...item, ...changes } : item));
  }

  function addManualRow() {
    const id = nextRow.current++;
    setManualRows((rows) => [...rows, { id, text: "", copies: "1" }]);
    requestAnimationFrame(() => rowInputs.current.get(id)?.focus());
  }

  function importManualList() {
    const added = parseManualLines(pasteText, 1).map((line) => ({ id: nextRow.current++, text: line.text, copies: String(line.quantity), canonical: true }));
    if (!added.length) return;
    setManualRows((rows) => [...rows.filter((item) => item.text.trim()), ...added]);
    setPasteText(""); setPasteOpen(false);
    requestAnimationFrame(() => rowInputs.current.get(added[0].id)?.focus());
  }

  /** Другая вкладка — итог и ошибка прошлой постановки к ней не относятся. */
  function switchTab(next: Tab) { if (next !== tab) { setTab(next); setNotice(""); setError(""); } }

  function chooseProfile(id: string) { setProfileId(id); remember(`${storage}:profile`, id); }

  /** Выбор задания: профиль — тот, что по размеру как в ЭТК (если такой откалиброван). */
  function chooseJob(item: Job) {
    if (incoming && !matchesIncoming(item)) return;
    if (item.key === chosenJob) return;
    setChosenJob(item.key); setReason(""); setError(""); setNotice("");
    const size = item.progress?.label_size;
    const fit = size ? calibrated.find((candidate) => sizeMatches(candidate.geometry, size)) : undefined;
    if (fit) setProfileId(fit.id);
  }

  function matchesIncoming(item: Job): boolean {
    const wanted = incomingSource?.source;
    return Boolean(wanted && item.source.version === wanted.version
      && ("group_id" in wanted ? "group_id" in item.source && item.source.group_id === wanted.group_id
        : "task_id" in item.source && item.source.task_id === wanted.task_id && item.source.operation_id === wanted.operation_id));
  }
  useEffect(() => {
    if (!incoming || !jobs || !catalog) return;
    const matches = jobs.filter(matchesIncoming);
    if (matches.length !== 1) {
      setChosenJob("");
      setIncomingError("Задание маркировки из инструкции изменилось или больше недоступно — откройте инструкцию заново");
      return;
    }
    if (appliedIncoming.current) return;
    appliedIncoming.current = true;
    setTab("jobs");
    chooseJob(matches[0]);
  }, [incoming, jobs, catalog]); // eslint-disable-line react-hooks/exhaustive-deps

  /** После постановки: остаток задания с сервера и свежий предпросмотр (поставленные в очередь уже не «осталось»). */
  async function refreshJob(target: Job) {
    try {
      const progress = await markingApi.progress(target.progressOf);
      setJobs((list) => list?.map((item) => item.key === target.key ? { ...item, progress } : item) ?? list);
    } catch { /* остаток в списке прежний — обновится при следующем открытии */ }
    setJobTick((tick) => tick + 1);
  }

  async function printJob(trial: boolean) {
    if (!job || !jobRequest || !jobPreview || busy || blocker || reasonMissing || (!trial && running)) return;
    // «Пробная» — первая из оставшихся настоящих этикеток: тратит свой номер и считается напечатанной.
    const body: MarkingRequest = { ...jobRequest, ...(trial ? { limit: 1 } : {}) };
    const extra = needsReason ? { reason: reason.trim() } : {};
    const payload = JSON.stringify({ ...body, ...extra });
    const slot = trial ? "job-trial" : "job-print";
    if (keys.current[slot]?.payload !== payload) keys.current[slot] = { payload, key: crypto.randomUUID() };
    setBusy(true); setError(""); setNotice("");
    try {
      let hash = jobPreview.manifest_hash, send = extra;
      if (trial) {
        // Хэш пробной — из своего предпросмотра на 1 этикетку: печатаем, только если это та же первая этикетка, что на экране.
        const fresh = await markingApi.preview(body);
        if (!active.current) return;
        const [seen, next] = [jobPreview.labels[0], fresh.labels[0]];
        if (!seen || !next || seen.key !== next.key || seen.serial !== next.serial || seen.text !== next.text
          || (fresh.profile_matches !== false) !== (jobPreview.profile_matches !== false)) {
          setJobTick((tick) => tick + 1);
          setNotice("Предпросмотр изменился — проверьте и нажмите ещё раз");
          return;
        }
        hash = fresh.manifest_hash;
        send = fresh.profile_matches === false ? { reason: reason.trim() } : {};
      }
      const result = await markingApi.enqueue({ ...body, ...send, manifest_hash: hash, idempotency_key: keys.current[slot].key });
      setBatch(result); remember(`${storage}:batch`, result.id);
      keys.current[slot] = { payload: "", key: "" };
      setNotice(trial ? "Пробная этикетка поставлена в очередь — проверьте её на проводе" : printText(result.total).replace("Печатать", "Поставлено в очередь:"));
      void refreshJob(job);
    } catch (problem) {
      if (problem instanceof ApiError && problem.status === 409) {
        // Кто-то напечатал раньше или задание изменилось: свежие версии заданий, остаток и предпросмотр — иначе каждый
        // следующий щелчок повторит тот же отказ.
        setError(`${markingProblem(problem, "Задание изменилось")} — задания и предпросмотр обновлены, проверьте и нажмите ещё раз`);
        setReload((value) => value + 1);
        void refreshJob(job);
      } else setError(markingProblem(problem, "Печать не поставлена в очередь"));
    }
    finally { setBusy(false); }
  }

  async function printManual(trial: boolean) {
    if (!request || busy || manualHardwareBlocker || catalogError) return;
    const body = { ...request, ...(trial ? { trial: true } : {}) };
    // «Печатать» — по предпросмотру на экране; если запрос изменился после него, сначала обновить и нажать ещё раз.
    if (!trial && !preview) {
      if (!inflight.current) setRefreshTick((tick) => tick + 1);
      setError(""); setNotice("Предпросмотр обновляется — проверьте этикетки и нажмите «Печатать» ещё раз");
      return;
    }
    const payload = JSON.stringify(body);
    const slot = trial ? "trial" : "print";
    // Тот же запрос после потерянного ответа — тот же ключ: сервер вернёт уже созданную партию.
    if (keys.current[slot]?.payload !== payload) keys.current[slot] = { payload, key: crypto.randomUUID() };
    setBusy(true); setError(""); setNotice("");
    try {
      // Пробная — одна этикетка: её предпросмотр свой и дешёвый; обычная печать берёт хэш показанного.
      const hash = trial ? (await markingApi.manualPreview(body)).manifest_hash : (preview as MarkingPreview).manifest_hash;
      if (!active.current) return;
      const result = await markingApi.manualEnqueue({ ...body, manifest_hash: hash, idempotency_key: keys.current[slot].key });
      setBatch(result); remember(`${storage}:batch`, result.id);
      keys.current[slot] = { payload: "", key: "" };
      setNotice(trial ? "Пробная этикетка поставлена в очередь" : `${printText(result.total).replace("Печатать", "Поставлено в очередь:")}`);
    } catch (problem) { setError(markingProblem(problem, "Печать не поставлена в очередь")); }
    finally { setBusy(false); }
  }

  const waitingJobs = jobs?.filter((item) => (item.progress?.remaining_labels ?? 1) > 0).length ?? 0;
  const tone = stateError ? "danger" : state?.state.tone ?? "neutral";
  const lastJob = state?.marking?.last_job;
  const hint = stateError ? "Проверьте сеть — состояние обновится само"
    : !machine?.binding && catalog ? "Привяжите принтер: «Наладка» → «Профили и принтер»"
    : tone === "danger" ? "Проверьте, что ПК принтера включён и агент печати запущен"
      : tone === "warn" ? "Проверьте кабель и питание принтера"
        : "Выберите задание или введите свой текст и нажмите «Печатать»";
  const title = [row.inventory, row.name].filter(Boolean).join(" · ");
  // Профиль рядом с размером из ЭТК: совпал — «· из ЭТК», нет — «· в ЭТК 25 × 9 мм» янтарём (уставка против источника).
  // Размер из ЭТК: показанный предпросмотр — правда сервера (null — в ЭТК не указан, причина не нужна); до него — /progress.
  const shownEtk = jobPreview ? jobPreview.etk_size ?? null : etkSize;
  const etkMode = tab === "jobs" && job && jobLeft !== 0;
  const profileFits = profile && shownEtk ? (jobPreview ? jobPreview.profile_matches !== false : sizeMatches(profile.geometry, shownEtk)) : null;
  const profileNote = !profile?.calibrated ? "не откалиброван"
    : !etkMode ? "откалиброван"
      : shownEtk ? (profileFits ? "из ЭТК" : `в ЭТК ${sizeText(shownEtk)}`)
        : job?.progress?.sizes_differ ? "в ЭТК размеры разные" : "размер в ЭТК не указан";
  const profileWarn = !profile?.calibrated || (etkMode && Boolean(shownEtk) && !profileFits);

  return <PageWindow label={title} close={close} width={1280} dismissable>
    <section className="panel machine-card marking-card">
      <header className="machine-card-head">
        <h2>{title}{row.article && <span className="machine-muted"> · {row.article}</span>}</h2>
        {openJournal && <button type="button" className="secondary small" onClick={openJournal}>Журнал</button>}
        <button type="button" className="secondary small machine-manual" aria-haspopup="dialog" onClick={() => setSetupOpen(true)}>
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
      {incoming && <p className={incomingError ? "error-banner" : "machine-muted"} role={incomingError ? "alert" : "status"}>
        {incomingError || `Операция ЭТК ${incoming.action.etk_position} · из задания`}</p>}

      <section className="marking-card-what" aria-label="Что печатаем">
        <div className="marking-card-what-head">
          <h3>Что печатаем</h3>
          <div className="seg-tabs" role="tablist" aria-label="Что печатаем">
            <button type="button" role="tab" aria-selected={tab === "jobs"} className={tab === "jobs" ? "on" : ""} onClick={() => switchTab("jobs")}>
              Из заданий<span className="ship-tab-count">{jobs ? waitingJobs : "…"}</span></button>
            <button type="button" role="tab" aria-selected={tab === "text"} className={tab === "text" ? "on" : ""} onClick={() => switchTab("text")}>Свой текст</button>
          </div>
          <label className="marking-card-profile">Профиль этикетки
            <select name="marking-card-profile" autoComplete="off" value={profile?.id ?? ""} disabled={!profiles.length || busy}
              onChange={(event) => chooseProfile(event.target.value)}>
              {!profiles.length && <option value="">{catalog ? "Нет профилей" : "Загрузка…"}</option>}
              {tab === "text" && profiles.length > 0 && !profile && <option value="">Выберите профиль</option>}
              {profiles.map((item) => <option key={item.id} value={item.id}>{item.name}{tab === "text"
                ? ` · ${item.calibrated ? "откалиброван" : "нужна калибровка"}` : item.calibrated ? "" : " · не откалиброван"}</option>)}
            </select></label>
          {profile && <span className={`marking-card-geometry tnum${profileWarn ? " tone-warn" : " machine-muted"}`}>{sizeText(profile.geometry)} · {profileNote}</span>}
        </div>

        {tab === "jobs" && <div className="marking-card-jobs">
          {jobsError && <div className="error-banner" role="alert">{jobsError}</div>}
          {!jobs && !jobsError && <p className="empty">Загрузка…</p>}
          {jobs && !jobs.length && !jobsError && <p className="empty">Нет заданий на печать маркировки — введите надписи во вкладке «Свой текст»</p>}
          {jobs && jobs.length > 0 && <DataTable rows={jobs} rowKey={(item) => item.key} label="Задания на печать маркировки" compactBelow={Infinity}
            pageSize={50} phoneCards={false} hideSearch onRowClick={chooseJob}
            rowClassName={(item) => item.key === chosenJob ? "is-selected" : ""} columns={[
              { key: "pick", title: "", value: () => "", render: (item) => <input type="radio" name="marking-card-job" aria-label={item.title}
                checked={item.key === chosenJob} disabled={Boolean(incoming) && !matchesIncoming(item)} onChange={() => chooseJob(item)} /> },
              { key: "title", title: "Задание", value: (item) => item.title, render: (item) => <strong>{item.title}</strong> },
              { key: "detail", title: "Что", value: (item) => item.detail, grow: true, wrap: true },
              { key: "planned", title: "План", value: (item) => item.planned ? formatMoment(item.planned) : EMPTY },
              { key: "size", title: "Этикетка по ЭТК, мм", value: (item) => item.progress?.label_size ? formatSize(item.progress.label_size)
                : item.progress?.sizes_differ ? "разные" : EMPTY },
              { key: "left", title: "Осталось этикеток", numeric: true, value: (item) => item.progress?.remaining_labels ?? EMPTY },
              { key: "done", title: "Изделий напечатано", numeric: true, value: (item) => item.progress
                ? `${formatNumber(item.progress.printed)} из ${formatNumber(item.progress.printed + item.progress.remaining)}` : EMPTY },
            ]} />}
          {job && jobLeft !== 0 && <div className="marking-card-pult" role="group" aria-label="Печать задания">
            <div className="marking-card-facts">
              {wireList.length
                ? <span className="tnum">Ø провода {wireList.map((value) => formatNumber(value)).join("; ")} мм · из ЭТК</span>
                : <span className="machine-muted">Ø провода в ЭТК не указан</span>}
              <div role="radiogroup" aria-label="Порядок этикеток" className="marking-card-order is-row"><span>Порядок</span>
                <label className="check-inline"><input type="radio" name="marking-card-job-order" checked={!interleave} disabled={busy} onChange={() => setInterleave(false)} />как в ЭТК</label>
                <label className="check-inline"><input type="radio" name="marking-card-job-order" checked={interleave} disabled={busy} onChange={() => setInterleave(true)} />по изделию</label>
              </div>
            </div>
            {needsReason && profile && <label className="marking-card-reason">
              <span className="tone-warn">Профиль {sizeText(profile.geometry)}, в ЭТК {shownEtk ? sizeText(shownEtk) : "другой размер"} — причина</span>
              <input name="marking-card-reason" autoComplete="off" maxLength={1000} required disabled={busy} value={reason}
                placeholder="Почему печатаем на этикетке не того размера" onChange={(event) => setReason(event.target.value)} /></label>}
            <div className="marking-card-preview" aria-label="Предпросмотр">
              <div className="marking-card-preview-head"><strong>Предпросмотр оставшихся</strong>
                <button type="button" className="secondary small" disabled={busy} onClick={() => setJobTick((tick) => tick + 1)}>Обновить предпросмотр</button>
                <span className="machine-muted tnum">{jobPreview ? `${formatNumber(jobPreview.total)} ${labelsWord(jobPreview.total)} · ${formatNumber(jobPreview.width_mm)} × ${formatNumber(jobPreview.height_mm)} мм` : jobPreviewError ? EMPTY : "…"}</span></div>
              {jobPreviewError && <p className="warning-inline">{jobPreviewError}</p>}
              {jobPreview && <LabelStrip preview={jobPreview} />}
            </div>
          </div>}
          <div className="form-actions marking-card-actions">
            <button type="button" className="primary" disabled={!job || !jobPreview || busy || Boolean(blocker) || running || jobLeft === 0 || reasonMissing}
              onClick={() => void printJob(false)}>
              {busy ? "Обработка…" : !job ? "Печатать…" : jobLeft === 0 ? "Всё напечатано" : printText(jobPreview?.total ?? jobLeft ?? 0)}</button>
            <button type="button" className="secondary" disabled={!job || !jobPreview || busy || Boolean(blocker) || jobLeft === 0 || reasonMissing}
              onClick={() => void printJob(true)}>Пробная (1 этикетка)</button>
            {!incoming && <button type="button" className="secondary" disabled={!job || busy || Boolean(blocker)} onClick={() => job && setPrinting(job)}>Выбрать надписи…</button>}
            {job?.group && <button type="button" className="secondary" disabled={busy} onClick={() => setOutside(job.group ?? null)}>Отметить напечатанные вне ERP</button>}
            <span className={blocker || reasonMissing ? "tone-warn" : "machine-muted"}>{blocker || (!job ? "Выберите задание в списке"
              : jobLeft === 0 ? "Все этикетки задания напечатаны" : reasonMissing ? "Укажите причину — размер этикетки не как в ЭТК"
                : running ? "Дождитесь конца текущей партии" : "")}</span>
          </div>
        </div>}

        {tab === "text" && <div className="marking-card-text">
          <div className="marking-text-editor">
            <div className="marking-text-heading"><strong>Надписи</strong>
              {numbered && <div className="marking-card-numbers">
                <span>Номера</span>
                <label>{"{№}"} с<input type="number" name="marking-card-first" autoComplete="off" inputMode="numeric" min={0} max={999999} step={1} value={first} disabled={busy}
                  aria-invalid={!numberingValid} onChange={(event) => setFirst(event.target.value)} /></label>
                <label>по<input type="number" name="marking-card-last" autoComplete="off" inputMode="numeric" min={0} max={999999} step={1} value={last} disabled={busy}
                  aria-invalid={!numberingValid} onChange={(event) => setLast(event.target.value)} /></label>
              </div>}
              <button type="button" className="secondary small" aria-expanded={pasteOpen} aria-controls="marking-paste-list"
                disabled={busy} onClick={() => setPasteOpen(!pasteOpen)}>Вставить список</button></div>
            {pasteOpen && <div className="marking-text-paste" id="marking-paste-list">
              <label>Список надписей<textarea name="marking-paste" autoComplete="off" spellCheck={false} rows={3} value={pasteText} disabled={busy}
                placeholder={"X1-{№}\nПИТАНИЕ +24В × 4\nGND…"} onChange={(event) => setPasteText(event.target.value)} /></label>
              <div className="marking-text-heading"><span className="machine-muted">Одна строка — одна надпись; «× 4» — четыре копии</span>
                <button type="button" className="secondary" disabled={busy || !parseManualLines(pasteText, 1).length} onClick={importManualList}>Добавить из списка</button></div>
            </div>}
            <div className="marking-text-list">
              <table aria-label="Надписи для печати">
                <thead><tr><th scope="col">Надпись</th><th scope="col">Копий</th><th scope="col">Итого</th><th scope="col">Удалить</th></tr></thead>
                <tbody>{manualRows.map((item, index) => {
                  const parsed = manualRowLines(item);
                  const quantity = parsed[0]?.quantity ?? 1;
                  const invalid = parsed.length > 0 && (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_COPIES);
                  const rowTotal = invalid || !numberingValid ? 0 : manualCount(parsed, firstNo, lastNo);
                  const hasNumber = TOKEN.test(item.text);
                  return <tr key={item.id}>
                    <td><input aria-label={`Надпись ${index + 1}`} name={`marking-text-${item.id}`} autoComplete="off" spellCheck={false} maxLength={310}
                      ref={(node) => { if (node) rowInputs.current.set(item.id, node); else rowInputs.current.delete(item.id); }}
                      value={item.text} disabled={busy} placeholder="Например, X1-{№}…" onChange={(event) => updateManualRow(item.id, { text: event.target.value })}
                      onBlur={() => { if (!item.canonical && COPIES.test(item.text) && parsed[0]) updateManualRow(item.id, { text: parsed[0].text, copies: String(parsed[0].quantity), canonical: true }); }} />
                      {hasNumber && <span className="marking-text-chip tnum">{item.text.match(TOKEN)?.[0]} → {numberingValid ? `${formatNumber(firstNo)}…${formatNumber(lastNo)}` : "задайте номера"}</span>}</td>
                    <td><input aria-label={`Копий в строке ${index + 1}`} name={`marking-copies-${item.id}`} autoComplete="off" type="number" inputMode="numeric"
                      min={1} max={500} step={1} value={item.copies} disabled={busy} aria-invalid={invalid} aria-describedby={invalid ? "marking-text-validation" : undefined}
                      onChange={(event) => updateManualRow(item.id, { copies: event.target.value })} /></td>
                    <td className="tnum"><output aria-label={`Итого в строке ${index + 1}`}>{invalid || (hasNumber && !numberingValid) ? EMPTY : formatNumber(rowTotal)}</output></td>
                    <td><button type="button" className="secondary marking-text-remove" aria-label={`Удалить строку ${index + 1}`} disabled={busy}
                      onClick={() => {
                        setManualRows((rows) => rows.length === 1 ? [{ ...item, text: "", copies: "1", canonical: false }] : rows.filter((entry) => entry.id !== item.id));
                        requestAnimationFrame(() => rowInputs.current.get(manualRows[index + 1]?.id ?? manualRows[index - 1]?.id ?? item.id)?.focus());
                      }}>×</button></td>
                  </tr>;
                })}</tbody>
              </table>
            </div>
            <div className="marking-text-heading"><button type="button" className="secondary" disabled={busy} onClick={addManualRow}>Добавить строку</button>
              <span className="machine-muted">{lines.length ? `${formatNumber(lines.length)} надп. · до 500 этикеток за раз` : "Введите надпись и укажите количество копий"}</span></div>
            <div className="marking-text-settings">
              <div className="marking-text-order"><span>Порядок печати</span>
                <div className="seg-tabs" role="group" aria-label="Порядок">
                  <button type="button" className={order === "listed" ? "on" : ""} aria-pressed={order === "listed"} disabled={busy} onClick={() => setOrder("listed")}>Как введено</button>
                  <button type="button" className={order === "number" ? "on" : ""} aria-pressed={order === "number"} disabled={busy} onClick={() => setOrder("number")}>По № провода</button>
                </div></div>
              <label className="marking-text-wire">Ø провода, мм <span className="machine-muted">необязательно</span>
                <input aria-label="Ø провода, мм" name="marking-card-wire" inputMode="decimal" autoComplete="off" spellCheck={false} placeholder="1,6…" value={wire} disabled={busy}
                  aria-invalid={Boolean(wireError)} aria-describedby={wireError ? "marking-text-validation" : undefined} onChange={(event) => setWire(event.target.value)} /></label>
            </div>
            {(copiesError || wireError || !numberingValid || totalError) && <p className="warning-inline" role="alert" id="marking-text-validation">
              {copiesError || wireError || (!numberingValid ? "Номера: «с» не больше «по»; укажите целые числа от 0" : totalError)}</p>}
          </div>
          <div className="marking-card-preview marking-text-preview" aria-label="Предпросмотр" aria-busy={Boolean(request && !preview && !previewError)}>
            <div className="marking-card-preview-head"><strong>Первые этикетки</strong>
              <span className="machine-muted tnum">{profile ? sizeText(profile.geometry) : "Выберите профиль"}</span></div>
            {preview ? <LabelStrip preview={preview} proportional /> : <div className="marking-text-preview-empty">
              <span className="marking-text-blank" aria-hidden="true" style={{ aspectRatio: profile ? `${profile.geometry.width_mm} / ${profile.geometry.height_mm}` : "25 / 9" }}>Аа</span>
              <p>{previewError ? "Предпросмотр не получен" : !lines.length ? "Введите надпись — здесь появится этикетка" : !request ? "Проверьте параметры слева" : "Обновляем предпросмотр…"}</p>
            </div>}
            {previewError && <button type="button" className="secondary" disabled={busy} onClick={() => setRefreshTick((tick) => tick + 1)}>Повторить предпросмотр</button>}
            <p className="machine-muted">Проверьте текст и размер. Пробная печать — одна этикетка.</p>
          </div>
          <div className="marking-text-footer">
            <p className="marking-text-summary tnum" aria-live="polite"><strong>{formatNumber(preview?.total ?? count)} {labelsWord(preview?.total ?? count).replace("этикетку", "этикетка")}</strong>
              <span> · профиль {profile ? sizeText(profile.geometry) : "не выбран"} · принтер {printer?.name ?? "не привязан"}</span></p>
            <div className="form-actions marking-card-actions">
              <button type="button" className="primary" disabled={busy || !request || Boolean(manualHardwareBlocker) || Boolean(catalogError) || running || !preview} aria-describedby="marking-text-blocker"
                onClick={() => void printManual(false)}>{busy ? "Обработка…" : printText(preview?.total ?? count)}</button>
              <button type="button" className="secondary" disabled={busy || !request || Boolean(manualHardwareBlocker) || Boolean(catalogError) || running} aria-describedby="marking-text-blocker"
                onClick={() => void printManual(true)}>Пробная</button>
              <span id="marking-text-blocker" role="status" className={manualBlocker && lines.length ? "tone-warn" : "machine-muted"}>
                {busy ? "Ставим этикетки в очередь…" : manualBlocker || "Готово к печати"}</span>
            </div>
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
      title={`${printing.title} · ${row.inventory}`} close={() => { setPrinting(null); setReload(reload + 1); setJobTick((tick) => tick + 1); }} />}
    {setupOpen && <PageWindow label="Наладка принтера" close={() => setSetupOpen(false)} width={1180}>
      <section className="panel machine-card marking-card-setup">
        <header className="machine-card-head"><h2>Наладка · {title}</h2>
          <button type="button" className="secondary small machine-manual" onClick={() => setProfilesOpen(true)}>Профили и принтер</button>
          <button type="button" className="secondary small" onClick={() => setSetupOpen(false)}>Закрыть</button></header>
        {row.equipment_id && <MachineSetupPart inventory={row.inventory} canWrite={canWrite} saved={() => undefined} />}
      </section>
    </PageWindow>}
    {outside && <MarkingOutsideWindow group={outside} close={() => setOutside(null)} onChange={() => { if (job) void refreshJob(job); }} />}
    {profilesOpen && <MarkingProfilesWindow equipmentIds={[equipmentId]} canEdit={canConfigure}
      close={() => { setProfilesOpen(false); setReload(reload + 1); }} />}
  </PageWindow>;
}

/** Первые этикетки предпросмотра картинками; остальные — числом. */
function LabelStrip({ preview, proportional = false }: { preview: MarkingPreview; proportional?: boolean }) {
  return <ol className={`marking-card-strip${proportional ? " is-proportional" : ""}`}>
    {preview.labels.slice(0, PREVIEW_SHOWN).map((label, index) => <li key={`${label.key}:${index}`}>
      <img src={`data:image/png;base64,${label.png_b64}`} alt={`Этикетка: ${label.text}`}
        width={Math.round(preview.width_mm * preview.dpi / 25.4)} height={Math.round(preview.height_mm * preview.dpi / 25.4)} />
      <span className="tnum">{label.text}{label.quantity > 1 ? ` × ${formatNumber(label.quantity)}` : ""}</span></li>)}
    {preview.labels.length > PREVIEW_SHOWN && <li className="machine-muted">ещё {formatNumber(preview.labels.length - PREVIEW_SHOWN)} надп.</li>}
  </ol>;
}
