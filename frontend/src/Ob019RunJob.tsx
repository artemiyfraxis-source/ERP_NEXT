import { useEffect, useState } from "react";
import { failureText } from "./api";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { machineControlApi, type MachineRun, type MachineRunState, type TransferChoice } from "./machineControlApi";
import type { Ob019Live } from "./machinesApi";
import { manualJobsApi, type ManualJob, type ManualJobOptions } from "./manualJobsApi";
import { StartForm } from "./ManualJobWindow";

/** «Выполнение задания» вверху карточки ОБ.019 (владелец 19.11.2026): выбрать задание — резку провода из ЭТК, идущую
 *  ручную работу или новую ручную работу; выбор сразу пишет его на станок (карточка вживую, Ob019SettingsPanel.tsx;
 *  список — `JobSelect`, на карточке он стоит в «Что режем»), после проверки записи — «Пуск»; пока станок режет —
 *  «сделано N из M» по счётчику станка; в конце число станка идёт в «Сдать группу» («Мои задания») или завершает
 *  ручную работу. Сброшенный счётчик — ввод вручную с пометкой. Стоп, Сброс и Пуск — одной строкой команд вверху
 *  карточки (Ob019Controls.tsx, навык erp-machine-hmi: каждая команда один раз); здесь — только «Завершить». */

export const NEW_MANUAL = "new";
export const manualKey = (id: string) => `manual:${id}`;

const NORMAL_ALARMS = [0, 4, 5];

const whole = (value: string) => (/^\d+$/.test(value.trim()) ? Number(value.trim()) : null);
const count = (value: number | null | undefined) => (value === null || value === undefined ? EMPTY : formatNumber(value));

function choiceText(item: TransferChoice): string {
  return `${item.number} · ${item.project} · ${item.wire} · ${item.length_mm.replace(".", ",")} мм · ${item.quantity} шт`;
}

function manualText(job: ManualJob): string {
  return [job.number, job.wire, job.length_mm ? `${job.length_mm} мм` : "", job.quantity_planned ? `${job.quantity_planned} шт` : "",
    job.purpose].filter(Boolean).join(" · ");
}

/** Что показать о прогоне: итог записи («записывается… / ✓ на станке / отказ») или ход; счёт «N из M» — только в полосе
 *  ниже и в строке состояния. */
function stageLine(run: MachineRun): [string, string] {
  switch (run.stage) {
    case "writing": return [`записывается… (${run.write.status_text.toLowerCase()})`, "info"];
    case "failed": return [`отказ: ${run.write.error || run.write.status_text} — проверьте станок и выберите задание ещё раз`, "danger"];
    case "moved": return ["отказ: счётчик станка сдвинулся, пока задание записывалось, — выберите задание ещё раз", "danger"];
    case "ready": return [`✓ на станке, проверено · счёт с ${count(run.counter_start)}`, "ok"];
    case "running": return ["идёт · счёт по станку", "info"];
    case "finished": return ["тираж готов", "ok"];
    case "reset": return ["Счётчик станка сброшен — введите количество вручную; «Пуск» — только после новой передачи задания", "warn"];
    case "nolink": return ["Нет свежих данных станка — счёт недоступен, количество вводится вручную", "warn"];
    case "closed": return [`Завершено ${run.closed_at ? formatMoment(run.closed_at) : ""}: ${run.machine_count === null
      ? "количество вручную" : `по станку ${formatNumber(run.machine_count)} шт`}`, "neutral"];
  }
}

function Progress({ run }: { run: MachineRun }) {
  const planned = run.quantity ?? run.total_set ?? 0;
  if (run.done === null || !planned) return null;
  const share = Math.min(100, Math.round((run.done / planned) * 100));
  return <div className="machine-run-progress">
    <div className="machine-run-bar" role="progressbar" aria-label="Сделано" aria-valuemin={0} aria-valuemax={planned}
      aria-valuenow={run.done}><span style={{ width: `${share}%` }} /></div>
    <strong className="machine-run-count">{formatNumber(run.done)} из {formatNumber(planned)} шт</strong>
  </div>;
}

/** Завершение: число станка (или вручную) — в «Сдать группу» для резки, в итоги ручной работы. */
function Finish({ inventory, run, manual, onDone, onCancel }: {
  inventory: string; run: MachineRun; manual: ManualJob | null; onDone: (text: string) => void; onCancel: () => void;
}) {
  const counted = run.done !== null && run.stage !== "reset" && run.stage !== "nolink";
  const [good, setGood] = useState(counted ? String(run.done) : "");
  const [defect, setDefect] = useState("0");
  const [reason, setReason] = useState("");
  const [material, setMaterial] = useState<"pending" | "not_issued">("pending");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isManual = run.kind === "manual";
  const goodValue = whole(good);
  const defectValue = defect.trim() ? whole(defect) : 0;
  const problem = !isManual ? ""
    : !manual ? "Ручная работа не найдена — обновите карточку"
      : goodValue === null ? "Укажите годные, шт"
        : defectValue === null ? "Брак — целое число"
          : defectValue > 0 && !reason.trim() ? "Укажите причину брака"
            : material === "not_issued" && !note.trim() ? "Укажите, почему материал не списывается" : "";

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const answer = await machineControlApi.closeRun(inventory, isManual && manual
        ? { run_id: run.id, version: manual.version, good: goodValue ?? 0, defect: defectValue ?? 0, defect_reason: reason,
          material, material_note: note }
        : { run_id: run.id });
      const total = answer.run.machine_count;
      onDone(isManual
        ? `Ручная работа ${manual?.number ?? ""} завершена: годных ${goodValue}${total === null ? ", количество вручную" : ""}`
        : total === null
          ? `Задание завершено. Счётчик станка не прочитан — в «Мои задания» → «Сдать группу» введите количество вручную`
          : `Задание завершено: по станку ${formatNumber(total)} шт — подтвердите в «Мои задания» → «Сдать группу»`);
    } catch (reason) {
      setError(failureText(reason, "Задание не завершено"));
    } finally {
      setBusy(false);
    }
  };

  return <div className="machine-confirm machine-run-finish" role="group" aria-label="Завершение задания">
    <p>
      <strong>{isManual ? "Завершить ручную работу" : "Завершить задание резки"}</strong>
      <span>{counted ? `По станку: ${formatNumber(run.done ?? 0)} шт` : "Счётчик станка сброшен или не прочитан — количество вручную"}</span>
    </p>
    {isManual ? <div className="machine-form">
      <label><span>Годные, шт</span>
        <input inputMode="numeric" autoComplete="off" value={good} onChange={(event) => setGood(event.target.value)} /></label>
      <label><span>Брак, шт</span>
        <input inputMode="numeric" autoComplete="off" value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
      {(defectValue ?? 0) > 0 && <label className="wide"><span>Причина брака</span>
        <input value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} /></label>}
      <label><span>Материал</span>
        <select value={material} onChange={(event) => setMaterial(event.target.value as "pending" | "not_issued")}>
          <option value="pending">Ждёт решения склада</option>
          <option value="not_issued">Не списывать</option>
        </select></label>
      {material === "not_issued" && <label className="wide"><span>Почему не списывать</span>
        <input value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} /></label>}
    </div> : <p className="machine-muted">Материалы не двигаются: число попадёт в «Сдать группу» в «Мои задания», там его
      подтверждают годными и браком.</p>}
    {problem && <p className="warning-inline">{problem}</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="machine-actions">
      <button type="button" className="primary" disabled={busy || Boolean(problem)} onClick={() => void save()}>
        {busy ? "Сохранение…" : isManual ? "Завершить работу" : "Завершить задание"}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onCancel}>Отмена</button>
    </div>
  </div>;
}

/** Список заданий: резка провода из ЭТК, идущая ручная работа, новая ручная работа. `last` — последнее известное задание
 *  станка (серым вместо «Выберите задание», например пока открыт пульт на ПК). */
export function JobSelect({ choices, manual, open, choice, onPick, id, last = "", disabled = false }: {
  choices: TransferChoice[] | null; manual: ManualJob | null; open: boolean; choice: string; onPick: (key: string) => void; id?: string;
  last?: string;
  disabled?: boolean;
}) {
  const greyed = !choice && !open && !!last && choices !== null;
  return <select id={id} aria-label="Задание" value={choice} disabled={disabled} className={greyed ? "is-last" : undefined}
    onChange={(event) => onPick(event.target.value)}>
    <option value="">{choices === null ? "Загрузка…" : open ? "Сменить задание…" : last ? `последнее: ${last}` : "Выберите задание"}</option>
    <optgroup label="Резка провода из ЭТК">
      {(choices ?? []).length === 0 && <option value="" disabled>Нет открытых групп резки провода</option>}
      {(choices ?? []).map((item) => <option key={item.key} value={item.key}>{choiceText(item)}</option>)}
    </optgroup>
    <optgroup label="Ручная работа">
      {manual && <option value={manualKey(manual.id)}>{manualText(manual)}</option>}
      <option value={NEW_MANUAL} disabled={!!manual}>{manual ? "Новая ручная работа — сначала завершите идущую" : "Новая ручная работа…"}</option>
    </optgroup>
  </select>;
}

export function Ob019RunJob({ inventory, equipmentId, state, live, choices, choice, onPick, onStarted, reload, picker = true,
  pickNote = null }: {
  inventory: string; equipmentId?: string; state: MachineRunState | null; live: Ob019Live | null;
  choices: TransferChoice[] | null; choice: string; onPick: (key: string) => void; onStarted: (job: ManualJob) => void;
  reload: () => void;
  /** false — список заданий стоит в другом месте карточки (`JobSelect`). */
  picker?: boolean;
  /** Итог записи только что выбранного задания, пока его прогона ещё нет: «записывается…», «отказ: …». */
  pickNote?: { tone: string; text: string } | null;
}) {
  const [options, setOptions] = useState<ManualJobOptions | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const run = state?.run ?? null;
  const manual = state?.manual ?? null;
  const open = run && run.stage !== "closed";
  const done = run?.stage === "finished" || run?.stage === "reset";

  useEffect(() => {
    if (choice !== NEW_MANUAL || options) return;
    let alive = true;
    manualJobsApi.options().then((answer) => { if (alive) setOptions(answer); })
      .catch((reason) => { if (alive) setMessage({ tone: "danger", text: failureText(reason, "Не удалось загрузить справочники") }); });
    return () => { alive = false; };
  }, [choice, options]);
  // The machine reported the run finished (or its counter went back): the finish opens by itself.
  useEffect(() => { if (done) setFinishing(true); }, [done, run?.id]);

  const manualRunning = manual && run?.kind === "manual" && run.job_id === manual.id ? null : manual;
  const alarm = live?.alarm && live.alarm.code !== null && !NORMAL_ALARMS.includes(live.alarm.code) ? live.alarm : null;
  const [line, tone] = run ? stageLine(run) : ["", "neutral"];
  // Nothing to say (no job on the machine, none picked): no empty panel — «Что режем» below is where a job is picked.
  if (!picker && !run && !pickNote && !message && !manualRunning && choice !== NEW_MANUAL) return null;
  return <section className="machine-run" aria-label="Выполнение задания">
    <div className="machine-run-head">
      <h3>Выполнение задания</h3>
      {picker && <label className="machine-input machine-run-pick">
        <span className="machine-input-label">Задание</span>
        <JobSelect choices={choices} manual={manual} open={!!open} choice={choice} onPick={onPick} />
      </label>}
    </div>
    {/* No link / pult open: the status strip says it once for the whole card. */}
    {pickNote && <p className={`machine-run-line tone-${pickNote.tone}`} role="status">{pickNote.text}</p>}
    {manualRunning && !choice && <p className="machine-muted">На станке идёт ручная работа {manualRunning.number} — выберите её,
      чтобы передать на станок и считать по станку</p>}

    {choice === NEW_MANUAL && (options
      ? <div className="machine-block machine-run-new">
        <h4>Новая ручная работа</h4>
        <StartForm options={options} equipmentId={equipmentId} inventory={inventory}
          onStarted={(job) => { onStarted(job); reload(); }} />
      </div>
      : <p className="empty">Загрузка…</p>)}

    {run && <div className={`machine-run-now${open ? "" : " is-closed"}`} role="status" aria-label="Задание на станке">
      <p className="machine-run-line">
        <strong>{run.label}</strong>
        <span className={`tone-${tone}`}>{line}</span>
      </p>
      {open && <Progress run={run} />}
      {open && alarm && <p className="tone-danger">Авария: {alarm.text}</p>}
      {open && !finishing && state?.can_edit && !["writing", "failed", "moved"].includes(run.stage) &&
        <div className="machine-actions machine-run-actions">
          <button type="button" className="secondary" onClick={() => setFinishing(true)}>Завершить</button>
        </div>}
    </div>}

    {finishing && run && open && run.stage !== "writing" && run.stage !== "failed" && run.stage !== "moved" &&
      <Finish key={run.id} inventory={inventory} run={run} manual={manual && manual.id === run.job_id ? manual : null}
        onCancel={() => setFinishing(false)}
        onDone={(text) => { setFinishing(false); setMessage({ tone: "ok", text }); reload(); }} />}
    {message && <div className={message.tone === "ok" ? "success-banner" : "error-banner"} role="status">{message.text}</div>}
  </section>;
}
