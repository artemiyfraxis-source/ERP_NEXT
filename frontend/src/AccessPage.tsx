import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { accessApi, type AccessMode, type AccessOverview, type AccessRow, type AccessSetting } from "./api";

/** NAV-ACCESS «Доступы» (SUPPLYMAP Portal.gs portalAccessGet/portalAccessSave, PortalApp.html openAccessHub and the
 *  per-person «Доступ» of «Сотрудники»): the role × section matrix and personal exceptions that are stronger than the
 *  role. An empty cell = «закрыто», as SUPPLYMAP has since 01.09.2026; the administrator has «полный» everywhere unless
 *  a cell says otherwise (`defaults`). */

// Roles with full access to every section that cannot be reduced (owner 2026-09-29): технолог, специалист по
// снабжению, директор производства, генеральный директор. Their columns show a locked «полный» and take no edits.
const FULL_ACCESS_ROLES = new Set(["admin", "buyer", "prodhead", "management"]);
const MODE_LABEL: Record<AccessSetting, string> = { none: "закрыт", view: "просмотр", edit: "полный" };
const SETTINGS: AccessSetting[] = ["none", "view", "edit"];
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

type Tab = "roles" | "people";

/** The role's sections as the server computes them: explicit cells (the admin starts from «полный»), none closes. */
export function roleModes(data: AccessOverview, role: string): Partial<Record<string, AccessMode>> {
  const result: Partial<Record<string, AccessMode>> = { ...(data.defaults[role] ?? {}) };
  for (const [section, mode] of Object.entries(data.matrix[role] ?? {})) {
    if (mode === "none") delete result[section];
    else if (mode) result[section] = mode;
  }
  return result;
}

const shown = (mode: AccessMode | undefined) => MODE_LABEL[mode ?? "none"];

type Section = AccessOverview["sections"][number];

/** A cell as a coloured pill: the effective mode picks the colour, an empty cell (the role's default) stays pale.
 *  The native select lies invisibly over the pill, so the list keeps its full wording while the cell stays short. */
function AccessCell({ label, value, fallback, emptyLabel, emptyShown, changed, onChange }: {
  label: string;
  /** What an empty cell shows; by default its fallback mode, or a dash when that is «закрыт». */
  emptyShown?: string;
  value: AccessSetting | "";
  fallback: AccessMode | undefined;
  emptyLabel: string;
  changed?: boolean;
  onChange: (value: string) => void;
}) {
  const effective = value || fallback || "none";
  const text = value ? MODE_LABEL[value] : emptyShown ?? (fallback ? MODE_LABEL[fallback] : "—");
  const classes = ["access-pill", "access-cell", `access-${effective}`, value ? "access-set" : "access-default", changed ? "access-changed" : ""];
  return <span className={classes.join(" ").trim()}>
    {text}
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{emptyLabel}</option>
      {SETTINGS.map((mode) => <option key={mode} value={mode}>{MODE_LABEL[mode]}</option>)}
    </select>
  </span>;
}

/** An empty cell reads as its default: a dash when the section stays closed, the mode otherwise. */
const emptyText = (fallback: AccessMode | undefined) => fallback ? `${MODE_LABEL[fallback]} (по умолч.)` : "—";

function Legend() {
  return <p className="access-legend">
    <span className="access-pill access-edit access-set">полный</span>
    <span className="access-pill access-view access-set">просмотр</span>
    <span className="access-pill access-none access-set">закрыт</span>
    <span>выдано явно</span>
    <span className="access-pill access-none access-default">—</span>
    <span>пусто: закрыт (у администратора бледное «полный»)</span>
    <span className="access-pill access-view access-set access-changed">просмотр</span>
    <span>не сохранено</span>
  </p>;
}

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

  const matrixColumns: DataColumn<Section>[] = data ? [
    { key: "section", title: "Раздел", value: (section) => section.label, render: (section) => <strong>{section.label}</strong>, wrap: true },
    ...data.roles.map((role): DataColumn<Section> => ({
      key: role.key,
      title: role.label,
      value: (section) => shown(roleModes({ ...data, matrix }, role.key)[section.key]),
      render: (section) => {
        if (FULL_ACCESS_ROLES.has(role.key))
          return <span className="access-pill access-edit access-set" title="Полный доступ всегда — урезать нельзя">полный 🔒</span>;
        const fallback = data.defaults[role.key]?.[section.key];
        const value = matrix[role.key]?.[section.key] ?? "";
        return <AccessCell
          label={`${section.label} · ${role.label}`}
          value={value}
          fallback={fallback}
          emptyLabel={emptyText(fallback)}
          changed={value !== (data.matrix[role.key]?.[section.key] ?? "")}
          onChange={(next) => setMatrix({ ...matrix, [role.key]: withCell(matrix[role.key] ?? {}, section.key, next) })}
        />;
      },
    })),
  ] : [];

  const personColumns = (role: string): DataColumn<Section>[] => data ? [
    { key: "section", title: "Раздел", value: (section) => section.label, render: (section) => <strong>{section.label}</strong>, wrap: true },
    {
      key: "role", title: `У роли «${roleLabel(role)}»`,
      value: (section) => shown(roleModes(data, role)[section.key]),
      render: (section) => {
        const mode = roleModes(data, role)[section.key] ?? "none";
        return <span className={`access-pill access-${mode} access-default`}>{MODE_LABEL[mode]}</span>;
      },
    },
    {
      key: "personal", title: "Исключение",
      value: (section) => personal[section.key] ? MODE_LABEL[personal[section.key]!] : "",
      render: (section) => {
        if (FULL_ACCESS_ROLES.has(role))
          return <span className="access-pill access-edit access-set" title="Полный доступ всегда — урезать нельзя">полный 🔒</span>;
        const value = personal[section.key] ?? "";
        return <AccessCell
          label={`Исключение: ${section.label}`}
          value={value}
          fallback={roleModes(data, role)[section.key]}
          emptyLabel="как у роли"
          emptyShown="как у роли"
          onChange={(next) => setPersonal(withCell(personal, section.key, next))}
        />;
      },
    },
  ] : [];

  return <section className="page access-page">
    <header className="page-head"><h1>Доступы</h1></header>
    <p className="page-sub">Пустая ячейка — раздел закрыт, «просмотр» — без правки. Исключение по сотруднику сильнее роли. «полный 🔒» — роль всегда с полным доступом.</p>
    <div className="shipping-tabs">
      <button className={tab === "roles" ? "secondary active" : "secondary"} onClick={() => setTab("roles")}>Роли × разделы</button>
      <button className={tab === "people" ? "secondary active" : "secondary"} onClick={() => setTab("people")}>Исключения по сотрудникам</button>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && tab === "roles" && <>
      <Legend />
      <div className="access-matrix">
        <DataTable label="Матрица доступов" rows={data.sections} rowKey={(section) => section.key} columns={matrixColumns} emptyText="Разделов нет" />
      </div>
      <div className={matrixDirty ? "form-actions access-savebar dirty" : "form-actions access-savebar"}>
        {matrixDirty && <span className="access-dirty-note">Есть несохранённые правки</span>}
        <button className="primary" disabled={busy || !matrixDirty} onClick={() => void saveRoles()}>Сохранить доступы ролей</button>{" "}
        <button className="secondary" disabled={busy || !matrixDirty} onClick={() => setMatrix(data.matrix)}>Отменить правки</button>
      </div>
    </>}
    {data && tab === "people" && <article className="access-people">
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
        <DataTable label={`Исключения: ${person.name}`} rows={data.sections} rowKey={(section) => section.key} columns={personColumns(person.role)} />
        <div className="form-actions">
          <button className="primary" disabled={busy} onClick={() => void savePerson()}>Сохранить исключения</button>
        </div>
      </> : <p className="page-sub">Исключения нужны, чтобы дать человеку раздел, закрытый его роли, или отнять открытый.</p>}
    </article>}
  </section>;
}
