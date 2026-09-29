import { useEffect, useState } from "react";
import { staffApi, type Employee, type EmployeeCreate, type StaffDirectory } from "./api";

/** ID-8 «Сотрудники» (SUPPLYMAP Staff.gs portalStaffGet/portalStaffSave): the administrator creates and
 *  edits employees and keeps the department list. The PIN (6–8 digits) and the optional password are set here and
 *  never shown again; the list says whether a PIN is assigned (`pinSet`). */

type Draft = EmployeeCreate & { active: boolean };

const EMPTY_DRAFT: Draft = { login: "", email: "", name: "", department: "", role: "staff", site: "", password: "", pin: "", active: true };
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

export function StaffPage() {
  const [data, setData] = useState<StaffDirectory | null>(null);
  const [editing, setEditing] = useState<{ id: string | null; draft: Draft } | null>(null);
  const [departments, setDepartments] = useState("");
  const [renameSource, setRenameSource] = useState("");
  const [renameTarget, setRenameTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const loaded = await staffApi.directory();
    setData(loaded);
    setDepartments(loaded.departments.join("\n"));
    return loaded;
  }

  useEffect(() => {
    load().catch((reason) => setError(errorText(reason, "Не удалось загрузить справочник сотрудников")));
  }, []);

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
    if (id === null) {
      const { active: _active, ...payload } = draft;
      const { user, pin } = await staffApi.create(payload);
      setEditing(null);
      await load();
      return `Сотрудник ${user.name} заведён, вход — ${user.login}, PIN — ${pin} (запишите, больше не покажем)`;
    }
    const { user } = await staffApi.update(id, {
      name: draft.name, department: draft.department, role: draft.role, site: draft.site, active: draft.active,
      password: draft.password || null, pin: draft.pin.trim() || null,
    });
    setEditing(null);
    await load();
    const parts = [`Сохранено: ${user.name}`];
    if (draft.pin.trim()) parts.push("PIN назначен");
    if (draft.password) parts.push("пароль сменён — прежние входы закрыты");
    return parts.join(", ");
  });

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

  function startEdit(user: Employee) {
    const { pin_set: _pinSet, ...fields } = user;
    setEditing({ id: user.id, draft: { ...fields, password: "", pin: "" } });
  }

  const roleLabel = (key: string) => data?.roles.find((role) => role.key === key)?.label ?? key;

  return <section className="page staff-page">
    <header className="page-head"><h1>Сотрудники</h1><span className="page-count">{data?.users.length ?? 0}</span></header>
    <p className="page-sub">
      <b>Роль</b> — что сотруднику открыто, <b>отдел</b> — где он работает; одно из другого не выводится.
      Отключённый сотрудник не может войти, его история остаётся. Должен остаться хотя бы один администратор.
    </p>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && <>
      <div className="table-wrap"><table aria-label="Сотрудники">
        <thead><tr><th>ФИО</th><th>Логин / e-mail</th><th>Отдел</th><th>Место работы</th><th>Роль</th><th>PIN</th><th>Вход</th><th /></tr></thead>
        <tbody>
          {data.users.map((user) => <tr key={user.id} className={user.active ? undefined : "muted"}>
            <td className="text"><strong>{user.name}</strong></td>
            <td className="text">{user.login}{user.email !== user.login && <><br /><small>{user.email}</small></>}</td>
            <td>{user.department || "—"}</td>
            <td>{user.site || "все"}</td>
            <td>{roleLabel(user.role)}</td>
            <td>{user.pin_set ? "✓ назначен" : "✗ нет"}</td>
            <td>{user.active ? "активен" : "отключён"}</td>
            <td><button className="secondary small" aria-label={`Править ${user.name}`} onClick={() => startEdit(user)}>Править</button></td>
          </tr>)}
        </tbody>
      </table></div>
      {editing
        ? <EmployeeForm data={data} editing={editing} busy={busy} onChange={(draft) => setEditing({ ...editing, draft })} onSave={() => void saveEmployee()} onCancel={() => setEditing(null)} />
        : <div className="form-actions"><button className="secondary" onClick={() => setEditing({ id: null, draft: { ...EMPTY_DRAFT } })}>+ Сотрудник</button></div>}
      <article className="panel">
        <h2>Отделы (список для выпадашки)</h2>
        <p className="page-sub">По одному на строку. Отдел, который у кого-то проставлен, из списка не уйдёт — переименуйте его. Названия ролей отделом не становятся.</p>
        <textarea aria-label="Список отделов" rows={Math.min(Math.max(data.departments.length + 1, 4), 14)} value={departments} onChange={(event) => setDepartments(event.target.value)} />
        <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void saveDepartments()}>Сохранить отделы</button></div>
        <div className="filters">
          <select aria-label="Переименовать отдел" value={renameSource} onChange={(event) => setRenameSource(event.target.value)}>
            <option value="">Переименовать отдел…</option>
            {data.departments.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <input aria-label="Новое название отдела" placeholder="Новое название" value={renameTarget} onChange={(event) => setRenameTarget(event.target.value)} />
          <button className="secondary" disabled={busy || !renameSource || !renameTarget.trim()} onClick={() => void rename()}>Переименовать</button>
        </div>
      </article>
    </>}
  </section>;
}

function EmployeeForm({ data, editing, busy, onChange, onSave, onCancel }: {
  data: StaffDirectory;
  editing: { id: string | null; draft: Draft };
  busy: boolean;
  onChange: (draft: Draft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { id, draft } = editing;
  const creating = id === null;
  const set = (field: keyof Draft, value: string | boolean) => onChange({ ...draft, [field]: value });
  // The department of this person stays selectable even when it is not in the list (a role-like old value).
  const departments = draft.department && !data.departments.includes(draft.department)
    ? [draft.department, ...data.departments]
    : data.departments;
  return <div className="panel">
    <h2>{creating ? "Новый сотрудник" : `Правка: ${draft.name}`}</h2>
    <div className="form-grid">
      {creating && <>
        <label>E-mail<input aria-label="E-mail" type="email" value={draft.email} onChange={(event) => set("email", event.target.value)} /></label>
        <label>Логин<input aria-label="Логин" placeholder="пусто — e-mail" value={draft.login} onChange={(event) => set("login", event.target.value)} /></label>
      </>}
      <label>ФИО<input aria-label="ФИО" value={draft.name} onChange={(event) => set("name", event.target.value)} /></label>
      <label>Отдел<select aria-label="Отдел" value={draft.department} onChange={(event) => set("department", event.target.value)}>
        <option value="">—</option>
        {departments.map((name) => <option key={name} value={name}>{name}</option>)}
      </select></label>
      <label>Место работы<select aria-label="Место работы" value={draft.site} onChange={(event) => set("site", event.target.value)}>
        <option value="">все площадки</option>
        {data.sites.map((site) => <option key={site.code} value={site.code}>{site.code} · {site.city}</option>)}
      </select></label>
      <label>Роль<select aria-label="Роль" value={draft.role} onChange={(event) => set("role", event.target.value)}>
        {data.roles.map((role) => <option key={role.key} value={role.key}>{role.label}</option>)}
      </select></label>
      <label>{creating ? "PIN" : "Новый PIN"}<input aria-label={creating ? "PIN" : "Новый PIN"} inputMode="numeric" autoComplete="off" maxLength={8} placeholder={creating ? "6–8 цифр, пусто — сгенерируем и покажем" : "пусто — не менять"} value={draft.pin} onChange={(event) => set("pin", event.target.value)} /></label>
      <label>{creating ? "Пароль" : "Новый пароль"}<input aria-label={creating ? "Пароль" : "Новый пароль"} type="password" autoComplete="new-password" placeholder={creating ? "необязательно, 8–128 символов" : "пусто — не менять"} value={draft.password} onChange={(event) => set("password", event.target.value)} /></label>
      {!creating && <label className="check-inline"><input aria-label="Вход разрешён" type="checkbox" checked={draft.active} onChange={(event) => set("active", event.target.checked)} /> Вход разрешён</label>}
    </div>
    <div className="form-actions">
      <button className="primary" disabled={busy} onClick={onSave}>{creating ? "Завести сотрудника" : "Сохранить сотрудника"}</button>{" "}
      <button className="secondary" onClick={onCancel}>Отмена</button>
    </div>
  </div>;
}
