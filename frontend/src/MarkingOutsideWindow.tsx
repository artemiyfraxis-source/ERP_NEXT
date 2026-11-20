import { useEffect, useRef, useState } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { countLabel, formatMoment, formatNumber } from "./format";
import { cuttingRows, type CuttingGroup } from "./cuttingGroups";
import { parseSequences } from "./markingApi";
import { markingOutsideApi, type MarkingOutsideAnswer, type MarkingOutsideRequest, type MarkingOutsideRow } from "./markingOutsideApi";

const LABELS = ["этикетку", "этикетки", "этикеток"] as const;
const rowKey = (row: MarkingOutsideRow) => `${row.task_id}:${row.serial}:${row.text}`;

/** «Отметить напечатанные вне ERP» (owner 20.11): numbered labels of the chosen item serials of the group's КБ were
 *  printed outside the ERP — marked, they count as printed and «Печатать оставшиеся» skips them. */
export function MarkingOutsideWindow({ group, close, onChange }: { group: CuttingGroup; close: () => void; onChange: () => void }) {
  const kbs = cuttingRows(group);
  const [serialsText, setSerialsText] = useState("1");
  const [taskId, setTaskId] = useState("");
  const [comment, setComment] = useState("");
  const [shown, setShown] = useState<MarkingOutsideAnswer | null>(null);
  const [shownError, setShownError] = useState("");
  const [marks, setMarks] = useState<MarkingOutsideRow[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const inFlight = useRef(false);
  const serials = parseSequences(serialsText);
  const request = (dryRun: boolean): MarkingOutsideRequest | null => serials && ({
    group_id: group.id, version: group.version, task_ids: taskId ? [taskId] : [], serials, comment: comment.trim(), dry_run: dryRun,
  });
  const requestKey = JSON.stringify([serials, taskId]);

  useEffect(() => {
    const controller = new AbortController();
    markingOutsideApi.list(group.id, controller.signal).then((answer) => setMarks(answer.rows))
      .catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason, "Отметки не загружены")); });
    return () => controller.abort();
  }, [group.id, tick]);

  useEffect(() => {
    setShown(null);
    const body = request(true);
    if (!body) { setShownError("Укажите номера изделий: 1 или 1, 3–5"); return; }
    setShownError("");
    let live = true;
    const timer = window.setTimeout(() => {
      markingOutsideApi.mark(body).then((answer) => { if (live) setShown(answer); })
        .catch((reason) => { if (live) setShownError(failureText(reason, "Что будет отмечено — не загружено")); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [requestKey, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const fresh = shown?.rows.filter((row) => row.state === "new") ?? [];
  const freshCount = fresh.reduce((sum, row) => sum + row.quantity, 0);
  const undoable = (marks ?? []).filter((row) => serials?.includes(row.serial) && (!taskId || row.task_id === taskId));

  async function run(action: "mark" | "undo") {
    const body = request(false);
    if (!body || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const answer = action === "mark" ? await markingOutsideApi.mark(body) : await markingOutsideApi.undo(body);
      const pieces = answer.rows.reduce((sum, row) => sum + (action === "undo" || row.state === "new" ? row.quantity : 0), 0);
      setNotice(action === "mark" ? `Отмечено как напечатанные вне ERP: ${countLabel(pieces, LABELS)}`
        : `Отметка снята: ${countLabel(pieces, LABELS)}`);
      onChange();
      setTick((value) => value + 1);
    } catch (reason) {
      setError(failureText(reason, action === "mark" ? "Отметка не сохранена" : "Отметка не снята"));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const columns = (extra: DataColumn<MarkingOutsideRow>[]): DataColumn<MarkingOutsideRow>[] => [
    { key: "item", title: "КБ", value: (row) => row.item, render: (row) => <b>{row.item}</b> },
    { key: "serial", title: "№ изделия", numeric: true, value: (row) => row.serial },
    { key: "text", title: "Надпись", value: (row) => row.text, grow: true, wrap: true },
    { key: "quantity", title: "Шт.", numeric: true, value: (row) => row.quantity, render: (row) => formatNumber(row.quantity) },
    ...extra,
  ];
  const close_ = () => { if (!inFlight.current) close(); };
  return <PageWindow label="Отметить напечатанные вне ERP" width={1280} close={close_}>
    <div className="panel cutting-labels">
      <div className="manual-stock-head"><div><h2>Отметить напечатанные вне ERP</h2><p className="muted">{group.project} · {group.number}</p></div>
        <button type="button" className="secondary" disabled={busy} onClick={close_}>Закрыть</button></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      <div className="form-grid">
        <label>Номера изделий<input name="outside-serials" autoComplete="off" inputMode="numeric" placeholder="1, 3–5" disabled={busy}
          value={serialsText} onChange={(event) => { setSerialsText(event.target.value); setNotice(""); }} /></label>
        <label>КБ<select name="outside-kb" autoComplete="off" disabled={busy} value={taskId}
          onChange={(event) => { setTaskId(event.target.value); setNotice(""); }}>
          <option value="">Все КБ группы ({kbs.length})</option>
          {kbs.map((row) => <option key={row.task_id} value={row.task_id}>{row.item}</option>)}
        </select></label>
        <label className="wide">Комментарий<input name="outside-comment" autoComplete="off" maxLength={1000} disabled={busy}
          placeholder="Где и когда напечатаны" value={comment} onChange={(event) => setComment(event.target.value)} /></label>
      </div>
      <h3>Будет отмечено</h3>
      {shownError && <div className="warning-inline" role="status">{shownError}</div>}
      {!shown && !shownError && <div className="empty" role="status">Загрузка…</div>}
      {shown && <DataTable rows={shown.rows} rowKey={rowKey} label="Будет отмечено" compactBelow={Infinity} pageSize={1000}
        phoneCards={false} columns={columns([{ key: "state", title: "Состояние", value: (row) => row.state === "new" ? "Будет отмечена" : "Уже отмечена" }])} />}
      <div className="form-actions">
        <button type="button" className="secondary" disabled={busy || !undoable.length} onClick={() => void run("undo")}>
          {undoable.length ? `Снять отметку: ${countLabel(undoable.reduce((sum, row) => sum + row.quantity, 0), LABELS)}` : "Снять отметку"}</button>
        <button type="button" className="primary" disabled={busy || !freshCount} onClick={() => void run("mark")}>
          {busy ? "Сохранение…" : freshCount ? `Отметить ${countLabel(freshCount, LABELS)}` : "Уже отмечены"}</button>
      </div>
      <h3>Отмечено вне ERP</h3>
      {!marks && !error && <div className="empty" role="status">Загрузка…</div>}
      {marks && <DataTable rows={marks} rowKey={rowKey} label="Отмечено вне ERP" compactBelow={Infinity} pageSize={1000} phoneCards={false}
        emptyText="Отметок нет — укажите номера изделий и нажмите «Отметить»." columns={columns([
          { key: "actor", title: "Кто", value: (row) => row.actor ?? "" },
          { key: "at", title: "Когда", value: (row) => row.at ?? "", render: (row) => formatMoment(row.at) },
          { key: "comment", title: "Комментарий", value: (row) => row.comment ?? "", wrap: true },
        ])} />}
    </div>
  </PageWindow>;
}
