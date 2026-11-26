import { useRef, useState, type ReactNode } from "react";
import { failureText, shippingApi } from "./api";
import { CARRIERS, isPickup, needsTrack, termsText } from "./carriers";
import { readFile } from "./DispatchCard";
import { EditSection } from "./EditSection";
import { formatDay } from "./format";
import { InlineField } from "./InlineField";
import type { DispatchShipment } from "./productionIntakeApi";
import { UPD_TYPES, updLabel } from "./ShipmentData";
import { editableKeys, PartialSaveError, saveShipment, storedValue, type DraftKey, type ShipmentDraft } from "./shipmentSaves";

/* Данные одной отправки на экране «Приёмка с производства» (владелец 26.11, v2): блоки «Документы», «Доставка»,
   «Получатель» и полоса «Ждём · Срок · комментарии · Организация». Каждое поле с правкой сервера правится на месте
   (карандаш) или всем блоком («Редактировать»); вычисляемое («Ждём», PL/DN, даты отгрузки) — только чтение.
   После отгрузки шапка — только «Исправить историю…» (руководитель), документы — дополнить. */

const dash = <span className="muted">—</span>;
const TERMS_OPTIONS: Array<readonly [string, string]> = [["", "не выбран"], ["САМОВЫВОЗ", "Самовывоз"], ["ДОСТАВКА ТК", "Доставка ТК"]];
const CARRIER_OPTIONS: Array<readonly [string, string]> = [["", "не выбран"], ...CARRIERS.map((name) => [name, name] as const)];

export function CopyButton({ text, label }: { text: string; label: string }) {
  return <button type="button" className="intake-copy" title={`Скопировать: ${label}`} aria-label={`Скопировать ${label}`}
    onClick={(event) => { event.stopPropagation(); try { void navigator.clipboard?.writeText(text); } catch { /* буфер недоступен — копировать вручную */ } }} />;
}

export interface ShipmentInfoRights {
  /** Правка данных отправки («Отгрузка» или «Отгрузка — склад»). */
  canEdit: boolean;
  /** «Исправить историю…» отгруженной отправки (руководитель). */
  canHistory: boolean;
}

export function IntakeShipmentInfo({ shipment, rights, gaps, pl, dn, deliveryButton, onShipment, onHistory }: {
  shipment: DispatchShipment;
  rights: ShipmentInfoRights;
  /** «Ждём»: чего не хватает, словами сервера. */
  gaps: string[];
  pl: ReactNode;
  dn: ReactNode;
  /** «Доставка КБ» — окно доставки КБ этой отправки. */
  deliveryButton?: ReactNode;
  /** Отправка из ответа сервера после правки — экран заменяет её у себя. */
  onShipment: (next: DispatchShipment) => void;
  onHistory: () => void;
}) {
  const editable = editableKeys(shipment, rights.canEdit);
  const can = (key: DraftKey) => editable.has(key);
  const [fileBusy, setFileBusy] = useState(false);
  const [fileError, setFileError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const historical = ["Отгружена", "Закрыта"].includes(shipment.status);
  const pickup = isPickup(shipment.carrier) || shipment.terms === "САМОВЫВОЗ";
  const noUpd = shipment.upd_type === "БЕЗ УПД";

  /** Одна правка: шапка → документы → «Для перевозчика»; что успело сохраниться — в экран, отказ — словами сервера. */
  const save = async (draft: ShipmentDraft) => {
    try { onShipment(await saveShipment(shipment, draft)); }
    catch (reason) {
      if (reason instanceof PartialSaveError) {
        if (reason.saved) onShipment(reason.shipment);
        throw new Error(failureText(reason.cause, "Не удалось сохранить отправку") + (reason.saved ? " Часть полей сохранена." : ""));
      }
      throw new Error(failureText(reason, "Не удалось сохранить отправку"));
    }
  };
  const one = (key: DraftKey) => (value: string) => save({ [key]: value });
  const values = (keys: DraftKey[]) => Object.fromEntries(keys.map((key) => [key, storedValue(shipment, key)]));
  const field = (key: DraftKey, label: string, extra: Partial<Parameters<typeof InlineField>[0]> = {}) =>
    <InlineField name={key} label={label} value={storedValue(shipment, key)} editable={can(key)} onSave={one(key)} {...extra} />;

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setFileBusy(true); setFileError("");
    try { onShipment((await shippingApi.uploadUpd(shipment.id, shipment.version, await readFile(file))).shipment as DispatchShipment); }
    catch (reason) { setFileError(failureText(reason, "Файл УПД не загружен")); }
    finally { setFileBusy(false); }
  };
  const removeFile = async () => {
    if (!window.confirm(`Убрать файл УПД из отправки ${shipment.number}?`)) return;
    setFileBusy(true); setFileError("");
    try { await save({ upd_url: "" }); }
    catch (reason) { setFileError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setFileBusy(false); }
  };
  const canFile = can("upd_url") && !noUpd;
  const file = <div className="intake-field">
    <span className="intake-field-label">Файл УПД</span>
    <span className="intake-field-value intake-file" data-label="Файл УПД">
      {shipment.upd_url
        ? <a href={shipment.upd_url} target="_blank" rel="noreferrer" aria-label={`Открыть файл УПД ${shipment.number}`}>Открыть файл</a>
        : noUpd || !shipment.upd_type ? dash : <span className="muted">файл не приложен</span>}
      {canFile && <>
        <button type="button" className="link-button" disabled={fileBusy} aria-label={`${shipment.upd_url ? "Заменить" : "Загрузить"} файл УПД ${shipment.number}`}
          onClick={() => fileInput.current?.click()}>{fileBusy ? "Загружаем…" : shipment.upd_url ? "Заменить" : "Загрузить"}</button>
        {shipment.upd_url && <button type="button" className="link-button" disabled={fileBusy} aria-label={`Убрать файл УПД ${shipment.number}`}
          onClick={() => void removeFile()}>Убрать</button>}
        <input ref={fileInput} type="file" hidden accept=".pdf,.xml,.zip,image/*,application/xml,text/xml,application/zip" aria-label={`Файл УПД ${shipment.number}`}
          onChange={(event) => { const chosen = event.target.files?.[0]; event.target.value = ""; void upload(chosen); }} />
      </>}
    </span>
    {fileError && <span className="inline-field-error" role="alert">{fileError}</span>}
  </div>;

  const updTypeOptions: Array<readonly [string, string]> = [["", shipment.delivery_snapshot_at ? "не выбран" : "из карточки проекта"], ...UPD_TYPES];
  const track = shipment.track_number.trim();
  const trackDisplay = track || (needsTrack(shipment) ? dash : <span className="muted">не нужен</span>);
  const historyButton = historical && rights.canHistory
    ? <button type="button" className="link-button" aria-label={`Исправить историю ${shipment.number}`} onClick={onHistory}>Исправить историю…</button> : null;

  const docKeys: DraftKey[] = ["upd_type", "upd_number", "upd_date"];
  const deliveryKeys: DraftKey[] = ["terms", "carrier", "track_number"];
  const recipientKeys: DraftKey[] = ["contact", "phone", "shipping_point"];
  const stripKeys: DraftKey[] = ["deadline", "note", "tk_note", "receiver"];
  const any = (keys: DraftKey[]) => keys.some(can);

  return <div className="intake-info">
    <div className="intake-panels">
      <EditSection title="Документы" editable={any(docKeys)} values={values(docKeys)} onSave={save}>
        <div className="intake-fields">
          {field("upd_type", "УПД", { kind: "select", options: updTypeOptions, dataLabel: "УПД",
            display: shipment.upd_type ? <>{updLabel(shipment.upd_type)}{shipment.inherited.includes("upd_type") && <small className="muted"> · из карточки</small>}</> : undefined })}
          {field("upd_number", "№ УПД", { editable: can("upd_number") && !noUpd })}
          {field("upd_date", "Дата УПД", { kind: "date", editable: can("upd_date") && !noUpd, display: shipment.upd_date ? formatDay(shipment.upd_date) : undefined })}
          {file}
          <div className="intake-field"><span className="intake-field-label">PL</span><span className="intake-field-value" data-label="PL">{pl}</span></div>
          <div className="intake-field"><span className="intake-field-label">DN</span><span className="intake-field-value" data-label="DN">{dn}</span></div>
        </div>
      </EditSection>
      <EditSection title="Доставка" editable={any(deliveryKeys)} values={values(deliveryKeys)} onSave={save} extra={deliveryButton}>
        <div className="intake-fields">
          {field("terms", "Способ доставки", { kind: "select", options: TERMS_OPTIONS, display: (pickup ? "Самовывоз" : termsText(shipment.terms)) || undefined })}
          {field("carrier", "ТК", { kind: "select", options: CARRIER_OPTIONS, display: pickup ? dash : undefined })}
          {field("track_number", "Трек-номер", { wide: true, dataLabel: "Трек", display: trackDisplay, editable: can("track_number") && (needsTrack(shipment) || Boolean(track)),
            after: track ? <CopyButton text={track} label="трек-номер" /> : null })}
        </div>
      </EditSection>
      <EditSection title="Получатель" editable={any(recipientKeys)} values={values(recipientKeys)} onSave={save} extra={historyButton}>
        <div className="intake-fields">
          {field("contact", "Получатель", { maxLength: 255 })}
          {field("phone", "Телефон", { kind: "tel", maxLength: 255, after: shipment.phone.trim() ? <CopyButton text={shipment.phone.trim()} label="телефон" /> : null })}
          {field("shipping_point", "Адрес", { wide: true, maxLength: 1000, display: pickup ? dash : undefined,
            after: !pickup && shipment.shipping_point.trim() ? <CopyButton text={shipment.shipping_point.trim()} label="адрес" /> : null })}
        </div>
      </EditSection>
    <EditSection title="Срок и комментарии" className="intake-panel intake-strip-section" editable={any(stripKeys)} values={values(stripKeys)} onSave={save}>
      <div className="intake-strip">
        <div className="intake-field"><span className="intake-field-label">Ждём</span><span className="intake-field-value" data-label="Ждём">
          {gaps.length ? <span className="tone-warn">{gaps.join(", ")}</span> : dash}</span></div>
        {field("deadline", "Срок", { kind: "date", display: shipment.deadline ? formatDay(shipment.deadline) : undefined })}
        {historical && <div className="intake-field"><span className="intake-field-label">Отгружена</span><span className="intake-field-value" data-label="Отгружена">
          {shipment.shipped_at ? formatDay(shipment.shipped_at) : dash}</span></div>}
        {field("note", "Комментарий для склада", { kind: "textarea", maxLength: 5000, dataLabel: "Комментарий для склада" })}
        {field("tk_note", "Для перевозчика", { kind: "textarea", maxLength: 5000 })}
        {field("receiver", "Организация", { maxLength: 512 })}
      </div>
    </EditSection>
    </div>
  </div>;
}
