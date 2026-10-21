import { useEffect, useState } from "react";
import { failureText, identityApi, internalProjectsApi, requestsApi, type InternalProject, type LoginStaff } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { refreshInternalProjects } from "./internalProjects";
import { purposeLabel } from "./purposeLabels";

/* Stage option `projects.internal`: the «Внутренние» tab of «Проекты». A project of the company itself — number «ВП-001»,
   name, purpose (one of the request purposes), responsible, open / closed. Requests, procurement and stock go under it
   through the same `project_reference` as a customer project. */

const FALLBACK_PURPOSES = ["RND", "FRX", "Хоз деятельность", "Обеспечение производства", "Ремонт и обслуживание", "Склад", "Расходники"];

/** «Строк закупки по этапам»: «В работе 2 · Жду счет 1»; «—» when nothing hangs on the project. */
export function stagesText(lines: Record<string, number>): string {
  const parts = Object.entries(lines).map(([stage, count]) => `${stage} ${count}`);
  return parts.length ? parts.join(" · ") : "—";
}

function CreateForm({ purposes, staff, onDone, onCancel }: {
  purposes: string[]; staff: LoginStaff[]; onDone: (created: InternalProject) => void; onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [responsible, setResponsible] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) { setProblem("Название обязательно"); return; }
    if (!purpose) { setProblem("Выберите назначение"); return; }
    setBusy(true);
    setProblem("");
    try {
      onDone(await internalProjectsApi.create({
        name: name.trim(), purpose, responsible_id: responsible || null, note: note.trim() || null,
      }));
    } catch (reason) {
      setProblem(failureText(reason, "Проект не создан"));
    } finally {
      setBusy(false);
    }
  }

  return <div className="panel dialog-form" role="group" aria-label="Новый внутренний проект">
    {problem && <div className="error-banner" role="alert">{problem}</div>}
    <div className="move-grid two">
      <label>Название *<input aria-label="Название" value={name} onChange={(event) => setName(event.target.value)} maxLength={255} /></label>
      <label>Назначение *
        <select aria-label="Назначение" value={purpose} onChange={(event) => setPurpose(event.target.value)}>
          <option value="">не выбрано</option>
          {purposes.map((item) => <option key={item} value={item}>{purposeLabel(item)}</option>)}
        </select>
      </label>
      {staff.length > 0 && <label>Ответственный
        <select aria-label="Ответственный" value={responsible} onChange={(event) => setResponsible(event.target.value)}>
          <option value="">не назначен</option>
          {staff.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
        </select>
      </label>}
      <label>Заметка<input aria-label="Заметка" value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} /></label>
    </div>
    <div className="move-actions">
      <button className="secondary" onClick={onCancel}>Отмена</button>
      <button className="primary" disabled={busy} onClick={() => void submit()}>Создать проект</button>
    </div>
  </div>;
}

export function InternalProjectsTab({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<InternalProject[] | null>(null);
  const [purposes, setPurposes] = useState<string[]>(FALLBACK_PURPOSES);
  const [staff, setStaff] = useState<LoginStaff[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  async function load() {
    try {
      setRows((await internalProjectsApi.list()).projects);
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить внутренние проекты"));
    }
  }

  useEffect(() => {
    void load();
    requestsApi.schema().then((schema) => { if (schema.purposes?.length) setPurposes(schema.purposes); }).catch(() => undefined);
    identityApi.loginStaff().then((result) => setStaff(result.staff)).catch(() => undefined);
  }, []);

  async function toggle(project: InternalProject) {
    setBusy(project.id);
    setError("");
    setNotice("");
    try {
      if (project.closed) await internalProjectsApi.reopen(project.id);
      else await internalProjectsApi.close(project.id);
      await load();
      void refreshInternalProjects();
    } catch (reason) {
      setError(failureText(reason, "Статус проекта не изменился"));
    } finally {
      setBusy("");
    }
  }

  const columns: DataColumn<InternalProject>[] = [
    { key: "number", title: "№", value: (row) => row.number, render: (row) => <strong>{row.number}</strong>, width: 90 },
    { key: "name", title: "Название", value: (row) => row.name, wrap: true, render: (row) => <>{row.name}{row.note && <small>{row.note}</small>}</> },
    { key: "purpose", title: "Назначение", value: (row) => purposeLabel(row.purpose), filter: true },
    { key: "responsible", title: "Ответственный", value: (row) => row.responsible ?? "", filter: true, render: (row) => row.responsible || "—" },
    { key: "lines", title: "Строк закупки по этапам", value: (row) => stagesText(row.lines), wrap: true },
    { key: "open_requests", title: "Открытых заявок", value: (row) => row.open_requests, numeric: true },
    {
      key: "status", title: "Статус", value: (row) => (row.closed ? "Закрыт" : "Открыт"), filter: true,
      render: (row) => <>
        <span className="status">{row.closed ? "Закрыт" : "Открыт"}</span>
        {canEdit && <> <button className="link-button" disabled={busy === row.id} onClick={() => void toggle(row)}>
          {row.closed ? "Открыть снова" : "Закрыть"}
        </button></>}
      </>,
    },
  ];

  return <section className="page projects-page" aria-label="Внутренние проекты">
    <header className="page-head"><h1>Внутренние проекты</h1>{rows && <span className="page-count">{rows.length}</span>}
      {canEdit && <div className="page-actions"><button className="primary" onClick={() => setCreating(true)}>+ Внутренний проект</button></div>}</header>
    {creating && <CreateForm purposes={purposes} staff={staff} onCancel={() => setCreating(false)} onDone={(created) => {
      setCreating(false);
      setNotice(`Создан проект ${created.number} · ${created.name}`);
      void load();
      void refreshInternalProjects();
    }} />}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {rows === null && !error && <div className="empty">Загрузка…</div>}
    {rows && !rows.length && <div className="empty">Внутренних проектов пока нет{canEdit ? " — создайте первый." : "."}</div>}
    {rows && rows.length > 0 && <DataTable label="Внутренние проекты" rows={rows} columns={columns} rowKey={(row) => row.id} storageKey="internalProjects" />}
  </section>;
}
