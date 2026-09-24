import { useEffect, useMemo, useState } from "react";
import { productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal, type Shipment } from "./api";

type Tab = "shipments" | "goods";
type Mutate = (key: string, operation: () => Promise<unknown>, message: string) => Promise<boolean>;

export function ShippingPage({ principal }: { principal: Principal }) {
  const [tab, setTab] = useState<Tab>("shipments");
  const [goods, setGoods] = useState<FinishedGood[]>([]);
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [readyBoxes, setReadyBoxes] = useState<FinishedGoodsReadyBox[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const canManage = principal.permissions.shipping === "edit" && principal.roles.some((role) => ["pm", "admin"].includes(role));
  const canWarehouse = principal.permissions.shipping === "edit" && principal.roles.some((role) => ["receiver", "admin"].includes(role));

  async function reload() {
    setError("");
    try {
      const [goodsResult, shipmentResult, productionResult] = await Promise.all([
        shippingApi.goods(), shippingApi.shipments(),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] }),
      ]);
      setGoods(goodsResult.goods); setShipments(shipmentResult.shipments);
      setReadyBoxes(productionResult.boxes);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить готовую продукцию и отгрузки");
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []);

  async function mutate(key: string, operation: () => Promise<unknown>, message: string) {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try { await operation(); setNotice(message); await reload(); return true; }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Операция не выполнена"); return false; }
    finally { setBusy(""); }
  }

  return <section className="page shipping-page">
    <header className="page-head"><h1>Готовая продукция и отгрузка</h1><span className="page-count">{tab === "goods" ? goods.length : shipments.length}</span></header>
    <p className="page-sub">Один реестр хранит остаток ГП: принято минус отобрано в заявки.</p>
    <div className="shipping-tabs"><button className={tab === "shipments" ? "secondary active" : "secondary"} onClick={() => setTab("shipments")}>Заявки на отгрузку</button><button className={tab === "goods" ? "secondary active" : "secondary"} onClick={() => setTab("goods")}>Склад ГП</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {tab === "goods" ? <GoodsTab goods={goods} readyBoxes={readyBoxes} canManage={canManage} canWarehouse={canWarehouse} busy={busy} mutate={mutate} /> : <ShipmentsTab goods={goods} shipments={shipments} canManage={canManage} canWarehouse={canWarehouse} busy={busy} mutate={mutate} />}
    {loaded && tab === "goods" && !goods.length && <div className="empty">Строк готовой продукции пока нет.</div>}
    {loaded && tab === "shipments" && !shipments.length && <div className="empty">Заявок на отгрузку пока нет.</div>}
  </section>;
}

function GoodsTab({ goods, readyBoxes, canManage, canWarehouse, busy, mutate }: { goods: FinishedGood[]; readyBoxes: FinishedGoodsReadyBox[]; canManage: boolean; canWarehouse: boolean; busy: string; mutate: Mutate }) {
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState({ project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" });
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  return <>
    {canManage && <div className="page-actions shipping-add"><button className="primary" onClick={() => setShow((value) => !value)}>+ Добавить КБ проекта</button></div>}
    {show && <form className="panel shipping-form" onSubmit={(event) => { event.preventDefault(); void mutate("good:new", () => shippingApi.registerGood(draft), "Позиция ГП добавлена").then((saved) => { if (saved) { setShow(false); setDraft({ project: "", item_index: "", customer: "", article: "", name: "", ordered: "", unit: "шт", note: "" }); } }); }}>
      {(["project", "item_index", "customer", "article", "name", "ordered", "unit"] as const).map((key) => <label key={key}>{({ project: "Проект", item_index: "КБ", customer: "Заказчик", article: "Артикул КД", name: "Наименование", ordered: "В заказе", unit: "Ед. изм." })[key]}<input required={["project", "item_index", "name", "ordered", "unit"].includes(key)} type={key === "ordered" ? "number" : "text"} min={key === "ordered" ? "0.000001" : undefined} step={key === "ordered" ? "any" : undefined} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <button className="primary" disabled={Boolean(busy)}>Сохранить</button>
    </form>}
    {canWarehouse && readyBoxes.length > 0 && <FinishedGoodsHandoff boxes={readyBoxes} goods={goods} busy={busy} mutate={mutate} />}
    <div className="table-wrap"><table className="grid wide"><thead><tr><th>Проект / КБ</th><th>Изделие</th><th>В заказе</th><th>Принято</th><th>Отобрано</th><th>Остаток</th><th>ГРН</th>{canWarehouse && <th>Приёмка из цеха</th>}</tr></thead><tbody>{goods.map((good) => <tr key={good.id}><td><strong>{good.project}</strong><small>{good.item_index}</small></td><td>{good.name}<small>{good.customer || "—"}</small></td><td>{good.ordered} {good.unit}</td><td>{good.accepted}</td><td>{good.shipped}</td><td><strong>{good.remaining}</strong></td><td>{good.grn || "—"}</td>{canWarehouse && <td><div className="inline-qty"><input aria-label={`Принять ${good.project} ${good.item_index}`} type="number" min="0.000001" step="any" value={amounts[good.id] ?? ""} onChange={(event) => setAmounts({ ...amounts, [good.id]: event.target.value })} /><button className="secondary small" disabled={!amounts[good.id] || Boolean(busy)} onClick={() => void mutate(`accept:${good.id}`, () => shippingApi.accept(good.id, good.version, amounts[good.id]), "Готовая продукция принята").then((saved) => { if (saved) setAmounts({ ...amounts, [good.id]: "" }); })}>Принять</button></div></td>}</tr>)}</tbody></table></div>
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

function ShipmentsTab({ goods, shipments, canManage, canWarehouse, busy, mutate }: { goods: FinishedGood[]; shipments: Shipment[]; canManage: boolean; canWarehouse: boolean; busy: string; mutate: Mutate }) {
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState({ customer: "", receiver: "", shipping_point: "", contact: "", phone: "", terms: "САМОВЫВОЗ", carrier: "", upd_type: "ЭДО", upd_number: "", upd_url: "", note: "" });
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const selected = useMemo(() => goods.filter((good) => Number(quantities[good.id] || 0) > 0), [goods, quantities]);
  return <>
    {canManage && <div className="page-actions shipping-add"><button className="primary" onClick={() => setShow((value) => !value)}>+ Заявка на отгрузку</button></div>}
    {show && <form className="panel shipping-form shipping-request" onSubmit={(event) => { event.preventDefault(); void mutate("shipment:new", () => shippingApi.create({ ...draft, lines: selected.map((good) => ({ good_id: good.id, quantity: quantities[good.id] })) }), "Заявка на отгрузку создана").then((saved) => { if (saved) { setShow(false); setQuantities({}); } }); }}>
      {(["customer", "receiver", "shipping_point", "contact", "phone", "carrier", "upd_number", "upd_url"] as const).map((key) => <label key={key}>{({ customer: "Заказчик", receiver: "Получатель", shipping_point: "Точка отгрузки", contact: "Контакт", phone: "Телефон", carrier: "ТК / способ", upd_number: "№ УПД", upd_url: "Ссылка на файл УПД" })[key]}<input required={["receiver", "shipping_point"].includes(key)} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <label>Условия<select value={draft.terms} onChange={(event) => setDraft({ ...draft, terms: event.target.value })}><option>САМОВЫВОЗ</option><option>ДОСТАВКА ТК</option></select></label><label>Тип УПД<select value={draft.upd_type} onChange={(event) => setDraft({ ...draft, upd_type: event.target.value })}><option>ЭДО</option><option>БУМАГА</option></select></label>
      <div className="shipping-lines"><strong>Что отгружаем</strong>{goods.map((good) => <label key={good.id}>{good.project} · {good.item_index} · остаток {good.remaining} {good.unit}<input aria-label={`Заказать ${good.project} ${good.item_index}`} type="number" min="0" step="any" value={quantities[good.id] ?? ""} onChange={(event) => setQuantities({ ...quantities, [good.id]: event.target.value })} /></label>)}</div>
      <button className="primary" disabled={!selected.length || Boolean(busy)}>Создать заявку</button>
    </form>}
    <div className="work-list shipping-list">{shipments.map((shipment) => <ShipmentCard key={`${shipment.id}:${shipment.version}`} shipment={shipment} canManage={canManage} canWarehouse={canWarehouse} busy={busy} mutate={mutate} />)}</div>
  </>;
}

function ShipmentCard({ shipment, canManage, canWarehouse, busy, mutate }: { shipment: Shipment; canManage: boolean; canWarehouse: boolean; busy: string; mutate: Mutate }) {
  const [documents, setDocuments] = useState({ upd_type: shipment.upd_type, upd_number: shipment.upd_number, upd_url: shipment.upd_url, track_number: shipment.track_number });
  const action = (name: "pick" | "pack" | "ship" | "close" | "cancel", message: string) => mutate(`${name}:${shipment.id}`, () => shippingApi.action(shipment.id, name, shipment.version), message);
  return <article className={`panel shipping-card ${shipment.status === "Отменена" ? "cancelled" : ""}`}>
    <div className="procurement-title"><div><strong>{shipment.number}</strong><small>{shipment.receiver} · {shipment.shipping_point}</small></div><span className="status">{shipment.status}</span></div>
    <div className="shipping-meta"><span>Менеджер: {shipment.manager}</span><span>{shipment.terms}{shipment.carrier ? ` · ${shipment.carrier}` : ""}</span><span>{shipment.pl_number || "PL —"} · {shipment.dn_number || "DN —"}</span></div>
    <div className="table-scroll"><table><thead><tr><th>Позиция</th><th>Заказано</th><th>Набрано</th><th>Недобор</th><th>ГРН</th>{canWarehouse && <th />}</tr></thead><tbody>{shipment.lines.map((line) => <tr key={line.id}><td>{line.project} · {line.item_index}<small>{line.name}</small></td><td>{line.requested} {line.unit}</td><td>{line.picked}</td><td>{line.shortage}</td><td>{line.grn || "—"}</td>{canWarehouse && <td>{Number(line.picked) > 0 && ["Новая", "Собрана"].includes(shipment.status) && <button className="link-button danger-link" disabled={Boolean(busy)} onClick={() => void mutate(`unpick:${line.id}`, () => shippingApi.unpick(shipment.id, shipment.version, line.id), "Позиция возвращена на склад ГП")}>Вернуть</button>}</td>}</tr>)}</tbody></table></div>
    {canManage && !["Закрыта", "Отменена"].includes(shipment.status) && <div className="shipment-docs"><label>Тип УПД<select value={documents.upd_type} onChange={(event) => setDocuments({ ...documents, upd_type: event.target.value as "ЭДО" | "БУМАГА" })}><option>ЭДО</option><option>БУМАГА</option></select></label><label>№ УПД<input value={documents.upd_number} onChange={(event) => setDocuments({ ...documents, upd_number: event.target.value })} /></label><label>Ссылка на файл УПД<input value={documents.upd_url} onChange={(event) => setDocuments({ ...documents, upd_url: event.target.value })} /></label><label>Трек-номер<input value={documents.track_number} onChange={(event) => setDocuments({ ...documents, track_number: event.target.value })} /></label><button className="secondary small" disabled={Boolean(busy)} onClick={() => void mutate(`docs:${shipment.id}`, () => shippingApi.documents(shipment.id, { version: shipment.version, ...documents }), "Данные УПД и доставки сохранены")}>Сохранить документы</button></div>}
    <div className="fulfillment-actions">{canWarehouse && shipment.status === "Новая" && <button className="secondary" disabled={Boolean(busy)} onClick={() => void action("pick", "Заявка набрана из доступного остатка")}>Набрать</button>}{canWarehouse && shipment.status === "Собрана" && <button className="primary" disabled={Boolean(busy)} onClick={() => void action("pack", "Отгрузка упакована")}>Упаковать</button>}{canWarehouse && shipment.status === "Упакована" && <button className="primary" disabled={Boolean(busy)} onClick={() => void action("ship", "Отгрузка отправлена")}>Отгружено</button>}{canWarehouse && shipment.status === "Отгружена" && <button className="primary" disabled={Boolean(busy)} onClick={() => void action("close", "Отгрузка закрыта")}>Закрыть</button>}{canManage && ["Новая", "Собрана", "Упакована"].includes(shipment.status) && <button className="link-button danger-link" disabled={Boolean(busy)} onClick={() => void action("cancel", "Заявка отменена, отбор возвращён на склад ГП")}>Отменить</button>}</div>
  </article>;
}
