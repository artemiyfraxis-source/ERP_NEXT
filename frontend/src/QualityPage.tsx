import { useEffect, useState } from "react";
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
} from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDay } from "./format";
import { canAct } from "./navigation";
import { isFinal } from "./productionGraph";
import { StatusChip } from "./StatusChip";
import { BoxName, boxMatches } from "./BoxLabel";

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<void>;

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
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  async function reload() {
    setError("");
    try {
      const [taskResult, boxResult, instrumentResult, operatorResult, defectResult, qcRejectionResult] = await Promise.all([
        canQuality ? productionApi.list() : Promise.resolve({ tasks: [] as ProductionTask[] }),
        canQuality ? productionApi.boxes() : Promise.resolve({ boxes: [] as ProductionBox[] }),
        canQuality ? productionApi.instruments() : Promise.resolve({ instruments: [] as ProductionInstrument[] }),
        canEdit ? productionApi.operators() : Promise.resolve({ operators: [] as ProductionOperator[] }),
        canEdit ? productionApi.defects() : Promise.resolve({ defects: [] as ProductionDefect[] }),
        canEdit ? productionApi.qcRejections() : Promise.resolve({ rejections: [] as ProductionQcRejection[] }),
      ]);
      setTasks(taskResult.tasks);
      setBoxes(boxResult.boxes);
      setInstruments(instrumentResult.instruments);
      setOperators(operatorResult.operators);
      setDefects(defectResult.defects);
      setQcRejections(qcRejectionResult.rejections);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить данные ОТК");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function mutate(key: string, operation: () => Promise<unknown>) {
    if (busy) return;
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      if (result && typeof result === "object" && "warnings" in result && Array.isArray(result.warnings) && result.warnings.length) {
        setNotice(result.warnings.join(" "));
      }
      await reload();
    } catch (reason) {
      // Коробку или решение успели изменить (другой контролёр, мастер) — показываем свежее, чтобы следующий клик прошёл.
      if (reason instanceof ApiError && reason.status === 409) await reload();
      setError(reason instanceof Error ? reason.message : "Действие ОТК не выполнено");
    } finally {
      setBusy("");
    }
  }

  return <section className="page quality-page">
    <header className="page-head"><h1>ОТК</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {!loaded && <div className="empty">Загрузка…</div>}
    {loaded && canQuality && <QualityPanel boxes={boxes} tasks={tasks} instruments={instruments} busy={Boolean(busy)} mutate={mutate} />}
    {loaded && canEdit && <div className="review-row">
      <QcRejectionReview rejections={qcRejections} busy={Boolean(busy)} mutate={mutate} />
      <DefectReview defects={defects} operators={operators} busy={Boolean(busy)} mutate={mutate} />
    </div>}
    {loaded && canQuality && <InstrumentRegistry instruments={instruments} busy={Boolean(busy)} mutate={mutate} />}
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

function QualityPanel({ boxes, tasks, instruments, busy, mutate }: {
  boxes: ProductionBox[];
  tasks: ProductionTask[];
  instruments: ProductionInstrument[];
  busy: boolean;
  mutate: Mutate;
}) {
  const [drafts, setDrafts] = useState<Record<string, { accepted: string; rejected: string; character: string; description: string; instrumentIds: string[] }>>({});
  const [query, setQuery] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [returning, setReturning] = useState<Record<string, boolean>>({});
  const ready = boxes.filter((box) => {
    const task = tasks.find((item) => item.id === box.task_id);
    const operation = task?.operations.find((item) => item.id === box.operation_id);
    // В ОТК идёт коробка финальной операции схемы; операция не нашлась в задании — по старому: последний номер.
    const final = task && operation ? isFinal(task, operation) : task ? box.operation_position === Math.max(...task.operations.map((item) => item.position)) : false;
    // Подтверждает только ОТК (владелец, 13.10.2026): в очередь — сданная коробка; «Подтверждена» и не проверена — старые,
    // подтверждённые мастером до 13.10.
    return (box.status === "Закрыта оператором" || box.status === "Подтверждена") && !box.qc_inspected && final;
  });
  if (!ready.length) return <div className="panel box-review qc-review"><div className="manual-stock-head"><h2>Очередь ОТК</h2><span className="page-count">0</span></div><div className="empty">Коробок на проверку нет — они появятся, когда монтажник сдаст коробку с последней операции.</div></div>;
  // Первая правка не должна сбрасывать «Годные ОТК» с показанного количества коробки.
  const update = (box: ProductionBox, patch: Partial<{ accepted: string; rejected: string; character: string; description: string; instrumentIds: string[] }>) => setDrafts((current) => {
    const previous = current[box.id] ?? { accepted: box.available, rejected: "0", character: "", description: "", instrumentIds: [] };
    return { ...current, [box.id]: { ...previous, ...patch } };
  });
  const shown = ready.filter((box) => boxMatches(box, query));
  return <div className="panel box-review qc-review"><div className="manual-stock-head"><h2>Очередь ОТК</h2><span className="page-count">{ready.length}</span>
    {ready.length > 3 && <input className="search box-search" type="search" aria-label="Найти коробку по коду" placeholder="Код с коробки или номер" value={query} onChange={(event) => setQuery(event.target.value)} />}</div>
    {!shown.length && <div className="empty">Нет коробки с кодом «{query}» — проверьте код на коробке.</div>}
    <div className="box-grid">{shown.map((box) => {
      const draft = drafts[box.id] ?? { accepted: box.available, rejected: "0", character: "", description: "", instrumentIds: [] };
      const totalMatches = Number(draft.accepted || 0) + Number(draft.rejected || 0) === Number(box.available);
      return <div className="production-box" key={`${box.id}:${box.version}`}>
        <BoxName box={box} /><span>{box.item} · {box.available} шт</span>
        <label>Годные ОТК<input aria-label={`Годные ОТК ${box.number}`} type="number" min="0" step="1" value={draft.accepted} onChange={(event) => update(box, { accepted: event.target.value })} /></label>
        <label>Брак ОТК<input aria-label={`Брак ОТК ${box.number}`} type="number" min="0" step="1" value={draft.rejected} onChange={(event) => update(box, { rejected: event.target.value })} /></label>
        {Number(draft.rejected) > 0 && <><label>Характер<input value={draft.character} onChange={(event) => update(box, { character: event.target.value })} /></label><label>Описание<textarea value={draft.description} onChange={(event) => update(box, { description: event.target.value })} /></label></>}
        <label>Средства измерения<select multiple aria-label={`Средства измерения ${box.number}`} value={draft.instrumentIds} onChange={(event) => update(box, { instrumentIds: Array.from(event.target.selectedOptions, (option) => option.value) })}>{instruments.filter((item) => item.status === "Действует").map((item) => <option key={item.id} value={item.id}>{item.inventory_number} · {item.name} · до {formatDay(item.verification_due)}</option>)}</select></label>
        {!totalMatches && <small className="danger-text">Сумма должна быть {box.available}.</small>}
        <button className="primary small" disabled={busy || !totalMatches || (Number(draft.rejected) > 0 && (!draft.character.trim() || !draft.description.trim()))} onClick={() => void mutate(`qc:${box.id}`, () => productionApi.inspectBox(box.id, {
          version: box.version, accepted: Number(draft.accepted), rejected: Number(draft.rejected),
          defect_character: draft.character, defect_description: draft.description,
          instrument_ids: draft.instrumentIds,
        }))}>Принять ОТК</button>
        {box.status === "Закрыта оператором" && (returning[box.id]
          ? <>
            <label>Причина возврата<textarea aria-label={`Причина возврата ${box.number}`} value={reasons[box.id] ?? ""} onChange={(event) => setReasons((current) => ({ ...current, [box.id]: event.target.value }))} /></label>
            <div className="box-return-act">
              <button className="secondary small" disabled={busy || !(reasons[box.id] ?? "").trim()} onClick={() => void mutate(`qc-return:${box.id}`, () => productionApi.returnBox(box.id, box.version, (reasons[box.id] ?? "").trim()))}>Вернуть монтажнику</button>
              <button className="link-button" onClick={() => { setReturning((current) => ({ ...current, [box.id]: false })); setReasons((current) => ({ ...current, [box.id]: "" })); }}>Отмена</button>
            </div>
          </>
          : <button className="link-button" onClick={() => setReturning((current) => ({ ...current, [box.id]: true }))}>Вернуть…</button>)}
      </div>;
    })}</div>
  </div>;
}

const BLANK_INSTRUMENT = { inventory_number: "", name: "", instrument_type: "", verification_number: "", verified_at: "", verification_due: "" };

const INSTRUMENT_COLUMNS: DataColumn<ProductionInstrument>[] = [
  { key: "inventory_number", title: "Инв. №", value: (item) => item.inventory_number },
  { key: "name", title: "Наименование", value: (item) => item.name },
  { key: "instrument_type", title: "Тип", value: (item) => item.instrument_type, filter: true },
  { key: "verification_number", title: "№ поверки", value: (item) => item.verification_number },
  { key: "verified_at", title: "Поверено", value: (item) => item.verified_at, render: (item) => formatDay(item.verified_at) },
  { key: "verification_due", title: "Поверка до", value: (item) => item.verification_due, render: (item) => formatDay(item.verification_due) },
  { key: "status", title: "Статус", value: (item) => item.status, filter: true, render: (item) => <StatusChip status={item.status} /> },
];

// Реестр средств измерения: форма появляется только по «+ Добавить СИ», список — плотной таблицей.
function InstrumentRegistry({ instruments, busy, mutate }: {
  instruments: ProductionInstrument[];
  busy: boolean;
  mutate: Mutate;
}) {
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState(BLANK_INSTRUMENT);
  const text = (field: "inventory_number" | "name" | "instrument_type" | "verification_number", title: string, half = false) =>
    <label className={half ? "half" : undefined}>{title}<input value={draft[field]} onChange={(event) => setDraft({ ...draft, [field]: event.target.value })} /></label>;
  return <div className="panel instrument-registry">
    <div className="manual-stock-head"><h2>Средства измерения</h2><button className="secondary small" aria-expanded={show} onClick={() => setShow((value) => !value)}>+ Добавить СИ</button></div>
    {show && <div className="instrument-form">
      <div className="form-grid">
        {text("inventory_number", "Инв. №")}
        {text("name", "Наименование", true)}
        {text("instrument_type", "Тип")}
        {text("verification_number", "№ поверки", true)}
        <label>Поверено<input type="datetime-local" value={draft.verified_at} onChange={(event) => setDraft({ ...draft, verified_at: event.target.value })} /></label>
        <label>Поверка до<input type="datetime-local" value={draft.verification_due} onChange={(event) => setDraft({ ...draft, verification_due: event.target.value })} /></label>
      </div>
      <div className="form-actions">
        <button className="secondary small" onClick={() => { setShow(false); setDraft(BLANK_INSTRUMENT); }}>Отмена</button>
        <button className="primary small" disabled={busy || Object.values(draft).some((value) => !value.trim())} onClick={() => void mutate("instrument:new", async () => {
          const result = await productionApi.registerInstrument(draft);
          setDraft(BLANK_INSTRUMENT);
          setShow(false);
          return result;
        })}>Сохранить СИ</button>
      </div>
    </div>}
    <DataTable label="Средства измерения" storageKey="quality-instruments" rows={instruments} columns={INSTRUMENT_COLUMNS} rowKey={(item) => item.id}
      exportName="Средства измерения" emptyText="Средств измерения нет — добавьте первое кнопкой «+ Добавить СИ»." />
  </div>;
}
