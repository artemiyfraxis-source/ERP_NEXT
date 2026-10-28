import { useMemo, useRef, useState } from "react";
import { failureText, shippingApi, type Shipment, type UpdType, type WarehouseReceiptScan } from "./api";
import { PageWindow } from "./PageWindow";
import { CARRIERS, PICKUP_CARRIER, isPickup } from "./carriers";
import { StatusChip } from "./StatusChip";
import { useUpdScan } from "./useUpdScan";

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

/* Полоса готовности данных (владелец 28.10): чип на каждую позицию данных — внесено зелёным, не внесено приглушённо
   «нет телефона» — и один общий чип. Одна и та же на панели упаковки, в «Отгрузке» и в «Данных отправок». */
export interface ReadinessItem { label: string; ok: boolean }
interface ReadinessSource {
  receiver: string; phone: string; shipping_point: string; carrier: string; terms: string | null; upd_type: string | null;
  upd_number?: string; upd_url?: string; track_number?: string;
}

export function readinessItems(source: ReadinessSource, withTrack = false): ReadinessItem[] {
  const pickup = source.terms === "САМОВЫВОЗ" || isPickup(source.carrier);
  const way = source.terms || source.carrier ? [source.terms === "ДОСТАВКА ТК" ? "Доставка ТК" : pickup ? "Самовывоз" : "", !pickup ? source.carrier : ""].filter(Boolean).join(" · ") : "";
  const items: ReadinessItem[] = [
    source.receiver.trim() ? { label: "Получатель", ok: true } : { label: "нет получателя", ok: false },
    way ? { label: way, ok: true } : { label: "нет условий доставки", ok: false },
  ];
  if (!pickup) items.push(source.shipping_point.trim() ? { label: "Адрес", ok: true } : { label: "нет адреса", ok: false });
  items.push(source.phone.trim() ? { label: "Телефон", ok: true } : { label: "нет телефона", ok: false });
  if (!source.upd_type) items.push({ label: "тип УПД не выбран", ok: false });
  else if (source.upd_type === "БЕЗ УПД") items.push({ label: "Без УПД", ok: true });
  else {
    const label = [updLabel(source.upd_type), source.upd_number ? `№ ${source.upd_number}` : ""].filter(Boolean).join(" ");
    items.push(source.upd_number === undefined || source.upd_number ? { label, ok: true } : { label: `${label} · нет №`, ok: false });
  }
  if (withTrack && !pickup) items.push(source.track_number?.trim() ? { label: "Трек", ok: true } : { label: "нет трека", ok: false });
  return items;
}

/** Чипы данных и общий итог. `gaps` — слова сервера для отправки (они главнее); для карточки проекта итог считается по чипам. */
export function Readiness({ source, gaps, withTrack = false }: { source: ReadinessSource; gaps?: string[]; withTrack?: boolean }) {
  const items = readinessItems(source, withTrack);
  const missing = gaps ?? items.filter((item) => !item.ok).map((item) => item.label);
  return <span className="ship-ready" aria-label="Готовность данных отправки">
    {items.map((item) => <StatusChip key={item.label} tone={item.ok ? "success" : "neutral"} className={item.ok ? "ship-ready-ok" : "ship-ready-gap"}>{item.label}</StatusChip>)}
    {missing.length === 0
      ? <StatusChip tone="success" title="Все данные внесены">Готово к отгрузке</StatusChip>
      : <StatusChip tone="warning" title={`Не хватает: ${missing.join(", ")}`}>Ждёт данных менеджера</StatusChip>}
  </span>;
}

const CARRIER_CHOICES =CARRIERS.filter((name) => name !== PICKUP_CARRIER);

/** Окно с данными одной отправки — только поля, которые вносит менеджер. Сохранение — через правку шапки и документов
 *  отправки (проверка версии на сервере): сначала шапка, потом документы, потом файл УПД. */
export function ShipmentDataWindow({ shipment: opened, close, onSaved }: { shipment: Shipment; close: () => void; onSaved: () => Promise<void> | void }) {
  // Скан или файл УПД сразу кладёт документ в отправку (версия растёт), поэтому окно ведёт свою копию отправки.
  const [shipment, setShipment] = useState<Shipment>(opened);
  const [delivery, setDelivery] = useState<string>(shipment.terms ?? (isPickup(shipment.carrier) ? "САМОВЫВОЗ" : ""));
  const [carrier, setCarrier] = useState(isPickup(shipment.carrier) ? "" : shipment.carrier);
  const [point, setPoint] = useState(shipment.shipping_point);
  const [receiver, setReceiver] = useState(shipment.receiver);
  const [phone, setPhone] = useState(shipment.phone);
  const [updType, setUpdType] = useState<string>(shipment.upd_type ?? "");
  const [updNumber, setUpdNumber] = useState(shipment.upd_number);
  const [updDate, setUpdDate] = useState(shipment.upd_date ?? "");
  const [note, setNote] = useState("");
  const [applying, setApplying] = useState(false);
  const [replacing, setReplacing] = useState<{ answer: WarehouseReceiptScan; typed: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const pickup = delivery === "САМОВЫВОЗ";
  const carrierTrip = delivery === "ДОСТАВКА ТК";
  const noUpd = updType === "БЕЗ УПД";
  const edoType = updType === "ЭДО";
  // Распознавание идёт долго, а его ответ приходит из замыкания кнопки: свежее состояние — через ref.
  const latest = useRef({ shipment, updNumber });
  latest.current = { shipment, updNumber };

  /** Ответ чтения ложится в отправку: файл, тип, № и дата документа, № счёта из «Основания». Другой уже внесённый № молча
   *  не затирается: сначала вопрос «Заменить?». */
  const applyAnswer = async (answer: WarehouseReceiptScan, number?: "replace" | "keep") => {
    const { shipment: current, updNumber: typedRaw } = latest.current;
    const found = (answer.recognised.upd_number ?? "").trim();
    const typed = typedRaw.trim();
    if (!number && found && typed && typed !== found) { setReplacing({ answer, typed }); return; }
    setReplacing(null); setApplying(true); setError(""); setNote("");
    try {
      const result = await shippingApi.applyUpdScan(current.id, {
        version: current.version, scan_file: answer.scan_file, upd_number: found, upd_date: answer.recognised.upd_date || null,
        invoice_number: (answer.recognised.invoice_number ?? "").trim(), number: number ?? "replace",
      });
      if (result.status === "conflict") { setReplacing({ answer, typed: result.current }); return; }
      const next = result.shipment;
      setShipment(next);
      setUpdType(next.upd_type ?? "");
      if (number !== "keep" && found) { setUpdNumber(next.upd_number); setUpdDate(next.upd_date ?? ""); }
      setNote(found ? `УПД № ${found} внесён${result.invoice ? `, № счёта ${result.invoice} — в проект` : ""}` : "Файл УПД приложен, номер в документе не найден — внесите его вручную");
      try { await onSaved(); } catch { /* список обновится при следующем открытии */ }
    } catch (reason) {
      setError(failureText(reason, "Не удалось приложить УПД"));
    } finally { setApplying(false); }
  };
  const answerRef = useRef(applyAnswer);
  answerRef.current = applyAnswer;
  const scanApi = useMemo(() => shippingApi.updScan(opened.id), [opened.id]);
  const scan = useUpdScan(scanApi, (answer) => void answerRef.current(answer));
  const coarse = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  const reading = scan.scanning || applying;

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
    {scan.agentDialogs()}
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
        {!noUpd && <div className="ship-span2 ship-upd-scan">
          <span className="ship-upd-buttons">
            {!edoType && (scan.pages.length > 0
              ? <button type="button" className="secondary" disabled={reading} onClick={() => void scan.fromScanner()}>+ Страница</button>
              : <button type="button" className="secondary" disabled={reading} onClick={() => void scan.fromScanner(true)}>Сканировать</button>)}
            <button type="button" className="secondary" disabled={reading} onClick={() => fileInput.current?.click()}>{edoType ? "Файл ЭДО…" : "Файл…"}</button>
            <input ref={fileInput} type="file" hidden multiple={!edoType} aria-label="Файл УПД"
              accept={edoType ? ".xml,.zip,application/xml,text/xml,application/zip" : ".pdf,.xml,.zip,image/*,application/xml,text/xml,application/zip"}
              {...(coarse && !edoType ? { capture: "environment" as const } : {})}
              onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; if (files.length) void scan.fromFiles(files); }} />
          </span>
          {shipment.upd_url && <a className="ship-upd-link" href={shipment.upd_url} target="_blank" rel="noreferrer">{edoType ? "Файл ЭДО" : "Скан УПД"} · открыть</a>}
          {(scan.statusText || applying || note) && <p className={`wr-scan-status${scan.error ? " error" : ""}`} role="status">
            {applying ? "Вношу УПД в отправку…" : scan.statusText || note}
            {scan.status ? <> <a href="#" onClick={(event) => { event.preventDefault(); scan.cancel(); }}>Отменить</a></> : null}
          </p>}
          {replacing && <p className="warning-inline" role="alert">
            Заменить № УПД «{replacing.typed}» на «{replacing.answer.recognised.upd_number}» из документа?
            {" "}<button type="button" className="secondary small" onClick={() => void applyAnswer(replacing.answer, "replace")}>Заменить</button>
            {" "}<button type="button" className="secondary small" onClick={() => void applyAnswer(replacing.answer, "keep")}>Оставить мой №</button></p>}
        </div>}
        {updType && !noUpd && <label>№ УПД<input aria-label="№ УПД" value={updNumber} onChange={(event) => setUpdNumber(event.target.value)} /></label>}
        {updType && !noUpd && <label>Дата УПД<input type="date" className="ship-narrow" aria-label="Дата УПД" value={updDate} onChange={(event) => setUpdDate(event.target.value)} /></label>}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ship-actions"><span className="ship-actions-end">
        <button type="button" className="secondary" onClick={close}>Отмена</button>
        <button className="primary" disabled={saving || reading}>{saving ? "Сохраняем…" : "Сохранить"}</button>
      </span></div>
    </form>
  </PageWindow>;
}
