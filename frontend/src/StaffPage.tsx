import { useEffect, useMemo, useState } from "react";
import { staffApi, type Employee, type EmployeeCreate, type StaffDirectory } from "./api";
import { SuggestInput } from "./SuggestInput";

/** ID-8 «Сотрудники» (SUPPLYMAP Staff.gs portalStaffGet/portalStaffSave): the administrator creates and
 *  edits employees and keeps the department list. The PIN (6–8 digits) and the optional password are set here and
 *  never shown again; the list says whether a PIN is assigned (`pinSet`). Кадровые поля «Личной карточки»
 *  (табельный номер, должность, квалификация, «в должности с», руководитель) and the department heads live here too. */

/** `managerText` is what the manager field shows; `manager_id` is set only when it names someone from the list. */
type Draft = EmployeeCreate & { active: boolean; managerText: string };

const EMPTY_DRAFT: Draft = {
  login: "", email: "", name: "", department: "", role: "staff", site: "", password: "", pin: "",
  personnel_number: "", job_title: "", qualification: "", job_since: null, manager_id: null, active: true, managerText: "",
};
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
/** Tooltips that keep the two things apart: the access role drives permissions, the position is a personnel fact. */
const ROLE_HINT = "что сотрудник может открывать и править в ERP; должность — в блоке «Кадры»";
const SITE_HINT = "ЦАР / ЖУК — сотрудник цеха с площадкой считается монтажником";

/** «ФИО · отдел» for every employee; equal labels get the login, so a label always names one person. */
function personLabels(users: Employee[]): Map<string, string> {
  const base = users.map((user) => [user.id, user.department ? `${user.name} · ${user.department}` : user.name] as const);
  const count = new Map<string, number>();
  base.forEach(([, label]) => count.set(label, (count.get(label) ?? 0) + 1));
  return new Map(base.map(([id, label]) => {
    const login = users.find((user) => user.id === id)?.login ?? "";
    return [id, (count.get(label) ?? 0) > 1 ? `${label} · ${login}` : label];
  }));
}

/** Who can be chosen as the manager of `selfId`: switched-on people other than the employee and their own
 *  subordinates (direct or not), plus the manager already set. The server checks the same rule. */
function managerCandidates(users: Employee[], selfId: string | null, currentId: string | null): Employee[] {
  const managerOf = new Map(users.map((user) => [user.id, user.manager_id]));
  const reportsToSelf = (userId: string) => {
    const seen = new Set<string>();
    let current = managerOf.get(userId) ?? null;
    while (current && !seen.has(current)) {
      if (current === selfId) return true;
      seen.add(current);
      current = managerOf.get(current) ?? null;
    }
    return false;
  };
  return users.filter((user) => user.id === currentId || (user.active && user.id !== selfId && !(selfId && reportsToSelf(user.id))));
}

export function StaffPage({ openCard, openUserId }: {
  /** «Карточка» в строке: личная карточка сотрудника. */
  openCard?: (id: string) => void;
  /** Открыть форму правки этого сотрудника сразу (кнопка «Редактировать» на его карточке). */
  openUserId?: string | null;
} = {}) {
  const [data, setData] = useState<StaffDirectory | null>(null);
  const [editing, setEditing] = useState<{ id: string | null; draft: Draft } | null>(null);
  const [departments, setDepartments] = useState("");
  const [renameSource, setRenameSource] = useState("");
  const [renameTarget, setRenameTarget] = useState("");
  const [headDepartment, setHeadDepartment] = useState("");
  const [headText, setHeadText] = useState("");
  const [busy, setBusy] = useState(false);
  const [showOff, setShowOff] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const labels = useMemo(() => personLabels(data?.users ?? []), [data]);
  const heads = data?.department_heads ?? {};

  async function load() {
    const loaded = await staffApi.directory();
    setData(loaded);
    setDepartments(loaded.departments.join("\n"));
    return loaded;
  }

  useEffect(() => {
    load()
      .then((loaded) => {
        const user = openUserId ? loaded.users.find((item) => item.id === openUserId) : undefined;
        if (user) startEdit(user, loaded.users);
      })
      .catch((reason) => setError(errorText(reason, "Не удалось загрузить справочник сотрудников")));
  }, []);
  // The form sits under the table: a person sent here from the card must see it, not the top of a long list.
  useEffect(() => {
    if (openUserId && editing?.id === openUserId) document.getElementById("staff-edit-form")?.scrollIntoView?.({ block: "start" });
  }, [openUserId, editing?.id]);

  async function run(operation: () => Promise<string>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await operation());
    } catch (reason) {
      setError(errorText(reason, "Ошибка"));
    } finally {
      setBusy(false);
    }
  }

  const saveEmployee = () => run(async () => {
    if (!editing) return "";
    const { id, draft } = editing;
    if (draft.managerText.trim() && !draft.manager_id) throw new Error(`Руководитель «${draft.managerText.trim()}» не найден — выберите из списка`);
    const personnel = {
      personnel_number: draft.personnel_number, job_title: draft.job_title, qualification: draft.qualification,
      job_since: draft.job_since || null, manager_id: draft.manager_id,
    };
    if (id === null) {
      const { user, pin } = await staffApi.create({
        login: draft.login, email: draft.email, name: draft.name, department: draft.department, role: draft.role,
        site: draft.site, password: draft.password, pin: draft.pin, ...personnel,
      });
      setEditing(null);
      await load();
      return `Сотрудник ${user.name} заведён, вход — ${user.login}, PIN — ${pin} (запишите, больше не покажем)`;
    }
    const { user } = await staffApi.update(id, {
      name: draft.name, department: draft.department, role: draft.role, site: draft.site, active: draft.active,
      password: draft.password || null, pin: draft.pin.trim() || null, ...personnel,
    });
    setEditing(null);
    await load();
    const parts = [`Сохранено: ${user.name}`];
    if (draft.pin.trim()) parts.push("PIN назначен");
    if (draft.password) parts.push("пароль сменён — прежние входы закрыты");
    return parts.join(", ");
  });

  const remove = (user: Employee) => {
    if (!window.confirm(`Удалить сотрудника ${user.name}? Если за ним есть операции, выдачи или другие записи, он будет отключён и скрыт, история останется.`)) return;
    void run(async () => {
      const { deleted } = await staffApi.remove(user.id);
      if (editing?.id === user.id) setEditing(null);
      await load();
      return deleted ? `Сотрудник ${user.name} удалён` : `Сотрудник ${user.name} отключён и скрыт: за ним есть история работы`;
    });
  };

  const saveDepartments = () => run(async () => {
    const saved = await staffApi.saveDepartments(departments.split(/\r?\n/));
    setData((current) => current && { ...current, departments: saved.departments });
    setDepartments(saved.departments.join("\n"));
    return "Список отделов сохранён";
  });

  const rename = () => run(async () => {
    const result = await staffApi.renameDepartment(renameSource, renameTarget);
    setRenameSource("");
    setRenameTarget("");
    await load();
    return `Отдел переименован, сотрудников перенесено: ${result.users}`;
  });

  const headCandidates = (data?.users ?? []).filter((user) => user.active || user.id === heads[headDepartment]);
  const headIdByLabel = new Map(headCandidates.map((user) => [labels.get(user.id) ?? user.name, user.id]));

  function pickHeadDepartment(name: string) {
    setHeadDepartment(name);
    const head = heads[name];
    setHeadText(head ? labels.get(head) ?? "" : "");
  }

  const saveHead = () => run(async () => {
    const text = headText.trim();
    const headId = text ? headIdByLabel.get(text) ?? null : null;
    if (text && !headId) throw new Error(`Сотрудник «${text}» не найден — выберите из списка`);
    const department = headDepartment;
    const result = await staffApi.setDepartmentHead(department, headId);
    setData((current) => current && { ...current, departments: result.departments, department_heads: result.department_heads });
    const name = data?.users.find((user) => user.id === headId)?.name;
    return name ? `Руководитель отдела «${department}» — ${name}` : `У отдела «${department}» больше нет руководителя`;
  });

  /** `users` — the list just loaded, when the form opens before `data` reaches the render (sent from the card). */
  function startEdit(user: Employee, users?: Employee[]) {
    const { pin_set: _pinSet, ...fields } = user;
    const names = users ? personLabels(users) : labels;
    const managerText = user.manager_id ? names.get(user.manager_id) ?? "" : "";
    setEditing({ id: user.id, draft: { ...fields, password: "", pin: "", managerText } });
  }

  const roleLabel = (key: string) => data?.roles.find((role) => role.key === key)?.label ?? key;
  const nameOf = (id: string | undefined) => data?.users.find((user) => user.id === id)?.name ?? "";
  const offCount = data?.users.filter((user) => !user.active).length ?? 0;
  const shown = data?.users.filter((user) => showOff || user.active) ?? [];
  const headButton = headText.trim() || !heads[headDepartment] ? "Назначить руководителя" : "Снять руководителя";

  return <section className="page staff-page">
    <header className="page-head"><h1>Сотрудники</h1><span className="page-count">{shown.length}</span>
      {offCount > 0 && <label className="check-inline"><input type="checkbox" checked={showOff} onChange={(event) => setShowOff(event.target.checked)} /> Показать отключённых ({offCount})</label>}
      {data && !editing && <div className="page-actions"><button className="primary" onClick={() => setEditing({ id: null, draft: { ...EMPTY_DRAFT } })}>+ Сотрудник</button></div>}</header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && <>
      <div className="table-wrap"><table aria-label="Сотрудники">
        <thead><tr><th>ФИО</th><th>Таб. №</th><th>Должность</th><th>Логин / e-mail</th><th>Отдел</th><th>Площадка</th><th title={ROLE_HINT}>Доступ</th><th>Вход</th><th /></tr></thead>
        <tbody>
          {shown.map((user) => <tr key={user.id} className={user.active ? "clickable" : "clickable muted"} onClick={() => startEdit(user)}>
            <td className="text"><strong>{user.name}</strong></td>
            <td className="tab-no">{user.personnel_number || "—"}</td>
            <td className="text">{user.job_title || "—"}{user.qualification && <small>{user.qualification}</small>}</td>
            <td className="text">{user.login}{user.email !== user.login && <><br /><small>{user.email}</small></>}</td>
            <td>{user.department || "—"}</td>
            <td>{user.site || "все"}</td>
            <td>{roleLabel(user.role)}</td>
            <td>{user.active ? "активен" : "отключён"}</td>
            <td className="row-action-cell"><span className="row-actions">
              {openCard && <button className="secondary small row-action" aria-label={`Карточка ${user.name}`} onClick={(event) => { event.stopPropagation(); openCard(user.id); }}>Карточка</button>}
              <button className="secondary small row-action" aria-label={`Править ${user.name}`} onClick={(event) => { event.stopPropagation(); startEdit(user); }}>Править</button>
              <button className="secondary small row-action" aria-label={`Удалить ${user.name}`} disabled={busy} onClick={(event) => { event.stopPropagation(); remove(user); }}>Удалить</button>
            </span></td>
          </tr>)}
        </tbody>
      </table></div>
      {editing && <EmployeeForm data={data} labels={labels} editing={editing} busy={busy} onChange={(draft) => setEditing({ ...editing, draft })} onSave={() => void saveEmployee()} onCancel={() => setEditing(null)} />}
      <article className="panel staff-departments">
        <h2>Отделы</h2>
        <div className="staff-dep-body">
        <div className="staff-dep-list">
          <textarea aria-label="Список отделов" rows={Math.min(Math.max(data.departments.length + 1, 4), 14)} value={departments} onChange={(event) => setDepartments(event.target.value)} />
          <small className="muted">По одному на строку. Отдел, который у кого-то проставлен, из списка не уйдёт — переименуйте его.</small>
          <div><button className="primary" disabled={busy} onClick={() => void saveDepartments()}>Сохранить отделы</button></div>
        </div>
        <div className="staff-dep-heads">
          <div className="table-wrap"><table aria-label="Руководители отделов">
            <thead><tr><th>Отдел</th><th>Руководитель</th></tr></thead>
            <tbody>
              {data.departments.map((name) => <tr key={name} className={name === headDepartment ? "clickable selected" : "clickable"} onClick={() => pickHeadDepartment(name)}>
                <td>{name}</td><td>{nameOf(heads[name]) || "—"}</td>
              </tr>)}
              {data.departments.length === 0 && <tr><td colSpan={2} className="muted">Отделов пока нет — впишите их слева</td></tr>}
            </tbody>
          </table></div>
          <div className="staff-dep-rename">
            <select aria-label="Отдел для руководителя" value={headDepartment} onChange={(event) => pickHeadDepartment(event.target.value)}>
              <option value="">Отдел…</option>
              {data.departments.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <SuggestInput aria-label="Руководитель отдела" placeholder="ФИО руководителя" options={[...headIdByLabel.keys()]} value={headText} onChange={setHeadText} />
            <button className="secondary" disabled={busy || !headDepartment || (!headText.trim() && !heads[headDepartment])} onClick={() => void saveHead()}>{headButton}</button>
          </div>
        </div>
        <div className="staff-dep-rename">
          <select aria-label="Переименовать отдел" value={renameSource} onChange={(event) => setRenameSource(event.target.value)}>
            <option value="">Переименовать отдел…</option>
            {data.departments.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <input aria-label="Новое название отдела" placeholder="Новое название" value={renameTarget} onChange={(event) => setRenameTarget(event.target.value)} />
          <button className="secondary" disabled={busy || !renameSource || !renameTarget.trim()} onClick={() => void rename()}>Переименовать</button>
        </div>
        </div>
      </article>
    </>}
  </section>;
}

function EmployeeForm({ data, labels, editing, busy, onChange, onSave, onCancel }: {
  data: StaffDirectory;
  labels: Map<string, string>;
  editing: { id: string | null; draft: Draft };
  busy: boolean;
  onChange: (draft: Draft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { id, draft } = editing;
  const creating = id === null;
  const set = (field: keyof Draft, value: string | boolean | null) => onChange({ ...draft, [field]: value });
  // The department of this person stays selectable even when it is not in the list (a role-like old value).
  const departments = draft.department && !data.departments.includes(draft.department)
    ? [draft.department, ...data.departments]
    : data.departments;
  const managers = managerCandidates(data.users, id, draft.manager_id);
  const managerIdByLabel = new Map(managers.map((user) => [labels.get(user.id) ?? user.name, user.id]));
  const setManager = (text: string) => onChange({ ...draft, managerText: text, manager_id: managerIdByLabel.get(text.trim()) ?? null });
  return <div className="panel" id="staff-edit-form">
    <h2>{creating ? "Новый сотрудник" : `Правка: ${draft.name}`}</h2>
    <section className="staff-form-block" aria-labelledby="staff-form-hr">
      <h3 id="staff-form-hr">Кадры</h3>
      <div className="form-grid staff-form">
        <label className="pair">ФИО<input aria-label="ФИО" value={draft.name} onChange={(event) => set("name", event.target.value)} /></label>
        <label>Должность<input aria-label="Должность" value={draft.job_title} onChange={(event) => set("job_title", event.target.value)} /></label>
        <label>Категория / квалификация<input aria-label="Категория / квалификация" value={draft.qualification} onChange={(event) => set("qualification", event.target.value)} /></label>
        <label>Отдел<select aria-label="Отдел" value={draft.department} onChange={(event) => set("department", event.target.value)}>
          <option value="">—</option>
          {departments.map((name) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <label>Руководитель<SuggestInput aria-label="Руководитель" placeholder="ФИО, пусто — не назначен" options={[...managerIdByLabel.keys()]} value={draft.managerText} onChange={setManager} /></label>
        <label>Табельный номер<input aria-label="Табельный номер" placeholder="например, FRX-0017" maxLength={32} value={draft.personnel_number} onChange={(event) => set("personnel_number", event.target.value)} /></label>
        <label>В должности с<input aria-label="В должности с" type="date" value={draft.job_since ?? ""} onChange={(event) => set("job_since", event.target.value || null)} /></label>
      </div>
    </section>
    <section className="staff-form-block" aria-labelledby="staff-form-access">
      <h3 id="staff-form-access">Доступ в ERP</h3>
      <div className="form-grid staff-form">
        {creating && <>
          <label className="pair">E-mail<input aria-label="E-mail" type="email" value={draft.email} onChange={(event) => set("email", event.target.value)} /></label>
          <label>Логин<input aria-label="Логин" placeholder="пусто — e-mail" value={draft.login} onChange={(event) => set("login", event.target.value)} /></label>
        </>}
        <label title={ROLE_HINT}>Роль доступа<select aria-label="Роль доступа" value={draft.role} onChange={(event) => set("role", event.target.value)}>
          {data.roles.map((role) => <option key={role.key} value={role.key}>{role.label}</option>)}
        </select></label>
        <label title={SITE_HINT}>Площадка<select aria-label="Площадка" value={draft.site} onChange={(event) => set("site", event.target.value)}>
          <option value="">все площадки</option>
          {data.sites.map((site) => <option key={site.code} value={site.code}>{site.code} · {site.city}</option>)}
        </select></label>
        <label>{creating ? "Пароль" : "Новый пароль"}<input aria-label={creating ? "Пароль" : "Новый пароль"} type="password" autoComplete="new-password" placeholder={creating ? "необязательно, 8–128 символов" : "пусто — не менять"} value={draft.password} onChange={(event) => set("password", event.target.value)} /></label>
        <label className={creating ? "pair" : undefined}>{creating ? "PIN" : "Новый PIN"}<input aria-label={creating ? "PIN" : "Новый PIN"} inputMode="numeric" autoComplete="off" maxLength={8} placeholder={creating ? "6–8 цифр, пусто — сгенерируем и покажем" : "пусто — не менять"} value={draft.pin} onChange={(event) => set("pin", event.target.value)} /></label>
        {!creating && <label className="check-inline staff-form-check"><input aria-label="Вход разрешён" type="checkbox" checked={draft.active} onChange={(event) => set("active", event.target.checked)} /> Вход разрешён</label>}
      </div>
    </section>
    <div className="form-actions">
      <button className="primary" disabled={busy} onClick={onSave}>{creating ? "Завести сотрудника" : "Сохранить сотрудника"}</button>{" "}
      <button className="secondary" onClick={onCancel}>Отмена</button>
    </div>
  </div>;
}
