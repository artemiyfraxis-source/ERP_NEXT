import { useState, type ReactNode } from "react";
import { failureText, shippingApi, type Shipment } from "./api";
import { isPickup, needsTrack, termsText } from "./carriers";
import { DataTable, matchesWords, type DataColumn, type DataPreset } from "./DataTable";
import { lineGrns } from "./dispatchPositionRows";
import { formatDate, formatDay, formatNumber, NBSP } from "./format";
import { PageWindow } from "./PageWindow";
import { useCanPrint } from "./rights";
import { UpdCell } from "./ShipmentGroupHeader";
import { compareShipments, shipmentDateKind, shipmentDay } from "./shipmentOrder";
import { openShippingForms } from "./shippingPrint";
import { StatusChip } from "./StatusChip";
import "./shipmentTable.css";

const sent = (shipment: Shipment) => ["Отгружена", "Закрыта"].includes(shipment.status);
const projectsOf = (shipment: Shipment) => [...new Set(shipment.lines.map((line) => line.project))];

export function shipmentSearch(shipment: Shipment): string[] {
  return [shipment.number, shipment.status, shipment.customer, shipment.receiver, shipment.shipping_point, shipment.phone,
    shipment.contact, shipment.carrier, termsText(shipment.terms), shipment.track_number, shipment.upd_type ?? "", shipment.upd_number,
    shipment.upd_url, shipment.pl_number, shipment.dn_number, shipment.note, shipment.tk_note, shipment.manager, shipment.weight,
    shipmentDay(shipment), formatDay(shipmentDay(shipment)), shipment.created_at, shipment.deadline ?? "", shipment.packed_at ?? "", shipment.shipped_at ?? "", shipment.upd_date ?? "",
    ...(shipment.missing_data ?? []), ...(shipment.missing_ship ?? []),
    ...shipment.lines.flatMap((line) => [line.project, line.item_index, line.article, line.name, line.requested, line.picked, line.shortage,
      line.unit, ...lineGrns(line), ...(line.lots ?? []).flatMap((lot) => [lot.grn, lot.quantity])]),
    ...shipment.boxes.flatMap((box) => [String(box.place), box.kind, box.dims, box.net, box.gross, box.seal,
      ...box.items.flatMap((item) => [item.item_index, item.quantity])])];
}

export function shipmentQuantities(shipment: Shipment): string[] {
  const units = new Map<string, number>();
  for (const line of shipment.lines) units.set(line.unit, (units.get(line.unit) ?? 0) + Number(line.picked));
  return [...units].map(([unit, quantity]) => `${formatNumber(quantity)}${NBSP}${unit}`);
}

function ShipmentDetail({ shipment, children }: { shipment: Shipment; children?: ReactNode }) {
  return <div className="shipment-detail">
    <h3>Позиции отправки · {shipment.number}</h3>
    <div className="table-scroll"><table className="ship-lines">
      <thead><tr><th>Проект · КБ</th><th>Наименование · артикул</th><th className="num">Запрошено</th><th className="num">Набрано</th><th className="num">Уехало</th><th className="num">Недобор</th><th>ГРН · количество партии</th></tr></thead>
      <tbody>{shipment.lines.map((line) => <tr key={line.id}>
        <td>{line.project} · {line.item_index}</td><td>{line.name}{line.article && <small>{line.article}</small>}</td>
        <td className="num">{formatNumber(line.requested)}{NBSP}{line.unit}</td><td className="num">{formatNumber(line.picked)}{NBSP}{line.unit}</td>
        <td className="num">{sent(shipment) ? `${formatNumber(line.picked)}${NBSP}${line.unit}` : "—"}</td>
        <td className="num">{formatNumber(line.shortage)}{NBSP}{line.unit}</td>
        <td>{lineGrns(line).map((grn) => <div key={grn}>{grn}</div>)}{line.lots?.map((lot, index) => <small key={index}>{lot.grn}: {formatNumber(lot.quantity)}{NBSP}{line.unit}</small>)}</td>
      </tr>)}</tbody>
    </table></div>
    {!shipment.lines.length && <p className="muted">В отправке нет позиций.</p>}
    <dl className="shipment-detail-fields">
      {[["Контакт", shipment.contact], ["Менеджер", shipment.manager], ["Создана", formatDate(shipment.created_at)],
        ["Срок", formatDay(shipment.deadline)], ["Упакована", formatDay(shipment.packed_at)], ["Отгружена", formatDay(shipment.shipped_at)],
        ["Вес брутто, кг", shipment.weight], ["Комментарий", shipment.note], ["Для перевозчика", shipment.tk_note],
        ["Без УПД разрешил", shipment.no_upd_approved_by], ["Разрешение без УПД", formatDate(shipment.no_upd_approved_at)],
        ["Не хватает данных", shipment.missing_data?.join(", ")], ["Для отгрузки", shipment.missing_ship?.join(", ")]].map(([label, value]) =>
        <div key={label}><dt>{label}</dt><dd>{value || "—"}</dd></div>)}
    </dl>
    {shipment.boxes.length > 0 && <><h3>Короба · {shipment.boxes.length}</h3><div className="table-scroll"><table className="ship-lines">
      <thead><tr><th>Место · вид</th><th>Состав</th><th className="num">Нетто, кг</th><th className="num">Брутто, кг</th><th>Габариты, см</th><th>Пломба</th></tr></thead>
      <tbody>{shipment.boxes.map((box) => <tr key={box.place}><td>{box.place} · {box.kind}</td>
        <td>{box.items.map((item, index) => <div key={index}>{item.item_index}: {formatNumber(item.quantity)}</div>)}</td>
        <td className="num">{formatNumber(box.net) || "—"}</td><td className="num">{formatNumber(box.gross) || "—"}</td><td>{box.dims || "—"}</td><td>{box.seal || "—"}</td></tr>)}</tbody>
    </table></div></>}
    {children}
  </div>;
}

/** Shipment rows share operational cells; the separate position view retains the existing Excel contract. */
export function ShipmentTable<T>({ shipments, label, storageKey, onOpenShipment, onOpenProject, onError, actions, renderOperations,
  positions, positionColumns, positionKey, positionShipment, positionLine, initialQuery = "", presets, emptyText }: {
  shipments: Shipment[]; label: string; storageKey: string; onOpenShipment?: (shipment: Shipment) => void;
  onOpenProject?: (project: string, shipment: Shipment) => void; onError: (error: string) => void;
  actions?: (shipment: Shipment) => ReactNode; renderOperations?: (shipment: Shipment) => ReactNode;
  positions: T[]; positionColumns: DataColumn<T>[]; positionKey: (row: T) => string;
  positionShipment: (row: T) => Shipment;
  positionLine: (row: T) => Shipment["lines"][number] | undefined;
  initialQuery?: string; presets?: DataPreset<Shipment>[]; emptyText?: string;
}) {
  const canPrint = useCanPrint();
  const [exportOpen, setExportOpen] = useState(false);
  const [query, setQuery] = useState(initialQuery);
  const [status, setStatus] = useState("");
  const [preset, setPreset] = useState("");
  const sorted = [...new Map(shipments.map((shipment) => [shipment.id, shipment])).values()].sort(compareShipments);
  const projectShips = new Map<string, Shipment[]>();
  for (const shipment of [...sorted].reverse()) for (const project of projectsOf(shipment)) {
    const group = projectShips.get(project) ?? [];
    group.push(shipment); projectShips.set(project, group);
  }
  const print = async (shipment: Shipment, kind: "pl" | "dn") => {
    try { openShippingForms((await shippingApi.print(shipment.id, kind)).forms); }
    catch (reason) { onError(failureText(reason, "Не удалось открыть документ")); }
  };
  const document = (shipment: Shipment, kind: "pl" | "dn") => {
    const number = kind === "pl" ? shipment.pl_number : shipment.dn_number;
    const blocked = kind === "dn" && ["Новая", "Собрана", "Упакована"].includes(shipment.status) && shipment.missing_data?.length > 0;
    return <span title={blocked ? `Не хватает: ${shipment.missing_data.join(", ")}` : kind === "pl" ? "Упаковочный лист" : "Отгрузочная ведомость"}>
      <span className="muted">{kind.toUpperCase()}: </span>{number ? canPrint
        ? <button type="button" className="link-button" disabled={blocked} aria-label={`Печать ${kind.toUpperCase()} ${number}`}
          onClick={(event) => { event.stopPropagation(); void print(shipment, kind); }}>{number}</button>
        : <span>{number}</span> : "—"}
    </span>;
  };
  const cell = (children: ReactNode) => <span className="shipment-cell">{children}</span>;
  const columns: DataColumn<Shipment>[] = [
    { key: "date", title: "Дата", value: shipmentDay, width: 92, render: (shipment) => cell(<><span>{formatDay(shipmentDay(shipment))}</span><small className="muted">{shipmentDateKind(shipment)}</small></>) },
    { key: "project", title: "Проект / заказчик", value: (shipment) => [...projectsOf(shipment), shipment.customer].join(" · "), wrap: true, minWidth: 124, maxWidth: 164,
      render: (shipment) => cell(<>{projectsOf(shipment).map((project) => onOpenProject
        ? <button key={project} type="button" className="link-button" onClick={(event) => { event.stopPropagation(); onOpenProject(project, shipment); }}>{project}</button>
        : <strong key={project}>{project}</strong>)}<small>{shipment.customer || "—"}</small></>) },
    { key: "number", title: "Отправка", value: (shipment) => shipment.number, wrap: true, minWidth: 112, maxWidth: 140,
      render: (shipment) => cell(<>{onOpenShipment ? <button type="button" className="link-button" aria-label={`Данные отправки ${shipment.number}`}
        onClick={(event) => { event.stopPropagation(); onOpenShipment(shipment); }}>{shipment.number}</button> : <strong>{shipment.number}</strong>}
      {projectsOf(shipment).map((project) => { const group = projectShips.get(project)!; return <small className="muted" key={project}>
        {projectsOf(shipment).length > 1 && `${project}: `}Отгрузка {group.findIndex((item) => item.id === shipment.id) + 1} из {group.length}</small>; })}
      {actions && <span className="shipment-actions" onClick={(event) => event.stopPropagation()}>{actions(shipment)}</span>}</>) },
    { key: "status", title: "Статус", value: (shipment) => shipment.status, width: 96, render: (shipment) => <StatusChip status={shipment.status} /> },
    { key: "quantity", title: "Количество", value: (shipment) => shipmentQuantities(shipment).join("; "), align: "right", width: 92,
      render: (shipment) => cell(<>{shipmentQuantities(shipment).map((quantity) => <strong key={quantity}>{quantity}</strong>)}
        <small className="muted">КБ: {new Set(shipment.lines.map((line) => `${line.project}\u0001${line.item_index}`)).size} · раскрыть</small></>) },
    { key: "delivery", title: "Доставка", value: (shipment) => [termsText(shipment.terms), shipment.carrier, shipment.track_number].filter(Boolean).join(" · "), wrap: true, minWidth: 108, maxWidth: 144,
      render: (shipment) => cell(<>{isPickup(shipment.carrier) || shipment.terms === "САМОВЫВОЗ" ? <span>Самовывоз</span>
        : <><span>{termsText(shipment.terms) || "Способ не указан"}</span><span>{shipment.carrier || "ТК не указана"}</span></>}
        {shipment.track_number ? <small>Трек: {shipment.track_number}</small> : needsTrack(shipment) && <small className="tone-warn">Трек не заполнен</small>}</>) },
    { key: "receiver", title: "Получатель", value: (shipment) => [shipment.receiver, shipment.phone, shipment.shipping_point].filter(Boolean).join(" · "), wrap: true, minWidth: 132, maxWidth: 184,
      render: (shipment) => cell(<><span>{shipment.receiver || "Получатель не указан"}</span><span>{shipment.phone || "Телефон не указан"}</span><small>{shipment.shipping_point || "Адрес не указан"}</small></>) },
    { key: "upd", title: "УПД", value: (shipment) => [shipment.upd_type, shipment.upd_number, shipment.upd_date].filter(Boolean).join(" · "), wrap: true, minWidth: 84, maxWidth: 112,
      render: (shipment) => cell(<><UpdCell shipment={shipment} />{shipment.upd_date && <small>{formatDay(shipment.upd_date)}</small>}</>) },
    { key: "documents", title: "PL / DN", value: (shipment) => `${shipment.pl_number} ${shipment.dn_number}`, wrap: true, minWidth: 96, maxWidth: 132,
      render: (shipment) => cell(<>{document(shipment, "pl")}{document(shipment, "dn")}</>) },
  ];
  const activePreset = presets?.find((item) => item.label === preset);
  const filtered = sorted.filter((shipment) => (!status || shipment.status === status)
    && (!preset || activePreset?.test(shipment))
    && matchesWords([...shipmentSearch(shipment), ...columns.map((column) => column.value(shipment))], query));
  const selectedIds = new Set(filtered.map((shipment) => shipment.id));
  return <div className="shipment-table">
    {presets && <div className="seg-tabs" role="group" aria-label="Отбор отправок">
      {[{ label: "", title: "Все" }, ...presets.map((item) => ({ label: item.label, title: item.label }))].map((item) =>
        <button key={item.label} type="button" className={preset === item.label ? "on" : undefined} aria-pressed={preset === item.label} onClick={() => setPreset(item.label)}>{item.title}</button>)}
    </div>}
    <DataTable label={label} storageKey={storageKey} rows={filtered} columns={columns} rowKey={(shipment) => shipment.id}
      hideSearch renderDetail={(shipment) => <ShipmentDetail shipment={shipment}>{renderOperations?.(shipment)}</ShipmentDetail>}
      rowLabel={(shipment) => shipment.number} rowTitle={() => "Раскрыть позиции и подробности отправки"}
      toolbar={<><form className="table-search shipment-search" role="search" onSubmit={(event) => event.preventDefault()}>
        <input type="search" className="search" aria-label={`Поиск: ${label}`} placeholder="Проект, КБ, ГРН, получатель, УПД или трек…"
          autoComplete="off" name="shipment-query" value={query} onChange={(event) => setQuery(event.target.value)} />
      </form><select aria-label="Статус отправки" value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="">Все статусы</option>{[...new Set(sorted.map((shipment) => shipment.status))].map((value) => <option key={value}>{value}</option>)}
      </select><button type="button" className="secondary small" onClick={() => setExportOpen(true)}>Excel по позициям</button></>}
      emptyText={emptyText} />
    {exportOpen && <PageWindow label={`${label} — по позициям`} close={() => setExportOpen(false)} width="min(1760px, 100%)" dismissable>
      <section className="panel"><header className="page-head"><h2>{label} — по позициям</h2><button type="button" className="secondary small" onClick={() => setExportOpen(false)}>Закрыть</button></header>
        <DataTable label={`${label} — по позициям`} rows={positions.filter((row) => {
          const shipment = positionShipment(row), line = positionLine(row);
          return selectedIds.has(shipment.id) && (!activePreset || activePreset.test({ ...shipment, lines: line ? [line] : [] }));
        })} columns={positionColumns} rowKey={positionKey}
          initialQuery={query} searchText={(row) => { const line = positionLine(row); return shipmentSearch({ ...positionShipment(row), lines: line ? [line] : [] }); }}
          exportName={label} searchPlaceholder="Проект, КБ, наименование, ГРН или отправка" />
      </section>
    </PageWindow>}
  </div>;
}
