import { useEffect, useMemo, useState, type ReactNode } from "react";
import { failureText, requestsApi, type RequestSchema } from "./api";
import { formatDay, formatQty, formatNumber } from "./format";
import { requestsViewApi, type CompositionRow, type RequestComposition as Composition, type RqLine, type RqScope } from "./procurementRequestsApi";
import { RB_LABELS, RB_TONES } from "./procurementRequestsLogic";
import { SuggestPanel, type ChosenLine } from "./RfqPool";
import { StatusChip } from "./StatusChip";
import { openPlace } from "./navigation";
import "./requestComposition.css";
import { purposeLabel } from "./purposeLabels";
import { pointLabel } from "./requestFormV2Logic";
import { PageWindow } from "./PageWindow";

export function safeCompositionUrl(value: string): string | null {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}

export function RequestComposition({ project, projectKey, scope, canAct, onSaved, actions, refreshKey = 0 }: {
  project: string; projectKey: string; scope: RqScope; canAct: boolean; onSaved: (saved: boolean) => void; actions?: (line: RqLine, reload: () => Promise<void>) => ReactNode; refreshKey?: number;
}) {
  const [snapshot, setSnapshot] = useState<Composition | null>(null);
  const [lines, setLines] = useState<RqLine[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [asking, setAsking] = useState<ChosenLine[] | null>(null);
  const [metadata, setMetadata] = useState({ purpose: "", delivery_point: "", procurement_deadline: "", planned_date: "" });
  const [schema, setSchema] = useState<RequestSchema | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  useEffect(() => { if (canAct) requestsApi.schema().then(setSchema).catch((reason) => setError(failureText(reason, "Справочник заявок не загрузился"))); }, [canAct]);

  async function readLines() {
    const view = await requestsViewApi.view({ scope, project: projectKey });
    return (await Promise.all(view.batches.map((batch) => requestsViewApi.batch(scope, batch.key)))).flatMap((batch) => batch.lines);
  }
  useEffect(() => {
    let current = true;
    setSnapshot(null); setLines([]); setSelected(new Set()); setAsking(null); setError(""); onSaved(false);
    Promise.all([requestsViewApi.composition(project), readLines()]).then(([next, nextLines]) => {
      if (!current) return;
      setSnapshot(next); setLines(nextLines); setUrl(next.source?.url ?? ""); onSaved(next.state === "saved");
    }).catch((reason) => { if (current) setError(failureText(reason, "Состав ЗКП не загрузился")); });
    return () => { current = false; };
  }, [project, projectKey, scope]); // onSaved is the parent's stable state setter.
  useEffect(() => {
    if (!refreshKey) return;
    let current = true;
    Promise.all([readLines(), requestsViewApi.composition(project)]).then(([next, nextSnapshot]) => { if (current) { setLines(next); setSnapshot(nextSnapshot); } }).catch((reason) => { if (current) setError(failureText(reason, "Заявки не обновились")); });
    return () => { current = false; };
  }, [refreshKey]);

  const rows = useMemo(() => {
    const used = new Set<string>();
    const source = (snapshot?.rows ?? []).flatMap((row) => {
      const linked = lines.filter((line) => row.procurement_ids.includes(line.id));
      linked.forEach((line) => used.add(line.id));
      return linked.length ? linked.map((line) => ({ key: line.id, source: row as CompositionRow | null, line: line as RqLine | null }))
        : [{ key: `source-${row.source_row}`, source: row as CompositionRow | null, line: null as RqLine | null }];
    });
    return [...source, ...lines.filter((line) => !used.has(line.id)).map((line) => ({ key: line.id, source: null, line }))];
  }, [snapshot, lines]);
  const shown = rows.filter(({ source, line }) => `${source?.name ?? line?.name} ${source?.item_type ?? line?.item_type}`.toLocaleLowerCase("ru").includes(query.trim().toLocaleLowerCase("ru")));
  const selectedRows = rows.filter((row) => selected.has(row.key));
  const hasSourceOnly = selectedRows.some(({ source, line }) => source && !line);

  async function refresh() {
    setBusy(true); setError("");
    try {
      await requestsViewApi.refreshComposition(project, url, snapshot?.version ?? 0);
      const next = await requestsViewApi.composition(project);
      setSnapshot(next); setSelected(new Set()); onSaved(next.state === "saved");
    } catch (reason) { setError(failureText(reason, "ЗКП не обновлён")); } finally { setBusy(false); }
  }

  async function ask() {
    if (!snapshot?.snapshot_id || !snapshot.source) return;
    setBusy(true); setError("");
    try {
      let ids = selectedRows.flatMap(({ line }) => line ? [line.id] : []);
      if (hasSourceOnly) {
        const result = await requestsViewApi.materialize({ project, snapshot_id: snapshot.snapshot_id, expected_version: snapshot.version,
          content_hash: snapshot.source.content_hash, source_rows: selectedRows.filter(({ line }) => !line).map(({ source }) => source!.source_row), ...metadata });
        ids = [...ids, ...result.rows.flatMap((row) => row.procurement_ids)];
      }
      const fresh = await readLines();
      const chosen = ids.map((id) => fresh.find((line) => line.id === id));
      if (chosen.some((line) => !line?.selectable)) throw new Error("Позиции изменились. Проверьте их статус и повторите выбор.");
      setLines(fresh);
      setSnapshot(await requestsViewApi.composition(project));
      setAsking(chosen.map((line) => ({ id: line!.id, name: line!.name, legacy_id: line!.legacy_id,
        version: line!.version, quantity: line!.to_buy, demand: line!.quantity, unit: line!.unit })));
    } catch (reason) { setError(failureText(reason, "Не удалось подготовить запрос КП")); } finally { setBusy(false); }
  }

  return <section className="rq-composition" aria-label="Состав ЗКП проекта">
    {actionId && actions && lines.find((line) => line.id === actionId) && <PageWindow label="Действия по заявке" close={() => setActionId(null)} width={1280}>
      <div className="filters"><h3>Действия по заявке</h3><button type="button" className="secondary small" onClick={() => setActionId(null)}>Закрыть</button></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {actions(lines.find((line) => line.id === actionId)!, async () => {
        try { const [next, nextSnapshot] = await Promise.all([readLines(), requestsViewApi.composition(project)]); setLines(next); setSnapshot(nextSnapshot); }
        catch (reason) { setError(failureText(reason, "Заявки не обновились. Обновите страницу перед следующим действием.")); }
      })}
    </PageWindow>}
    <div className="filters"><h3>ЗКП · {project}</h3>{snapshot?.source && <span className="muted">Сохранён {formatDay(snapshot.source.imported_at)}</span>}
      {canAct && <><input aria-label="Ссылка на расчёт ЗКП" type="url" value={url} placeholder="https://docs.google.com/spreadsheets/…" onChange={(event) => setUrl(event.target.value)} />
        <button type="button" className="secondary small" disabled={busy || !url.trim()} onClick={() => void refresh()}>Обновить ЗКП</button></>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!snapshot && !error && <div className="empty">Загрузка состава ЗКП…</div>}
    {snapshot?.state === "missing" && <p className="page-sub">Полный ЗКП не сохранён. Укажите ссылку на расчёт и обновите состав. Ниже — существующие заявки.</p>}
    {snapshot?.state === "saved" && <>
      {asking ? <SuggestPanel lineIds={asking.map((line) => line.id)} lines={asking} onBack={() => setAsking(null)} onCreated={(id) => openPlace("procurement", { tab: "rfq", rfq: id, letters: "1" })} /> : <>
        <div className="filters"><input type="search" aria-label="Поиск в ЗКП" placeholder="Позиция или тип…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
        <div className="table-wrap"><table className="grid wide" aria-label="Позиции ЗКП и заявки"><thead><tr>
          {canAct && <th aria-label="Выбор" />}<th>Позиция</th><th className="num">Под проект</th><th>Партии на складе</th><th className="num">План из расчёта</th><th>Покупали</th><th>Ход закупки</th>
        </tr></thead><tbody>{shown.map(({ key, source, line }) => {
          const name = source?.name ?? line!.name;
          const unit = source?.unit ?? line!.unit;
          const link = safeCompositionUrl(source?.link ?? line?.plan?.link ?? "");
          const blocked = source && (source.issues.length > 0 || ["source_changed", "request_missing"].includes(source.relation));
          const selectable = !blocked && (line ? line.selectable : source?.relation === "unlinked");
          return <tr key={key} className={selected.has(key) ? "rq-picked" : undefined}>
            {canAct && <td><input type="checkbox" aria-label={`Выбрать ${name}`} disabled={busy || !selectable} checked={selected.has(key)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(key); else next.delete(key); return next; })} /></td>}
            <td className="text"><strong>{name}</strong><small>{source?.item_type ?? line?.item_type}{!source ? " · Вне ЗКП" : ""}</small>
              {link ? <a href={link} target="_blank" rel="noreferrer">Описание / ссылка ↗</a> : source?.link && <small>{source.link}</small>}
              {source?.issues.length ? <small className="warning-inline">Проверьте количество, цену и единицу в расчёте</small> : null}
              {source && ["source_changed", "request_missing"].includes(source.relation) && <small className="warning-inline">Связь с заявкой не подтверждена — требуется сверка</small>}
            </td>
            <td className="num">{formatQty(source?.quantity ?? line?.quantity ?? "", unit)}</td>
            <td>{source?.lots.length ? source.lots.map((lot) => <div key={lot.id} className="rq-composition-lot">
              <a href={`?section=stock&q=${encodeURIComponent(lot.grn)}`}>{lot.grn}{lot.package ? ` · ${lot.package}` : ""}</a><small>{lot.warehouse} · всего {formatQty(lot.balance, unit)} · резерв {formatQty(lot.reserved, unit)} · свободно {formatQty(lot.free, unit)}</small>
              {lot.match_state !== "exact" && <small className="warning-inline">Совпадение неоднозначно</small>}
            </div>) : <span className="muted">{line?.free_stock ? `Свободно ${formatQty(line.free_stock, unit)}` : "Нет подтверждённых партий"}</span>}</td>
            <td className="num">{source?.price_with_vat != null ? `${formatNumber(source.price_with_vat)} ₽ с НДС` : source?.price != null ? `${formatNumber(source.price)} ₽ без НДС` : line?.plan?.price_with_vat != null ? `${formatNumber(line.plan.price_with_vat)} ₽ с НДС` : "—"}</td>
            <td>{line?.last ? <><strong>{formatNumber(line.last.price)} ₽</strong><small>{line.last.supplier} · {formatDay(line.last.date)}</small><small>{line.last.source === "upd" ? "Источник: УПД" : "Источник: оплаченная закупка"}
              {line.last.document_id ? <> · <a href={`?section=primarydocs&tab=upd&upd=${encodeURIComponent(line.last.document_id)}`}>{line.last.document_number || "Открыть УПД"}</a></>
                : line.last.procurement_line_id ? <> · <a href={`?section=procurement&tab=requests&line=${encodeURIComponent(line.last.procurement_line_id)}`}>{line.last.document_number || "Открыть закупку"}</a></> : null}</small></> : "—"}</td>
            <td>{line ? <><StatusChip tone={RB_TONES[line.rb_status]}>{RB_LABELS[line.rb_status]}</StatusChip><small>{line.legacy_id}</small>
              {actions && <button type="button" className="secondary small" onClick={() => setActionId(line.id)}>Действия</button>}
            </> : source?.procurement_ids.length ? "Передана в закупку · смотрите «Все»" : source?.request_id ? "Заявка создана, не передана" : "Не заказано"}</td>
          </tr>;
        })}</tbody></table></div>
        {shown.length === 0 && <div className="empty">Позиции не найдены</div>}
        {canAct && selected.size > 0 && <>
          {hasSourceOnly && <div className="form-grid">
            <label>Назначение<select value={metadata.purpose} onChange={(event) => setMetadata({ ...metadata, purpose: event.target.value })}><option value="">Выберите…</option>{schema?.purposes.map((value) => <option key={value} value={value}>{purposeLabel(value)}</option>)}</select></label>
            <label>Куда доставить<select value={metadata.delivery_point} onChange={(event) => setMetadata({ ...metadata, delivery_point: event.target.value })}><option value="">Выберите…</option>{schema?.delivery_points.map((value) => <option key={value} value={value}>{pointLabel(value)}</option>)}</select></label>
            <label>Закупить к<input type="date" value={metadata.procurement_deadline} onChange={(event) => setMetadata({ ...metadata, procurement_deadline: event.target.value })} /></label>
            <label>Нужно на складе к<input type="date" value={metadata.planned_date} onChange={(event) => setMetadata({ ...metadata, planned_date: event.target.value })} /></label>
          </div>}
          <div className="bulk-bar"><span>Выбрано: {selected.size}</span><button type="button" className="secondary small" disabled={busy} onClick={() => setSelected(new Set())}>Снять выбор</button>
            <button type="button" className="primary small" disabled={busy || (hasSourceOnly && Object.values(metadata).some((value) => !value.trim()))} onClick={() => void ask()}>{hasSourceOnly ? "Создать выбранные заявки и запросить КП" : "Запросить КП"}</button></div>
        </>}
      </>}
    </>}
  </section>;
}
