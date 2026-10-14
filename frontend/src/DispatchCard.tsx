import { useEffect, useState } from "react";
import { ApiError, failureText } from "./api";
import { PageWindow } from "./PageWindow";
import { intakeApi, type DispatchData } from "./productionIntakeApi";

/* «Данные отправки» проекта (владелец 14.10): пока проект в работе и в отгрузках, Алексей и Ульяна вносят, куда и как
   его отправлять. Один и тот же адресат проекта, что и в «Отгрузке»; плановая дата и комментарий — к нему же. */

const TERMS: Array<[string, string]> = [["ДОСТАВКА ТК", "Доставка ТК"], ["САМОВЫВОЗ", "Самовывоз"]];
const UPD: Array<[string, string]> = [["ЭДО", "ЭДО"], ["БУМАГА", "Бумага"], ["БЕЗ УПД", "Без УПД"]];

interface Draft { receiver: string; shipping_point: string; contact: string; phone: string; terms: string; carrier: string; upd_type: string; plan_date: string; note: string }

const toDraft = (data: DispatchData | null): Draft => ({
  receiver: data?.receiver ?? "", shipping_point: data?.shipping_point ?? "", contact: data?.contact ?? "", phone: data?.phone ?? "",
  terms: data?.terms ?? "", carrier: data?.carrier ?? "", upd_type: data?.upd_type ?? "", plan_date: data?.plan_date ?? "", note: data?.note ?? "",
});

export function DispatchCard({ project, customer, canEdit, close, onSaved }: {
  project: string; customer?: string; canEdit: boolean; close: () => void; onSaved?: () => void;
}) {
  const [data, setData] = useState<DispatchData | null>(null);
  const [draft, setDraft] = useState<Draft>(toDraft(null));
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    intakeApi.dispatch(project)
      .then((result) => { if (live) { setData(result.addressee); setDraft(toDraft(result.addressee)); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Не удалось загрузить данные отправки")); })
      .finally(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [project]);

  const set = (key: keyof Draft) => (event: { target: { value: string } }) => { setDraft((current) => ({ ...current, [key]: event.target.value })); setSaved(false); };

  async function save() {
    if (busy) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      const result = await intakeApi.saveDispatch({
        project, version: data?.version ?? 0,
        receiver: draft.receiver, shipping_point: draft.shipping_point, contact: draft.contact, phone: draft.phone, carrier: draft.carrier,
        ...(draft.terms ? { terms: draft.terms } : {}), ...(draft.upd_type ? { upd_type: draft.upd_type } : {}),
        plan_date: draft.plan_date || null, note: draft.note,
      });
      setData(result.addressee); setDraft(toDraft(result.addressee)); setSaved(true);
      onSaved?.();
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409
        ? "Данные отправки уже изменил другой сотрудник. Закройте окно и откройте его заново."
        : failureText(reason, "Данные отправки не сохранены"));
    } finally { setBusy(false); }
  }

  const dis = !canEdit || busy;
  return <PageWindow label={`Данные отправки ${project}`} close={close} width={900}>
    <section className="panel dispatch-card">
      <header className="page-head"><h2>Данные отправки · {project}{customer ? ` · ${customer}` : ""}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {saved && <div className="success-banner" role="status">Данные отправки сохранены</div>}
      {!loaded ? <div className="empty">Загрузка…</div> : <>
        <div className="form-grid dispatch-grid">
          <label>Получатель<input value={draft.receiver} disabled={dis} onChange={set("receiver")} /></label>
          <label className="half">Адрес доставки<input value={draft.shipping_point} disabled={dis} onChange={set("shipping_point")} /></label>
          <label>Контактное лицо<input value={draft.contact} disabled={dis} onChange={set("contact")} /></label>
          <label>Телефон<input value={draft.phone} disabled={dis} onChange={set("phone")} /></label>
          <label>Условия доставки<select value={draft.terms} disabled={dis} onChange={set("terms")}>
            <option value="">не выбрано</option>{TERMS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Способ отгрузки / ТК<input value={draft.carrier} disabled={dis} onChange={set("carrier")} /></label>
          <label>УПД<select value={draft.upd_type} disabled={dis} onChange={set("upd_type")}>
            <option value="">не выбрано</option>{UPD.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Плановая дата отгрузки<input type="date" value={draft.plan_date} disabled={dis} onChange={set("plan_date")} /></label>
          <label className="wide">Комментарий<input value={draft.note} disabled={dis} onChange={set("note")} /></label>
        </div>
        <div className="dispatch-actions">
          {canEdit
            ? <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняем…" : "Сохранить"}</button>
            : <span className="muted">Только просмотр</span>}
        </div>
      </>}
    </section>
  </PageWindow>;
}
