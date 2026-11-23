import { useCallback, useEffect, useRef, useState } from "react";
import { failureText, referencesApi, type ProductionInstrument, type ReferenceInstrumentsResult } from "./api";
import { AddColumnButton, useColumnRemoval } from "./CustomColumn";
import { DataTable, type DataColumn, type DataPreset } from "./DataTable";
import { formatDay, formatNumber } from "./format";
import { InstrumentWindow } from "./InstrumentWindow";
import { StatusChip } from "./StatusChip";

const columns: DataColumn<ProductionInstrument>[] = [
  { key: "inventory_number", title: "Инв. №", value: (row) => row.inventory_number, width: 96 },
  { key: "name", title: "Наименование", value: (row) => row.name, grow: true, minWidth: 200,
    render: (row) => <span className="instrument-cell" title={row.name}>{row.name || "—"}</span> },
  { key: "kind", title: "Вид", value: (row) => row.kind, filter: true, width: 96,
    render: (row) => <span title={row.kind}>{row.kind === "Средство измерений" ? "СИ" : "Принадл."}</span> },
  { key: "quantity", title: "Кол-во, шт.", value: (row) => Number(row.quantity), numeric: true, width: 96 },
  { key: "serial_number", title: "Зав. №", value: (row) => row.serial_number, width: 112 },
  { key: "verification_due", title: "Поверка до", value: (row) => row.verification_due,
    render: (row) => <span className="instrument-due">{formatDay(row.verification_due) || "—"}</span>, width: 104 },
  { key: "status", title: "Статус", value: (row) => row.status, filter: true, width: 152,
    render: (row) => <StatusChip status={row.status} title={row.status}>{row.status === "Поверка не подтверждена" ? "Не подтверждена" : row.status}</StatusChip> },
  { key: "storage_location", title: "Место", value: (row) => row.storage_location, filter: true, width: 112 },
  { key: "manufacturer", title: "Производитель", value: (row) => row.manufacturer, filter: true, hidden: true, export: true },
  { key: "article", title: "Артикул", value: (row) => row.article, filter: true, hidden: true, export: true },
  { key: "measurement_category", title: "Категория измерений", value: (row) => row.measurement_category, filter: true, hidden: true, export: true },
  { key: "notes", title: "Примечание", value: (row) => row.notes, hidden: true, export: true },
  { key: "instrument_type", title: "Тип", value: (row) => row.instrument_type, filter: true, hidden: true, export: true },
];

// Only offer a status that exists in the registry; unused instruments are not written-off instruments.
const statusPresets = [
  ["Действует", "В работе"], ["На поверке", "На поверке"], ["Списан", "Списаны"],
  ["Не используется", "Не используется"], ["Поверка не подтверждена", "Поверка не подтверждена"],
] as const;

export function ReferenceInstrumentRegistry({ onCount }: { onCount?: (count: number, quantity: number) => void }) {
  const [data, setData] = useState<ReferenceInstrumentsResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [record, setRecord] = useState<ProductionInstrument | null>(null);
  const countCallback = useRef(onCount);
  countCallback.current = onCount;
  const request = useRef(0);
  const [requested] = useState(() => new URLSearchParams(window.location.search).get("equipment") ?? "");
  const [requestedNumber] = useState(() => new URLSearchParams(window.location.search).get("si") ?? "");

  const reload = useCallback(async () => {
    const stamp = ++request.current;
    setError("");
    try {
      const result = await referencesApi.instruments();
      if (stamp !== request.current) return;
      setData(result);
      countCallback.current?.(result.count, Number(result.quantity));
    } catch (reason) {
      if (stamp === request.current) {
        setError(failureText(reason, "Не удалось загрузить средства измерений"));
        throw reason;
      }
    } finally { if (stamp === request.current) setLoading(false); }
  }, []);

  const select = useCallback((item: ProductionInstrument | null) => {
    setRecord(item);
    const url = new URL(window.location.href);
    if (item) {
      url.searchParams.set("section", "references");
      url.searchParams.set("tab", "ob_measuring");
      url.searchParams.set("equipment", item.id);
    } else url.searchParams.delete("equipment");
    url.searchParams.delete("si");
    window.history.replaceState(window.history.state, "", url);
  }, []);

  useEffect(() => {
    void reload().catch(() => undefined);
    let active = true;
    if (requested) {
      void referencesApi.instrument(requested).then(({ instrument }) => {
        if (active) select(instrument);
      }).catch((reason) => { if (active) setError(failureText(reason, "СИ не найдено")); });
    }
    return () => { active = false; request.current += 1; };
  }, [reload, requested, select]);

  const numberOpened = useRef(false);
  useEffect(() => {
    if (!data || !requestedNumber || requested || numberOpened.current) return;
    numberOpened.current = true;
    const item = data.instruments.find((row) => row.inventory_number === requestedNumber);
    if (item) select(item); else setError(`${requestedNumber} — запись не найдена`);
  }, [data, requested, requestedNumber, select]);

  const rows = data?.instruments ?? [];
  const removal = useColumnRemoval("ob_measuring", Boolean(data?.can_edit_custom),
    (name) => rows.filter((item) => String(item.custom_fields?.[name] ?? "").trim()).length, reload);
  const ownColumns: DataColumn<ProductionInstrument>[] = (data?.custom_columns ?? []).map((name) => ({
    key: `custom:${name}`, title: name, value: (row) => row.custom_fields?.[name] ?? "",
    filter: true, remove: removal.removeOf(name),
  }));
  const presets: DataPreset<ProductionInstrument>[] = statusPresets
    .filter(([status]) => rows.some((row) => String(row.status) === status))
    .map(([status, label]) => ({ label, test: (row) => String(row.status) === status }));

  return <div className="panel instrument-registry">
    <div className="manual-stock-head"><h2>Средства измерений</h2>
      {data && <span className="tnum">{formatNumber(data.count)} записей · {formatNumber(data.quantity)} шт.</span>}
    </div>
    {error && <div className="error-banner" role="alert">{error}
      {!data && <button type="button" className="secondary small" onClick={() => { setLoading(true); void reload().catch(() => undefined); }}>Повторить</button>}
    </div>}
    <DataTable label="Средства измерений" storageKey="reference-instruments-v1" sharedKey="ob_measuring"
      rows={rows} columns={[...columns, ...ownColumns]} rowKey={(item) => item.id}
      rowLabel={(item) => item.inventory_number || item.name} onRowClick={select} pageSize={200}
      loading={loading} presets={presets} searchLabel="Найти средство измерений"
      searchPlaceholder="Инв. №, наименование, заводской №…"
      searchText={(item) => [item.doc_designation, item.warehouse_grn]}
      exportName="Средства измерений" emptyText="Средств измерений нет — добавьте запись в реестре ОТК."
      toolbar={data?.can_edit_custom ? <AddColumnButton tab="ob_measuring" onAdded={reload} />
        : <span title="Нужно право правки базы данных"><button type="button" className="secondary small" disabled>+ Столбец</button></span>} />
    {removal.dialog}
    {data && !data.can_edit_custom && <p className="muted-text">Столбцы и их значения доступны для просмотра. Для изменения нужно право правки базы данных.</p>}
    {record && data && <InstrumentWindow key={record.id} instrument={record} kind={record.kind} parentId={record.parent_id}
      instruments={rows} source="references" canEditIdentity={data.can_edit_identity} canEditCustom={data.can_edit_custom}
      customColumns={data.custom_columns} close={() => select(null)} onChanged={reload} onAccessory={() => undefined} />}
  </div>;
}
