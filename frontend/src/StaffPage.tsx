import { useEffect, useMemo, useState } from "react";
import { identityApi, staffApi, type Employee, type EmployeeCreate, type StaffDirectory } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { RowActionMenu } from "./RowActionMenu";
import { StatusChip } from "./StatusChip";
import { SuggestInput } from "./SuggestInput";

/** ID-8 «Сотрудники» (SUPPLYMAP Staff.gs portalStaffGet/portalStaffSave): the administrator creates and
 *  edits employees and keeps the department list. The PIN (6–8 digits) and the optional password are set here and
 *  never shown again; the list says whether a PIN is assigned (`pinSet`). Кадровые поля «Личной карточки»
 *  (табельный номер, должность, квалификация, «в должности с», руководитель) and the department heads live here too.
 *  Доступ — уровень (owner 04.10.2026) плюс два флага человека, «Монтажник» и «Мастер площадки»; the levels
 *  «Суперадминистратор» и «Администратор» are given and taken only by a superadministrator. */

/** `managerText` is what the manager field shows; `manager_id` is set only when it names someone from the list. */
type Draft = EmployeeCreate & { active: boolean; managerText: string };

const EMPTY_DRAFT: Draft = {
  login: "", email: "", name: "", department: "", access_level: "operator", site: "", password: "", pin: "",
  shop_worker: false, site_master: false,
  personnel_number: "", job_title: "", qualification: "", job_since: null, manager_id: null, active: true, managerText: "",
};
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
/** Tooltips that keep the two things apart: the access level drives permissions, the position is a personnel fact. */
const LEVEL_HINT = "что сотрудник может открывать и править в ERP; должность — в блоке «Кадры»";
const LEVEL_LOCKED_HINT = "Уровни «Суперадминистратор» и «Администратор» выдаёт и снимает только суперадминистратор";
const SITE_HINT = "ЦАР / ЖУК — площадка, на которой работает сотрудник; пусто — все площадки";
const SHOP_WORKER_HINT = "Исполнитель операций в цеху: ему назначают задания производства";
const SITE_MASTER_HINT = "Руководит монтажниками площадки: они видны в его личной карточке";

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
  // Whose level the session holds: only a superadministrator may give or take the two full levels. Unknown = not.
  const [actorLevel, setActorLevel] = useState("");

  const labels = useMemo(() => personLabels(data?.users ?? []), [data]);
  const heads = data?.department_heads ?? {};

  async function load() {
    const loaded = await staffApi.directory();
    setData(loaded);
    setDepartments(loaded.departments.join("\n"));
    return loaded;
  }

  useEffect(() => {
    identityApi.me().then((principal) => setActorLevel(principal.access_level ?? "")).catch(() => setActorLevel(""));
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
        login: draft.login, email: draft.email, name: draft.name, department: draft.department,
        access_level: draft.access_level, site: draft.site, password: draft.password, pin: draft.pin,
        shop_worker: draft.shop_worker, site_master: draft.site_master, ...personnel,
      });
      setEditing(null);
      await load();
      return `Сотрудник ${user.name} заведён, вход — ${user.login}, PIN — ${pin} (запишите, больше не покажем)`;
    }
    // A switch the owner did not touch is not sent: the server keeps the stored flag (KEEP), so one that still follows
    // the old role rule moves with a new site or department.
    const stored = data?.users.find((item) => item.id === id);
    const flags = {
      ...(draft.shop_worker !== stored?.shop_worker ? { shop_worker: draft.shop_worker } : {}),
      ...(draft.site_master !== stored?.site_master ? { site_master: draft.site_master } : {}),
    };
    const { user } = await staffApi.update(id, {
      name: draft.name, department: draft.department, access_level: draft.access_level, site: draft.site,
      active: draft.active, password: draft.password || null, pin: draft.pin.trim() || null, ...flags, ...personnel,
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
    const { pin_set: _pinSet, role: _role, access_level_label: _levelLabel, ...fields } = user;
    const names = users ? personLabels(users) : labels;
    const managerText = user.manager_id ? names.get(user.manager_id) ?? "" : "";
    setEditing({ id: user.id, draft: { ...fields, password: "", pin: "", managerText } });
  }

  const levelLabel = (user: Employee) => data?.levels.find((level) => level.key === user.access_level)?.label ?? user.access_level_label ?? user.access_level;
  const nameOf = (id: string | undefined) => data?.users.find((user) => user.id === id)?.name ?? "";
  const offCount = data?.users.filter((user) => !user.active).length ?? 0;
  const shown = data?.users.filter((user) => showOff || user.active) ?? [];
  const columns: DataColumn<Employee>[] = [
    { key: "name", title: "ФИО", value: (user) => user.name, render: (user) => <strong>{user.name}</strong> },
    { key: "tab", title: "Таб. №", value: (user) => user.personnel_number, render: (user) => <span className="tab-no">{user.personnel_number || "—"}</span> },
    {
      key: "job", title: "Должность", value: (user) => `${user.job_title} ${user.qualification}`.trim(),
      render: (user) => <>{user.job_title || "—"}{user.qualification && <small>{user.qualification}</small>}</>,
    },
    {
      key: "login", title: "Логин / e-mail", value: (user) => `${user.login} ${user.email}`,
      render: (user) => <>{user.login}{user.email !== user.login && <small>{user.email}</small>}</>,
    },
    { key: "department", title: "Отдел", value: (user) => user.department, filter: true },
    { key: "site", title: "Площадка", value: (user) => user.site || "все", filter: true },
    { key: "level", title: "Доступ", hint: LEVEL_HINT, value: (user) => levelLabel(user), filter: true },
    {
      key: "active", title: "Вход", value: (user) => user.active ? "активен" : "отключён", filter: true,
      render: (user) => <StatusChip tone={user.active ? "success" : "neutral"}>{user.active ? "активен" : "отключён"}</StatusChip>,
    },
    {
      key: "actions", title: "", width: 44, value: () => "",
      render: (user) => <RowActionMenu label={`Действия: ${user.name}`} disabled={busy} items={[
        ...(openCard ? [{ label: "Открыть карточку", run: () => openCard(user.id) }] : []),
        { label: "Править", run: () => startEdit(user) },
        { label: "Удалить", danger: true, run: () => remove(user) },
      ]} />,
    },
  ];
  const headButton = headText.trim() || !heads[headDepartment] ? "Назначить руководителя" : "Снять руководителя";

  return <section className="page staff-page">
    <header className="page-head"><h1>Сотрудники</h1><span className="page-count">{shown.length}</span>
      {offCount > 0 && <label className="check-inline"><input type="checkbox" checked={showOff} onChange={(event) => setShowOff(event.target.checked)} /> Показать отключённых ({offCount})</label>}
      {data && !editing && <div className="page-actions"><button className="primary" onClick={() => setEditing({ id: null, draft: { ...EMPTY_DRAFT } })}>+ Сотрудник</button></div>}</header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && <>
      <DataTable label="Сотрудники" rows={shown} columns={columns} rowKey={(user) => user.id} storageKey="staff-directory"
        exportName="Сотрудники" searchPlaceholder="ФИО, логин, отдел"
        rowClassName={(user) => user.active ? "" : "muted"}
        rowTitle={(user) => openCard ? `Открыть карточку: ${user.name}` : `Править: ${user.name}`}
        onRowClick={(user) => openCard ? openCard(user.id) : startEdit(user)} />
      {editing && <EmployeeForm data={data} labels={labels} editing={editing} actorIsSuperadmin={actorLevel === "superadmin"} busy={busy} onChange={(draft) => setEditing({ ...editing, draft })} onSave={() => void saveEmployee()} onCancel={() => setEditing(null)} />}
      <article className="panel staff-departments">
        <h2>Отделы</h2>
        <div className="staff-dep-body">
        <div className="staff-dep-heads">
          <h3>Руководители отделов</h3>
          <div className="table-wrap"><table aria-label="Руководители отделов">
            <thead><tr><th>Отдел</th><th>Руководитель</th></tr></thead>
            <tbody>
              {data.departments.map((name) => <tr key={name} className={name === headDepartment ? "clickable selected" : "clickable"} onClick={() => pickHeadDepartment(name)}>
                <td>{name}</td><td>{nameOf(heads[name]) || "—"}</td>
              </tr>)}
              {data.departments.length === 0 && <tr><td colSpan={2} className="muted">Отделов пока нет — впишите их справа</td></tr>}
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
        <div className="staff-dep-list">
          <h3>Список отделов</h3>
          <textarea aria-label="Список отделов" rows={Math.min(Math.max(data.departments.length + 1, 4), 14)} value={departments} onChange={(event) => setDepartments(event.target.value)} />
          <small className="muted">По одному на строку. Отдел, который у кого-то проставлен, из списка не уйдёт — переименуйте его.</small>
          <div><button className="primary" disabled={busy} onClick={() => void saveDepartments()}>Сохранить отделы</button></div>
          <div className="staff-dep-rename">
            <select aria-label="Переименовать отдел" value={renameSource} onChange={(event) => setRenameSource(event.target.value)}>
              <option value="">Переименовать отдел…</option>
              {data.departments.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <input aria-label="Новое название отдела" placeholder="Новое название" value={renameTarget} onChange={(event) => setRenameTarget(event.target.value)} />
            <button className="secondary" disabled={busy || !renameSource || !renameTarget.trim()} onClick={() => void rename()}>Переименовать</button>
          </div>
        </div>
        </div>
      </article>
    </>}
  </section>;
}

function EmployeeForm({ data, labels, editing, actorIsSuperadmin, busy, onChange, onSave, onCancel }: {
  data: StaffDirectory;
  labels: Map<string, string>;
  /** The session is a superadministrator: the only level that may give or take «Суперадминистратор» / «Администратор». */
  actorIsSuperadmin: boolean;
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
  // A holder of a full level cannot be moved by an administrator; the full options are shut for everyone but a superadmin.
  const storedLevel = id === null ? undefined : data.users.find((user) => user.id === id)?.access_level;
  const levelLocked = !actorIsSuperadmin && data.levels.some((level) => level.full && level.key === storedLevel);
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
        <label title={levelLocked ? LEVEL_LOCKED_HINT : LEVEL_HINT}>Уровень доступа<select aria-label="Уровень доступа" value={draft.access_level} disabled={levelLocked} onChange={(event) => set("access_level", event.target.value)}>
          {data.levels.map((level) => {
            const closed = level.full && !actorIsSuperadmin;
            return <option key={level.key} value={level.key} disabled={closed} title={closed ? LEVEL_LOCKED_HINT : undefined}>{level.label}</option>;
          })}
        </select></label>
        <label title={SITE_HINT}>Площадка<select aria-label="Площадка" value={draft.site} onChange={(event) => set("site", event.target.value)}>
          <option value="">все площадки</option>
          {data.sites.map((site) => <option key={site.code} value={site.code}>{site.code} · {site.city}</option>)}
        </select></label>
        <label>{creating ? "Пароль" : "Новый пароль"}<input aria-label={creating ? "Пароль" : "Новый пароль"} type="password" autoComplete="new-password" placeholder={creating ? "необязательно, 8–128 символов" : "пусто — не менять"} value={draft.password} onChange={(event) => set("password", event.target.value)} /></label>
        <label className={creating ? "pair" : undefined}>{creating ? "PIN" : "Новый PIN"}<input aria-label={creating ? "PIN" : "Новый PIN"} inputMode="numeric" autoComplete="off" maxLength={8} placeholder={creating ? "6–8 цифр, пусто — сгенерируем и покажем" : "пусто — не менять"} value={draft.pin} onChange={(event) => set("pin", event.target.value)} /></label>
        <div className="staff-flags">
          <label className="staff-switch" title={SHOP_WORKER_HINT}><input type="checkbox" role="switch" aria-label="Монтажник (работает в цеху)" checked={draft.shop_worker} onChange={(event) => set("shop_worker", event.target.checked)} /><span>Монтажник (работает в цеху)</span></label>
          <label className="staff-switch" title={SITE_MASTER_HINT}><input type="checkbox" role="switch" aria-label="Мастер площадки" checked={draft.site_master} onChange={(event) => set("site_master", event.target.checked)} /><span>Мастер площадки</span></label>
          {!creating && <label className="staff-switch"><input aria-label="Вход разрешён" type="checkbox" role="switch" checked={draft.active} onChange={(event) => set("active", event.target.checked)} /><span>Вход разрешён</span></label>}
        </div>
      </div>
    </section>
    <div className="form-actions">
      <button className="primary" disabled={busy} onClick={onSave}>{creating ? "Завести сотрудника" : "Сохранить сотрудника"}</button>{" "}
      <button className="secondary" onClick={onCancel}>Отмена</button>
    </div>
  </div>;
}
