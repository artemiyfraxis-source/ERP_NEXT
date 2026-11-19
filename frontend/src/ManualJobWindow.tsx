import { useEffect, useMemo, useState, type FormEvent } from "react";
import { failureText } from "./api";
import { ComboBox, type ComboOption } from "./ComboBox";
import { EMPTY, formatWallMoment } from "./format";
import { manualJobsApi, type ManualJob, type ManualJobOptions } from "./manualJobsApi";
import { PageWindow } from "./PageWindow";
import "./manualJobs.css";

/** Окно «Ручная работа»: новая работа на станке (станок, эталон, провод / контакт из справочников или свои, длина,
 *  количество, «Зачем»), завершение (годные, брак с причиной, что делать с материалом) и отмена с причиной.
 *  «Списать с партии» появится со складом (срез S3) — пока кнопка видна, но выключена. */

type Material = "pending" | "not_issued";

const text = (value: string | number | null | undefined) => (value === null || value === undefined || value === "" ? EMPTY : String(value));
const whole = (value: string) => (/^\d+$/.test(value.trim()) ? Number(value.trim()) : null);

function Facts({ job }: { job: ManualJob }) {
  const facts: [string, string, string?][] = [
    ["Станок", [job.inventory, job.machine].filter(Boolean).join(" · ")],
    ["Зачем", job.purpose],
    ["Провод", job.wire], ["Контакт", job.contact], ["Длина, мм", job.length_mm ?? ""],
    ["План, шт", job.quantity_planned === null ? "" : String(job.quantity_planned)],
    ["Начата", `${formatWallMoment(job.started_at)} · ${job.operator}`],
    ["Завершена", job.finished_at ? [formatWallMoment(job.finished_at), job.finished_by].filter(Boolean).join(" · ") : ""],
    ["Счёт", job.count_text],
  ];
  if (job.status === "done") {
    facts.push(["Годные, шт", String(job.good ?? "")],
      ["Брак, шт", job.defect ? `${job.defect} — ${job.defect_reason}` : "0"],
      ["Материал", [job.material_text, job.material_note].filter(Boolean).join(": "), job.material_tone]);
  }
  if (job.status === "cancelled") facts.push(["Причина отмены", job.cancel_reason]);
  return <dl className="mj-facts">{facts.map(([label, value, tone]) => <div key={label}>
    <dt>{label}</dt><dd className={tone && tone !== "neutral" ? `tone-${tone}` : undefined}>{text(value)}</dd>
  </div>)}</dl>;
}

function StartForm({ options, equipmentId, inventory, onStarted }: {
  options: ManualJobOptions; equipmentId?: string; inventory?: string; onStarted: (job: ManualJob) => void;
}) {
  const initial = options.machines.find((item) => item.id === equipmentId || (inventory && item.inventory === inventory));
  const [machine, setMachine] = useState(initial?.id ?? "");
  const [reference, setReference] = useState("");
  const [wire, setWire] = useState("");
  const [contact, setContact] = useState("");
  const [length, setLength] = useState("");
  const [quantity, setQuantity] = useState("");
  const [purpose, setPurpose] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const machines: ComboOption[] = useMemo(() => options.machines.map((item) => ({
    value: item.id, label: [item.inventory, item.name].filter(Boolean).join(" · "),
    desc: item.running ? `идёт ${item.running}` : undefined })), [options.machines]);
  const wires: ComboOption[] = useMemo(() => options.wires.map((label) => ({ value: label, label })), [options.wires]);
  const contacts: ComboOption[] = useMemo(() => options.contacts.map((item) => ({ value: item.label, label: item.label, desc: item.maker || undefined })), [options.contacts]);
  const chosen = options.machines.find((item) => item.id === machine);
  const references = options.references.filter((item) => item.equipment_id === machine);
  const quantityValue = quantity.trim() ? whole(quantity) : null;
  const quantityBad = Boolean(quantity.trim()) && !quantityValue;
  const ready = Boolean(chosen && !chosen.running && purpose.trim() && !quantityBad && !busy);

  const pickReference = (id: string) => {
    setReference(id);
    const found = options.references.find((item) => item.id === id);
    if (found) { if (found.wire) setWire(found.wire); if (found.contact) setContact(found.contact); }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError("");
    try {
      onStarted(await manualJobsApi.start({ equipment_id: machine, purpose, wire, contact, length_mm: length,
        quantity_planned: quantityValue, reference_id: reference || null }));
    } catch (reason) {
      setError(failureText(reason, "Ручная работа не начата"));
    } finally {
      setBusy(false);
    }
  };
  return <form className="mj-form" onSubmit={submit} aria-label="Новая ручная работа">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="form-grid mj-grid">
      <label className="half">Станок<ComboBox strict options={machines} value={machine} aria-label="Станок" placeholder="№ или название станка…"
        onChange={(value) => { setMachine(value); setReference(""); }} onClear={() => { setMachine(""); setReference(""); }} /></label>
      <label className="half">Эталон наладки<select value={reference} disabled={!references.length} onChange={(event) => pickReference(event.target.value)}>
        <option value="">{references.length ? "Без эталона" : "У станка нет эталонов"}</option>
        {references.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select></label>
      <label className="half">Провод<ComboBox options={wires} value={wire} onChange={setWire} aria-label="Провод" name="wire" placeholder="Из справочника или свой…" /></label>
      <label className="half">Контакт<ComboBox options={contacts} value={contact} onChange={setContact} aria-label="Контакт" name="contact" placeholder="Из справочника или свой…" /></label>
      <label>Длина, мм<input name="length_mm" inputMode="decimal" autoComplete="off" value={length} onChange={(event) => setLength(event.target.value)} /></label>
      <label>Количество, шт<input name="quantity_planned" inputMode="numeric" autoComplete="off" placeholder="Не обязательно…" aria-invalid={quantityBad || undefined}
        value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
      <label className="half">Зачем<input name="purpose" required autoComplete="off" placeholder="Например: проба на образцах для ОТК…" value={purpose}
        onChange={(event) => setPurpose(event.target.value)} /></label>
    </div>
    {chosen?.running && <p className="warning-inline">На станке уже идёт ручная работа {chosen.running} — сначала завершите или отмените её</p>}
    {quantityBad && <p className="warning-inline">Количество — целое число больше нуля</p>}
    <div className="mj-actions">
      <button type="submit" className="primary" disabled={!ready}>{busy ? "Начинаю…" : "Начать работу"}</button>
    </div>
  </form>;
}

function FinishForm({ job, onSaved }: { job: ManualJob; onSaved: (job: ManualJob) => void }) {
  const [good, setGood] = useState("");
  const [defect, setDefect] = useState("0");
  const [reason, setReason] = useState("");
  const [material, setMaterial] = useState<Material | "">("");
  const [note, setNote] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const goodValue = whole(good);
  const defectValue = defect.trim() ? whole(defect) : 0;
  const problem = goodValue === null ? "Укажите годные, шт"
    : defectValue === null ? "Брак — целое число"
      : defectValue > 0 && !reason.trim() ? "Укажите причину брака"
        : !material ? "Выберите, что делать с материалом"
          : material === "not_issued" && !note.trim() ? "Укажите, почему материал не списывается" : "";

  const run = async (action: () => Promise<ManualJob>, fallback: string) => {
    setBusy(true);
    setError("");
    try {
      onSaved(await action());
    } catch (failure) {
      setError(failureText(failure, fallback));
    } finally {
      setBusy(false);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (problem || busy || !material) return;
    void run(() => manualJobsApi.finish(job.id, { version: job.version, good: goodValue ?? 0, defect: defectValue ?? 0,
      defect_reason: reason, material, material_note: note }), "Ручная работа не завершена");
  };
  return <>
    <form className="mj-form" onSubmit={submit} aria-label="Завершить работу">
      <h3>Завершить работу</h3>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="form-grid mj-grid">
        <label>Годные, шт<input name="good" inputMode="numeric" autoComplete="off" required value={good} onChange={(event) => setGood(event.target.value)} /></label>
        <label>Брак, шт<input name="defect" inputMode="numeric" autoComplete="off" value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
        <label className="half">Причина брака<input name="defect_reason" autoComplete="off" disabled={!defectValue} required={Boolean(defectValue)}
          placeholder={defectValue ? "Например: надрез жилы…" : "Брака нет"} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      </div>
      <fieldset className="mj-material">
        <legend>Материал</legend>
        <label className="mj-choice is-off" title="Появится со складом">
          <input type="radio" name="mj-material" disabled />Списать с партии<small>появится со складом</small>
        </label>
        <label className="mj-choice"><input type="radio" name="mj-material" checked={material === "not_issued"}
          onChange={() => setMaterial("not_issued")} />Не списывать</label>
        <label className="mj-choice"><input type="radio" name="mj-material" checked={material === "pending"}
          onChange={() => setMaterial("pending")} />Ждёт решения склада</label>
        {material === "not_issued" && <label className="mj-note">Почему не списывать<input name="material_note" autoComplete="off" required value={note}
          placeholder="Например: обрезки из отходов…" onChange={(event) => setNote(event.target.value)} /></label>}
      </fieldset>
      <div className="mj-actions">
        <button type="submit" className="primary" disabled={Boolean(problem) || busy}>{busy ? "Сохраняю…" : "Завершить работу"}</button>
        <span className="mj-hint" aria-live="polite">{problem}</span>
        <button type="button" className="secondary small mj-cancel-toggle" aria-expanded={cancelOpen} onClick={() => setCancelOpen(!cancelOpen)}>
          Отменить работу…</button>
      </div>
    </form>
    {cancelOpen && <form className="mj-cancel" aria-label="Отменить работу" onSubmit={(event) => {
      event.preventDefault();
      if (cancelReason.trim() && !busy) void run(() => manualJobsApi.cancel(job.id, job.version, cancelReason), "Ручная работа не отменена");
    }}>
      <label>Причина отмены<input name="cancel_reason" autoComplete="off" required value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} /></label>
      <button type="submit" className="secondary" disabled={!cancelReason.trim() || busy}>Отменить работу</button>
    </form>}
  </>;
}

export function ManualJobWindow({ job, equipmentId, inventory, canEdit, close, onSaved }: {
  /** `canEdit` не задан — решает сервер (`options.can_edit`). */
  job?: ManualJob | null; equipmentId?: string | null; inventory?: string; canEdit?: boolean; close: () => void;
  onSaved?: (job: ManualJob) => void;
}) {
  const [current, setCurrent] = useState<ManualJob | null>(job ?? null);
  const [options, setOptions] = useState<ManualJobOptions | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (current) return;
    const controller = new AbortController();
    manualJobsApi.options(controller.signal).then(setOptions)
      .catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason, "Не удалось загрузить станки и справочники")); });
    return () => controller.abort();
  }, [current]);
  const saved = (next: ManualJob) => { setCurrent(next); onSaved?.(next); };
  const title = current ? `Ручная работа ${current.number}` : "Новая ручная работа";
  return <PageWindow label={title} close={close} width={920}>
    <section className="panel mj-window">
      <header className="mj-window-head">
        <h2>{title}</h2>
        {current && <span className={`mj-status tone-${current.tone}`}>{current.status_text}</span>}
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {current && <Facts job={current} />}
      {current?.status === "running" && (canEdit ?? true) && <FinishForm key={current.version} job={current} onSaved={saved} />}
      {!current && error && <div className="error-banner" role="alert">{error}</div>}
      {!current && !error && !options && <p className="empty">Загрузка…</p>}
      {!current && options && ((canEdit ?? options.can_edit)
        ? <StartForm options={options} equipmentId={equipmentId ?? undefined} inventory={inventory} onStarted={saved} />
        : <p className="empty">Начинать ручную работу вам не разрешено</p>)}
    </section>
  </PageWindow>;
}
