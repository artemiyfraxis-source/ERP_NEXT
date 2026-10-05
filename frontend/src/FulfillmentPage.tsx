import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ApiError,
  fulfillmentApi,
  type FulfillmentCalcLine,
  type FulfillmentLine,
  type FulfillmentLot,
  type FulfillmentPlaces,
  type FulfillmentRequest,
  type Principal,
} from "./api";
import { canAct, hasFullAccess } from "./navigation";
import { DataTable, matchesWords, type DataColumn } from "./DataTable";
import { openPrintSheet } from "./print";
import { BulkLotsPanel, bindPosition, bulkApi, isOffBook, type BulkPosition } from "./FulfillmentBulkLots";

/* NAV-1: one request, two sections (SUPPLYMAP ISS_MODES_, owner 02.09.2026): «Запросы на комплектацию» picks and
   only reserves, «Выдача со списанием» hands the kit out and writes it off. «Готов к выдаче» is the joint: for
   picking it is done (hidden unless «показать» is ticked), for handout it is the input. Closed requests live in the
   archive of either screen. */
const MODES = {
  pick: {
    title: "Запросы на комплектацию",
    stages: ["Новая", "Недоукомплектован", "Укомплектован", "Готов к выдаче"],
    hidden: ["Готов к выдаче"],
    empty: "Заявок на комплектацию нет. Они появятся из состава производственного комплекта.",
  },
  give: {
    title: "Выдача со списанием",
    stages: ["Готов к выдаче", "КБ Выдана", "КБ Возвращена"],
    hidden: [] as string[],
    empty: "Заявок к выдаче нет. Сюда приходят укомплектованные заявки, отправленные на выдачу.",
  },
};
const ARCHIVE = ["Готово", "Отменена"];
const ALL_STAGES = ["Новая", "Недоукомплектован", "Укомплектован", "Готов к выдаче", "КБ Выдана", "КБ Возвращена"];

type Mutate = (requestId: string, operation: () => Promise<unknown>) => Promise<void>;

/** SUPPLYMAP issueProjectOf_ / issProj_: the part of НАЗНАЧЕНИЕ before « · »; a manual request has none. */
export function projectOf(request: Pick<FulfillmentRequest, "purpose" | "project">) {
  if (request.project !== undefined) return request.project;
  const parts = String(request.purpose ?? "").split(" · ");
  return parts.length > 1 ? parts[0].trim() : "";
}

/** issKb_: the КБ of a request inside its project (second part of НАЗНАЧЕНИЕ), otherwise its number. */
function kbOf(request: FulfillmentRequest) {
  return String(request.purpose ?? "").split(" · ")[1] || request.number;
}

/** «Набрано» of a line: what the lot holds for it, or what was issued (SUPPLYMAP КОЛ-ВО stays after issue). */
function picked(line: FulfillmentLine) {
  return Number(line.issued) > 0 ? Number(line.issued) : Number(line.reserved);
}

/** A line is closed by a lot that covers it, or by «есть, не заведено» (the part is there, not entered yet). */
function closedLine(line: FulfillmentLine) {
  return isOffBook(line) || (Boolean(line.lot) && picked(line) >= Number(line.requirement));
}

function openLines(request: FulfillmentRequest) {
  return (request.lines ?? []).filter((line) => !closedLine(line));
}

function stamp(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function day(value?: string | null) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value ?? "");
  return match ? `${match[3]}.${match[2]}.${match[1]}` : value ?? "";
}

// issChip_: the colour of a stage — grey waits, green went through, red is closed.
const STAGE_COLORS: Record<string, string> = {
  "Новая": "#6b7280",
  "Недоукомплектован": "#b45309",
  "Укомплектован": "#1d4ed8",
  "Готов к выдаче": "#0f766e",
  "КБ Выдана": "#15803d",
  "КБ Возвращена": "#7c3aed",
  "Готово": "#15803d",
  "Отменена": "#b91c1c",
};

// The screen opens as tiles (SUPPLYMAP issRender); «Таблицей» is remembered per browser, and a blocked storage keeps tiles.
const VIEW_KEY = "erp.fulfillment.view";

function savedView(): "tiles" | "table" {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "table" ? "table" : "tiles";
  } catch {
    return "tiles";
  }
}

function saveView(view: "tiles" | "table") {
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    // Private window or blocked site data: the choice lives until the page is reloaded.
  }
}

/** issMatch_: every word of the search is found in the number, purpose, requester or stage. */
function issMatch(request: FulfillmentRequest, query: string) {
  return matchesWords([request.number, request.purpose, request.requester, request.status], query);
}

function createdDay(value?: string | null) {
  const date = new Date(value ?? "");
  return Number.isNaN(date.getTime()) ? day(value) : date.toLocaleDateString("ru-RU");
}

function StageChips({ counts }: { counts: Map<string, number> }) {
  return <div className="kit-chips">{[...counts].map(([status, count]) => <span key={status} className="iss-chip-wrap">
    <span className="iss-chip" style={{ background: STAGE_COLORS[status] ?? "#6b7280" }}>{status}</span>
    {count > 1 && <small className="t-sub">×{count}</small>}
  </span>)}</div>;
}

type TileItem = { project: string; requests: FulfillmentRequest[] } | { project: ""; request: FulfillmentRequest };

/* issRender: tiles, not a table (owner 22.09.2026 «карточки топ»). A request is created per КБ, so the requests of one
   project are one tile that opens the project card; a manual request without a project keeps its own tile. The order
   is the sheet's (creation), a project standing where its first request stands. */
function RequestTiles({ requests, openProject, openRequest }: {
  requests: FulfillmentRequest[];
  openProject: (project: string) => void;
  openRequest: (id: string) => void;
}) {
  const items: TileItem[] = [];
  const byProject = new Map<string, { project: string; requests: FulfillmentRequest[] }>();
  for (const request of requests) {
    const project = projectOf(request);
    if (!project) { items.push({ project: "", request }); continue; }
    let group = byProject.get(project);
    if (!group) { group = { project, requests: [] }; byProject.set(project, group); items.push(group); }
    group.requests.push(request);
  }
  const tile = (key: string, title: string, open: () => void, body: ReactNode) =>
    <div key={key} className="tile iss-tile" role="button" tabIndex={0} title={title} onClick={open}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
      <div className="tile-body">{body}</div>
    </div>;

  return <div className="tiles iss-tiles">{items.map((item) => {
    if ("request" in item) {
      const request = item.request;
      const count = (request.lines ?? []).length;
      return tile(request.id, `Открыть заявку ${request.number}`, () => openRequest(request.id), <>
        <div className="t-row"><strong className="t-h">{request.number}</strong><span className="t-sub">{createdDay(request.created_at)}</span></div>
        <StageChips counts={new Map([[request.status, 1]])} />
        <div className="t-in">{request.purpose || "—"}</div>
        {request.shortfall && <div className="t-warn">недобор: {request.shortfall}</div>}
        <div className="t-sub">{request.requester}{request.deadline ? ` · до ${day(request.deadline)}` : ""}{count ? ` · позиций ${count}` : ""}</div>
      </>);
    }
    const counts = new Map<string, number>();
    const who = new Set<string>();
    let count = 0;
    let deadline = "";
    for (const request of item.requests) {
      counts.set(request.status, (counts.get(request.status) ?? 0) + 1);
      if (request.requester) who.add(request.requester);
      count += (request.lines ?? []).length;
      if (request.deadline && (!deadline || request.deadline < deadline)) deadline = request.deadline;
    }
    const short = item.requests.filter((request) => request.shortfall).length;
    // «Сборки:» as a caption: a bare list of КБ read like an error (owner 22.09.2026), though it is the order's make-up.
    return tile(`project:${item.project}`, `Открыть проект ${item.project}`, () => openProject(item.project), <>
      <div className="t-row"><strong className="t-h">{item.project}</strong><span className="t-sub">сборок: {item.requests.length}</span></div>
      <StageChips counts={counts} />
      <div className="t-in">Сборки: {item.requests.map(kbOf).join(" · ")}</div>
      {short > 0 && <div className="t-warn">с недобором: {short} из {item.requests.length}</div>}
      <div className="t-sub">{[...who].join(", ")}{deadline ? ` · до ${day(deadline)}` : ""}{count ? ` · позиций ${count}` : ""}</div>
    </>);
  })}</div>;
}

export function FulfillmentPage({ principal, mode }: { principal: Principal; mode?: keyof typeof MODES }) {
  const [archive, setArchive] = useState(false);
  const [showReady, setShowReady] = useState(false);
  const [byProject, setByProject] = useState(false);
  const [project, setProject] = useState<string | null>(null);
  const [view, setView] = useState(savedView);
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [fromCalc, setFromCalc] = useState(false);
  const [requests, setRequests] = useState<FulfillmentRequest[]>([]);
  const [lots, setLots] = useState<FulfillmentLot[]>([]);
  const [places, setPlaces] = useState<FulfillmentPlaces | undefined>(undefined);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loaded, setLoaded] = useState(false);
  // The matrix alone decides (picking: fulfillment edit); a full-access level passes every section.
  const canEdit = canAct(principal, "fulfillment");
  // NAV-SECTIONS: handing out writes off stock and has its own key; picking (reserve) stays on `fulfillment`.
  const canHandout = canAct(principal, "handout");
  // FF-5: deleting is the administrator's cleanup (portalIssueDelete + portalRealAdmin_).
  const isAdmin = hasFullAccess(principal);

  const screen = mode ? MODES[mode] : null;
  const stages = screen ? screen.stages.filter((stage) => showReady || !screen.hidden.includes(stage)) : null;
  const inScreen = (request: FulfillmentRequest) => archive
    ? ARCHIVE.includes(request.status)
    : stages ? stages.includes(request.status) : true;
  const shown = requests.filter(inScreen);

  async function reload() {
    setError("");
    try {
      const [requestResult, lotResult] = await Promise.all([
        fulfillmentApi.list(),
        canEdit ? fulfillmentApi.lots() : Promise.resolve({ lots: [] }),
      ]);
      setRequests(requestResult.requests);
      setPlaces(requestResult.places);
      setLots(lotResult.lots);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить выдачу");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void reload(); }, []);

  const mutate: Mutate = async (requestId, operation) => {
    if (busy) return;
    setBusy(requestId);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      if (result && typeof result === "object" && "message" in result) setNotice(String((result as { message: string }).message));
      await reload();
    } catch (reason) {
      // A stale version: show what is there now, then say why the action did not go through.
      if (reason instanceof ApiError && reason.status === 409) await reload();
      setError(reason instanceof Error ? reason.message : "Операция выдачи не выполнена");
    } finally {
      setBusy("");
    }
  };

  const cardProps = {
    lots, places, canEdit, canHandout, isAdmin,
    showPick: !mode || mode === "pick",
    showGive: !mode || mode === "give",
    mutate,
  };

  // issFilterRender_: one chip per stage of the screen with its count; the archive splits into its two closings.
  const presets = (archive ? ARCHIVE : stages ?? ALL_STAGES).map((stage) => ({
    label: stage,
    test: (request: FulfillmentRequest) => request.status === stage,
  }));

  const columns: DataColumn<FulfillmentRequest>[] = [
    { key: "number", title: "№", value: (request) => request.number, render: (request) => <strong>{request.number}</strong> },
    {
      key: "project",
      title: "Проект",
      value: (request) => projectOf(request),
      render: (request) => projectOf(request)
        ? <button type="button" className="link-button table-link" onClick={(event) => { event.stopPropagation(); setProject(projectOf(request)); }}>{projectOf(request)}</button>
        : "—",
    },
    { key: "purpose", title: "Назначение", value: (request) => request.purpose, wrap: true },
    { key: "requester", title: "Заявитель", value: (request) => request.requester },
    { key: "status", title: "Статус", value: (request) => request.status, filter: true, render: (request) => <span className="status">{request.status}</span> },
    { key: "deadline", title: "Дедлайн", value: (request) => request.deadline ?? "", render: (request) => day(request.deadline) || "—" },
    { key: "count", title: "Позиций", numeric: true, value: (request) => (request.lines ?? []).length },
    { key: "waiting", title: "Ждут ГРН", numeric: true, value: (request) => openLines(request).length },
  ];

  const projectRequests = project ? requests.filter((request) => projectOf(request) === project && inScreenIgnoringStage(request)) : [];
  function inScreenIgnoringStage(request: FulfillmentRequest) {
    // portalIssueProject reads active or archive requests, not one stage.
    return archive ? ARCHIVE.includes(request.status) : !ARCHIVE.includes(request.status);
  }

  function switchView(next: "tiles" | "table") {
    setView(next);
    saveView(next);
  }

  // issFilterRender_: the chips count what the search left, not what the chip itself narrows to.
  const chipStages = presets.map((item) => item.label);
  const found = shown.filter((request) => issMatch(request, query));
  const tiled = stage ? found.filter((request) => request.status === stage) : found;
  const opened = openId ? requests.find((request) => request.id === openId) : undefined;
  const emptyText = archive ? "В архиве пусто." : screen?.empty ?? "Заявок на выдачу нет. Они появятся из состава производственного комплекта.";
  const readyToggle = screen && screen.hidden.length > 0 && !archive && <label className="check-inline"><input type="checkbox" checked={showReady} onChange={(event) => {
    setShowReady(event.target.checked);
    // A chip pointing at the stage that was just hidden would show an empty screen.
    if (!event.target.checked && screen.hidden.includes(stage)) setStage("");
  }} /> показать «{screen.hidden[0]}»</label>;

  return (
    <section className="page">
      <header className="page-head">
        <h1>{screen?.title ?? "Выдача в цех"}</h1><span className="page-count">{shown.length}</span>
        <div className="page-actions">
        {mode && <button className={archive ? "secondary small active" : "secondary small"} onClick={() => { setArchive((value) => !value); setStage(""); setOpenId(null); }}>{archive ? "Активные" : "Архив"}</button>}
        {canEdit && mode !== "give" && !archive && <button className="primary small" onClick={() => { setCreating((value) => !value); setFromCalc(false); }}>+ Заявка</button>}
        {canEdit && mode !== "give" && !archive && <button className="secondary small" onClick={() => { setFromCalc((value) => !value); setCreating(false); }}>Заявка по расчёту</button>}
        <button className="secondary small" onClick={() => switchView(view === "tiles" ? "table" : "tiles")}>{view === "tiles" ? "Таблицей" : "Плитками"}</button>
        </div>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      {fromCalc && <CalcRequestForm onClose={() => setFromCalc(false)} onCreated={async () => { setFromCalc(false); await reload(); }} />}
      {creating && <CreateRequestForm lots={lots} onClose={() => setCreating(false)} onCreated={async () => { setCreating(false); await reload(); }} />}
      {project && <ProjectPanel project={project} requests={projectRequests} onClose={() => setProject(null)} busyId={busy} {...cardProps} />}
      {!project && view === "tiles" && (opened
        ? <div className="fulfillment-open">
          <button className="secondary small" onClick={() => setOpenId(null)}>← К заявкам</button>
          <FulfillmentCard key={`${opened.id}:${opened.version}`} request={opened} busy={busy === opened.id} {...cardProps} />
        </div>
        : <>
          <div className="filters table-toolbar">
            <div className="table-search"><input className="search" type="search" aria-label={`Поиск: ${screen?.title ?? "Заявки на выдачу"}`} placeholder="поиск: 708К.4 КБ1 …" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
            {readyToggle}
          </div>
          <div className="calendar-chips" role="group" aria-label="Стадии">
            <button type="button" className={`calendar-chip${stage ? "" : " active"}`} aria-pressed={!stage} onClick={() => setStage("")}>Все · {found.length}</button>
            {chipStages.map((item) => <button type="button" key={item} className={`calendar-chip${stage === item ? " active" : ""}`} aria-pressed={stage === item} onClick={() => setStage(stage === item ? "" : item)}>{item} · {found.filter((request) => request.status === item).length}</button>)}
          </div>
          {tiled.length
            ? <RequestTiles requests={tiled} openProject={setProject} openRequest={setOpenId} />
            : <div className="empty">{!loaded ? "Загружаю…" : query ? "Ничего не нашлось." : stage ? `На стадии «${stage}» заявок нет.` : emptyText}</div>}
        </>)}
      {!project && view === "table" && <DataTable
        rows={shown}
        columns={columns}
        rowKey={(request) => request.id}
        label={screen?.title ?? "Заявки на выдачу"}
        presets={presets}
        presetCountsFollowSearch
        groupBy={byProject ? (request) => projectOf(request) || "Без проекта" : undefined}
        emptyText={loaded ? emptyText : "Загружаю…"}
        exportName={mode === "give" ? "vydacha" : "komplektaciya"}
        toolbar={<>
          <label className="check-inline"><input type="checkbox" checked={byProject} onChange={(event) => setByProject(event.target.checked)} /> По проектам</label>
          {readyToggle}
        </>}
        renderDetail={(request) => <FulfillmentCard key={`${request.id}:${request.version}`} request={request} busy={busy === request.id} {...cardProps} />}
      />}
    </section>
  );
}

type CardProps = {
  lots: FulfillmentLot[];
  places?: FulfillmentPlaces;
  canEdit: boolean;
  canHandout: boolean;
  isAdmin: boolean;
  showPick: boolean;
  showGive: boolean;
  mutate: Mutate;
};

/* portalIssueProject: requests are created one per КБ; the project card glues them on reading — «Весь заказ» (every
   position across the КБ, picked / needed per КБ) and a tab per КБ with the ordinary request card. */
function ProjectPanel({ project, requests, onClose, busyId, ...cardProps }: CardProps & {
  project: string;
  requests: FulfillmentRequest[];
  onClose: () => void;
  busyId: string;
}) {
  const [tab, setTab] = useState("all");
  const current = requests.find((request) => request.id === tab);
  const positions = useMemo(() => {
    const groups = new Map<string, { article: string; name: string; unit: string; need: number; got: number; off: number; remaining: number; kb: Map<string, { request: FulfillmentRequest; need: number; got: number; off: number; remaining: number }> }>();
    for (const request of requests) {
      for (const line of request.lines ?? []) {
        const key = `${line.article}|${line.name}`.trim().toLocaleLowerCase("ru");
        if (!groups.has(key)) groups.set(key, { article: line.article, name: line.name, unit: line.unit, need: 0, got: 0, off: 0, remaining: 0, kb: new Map() });
        const group = groups.get(key)!;
        const off = isOffBook(line) ? Math.max(Number(line.requirement) - picked(line), 0) : 0;
        const open = closedLine(line) ? 0 : Math.max(Number(line.requirement) - picked(line), 0);
        group.need += Number(line.requirement);
        group.got += picked(line);
        group.off += off;
        group.remaining += open;
        const kb = group.kb.get(request.id) ?? { request, need: 0, got: 0, off: 0, remaining: 0 };
        kb.need += Number(line.requirement);
        kb.got += picked(line);
        kb.off += off;
        kb.remaining += open;
        group.kb.set(request.id, kb);
      }
    }
    return [...groups.values()];
  }, [requests]);
  const round = (value: number) => Math.round(value * 100) / 100;
  const done = positions.filter((position) => round(position.got + position.off) >= round(position.need)).length;
  const [bulkKey, setBulkKey] = useState<string | null>(null);
  const canBulk = cardProps.canEdit && cardProps.showPick;
  const versionKey = requests.map((request) => `${request.id}:${request.version}`).join(",");
  const bulkOf = (position: (typeof positions)[number]): BulkPosition => ({
    article: position.article, name: position.name, unit: position.unit,
    kbs: [...position.kb.values()].filter((kb) => kb.remaining > 0.0001)
      .map((kb) => ({ request: kb.request, label: kbOf(kb.request), remaining: round(kb.remaining) }))
      .sort((a, b) => a.label.localeCompare(b.label, "ru", { numeric: true })),
  });
  // Both actions go through the page's mutate: one busy lock, the banner, the reload; a 409 reloads and says so.
  const runBulk = (operation: () => Promise<{ message: string }>) => void cardProps.mutate(`project:${project}`, operation);

  return <div className="panel fulfillment-project">
    <div className="procurement-title">
      <div><strong>Проект {project}</strong><small>КБ: {requests.length} · набрано {done} из {positions.length} поз. по заказу</small></div>
      <button className="secondary small" onClick={onClose}>← К заявкам</button>
    </div>
    <div className="calendar-chips" role="tablist" aria-label="Заявки проекта">
      <button type="button" role="tab" aria-selected={tab === "all"} className={`calendar-chip${tab === "all" ? " active" : ""}`} onClick={() => setTab("all")}>Весь заказ</button>
      {requests.map((request) => {
        const waiting = openLines(request).length;
        return <button type="button" role="tab" key={request.id} aria-selected={tab === request.id} className={`calendar-chip${tab === request.id ? " active" : ""}`} onClick={() => setTab(request.id)}>
          {kbOf(request)}{waiting ? ` · ждут ${waiting}` : " ✓"}
        </button>;
      })}
    </div>
    {current
      ? <FulfillmentCard key={`${current.id}:${current.version}`} request={current} busy={busyId === current.id} {...cardProps} />
      : <div className="table-wrap"><table className="grid wide"><thead><tr><th>Артикул КД</th><th>Наименование</th><th>По КБ (набрано / нужно)</th><th className="num">Нужно</th><th className="num">Набрано</th>{canBulk && <th />}</tr></thead><tbody>
        {positions.map((position) => {
          const key = `${position.article}|${position.name}`;
          const open = canBulk && position.remaining > 0.0001;
          return <Fragment key={key}>
            <tr>
              <td><strong>{position.article || "—"}</strong></td><td>{position.name}</td>
              <td>{[...position.kb.values()].map((kb) => <button type="button" key={kb.request.id} className="secondary small" onClick={() => setTab(kb.request.id)}>{kbOf(kb.request)} {round(kb.got)} / {round(kb.need)}{kb.off > 0 ? " · не заведено" : ""}</button>)}</td>
              <td className="num">{round(position.need)} {position.unit}</td>
              <td className="num">{round(position.got)}{position.off > 0 && <small>{round(position.off)} есть, не заведено</small>}</td>
              {canBulk && <td>{open && <>
                <button type="button" className="secondary small" aria-expanded={bulkKey === key} disabled={Boolean(busyId)} onClick={() => setBulkKey(bulkKey === key ? null : key)}>ГРН на все КБ</button>{" "}
                <button type="button" className="secondary small" disabled={Boolean(busyId)} aria-label={`Есть, не заведено: ${position.name}`} onClick={() => runBulk(() => bindPosition(project, bulkOf(position), [], true))}>Есть, не заведено</button>
              </>}</td>}
            </tr>
            {open && bulkKey === key && <tr className="detail"><td colSpan={6}>
              <BulkLotsPanel project={project} position={bulkOf(position)} versionKey={versionKey} busy={Boolean(busyId)} run={runBulk} onClose={() => setBulkKey(null)} />
            </td></tr>}
          </Fragment>;
        })}
        {!positions.length && <tr><td colSpan={canBulk ? 6 : 5} className="empty">Позиций нет.</td></tr>}
      </tbody></table></div>}
  </div>;
}

function FulfillmentCard({ request, lots, places, canEdit, canHandout, isAdmin, showPick, showGive, busy, mutate }: CardProps & {
  request: FulfillmentRequest;
  busy: boolean;
}) {
  const [returnLocation, setReturnLocation] = useState(request.return_location ?? "");
  const [sending, setSending] = useState(false);
  const [printError, setPrintError] = useState("");
  const closed = ["КБ Выдана", "КБ Возвращена", "Готово", "Отменена"].includes(request.status);
  const hasIssuedLines = request.lines.some((line) => Number(line.issued) > 0);
  const waiting = openLines(request);
  const canPick = canEdit && showPick && !closed && request.status !== "Готов к выдаче";
  const meta: Array<[string, string]> = ([
    ["Дедлайн", day(request.deadline)],
    ["Создана", stamp(request.created_at)],
    ["Место выдачи", request.handout_location ?? ""],
    ["Место возврата", request.return_location ?? ""],
    ["Взята в работу", stamp(request.at_work)],
    ["Отправлена на выдачу", stamp(request.at_picked)],
    ["Выдана", stamp(request.at_issued)],
    ["Возвращена", stamp(request.at_returned)],
    ["Закрыта", stamp(request.at_closed)],
  ] as Array<[string, string]>).filter(([, value]) => value);

  async function print(load: () => Promise<{ sheet: Parameters<typeof openPrintSheet>[0] }>) {
    setPrintError("");
    try {
      const { sheet } = await load();
      openPrintSheet(sheet);
    } catch (reason) {
      setPrintError(reason instanceof Error ? reason.message : "Не удалось сформировать бланк");
    }
  }

  // issMove_: every stage move is confirmed with what it does; handing out writes stock off and only a return undoes it.
  function move(status: string, hint: string, operation: () => Promise<unknown>) {
    if (window.confirm(`Заявка ${request.number} → ${status}\n\n${hint}`)) void mutate(request.id, operation);
  }

  function remove() {
    const sure = window.confirm(`Удалить заявку ${request.number}? Строки заявки исчезнут совсем — это не «Отменена», в архив она не уедет. Резерв вернётся складу.`);
    if (sure) void mutate(request.id, () => fulfillmentApi.remove(request.id));
  }

  return (
    <article className="panel fulfillment-card">
      <div className="procurement-title"><div><strong>{request.number}</strong><small>{request.purpose} · {request.requester}</small></div><span className="status">{request.status}</span></div>
      {meta.length > 0 && <dl className="fulfillment-meta">{meta.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
      <p className="fulfillment-progress">Набрано {request.lines.length - waiting.length} из {request.lines.length} поз.{waiting.length > 0 && !closed ? ` · ждут ГРН: ${waiting.length}` : ""}</p>
      {request.shortfall && <details className="fulfillment-short"><summary>Недобор по составу</summary><div>{request.shortfall}</div></details>}
      <div className="table-wrap fulfillment-lines"><table className="grid wide"><thead><tr><th>Позиция</th><th className="num">Потребность</th><th className="num">Резерв</th><th className="num">Выдано</th><th>Партия</th><th>Место</th><th /></tr></thead><tbody>
        {request.lines.map((line) => <FulfillmentLineRow key={line.id} request={request} line={line} lots={lots} canEdit={canPick} busy={busy} mutate={mutate} />)}
      </tbody></table></div>
      <div className="fulfillment-actions">
        {canPick && ["Новая", "Недоукомплектован"].includes(request.status) && !waiting.length && <button className="secondary" disabled={busy} onClick={() => move("Укомплектован", "Комплект собран полностью. Отдать его в цех — отдельной кнопкой «Отправить на выдачу».", () => fulfillmentApi.complete(request.id, request.version))}>Укомплектован</button>}
        {canPick && request.status === "Укомплектован" && !sending && <button className="secondary" disabled={busy} onClick={() => setSending(true)}>Отправить на выдачу</button>}
        {canHandout && showGive && request.status === "Готов к выдаче" && <button className="primary" disabled={busy} onClick={() => move("КБ Выдана", "Количество спишется со склада (резерв → списано), заявка станет выданной. Отменить это можно только возвратом.", () => fulfillmentApi.issue(request.id, request.version))}>Выдать комплект</button>}
        {canHandout && showGive && request.status === "КБ Выдана" && <><label>Место возврата<input value={returnLocation} onChange={(event) => setReturnLocation(event.target.value)} /></label><button className="secondary" disabled={busy || !returnLocation.trim()} onClick={() => void mutate(request.id, () => fulfillmentApi.returned(request.id, request.version, returnLocation))}>Комплект вернулся</button><button className="secondary" disabled={busy} onClick={() => move("Готово", "Заявка закроется и уйдёт в архив.", () => fulfillmentApi.close(request.id, request.version))}>Готово</button></>}
        {canEdit && !closed && <button className="link-button danger-link" disabled={busy} onClick={() => move("Отменена", "Резерв освободится, заявка уйдёт в архив.", () => fulfillmentApi.cancel(request.id, request.version))}>Отменить заявку</button>}
        <button className="secondary small" onClick={() => void print(() => fulfillmentApi.printSheet(request.id))}>Печать заявки</button>
        {hasIssuedLines && <button className="secondary small" onClick={() => void print(() => fulfillmentApi.returnSheet(request.id))}>Печать листа возврата</button>}
        {isAdmin && <button className="link-button danger-link" disabled={busy} onClick={remove}>Удалить заявку</button>}
      </div>
      {sending && <HandoutPlaceForm request={request} places={places} busy={busy} mutate={mutate} onCancel={() => setSending(false)} />}
      {printError && <div className="error-banner" role="alert">{printError}</div>}
      {canHandout && showGive && request.status === "КБ Возвращена" && <ReturnForm request={request} busy={busy} mutate={mutate} />}
    </article>
  );
}

/* issSendToHandout_: the kit lies on a warehouse shelf, so the place is a rack address (rack / shelf / № / cell →
   placeCode_ on the server). Free text stays as a fallback for a place outside the layout. */
function HandoutPlaceForm({ request, places, busy, mutate, onCancel }: {
  request: FulfillmentRequest;
  places?: FulfillmentPlaces;
  busy: boolean;
  mutate: Mutate;
  onCancel: () => void;
}) {
  const [rack, setRack] = useState("");
  const [shelf, setShelf] = useState("");
  const [num, setNum] = useState("");
  const [slot, setSlot] = useState("");
  const [text, setText] = useState("");
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const shelvesIn = rack && places ? places.per_rack[rack] ?? places.shelves : 0;
  const address = rack || shelf || num || slot;
  const code = rack && shelf && Number(num) >= 1 ? `${rack}${shelf}${Number(num)}${slot}` : "";
  const ready = address ? Boolean(code) : Boolean(text.trim());

  function send() {
    const place = address ? { rack, shelf, num, slot } : text.trim();
    void mutate(request.id, () => fulfillmentApi.ready(request.id, request.version, place));
  }

  return <div className="return-form handout-place">
    <h3>Отправить на выдачу</h3>
    <p>Укажи, куда положен комплект: заявитель получит уведомление с этим адресом, и заявка уйдёт в раздел «Выдача».</p>
    {places && <div className="return-line">
      <label>Стеллаж<select aria-label="Стеллаж" value={rack} onChange={(event) => { setRack(event.target.value); setShelf(""); }}><option value="">—</option>{Array.from({ length: places.racks }, (_, index) => String(index + 1)).map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Полка<select aria-label="Полка" value={shelf} onChange={(event) => setShelf(event.target.value)}><option value="">—</option>{letters.slice(0, shelvesIn).split("").map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>№ на полке<input aria-label="№ на полке" type="number" min="1" step="1" value={num} onChange={(event) => setNum(event.target.value)} /></label>
      <label>Ячейка<select aria-label="Ячейка" value={slot} onChange={(event) => setSlot(event.target.value)}><option value="">—</option>{(places.slots || letters).split("").map((value) => <option key={value}>{value}</option>)}</select></label>
      <span>Адрес: <b>{code || "—"}</b></span>
    </div>}
    <label>{places ? "Или место текстом" : "Место выдачи"}<input aria-label="Место выдачи текстом" value={text} disabled={Boolean(address)} onChange={(event) => setText(event.target.value)} /></label>
    <div className="fulfillment-actions">
      <button className="primary" disabled={busy || !ready} onClick={send}>Отправить</button>
      <button className="secondary" onClick={onCancel}>Отмена</button>
    </div>
  </div>;
}

function FulfillmentLineRow({ request, line, lots, canEdit, busy, mutate }: {
  request: FulfillmentRequest;
  line: FulfillmentLine;
  lots: FulfillmentLot[];
  canEdit: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const [picking, setPicking] = useState(false);
  const off = isOffBook(line);
  return <>
    <tr>
      <td className="text"><strong>{line.article || "—"}</strong><small>{line.name}</small></td>
      <td className="num">{line.requirement} {line.unit}</td><td className="num">{line.reserved}</td><td className="num">{line.issued}</td>
      <td>{line.lot ? `${line.lot.grn}${line.lot.package ? ` · ${line.lot.package}` : ""}` : off ? <span className="status">есть, не заведено</span> : "Ждём поставки"}</td>
      <td>{line.lot ? line.lot.storage_location || "без места" : "—"}</td>
      <td>{canEdit && Number(line.issued) <= 0 && (line.lot
        ? <button className="link-button table-link" disabled={busy} onClick={() => void mutate(request.id, () => fulfillmentApi.unbind(request.id, line.id, request.version))}>Снять резерв</button>
        : off
          ? <button className="link-button table-link" disabled={busy} onClick={() => void mutate(request.id, () => bulkApi.offBook(request.id, line.id, request.version, false))}>Снять отметку</button>
          : <>
            <button className="secondary small" disabled={busy} aria-expanded={picking} onClick={() => setPicking((value) => !value)}>Выбрать ГРН</button>{" "}
            <button className="link-button table-link" disabled={busy} onClick={() => void mutate(request.id, () => bulkApi.offBook(request.id, line.id, request.version, true))}>Есть, не заведено</button>
          </>)}</td>
    </tr>
    {picking && !line.lot && <tr className="detail"><td colSpan={7}>
      <LotPicker line={line} lots={lots} busy={busy} onPick={(lotId) => void mutate(request.id, () => fulfillmentApi.bind(request.id, line.id, request.version, lotId))} />
    </td></tr>}
  </>;
}

/* stockPick_ / portalIssueStock: the whole warehouse with a balance, searched by words (stockPickMatch_: наименование,
   ГРН, пакет, место, склад, единица). The first opening is already filtered by the position's name — usually what is
   needed is on screen at once; if not, the person erases the words and searches their own way. */
function LotPicker({ line, lots, busy, onPick }: {
  line: FulfillmentLine;
  lots: FulfillmentLot[];
  busy: boolean;
  onPick: (lotId: string) => void;
}) {
  const [query, setQuery] = useState(line.name);
  const found = lots.filter((lot) => matchesWords([lot.article, lot.name, lot.grn, lot.package, lot.storage_location, lot.warehouse, lot.unit], query));
  const need = Number(line.requirement);
  return <div className="lot-picker">
    <input className="search" type="search" aria-label={`Поиск партии для ${line.name}`} placeholder="поиск: вилка usb · 3B4 · ГРН-2026 …" value={query} onChange={(event) => setQuery(event.target.value)} />
    {!found.length && <div className="empty">{lots.length ? `По запросу ничего нет. Сотри слова — покажется весь склад (${lots.length} строк).` : "На складе нет ни одной строки с остатком."}</div>}
    {found.length > 0 && <table className="grid wide"><thead><tr><th>Наименование</th><th>ГРН</th><th className="num">Доступно</th><th /></tr></thead><tbody>
      {found.slice(0, 300).map((lot) => {
        const low = need > 0 && Number(lot.available) < need;
        return <tr key={lot.id}>
          <td className="text"><strong>{lot.article || "—"}</strong> <span>{lot.name}</span><small>{lot.storage_location || "без места"}</small></td>
          <td>{lot.grn}<small>{lot.warehouse}{lot.package ? ` · пакет ${lot.package}` : ""}</small></td>
          <td className="num">{lot.available} {lot.unit}{low && <small>меньше нужного</small>}</td>
          <td><button className="secondary small" disabled={busy} aria-label={`Выбрать ${lot.grn}`} onClick={() => onPick(lot.id)}>Выбрать</button></td>
        </tr>;
      })}
    </tbody></table>}
    {found.length > 300 && <p className="page-sub">Показаны первые 300 из {found.length} — уточни поиск.</p>}
  </div>;
}

/** FIFO order of warehouse lots: the older receipt first. ERP keeps no receipt date on a lot, the ГРН is issued in
    order («FRX.26.0000012»: year, then number), so it stands in for the order of the SUPPLYMAP sheet rows. */
export function fifoOrder(a: FulfillmentLot, b: FulfillmentLot) {
  const key = (lot: FulfillmentLot) => {
    const match = /(\d{2})\.(\d+)$/.exec(lot.grn);
    return match ? [Number(match[1]), Number(match[2])] : [Number.MAX_SAFE_INTEGER, 0];
  };
  const [yearA, numberA] = key(a);
  const [yearB, numberB] = key(b);
  return yearA - yearB || numberA - numberB || a.grn.localeCompare(b.grn) || a.package.localeCompare(b.package, "ru", { numeric: true });
}

/** SUPPLYMAP fifoPick_: from the first delivery to the last until the nominal quantity is reached. */
export function fifoPick(rows: Array<{ lot: FulfillmentLot; free: number }>, need: number) {
  let rest = need;
  const picks: Array<{ lot: FulfillmentLot; qty: number }> = [];
  if (!(need > 0)) return { picks, short: 0 };
  for (const row of rows) {
    if (rest <= 0.0001) break;
    if (!(row.free > 0)) continue;
    const take = Math.min(row.free, rest);
    picks.push({ lot: row.lot, qty: Math.round(take * 100) / 100 });
    rest = Math.round((rest - take) * 100) / 100;
  }
  return { picks, short: Math.max(rest, 0) };
}

/* Ручная заявка (SUPPLYMAP IssueForm.html → queueIssueRequest → issueCreateTasks_): назначение, дедлайн, склад; поиск
   по складу даёт наименования с доступным остатком, количество подбирается FIFO по партиям этого наименования,
   нехватка показывается как недобор. «Оформить» шлёт выбранные партии: сервер резервирует их по свежему остатку, чего
   не хватило — строкой «ждём поставки». Idempotency-Key живёт на форму: повтор отправки не плодит дубль. */
function CreateRequestForm({ lots, onClose, onCreated }: { lots: FulfillmentLot[]; onClose: () => void; onCreated: () => Promise<void> }) {
  const key = useRef(crypto.randomUUID());
  // WAREHOUSE_STOCK_SHEETS order: ЦАР, ЖУК; any other warehouse after them.
  const warehouses = [...new Set(["ЦАР", "ЖУК", ...lots.map((lot) => lot.warehouse)])].filter((name) => lots.some((lot) => lot.warehouse === name));
  const [purpose, setPurpose] = useState("");
  const [deadline, setDeadline] = useState("");
  const [warehouse, setWarehouse] = useState(warehouses[0] ?? "");
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [quantity, setQuantity] = useState("");
  const [added, setAdded] = useState<Array<{ lot: FulfillmentLot; qty: number }>>([]);
  const [shorts, setShorts] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const title = (lot: FulfillmentLot) => lot.article || lot.name;
  const norm = (value: string) => value.toLocaleLowerCase("ru").replace(/ё/g, "е").trim();
  const taken = (lot: FulfillmentLot) => added.filter((pick) => pick.lot.id === lot.id).reduce((sum, pick) => sum + pick.qty, 0);
  const free = (lot: FulfillmentLot) => Math.round((Number(lot.available) - taken(lot)) * 100) / 100;
  const here = lots.filter((lot) => lot.warehouse === (warehouse || warehouses[0]));
  const names = (() => {
    const wanted = norm(query);
    const byKey = new Map<string, { name: string; unit: string; avail: number }>();
    for (const lot of here) {
      if (wanted && !norm(title(lot)).includes(wanted) && !norm(lot.name).includes(wanted) && !norm(lot.grn).includes(wanted)) continue;
      const itemKey = `${norm(title(lot))}|${lot.unit}`;
      const item = byKey.get(itemKey) ?? { name: title(lot), unit: lot.unit, avail: 0 };
      item.avail = Math.round((item.avail + free(lot)) * 100) / 100;
      byKey.set(itemKey, item);
    }
    return [...byKey.values()];
  })();
  const chosenAvail = chosen ? here.filter((lot) => norm(title(lot)) === norm(chosen)).reduce((sum, lot) => sum + free(lot), 0) : 0;

  function add() {
    if (!chosen) return;
    const rows = here.filter((lot) => norm(title(lot)) === norm(chosen)).sort(fifoOrder).map((lot) => ({ lot, free: free(lot) }));
    const result = fifoPick(rows, Number(quantity));
    setAdded((current) => [...current, ...result.picks]);
    if (result.short > 0) setShorts((current) => [...current, `${chosen}: −${result.short}`]);
    setQuantity("");
    setChosen(null);
  }

  async function submit() {
    setError("");
    if (!purpose.trim()) { setError("Укажи назначение."); return; }
    if (!deadline) { setError("Укажи дедлайн."); return; }
    if (!added.length) { setError("Нет позиций для выдачи."); return; }
    setSaving(true);
    try {
      await fulfillmentApi.create({
        purpose: purpose.trim(),
        deadline,
        lines: added.map((pick) => ({
          article: pick.lot.article ?? "", name: pick.lot.name || pick.lot.article || "—", unit: pick.lot.unit || "шт",
          requirement: String(pick.qty), lot_id: pick.lot.id,
        })),
      }, key.current);
      key.current = crypto.randomUUID();
      await onCreated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Заявка не создана");
    } finally {
      setSaving(false);
    }
  }

  return <div className="panel fulfillment-create" role="form" aria-label="Новая заявка на комплектацию">
    <h3>Заявка на выдачу КБ</h3>
    <div className="return-line">
      <label>Назначение *<input aria-label="Назначение" placeholder="напр. 48К.13" value={purpose} onChange={(event) => setPurpose(event.target.value)} /></label>
      <label>Дедлайн *<input aria-label="Дедлайн" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
      <label>Склад<select aria-label="Склад" value={warehouse} onChange={(event) => { setWarehouse(event.target.value); setChosen(null); }}>{warehouses.map((value) => <option key={value}>{value}</option>)}</select></label>
    </div>
    <div className="return-line">
      <label>Поиск<input aria-label="Поиск по складу" placeholder="Поиск: артикул / наименование" value={query} onChange={(event) => { setQuery(event.target.value); setChosen(null); }} /></label>
      <label>Кол-во<input aria-label="Кол-во" type="number" min="0" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
      <button type="button" className="secondary small" disabled={!chosen || !(Number(quantity) > 0)} onClick={add}>Добавить</button>
    </div>
    <div className="lot-picker" role="listbox" aria-label="Наименования на складе">
      {names.slice(0, 40).map((item) => <button type="button" role="option" aria-selected={chosen === item.name} key={`${item.name}|${item.unit}`} className={chosen === item.name ? "secondary small active" : "secondary small"} onClick={() => setChosen(item.name)}>{item.name} — доступно {item.avail} {item.unit}</button>)}
      {!names.length && <span className="page-sub">Ничего не найдено (или всё в резерве)</span>}
      {chosen && <span className="page-sub">Выбрано: {chosen} · доступно {Math.round(chosenAvail * 100) / 100}</span>}
    </div>
    <h4>Позиции заявки (FIFO — от первой поставки)</h4>
    <div className="table-wrap"><table className="grid wide"><thead><tr><th>Наименование</th><th>ГРН</th><th>Пакет</th><th className="num">Кол-во</th><th>Место</th><th /></tr></thead><tbody>
      {added.map((pick, index) => <tr key={`${pick.lot.id}:${index}`}>
        <td>{title(pick.lot)}</td><td>{pick.lot.grn}</td><td>{pick.lot.package}</td><td className="num">{pick.qty} {pick.lot.unit}</td><td>{pick.lot.storage_location}</td>
        <td><button type="button" className="link-button danger-link" aria-label={`Удалить позицию ${index + 1}`} onClick={() => setAdded((current) => current.filter((_, at) => at !== index))}>✕</button></td>
      </tr>)}
    </tbody></table></div>
    {shorts.length > 0 && <p className="fulfillment-progress">Недобор (на складе меньше запрошенного): {shorts.join("; ")}</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="fulfillment-actions">
      <button type="button" className="primary" disabled={saving || !added.length} onClick={() => void submit()}>Оформить заявку</button>
      <button type="button" className="secondary" onClick={onClose}>Отмена</button>
    </div>
  </div>;
}

/* «Заявка по расчёту» (SUPPLYMAP IssueCalc.html → issueCalcPreview → queueIssueRequest): ссылка на расчёт (Google
   Таблица с листом ЗКП) и склад → позиции расчёта, подобранные по складу FIFO: подобрано / недобор / нет на складе.
   «Оформить» шлёт подобранные партии, а позиции, которых нет на складе, — пометкой в НЕДОБОР заявки. */
function CalcRequestForm({ onClose, onCreated }: { onClose: () => void; onCreated: () => Promise<void> }) {
  const key = useRef(crypto.randomUUID());
  const [url, setUrl] = useState("");
  const [purpose, setPurpose] = useState("");
  const [deadline, setDeadline] = useState("");
  const [warehouse, setWarehouse] = useState("ЦАР");
  const [lines, setLines] = useState<FulfillmentCalcLine[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function pick() {
    setError("");
    setMessage("");
    if (!url.trim()) { setError("Вставь ссылку на расчёт."); return; }
    setBusy(true);
    setMessage("Читаю расчёт и подбираю по складу…");
    try {
      const result = await fulfillmentApi.calcPreview(url.trim(), warehouse);
      setLines(result.lines);
      if (result.project && !purpose) setPurpose(result.project);
      setMessage("");
    } catch (reason) {
      setMessage("");
      setError(reason instanceof Error ? reason.message : "Расчёт не прочитан");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setError("");
    if (!lines) return;
    if (!purpose.trim()) { setError("Укажи назначение."); return; }
    if (!deadline) { setError("Укажи дедлайн."); return; }
    const picked = lines.flatMap((line) => line.picks.map((item) => ({
      article: item.article, name: item.name || item.article || line.name, unit: item.unit || line.unit || "шт",
      requirement: item.qty, lot_id: item.lot_id,
    })));
    const shortNote = lines.filter((line) => !line.found).map((line) => `${line.name}: нет на складе (−${line.qty})`);
    setBusy(true);
    try {
      await fulfillmentApi.create({ purpose: purpose.trim(), deadline, lines: picked, short_note: shortNote }, key.current);
      key.current = crypto.randomUUID();
      await onCreated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Заявка не создана");
    } finally {
      setBusy(false);
    }
  }

  const full = lines?.filter((line) => line.found && !(Number(line.short) > 0)).length ?? 0;
  const part = lines?.filter((line) => line.found && Number(line.short) > 0).length ?? 0;
  const missing = lines?.filter((line) => !line.found).length ?? 0;

  return <div className="panel fulfillment-create" role="form" aria-label="Заявка по расчёту">
    <h3>Заявка на выдачу по расчёту</h3>
    <div className="return-line">
      <label>Ссылка на расчёт<input aria-label="Ссылка на расчёт" placeholder="Ссылка на расчёт (Google Таблица с листом ЗКП)" value={url} onChange={(event) => setUrl(event.target.value)} /></label>
      <label>Склад<select aria-label="Склад расчёта" value={warehouse} onChange={(event) => {
        setWarehouse(event.target.value);
        if (lines) { setLines(null); setMessage("Склад сменился — нажми «Подобрать» заново."); }
      }}><option>ЦАР</option><option>ЖУК</option></select></label>
      <button type="button" className="secondary small" disabled={busy} onClick={() => void pick()}>Подобрать</button>
    </div>
    <div className="return-line">
      <label>Назначение *<input aria-label="Назначение по расчёту" value={purpose} onChange={(event) => setPurpose(event.target.value)} /></label>
      <label>Дедлайн *<input aria-label="Дедлайн по расчёту" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
    </div>
    {message && <p className="fulfillment-progress" role="status">{message}</p>}
    {lines && <>
      <p className="fulfillment-progress">Позиций: {lines.length} · полностью: {full} · с недобором: {part} · нет на складе: {missing}</p>
      <div className="table-wrap"><table className="grid wide"><thead><tr><th>Позиция расчёта</th><th className="num">Нужно</th><th>Подобрано (FIFO)</th><th>Статус</th></tr></thead><tbody>
        {lines.map((line, index) => <tr key={`${line.name}:${index}`}>
          <td>{line.name}</td><td className="num">{line.qty} {line.unit}</td>
          <td>{line.picks.map((item) => `${item.grn}${item.package ? `·п${item.package}` : ""}: ${item.qty}${item.reserved ? ` (резерв под заявку ${item.reserved})` : ""}`).join(", ") || "—"}</td>
          <td>{!line.found ? "нет на складе" : Number(line.short) > 0 ? `недобор ${line.short}` : "✓"}</td>
        </tr>)}
      </tbody></table></div>
    </>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="fulfillment-actions">
      <button type="button" className="primary" disabled={busy || !lines?.some((line) => line.picks.length)} onClick={() => void send()}>Оформить заявку</button>
      <button type="button" className="secondary" onClick={onClose}>Отмена</button>
    </div>
  </div>;
}

type ReturnValues = {
  returned_good: string;
  defective_returned: string;
  defective_missing: string;
  storage_location: string;
};

function ReturnForm({ request, busy, mutate }: {
  request: FulfillmentRequest;
  busy: boolean;
  mutate: Mutate;
}) {
  const [values, setValues] = useState<Record<string, ReturnValues>>(() => Object.fromEntries(
    request.lines.map((line) => [line.id, {
      returned_good: line.returned_good,
      defective_returned: line.defective_returned,
      defective_missing: line.defective_missing,
      storage_location: line.lot?.storage_location ?? "",
    }]),
  ));

  function change(lineId: string, field: keyof ReturnValues, value: string) {
    setValues((current) => ({
      ...current,
      [lineId]: { ...current[lineId], [field]: value },
    }));
  }

  const payload = request.lines.map((line) => ({
    line_id: line.id,
    returned_good: values[line.id]?.returned_good || "0",
    defective_returned: values[line.id]?.defective_returned || "0",
    defective_missing: values[line.id]?.defective_missing || "0",
    storage_location: values[line.id]?.storage_location.trim() || null,
  }));

  return <div className="return-form">
    <h3>Разбор возвращённого комплекта</h3>
    <p>Укажи годное, два вида брака и новое место. Остаток от выданного будет зафиксирован как израсходованный.</p>
    {request.lines.map((line) => <div className="return-line" key={line.id}>
      <strong>{line.article || line.name}</strong><span>выдано {line.issued} {line.unit}</span>
      <label>Годное<input aria-label={`Годное ${line.name}`} type="number" min="0" step="any" value={values[line.id]?.returned_good ?? "0"} onChange={(event) => change(line.id, "returned_good", event.target.value)} /></label>
      <label>Брак возвращён<input aria-label={`Брак возвращён ${line.name}`} type="number" min="0" step="any" value={values[line.id]?.defective_returned ?? "0"} onChange={(event) => change(line.id, "defective_returned", event.target.value)} /></label>
      <label>Брак не возвращён<input aria-label={`Брак не возвращён ${line.name}`} type="number" min="0" step="any" value={values[line.id]?.defective_missing ?? "0"} onChange={(event) => change(line.id, "defective_missing", event.target.value)} /></label>
      <label>Место хранения<input aria-label={`Место хранения ${line.name}`} value={values[line.id]?.storage_location ?? ""} onChange={(event) => change(line.id, "storage_location", event.target.value)} /></label>
    </div>)}
    <button className="primary" disabled={busy} onClick={() => void mutate(request.id, () => fulfillmentApi.completeReturn(request.id, request.version, payload))}>Провести возврат</button>
  </div>;
}
