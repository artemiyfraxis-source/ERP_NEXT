import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { failureText, requestsApi, procurementApi, type ProcurementLine } from "./api";
import { formatDay, formatNumber, formatQty, NBSP, unitLabel } from "./format";
import { useInternalProjects } from "./internalProjects";
import { useLineHistory } from "./LineHistory";
import { openPlace, urlParam } from "./navigation";
import {
  requestsViewApi, type RbStatus, type RqBatch, type RqCounts, type RqLine, type RqScope, type RqView,
} from "./procurementRequestsApi";
import {
  EMPTY_GROUPS, GROUP_LABELS, RB_LABELS, RB_TONES, groupLines, orderedCounts, planSum, projectTitle, quantityInput,
  rangeIds, readGroupBy, setMany, shownOrder, writeGroupBy, type GroupBy,
} from "./procurementRequestsLogic";
import { ProcurementStockPicker } from "./ProcurementStockPicker";
import { SuggestPanel, type ChosenLine } from "./RfqPool";
import { StatusChip } from "./StatusChip";

/* Roller_Buyer's «Заявки» (RequestsPage + RequestLinesTable) in «Лист закупки».
   Project → request (the lines of one batch, НАЗНАЧЕНИЕ) → lines; «Ход закупки» chips per project and per request;
   lines grouped by type / plan from the calculation / last purchase; selection by group, Shift-range and drag (own new
   and requote lines only); «Запросить КП» and «Отменить выбранные». Several buyers: «Мои» is one's own lines and the
   unclaimed «Новый» ones, «Все» is read only. */

const money = (value: string | number | null | undefined) =>
  value === null || value === undefined || value === "" ? "—" : `${formatNumber(value, { digits: 2, minDigits: 2 })}${NBSP}₽`;

export function StatusChips({ counts, single = false }: { counts: RqCounts; single?: boolean }) {
  return (
    <span className="rq-chips">
      {orderedCounts(counts).map(([status, count]) => (
        <StatusChip key={status} tone={RB_TONES[status]}>{RB_LABELS[status]}{single ? "" : ` ${count}`}</StatusChip>
      ))}
    </span>
  );
}

function LineChip({ status }: { status: RbStatus }) {
  return <StatusChip tone={RB_TONES[status]}>{RB_LABELS[status]}</StatusChip>;
}

/** Keeps `&project=` in the address bar so the view can be linked and survives a reload; `&line=` is used once. */
function syncAddress(project: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("tab", "requests");
  if (project) url.searchParams.set("project", project);
  else url.searchParams.delete("project");
  url.searchParams.delete("line");
  window.history.replaceState(window.history.state, "", url);
}

export function ProcurementRequests({ readOnly = false }: { readOnly?: boolean }) {
  useInternalProjects(); // «ВП-001 · Название» in the project names
  const history = useLineHistory();
  const [scope, setScope] = useState<RqScope>(readOnly ? "all" : "mine");
  const [project, setProject] = useState(() => urlParam("project"));
  const focusLine = useRef<string | null>(urlParam("line") || null);
  const [highlight, setHighlight] = useState<string | null>(focusLine.current);
  const [view, setView] = useState<RqView | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [linesOf, setLinesOf] = useState<Record<string, RqLine[]>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [groupBy, setGroupBy] = useState<GroupBy>(readGroupBy);
  const [asking, setAsking] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("не закупаем");
  const [busy, setBusy] = useState(false);
  const openRef = useRef(open);
  openRef.current = open;

  useEffect(() => writeGroupBy(groupBy), [groupBy]);
  useEffect(() => syncAddress(project), [project]);

  const loadBatch = useCallback(async (key: string) => {
    try {
      const result = await requestsViewApi.batch(scope, key);
      setLinesOf((current) => ({ ...current, [key]: result.lines }));
    } catch (reason) {
      setError(failureText(reason, "Позиции заявки не загрузились"));
    }
  }, [scope]);

  const load = useCallback(async () => {
    try {
      const result = await requestsViewApi.view({ scope, project: project || undefined, line: focusLine.current ?? undefined });
      setView(result);
      setError("");
      const focus = focusLine.current ? result.focus : null;
      focusLine.current = null;
      const keys = new Set(openRef.current);
      if (focus) {
        keys.add(focus.batch);
        setOpen(keys);
        if (project && project !== focus.project) setProject(focus.project);
      }
      await Promise.all([...keys].map((key) => loadBatch(key)));
    } catch (reason) {
      setError(failureText(reason, "«Заявки» не загрузились"));
    }
  }, [scope, project, loadBatch]);

  useEffect(() => { void load(); }, [load]);

  // A `&line=` link: once its batch is in, the row scrolls into view.
  useEffect(() => {
    if (!highlight) return;
    const row = document.getElementById(`rq-line-${highlight}`);
    if (row && typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "center" });
  }, [highlight, linesOf]);

  const known = useMemo(() => {
    const map = new Map<string, RqLine>();
    for (const lines of Object.values(linesOf)) for (const line of lines) map.set(line.id, line);
    return map;
  }, [linesOf]);
  // A line that left «к запросу» (taken into a request, cancelled, another scope) leaves the selection too.
  useEffect(() => {
    setSelected((current) => {
      const kept = [...current].filter((id) => known.get(id)?.selectable);
      return kept.length === current.size ? current : new Set(kept);
    });
  }, [known]);

  const chosen: ChosenLine[] = useMemo(
    () => [...selected].map((id) => ({ id, name: known.get(id)?.name ?? id, legacy_id: known.get(id)?.legacy_id ?? "" })),
    [selected, known],
  );
  const chosenIds = useMemo(() => chosen.map((line) => line.id), [chosen]);

  const toggleBatch = (key: string) => {
    const next = new Set(open);
    if (next.has(key)) next.delete(key);
    else {
      next.add(key);
      if (!linesOf[key]) void loadBatch(key);
    }
    setOpen(next);
  };

  async function act(run: () => Promise<string>) {
    setBusy(true);
    setError("");
    try {
      setNotice(await run());
    } catch (reason) {
      setError(failureText(reason, "Не получилось"));
    } finally {
      setBusy(false);
      await load();
    }
  }

  async function cancelSelected() {
    const lines = [...selected].map((id) => known.get(id)).filter((line): line is RqLine => Boolean(line));
    await act(async () => {
      const result = await requestsViewApi.cancelMany(lines.map((line) => ({ id: line.id, version: line.version })), reason.trim());
      setSelected(new Set());
      setCancelling(false);
      const skipped = result.skipped.map((item) => `${known.get(item.id)?.legacy_id ?? item.id} — ${item.reason}`);
      return `Отменено строк: ${result.cancelled.length}` + (skipped.length ? `. Не отменены: ${skipped.join("; ")}` : "");
    });
  }

  const projects = view?.projects ?? [];
  const current = projects.find((item) => item.key === project) ?? null;
  const totals = useMemo(() => {
    const counts: RqCounts = {};
    for (const item of projects) for (const [status, count] of Object.entries(item.counts) as Array<[RbStatus, number]>) counts[status] = (counts[status] ?? 0) + count;
    return counts;
  }, [projects]);
  const batches = view?.batches ?? [];
  const canAct = !readOnly && scope === "mine";

  return (
    <section className="page procurement-requests">
      {history.node}
      <div className="filters rq-toolbar">
        <label className="rq-project">Проект
          <select aria-label="Проект" value={project} onChange={(event) => setProject(event.target.value)}>
            <option value="">Все проекты</option>
            {project && !current && <option value={project}>{project}</option>}
            {projects.map((item) => (
              <option key={item.key} value={item.key}>{projectTitle(item)} · {item.lines} поз.</option>
            ))}
          </select>
        </label>
        {!readOnly && (
          <div className="seg-tabs" role="tablist" aria-label="Чьи позиции">
            <button type="button" role="tab" aria-selected={scope === "mine"} className={scope === "mine" ? "on" : undefined} onClick={() => { setScope("mine"); setSelected(new Set()); setLinesOf({}); }}>Мои</button>
            <button type="button" role="tab" aria-selected={scope === "all"} className={scope === "all" ? "on" : undefined} onClick={() => { setScope("all"); setSelected(new Set()); setLinesOf({}); }}>Все</button>
          </div>
        )}
        <span className="rq-progress" aria-label="Ход закупки">
          <span className="muted">Ход закупки:</span> <StatusChips counts={current ? current.counts : totals} />
        </span>
        <span className="spacer" />
        {!readOnly && (
          <button
            type="button" className="secondary small"
            title="Оформить заявку — проект подставится"
            onClick={() => openPlace("newrequest", current && current.kind !== "none" ? { project: current.value } : {})}
          >Новая заявка</button>
        )}
      </div>
      {scope === "all" && <p className="page-sub">{readOnly ? "Заявки открыты вам только на просмотр." : "Все закупщики — только посмотреть; выбирать и менять можно в «Мои»."}</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      {!error && view === null && <div className="empty">Загружаю…</div>}
      {view !== null && batches.length === 0 && (
        <div className="empty">{scope === "mine" ? "Ваших позиций и неразобранных новых нет — посмотрите «Все» или оформите заявку." : "Заявок нет."}</div>
      )}

      {asking && chosen.length > 0 && (
        <section className="panel rfq-pool" aria-label="Новый запрос КП">
          <SuggestPanel
            lineIds={chosenIds}
            lines={chosen}
            onBack={() => setAsking(false)}
            onCreated={(id) => openPlace("procurement", { tab: "rfq", rfq: id, letters: "1" })}
          />
        </section>
      )}

      {view !== null && view.batches_total > batches.length && (
        <p className="page-sub">Показаны последние {batches.length} заявок из {view.batches_total} — выберите проект, чтобы увидеть все его заявки.</p>
      )}
      {batches.length > 0 && (
        <div className="data-table">
          <div className="table-wrap">
            <table className="grid wide rq-batches" aria-label="Заявки">
              <thead>
                <tr>
                  <th aria-label="Открыть" />
                  <th className="grow">Заявка</th>
                  <th>Проект</th>
                  <th>Инициатор</th>
                  <th>Поступила</th>
                  <th>Нужно к</th>
                  <th className="num">Позиций</th>
                  <th>Ход закупки</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <BatchRow
                    key={batch.key} batch={batch} open={open.has(batch.key)} onToggle={() => toggleBatch(batch.key)}
                    lines={linesOf[batch.key]} groupBy={groupBy} onGroupBy={setGroupBy}
                    selected={selected} onSet={(ids, on) => setSelected((current) => setMany(current, ids, on))}
                    canAct={canAct} showBuyer={scope === "all"} busy={busy} highlight={highlight} onHighlight={setHighlight}
                    historyOpen={history.enabled ? history.open : undefined}
                    act={act}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Hidden while the supplier step is open — that panel carries the one primary action then (RB). */}
      {canAct && selected.size > 0 && !asking && (
        <div className="bulk-bar rfq-pool-bar rq-bulk" role="toolbar" aria-label="Выбранные строки">
          <span>Выбрано строк: {selected.size}</span>
          <span className="spacer" />
          <button type="button" className="secondary small" onClick={() => { setSelected(new Set()); setCancelling(false); }}>Снять выбор</button>
          {cancelling ? (
            <>
              <label className="rq-reason">Причина (видна в истории строки)
                <input aria-label="Причина отмены" value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
              </label>
              <button type="button" className="primary small danger" disabled={busy || !reason.trim()} onClick={() => void cancelSelected()}>Подтвердить отмену</button>
              <button type="button" className="secondary small" onClick={() => setCancelling(false)}>Не отменять</button>
            </>
          ) : (
            <button
              type="button" className="secondary small" disabled={busy}
              title="Например, всю «Маркировку»: отметьте группу галочкой в заголовке и отмените разом"
              onClick={() => setCancelling(true)}
            >Отменить выбранные</button>
          )}
          {!cancelling && (
            <button type="button" className="primary small" onClick={() => setAsking(true)}>Запросить КП ({selected.size})</button>
          )}
        </div>
      )}
    </section>
  );
}

function BatchRow({ batch, open, onToggle, lines, groupBy, onGroupBy, selected, onSet, canAct, showBuyer, busy, highlight, onHighlight, historyOpen, act }: {
  batch: RqBatch; open: boolean; onToggle: () => void; lines: RqLine[] | undefined;
  groupBy: GroupBy; onGroupBy: (value: GroupBy) => void;
  selected: ReadonlySet<string>; onSet: (ids: string[], on: boolean) => void;
  canAct: boolean; showBuyer: boolean; busy: boolean; highlight: string | null; onHighlight: (id: string | null) => void;
  historyOpen?: (lineId: string, title: string) => void;
  act: (run: () => Promise<string>) => Promise<void>;
}) {
  return (
    <>
      <tr className={open ? "clickable open" : "clickable"} onClick={onToggle} aria-expanded={open}>
        <td className="rq-toggle">{open ? "▾" : "▸"}</td>
        <td className="text"><strong>{batch.designation}</strong></td>
        <td>{projectTitle(batch.project)}</td>
        <td>{batch.initiators.join(", ")}</td>
        <td>{formatDay(batch.request_date)}</td>
        <td>{formatDay(batch.planned_date)}</td>
        <td className="num">
          {batch.lines}
          {batch.selectable > 0 && <small className="rq-sub" title="Свои новые позиции и перезапрос — можно запросить КП"> к запросу {batch.selectable}</small>}
          {batch.to_take > 0 && <small className="rq-sub" title="Неразобранные «Новый» — возьмите в работу"> взять {batch.to_take}</small>}
        </td>
        <td><StatusChips counts={batch.counts} single={batch.lines === 1} /></td>
      </tr>
      {open && (
        <tr className="detail">
          <td colSpan={8}>
            <div className="detail-body">
              {lines === undefined ? <div className="empty">Загружаю…</div> : (
                <RequestLinesTable
                  lines={lines} groupBy={groupBy} onGroupBy={onGroupBy} selected={selected} onSet={onSet}
                  canAct={canAct} showBuyer={showBuyer} busy={busy} highlight={highlight} onHighlight={onHighlight}
                  historyOpen={historyOpen} act={act}
                />
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

type Panel = { id: string; kind: "split" | "cancel" | "stock" };

/** RB `RequestLinesTable`: grouped lines, a checkbox per group and for all, Shift+click for a range, a drag down the
 *  checkbox column; each line with the calculation's plan and the last real purchase — the price to beat. */
export function RequestLinesTable({ lines, groupBy, onGroupBy, selected, onSet, canAct, showBuyer = false, busy, highlight, onHighlight, historyOpen, act }: {
  lines: RqLine[]; groupBy: GroupBy; onGroupBy: (value: GroupBy) => void;
  selected: ReadonlySet<string>; onSet: (ids: string[], on: boolean) => void;
  canAct: boolean; showBuyer?: boolean; busy: boolean; highlight: string | null; onHighlight: (id: string | null) => void;
  historyOpen?: (lineId: string, title: string) => void;
  act: (run: () => Promise<string>) => Promise<void>;
}) {
  const groups = useMemo(() => groupLines(lines, groupBy), [lines, groupBy]);
  const order = useMemo(() => shownOrder(groups), [groups]);
  const lastClicked = useRef<string | null>(null);
  const drag = useRef<{ on: boolean; moved: boolean } | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [splitQty, setSplitQty] = useState("");
  const [reason, setReason] = useState("");

  // A drag ends wherever the button is released.
  useEffect(() => {
    const stop = () => { drag.current = null; };
    window.addEventListener("pointerup", stop);
    return () => window.removeEventListener("pointerup", stop);
  }, []);

  if (lines.length === 0) return <div className="empty">В заявке нет позиций</div>;
  const pickable = canAct ? order.filter((line) => line.selectable) : [];
  const allOn = pickable.length > 0 && pickable.every((line) => selected.has(line.id));

  function clickBox(line: RqLine, event: MouseEvent) {
    const on = !selected.has(line.id);
    const range = event.shiftKey ? rangeIds(order, lastClicked.current, line.id) : null;
    onSet(range ?? [line.id], on);
    lastClicked.current = line.id;
  }
  function boxDown(line: RqLine) {
    drag.current = { on: !selected.has(line.id), moved: false };
    lastClicked.current = line.id;
  }
  function boxEnter(line: RqLine, event: PointerEvent) {
    const state = drag.current;
    if (!state || event.buttons !== 1 || !line.selectable) return;
    if (!state.moved) {
      // The row the drag started on follows too (its own click never fires once the pointer left it).
      const start = order.find((item) => item.id === lastClicked.current);
      if (start?.selectable) onSet([start.id], state.on);
      state.moved = true;
    }
    onSet([line.id], state.on);
    lastClicked.current = line.id;
  }
  const togglePanel = (id: string, kind: Panel["kind"]) => {
    setPanel((current) => (current?.id === id && current.kind === kind ? null : { id, kind }));
    setSplitQty("");
    setReason("");
  };
  const columns = 7 + (canAct ? 1 : 0) + (showBuyer ? 1 : 0);

  return (
    <div className="rq-lines">
      <div className="rq-lines-tools">
        <label>Группировать{" "}
          <select aria-label="Группировать" value={groupBy} onChange={(event) => onGroupBy(event.target.value as GroupBy)}>
            {(Object.keys(GROUP_LABELS) as GroupBy[]).map((key) => <option key={key} value={key}>{GROUP_LABELS[key]}</option>)}
          </select>
        </label>
        {canAct && <span className="muted">Отметить: галочка у группы, Shift+клик — диапазон, или зажмите на галочке и проведите вниз</span>}
      </div>
      <table className="grid wide rq-lines-table" aria-label="Позиции заявки">
        <thead>
          <tr>
            {canAct && (
              <th className="rq-check">
                <input
                  type="checkbox" aria-label="Выбрать все строки для запроса КП" disabled={pickable.length === 0}
                  checked={allOn} onChange={() => onSet(pickable.map((line) => line.id), !allOn)}
                />
              </th>
            )}
            <th className="grow">Позиция</th>
            <th className="num">Кол-во</th>
            <th title="Цена за единицу с НДС и где её взяли в расчёте проекта (колонка «Ссылка»). От неё отталкиваемся: КП дороже плана — повод торговаться.">План из расчёта</th>
            <th title="Последняя закупка этой позиции: оплаченная строка закупки или УПД">Покупали</th>
            <th title="Свободный остаток позиции на складе: баланс минус резерв. «Со склада» закроет строку им вместо закупки; если не хватает, остаток останется к закупке.">На складе</th>
            {showBuyer && <th>Закупщик</th>}
            <th>Статус</th>
            <th aria-label="Действия" />
          </tr>
        </thead>
        <tbody>
          {groups.map(([name, rows]) => {
            const groupPick = canAct ? rows.filter((line) => line.selectable) : [];
            const groupOn = groupPick.length > 0 && groupPick.every((line) => selected.has(line.id));
            const sum = planSum(rows);
            return (
              <Fragment key={name || "—"}>
                {groupBy !== "none" && (
                  <tr className="group-row">
                    {canAct && (
                      <td className="rq-check">
                        <input
                          type="checkbox" aria-label={`Выбрать группу ${name || EMPTY_GROUPS[groupBy]}`}
                          disabled={groupPick.length === 0} checked={groupOn}
                          onChange={() => onSet(groupPick.map((line) => line.id), !groupOn)}
                        />
                      </td>
                    )}
                    <td colSpan={columns - (canAct ? 1 : 0)}>
                      <strong>{name || EMPTY_GROUPS[groupBy]}</strong>
                      <span className="muted">
                        {" "}· {rows.length} поз.{canAct && groupPick.length !== rows.length ? `, к запросу ${groupPick.length}` : ""}
                        {sum > 0 ? ` · по плану ${money(Math.round(sum))}` : ""}
                      </span>
                    </td>
                  </tr>
                )}
                {rows.map((line) => (
                  <LineRows
                    key={line.id} line={line} canAct={canAct} showBuyer={showBuyer} busy={busy} picked={selected.has(line.id)}
                    highlighted={highlight === line.id} columns={columns} panel={panel?.id === line.id ? panel.kind : null}
                    onBox={(event) => clickBox(line, event)} onBoxDown={() => line.selectable && boxDown(line)}
                    onBoxEnter={(event) => boxEnter(line, event)} onPanel={(kind) => togglePanel(line.id, kind)}
                    splitQty={splitQty} onSplitQty={setSplitQty} reason={reason} onReason={setReason}
                    onClosePanel={() => setPanel(null)} onHighlight={onHighlight} historyOpen={historyOpen} act={act}
                  />
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function asStockLine(line: RqLine): ProcurementLine {
  // The stock picker reads only these fields of a procurement line.
  return {
    id: line.id, version: line.version, legacy_id: line.legacy_id, status: line.status, paid_at: null,
    calculated_quantity: line.quantity, calculated_unit: line.unit, to_buy: line.to_buy, stock_covered: line.stock_covered,
  } as unknown as ProcurementLine;
}

function LineRows({
  line, canAct, showBuyer, busy, picked, highlighted, columns, panel, onBox, onBoxDown, onBoxEnter, onPanel, splitQty, onSplitQty,
  reason, onReason, onClosePanel, onHighlight, historyOpen, act,
}: {
  line: RqLine; canAct: boolean; showBuyer: boolean; busy: boolean; picked: boolean; highlighted: boolean; columns: number;
  panel: Panel["kind"] | null;
  onBox: (event: MouseEvent) => void; onBoxDown: () => void; onBoxEnter: (event: PointerEvent) => void;
  onPanel: (kind: Panel["kind"]) => void;
  splitQty: string; onSplitQty: (value: string) => void; reason: string; onReason: (value: string) => void;
  onClosePanel: () => void; onHighlight: (id: string | null) => void;
  historyOpen?: (lineId: string, title: string) => void;
  act: (run: () => Promise<string>) => Promise<void>;
}) {
  const mine = canAct && line.mine;
  const classes = [line.rb_status === "cancelled" ? "muted" : "", picked ? "rq-picked" : "", highlighted ? "rq-highlight" : ""].filter(Boolean).join(" ");
  const span = columns - (canAct ? 1 : 0);
  const sub = (content: ReactNode, className = "") => (
    <tr className={`rq-sub-row ${className}`.trim()}>
      {canAct && <td />}
      <td colSpan={span}>{content}</td>
    </tr>
  );
  return (
    <>
      <tr id={`rq-line-${line.id}`} className={classes || undefined} onClick={highlighted ? () => onHighlight(null) : undefined}>
        {canAct && (
          <td className="rq-check" onPointerDown={onBoxDown} onPointerEnter={onBoxEnter}>
            {line.selectable && (
              <input type="checkbox" aria-label={`Выбрать ${line.legacy_id}`} checked={picked} onClick={onBox} onChange={() => undefined} />
            )}
          </td>
        )}
        <td className="text">
          <strong>{line.name}</strong>
          <small>
            {line.legacy_id}
            {line.article && line.article.trim().toLowerCase() !== line.name.trim().toLowerCase() ? ` · ${line.article}` : ""}
          </small>
        </td>
        <td className="num">
          {formatQty(line.quantity, line.unit)}
          {Number(line.stock_covered) > 0 && <small className="rq-sub">со склада {formatQty(line.stock_covered, line.unit)}</small>}
        </td>
        <td>
          {line.plan ? (
            <span className="rq-plan">
              {line.plan.price_with_vat !== null && <b>{money(line.plan.price_with_vat)}</b>}
              {line.plan.label && (line.plan.link
                ? <a href={line.plan.link} target="_blank" rel="noreferrer" title={line.plan.link}>{line.plan.label} ↗</a>
                : <span className="muted">{line.plan.label}</span>)}
            </span>
          ) : <span className="muted">—</span>}
        </td>
        <td>
          {line.last ? (
            <span className="rq-plan" title={`последняя закупка ${formatDay(line.last.date)}${line.last.source === "upd" ? " (по УПД)" : ""}`}>
              <b>{money(line.last.price)}{line.last.unit && unitLabel(line.last.unit) !== unitLabel(line.unit) ? `/${unitLabel(line.last.unit)}` : ""}</b>
              <span className="muted">{[line.last.supplier, formatDay(line.last.date)].filter(Boolean).join(" · ")}</span>
            </span>
          ) : <span className="muted">—</span>}
        </td>
        <td>
          {line.free_stock ? (
            <span className="rq-plan">
              <b>свободно {formatQty(line.free_stock, line.unit)}</b>
              {mine && line.can_cover && (
                <button type="button" className="link-button" disabled={busy} onClick={() => onPanel("stock")}>
                  {Number(line.free_stock) >= Number(line.to_buy) ? "Со склада" : `Со склада ${formatQty(line.free_stock, line.unit)}, остаток купить`}
                </button>
              )}
            </span>
          ) : <span className="muted">—</span>}
        </td>
        {showBuyer && <td>{line.buyer ?? <span className="muted">не взята</span>}</td>}
        <td>
          <LineChip status={line.rb_status} />
          {line.rfq && (
            <button
              type="button" className="link-button rq-rfq" title="Открыть запрос КП"
              onClick={() => openPlace("procurement", { tab: "rfq", rfq: line.rfq!.id })}
            >{line.rfq.number}</button>
          )}
        </td>
        <td className="rq-actions"><div className="rq-actions-in">
          {historyOpen && <button type="button" className="secondary small" onClick={() => historyOpen(line.id, line.legacy_id)}>История</button>}
          {canAct && line.can_take && (
            <button
              type="button" className="secondary small" disabled={busy}
              onClick={() => void act(async () => {
                const result = await procurementApi.take([line.id]);
                return result.skipped?.length ? `${line.legacy_id} уже взял другой закупщик` : `${line.legacy_id} взята в работу`;
              })}
            >Взять в работу</button>
          )}
          {mine && line.can_split && <button type="button" className="secondary small" disabled={busy} onClick={() => onPanel("split")}>Разделить</button>}
          {mine && line.can_cancel && <button type="button" className="secondary small" disabled={busy} onClick={() => onPanel("cancel")}>Отменить</button>}
          {mine && line.can_resend && (
            <button
              type="button" className="secondary small" disabled={busy}
              title="Заявка уйдёт в закупку новой строкой со следующей ревизией; отменённая останется в истории"
              onClick={() => {
                if (!window.confirm("Перенести заявку в закупку повторно? Появится новая строка со следующей ревизией, прежняя останется.")) return;
                void act(async () => {
                  await requestsApi.handoff([line.source_request_id], true);
                  return `${line.legacy_id}: заявка передана в закупку повторно — новая строка появится в очереди`;
                });
              }}
            >Повторно в закупку</button>
          )}
        </div></td>
      </tr>
      {line.rb_status === "cancelled" && line.cancel_reason && sub(<>Причина отмены: {line.cancel_reason}</>, "muted")}
      {line.rb_status === "requote" && line.requote_from && sub(<>Перезапрос. Прошлый запрос: {line.requote_from}</>, "muted rq-requote")}
      {panel === "split" && sub(
        <form
          className="rq-form"
          onSubmit={(event) => {
            event.preventDefault();
            const quantity = quantityInput(splitQty);
            if (!quantity) return;
            onClosePanel();
            void act(async () => {
              const result = await requestsViewApi.splitQty(line.id, line.version, quantity);
              return `${line.legacy_id}: отделено ${formatQty(quantity, line.unit)} в ${result.created.legacy_id}`
                + (result.rfq ? ` — КП из ${result.rfq.rfq_number} у обеих частей` : "");
            });
          }}
        >
          <label>Отделить в новую строку (из {formatQty(line.to_buy, line.unit)})
            <input aria-label="Сколько отделить" value={splitQty} placeholder="например, 300" onChange={(event) => onSplitQty(event.target.value)} />
          </label>
          <button type="submit" className="primary small" disabled={busy || !quantityInput(splitQty)}>Разделить</button>
          <button type="button" className="secondary small" onClick={onClosePanel}>Отмена</button>
          <span className="muted">Например, чтобы часть взять у одного поставщика, а часть у другого: КП по строке останутся у обеих частей.</span>
        </form>,
      )}
      {panel === "cancel" && sub(
        <form
          className="rq-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!reason.trim()) return;
            onClosePanel();
            void act(async () => {
              const result = await requestsViewApi.cancelMany([{ id: line.id, version: line.version }], reason.trim());
              return result.cancelled.length ? `${line.legacy_id} отменена` : `${line.legacy_id} не отменена: ${result.skipped[0]?.reason ?? ""}`;
            });
          }}
        >
          <label>Причина отмены
            <input aria-label="Причина отмены" value={reason} maxLength={500} onChange={(event) => onReason(event.target.value)} />
          </label>
          <button type="submit" className="primary small danger" disabled={busy || !reason.trim()}>Подтвердить отмену</button>
          <button type="button" className="secondary small" onClick={onClosePanel}>Отмена</button>
        </form>,
      )}
      {panel === "stock" && sub(
        <ProcurementStockPicker
          line={asStockLine(line)} disabled={busy} onClose={onClosePanel}
          onDone={(message) => { onClosePanel(); void act(async () => message); }}
        />,
      )}
    </>
  );
}
