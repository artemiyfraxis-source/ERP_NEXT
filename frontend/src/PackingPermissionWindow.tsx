import { useEffect, useState } from "react";
import { ApiError, failureText, shippingApi, type PackPermission } from "./api";
import { num } from "./intakeStages";
import { PageWindow } from "./PageWindow";
import { useCanAct } from "./rights";

export function PackDecision({ project, editable, onReload }: {
  project: string; editable: boolean; onReload?: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const canEdit = useCanAct("shipping") && editable;
  return <div className="ship-pack-decision" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
    <button type="button" className="secondary small" onClick={() => setOpen(true)}>{canEdit ? "Разрешить упаковку по КБ" : "Разрешения упаковки по КБ"}</button>
    {open && <PackingPermissionWindow project={project} editable={canEdit} close={() => setOpen(false)} saved={onReload} />}
  </div>;
}

export function PackingPermissionWindow({ project, editable, close, saved }: {
  project: string; editable: boolean; close: () => void; saved?: () => Promise<void>;
}) {
  const [rows, setRows] = useState<PackPermission[] | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [marked, setMarked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = async () => {
    const result = await shippingApi.packPermissions(project);
    setRows(result.permissions);
    setDraft((old) => Object.fromEntries(result.permissions.map((row) => [row.item_index, old[row.item_index] ?? row.quantity])));
  };
  useEffect(() => { let live = true;
    shippingApi.packPermissions(project).then((result) => {
      if (live) { setRows(result.permissions); setDraft(Object.fromEntries(result.permissions.map((row) => [row.item_index, row.quantity]))); }
    }).catch((reason) => { if (live) setError(failureText(reason, "Разрешения не загружены")); });
    return () => { live = false; };
  }, [project]);
  const chosen = (rows ?? []).filter((row) => marked[row.item_index]);
  async function save() {
    if (busy || !editable || !chosen.length) return;
    const invalid = chosen.find((row) => !draft[row.item_index]?.trim() || !Number.isFinite(Number(draft[row.item_index])) || num(draft[row.item_index]) < num(row.consumed));
    if (invalid) { setError(`${invalid.item_index}: общий лимит не может быть меньше уже упакованного (${invalid.consumed})`); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await shippingApi.savePackPermissions(project, chosen.map((row) => ({ item_index: row.item_index, quantity: draft[row.item_index], version: row.version })));
      setRows(result.permissions); setMarked({});
      setDraft(Object.fromEntries(result.permissions.map((row) => [row.item_index, row.quantity])));
      setNotice("Разрешения упаковки сохранены"); await saved?.();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError("Разрешения изменились. Проверьте обновлённые количества и повторите сохранение. Ваш ввод сохранён.");
        try { await load(); } catch (reloadReason) { setRows(null); setError(failureText(reloadReason, "Разрешения не обновлены")); }
      } else setError(failureText(reason, "Разрешения не сохранены"));
    } finally { setBusy(false); }
  }
  return <PageWindow label={`Разрешения упаковки ${project}`} close={close} width={1100}>
    <section className="panel intake-window pack-permission-window">
      <header className="page-head"><h2>Упаковка по КБ · {project}</h2><button className="secondary small" onClick={close}>Закрыть</button></header>
      <p className="muted">Общий лимит включает уже упакованное и отгруженное. Изменяются только отмеченные КБ.</p>
      {error && <div role="alert" className="error-banner">{error}</div>}
      {notice && <div role="status" className="success-banner">{notice}</div>}
      {!rows ? <div className="empty">{error ? "Не удалось загрузить разрешения" : "Загрузка…"}</div> : !rows.length ? <div className="empty">В проекте нет КБ.</div> : <table className="grid compact intake-window-table">
        <colgroup><col style={{ width: "4%" }} /><col style={{ width: "36%" }} /><col style={{ width: "16%" }} /><col style={{ width: "16%" }} /><col style={{ width: "13%" }} /><col style={{ width: "15%" }} /></colgroup>
        <thead><tr><th aria-label="Отметка" /><th>КБ / наименование</th><th className="num">Уже упаковано, включая отгруженное</th><th className="num">На складе не упаковано</th><th className="num">Разрешено ещё</th><th className="num">Общий лимит, шт</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.item_index}>
          <td><input type="checkbox" aria-label={`Изменить ${row.item_index}`} disabled={!editable || busy} checked={Boolean(marked[row.item_index])} onChange={(event) => setMarked({ ...marked, [row.item_index]: event.target.checked })} /></td>
          <td><strong>{row.item_index}</strong><div title={row.name}>{row.name}</div></td>
          <td className="num">{row.consumed}</td><td className="num">{row.physical_remaining}</td><td className="num">{row.remaining}</td>
          <td className="num">{editable ? <input className="ship-qty" type="number" step="any" min={row.consumed} aria-label={`Общий лимит ${row.item_index}`} disabled={busy} value={draft[row.item_index] ?? ""} onChange={(event) => { setDraft({ ...draft, [row.item_index]: event.target.value }); setMarked({ ...marked, [row.item_index]: true }); }} /> : row.quantity}</td>
        </tr>)}</tbody>
      </table>}
      <div className="intake-window-actions">{editable ? <button className="primary" disabled={busy || !chosen.length} onClick={() => void save()}>{busy ? "Сохраняем…" : `Сохранить разрешения · ${chosen.length} КБ`}</button> : <span className="muted">Только просмотр</span>}</div>
    </section>
  </PageWindow>;
}
