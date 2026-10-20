import { useEffect, useMemo, useState, type FormEvent } from "react";
import { failureText } from "./api";
import { countLabel, formatDay, formatDayMonth, formatNumber, formatQty, NBSP, unitLabel } from "./format";
import { useProjectLabel } from "./internalProjects";
import {
  rfqApi,
  type PastPrice,
  type PoolLine,
  type RfqPoolResult,
  type SuggestReason,
  type SuggestResult,
  type SupplierBrief,
  type SupplierPick,
  type SupplierSuggestion,
} from "./purchasingRfqApi";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";

/** «Новый запрос КП» as Roller_Buyer's RequestsPage + RfqCreatePanel: the pool of the buyer's lines by nomenclature
 *  category (what was bought before and how dear), a selection bar, then whom to ask — suppliers ranked by purchase
 *  statistics with their reasons, preselected — and the request is created with its lines and suppliers at once. */

const LINE_FORMS = ["строка", "строки", "строк"] as const;
const money = (value: string | null | undefined) =>
  value === null || value === undefined || value === "" ? "—" : `${formatNumber(value, { digits: 2, minDigits: 2 })}${NBSP}₽`;
const MATCHED_BY: Record<string, string> = { link: "связана вручную", part_key: "по артикулу", alias: "по названию" };

function perUnit(unit: string | null, lineUnit: string) {
  return unit && unitLabel(unit) !== unitLabel(lineUnit) ? `/${unitLabel(unit)}` : "";
}

function samePurchase(a: PastPrice, b: PastPrice) {
  return a.price === b.price && a.date === b.date && a.supplier === b.supplier;
}

/** «покупали: 9,80 ₽ (мин 9,10 ₽ у ООО Альфа, 03.09)». */
export function historyText(line: PoolLine): string {
  const { history } = line;
  if (!history?.last) return "";
  const { last, min } = history;
  const head = `покупали: ${money(last.price)}${perUnit(last.unit, line.unit)}`;
  if (!min || samePurchase(min, last)) {
    return `${head} (${last.supplier ? `у ${last.supplier}, ` : ""}${formatDayMonth(last.date)})`;
  }
  return `${head} (мин ${money(min.price)}${perUnit(min.unit, line.unit)}${min.supplier ? ` у ${min.supplier}` : ""}, ${formatDayMonth(min.date)})`;
}

function NomenclatureCell({ line }: { line: PoolLine }) {
  if (!line.item) return <span className="cell-empty">—</span>;
  return (
    <div className="rfq-nomen">
      <StatusChip tone="info" title={`${line.item.name} · ${MATCHED_BY[line.item.matched_by] ?? ""}`}>в номенклатуре</StatusChip>
      {line.cheaper_before_pct !== null && (
        <StatusChip tone="warning" title={`Самая низкая прошлая цена в той же единице ниже бюджета${line.cheaper_source_label ? `: ${line.cheaper_source_label}` : ""}`}>
          раньше дешевле на {formatNumber(line.cheaper_before_pct, { digits: 1 })}%{line.cheaper_source_label ? ` (${line.cheaper_source_label})` : ""}
        </StatusChip>
      )}
      {line.history?.last && (
        <small title={`Оплаченных закупок: ${line.history.purchases}`}>{historyText(line)}</small>
      )}
    </div>
  );
}

function matches(line: PoolLine, words: string[], project: (value: string | null) => string) {
  if (!words.length) return true;
  const hay = [line.name, line.legacy_id, line.article, line.supplier, line.supplier_name, project(line.project), line.item?.name]
    .filter(Boolean).join(" ").toLowerCase();
  return words.every((word) => hay.includes(word));
}

function originals(suppliers: { name: string; lines: number }[]) {
  const shown = suppliers.slice(0, 4).map((supplier) => `${supplier.name} ${supplier.lines}`);
  return suppliers.length > 4 ? `${shown.join(", ")} и ещё ${suppliers.length - 4}` : shown.join(", ");
}

export function RfqPool({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [pool, setPool] = useState<RfqPoolResult | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [asking, setAsking] = useState<string[] | null>(null);
  const project = useProjectLabel();

  useEffect(() => {
    rfqApi.pool().then(setPool).catch((reason) => setError(failureText(reason, "Строки не загрузились")));
  }, []);

  // The chosen lines in the order they were ticked: the supplier step lists them under each supplier.
  const askingLines = useMemo(() => {
    const byId = new Map((pool?.groups ?? []).flatMap((group) => group.lines.map((line) => [line.id, line] as const)));
    return (asking ?? []).map((id): ChosenLine => byId.get(id) ?? { id, name: id, legacy_id: "" });
  }, [asking, pool]);

  const groups = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return (pool?.groups ?? []).map((group) => ({ group, rows: group.lines.filter((line) => matches(line, words, project)) }));
  }, [pool, query, project]);

  const toggle = (id: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleGroup = (rows: PoolLine[], on: boolean) => setPicked((current) => {
    const next = new Set(current);
    for (const row of rows) { if (on) next.add(row.id); else next.delete(row.id); }
    return next;
  });
  const toggleClosed = (category: string) => setClosed((current) => {
    const next = new Set(current);
    if (next.has(category)) next.delete(category); else next.add(category);
    return next;
  });

  const shown = groups.reduce((sum, { rows }) => sum + rows.length, 0);
  return (
    <section className="panel rfq-pool" aria-label="Новый запрос КП">
      <div className="rfq-pool-head">
        <h2>Новый запрос КП</h2>
        {pool && <span className="muted">строк для запроса: {pool.total}</span>}
        <button type="button" className="secondary small" style={{ marginLeft: "auto" }} onClick={onClose}>Закрыть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!pool && !error && <div className="empty">Загрузка…</div>}
      {pool?.total === 0 && (
        <div className="empty">Нет строк «В работе» / «Готов к запросу» вне запросов КП — возьмите строки в работу на листе закупки.</div>
      )}
      {asking ? (
        <SuggestPanel lineIds={asking} lines={askingLines} onBack={() => setAsking(null)} onCreated={onCreated} />
      ) : pool && pool.total > 0 && (
        <div className="data-table">
          <div className="filters table-toolbar">
            <div className="table-search">
              <input
                className="search" type="search" aria-label="Поиск по строкам" value={query}
                placeholder="Позиция, артикул, проект, поставщик" onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          </div>
          <div className="table-wrap">
            <table className="grid wide selectable rfq-pool-table">
              <thead>
                <tr>
                  <th aria-label="Выбор" />
                  <th>Позиция</th><th>Артикул</th><th className="num">Кол-во</th><th>Проект</th><th>Поставщик в строке</th>
                  <th className="num">Бюджет с НДС</th><th>Номенклатура</th>
                </tr>
              </thead>
              {groups.map(({ group, rows }) => {
                const pickedIn = rows.filter((row) => picked.has(row.id)).length;
                const open = !closed.has(group.category);
                return (
                  <tbody key={group.category}>
                    <tr className="group-row">
                      <td colSpan={8}>
                        <input
                          type="checkbox" aria-label={`Выбрать группу «${group.label}»`} disabled={rows.length === 0}
                          checked={rows.length > 0 && pickedIn === rows.length}
                          ref={(input) => { if (input) input.indeterminate = pickedIn > 0 && pickedIn < rows.length; }}
                          onChange={(event) => toggleGroup(rows, event.currentTarget.checked)}
                        />
                        <button type="button" className="link-button rfq-group-toggle" aria-expanded={open} onClick={() => toggleClosed(group.category)}>
                          {open ? "▾" : "▸"} <strong>{group.label}</strong>
                        </button>
                        {" · "}
                        {rows.length === group.count ? countLabel(group.count, LINE_FORMS) : `${rows.length} из ${countLabel(group.count, LINE_FORMS)}`}
                        {group.suppliers.length > 0 && <span className="muted"> · изначально: {originals(group.suppliers)}</span>}
                        {(group.from_stock ?? 0) > 0 && <span className="muted"> · со склада: {group.from_stock}</span>}
                        {pickedIn > 0 && <span className="group-picked"> · выбрано {pickedIn}</span>}
                      </td>
                    </tr>
                    {open && rows.map((line) => (
                      <tr key={line.id} className={picked.has(line.id) ? "clickable open" : "clickable"} onClick={() => toggle(line.id)}>
                        <td>
                          <input
                            type="checkbox" aria-label={`Взять ${line.legacy_id}`} checked={picked.has(line.id)}
                            onChange={() => toggle(line.id)} onClick={(event) => event.stopPropagation()}
                          />
                        </td>
                        <td className="text"><strong>{line.name}</strong><small>{line.legacy_id}{line.need_by ? ` · к ${formatDay(line.need_by)}` : ""}</small></td>
                        {/* The article repeats the name on most calc lines — shown only when it adds something. */}
                        <td>{line.article && line.article.trim().toLocaleLowerCase("ru") !== line.name.trim().toLocaleLowerCase("ru") ? line.article : "—"}</td>
                        <td className="num">{formatQty(line.quantity, line.unit)}</td>
                        <td>{project(line.project) || <span className="cell-empty">—</span>}</td>
                        <td title={line.supplier && line.supplier !== line.supplier_name ? `В строке: ${line.supplier}` : undefined}>
                          {line.supplier_name ?? ""}
                        </td>
                        <td className="num">{money(line.budget_unit_gross)}</td>
                        <td><NomenclatureCell line={line} /></td>
                      </tr>
                    ))}
                  </tbody>
                );
              })}
            </table>
          </div>
          {shown === 0 && <div className="empty">Ничего не найдено</div>}
          {picked.size > 0 && (
            <div className="bulk-bar rfq-pool-bar" role="toolbar" aria-label="Выбранные строки">
              <span>Выбрано {picked.size}</span>
              <span className="spacer" />
              <button type="button" className="secondary small" onClick={() => setPicked(new Set())}>Снять выбор</button>
              <button type="button" className="primary small" onClick={() => setAsking([...picked])}>Запросить КП ({picked.size})</button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ── whom to ask ───────────────────────────────────────────────────────────────────────────────────────────────────

const REASON_TONE: Record<string, Tone> = {
  items: "success", category_history: "info", original: "info", key: "success", rotation: "neutral", category: "neutral",
  manual: "neutral",
};

export const suggestionKey = (item: SupplierSuggestion) => item.supplier_id ?? `name:${item.name.toLowerCase()}`;

/** A line of the new request as the supplier step lists it. */
export type ChosenLine = { id: string; name: string; legacy_id: string };

// Per line: why the supplier is suggested for it (the chip shows the best reason, the hint all of them).
const LINE_REASON: Record<string, string> = {
  items: "поставлял", category_history: "такая же категория", original: "в строке", key: "ключевой",
  rotation: "по категории", category: "по категории",
};
const LINE_REASON_HINT: Record<string, string> = {
  items: "поставлял эту позицию", category_history: "поставлял позиции этой категории",
  original: "указан в строке заявки", key: "ключевой поставщик этой категории",
  rotation: "категория в справочнике, давно не спрашивали", category: "категория в справочнике",
};

function reasonText(reason: SuggestReason) {
  // Rotation and the rest of the category carry the day the supplier was last asked, history reasons the last purchase.
  if (reason.code === "rotation" || reason.code === "category") {
    if (reason.last_date) return `${reason.label} · спрашивали ${formatDay(reason.last_date)}`;
    return reason.code === "rotation" ? "ещё не спрашивали" : reason.label;
  }
  const parts = [reason.label];
  if (reason.lines) parts.push(countLabel(reason.lines, LINE_FORMS));
  if (reason.last_date) parts.push(`последняя ${formatDay(reason.last_date)}`);
  return parts.join(" · ");
}

/** Suppliers with their reason chips and a checkbox each (the pool's panel and «Подобрать поставщиков»). */
export function SuggestionList({ items, checked, onToggle }: {
  items: SupplierSuggestion[]; checked: Set<string>; onToggle: (key: string) => void;
}) {
  return (
    <div className="rfq-suggest-list" role="group" aria-label="Поставщики">
      {items.map((item) => {
        const key = suggestionKey(item);
        return (
          <label key={key}>
            <input
              type="checkbox" aria-label={`Спросить ${item.name}`} disabled={item.in_rfq}
              checked={item.in_rfq || checked.has(key)} onChange={() => onToggle(key)}
            />
            <SupplierTitle item={item} />
            {item.in_rfq && <span className="muted">уже в запросе</span>}
          </label>
        );
      })}
    </div>
  );
}

function SupplierTitle({ item }: { item: SupplierSuggestion }) {
  return <>
    <strong>{item.name}{item.is_key ? " ★" : ""}</strong>
    {item.supplier_id === null && (
      <StatusChip tone="warning" title="Добавится в справочник поставщиков вместе с запросом">нет в справочнике</StatusChip>
    )}
    {item.reasons.map((reason) => (
      <StatusChip key={reason.code} tone={REASON_TONE[reason.code] ?? "neutral"}>{reasonText(reason)}</StatusChip>
    ))}
  </>;
}

/** The lines a supplier is suggested for — ticked by default; none known (a supplier added by hand) — every line. */
export function relatedLines(item: SupplierSuggestion, lineIds: string[]): string[] {
  const own = new Set(item.lines.map((line) => line.line_id));
  const related = lineIds.filter((id) => own.has(id));
  return related.length > 0 ? related : lineIds;
}

/** The picks of the new request, each supplier with its ticked lines (a supplier with none is not asked). */
export function picksWithLines(items: SupplierSuggestion[], chosen: Record<string, string[]>): SupplierPick[] {
  return items.flatMap((item) => {
    const lineIds = chosen[suggestionKey(item)] ?? [];
    if (item.in_rfq || lineIds.length === 0) return [];
    const who = item.supplier_id ? { supplier_id: item.supplier_id } : { name: item.name };
    return [{ ...who, reason: item.reason, line_ids: lineIds }];
  });
}

/** Suppliers of the new request with the positions each is asked for: the supplier checkbox takes or drops all its
 *  suggested positions (half-ticked when some are dropped by hand), «позиций: N из M» opens them one by one. */
function SupplierLinesList({ items, lines, chosen, onChange }: {
  items: SupplierSuggestion[]; lines: ChosenLine[]; chosen: Record<string, string[]>;
  onChange: (key: string, lineIds: string[]) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const lineIds = lines.map((line) => line.id);
  const ordered = (ids: Set<string>) => lineIds.filter((id) => ids.has(id));
  const toggleOpen = (key: string) => setOpen((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  return (
    <div className="rfq-suggest-list" role="group" aria-label="Поставщики">
      {items.map((item) => {
        const key = suggestionKey(item);
        const picked = new Set(chosen[key] ?? []);
        const related = relatedLines(item, lineIds);
        const whole = picked.size > 0 && related.every((id) => picked.has(id));
        const why = new Map(item.lines.map((line) => [line.line_id, line.reasons]));
        const expanded = open.has(key) && !item.in_rfq;
        const toggleLine = (id: string) => {
          const next = new Set(picked);
          if (next.has(id)) next.delete(id); else next.add(id);
          onChange(key, ordered(next));
        };
        return (
          <div key={key} className="rfq-supplier" role="group" aria-label={`Поставщик ${item.name}`}>
            <div className="rfq-supplier-head">
              <input
                type="checkbox" aria-label={`Спросить ${item.name}`} disabled={item.in_rfq} checked={item.in_rfq || whole}
                ref={(input) => { if (input) input.indeterminate = !item.in_rfq && picked.size > 0 && !whole; }}
                onChange={(event) => onChange(key, event.currentTarget.checked ? ordered(new Set([...picked, ...related])) : [])}
              />
              <SupplierTitle item={item} />
              {item.in_rfq ? <span className="muted">уже в запросе</span> : (
                <button
                  type="button" className="link-button rfq-supplier-toggle" aria-expanded={expanded}
                  title="Какие позиции спросить у этого поставщика" onClick={() => toggleOpen(key)}
                >позиций: {picked.size} из {lineIds.length} {expanded ? "▾" : "▸"}</button>
              )}
            </div>
            {expanded && (
              <div className="rfq-supplier-lines" role="group" aria-label={`Позиции для ${item.name}`}>
                <span className="rfq-supplier-quick">
                  <button type="button" className="link-button" onClick={() => onChange(key, lineIds)}>все</button>
                  {" · "}
                  <button type="button" className="link-button" onClick={() => onChange(key, related)}>только по причинам</button>
                </span>
                {lines.map((line) => {
                  const reasons = why.get(line.id) ?? [];
                  return (
                    <label key={line.id}>
                      <input
                        type="checkbox" aria-label={`Запросить ${line.legacy_id || line.name} у ${item.name}`}
                        checked={picked.has(line.id)} onChange={() => toggleLine(line.id)}
                      />
                      <span className="rfq-supplier-line-name" title={line.name}>{line.name}</span>
                      {line.legacy_id && <small>{line.legacy_id}</small>}
                      {reasons.length > 0 && (
                        <StatusChip
                          tone={REASON_TONE[reasons[0]] ?? "neutral"}
                          title={reasons.map((code) => LINE_REASON_HINT[code] ?? code).join("; ")}
                        >{LINE_REASON[reasons[0]] ?? reasons[0]}</StatusChip>
                      )}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** The supplier picks to send: from the directory by id, the rest by name (the server quick-adds them). */
export function picksOf(items: SupplierSuggestion[], checked: Set<string>): SupplierPick[] {
  return items
    .filter((item) => checked.has(suggestionKey(item)) && !item.in_rfq)
    .map((item) => (item.supplier_id ? { supplier_id: item.supplier_id, reason: item.reason } : { name: item.name, reason: item.reason }));
}

export function manualSuggestion(name: string, directory: SupplierBrief[]): SupplierSuggestion {
  const clean = name.trim().replace(/\s+/g, " ");
  const known = directory.find((supplier) => supplier.name.trim().toLowerCase() === clean.toLowerCase());
  return {
    supplier_id: known?.supplier_id ?? null, name: known?.name ?? clean, is_key: known?.is_key ?? false,
    categories: known?.categories ?? [], reasons: [{ code: "manual", label: "добавлен вручную", lines: null, last_date: null }],
    reason: "manual", rfq_reason: "manual", preselect: true, in_rfq: false, last_asked_at: null, lines: [],
  };
}

export function SuggestPanel({ lineIds, lines, onBack, onCreated }: {
  lineIds: string[]; lines: ChosenLine[]; onBack: () => void; onCreated: (id: string) => void;
}) {
  const [result, setResult] = useState<SuggestResult | null>(null);
  const [extra, setExtra] = useState<SupplierSuggestion[]>([]);
  // Supplier key → the lines asked of it; empty — the supplier is not asked.
  const [chosen, setChosen] = useState<Record<string, string[]>>({});
  const [directory, setDirectory] = useState<SupplierBrief[]>([]);
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    rfqApi.suggestForLines(lineIds)
      .then((next) => {
        setResult(next);
        setChosen(Object.fromEntries(
          next.suggestions.filter((item) => item.preselect).map((item) => [suggestionKey(item), relatedLines(item, lineIds)]),
        ));
      })
      .catch((reason) => setError(failureText(reason, "Подсказка не загрузилась")));
    rfqApi.directory().then(({ suppliers }) => setDirectory(suppliers)).catch(() => setDirectory([]));
  }, [lineIds]);

  const items = [...(result?.suggestions ?? []), ...extra];
  const choose = (key: string, ids: string[]) => setChosen((current) => ({ ...current, [key]: ids }));

  function addByName(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    const item = manualSuggestion(name, directory);
    const key = suggestionKey(item);
    if (!items.some((existing) => suggestionKey(existing) === key)) setExtra((current) => [...current, item]);
    setChosen((current) => (current[key]?.length ? current : { ...current, [key]: lineIds }));
    setName("");
  }

  async function create() {
    setBusy(true);
    setError("");
    try {
      const created = await rfqApi.create(lineIds, title.trim(), picks);
      onCreated(created.id);
    } catch (reason) {
      setError(failureText(reason, "Запрос не создан"));
      setBusy(false);
    }
  }

  const picks = picksWithLines(items, chosen);
  const asks = picks.reduce((sum, pick) => sum + (pick.line_ids?.length ?? 0), 0);
  return (
    <div className="rfq-suggest" aria-label="Поставщики для запроса" role="region">
      <div className="rfq-pool-head">
        <h3>Запрос КП по {countLabel(lineIds.length, ["строке", "строкам", "строкам"])}</h3>
        <button type="button" className="link-button" style={{ padding: 0 }} onClick={onBack}>← к строкам</button>
      </div>
      <label className="rfq-suggest-title">Название (необязательно)
        <input value={title} maxLength={255} onChange={(event) => setTitle(event.target.value)} placeholder="Например, разъёмы для RND.012" />
      </label>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!result && !error && <div className="empty">Подбираю поставщиков…</div>}
      {result && items.length === 0 && (
        <div className="empty">Подсказать некого: истории закупок этих позиций нет, в строках поставщик не указан, у поставщиков справочника нет таких категорий. Добавьте поставщика по названию.</div>
      )}
      {items.length > 0 && <SupplierLinesList items={items} lines={lines} chosen={chosen} onChange={choose} />}
      <form className="rfq-add-supplier" onSubmit={addByName}>
        <input
          aria-label="Поставщик по названию" list="rfq-pool-directory" value={name} placeholder="Поставщик из справочника или новый"
          onChange={(event) => setName(event.target.value)}
        />
        <datalist id="rfq-pool-directory">
          {directory.map((supplier) => <option key={supplier.supplier_id} value={supplier.name} />)}
        </datalist>
        <button type="submit" className="secondary small" disabled={!name.trim()}>+ поставщик по названию</button>
      </form>
      <div className="bulk-bar rfq-pool-bar" role="toolbar" aria-label="Создание запроса">
        <span>Поставщиков: {picks.length} · запросов позиций: {asks}</span>
        <span className="spacer" />
        <button type="button" className="secondary small" onClick={onBack}>Назад</button>
        <button type="button" className="primary small" disabled={busy || picks.length === 0} onClick={() => void create()}>Создать запрос</button>
      </div>
    </div>
  );
}
