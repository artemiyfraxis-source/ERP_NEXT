import { useState, type FormEvent } from "react";
import { createTdIssue, failureText, updateTdIssue } from "./api";
import { PageWindow } from "./PageWindow";
import type { TdIssue, TdIssueInput, TdSignatories, TdSignatory } from "./tdTypes";

export const TD_LITERAS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "без литеры" },
  ...["П", "О", "О1", "О2", "О3", "О4", "О5", "А", "Б", "И"].map((value) => ({ value, label: value })),
];
export const TD_METHODS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "85", label: "85 — электромонтаж" },
  { value: "88", label: "88 — сборка" },
  { value: "80", label: "80 — пайка" },
];
const SIGNATORY_ROWS: ReadonlyArray<{ key: keyof TdSignatories; label: string }> = [
  { key: "developer", label: "Разработал" },
  { key: "checker", label: "Проверил" },
  { key: "normcontrol", label: "Н. контроль" },
  { key: "approver", label: "Утвердил" },
  { key: "agreed_vp", label: "Согласовано ВП" },
];
const EMPTY: TdSignatory = { name: "", position: "" };
const emptySignatories = (): TdSignatories => ({ developer: EMPTY, checker: EMPTY, normcontrol: EMPTY, approver: EMPTY, agreed_vp: EMPTY });

const sameName = (a: string, b: string) => a.trim() !== "" && a.trim().toLowerCase() === b.trim().toLowerCase();

/** Проверка формы выпуска: Н. контроль не совпадает с разработчиком и проверяющим; извещение и причина — не у первого выпуска. */
export function tdFormProblems(input: TdIssueInput, needsNotice: boolean): string[] {
  const problems: string[] = [];
  const { developer, checker, normcontrol } = input.signatories;
  if (sameName(normcontrol.name, developer.name) || sameName(normcontrol.name, checker.name)) {
    problems.push("Нормоконтролёр не может быть разработчиком или проверяющим этого комплекта: укажите другого сотрудника.");
  }
  if (needsNotice && !input.notice_no.trim()) problems.push("Укажите номер извещения об изменении.");
  if (needsNotice && !input.reason.trim()) problems.push("Укажите причину изменения.");
  return problems;
}

/** Окно выпуска комплекта ТД: новый выпуск (`issue` пуст) или правка проекта. `previous` — подписанты берутся из него. */
export function TdIssueWindow({ cardId, issue, previous, isFirst, close, onSaved }: {
  cardId: string; issue: TdIssue | null; previous: TdIssue | null; isFirst: boolean; close: () => void; onSaved: (issue: TdIssue) => void;
}) {
  const source = issue ?? previous;
  const needsNotice = issue ? issue.change_no > 0 : !isFirst;
  const [litera, setLitera] = useState(source?.litera ?? "");
  const [method, setMethod] = useState(source?.method_code ?? "85");
  const [signatories, setSignatories] = useState<TdSignatories>(source?.signatories ?? emptySignatories());
  const [noticeNo, setNoticeNo] = useState(issue?.notice_no ?? "");
  const [reason, setReason] = useState(issue?.reason ?? "");
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const setPerson = (key: keyof TdSignatories, field: keyof TdSignatory, value: string) =>
    setSignatories((current) => ({ ...current, [key]: { ...current[key], [field]: value } }));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const input: TdIssueInput = { litera, method_code: method, signatories, notice_no: needsNotice ? noticeNo.trim() : "", reason: needsNotice ? reason.trim() : "" };
    const found = tdFormProblems(input, needsNotice);
    setProblems(found);
    if (found.length) return;
    setBusy(true); setError("");
    try {
      onSaved(issue ? await updateTdIssue(issue.id, issue.version, input) : await createTdIssue(cardId, input));
      close();
    } catch (reason2) {
      setError(failureText(reason2, "Выпуск не сохранён"));
    } finally { setBusy(false); }
  }

  const title = issue ? `Правка выпуска ${issue.designations.vtd}` : needsNotice ? "Извещение об изменении" : "Новый выпуск комплекта ТД";
  return <PageWindow label={title} width={860} close={close}>
    <form className="panel td-form" onSubmit={(event) => void save(event)}>
      <div className="td-head"><h2>{title}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></div>
      <div className="td-form-top">
        <label>Литера
          <select value={litera} onChange={(event) => setLitera(event.target.value)}>
            {TD_LITERAS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select></label>
        <label>Вид ТП
          <select value={method} onChange={(event) => setMethod(event.target.value)}>
            {TD_METHODS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select></label>
        {needsNotice && <label>№ извещения
          <input value={noticeNo} onChange={(event) => setNoticeNo(event.target.value)} /></label>}
      </div>
      {needsNotice && <label className="td-reason">Причина изменения
        <textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)} /></label>}
      <table className="td-sign">
        <thead><tr><th>Подписант</th><th>Должность</th><th>ФИО</th></tr></thead>
        <tbody>{SIGNATORY_ROWS.map((row) => <tr key={row.key}>
          <th scope="row">{row.label}</th>
          <td><input aria-label={`${row.label}: должность`} value={signatories[row.key].position} onChange={(event) => setPerson(row.key, "position", event.target.value)} /></td>
          <td><input aria-label={`${row.label}: ФИО`} value={signatories[row.key].name} onChange={(event) => setPerson(row.key, "name", event.target.value)} /></td>
        </tr>)}</tbody>
      </table>
      {problems.length > 0 && <div className="error-banner" role="alert">{problems.map((text) => <div key={text}>{text}</div>)}</div>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="td-actions">
        <button type="submit" className="primary" disabled={busy}>{issue ? "Сохранить" : needsNotice ? "Оформить извещение" : "Создать выпуск"}</button>
        <button type="button" className="secondary" onClick={close}>Отмена</button>
      </div>
    </form>
  </PageWindow>;
}
