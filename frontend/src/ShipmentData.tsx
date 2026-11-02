import { useMemo, useRef, useState } from "react";
import { failureText, shippingApi, type Shipment, type UpdType, type WarehouseReceiptScan } from "./api";
import { PageWindow } from "./PageWindow";
import { CARRIERS, PICKUP_CARRIER, isPickup, needsTrack } from "./carriers";
import { StatusChip } from "./StatusChip";
import { useUpdScan } from "./useUpdScan";
import { ScanDeviceLine } from "./ScanAgent";
import { useCanPrint, useCanScan } from "./rights";
import { openShippingForms } from "./shippingPrint";
import { formatDate } from "./format";
import { shipmentDispatchStage, type DispatchStage } from "./dispatchStage";
import { PackDecision } from "./shippingCells";
import "./dispatch.css";

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

/* Один итог готовности и фактический список недостающих данных; общий для склада и менеджера. */
export interface ReadinessItem { label: string; ok: boolean }
interface ReadinessSource {
  receiver: string; phone: string; shipping_point: string; carrier: string; terms: string | null; upd_type: string | null;
  upd_number?: string; upd_url?: string; track_number?: string;
  ready_to_ship?: boolean; missing_ship?: string[];
  track_required?: boolean;
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
  if (withTrack && needsTrack(source)) items.push(source.track_number?.trim() ? { label: "Трек", ok: true } : { label: "нет трека", ok: false });
  return items;
}

/** `gaps` — слова сервера для отправки (они главнее); для карточки проекта считаем незаполненные поля. */
export function Readiness({ source, gaps, withTrack = false }: { source: ReadinessSource; gaps?: string[]; withTrack?: boolean }) {
  const items = readinessItems(source, withTrack);
  const allMissing = gaps ?? items.filter((item) => !item.ok).map((item) => item.label);
  const missing = allMissing.filter((gap) => gap !== "трек-номер" && gap !== "нет трека");
  const trackMissing = allMissing.includes("трек-номер") || allMissing.includes("нет трека") || source.missing_ship?.includes("трек-номер")
    || (withTrack && items.some((item) => item.label === "нет трека"));
  const warehouse = source.ready_to_ship === false || Boolean(source.missing_ship?.length) || trackMissing;
  return <span className="ship-ready" aria-label="Готовность данных отправки">
    {missing.length === 0
      ? warehouse
        ? <StatusChip tone="warning" title={`Не хватает: ${(source.missing_ship ?? allMissing).join(", ") || "готовность склада"}`}>
          {trackMissing ? "Ждёт трек от склада" : "Ждёт склад"}</StatusChip>
        : <StatusChip tone="success" title="Все данные внесены">{source.ready_to_ship === true ? "Готово к отгрузке" : "Данные внесены"}</StatusChip>
      : <StatusChip tone="warning" title={`Не хватает: ${missing.join(", ")}`}>Ждёт данных менеджера</StatusChip>}
    {missing.length > 0 && <span className="ship-gaps-list">{missing.join(", ")}</span>}
  </span>;
}

const CARRIER_CHOICES =CARRIERS.filter((name) => name !== PICKUP_CARRIER);

/** Окно с данными одной отправки — только поля, которые вносит менеджер. Сохранение — через правку шапки и документов
 *  отправки (проверка версии на сервере): сначала шапка, потом документы, потом файл УПД. */
export function ShipmentDataWindow({ shipment: opened, close, onSaved, stage: given, canEdit = true }: {
  shipment: Shipment; close: () => void; onSaved: () => Promise<void> | void;
  /** Этап на общей лестнице (29.10); без него — как у принятой на склад отправки. */
  stage?: DispatchStage | null; canEdit?: boolean;
}) {
  const stage: DispatchStage = given ?? shipmentDispatchStage(opened, "Принято складом") ?? "accepted";
  const shipped = stage === "shipped";
  const packed = stage === "packed";
  const afterPack = stage === "to_ship" || shipped;
  const deciding = stage === "ready" || stage === "accepted";
  // Скан или файл УПД сразу кладёт документ в отправку (версия растёт), поэтому окно ведёт свою копию отправки.
  const [shipment, setShipment] = useState<Shipment>(opened);
  const [delivery, setDelivery] = useState<string>(shipment.terms ?? (isPickup(shipment.carrier) ? "САМОВЫВОЗ" : ""));
  const [carrier, setCarrier] = useState(isPickup(shipment.carrier) ? "" : shipment.carrier);
  const [point, setPoint] = useState(shipment.shipping_point);
  const [receiver, setReceiver] = useState(shipment.receiver);
  const [phone, setPhone] = useState(shipment.phone);
  const [warehouseNote, setWarehouseNote] = useState(shipment.note);
  const [updType, setUpdType] = useState<string>(shipment.upd_type ?? "");
  const [updNumber, setUpdNumber] = useState(shipment.upd_number);
  const [updDate, setUpdDate] = useState(shipment.upd_date ?? "");
  // «Убрать файл»: the attached file is dropped on «Сохранить» (empty `upd_url`); «Отмена» keeps it.
  const [removedUrl, setRemovedUrl] = useState("");
  const fileShown = Boolean(shipment.upd_url) && shipment.upd_url !== removedUrl;
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
  const latest = useRef({ shipment, updNumber, edoType });
  latest.current = { shipment, updNumber, edoType };

  /** Ответ чтения ложится в отправку: файл, тип, № и дата документа, № счёта из «Основания». Другой уже внесённый № молча
   *  не затирается: сначала вопрос «Заменить?». */
  const applyAnswer = async (answer: WarehouseReceiptScan, number?: "replace" | "keep") => {
    if (!canEdit) return;
    const { shipment: current, updNumber: typedRaw, edoType: edo } = latest.current;
    const found = (answer.recognised.upd_number ?? "").trim();
    const typed = typedRaw.trim();
    if (!number && found && typed && typed !== found) { setReplacing({ answer, typed }); return; }
    setReplacing(null); setApplying(true); setError(""); setNote("");
    try {
      const result = await shippingApi.applyUpdScan(current.id, {
        version: current.version, scan_file: answer.scan_file, upd_number: found, upd_date: answer.recognised.upd_date || null,
        invoice_number: (answer.recognised.invoice_number ?? "").trim(), number: number ?? "replace", ...(edo ? { edo } : {}),
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
  const canScan = useCanScan();
  const canPrint = useCanPrint();
  const printPl = async () => {
    setError("");
    try { openShippingForms((await shippingApi.print(shipment.id, "pl")).forms); }
    catch (reason) { setError(failureText(reason, "Не удалось открыть упаковочный лист")); }
  };
  const coarse = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  const reading = scan.scanning || applying;

  const save = async () => {
    if (!canEdit) return;
    setSaving(true); setError("");
    try {
      let current = shipment;
      const head: Record<string, unknown> = {};
      const wantedCarrier = pickup ? PICKUP_CARRIER : carrier.trim();
      if (receiver.trim() !== shipment.receiver.trim()) head.receiver = receiver.trim();
      if (phone.trim() !== shipment.phone.trim()) head.phone = phone.trim();
      if (warehouseNote.trim() !== shipment.note.trim()) head.note = warehouseNote.trim();
      if (delivery && delivery !== shipment.terms) head.terms = delivery;
      if (delivery && wantedCarrier !== shipment.carrier.trim()) head.carrier = wantedCarrier;
      if (!shipped && !pickup && point.trim() !== shipment.shipping_point.trim()) head.shipping_point = point.trim();
      if (Object.keys(head).length) current = (await shippingApi.head(shipment.id, { version: current.version, ...head })).shipment;
      const number = noUpd ? "" : updNumber.trim();
      const day = noUpd ? "" : updDate;
      const url = removedUrl && current.upd_url === removedUrl ? "" : current.upd_url;
      if (updType !== (current.upd_type ?? "") || number !== current.upd_number || day !== (current.upd_date ?? "")
        || url !== current.upd_url) {
        current = (await shippingApi.documents(shipment.id, {
          version: current.version, upd_type: updType || null, upd_number: number, upd_date: day || null,
          upd_url: url, track_number: current.track_number,
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
    {canEdit && scan.agentDialogs()}
    <form className="panel ship-data" onSubmit={(event) => { event.preventDefault(); if (canEdit) void save(); }}>
      <h2>Данные отправки {shipment.number}</h2>
      <p className="ship-data-sub">{shipment.customer || shipment.receiver}{shipment.lines.length ? ` · ${[...new Set(shipment.lines.map((line) => line.project))].join(", ")}` : ""}
        {gaps.length > 0 && <span className="tone-warn"> · не хватает: {gaps.join(", ")}</span>}</p>
      {deciding && <div className="ship-block"><h3>Упаковка</h3>
        {[...new Set(shipment.lines.map((line) => line.project))].map((name) => <PackDecision key={name} project={name} editable={canEdit} />)}</div>}
      {!shipped && <div className="ship-block"><h3>Адрес</h3><div className="ship-form">
        {!pickup && <label className="ship-span2">Адрес доставки<input aria-label="Адрес доставки" value={point} readOnly={!canEdit} onChange={canEdit ? (event) => setPoint(event.target.value) : undefined} /></label>}
        <label>Получатель<input aria-label="Получатель" value={receiver} readOnly={!canEdit} onChange={canEdit ? (event) => setReceiver(event.target.value) : undefined} /></label>
        <label>Телефон получателя<input aria-label="Телефон получателя" value={phone} readOnly={!canEdit} onChange={canEdit ? (event) => setPhone(event.target.value) : undefined} /></label>
        <label className="ship-span2">Комментарий для склада<textarea aria-label="Комментарий для склада" maxLength={5000} value={warehouseNote} readOnly={!canEdit} onChange={canEdit ? (event) => setWarehouseNote(event.target.value) : undefined} /></label>
      </div></div>}
      <div className="ship-block"><h3>ТК и УПД</h3><div className="ship-form">
        <label className={packed && !delivery ? "ship-required" : undefined}>Тип доставки<select aria-label="Тип доставки" value={delivery} disabled={!canEdit} onChange={canEdit ? (event) => setDelivery(event.target.value) : undefined}>
          <option value="">не выбран</option><option value="САМОВЫВОЗ">Самовывоз</option><option value="ДОСТАВКА ТК">Доставка ТК</option></select></label>
        {carrierTrip && <label className={packed && !carrier ? "ship-required" : undefined}>Перевозчик<select aria-label="Перевозчик" value={carrier} disabled={!canEdit} onChange={canEdit ? (event) => setCarrier(event.target.value) : undefined}>
          <option value="">не выбран</option>{CARRIER_CHOICES.map((name) => <option key={name} value={name}>{name}</option>)}
          {carrier && !(CARRIER_CHOICES as readonly string[]).includes(carrier) && <option value={carrier}>{carrier} (прежнее)</option>}</select></label>}
        <label className={packed && !updType ? "ship-required" : undefined}>УПД<select aria-label="Тип УПД" value={updType} disabled={!canEdit} onChange={canEdit ? (event) => setUpdType(event.target.value) : undefined}>
          <option value="">не выбран</option>{UPD_TYPES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        {!noUpd && (canEdit || fileShown) && <div className="ship-span2 ship-upd-scan">
          {canEdit && <span className="ship-upd-buttons">
            {!edoType && canScan && (scan.pages.length > 0
              ? <button type="button" className="secondary" disabled={reading} onClick={() => void scan.fromScanner()}>+ Страница</button>
              : <button type="button" className="secondary" disabled={reading} onClick={() => void scan.fromScanner(true)}>Сканировать</button>)}
            <button type="button" className="secondary" disabled={reading} onClick={() => fileInput.current?.click()}>{edoType ? "Файл ЭДО…" : "Файл…"}</button>
            <input ref={fileInput} type="file" hidden multiple={!edoType} aria-label="Файл УПД"
              accept={edoType ? ".pdf,.xml,.zip,application/pdf,application/xml,text/xml,application/zip" : ".pdf,.xml,.zip,image/*,application/xml,text/xml,application/zip"}
              {...(coarse && !edoType ? { capture: "environment" as const } : {})}
              onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; if (files.length) void scan.fromFiles(files); }} />
          </span>}
          {canEdit && !edoType && <ScanDeviceLine />}
          {fileShown && <span className="ship-upd-attached">
            <a className="ship-upd-link" href={shipment.upd_url} target="_blank" rel="noreferrer">{edoType ? "Файл ЭДО" : "Скан УПД"} · открыть</a>
            {canEdit && <button type="button" className="secondary small" onClick={() => setRemovedUrl(shipment.upd_url ?? "")}>Убрать файл</button>}
          </span>}
          {canEdit && !fileShown && removedUrl && shipment.upd_url === removedUrl && <span className="ship-upd-removed">Файл уберётся при сохранении ·{" "}
            <a href="#" onClick={(event) => { event.preventDefault(); setRemovedUrl(""); }}>Вернуть</a></span>}
          {canEdit && (scan.statusText || applying || note) && <p className={`wr-scan-status${scan.error ? " error" : ""}`} role="status">
            {applying ? "Вношу УПД в отправку…" : scan.statusText || note}
            {scan.status ? <> <a href="#" onClick={(event) => { event.preventDefault(); scan.cancel(); }}>Отменить</a></> : null}
          </p>}
          {canEdit && replacing && <p className="warning-inline" role="alert">
            Заменить № УПД «{replacing.typed}» на «{replacing.answer.recognised.upd_number}» из документа?
            {" "}<button type="button" className="secondary small" onClick={() => void applyAnswer(replacing.answer, "replace")}>Заменить</button>
            {" "}<button type="button" className="secondary small" onClick={() => void applyAnswer(replacing.answer, "keep")}>Оставить мой №</button></p>}
        </div>}
        {updType && !noUpd && <label>№ УПД<input aria-label="№ УПД" value={updNumber} readOnly={!canEdit} onChange={canEdit ? (event) => setUpdNumber(event.target.value) : undefined} /></label>}
        {updType && !noUpd && <label>Дата УПД<input type="date" className="ship-narrow" aria-label="Дата УПД" value={updDate} disabled={!canEdit} onChange={canEdit ? (event) => setUpdDate(event.target.value) : undefined} /></label>}
      </div>
      {packed && (!updType || !delivery) && <p className="ship-required-note">Упаковано: УПД и ТК обязательны — внесите, чтобы склад мог отгрузить</p>}
      {afterPack
        ? <p className="muted ship-track">{(needsTrack(shipment) || shipment.track_number) && <>Трек: <strong>{shipment.track_number || "—"}</strong>{" · "}</>}{shipped ? "отгружено" : "упаковано"} {formatDate(String((shipped ? shipment.shipped_at : shipment.packed_at) ?? "").slice(0, 10)) || "—"}</p>
        : (needsTrack(shipment) || shipment.track_number) && <p className="muted ship-track">{shipment.track_number ? `Трек: ${shipment.track_number}` : "трек: ждём склад"}</p>}
      {shipment.no_upd_approved_by && <p className="ship-no-upd">без УПД — разрешил {shipment.no_upd_approved_by}{shipment.no_upd_approved_at ? `, ${formatDate(String(shipment.no_upd_approved_at).slice(0, 10))}` : ""}</p>}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ship-actions"><span className="ship-actions-end">
        {shipment.pl_number && canPrint && <button type="button" className="secondary" onClick={() => void printPl()}>Печать ПЛ · {shipment.pl_number}</button>}
        <button type="button" className="secondary" onClick={close}>Отмена</button>
        {canEdit && <button className="primary" disabled={saving || reading}>{saving ? "Сохраняем…" : "Сохранить"}</button>}
      </span></div>
    </form>
  </PageWindow>;
}
