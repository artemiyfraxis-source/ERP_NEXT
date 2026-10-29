import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type FinishedGood, type Shipment } from "./api";
import { CarrierSelect, termsFor, termsText } from "./carriers";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";
import { PageWindow } from "./PageWindow";
import { useCanPrint } from "./rights";
import { Banners, ShipStatus, useMutate } from "./ShipmentCard";
import { UPD_TYPES, updLabel } from "./ShipmentData";
import { openShippingForms } from "./shippingPrint";

/* Окна шапки «Приёмки с производства» (владелец 29.10.2026, экран «Отгрузка» убран): журнал отгрузок, КБ проекта в
   готовую продукцию вручную и заявка на отгрузку заранее. */

const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;

/* ---------- Журнал: строка на позицию отправки, как журнал «ОТГРУЗКИ» ---------- */

const JOURNAL_STATUS = ["Новая", "Собрана", "Упакована", "Отгружена", "Закрыта"];
const SHIPPED = ["Отгружена", "Закрыта"];

export interface JournalRow {
  key: string; shipment: Shipment; line: Shipment["lines"][number];
  date: string; ordered: number; shipped: number | ""; partial: boolean; weight: string; dims: string;
}

/** Вес брутто — сумма по местам; нет мест — вес отправки. Габариты — список по местам. */
function boxTotals(shipment: Shipment) {
  const gross = round2(shipment.boxes.reduce((sum, box) => sum + num(box.gross), 0));
  const dims = shipment.boxes.map((box) => box.dims.trim()).filter(Boolean);
  return { weight: gross > 0 ? String(gross).replace(".", ",") : shipment.weight, dims: dims.join("; ") };
}

/** Отменённые заявки в журнал не идут: груз по ним не двигался. */
export function journalRows(shipments: Shipment[]): JournalRow[] {
  return shipments.filter((shipment) => JOURNAL_STATUS.includes(shipment.status))
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    .flatMap((shipment) => {
      const totals = boxTotals(shipment);
      return shipment.lines.map((line): JournalRow => {
        const ordered = num(line.requested);
        const gone = SHIPPED.includes(shipment.status);
        const shipped = gone ? num(line.picked) : "";
        return { key: `${shipment.id}:${line.id}`, shipment, line, date: shipment.created_at.slice(0, 10), ordered, shipped,
          partial: gone && num(line.picked) < ordered, weight: totals.weight, dims: totals.dims };
      });
    });
}

/** Печать PL / DN по номеру прямо из строки журнала. */
function useDocLink() {
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
    return number ? <button type="button" className="link-button ship-doc-link" title={`Печать ${kind} ${number}`}
      onClick={(event) => { event.stopPropagation(); void open(shipment, document); }}>{number}</button> : <span className="muted">—</span>;
  };
  return { error, link };
}

export function JournalWindow({ shipments, close }: { shipments: Shipment[]; close: () => void }) {
  const rows = useMemo(() => journalRows(shipments), [shipments]);
  const { error, link: docLink } = useDocLink();
  const day = (value: string | null | undefined) => value ? formatDate(value) : "";
  const columns: DataColumn<JournalRow>[] = [
    { key: "date", title: "Дата", value: (row) => row.date, render: (row) => formatDate(row.date) },
    { key: "grn", title: "ГРН", value: (row) => row.line.grn ?? "" },
    { key: "project", title: "Проект", value: (row) => row.line.project, render: (row) => <strong>{row.line.project}</strong> },
    { key: "customer", title: "Заказчик", value: (row) => row.shipment.customer || row.shipment.receiver, filter: true },
    { key: "kb", title: "КБ", value: (row) => row.line.item_index },
    { key: "name", title: "Наименование", value: (row) => row.line.name, wrap: true, maxWidth: 240 },
    { key: "ordered", title: "В заказе", value: (row) => row.ordered, numeric: true },
    { key: "shipped", title: "Отгружено", value: (row) => row.shipped, numeric: true, hint: "Уехавшее по отправке; пока отправка не ушла — пусто",
      render: (row) => row.shipped === "" ? <span className="muted">—</span>
        : row.partial ? <span className="tone-warn ship-partial">{row.shipped}<small>недогруз {round2(row.ordered - row.shipped)}</small></span> : row.shipped },
    { key: "unit", title: "Ед.", value: (row) => row.line.unit },
    { key: "pl", title: "№ PL", value: (row) => row.shipment.pl_number, render: (row) => docLink(row.shipment, "pl") },
    { key: "packed", title: "Дата упаковки", value: (row) => row.shipment.packed_at ?? "", render: (row) => day(row.shipment.packed_at) },
    { key: "terms", title: "Условия доставки", value: (row) => row.shipment.terms ?? "", filter: true },
    { key: "carrier", title: "Способ отгрузки", value: (row) => row.shipment.carrier, filter: true },
    { key: "track", title: "Трек-номер", value: (row) => row.shipment.track_number },
    { key: "shippedAt", title: "Дата отгрузки", value: (row) => row.shipment.shipped_at ?? "", render: (row) => day(row.shipment.shipped_at) },
    { key: "status", title: "Статус", value: (row) => row.shipment.status, filter: true, render: (row) => <ShipStatus status={row.shipment.status} /> },
    { key: "address", title: "Адрес доставки", value: (row) => row.shipment.shipping_point, wrap: true, maxWidth: 220 },
    { key: "contact", title: "Контактное лицо", value: (row) => row.shipment.contact },
    { key: "phone", title: "Телефон", value: (row) => row.shipment.phone },
    { key: "upd", title: "УПД", value: (row) => updLabel(row.shipment.upd_type), filter: true },
    { key: "updNumber", title: "№ УПД", value: (row) => row.shipment.upd_number },
    { key: "updDate", title: "Дата УПД", value: (row) => row.shipment.upd_date ?? "", render: (row) => day(row.shipment.upd_date) },
    { key: "dn", title: "№ DN", value: (row) => row.shipment.dn_number, render: (row) => docLink(row.shipment, "dn") },
    { key: "weight", title: "Вес", value: (row) => row.weight, numeric: true, hint: "Брутто по местам, кг" },
    { key: "dims", title: "Габариты", value: (row) => row.dims, wrap: true, hint: "Шир.×Длина×Высота, см — по местам" },
    { key: "note", title: "Комментарий", value: (row) => row.shipment.note, wrap: true },
  ];
  return <PageWindow label="Журнал отгрузок" close={close} width="min(1760px, 100%)" dismissable>
    <section className="panel intake-journal">
      <header className="page-head"><h2>Журнал отгрузок</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <DataTable label="Журнал отгрузок" storageKey="shipping-journal" rows={rows} columns={columns} rowKey={(row) => row.key}
        exportName="Журнал отгрузок" searchPlaceholder="Проект, КБ, наименование, ГРН, PL, DN или трек"
        presets={[{ label: "Не уехали", test: (row) => !SHIPPED.includes(row.shipment.status) }, { label: "Отгружены", test: (row) => SHIPPED.includes(row.shipment.status) },
          { label: "Частичные", test: (row) => row.partial }]}
        emptyText="В журнале пусто — строки появятся, когда упакуете принятое." />
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
    <input placeholder={key === "customer" ? "по выбранному проекту" : "из карточки проекта"} value={key === "customer" ? customer : draft[key]}
      onChange={(event) => { if (key === "customer") setCustomerTyped(true); setDraft({ ...draft, [key]: event.target.value }); }} /></label>;
  return <PageWindow label="Заявка на отгрузку" close={close} width={1200}>
    <form className="panel ship-request" onSubmit={(event) => { event.preventDefault(); void mutate("shipment:new", () => shippingApi.create(payload()), "Заявка на отгрузку создана").then((saved) => { if (saved) close(); }); }}>
      <header className="page-head"><h2>Заявка на отгрузку</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      <div className="ship-form">
        {text("customer", "Заказчик")}{text("receiver", "Получатель")}{text("shipping_point", "Точка отгрузки", "ship-span2")}
        {text("contact", "Контакт")}{text("phone", "Телефон")}
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
