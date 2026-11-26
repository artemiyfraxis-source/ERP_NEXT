import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureText, productionApi } from "./api";
import { etkDocOperations } from "./etkDocOps";
import { crimpText, etkCrimpEnds } from "./etkDraw";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { machinesApi, type MachineTaskContext, type Ob099Live, type UnitSetup } from "./machinesApi";

/** Наладка пресса ОБ.099 (владелец 23.11.2026: «тут не готов всё ещё экран настройки»; навык erp-machine-hmi):
 *  одна главная команда — «Подтвердить наладку» с результатом рядом, «Что обжимаем» (задание наладки, операция из
 *  инструкции или клемма и провод вручную) и таблица «Требуется | На прессе | Настроено». Требуемое — из операции ЭТК
 *  задания, иначе из эталона той же клеммы, провода и аппликатора (несколько подходящих — наладчик выбирает); «На прессе» — то, что агент прочитал из ПЛК. Агент пресса только
 *  читает (FINS 01 01): ERP не пишет в пресс, значения выставляются на пульте пресса, а «Подтвердить наладку» —
 *  запись наладчика в ERP (журнал наладки, задание наладки → «Готова»). */

export const PRESS_READ_ONLY = "Запись в пресс из ERP не поддерживается — значения выставляются на пульте пресса";

/** Что требует операция ЭТК пресса: клеммы, провод, аппликатор и высота обжима по записям обжима (`fields.crimpEnds`). */
export interface PressRequired { terminal: string; wire: string; sections: string[]; applicator: string; crimpHeight: string }

const distinct = (values: (string | undefined)[]) => [...new Set(values.map((value) => (value ?? "").trim()).filter(Boolean))];

/** Операция `position` документа ЭТК → требуемое прессу; null — операции нет или в ней нет записей обжима. */
export function etkPressRequired(document: Record<string, unknown> | null | undefined, position: number): PressRequired | null {
  const operation = etkDocOperations(document ?? null).find((item) => item.position === position) as { fields?: unknown } | undefined;
  const entries = operation ? etkCrimpEnds((operation.fields ?? {}) as Record<string, unknown>) : [];
  if (!entries.length) return null;
  const bySection = (pick: (entry: typeof entries[number]) => string | undefined) => {
    const values = distinct(entries.map(pick));
    if (values.length < 2) return values[0] ?? "";
    return entries.map((entry) => `${entry.section || entry.wire || EMPTY}: ${pick(entry)?.trim() || EMPTY}`).join("; ");
  };
  return {
    terminal: distinct(entries.map((entry) => entry.art)).join(", "),
    wire: distinct(entries.map((entry) => entry.section && !(entry.wire ?? "").includes(entry.section)
      ? [entry.wire, `(${entry.section})`].filter(Boolean).join(" ") : entry.wire)).join(", "),
    sections: distinct(entries.map((entry) => entry.section)),
    applicator: bySection((entry) => entry.applicator),
    crimpHeight: bySection((entry) => (entry.crimpHeight ? crimpText(entry.crimpHeight) : "")),
  };
}

const plainKey = (text: string) => text.toUpperCase().replace(/[\s_\-–.]/g, "");

/** Одно из перечисленных («A, B» / «A; B») совпадает со значением пресса; без требования — не сравнивается. */
function sameName(required: string, live: string | null): boolean | null {
  if (!required || !live || required.includes(":")) return null;
  return required.split(/[,;]/).map(plainKey).filter(Boolean).includes(plainKey(live));
}

const decimal = (text: string) => Number(text.replace(",", "."));

/** Высота в норме: «1,10–1,20», «1,15 ± 0,05»; одно число «1,15» — только с допуском (`tolerance`, допуск пресса), без
 *  допуска не сравнивается; другой вид (по сечениям) — тоже. */
export function heightFits(required: string, live: number | null, tolerance: number | null = null): boolean | null {
  if (!required || live === null) return null;
  const text = required.replace(/\s*мм\s*$/, "").trim();
  const number = "(\\d+(?:[.,]\\d+)?)";
  const eps = 0.0005;
  let match = new RegExp(`^${number}\\s*[–-]\\s*${number}$`).exec(text);
  if (match) return live >= decimal(match[1]) - eps && live <= decimal(match[2]) + eps;
  match = new RegExp(`^${number}\\s*±\\s*${number}$`).exec(text);
  if (match) return Math.abs(live - decimal(match[1])) <= decimal(match[2]) + eps;
  match = new RegExp(`^${number}$`).exec(text);
  return match && tolerance !== null ? Math.abs(live - decimal(match[1])) <= tolerance + eps : null;
}

const plural = (count: number) => count % 10 === 1 && count % 100 !== 11 ? "параметр"
  : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? "параметра" : "параметров";

const mm3 = (value: number) => formatNumber(value, { digits: 3, minDigits: 3 });

/** Строка таблицы: `id` — что это на прессе и в ЭТК; `key` — ключ записи (параметр модели наладки, без модели —
 *  подпись с единицей; «contact»/«wire» — клемма и провод записи). */
interface PressRow { id: string; key: string; label: string; unit: string; numeric: boolean }

const PRESS_ROWS: PressRow[] = [
  { id: "applicator", key: "applicator", label: "Аппликатор", unit: "", numeric: false },
  { id: "crimp_height_mm", key: "crimp_height_mm", label: "Высота обжима жилы", unit: "мм", numeric: true },
  { id: "ins_crimp_height_mm", key: "ins_crimp_height_mm", label: "Высота обжима изоляции", unit: "мм", numeric: true },
  { id: "force_window", key: "force_window", label: "Окно усилия обжима", unit: "Н", numeric: true },
  { id: "program", key: "program", label: "Программа пресса", unit: "", numeric: false },
];
const IDENTITY_ROWS: PressRow[] = [
  { id: "contact", key: "contact", label: "Клемма", unit: "", numeric: false },
  { id: "wire", key: "wire", label: "Провод", unit: "", numeric: false },
];

/** Данные наладки станка: загрузка и перезагрузка после записи (их читают и «Наладка», и «Журнал»). */
export function usePressSetup(inventory: string) {
  const [setup, setSetup] = useState<UnitSetup | null>(null);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try { setSetup(await machinesApi.setup(inventory)); setError(""); }
    catch (reason) { setError(failureText(reason, "Не удалось загрузить наладку пресса")); }
  }, [inventory]);
  useEffect(() => { void reload(); }, [reload]);
  return { setup, error, reload };
}

type Choice = string; // `task:<id>` · «instruction» · «manual»
const MANUAL = "manual", INSTRUCTION = "instruction";

type Reference = UnitSetup["references"][number];

/** Размер провода целыми токенами: «AWG22» / «22 AWG» → `awg22`; сечение — числом («0,35» и «0.350» → `mm0.35`),
 *  у «2х0,5» — сечение жилы `mm0.5`. «1» не совпадает с «1,5», «0,5» — с «10,5». */
export function wireTokens(text: string): Set<string> {
  const tokens = new Set<string>();
  let rest = text.replace(/(?:\bAWG\s*(\d+)|(\d+)\s*AWG)\b/gi, (_, a, b) => { tokens.add(`awg${Number(a ?? b)}`); return " "; });
  rest = rest.replace(/\d+\s*[хx×*]\s*(\d+(?:[.,]\d+)?)/gi, (_, size) => { tokens.add(`mm${Number(size.replace(",", "."))}`); return " "; });
  for (const number of rest.match(/\d+(?:[.,]\d+)?/g) ?? []) tokens.add(`mm${Number(number.replace(",", "."))}`);
  return tokens;
}

/** Эталоны клеммы, подходящие по проводу (сечение из ЭТК, провод ЭТК или пресса) и аппликатору, когда они известны.
 *  Провод без размера (метка пресса «A») сравнивается целиком. */
export function referenceCandidates(references: Reference[], contact: string, wires: string[], applicator: string): Reference[] {
  const key = plainKey(contact);
  if (!key) return [];
  const sizes = new Set(wires.flatMap((wire) => [...wireTokens(wire)]));
  const wireKeys = wires.map(plainKey).filter(Boolean);
  const wireFits = (wire: string) => sizes.size ? [...wireTokens(wire)].some((token) => sizes.has(token)) : wireKeys.includes(plainKey(wire));
  const applicatorKey = plainKey(applicator);
  return references.filter((item) => plainKey(item.contact) === key)
    .filter((item) => !wireKeys.length || wireFits(item.wire))
    .filter((item) => !applicatorKey || !item.values.applicator || plainKey(item.values.applicator) === applicatorKey)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

const referenceLabel = (item: Reference) => [formatMoment(item.created_at).slice(0, 5), item.wire, item.values.applicator].filter(Boolean).join(" · ");

interface EtkSource { taskId: string; operationId: string; position: number }
type EtkState = { key: string; done: boolean; required: PressRequired | null; error: string } | null;

function firstChoice(setup: UnitSetup, incoming?: MachineTaskContext): Choice {
  if (incoming) {
    const same = setup.open_tasks.find((item) => item.source?.task_id === incoming.taskId
      && item.source.operation_id === incoming.operationId && item.source.etk_position === incoming.action.etk_position);
    return same ? `task:${same.id}` : INSTRUCTION;
  }
  const task = setup.open_tasks.find((item) => item.mine && item.can_confirm) ?? setup.open_tasks.find((item) => item.can_confirm);
  return task ? `task:${task.id}` : MANUAL;
}

export function Ob099Setup({ inventory, live, fresh, canWrite = false, incoming, setup, error: loadError, reload, onHint }: {
  inventory: string; live: Ob099Live | null; fresh: boolean; canWrite?: boolean; incoming?: MachineTaskContext;
  setup: UnitSetup | null; error: string; reload: () => Promise<void>; onHint?: (hint: string) => void;
}) {
  const [choice, setChoice] = useState<Choice | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [etk, setEtk] = useState<EtkState>(null);
  const [picked, setPicked] = useState("");
  const [confirming, setConfirming] = useState(false);
  // A second click while the first request is pending: `busy` is not rendered yet, the ref stops it at once.
  const sending = useRef(false);

  const chosen = setup && (choice ?? firstChoice(setup, incoming));
  const task = chosen?.startsWith("task:") ? setup?.open_tasks.find((item) => `task:${item.id}` === chosen) ?? null : null;
  const source: EtkSource | null = task?.source ? { taskId: task.source.task_id, operationId: task.source.operation_id, position: task.source.etk_position }
    : chosen === INSTRUCTION && incoming ? { taskId: incoming.taskId, operationId: incoming.operationId, position: incoming.action.etk_position } : null;
  const sourceKey = source ? `${source.taskId}:${source.operationId}:${source.position}` : "";
  useEffect(() => {
    if (!source) { setEtk(null); return; }
    let alive = true;
    setEtk({ key: sourceKey, done: false, required: null, error: "" });
    productionApi.operationEtk(source.taskId, source.operationId)
      .then((answer) => { if (alive) setEtk({ key: sourceKey, done: true, required: etkPressRequired(answer.card.document, source.position), error: "" }); })
      .catch((reason) => { if (alive) setEtk({ key: sourceKey, done: true, required: null, error: failureText(reason, "ЭТК задания недоступна") }); });
    return () => { alive = false; };
    // The key names the source; the object itself is rebuilt on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey]);
  const required = etk?.key === sourceKey ? etk.required : null;
  const etkLoading = Boolean(source) && !(etk?.key === sourceKey && etk.done);

  const model = setup?.parameters ?? [];
  const rows: PressRow[] = model.length
    ? model.map((item) => ({ id: item.key, key: item.key, label: item.label, unit: item.unit, numeric: Boolean(item.unit) }))
    : PRESS_ROWS.map((row) => ({ ...row, key: row.unit ? `${row.label}, ${row.unit}` : row.label }));
  const configuration = setup?.configuration ?? null;

  /** Что прочитано с пресса: показ (с единицами) и сырое значение для «Настроено». */
  const pressValue = (key: string): { shown: string; raw: string; fits: boolean | null } | null => {
    if (!live) return null;
    const text = (value: string | null) => ({ shown: value || EMPTY, raw: value ?? "" });
    switch (key) {
      case "contact": return { ...text(live.job.terminal), fits: sameName(required?.terminal ?? "", live.job.terminal) };
      case "wire": return { ...text(live.job.wire), fits: null };
      case "applicator": return { ...text(live.job.applicator), fits: sameName(requiredOf("applicator").value, live.job.applicator) };
      case "program": return { ...text(live.job.product), fits: null };
      case "crimp_height_mm": {
        const height = live.crimp.height_mm;
        if (height === null) return { shown: EMPTY, raw: "", fits: null };
        const tolerance = live.crimp.tolerance_mm === null ? "" : ` ± ${mm3(live.crimp.tolerance_mm)}`;
        return { shown: `${mm3(height)} мм${tolerance}`, raw: mm3(height), fits: heightFits(requiredOf(key).value, height, live.crimp.tolerance_mm) };
      }
      default: return null;
    }
  };

  // Эталон той же клеммы, провода и аппликатора: требуемое, когда в ЭТК его нет или наладка без задания. Несколько
  // подходящих — не выбираем молча: «Несколько эталонов — выберите».
  const contactNow = values.contact ?? (required?.terminal || (fresh ? live?.job.terminal : "") || configuration?.contact || "");
  const liveWire = fresh ? live?.job.wire ?? "" : "", liveApplicator = fresh ? live?.job.applicator ?? "" : "";
  const wiresNow = useMemo(() => values.wire ? [values.wire] : required ? [...required.sections, required.wire].filter(Boolean)
    : liveWire ? [liveWire] : [], [values.wire, required, liveWire]);
  const applicatorNow = values.applicator ?? (required?.applicator && !/[;:]/.test(required.applicator) ? required.applicator : liveApplicator);
  const candidates = useMemo(() => referenceCandidates(setup?.references ?? [], contactNow, wiresNow, applicatorNow),
    [setup, contactNow, wiresNow, applicatorNow]);
  const reference = candidates.length === 1 ? candidates[0] : candidates.find((item) => item.id === picked) ?? null;
  const choose = candidates.length > 1 && !reference;
  function requiredOf(key: string): { value: string; from: string } {
    const fromEtk = key === "contact" ? required?.terminal : key === "wire" ? required?.wire
      : key === "applicator" ? required?.applicator : key === "crimp_height_mm" ? required?.crimpHeight : "";
    if (fromEtk) return { value: fromEtk, from: "ЭТК" };
    const stored = reference && key !== "contact" ? key === "wire" ? reference.wire : reference.values[key] : "";
    const numeric = rows.some((row) => row.id === key && row.numeric);
    return stored ? { value: numeric ? stored.replace(".", ",") : stored, from: `эталон ${formatMoment(reference!.created_at).slice(0, 5)}` }
      : { value: "", from: "" };
  }

  /** «Настроено»: введённое, иначе с пресса (свежее и не расходится с требуемым), иначе одно число/имя из требуемого,
   *  иначе как было записано. Пресс расходится с требуемым — пусто: наладчик вводит, что выставил. */
  const draftOf = (row: PressRow): { value: string; from: string } => {
    if (values[row.key] !== undefined) return { value: values[row.key], from: "" };
    const press = fresh ? pressValue(row.id) : null;
    if (press?.fits === false) return { value: "", from: "" };
    if (press?.raw) return { value: press.raw, from: "с пресса" };
    const need = requiredOf(row.id).value;
    if (need && (row.numeric ? /^\d+(?:,\d+)?$/.test(need) : !/[,;:]/.test(need))) return { value: need, from: requiredOf(row.id).from };
    const before = row.key === "contact" ? configuration?.contact : row.key === "wire" ? configuration?.wire : configuration?.values[row.key];
    return before ? { value: row.numeric ? before.replace(".", ",") : before, from: "как записано" } : { value: "", from: "" };
  };

  const differs = [...IDENTITY_ROWS, ...rows].filter((row) => pressValue(row.id)?.fits === false).map((row) => row.label);
  /** «Настроено» не как требуется (там, где это можно сравнить). */
  const draftFits = (row: PressRow): boolean | null => {
    const value = draftOf(row).value.trim(), need = requiredOf(row.id).value;
    if (!value || !need) return null;
    if (row.id === "crimp_height_mm") return heightFits(need, decimal(value), live?.crimp.tolerance_mm ?? null);
    return row.id === "contact" || row.id === "applicator" ? sameName(need, value) : null;
  };
  const mismatchedRows = [...IDENTITY_ROWS, ...rows].filter((row) => pressValue(row.id)?.fits === false || draftFits(row) === false);
  const mismatched = mismatchedRows.map((row) => row.label);
  /** «Как есть»: строка, где пресс расходится с требуемым, а «Настроено» пусто, — записывается значение пресса. */
  const asIs = (row: PressRow) => {
    const press = fresh ? pressValue(row.id) : null;
    return press?.fits === false && !draftOf(row).value.trim() ? press.raw.trim() : "";
  };
  // The dialog lists the differences; with none left it closes by itself.
  useEffect(() => { if (!mismatched.length) setConfirming(false); }, [mismatched.length]);
  const byTask = Boolean(task);
  const draft = Object.fromEntries(rows.map((row) => [row.key, draftOf(row).value.trim()]).filter(([, text]) => text));
  const contact = draftOf(IDENTITY_ROWS[0]).value.trim(), wire = draftOf(IDENTITY_ROWS[1]).value.trim();
  const why = !setup ? "" : !canWrite ? "Нет права записывать наладку"
    : task && !task.can_confirm ? "Задание наладки назначено другому наладчику"
      : !Object.keys(draft).length ? "Заполните «Настроено»"
        : !byTask && !reason.trim() ? "Укажите причину наладки без задания" : "";

  const hint = !setup || !live ? "" : differs.length
    ? `Пресс не как требуется: ${differs.join(", ").toLocaleLowerCase("ru")} — выставьте на пульте пресса и нажмите «${byTask ? "Подтвердить наладку" : "Записать наладку"}»`
    : byTask && task?.can_confirm ? "Сверьте пресс с ЭТК и нажмите «Подтвердить наладку»" : "";
  useEffect(() => { onHint?.(hint); }, [hint, onHint]);

  /** First press with differences — a confirmation listing them; «Подтвердить, как есть» records. */
  function press() {
    if (!setup || busy || sending.current || why) return;
    if (mismatched.length && !confirming) { setConfirming(true); return; }
    void save(false);
  }

  async function save(confirmedAsIs: boolean) {
    if (!setup || sending.current || why) return;
    sending.current = true;
    setBusy(true); setResult(null); setConfirming(false);
    try {
      const answer = await machinesApi.configure(inventory, {
        expected_version: setup.configuration?.version ?? 0, task_id: task ? task.id : null,
        ...(task ? { task_version: task.version } : {}), reason: reason.trim(),
        ...(confirmedAsIs ? {
          values: { ...draft, ...Object.fromEntries(rows.map((row) => [row.key, asIs(row)]).filter(([, text]) => text)) },
          wire: wire || asIs(IDENTITY_ROWS[1]), contact: contact || asIs(IDENTITY_ROWS[0]),
        } : { values: draft, wire, contact }) });
      const at = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      setResult({ tone: "ok", text: `Записано в ERP ${at}${answer.task_status === "Ready" ? " · задание наладки готово" : ""}` });
      setValues({}); setReason(""); setChoice(null); setPicked("");
      await reload();
    } catch (reason) { setResult({ tone: "danger", text: `Отказ: ${failureText(reason, "не удалось записать наладку")}` }); }
    finally { sending.current = false; setBusy(false); }
  }

  if (!setup) return <section className="machine-block" aria-label="Наладка пресса">
    {loadError ? <p className="error-banner" role="alert">{loadError}</p> : <p className="empty">Загрузка наладки…</p>}
  </section>;

  const action = byTask ? "Подтвердить наладку" : "Записать наладку";
  const showPress = Boolean(live);
  const instruction = chosen === INSTRUCTION || (incoming && !setup.open_tasks.some((item) => item.source?.task_id === incoming.taskId
    && item.source.operation_id === incoming.operationId && item.source.etk_position === incoming.action.etk_position));
  const liveLine = live ? [live.job.terminal && `клемма ${live.job.terminal}`, live.job.applicator && `аппликатор ${live.job.applicator}`,
    live.job.wire && `провод ${live.job.wire}`, live.crimp.height_mm !== null && `высота ${mm3(live.crimp.height_mm)} мм`].filter(Boolean).join(" · ") : "";
  const cell = (row: PressRow) => {
    const draftValue = draftOf(row);
    return <td><span className={`machine-param-input${row.numeric ? "" : " is-text"}`}>
      <input name={`press_${row.key}`} autoComplete="off" spellCheck={false} aria-label={`${row.label}, настроено`}
        value={draftValue.value} disabled={!canWrite || busy} inputMode={row.numeric ? "decimal" : "text"}
        onChange={(event) => { setValues({ ...values, [row.key]: event.target.value }); setConfirming(false); }} />
      {draftValue.from && <span className="machine-muted machine-hint">{draftValue.from}</span>}
    </span></td>;
  };
  const line = (row: PressRow, extra?: string) => {
    const need = requiredOf(row.id), press = pressValue(row.id);
    return <tr key={row.key} className={press?.fits === false ? "is-diff" : ""}>
      <th scope="row">{row.label}{(row.unit || extra) && <small>{[row.unit, extra].filter(Boolean).join(" · ")}</small>}</th>
      <td>{need.value || <span className="machine-muted">{etkLoading && row.id !== "program" ? "…" : EMPTY}</span>}
        {need.from && <span className="machine-muted machine-hint"> · {need.from}</span>}</td>
      {showPress && <td className={`machine-param-live machine-mono${fresh ? "" : " machine-muted"}`}>{press?.shown ?? EMPTY}</td>}
      {cell(row)}
    </tr>;
  };
  const limits = live && live.crimp.height_min_mm !== null && live.crimp.height_max_mm !== null
    ? `пределы пресса ${mm3(live.crimp.height_min_mm)}–${mm3(live.crimp.height_max_mm)}` : "";

  return <>
    <div className="machine-controls-bar" aria-label="Команда наладки">
      <div className="machine-cmd">
        <button type="button" className="primary" disabled={busy || Boolean(why)} onClick={press}>{busy ? "Записываю…" : action}</button>
        <span className={`machine-cmd-note${result ? ` tone-${result.tone}` : " machine-muted"}`} role="status">{result?.text ?? why}</span>
      </div>
      <span className="machine-muted machine-setup-note">{PRESS_READ_ONLY}</span>
    </div>
    {confirming && !why && <div className="warning-inline machine-setup-actions" role="alertdialog" aria-label="Не как требуется">
      <span>Подтвердить, как есть: {mismatched.length} {plural(mismatched.length)} не как требуется — {mismatchedRows.map((row) => {
        const kept = asIs(row);
        return `${row.label.toLocaleLowerCase("ru")}${kept ? ` (запишется как на прессе: ${kept})` : ""}`;
      }).join(", ")}</span>
      <button type="button" className="primary" disabled={busy} onClick={() => void save(true)}>Подтвердить, как есть</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => setConfirming(false)}>Отмена</button>
    </div>}

    <section className="machine-block machine-what" aria-label="Что обжимаем">
      <h3>Что обжимаем</h3>
      {liveLine && <p className={`machine-preview${fresh ? "" : " machine-muted"}`}>На прессе: {liveLine}</p>}
      <div className="machine-setup-tasks" role="radiogroup" aria-label="Для чего наладка">
        {setup.open_tasks.map((item) => <label key={item.id} className={chosen === `task:${item.id}` ? "is-on" : ""}>
          <input type="radio" name={`press-source-${inventory}`} checked={chosen === `task:${item.id}`} disabled={busy}
            onChange={() => { setChoice(`task:${item.id}`); setValues({}); setResult(null); }} />
          <span><strong>{item.operation}</strong>
            <span className="machine-muted"> · {item.status}{item.planned_start ? ` · ${formatMoment(item.planned_start)}` : ""}
              {item.can_confirm ? " · ждёт наладки" : " · назначено другому наладчику"}</span></span>
        </label>)}
        {incoming && instruction && <label className={chosen === INSTRUCTION ? "is-on" : ""}>
          <input type="radio" name={`press-source-${inventory}`} checked={chosen === INSTRUCTION} disabled={busy}
            onChange={() => { setChoice(INSTRUCTION); setValues({}); setResult(null); }} />
          <span><strong>Операция ЭТК {incoming.action.etk_position} · из инструкции</strong>
            <span className="machine-muted"> · без задания наладки, нужна причина</span></span>
        </label>}
        <label className={chosen === MANUAL ? "is-on" : ""}>
          <input type="radio" name={`press-source-${inventory}`} checked={chosen === MANUAL} disabled={busy}
            onChange={() => { setChoice(MANUAL); setValues({}); setResult(null); }} />
          <span><strong>Без задания</strong><span className="machine-muted"> · клемма, провод и аппликатор вручную, нужна причина</span></span>
        </label>
      </div>
      {!byTask && <label className="machine-setup-reason"><span>Причина</span>
        <input name="press_reason" autoComplete="off" value={reason} maxLength={500} disabled={!canWrite || busy}
          placeholder={chosen === INSTRUCTION ? "например, обжим по инструкции" : "например, смена аппликатора"}
          onChange={(event) => setReason(event.target.value)} /></label>}
      {candidates.length > 1 && <label className="machine-pick"><span>{choose ? "Несколько эталонов — выберите" : "Эталон"}</span>
        <select name="press_reference" value={reference?.id ?? ""} disabled={busy} onChange={(event) => { setPicked(event.target.value); setConfirming(false); }}>
          <option value="">не брать эталон</option>
          {candidates.map((item) => <option key={item.id} value={item.id}>{referenceLabel(item)}</option>)}
        </select></label>}
      {source && etk?.key === sourceKey && etk.error && <p className="warning-inline">ЭТК задания не прочитана: {etk.error}</p>}
      {source && etk?.key === sourceKey && !etk.error && !etk.required && !etkLoading
        && <p className="warning-inline">В операции ЭТК {source.position} нет клеммы и высоты обжима</p>}
    </section>

    <table className="machine-table machine-setup-table machine-params" aria-label="Параметры пресса">
      <thead><tr>
        <th scope="col">Параметр</th>
        <th scope="col">Требуется</th>
        {showPress && <th scope="col">На прессе{fresh ? "" : " · последние"}</th>}
        <th scope="col">Настроено</th>
      </tr></thead>
      <tbody>
        {IDENTITY_ROWS.map((row) => line(row))}
        {rows.map((row) => line(row, row.id === "crimp_height_mm" ? limits : undefined))}
      </tbody>
    </table>
    {configuration && <p className="machine-muted machine-setup-note">
      Записано в ERP: {configuration.set_by || EMPTY}{configuration.set_at ? `, ${formatMoment(configuration.set_at)}` : ""}</p>}
  </>;
}
