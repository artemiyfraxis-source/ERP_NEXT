import { useEffect, useState } from "react";
import { accessApi, type AccessMode, type AccessOverview, type AccessRow, type AccessSetting } from "./api";

/** NAV-ACCESS «Доступы» (SUPPLYMAP Portal.gs portalAccessGet/portalAccessSave, PortalApp.html openAccessHub and the
 *  per-person «Доступ» of «Сотрудники»): the role × section matrix on top of what each role has by default, and
 *  personal exceptions that are stronger than the role. An empty cell = «как задумано ролью», not «закрыто». */

const MODE_LABEL: Record<AccessSetting, string> = { none: "закрыт", view: "просмотр", edit: "полный" };
const SETTINGS: AccessSetting[] = ["none", "view", "edit"];
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

type Tab = "roles" | "people";

/** The role's sections as the server computes them: defaults with the saved matrix on top (none closes). */
export function roleModes(data: AccessOverview, role: string): Partial<Record<string, AccessMode>> {
  const result: Partial<Record<string, AccessMode>> = { ...(data.defaults[role] ?? {}) };
  for (const [section, mode] of Object.entries(data.matrix[role] ?? {})) {
    if (mode === "none") delete result[section];
    else if (mode) result[section] = mode;
  }
  return result;
}

const shown = (mode: AccessMode | undefined) => MODE_LABEL[mode ?? "none"];

function withCell(row: AccessRow, section: string, value: string): AccessRow {
  const next = { ...row };
  if (value) next[section] = value as AccessSetting;
  else delete next[section];
  return next;
}

export function AccessPage() {
  const [data, setData] = useState<AccessOverview | null>(null);
  const [tab, setTab] = useState<Tab>("roles");
  const [matrix, setMatrix] = useState<Record<string, AccessRow>>({});
  const [personId, setPersonId] = useState("");
  const [personal, setPersonal] = useState<AccessRow>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const loaded = await accessApi.overview();
    setData(loaded);
    setMatrix(loaded.matrix);
    return loaded;
  }

  useEffect(() => {
    load().catch((reason) => setError(errorText(reason, "Не удалось загрузить доступы")));
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

  const saveRoles = () => run(async () => {
    await accessApi.saveRoles(matrix);
    await load();
    return "Доступы ролей сохранены. Люди увидят изменения при следующем обновлении страницы.";
  });

  function choosePerson(id: string) {
    setPersonId(id);
    setPersonal(data?.users.find((user) => user.id === id)?.access ?? {});
  }

  const savePerson = () => run(async () => {
    const person = data?.users.find((user) => user.id === personId);
    if (!person) return "";
    const saved = await accessApi.saveUser(person.id, personal);
    const loaded = await load();
    setPersonal(loaded.users.find((user) => user.id === person.id)?.access ?? saved.access);
    return `Исключения сохранены: ${person.name}`;
  });

  const roleLabel = (key: string) => data?.roles.find((role) => role.key === key)?.label ?? key;
  const person = data?.users.find((user) => user.id === personId);
  const matrixDirty = data !== null && JSON.stringify(matrix) !== JSON.stringify(data.matrix);

  return <section className="page access-page">
    <header className="page-head"><h1>Доступы</h1></header>
    <p className="page-sub">
      Кто какие разделы видит. Пустая ячейка — <b>как задумано ролью</b> (подсказка показывает, что это значит);
      <b> закрыт</b> прячет раздел и на сервере, <b>просмотр</b> снимает правку. Персональное исключение сильнее роли.
      Часть действий на сервере по-прежнему закреплена за ролями (например, правит закупку только снабжение и
      технолог) — матрица их другой роли не откроет. Хотя бы один администратор всегда сохраняет «Доступы».
    </p>
    <div className="shipping-tabs">
      <button className={tab === "roles" ? "secondary active" : "secondary"} onClick={() => setTab("roles")}>Роли × разделы</button>
      <button className={tab === "people" ? "secondary active" : "secondary"} onClick={() => setTab("people")}>Исключения по сотрудникам</button>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && tab === "roles" && <>
      <div className="table-wrap"><table aria-label="Матрица доступов" className="access-matrix">
        <thead><tr><th>Раздел</th>{data.roles.map((role) => <th key={role.key}>{role.label}</th>)}</tr></thead>
        <tbody>
          {data.sections.map((section) => <tr key={section.key}>
            <td className="text"><strong>{section.label}</strong></td>
            {data.roles.map((role) => {
              const value = matrix[role.key]?.[section.key] ?? "";
              const fallback = shown(data.defaults[role.key]?.[section.key]);
              return <td key={role.key}>
                <select
                  aria-label={`${section.label} · ${role.label}`}
                  className={value ? "access-set" : "access-default"}
                  value={value}
                  onChange={(event) => setMatrix({ ...matrix, [role.key]: withCell(matrix[role.key] ?? {}, section.key, event.target.value) })}
                >
                  <option value="">как задумано: {fallback}</option>
                  {SETTINGS.map((mode) => <option key={mode} value={mode}>{MODE_LABEL[mode]}</option>)}
                </select>
              </td>;
            })}
          </tr>)}
        </tbody>
      </table></div>
      <div className="form-actions">
        <button className="primary" disabled={busy || !matrixDirty} onClick={() => void saveRoles()}>Сохранить доступы ролей</button>{" "}
        <button className="secondary" disabled={busy || !matrixDirty} onClick={() => setMatrix(data.matrix)}>Отменить правки</button>
      </div>
    </>}
    {data && tab === "people" && <article className="panel access-people">
      <div className="filters">
        <select aria-label="Сотрудник" value={personId} onChange={(event) => choosePerson(event.target.value)}>
          <option value="">Выберите сотрудника…</option>
          {data.users.map((user) => {
            const count = Object.keys(user.access).length;
            return <option key={user.id} value={user.id}>
              {user.name} · {roleLabel(user.role)}{user.active ? "" : " · отключён"}{count ? ` · исключений: ${count}` : ""}
            </option>;
          })}
        </select>
      </div>
      {person ? <>
        <div className="table-wrap"><table aria-label={`Исключения: ${person.name}`}>
          <thead><tr><th>Раздел</th><th>У роли «{roleLabel(person.role)}»</th><th>Исключение</th></tr></thead>
          <tbody>
            {data.sections.map((section) => {
              const byRole = roleModes(data, person.role)[section.key];
              const value = personal[section.key] ?? "";
              return <tr key={section.key}>
                <td className="text">{section.label}</td>
                <td>{shown(byRole)}</td>
                <td><select
                  aria-label={`Исключение: ${section.label}`}
                  className={value ? "access-set" : "access-default"}
                  value={value}
                  onChange={(event) => setPersonal(withCell(personal, section.key, event.target.value))}
                >
                  <option value="">как у роли</option>
                  {SETTINGS.map((mode) => <option key={mode} value={mode}>{mode === "none" ? "закрыть (сильнее роли)" : MODE_LABEL[mode]}</option>)}
                </select></td>
              </tr>;
            })}
          </tbody>
        </table></div>
        <div className="form-actions">
          <button className="primary" disabled={busy} onClick={() => void savePerson()}>Сохранить исключения</button>
        </div>
      </> : <p className="page-sub">Исключения нужны, чтобы дать человеку раздел, закрытый его роли, или отнять открытый.</p>}
    </article>}
  </section>;
}
