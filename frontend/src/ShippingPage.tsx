import { useEffect, useMemo, useRef, useState } from "react";
import { AutoTextarea } from "./AutoTextarea";
import {
  ApiError, failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type FromProductionRow, type Principal,
  type ProjectAddressee, type Shipment,
} from "./api";
import { canAct, hasFullAccess } from "./navigation";
import { DataTable, type DataColumn } from "./DataTable";
import { fetchBoxLabels, openBoxLabels, openShippingForms } from "./shippingPrint";
import { formatDate } from "./format";
import { StatusChip as SharedChip } from "./StatusChip";
import { isoDay } from "./Gantt";
import { GoodsStateBar, goodsPieces, sumPieces } from "./FinishedGoodsStates";

/* Отгрузка (владелец 12.10.2026) — путь готовой продукции слева направо, штуки КБ в сквозных статусах:
   «Из производства» — что цех сдал; кладовщик отмечает КБ и принимает одной кнопкой (В производстве → Принято складом);
   «На складе» — принятое, ещё не упакованное, по проектам; «Упаковать» заводит отправку (→ Упаковано);
   «Отправки» — по статусам; документы PL/DN, «Отгрузить» (→ Отгружено), откат отгрузки — только администратору. */
type Tab = "production" | "stock" | "shipments";
const TABS: Tab[] = ["production", "stock", "shipments"];
/** Сообщение после действия: строка — успех, `{ warning }` — сделано не всё (показывается как ошибка). */
type Outcome = string | { warning: string };
type Mutate = (key: string, operation: () => Promise<unknown>, message: string | ((result: unknown) => Outcome)) => Promise<boolean>;

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;
const today = () => isoDay(new Date());

/** Срок прошёл, а груз ещё не уехал. */
function overdue(shipment: Shipment) {
  return Boolean(shipment.deadline) && String(shipment.deadline) < today() && ACTIVE.includes(shipment.status);
}

const STATUS_HINT: Record<string, string> = {
  "Новая": "заявка — ничего не набрано", "Собрана": "набрана, PL и DN не сформированы", "Упакована": "упакована, PL и DN готовы",
  "Отгружена": "уехала, не закрыта", "Закрыта": "уехала, документы на месте", "Отменена": "отменена, набранное вернулось на склад",
};
function StatusChip({ status }: { status: string }) {
  return <SharedChip status={status} title={STATUS_HINT[status]} />;
}
function Due({ day, late }: { day: string | null; late: boolean }) {
  if (!day) return <span className="muted">—</span>;
  return <span className={late ? "tone-danger" : undefined}>{formatDate(day)}{late && <small className="tone-danger">просрочена</small>}</span>;
}

/** Почему отгруженная отправка не закрылась: «Нельзя закрыть: не хватает — файл УПД» → «Не закрыта — не хватает: файл УПД». */
function notClosed(why: string | undefined) {
  const text = String(why || "").replace(/^Нельзя закрыть:\s*/, "");
  const missing = /^не хватает\s*—\s*/.exec(text);
  return missing ? `Не закрыта — не хватает: ${text.slice(missing[0].length)}` : `Не закрыта — ${text || "не хватает данных"}`;
}

function readFile(file: File) {
  return new Promise<{ name: string; type: string; base64: string }>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, type: file.type || "application/octet-stream", base64: String(reader.result ?? "").split(",")[1] ?? "" });
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

function initialTab(): Tab {
  const wanted = new URLSearchParams(window.location.search).get("tab");
  return TABS.includes(wanted as Tab) ? wanted as Tab : "production";
}

export function ShippingPage({ principal }: { principal: Principal }) {
  // Два ключа матрицы: «Отгрузка» (`shipping`) — менеджер проекта, «Отгрузка — склад» (`shipping_goods`) — кладовщик.
  const canManage = canAct(principal, "shipping");
  const canWarehouse = canAct(principal, "shipping_goods");
  // Откат «Отгружено» — только администратор (сервер: full_access).
  const canUnship = hasFullAccess(principal);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [goods, setGoods] = useState<FinishedGood[]>([]);
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [addressees, setAddressees] = useState<ProjectAddressee[]>([]);
  const [incoming, setIncoming] = useState<FromProductionRow[]>([]);
  const [readyBoxes, setReadyBoxes] = useState<FinishedGoodsReadyBox[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const canEdit = canManage || canWarehouse;

  async function reload() {
    try {
      const [goodsResult, shipmentResult, addresseeResult, incomingResult, boxesResult] = await Promise.all([
        shippingApi.goods(), shippingApi.shipments(), shippingApi.addressees(), shippingApi.fromProduction(),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] }),
      ]);
      setGoods(goodsResult.goods); setShipments(shipmentResult.shipments);
      setAddressees(addresseeResult.addressees ?? []); setIncoming(incomingResult.rows ?? []); setReadyBoxes(boxesResult.boxes ?? []);
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить готовую продукцию и отгрузки"));
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const mutate: Mutate = async (key, operation, message) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try {
      const result = await operation();
      const outcome = typeof message === "function" ? message(result) : message;
      if (typeof outcome === "string") setNotice(outcome); else setError(outcome.warning);
      await reload();
      return typeof outcome === "string";
    } catch (reason) { setError(failureText(reason, "Операция не выполнена")); return false; }
    finally { setBusy(""); }
  };

  const rows = useMemo(() => projectRows(goods, shipments, addressees), [goods, shipments, addressees]);
  const stockRows = rows.filter((row) => row.ready > 0);
  const toAccept = incoming.filter((row) => num(row.remaining) > 0).length;
  const activeShipments = shipments.filter((shipment) => ACTIVE.includes(shipment.status)).length;
  const tabs: Array<[Tab, string, number, string]> = [
    ["production", "Из производства", toAccept, "КБ, которые цех сдал, а склад ещё не принял"],
    ["stock", "На складе", stockRows.length, "Проекты, у которых есть принятое и ещё не упакованное"],
    ["shipments", "Отправки", activeShipments, "Отправки, которые ещё не уехали"],
  ];
  const context = { goods, shipments, addressees, canManage, canWarehouse, canEdit, canUnship, busy, mutate };
  return <section className="page shipping-page">
    <header className="page-head"><h1>Отгрузка</h1></header>
    <div className="seg-tabs ship-tabs" role="tablist" aria-label="Отгрузка">{tabs.map(([key, label, count, hint]) =>
      <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} title={hint} onClick={() => { setTab(key); setNotice(""); }}>
        {label}<span className={count > 0 ? "ship-tab-count" : "ship-tab-count is-zero"}>{count}</span>
      </button>)}</div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {!loaded ? <div className="empty">Загрузка…</div> : <>
      {tab === "production" && <FromProductionTab {...context} rows={incoming} readyBoxes={readyBoxes} />}
      {tab === "stock" && <StockTab {...context} rows={stockRows} />}
      {tab === "shipments" && <ShipmentsTab {...context} />}
    </>}
  </section>;
}

interface Context {
  goods: FinishedGood[];
  shipments: Shipment[];
  addressees: ProjectAddressee[];
  canManage: boolean;
  canWarehouse: boolean;
  canEdit: boolean;
  canUnship: boolean;
  busy: string;
  mutate: Mutate;
}

/* ---------- Из производства: что цех сдал — отметить и принять одной кнопкой ---------- */

const incomingKey = (row: FromProductionRow) => `${row.project}\u0000${row.item_index}`;

function tasksText(row: FromProductionRow) {
  return row.tasks.map((task) => `${task.number}${task.site ? ` · ${task.site}` : ""} · сдано ${task.delivered} из ${task.quantity}`).join("\n");
}

function FromProductionTab({ rows, readyBoxes, goods, canWarehouse, busy, mutate }: Context & { rows: FromProductionRow[]; readyBoxes: FinishedGoodsReadyBox[] }) {
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [qty, setQty] = useState<Record<string, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [day, setDay] = useState(today);
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже сделанную приёмку, а не примет второй раз.
  const acceptKey = useRef(crypto.randomUUID());
  const customers = useMemo(() => {
    const byProject: Record<string, string> = {};
    for (const good of goods) if (good.customer && !byProject[good.project]) byProject[good.project] = good.customer;
    return byProject;
  }, [goods]);
  const amount = (row: FromProductionRow) => qty[incomingKey(row)] ?? row.remaining;
  const acceptable = rows.filter((row) => num(row.remaining) > 0);
  const chosen = acceptable.filter((row) => picked[incomingKey(row)] && num(amount(row)) > 0);
  const total = round2(chosen.reduce((sum, row) => sum + num(amount(row)), 0));
  const allPicked = acceptable.length > 0 && acceptable.every((row) => picked[incomingKey(row)]);

  const pick = (row: FromProductionRow, on: boolean) => {
    setPicked((current) => ({ ...current, [incomingKey(row)]: on }));
    setRowErrors((current) => ({ ...current, [incomingKey(row)]: "" }));
  };
  const pickAll = (on: boolean) => setPicked(Object.fromEntries(acceptable.map((row) => [incomingKey(row), on])));

  const submit = () => {
    const over: Record<string, string> = {};
    for (const row of chosen) if (num(amount(row)) > num(row.remaining)) over[incomingKey(row)] = `больше сданного: можно ${row.remaining}`;
    setRowErrors(over);
    if (Object.keys(over).length) return;
    const lines = chosen.map((row) => ({ project: row.project, item_index: row.item_index, quantity: String(amount(row)).replace(",", ".") }));
    const count = chosen.length;
    void mutate("accept", async () => {
      try {
        const result = await shippingApi.acceptFromProduction(lines, day || null, acceptKey.current);
        acceptKey.current = crypto.randomUUID();
        return result;
      } catch (reason) {
        // Пакет — все строки или ни одной: сервер называет строку («591К.3 КБ1: сдано цехом …»), показываем её там.
        const text = reason instanceof Error ? reason.message : "";
        const hit = reason instanceof ApiError && reason.status === 422 ? chosen.find((row) => text.startsWith(`${row.project} ${row.item_index}:`)) : undefined;
        if (hit) {
          setRowErrors({ [incomingKey(hit)]: text.slice(`${hit.project} ${hit.item_index}:`.length).trim() });
          throw new Error(`Ничего не принято: ${hit.project} ${hit.item_index} — ${text.slice(`${hit.project} ${hit.item_index}:`.length).trim()}`);
        }
        throw reason;
      }
    }, `Принято на склад: ${count} КБ · ${total} шт`).then((saved) => { if (saved) { setPicked({}); setQty({}); } });
  };

  const columns: DataColumn<FromProductionRow>[] = [
    ...(canWarehouse ? [{ key: "take", title: "Принять, шт", value: (row: FromProductionRow) => picked[incomingKey(row)] ? num(amount(row)) : "",
      render: (row: FromProductionRow) => {
        const key = incomingKey(row);
        if (!(num(row.remaining) > 0)) return <span className="muted">ещё в производстве</span>;
        const problem = rowErrors[key];
        return <span className="ship-take">
          <input type="checkbox" aria-label={`Принять ${row.project} ${row.item_index}`} checked={Boolean(picked[key])} onChange={(event) => pick(row, event.target.checked)} />
          <input className={problem ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько принять ${row.project} ${row.item_index}`} aria-invalid={problem ? true : undefined}
            type="number" min="0" max={row.remaining} step="any" value={amount(row)}
            onChange={(event) => { setQty({ ...qty, [key]: event.target.value }); pick(row, num(event.target.value) > 0); }} />
          {problem && <small className="tone-danger ship-row-error" role="alert">{problem}</small>}
        </span>;
      } }] : []),
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.item_index, render: (row) => <strong>{row.item_index}</strong> },
    { key: "name", title: "Наименование", value: (row) => row.name, wrap: true },
    { key: "needed", title: "Заказано", value: (row) => num(row.needed), numeric: true },
    { key: "delivered", title: "Сдано цехом", value: (row) => num(row.delivered), numeric: true, hint: "По живым заданиям: площадка — «Сдано», цех — годные финальной операции",
      render: (row) => <span title={tasksText(row)}>{row.delivered}</span> },
    { key: "accepted", title: "Принято", value: (row) => num(row.accepted), numeric: true },
    { key: "remaining", title: "Можно принять", value: (row) => num(row.remaining), numeric: true,
      render: (row) => num(row.remaining) > 0 ? <strong className="tone-ok">{row.remaining}</strong> : <span className="muted">—</span> },
    { key: "tasks", title: "Задания", value: (row) => row.tasks.map((task) => task.number).join(", "), wrap: true,
      render: (row) => row.tasks.length ? <span className="muted" title={tasksText(row)}>{row.tasks.map((task) => `${task.number}${task.site ? ` · ${task.site}` : ""}`).join(", ")}</span> : <span className="muted">—</span> },
  ];
  const toolbar = canWarehouse && acceptable.length > 0 ? <span className="ship-accept-bar">
    <label className="check-inline"><input type="checkbox" checked={allPicked} onChange={(event) => pickAll(event.target.checked)} />Отметить всё сданное · {acceptable.length}</label>
    <label className="ship-inline">Дата приёмки<input type="date" aria-label="Дата приёмки" value={day} max={today()} onChange={(event) => setDay(event.target.value)} /></label>
    <button type="button" className="primary" disabled={!chosen.length || Boolean(busy)} onClick={submit}>
      {busy === "accept" ? "Принимаем…" : `Принять на склад · ${total} шт`}</button>
  </span> : undefined;

  return <>
    <DataTable label="Из производства" storageKey="shipping-incoming" rows={rows} columns={columns} rowKey={incomingKey}
      groupBy={(row) => row.project} groupLabel={(project) => customers[project] ? `${project} · ${customers[project]}` : project}
      presets={[{ label: "Можно принять", test: (row) => num(row.remaining) > 0 }, { label: "Ещё в производстве", test: (row) => !(num(row.remaining) > 0) }]}
      defaultPreset="Можно принять" toolbar={toolbar} exportName="Из производства" searchPlaceholder="Проект, КБ, наименование или задание"
      rowClassName={(row) => num(row.remaining) > 0 ? (picked[incomingKey(row)] ? "ship-row-picked" : "") : "ship-line-off"}
      emptyText={rows.length ? "Цех пока ничего не сдал сверх принятого — КБ в производстве во вкладке «Все»." : "В производстве нет КБ, которые ждут склада."} />
    {canWarehouse && readyBoxes.length > 0 && <FinishedGoodsHandoff boxes={readyBoxes} goods={goods} busy={busy} mutate={mutate} />}
  </>;
}

/* ---------- На складе: принятое и ещё не упакованное, по проектам ---------- */

interface ProjectLine { good: FinishedGood; ready: number; packed: number; sent: number }
interface ProjectRow {
  project: string; customer: string; lines: ProjectLine[]; ordered: number; accepted: number; ready: number;
  packed: number; sent: number; point: string; open: Shipment[]; due: string | null; late: boolean;
}

function projectRows(goods: FinishedGood[], shipments: Shipment[], addressees: ProjectAddressee[]): ProjectRow[] {
  const open = shipments.filter((shipment) => ACTIVE.includes(shipment.status));
  const order: string[] = [];
  for (const good of goods) if (!order.includes(good.project)) order.push(good.project);
  const day = today();
  const rows = order.map((project): ProjectRow => {
    const lines = goods.filter((good) => good.project === project).map((good) => {
      const pieces = goodsPieces(good);
      return { good, ready: pieces.accepted, packed: pieces.packed, sent: pieces.shipped };
    });
    const sum = (pick: (line: ProjectLine) => number) => round2(lines.reduce((total, line) => total + pick(line), 0));
    const projectOpen = open.filter((shipment) => shipment.lines.some((line) => line.project === project));
    const due = projectOpen.map((shipment) => shipment.deadline).filter((value): value is string => Boolean(value)).sort()[0] ?? null;
    return {
      project, customer: lines.find((line) => line.good.customer)?.good.customer ?? "", lines,
      ordered: sum((line) => num(line.good.ordered)), accepted: sum((line) => num(line.good.accepted)),
      ready: sum((line) => line.ready), packed: sum((line) => line.packed), sent: sum((line) => line.sent),
      point: addressees.find((item) => item.project === project)?.shipping_point ?? "", open: projectOpen, due,
      late: Boolean(due) && String(due) < day,
    };
  });
  return rows.sort((a, b) => (a.customer || "яяя").localeCompare(b.customer || "яяя", "ru") || a.project.localeCompare(b.project, "ru"));
}

function StockTab(context: Context & { rows: ProjectRow[] }) {
  const { rows, canManage, busy, mutate } = context;
  const [adding, setAdding] = useState(false);
  const columns: DataColumn<ProjectRow>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <strong>{row.project}</strong> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true, hidden: true },
    { key: "kb", title: "КБ на складе", value: (row) => row.lines.filter((line) => line.ready > 0).map((line) => `${line.good.item_index} · ${line.ready}`).join(", "), wrap: true },
    { key: "ready", title: "Принято складом", value: (row) => row.ready, numeric: true, hint: "Лежит на складе, ещё не упаковано",
      render: (row) => <strong className="tone-ok">{row.ready}</strong> },
    { key: "states", title: "Статус КБ", value: (row) => row.ready, wrap: true,
      render: (row) => <GoodsStateBar pieces={sumPieces(row.lines.map((line) => goodsPieces(line.good)))} /> },
    { key: "due", title: "Срок", value: (row) => row.due ?? "", render: (row) => <Due day={row.due} late={row.late} /> },
    { key: "point", title: "Куда", value: (row) => row.point, wrap: true,
      render: (row) => row.point || <span className="tone-danger">адрес не указан</span> },
  ];
  return <>
    {adding && <RegisterGood busy={busy} mutate={mutate} onDone={() => setAdding(false)} />}
    <DataTable label="На складе" storageKey="shipping-stock" rows={rows} columns={columns} rowKey={(row) => row.project}
      groupBy={(row) => row.customer || "Заказчик не указан"} exportName="Отгрузка — на складе"
      toolbar={canManage && !adding ? <button type="button" className="secondary small" onClick={() => setAdding(true)}>+ Добавить КБ проекта</button> : undefined}
      emptyText="На складе ничего не ждёт упаковки — примите готовое во вкладке «Из производства»."
      renderDetail={(row) => <ShipPanel key={`${row.project}:${row.ready}:${row.packed}:${context.addressees.find((item) => item.project === row.project)?.version ?? 0}`} {...context} row={row} />} />
  </>;
}

/** КБ проекта в реестр ГП вручную — обычно КБ встают сюда при заведении проекта. */
function RegisterGood({ busy, mutate, onDone }: { busy: string; mutate: Mutate; onDone: () => void }) {
  const blank = { project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" };
  const [draft, setDraft] = useState(blank);
  const labels = { project: "Проект", item_index: "КБ", customer: "Заказчик", article: "Артикул КД", name: "Наименование", ordered: "В заказе", unit: "Ед. изм." };
  return <form className="panel ship-request" onSubmit={(event) => { event.preventDefault(); void mutate("good:new", () => shippingApi.registerGood(draft), "КБ добавлена в готовую продукцию").then((saved) => { if (saved) onDone(); }); }}>
    <h2>КБ проекта в готовую продукцию</h2>
    <div className="ship-form">
      {(["project", "item_index", "customer", "article", "name", "ordered", "unit"] as const).map((key) => <label key={key} className={key === "name" ? "ship-span2" : undefined}>{labels[key]}
        <input required={["project", "item_index", "name", "ordered", "unit"].includes(key)} type={key === "ordered" ? "number" : "text"} min={key === "ordered" ? "0.000001" : undefined} step={key === "ordered" ? "any" : undefined} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
    </div>
    <div className="ship-actions"><span className="ship-actions-end"><button type="button" className="secondary" onClick={onDone}>Отмена</button><button className="primary" disabled={Boolean(busy)}>Сохранить</button></span></div>
  </form>;
}

const CARD_FIELDS: Array<["receiver" | "shipping_point" | "contact" | "phone" | "carrier", string, string]> = [
  ["receiver", "Получатель", "как в документах"], ["shipping_point", "Адрес отгрузки", "куда везём"], ["contact", "Контактное лицо", "кто принимает"],
  ["phone", "Телефон", ""], ["carrier", "ТК / способ", "СДЭК, Деловые линии…"],
];
const REQUISITE_FIELDS: Array<[keyof ProjectAddressee, string, string]> = [
  ["po", "Заказ № (PO)", "номер заказа заказчика"], ["contract", "№ договора", ""], ["spec", "№ спецификации", "пусто — № проекта"],
  ["invoice", "№ счёта", ""], ["pays", "Оплачивает доставку", "заказчик / мы"], ["customer_address", "Адрес заказчика", "юридический"],
  ["hours", "Время работы", "пн–пт 9:00–18:00"],
];
type CardDraft = Record<"receiver" | "shipping_point" | "contact" | "phone" | "carrier" | "terms" | "upd_type", string>;

/** Проект на вкладке «На складе»: что упаковать, куда, документы — и одна кнопка «Упаковать». Отгружают — в «Отправках». */
function ShipPanel({ row, addressees, canManage, canEdit, busy, mutate }: Context & { row: ProjectRow }) {
  const stored = addressees.find((item) => item.project === row.project);
  const storedCard = (): CardDraft => ({
    receiver: stored?.receiver ?? "", shipping_point: stored?.shipping_point ?? "", contact: stored?.contact ?? "",
    phone: stored?.phone ?? "", carrier: stored?.carrier ?? "", terms: stored?.terms ?? "", upd_type: stored?.upd_type ?? "",
  });
  const [card, setCard] = useState<CardDraft>(storedCard);
  const [requisites, setRequisites] = useState<Record<string, string>>(() => Object.fromEntries(REQUISITE_FIELDS.map(([key]) => [key, String(stored?.[key] ?? "")])));
  const readyLines = row.lines.filter((line) => line.ready > 0);
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(readyLines.map((line) => [line.good.id, String(line.ready)])));
  const [marked, setMarked] = useState<Record<string, boolean>>(() => Object.fromEntries(readyLines.map((line) => [line.good.id, true])));
  const [docs, setDocs] = useState({ upd_number: "", upd_date: today(), deadline: "" });
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState("");
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже заведённую отправку, а не заведёт вторую.
  const packKey = useRef(crypto.randomUUID());
  const chosen = readyLines.filter((line) => marked[line.good.id] && num(qty[line.good.id]) > 0);
  const total = round2(chosen.reduce((sum, line) => sum + num(qty[line.good.id]), 0));
  const editCard = canManage;

  const saveRequisite = (key: string, value: string) => {
    if (String(stored?.[key as keyof ProjectAddressee] ?? "") === value) return;
    void mutate(`addressee:${row.project}`, () => shippingApi.saveAddressee({ project: row.project, version: stored?.version ?? 0, [key]: value }), "Реквизиты проекта сохранены");
  };

  const pack = () => {
    setProblem("");
    const over = chosen.find((line) => num(qty[line.good.id]) > line.ready);
    if (over) { setProblem(`${over.good.item_index}: на складе только ${over.ready}`); return; }
    if (!card.shipping_point.trim()) { setProblem("Укажите адрес отгрузки"); return; }
    const lines = chosen.map((line) => ({ good_id: line.good.id, version: line.good.version, quantity: qty[line.good.id] }));
    void mutate(`pack:${row.project}`, async () => {
      const before = storedCard();
      const changed = editCard ? Object.fromEntries(Object.entries(card).filter(([key, value]) => value.trim() !== before[key as keyof CardDraft])
        .map(([key, value]) => [key, (key === "terms" || key === "upd_type") ? value || null : value.trim()])) : {};
      const number = docs.upd_number.trim();
      const upload = file ? await readFile(file) : null;
      // Один запрос — одна транзакция: карточка, отправка с PL/DN и файл УПД сохраняются вместе или никак.
      // Адресат не копируется в отправку: пустые поля идут за карточкой проекта, пока отправка открыта.
      const result = await shippingApi.projectShip({
        project: row.project, addressee_version: stored?.version ?? 0, lines, ship: false,
        ...(Object.keys(changed).length ? { addressee: changed } : {}),
        ...(canManage ? { documents: { upd_number: number, upd_date: number ? docs.upd_date || null : null, deadline: docs.deadline || null } } : {}),
        ...(upload ? { upd_file: { file_name: upload.name, content_type: upload.type, content_base64: upload.base64 } } : {}),
      }, packKey.current);
      packKey.current = crypto.randomUUID();
      return result;
    }, (outcome) => {
      const { shipment } = outcome as { shipment: Shipment };
      return `Упаковано: ${shipment.number}${shipment.pl_number ? ` · ${shipment.pl_number}` : ""} — отгрузите во вкладке «Отправки»`;
    });
  };

  const field = ([key, label, placeholder]: [keyof CardDraft, string, string], className?: string) => <label key={key} className={className}>{label}
    <input aria-label={`${label} проекта ${row.project}`} disabled={!editCard} placeholder={editCard ? placeholder : ""} value={card[key]} onChange={(event) => setCard({ ...card, [key]: event.target.value })} /></label>;
  const choice = (key: "terms" | "upd_type", label: string, options: string[]) => <label>{label}
    <select aria-label={`${label} проекта ${row.project}`} disabled={!editCard} value={card[key]} onChange={(event) => setCard({ ...card, [key]: event.target.value })}>
      <option value="">не выбрано</option>{options.map((option) => <option key={option}>{option}</option>)}</select></label>;
  const filled = REQUISITE_FIELDS.map(([key]) => String(stored?.[key] ?? "")).filter(Boolean);

  return <div className="ship-panel">
    <div className="table-scroll"><table className="ship-lines">
      <thead><tr>{canEdit && <th aria-label="Отметка" />}<th>КБ</th><th>Наименование</th><th className="num">В заказе</th><th className="num">Принято складом</th><th className="num">Упаковано</th><th className="num">Отгружено</th>{canEdit && <th className="num">Упаковать</th>}</tr></thead>
      <tbody>{row.lines.map((line) => {
        const can = line.ready > 0;
        const tooMuch = num(qty[line.good.id]) > line.ready;
        return <tr key={line.good.id} className={can ? undefined : "ship-line-off"}>
          {canEdit && <td><input type="checkbox" aria-label={`Упаковать ${line.good.item_index}`} disabled={!can} checked={Boolean(marked[line.good.id])} onChange={(event) => setMarked({ ...marked, [line.good.id]: event.target.checked })} /></td>}
          <td><strong>{line.good.item_index}</strong></td><td className="ship-name">{line.good.name}</td>
          <td className="num">{line.good.ordered}</td>
          <td className="num"><strong>{line.ready}</strong></td><td className="num">{line.packed}</td><td className="num">{line.sent}</td>
          {canEdit && <td className="num">{can
            ? <input className={tooMuch ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько упаковать ${line.good.item_index}`} type="number" min="0" max={line.ready} step="any" value={qty[line.good.id] ?? ""}
              onChange={(event) => { setQty({ ...qty, [line.good.id]: event.target.value }); setMarked({ ...marked, [line.good.id]: num(event.target.value) > 0 }); }} />
            : <span className="muted">нет на складе</span>}</td>}
        </tr>;
      })}</tbody>
    </table></div>

    {canEdit && readyLines.length > 0 && <>
      <div className="ship-form">
        {field(CARD_FIELDS[0])}{field(CARD_FIELDS[1], "ship-span2")}{field(CARD_FIELDS[2])}
        {field(CARD_FIELDS[3])}{choice("terms", "Условия доставки", ["САМОВЫВОЗ", "ДОСТАВКА ТК"])}{field(CARD_FIELDS[4])}{choice("upd_type", "Тип УПД", ["ЭДО", "БУМАГА"])}
        {canManage && <label>№ УПД<input aria-label={`№ УПД проекта ${row.project}`} value={docs.upd_number} onChange={(event) => setDocs({ ...docs, upd_number: event.target.value })} /></label>}
        {canManage && <label>Дата УПД<input aria-label={`Дата УПД проекта ${row.project}`} type="date" value={docs.upd_date} onChange={(event) => setDocs({ ...docs, upd_date: event.target.value })} /></label>}
        <label>Файл УПД<span className="ship-file"><span className="secondary">Выбрать файл</span><span>{file ? file.name : "не выбран"}</span>
          <input aria-label={`Файл УПД проекта ${row.project}`} type="file" accept=".pdf,image/*" hidden onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></span></label>
        {canManage && <label>Отгрузить до<input aria-label={`Отгрузить до: проект ${row.project}`} type="date" value={docs.deadline} onChange={(event) => setDocs({ ...docs, deadline: event.target.value })} /></label>}
      </div>
      {canManage && <details className="ship-more"><summary>Реквизиты для отгрузочной ведомости <span className="muted">· {filled.length ? filled.join(" · ") : "не заполнены"}</span></summary>
        <div className="ship-form">{REQUISITE_FIELDS.map(([key, label, placeholder]) => <label key={key}>{label}
          <input aria-label={`${label} проекта ${row.project}`} placeholder={placeholder} value={requisites[key] ?? ""} onChange={(event) => setRequisites({ ...requisites, [key]: event.target.value })} onBlur={(event) => saveRequisite(key, event.target.value.trim())} /></label>)}</div>
      </details>}
      <div className="ship-actions">
        <span className="ship-total">Упаковать: <strong>{chosen.length} КБ · {total} шт</strong></span>
        {problem && <span className="tone-danger" role="alert">{problem}</span>}
        <span className="ship-actions-end">
          <button type="button" className="primary" disabled={!chosen.length || Boolean(busy)} onClick={pack}>{busy === `pack:${row.project}` ? "Упаковываем…" : `Упаковать · ${total} шт`}</button>
        </span>
      </div>
    </>}
    {readyLines.length === 0 && <div className="empty">На складе по проекту ничего нет — примите готовое во вкладке «Из производства».</div>}
    {row.open.length > 0 && <p className="ship-open-note">В отправках: {row.open.map((shipment) => <span key={shipment.id}>{shipment.number} <StatusChip status={shipment.status} /></span>)}</p>}
  </div>;
}

/* ---------- Отправки: по статусам ---------- */

/** Этап отправки для группировки: «Собирается» — заявка и набор, «Упакована» — PL/DN готовы, «Отгружена» — уехала. */
const PHASES: Array<[string, string[]]> = [
  ["Собирается", ["Новая", "Собрана"]], ["Упакована", ["Упакована"]], ["Отгружена", ["Отгружена", "Закрыта"]], ["Отменена", ["Отменена"]],
];
const phaseOf = (shipment: Shipment) => PHASES.findIndex(([, statuses]) => statuses.includes(shipment.status));
const phaseName = (shipment: Shipment) => PHASES[phaseOf(shipment)]?.[0] ?? shipment.status;

function shipmentRows(shipments: Shipment[]) {
  // По этапам; внутри — то, что ещё надо везти, по сроку; уехавшие и отменённые — свежие первыми.
  return shipments.slice().sort((a, b) => {
    const pa = phaseOf(a); const pb = phaseOf(b);
    if (pa !== pb) return pa - pb;
    const done = pa >= 2;
    if (!done) { const da = a.deadline ?? "9999"; const db = b.deadline ?? "9999"; if (da !== db) return da < db ? -1 : 1; }
    return a.created_at < b.created_at ? (done ? 1 : -1) : a.created_at > b.created_at ? (done ? -1 : 1) : 0;
  });
}
/** «591К.3: КБ1, КБ2; 600К.1: КБ7». */
function projectsOf(shipment: Shipment) {
  const byProject = new Map<string, string[]>();
  for (const line of shipment.lines) {
    const items = byProject.get(line.project) ?? [];
    if (!items.includes(line.item_index)) items.push(line.item_index);
    byProject.set(line.project, items);
  }
  return [...byProject].map(([project, items]) => `${project}: ${items.join(", ")}`).join("; ");
}
const sumOf = (shipment: Shipment, key: "picked" | "requested") => round2(shipment.lines.reduce((sum, line) => sum + num(line[key]), 0));
const noUpd = (shipment: Shipment) => ["Отгружена", "Закрыта"].includes(shipment.status) && (!shipment.upd_number || !shipment.upd_url);

function ShipmentsTab({ goods, shipments, canManage, canWarehouse, canEdit, canUnship, busy, mutate }: Context) {
  const rows = useMemo(() => shipmentRows(shipments), [shipments]);
  const [creating, setCreating] = useState(false);
  const columns: DataColumn<Shipment>[] = [
    { key: "number", title: "№ отправки", value: (row) => row.number, render: (row) => <><strong>{row.number}</strong><small>{formatDate(row.created_at.slice(0, 10))}</small></> },
    { key: "status", title: "Статус", value: (row) => row.status, filter: true,
      render: (row) => <><StatusChip status={row.status} />{row.shipped_at ? <small>отгружена {formatDate(row.shipped_at)}</small> : row.packed_at && <small>упакована {formatDate(row.packed_at)}</small>}</> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer || row.receiver, filter: true, hidden: true },
    { key: "projects", title: "Проект · КБ", value: (row) => projectsOf(row), wrap: true },
    { key: "picked", title: "Упаковано, шт", value: (row) => sumOf(row, "picked"), numeric: true, hint: "Набрано в отправку из заказанного",
      render: (row) => { const picked = sumOf(row, "picked"); const asked = sumOf(row, "requested"); return picked < asked ? <span className="tone-warn">{picked} из {asked}</span> : picked; } },
    { key: "point", title: "Куда", value: (row) => `${row.shipping_point} ${row.receiver}`, wrap: true,
      render: (row) => <>{row.shipping_point || <span className="tone-danger">адрес не указан</span>}<small>{[row.receiver, row.terms, row.carrier].filter(Boolean).join(" · ")}</small></> },
    { key: "deadline", title: "Срок", value: (row) => row.deadline ?? "", render: (row) => <Due day={row.deadline} late={overdue(row)} /> },
    { key: "docs", title: "PL · DN", value: (row) => row.pl_number ? `${row.pl_number} · ${row.dn_number}` : "",
      render: (row) => row.pl_number ? `${row.pl_number} · ${row.dn_number}` : <span className="muted">—</span> },
    { key: "upd", title: "УПД", value: (row) => row.upd_number,
      render: (row) => noUpd(row) ? <span className="tone-danger">{row.upd_number ? `${row.upd_number} · нет файла` : "нет"}</span> : row.upd_number || <span className="muted">—</span> },
  ];
  return <>
    {creating && <CreateShipment goods={goods} busy={busy} mutate={mutate} onDone={() => setCreating(false)} />}
    <DataTable label="Отправки" storageKey="shipping-shipments-v3" rows={rows} columns={columns} rowKey={(row) => row.id}
      groupBy={phaseName}
      presets={[{ label: "В работе", test: (row) => ACTIVE.includes(row.status) }, { label: "Отгружена", test: (row) => ["Отгружена", "Закрыта"].includes(row.status) },
        { label: "Просрочены", test: overdue }, { label: "Отгружено без УПД", test: noUpd }]}
      defaultPreset="В работе"
      toolbar={canManage && !creating ? <button type="button" className="secondary small" onClick={() => setCreating(true)}>+ Заявка на отгрузку</button> : undefined}
      rowClassName={(row) => row.status === "Отменена" ? "cancelled" : ""} exportName="Отправки"
      emptyText={rows.length ? "Отправок в работе нет — упакуйте принятое во вкладке «На складе». Уехавшие — в «Отгружена» и «Все»." : "Отправок нет — они появляются, когда на вкладке «На складе» нажимают «Упаковать»."}
      renderDetail={(row) => <ShipmentDetail key={`${row.id}:${row.version}`} shipment={row} canManage={canManage} canWarehouse={canWarehouse} canEdit={canEdit} canUnship={canUnship} busy={busy} mutate={mutate} />} />
  </>;
}

/** Заявка заранее: заказать можно больше, чем на складе, и из разных проектов — набирается по мере приёмки. */
function CreateShipment({ goods, busy, mutate, onDone }: { goods: FinishedGood[]; busy: string; mutate: Mutate; onDone: () => void }) {
  const blank = { customer: "", receiver: "", shipping_point: "", contact: "", phone: "", terms: "", carrier: "", deadline: "", upd_type: "" };
  const [draft, setDraft] = useState(blank);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [noDocs, setNoDocs] = useState(false);
  const selected = goods.filter((good) => num(quantities[good.id]) > 0);
  const open = goods.filter((good) => num(good.sent) < num(good.ordered));
  // Пустые получатель, точка, условия и тип УПД наследуются из карточки проекта — сервер скажет, если их нет и там.
  const payload = () => {
    const body: Record<string, unknown> = { ...draft, no_docs: noDocs, lines: selected.map((good) => ({ good_id: good.id, quantity: quantities[good.id] })) };
    for (const key of ["terms", "upd_type", "deadline"]) if (!body[key]) body[key] = null;
    return body;
  };
  const text = (key: keyof typeof blank, label: string, className?: string) => <label key={key} className={className}>{label}
    <input placeholder={key === "customer" ? "" : "из карточки проекта"} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>;
  return <form className="panel ship-request" onSubmit={(event) => { event.preventDefault(); void mutate("shipment:new", () => shippingApi.create(payload()), "Заявка на отгрузку создана").then((saved) => { if (saved) onDone(); }); }}>
    <h2>Заявка на отгрузку</h2>
    <div className="ship-form">
      {text("customer", "Заказчик")}{text("receiver", "Получатель")}{text("shipping_point", "Точка отгрузки", "ship-span2")}
      {text("contact", "Контакт")}{text("phone", "Телефон")}
      <label>Условия<select value={draft.terms} onChange={(event) => setDraft({ ...draft, terms: event.target.value })}><option value="">из карточки проекта</option><option>САМОВЫВОЗ</option><option>ДОСТАВКА ТК</option></select></label>
      {text("carrier", "ТК / способ")}
      <label>Тип УПД<select value={draft.upd_type} onChange={(event) => setDraft({ ...draft, upd_type: event.target.value })}><option value="">из карточки проекта</option><option>ЭДО</option><option>БУМАГА</option></select></label>
      <label>Дедлайн<input type="date" value={draft.deadline} onChange={(event) => setDraft({ ...draft, deadline: event.target.value })} /></label>
    </div>
    <div className="table-scroll"><table className="ship-lines">
      <thead><tr><th>Проект</th><th>КБ</th><th>Наименование</th><th className="num">В заказе</th><th className="num">На складе</th><th className="num">Заказать</th></tr></thead>
      <tbody>{open.map((good) => <tr key={good.id}><td><strong>{good.project}</strong></td><td>{good.item_index}</td><td className="ship-name">{good.name}</td>
        <td className="num">{good.ordered}</td><td className="num">{good.remaining}</td>
        <td className="num"><input className="ship-qty" aria-label={`Заказать ${good.project} ${good.item_index}`} type="number" min="0" step="any" value={quantities[good.id] ?? ""} onChange={(event) => setQuantities({ ...quantities, [good.id]: event.target.value })} /></td></tr>)}</tbody>
    </table></div>
    <div className="ship-actions">
      <label className="check-inline"><input type="checkbox" checked={noDocs} onChange={(event) => setNoDocs(event.target.checked)} />Без отгрузочных документов (только набрать)</label>
      <span className="ship-actions-end">
        <button type="button" className="secondary" onClick={onDone}>Отмена</button>
        <button className="primary" disabled={!selected.length || Boolean(busy)}>Создать заявку</button>
      </span>
    </div>
  </form>;
}

function ShipmentDetail({ shipment, canManage, canWarehouse, canEdit, canUnship, busy, mutate }: { shipment: Shipment; canManage: boolean; canWarehouse: boolean; canEdit: boolean; canUnship: boolean; busy: string; mutate: Mutate }) {
  const [documents, setDocuments] = useState({ upd_type: shipment.inherited.includes("upd_type") ? "" : shipment.upd_type ?? "", upd_number: shipment.upd_number, upd_date: shipment.upd_date ?? "", track_number: shipment.track_number });
  const [requested, setRequested] = useState<Record<string, string>>({});
  const [layout, setLayout] = useState(false);
  const [printError, setPrintError] = useState("");
  const [rollback, setRollback] = useState<string | null>(null);
  const action = (name: "pick" | "pack" | "ship" | "close" | "cancel", message: string | ((result: unknown) => string)) => mutate(`${name}:${shipment.id}`, () => shippingApi.action(shipment.id, name, shipment.version), message);
  const got = shipment.lines.some((line) => num(line.picked) > 0);
  const open = !["Закрыта", "Отменена"].includes(shipment.status);
  const shipped = ["Отгружена", "Закрыта"].includes(shipment.status);
  const late = overdue(shipment);
  const print = async (document: "pl" | "dn") => {
    setPrintError("");
    try { openShippingForms((await shippingApi.print(shipment.id, document)).forms); }
    catch (reason) { setPrintError(failureText(reason, "Не удалось подготовить печать")); }
  };
  const printLabels = async () => {
    setPrintError("");
    try { openBoxLabels((await fetchBoxLabels(shipment.id)).labels); }
    catch (reason) { setPrintError(failureText(reason, "Не удалось подготовить этикетки")); }
  };
  const uploadUpd = (file: File | undefined) => {
    if (!file) return;
    void mutate(`upd:${shipment.id}`, async () => shippingApi.uploadUpd(shipment.id, shipment.version, await readFile(file)), "УПД приложена");
  };
  const saveRequested = (lineId: string, current: string) => {
    const value = requested[lineId];
    if (value === undefined || value === "" || num(value) === num(current)) return;
    void mutate(`qty:${lineId}`, () => shippingApi.setRequested(shipment.id, shipment.version, lineId, value), "Заказанное количество сохранено");
  };
  const ship = () => void action("ship", (result) => {
    const outcome = result as { closed?: boolean; why?: string };
    return outcome.closed ? `Отгружено и закрыто: ${shipment.number}` : `Отгружено: ${shipment.number}. ${notClosed(outcome.why)}`;
  });
  const unship = () => {
    const reason = (rollback ?? "").trim();
    if (!reason) return;
    void mutate(`unship:${shipment.id}`, () => shippingApi.unship(shipment.id, shipment.version, reason), `Отгрузка откатена: ${shipment.number} снова на складе, упакована`)
      .then((saved) => { if (saved) setRollback(null); });
  };
  const disabled = Boolean(busy);
  return <article className={`ship-card${shipment.status === "Отменена" ? " cancelled" : ""}`}>
    <div className="ship-card-head">
      <strong>{shipment.number}</strong><StatusChip status={shipment.status} />
      <span>{shipment.receiver || shipment.customer} · {shipment.shipping_point || <span className="tone-danger">адрес не указан</span>}</span>
      {(shipment.terms || shipment.carrier) && <span className="muted">{[shipment.terms, shipment.carrier].filter(Boolean).join(" · ")}</span>}
      {shipment.deadline && <span className={late ? "tone-danger" : undefined}>срок {formatDate(shipment.deadline)}{late ? " · просрочена" : ""}</span>}
      {shipment.pl_number && <span className="muted">{shipment.pl_number} · {shipment.dn_number}</span>}
      {shipment.places > 0 && <span className="muted">мест {shipment.places}{shipment.weight ? ` · ${shipment.weight} кг` : ""}</span>}
      {shipment.shipped_at ? <span className="muted">отгружена {formatDate(shipment.shipped_at)}</span> : shipment.packed_at && <span className="muted">упакована {formatDate(shipment.packed_at)}</span>}
      {shipment.upd_url ? <a href={shipment.upd_url} target="_blank" rel="noreferrer">УПД{shipment.upd_number ? ` № ${shipment.upd_number}` : ""}</a> : <span className="tone-danger">файла УПД нет</span>}
    </div>
    <div className="table-scroll"><table className="ship-lines">
      <thead><tr><th>Позиция</th><th>Наименование</th><th className="num">Заказано</th><th className="num">Упаковано</th><th className="num">Недобор</th><th>ГРН</th>{canWarehouse && <th aria-label="Возврат" />}</tr></thead>
      <tbody>{shipment.lines.map((line) => <tr key={line.id}>
        <td><strong>{line.project} · {line.item_index}</strong></td><td className="ship-name">{line.name}</td>
        <td className="num">{canEdit && open ? <input className="ship-qty" aria-label={`Заказано ${line.item_index}`} type="number" min="0.01" step="any" value={requested[line.id] ?? line.requested} onChange={(event) => setRequested({ ...requested, [line.id]: event.target.value })} onBlur={() => saveRequested(line.id, line.requested)} /> : `${line.requested} ${line.unit}`}</td>
        <td className="num">{line.picked}</td><td className={num(line.shortage) > 0 ? "num tone-warn" : "num"}>{line.shortage}</td><td>{line.grn || ""}</td>
        {canWarehouse && <td>{num(line.picked) > 0 && ["Новая", "Собрана"].includes(shipment.status) && <button type="button" className="danger small" disabled={disabled} onClick={() => void mutate(`unpick:${line.id}`, () => shippingApi.unpick(shipment.id, shipment.version, line.id), "Позиция возвращена на склад")}>Вернуть на склад</button>}</td>}
      </tr>)}</tbody>
    </table></div>
    {printError && <div className="error-banner" role="alert">{printError}</div>}
    <div className="ship-actions">
      {canWarehouse && got && ACTIVE.includes(shipment.status) && <button type="button" className="primary" disabled={disabled} onClick={ship}>Отгрузить</button>}
      {canWarehouse && shipment.status === "Отгружена" && <button type="button" className="primary" disabled={disabled} onClick={() => void action("close", `Отгрузка закрыта: ${shipment.number}`)}>Закрыть</button>}
      {canWarehouse && shipment.status === "Новая" && <button type="button" className="secondary" disabled={disabled} onClick={() => void action("pick", "Заявка набрана из доступного остатка")}>Набрать со склада</button>}
      {shipment.pl_number && got && <><button type="button" className="secondary" onClick={() => void print("pl")}>Печать PL</button><button type="button" className="secondary" onClick={() => void print("dn")}>Печать DN</button><button type="button" className="secondary" onClick={() => void printLabels()}>Печать этикеток</button></>}
      {canEdit && got && (ACTIVE.includes(shipment.status) || (shipment.pl_number && shipment.status === "Отгружена")) && !layout && <button type="button" className="secondary" disabled={disabled} onClick={() => setLayout(true)}>{shipment.pl_number ? "Перевыпустить" : "Сформировать отгрузочные"}</button>}
      {canEdit && open && <label className="secondary file-button">Загрузить УПД<input type="file" accept=".pdf,image/*" aria-label={`Загрузить УПД ${shipment.number}`} hidden onChange={(event) => { uploadUpd(event.target.files?.[0]); event.target.value = ""; }} /></label>}
      <span className="ship-actions-end">
        {canUnship && shipped && rollback === null && <button type="button" className="danger small" disabled={disabled} onClick={() => setRollback("")}>Откатить отгрузку</button>}
        {canManage && ACTIVE.includes(shipment.status) && <button type="button" className="danger small" disabled={disabled} onClick={() => { if (window.confirm(`Отменить заявку ${shipment.number}? Набранное вернётся на склад.`)) void action("cancel", "Заявка отменена, набранное вернулось на склад"); }}>Отменить заявку</button>}
      </span>
    </div>
    {canUnship && shipped && rollback !== null && <div className="ship-rollback" role="group" aria-label={`Откат отгрузки ${shipment.number}`}>
      <label className="ship-span2">Причина отката<input aria-label={`Причина отката ${shipment.number}`} placeholder="например: нажали «Отгрузить» по ошибке, груз на складе" value={rollback} onChange={(event) => setRollback(event.target.value)} /></label>
      <button type="button" className="danger" disabled={disabled || !rollback.trim()} onClick={unship}>Откатить в «Упакована»</button>
      <button type="button" className="secondary" onClick={() => setRollback(null)}>Не откатывать</button>
    </div>}
    {layout && <BoxesEditor shipment={shipment} busy={busy} mutate={mutate} onDone={() => setLayout(false)} />}
    {canManage && open && <div className="ship-docs">
      <label>Тип УПД<select value={documents.upd_type} onChange={(event) => setDocuments({ ...documents, upd_type: event.target.value })}><option value="">{`из карточки проекта${shipment.inherited.includes("upd_type") && shipment.upd_type ? `: ${shipment.upd_type}` : ""}`}</option><option>ЭДО</option><option>БУМАГА</option></select></label>
      <label>№ УПД<input value={documents.upd_number} onChange={(event) => setDocuments({ ...documents, upd_number: event.target.value })} /></label>
      <label>Дата УПД<input type="date" value={documents.upd_date} onChange={(event) => setDocuments({ ...documents, upd_date: event.target.value })} /></label>
      <label>Трек-номер<input value={documents.track_number} onChange={(event) => setDocuments({ ...documents, track_number: event.target.value })} /></label>
      <button type="button" className="secondary" disabled={disabled} onClick={() => void mutate(`docs:${shipment.id}`, () => shippingApi.documents(shipment.id, { version: shipment.version, ...documents, upd_url: shipment.upd_url, upd_type: documents.upd_type || null, upd_date: documents.upd_date || null }), "Данные УПД и доставки сохранены")}>Сохранить документы</button>
    </div>}
    {canEdit && open && <details className="ship-more"><summary>Получатель и адрес этой отправки</summary><ShipmentHead shipment={shipment} busy={busy} mutate={mutate} /></details>}
  </article>;
}

const HEAD_FIELDS: Array<[keyof Shipment & string, string]> = [
  ["receiver", "Получатель"], ["shipping_point", "Адрес отгрузки"], ["contact", "Контактное лицо"], ["phone", "Телефон"],
  ["carrier", "ТК / способ"],
];

/** Шапка открытой отправки. Поле сохраняется при уходе из него; пустое поле адресата снова берётся из
 * карточки проекта и идёт за ней, пока отправка не закрыта. */
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
  const mark = (key: string) => inherited(key) ? <small className="ship-inherited">из карточки проекта</small> : null;
  return <div className="ship-form">
    {HEAD_FIELDS.map(([key, label]) => <label key={key} className={key === "shipping_point" ? "ship-span2" : undefined}><span>{label}{mark(key)}</span><input aria-label={`${label} ${shipment.number}`} disabled={Boolean(busy)} placeholder={inherited(key) ? "" : "пусто — из карточки проекта"} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} onBlur={() => save(key)} /></label>)}
    <label><span>Условия доставки{mark("terms")}</span><select aria-label={`Условия доставки ${shipment.number}`} disabled={Boolean(busy)} value={draft.terms} onChange={(event) => setDraft({ ...draft, terms: event.target.value })} onBlur={() => save("terms")}><option value="">из карточки проекта</option><option>САМОВЫВОЗ</option><option>ДОСТАВКА ТК</option></select></label>
    <label>Срок отгрузки<input type="date" aria-label={`Дедлайн ${shipment.number}`} disabled={Boolean(busy)} value={draft.deadline} onChange={(event) => setDraft({ ...draft, deadline: event.target.value })} onBlur={() => save("deadline")} /></label>
    <label className="ship-span2">Комментарий<AutoTextarea aria-label={`Комментарий ${shipment.number}`} disabled={Boolean(busy)} value={draft.note} onChange={(note) => setDraft({ ...draft, note })} onBlur={() => save("note")} /></label>
  </div>;
}

interface BoxDraft { kind: string; dims: string; net: string; gross: string; seal: string; q: Record<string, string> }

/** Сетка «КБ × BXN», характеристики каждого места и примечания для ТК. */
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
  return <div className="ship-boxes">
    <div className="ship-boxes-head"><strong>{shipment.pl_number ? "Перевыпустить документы" : "Отгрузочные документы"} · {shipment.number}</strong>
      <label className="ship-inline">Коробов<input aria-label="Коробов" type="number" min="1" max="30" value={boxes.length} onChange={(event) => resize(Number(event.target.value))} /></label></div>
    <div className="table-scroll"><table className="ship-lines"><thead><tr><th>КБ</th><th className="num">Упак.</th>{boxes.map((_, index) => <th key={index}>BXN {index + 1}</th>)}</tr></thead><tbody>
      {packed.map((item) => <tr key={item.index}><td><strong>{item.index}</strong><small>{item.name}</small></td><td className="num">{item.qty}</td>{boxes.map((box, index) => <td key={index}><input className="ship-qty" aria-label={`${item.index} в BXN ${index + 1}`} value={box.q[item.index] ?? ""} onChange={(event) => change(index, { q: { ...box.q, [item.index]: event.target.value } })} /></td>)}</tr>)}
      {fields.map(([key, label, placeholder]) => <tr key={key}><td colSpan={2}>{label}</td>{boxes.map((box, index) => <td key={index}><input aria-label={`${label} BXN ${index + 1}`} placeholder={placeholder} value={String(box[key] ?? "")} onChange={(event) => change(index, { [key]: event.target.value })} /></td>)}</tr>)}
    </tbody></table></div>
    <label>Примечания для ТК<textarea rows={2} value={tkNote} onChange={(event) => setTkNote(event.target.value)} /></label>
    <div className="ship-actions"><button type="button" className="primary" disabled={Boolean(busy)} onClick={submit}>{shipment.pl_number ? "Перевыпустить" : "Сформировать"}</button><button type="button" className="secondary" onClick={onDone}>Отмена</button></div>
  </div>;
}

/* ---------- Коробки из цеха (ОТК) — приёмка с родословной ---------- */

function boxCandidates(box: FinishedGoodsReadyBox, goods: FinishedGood[]) {
  const item = box.task_item.toUpperCase();
  return goods.filter((good) => good.project === box.project && (item === good.item_index.toUpperCase() || item.endsWith(`.${good.item_index.toUpperCase()}`) || item.endsWith(`-${good.item_index.toUpperCase()}`)));
}

/** Коробки, принятые ОТК, — на склад с родословной: отметить и принять разом. */
function FinishedGoodsHandoff({ boxes, goods, busy, mutate }: { boxes: FinishedGoodsReadyBox[]; goods: FinishedGood[]; busy: string; mutate: Mutate }) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const target = (box: FinishedGoodsReadyBox) => selected[box.id] ?? boxCandidates(box, goods)[0]?.id ?? "";
  const columns: DataColumn<FinishedGoodsReadyBox>[] = [
    { key: "number", title: "Коробка", value: (box) => box.number ?? "", render: (box) => <strong>{box.number}</strong> },
    { key: "item", title: "Проект · КБ", value: (box) => `${box.project} · ${box.task_item}` },
    { key: "quantity", title: "Кол-во", value: (box) => box.quantity, numeric: true },
    { key: "confirmed", title: "Подтверждена", value: (box) => box.confirmed_at ?? "", render: (box) => formatDate(box.confirmed_at) || <span className="muted">—</span> },
    { key: "good", title: "КБ в готовой продукции", value: (box) => target(box), render: (box) => {
      const candidates = boxCandidates(box, goods);
      if (!candidates.length) return <span className="tone-danger">нет КБ проекта в готовой продукции</span>;
      return <select aria-label={`КБ для коробки ${box.number}`} value={target(box)} onClick={(event) => event.stopPropagation()} onChange={(event) => setSelected((current) => ({ ...current, [box.id]: event.target.value }))}>
        {candidates.map((good) => <option key={good.id} value={good.id}>{good.project} · {good.item_index} · {good.name}</option>)}</select>;
    } },
  ];
  const handoff = (rows: FinishedGoodsReadyBox[]) => mutate("handoff", async () => {
    for (const box of rows) { const goodId = target(box); if (goodId) await productionApi.handoff(box.id, box.version, goodId); }
  }, `Принято на склад коробок: ${rows.filter(target).length}`);
  return <div className="ship-handoff">
    <h2>Коробки из цеха после ОТК <span className="page-count">{boxes.length}</span></h2>
    <DataTable label="Коробки из цеха" rows={boxes} columns={columns} rowKey={(box) => box.id} compactBelow={8}
      bulkActions={[{ label: "Принять на склад", run: handoff, disabled: (rows) => Boolean(busy) || !rows.some(target) }]}
      rowLabel={(box) => `коробка ${box.number}`} />
  </div>;
}
