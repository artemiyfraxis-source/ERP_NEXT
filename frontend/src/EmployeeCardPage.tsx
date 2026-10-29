import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  employeeCardApi,
  failureText,
  documentsApi,
  leaveApi,
  type Employee,
  type EmployeeCard,
  type EmployeeCardAbsence,
  type EmployeeCardSkill,
  type Principal,
} from "./api";
import { formatDate, formatDay } from "./format";
import { hasFullAccess } from "./navigation";
import { LeaveRequests } from "./LeaveRequests";
import { EmployeeDocuments } from "./EmployeeDocuments";
import { EmployeeSkills } from "./EmployeeSkills";
import { StatusChip } from "./StatusChip";
import { WorkTime } from "./WorkTime";
import { ViewAsPicker } from "./ViewAs";
import { ApprovalPin, ChangePassword } from "./ProfilePanel";

/** «Личная карточка» сотрудника (концепт владельца, 04.10): одна страница про человека из данных всех модулей, только
 *  чтение. Вкладка «Обзор» — карточки в порядке концепта: Основные данные · Подчинённость · Проекты · Отпуска · Рабочее
 *  время · Компетенции · Мои заявки на покупку · Задачи. Нет данных — «нет данных», а не ноль. Своя карточка — ещё вкладка
 *  «Отпуска и заявления» (заявления, входящие на согласование, журнал кадров) со счётчиком ждущих решения. */

type Tab = "overview" | "leave" | "documents" | "skills" | "worktime";
const OWN_TABS: Tab[] = ["leave", "documents", "skills", "worktime"];
const OWN_ONLY: Tab[] = ["leave", "documents"];
const NO_DATA = "нет данных";
const SKILLS_SHOWN = 8;

type Props = {
  principal: Principal;
  /** Чужая карточка (раздел «Сотрудники»); пусто — своя. */
  userId?: string | null;
  /** Карточка проекта в «Проектах»; без доступа к разделу номер проекта — просто текст. */
  openProject?: (project: string) => void;
  /** Раздел «Мои заявки» (только у своей карточки). */
  openMyRequests?: () => void;
  /** Форма правки в «Сотрудниках» (только с правкой справочника). */
  openStaffEdit?: (id: string) => void;
  /** Карточка другого сотрудника (подчинённого). */
  openCard?: (id: string) => void;
  /** Назад в список сотрудников (с чужой карточки). */
  openStaff?: () => void;
  /** «Смотреть как сотрудник»: весь ERP глазами выбранного, только просмотр (кнопка — на своей карточке администратора). */
  startViewAs?: (employee: Employee) => Promise<void>;
};

export function EmployeeCardPage({ principal, userId, openProject, openMyRequests, openStaffEdit, openCard, openStaff, startViewAs }: Props) {
  const own = !userId || userId === principal.user.id;
  const [card, setCard] = useState<EmployeeCard | null>(null);
  const [error, setError] = useState("");
  // `?section=card&tab=leave|documents|skills|worktime` opens that tab at once (a link from a notification or a bookmark).
  const [tab, setTab] = useState<Tab>(() => {
    const wanted = new URLSearchParams(window.location.search).get("tab");
    const allowed = OWN_TABS.includes(wanted as Tab) && (own || !OWN_ONLY.includes(wanted as Tab));
    return allowed ? (wanted as Tab) : "overview";
  });
  const [waiting, setWaiting] = useState(0);
  const [unread, setUnread] = useState(0);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    let live = true;
    setCard(null);
    setError("");
    (own ? employeeCardApi.me() : employeeCardApi.byId(userId as string))
      .then((loaded) => { if (live) setCard(loaded); })
      .catch((reason) => { if (live) setError(failureText(reason, "Карточка не загружена")); });
    return () => { live = false; };
  }, [own, userId]);

  useEffect(() => {
    if (!own) return;
    let live = true;
    leaveApi.waiting().then((result) => { if (live) setWaiting(result.count); }).catch(() => undefined);
    return () => { live = false; };
  }, [own]);

  useEffect(() => {
    if (!own) return;
    let live = true;
    documentsApi.pending().then((result) => { if (live) setUnread(result.count); }).catch(() => undefined);
    return () => { live = false; };
  }, [own]);

  // «Сотрудники» — только superadmin / admin (backend staffdir_principal): правка карточки и чужие карточки.
  const canEdit = Boolean(openStaffEdit) && hasFullAccess(principal);
  const canOpenOthers = hasFullAccess(principal);
  // Только со своей карточки и не изнутри чужого вида (сервер всё равно пустит лишь администратора).
  const canViewAs = own && Boolean(startViewAs) && hasFullAccess(principal) && !principal.viewing;

  if (error) return <section className="page ec-page"><div className="error-banner" role="alert">{error}</div></section>;
  if (!card) return <section className="page ec-page"><div className="empty">Загрузка…</div></section>;

  const { header, personnel } = card;
  const roleLabels = header.roles.map((role) => role.label).join(", ");
  // The position is a personnel fact; the access role is a different thing and never stands in for it.
  const position = personnel?.position?.trim() ?? "";
  const site = header.site ? `${header.site} · ${header.site_label ?? ""}`.replace(/ · $/, "") : "все площадки";

  return <section className="page ec-page">
    {!own && openStaff && <button className="link-button back-link" onClick={openStaff}>← Сотрудники</button>}
    <header className="panel ec-head">
      <span className="ec-avatar" aria-hidden="true">{initials(header.name)}</span>
      <div className="ec-id">
        <h1>{header.name}</h1>
        <p className="ec-sub">
          {position || <span className="ec-nodata">должность не указана</span>}
          {header.department ? ` · ${header.department}` : ""}
        </p>
        {roleLabels && <p className="ec-access">доступ: {roleLabels}</p>}
        <p className="ec-meta">
          {personnel?.category && <span>Категория: <strong>{personnel.category}</strong></span>}
          {personnel?.since && <span>В должности с <strong>{formatDate(personnel.since)}</strong></span>}
          <span>{site}</span>
        </p>
      </div>
      <span className={header.active ? "ec-badge on" : "ec-badge"}>{header.active ? "Карточка активна" : "Не работает"}</span>
      {canEdit && <button className="secondary" onClick={() => openStaffEdit?.(header.id)}>Редактировать</button>}
      {canViewAs && <button className="secondary" onClick={() => setPicking(true)}>Смотреть как сотрудник</button>}
    </header>
    {picking && startViewAs && <ViewAsPicker selfId={principal.user.id} onPick={startViewAs} onClose={() => setPicking(false)} />}
    <div className="ec-tabs">
      <div className="seg-tabs" role="tablist" aria-label="Разделы карточки">
        <button role="tab" aria-selected={tab === "overview"} className={tab === "overview" ? "on" : undefined} onClick={() => setTab("overview")}>Обзор</button>
        {own && <button role="tab" aria-selected={tab === "leave"} className={tab === "leave" ? "on" : undefined} onClick={() => setTab("leave")}>
          Отпуска и заявления{waiting > 0 && <span className="ec-tab-count" aria-label={`ждут решения: ${waiting}`}>{waiting}</span>}
        </button>}
        {own && <button role="tab" aria-selected={tab === "documents"} className={tab === "documents" ? "on" : undefined} onClick={() => setTab("documents")}>
          Документы{unread > 0 && <span className="ec-tab-count" aria-label={`ждут ознакомления: ${unread}`}>{unread}</span>}
        </button>}
        <button role="tab" aria-selected={tab === "skills"} className={tab === "skills" ? "on" : undefined} onClick={() => setTab("skills")}>Компетенции</button>
        <button role="tab" aria-selected={tab === "worktime"} className={tab === "worktime" ? "on" : undefined} onClick={() => setTab("worktime")}>Рабочее время</button>
      </div>
      <span className="ec-asof">Сведения на {formatDate(card.as_of)}</span>
    </div>

    {tab === "leave" && own ? <LeaveRequests onWaiting={setWaiting} />
      : tab === "documents" && own ? <EmployeeDocuments onPending={setUnread} />
      : tab === "skills" ? <EmployeeSkills skills={card.skills} personnel={personnel} />
      : tab === "worktime" ? <WorkTime userId={own ? undefined : userId ?? undefined} />
      : <div className="ec-grid">
      <Card title="Основные данные">
        <dl className="ec-facts">
          {personnel && <Fact label="Табельный номер" value={personnel.tab_number} />}
          {personnel && <Fact label="Должность" value={personnel.position} />}
          <Fact label="Отдел" value={header.department} />
          <Fact label="Роль доступа" value={roleLabels} />
          <Fact label="Площадка" value={site} />
          {personnel && <Fact label="Категория" value={personnel.category} />}
          {personnel && <Fact label="В должности с" value={personnel.since ? formatDate(personnel.since) : null} />}
          {personnel && <Fact label="Руководитель" value={personnel.manager_name} />}
        </dl>
      </Card>

      <Card title="Подчинённость" count={card.subordinates.length || undefined}>
        {card.subordinates.length > 0 ? <>
          <ul className="ec-list">
            {card.subordinates.map((item) => <li key={item.id} className="ec-row">
              <span className="ec-main">
                {canOpenOthers && openCard
                  ? <button className="link-button" onClick={() => openCard(item.id)}>{item.name}</button>
                  : <strong>{item.name}</strong>}
              </span>
              <span className="ec-note">{[item.site, item.department].filter(Boolean).join(" · ") || NO_DATA}</span>
            </li>)}
          </ul>
        </> : <NoData />}
      </Card>

      <Card title="Проекты" count={card.projects.length || undefined}>
        {card.projects.length > 0 ? <ul className="ec-list">
          {card.projects.map((item) => <li key={item.project} className="ec-row ec-project">
            <span className="ec-main">
              {openProject
                ? <button className="link-button" aria-label={`Карточка проекта ${item.project}`} onClick={() => openProject(item.project)}>{item.project}</button>
                : <strong>{item.project}</strong>}
            </span>
            <span className="ec-note">{item.roles.join(" · ")}</span>
            <span className={item.deadline && item.deadline < card.as_of ? "ec-date tone-danger" : "ec-date"}>
              {item.deadline ? <>срок {formatDate(item.deadline)}</> : <>срок: <NoData /></>}
            </span>
          </li>)}
        </ul> : <NoData />}
      </Card>

      <Card title="Отпуска">
        <Vacations absences={card.absences} asOf={card.as_of} />
      </Card>

      <Card title="Рабочее время">
        {card.hours_this_month.hours === null
          ? <><NoData /><p className="ec-note">за {card.hours_this_month.label}</p></>
          : card.hours_this_month.hours === 0 && (card.hours_this_month.sessions ?? 0) > 0
            ? <>
              <p className="ec-big"><NoData /></p>
              <p className="ec-note">за {card.hours_this_month.label} · рабочих сессий: {card.hours_this_month.sessions}, учтённых часов нет</p>
            </>
            : <>
              <p className="ec-big">{hoursText(card.hours_this_month.hours)} <span>ч</span></p>
              <p className="ec-note">за {card.hours_this_month.label} · рабочих сессий: {card.hours_this_month.sessions ?? 0}</p>
            </>}
      </Card>

      <Card title="Компетенции" count={card.skills.length || undefined}>
        <Skills skills={card.skills} />
      </Card>

      <Card title={own ? "Мои заявки на покупку" : "Заявки на покупку"} count={card.requests.count || undefined}>
        {card.requests.count === null ? <NoData />
          : card.requests.count === 0 ? <p className="ec-note">Открытых заявок нет</p>
          : <>
            <ul className="ec-list">
              {card.requests.items.map((item) => <li key={item.id} className="ec-row">
                <span className="ec-main"><strong>{item.number}</strong> {item.name}</span>
                <StatusChip status={item.status} />
              </li>)}
            </ul>
            {card.requests.count > card.requests.items.length && <p className="ec-note">Показаны первые {card.requests.items.length} из {card.requests.count}</p>}
          </>}
        {own && openMyRequests && card.requests.count !== null && <button className="link-button" onClick={openMyRequests}>Открыть «Мои заявки»</button>}
      </Card>

      <Card title="Задачи" count={card.tasks.count || undefined}>
        {card.tasks.count === null ? <NoData />
          : card.tasks.count === 0 ? <p className="ec-note">Открытых задач нет</p>
          : <>
            <ul className="ec-list">
              {card.tasks.items.map((item) => <li key={item.id} className="ec-row ec-task">
                <span className="ec-main">{item.title}</span>
                <span className="ec-note">{item.module_label}</span>
                <span className="ec-date">
                  {openProject
                    ? <button className="link-button" aria-label={`Карточка проекта ${item.project}`} onClick={() => openProject(item.project)}>{item.project}</button>
                    : item.project}
                  {" · "}{formatDay(item.created_at).slice(0, 5)}
                </span>
              </li>)}
            </ul>
            {card.tasks.count > card.tasks.items.length && <p className="ec-note">Показаны первые {card.tasks.items.length} из {card.tasks.count}</p>}
          </>}
      </Card>

      {own && !principal.viewing && <Card title="Вход и пароль"><ChangePassword /></Card>}
      {own && !principal.viewing && <Card title="Разрешительный ПИН"><ApprovalPin /></Card>}
    </div>}
  </section>;
}

function Card({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return <section className="panel ec-card" aria-label={title}>
    <h2>{title}{count !== undefined && <span className="page-count">{count}</span>}</h2>
    <div className="ec-body">{children}</div>
  </section>;
}

function NoData() {
  return <span className="ec-nodata">{NO_DATA}</span>;
}

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  return <div className="ec-fact"><dt>{label}</dt><dd>{value?.trim() ? value : <NoData />}</dd></div>;
}

function Vacations({ absences, asOf }: { absences: EmployeeCard["absences"]; asOf: string }) {
  const { next_vacation: next, upcoming } = absences;
  if (!next && upcoming.length === 0) return <NoData />;
  const others = upcoming.filter((item) => item !== next && !(next && sameAbsence(item, next)));
  return <>
    <dl className="ec-facts">
      <div className="ec-fact"><dt>Ближайший отпуск</dt><dd>{next ? <>{period(next)} <span className="ec-note">· {daysText(next)}</span></> : <NoData />}</dd></div>
    </dl>
    {others.length > 0 && <>
      <h3 className="ec-sub-title">Отсутствия</h3>
      <ul className="ec-list">
        {others.map((item) => <li key={`${item.kind}${item.from_date}${item.to_date}`} className="ec-row">
          <span className="ec-main">{capitalize(item.kind) || "Отсутствие"}{item.from_date <= asOf && asOf <= item.to_date && <span className="ec-now">сейчас</span>}</span>
          <span className="ec-date">{period(item)} · {daysText(item)}</span>
        </li>)}
      </ul>
    </>}
  </>;
}

function Skills({ skills }: { skills: EmployeeCardSkill[] }) {
  const [all, setAll] = useState(false);
  // Сначала допущенные, лучшие сверху; без допуска — в конце.
  const sorted = useMemo(
    () => [...skills].sort((a, b) => Number(b.allowed) - Number(a.allowed) || b.stars - a.stars || a.operation.localeCompare(b.operation, "ru")),
    [skills],
  );
  if (skills.length === 0) return <NoData />;
  const shown = all ? sorted : sorted.slice(0, SKILLS_SHOWN);
  return <>
    <ul className="ec-list">
      {shown.map((item) => {
        const name = item.name?.trim();
        const coefficient = Number(item.coefficient);
        return <li key={item.operation} className="ec-row ec-skill">
          <span className="ec-main">{name ? <>{name}<small className="ec-code">{item.operation}</small></> : <span title={item.operation}>операция без названия</span>}</span>
          {item.allowed
            ? <span className="ec-date"><span className="ec-stars" aria-label={`${item.stars} из 5`}>{"★".repeat(item.stars) || "—"}</span>{coefficient !== 1 && Number.isFinite(coefficient) ? ` ×${item.coefficient.replace(".", ",")}` : ""}</span>
            : <span className="ec-date tone-danger">нет допуска</span>}
        </li>;
      })}
    </ul>
    <p className="ec-note ec-legend"><span className="ec-stars" aria-hidden="true">★</span> — уровень владения операцией, от 1 до 5</p>
    {sorted.length > SKILLS_SHOWN && <button className="link-button" onClick={() => setAll((open) => !open)}>
      {all ? "Свернуть" : `Показать все (${sorted.length})`}
    </button>}
  </>;
}

function initials(name: string) {
  return name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "·";
}

function capitalize(text: string) {
  const value = text.trim();
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function hoursText(hours: number) {
  return String(Math.round(hours * 100) / 100).replace(".", ",");
}

function sameAbsence(a: EmployeeCardAbsence, b: EmployeeCardAbsence) {
  return a.kind === b.kind && a.from_date === b.from_date && a.to_date === b.to_date;
}

function period(item: EmployeeCardAbsence) {
  return item.from_date === item.to_date ? formatDate(item.from_date) : `${formatDate(item.from_date)} – ${formatDate(item.to_date)}`;
}

function daysText(item: EmployeeCardAbsence) {
  const from = Date.parse(`${item.from_date}T00:00:00Z`);
  const to = Date.parse(`${item.to_date}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) return "";
  return `${Math.round((to - from) / 86_400_000) + 1} дн.`;
}
