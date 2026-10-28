import { useState } from "react";
import { failureText, shippingApi, type Shipment, type UpdType } from "./api";
import { PageWindow } from "./PageWindow";
import { readFile } from "./DispatchCard";
import { CARRIERS, PICKUP_CARRIER, isPickup } from "./carriers";
import { StatusChip } from "./StatusChip";

/* Данные отправки (владелец 28.10.2026): упаковывает и отгружает кладовщик, данные для отгрузки — тип доставки,
   перевозчик, адрес, получатель, телефон, УПД — вносит менеджер (Ульяна) в своём разделе «Данные отправок».
   Кладовщик может внести их сам теми же полями. Чего не хватает — решает сервер (`missing_data`, `missing_ship`):
   экран только показывает его слова. */

export const UPD_TYPES: Array<[UpdType, string]> = [["ЭДО", "ЭДО"], ["БУМАГА", "Бумага"], ["БЕЗ УПД", "Без УПД"]];
export const updLabel = (value: string | null | undefined) => UPD_TYPES.find(([key]) => key === value)?.[1] ?? "";

/** «Ждёт данных: адрес доставки, № УПД» или «Готова к отгрузке»; `ship` — со списком для «Отгрузить» (с треком ТК). */
export function DataChip({ shipment, ship = false }: { shipment: Shipment; ship?: boolean }) {
  const missing = ship ? shipment.missing_ship : shipment.missing_data;
  if (!missing.length) return <StatusChip tone="success" title="Все данные внесены">Готова к отгрузке</StatusChip>;
  return <span className="ship-gaps"><StatusChip tone="warning" title={`Не хватает: ${missing.join(", ")}`}>Ждёт данных</StatusChip>
    <span className="ship-gaps-list" title={missing.join(", ")}>{missing.join(", ")}</span></span>;
}

const CARRIER_CHOICES = CARRIERS.filter((name) => name !== PICKUP_CARRIER);

/** Окно с данными одной отправки — только поля, которые вносит менеджер. Сохранение — через правку шапки и документов
 *  отправки (проверка версии на сервере): сначала шапка, потом документы, потом файл УПД. */
export function ShipmentDataWindow({ shipment, close, onSaved }: { shipment: Shipment; close: () => void; onSaved: () => Promise<void> | void }) {
  const [delivery, setDelivery] = useState<string>(shipment.terms ?? (isPickup(shipment.carrier) ? "САМОВЫВОЗ" : ""));
  const [carrier, setCarrier] = useState(isPickup(shipment.carrier) ? "" : shipment.carrier);
  const [point, setPoint] = useState(shipment.shipping_point);
  const [receiver, setReceiver] = useState(shipment.receiver);
  const [phone, setPhone] = useState(shipment.phone);
  const [updType, setUpdType] = useState<string>(shipment.upd_type ?? "");
  const [updNumber, setUpdNumber] = useState(shipment.upd_number);
  const [updDate, setUpdDate] = useState(shipment.upd_date ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const pickup = delivery === "САМОВЫВОЗ";
  const carrierTrip = delivery === "ДОСТАВКА ТК";
  const noUpd = updType === "БЕЗ УПД";
  const paper = updType === "БУМАГА";
  // Автозаполнение полей по скану или ЭДО-файлу УПД (позже): прочитанные номер, дата и основание встанут сюда же,
  // через setUpdNumber / setUpdDate; сейчас файл только прикладывается к отправке.

  const save = async () => {
    setSaving(true); setError("");
    try {
      let current = shipment;
      const head: Record<string, unknown> = {};
      const wantedCarrier = pickup ? PICKUP_CARRIER : carrier.trim();
      if (receiver.trim() !== shipment.receiver.trim()) head.receiver = receiver.trim();
      if (phone.trim() !== shipment.phone.trim()) head.phone = phone.trim();
      if (delivery && delivery !== shipment.terms) head.terms = delivery;
      if (delivery && wantedCarrier !== shipment.carrier.trim()) head.carrier = wantedCarrier;
      // Самовывозу адрес не нужен: то, что было записано, остаётся как есть.
      if (!pickup && point.trim() !== shipment.shipping_point.trim()) head.shipping_point = point.trim();
      if (Object.keys(head).length) current = (await shippingApi.head(shipment.id, { version: current.version, ...head })).shipment;
      const number = noUpd ? "" : updNumber.trim();
      const day = noUpd ? "" : updDate;
      if (updType !== (current.upd_type ?? "") || number !== current.upd_number || day !== (current.upd_date ?? "")) {
        current = (await shippingApi.documents(shipment.id, {
          version: current.version, upd_type: updType || null, upd_number: number, upd_date: day || null,
          upd_url: current.upd_url, track_number: current.track_number,
        })).shipment;
      }
      if (file) current = (await shippingApi.uploadUpd(shipment.id, current.version, await readFile(file))).shipment;
      await onSaved();
      close();
    } catch (reason) {
      setError(failureText(reason, "Не удалось сохранить данные отправки"));
      // Часть полей могла сохраниться: список и версия обновятся, окно остаётся открытым.
      try { await onSaved(); } catch { /* ошибку сохранения уже видно */ }
    } finally { setSaving(false); }
  };

  const gaps = shipment.missing_data;
  return <PageWindow label={`Данные отправки ${shipment.number}`} width={760} close={close}>
    <form className="panel ship-data" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <h2>Данные отправки {shipment.number}</h2>
      <p className="ship-data-sub">{shipment.customer || shipment.receiver}{shipment.lines.length ? ` · ${[...new Set(shipment.lines.map((line) => line.project))].join(", ")}` : ""}
        {gaps.length > 0 && <span className="tone-warn"> · не хватает: {gaps.join(", ")}</span>}</p>
      <div className="ship-form">
        <label>Тип доставки<select aria-label="Тип доставки" value={delivery} onChange={(event) => setDelivery(event.target.value)}>
          <option value="">не выбран</option><option value="САМОВЫВОЗ">Самовывоз</option><option value="ДОСТАВКА ТК">Доставка ТК</option></select></label>
        {carrierTrip && <label>Перевозчик<select aria-label="Перевозчик" value={carrier} onChange={(event) => setCarrier(event.target.value)}>
          <option value="">не выбран</option>{CARRIER_CHOICES.map((name) => <option key={name} value={name}>{name}</option>)}
          {carrier && !(CARRIER_CHOICES as readonly string[]).includes(carrier) && <option value={carrier}>{carrier} (прежнее)</option>}</select></label>}
        {carrierTrip && <label className="ship-span2">Адрес доставки<input aria-label="Адрес доставки" value={point} onChange={(event) => setPoint(event.target.value)} /></label>}
        <label className={pickup || !delivery ? "ship-span2" : undefined}>Получатель<input aria-label="Получатель" value={receiver} onChange={(event) => setReceiver(event.target.value)} /></label>
        <label>Телефон получателя<input aria-label="Телефон получателя" value={phone} onChange={(event) => setPhone(event.target.value)} /></label>
        <label>УПД<select aria-label="Тип УПД" value={updType} onChange={(event) => setUpdType(event.target.value)}>
          <option value="">не выбран</option>{UPD_TYPES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        {updType && !noUpd && <label>№ УПД<input aria-label="№ УПД" value={updNumber} onChange={(event) => setUpdNumber(event.target.value)} /></label>}
        {updType && !noUpd && <label>Дата УПД<input type="date" aria-label="Дата УПД" value={updDate} onChange={(event) => setUpdDate(event.target.value)} /></label>}
        {updType && !noUpd && <label className="ship-span2">Файл УПД{paper ? "" : " (по желанию)"}
          <span className="ship-upd-file">
            {shipment.upd_url && <a href={shipment.upd_url} target="_blank" rel="noreferrer">открыть приложенный</a>}
            <input type="file" accept=".pdf,image/*,.xml" aria-label="Файл УПД" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></span></label>}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ship-actions"><span className="ship-actions-end">
        <button type="button" className="secondary" onClick={close}>Отмена</button>
        <button className="primary" disabled={saving}>{saving ? "Сохраняем…" : "Сохранить"}</button>
      </span></div>
    </form>
  </PageWindow>;
}
