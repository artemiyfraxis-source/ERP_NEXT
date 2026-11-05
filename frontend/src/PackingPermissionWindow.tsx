import { useEffect, useState } from "react";
import { ApiError, failureText, shippingApi, type PackPermission } from "./api";
import { num } from "./intakeStages";
import { PageWindow } from "./PageWindow";
import { useCanAct } from "./rights";

const micros = (text: string | undefined): bigint | null => {
  if (!text || !/^\d+(?:\.\d{0,6})?$/.test(text.trim())) return null;
  const [whole, fraction = ""] = text.trim().split(".");
  return BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, "0"));
};
const quantityText = (value: bigint) => `${value / 1000000n}.${String(value % 1000000n).padStart(6, "0")}`.replace(/\.?0+$/, "");

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
  const [edited, setEdited] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = async () => {
    const result = await shippingApi.packPermissions(project);
    setRows(result.permissions);
    setDraft((old) => Object.fromEntries(result.permissions.map((row) => [row.item_index, old[row.item_index] ?? row.remaining])));
  };
  useEffect(() => { let live = true;
    shippingApi.packPermissions(project).then((result) => {
      if (live) { setRows(result.permissions); setDraft(Object.fromEntries(result.permissions.map((row) => [row.item_index, row.remaining]))); }
    }).catch((reason) => { if (live) setError(failureText(reason, "Разрешения не загружены")); });
    return () => { live = false; };
  }, [project]);
  const totalCap = (row: PackPermission) => {
    const remaining = micros(draft[row.item_index]), consumed = micros(row.consumed);
    return remaining === null || consumed === null ? null : quantityText(consumed + remaining);
  };
  const chosen = (rows ?? []).filter((row) => marked[row.item_index] && (totalCap(row) === null || micros(totalCap(row)!) !== micros(row.quantity)));
  const select = (row: PackPermission, checked: boolean) => {
    setMarked({ ...marked, [row.item_index]: checked });
    if (checked && !edited[row.item_index] && num(row.remaining) === 0) setDraft({ ...draft, [row.item_index]: row.physical_remaining });
    setNotice("");
  };
  async function save() {
    if (busy || !editable || !chosen.length) return;
    const invalid = chosen.find((row) => totalCap(row) === null);
    if (invalid) { setError(`${invalid.item_index}: укажите неотрицательное количество, не более 6 знаков после запятой`); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await shippingApi.savePackPermissions(project, chosen.map((row) => ({ item_index: row.item_index, quantity: totalCap(row)!, version: row.version })));
      setRows(result.permissions); setMarked({}); setEdited({});
      setDraft(Object.fromEntries(result.permissions.map((row) => [row.item_index, row.remaining])));
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
      <p className="muted">Для КБ без разрешения предложим неупакованный остаток. «Разрешить ещё» — количество для дальнейшей упаковки, без уже упакованного.</p>
      {error && <div role="alert" className="error-banner">{error}</div>}
      {notice && <div role="status" className="success-banner">{notice}</div>}
      {!rows ? <div className="empty">{error ? "Не удалось загрузить разрешения" : "Загрузка…"}</div> : !rows.length ? <div className="empty">В проекте нет КБ.</div> : <table className="grid compact intake-window-table">
        <colgroup><col style={{ width: "4%" }} /><col style={{ width: "36%" }} /><col style={{ width: "16%" }} /><col style={{ width: "16%" }} /><col style={{ width: "13%" }} /><col style={{ width: "15%" }} /></colgroup>
        <thead><tr><th aria-label="Отметка" /><th>КБ / наименование</th><th className="num">Уже упаковано, включая отгруженное</th><th className="num">На складе не упаковано</th><th className="num">Сейчас разрешено ещё</th><th className="num">Разрешить ещё, шт</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.item_index}>
          <td><input type="checkbox" aria-label={`Изменить ${row.item_index}`} disabled={!editable || busy} checked={Boolean(marked[row.item_index])} onChange={(event) => select(row, event.target.checked)} /></td>
          <td><strong>{row.item_index}</strong><div title={row.name}>{row.name}</div></td>
          <td className="num">{row.consumed}</td><td className="num">{row.physical_remaining}</td><td className="num">{row.remaining}</td>
          <td className="num">{editable ? <><input className="ship-qty" type="number" step="0.000001" min="0" name={`pack-remaining-${row.item_index}`} autoComplete="off" aria-label={`Разрешить ещё ${row.item_index}`} disabled={busy} value={draft[row.item_index] ?? ""} onChange={(event) => { setDraft({ ...draft, [row.item_index]: event.target.value }); setEdited({ ...edited, [row.item_index]: true }); setMarked({ ...marked, [row.item_index]: true }); setNotice(""); }} /><small className="muted">Общий лимит: {totalCap(row) ?? "—"}</small></> : row.remaining}</td>
        </tr>)}</tbody>
      </table>}
      <div className="intake-window-actions">{editable ? <button className="primary" disabled={busy || !chosen.length} onClick={() => void save()}>{busy ? "Сохраняем…" : `Сохранить разрешения · ${chosen.length} КБ`}</button> : <span className="muted">Только просмотр</span>}</div>
    </section>
  </PageWindow>;
}
