import { useEffect, useState, type FormEvent } from "react";
import { DataTable, matchesWords, type DataColumn } from "./DataTable";
import { procurementApi, type ProcurementLine } from "./api";

/* Лист закупки целиком и обзор «По поставщикам» — то, что в SUPPLYMAP давали вид purchase
   (portalViewDefs_) и portalPurchaseBySupplier. Оба только для чтения: правки живут на этапах
   в очередях закупщика. */

const NO_SUPPLIER = "Без поставщика";

export const sheetColumns: DataColumn<ProcurementLine>[] = [
  { key: "legacy_id", title: "System ID", value: (line) => line.legacy_id, render: (line) => <><strong>{line.legacy_id}</strong><small>рев. {line.revision}</small></> },
  { key: "status", title: "Статус", value: (line) => line.status, filter: true },
  { key: "priority", title: "Приоритет", value: (line) => line.priority, filter: true },
  { key: "task_type", title: "Тип задачи", value: (line) => line.task_type ?? "", filter: true, hidden: true },
  { key: "designation", title: "Назначение", value: (line) => line.designation },
  { key: "project", title: "Проект", value: (line) => line.project_reference, hidden: true },
  { key: "name", title: "Наименование", value: (line) => line.calculated_name, wrap: true },
  { key: "article", title: "Артикул КД", value: (line) => line.design_article, hidden: true },
  { key: "qty", title: "Кол-во", value: (line) => `${line.actual_quantity ?? line.calculated_quantity} ${line.actual_unit ?? line.calculated_unit}` },
  { key: "buyer", title: "Закупщик", value: (line) => line.buyer, filter: true },
  { key: "supplier", title: "Поставщик", value: (line) => line.supplier, filter: true },
  { key: "order", title: "№ заказа", value: (line) => line.order_number },
  { key: "sum", title: "Сумма (факт)", value: (line) => line.actual_sum, numeric: true },
  { key: "point", title: "Точка отгрузки", value: (line) => line.delivery_point, hidden: true },
  { key: "planned", title: "План. дата", value: (line) => line.planned_date },
  { key: "eta", title: "Ожид. дата", value: (line) => line.expected_arrival },
  { key: "initiator", title: "Инициатор", value: (line) => line.initiator, hidden: true },
  { key: "link", title: "Ссылка", value: (line) => line.link, hidden: true, render: (line) => line.link ? <a href={line.link} target="_blank" rel="noreferrer">открыть</a> : "—" },
];

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

export function ProcurementSheetView() {
  const [lines, setLines] = useState<ProcurementLine[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [buyers, setBuyers] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [draft, setDraft] = useState({ q: "", status: "", buyer: "" });
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
      setError(message(reason, "Лист закупки не загрузился"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(draft);
    procurementApi.meta().then((meta) => setStatuses(meta.statuses)).catch(() => undefined);
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    void load(draft);
  }

  return (
    <section className="panel table-panel">
      <div className="panel-heading"><h2>Весь лист закупки</h2><span>Все статусы и закупщики, только просмотр</span></div>
      <form className="inline-action" onSubmit={submit} aria-label="Отбор листа закупки">
        <label>Поиск<input type="search" value={draft.q} onChange={(event) => setDraft({ ...draft, q: event.target.value })} placeholder="ID, наименование, поставщик, № заказа…" /></label>
        <label>Статус<select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
          <option value="">все</option>
          {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
        </select></label>
        <label>Закупщик<select value={draft.buyer} onChange={(event) => setDraft({ ...draft, buyer: event.target.value })}>
          <option value="">все</option>
          {buyers.map((buyer) => <option key={buyer} value={buyer}>{buyer}</option>)}
        </select></label>
        <button className="primary small" disabled={loading}>{loading ? "Загружаем…" : "Показать"}</button>
      </form>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <DataTable
        label="Лист закупки"
        rows={lines}
        columns={sheetColumns}
        rowKey={(line) => line.id}
        storageKey="procurement-sheet"
        exportName="Лист закупки"
        emptyText={loading ? "Загружаем…" : "Строк нет"}
      />
      {cursor && (
        <button className="secondary" disabled={loading} onClick={() => void load(applied, cursor)}>
          {loading ? "Загружаем…" : "Показать ещё"}
        </button>
      )}
    </section>
  );
}

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
            columns={sheetColumns.filter((column) => ["status", "name", "designation", "order", "buyer"].includes(column.key)).map((column) => ({ ...column, hidden: false }))}
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
