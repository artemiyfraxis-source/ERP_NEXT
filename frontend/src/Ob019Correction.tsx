import { useEffect, useState } from "react";
import { failureText } from "./api";
import { formatMoment } from "./format";
import { machineControlApi, type CorrectionCase, type MachineControl, type TransferPlan } from "./machineControlApi";

/** «Проба и поправка» карточки ОБ.019 (навык erp-machine-hmi, владелец 19.11.2026): наладчик вводит только то, что
 *  намерил («Получилось, мм»); сервер считает поправку от значения на станке (на станке − получилось). Предпросмотр
 *  словами — только показ, решает сервер: показанное значение станка уходит с правкой (`seen`), и если на станке уже
 *  другое или туда идёт запись, сервер откажет — причина видна у кнопки. Ручная поправка со знаком — свёрнута. Диаметр и отвод ножа так не правятся:
 *  их подбирают на чертеже, настройка провода сохраняется сама. */

/** Что можно измерить линейкой после пробы; стягивание — режимом на чертеже (владелец 19.11.2026). */
export const MEASURABLE: [string, string][] = [
  ["length_mm", "Длина провода"], ["strip_front_mm", "Зачистка начала"], ["strip_back_mm", "Зачистка конца"],
];
const CASES: [CorrectionCase, string][] = [
  ["machine", "Станок режет не так"], ["etk", "Размер в ЭТК неверный"], ["both", "И станок, и ЭТК"],
];

function decimal(text: string | null | undefined): number | null {
  if (!text) return null;
  const value = Number(text.replace(",", ".").replace(/\s/g, ""));
  return text.trim() && Number.isFinite(value) ? value : null;
}

const mm = (value: number) => value.toFixed(2).replace(".", ",");
const signed = (value: number) => `${value > 0.004 ? "+" : ""}${mm(value)}`;
const signedText = (text: string) => { const value = decimal(text); return value === null ? text : signed(value); };

/** Предпросмотр словами: «На станке 325,00, получилось 320,00 → поправка +5,00, следующая запись 330,00». */
export function correctionPreview(written: number, measured: number, base: number | null): string {
  const offset = written - measured;
  const next = base === null ? "" : `, следующая запись ${mm(Math.max(base + offset, 0))}`;
  return `На станке ${mm(written)}, получилось ${mm(measured)} → поправка ${signed(offset)}${next}`;
}

export function Ob019Correction({ inventory, control, plan, machine, linkUp, onSaved }: {
  inventory: string; control: MachineControl; plan: TransferPlan | null;
  /** Значения на станке сейчас, мм (по ключу параметра). */
  machine: Record<string, number | null>;
  linkUp: boolean;
  /** После записи: план заново, новое значение параметра — на станок; → что сделано, словами. */
  onSaved: (parameter: string) => Promise<string>;
}) {
  const [parameter, setParameter] = useState("length_mm");
  const [kind, setKind] = useState<CorrectionCase>("machine");
  const [measured, setMeasured] = useState("");
  const [manual, setManual] = useState(false);
  const [offset, setOffset] = useState("");
  const [etkValue, setEtkValue] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const stored = control.offsets.find((item) => item.key === parameter);
  const row = plan?.rows.find((item) => item.key === parameter);
  const etkAllowed = !!stored?.etk && !!plan?.group_id;
  useEffect(() => { if (!etkAllowed) setKind("machine"); }, [etkAllowed]);

  const got = decimal(measured);
  const written = linkUp ? machine[parameter] ?? null : null;
  // Base of the next write: the job's nominal (the offset goes on top), else what the machine holds minus its offset.
  const base = row ? (row.offset === null ? null : decimal(row.nominal))
    : written === null ? null : written - (decimal(stored?.offset) ?? 0);
  const byMeasure = kind !== "etk" && !(manual && offset.trim());
  const preview = byMeasure && got !== null && written !== null ? correctionPreview(written, got, base) : "";
  const why = !control.can_edit ? "Запись поправок вам не разрешена"
    : kind !== "etk" && byMeasure && got === null ? "Введите, сколько получилось"
    : kind !== "etk" && byMeasure && written === null ? "Нужна связь со станком: поправка считается от значения на станке"
    : kind !== "machine" && !etkValue.trim() ? "Введите верный размер ЭТК" : "";

  const save = async () => {
    setBusy(true);
    setResult(null);
    try {
      const answer = await machineControlApi.correct(inventory, {
        parameter, case: kind, measured: measured.trim() || undefined, note: note.trim() || undefined,
        // The machine value the preview showed: the server refuses (409) when the machine holds another one now.
        seen: preview && written !== null ? mm(written) : undefined,
        offset: kind !== "etk" && manual && offset.trim() ? offset.trim() : undefined,
        etk_value: kind !== "machine" ? etkValue.trim() : undefined, group_id: kind !== "machine" ? plan?.group_id ?? undefined : undefined,
      });
      const machinePart = answer.offset === null ? ""
        : answer.written && answer.measured && !(manual && offset.trim())
          ? `на станке ${answer.written}, получилось ${answer.measured} → поправка ${signedText(answer.offset)} мм`
          : `поправка ${signedText(answer.offset)} мм`;
      const parts = [machinePart, ...answer.etk.map((item) => `ЭТК ${item.project}, ревизия ${item.revision}`)].filter(Boolean);
      setMeasured(""); setNote(""); setOffset(""); setEtkValue("");
      const done = await onSaved(parameter);
      setResult({ tone: "ok", text: `Поправка записана: ${parts.join("; ") || "без изменений"}${done ? `. ${done[0].toUpperCase()}${done.slice(1)}` : ""}` });
    } catch (reason) {
      setResult({ tone: "danger", text: failureText(reason, "Не удалось записать поправку") });
    } finally {
      setBusy(false);
    }
  };

  const changed = control.offsets.filter((item) => item.offset !== "0,00");
  return <section className="machine-block machine-correction" aria-label="Проба и поправка">
    <h3>Проба и поправка</h3>
    <div className="machine-form">
      <label><span>Что мерили</span>
        <select value={parameter} onChange={(event) => { setParameter(event.target.value); setResult(null); }}>
          {MEASURABLE.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      <label><span>Получилось, мм</span>
        <input inputMode="decimal" autoComplete="off" value={measured} onChange={(event) => setMeasured(event.target.value)} />
      </label>
      {etkAllowed && <fieldset className="machine-cases"><legend>Что не так</legend>
        {CASES.map(([value, text]) => <label key={value}>
          <input type="radio" name="machine-case" value={value} checked={kind === value} onChange={() => setKind(value)} />{text}
        </label>)}
      </fieldset>}
      {kind !== "machine" && <label><span>Верный размер ЭТК, мм</span>
        <input inputMode="decimal" value={etkValue} onChange={(event) => setEtkValue(event.target.value)}
          placeholder={row?.nominal ? `сейчас ${row.nominal}` : ""} />
      </label>}
    </div>
    {preview && <p className="machine-preview" role="status">{preview}</p>}
    {kind !== "etk" && <button type="button" className="machine-more-toggle" aria-expanded={manual} onClick={() => setManual(!manual)}>
      <span aria-hidden="true">{manual ? "▾" : "▸"}</span> Ввести поправку вручную
    </button>}
    {kind !== "etk" && manual && <div className="machine-form">
      <label><span>Поправка станка, мм (со знаком)</span>
        <input inputMode="decimal" value={offset} onChange={(event) => setOffset(event.target.value)}
          placeholder={stored ? `сейчас ${stored.offset}` : ""} />
      </label>
      <label className="wide"><span>Комментарий</span>
        <input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} />
      </label>
    </div>}
    <div className="machine-actions machine-action-row">
      <button type="button" className="primary" disabled={busy || Boolean(why)} onClick={() => void save()}>Записать поправку</button>
      {why && <span className="machine-muted">{why}</span>}
      {result && <span className={result.tone === "ok" ? "tone-ok" : "tone-danger"} role="status">{result.text}</span>}
    </div>
    <p className="machine-muted machine-hint">Диаметр и отвод ножа подбираются на чертеже: изменённое значение сразу уходит на станок
      и сохраняется в настройку провода</p>
    {changed.length > 0 && <ul className="machine-offsets" aria-label="Поправки станка">
      {changed.map((item) => <li key={item.key}>
        <span>{item.label}</span><b>{signedText(item.offset)} мм</b>
        <span className="machine-muted">{[item.reason, item.updated_by, formatMoment(item.updated_at)].filter(Boolean).join(" · ")}</span>
      </li>)}
    </ul>}
  </section>;
}
