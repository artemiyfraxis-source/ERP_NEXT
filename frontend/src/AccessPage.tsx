import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { accessApi, type AccessMode, type AccessOverview, type AccessRow, type AccessSetting } from "./api";
// Full-access roles (owner 2026-09-29): their columns show a locked «полный» and take no edits.
import { FULL_ACCESS_ROLES } from "./navigation";

/** NAV-ACCESS «Доступы» (SUPPLYMAP Portal.gs portalAccessGet/portalAccessSave, PortalApp.html openAccessHub and the
 *  per-person «Доступ» of «Сотрудники»): the role × section matrix and personal exceptions that are stronger than the
 *  role. An empty cell = «закрыто», as SUPPLYMAP has since 01.09.2026; the administrator has «полный» everywhere unless
 *  a cell says otherwise (`defaults`). */

const MODE_LABEL: Record<AccessSetting, string> = { none: "закрыт", view: "просмотр", edit: "полный" };
/** The chip's colour is the app's tone of the mode (green / blue / red), the same classes as the statuses elsewhere. */
const MODE_TONE: Record<AccessSetting, string> = { none: "tone-danger", view: "tone-info", edit: "tone-ok" };
const FIXED_FULL_HINT = "Всегда полный доступ";
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

/** The small padlock of a cell that cannot be reduced (drawn, not an emoji: it takes the text colour and size). */
function LockIcon() {
  return <svg className="access-lock" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false">
    <path d="M3.6 5.2V3.7a2.4 2.4 0 0 1 4.8 0v1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    <rect x="2" y="5.2" width="8" height="5.6" rx="1.3" fill="currentColor" />
  </svg>;
}

/** The role's always-full cell: the mode word and a padlock, no list to open. */
function FixedFullCell() {
  return <span className="access-pill tone-ok access-fixed" title={FIXED_FULL_HINT}>
    {MODE_LABEL.edit}<LockIcon />
  </span>;
}

/** A cell as a compact chip: the tone of the effective mode picks the colour, an empty cell (the role's default)
 *  stays pale. The native select lies invisibly over the chip, so the list keeps its full wording while the cell
 *  stays short. */
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
  const classes = ["access-pill", "access-cell", `access-${effective}`, MODE_TONE[effective], value ? "access-set" : "access-default", changed ? "access-changed" : ""];
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

/** One line of marks. The longer rule sits in the «?» tooltip: an empty cell closes the section, «просмотр» is
 *  without editing, a person's exception is stronger than the role. */
function Legend() {
  return <div className="access-legend" role="group" aria-label="Обозначения">
    <span className="access-pill tone-ok access-set">полный</span>
    <span className="access-pill tone-info access-set">просмотр</span>
    <span className="access-pill tone-danger access-set">закрыт</span>
    <span className="access-pill tone-ok access-default">полный</span>
    <span>по умолчанию у роли</span>
    <span className="access-pill tone-info access-set access-changed">просмотр</span>
    <span>не сохранено</span>
    <span className="access-pill tone-ok access-fixed"><LockIcon /></span>
    <span>всегда полный доступ</span>
    <span className="access-help" tabIndex={0} role="note" aria-label="Подсказка"
      title="Пустая ячейка — раздел закрыт, «просмотр» — без правки. Исключение по сотруднику сильнее роли.">?</span>
  </div>;
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
    { key: "section", title: "Раздел", value: (section) => section.label, wrap: true },
    ...data.roles.map((role): DataColumn<Section> => ({
      key: role.key,
      title: role.label,
      hint: FULL_ACCESS_ROLES.has(role.key) ? `${role.label} — ${FIXED_FULL_HINT.toLowerCase()}` : role.label,
      value: (section) => shown(roleModes({ ...data, matrix }, role.key)[section.key]),
      render: (section) => {
        if (FULL_ACCESS_ROLES.has(role.key)) return <FixedFullCell />;
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
    { key: "section", title: "Раздел", value: (section) => section.label, wrap: true },
    {
      key: "role", title: `У роли «${roleLabel(role)}»`,
      value: (section) => shown(roleModes(data, role)[section.key]),
      render: (section) => {
        const mode = roleModes(data, role)[section.key] ?? "none";
        return <span className={`access-pill access-${mode} ${MODE_TONE[mode]} access-default`}>{MODE_LABEL[mode]}</span>;
      },
    },
    {
      key: "personal", title: "Исключение",
      value: (section) => personal[section.key] ? MODE_LABEL[personal[section.key]!] : "",
      render: (section) => {
        if (FULL_ACCESS_ROLES.has(role)) return <FixedFullCell />;
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
    <div className="access-bar">
      <div className="shipping-tabs">
        <button className={tab === "roles" ? "secondary active" : "secondary"} onClick={() => setTab("roles")}>Роли × разделы</button>
        <button className={tab === "people" ? "secondary active" : "secondary"} onClick={() => setTab("people")}>Исключения по сотрудникам</button>
      </div>
      {data && tab === "roles" && <Legend />}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && tab === "roles" && <>
      <div className="access-matrix">
        <DataTable label="Матрица доступов" rows={data.sections} rowKey={(section) => section.key} columns={matrixColumns} emptyText="Разделов нет" />
      </div>
      <div className={matrixDirty ? "form-actions access-savebar dirty" : "form-actions access-savebar"}>
        <button className="primary small" disabled={busy || !matrixDirty} onClick={() => void saveRoles()}>Сохранить доступы ролей</button>
        <button className="secondary small" disabled={busy || !matrixDirty} onClick={() => setMatrix(data.matrix)}>Отменить правки</button>
        {matrixDirty && <span className="access-dirty-note">Есть несохранённые правки</span>}
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
        <div className="form-actions access-savebar">
          <button className="primary small" disabled={busy} onClick={() => void savePerson()}>Сохранить исключения</button>
        </div>
      </> : <p className="page-sub">Исключения нужны, чтобы дать человеку раздел, закрытый его роли, или отнять открытый.</p>}
    </article>}
  </section>;
}
