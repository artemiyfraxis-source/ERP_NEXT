import type { ReactNode } from "react";
import { EMPTY } from "./format";
import type { MarkingPlan } from "./machineControlApi";

/** Лазерная маркировка в карточке ОБ.019 (владелец 20.11.2026: «из ЭТК взять задание с маркировкой или в ручной работе;
 *  режимы — маркировка по краям и по всей длине»). Режим читается с регистров станка; выбор режима пишет одной записью
 *  все регистры этого режима (и явно стирает тексты другого). Правила — как на сервере (workflows/ob019_marking.py);
 *  запись и её итоги — в Ob019SettingsPanel.tsx. */

export type MarkMode = "none" | "ends" | "full";
export const MARK_MODES: [MarkMode, string][] = [["none", "Без"], ["ends", "По краям"], ["full", "По всей длине"]];
/** Mode fields; the main card's marking settings expose font, speed and rotation separately. */
export const MARK_KEYS = ["marking", "mark_by_step", "mark_count", "mark_front", "mark_middle", "mark_back",
  "mark_pos_front_mm", "mark_pos_back_mm", "mark_step_mm"];
export const MARK_TEXTS = ["mark_front", "mark_middle", "mark_back"];
/** Станок хранит до 30 знаков ASCII (сервер: machines.TEXT_CHARS). */
export const TEXT_CHARS = 30;
/** Без значения на станке — как сервер: метки в 20 мм от торцов, шаг 100 мм. */
export const DEFAULT_MARGIN_MM = "20";
export const DEFAULT_STEP_MM = "100";

const LATIN: Record<string, string> = Object.fromEntries([..."абвгдеёжзийклмнопрстуфхцчшщъыьэюя"].map((char, index) => [char,
  ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts",
    "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"][index]]));

const spaced = (text: string) => text.split(/\s+/).filter(Boolean).join(" ");

/** Текст в латиницу без обрезки (сервер: machine_writes_api.latin); прочие не-ASCII знаки выпадают. */
function latinFull(text: string): string {
  let out = "";
  for (const char of spaced(text)) {
    const low = char.toLowerCase();
    if (low in LATIN) out += char !== low ? LATIN[low].toUpperCase() : LATIN[low];
    else if (char.charCodeAt(0) >= 32 && char.charCodeAt(0) <= 126) out += char;
  }
  return out;
}

/** «Ж1 к XS17» → «Zh1 k XS17», до 30 знаков — так текст ляжет на станок. */
export function latin(text: string): string {
  return latinFull(text).slice(0, TEXT_CHARS);
}

/** Длина текста в латинице до обрезки: длиннее 30 — станок его не примет целиком. */
export function latinLength(text: string): number {
  return latinFull(text).length;
}

/** Режим по регистрам станка: маркировка выкл — «Без»; вкл и средние метки по шагу — «По всей длине»; иначе «По краям».
 *  `marking` / `byStep` — «1», «0» или «» (не прочитано). */
export function markMode(marking: string, byStep: string): MarkMode | null {
  if (marking === "0") return "none";
  if (marking !== "1") return null;
  return byStep === "1" ? "full" : "ends";
}

const positive = (text: string) => {
  const value = Number(text.replace(",", ".").replace(/\s/g, ""));
  return text.trim() !== "" && Number.isFinite(value) && value > 0;
};

/** One batch per mode, matching the ETK planner. Off clears all texts and the middle-mark mode; ends clears the
 *  middle, full clears both edges. Preserve the current mode's text, falling back to another mode's text. */
export function modeValues(mode: MarkMode, now: (key: string) => string): Record<string, string> {
  if (mode === "none") return { marking: "0", mark_by_step: "0", mark_count: "0", mark_front: "", mark_middle: "", mark_back: "" };
  const front = now("mark_front");
  const middle = now("mark_middle");
  const back = now("mark_back");
  const margin = (key: string) => (positive(now(key)) ? now(key) : DEFAULT_MARGIN_MM);
  const positions = { mark_pos_front_mm: margin("mark_pos_front_mm"), mark_pos_back_mm: margin("mark_pos_back_mm") };
  if (mode === "ends") {
    return { marking: "1", mark_by_step: "0", mark_count: "0", mark_front: latin(front || middle), mark_back: latin(back || middle),
      mark_middle: "", ...positions };
  }
  const step = now("mark_step_mm");
  return { marking: "1", mark_by_step: "1", mark_step_mm: positive(step) ? step : DEFAULT_STEP_MM,
    mark_middle: latin(middle || front || back), mark_front: "", mark_back: "", mark_count: "0", ...positions };
}

/** Регистры маркировки из ЭТК для записи: «Вкл»/«Выкл» → «1»/«0», тексты как есть («» — стереть). */
export function planValues(plan: MarkingPlan): Record<string, string> {
  return Object.fromEntries(plan.rows.map((row) => [row.key, row.value === "Вкл" ? "1" : row.value === "Выкл" ? "0" : row.value]));
}

const quoted = (text: string | undefined) => `«${text || EMPTY}»`;

/** «из ЭТК: По краям «C1.5 to XS17.7» / «XS17.7»». */
export function etkText(plan: MarkingPlan): string {
  const values = Object.fromEntries(plan.rows.map((row) => [row.key, row.value]));
  if (plan.mode === "ends") return `из ЭТК: ${plan.mode_label} ${quoted(values.mark_front)} / ${quoted(values.mark_back)}`;
  if (plan.mode === "full") {
    const step = values.mark_step_mm ? ` · шаг ${values.mark_step_mm.replace(".", ",")} мм` : "";
    return `из ЭТК: ${plan.mode_label} ${quoted(values.mark_middle)}${step}`;
  }
  return `из ЭТК: ${plan.mode_label}`;
}

type Note = { tone: string; text: string } | null;

/** Блок «Маркировка»: переключатель режима (с регистров станка) и маркировка задания из ЭТК. */
export function Ob019MarkingBlock({ mode, disabled, old, note, plan, onPick }: {
  mode: MarkMode | null; disabled: boolean; old: boolean; note: Note; plan: MarkingPlan | null; onPick: (mode: MarkMode) => void;
}) {
  return <section className={`machine-block machine-marking${old ? " is-old" : ""}`} aria-label="Маркировка">
    <div className="machine-marking-head">
      <h3>Маркировка</h3>
      <span className="machine-pull-modes machine-mark-modes" role="radiogroup" aria-label="Режим маркировки">
        {MARK_MODES.map(([code, text]) => <button key={code} type="button" role="radio" aria-checked={mode === code}
          className={mode === code ? "on" : undefined} disabled={disabled}
          onClick={() => { if (mode !== code) onPick(code); }}>{text}</button>)}
      </span>
      {note ? <span className={`tone-${note.tone}`} role="status">{note.text}</span>
        : mode === null ? <span className="machine-muted">станок не прислал режим маркировки</span> : null}
    </div>
    {plan && <p className="machine-marking-etk">{etkText(plan)}</p>}
    {plan && plan.warnings.length > 0 && <ul className="machine-marking-warnings">
      {plan.warnings.map((text) => <li key={text} className="tone-warn">{text}</li>)}
    </ul>}
    {plan && plan.blocking.length > 0 && <p className="warning-inline" role="status">
      Маркировка из ЭТК не записывается: {plan.blocking.join("; ")}
    </p>}
  </section>;
}

/** Текст метки на чертеже: латиница до 30 знаков; кириллица переводится, перевод виден до записи. */
export function MarkText({ id, label, name, value, dirty, disabled, old, note, onChange, onCommit, onCancel }: {
  id: string; label: string; name: string; value: string; dirty: boolean; disabled: boolean; old: boolean; note: Note;
  onChange: (value: string) => void; onCommit: (value: string) => void; onCancel: () => void;
}) {
  const shown = latin(value);
  const length = latinLength(value);
  const over = length > TEXT_CHARS;
  const translated = spaced(value) !== "" && shown !== spaced(value);
  const writing = note?.text === "записывается…";
  return <div className={`machine-input machine-mark-text${writing ? " is-writing" : ""}${over ? " is-missing" : ""}${old ? " is-old" : ""}`}>
    <label className="machine-input-label" htmlFor={id}>{label}</label>
    <span className="machine-input-box">
      <input id={id} aria-label={name} value={value} disabled={disabled} maxLength={60} autoComplete="off" spellCheck={false}
        placeholder="латиница, до 30 знаков"
        onChange={(event) => onChange(event.target.value)}
        onBlur={() => { if (dirty) onCommit(value); }}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); onCommit((event.target as HTMLInputElement).value); }
          if (event.key === "Escape") onCancel();
        }} />
    </span>
    <span className="machine-input-note">
      {note && <span className={`tone-${note.tone}`} role="status">{note.text}</span>}
      {over ? <span className="tone-danger">длиннее {TEXT_CHARS} знаков (в латинице {length})</span>
        : translated ? <span className="machine-muted">на станок: «{shown}»</span>
        : !note ? <span className="machine-muted">латиница, до {TEXT_CHARS} знаков</span> : null}
    </span>
  </div>;
}

/** Поля маркировки под проводом: «По краям» — текст и отступ у каждого торца, «По всей длине» — текст и шаг. */
export function Ob019MarkingRow({ mode, text, number }: {
  mode: MarkMode | null; text: (key: string, label: string) => ReactNode; number: (key: string, label: string) => ReactNode;
}) {
  if (mode === "ends") return <div className="wire-cell wire-mark-row is-ends" role="group" aria-label="Метки по краям">
    <div className="wire-mark-group">{text("mark_front", "Текст у начала")}{number("mark_pos_front_mm", "от торца")}</div>
    <div className="wire-mark-group">{text("mark_back", "Текст у конца")}{number("mark_pos_back_mm", "от торца")}</div>
  </div>;
  if (mode === "full") return <div className="wire-cell wire-mark-row is-full" role="group" aria-label="Метки по всей длине">
    <div className="wire-mark-group">{text("mark_middle", "Текст по длине")}{number("mark_step_mm", "Шаг меток")}</div>
  </div>;
  return null;
}
