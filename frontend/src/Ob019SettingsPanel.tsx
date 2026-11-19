import { useCallback, useEffect, useMemo, useState } from "react";
import { failureText } from "./api";
import { EMPTY, formatMoment, formatNumber } from "./format";
import {
  machineControlApi, type CorrectionCase, type MachineCommand, type MachineControl, type SettingField, type TransferChoice,
  type TransferPlan,
} from "./machineControlApi";
import { usePolled } from "./machinesApi";

/** «Настройки» карточки ОБ.019: все страницы штатного пульта (только чтение для того, что ERP не пишет), запись
 *  задания, количеств, длин и текстов маркировки на станок, «Передать на станок» из ЭТК и правка после пробы
 *  (владелец 19.11.2026). Запись — только когда станок ждёт пуска или тираж готов, пульт PXS_HMI закрыт. */

const STATES: Record<number, string> = {
  100: "Не сброшен", 101: "Идёт сброс", 102: "Готов, ждёт пуска", 103: "Работает", 104: "Пауза", 105: "Аварийный стоп",
  106: "Пучок готов", 107: "Тираж готов",
};
const MODES: [string, string][] = [["0", "Пошаговый"], ["1", "Один цикл"], ["2", "Автомат"]];
const CASES: [CorrectionCase, string][] = [
  ["machine", "Станок режет не так"], ["etk", "Размер в ЭТК неверный"], ["both", "И станок, и ЭТК"],
];

function stateText(state: number | null): string {
  return state === null ? "неизвестно" : STATES[state] ?? `код ${state}`;
}

function shown(field: SettingField): string {
  if (field.value === null || field.value === undefined) return EMPTY;
  if (field.kind === "mode") return field.text ?? String(field.value);
  if (typeof field.value === "boolean") return field.value ? "Вкл" : "Выкл";
  if (typeof field.value === "string") return field.value || EMPTY;
  const digits = field.unit === "мм" ? 2 : field.unit === "с" ? 3 : 0;
  const number = formatNumber(field.value, { digits, minDigits: digits });
  return field.unit ? `${number} ${field.unit}` : number;
}

function decimal(text: string): number | null {
  const value = Number(text.replace(",", ".").replace(/\s/g, ""));
  return text.trim() && Number.isFinite(value) ? value : null;
}

function comma(value: number): string {
  return value.toFixed(2).replace(".", ",");
}

function CommandLine({ command }: { command: MachineCommand }) {
  return <li>
    <span className={`tone-${command.tone}`}>{command.status_text}</span>
    <span>{command.summary}</span>
    {(command.state_before || command.state_after) && <span>{command.state_before ?? EMPTY} → {command.state_after ?? EMPTY}</span>}
    <span className="machine-muted">{command.created_by} · {formatMoment(command.created_at)}</span>
    {command.error && <span className="tone-danger">{command.error}</span>}
  </li>;
}

/** Подтверждение записи: что изменится и в каком состоянии станок сейчас. */
function Confirm({ state, lines, busy, onYes, onNo }: {
  state: number | null; lines: string[]; busy: boolean; onYes: () => void; onNo: () => void;
}) {
  return <div className="machine-confirm" role="alertdialog" aria-label="Подтверждение записи на станок">
    <p><strong>Станок: {stateText(state)}.</strong> Записать на станок:</p>
    <ul>{lines.map((line) => <li key={line}>{line}</li>)}</ul>
    <div className="machine-actions">
      <button className="primary small" disabled={busy} onClick={onYes}>Подтвердить запись</button>
      <button className="secondary small" disabled={busy} onClick={onNo}>Отмена</button>
    </div>
  </div>;
}

export function Ob019SettingsPanel({ inventory }: { inventory: string }) {
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(inventory, signal), [inventory]);
  const loadControl = useCallback((signal: AbortSignal) => machineControlApi.control(inventory, signal), [inventory]);
  const { data: card, error: cardError } = usePolled(loadWindow, undefined, "Не удалось загрузить настройки станка");
  const { data: control, error: controlError } = usePolled(loadControl, undefined, "Не удалось загрузить запись на станок");
  const [group, setGroup] = useState(0);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState<"" | "settings" | "transfer">("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [choices, setChoices] = useState<TransferChoice[] | null>(null);
  const [choice, setChoice] = useState("");
  const [plan, setPlan] = useState<TransferPlan | null>(null);

  useEffect(() => {
    let live = true;
    machineControlApi.choices(inventory).then((answer) => { if (live) setChoices(answer.choices); })
      .catch((reason) => { if (live) setMessage({ tone: "danger", text: failureText(reason, "Не удалось загрузить задания резки") }); });
    return () => { live = false; };
  }, [inventory]);

  const picked = choices?.find((item) => item.key === choice) ?? null;
  useEffect(() => {
    setPlan(null);
    if (!picked) return;
    let live = true;
    machineControlApi.plan(inventory, picked.group_id, picked.length_mm).then((answer) => { if (live) setPlan(answer); })
      .catch((reason) => { if (live) setMessage({ tone: "danger", text: failureText(reason, "Не удалось посчитать значения") }); });
    return () => { live = false; };
  }, [inventory, picked?.group_id, picked?.length_mm]); // eslint-disable-line react-hooks/exhaustive-deps

  const fields = useMemo(() => new Map((card?.settings ?? []).flatMap((item) => item.fields)
    .filter((field) => field.key).map((field) => [field.key as string, field])), [card]);
  const changes = Object.entries(edits).filter(([key, value]) => fields.has(key) && value.trim() !== "");
  const canWrite = !!control && control.can_edit && !control.problem;

  const run = async (action: () => Promise<MachineCommand>, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const command = await action();
      setMessage({ tone: "ok", text: `${done}: ${command.status_text.toLowerCase()}` });
      setConfirming("");
      return true;
    } catch (reason) {
      setMessage({ tone: "danger", text: failureText(reason, "Не удалось записать на станок") });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const writeSettings = async () => {
    const values = Object.fromEntries(changes.map(([key, value]) => [key, value]));
    if (await run(() => machineControlApi.write(inventory, values, control?.state ?? null), "Запись настроек")) setEdits({});
  };
  const transfer = () => plan && run(() => machineControlApi.transfer(inventory, plan.group_id, picked?.length_mm ?? plan.length_mm,
    control?.state ?? null), "Передать на станок");

  const error = cardError || controlError;
  const groups = card?.settings ?? [];
  const current = groups[Math.min(group, Math.max(groups.length - 1, 0))];
  const lastCommand = control?.commands[0];

  return <section className="machine-settings" aria-label="Настройки станка">
    <h3>Настройки</h3>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {control && <p className={control.problem ? "warning-inline" : "machine-muted"} role="status">
      {control.problem || `Можно записывать: ${stateText(control.state).toLowerCase()}, пульт PXS_HMI закрыт`}
      {!control.can_edit && " · запись вам не разрешена"}
    </p>}
    {message && <div className={message.tone === "ok" ? "success-banner" : "error-banner"} role="status">{message.text}</div>}
    {lastCommand && <ul className="machine-commands" aria-label="Последние записи">
      {control!.commands.slice(0, 3).map((command) => <CommandLine key={command.id} command={command} />)}
    </ul>}

    <div className="machine-block">
      <h4>Передать на станок из ЭТК</h4>
      <label className="machine-pick">
        <span>Задание резки</span>
        <select value={choice} onChange={(event) => { setChoice(event.target.value); setConfirming(""); }}>
          <option value="">{choices === null ? "Загрузка…" : choices.length ? "Выберите задание" : "Нет открытых групп резки провода"}</option>
          {(choices ?? []).map((item) => <option key={item.key} value={item.key}>
            {`${item.number} · ${item.project} · ${item.wire} · ${item.length_mm.replace(".", ",")} мм · ${item.quantity} шт`}
          </option>)}
        </select>
      </label>
      {picked && !plan && <p className="empty">Загрузка…</p>}
      {plan && <>
        <table className="machine-table">
          <thead><tr><th>Параметр</th><th>По ЭТК</th><th>Поправка станка</th><th>Будет записано</th><th>На станке</th></tr></thead>
          <tbody>{plan.rows.map((row) => <tr key={row.key}>
            <th scope="row">{row.label}<small>{row.source}</small></th>
            <td>{row.nominal ?? EMPTY}</td>
            <td>{row.offset === "0,00" ? EMPTY : row.offset}</td>
            <td className={row.value === null ? "machine-muted" : "machine-strong"}>{row.value ?? "остаётся как на станке"}</td>
            <td>{row.live ?? EMPTY}</td>
          </tr>)}</tbody>
        </table>
        <p className="machine-muted">
          Тираж и количество по заказу: {plan.quantity} шт · Заказ: {plan.job.order_number || EMPTY} · Изделие: {plan.job.product || EMPTY}
        </p>
        {plan.blocking.length > 0 && <p className="warning-inline">В ЭТК не задано: {plan.blocking.join(", ")}</p>}
        {confirming === "transfer"
          ? <Confirm state={control?.state ?? null} busy={busy} onNo={() => setConfirming("")} onYes={() => void transfer()}
            lines={[...plan.rows.filter((row) => row.value !== null).map((row) => `${row.label}: ${row.value} мм`),
              `Тираж: ${plan.quantity} шт`]} />
          : <div className="machine-actions">
            <button className="primary small" disabled={!canWrite || busy || plan.blocking.length > 0}
              onClick={() => setConfirming("transfer")}>Передать на станок</button>
          </div>}
      </>}
    </div>

    {groups.length > 0 && <div className="machine-block">
      <div className="seg-tabs" role="tablist" aria-label="Страницы пульта">
        {groups.map((item, index) => <button key={item.title} role="tab" aria-selected={index === group}
          className={index === group ? "on" : undefined} onClick={() => setGroup(index)}>{item.title}</button>)}
      </div>
      {current && <table className="machine-table" aria-label={current.title}>
        <thead><tr><th>Параметр</th><th>На станке</th><th>Записать</th></tr></thead>
        <tbody>{current.fields.map((field) => <tr key={`${field.label}-${field.kind}`}>
          <th scope="row">{field.label}</th>
          <td>{field.read ? shown(field) : EMPTY}</td>
          <td>{field.key && control?.can_edit
            ? field.kind === "mode"
              ? <select aria-label={`${field.label}: новое значение`} value={edits[field.key] ?? ""}
                onChange={(event) => setEdits({ ...edits, [field.key as string]: event.target.value })}>
                <option value="">Не менять</option>
                {MODES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
              </select>
              : <input aria-label={`${field.label}: новое значение`} value={edits[field.key] ?? ""}
                inputMode={field.kind === "text15" ? "text" : "decimal"} maxLength={field.kind === "text15" ? 30 : 12}
                placeholder={field.kind === "text15" ? "латиница, до 30 знаков" : field.unit}
                onChange={(event) => setEdits({ ...edits, [field.key as string]: event.target.value })} />
            : <span className="machine-muted">{field.key ? "" : "только просмотр"}</span>}</td>
        </tr>)}</tbody>
      </table>}
      {changes.length > 0 && (confirming === "settings"
        ? <Confirm state={control?.state ?? null} busy={busy} onNo={() => setConfirming("")} onYes={() => void writeSettings()}
          lines={changes.map(([key, value]) => {
            const field = fields.get(key);
            const after = field?.kind === "mode" ? MODES.find(([code]) => code === value)?.[1] ?? value : value || "пусто";
            return `${field?.label ?? key}: ${field ? shown(field) : EMPTY} → ${after}${field?.unit && field.kind !== "mode" ? ` ${field.unit}` : ""}`;
          })} />
        : <div className="machine-actions">
          <button className="primary small" disabled={!canWrite || busy} onClick={() => setConfirming("settings")}>
            Записать на станок ({changes.length})
          </button>
          <button className="secondary small" disabled={busy} onClick={() => setEdits({})}>Сбросить</button>
        </div>)}
    </div>}

    {control && <Correction inventory={inventory} control={control} plan={plan} onDone={setMessage} />}
  </section>;
}

/** «Правка после пробы»: наладчик говорит, какой это случай (владелец 19.11.2026). */
function Correction({ inventory, control, plan, onDone }: {
  inventory: string; control: MachineControl; plan: TransferPlan | null;
  onDone: (message: { tone: "ok" | "danger"; text: string }) => void;
}) {
  const [parameter, setParameter] = useState("strip_front_mm");
  const [kind, setKind] = useState<CorrectionCase>("machine");
  const [measured, setMeasured] = useState("");
  const [offset, setOffset] = useState("");
  const [etkValue, setEtkValue] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const stored = control.offsets.find((item) => item.key === parameter);
  const row = plan?.rows.find((item) => item.key === parameter);
  const etkAllowed = !!stored?.etk;
  useEffect(() => { if (!etkAllowed) setKind("machine"); }, [etkAllowed]);

  // The suggestion: what the machine lacked against the ETK size goes into its correction.
  useEffect(() => {
    const got = decimal(measured);
    const nominal = row?.nominal ? decimal(row.nominal) : null;
    const old = stored ? decimal(stored.offset) ?? 0 : 0;
    if (got !== null && nominal !== null) setOffset(comma(old + nominal - got));
  }, [measured, row?.nominal, stored?.offset]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setBusy(true);
    try {
      const answer = await machineControlApi.correct(inventory, {
        parameter, case: kind, measured: measured || undefined, note: note || undefined,
        offset: kind !== "etk" ? offset : undefined, etk_value: kind !== "machine" ? etkValue : undefined,
        group_id: kind !== "machine" ? plan?.group_id : undefined,
      });
      const parts = [answer.offset !== null ? `поправка станка ${answer.offset} мм` : "",
        ...answer.etk.map((item) => `ЭТК ${item.project}, ревизия ${item.revision}`)].filter(Boolean);
      onDone({ tone: "ok", text: `Правка записана: ${parts.join("; ") || "без изменений"}` });
      setMeasured(""); setNote("");
    } catch (reason) {
      onDone({ tone: "danger", text: failureText(reason, "Не удалось записать правку") });
    } finally {
      setBusy(false);
    }
  };

  return <div className="machine-block">
    <h4>Правка после пробы</h4>
    <div className="machine-form">
      <label><span>Параметр</span>
        <select value={parameter} onChange={(event) => setParameter(event.target.value)}>
          {control.offsets.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select>
      </label>
      <label><span>Получилось, мм</span>
        <input inputMode="decimal" value={measured} onChange={(event) => setMeasured(event.target.value)} />
      </label>
      <fieldset className="machine-cases"><legend>Что не так</legend>
        {CASES.map(([value, text]) => <label key={value}>
          <input type="radio" name="machine-case" value={value} checked={kind === value}
            disabled={value !== "machine" && !etkAllowed} onChange={() => setKind(value)} />{text}
        </label>)}
      </fieldset>
      {kind !== "etk" && <label><span>Поправка станка, мм</span>
        <input inputMode="decimal" value={offset} onChange={(event) => setOffset(event.target.value)}
          placeholder={stored ? `сейчас ${stored.offset}` : ""} />
      </label>}
      {kind !== "machine" && <label><span>Верный размер ЭТК, мм</span>
        <input inputMode="decimal" value={etkValue} onChange={(event) => setEtkValue(event.target.value)}
          placeholder={row?.nominal ? `сейчас ${row.nominal}` : ""} />
      </label>}
      <label className="wide"><span>Комментарий</span>
        <input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} />
      </label>
    </div>
    {kind !== "machine" && !plan && <p className="warning-inline">Выберите задание резки выше — правится его ЭТК</p>}
    {!etkAllowed && <p className="machine-muted">В ЭТК с карточки правится только зачистка начала и конца</p>}
    <div className="machine-actions">
      <button className="primary small" disabled={busy || !control.can_edit || (kind !== "machine" && !plan)
        || (kind !== "etk" && !offset.trim()) || (kind !== "machine" && !etkValue.trim())}
        onClick={() => void save()}>Записать правку</button>
    </div>
    <table className="machine-table" aria-label="Поправки станка">
      <thead><tr><th>Поправка станка</th><th>Значение</th><th>Почему</th><th>Кто и когда</th></tr></thead>
      <tbody>{control.offsets.map((item) => <tr key={item.key}>
        <th scope="row">{item.label}</th>
        <td>{item.offset === "0,00" ? EMPTY : `${item.offset} мм`}</td>
        <td>{item.reason || EMPTY}</td>
        <td>{item.updated_at ? `${item.updated_by} · ${formatMoment(item.updated_at)}` : EMPTY}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}
