import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type FinishedGood, type Shipment } from "./api";
import { CarrierSelect, isPickup, needsTrack, termsFor, termsText } from "./carriers";
import { DataTable, type DataColumn } from "./DataTable";
import { lineGrns } from "./dispatchPositionRows";
import { formatDay, formatNumber } from "./format";
import { PageWindow } from "./PageWindow";
import { useCanPrint } from "./rights";
import { Banners, ShipStatus, useMutate } from "./ShipmentCard";
import { ShipmentDataWindow, UPD_TYPES, updLabel } from "./ShipmentData";
import { orderShipmentRows, shipmentGroupKey, shipmentSequence } from "./ShipmentGroupHeader";
import { openShippingForms } from "./shippingPrint";
import { compareShipments, shipmentDay } from "./shipmentOrder";
import { shipmentSearch } from "./shipmentSearch";

/* Окна шапки «Приёмки с производства» (владелец 29.10.2026, экран «Отгрузка» убран): журнал отгрузок, КБ проекта в
   готовую продукцию вручную и заявка на отгрузку заранее. */

const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;

const JOURNAL_STATUS = ["Новая", "Собрана", "Упакована", "Отгружена", "Закрыта"];
const SHIPPED = ["Отгружена", "Закрыта"];

export interface JournalRow {
  key: string; project: string; shipment: Shipment; line: Shipment["lines"][number];
  date: string; ordered: number; shipped: number | ""; partial: boolean; weight: string; dims: string;
}

/** Вес брутто — сумма по местам; нет мест — вес отправки. Габариты — список по местам. */
function boxTotals(shipment: Shipment) {
  const gross = round2(shipment.boxes.reduce((sum, box) => sum + num(box.gross), 0));
  const dims = shipment.boxes.map((box) => box.dims.trim()).filter(Boolean);
  return { weight: gross > 0 ? String(gross).replace(".", ",") : shipment.weight, dims: dims.join("; ") };
}

/** Отменённые заявки видны только по прямой ссылке из поиска. */
export function journalRows(shipments: Shipment[], selectedId = ""): JournalRow[] {
  return shipments.filter((shipment) => (selectedId ? shipment.id === selectedId : JOURNAL_STATUS.includes(shipment.status)))
    .sort(compareShipments)
    .flatMap((shipment) => {
      const totals = boxTotals(shipment);
      return shipment.lines.map((line): JournalRow => {
        const ordered = num(line.requested);
        const gone = SHIPPED.includes(shipment.status);
        const shipped = gone ? num(line.picked) : "";
        return { key: `${shipment.id}:${line.id}`, project: line.project, shipment, line, date: shipmentDay(shipment), ordered, shipped,
          partial: gone && num(line.picked) < ordered, weight: totals.weight, dims: totals.dims };
      });
    });
}

/** Печать PL / DN по номеру прямо из строки таблицы (журнал, «Приёмка с производства»). */
export function useDocLink() {
  const canPrint = useCanPrint();
  const [error, setError] = useState("");
  const open = async (shipment: Shipment, document: "pl" | "dn") => {
    setError("");
    try { openShippingForms((await shippingApi.print(shipment.id, document)).forms); }
    catch (reason) { setError(failureText(reason, "Не удалось подготовить печать")); }
  };
  const link = (shipment: Shipment, document: "pl" | "dn") => {
    const number = document === "pl" ? shipment.pl_number : shipment.dn_number;
    const kind = document === "pl" ? "PL" : "DN";
    if (number && !canPrint) return <span>{number}</span>;
    const blocked = document === "dn" && ["Новая", "Собрана", "Упакована"].includes(shipment.status) && shipment.missing_data.length > 0;
    return number ? <button type="button" className="link-button ship-doc-link" disabled={blocked}
      title={blocked ? `Не хватает: ${shipment.missing_data.join(", ")}` : `Печать ${kind} ${number}`} aria-label={`Печать ${kind} ${number}`}
      onClick={(event) => { event.stopPropagation(); void open(shipment, document); }}>{number}</button> : <span className="muted">—</span>;
  };
  return { error, link };
}

export function JournalWindow({ shipments, close, initialQuery = "", selectedShipment }: { shipments: Shipment[]; close: () => void; initialQuery?: string; selectedShipment?: Shipment }) {
  const rows = useMemo(() => orderShipmentRows(journalRows(shipments, selectedShipment?.id)), [shipments, selectedShipment]);
  const sequence = useMemo(() => shipmentSequence(shipments.flatMap((shipment) => shipment.lines.map((line) => ({ project: line.project, shipment })))), [shipments]);
  const { error, link: docLink } = useDocLink();
  const [opened, setOpened] = useState<Shipment | null>(null);
  const day = (value: string | null | undefined) => formatDay(value) || "—";
  const pickup = (row: JournalRow) => isPickup(row.shipment.carrier) || row.shipment.terms === "САМОВЫВОЗ";
  const shipmentLink = (row: JournalRow) => <button type="button" className="link-button" aria-label={`Данные отправки ${row.shipment.number}`}
    onClick={() => setOpened(row.shipment)}>{row.shipment.number}</button>;
  const columns: DataColumn<JournalRow>[] = [
    { key: "kb", title: "КБ", value: (row) => row.line.item_index, width: 64 },
    { key: "name", title: "Наименование", value: (row) => row.line.name, wrap: true, grow: true, minWidth: 180, maxWidth: 260 },
    { key: "ordered", title: "В заказе", value: (row) => row.ordered, numeric: true, width: 88 },
    { key: "picked", title: "Набрано", value: (row) => num(row.line.picked), numeric: true, width: 88 },
    { key: "shipped", title: "Отгружено", value: (row) => row.shipped, numeric: true, width: 100,
      hint: "Уехавшее по отправке; пока отправка не ушла — пусто", render: (row) => row.shipped === "" ? "—"
        : <span className={row.partial ? "tone-warn" : undefined}>{formatNumber(row.shipped)}</span> },
    { key: "shortage", title: "Недобор", value: (row) => num(row.line.shortage), numeric: true, width: 88 },
    { key: "underload", title: "Недогруз", value: (row) => row.partial ? round2(row.ordered - Number(row.shipped)) : "", numeric: true, width: 88 },
    { key: "unit", title: "Ед.", value: (row) => row.line.unit, width: 48 },
    { key: "action", title: "Действие", value: () => "Данные отправки", width: 112,
      render: (row) => <button type="button" className="secondary small" aria-label={`Открыть ${row.project} ${row.line.item_index}`}
        onClick={() => setOpened(row.shipment)}>Данные</button> },
    { key: "grn", title: "ГРН", value: (row) => lineGrns(row.line).join(", "), width: 136 },
    { key: "lots", title: "Партии, шт", hint: "Сколько взято из каждой партии (ГРН)", wrap: true, minWidth: 160, maxWidth: 240,
      value: (row) => (row.line.lots ?? []).map((lot) => `${lot.grn}: ${formatNumber(lot.quantity)}`).join("; ") },
    { key: "places", title: "Места", hint: "Места (короба) с этой КБ и штуки в них", wrap: true, minWidth: 120, maxWidth: 200,
      value: (row) => row.shipment.boxes.flatMap((box) => box.items.filter((item) => item.item_index === row.line.item_index)
        .map((item) => `№${box.place}: ${formatNumber(item.quantity)}`)).join("; ") },
    { key: "seals", title: "Пломбы", wrap: true, minWidth: 104, maxWidth: 180,
      value: (row) => row.shipment.boxes.filter((box) => box.seal.trim() && box.items.some((item) => item.item_index === row.line.item_index))
        .map((box) => box.seal.trim()).join(", ") },
    { key: "project", title: "Проект", value: (row) => row.project, width: 104, render: (row) => <strong>{row.project}</strong> },
    { key: "customer", title: "Заказчик", value: (row) => row.shipment.customer, filter: true, width: 160 },
    { key: "shipment", title: "Отправка", value: (row) => row.shipment.number, width: 176, render: shipmentLink },
    { key: "sequence", title: "Отгрузка", value: (row) => sequence.get(shipmentGroupKey(row)) ?? "", width: 88 },
    { key: "status", title: "Статус", value: (row) => row.shipment.status, filter: true, width: 104, render: (row) => <ShipStatus status={row.shipment.status} /> },
    { key: "created", title: "Создана", value: (row) => row.shipment.created_at, width: 104, render: (row) => day(row.shipment.created_at) },
    { key: "deadline", title: "Срок", value: (row) => row.shipment.deadline, width: 104, render: (row) => day(row.shipment.deadline) },
    { key: "date", title: "Дата", value: (row) => row.date, width: 104, render: (row) => day(row.date) },
    { key: "packed", title: "Дата упаковки", value: (row) => row.shipment.packed_at, width: 120, render: (row) => day(row.shipment.packed_at) },
    { key: "shippedAt", title: "Отгружена", value: (row) => row.shipment.shipped_at, width: 104, render: (row) => day(row.shipment.shipped_at) },
    { key: "terms", title: "Способ доставки", value: (row) => pickup(row) ? "Самовывоз" : termsText(row.shipment.terms), filter: true, width: 144 },
    { key: "carrier", title: "ТК", value: (row) => pickup(row) ? "" : row.shipment.carrier, filter: true, width: 136 },
    { key: "track", title: "Трек", value: (row) => row.shipment.track_number, width: 144,
      render: (row) => row.shipment.track_number || (needsTrack(row.shipment) ? <span className="tone-warn">не заполнен</span> : "—") },
    { key: "receiver", title: "Организация", value: (row) => row.shipment.receiver, width: 160 },
    { key: "contact", title: "Получатель", value: (row) => row.shipment.contact, width: 160 },
    { key: "phone", title: "Телефон", value: (row) => row.shipment.phone, width: 136 },
    { key: "address", title: "Адрес", value: (row) => row.shipment.shipping_point, wrap: true, minWidth: 180, maxWidth: 240 },
    { key: "upd", title: "УПД", value: (row) => updLabel(row.shipment.upd_type), filter: true, width: 104 },
    { key: "updNumber", title: "№ УПД", value: (row) => row.shipment.upd_number, width: 104 },
    { key: "updDate", title: "Дата УПД", value: (row) => row.shipment.upd_date, width: 104, render: (row) => day(row.shipment.upd_date) },
    { key: "updFile", title: "Файл УПД", value: (row) => row.shipment.upd_url, width: 104,
      render: (row) => row.shipment.upd_url ? <a href={row.shipment.upd_url} target="_blank" rel="noreferrer">файл</a> : "—" },
    { key: "pl", title: "PL", value: (row) => row.shipment.pl_number, width: 144, render: (row) => docLink(row.shipment, "pl") },
    { key: "dn", title: "DN", value: (row) => row.shipment.dn_number, width: 144, render: (row) => docLink(row.shipment, "dn") },
    { key: "weight", title: "Вес", value: (row) => row.weight, numeric: true, width: 88, hint: "Брутто по местам, кг" },
    { key: "dims", title: "Габариты", value: (row) => row.dims, wrap: true, minWidth: 144, maxWidth: 200, hint: "Шир.×Длина×Высота, см — по местам" },
    { key: "note", title: "Комментарий", value: (row) => row.shipment.note, wrap: true, minWidth: 160, maxWidth: 240 },
  ];
  return <PageWindow label="Журнал отгрузок" close={close} width="min(1760px, 100%)" dismissable>
    <section className="panel intake-journal">
      <header className="page-head"><h2>Журнал отгрузок</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {selectedShipment && !selectedShipment.lines.length && <p className="muted"><strong>{selectedShipment.number}</strong>{" · "}
        <ShipStatus status={selectedShipment.status} /> · В отгрузке нет позиций.</p>}
      <DataTable label="Журнал отгрузок" storageKey="shipping-journal:positions:v2" rows={rows} columns={columns} rowKey={(row) => row.key}
        rowLabel={(row) => `${row.project} ${row.line.item_index}`}
        groupBy={shipmentGroupKey} groupSort groupLabel={(_name, group) => {
          const row = group[0];
          return <span><strong>Отгрузка {sequence.get(shipmentGroupKey(row))} · {row.shipment.number}</strong>{" "}
            <ShipStatus status={row.shipment.status} /></span>;
        }}
        initialQuery={initialQuery} exportName="Журнал отгрузок" searchPlaceholder="Проект, КБ, ГРН, организация, получатель, УПД или трек…"
        searchText={(row) => shipmentSearch({ ...row.shipment, lines: [row.line] })}
        presets={[{ label: "Не уехали", test: (row) => !SHIPPED.includes(row.shipment.status) }, { label: "Отгружены", test: (row) => SHIPPED.includes(row.shipment.status) },
          { label: "Частичные", test: (row) => row.partial }]}
        emptyText="В журнале пусто — строки появятся, когда упакуете принятое." />
      {opened && <ShipmentDataWindow shipment={opened} canEdit={false} canCorrectHistory={false} close={() => setOpened(null)} onSaved={() => undefined} />}
    </section>
  </PageWindow>;
}

/* ---------- «+ Позиция ГП»: КБ проекта в реестр готовой продукции вручную ---------- */

export function RegisterGoodWindow({ close, reload }: { close: () => void; reload: () => Promise<void> | void }) {
  const blank = { project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" };
  const [draft, setDraft] = useState(blank);
  const { busy, error, notice, mutate } = useMutate(reload);
  const labels = { project: "Проект", item_index: "КБ", customer: "Заказчик", article: "Артикул КД", name: "Наименование", ordered: "В заказе", unit: "Ед. изм." };
  return <PageWindow label="Позиция готовой продукции" close={close} width={880}>
    <form className="panel ship-request" onSubmit={(event) => { event.preventDefault(); void mutate("good:new", () => shippingApi.registerGood(draft), "КБ добавлена в готовую продукцию").then((saved) => { if (saved) close(); }); }}>
      <header className="page-head"><h2>КБ проекта в готовую продукцию</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      <div className="ship-form">
        {(["project", "item_index", "customer", "article", "name", "ordered", "unit"] as const).map((key) => <label key={key} className={key === "name" ? "ship-span2" : undefined}>{labels[key]}
          <input required={["project", "item_index", "name", "ordered", "unit"].includes(key)} type={key === "ordered" ? "number" : "text"} min={key === "ordered" ? "0.000001" : undefined} step={key === "ordered" ? "any" : undefined} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      </div>
      <div className="ship-actions"><span className="ship-actions-end"><button type="button" className="secondary" onClick={close}>Отмена</button><button className="primary" disabled={Boolean(busy)}>Сохранить</button></span></div>
    </form>
  </PageWindow>;
}

/* ---------- «+ Заявка на отгрузку»: заранее, больше чем на складе и из разных проектов ---------- */

export function CreateShipmentWindow({ close, reload }: { close: () => void; reload: () => Promise<void> | void }) {
  const blank = { customer: "", receiver: "", shipping_point: "", contact: "", phone: "", terms: "", carrier: "", deadline: "", upd_type: "" };
  const [goods, setGoods] = useState<FinishedGood[] | null>(null);
  const [draft, setDraft] = useState(blank);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [noDocs, setNoDocs] = useState(false);
  const [customerTyped, setCustomerTyped] = useState(false);
  const { busy, error, notice, mutate, setError } = useMutate(reload);
  useEffect(() => {
    let live = true;
    shippingApi.goods().then((result) => { if (live) setGoods(result.goods ?? []); })
      .catch((reason) => { if (live) { setGoods([]); setError(failureText(reason, "Не удалось загрузить готовую продукцию")); } });
    return () => { live = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const list = goods ?? [];
  const selected = list.filter((good) => num(quantities[good.id]) > 0);
  const open = list.filter((good) => num(good.sent) < num(good.ordered));
  // Заказчик по умолчанию — из готовой продукции выбранного проекта (сервер подставляет его так же); набранное руками важнее.
  const customer = customerTyped ? draft.customer : selected.find((good) => good.customer)?.customer ?? "";
  const payload = () => {
    const body: Record<string, unknown> = { ...draft, customer, no_docs: noDocs, lines: selected.map((good) => ({ good_id: good.id, quantity: quantities[good.id] })) };
    for (const key of ["terms", "upd_type", "deadline"]) if (!body[key]) body[key] = null;
    return body;
  };
  const text = (key: keyof typeof blank, label: string, className?: string) => <label key={key} className={className}>{label}
    <input placeholder={key === "customer" ? "по выбранному проекту" : key === "receiver" ? "как в документах" : key === "contact" ? "ФИО, кому передать груз" : "из карточки проекта"} value={key === "customer" ? customer : draft[key]}
      onChange={(event) => { if (key === "customer") setCustomerTyped(true); setDraft({ ...draft, [key]: event.target.value }); }} /></label>;
  return <PageWindow label="Заявка на отгрузку" close={close} width={1200}>
    <form className="panel ship-request" onSubmit={(event) => { event.preventDefault(); void mutate("shipment:new", () => shippingApi.create(payload()), "Заявка на отгрузку создана").then((saved) => { if (saved) close(); }); }}>
      <header className="page-head"><h2>Заявка на отгрузку</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      <div className="ship-form">
        {text("customer", "Заказчик")}{text("receiver", "Организация")}{text("contact", "Получатель")}{text("phone", "Телефон")}
        {text("shipping_point", "Адрес", "ship-span2")}
        <label>ТК<CarrierSelect label="ТК заявки" empty="из карточки проекта" value={draft.carrier} onChange={(carrier) => setDraft({ ...draft, carrier, terms: termsFor(carrier) ?? "" })} /></label>
        <label>Условия<span className="ship-terms">{termsText(draft.terms) || "из карточки проекта"}</span></label>
        <label>Тип УПД<select value={draft.upd_type} onChange={(event) => setDraft({ ...draft, upd_type: event.target.value })}><option value="">из карточки проекта</option>{UPD_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Дедлайн<input type="date" value={draft.deadline} onChange={(event) => setDraft({ ...draft, deadline: event.target.value })} /></label>
      </div>
      {!goods ? <div className="empty">Загрузка…</div> : <div className="table-scroll"><table className="ship-lines">
        <thead><tr><th>Проект</th><th>КБ</th><th>Наименование</th><th className="num">В заказе</th><th className="num">На складе</th><th className="num">Заказать</th></tr></thead>
        <tbody>{open.map((good) => <tr key={good.id}><td><strong>{good.project}</strong></td><td>{good.item_index}</td><td className="ship-name">{good.name}</td>
          <td className="num">{good.ordered}</td><td className="num">{good.remaining}</td>
          <td className="num"><input className="ship-qty" aria-label={`Заказать ${good.project} ${good.item_index}`} type="number" min="0" step="any" value={quantities[good.id] ?? ""} onChange={(event) => setQuantities({ ...quantities, [good.id]: event.target.value })} /></td></tr>)}</tbody>
      </table></div>}
      <div className="ship-actions">
        <label className="check-inline"><input type="checkbox" checked={noDocs} onChange={(event) => setNoDocs(event.target.checked)} />Без отгрузочных документов (только набрать)</label>
        <span className="ship-actions-end">
          <button type="button" className="secondary" onClick={close}>Отмена</button>
          <button className="primary" disabled={!selected.length || Boolean(busy)}>Создать заявку</button>
        </span>
      </div>
    </form>
  </PageWindow>;
}
