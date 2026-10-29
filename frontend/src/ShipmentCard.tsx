import { useEffect, useMemo, useState } from "react";
import { AutoTextarea } from "./AutoTextarea";
import { failureText, shippingApi, type Shipment } from "./api";
import { CarrierSelect, isPickup, termsFor, termsText } from "./carriers";
import { readFile } from "./DispatchCard";
import { formatDate } from "./format";
import { isoDay } from "./Gantt";
import { PageWindow } from "./PageWindow";
import { daysUntil } from "./productionGraph";
import { intakeApi, type DispatchShipment, type NoUpdApprover } from "./productionIntakeApi";
import { useCanPrint } from "./rights";
import { DataChip, Readiness, UPD_TYPES, updLabel } from "./ShipmentData";
import { fetchBoxLabels, openBoxLabels, openShippingForms } from "./shippingPrint";
import { StatusChip as SharedChip } from "./StatusChip";
import "./shipping.css";

/* Карточка отправки (владелец 29.10.2026: всё на «Приёмке с производства», экран «Отгрузка» убран) — открывается в
   окнах «Упаковано» и «Отгружено». Набор, PL/DN и короба, этикетки, УПД и трек, «Отгрузить», «Закрыть», откат.
   Отгрузить — когда упакована и есть УПД, ТК и трек (`ready_to_ship` сервера); без УПД — по ПИН уполномоченного. */

/** Сообщение после действия: строка — успех, `{ warning }` — сделано не всё (показывается как ошибка). */
export type Outcome = string | { warning: string };
export type Mutate = (key: string, operation: () => Promise<unknown>, message: string | ((result: unknown) => Outcome)) => Promise<boolean>;

export const ACTIVE = ["Новая", "Собрана", "Упакована"];
const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;
const today = () => isoDay(new Date());
export const withoutUpd = (shipment: Shipment) => shipment.upd_type === "БЕЗ УПД";

/** Ошибки, сообщение и «занято» одного окна: окно поверх страницы, её баннеров не видно. */
export function useMutate(reload: () => Promise<void> | void) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
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
  return { busy, error, notice, mutate, setError };
}

export function Banners({ error, notice }: { error: string; notice: string }) {
  return <>{error && <div className="error-banner" role="alert">{error}</div>}{notice && <div className="success-banner" role="status">{notice}</div>}</>;
}

/** Срок прошёл, а груз ещё не уехал. */
export function overdue(shipment: Shipment) {
  return Boolean(shipment.deadline) && String(shipment.deadline) < today() && ACTIVE.includes(shipment.status);
}

const STATUS_HINT: Record<string, string> = {
  "Новая": "заявка — ничего не набрано", "Собрана": "набрана, PL и DN не сформированы", "Упакована": "упакована, PL и DN готовы",
  "Отгружена": "уехала, не закрыта", "Закрыта": "уехала, документы на месте", "Отменена": "отменена, набранное вернулось на склад",
};
export function ShipStatus({ status }: { status: string }) {
  return <SharedChip status={status} title={STATUS_HINT[status]} />;
}

/** Срок одной строкой: «16.10.2026 · просрочено на 3 дн.»; срок раньше заведения — подсказка проверить дату. */
export function Due({ day, late, since }: { day: string | null; late: boolean; since?: string | null }) {
  if (!day) return <span className="muted">—</span>;
  const behind = late ? Math.max(1, -daysUntil(day)) : 0;
  const odd = late && Boolean(since) && day < String(since).slice(0, 10);
  const title = odd ? `Просрочено на ${behind} дн.: срок раньше даты заведения ${formatDate(String(since).slice(0, 10))} — похоже на опечатку в дате` : undefined;
  return <span className={late ? "ship-due tone-danger" : "ship-due"} title={title}>{formatDate(day)}
    {late && <span className="ship-due-late">{odd ? " · проверьте дату" : ` · просрочено на ${behind} дн.`}</span>}</span>;
}

const TERMS_LABEL: Record<string, string> = { "САМОВЫВОЗ": "Самовывоз", "ДОСТАВКА ТК": "Доставка ТК" };
const sameWords = (text: string) => text.toLowerCase().replace(/[«»"'“”]/g, "").replace(/\s+/g, " ").trim();

/** «Кому · куда» без повторов: получатель, адрес, условия, ТК — каждое слово один раз. */
export function destination(shipment: Pick<Shipment, "receiver" | "shipping_point" | "terms" | "carrier">): string {
  const parts: string[] = [];
  const terms = shipment.terms ? TERMS_LABEL[shipment.terms] ?? shipment.terms : "";
  for (const raw of [shipment.receiver, shipment.shipping_point, terms, shipment.carrier]) {
    const text = String(raw ?? "").trim();
    if (!text) continue;
    const twin = parts.findIndex((part) => sameWords(part) === sameWords(text));
    if (twin < 0) parts.push(text);
    else if (parts[twin] === parts[twin].toLowerCase() && text !== text.toLowerCase()) parts[twin] = text;
  }
  return parts.join(" · ");
}

/** «591К.3: КБ1, КБ2; 600К.1: КБ7». */
export function projectsOf(shipment: Shipment) {
  const byProject = new Map<string, string[]>();
  for (const line of shipment.lines) {
    const items = byProject.get(line.project) ?? [];
    if (!items.includes(line.item_index)) items.push(line.item_index);
    byProject.set(line.project, items);
  }
  return [...byProject].map(([project, items]) => `${project}: ${items.join(", ")}`).join("; ");
}

/** УПД без файла отгрузить нельзя, кроме ЭДО и «Без УПД» (сервер, `mark_shipped`). */
const needsUpdFile = (shipment: Shipment) => !["ЭДО", "БЕЗ УПД"].includes(shipment.upd_type ?? "") && !shipment.upd_url;
/** Что держит отгрузку из-за УПД; у «Без УПД» сервер пишет «разрешение на отгрузку без УПД», пока его не дали. */
const UPD_GAPS = ["тип УПД", "№ УПД", "файл УПД", "разрешение на отгрузку без УПД"];
/** Отгружают набранное: собранную (самовывоз) или упакованную отправку. */
const SHIPPABLE = ["Собрана", "Упакована"];
const picked = (shipment: Shipment) => shipment.lines.some((line) => num(line.picked) > 0);

/** Отгрузить можно: слово сервера `ready_to_ship` (собрана или упакована, что-то набрано, всего хватает). */
export const readyToShip = (shipment: DispatchShipment) =>
  shipment.ready_to_ship ?? (SHIPPABLE.includes(shipment.status) && picked(shipment) && shipment.missing_ship.length === 0);
/** Трек вписать можно: слово сервера `ready_for_track`; без него — не хватает только трека. */
export const readyForTrack = (shipment: DispatchShipment) =>
  shipment.ready_for_track ?? (shipment.missing_ship.length > 0 && shipment.missing_ship.every((gap) => gap === "трек-номер"));
/** Трек нужен доставке ТК; самовывоз уезжает без него. */
export const needsTrack = (shipment: Shipment) => shipment.terms !== "САМОВЫВОЗ" && !isPickup(shipment.carrier);
/** Держит только УПД (и трек, который без УПД не вписать) — можно разрешить отгрузку без УПД по ПИН. */
export function onlyUpdBlocks(shipment: DispatchShipment) {
  if (!SHIPPABLE.includes(shipment.status) || !picked(shipment) || readyToShip(shipment) || shipment.no_upd_approved_by) return false;
  const gaps = shipment.missing_ship;
  return gaps.some((gap) => UPD_GAPS.includes(gap)) && gaps.every((gap) => UPD_GAPS.includes(gap) || gap === "трек-номер");
}

/** Почему отгруженная отправка не закрылась: «Нельзя закрыть: не хватает — файл УПД» → «Не закрыта — не хватает: файл УПД». */
function notClosed(why: string | undefined) {
  const text = String(why || "").replace(/^Нельзя закрыть:\s*/, "");
  const missing = /^не хватает\s*—\s*/.exec(text);
  return missing ? `Не закрыта — не хватает: ${text.slice(missing[0].length)}` : `Не закрыта — ${text || "не хватает данных"}`;
}

export interface CardRights { canManage: boolean; canWarehouse: boolean; canEdit: boolean; canUnship: boolean }

/** Отправка целиком: строки, набор и возврат, PL/DN и короба, этикетки, УПД и трек, отгрузка, закрытие, откат. */
export function ShipmentCard({ shipment, rights, busy, mutate, openData }: {
  shipment: DispatchShipment; rights: CardRights; busy: string; mutate: Mutate; openData: () => void;
}) {
  const { canManage, canWarehouse, canEdit, canUnship } = rights;
  const [documents, setDocuments] = useState({ upd_type: shipment.inherited.includes("upd_type") ? "" : shipment.upd_type ?? "", upd_number: shipment.upd_number, upd_date: shipment.upd_date ?? "", track_number: shipment.track_number });
  const [requested, setRequested] = useState<Record<string, string>>({});
  const [layout, setLayout] = useState(false);
  const [printError, setPrintError] = useState("");
  const [rollback, setRollback] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const action = (name: "pick" | "pack" | "ship" | "close" | "cancel", message: string | ((result: unknown) => string)) => mutate(`${name}:${shipment.id}`, () => shippingApi.action(shipment.id, name, shipment.version), message);
  const got = shipment.lines.some((line) => num(line.picked) > 0);
  const open = !["Закрыта", "Отменена"].includes(shipment.status);
  const shipped = ["Отгружена", "Закрыта"].includes(shipment.status);
  const active = ACTIVE.includes(shipment.status);
  const canPrintDocs = useCanPrint();
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
  // Слова сервера: чего не хватает, чтобы напечатать DN (`missing_data`) и чтобы отгрузить (`missing_ship`, ещё и трек ТК).
  const dnGaps = active ? shipment.missing_data : [];
  const shipGaps = shipment.missing_ship;
  const canShip = readyToShip(shipment);
  const trackOpen = readyForTrack(shipment) || Boolean(shipment.track_number);
  return <article className={`ship-card${shipment.status === "Отменена" ? " cancelled" : ""}`} aria-label={`Отправка ${shipment.number}`}>
    <div className="ship-card-head">
      <strong>{shipment.number}</strong><ShipStatus status={shipment.status} />
      <span>{shipment.shipping_point.trim() ? destination({ ...shipment, receiver: shipment.receiver || shipment.customer })
        : <><span className={["Новая", "Собрана"].includes(shipment.status) ? "muted" : "tone-danger"}>адрес не указан</span>{` · ${shipment.receiver || shipment.customer}`}</>}</span>
      {shipment.deadline && <span>срок <Due day={shipment.deadline} late={overdue(shipment)} since={shipment.created_at} /></span>}
      {shipment.pl_number && <span className="muted">{shipment.pl_number} · {shipment.dn_number}</span>}
      {shipment.places > 0 && <span className="muted">мест {shipment.places}{shipment.weight ? ` · ${shipment.weight} кг` : ""}</span>}
      {shipment.shipped_at ? <span className="muted">отгружена {formatDate(shipment.shipped_at)}</span> : shipment.packed_at && <span className="muted">упакована {formatDate(shipment.packed_at)}</span>}
      {withoutUpd(shipment) ? <span className="muted">Без УПД</span>
        : shipment.upd_url ? <a href={shipment.upd_url} target="_blank" rel="noreferrer">УПД{shipment.upd_number ? ` № ${shipment.upd_number}` : ""}</a> : <span className={needsUpdFile(shipment) || shipment.status === "Отгружена" ? "tone-danger" : "muted"} title={needsUpdFile(shipment) ? "Без файла не отгрузить" : "Файл понадобится, чтобы закрыть отправку"}>файла УПД нет</span>}
      {shipment.track_number && <span>трек <strong>{shipment.track_number}</strong></span>}
      {shipment.no_upd_approved_by && <span className="tone-warn" title="Отгрузку без УПД разрешил уполномоченный по ПИН">без УПД разрешил {shipment.no_upd_approved_by}{shipment.no_upd_approved_at ? `, ${formatDate(shipment.no_upd_approved_at)}` : ""}</span>}
      {shipment.note && <span className="ship-note" title="Комментарий отправки">{shipment.note}</span>}
      {shipment.status !== "Упакована" && active && shipment.pl_number && <DataChip shipment={shipment} ship />}
    </div>
    {shipment.status === "Упакована" && <div className="ship-card-ready"><Readiness source={shipment} gaps={shipGaps} withTrack /></div>}
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
      {canWarehouse && got && SHIPPABLE.includes(shipment.status) && <button type="button" className="primary" disabled={disabled || !canShip}
        title={canShip ? undefined : `Отгрузить можно, когда упаковано и есть УПД, ТК и трек. Не хватает: ${shipGaps.join(", ") || "данных"}`} onClick={ship}>Отгрузить</button>}
      {canWarehouse && onlyUpdBlocks(shipment) && <button type="button" className="secondary" disabled={disabled} onClick={() => setApproving(true)}>Разрешить без УПД</button>}
      {canWarehouse && shipment.status === "Отгружена" && <button type="button" className="primary" disabled={disabled} onClick={() => void action("close", `Отгрузка закрыта: ${shipment.number}`)}>Закрыть</button>}
      {canWarehouse && shipment.status === "Новая" && <button type="button" className="secondary" disabled={disabled} onClick={() => void action("pick", "Заявка набрана из доступного остатка")}>Набрать со склада</button>}
      {shipment.pl_number && got && <>{canPrintDocs && <><button type="button" className="secondary" onClick={() => void print("pl")}>Печать PL</button><button type="button" className="secondary" disabled={dnGaps.length > 0} title={dnGaps.length ? `DN печатается, когда внесены данные. Не хватает: ${dnGaps.join(", ")}` : undefined} onClick={() => void print("dn")}>Печать DN</button></>}<button type="button" className="secondary" onClick={() => void printLabels()}>Печать этикеток</button></>}
      {canEdit && got && (active || (shipment.pl_number && shipment.status === "Отгружена")) && !layout && <button type="button" className="secondary" disabled={disabled} onClick={() => setLayout(true)}>{shipment.pl_number ? "Перевыпустить" : "Сформировать отгрузочные"}</button>}
      {canEdit && open && <button type="button" className="secondary" disabled={disabled} onClick={openData}>Данные отправки</button>}
      {canEdit && open && !withoutUpd(shipment) && <label className="secondary file-button">Загрузить УПД<input type="file" accept=".pdf,image/*" aria-label={`Загрузить УПД ${shipment.number}`} hidden onChange={(event) => { uploadUpd(event.target.files?.[0]); event.target.value = ""; }} /></label>}
      <span className="ship-actions-end">
        {canUnship && shipped && rollback === null && <button type="button" className="danger small" disabled={disabled} onClick={() => setRollback("")}>Откатить отгрузку</button>}
        {canManage && active && <button type="button" className="danger small" disabled={disabled} onClick={() => { if (window.confirm(`Отменить заявку ${shipment.number}? Набранное вернётся на склад.`)) void action("cancel", "Заявка отменена, набранное вернулось на склад"); }}>Отменить заявку</button>}
      </span>
    </div>
    {canUnship && shipped && rollback !== null && <div className="ship-rollback" role="group" aria-label={`Откат отгрузки ${shipment.number}`}>
      <label className="ship-span2">Причина отката<input aria-label={`Причина отката ${shipment.number}`} placeholder="например: нажали «Отгрузить» по ошибке, груз на складе" value={rollback} onChange={(event) => setRollback(event.target.value)} /></label>
      <button type="button" className="danger" disabled={disabled || !rollback.trim()} onClick={unship}>Откатить в «Упакована»</button>
      <button type="button" className="secondary" onClick={() => setRollback(null)}>Не откатывать</button>
    </div>}
    {layout && <BoxesEditor shipment={shipment} busy={busy} mutate={mutate} onDone={() => setLayout(false)} />}
    {canEdit && open && <div className="ship-docs">
      <label>Тип УПД<select aria-label={`Тип УПД ${shipment.number}`} value={documents.upd_type} onChange={(event) => setDocuments({ ...documents, upd_type: event.target.value })}><option value="">{`из карточки проекта${shipment.inherited.includes("upd_type") && shipment.upd_type ? `: ${updLabel(shipment.upd_type)}` : ""}`}</option>{UPD_TYPES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
      <label>№ УПД<input value={documents.upd_number} onChange={(event) => setDocuments({ ...documents, upd_number: event.target.value })} /></label>
      <label>Дата УПД<input type="date" value={documents.upd_date} onChange={(event) => setDocuments({ ...documents, upd_date: event.target.value })} /></label>
      <label>Трек-номер<input aria-label={`Трек-номер ${shipment.number}`} disabled={!trackOpen} title={trackOpen ? undefined : "Трек вносится, когда есть УПД и ТК"} value={documents.track_number} onChange={(event) => setDocuments({ ...documents, track_number: event.target.value })} /></label>
      <button type="button" className="secondary" disabled={disabled} onClick={() => void mutate(`docs:${shipment.id}`, () => shippingApi.documents(shipment.id, { version: shipment.version, ...documents, upd_url: shipment.upd_url, upd_type: documents.upd_type || null, upd_date: documents.upd_date || null }), "Данные УПД и доставки сохранены")}>Сохранить документы</button>
    </div>}
    {canEdit && open && <details className="ship-more"><summary>Получатель и адрес этой отправки</summary><ShipmentHead shipment={shipment} busy={busy} mutate={mutate} /></details>}
    {approving && <NoUpdWindow shipment={shipment} close={() => setApproving(false)} mutate={mutate} />}
  </article>;
}

/** «Разрешить без УПД» (владелец 29.10): уполномоченный (Артемий, Зингер, Касаточкин) выбирает себя и вводит ПИН на
 *  экране кладовщика — разрешение на одну эту отправку. Неверный ПИН и блокировку объясняет сервер. */
export function NoUpdWindow({ shipment, close, mutate }: { shipment: DispatchShipment; close: () => void; mutate: Mutate }) {
  const [approvers, setApprovers] = useState<NoUpdApprover[] | null>(null);
  const [approver, setApprover] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    intakeApi.noUpdApprovers()
      .then((list) => { if (live) { setApprovers(list); if (list.length === 1) setApprover(list[0].id); } })
      .catch((reason) => { if (live) { setApprovers([]); setError(failureText(reason, "Не удалось загрузить список уполномоченных")); } });
    return () => { live = false; };
  }, []);
  const name = approvers?.find((item) => item.id === approver)?.name ?? "";
  async function submit() {
    if (busy || !approver || !pin.trim()) return;
    setBusy(true); setError("");
    // Неверный ПИН и блокировку показывает само окно словами сервера; успех — сообщение окна отправок и перезагрузка.
    let result: unknown;
    try { result = await intakeApi.approveNoUpd(shipment.id, { version: shipment.version, approver_id: approver, pin: pin.trim() }); }
    catch (reason) { setPin(""); setError(failureText(reason, "Разрешение не принято")); setBusy(false); return; }
    setBusy(false);
    close();
    await mutate(`noupd:${shipment.id}`, async () => result, `Отгрузка без УПД разрешена: ${shipment.number} — ${name}`);
  }
  return <PageWindow label={`Отгрузка без УПД · ${shipment.number}`} close={close} width={520}>
    <form className="panel intake-pin" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <header className="page-head"><h2>Отгрузка без УПД · {shipment.number}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <p className="muted">Разрешение на одну эту отправку. Уполномоченный выбирает себя и вводит свой ПИН.</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="form-grid intake-pin-grid">
        <label>Кто разрешает<select aria-label="Кто разрешает" value={approver} disabled={!approvers} onChange={(event) => setApprover(event.target.value)}>
          <option value="">{approvers ? "выберите" : "загрузка…"}</option>
          {(approvers ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>ПИН<input aria-label="ПИН" type="password" inputMode="numeric" autoComplete="off" maxLength={12} value={pin} onChange={(event) => setPin(event.target.value)} /></label>
      </div>
      <div className="intake-window-actions">
        <button type="submit" className="primary" disabled={busy || !approver || !pin.trim()}>{busy ? "Проверяем…" : "Разрешить отгрузку без УПД"}</button>
      </div>
    </form>
  </PageWindow>;
}

const HEAD_FIELDS: Array<[keyof Shipment & string, string]> = [
  ["receiver", "Получатель"], ["shipping_point", "Адрес отгрузки"], ["contact", "Контактное лицо"], ["phone", "Телефон"],
];

/** Шапка открытой отправки. Поле сохраняется при уходе из него; пустое поле адресата снова берётся из карточки проекта. */
function ShipmentHead({ shipment, busy, mutate }: { shipment: Shipment; busy: string; mutate: Mutate }) {
  const initial = (): Record<string, string> => ({
    ...Object.fromEntries(HEAD_FIELDS.map(([key]) => [key, String(shipment[key] ?? "")])),
    deadline: shipment.deadline ?? "", note: shipment.note,
  });
  const [draft, setDraft] = useState(initial);
  useEffect(() => setDraft(initial()), [shipment.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const inherited = (key: string) => shipment.inherited.includes(key);
  const save = (key: string) => {
    const value = draft[key].trim();
    const stored = key === "terms" ? shipment.terms ?? "" : key === "deadline" ? shipment.deadline ?? "" : String(shipment[key as keyof Shipment] ?? "");
    if (value === stored.trim() || (value === "" && inherited(key))) return;
    const sent = value === "" && (key === "terms" || key === "deadline") ? null : value;
    void mutate(`head:${shipment.id}:${key}`, () => shippingApi.head(shipment.id, { version: shipment.version, [key]: sent }), "Отправка сохранена");
  };
  // ТК выбирается из списка, условия выводятся из него и уходят в той же правке (владелец 22.10).
  const changeCarrier = (carrier: string) => {
    if (carrier === shipment.carrier.trim()) return;
    const terms = termsFor(carrier);
    void mutate(`head:${shipment.id}:carrier`, () => shippingApi.head(shipment.id, { version: shipment.version, carrier, ...(terms && terms !== shipment.terms ? { terms } : {}) }), "Отправка сохранена");
  };
  const pickup = isPickup(shipment.carrier);
  const mark = (key: string) => inherited(key) ? <small className="ship-inherited">из карточки проекта</small> : null;
  return <div className="ship-form">
    {HEAD_FIELDS.filter(([key]) => !(pickup && key === "shipping_point")).map(([key, label]) => <label key={key} className={key === "shipping_point" ? "ship-span2" : undefined}><span>{label}{mark(key)}</span><input aria-label={`${label} ${shipment.number}`} disabled={Boolean(busy)} placeholder={inherited(key) ? "" : "пусто — из карточки проекта"} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} onBlur={() => save(key)} /></label>)}
    <label><span>ТК{mark("carrier")}</span><CarrierSelect label={`ТК ${shipment.number}`} empty={inherited("carrier") ? "из карточки проекта" : "не выбрано"} disabled={Boolean(busy)} value={shipment.carrier} onChange={changeCarrier} /></label>
    <label><span>Условия{mark("terms")}</span><span className="ship-terms" aria-label={`Условия доставки ${shipment.number}`}>{termsText(shipment.terms) || "—"}</span></label>
    <label>Срок отгрузки<input type="date" aria-label={`Дедлайн ${shipment.number}`} disabled={Boolean(busy)} value={draft.deadline} onChange={(event) => setDraft({ ...draft, deadline: event.target.value })} onBlur={() => save("deadline")} /></label>
    <label className="ship-span2">Комментарий<AutoTextarea aria-label={`Комментарий ${shipment.number}`} disabled={Boolean(busy)} value={draft.note} onChange={(note) => setDraft({ ...draft, note })} onBlur={() => save("note")} /></label>
  </div>;
}

interface BoxDraft { kind: string; dims: string; net: string; gross: string; seal: string; q: Record<string, string> }

/** Сетка «КБ × BXN», характеристики каждого места и примечания для ТК: «Сформировать» — PL и DN, «Перевыпустить» — заново. */
export function BoxesEditor({ shipment, busy, mutate, onDone }: { shipment: Shipment; busy: string; mutate: Mutate; onDone: () => void }) {
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
