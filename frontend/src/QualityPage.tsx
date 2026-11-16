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
  type VswrTaskListItem,
} from "./api";
import { InstrumentRegistry } from "./InstrumentRegistry";
import { formatDay } from "./format";
import { canAct } from "./navigation";
import { isFinal } from "./productionGraph";
import { BoxName, boxMatches } from "./BoxLabel";
import { VswrSection, type VswrRejectRequest } from "./VswrSection";
import { PageWindow } from "./PageWindow";

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<boolean>;
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
        canQuality ? productionApi.vswrTasks() : Promise.resolve({ tasks: [] as VswrTaskListItem[] }),
      ]);
      setTasks(taskResult.tasks);
      setBoxes(boxResult.boxes);
      setInstruments(instrumentResult.instruments);
      setOperators(operatorResult.operators);
      setDefects(defectResult.defects);
      setQcRejections(qcRejectionResult.rejections);
      setVswrTasks(vswrResult.tasks);
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
        onOverview={(overview) => setVswrTasks((current) => current.map((item) => item.id === overview.task.id ? { ...item, required: overview.required, control: overview.control } : item))}
        canUpload={canQuality} canReject={(taskId) => Boolean(queueBox(taskId))} onReject={rejectFromVswr} />
    </div>}
    {loaded && canEdit && <div role="tabpanel" id="quality-panel-rejects" aria-labelledby="quality-tab-rejects" hidden={tab !== "rejects"}><div className="review-row">
      <QcRejectionReview rejections={qcRejections} busy={Boolean(busy)} mutate={mutate} />
      <DefectReview defects={defects} operators={operators} busy={Boolean(busy)} mutate={mutate} />
    </div></div>}
    {loaded && canQuality && <div role="tabpanel" id="quality-panel-instruments" aria-labelledby="quality-tab-instruments" hidden={tab !== "instruments"}>
      <InstrumentRegistry instruments={instruments} onChanged={reload} />
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

/** Коробка в очереди ОТК: сдана с финальной операции схемы и ещё не проверена. */
function inQcQueue(box: ProductionBox, tasks: ProductionTask[]): boolean {
  const task = tasks.find((item) => item.id === box.task_id);
  const operation = task?.operations.find((item) => item.id === box.operation_id);
  // В ОТК идёт коробка финальной операции схемы; операция не нашлась в задании — по старому: последний номер.
  const final = task && operation ? isFinal(task, operation) : task ? box.operation_position === Math.max(...task.operations.map((item) => item.position)) : false;
  // Подтверждает только ОТК (владелец, 13.10.2026): в очередь — сданная коробка; «Подтверждена» и не проверена — старые,
  // подтверждённые мастером до 13.10.
  return (box.status === "Закрыта оператором" || box.status === "Подтверждена") && !box.qc_inspected && final;
}

type QcDraft = { accepted: string; rejected: string; character: string; description: string; instrumentIds: string[] };
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
  const [drafts, setDrafts] = useState<Record<string, QcDraft>>({});
  const [lastInstruments, setLastInstruments] = useState(savedInstruments);
  const [query, setQuery] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [dialog, setDialog] = useState<{ boxId: string; kind: "reject" | "return" } | null>(null);
  const ready = boxes.filter((box) => inQcQueue(box, tasks));
  const active = instruments.filter((item) => item.kind === "Средство измерений" && item.status === "Действует");
  const validIds = (ids: string[]) => [...new Set(ids)].filter((id) => active.some((item) => item.id === id));
  const draftFor = (box: ProductionBox): QcDraft => drafts[box.id] ?? {
    accepted: box.available, rejected: "0", character: "", description: "", instrumentIds: validIds(lastInstruments),
  };
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!prefill) return;
    const box = ready.find((item) => item.id === prefill.boxId);
    if (!box) return;
    setDrafts((current) => ({
      ...current,
      [box.id]: { ...draftFor(box), accepted: String(Math.max(Number(box.available) - 1, 0)), rejected: "1",
        character: "КСВН выше нормы", description: prefill.description },
    }));
    setQuery(""); setDialog({ boxId: box.id, kind: "reject" });
    panel.current?.scrollIntoView?.({ block: "start" });
  }, [prefill?.stamp]);

  const update = (box: ProductionBox, patch: Partial<QcDraft>) => setDrafts((current) => ({
    ...current, [box.id]: { ...draftFor(box), ...patch },
  }));
  const totalMatches = (box: ProductionBox, draft: QcDraft) => {
    const accepted = Number(draft.accepted || 0), rejected = Number(draft.rejected || 0);
    return Number.isInteger(accepted) && Number.isInteger(rejected) && accepted >= 0 && rejected >= 0
      && accepted + rejected === Number(box.available);
  };
  const close = () => { if (!busy) setDialog(null); };
  async function inspect(box: ProductionBox) {
    const draft = draftFor(box);
    if (!totalMatches(box, draft) || (Number(draft.rejected) > 0 && (!draft.character.trim() || !draft.description.trim()))) return;
    const ids = validIds(draft.instrumentIds);
    const success = await mutate(`qc:${box.id}`, () => productionApi.inspectBox(box.id, {
      version: box.version, accepted: Number(draft.accepted), rejected: Number(draft.rejected),
      defect_character: draft.character.trim(), defect_description: draft.description.trim(), instrument_ids: ids,
    }));
    if (success) {
      setDialog(null); setLastInstruments(ids);
      try { localStorage.setItem(lastInstrumentsKey, JSON.stringify(ids)); } catch { /* Selection remains usable without storage. */ }
    }
  }
  async function returnBox(box: ProductionBox) {
    const reason = (reasons[box.id] ?? "").trim();
    if (!reason) return;
    if (await mutate(`qc-return:${box.id}`, () => productionApi.returnBox(box.id, box.version, reason))) {
      setDialog(null); setReasons((current) => ({ ...current, [box.id]: "" }));
    }
  }
  const shown = ready.filter((box) => boxMatches(box, query));
  const dialogBox = dialog && ready.find((box) => box.id === dialog.boxId);
  const dialogDraft = dialogBox ? draftFor(dialogBox) : null;
  return <div ref={panel} className="panel box-review qc-review">
    <div className="manual-stock-head"><h2>Очередь ОТК</h2><span className="page-count">{ready.length}</span>
      {ready.length > 3 && <input className="search box-search" type="search" aria-label="Найти коробку по коду" placeholder="Код с коробки или номер…" autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} />}
    </div>
    {!ready.length ? <div className="empty">Коробок на проверку нет — они появятся, когда монтажник сдаст коробку с последней операции.</div>
      : !shown.length ? <div className="empty">Нет коробки с кодом «{query}» — проверьте код на коробке.</div>
      : <div className="table-wrap qc-queue-wrap"><table className="grid wide qc-queue" aria-label="Коробки на проверку ОТК">
        <colgroup><col className="qc-box-col" /><col className="qc-item-col" /><col className="qc-qty-col" /><col className="qc-number-col" /><col className="qc-number-col" /><col /><col className="qc-vswr-col" /><col className="qc-actions-col" /></colgroup>
        <thead><tr><th scope="col">Коробка</th><th scope="col">КБ / изделие</th><th scope="col" className="num">Кол-во</th><th scope="col" className="num">Годные</th><th scope="col" className="num">Брак</th><th scope="col">СИ</th><th scope="col">КСВН</th><th scope="col">Действия</th></tr></thead>
        <tbody>{shown.map((box) => {
          const draft = draftFor(box);
          const valid = totalMatches(box, draft);
          const task = tasks.find((item) => item.id === box.task_id);
          const vswr = vswrTasks.find((item) => item.id === box.task_id);
          const measured = vswr && (vswr.control.mode === "none" ? "не требуется" : `замерено ${vswr.required.measured} из ${vswr.required.total}`);
          return <tr key={`${box.id}:${box.version}`} title={valid ? undefined : `Сумма должна быть ${box.available}.`}>
            <td className="key"><BoxName box={box} /></td>
            <td><span className="qc-item" title={[box.item, task?.name].filter(Boolean).join(" · ")}>{box.item}{task?.name && <small>{task.name}</small>}</span></td>
            <td className="num">{box.available}</td>
            <td className="num"><input aria-label={`Годные ОТК ${box.number}`} name={`accepted-${box.id}`} type="number" min="0" max={box.available} step="1" disabled={busy} value={draft.accepted} onChange={(event) => update(box, { accepted: event.target.value })} /></td>
            <td className="num"><input aria-label={`Брак ОТК ${box.number}`} name={`rejected-${box.id}`} type="number" min="0" max={box.available} step="1" disabled={busy} value={draft.rejected} onChange={(event) => update(box, { rejected: event.target.value })} /></td>
            <td><QcInstrumentPicker instruments={active} selected={draft.instrumentIds} disabled={busy} boxNumber={box.number}
              onChange={(instrumentIds) => update(box, { instrumentIds })} /></td>
            <td>{vswr && <button type="button" className={`qc-vswr-link ${vswr.control.mode === "none" ? "tone-neutral" : vswr.required.measured >= vswr.required.total ? "tone-ok" : "tone-warn"}`} onClick={() => openVswr(box.task_id)}>{measured}</button>}</td>
            <td><div className="qc-actions">
              <button className="primary small" disabled={busy || !valid} onClick={() => Number(draft.rejected) > 0 ? setDialog({ boxId: box.id, kind: "reject" }) : void inspect(box)}>Принять ОТК</button>
              {box.status === "Закрыта оператором" && <button className="link-button" disabled={busy} onClick={() => setDialog({ boxId: box.id, kind: "return" })}>Вернуть…</button>}
              {!valid && <small className="danger-text" role="status">Сумма должна быть {box.available}.</small>}
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}
    {dialogBox && dialogDraft && <PageWindow label={dialog?.kind === "reject" ? `Приёмка с браком · ${dialogBox.number}` : `Возврат коробки ${dialogBox.number}`} width={560} close={close}>
      <form className="panel qc-dialog" onSubmit={(event) => { event.preventDefault(); if (!busy) void (dialog?.kind === "reject" ? inspect(dialogBox) : returnBox(dialogBox)); }}>
        <div className="manual-stock-head"><h2>{dialog?.kind === "reject" ? "Брак ОТК" : "Вернуть монтажнику"}</h2><BoxName box={dialogBox} /></div>
        {error && <div className="error-banner">{error}</div>}
        {dialog?.kind === "reject" ? <div className="form-grid">
          <label className="wide">Характер<input name="character" autoComplete="off" disabled={busy} value={dialogDraft.character} onChange={(event) => update(dialogBox, { character: event.target.value })} /></label>
          <label className="wide">Описание<textarea name="description" disabled={busy} value={dialogDraft.description} onChange={(event) => update(dialogBox, { description: event.target.value })} /></label>
        </div> : <label>Причина возврата<textarea name="reason" aria-label={`Причина возврата ${dialogBox.number}`} disabled={busy} value={reasons[dialogBox.id] ?? ""} onChange={(event) => setReasons((current) => ({ ...current, [dialogBox.id]: event.target.value }))} /></label>}
        <div className="form-actions">
          <button type="button" className="secondary small" disabled={busy} onClick={close}>Отмена</button>
          <button type="submit" className="primary small" disabled={busy || (dialog?.kind === "reject" ? !totalMatches(dialogBox, dialogDraft) || !dialogDraft.character.trim() || !dialogDraft.description.trim() : !(reasons[dialogBox.id] ?? "").trim())}>
            {busy ? "Сохраняю…" : dialog?.kind === "reject" ? "Принять ОТК" : "Вернуть монтажнику"}
          </button>
        </div>
      </form>
    </PageWindow>}
  </div>;
}
