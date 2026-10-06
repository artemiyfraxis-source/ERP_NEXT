import { useEffect, useState, type FormEvent } from "react";
import { urlParam } from "./navigation";
import { DataTable, matchesWords } from "./DataTable";
import { failureText, procurementApi, type ProcurementLine } from "./api";
import type { LineEditing } from "./ProcurementCellEdit";
import { useInternalProjects } from "./internalProjects";
import { buildSheetColumns } from "./ProcurementSheetColumns";

/* Лист закупки целиком и обзор «По поставщикам» — то, что в SUPPLYMAP давали вид purchase
   (portalViewDefs_) и portalPurchaseBySupplier. Лист открыт на чтение; правятся только «Комментарий» и
   «Ожид. дата» — в своих строках закупщика, прямо в ячейке (колонки и их порядок — ProcurementSheetColumns). */

const NO_SUPPLIER = "Без поставщика";

/** Колонки обзора «По поставщикам»: часть листа, в своём порядке. */
const SUPPLIER_VIEW_KEYS = ["status", "designation", "name", "buyer", "order"];

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

/** `buyerName` — имя вошедшего: правка комментария и даты открыта только в строках, где он закупщик. */
export function ProcurementSheetView({ buyerName = "" }: { buyerName?: string } = {}) {
  useInternalProjects(); // option projects.internal: the «Проект» column shows «ВП-001 · Название»
  const [lines, setLines] = useState<ProcurementLine[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [buyers, setBuyers] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [draft, setDraft] = useState({ q: urlParam("q"), status: "", buyer: "" });
  const [applied, setApplied] = useState(draft);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load(filters: typeof draft, after?: string) {
    setLoading(true);
    setError("");
    try {
      const result = await procurementApi.sheet(filters, after);
      setLines((current) => (after ? [...current, ...result.lines.filter((line) => !current.some((item) => item.id === line.id))] : result.lines));
      setCursor(result.next_cursor);
      setBuyers(result.buyers);
      setApplied(filters);
    } catch (reason) {
      setError(failureText(reason, "Лист закупки не загрузился"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(draft);
    procurementApi.meta().then((meta) => setStatuses(meta.statuses)).catch(() => undefined);
  }, []);

  /** Свежая строка после правки в ячейке — на её место в листе, без перезагрузки страниц. */
  const replaceLine = (saved: ProcurementLine) => setLines((current) => current.map((item) => (item.id === saved.id ? saved : item)));

  /** 409: строку уже меняли — перечитываем именно её (поиск по System ID, затем по id среди найденных). */
  async function reloadLine(stale: ProcurementLine) {
    const result = await procurementApi.sheet({ q: stale.legacy_id });
    const fresh = result.lines.find((item) => item.id === stale.id);
    if (fresh) replaceLine(fresh);
    else await load(applied);
  }

  const editing: LineEditing = { onSaved: replaceLine, onConflict: reloadLine };
  const columns = buildSheetColumns({ editing, buyerName, disabled: loading });

  // Server-side отбор: status and buyer apply at once, the search on «Найти»/Enter — all in the table's own toolbar.
  function apply(next: typeof draft) {
    setDraft(next);
    void load(next);
  }

  return (
    <section className="panel table-panel procurement-sheet">
      <div className="panel-heading"><h2>Весь лист закупки</h2><span>Все статусы и закупщики · в своих строках правятся «Комментарий» и «Ожид. дата»</span></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <DataTable
        label="Лист закупки"
        rows={lines}
        columns={columns}
        rowKey={(line) => line.id}
        storageKey="procurement-sheet-v2"
        exportName="Лист закупки"
        emptyText={loading ? "Загружаем…" : "Строк нет"}
        onSearch={(q) => apply({ ...draft, q })}
        searchLabel="Поиск"
        searchPlaceholder="ID, наименование, поставщик, № заказа — «Найти» ищет по всему листу"
        toolbar={<>
          <select aria-label="Статус" value={draft.status} disabled={loading} onChange={(event) => apply({ ...draft, status: event.target.value })}>
            <option value="">Статус: все</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
          <select aria-label="Закупщик" value={draft.buyer} disabled={loading} onChange={(event) => apply({ ...draft, buyer: event.target.value })}>
            <option value="">Закупщик: все</option>
            {buyers.map((buyer) => <option key={buyer} value={buyer}>{buyer}</option>)}
          </select>
        </>}
      />
      {cursor && (
        <button className="secondary" disabled={loading} onClick={() => void load(applied, cursor)}>
          {loading ? "Загружаем…" : "Показать ещё"}
        </button>
      )}
    </section>
  );
}

const supplierViewColumns = (() => {
  const all = buildSheetColumns();
  return SUPPLIER_VIEW_KEYS.flatMap((key) => all.filter((column) => column.key === key)).map((column) => ({ ...column, hidden: false }));
})();

type SupplierGroup = { name: string; lines: ProcurementLine[]; statuses: Map<string, number>; orders: Set<string> };

/** `pbsMatch_`: все слова — в поставщике, наименовании, заказе, назначении, статусе или закупщике. */
function overviewMatch(line: ProcurementLine, query: string) {
  return matchesWords([line.supplier, line.calculated_name, line.order_number, line.designation, line.status, line.buyer], query);
}

export function ProcurementBySupplier() {
  const [lines, setLines] = useState<ProcurementLine[]>([]);
  const [stages, setStages] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [supplier, setSupplier] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    procurementApi
      .bySupplier()
      .then((result) => {
        setLines(result.lines);
        setStages(result.stages);
      })
      .catch((reason) => setError(message(reason, "Сервер не ответил.")))
      .finally(() => setLoading(false));
  }, []);

  const rows = lines.filter((line) => overviewMatch(line, query));
  const groups = new Map<string, SupplierGroup>();
  for (const line of rows) {
    const name = line.supplier || NO_SUPPLIER;
    const group: SupplierGroup = groups.get(name) ?? { name, lines: [], statuses: new Map(), orders: new Set() };
    group.lines.push(line);
    group.statuses.set(line.status, (group.statuses.get(line.status) ?? 0) + 1);
    if (line.order_number) group.orders.add(line.order_number);
    groups.set(name, group);
  }
  // Больше всего висит — выше: с этих поставщиков закупщик и начинает разбор.
  const list = [...groups.values()].sort((a, b) => b.lines.length - a.lines.length || a.name.localeCompare(b.name, "ru"));
  const opened = supplier ? groups.get(supplier) : undefined;

  return (
    <section className="panel table-panel">
      <div className="panel-heading"><h2>Закупка по поставщикам</h2><span>Только просмотр</span></div>
      <p className="hint">Карточка на поставщика: сколько у него позиций и на каких стадиях. Открой карточку — увидишь сами позиции. Стадии «В пути» и «Готово» сюда не попадают: первые живут в приёмке, вторые уже на складе.</p>
      <input className="search" type="search" aria-label="Поиск по поставщикам" placeholder="поиск: поставщик, наименование, № заказа…" value={query} onChange={(event) => { setQuery(event.target.value); setSupplier(null); }} />
      {error && <div className="error-banner" role="alert">{error}</div>}
      {loading && <div className="empty">Загружаю…</div>}
      {!loading && !error && !rows.length && <div className="empty">{query ? "Ничего не нашлось." : "Незакрытых позиций в Закупке нет."}</div>}
      {opened ? (
        <>
          <div className="inline-action">
            <button type="button" className="secondary small" onClick={() => setSupplier(null)}>← Все поставщики</button>
            <strong>{opened.name}</strong><small>позиций: {opened.lines.length}</small>
          </div>
          <DataTable
            label={`Позиции поставщика ${opened.name}`}
            rows={opened.lines}
            columns={supplierViewColumns}
            rowKey={(line) => line.id}
          />
        </>
      ) : (
        <div className="tiles">
          {list.map((group) => {
            // Порядок стадий — как в пайплайне (PUR_STATUS_ORDER с сервера); статусы вне этих пяти чипа не получают, как в SUPPLYMAP.
            const order = stages.length ? stages : [...group.statuses.keys()];
            return (
              <button type="button" className="tile" key={group.name} title="Показать позиции поставщика" onClick={() => setSupplier(group.name)}>
                <div className="tile-body">
                  <span className="t-h">{group.name}</span>
                  <span className="t-in">{order.filter((status) => group.statuses.get(status)).map((status) => <span className="chip" key={status}>{status} · {group.statuses.get(status)} </span>)}</span>
                  <span className="t-sub">{group.orders.size ? `заказов: ${group.orders.size}` : "заказ не сформирован"}</span>
                </div>
                <span className="tile-count">поз.: {group.lines.length}</span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
