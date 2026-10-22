import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { ApiError, failureText, shippingApi, type ProjectAddressee } from "./api";
import { PageWindow } from "./PageWindow";
import { intakeApi, type DispatchData } from "./productionIntakeApi";
import "./dispatch.css";

/* «Данные отправки» проекта (владелец 14.10): «надо только получатель, адрес, телефон, вариант УПД; если УПД бумага —
   возможность приложить УПД». Окно в «Приёмке с производства» и тот же набор полей в «Отгрузке» (`DispatchFields`).
   Остальные поля карточки (контакт, условия, ТК, план, реквизиты) хранятся как были — эти формы их не шлют. */

const UPD: Array<[string, string]> = [["ЭДО", "ЭДО"], ["БУМАГА", "Бумага"], ["БЕЗ УПД", "Без УПД"]];
const CONFLICT = "Данные отправки уже изменил другой сотрудник. Закройте окно и откройте его заново.";

export type DispatchKey = "receiver" | "shipping_point" | "phone" | "upd_type";
export type DispatchDraft = Record<DispatchKey, string>;
const KEYS: DispatchKey[] = ["receiver", "shipping_point", "phone", "upd_type"];

export const dispatchDraft = (card?: Partial<ProjectAddressee> | Partial<DispatchData> | null): DispatchDraft => ({
  receiver: card?.receiver ?? "", shipping_point: card?.shipping_point ?? "", phone: card?.phone ?? "", upd_type: card?.upd_type ?? "",
});

/** Только изменённые поля из четырёх: остальное в карточке не трогается (PUT меняет присланное, пустое стирает). */
export function changedDispatch(draft: DispatchDraft, card?: Partial<ProjectAddressee> | Partial<DispatchData> | null): Record<string, string | null> {
  const before = dispatchDraft(card);
  return Object.fromEntries(KEYS.filter((key) => draft[key].trim() !== before[key])
    .map((key) => [key, key === "upd_type" ? draft[key] || null : draft[key].trim()]));
}

export function readFile(file: File) {
  return new Promise<{ name: string; type: string; base64: string }>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, type: file.type || "application/octet-stream", base64: String(reader.result ?? "").split(",")[1] ?? "" });
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

const FIELDS: Array<[Exclude<DispatchKey, "upd_type">, string, string]> = [
  ["receiver", "Получатель", "как в документах"], ["shipping_point", "Адрес доставки", "куда везём"], ["phone", "Телефон", ""],
];

/** Получатель, адрес, телефон, вариант УПД и — у бумажной — её файл. Файл прикладывается сразу (загрузка + правка
 *  карточки), поля сохраняет тот, кто встроил форму. `suffix` — к подписям для чтения с экрана, когда форм несколько. */
export function DispatchFields({ project, draft, onChange, editable, card, onCardSaved, suffix = "", hideAddress = false }: {
  project: string; draft: DispatchDraft; onChange: (draft: DispatchDraft) => void; editable: boolean;
  card: Partial<ProjectAddressee> | Partial<DispatchData> | null | undefined;
  onCardSaved: (card: ProjectAddressee, message: string) => void; suffix?: string;
  /** Самовывоз (22.10): адреса доставки нет — поле не показывается, в карточке остаётся как было. */
  hideAddress?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const url = card?.upd_url ?? "";
  const fileName = card?.upd_file_name || "файл";

  async function saveFile(nextUrl: string, message: string, file?: File) {
    if (busy) return;
    setBusy(true); setProblem("");
    try {
      let link = nextUrl;
      if (file) link = (await shippingApi.uploadCardUpd(project, await readFile(file))).file.url;
      const { addressee } = await shippingApi.saveAddressee({
        project, version: card?.version ?? 0, upd_url: link, ...(link ? { upd_type: "БУМАГА" } : {}),
      });
      onCardSaved(addressee, message);
    } catch (reason) {
      setProblem(reason instanceof ApiError && reason.status === 409 ? CONFLICT : failureText(reason, "Файл УПД не сохранён"));
    } finally { setBusy(false); }
  }

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void saveFile("", "УПД приложена", file);
  };
  const fileInput = <input ref={input} type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" hidden
    aria-label={`Приложить УПД${suffix}`} disabled={busy} onChange={pick} />;

  return <>
    <div className={hideAddress ? "form-grid dispatch-grid dispatch-grid-3" : "form-grid dispatch-grid"}>
      {FIELDS.filter(([key]) => !(hideAddress && key === "shipping_point")).map(([key, label, placeholder]) => <label key={key}>{label}
        <input aria-label={`${label}${suffix}`} value={draft[key]} disabled={!editable} placeholder={editable ? placeholder : ""}
          inputMode={key === "phone" ? "tel" : undefined} onChange={(event) => onChange({ ...draft, [key]: event.target.value })} /></label>)}
      <label>УПД<select aria-label={`УПД${suffix}`} value={draft.upd_type} disabled={!editable} onChange={(event) => onChange({ ...draft, upd_type: event.target.value })}>
        <option value="">не выбрано</option>{UPD.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    </div>
    {draft.upd_type === "БУМАГА" && <div className="dispatch-upd">
      {url
        ? <>УПД: <a href={url} target="_blank" rel="noreferrer">{fileName}</a></>
        : <span className={editable ? "muted" : "tone-warn"}>Файл бумажной УПД не приложен</span>}
      {editable && <>
        {fileInput}
        <button type="button" className={url ? "link-button" : "secondary small"} disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Загружаем…" : url ? "Заменить" : "Приложить УПД"}</button>
        {url && <button type="button" className="link-button danger-link" disabled={busy} onClick={() => void saveFile("", "УПД убрана")}>Убрать</button>}
      </>}
      {problem && <span className="tone-danger" role="alert">{problem}</span>}
    </div>}
  </>;
}

export function DispatchCard({ project, customer, canEdit, close, onSaved }: {
  project: string; customer?: string; canEdit: boolean; close: () => void; onSaved?: () => void;
}) {
  const [data, setData] = useState<DispatchData | null>(null);
  const [draft, setDraft] = useState<DispatchDraft>(dispatchDraft(null));
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    intakeApi.dispatch(project)
      .then((result) => { if (live) { setData(result.addressee); setDraft(dispatchDraft(result.addressee)); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Не удалось загрузить данные отправки")); })
      .finally(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [project]);

  async function save() {
    if (busy) return;
    setBusy(true); setError(""); setSaved("");
    try {
      const result = await intakeApi.saveDispatch({ project, version: data?.version ?? 0, ...changedDispatch(draft, data) });
      setData(result.addressee); setDraft(dispatchDraft(result.addressee)); setSaved("Данные отправки сохранены");
      onSaved?.();
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409 ? CONFLICT : failureText(reason, "Данные отправки не сохранены"));
    } finally { setBusy(false); }
  }

  // Файл УПД сохранён сразу: новая версия карточки, набранное в полях остаётся.
  const fileSaved = (card: ProjectAddressee, message: string) => {
    setData(card as DispatchData); setDraft((current) => ({ ...current, upd_type: card.upd_type ?? current.upd_type })); setSaved(message);
    onSaved?.();
  };

  return <PageWindow label={`Данные отправки ${project}`} close={close} width={900}>
    <section className="panel dispatch-card">
      <header className="page-head"><h2>Данные отправки · {project}{customer ? ` · ${customer}` : ""}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {saved && <div className="success-banner" role="status">{saved}</div>}
      {!loaded ? <div className="empty">Загрузка…</div> : <>
        <DispatchFields project={project} draft={draft} onChange={(next) => { setDraft(next); setSaved(""); }} editable={canEdit}
          card={data} onCardSaved={fileSaved} />
        <div className="dispatch-actions">
          {canEdit
            ? <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняем…" : "Сохранить"}</button>
            : <span className="muted">Только просмотр</span>}
        </div>
      </>}
    </section>
  </PageWindow>;
}
