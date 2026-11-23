import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ApiError,
  productionApi,
  type Principal,
  type ProductionBox,
  type ProductionDefect,
  type ProductionInstrument,
  type ProductionOperator,
  type ProductionQcRejection,
  type ProductionTask,
  type QcCheck,
  type QcPlan,
  type VswrTaskListItem,
  type VswrTaskOverview,
} from "./api";
import { InstrumentRegistry } from "./InstrumentRegistry";
import { formatDay, formatMoment, formatNumber } from "./format";
import { canAct, canSee } from "./navigation";
import { isFinal } from "./productionGraph";
import { BoxName, boxCodeKey } from "./BoxLabel";
import { VswrSection, progressText, type VswrRejectRequest } from "./VswrSection";
import { PageWindow } from "./PageWindow";
import { DataTable, type DataColumn } from "./DataTable";
import { printCoaxPassport } from "./coaxPassportPrint";
import { useCanPrint } from "./rights";

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<boolean>;
// «ВАЦ — рабочее место» переехало в карточку прибора «Оборудования» (владелец 20.11.2026); `?tab=vna` ведёт туда (MOVED_TABS).
type QualityTab = "queue" | "vswr" | "rejects" | "instruments";

function urlTab(): string {
  return new URLSearchParams(window.location.search).get("tab") ?? "";
}

/* «ОТК» (owner, 04.10.2026): очередь на проверку, брак на решение и реестр средств измерения вынесены из «Ресурсного
   планирования». Права — только по разделам: приёмку ОТК и реестр средств измерения ведёт правка «ОТК» (`quality`),
   решение по браку принимает правка «Ресурсного планирования» (`production`); уровни с полным доступом проходят оба. */
export function QualityPage({ principal }: { principal: Principal }) {
  const canEdit = canAct(principal, "production");
  const canQuality = canAct(principal, "quality");
  const [tasks, setTasks] = useState<ProductionTask[]>([]);
  const [boxes, setBoxes] = useState<ProductionBox[]>([]);
  const [operators, setOperators] = useState<ProductionOperator[]>([]);
  const [defects, setDefects] = useState<ProductionDefect[]>([]);
  const [instruments, setInstruments] = useState<ProductionInstrument[]>([]);
  const [qcRejections, setQcRejections] = useState<ProductionQcRejection[]>([]);
  const [vswrTasks, setVswrTasks] = useState<VswrTaskListItem[]>([]);
  const [vswrTaskId, setVswrTaskId] = useState("");
  const [pickedTab, setPickedTab] = useState(urlTab);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  // «Не годно» по КСВН → очередь ОТК: описание брака подставляется в черновик первой коробки этого задания.
  const [prefill, setPrefill] = useState<(VswrRejectRequest & { boxId: string; stamp: number }) | null>(null);

  async function reload() {
    setError("");
    try {
      const [taskResult, boxResult, instrumentResult, operatorResult, defectResult, qcRejectionResult, vswrResult] = await Promise.all([
        canQuality ? productionApi.list() : Promise.resolve({ tasks: [] as ProductionTask[] }),
        canQuality ? productionApi.boxes() : Promise.resolve({ boxes: [] as ProductionBox[] }),
        canQuality ? productionApi.instruments() : Promise.resolve({ instruments: [] as ProductionInstrument[] }),
        canEdit ? productionApi.operators() : Promise.resolve({ operators: [] as ProductionOperator[] }),
        canEdit ? productionApi.defects() : Promise.resolve({ defects: [] as ProductionDefect[] }),
        canEdit ? productionApi.qcRejections() : Promise.resolve({ rejections: [] as ProductionQcRejection[] }),
        // Замеры КСВН — отдельно: их сбой не должен опустошать очередь ОТК и реестр СИ.
        canQuality ? productionApi.vswrTasks().catch(() => ({ tasks: [] as VswrTaskListItem[], failed: true })) : Promise.resolve({ tasks: [] as VswrTaskListItem[] }),
      ]);
      setTasks(taskResult.tasks);
      setBoxes(boxResult.boxes);
      setInstruments(instrumentResult.instruments);
      setOperators(operatorResult.operators);
      setDefects(defectResult.defects);
      setQcRejections(qcRejectionResult.rejections);
      setVswrTasks(vswrResult.tasks);
      if ("failed" in vswrResult) setError("Не удалось загрузить задания на коаксиальные сборки — замеры КСВН недоступны.");
      setVswrTaskId((current) => vswrResult.tasks.some((task) => task.id === current) ? current
        : (vswrResult.tasks.find((task) => boxResult.boxes.some((box) => box.task_id === task.id && inQcQueue(box, taskResult.tasks)))
          ?? vswrResult.tasks[0])?.id ?? "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить данные ОТК");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void reload(); }, []);

  const ready = boxes.filter((box) => inQcQueue(box, tasks));
  const rejectCount = qcRejections.filter((item) => item.status === "Ожидает решения").length
    + defects.filter((item) => item.status === "Ожидает решения" && !item.replaced_by_output_id).length;
  const tabs: { id: QualityTab; label: string }[] = [
    ...(canQuality ? [{ id: "queue" as const, label: `Очередь ОТК · ${ready.length}` }, { id: "vswr" as const, label: "Замеры КСВН" }] : []),
    ...(canEdit ? [{ id: "rejects" as const, label: `Брак · ${rejectCount}` }] : []),
    ...(canQuality ? [{ id: "instruments" as const, label: "Средства измерения" }] : []),
  ];
  const tab = tabs.find((item) => item.id === pickedTab)?.id ?? tabs[0]?.id;
  useEffect(() => {
    if (!tab) return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState(window.history.state, "", url);
  }, [tab, pickedTab]);
  useEffect(() => {
    const follow = () => setPickedTab(urlTab());
    window.addEventListener("popstate", follow);
    return () => window.removeEventListener("popstate", follow);
  }, []);

  const queueBox = (taskId: string) => ready.find((box) => box.task_id === taskId);
  const rejectFromVswr = (request: VswrRejectRequest) => {
    const box = queueBox(request.taskId);
    if (box) {
      setPickedTab("queue");
      setPrefill((current) => ({ ...request, boxId: box.id, stamp: (current?.stamp ?? 0) + 1 }));
    }
  };

  async function mutate(key: string, operation: () => Promise<unknown>) {
    if (busy) return false;
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      if (result && typeof result === "object" && "warnings" in result && Array.isArray(result.warnings) && result.warnings.length) {
        setNotice(result.warnings.join(" "));
      }
      await reload();
      return true;
    } catch (reason) {
      // Коробку или решение успели изменить (другой контролёр, мастер) — показываем свежее, чтобы следующий клик прошёл.
      if (reason instanceof ApiError && reason.status === 409) await reload();
      setError(reason instanceof Error ? reason.message : "Действие ОТК не выполнено");
      return false;
    } finally {
      setBusy("");
    }
  }

  return <section className="page quality-page">
    <header className="page-head"><h1>ОТК</h1></header>
    {tabs.length > 0 && <div className="seg-tabs quality-tabs" role="tablist" aria-label="Разделы ОТК" onKeyDown={(event) => {
      const index = tabs.findIndex((item) => item.id === tab);
      const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
      if (next < 0) return;
      event.preventDefault(); setPickedTab(tabs[next].id); document.getElementById(`quality-tab-${tabs[next].id}`)?.focus();
    }}>
      {tabs.map((item) => <button key={item.id} type="button" role="tab" id={`quality-tab-${item.id}`} aria-controls={`quality-panel-${item.id}`}
        aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} className={tab === item.id ? "on" : ""} onClick={() => setPickedTab(item.id)}>{item.label}</button>)}
    </div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {!loaded && <div className="empty">Загрузка…</div>}
    {loaded && canQuality && <div role="tabpanel" id="quality-panel-queue" aria-labelledby="quality-tab-queue" hidden={tab !== "queue"}>
      <QualityPanel boxes={boxes} tasks={tasks} instruments={instruments} vswrTasks={vswrTasks} busy={Boolean(busy)} error={error} mutate={mutate} prefill={prefill}
        openVswr={(id) => { setVswrTaskId(id); setPickedTab("vswr"); }} />
    </div>}
    {loaded && canQuality && <div role="tabpanel" id="quality-panel-vswr" aria-labelledby="quality-tab-vswr" hidden={tab !== "vswr"}>
      <VswrSection instruments={instruments} choices={vswrTasks} taskId={vswrTaskId} onTaskChange={setVswrTaskId}
        onOverview={(overview) => setVswrTasks((current) => current.map((item) => item.id === overview.task.id ? { ...item, control: overview.control,
          required: { ...item.required, ...overview.required, ...overviewTotals(overview) } } : item))}
        canUpload={canQuality} canReject={(taskId) => Boolean(queueBox(taskId))} onReject={rejectFromVswr} />
    </div>}
    {loaded && canEdit && <div role="tabpanel" id="quality-panel-rejects" aria-labelledby="quality-tab-rejects" hidden={tab !== "rejects"}><div className="review-row">
      <QcRejectionReview rejections={qcRejections} busy={Boolean(busy)} mutate={mutate} />
      <DefectReview defects={defects} operators={operators} busy={Boolean(busy)} mutate={mutate} />
    </div></div>}
    {loaded && canQuality && <div role="tabpanel" id="quality-panel-instruments" aria-labelledby="quality-tab-instruments" hidden={tab !== "instruments"}>
      <InstrumentRegistry instruments={instruments} onChanged={reload} canSeeReferences={canSee(principal, "erpref")} />
    </div>}
    {loaded && !canQuality && !canEdit && <div className="empty">ОТК открыт вам только на просмотр.</div>}
  </section>;
}

function DefectReview({ defects, operators, busy, mutate }: {
  defects: ProductionDefect[];
  operators: ProductionOperator[];
  busy: boolean;
  mutate: Mutate;
}) {
  const [dispositions, setDispositions] = useState<Record<string, "Доработка" | "Списание" | "Использовать как есть">>({});
  const [acts, setActs] = useState<Record<string, string>>({});
  // A defect record replaced by a box correction stays on record but is decided through its replacement.
  const pending = defects.filter((defect) => defect.status === "Ожидает решения" && !defect.replaced_by_output_id);
  if (!pending.length) return <div className="panel box-review defect-review"><div className="manual-stock-head"><h2>Брак цеха</h2><span className="page-count">0</span></div><div className="empty">Брака цеха на решение нет — он появится, когда оператор сдаст брак.</div></div>;
  return <div className="panel box-review defect-review"><div className="manual-stock-head"><h2>Брак цеха</h2><span className="page-count">{pending.length}</span></div>
    <div className="box-grid">{pending.map((defect) => {
      const disposition = dispositions[defect.id] ?? "Доработка";
      return <div className="production-box defect-box" key={`${defect.id}:${defect.version}`}>
        <strong>{defect.number}</strong><span>{defect.quantity} шт · {defect.character}</span><span>{defect.description}</span><small>{operators.find((operator) => operator.id === defect.operator_id)?.name ?? defect.operator_id}</small>
        <label>Решение<select value={disposition} onChange={(event) => setDispositions((current) => ({ ...current, [defect.id]: event.target.value as typeof disposition }))}><option>Доработка</option><option>Списание</option><option>Использовать как есть</option></select></label>
        <label>Акт / основание<input aria-label={`Акт для ${defect.number}`} value={acts[defect.id] ?? ""} onChange={(event) => setActs((current) => ({ ...current, [defect.id]: event.target.value }))} /></label>
        <button className="primary small" disabled={busy || !(acts[defect.id] ?? "").trim()} onClick={() => void mutate(defect.id, () => productionApi.decideDefect(defect.id, defect.version, disposition, acts[defect.id]))}>Принять решение</button>
      </div>;
    })}</div>
  </div>;
}

function QcRejectionReview({ rejections, busy, mutate }: {
  rejections: ProductionQcRejection[];
  busy: boolean;
  mutate: Mutate;
}) {
  const [dispositions, setDispositions] = useState<Record<string, "Доработка" | "Списание" | "Использовать как есть">>({});
  const [acts, setActs] = useState<Record<string, string>>({});
  const pending = rejections.filter((item) => item.status === "Ожидает решения");
  if (!pending.length) return <div className="panel box-review defect-review"><div className="manual-stock-head"><h2>Брак ОТК</h2><span className="page-count">0</span></div><div className="empty">Брака ОТК на решение нет — он появится, когда ОТК забракует коробку.</div></div>;
  return <div className="panel box-review defect-review"><div className="manual-stock-head"><h2>Брак ОТК</h2><span className="page-count">{pending.length}</span></div>
    <div className="box-grid">{pending.map((item) => {
      const disposition = dispositions[item.id] ?? "Доработка";
      return <div className="production-box defect-box" key={item.id}>
        <strong>{item.defect_number}</strong><span>{item.rejected} шт · {item.defect_character}</span><span>{item.defect_description}</span><small>{item.box_code ?? item.box_number ?? item.box_id} · {item.item}</small>
        <label>Решение<select value={disposition} onChange={(event) => setDispositions((current) => ({ ...current, [item.id]: event.target.value as typeof disposition }))}><option>Доработка</option><option>Списание</option><option>Использовать как есть</option></select></label>
        <label>Акт / основание<input aria-label={`Акт ОТК для ${item.defect_number}`} value={acts[item.id] ?? ""} onChange={(event) => setActs((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
        <button className="primary small" disabled={busy || !(acts[item.id] ?? "").trim()} onClick={() => void mutate(`qc-decision:${item.id}`, () => productionApi.decideQcRejection(item.id, disposition, acts[item.id]))}>Принять решение</button>
      </div>;
    })}</div>
  </div>;
}

/** Брак и наибольший КСВН задания по последним замерам изделий — как в списке заданий на замер. */
function overviewTotals(overview: VswrTaskOverview): { failed: number; max_vswr: number | null } {
  const peaks = overview.serials.map((row) => row.latest?.max_vswr).filter((value): value is number => value != null);
  return { failed: overview.serials.filter((row) => row.latest?.verdict === "fail").length, max_vswr: peaks.length ? Math.max(...peaks) : null };
}

/** Коробка в очереди ОТК: сдана с финальной операции схемы и ещё не проверена. */
function inQcQueue(box: ProductionBox, tasks: ProductionTask[]): boolean {
  const task = tasks.find((item) => item.id === box.task_id);
  const operation = task?.operations.find((item) => item.id === box.operation_id);
  // В ОТК идёт коробка финальной операции схемы; операция не нашлась в задании — по старому: последний номер.
  const final = task && operation ? isFinal(task, operation) : task ? box.operation_position === Math.max(...task.operations.map((item) => item.position)) : false;
  // Подтверждает только ОТК (владелец, 13.10.2026): в очередь — сданная коробка; «Подтверждена» и не проверена — старые,
  // подтверждённые мастером до 13.10. КБ уже уехала целиком (склад принял и отгрузил мимо ОТК) — проверять нечего
  // (владелец 18.11).
  return (box.status === "Закрыта оператором" || box.status === "Подтверждена") && !box.qc_inspected && !box.kb_shipped && final;
}

const lastInstrumentsKey = "erp-quality-last-instruments";

function savedInstruments(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(lastInstrumentsKey) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch { return []; }
}

function QcInstrumentPicker({ instruments, selected, onChange, disabled, boxNumber }: {
  instruments: ProductionInstrument[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled: boolean;
  boxNumber: string | null;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ left: number; top?: number; bottom?: number } | null>(null);
  const [query, setQuery] = useState("");
  const picked = instruments.filter((item) => selected.includes(item.id));
  const title = (item: ProductionInstrument) => `${item.inventory_number} ${item.name} · поверка до ${formatDay(item.verification_due) || "—"}`;
  const close = () => { setPlace(null); trigger.current?.focus(); };
  useEffect(() => {
    if (!place) return;
    popup.current?.querySelector<HTMLInputElement>("input")?.focus();
    const outside = (event: Event) => {
      if (event.target instanceof Node && !popup.current?.contains(event.target) && !trigger.current?.contains(event.target)) setPlace(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); event.stopPropagation(); close();
    };
    const moved = (event: Event) => {
      if (event.target instanceof Node && popup.current?.contains(event.target)) return;
      setPlace(null);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape, true);
    window.addEventListener("resize", moved);
    window.addEventListener("scroll", moved, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", escape, true);
      window.removeEventListener("resize", moved);
      window.removeEventListener("scroll", moved, true);
    };
  }, [place]);
  const toggle = (item: ProductionInstrument) => onChange(selected.includes(item.id) ? selected.filter((key) => key !== item.id) : [...selected, item.id]);
  const shown = instruments.filter((item) => `${item.inventory_number} ${item.name}`.toLocaleLowerCase("ru").includes(query.trim().toLocaleLowerCase("ru")));
  return <div className="qc-instruments" role="group" aria-label={`Средства измерения ${boxNumber}`}>
    {picked.length > 0 ? <div className={`qc-selected${picked.length > 2 ? " qc-selected-many" : ""}`}>
      {picked.map((item) => <button key={item.id} type="button" className="secondary small qc-instrument" disabled={disabled}
        title={title(item)} aria-label={`Убрать ${item.inventory_number} ${item.name}`} onClick={() => { toggle(item); trigger.current?.focus(); }}>
        <span>{item.inventory_number} {item.name.split(/[·,;\n]| [—–] /)[0].trim()}</span><span aria-hidden="true">×</span>
      </button>)}
    </div> : <span className="muted qc-unselected">не выбраны</span>}
    <button ref={trigger} type="button" className="secondary small qc-instrument-add" disabled={disabled} aria-haspopup="dialog"
      aria-expanded={Boolean(place)} aria-controls={place ? id : undefined} onClick={() => {
        if (place) { close(); return; }
        const rect = trigger.current!.getBoundingClientRect();
        setQuery("");
        setPlace({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 328)),
          ...(window.innerHeight - rect.bottom >= Math.min(360, window.innerHeight / 2)
            ? { top: rect.bottom + 4 } : { bottom: window.innerHeight - rect.top + 4 }) });
      }}>+ СИ</button>
    {place && createPortal(<div ref={popup} id={id} role="dialog" aria-label={`Выбрать СИ · ${boxNumber}`} className="qc-instrument-popup" style={place}>
      <div className="qc-instrument-popup-head"><strong>Средства измерения</strong><button type="button" className="secondary small" aria-label="Закрыть выбор СИ" onClick={close}>×</button></div>
      {instruments.length > 8 && <input type="search" name="instrument-search" autoComplete="off" aria-label="Найти СИ" placeholder="Инв. № или наименование…" value={query} onChange={(event) => setQuery(event.target.value)} />}
      <div className="qc-instrument-options">
        {shown.map((item) => <label key={item.id} title={title(item)}>
          <input type="checkbox" checked={selected.includes(item.id)} disabled={disabled} onChange={() => toggle(item)} />
          <span>{item.inventory_number} {item.name}</span>
        </label>)}
        {!shown.length && <span className="muted">{instruments.length ? "СИ не найдены" : "Нет действующих СИ"}</span>}
      </div>
    </div>, document.body)}
  </div>;
}

const CHECK_FALLBACK: Record<QcCheck["kind"], string> = {
  visual: "Типовые дефекты разделов СТО",
  dimensions: "Размеры по КД и ЭТК",
  vswr: "КСВН по норме ЭТК",
  continuity: "Цепи и подключение экрана",
};
const OTHER_REASON = "other";
const qty = (text: string) => (text.trim() === "" ? NaN : Number(text));

/** Строка «КСВН» окна ОТК: «померено 12 из 20 · брак 1 · макс. КСВН 1,18 при норме ≤ 1,35». */
export function qcVswrText(vswr: NonNullable<QcPlan["vswr"]>): string {
  if (vswr.mode === "none") return "контроль КСВН по ЭТК не проводится";
  const peak = vswr.max_vswr != null ? ` · макс. КСВН ${formatNumber(vswr.max_vswr, { digits: 2 })}` : "";
  const norm = vswr.norm_vswr_max != null ? ` при норме ≤ ${formatNumber(vswr.norm_vswr_max, { digits: 2 })}` : vswr.norm_status === "ok" ? "" : " · норма не определена";
  return `померено ${vswr.measured} из ${vswr.total}${vswr.failed ? ` · брак ${vswr.failed}` : ""}${peak}${peak ? norm : ""}`;
}

/** Ошибки решения ОТК; пусто — можно «Принять». Годные не подставляются (владелец 19.11): контролёр вводит оба числа. */
export function qcProblems(available: number, draft: { accepted: string; rejected: string; character: string; description: string }): string[] {
  const accepted = qty(draft.accepted), rejected = qty(draft.rejected);
  if (!Number.isInteger(accepted) || !Number.isInteger(rejected) || accepted < 0 || rejected < 0) return ["Введите годные и брак — целые числа"];
  const problems: string[] = [];
  if (accepted + rejected !== available) problems.push(`Годные и брак вместе — ${available} шт, сейчас ${accepted + rejected}`);
  if (rejected > 0 && !draft.character.trim()) problems.push("Выберите причину брака");
  if (rejected > 0 && !draft.description.trim()) problems.push("Опишите брак");
  return problems;
}

type QcDraft = { accepted: string; rejected: string; reason: string; character: string; description: string; instrumentIds: string[] | null };

function QualityPanel({ boxes, tasks, instruments, vswrTasks, openVswr, busy, error, mutate, prefill }: {
  boxes: ProductionBox[];
  tasks: ProductionTask[];
  instruments: ProductionInstrument[];
  vswrTasks: VswrTaskListItem[];
  openVswr: (taskId: string) => void;
  busy: boolean;
  error: string;
  mutate: Mutate;
  prefill: { boxId: string; description: string; stamp: number } | null;
}) {
  const [openId, setOpenId] = useState("");
  const [drafts, setDrafts] = useState<Record<string, QcDraft>>({});
  const ready = boxes.filter((box) => inQcQueue(box, tasks));
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!prefill) return;
    const box = ready.find((item) => item.id === prefill.boxId);
    if (!box) return;
    // «В брак ОТК» из замеров: брак 1 шт с причиной «КСВН вне нормы»; годные контролёр вводит сам.
    setDrafts((current) => ({ ...current, [box.id]: { ...(current[box.id] ?? emptyDraft()), accepted: "", rejected: "1", reason: "vswr",
      character: "", description: prefill.description } }));
    setOpenId(box.id);
    panel.current?.scrollIntoView?.({ block: "start" });
  }, [prefill?.stamp]);

  const taskOf = (box: ProductionBox) => tasks.find((item) => item.id === box.task_id);
  const columns: DataColumn<ProductionBox>[] = [
    { key: "box", title: "Коробка", value: (box) => box.code ?? box.number ?? "", width: 220, render: (box) => <BoxName box={box} /> },
    { key: "project", title: "Проект", value: (box) => taskOf(box)?.project ?? "", filter: true },
    { key: "kb", title: "КБ", value: (box) => box.item },
    { key: "name", title: "Изделие", value: (box) => taskOf(box)?.name ?? "", grow: true, wrap: true },
    { key: "qty", title: "Кол-во", value: (box) => Number(box.available), numeric: true },
    { key: "vswr", title: "КСВН", value: (box) => { const vswr = vswrTasks.find((item) => item.id === box.task_id); return vswr ? progressText(vswr.control, vswr.required) : ""; },
      render: (box) => {
        const vswr = vswrTasks.find((item) => item.id === box.task_id);
        if (!vswr) return <span className="muted">не требуется</span>;
        const tone = vswr.control.mode === "none" ? "" : vswr.required.failed ? "tone-danger" : vswr.required.measured >= vswr.required.total ? "tone-ok" : "tone-warn";
        return <span className={tone}>{progressText(vswr.control, vswr.required)}</span>;
      } },
    { key: "handed", title: "Сдана", value: (box) => box.closed_at ?? box.created_at, render: (box) => formatMoment(box.closed_at ?? box.created_at) },
    { key: "status", title: "Статус", value: (box) => (box.status === "Подтверждена" ? "подтверждена мастером" : "сдана монтажником"), filter: true },
  ];
  const open = ready.find((box) => box.id === openId);
  return <div ref={panel} className="panel box-review qc-review">
    <div className="manual-stock-head"><h2>Очередь ОТК</h2><span className="page-count">{ready.length}</span></div>
    <DataTable label="Коробки на проверку ОТК" storageKey="quality-queue" rows={ready} columns={columns} rowKey={(box) => box.id}
      searchPlaceholder="Код с коробки, номер, проект или КБ…" searchText={(box) => [box.code, box.number, boxCodeKey(box.code ?? "")]}
      onRowClick={(box) => setOpenId(box.id)} rowTitle={() => "Открыть окно ОТК"}
      emptyText="Коробок на проверку нет — они появятся, когда монтажник сдаст коробку с последней операции." />
    {open && <QcWindow box={open} task={taskOf(open)} instruments={instruments} busy={busy} error={error} mutate={mutate}
      draft={drafts[open.id] ?? emptyDraft()} setDraft={(patch) => setDrafts((current) => ({ ...current, [open.id]: { ...(current[open.id] ?? emptyDraft()), ...patch } }))}
      openVswr={() => { setOpenId(""); openVswr(open.task_id); }}
      close={() => { if (!busy) setOpenId(""); }}
      done={() => { setOpenId(""); setDrafts((current) => { const rest = { ...current }; delete rest[open.id]; return rest; }); }} />}
  </div>;
}

const emptyDraft = (): QcDraft => ({ accepted: "", rejected: "", reason: "", character: "", description: "", instrumentIds: null });

function QcWindow({ box, task, instruments, busy, error, mutate, draft, setDraft, openVswr, close, done }: {
  box: ProductionBox;
  task: ProductionTask | undefined;
  instruments: ProductionInstrument[];
  busy: boolean;
  error: string;
  mutate: Mutate;
  draft: QcDraft;
  setDraft: (patch: Partial<QcDraft>) => void;
  openVswr: () => void;
  close: () => void;
  done: () => void;
}) {
  const canPrint = useCanPrint();
  const [plan, setPlan] = useState<QcPlan | null>(null);
  const [planError, setPlanError] = useState("");
  const [returning, setReturning] = useState(false);
  const [returnReason, setReturnReason] = useState("");
  const [printError, setPrintError] = useState("");
  const active = instruments.filter((item) => item.kind === "Средство измерений" && item.status === "Действует");
  const validIds = (ids: string[]) => [...new Set(ids)].filter((id) => active.some((item) => item.id === id));
  const available = Number(box.available);

  useEffect(() => {
    let alive = true;
    setPlan(null); setPlanError("");
    productionApi.qcPlan(box.task_id).then((loaded) => {
      if (!alive) return;
      setPlan(loaded);
      // СИ — из плана (ЭТК, СТО, ВАЦ замеров); план ничего не нашёл — прошлый выбор контролёра.
      if (draft.instrumentIds === null) {
        const planned = validIds(loaded.instruments.map((item) => item.id));
        setDraft({ instrumentIds: planned.length ? planned : validIds(savedInstruments()) });
      }
      if (draft.reason === "vswr") {
        const row = loaded.defects.find((item) => item.code === "4.8.16");
        setDraft(row ? { reason: row.code, character: `${row.code} ${row.defect}` } : { reason: OTHER_REASON, character: "КСВН выше нормы" });
      }
    }).catch((reason) => {
      if (!alive) return;
      setPlanError(reason instanceof Error ? reason.message : "Проверки по ЭТК не загружены");
      if (draft.instrumentIds === null) setDraft({ instrumentIds: validIds(savedInstruments()) });
      if (draft.reason === "vswr") setDraft({ reason: OTHER_REASON, character: "КСВН выше нормы" });
    });
    return () => { alive = false; };
  }, [box.task_id]);

  const selected = draft.instrumentIds ?? [];
  const problems = qcProblems(available, draft);
  const rejected = qty(draft.rejected);
  const accepted = qty(draft.accepted);

  function pickReason(code: string) {
    const row = plan?.defects.find((item) => item.code === code);
    const previous = plan?.defects.find((item) => item.code === draft.reason);
    const keep = draft.description.trim() && draft.description !== previous?.criterion;
    setDraft({ reason: code, character: row ? `${row.code} ${row.defect}` : "", ...(row && !keep ? { description: row.criterion } : {}) });
  }

  async function accept() {
    if (busy || problems.length) return;
    const ids = validIds(selected);
    const success = await mutate(`qc:${box.id}`, () => productionApi.inspectBox(box.id, {
      version: box.version, accepted, rejected,
      defect_character: rejected > 0 ? draft.character.trim() : "", defect_description: rejected > 0 ? draft.description.trim() : "", instrument_ids: ids,
    }));
    if (success) {
      try { localStorage.setItem(lastInstrumentsKey, JSON.stringify(ids)); } catch { /* Selection remains usable without storage. */ }
      done();
    }
  }
  async function sendBack() {
    const reason = returnReason.trim();
    if (!reason) return;
    if (await mutate(`qc-return:${box.id}`, () => productionApi.returnBox(box.id, box.version, reason))) done();
  }
  async function passport(position: number) {
    setPrintError("");
    try { printCoaxPassport(await productionApi.vswrPassport(box.task_id, position)); }
    catch (reason) { setPrintError(reason instanceof Error ? reason.message : "Паспорт не получен"); }
  }

  const head = [task?.project, box.item, task?.name].filter(Boolean).join(" · ");
  const rows = (plan?.checks ?? []).flatMap((check) => (check.items.length ? check.items : [{ operation: "", text: CHECK_FALLBACK[check.kind], tolerance: "" }])
    .map((item, index) => ({ check, item, first: index === 0, span: Math.max(check.items.length, 1) })));
  const sections = [...new Set((plan?.defects ?? []).map((row) => row.section))];
  const passed = plan?.vswr?.units.filter((unit) => unit.verdict === "pass") ?? [];

  return <PageWindow label={`ОТК · ${box.code ?? box.number}`} width={1120} close={close}>
    <form className="panel qc-window" onSubmit={(event) => { event.preventDefault(); void (returning ? sendBack() : accept()); }}>
      <div className="manual-stock-head"><h2>ОТК · <BoxName box={box} /></h2>
        <span className="qc-window-head">{head}</span>
        <span className="page-count">{available} шт</span>
        {plan?.etk.revision != null && <span className="muted">ЭТК {plan.etk.item_index}, ред. {plan.etk.revision}</span>}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {returning ? <>
        <label>Причина возврата монтажнику<textarea name="reason" aria-label={`Причина возврата ${box.number}`} disabled={busy} value={returnReason} onChange={(event) => setReturnReason(event.target.value)} /></label>
        <div className="form-actions">
          <button type="button" className="secondary small" disabled={busy} onClick={() => setReturning(false)}>Назад к проверке</button>
          <button type="submit" className="primary small" disabled={busy || !returnReason.trim()}>{busy ? "Сохраняю…" : "Вернуть монтажнику"}</button>
        </div>
      </> : <>
        <h3>Что проверить</h3>
        {planError && <p className="warning-inline" role="status">{planError} — проверьте по ЭТК задания.</p>}
        {!plan && !planError && <div className="empty">Загрузка проверок по ЭТК…</div>}
        {plan && !rows.length && <div className="empty">В ЭТК задания нет операций с контролем — проверьте по КД.</div>}
        {rows.length > 0 && <div className="table-wrap"><table className="grid qc-checks" aria-label="Проверки ОТК по ЭТК">
          <thead><tr><th scope="col">Проверка</th><th scope="col">Операция ЭТК</th><th scope="col">Что проверить</th><th scope="col">Допуск</th><th scope="col">Методика</th><th scope="col">Чем</th></tr></thead>
          <tbody>{rows.map(({ check, item, first, span }, index) => <tr key={`${check.kind}:${index}`}>
            {first && <th scope="row" rowSpan={span}>{check.title}{check.required === false && <small className="muted"> · не требуется</small>}</th>}
            <td>{item.operation || <span className="muted">—</span>}</td>
            <td>{item.text}</td>
            <td className="num">{item.tolerance || <span className="muted">—</span>}</td>
            {first && <td rowSpan={span}>{check.method}</td>}
            {first && <td rowSpan={span}>{check.tools.join(", ") || <span className="muted">—</span>}</td>}
          </tr>)}</tbody>
        </table></div>}
        {plan?.vswr && <div className="qc-vswr-status">
          <span><b>Замеры КСВН:</b> <span className={plan.vswr.failed ? "tone-danger" : plan.vswr.measured >= plan.vswr.total ? "tone-ok" : "tone-warn"}>{qcVswrText(plan.vswr)}</span></span>
          <button type="button" className="link-button" onClick={openVswr}>Протокол замеров</button>
          {canPrint && passed.length > 0 && <span className="qc-passports" aria-label="Паспорта годных изделий">Паспорта:
            {passed.slice(0, 12).map((unit) => <button key={unit.position} type="button" className="link-button" title={`Паспорт ${unit.serial_number}`}
              onClick={() => void passport(unit.position)}>№ {unit.position}</button>)}
            {passed.length > 12 && <button type="button" className="link-button" onClick={openVswr}>ещё {passed.length - 12}</button>}
          </span>}
          {printError && <small className="danger-text" role="alert">{printError}</small>}
        </div>}
        <h3>Средства измерения</h3>
        <QcInstrumentPicker instruments={active} selected={selected} disabled={busy} boxNumber={box.number} onChange={(instrumentIds) => setDraft({ instrumentIds })} />
        {plan?.missing_instruments.length ? <p className="warning-inline">Нет действующего СИ в реестре: {plan.missing_instruments.map((item) => `${item.tool.toLocaleLowerCase("ru")} (${item.purpose.toLocaleLowerCase("ru")})`).join(", ")}.</p> : null}
        <h3>Решение ОТК</h3>
        <div className="qc-decision">
          <label>Годные, шт<input name="accepted" type="number" min="0" max={available} step="1" inputMode="numeric" aria-label={`Годные ОТК ${box.number}`}
            disabled={busy} value={draft.accepted} onChange={(event) => setDraft({ accepted: event.target.value })} /></label>
          <label>Брак, шт<input name="rejected" type="number" min="0" max={available} step="1" inputMode="numeric" aria-label={`Брак ОТК ${box.number}`}
            disabled={busy} value={draft.rejected} onChange={(event) => setDraft({ rejected: event.target.value })} /></label>
          <span className="qc-left" role="status">В коробке {available} шт{Number.isInteger(accepted + rejected) && accepted + rejected !== available ? ` · не распределено ${available - accepted - rejected}` : ""}</span>
          {rejected > 0 && <>
            <label className="qc-reason">Причина брака ({plan?.sto.designation ?? "СТО ФРАК-001-2026"})
              <select name="reason" disabled={busy} value={draft.reason} onChange={(event) => pickReason(event.target.value)}>
                <option value="">— выберите типовой дефект —</option>
                {sections.map((section) => <optgroup key={section} label={`Раздел ${section}`}>
                  {plan!.defects.filter((row) => row.section === section).map((row) => <option key={row.code} value={row.code}>{row.code} {row.defect} · {row.class.toLocaleLowerCase("ru")}</option>)}
                </optgroup>)}
                <option value={OTHER_REASON}>Другая причина…</option>
              </select>
            </label>
            {draft.reason === OTHER_REASON && <label className="qc-reason">Характер брака<input name="character" autoComplete="off" disabled={busy} value={draft.character} onChange={(event) => setDraft({ character: event.target.value })} /></label>}
            <label className="qc-description">Описание брака<textarea name="description" disabled={busy} value={draft.description} onChange={(event) => setDraft({ description: event.target.value })} /></label>
          </>}
        </div>
        {problems.length > 0 && (draft.accepted !== "" || draft.rejected !== "") && <small className="danger-text" role="status">{problems.join(". ")}.</small>}
        <div className="form-actions">
          {box.status === "Закрыта оператором" && <button type="button" className="link-button qc-return" disabled={busy} onClick={() => setReturning(true)}>Вернуть монтажнику…</button>}
          <button type="button" className="secondary small" disabled={busy} onClick={close}>Отмена</button>
          <button type="submit" className="primary small" disabled={busy || problems.length > 0}>
            {busy ? "Сохраняю…" : problems.length ? "Принять ОТК" : `Принять ОТК: годных ${accepted}${rejected ? `, брак ${rejected}` : ""}`}
          </button>
        </div>
      </>}
    </form>
  </PageWindow>;
}
