import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { ApiTokens } from "./ApiTokens";
import { accessApi, type AccessLevel, type AccessMode, type AccessOverview, type AccessRow, type AccessSetting } from "./api";

/** NAV-ACCESS «Доступы» (SUPPLYMAP Portal.gs portalAccessGet/portalAccessSave, PortalApp.html openAccessHub and the
 *  per-person «Доступ» of «Сотрудники»): the level × section matrix and personal exceptions that are stronger than the
 *  level (owner 04.10.2026, docs/migration/access-levels-plan.md). The columns of the matrix are the levels the server
 *  lists (14.10.2026: seven, «Администратор» gone, «Менеджер по проектам» and «Кладовщик» added): «Суперадминистратор»
 *  is always full and not editable (`full` in the answer), the others are edited here. An empty cell = «закрыт»; a person's exception left empty = «как у уровня». */

const MODE_LABEL: Record<AccessSetting, string> = { none: "закрыт", view: "просмотр", edit: "полный" };
/** The chip's colour is the app's tone of the mode (green / blue / red), the same classes as the statuses elsewhere. */
const MODE_TONE: Record<AccessSetting, string> = { none: "tone-danger", view: "tone-info", edit: "tone-ok" };
const FIXED_FULL_HINT = "Всегда полный доступ";
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

type Tab = "levels" | "people" | "tokens";
type Section = AccessOverview["sections"][number];
type Option = [value: AccessSetting | "", label: string];

/** The level matrix has three states of a cell: an empty cell and a stored `none` both read «закрыт». */
const matrixCell = (row: AccessRow | undefined, section: string): AccessSetting | "" => {
  const mode = row?.[section];
  return !mode || mode === "none" ? "" : mode;
};
const MATRIX_OPTIONS: Option[] = [["", MODE_LABEL.none], ["view", MODE_LABEL.view], ["edit", MODE_LABEL.edit]];
const EXCEPTION_OPTIONS: Option[] = [["", "как у уровня"], ["none", MODE_LABEL.none], ["view", MODE_LABEL.view], ["edit", MODE_LABEL.edit]];

/** The level's sections as the server computes them: what it has with nothing stored (superadmin/admin: everything on
 *  edit) with the stored row on top; `none` closes. */
export function levelModes(data: AccessOverview, level: string): Partial<Record<string, AccessMode>> {
  const result: Partial<Record<string, AccessMode>> = { ...(data.defaults[level] ?? {}) };
  for (const [section, mode] of Object.entries(data.matrix[level] ?? {})) {
    if (mode === "none") delete result[section];
    else if (mode) result[section] = mode;
  }
  return result;
}

const shown = (mode: AccessMode | undefined) => MODE_LABEL[mode ?? "none"];

/** The small padlock of a cell that cannot be reduced (drawn, not an emoji: it takes the text colour and size). */
function LockIcon() {
  return <svg className="access-lock" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false">
    <path d="M3.6 5.2V3.7a2.4 2.4 0 0 1 4.8 0v1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    <rect x="2" y="5.2" width="8" height="5.6" rx="1.3" fill="currentColor" />
  </svg>;
}

/** The always-full cell of superadmin/admin: the mode word and a padlock, no list to open. */
function FixedFullCell() {
  return <span className="access-pill tone-ok access-fixed" title={FIXED_FULL_HINT}>
    {MODE_LABEL.edit}<LockIcon />
  </span>;
}

/** A cell as a compact chip: the tone of the effective mode picks the colour, an empty cell stays pale. The native
 *  select lies invisibly over the chip, so the list keeps its full wording while the cell stays short. */
function AccessCell({ label, value, effective, text, options, changed, onChange }: {
  label: string;
  value: AccessSetting | "";
  /** The mode the cell amounts to: its own, or what the level gives when the cell is empty. */
  effective: AccessSetting;
  /** What the chip says. */
  text: string;
  options: Option[];
  changed?: boolean;
  onChange: (value: string) => void;
}) {
  const classes = ["access-pill", "access-cell", `access-${effective}`, MODE_TONE[effective], value ? "access-set" : "access-default", changed ? "access-changed" : ""];
  return <span className={classes.join(" ").trim()}>
    {text}
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map(([mode, optionLabel]) => <option key={mode} value={mode}>{optionLabel}</option>)}
    </select>
  </span>;
}

/** One line of marks. The longer rule sits in the «?» tooltip: an empty cell closes the section, «просмотр» is
 *  without editing, a person's exception is stronger than the level. */
function Legend() {
  return <div className="access-legend" role="group" aria-label="Обозначения">
    <span className="access-pill tone-ok access-set">полный</span>
    <span className="access-pill tone-info access-set">просмотр</span>
    <span className="access-pill tone-danger access-default access-none">закрыт</span>
    <span className="access-pill tone-info access-set access-changed">просмотр</span>
    <span>не сохранено</span>
    <span className="access-pill tone-ok access-fixed"><LockIcon /></span>
    <span>всегда полный доступ</span>
    <span className="access-help" tabIndex={0} role="note" aria-label="Подсказка"
      title="Пустая ячейка — раздел закрыт, «просмотр» — без правки. Исключение по сотруднику сильнее уровня.">?</span>
  </div>;
}

function withCell(row: AccessRow, section: string, value: string): AccessRow {
  const next = { ...row };
  if (value) next[section] = value as AccessSetting;
  else delete next[section];
  return next;
}

/** The request body: the rows that still open something (a level left out is closed). */
function filledRows(matrix: Record<string, AccessRow>): Record<string, AccessRow> {
  return Object.fromEntries(Object.entries(matrix).filter(([, row]) => Object.values(row).some(Boolean)));
}

export function AccessPage() {
  const [data, setData] = useState<AccessOverview | null>(null);
  const [tab, setTab] = useState<Tab>("levels");
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

  const saveLevels = () => run(async () => {
    await accessApi.saveLevels(filledRows(matrix));
    await load();
    return "Доступы уровней сохранены. Люди увидят изменения при следующем обновлении страницы.";
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

  const levelOf = (key: string) => data?.levels.find((level) => level.key === key);
  const levelLabel = (key: string) => levelOf(key)?.label ?? key;
  const person = data?.users.find((user) => user.id === personId);
  const personLevel = person && (levelOf(person.access_level) ?? { key: person.access_level, label: person.access_level, full: false });
  const editable = data?.levels.filter((level) => !level.full) ?? [];
  const cellChanged = (level: string, section: string) =>
    matrixCell(matrix[level], section) !== matrixCell(data?.matrix[level], section);
  const matrixDirty = data !== null && editable.some((level) => data.sections.some((section) => cellChanged(level.key, section.key)));

  const matrixColumns: DataColumn<Section>[] = data ? [
    { key: "section", title: "Раздел", value: (section) => section.label, wrap: true },
    ...data.levels.map((level): DataColumn<Section> => ({
      key: level.key,
      title: level.label,
      hint: level.full ? `${level.label} — ${FIXED_FULL_HINT.toLowerCase()}` : level.label,
      value: (section) => level.full ? MODE_LABEL.edit : MODE_LABEL[matrixCell(matrix[level.key], section.key) || "none"],
      render: (section) => {
        if (level.full) return <FixedFullCell />;
        const value = matrixCell(matrix[level.key], section.key);
        return <AccessCell
          label={`${section.label} · ${level.label}`}
          value={value}
          effective={value || "none"}
          text={MODE_LABEL[value || "none"]}
          options={MATRIX_OPTIONS}
          changed={cellChanged(level.key, section.key)}
          onChange={(next) => setMatrix({ ...matrix, [level.key]: withCell(matrix[level.key] ?? {}, section.key, next) })}
        />;
      },
    })),
  ] : [];

  const personColumns = (level: AccessLevel): DataColumn<Section>[] => data ? [
    { key: "section", title: "Раздел", value: (section) => section.label, wrap: true },
    {
      key: "level", title: `У уровня «${level.label}»`,
      value: (section) => shown(levelModes(data, level.key)[section.key]),
      render: (section) => {
        const mode = levelModes(data, level.key)[section.key] ?? "none";
        return <span className={`access-pill access-${mode} ${MODE_TONE[mode]} access-default`}>{MODE_LABEL[mode]}</span>;
      },
    },
    {
      key: "personal", title: "Исключение",
      value: (section) => personal[section.key] ? MODE_LABEL[personal[section.key]!] : "",
      render: (section) => {
        if (level.full) return <FixedFullCell />;
        const value = personal[section.key] ?? "";
        const fallback = levelModes(data, level.key)[section.key];
        return <AccessCell
          label={`Исключение: ${section.label}`}
          value={value}
          effective={value || fallback || "none"}
          text={value ? MODE_LABEL[value] : "как у уровня"}
          options={EXCEPTION_OPTIONS}
          onChange={(next) => setPersonal(withCell(personal, section.key, next))}
        />;
      },
    },
  ] : [];

  return <section className="page access-page">
    <header className="page-head"><h1>Доступы</h1></header>
    <div className="access-bar">
      <div className="shipping-tabs">
        <button className={tab === "levels" ? "secondary active" : "secondary"} onClick={() => setTab("levels")}>Уровни × разделы</button>
        <button className={tab === "people" ? "secondary active" : "secondary"} onClick={() => setTab("people")}>Исключения по сотрудникам</button>
        <button className={tab === "tokens" ? "secondary active" : "secondary"} onClick={() => setTab("tokens")}>Токены API</button>
      </div>
      {data && tab === "levels" && <Legend />}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {data && tab === "levels" && <>
      <div className="access-matrix">
        <DataTable label="Матрица доступов" rows={data.sections} rowKey={(section) => section.key} columns={matrixColumns} emptyText="Разделов нет" />
      </div>
      <div className={matrixDirty ? "form-actions access-savebar dirty" : "form-actions access-savebar"}>
        <button className="primary small" disabled={busy || !matrixDirty} onClick={() => void saveLevels()}>Сохранить доступы уровней</button>
        <button className="secondary small" disabled={busy || !matrixDirty} onClick={() => setMatrix(data.matrix)}>Отменить правки</button>
        {matrixDirty && <span className="access-dirty-note">Есть несохранённые правки</span>}
      </div>
    </>}
    {tab === "tokens" && <ApiTokens />}
    {data && tab === "people" && <article className="access-people">
      <div className="filters">
        <select aria-label="Сотрудник" value={personId} onChange={(event) => choosePerson(event.target.value)}>
          <option value="">Выберите сотрудника…</option>
          {data.users.map((user) => {
            const count = Object.keys(user.access).length;
            return <option key={user.id} value={user.id}>
              {user.name} · {levelLabel(user.access_level)}{user.active ? "" : " · отключён"}{count ? ` · исключений: ${count}` : ""}
            </option>;
          })}
        </select>
      </div>
      {person && personLevel ? <>
        <DataTable label={`Исключения: ${person.name}`} rows={data.sections} rowKey={(section) => section.key} columns={personColumns(personLevel)} />
        {!personLevel.full && <div className="form-actions access-savebar">
          <button className="primary small" disabled={busy} onClick={() => void savePerson()}>Сохранить исключения</button>
        </div>}
      </> : <p className="page-sub">Исключения нужны, чтобы дать человеку раздел, закрытый его уровню, или отнять открытый.</p>}
    </article>}
  </section>;
}
