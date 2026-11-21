import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureText } from "./api";
import { formatNumber } from "./format";
import { cuttingQueueApi, type CuttingQueue as Queue, type CuttingQueueMachine, type CuttingQueueRow } from "./cuttingQueueApi";
import "./cuttingQueue.css";

/* «Очередь резки» (владелец 20.11): один список на станок через все проекты; оператор идёт строка за строкой,
   после каждой видит «Готово: проект · КБ · шт». Нехватка данных ЭТК — замечание в строке, не блокировка. */

export const REFRESH_MS = 60_000;
const NO_MACHINE = "none";
const ALL_MACHINES = "all";
const STRIP_MODE = { full: "полная", partial: "частичная" } as const;
const MARKING = { full: "по всей длине", ends: "по краям" } as const;

const machineValue = (machine: CuttingQueueMachine) => machine.machine_id ?? NO_MACHINE;
const num = (value: string | number | null | undefined) => formatNumber(value, { digits: 2 });
const two = (value: number) => String(value).padStart(2, "0");

function marking(row: CuttingQueueRow): string {
  if (row.marking_mode) return MARKING[row.marking_mode];
  return row.laser_mark ? "лазер" : "";
}

function Dash({ value }: { value: string }) { return value ? <>{value}</> : <span className="cq-empty">—</span>; }

export interface CuttingQueueProps {
  /** Сдаёт за другого сотрудника (мастер в «Заданиях»): уходит как `on_behalf_of`. */
  onBehalfOf?: string;
  /** Только просмотр: без «Готово» (планирование, чужой вид). */
  readOnly?: boolean;
  /** В «Заданиях» оператора: ничего не показывать, пока резать нечего. */
  hideWhenEmpty?: boolean;
  /** Сообщает, есть ли что резать (скрыть пустую подсказку «Заданий»). */
  onHasWork?: (hasWork: boolean) => void;
  /** Операции, которые очередь показывает строками (их карточки в «Заданиях» скрывают: сдают здесь). */
  onRows?: (operationIds: Set<string>) => void;
  /** «Готово» принято: страница перечитывает свои задания. */
  onChanged?: () => void;
  /** Карточка станка: очередь одного агрегата (его строки и «любой свободный» его модели), без выбора станка;
   *  «Готово» уходит с `equipment_id`. Без свойства экран работает как раньше. */
  equipmentId?: string;
}

export function CuttingQueue({ onBehalfOf = "", readOnly = false, hideWhenEmpty = false, onHasWork, onRows, onChanged, equipmentId }: CuttingQueueProps = {}) {
  const [machines, setMachines] = useState<CuttingQueueMachine[] | null>(null);
  const fixed = useMemo<CuttingQueueMachine[] | null>(() => equipmentId
    ? [{ machine_id: equipmentId, machine_name: "Этот станок", rows: 0, left: "0", projects: 0 }] : null, [equipmentId]);
  const [selected, setSelected] = useState("");
  const [queue, setQueue] = useState<Queue | null>(null);
  const [error, setError] = useState("");
  const [loadedAt, setLoadedAt] = useState("");
  const [busy, setBusy] = useState("");
  const [last, setLast] = useState<string[]>([]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const retry = useRef<Record<string, { payload: string; key: string }>>({});
  const ticket = useRef(0);
  useEffect(() => { if (machines && !error) onHasWork?.(machines.length > 0); }, [machines, error, onHasWork]);
  // Для скрытия дублей в «Заданиях»: строки всех станков (`machine_id=all`), независимо от выбранного в списке.
  const [allOps, setAllOps] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!onRows || !allOps) return;
    onRows(allOps);
    return () => onRows(new Set());
  }, [allOps, onRows]);
  const wantAll = useRef(false);
  wantAll.current = Boolean(onRows);
  const chosen = useRef("");
  chosen.current = selected;

  const load = useCallback(async () => {
    const mine = ++ticket.current;
    try {
      const list = fixed ?? await cuttingQueueApi.machines();
      if (mine !== ticket.current) return;
      const current = chosen.current;
      const value = list.some((machine) => machineValue(machine) === current) ? current : list.length ? machineValue(list[0]) : "";
      const [result, everything] = await Promise.all([
        equipmentId ? cuttingQueueApi.queueOfUnit(equipmentId)
          : value ? cuttingQueueApi.queue(value === NO_MACHINE ? null : value) : Promise.resolve(null),
        wantAll.current ? cuttingQueueApi.queue(ALL_MACHINES).catch(() => null) : Promise.resolve(null),
      ]);
      if (mine !== ticket.current) return;
      // Прячем карточку только у строк, которые здесь можно сдать: заблокированная строка («Нет ЭТК задания…»,
      // группа на карточке) сдаётся на своей карточке. Нет ответа «Все станки» — ничего не прячем.
      if (wantAll.current) setAllOps(new Set((everything?.rows ?? []).filter((item) => !item.blocked).map((item) => item.operation_id)));
      setMachines(list);
      if (value !== current) setSelected(value);
      setQueue(result);
      setError("");
      const now = new Date();
      setLoadedAt(`${two(now.getHours())}:${two(now.getMinutes())}`);
    } catch (reason) {
      if (mine === ticket.current) {
        setError(failureText(reason, "«Очередь резки» не загрузилась"));
        setMachines((list) => list ?? []);
        if (wantAll.current) setAllOps(new Set());  // не держим устаревший список скрытых карточек
      }
    }
  }, [fixed, equipmentId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  function pick(value: string) {
    chosen.current = value;
    setSelected(value);
    setQueue(null);
    void load();
  }

  async function done(row: CuttingQueueRow) {
    const quantity = Number(quantities[row.key] ?? Math.trunc(Number(row.left)));
    if (!Number.isInteger(quantity) || quantity <= 0) { setError("Укажите целое число штук больше нуля"); return; }
    const payload = `${row.task_id}|${row.operation_id}|${quantity}|${onBehalfOf}`;
    if (retry.current[row.key]?.payload !== payload) retry.current[row.key] = { payload, key: crypto.randomUUID() };
    setBusy(row.key);
    try {
      const result = await cuttingQueueApi.done(row.task_id, row.operation_id, quantity, retry.current[row.key].key, onBehalfOf, equipmentId);
      delete retry.current[row.key];
      setLast((current) => [result.notice, ...current].slice(0, 5));
      setQuantities((current) => { const next = { ...current }; delete next[row.key]; return next; });
      setError("");
      await load();
      onChanged?.();
    } catch (reason) {
      const message = failureText(reason, "");
      await load();
      setError(message || "Не удалось отметить «Готово»");
    } finally {
      setBusy("");
    }
  }

  // Пока список станков грузится, «Загрузка…» (не пустое место): карточки заданий ждут первого ответа очереди.
  if (hideWhenEmpty && !error && machines && !machines.length) return null;
  if (!machines) return error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загрузка…</div>;

  const rows = queue?.rows ?? [];
  const currentKey = rows.find((row) => Number(row.left) > 0 && !row.blocked)?.key;
  const showMachine = selected === ALL_MACHINES;
  const columns = (readOnly ? 11 : 12) + (showMachine ? 1 : 0);
  const now = new Date();
  const today = `${two(now.getDate())}.${two(now.getMonth() + 1)}.${now.getFullYear()}`;

  return <div className="cq">
    {!equipmentId && <h2 className="cq-title">Очередь резки</h2>}
    <div className="cq-head">
      {!equipmentId && <label>Станок{" "}
        <select aria-label="Станок" value={selected} onChange={(event) => pick(event.target.value)} disabled={!machines.length}>
          {machines.map((machine) => <option key={machineValue(machine)} value={machineValue(machine)}>{machine.machine_name} · {machine.rows} строк</option>)}
        </select>
      </label>}
      {readOnly && <span>«Готово» сдаёт оператор в «Заданиях»</span>}
      <span>Сейчас <b>{today}</b>{loadedAt && <> <b>{loadedAt}</b></>} · в очереди <b>{rows.length}</b> строк</span>
      <span className="cq-refresh" aria-live="polite">
        {loadedAt && <>обновлено {loadedAt} · раз в минуту</>}
        <button type="button" className="secondary small" onClick={() => void load()}>Обновить</button>
      </span>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {last.length > 0 && <section className="cq-last" aria-label="Последние">
      <h2>Последние</h2>
      <ul>{last.map((text, index) => <li key={`${index}:${text}`}>{text}</li>)}</ul>
    </section>}
    {!machines.length ? <div className="empty">Нет открытой резки — очередь пуста.</div>
      : !queue ? <div className="empty">Загрузка…</div>
      : !rows.length ? <div className="empty">На этом станке резать нечего.</div>
        : <div className="table-wrap"><table className="grid cq-table">
          <thead><tr>
            <th>Проект</th><th>КБ</th>{showMachine && <th>Станок</th>}<th>Провод</th><th className="num">Длина, мм</th><th className="num">Осталось, шт</th>
            <th>Зачистка А</th><th>Режим А</th><th>Зачистка Б</th><th>Режим Б</th><th>Маркировка</th><th>Замечания</th>{!readOnly && <th>Готово</th>}
          </tr></thead>
          <tbody>{rows.map((row) => {
            const current = row.key === currentKey;
            const value = quantities[row.key] ?? String(Math.trunc(Number(row.left)));
            return <Fragment key={row.key}>
              {row.setup_change && <tr className="cq-setup"><td className="cq-setup-cell" colSpan={columns}><div className="cq-clip" title={row.setup_text}>{row.setup_text}</div></td></tr>}
              <tr className={current ? "cq-current" : undefined} aria-current={current ? "true" : undefined}>
                <td>{row.project}</td>
                <td title={row.group_number ?? undefined}>{row.item}</td>
                {showMachine && <td><Dash value={(row.machines ?? []).map((machine) => machine.machine_name).join(", ")} /></td>}
                <td className="cq-wire"><div className="cq-clip" title={row.wire_title || row.wire_name || row.wire || undefined}><Dash value={row.wire_name || row.wire || ""} /></div></td>
                <td className="num"><Dash value={num(row.length_mm)} /></td>
                <td className="num"><b>{num(row.left)}</b></td>
                <td><Dash value={row.strip_a ?? ""} /></td>
                <td><Dash value={row.strip_a_mode ? STRIP_MODE[row.strip_a_mode] : ""} /></td>
                <td><Dash value={row.strip_b ?? ""} /></td>
                <td><Dash value={row.strip_b_mode ? STRIP_MODE[row.strip_b_mode] : ""} /></td>
                <td><Dash value={marking(row)} /></td>
                <td className="cq-warn">{(() => {
                  const notes = [...(row.blocked ? [row.blocked] : []), ...row.warnings].join("; ");
                  return notes ? <div className="cq-clip" title={notes}>{notes}</div> : null;
                })()}</td>
                {!readOnly && <td className="cq-act">
                  <input type="number" min={1} step={1} inputMode="numeric" aria-label={`Сколько готово: ${row.project} ${row.item}`} value={value}
                    disabled={Boolean(row.blocked) || busy === row.key} onChange={(event) => setQuantities((all) => ({ ...all, [row.key]: event.target.value }))} />
                  <button type="button" className="primary small" disabled={Boolean(row.blocked) || Boolean(busy)} title={row.blocked ?? undefined}
                    onClick={() => void done(row)}>Готово</button>
                </td>}
              </tr>
            </Fragment>;
          })}</tbody>
        </table></div>}
  </div>;
}
