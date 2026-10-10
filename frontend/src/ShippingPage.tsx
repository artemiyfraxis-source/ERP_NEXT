import { useEffect, useMemo, useRef, useState } from "react";
import { AutoTextarea } from "./AutoTextarea";
import {
  failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal, type ProjectAddressee,
  type Shipment,
} from "./api";
import { canAct } from "./navigation";
import { DataTable, type DataColumn } from "./DataTable";
import { openPrintSheets } from "./print";
import { formatDate } from "./format";
import { isoDay } from "./Gantt";

/* Отгрузка: «К отгрузке» — что готово по заказчикам и проектам и одна кнопка «Отгрузить» с реквизитами
   в одном месте; «Отправки» — реестр и история; «Готовая продукция» — склад ГП и приёмка из цеха. */
type Tab = "ready" | "shipments" | "goods";
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

const STATUS_TONE: Record<string, string> = {
  // Серый — ждёт цех, бирюзовый — в работе склада, жёлтый — уехала, но не закрыта, зелёный — закрыта.
  "Новая": "", "Собрана": "tone-work", "Упакована": "tone-work", "Отгружена": "tone-prep", "Закрыта": "tone-stock", "Отменена": "tone-done",
};
const STATUS_HINT: Record<string, string> = {
  "Новая": "ждёт сдачи цеха — ничего не набрано", "Собрана": "набрана, PL и DN не сформированы", "Упакована": "упакована, PL и DN готовы",
  "Отгружена": "уехала, не закрыта", "Закрыта": "уехала, документы на месте", "Отменена": "отменена, набранное вернулось на склад ГП",
};
function StatusChip({ status }: { status: string }) {
  return <span className={`kit-status ${STATUS_TONE[status] ?? ""}`} title={STATUS_HINT[status]}>{status}</span>;
}
function Due({ day, late }: { day: string | null; late: boolean }) {
  if (!day) return null;
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

export function ShippingPage({ principal }: { principal: Principal }) {
  // Два ключа матрицы: «Отгрузка» (`shipping`) — менеджер проекта, «Отгрузка — склад» (`shipping_goods`) — кладовщик.
  const canManage = canAct(principal, "shipping");
  const canWarehouse = canAct(principal, "shipping_goods");
  const [tab, setTab] = useState<Tab>("ready");
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
    try {
      const [goodsResult, shipmentResult, addresseeResult, productionResult] = await Promise.all([
        shippingApi.goods(), shippingApi.shipments(), shippingApi.addressees(),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] }),
      ]);
      setGoods(goodsResult.goods); setShipments(shipmentResult.shipments);
      setAddressees(addresseeResult.addressees ?? []); setReadyBoxes(productionResult.boxes);
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

  const rows = useMemo(() => projectRows(goods, shipments, addressees, readyBoxes), [goods, shipments, addressees, readyBoxes]);
  const toShip = rows.filter(isToShip).length;
  const activeShipments = shipments.filter((shipment) => ACTIVE.includes(shipment.status)).length;
  const tabs: Array<[Tab, string, number]> = [
    ["ready", "К отгрузке", toShip], ["shipments", "Отправки", activeShipments], ["goods", "Готовая продукция", readyBoxes.length],
  ];
  const context = { goods, shipments, addressees, readyBoxes, canManage, canWarehouse, canEdit, busy, mutate };
  return <section className="page shipping-page">
    <header className="page-head"><h1>Отгрузка</h1></header>
    <div className="seg-tabs ship-tabs" role="tablist" aria-label="Отгрузка">{tabs.map(([key, label, count]) =>
      <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => setTab(key)}>
        {label}{count > 0 && <span className="ship-tab-count">{count}</span>}
      </button>)}</div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {!loaded ? <div className="empty">Загрузка…</div> : <>
      {tab === "ready" && <ReadyTab {...context} rows={rows} />}
      {tab === "shipments" && <ShipmentsTab {...context} />}
      {tab === "goods" && <GoodsTab {...context} />}
    </>}
  </section>;
}

interface Context {
  goods: FinishedGood[];
  shipments: Shipment[];
  addressees: ProjectAddressee[];
  readyBoxes: FinishedGoodsReadyBox[];
  canManage: boolean;
  canWarehouse: boolean;
  canEdit: boolean;
  busy: string;
  mutate: Mutate;
}

/* ---------- К отгрузке: проекты по заказчикам ---------- */

interface ProjectLine { good: FinishedGood; ready: number; packed: number; sent: number }
interface ProjectRow {
  project: string; customer: string; lines: ProjectLine[]; ordered: number; accepted: number; ready: number;
  packed: number; sent: number; point: string; noUpd: string[]; open: Shipment[]; due: string | null; late: boolean;
  waiting: number; waitingQty: number; stage: Stage;
}
type Stage = "ready" | "packed" | "noupd" | "prod" | "done";
const STAGES: Record<Stage, [string, string]> = {
  ready: ["Готово к отгрузке", "tone-stock"], packed: ["Упаковано — отгрузить", "tone-work"], noupd: ["Уехало без УПД", "tone-danger"],
  prod: ["В производстве", "tone-prep"], done: ["Отгружено", "tone-done"],
};
const STAGE_ORDER: Stage[] = ["ready", "packed", "noupd", "prod", "done"];
const isToShip = (row: ProjectRow) => row.ready > 0 || row.packed > 0;

function projectRows(goods: FinishedGood[], shipments: Shipment[], addressees: ProjectAddressee[], boxes: FinishedGoodsReadyBox[]): ProjectRow[] {
  const open = shipments.filter((shipment) => ACTIVE.includes(shipment.status));
  const packedByGood: Record<string, number> = {};
  for (const shipment of open) for (const line of shipment.lines) packedByGood[line.good_id] = (packedByGood[line.good_id] ?? 0) + num(line.picked);
  const order: string[] = [];
  for (const good of goods) if (!order.includes(good.project)) order.push(good.project);
  const day = today();
  const rows = order.map((project): ProjectRow => {
    const lines = goods.filter((good) => good.project === project).map((good) => ({
      good, ready: round2(num(good.remaining)), packed: round2(packedByGood[good.id] ?? 0), sent: round2(num(good.sent)),
    }));
    const sum = (pick: (line: ProjectLine) => number) => round2(lines.reduce((total, line) => total + pick(line), 0));
    const ordered = sum((line) => num(line.good.ordered)); const ready = sum((line) => line.ready);
    const packed = sum((line) => line.packed); const sent = sum((line) => line.sent);
    const mine = (shipment: Shipment) => shipment.lines.some((line) => line.project === project);
    const projectOpen = open.filter(mine);
    // Уехало, а закрыть нечем: нет № или файла УПД.
    const noUpd = shipments.filter((shipment) => shipment.status === "Отгружена" && mine(shipment) && (!shipment.upd_number || !shipment.upd_url)).map((shipment) => shipment.number);
    const due = projectOpen.map((shipment) => shipment.deadline).filter((value): value is string => Boolean(value)).sort()[0] ?? null;
    const waitingBoxes = boxes.filter((box) => box.project === project);
    const stage: Stage = ready > 0 ? "ready" : packed > 0 ? "packed" : noUpd.length ? "noupd" : (ordered > 0 && sent >= ordered) ? "done" : "prod";
    return {
      project, customer: lines.find((line) => line.good.customer)?.good.customer ?? "", lines, ordered,
      accepted: sum((line) => num(line.good.accepted)), ready, packed, sent,
      point: addressees.find((item) => item.project === project)?.shipping_point ?? "", noUpd, open: projectOpen, due,
      late: Boolean(due) && String(due) < day, waiting: waitingBoxes.length, waitingQty: round2(waitingBoxes.reduce((total, box) => total + num(box.quantity), 0)), stage,
    };
  });
  return rows.sort((a, b) => (a.customer || "яяя").localeCompare(b.customer || "яяя", "ru")
    || STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage) || a.project.localeCompare(b.project, "ru"));
}

function ReadyTab(context: Context & { rows: ProjectRow[] }) {
  const { rows, readyBoxes } = context;
  const columns: DataColumn<ProjectRow>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <strong>{row.project}</strong> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true, hidden: true },
    { key: "stage", title: "Состояние", value: (row) => STAGES[row.stage][0], filter: true,
      render: (row) => <span className={`kit-status ${STAGES[row.stage][1]}`}>{STAGES[row.stage][0]}</span> },
    { key: "kb", title: "КБ", value: (row) => row.lines.map((line) => line.good.item_index).join(", ") },
    { key: "ready", title: "Готово", value: (row) => row.ready, numeric: true, hint: "На складе ГП и ещё не в отправке",
      render: (row) => row.ready > 0 ? <strong className="tone-ok">{row.ready}</strong> : <span className="muted">0</span> },
    { key: "packed", title: "Упаковано", value: (row) => row.packed, numeric: true, hint: "В отправках, которые ещё не уехали" },
    { key: "sent", title: "Отгружено", value: (row) => `${row.sent} из ${row.ordered}` },
    { key: "accepted", title: "Сдано цехом", value: (row) => `${row.accepted} из ${row.ordered}` },
    ...(readyBoxes.length ? [{ key: "waiting", title: "Ждут приёмки", value: (row: ProjectRow) => row.waiting, hint: "Коробки из цеха, ещё не принятые на склад ГП",
      render: (row: ProjectRow) => row.waiting ? `${row.waiting} кор. · ${row.waitingQty} шт` : "" }] : []),
    { key: "due", title: "Срок", value: (row) => row.due ?? "", render: (row) => <Due day={row.due} late={row.late} /> },
    { key: "point", title: "Куда", value: (row) => row.point, wrap: true,
      render: (row) => row.point || <span className="tone-danger">адрес не указан</span> },
    { key: "noUpd", title: "Без УПД", value: (row) => row.noUpd.join(", "),
      render: (row) => row.noUpd.length ? <span className="tone-danger">{row.noUpd.join(", ")}</span> : "" },
  ];
  return <DataTable label="К отгрузке" storageKey="shipping-ready" rows={rows} columns={columns} rowKey={(row) => row.project}
    groupBy={(row) => row.customer || "Заказчик не указан"} exportName="Отгрузка — проекты"
    presets={[{ label: "К отгрузке", test: isToShip }, { label: "Уехало без УПД", test: (row) => row.noUpd.length > 0 },
      { label: "В производстве", test: (row) => row.stage === "prod" }, { label: "Отгружено", test: (row) => row.stage === "done" }]}
    defaultPreset="К отгрузке"
    emptyText={rows.length ? "Отгружать нечего: готовое уже в отправках или уехало. Остальные проекты — в «Все»." : "Готовой продукции пока нет — КБ встают сюда при заведении проекта."}
    renderDetail={(row) => <ShipPanel key={`${row.project}:${row.ready}:${row.packed}:${context.addressees.find((item) => item.project === row.project)?.version ?? 0}`} {...context} row={row} />} />;
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

/** Карточка проекта на вкладке «К отгрузке»: что отгружаем, куда, документы — и одна кнопка. */
function ShipPanel({ row, shipments, addressees, canManage, canWarehouse, canEdit, busy, mutate }: Context & { row: ProjectRow }) {
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
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже сделанную отгрузку, а не заведёт вторую.
  const shipKey = useRef(crypto.randomUUID());
  const chosen = readyLines.filter((line) => marked[line.good.id] && num(qty[line.good.id]) > 0);
  const total = round2(chosen.reduce((sum, line) => sum + num(qty[line.good.id]), 0));
  const editCard = canManage;

  const saveRequisite = (key: string, value: string) => {
    if (String(stored?.[key as keyof ProjectAddressee] ?? "") === value) return;
    void mutate(`addressee:${row.project}`, () => shippingApi.saveAddressee({ project: row.project, version: stored?.version ?? 0, [key]: value }), "Реквизиты проекта сохранены");
  };

  const submit = (shipNow: boolean) => {
    setProblem("");
    const over = chosen.find((line) => num(qty[line.good.id]) > line.ready);
    if (over) { setProblem(`${over.good.item_index}: на складе только ${over.ready}`); return; }
    if (!card.shipping_point.trim()) { setProblem("Укажите адрес отгрузки"); return; }
    if (shipNow && card.upd_type !== "ЭДО" && !file) { setProblem("По бумажной УПД отгрузить можно только с файлом УПД — приложите его или выберите тип «ЭДО»"); return; }
    const lines = chosen.map((line) => ({ good_id: line.good.id, version: line.good.version, quantity: qty[line.good.id] }));
    void mutate(`ship:${row.project}`, async () => {
      const before = storedCard();
      const changed = editCard ? Object.fromEntries(Object.entries(card).filter(([key, value]) => value.trim() !== before[key as keyof CardDraft])
        .map(([key, value]) => [key, (key === "terms" || key === "upd_type") ? value || null : value.trim()])) : {};
      const number = docs.upd_number.trim();
      const upload = file ? await readFile(file) : null;
      // Один запрос — одна транзакция: карточка, отправка, файл УПД и отгрузка сохраняются вместе или никак.
      // Адресат не копируется в отправку: пустые поля идут за карточкой проекта, пока отправка открыта.
      const result = await shippingApi.projectShip({
        project: row.project, addressee_version: stored?.version ?? 0, lines, ship: shipNow,
        ...(Object.keys(changed).length ? { addressee: changed } : {}),
        ...(canManage ? { documents: { upd_number: number, upd_date: number ? docs.upd_date || null : null, deadline: docs.deadline || null } } : {}),
        ...(upload ? { upd_file: { file_name: upload.name, content_type: upload.type, content_base64: upload.base64 } } : {}),
      }, shipKey.current);
      shipKey.current = crypto.randomUUID();
      return result;
    }, (outcome) => {
      const { shipment, shipped, closed, why } = outcome as { shipment: Shipment; shipped: boolean; closed: boolean; why: string };
      if (!shipped) return `Упаковано: ${shipment.number}${shipment.pl_number ? ` · ${shipment.pl_number}` : ""}`;
      return closed ? `Отгружено и закрыто: ${shipment.number}` : `Отгружено: ${shipment.number}. ${notClosed(why)}`;
    });
  };

  const field = ([key, label, placeholder]: [keyof CardDraft, string, string], className?: string) => <label key={key} className={className}>{label}
    <input aria-label={`${label} проекта ${row.project}`} disabled={!editCard} placeholder={editCard ? placeholder : ""} value={card[key]} onChange={(event) => setCard({ ...card, [key]: event.target.value })} /></label>;
  const choice = (key: "terms" | "upd_type", label: string, options: string[]) => <label>{label}
    <select aria-label={`${label} проекта ${row.project}`} disabled={!editCard} value={card[key]} onChange={(event) => setCard({ ...card, [key]: event.target.value })}>
      <option value="">не выбрано</option>{options.map((option) => <option key={option}>{option}</option>)}</select></label>;
  const filled = REQUISITE_FIELDS.map(([key]) => String(stored?.[key] ?? "")).filter(Boolean);
  const projectShipments = shipments.filter((shipment) => (ACTIVE.includes(shipment.status) || shipment.status === "Отгружена") && shipment.lines.some((line) => line.project === row.project));

  return <div className="ship-panel">
    <div className="table-scroll"><table className="ship-lines">
      <thead><tr>{canEdit && <th aria-label="Отметка" />}<th>КБ</th><th>Наименование</th><th className="num">В заказе</th><th className="num">Сдано цехом</th><th className="num">Готово</th><th className="num">Упаковано</th><th className="num">Отгружено</th>{canEdit && <th className="num">Отгрузить</th>}</tr></thead>
      <tbody>{row.lines.map((line) => {
        const can = line.ready > 0;
        const tooMuch = num(qty[line.good.id]) > line.ready;
        return <tr key={line.good.id} className={can ? undefined : "ship-line-off"}>
          {canEdit && <td><input type="checkbox" aria-label={`Отгрузить ${line.good.item_index}`} disabled={!can} checked={Boolean(marked[line.good.id])} onChange={(event) => setMarked({ ...marked, [line.good.id]: event.target.checked })} /></td>}
          <td><strong>{line.good.item_index}</strong></td><td className="ship-name">{line.good.name}</td>
          <td className="num">{line.good.ordered}</td><td className="num">{line.good.accepted}</td>
          <td className="num"><strong>{line.ready}</strong></td><td className="num">{line.packed}</td><td className="num">{line.sent}</td>
          {canEdit && <td className="num">{can
            ? <input className={tooMuch ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько отгрузить ${line.good.item_index}`} type="number" min="0" max={line.ready} step="any" value={qty[line.good.id] ?? ""}
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
        <span className="ship-total">К отгрузке: <strong>{chosen.length} КБ · {total} шт</strong></span>
        {problem && <span className="tone-danger" role="alert">{problem}</span>}
        <span className="ship-actions-end">
          <button type="button" className="secondary" disabled={!chosen.length || Boolean(busy)} onClick={() => submit(false)}>Упаковать, отгрузить позже</button>
          {canWarehouse && <button type="button" className="primary" disabled={!chosen.length || Boolean(busy)} onClick={() => submit(true)}>{busy === `ship:${row.project}` ? "Отгружаем…" : `Отгрузить ${total} шт`}</button>}
        </span>
      </div>
    </>}
    {readyLines.length === 0 && <div className="empty">{row.packed > 0 ? "Всё готовое уже упаковано — отгрузите отправку ниже." : "На складе ГП по проекту ничего нет — цех ещё не сдал готовую продукцию."}</div>}

    {projectShipments.length > 0 && <div className="ship-project-shipments">
      <h3>Отправки проекта</h3>
      {projectShipments.map((shipment) => <ShipmentDetail key={`${shipment.id}:${shipment.version}`} shipment={shipment} canManage={canManage} canWarehouse={canWarehouse} canEdit={canEdit} busy={busy} mutate={mutate} />)}
    </div>}
  </div>;
}

/* ---------- Отправки: реестр и история ---------- */

function shipmentRows(shipments: Shipment[]) {
  // Сверху то, что ещё надо везти, по сроку; уехавшие, закрытые, отменённые — вниз, свежие первыми.
  const done: Record<string, number> = { "Отгружена": 1, "Закрыта": 2, "Отменена": 3 };
  return shipments.slice().sort((a, b) => {
    const ga = done[a.status] ?? 0; const gb = done[b.status] ?? 0;
    if (ga !== gb) return ga - gb;
    if (!ga) { const da = a.deadline ?? "9999"; const db = b.deadline ?? "9999"; if (da !== db) return da < db ? -1 : 1; }
    return a.created_at < b.created_at ? (ga ? 1 : -1) : a.created_at > b.created_at ? (ga ? -1 : 1) : 0;
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

function ShipmentsTab({ goods, shipments, canManage, canWarehouse, canEdit, busy, mutate }: Context) {
  const rows = useMemo(() => shipmentRows(shipments), [shipments]);
  const [creating, setCreating] = useState(false);
  const columns: DataColumn<Shipment>[] = [
    { key: "number", title: "№ отправки", value: (row) => row.number, render: (row) => <><strong>{row.number}</strong><small>{formatDate(row.created_at.slice(0, 10))}</small></> },
    { key: "status", title: "Статус", value: (row) => row.status, filter: true,
      render: (row) => <><StatusChip status={row.status} />{row.shipped_at && <small>отгружена {formatDate(row.shipped_at)}</small>}</> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer || row.receiver, filter: true, hidden: true },
    { key: "projects", title: "Проект · КБ", value: (row) => projectsOf(row), wrap: true },
    { key: "picked", title: "Набрано", value: (row) => sumOf(row, "picked"), numeric: true,
      render: (row) => { const picked = sumOf(row, "picked"); const asked = sumOf(row, "requested"); return picked < asked ? <span className="tone-warn">{picked} из {asked}</span> : picked; } },
    { key: "point", title: "Куда", value: (row) => `${row.shipping_point} ${row.receiver}`, wrap: true,
      render: (row) => <>{row.shipping_point || <span className="tone-danger">адрес не указан</span>}<small>{[row.receiver, row.terms, row.carrier].filter(Boolean).join(" · ")}</small></> },
    { key: "deadline", title: "Срок", value: (row) => row.deadline ?? "", render: (row) => <Due day={row.deadline} late={overdue(row)} /> },
    { key: "docs", title: "PL · DN", value: (row) => row.pl_number ? `${row.pl_number} · ${row.dn_number}` : "" },
    { key: "upd", title: "УПД", value: (row) => row.upd_number,
      render: (row) => ["Отгружена", "Закрыта"].includes(row.status) && (!row.upd_number || !row.upd_url)
        ? <span className="tone-danger">{row.upd_number ? `${row.upd_number} · нет файла` : "нет"}</span> : row.upd_number },
  ];
  return <>
    {creating && <CreateShipment goods={goods} busy={busy} mutate={mutate} onDone={() => setCreating(false)} />}
    <DataTable label="Отправки" storageKey="shipping-shipments-v2" rows={rows} columns={columns} rowKey={(row) => row.id}
      presets={[{ label: "В работе", test: (row) => ACTIVE.includes(row.status) }, { label: "Просрочены", test: overdue }, { label: "Уехало без УПД", test: (row) => row.status === "Отгружена" && (!row.upd_number || !row.upd_url) }]}
      toolbar={canManage && !creating ? <button type="button" className="secondary small" onClick={() => setCreating(true)}>+ Заявка на отгрузку</button> : undefined}
      rowClassName={(row) => row.status === "Отменена" ? "cancelled" : ""} exportName="Отправки"
      emptyText="Отправок нет. Они появляются на вкладке «К отгрузке» — кнопками «Отгрузить» и «Упаковать»."
      renderDetail={(row) => <ShipmentDetail key={`${row.id}:${row.version}`} shipment={row} canManage={canManage} canWarehouse={canWarehouse} canEdit={canEdit} busy={busy} mutate={mutate} />} />
  </>;
}

/** Заявка заранее: заказать можно больше, чем на складе, и из разных проектов — набирается по мере сдачи цеха. */
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
      <thead><tr><th>Проект</th><th>КБ</th><th>Наименование</th><th className="num">В заказе</th><th className="num">Готово</th><th className="num">Заказать</th></tr></thead>
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

function ShipmentDetail({ shipment, canManage, canWarehouse, canEdit, busy, mutate }: { shipment: Shipment; canManage: boolean; canWarehouse: boolean; canEdit: boolean; busy: string; mutate: Mutate }) {
  const [documents, setDocuments] = useState({ upd_type: shipment.inherited.includes("upd_type") ? "" : shipment.upd_type ?? "", upd_number: shipment.upd_number, upd_date: shipment.upd_date ?? "", track_number: shipment.track_number });
  const [requested, setRequested] = useState<Record<string, string>>({});
  const [layout, setLayout] = useState(false);
  const [printError, setPrintError] = useState("");
  const action = (name: "pick" | "pack" | "ship" | "close" | "cancel", message: string | ((result: unknown) => string)) => mutate(`${name}:${shipment.id}`, () => shippingApi.action(shipment.id, name, shipment.version), message);
  const got = shipment.lines.some((line) => num(line.picked) > 0);
  const open = !["Закрыта", "Отменена"].includes(shipment.status);
  const late = overdue(shipment);
  const print = async (document: "pl" | "dn") => {
    setPrintError("");
    try { openPrintSheets((await shippingApi.print(shipment.id, document)).sheets); }
    catch (reason) { setPrintError(failureText(reason, "Не удалось подготовить печать")); }
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
      <thead><tr><th>Позиция</th><th>Наименование</th><th className="num">Заказано</th><th className="num">Набрано</th><th className="num">Недобор</th><th>ГРН</th>{canWarehouse && <th aria-label="Возврат" />}</tr></thead>
      <tbody>{shipment.lines.map((line) => <tr key={line.id}>
        <td><strong>{line.project} · {line.item_index}</strong></td><td className="ship-name">{line.name}</td>
        <td className="num">{canEdit && open ? <input className="ship-qty" aria-label={`Заказано ${line.item_index}`} type="number" min="0.01" step="any" value={requested[line.id] ?? line.requested} onChange={(event) => setRequested({ ...requested, [line.id]: event.target.value })} onBlur={() => saveRequested(line.id, line.requested)} /> : `${line.requested} ${line.unit}`}</td>
        <td className="num">{line.picked}</td><td className={num(line.shortage) > 0 ? "num tone-warn" : "num"}>{line.shortage}</td><td>{line.grn || ""}</td>
        {canWarehouse && <td>{num(line.picked) > 0 && ["Новая", "Собрана"].includes(shipment.status) && <button type="button" className="link-button danger-link" disabled={disabled} onClick={() => void mutate(`unpick:${line.id}`, () => shippingApi.unpick(shipment.id, shipment.version, line.id), "Позиция возвращена на склад ГП")}>Вернуть</button>}</td>}
      </tr>)}</tbody>
    </table></div>
    {printError && <div className="error-banner" role="alert">{printError}</div>}
    <div className="ship-actions">
      {canWarehouse && got && ACTIVE.includes(shipment.status) && <button type="button" className="primary" disabled={disabled} onClick={ship}>Отгрузить</button>}
      {canWarehouse && shipment.status === "Отгружена" && <button type="button" className="primary" disabled={disabled} onClick={() => void action("close", `Отгрузка закрыта: ${shipment.number}`)}>Закрыть</button>}
      {canWarehouse && shipment.status === "Новая" && <button type="button" className="secondary" disabled={disabled} onClick={() => void action("pick", "Заявка набрана из доступного остатка")}>Набрать</button>}
      {shipment.pl_number && got && <><button type="button" className="secondary" onClick={() => void print("pl")}>Печать PL</button><button type="button" className="secondary" onClick={() => void print("dn")}>Печать DN</button></>}
      {canEdit && got && (ACTIVE.includes(shipment.status) || (shipment.pl_number && shipment.status === "Отгружена")) && !layout && <button type="button" className="secondary" disabled={disabled} onClick={() => setLayout(true)}>{shipment.pl_number ? "Перевыпустить" : "Сформировать отгрузочные"}</button>}
      {canEdit && open && <label className="secondary file-button">Загрузить УПД<input type="file" accept=".pdf,image/*" aria-label={`Загрузить УПД ${shipment.number}`} hidden onChange={(event) => { uploadUpd(event.target.files?.[0]); event.target.value = ""; }} /></label>}
      {canManage && ACTIVE.includes(shipment.status) && <span className="ship-actions-end"><button type="button" className="link-button danger-link" disabled={disabled} onClick={() => void action("cancel", "Заявка отменена, отбор возвращён на склад ГП")}>Отменить</button></span>}
    </div>
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

/* ---------- Готовая продукция: склад ГП и приёмка из цеха ---------- */

function GoodsTab({ goods, readyBoxes, canManage, canWarehouse, busy, mutate }: Context) {
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState({ project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" });
  const [acceptId, setAcceptId] = useState("");
  const columns: DataColumn<FinishedGood>[] = [
    { key: "project", title: "Проект", value: (good) => good.project, render: (good) => <strong>{good.project}</strong>, filter: true },
    { key: "item_index", title: "КБ", value: (good) => good.item_index },
    { key: "name", title: "Наименование", value: (good) => good.name, wrap: true },
    { key: "customer", title: "Заказчик", value: (good) => good.customer, filter: true },
    { key: "article", title: "Артикул КД", value: (good) => good.article, hidden: true },
    { key: "ordered", title: "В заказе", value: (good) => good.ordered, numeric: true },
    { key: "accepted", title: "Сдано цехом", value: (good) => good.accepted, numeric: true },
    { key: "remaining", title: "На складе", value: (good) => good.remaining, numeric: true, render: (good) => <strong>{good.remaining}</strong> },
    { key: "packed", title: "В отправках", value: (good) => round2(num(good.shipped) - num(good.sent)), numeric: true, hint: "Набрано в отправки, ещё не уехало" },
    { key: "sent", title: "Отгружено", value: (good) => good.sent, numeric: true },
    { key: "before_erp", title: "Отгружено до ERP", value: (good) => good.before_erp, numeric: true, hidden: true },
    { key: "grn", title: "ГРН", value: (good) => good.grn ?? "", render: (good) => good.grn || <span className="muted">цех не сдавал</span> },
    { key: "received_at", title: "Приёмка", value: (good) => good.received_at ?? "" },
  ];
  return <>
    {canWarehouse && <AcceptBar goods={goods} chosen={acceptId} onChoose={setAcceptId} busy={busy} mutate={mutate} />}
    {canWarehouse && readyBoxes.length > 0 && <FinishedGoodsHandoff boxes={readyBoxes} goods={goods} busy={busy} mutate={mutate} />}
    {show && <form className="panel ship-request" onSubmit={(event) => { event.preventDefault(); void mutate("good:new", () => shippingApi.registerGood(draft), "Позиция ГП добавлена").then((saved) => { if (saved) { setShow(false); setDraft({ project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" }); } }); }}>
      <h2>КБ проекта в реестр ГП</h2>
      <div className="ship-form">
        {(["project", "item_index", "customer", "article", "name", "ordered", "unit"] as const).map((key) => <label key={key} className={key === "name" ? "ship-span2" : undefined}>{({ project: "Проект", item_index: "КБ", customer: "Заказчик", article: "Артикул КД", name: "Наименование", ordered: "В заказе", unit: "Ед. изм." })[key]}<input required={["project", "item_index", "name", "ordered", "unit"].includes(key)} type={key === "ordered" ? "number" : "text"} min={key === "ordered" ? "0.000001" : undefined} step={key === "ordered" ? "any" : undefined} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      </div>
      <div className="ship-actions"><span className="ship-actions-end"><button type="button" className="secondary" onClick={() => setShow(false)}>Отмена</button><button className="primary" disabled={Boolean(busy)}>Сохранить</button></span></div>
    </form>}
    <DataTable label="Склад готовой продукции" storageKey="shipping-goods-v2" rows={goods} columns={columns} rowKey={(good) => good.id} exportName="Склад ГП"
      toolbar={canManage && !show ? <button type="button" className="secondary small" onClick={() => setShow(true)}>+ Добавить КБ проекта</button> : undefined}
      onRowClick={canWarehouse ? (good) => setAcceptId(good.id) : undefined} rowTitle={canWarehouse ? () => "Выбрать для приёмки" : undefined}
      emptyText="Строк готовой продукции пока нет — КБ встают сюда при заведении проекта." />
  </>;
}

/** Приёмка из цеха вручную — одна строка над таблицей вместо поля и кнопки в каждой строке. */
function AcceptBar({ goods, chosen, onChoose, busy, mutate }: { goods: FinishedGood[]; chosen: string; onChoose: (id: string) => void; busy: string; mutate: Mutate }) {
  const [amount, setAmount] = useState("");
  const [day, setDay] = useState(today);
  const owed = (good: FinishedGood) => Math.max(round2(num(good.ordered) - num(good.accepted)), 0);
  const choices = goods.filter((good) => owed(good) > 0 || good.id === chosen);
  const good = goods.find((item) => item.id === chosen);
  return <div className="panel ship-accept">
    <strong>Приёмка из цеха</strong>
    <label>Позиция<select aria-label="Позиция для приёмки" value={chosen} onChange={(event) => { onChoose(event.target.value); setAmount(""); }}>
      <option value="">выберите КБ</option>{choices.map((item) => <option key={item.id} value={item.id}>{item.project} · {item.item_index} · {item.name} — сдано {item.accepted} из {item.ordered}</option>)}</select></label>
    <label>Количество<input aria-label="Принять, шт" type="number" min="0.000001" step="any" placeholder={good ? String(owed(good)) : ""} value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
    <label>Дата поступления<input aria-label="Дата поступления" type="date" value={day} onChange={(event) => setDay(event.target.value)} /></label>
    <button type="button" className="primary" disabled={!good || !amount || Boolean(busy)} onClick={() => good && void mutate(`accept:${good.id}`, () => shippingApi.accept(good.id, good.version, amount, day || undefined), `Принято на склад ГП: ${good.project} ${good.item_index}, ${amount} ${good.unit}`).then((saved) => { if (saved) { setAmount(""); setDay(today()); } })}>Принять</button>
  </div>;
}

function boxCandidates(box: FinishedGoodsReadyBox, goods: FinishedGood[]) {
  const item = box.task_item.toUpperCase();
  return goods.filter((good) => good.project === box.project && (item === good.item_index.toUpperCase() || item.endsWith(`.${good.item_index.toUpperCase()}`) || item.endsWith(`-${good.item_index.toUpperCase()}`)));
}

/** Коробки, принятые ОТК, — на склад ГП с родословной: отметить и принять разом. */
function FinishedGoodsHandoff({ boxes, goods, busy, mutate }: { boxes: FinishedGoodsReadyBox[]; goods: FinishedGood[]; busy: string; mutate: Mutate }) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const target = (box: FinishedGoodsReadyBox) => selected[box.id] ?? boxCandidates(box, goods)[0]?.id ?? "";
  const columns: DataColumn<FinishedGoodsReadyBox>[] = [
    { key: "number", title: "Коробка", value: (box) => box.number ?? "", render: (box) => <strong>{box.number}</strong> },
    { key: "item", title: "Проект · КБ", value: (box) => `${box.project} · ${box.task_item}` },
    { key: "quantity", title: "Кол-во", value: (box) => box.quantity, numeric: true },
    { key: "confirmed", title: "Подтверждена", value: (box) => box.confirmed_at ?? "" },
    { key: "good", title: "Позиция ГП", value: (box) => target(box), render: (box) => {
      const candidates = boxCandidates(box, goods);
      if (!candidates.length) return <span className="tone-danger">нет КБ проекта в реестре ГП</span>;
      return <select aria-label={`Позиция ГП для коробки ${box.number}`} value={target(box)} onClick={(event) => event.stopPropagation()} onChange={(event) => setSelected((current) => ({ ...current, [box.id]: event.target.value }))}>
        {candidates.map((good) => <option key={good.id} value={good.id}>{good.project} · {good.item_index} · {good.name}</option>)}</select>;
    } },
  ];
  const handoff = (rows: FinishedGoodsReadyBox[]) => mutate("handoff", async () => {
    for (const box of rows) { const goodId = target(box); if (goodId) await productionApi.handoff(box.id, box.version, goodId); }
  }, `Принято на склад ГП коробок: ${rows.filter(target).length}`);
  return <div className="ship-handoff">
    <h2>Коробки из цеха <span className="page-count">{boxes.length}</span></h2>
    <DataTable label="Коробки из цеха" rows={boxes} columns={columns} rowKey={(box) => box.id} compactBelow={8}
      bulkActions={[{ label: "Принять на склад ГП", run: handoff, disabled: (rows) => Boolean(busy) || !rows.some(target) }]}
      rowLabel={(box) => `коробка ${box.number}`} />
  </div>;
}
