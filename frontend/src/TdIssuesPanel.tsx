import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  failureText, getSto, getTdNormcontrol, getTdPrintModel, listTdIssues, resnapshotTdIssue, setTdIssueStatus, tdFindingsOf,
} from "./api";
import { formatDay } from "./format";
import { PageWindow } from "./PageWindow";
import { openRouteSheetPrint, openStoPrint, openTdKitPrint } from "./tdPrint";
import type { RouteSheetOptions, TdFinding, TdIssue } from "./tdTypes";
import { TdIssueWindow } from "./TdIssueWindow";
import "./TdIssues.css";

const BLOCKED_TIP = "Есть замечания, которые блокируют: исправьте их и повторите";

function FindingsList({ findings }: { findings: TdFinding[] }) {
  if (!findings.length) return null;
  return <ul className="td-findings" aria-label="Замечания нормоконтроля">
    {findings.map((finding, index) => <li key={`${finding.code}-${finding.operation_key ?? ""}-${index}`}
      className={finding.severity === "blocking" ? "td-finding-blocking" : "td-finding-warning"}>
      {finding.severity === "blocking" && <strong>блокирует</strong>} {finding.text}
    </li>)}
  </ul>;
}

/** Сопроводительный лист: серийный номер, партия и количество вводятся перед печатью. */
function RouteSheetWindow({ close, onPrint }: { close: () => void; onPrint: (options: RouteSheetOptions) => void }) {
  const [serial, setSerial] = useState("");
  const [batch, setBatch] = useState("");
  const [qty, setQty] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); onPrint({ serial: serial.trim(), batch: batch.trim(), qty: qty.trim() }); close(); };
  return <PageWindow label="Сопроводительный лист" width={520} close={close}>
    <form className="panel td-form" onSubmit={submit}>
      <div className="td-head"><h2>Сопроводительный лист</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></div>
      <div className="td-form-top">
        <label>Заводской номер<input value={serial} onChange={(event) => setSerial(event.target.value)} /></label>
        <label>Партия<input value={batch} onChange={(event) => setBatch(event.target.value)} /></label>
        <label>Количество<input value={qty} inputMode="numeric" onChange={(event) => setQty(event.target.value)} /></label>
      </div>
      <div className="td-actions"><button type="submit" className="primary">Печать</button><button type="button" className="secondary" onClick={close}>Отмена</button></div>
    </form>
  </PageWindow>;
}

/** Блок «Комплект ТД (ЕСТД)» экрана ЭТК: выпуски комплекта, нормоконтроль, смена статуса и печать. */
export function TdIssuesPanel({ cardId, canEdit }: { cardId: string; canEdit: boolean }) {
  const [issues, setIssues] = useState<TdIssue[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [findings, setFindings] = useState<TdFinding[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ issue: TdIssue | null } | null>(null);
  const [routeSheet, setRouteSheet] = useState(false);

  const load = useCallback(async (focusId?: string) => {
    try {
      const result = await listTdIssues(cardId);
      const list = Array.isArray(result) ? result : [];
      setIssues(list);
      setSelectedId((current) => focusId ?? (list.some((item) => item.id === current) ? current : list[0]?.id ?? ""));
    } catch (reason) { setError(failureText(reason, "Комплект ТД не загружен")); setIssues([]); }
  }, [cardId]);
  useEffect(() => { void load(); }, [load]);

  const selected = issues?.find((item) => item.id === selectedId) ?? null;
  const selectedKey = selected ? `${selected.id}:${selected.version}` : "";
  useEffect(() => {
    if (!selected) { setFindings([]); return; }
    let live = true;
    void getTdNormcontrol(selected.id).then((result) => { if (live) setFindings(result.findings); }).catch(() => { if (live) setFindings([]); });
    return () => { live = false; };
  }, [selectedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const blocking = findings.some((item) => item.severity === "blocking");
  const activeIssue = issues?.find((item) => item.status === "active") ?? null;

  async function act(work: () => Promise<TdIssue>) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const updated = await work();
      await load(updated.id);
    } catch (reason) {
      const blocked = tdFindingsOf(reason);
      if (blocked.length) { setFindings(blocked); setError("Действие заблокировано замечаниями нормоконтроля."); }
      else setError(failureText(reason, "Действие не выполнено"));
    } finally { setBusy(false); }
  }
  const move = (to: "signing" | "draft" | "active" | "annulled") => selected && void act(() => setTdIssueStatus(selected.id, selected.version, to));
  const annul = () => { if (selected && window.confirm(`Аннулировать выпуск ${selected.designations.vtd}?`)) move("annulled"); };

  async function printWith(open: (model: Awaited<ReturnType<typeof getTdPrintModel>>) => void) {
    if (!selected || busy) return;
    setBusy(true); setError("");
    try { open(await getTdPrintModel(selected.id)); }
    catch (reason) { setError(failureText(reason, "Печать не открыта")); }
    finally { setBusy(false); }
  }
  async function printSto() {
    setError("");
    try { openStoPrint(await getSto()); }
    catch (reason) { setError(failureText(reason, "СТО не открыт")); }
  }

  const mutable = selected && canEdit;
  return <section className="td-panel" aria-label="Комплект ТД (ЕСТД)">
    <div className="td-head">
      <strong className="td-title">Комплект ТД (ЕСТД)</strong>
      {canEdit && issues && issues.length === 0 && <button type="button" className="primary small" onClick={() => setForm({ issue: null })}>Новый выпуск</button>}
      <button type="button" className="secondary small td-sto" onClick={() => void printSto()}>Печать СТО</button>
    </div>
    {activeIssue?.has_changes && <div className="warning-inline td-changes" role="status">
      <span>ЭТК изменилась (ревизия {activeIssue.current_etk_revision}), действующий комплект — ревизия {activeIssue.etk_revision}. Оформите извещение об изменении.</span>
      {canEdit && <button type="button" className="primary small" onClick={() => setForm({ issue: null })}>Оформить извещение</button>}
    </div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {issues === null && <div className="empty">Загрузка…</div>}
    {issues && issues.length === 0 && !error && <div className="empty">Комплект ТД ещё не выпущен — нажмите «Новый выпуск».</div>}
    {issues && issues.length > 0 && <div className="table-wrap"><table className="td-table">
      <thead><tr><th>Обозначение</th><th>Изм.</th><th>Литера</th><th>Статус</th><th>Ревизия ЭТК</th><th>Утверждён</th></tr></thead>
      <tbody>{issues.map((item) => <tr key={item.id} className={item.id === selectedId ? "td-selected" : ""}
        aria-selected={item.id === selectedId} onClick={() => { setSelectedId(item.id); setError(""); }}>
        <td><strong>{item.designations.vtd}</strong></td>
        <td>{item.change_no}</td>
        <td>{item.litera || "—"}</td>
        <td><span className={`td-status td-status-${item.status}`}>{item.status_label}</span></td>
        <td>{item.etk_revision}</td>
        <td>{formatDay(item.approved_at) || "—"}</td>
      </tr>)}</tbody>
    </table></div>}
    {selected && <div className="td-selected-block">
      <FindingsList findings={findings} />
      <div className="td-toolbar">
        {mutable && selected.status === "draft" && <>
          <button type="button" className="primary small" disabled={busy || blocking} title={blocking ? BLOCKED_TIP : undefined} onClick={() => move("signing")}>Отправить на подпись</button>
          <button type="button" className="secondary small" disabled={busy} onClick={() => setForm({ issue: selected })}>Править</button>
          <button type="button" className="secondary small" disabled={busy} onClick={() => void act(() => resnapshotTdIssue(selected.id, selected.version))}>Обновить снимок ЭТК</button>
          <button type="button" className="danger small" disabled={busy} onClick={annul}>Аннулировать</button>
        </>}
        {mutable && selected.status === "signing" && <>
          <button type="button" className="primary small" disabled={busy} onClick={() => move("active")}>Утвердить</button>
          <button type="button" className="secondary small" disabled={busy} onClick={() => move("draft")}>Вернуть в проект</button>
        </>}
        {mutable && selected.status === "active" && <button type="button" className="danger small" disabled={busy} onClick={annul}>Аннулировать</button>}
        <span className="td-spacer" />
        <button type="button" className="secondary small" disabled={busy} onClick={() => void printWith(openTdKitPrint)}>Печать комплекта ТД</button>
        <button type="button" className="secondary small" disabled={busy} onClick={() => setRouteSheet(true)}>Сопроводительный лист</button>
      </div>
    </div>}
    {form && <TdIssueWindow cardId={cardId} issue={form.issue} previous={issues?.[0] ?? null} isFirst={!issues?.length}
      close={() => setForm(null)} onSaved={(saved) => void load(saved.id)} />}
    {routeSheet && selected && <RouteSheetWindow close={() => setRouteSheet(false)}
      onPrint={(options) => void printWith((model) => openRouteSheetPrint(model, options))} />}
  </section>;
}
