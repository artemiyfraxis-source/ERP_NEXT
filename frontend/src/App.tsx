import { useEffect, useMemo, useRef, useState } from "react";
import {
  AUTH_REQUIRED_EVENT,
  ApiError,
  identityApi,
  legacyApi,
  type AccessMode,
  type Principal,
} from "./api";
import { LoginPage } from "./LoginPage";
import { FulfillmentPage } from "./FulfillmentPage";
import { ProcurementPage } from "./ProcurementPage";
import { ProductionPage } from "./ProductionPage";
import { ProductionPlanPage } from "./ProductionPlanPage";
import { RequestsPage } from "./RequestsPage";
import { ReceivingPage } from "./ReceivingPage";
import { WarehousePage } from "./WarehousePage";
import { ShippingPage } from "./ShippingPage";
import { ReferencesPage } from "./ReferencesPage";
import { NotificationsPage } from "./NotificationsPage";
import { OperationsPage } from "./OperationsPage";
import { TechcardsPage } from "./TechcardsPage";
import { StaffPage } from "./StaffPage";

type Section = "cabinet" | "projects" | "calculation" | "techcards" | "requests" | "procurement" | "receiving" | "stock" | "issues" | "prodplan" | "production" | "myops" | "shipping" | "references" | "notifications" | "operations" | "staff";
type NavGroup = "projects" | "supply" | "stock" | "production" | "data";

const NAV: Array<{
  id: Section;
  label: string;
  permission?: string;
  minimum?: AccessMode;
  roles?: string[];
  group?: NavGroup;
}> = [
  { id: "cabinet", label: "Личный кабинет" },
  { id: "projects", label: "Проекты", permission: "projects", group: "projects" },
  { id: "calculation", label: "Расчёт и КП", permission: "calculation", group: "projects" },
  {
    id: "techcards",
    label: "Техкарты проектов",
    permission: "techcards",
    roles: ["engineer", "master", "prodhead", "management", "admin"],
    group: "projects",
  },
  { id: "requests", label: "Заявки", permission: "requests", group: "supply" },
  {
    id: "procurement",
    label: "Лист закупки",
    permission: "procurement",
    minimum: "edit",
    roles: ["buyer", "admin"],
    group: "supply",
  },
  {
    id: "receiving",
    label: "Приёмка",
    permission: "receiving",
    minimum: "edit",
    roles: ["receiver", "admin"],
    group: "stock",
  },
  {
    id: "stock",
    label: "Склад",
    permission: "warehouse",
    minimum: "edit",
    roles: ["receiver", "admin"],
    group: "stock",
  },
  {
    id: "shipping",
    label: "Готовая продукция и отгрузка",
    permission: "shipping",
    roles: ["pm", "receiver", "master", "prodhead", "management", "admin"],
    group: "stock",
  },
  {
    id: "issues",
    label: "Выдача в цех",
    permission: "fulfillment",
    roles: ["engineer", "receiver", "prodhead", "master", "admin"],
    group: "production",
  },
  {
    id: "prodplan",
    label: "Производственный план",
    permission: "production",
    roles: ["master", "prodhead", "management", "admin"],
    group: "production",
  },
  {
    id: "production",
    label: "Ресурсное планирование",
    permission: "production",
    roles: ["master", "prodhead", "management", "qc", "admin"],
    group: "production",
  },
  {
    id: "myops",
    label: "Мои задания",
    permission: "production",
    roles: ["staff", "master", "admin"],
    group: "production",
  },
  {
    id: "references",
    label: "Справочники",
    permission: "references",
    group: "data",
  },
  {
    id: "notifications",
    label: "Уведомления",
    group: "data",
  },
  {
    id: "operations",
    label: "Системный журнал",
    permission: "operations",
    roles: ["management", "admin"],
    group: "data",
  },
  {
    id: "staff",
    label: "Сотрудники",
    permission: "identity",
    minimum: "edit",
    roles: ["admin"],
    group: "data",
  },
];

const NAV_GROUPS: Array<{ id: NavGroup; label: string }> = [
  { id: "projects", label: "Проекты" },
  { id: "supply", label: "Снабжение" },
  { id: "stock", label: "Склад" },
  { id: "production", label: "Производство" },
  { id: "data", label: "Данные" },
];

function hasAccess(
  permissions: Principal["permissions"],
  permission?: string,
  minimum: AccessMode = "view",
) {
  if (!permission) return true;
  const actual = permissions[permission];
  return actual === "edit" || (minimum === "view" && actual === "view");
}

function hasNavAccess(
  principal: Principal,
  item: (typeof NAV)[number],
) {
  return (
    hasAccess(principal.permissions, item.permission, item.minimum) &&
    (!item.roles || item.roles.some((role) => principal.roles.includes(role)))
  );
}

function sectionFromUrl(): Section {
  const value = new URLSearchParams(window.location.search).get("section");
  if (value === "warehouse") return "stock";
  if (value === "home") return "cabinet";
  return NAV.some(({ id }) => id === value) ? (value as Section) : "cabinet";
}

export function App() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [section, setSection] = useState<Section>(sectionFromUrl);
  const [shellError, setShellError] = useState("");

  useEffect(() => {
    identityApi
      .me()
      .then(setPrincipal)
      .catch((error) => {
        if (!(error instanceof ApiError) || error.status !== 401) console.error(error);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const listener = () => setSection(sectionFromUrl());
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);

  useEffect(() => {
    const requireLogin = () => setPrincipal(null);
    window.addEventListener(AUTH_REQUIRED_EVENT, requireLogin);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireLogin);
  }, []);

  const visibleNav = useMemo(
    () =>
      NAV.filter((item) =>
        principal ? hasNavAccess(principal, item) : !item.permission,
      ),
    [principal],
  );

  useEffect(() => {
    if (!loading && principal && !visibleNav.some(({ id }) => id === section)) {
      setSection("cabinet");
    }
  }, [loading, principal, section, visibleNav]);

  function navigate(next: Section) {
    const url = new URL(window.location.href);
    url.searchParams.set("section", next);
    window.history.pushState({}, "", url);
    setSection(next);
  }

  async function signOut() {
    setShellError("");
    try {
      await identityApi.logout();
      setPrincipal(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setPrincipal(null);
        return;
      }
      setShellError(
        error instanceof Error
          ? `Выход не выполнен: ${error.message}`
          : "Выход не выполнен: сервер недоступен",
      );
    }
  }

  if (loading) return <div className="screen-center muted">Загружаем ERP…</div>;
  if (!principal) return <LoginPage onLogin={setPrincipal} />;
  const activeSection = visibleNav.some(({ id }) => id === section) ? section : "cabinet";

  return (
    <div className="app-shell shell">
      <aside className="sidebar side">
        <div className="brand">
          <strong className="wordmark">FRAXIS</strong>
          <span className="mod">ERP</span>
        </div>
        <nav aria-label="Основная навигация">
          {visibleNav.filter((item) => !item.group).map((item) => <NavButton key={item.id} item={item} activeSection={activeSection} navigate={navigate} />)}
          {NAV_GROUPS.map((group) => {
            const items = visibleNav.filter((item) => item.group === group.id);
            return items.length ? (
              <div className="nav-group" key={group.id}>
                <h4>{group.label}</h4>
                {items.map((item) => <NavButton key={item.id} item={item} activeSection={activeSection} navigate={navigate} />)}
              </div>
            ) : null;
          })}
        </nav>
        <div className="profile side-foot">
          <strong>{principal.user.name}</strong>
          <span>{principal.user.department}</span>
          <button
            className="link-button"
            onClick={() => void signOut()}
          >
            Выйти
          </button>
        </div>
      </aside>
      <main className="workspace main">
        {shellError && <div className="shell-error error-banner" role="alert">{shellError}</div>}
        {activeSection === "cabinet" && <Home principal={principal} navigate={navigate} />}
        {activeSection === "projects" && <LegacyFrame section="projects" scope="projects" title="Проекты" />}
        {activeSection === "calculation" && <LegacyFrame section="calc" scope="calculation" title="Расчёт и КП" />}
        {activeSection === "techcards" && <TechcardsPage principal={principal} />}
        {activeSection === "requests" && <RequestsPage principal={principal} />}
        {activeSection === "procurement" && <ProcurementPage />}
        {activeSection === "receiving" && <ReceivingPage />}
        {activeSection === "stock" && <WarehousePage admin={principal.roles.includes("admin")} />}
        {activeSection === "shipping" && <ShippingPage principal={principal} />}
        {activeSection === "issues" && <FulfillmentPage principal={principal} />}
        {activeSection === "prodplan" && <ProductionPlanPage principal={principal} />}
        {activeSection === "production" && <ProductionPage principal={principal} />}
        {activeSection === "myops" && <ProductionPage principal={principal} mine />}
        {activeSection === "references" && <ReferencesPage principal={principal} />}
        {activeSection === "notifications" && <NotificationsPage />}
        {activeSection === "operations" && <OperationsPage />}
        {activeSection === "staff" && <StaffPage />}
      </main>
    </div>
  );
}

function NavButton({
  item,
  activeSection,
  navigate,
}: {
  item: (typeof NAV)[number];
  activeSection: Section;
  navigate: (section: Section) => void;
}) {
  return (
    <button
      className={activeSection === item.id ? "nav-item active" : "nav-item"}
      onClick={() => navigate(item.id)}
    >
      <span>{item.label}</span>
    </button>
  );
}

function Home({
  principal,
  navigate,
}: {
  principal: Principal;
  navigate: (section: Section) => void;
}) {
  const cards = NAV.filter(
    (item) => item.permission && hasNavAccess(principal, item),
  );
  return (
    <section className="page">
      <header className="page-header">
        <div><span className="eyebrow">Рабочее пространство</span><h1>Добрый день, {principal.user.name}</h1></div>
      </header>
      <div className="module-grid">
        {cards.map((card) => (
          <button className="module-card" key={card.id} onClick={() => navigate(card.id)}>
            <span>{card.label}</span><small>Открыть раздел →</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function LegacyFrame({
  section,
  scope,
  title,
}: {
  section: string;
  scope: "projects" | "calculation";
  title: string;
}) {
  const [ticket, setTicket] = useState("");
  const [error, setError] = useState("");
  const frameRef = useRef<HTMLIFrameElement>(null);
  const grantRef = useRef("");
  const base = import.meta.env.VITE_LEGACY_ERP_URL ?? "http://localhost:5177";
  useEffect(() => {
    let active = true;
    setTicket("");
    setError("");
    grantRef.current = "";
    const refresh = async () => {
      try {
        const result = await legacyApi.grant(scope);
        if (!active) return;
        if (!grantRef.current) {
          const bootstrap = await legacyApi.exchange(base, result.grant);
          if (!active) return;
          setTicket(bootstrap.ticket);
        } else {
          frameRef.current?.contentWindow?.postMessage(
            { type: "erp:legacy-grant", grant: result.grant },
            new URL(base).origin,
          );
        }
        grantRef.current = result.grant;
      } catch (reason) {
        if (active && !grantRef.current) {
          setError(reason instanceof Error ? reason.message : "Legacy bridge недоступен");
        }
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [base, scope]);
  const src = ticket ? resolveLegacyUrl(base, window.location.href, section, ticket) : null;
  if (error) {
    return <section className="page"><div className="error-banner" role="alert">{error}</div></section>;
  }
  if (!ticket) {
    return <div className="screen-center muted">Открываем защищённый legacy-раздел…</div>;
  }
  if (!src) {
    return (
      <section className="page">
        <div className="error-banner" role="alert">
          Восстановленный ERP должен работать на отдельном origin. Проверьте VITE_LEGACY_ERP_URL.
        </div>
      </section>
    );
  }
  return (
    <section className="legacy-page">
      <header className="page-header compact"><div><span className="eyebrow">Восстановленный модуль</span><h1>{title}</h1></div></header>
      <iframe
        ref={frameRef}
        onLoad={() => {
          if (grantRef.current) {
            frameRef.current?.contentWindow?.postMessage(
              { type: "erp:legacy-grant", grant: grantRef.current },
              new URL(base).origin,
            );
          }
        }}
        className="legacy-frame"
        src={src}
        title={title}
        sandbox="allow-forms allow-scripts allow-same-origin allow-downloads"
      />
    </section>
  );
}

export function resolveLegacyUrl(
  base: string,
  currentUrl: string,
  section: string,
  ticket: string,
) {
  try {
    const legacy = new URL(base, currentUrl);
    if (
      !["http:", "https:"].includes(legacy.protocol) ||
      legacy.hostname.toLowerCase() === new URL(currentUrl).hostname.toLowerCase() ||
      legacy.username ||
      legacy.password
    ) {
      return null;
    }
    legacy.pathname = "/login";
    legacy.search = new URLSearchParams({ section, ticket }).toString();
    return legacy.toString();
  } catch {
    return null;
  }
}
