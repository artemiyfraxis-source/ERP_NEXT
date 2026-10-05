import { useEffect, useMemo, useState } from "react";
import {
  productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal, type ProjectAddressee, type Shipment,
} from "./api";
import { canAct } from "./navigation";
import { DataTable, type DataColumn } from "./DataTable";
import { openPrintSheets } from "./print";
import { formatDate } from "./format";
import { isoDay } from "./Gantt";

/* Отгрузка — вкладки SUPPLYMAP openShipHub: «Проекты» (карточка отгрузки проекта), «Отправки» (реестр всех
   отправок) и «Готовая продукция» (склад ГП). Все три — над одними данными, как portalShipList. */
type Tab = "projects" | "shipments" | "goods";
type Mutate = (key: string, operation: () => Promise<unknown>, message: string | ((result: unknown) => string)) => Promise<boolean>;

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;
const today = () => new Date().toISOString().slice(0, 10);

/** `shipOverdue_`: дедлайн прошёл, а груз ещё не уехал. */
function overdue(shipment: Shipment) {
  return Boolean(shipment.deadline) && String(shipment.deadline) < today() && ACTIVE.includes(shipment.status);
}

/** `shipStage_`: этап отправки словами карточки проекта. */
const STAGE_WORD: Record<string, string> = {
  "Новая": "ждёт сдачи цеха", "Собрана": "упакована", "Упакована": "документы готовы",
  "Отгружена": "отправлена", "Закрыта": "закрыта", "Отменена": "отменена",
};

export function ShippingPage({ principal }: { principal: Principal }) {
  // Два ключа матрицы: «Отгрузка» (`shipping`) — менеджер проекта, «Отгрузка — склад» (`shipping_goods`) — кладовщик.
  const canManage = canAct(principal, "shipping");
  const canWarehouse = canAct(principal, "shipping_goods");
  // SHIP_ROLE_VIEWS_: у кладовщика (правка склада без правки менеджера) первой идёт «Готовая продукция», у остальных — «Проекты».
  const [tab, setTab] = useState<Tab>(canWarehouse && !canManage ? "goods" : "projects");
  const [goods, setGoods] = useState<FinishedGood[]>([]);
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [addressees, setAddressees] = useState<ProjectAddressee[]>([]);
  const [readyBoxes, setReadyBoxes] = useState<FinishedGoodsReadyBox[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const canEdit = canManage || canWarehouse;

  async function reload() {
    setError("");
    try {
      const [goodsResult, shipmentResult, addresseeResult, productionResult] = await Promise.all([
        shippingApi.goods(), shippingApi.shipments(), shippingApi.addressees(),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] }),
      ]);
      setGoods(goodsResult.goods); setShipments(shipmentResult.shipments);
      setAddressees(addresseeResult.addressees ?? []); setReadyBoxes(productionResult.boxes);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить готовую продукцию и отгрузки");
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []);

  const mutate: Mutate = async (key, operation, message) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try {
      const result = await operation();
      setNotice(typeof message === "function" ? message(result) : message);
      await reload();
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Операция не выполнена"); return false; }
    finally { setBusy(""); }
  };

  const tabs: Array<[Tab, string]> = [["projects", "Проекты"], ["shipments", "Отправки"], ["goods", "Готовая продукция"]];
  const context = { goods, shipments, addressees, canManage, canWarehouse, canEdit, busy, mutate };
  return <section className="page shipping-page">
    <header className="page-head"><h1>Готовая продукция и отгрузка</h1><span className="page-count">{tab === "goods" ? goods.length : tab === "shipments" ? shipments.length : new Set(goods.map((good) => good.project)).size}</span></header>
    <div className="shipping-tabs">{tabs.map(([key, label]) => <button key={key} className={tab === key ? "secondary active" : "secondary"} onClick={() => setTab(key)}>{label}</button>)}</div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {tab === "projects" && <ProjectsTab {...context} />}
    {tab === "shipments" && <ShipmentsTab {...context} />}
    {tab === "goods" && <GoodsTab {...context} readyBoxes={readyBoxes} />}
    {loaded && tab === "goods" && !goods.length && <div className="empty">Строк готовой продукции пока нет.</div>}
  </section>;
}

interface Context {
  goods: FinishedGood[];
  shipments: Shipment[];
  addressees: ProjectAddressee[];
  canManage: boolean;
  canWarehouse: boolean;
  canEdit: boolean;
  busy: string;
  mutate: Mutate;
}

/* ---------- Проекты (shipProjListRender / shipProjCardRender) ---------- */

interface ProjectLine { good: FinishedGood; packed: number; sent: number }
interface ProjectRow {
  project: string; customer: string; lines: ProjectLine[]; run: number; accepted: number; remain: number;
  packed: number; sent: number; point: string; noUpd: string[]; stage: string;
}

const STAGES: Array<[string, string]> = [
  ["prod", "В производстве"], ["ready", "На складе — упаковать"], ["wait", "Ждут адрес / УПД"],
  ["packed", "Упакованы — отправить"], ["sent", "Отправлены"],
];

/** `shipProjLines_`: по КБ — сколько в неуехавших отправках и сколько уехало (регистр ГП, с отгруженным до ERP). */
function projectLines(project: string, goods: FinishedGood[], shipments: Shipment[]): ProjectLine[] {
  const packed: Record<string, number> = {};
  for (const shipment of shipments) {
    if (["Отменена", "Отгружена", "Закрыта"].includes(shipment.status)) continue;
    for (const line of shipment.lines) {
      if (line.project === project) packed[line.good_id] = (packed[line.good_id] ?? 0) + num(line.picked);
    }
  }
  return goods.filter((good) => good.project === project).map((good) => ({
    good, packed: round2(packed[good.id] ?? 0), sent: round2(num(good.sent)),
  }));
}

function projectRows(goods: FinishedGood[], shipments: Shipment[], addressees: ProjectAddressee[]): ProjectRow[] {
  const order: string[] = [];
  for (const good of goods) if (!order.includes(good.project)) order.push(good.project);
  const rows = order.map((project) => {
    const lines = projectLines(project, goods, shipments);
    const sum = (pick: (line: ProjectLine) => number) => round2(lines.reduce((total, line) => total + pick(line), 0));
    const run = sum((line) => num(line.good.ordered)); const remain = sum((line) => num(line.good.remaining));
    const packed = sum((line) => line.packed); const sent = sum((line) => line.sent);
    const point = addressees.find((item) => item.project === project)?.shipping_point ?? "";
    // Уехало, а закрыть нечем: нет № или файла УПД.
    const noUpd = shipments.filter((shipment) => shipment.status === "Отгружена" && shipment.lines.some((line) => line.project === project)
      && (!shipment.upd_number || !shipment.upd_url)).map((shipment) => shipment.number);
    const stage = (noUpd.length || (!point && (remain > 0 || packed > 0))) ? "wait"
      : packed > 0 ? "packed" : remain > 0 ? "ready" : (run > 0 && sent >= run) ? "sent" : "prod";
    return {
      project, customer: lines[0]?.good.customer ?? "", lines, run, accepted: sum((line) => num(line.good.accepted)),
      remain, packed, sent, point, noUpd, stage,
    };
  });
  const rank = (stage: string) => STAGES.findIndex(([key]) => key === stage);
  return rows.sort((a, b) => rank(a.stage) - rank(b.stage));
}

function ProjectsTab(context: Context) {
  const rows = useMemo(() => projectRows(context.goods, context.shipments, context.addressees), [context.goods, context.shipments, context.addressees]);
  const stageName = (stage: string) => STAGES.find(([key]) => key === stage)?.[1] ?? stage;
  const columns: DataColumn<ProjectRow>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <strong>{row.project}</strong> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true },
    { key: "kb", title: "КБ", value: (row) => row.lines.length, numeric: true },
    { key: "accepted", title: "Сдано цехом", value: (row) => `${row.accepted} из ${row.run}` },
    { key: "remain", title: "На складе", value: (row) => row.remain, numeric: true },
    { key: "packed", title: "В отправке", value: (row) => row.packed, numeric: true },
    { key: "sent", title: "Отгружено", value: (row) => row.sent, numeric: true },
    { key: "point", title: "Адрес отгрузки", value: (row) => row.point, render: (row) => row.point || <span className="danger-text">адрес отгрузки не указан</span>, wrap: true },
    { key: "noUpd", title: "Уехало без УПД", value: (row) => row.noUpd.join(", "), render: (row) => row.noUpd.length ? <span className="danger-text">{row.noUpd.join(", ")}</span> : "" },
    { key: "stage", title: "Этап", value: (row) => stageName(row.stage), filter: true },
  ];
  return <>
    <DataTable label="Проекты на отгрузку" storageKey="shipping-projects" rows={rows} columns={columns} rowKey={(row) => row.project}
      groupBy={(row) => stageName(row.stage)} exportName="Отгрузка — проекты"
      emptyText="Проектов на отгрузку нет — карточки встают при заведении проекта из расчёта."
      renderDetail={(row) => <ProjectCard {...context} row={row} />} />
  </>;
}

const ADDRESS_FIELDS: Array<[keyof ProjectAddressee, string, string]> = [
  ["shipping_point", "Адрес отгрузки", "куда везём"], ["contact", "Контактное лицо", "кто принимает"], ["phone", "Телефон", "для связи"],
  ["receiver", "Получатель", "как в документах"], ["carrier", "ТК / способ", "СДЭК, Деловые линии …"],
];
const REQUISITE_FIELDS: Array<[keyof ProjectAddressee, string, string]> = [
  ["po", "Заказ № (PO)", "номер заказа заказчика"], ["contract", "№ договора", ""], ["spec", "№ спецификации", "пусто — № проекта"],
  ["invoice", "№ счёта", ""], ["pays", "Оплачивает доставку", "заказчик / мы"], ["customer_address", "Адрес заказчика", "юридический"],
  ["hours", "Время работы", "пн–пт 9:00–18:00"],
];

function ProjectCard({ row, shipments, addressees, canEdit, canWarehouse, canManage, busy, mutate }: Context & { row: ProjectRow }) {
  const stored = addressees.find((item) => item.project === row.project);
  const [card, setCard] = useState<Record<string, string>>(() => {
    const base: Record<string, string> = {};
    for (const [key] of [...ADDRESS_FIELDS, ...REQUISITE_FIELDS]) base[key] = String(stored?.[key] ?? "");
    base.terms = stored?.terms ?? ""; base.upd_type = stored?.upd_type ?? "";
    return base;
  });
  const [pack, setPack] = useState<Record<string, string>>({});
  const [marked, setMarked] = useState<Record<string, boolean>>({});
  const save = (field: string, value: string) => {
    if (String(stored?.[field as keyof ProjectAddressee] ?? "") === value) return;
    void mutate(`addressee:${row.project}`, () => shippingApi.saveAddressee({ project: row.project, version: stored?.version ?? 0, [field]: value || (field === "terms" || field === "upd_type" ? null : "") }), "Карточка проекта сохранена");
  };
  const input = ([key, label, placeholder]: [keyof ProjectAddressee, string, string]) => <label key={key}>{label}
    <input aria-label={`${label} проекта ${row.project}`} disabled={!canEdit} placeholder={placeholder} value={card[key] ?? ""} onChange={(event) => setCard({ ...card, [key]: event.target.value })} onBlur={(event) => save(key, event.target.value.trim())} /></label>;
  const select = (key: "terms" | "upd_type", label: string, options: string[]) => <label>{label}
    <select aria-label={`${label} проекта ${row.project}`} disabled={!canEdit} value={card[key] ?? ""} onChange={(event) => { setCard({ ...card, [key]: event.target.value }); save(key, event.target.value); }}>
      <option value="" />{options.map((option) => <option key={option}>{option}</option>)}</select></label>;
  const picked = row.lines.filter((line) => marked[line.good.id]);
  const projectShipments = shipments.filter((shipment) => shipment.status !== "Отменена" && shipment.lines.some((line) => line.project === row.project));
  const doPack = () => {
    const lines = picked.map((line) => ({ good_id: line.good.id, quantity: pack[line.good.id] ?? line.good.remaining })).filter((line) => num(line.quantity) > 0);
    if (!lines.length) return;
    void mutate(`pack:${row.project}`, () => shippingApi.projectPack(row.project, lines), (result) => {
      const shipment = (result as { shipment?: Shipment }).shipment;
      return `Упаковано: отправка ${shipment?.number ?? ""}`;
    }).then((saved) => { if (saved) { setMarked({}); setPack({}); } });
  };
  return <div className="shipping-project-card">
    <div className="shipping-form">{ADDRESS_FIELDS.slice(0, 4).map(input)}{select("terms", "Условия доставки", ["САМОВЫВОЗ", "ДОСТАВКА ТК"])}{input(ADDRESS_FIELDS[4])}{select("upd_type", "Тип УПД", ["ЭДО", "БУМАГА"])}</div>
    <small className="muted">Эти поля попадают во все отправки проекта и в их PL/DN. №, дата и файл УПД — у каждой отправки ниже. Без файла УПД отправить можно только по ЭДО.</small>
    <details open={!(stored?.po || stored?.contract || stored?.spec || stored?.invoice)}><summary>Реквизиты для отгрузочной ведомости</summary>
      <div className="shipping-form">{REQUISITE_FIELDS.map(input)}</div></details>
    <div className="table-scroll"><table><thead><tr><th>КБ</th><th>Наименование</th><th>В заказе</th><th>Сдано цехом</th><th>На складе</th><th>В отправке</th><th>Отгружено</th>{canWarehouse && <th>Принять</th>}{canEdit && <th>Упаковать</th>}</tr></thead>
      <tbody>{row.lines.map((line) => {
        const can = num(line.good.remaining) > 0;
        return <tr key={line.good.id}><td><strong>{line.good.item_index}</strong></td><td>{line.good.name}</td><td>{line.good.ordered}</td><td>{line.good.accepted}</td><td><strong>{line.good.remaining}</strong></td><td>{line.packed}</td><td>{line.sent}</td>
          {canWarehouse && <td><AcceptCell good={line.good} busy={busy} mutate={mutate} /></td>}
          {canEdit && <td><div className="inline-qty"><input type="checkbox" aria-label={`Упаковать ${line.good.item_index}`} disabled={!can} checked={Boolean(marked[line.good.id])} onChange={(event) => setMarked({ ...marked, [line.good.id]: event.target.checked })} />
            <input aria-label={`Сколько упаковать ${line.good.item_index}`} type="number" min="0" step="any" disabled={!can} value={pack[line.good.id] ?? (can ? line.good.remaining : "")} onChange={(event) => setPack({ ...pack, [line.good.id]: event.target.value })} /></div></td>}
        </tr>;
      })}</tbody></table></div>
    {canEdit && <div className="page-actions"><button className="primary" disabled={!picked.length || Boolean(busy)} onClick={doPack}>Упаковать{picked.length ? ` · ${picked.length}` : ""}</button></div>}
    <h3>Отправки проекта</h3>
    {projectShipments.length ? <div className="work-list shipping-list">{projectShipments.map((shipment) => <ShipmentDetail key={`${shipment.id}:${shipment.version}`} shipment={shipment} canManage={canManage} canWarehouse={canWarehouse} canEdit={canEdit} busy={busy} mutate={mutate} />)}</div>
      : <div className="empty">Отправок пока нет. Отметь в строках, что упаковать, и нажми «Упаковать».</div>}
  </div>;
}

/* ---------- Отправки (shipReqRender / shipCardRender) ---------- */

function shipmentRows(shipments: Shipment[]) {
  // Очередь, а не архив: сверху то, что ещё надо везти, по дедлайну; уехавшие, закрытые, отменённые — вниз.
  const done: Record<string, number> = { "Отгружена": 1, "Закрыта": 2, "Отменена": 3 };
  return shipments.slice().sort((a, b) => {
    const ga = done[a.status] ?? 0; const gb = done[b.status] ?? 0;
    if (ga !== gb) return ga - gb;
    if (!ga) { const da = a.deadline ?? "9999"; const db = b.deadline ?? "9999"; if (da !== db) return da < db ? -1 : 1; }
    return a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0;
  });
}

function ShipmentsTab({ goods, shipments, canManage, canWarehouse, canEdit, busy, mutate }: Context) {
  const rows = useMemo(() => shipmentRows(shipments), [shipments]);
  const columns: DataColumn<Shipment>[] = [
    { key: "status", title: "Статус", value: (row) => row.status, filter: true },
    { key: "number", title: "№ отправки", value: (row) => row.number, render: (row) => <><strong>{row.number}</strong><small>{formatDate(row.created_at.slice(0, 10))}</small></> },
    { key: "receiver", title: "Получатель · точка отгрузки", value: (row) => `${row.receiver || row.customer} ${row.shipping_point}`, wrap: true,
      render: (row) => <>{row.receiver || row.customer || "—"}<small>{row.shipping_point || "точка не указана"}{row.deadline ? ` · до ${row.deadline}` : ""}</small>{overdue(row) && <small className="danger-text">просрочена</small>}</> },
    { key: "projects", title: "Проект · КБ", value: (row) => [...new Set(row.lines.map((line) => `${line.project} ${line.item_index}`))].join(", "), wrap: true },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, hidden: true },
    { key: "grn", title: "ГРН", value: (row) => row.lines.map((line) => line.grn ?? "").filter(Boolean).join(", "), hidden: true },
    { key: "count", title: "Позиций", value: (row) => row.lines.length, numeric: true },
    { key: "picked", title: "Набрано", value: (row) => `${round2(row.lines.reduce((sum, line) => sum + num(line.picked), 0))} / ${round2(row.lines.reduce((sum, line) => sum + num(line.requested), 0))}` },
    { key: "docs", title: "Документы", value: (row) => row.pl_number ? `${row.pl_number} · ${row.dn_number}` : "" },
    { key: "deadline", title: "Дедлайн", value: (row) => row.deadline ?? "", hidden: true },
  ];
  return <>
    {canManage && <CreateShipment goods={goods} busy={busy} mutate={mutate} />}
    <DataTable label="Отправки" storageKey="shipping-shipments" rows={rows} columns={columns} rowKey={(row) => row.id}
      presets={[{ label: "В работе", test: (row) => ACTIVE.includes(row.status) }, { label: "Просрочены", test: overdue }, { label: "Уехало без УПД", test: (row) => row.status === "Отгружена" && (!row.upd_number || !row.upd_url) }]}
      rowClassName={(row) => row.status === "Отменена" ? "cancelled" : ""} exportName="Отправки"
      emptyText="Отправок нет. Они появляются из карточки проекта — кнопкой «Упаковать»."
      renderDetail={(row) => <ShipmentDetail key={`${row.id}:${row.version}`} shipment={row} canManage={canManage} canWarehouse={canWarehouse} canEdit={canEdit} busy={busy} mutate={mutate} />} />
  </>;
}

function CreateShipment({ goods, busy, mutate }: { goods: FinishedGood[]; busy: string; mutate: Mutate }) {
  const blank = { customer: "", receiver: "", shipping_point: "", contact: "", phone: "", terms: "", carrier: "", deadline: "", upd_type: "", upd_number: "", upd_url: "", note: "" };
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState(blank);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [noDocs, setNoDocs] = useState(false);
  const selected = goods.filter((good) => num(quantities[good.id]) > 0);
  // Пустые получатель, точка, условия и тип УПД наследуются из карточки проекта (SH-3) — сервер скажет, если их нет и там.
  const payload = () => {
    const body: Record<string, unknown> = { ...draft, no_docs: noDocs, lines: selected.map((good) => ({ good_id: good.id, quantity: quantities[good.id] })) };
    for (const key of ["terms", "upd_type", "deadline"]) if (!body[key]) body[key] = null;
    return body;
  };
  return <>
    <div className="page-actions shipping-add"><button className="primary" onClick={() => setShow((value) => !value)}>+ Заявка на отгрузку</button></div>
    {show && <form className="panel shipping-form shipping-request" onSubmit={(event) => { event.preventDefault(); void mutate("shipment:new", () => shippingApi.create(payload()), "Заявка на отгрузку создана").then((saved) => { if (saved) { setShow(false); setQuantities({}); setNoDocs(false); setDraft(blank); } }); }}>
      {(["customer", "receiver", "shipping_point", "contact", "phone", "carrier", "upd_number", "upd_url"] as const).map((key) => <label key={key}>{({ customer: "Заказчик", receiver: "Получатель", shipping_point: "Точка отгрузки", contact: "Контакт", phone: "Телефон", carrier: "ТК / способ", upd_number: "№ УПД", upd_url: "Ссылка на файл УПД" })[key]}<input value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <label>Условия<select value={draft.terms} onChange={(event) => setDraft({ ...draft, terms: event.target.value })}><option value="">как в карточке проекта</option><option>САМОВЫВОЗ</option><option>ДОСТАВКА ТК</option></select></label>
      <label>Тип УПД<select value={draft.upd_type} onChange={(event) => setDraft({ ...draft, upd_type: event.target.value })}><option value="">как в карточке проекта</option><option>ЭДО</option><option>БУМАГА</option></select></label>
      <label>Дедлайн<input type="date" value={draft.deadline} onChange={(event) => setDraft({ ...draft, deadline: event.target.value })} /></label>
      <div className="shipping-lines"><strong>Что отгружаем</strong>{goods.map((good) => <label key={good.id}>{good.project} · {good.item_index} · остаток {good.remaining} {good.unit}<input aria-label={`Заказать ${good.project} ${good.item_index}`} type="number" min="0" step="any" value={quantities[good.id] ?? ""} onChange={(event) => setQuantities({ ...quantities, [good.id]: event.target.value })} /></label>)}</div>
      <label className="check-inline"><input type="checkbox" checked={noDocs} onChange={(event) => setNoDocs(event.target.checked)} />Без отгрузочных документов (только набрать)</label>
      <button className="primary" disabled={!selected.length || Boolean(busy)}>Создать заявку</button>
    </form>}
  </>;
}

function ShipmentDetail({ shipment, canManage, canWarehouse, canEdit, busy, mutate }: { shipment: Shipment; canManage: boolean; canWarehouse: boolean; canEdit: boolean; busy: string; mutate: Mutate }) {
  const [documents, setDocuments] = useState({ upd_type: shipment.inherited.includes("upd_type") ? "" : shipment.upd_type ?? "", upd_number: shipment.upd_number, upd_date: shipment.upd_date ?? "", upd_url: shipment.upd_url, track_number: shipment.track_number });
  const [requested, setRequested] = useState<Record<string, string>>({});
  const [layout, setLayout] = useState(false);
  const [printError, setPrintError] = useState("");
  const action = (name: "pick" | "pack" | "ship" | "close" | "cancel", message: string | ((result: unknown) => string)) => mutate(`${name}:${shipment.id}`, () => shippingApi.action(shipment.id, name, shipment.version), message);
  const got = shipment.lines.some((line) => num(line.picked) > 0);
  const open = !["Закрыта", "Отменена"].includes(shipment.status);
  const print = async (document: "pl" | "dn") => {
    setPrintError("");
    try { openPrintSheets((await shippingApi.print(shipment.id, document)).sheets); }
    catch (reason) { setPrintError(reason instanceof Error ? reason.message : "Не удалось подготовить печать"); }
  };
  // shipUpdPick_: файл читается в base64 — формат, который принимает сервер.
  const uploadUpd = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = String(reader.result ?? "").split(",")[1] ?? "";
      void mutate(`upd:${shipment.id}`, () => shippingApi.uploadUpd(shipment.id, shipment.version, { name: file.name, type: file.type || "application/octet-stream", base64 }), "УПД приложена");
    };
    reader.onerror = () => setPrintError("Не удалось прочитать файл");
    reader.readAsDataURL(file);
  };
  const saveRequested = (lineId: string, current: string) => {
    const value = requested[lineId];
    if (value === undefined || value === "" || num(value) === num(current)) return;
    void mutate(`qty:${lineId}`, () => shippingApi.setRequested(shipment.id, shipment.version, lineId, value), "Заказанное количество сохранено");
  };
  return <article className={`panel shipping-card ${shipment.status === "Отменена" ? "cancelled" : ""}`}>
    <div className="procurement-title"><div><strong>{shipment.number}</strong><small>{shipment.receiver} · {shipment.shipping_point}</small></div><span className="status" title={shipment.status}>{STAGE_WORD[shipment.status] ?? shipment.status}</span></div>
    <div className="shipping-meta"><span>Менеджер: {shipment.manager}</span><span>{shipment.terms || "условия не указаны"}{shipment.carrier ? ` · ${shipment.carrier}` : ""}</span>{shipment.deadline && <span className={overdue(shipment) ? "danger-text" : ""}>Дедлайн: {shipment.deadline}{overdue(shipment) ? " · просрочена" : ""}</span>}<span>{shipment.pl_number || "PL —"} · {shipment.dn_number || "DN —"}</span>{shipment.places > 0 && <span>мест {shipment.places}{shipment.weight ? ` · ${shipment.weight} кг` : ""}</span>}{shipment.packed_at && <span>упакована {shipment.packed_at}</span>}{shipment.shipped_at && <span>отгружена {shipment.shipped_at}</span>}{shipment.upd_url ? <a href={shipment.upd_url} target="_blank" rel="noreferrer">Открыть УПД</a> : <span className="danger-text">файла УПД нет</span>}</div>
    <div className="table-scroll"><table><thead><tr><th>Позиция</th><th>Заказано</th><th>Набрано</th><th>Недобор</th><th>ГРН</th>{canWarehouse && <th />}</tr></thead><tbody>{shipment.lines.map((line) => <tr key={line.id}><td>{line.project} · {line.item_index}<small>{line.name}</small></td>
      <td>{canEdit ? <input className="qty-input" aria-label={`Заказано ${line.item_index}`} type="number" min="0.01" step="any" value={requested[line.id] ?? line.requested} onChange={(event) => setRequested({ ...requested, [line.id]: event.target.value })} onBlur={() => saveRequested(line.id, line.requested)} /> : `${line.requested} ${line.unit}`}</td>
      <td>{line.picked}</td><td>{line.shortage}</td><td>{line.grn || "—"}</td>{canWarehouse && <td>{num(line.picked) > 0 && ["Новая", "Собрана"].includes(shipment.status) && <button className="link-button danger-link" disabled={Boolean(busy)} onClick={() => void mutate(`unpick:${line.id}`, () => shippingApi.unpick(shipment.id, shipment.version, line.id), "Позиция возвращена на склад ГП")}>Вернуть</button>}</td>}</tr>)}</tbody></table></div>
    {canEdit && open && <ShipmentHead shipment={shipment} busy={busy} mutate={mutate} />}
    {canManage && open && <div className="shipment-docs"><label>Тип УПД<select value={documents.upd_type} onChange={(event) => setDocuments({ ...documents, upd_type: event.target.value })}><option value="">{shipment.inherited.includes("upd_type") ? `из карточки проекта${shipment.upd_type ? `: ${shipment.upd_type}` : ""}` : "из карточки проекта"}</option><option>ЭДО</option><option>БУМАГА</option></select></label><label>№ УПД<input value={documents.upd_number} onChange={(event) => setDocuments({ ...documents, upd_number: event.target.value })} /></label><label>Дата УПД<input type="date" value={documents.upd_date} onChange={(event) => setDocuments({ ...documents, upd_date: event.target.value })} /></label><label>Ссылка на файл УПД<input value={documents.upd_url} onChange={(event) => setDocuments({ ...documents, upd_url: event.target.value })} /></label><label>Трек-номер<input value={documents.track_number} onChange={(event) => setDocuments({ ...documents, track_number: event.target.value })} /></label><button className="secondary small" disabled={Boolean(busy)} onClick={() => void mutate(`docs:${shipment.id}`, () => shippingApi.documents(shipment.id, { version: shipment.version, ...documents, upd_type: documents.upd_type || null, upd_date: documents.upd_date || null }), "Данные УПД и доставки сохранены")}>Сохранить документы</button></div>}
    {layout && <BoxesEditor shipment={shipment} busy={busy} mutate={mutate} onDone={() => setLayout(false)} />}
    {printError && <div className="error-banner" role="alert">{printError}</div>}
    <div className="fulfillment-actions">
      {canWarehouse && shipment.status === "Новая" && <button className="secondary" disabled={Boolean(busy)} onClick={() => void action("pick", "Заявка набрана из доступного остатка")}>Набрать</button>}
      {canEdit && got && (ACTIVE.includes(shipment.status) || (shipment.pl_number && shipment.status === "Отгружена")) && !layout && <button className="secondary" disabled={Boolean(busy)} onClick={() => setLayout(true)}>{shipment.pl_number ? "Перевыпустить" : "Сформировать отгрузочные"}</button>}
      {canEdit && open && <label className="secondary file-button">Загрузить УПД<input type="file" accept=".pdf,image/*" aria-label={`Загрузить УПД ${shipment.number}`} hidden onChange={(event) => { uploadUpd(event.target.files?.[0]); event.target.value = ""; }} /></label>}
      {shipment.pl_number && got && <><button className="secondary" onClick={() => void print("pl")}>Печать PL</button><button className="secondary" onClick={() => void print("dn")}>Печать DN</button></>}
      {canWarehouse && got && ACTIVE.includes(shipment.status) && <button className="primary" disabled={Boolean(busy)} onClick={() => void action("ship", (result) => { const outcome = result as { closed?: boolean; why?: string }; return outcome.closed ? "Отгружено и закрыто" : `Отгружено. Не закрыта: ${outcome.why || "не хватает данных"}`; })}>Отгружено</button>}
      {canWarehouse && shipment.status === "Отгружена" && <button className="primary" disabled={Boolean(busy)} onClick={() => void action("close", "Отгрузка закрыта")}>Закрыть</button>}
      {canManage && ACTIVE.includes(shipment.status) && <button className="link-button danger-link" disabled={Boolean(busy)} onClick={() => void action("cancel", "Заявка отменена, отбор возвращён на склад ГП")}>Отменить</button>}
    </div>
  </article>;
}

const HEAD_FIELDS: Array<[keyof Shipment & string, string]> = [
  ["receiver", "Получатель"], ["shipping_point", "Адрес отгрузки"], ["contact", "Контактное лицо"], ["phone", "Телефон"],
  ["carrier", "ТК / способ"],
];

/** `shipSetHead_`: шапка открытой отправки. Поле сохраняется при уходе из него; пустое поле адресата снова
 * берётся из карточки проекта и идёт за ней, пока отправка не закрыта. */
function ShipmentHead({ shipment, busy, mutate }: { shipment: Shipment; busy: string; mutate: Mutate }) {
  const initial = (): Record<string, string> => ({
    ...Object.fromEntries(HEAD_FIELDS.map(([key]) => [key, String(shipment[key] ?? "")])),
    terms: shipment.terms ?? "", deadline: shipment.deadline ?? "", note: shipment.note,
  });
  const [draft, setDraft] = useState(initial);
  // Сохранили поле или карточка проекта поменяла адрес — показываем то, что теперь в отправке.
  useEffect(() => setDraft(initial()), [shipment.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const inherited = (key: string) => shipment.inherited.includes(key);
  const save = (key: string) => {
    const value = draft[key].trim();
    const stored = key === "terms" ? shipment.terms ?? "" : key === "deadline" ? shipment.deadline ?? "" : String(shipment[key as keyof Shipment] ?? "");
    // Пустое поле адресата, уже идущее за карточкой, — не правка: сервер вернул бы то же значение.
    if (value === stored.trim() || (value === "" && inherited(key))) return;
    const sent = value === "" && (key === "terms" || key === "deadline") ? null : value;
    void mutate(`head:${shipment.id}:${key}`, () => shippingApi.head(shipment.id, { version: shipment.version, [key]: sent }), "Отправка сохранена");
  };
  // Подпись под каждым полем (и пустая у своих) держит поля одной строки на одной высоте.
  const hint = (key: string) => <small className="muted">{inherited(key) ? "из карточки проекта" : key === "deadline" || key === "note" ? "только у этой отправки" : "своё у отправки"}</small>;
  return <div className="shipment-docs">
    {HEAD_FIELDS.map(([key, label]) => <label key={key}>{label}{hint(key)}<input aria-label={`${label} ${shipment.number}`} disabled={Boolean(busy)} placeholder={inherited(key) ? "" : "пусто — из карточки проекта"} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} onBlur={() => save(key)} /></label>)}
    <label>Условия доставки{hint("terms")}<select aria-label={`Условия доставки ${shipment.number}`} disabled={Boolean(busy)} value={draft.terms} onChange={(event) => setDraft({ ...draft, terms: event.target.value })} onBlur={() => save("terms")}><option value="">из карточки проекта</option><option>САМОВЫВОЗ</option><option>ДОСТАВКА ТК</option></select></label>
    <label>Дедлайн{hint("deadline")}<input type="date" aria-label={`Дедлайн ${shipment.number}`} disabled={Boolean(busy)} value={draft.deadline} onChange={(event) => setDraft({ ...draft, deadline: event.target.value })} onBlur={() => save("deadline")} /></label>
    <label>Комментарий{hint("note")}<input aria-label={`Комментарий ${shipment.number}`} disabled={Boolean(busy)} value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} onBlur={() => save("note")} /></label>
  </div>;
}

interface BoxDraft { kind: string; dims: string; net: string; gross: string; seal: string; q: Record<string, string> }

/** `shipDocsModal_`: сетка «КБ × BXN», характеристики каждого места и примечания для ТК. */
function BoxesEditor({ shipment, busy, mutate, onDone }: { shipment: Shipment; busy: string; mutate: Mutate; onDone: () => void }) {
  const packed = useMemo(() => {
    const order: Array<{ index: string; name: string; qty: number }> = [];
    for (const line of shipment.lines) {
      if (!(num(line.picked) > 0)) continue;
      const found = order.find((item) => item.index === line.item_index);
      if (found) found.qty = round2(found.qty + num(line.picked)); else order.push({ index: line.item_index, name: line.name, qty: num(line.picked) });
    }
    return order;
  }, [shipment]);
  const blank = (): BoxDraft => ({ kind: "Короб", dims: "", net: "", gross: "", seal: "", q: {} });
  const [boxes, setBoxes] = useState<BoxDraft[]>(() => shipment.boxes.length
    ? shipment.boxes.map((box) => ({ kind: box.kind, dims: box.dims, net: box.net, gross: box.gross, seal: box.seal, q: Object.fromEntries(box.items.map((item) => [item.item_index, item.quantity])) }))
    : [{ ...blank(), q: Object.fromEntries(packed.map((item) => [item.index, String(item.qty)])) }]);
  const [tkNote, setTkNote] = useState(shipment.tk_note);
  const resize = (wanted: number) => {
    const count = Math.max(1, Math.min(30, Math.floor(wanted) || 1));
    setBoxes((current) => {
      const next = current.map((box) => ({ ...box, q: { ...box.q } }));
      while (next.length < count) next.push(blank());
      // Убранные короба не теряют количество: оно возвращается в последний оставшийся.
      while (next.length > count) {
        const gone = next.pop()!; const last = next[next.length - 1];
        for (const item of packed) last.q[item.index] = String(round2(num(last.q[item.index]) + num(gone.q[item.index])) || "");
      }
      return next;
    });
  };
  const change = (index: number, patch: Partial<BoxDraft>) => setBoxes((current) => current.map((box, position) => position === index ? { ...box, ...patch } : box));
  const submit = () => void mutate(`boxes:${shipment.id}`, () => shippingApi.boxes(shipment.id, {
    version: shipment.version, tk_note: tkNote,
    boxes: boxes.map((box) => ({ kind: box.kind, dims: box.dims, net: box.net, gross: box.gross, seal: box.seal,
      items: packed.map((item) => ({ item_index: item.index, quantity: String(num(box.q[item.index])) })).filter((item) => num(item.quantity) > 0) })),
  }), shipment.pl_number ? `Перевыпущены: ${shipment.pl_number} и ${shipment.dn_number}` : "Отгрузочные документы сформированы").then((saved) => { if (saved) onDone(); });
  const fields: Array<[keyof BoxDraft, string, string]> = [["kind", "Вид упаковки", "Короб"], ["dims", "Габариты ШхДхВ, см", "30х30х30"], ["net", "Вес нетто, кг", "13,6"], ["gross", "Вес брутто, кг", "14,2"], ["seal", "Пломба №", ""]];
  return <div className="panel shipping-boxes">
    <strong>{shipment.pl_number ? "Перевыпустить документы" : "Отгрузочные документы"} · {shipment.number}</strong>
    <p className="muted">Разложите упакованное по коробам: на каждый короб — своя упаковочная ведомость, отгрузочная — одна на отправку.{shipment.pl_number ? ` Номера ${shipment.pl_number} и ${shipment.dn_number} сохранятся.` : ""}</p>
    <label>Коробов<input aria-label="Коробов" type="number" min="1" max="30" value={boxes.length} onChange={(event) => resize(Number(event.target.value))} /></label>
    <div className="table-scroll"><table><thead><tr><th>КБ</th><th>Упак.</th>{boxes.map((_, index) => <th key={index}>BXN {index + 1}</th>)}</tr></thead><tbody>
      {packed.map((item) => <tr key={item.index}><td><strong>{item.index}</strong><small>{item.name}</small></td><td>{item.qty}</td>{boxes.map((box, index) => <td key={index}><input aria-label={`${item.index} в BXN ${index + 1}`} value={box.q[item.index] ?? ""} onChange={(event) => change(index, { q: { ...box.q, [item.index]: event.target.value } })} /></td>)}</tr>)}
      {fields.map(([key, label, placeholder]) => <tr key={key}><td colSpan={2}>{label}</td>{boxes.map((box, index) => <td key={index}><input aria-label={`${label} BXN ${index + 1}`} placeholder={placeholder} value={String(box[key] ?? "")} onChange={(event) => change(index, { [key]: event.target.value })} /></td>)}</tr>)}
    </tbody></table></div>
    <label>Примечания для ТК<textarea rows={2} value={tkNote} onChange={(event) => setTkNote(event.target.value)} /></label>
    <div className="fulfillment-actions"><button className="primary" disabled={Boolean(busy)} onClick={submit}>{shipment.pl_number ? "Перевыпустить" : "Сформировать"}</button><button className="secondary" onClick={onDone}>Отмена</button></div>
  </div>;
}

/* ---------- Готовая продукция (shipGpRender) ---------- */

function AcceptCell({ good, busy, mutate }: { good: FinishedGood; busy: string; mutate: Mutate }) {
  const [amount, setAmount] = useState("");
  const [day, setDay] = useState(() => isoDay(new Date()));
  return <div className="inline-qty" onClick={(event) => event.stopPropagation()}>
    <input aria-label={`Принять ${good.project} ${good.item_index}`} type="number" min="0.000001" step="any" placeholder={String(Math.max(num(good.ordered) - num(good.accepted), 0))} value={amount} onChange={(event) => setAmount(event.target.value)} />
    <input aria-label={`Дата поступления ${good.project} ${good.item_index}`} type="date" title="Дата поступления из цеха (по умолчанию сегодня)" value={day} onChange={(event) => setDay(event.target.value)} />
    <button className="secondary small" disabled={!amount || Boolean(busy)} onClick={() => void mutate(`accept:${good.id}`, () => shippingApi.accept(good.id, good.version, amount, day || undefined), "Готовая продукция принята").then((saved) => { if (saved) { setAmount(""); setDay(isoDay(new Date())); } })}>Принять</button>
  </div>;
}

function GoodsTab({ goods, readyBoxes, canManage, canWarehouse, busy, mutate }: Context & { readyBoxes: FinishedGoodsReadyBox[] }) {
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState({ project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" });
  const columns: DataColumn<FinishedGood>[] = [
    { key: "project", title: "Проект", value: (good) => good.project, render: (good) => <strong>{good.project}</strong>, filter: true },
    { key: "item_index", title: "КБ", value: (good) => good.item_index },
    { key: "name", title: "Наименование", value: (good) => good.name, wrap: true },
    { key: "customer", title: "Заказчик", value: (good) => good.customer, hidden: true },
    { key: "article", title: "Артикул КД", value: (good) => good.article, hidden: true },
    { key: "ordered", title: "В заказе", value: (good) => good.ordered, numeric: true },
    { key: "accepted", title: "Принято", value: (good) => good.accepted, numeric: true },
    { key: "shipped", title: "Отобрано", value: (good) => good.shipped, numeric: true, hidden: true },
    { key: "sent", title: "Уехало", value: (good) => good.sent, numeric: true, hidden: true },
    { key: "before_erp", title: "Отгружено до ERP", value: (good) => good.before_erp, numeric: true, hidden: true },
    { key: "remaining", title: "На складе", value: (good) => good.remaining, numeric: true, render: (good) => <strong>{good.remaining}</strong> },
    { key: "grn", title: "ГРН", value: (good) => good.grn ?? "", render: (good) => good.grn || <span className="muted">цех не сдавал</span> },
    { key: "received_at", title: "Дата приёмки", value: (good) => good.received_at ?? "", hidden: true },
    ...(canWarehouse ? [{ key: "accept", title: "Приёмка из цеха", value: () => "", render: (good: FinishedGood) => <AcceptCell good={good} busy={busy} mutate={mutate} /> }] : []),
  ];
  return <>
    {canManage && <div className="page-actions shipping-add"><button className="primary" onClick={() => setShow((value) => !value)}>+ Добавить КБ проекта</button></div>}
    {show && <form className="panel shipping-form" onSubmit={(event) => { event.preventDefault(); void mutate("good:new", () => shippingApi.registerGood(draft), "Позиция ГП добавлена").then((saved) => { if (saved) { setShow(false); setDraft({ project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" }); } }); }}>
      {(["project", "item_index", "customer", "article", "name", "ordered", "unit"] as const).map((key) => <label key={key}>{({ project: "Проект", item_index: "КБ", customer: "Заказчик", article: "Артикул КД", name: "Наименование", ordered: "В заказе", unit: "Ед. изм." })[key]}<input required={["project", "item_index", "name", "ordered", "unit"].includes(key)} type={key === "ordered" ? "number" : "text"} min={key === "ordered" ? "0.000001" : undefined} step={key === "ordered" ? "any" : undefined} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <button className="primary" disabled={Boolean(busy)}>Сохранить</button>
    </form>}
    {canWarehouse && readyBoxes.length > 0 && <FinishedGoodsHandoff boxes={readyBoxes} goods={goods} busy={busy} mutate={mutate} />}
    {goods.length > 0 && <DataTable label="Склад готовой продукции" storageKey="shipping-goods" rows={goods} columns={columns} rowKey={(good) => good.id} exportName="Склад ГП" />}
  </>;
}

function FinishedGoodsHandoff({ boxes, goods, busy, mutate }: { boxes: FinishedGoodsReadyBox[]; goods: FinishedGood[]; busy: string; mutate: Mutate }) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  return <div className="panel box-review"><div className="manual-stock-head"><h2>Приёмка коробок из цеха</h2><span className="page-count">{boxes.length}</span></div>
    <div className="box-grid">{boxes.map((box) => {
      const candidates = goods.filter((good) => good.project === box.project && (box.task_item.toUpperCase() === good.item_index.toUpperCase() || box.task_item.toUpperCase().endsWith(`.${good.item_index.toUpperCase()}`) || box.task_item.toUpperCase().endsWith(`-${good.item_index.toUpperCase()}`)));
      const goodId = selected[box.id] ?? candidates[0]?.id ?? "";
      return <div className="production-box" key={`${box.id}:${box.version}`}>
        <strong>{box.number}</strong><span>{box.project} · {box.task_item}</span><span>{box.quantity} шт</span>
        <label>Позиция ГП<select aria-label={`Позиция ГП для коробки ${box.number}`} value={goodId} onChange={(event) => setSelected((current) => ({ ...current, [box.id]: event.target.value }))}><option value="">Не выбрана</option>{candidates.map((good) => <option key={good.id} value={good.id}>{good.project} · {good.item_index} · {good.name}</option>)}</select></label>
        {!candidates.length && <small className="danger-text">Сначала добавьте соответствующую КБ проекта в реестр ГП.</small>}
        <button className="primary small" disabled={!goodId || Boolean(busy)} onClick={() => void mutate(`handoff:${box.id}`, () => productionApi.handoff(box.id, box.version, goodId), "Коробка принята на склад ГП с сохранением родословной")}>Принять коробку</button>
      </div>;
    })}</div>
  </div>;
}
