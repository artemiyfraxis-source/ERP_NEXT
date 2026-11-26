import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureText, isAborted } from "./api";
import { formatNumber } from "./format";
import {
  ALL_MACHINES, NO_MACHINE, cuttingQueueApi, rowsOf,
  type CuttingQueue as Queue, type CuttingQueueMachine, type CuttingQueueRow,
} from "./cuttingQueueApi";
import "./cuttingQueue.css";

/* «Очередь резки» (владелец 20.11): один список на станок через все проекты; оператор идёт строка за строкой,
   после каждой видит «Готово: проект · КБ · шт». Нехватка данных ЭТК — замечание в строке, не блокировка.
   Обновление (владелец 23.11 «почему это так висит»): один запрос снимка «Все станки» на обновление, список станка
   делится на месте; не больше одного запроса сразу, скрытая вкладка не опрашивает, при сбое остаются прежние строки,
   неизменная версия не меняет строк. */

export const REFRESH_MS = 60_000;
const STRIP_MODE = { full: "полная", partial: "частичная" } as const;
const MARKING = { full: "по всей длине", ends: "по краям" } as const;

const machineValue = (machine: CuttingQueueMachine) => machine.machine_id ?? NO_MACHINE;
const num = (value: string | number | null | undefined) => formatNumber(value, { digits: 2 });
const two = (value: number) => String(value).padStart(2, "0");
const clock = (at: Date) => `${two(at.getHours())}:${two(at.getMinutes())}`;

function marking(row: CuttingQueueRow): string {
  if (row.marking_mode) return MARKING[row.marking_mode];
  return row.laser_mark ? "лазер" : "";
}

function Dash({ value }: { value: string }) { return value ? <>{value}</> : <span className="cq-empty">—</span>; }

/** Очередь агрегата, которую читает карточка станка (один владелец обновления для карточки и этой таблицы). */
export interface SuppliedCuttingQueue {
  queue: Queue | null;
  /** Ошибка последнего чтения: прежние строки остаются на экране. */
  error?: string;
  /** Время последнего удачного чтения. */
  loadedAt?: Date | null;
}

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
  /** С `equipmentId`: очередь агрегата читает родитель — без своего запроса и таймера. */
  supplied?: SuppliedCuttingQueue;
  /** С `supplied`: «Обновить» и перечитать после «Готово». */
  onRefresh?: () => Promise<unknown> | void;
}

interface Loaded { machines: CuttingQueueMachine[]; queue: Queue }

export function CuttingQueue({
  onBehalfOf = "", readOnly = false, hideWhenEmpty = false, onHasWork, onRows, onChanged, equipmentId, supplied, onRefresh,
}: CuttingQueueProps = {}) {
  const fixed = useMemo<CuttingQueueMachine[] | null>(() => equipmentId
    ? [{ machine_id: equipmentId, machine_name: "Этот станок", rows: 0, left: "0", projects: 0 }] : null, [equipmentId]);
  const external = supplied !== undefined;
  const [own, setOwn] = useState<Loaded | null>(null);
  const [selected, setSelected] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [last, setLast] = useState<string[]>([]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const retry = useRef<Record<string, { payload: string; key: string }>>({});
  const inflight = useRef<AbortController | null>(null);
  const lastGood = useRef(0);
  const chosen = useRef("");
  chosen.current = selected;
  const wantAll = useRef(false);
  wantAll.current = Boolean(onRows);
  // Для скрытия дублей в «Заданиях»: строки всех станков (`machine_id=all`), независимо от выбранного в списке.
  const [allOps, setAllOps] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!onRows || !allOps) return;
    onRows(allOps);
    return () => onRows(new Set());
  }, [allOps, onRows]);

  /** Одно чтение: снимок «Все станки» (или очередь агрегата). `force` — заменить идущее (кнопка, после «Готово»);
   *  без него при идущем чтении ничего не делает (опрос не наслаивается). */
  const load = useCallback(async (force = false) => {
    if (inflight.current) {
      if (!force) return;
      inflight.current.abort();
    }
    const controller = new AbortController();
    inflight.current = controller;
    const { signal } = controller;
    try {
      let result: Loaded;
      let everything: CuttingQueueRow[] | null = null;
      if (equipmentId) {
        const [unit, all] = await Promise.all([
          cuttingQueueApi.queueOfUnit(equipmentId, signal),
          wantAll.current ? cuttingQueueApi.snapshot(signal).catch((reason) => { if (isAborted(reason)) throw reason; return null; })
            : Promise.resolve(null),
        ]);
        result = { machines: fixed ?? [], queue: unit };
        everything = all?.rows ?? null;
      } else {
        const snapshot = await cuttingQueueApi.snapshot(signal);
        result = { machines: snapshot.machines ?? [], queue: snapshot };
        everything = snapshot.rows;
      }
      if (signal.aborted) return;
      // Прячем карточку только у строк, которые здесь можно сдать: заблокированная строка («Нет ЭТК задания…»,
      // группа на карточке) сдаётся на своей карточке. Нет ответа «Все станки» — ничего не прячем.
      if (wantAll.current) setAllOps(new Set((everything ?? []).filter((item) => !item.blocked).map((item) => item.operation_id)));
      // Та же версия — те же строки: экран не перестраивает таблицу.
      setOwn((previous) => previous && previous.queue.version === result.queue.version
        && previous.machines.length === result.machines.length ? previous : result);
      const current = chosen.current;
      if (!equipmentId && !result.machines.some((machine) => machineValue(machine) === current)) {
        const first = result.machines.length ? machineValue(result.machines[0]) : "";
        chosen.current = first;
        setSelected(first);
      }
      setLoadError("");
      lastGood.current = Date.now();
      setLoadedAt(new Date());
    } catch (reason) {
      if (signal.aborted || isAborted(reason)) return;
      setLoadError(failureText(reason, "«Очередь резки» не загрузилась"));
      if (wantAll.current) setAllOps(new Set());  // не держим устаревший список скрытых карточек
    } finally {
      if (inflight.current === controller) inflight.current = null;
    }
  }, [equipmentId, fixed]);

  // Опрос: следующий через минуту после конца предыдущего; скрытая вкладка не опрашивает, при возврате — сразу,
  // если данные старше минуты. Размонтирование отменяет идущее чтение.
  useEffect(() => {
    if (external) return;
    let alive = true;
    let timer: number | undefined;
    const schedule = (delay: number) => {
      window.clearTimeout(timer);
      if (alive && !document.hidden) timer = window.setTimeout(() => void tick(), delay);
    };
    const tick = async () => {
      await load();
      schedule(REFRESH_MS);
    };
    const visibility = () => {
      if (document.hidden) { window.clearTimeout(timer); return; }
      const age = Date.now() - lastGood.current;
      if (age >= REFRESH_MS) void tick();
      else schedule(REFRESH_MS - age);
    };
    setOwn(null);
    void tick();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
      inflight.current?.abort();
      inflight.current = null;
    };
  }, [load, external]);

  const refresh = useCallback(async () => {
    if (external) await onRefresh?.();
    else await load(true);
  }, [external, onRefresh, load]);

  const machines = external ? (fixed ?? []) : own?.machines ?? null;
  const queue = external ? supplied?.queue ?? null : own?.queue ?? null;
  const failure = external ? supplied?.error ?? "" : loadError;
  const shownAt = external ? supplied?.loadedAt ?? null : loadedAt;
  const value = equipmentId ? "" : selected;
  const rows = useMemo(() => !queue ? [] : equipmentId ? queue.rows : rowsOf(queue.rows, value), [queue, equipmentId, value]);

  useEffect(() => { if (machines && !failure) onHasWork?.(machines.length > 0); }, [machines, failure, onHasWork]);

  function pick(next: string) {
    chosen.current = next;
    setSelected(next);
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
      await refresh();
      onChanged?.();
    } catch (reason) {
      const message = failureText(reason, "");
      await refresh();
      setError(message || "Не удалось отметить «Готово»");
    } finally {
      setBusy("");
    }
  }

  // Пока список станков грузится, «Загрузка…» (не пустое место): карточки заданий ждут первого ответа очереди.
  if (hideWhenEmpty && !failure && machines && !machines.length) return null;
  if (!machines) return failure ? <div className="error-banner" role="alert">{failure}</div> : <div className="empty">Загрузка…</div>;

  const currentKey = rows.find((row) => Number(row.left) > 0 && !row.blocked)?.key;
  const showMachine = value === ALL_MACHINES;
  const columns = (readOnly ? 11 : 12) + (showMachine ? 1 : 0);
  const now = new Date();
  const today = `${two(now.getDate())}.${two(now.getMonth() + 1)}.${now.getFullYear()}`;
  const at = shownAt ? clock(shownAt) : "";
  const alerts = [failure && (at ? `${failure} — показано на ${at}` : failure), error].filter(Boolean);

  return <div className="cq">
    {!equipmentId && <h2 className="cq-title">Очередь резки</h2>}
    <div className="cq-head">
      {!equipmentId && <label>Станок{" "}
        <select aria-label="Станок" value={value} onChange={(event) => pick(event.target.value)} disabled={!machines.length}>
          {machines.map((machine) => <option key={machineValue(machine)} value={machineValue(machine)}>{machine.machine_name} · {machine.rows} строк</option>)}
        </select>
      </label>}
      {readOnly && <span>«Готово» сдаёт оператор в «Заданиях»</span>}
      <span>Сейчас <b>{today}</b>{at && <> <b>{at}</b></>} · в очереди <b>{rows.length}</b> строк</span>
      <span className={failure ? "cq-refresh cq-stale" : "cq-refresh"} aria-live="polite">
        {at && <>{failure ? "не обновилось, данные на" : "обновлено"} {at} · раз в минуту</>}
        <button type="button" className="secondary small" onClick={() => void refresh()}>Обновить</button>
      </span>
    </div>
    {alerts.map((text, index) => <div key={index} className="error-banner" role="alert">{text}</div>)}
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
            const amount = quantities[row.key] ?? String(Math.trunc(Number(row.left)));
            return <Fragment key={row.key}>
              {row.setup_change && <tr className="cq-setup"><td className="cq-setup-cell" colSpan={columns}><div className="cq-clip" title={row.setup_text}>{row.setup_text}</div></td></tr>}
              <tr className={current ? "cq-current" : undefined} aria-current={current ? "true" : undefined}>
                <td>{row.project}</td>
                <td title={row.group_number ?? undefined}>{row.item}</td>
                {showMachine && (() => {
                  const names = (row.machines ?? []).map((machine) => machine.machine_name).join(", ");
                  return <td className="cq-machine">{names ? <div className="cq-clip" title={names}>{names}</div> : <Dash value="" />}</td>;
                })()}
                <td className="cq-wire"><div className="cq-clip" title={row.wire_title || row.wire_name || row.wire || undefined}><Dash value={row.wire_name || row.wire || ""} /></div></td>
                <td className="num"><Dash value={num(row.length_mm)} /></td>
                <td className="num"><b>{num(row.left)}</b></td>
                <td><Dash value={row.strip_a ?? ""} /></td>
                <td><Dash value={row.strip_a_mode ? STRIP_MODE[row.strip_a_mode] : ""} /></td>
                <td><Dash value={row.strip_b ?? ""} /></td>
                <td><Dash value={row.strip_b_mode ? STRIP_MODE[row.strip_b_mode] : ""} /></td>
                <td><Dash value={marking(row)} /></td>
                <td className="cq-warn">
                  {row.blocked && <div className="cq-blocked">{row.blocked}</div>}
                  {row.warnings.length > 0 && <div className="cq-clip" title={row.warnings.join("; ")}>{row.warnings.join("; ")}</div>}
                </td>
                {!readOnly && <td className="cq-act">
                  <input type="number" min={1} step={1} inputMode="numeric" aria-label={`Сколько готово: ${row.project} ${row.item}`} value={amount}
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
