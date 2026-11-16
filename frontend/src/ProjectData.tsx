import { useState } from "react";
import { ApiError, DELIVERY_FIELDS, failureText, shippingApi, type DeliveryField, type DeliveryValues, type ItemAddressee, type ProjectAddressee } from "./api";
import { PageWindow } from "./PageWindow";
import { CARRIERS, PICKUP_CARRIER, isPickup } from "./carriers";
import { UPD_TYPES } from "./ShipmentData";

/* Данные отправки проекта — карточка, которую наследуют новые отправки (владелец 14.10 и 28.10). Те же поля и тот же вид,
   что в окне данных отправки; реквизиты проекта, плановая дата и комментарий — в свёрнутом блоке, не на панели упаковки.
   Открывается из панели упаковки по «Изменить…». */

const CARRIER_CHOICES = CARRIERS.filter((name) => name !== PICKUP_CARRIER);
const REQUISITES: Array<[keyof ProjectAddressee, string, string]> = [
  ["po", "Заказ № (PO)", "пусто — № проекта"], ["contract", "№ договора", "если есть"], ["spec", "№ спецификации", "если есть"],
  ["invoice", "№ счёта (из УПД)", "подставится из УПД"], ["hours", "Время работы", "пн–пт 9:00–18:00"],
];

export function ProjectDataWindow({ project, customer, stored, canManage, close, onSaved }: {
  project: string; customer: string; stored: ProjectAddressee | undefined; canManage: boolean; close: () => void; onSaved: () => Promise<void> | void;
}) {
  const [delivery, setDelivery] = useState<string>(stored?.terms ?? (isPickup(stored?.carrier ?? "") ? "САМОВЫВОЗ" : ""));
  const [carrier, setCarrier] = useState(isPickup(stored?.carrier ?? "") ? "" : stored?.carrier ?? "");
  const [point, setPoint] = useState(stored?.shipping_point ?? "");
  const [receiver, setReceiver] = useState(stored?.receiver ?? "");
  const [phone, setPhone] = useState(stored?.phone ?? "");
  const [contact, setContact] = useState(stored?.contact ?? "");
  const [updType, setUpdType] = useState<string>(stored?.upd_type ?? "");
  const [requisites, setRequisites] = useState<Record<string, string>>(() => Object.fromEntries(REQUISITES.map(([key]) => [key, String(stored?.[key] ?? "")])));
  const [plan, setPlan] = useState({ plan_date: stored?.plan_date ?? "", note: stored?.note ?? "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const pickup = delivery === "САМОВЫВОЗ";
  const carrierTrip = delivery === "ДОСТАВКА ТК";

  const save = async () => {
    setSaving(true); setError("");
    try {
      const changed: Record<string, unknown> = {};
      const was = (key: keyof ProjectAddressee) => String(stored?.[key] ?? "").trim();
      const text: Array<[keyof ProjectAddressee, string]> = [["receiver", receiver], ["phone", phone], ["contact", contact]];
      for (const [key, value] of text) if (value.trim() !== was(key)) changed[key] = value.trim();
      if (!pickup && point.trim() !== was("shipping_point")) changed.shipping_point = point.trim();
      if (delivery !== (stored?.terms ?? "")) changed.terms = delivery || null;
      const wantedCarrier = pickup ? PICKUP_CARRIER : carrier.trim();
      if (delivery && (delivery !== (stored?.terms ?? "") || (carrierTrip && wantedCarrier !== was("carrier")))) changed.carrier = wantedCarrier;
      if (updType !== (stored?.upd_type ?? "")) changed.upd_type = updType || null;
      if (canManage) for (const [key] of REQUISITES) if (requisites[key].trim() !== was(key)) changed[key] = requisites[key].trim();
      if (plan.plan_date !== (stored?.plan_date ?? "")) changed.plan_date = plan.plan_date || null;
      if (plan.note.trim() !== (stored?.note ?? "").trim()) changed.note = plan.note.trim();
      if (Object.keys(changed).length) await shippingApi.saveAddressee({ project, version: stored?.version ?? 0, ...changed });
      await onSaved();
      close();
    } catch (reason) {
      setError(failureText(reason, "Не удалось сохранить данные отправки"));
    } finally { setSaving(false); }
  };

  return <PageWindow label={`Данные отправки проекта ${project}`} width={760} close={close}>
    <form className="panel ship-data" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <h2>Данные отправки · {project}</h2>
      <p className="ship-data-sub">{customer || "Заказчик не указан"} · доставка по умолчанию для КБ проекта</p>
      <div className="ship-form">
        <label>Тип доставки<select aria-label="Тип доставки" value={delivery} onChange={(event) => setDelivery(event.target.value)}>
          <option value="">не выбран</option><option value="САМОВЫВОЗ">Самовывоз</option><option value="ДОСТАВКА ТК">Доставка ТК</option></select></label>
        {carrierTrip && <label>Перевозчик<select aria-label="Перевозчик" value={carrier} onChange={(event) => setCarrier(event.target.value)}>
          <option value="">не выбран</option>{CARRIER_CHOICES.map((name) => <option key={name} value={name}>{name}</option>)}
          {carrier && !(CARRIER_CHOICES as readonly string[]).includes(carrier) && <option value={carrier}>{carrier} (прежнее)</option>}</select></label>}
        {carrierTrip && <label className="ship-span2">Адрес доставки<input aria-label="Адрес доставки" value={point} onChange={(event) => setPoint(event.target.value)} /></label>}
        <label className={pickup || !delivery ? "ship-span2" : undefined}>Получатель<input aria-label="Получатель" value={receiver} onChange={(event) => setReceiver(event.target.value)} /></label>
        <label>Телефон получателя<input aria-label="Телефон получателя" value={phone} onChange={(event) => setPhone(event.target.value)} /></label>
        <label>Контактное лицо<input aria-label="Контактное лицо" value={contact} onChange={(event) => setContact(event.target.value)} /></label>
        <label>УПД<select aria-label="Тип УПД" value={updType} onChange={(event) => setUpdType(event.target.value)}>
          <option value="">не выбран</option>{UPD_TYPES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
      </div>
      <details className="ship-more"><summary>Реквизиты проекта</summary>
        <div className="ship-form">
          {canManage && REQUISITES.map(([key, label, placeholder]) => <label key={key}>{label}
            <input aria-label={label} placeholder={placeholder} value={requisites[key]} onChange={(event) => setRequisites({ ...requisites, [key]: event.target.value })} /></label>)}
          <label>Плановая дата проекта<input type="date" className="ship-narrow" aria-label="Плановая дата проекта" value={plan.plan_date} onChange={(event) => setPlan({ ...plan, plan_date: event.target.value })} /></label>
          <label className="ship-span2">Комментарий проекта<input aria-label="Комментарий проекта" placeholder="попадёт в новые отправки" value={plan.note} onChange={(event) => setPlan({ ...plan, note: event.target.value })} /></label>
        </div>
      </details>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ship-actions"><span className="ship-actions-end">
        <button type="button" className="secondary" onClick={close}>Отмена</button>
        <button className="primary" disabled={saving}>{saving ? "Сохраняем…" : "Сохранить"}</button>
      </span></div>
    </form>
  </PageWindow>;
}

const DELIVERY_LABELS: Record<DeliveryField, string> = {
  receiver: "Получатель", shipping_point: "Адрес доставки", contact: "Контактное лицо", phone: "Телефон",
  terms: "Способ доставки", carrier: "Перевозчик", upd_type: "Тип УПД",
};

/** One project per atomic batch; receipt rows are deduplicated by catalog KB identity. */
export function ItemDeliveryWindow({ project, items: initial, close, onSaved }: {
  project: string; items: ItemAddressee[]; close: () => void; onSaved: () => Promise<void> | void;
}) {
  const [items, setItems] = useState(() => [...new Map(initial.map((item) => [item.item_index, item])).values()]);
  const [draft, setDraft] = useState<Record<DeliveryField, string>>(() => Object.fromEntries(DELIVERY_FIELDS.map((key) => [
    key, initial.every((item) => item.effective[key] === initial[0]?.effective[key]) ? initial[0]?.effective[key] ?? "" : "",
  ])) as Record<DeliveryField, string>);
  const [modes, setModes] = useState<Partial<Record<DeliveryField, "value" | "inherit">>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [refreshed, setRefreshed] = useState(false);
  const single = items.length === 1;
  const save = async (reset = false) => {
    setSaving(true); setError("");
    const fields = Object.fromEntries(DELIVERY_FIELDS.filter((key) => modes[key] === "value").map((key) => [
      key, key === "terms" || key === "upd_type" ? draft[key] || null : draft[key].trim(),
    ])) as Partial<DeliveryValues>;
    const inherit = reset ? [...DELIVERY_FIELDS] : DELIVERY_FIELDS.filter((key) => modes[key] === "inherit");
    const changes = items.map((item) => ({ item_index: item.item_index, version: item.version, fields: reset ? {} : fields, inherit }));
    try {
      if (single) await shippingApi.saveItemAddressee(project, changes[0]);
      else await shippingApi.saveItemAddressees(project, changes);
      await onSaved(); close();
    } catch (reason) {
      setError(failureText(reason, "Доставка КБ не сохранена"));
      setConflict(reason instanceof ApiError && reason.status === 409);
    } finally { setSaving(false); }
  };
  const refresh = async () => {
    setSaving(true);
    try {
      const result = await shippingApi.itemAddressees(project);
      const wanted = new Set(items.map((item) => item.item_index));
      const current = result.items.filter((item) => wanted.has(item.item_index));
      if (current.length !== items.length) throw new Error("Состав КБ изменился. Откройте доставку заново.");
      setItems(current); setConflict(false); setRefreshed(true); setError("Данные обновлены. Проверьте значения и сохраните ещё раз; ваш ввод сохранён.");
    } catch (reason) { setError(failureText(reason, "Не удалось обновить данные")); }
    finally { setSaving(false); }
  };
  return <PageWindow label={`Доставка КБ · ${project}`} width={800} close={close}>
    <form className="panel ship-data item-delivery-editor" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <h2>Доставка · {project} · {single ? items[0].item_index : `${items.length} КБ`}</h2>
      <p className="ship-data-sub">{items.map((item) => item.item_index).join(", ")}</p>
      <div className="ship-form">
        {DELIVERY_FIELDS.map((key) => {
          const different = items.some((item) => item.effective[key] !== items[0].effective[key]);
          const mode = modes[key];
          const set = (value: string) => { setDraft({ ...draft, [key]: value }); setModes({ ...modes, [key]: "value" }); };
          const choices = key === "terms" ? [["САМОВЫВОЗ", "Самовывоз"], ["ДОСТАВКА ТК", "Доставка ТК"]] : key === "upd_type" ? UPD_TYPES : null;
          return <div key={key} className={key === "shipping_point" ? "ship-span2" : undefined}>
            <label>{DELIVERY_LABELS[key]}
              {choices ? <select name={key} autoComplete="off" aria-label={DELIVERY_LABELS[key]} disabled={saving || mode === "inherit"} value={draft[key]} onChange={(event) => set(event.target.value)}>
                <option value="">{different && !mode ? "Разные значения" : "Не задано"}</option>
                {choices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select> : <input name={key} autoComplete="off" type={key === "phone" ? "tel" : "text"} aria-label={DELIVERY_LABELS[key]} disabled={saving || mode === "inherit"} value={draft[key]}
                placeholder={different && !mode ? "Разные значения" : undefined} onChange={(event) => set(event.target.value)} />}
            </label>
            <select name={`${key}_mode`} autoComplete="off" className="item-delivery-mode" aria-label={`Применить: ${DELIVERY_LABELS[key]}`} disabled={saving}
              value={mode ?? "keep"} onChange={(event) => {
                const next = { ...modes };
                if (event.target.value === "keep") delete next[key]; else next[key] = event.target.value as "value" | "inherit";
                setModes(next);
              }}>
              <option value="keep">{single ? (key in items[0].overrides ? "Задано для КБ" : "Как у проекта") : "Не менять"}</option>
              <option value="value">{single ? "Задать для КБ" : "Задать выбранным КБ"}</option>
              {single && <option value="inherit">Как у проекта — сбросить</option>}
            </select>
            {refreshed && <span className="muted">Сейчас: {items.map((item) => item.effective[key] || "—").filter((v, i, all) => all.indexOf(v) === i).join("; ")}</span>}
          </div>;
        })}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ship-actions">
        <button type="button" className="secondary" disabled={saving || conflict} onClick={() => void save(true)}>Как у проекта</button>
        {conflict && <button type="button" className="secondary" disabled={saving} onClick={() => void refresh()}>Обновить данные</button>}
        <span className="ship-actions-end"><button type="button" className="secondary" onClick={close}>Отмена</button>
          <button className="primary" disabled={saving || conflict || !Object.keys(modes).length}>{saving ? "Сохраняем…" : "Сохранить"}</button></span>
      </div>
    </form>
  </PageWindow>;
}
