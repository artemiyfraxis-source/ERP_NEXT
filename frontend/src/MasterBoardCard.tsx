import type { KitsetTile } from "./api";
import { formatDateTime } from "./format";
import { tcProgress } from "./ProjectCard";
import { deadlineChips, type DeadlineChip } from "./productionGraph";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";

/* Карточка проекта на доске мастера (владелец 29.10: «пересмотреть формат карточек — инфы много, она разная, ООО такое
   маленькое»). Одна высота у всех: шапка (проект, заказчик, ДЛ), один чип статуса, одна строка «к запуску / без
   исполнителя», проблемы — счётчиками. Списки КБ и операций — только в подсказках и в окне проекта. */

type FlagKey = "late" | "wait" | "over";

export type BoardCardInput = {
  project: string;
  /** Плитка проекта из «Проектов»; нет — проект знают только задания (этап неизвестен). */
  tile?: KitsetTile;
  /** Этапы проекта по порядку (сервер `statuses`): у отгруженных и отменённых факты про ТК и набор не нужны. */
  statuses: string[];
  /** Заказчик, если плитки нет (из КБ к запуску). */
  customer?: string;
  plan?: { deadline: string | null; days_left: number | null };
  taskDeadline: string;
  /** Операции без исполнителя и все операции заданий, не отданных на площадку. */
  free: number;
  total: number;
  toLaunch: number;
  handed: number;
  handedSite: string;
  /** Операции проекта с каждой отметкой — подписи для подсказки. */
  flags: Record<FlagKey, string[]>;
  now?: Date;
};

export type BoardCardLine = { text: string; tone: Tone; title: string; count?: number };

export type BoardCardModel = {
  project: string;
  customer: string;
  headTitle: string;
  deadline: DeadlineChip | null;
  deadlineTitle: string;
  status: { word: string; title: string };
  actions: BoardCardLine[];
  problems: BoardCardLine[];
  /** 0 — горит (блокер, срок, просроченные операции, ТК с погрешностями), 1 — есть замечания, 2 — чисто. */
  rank: 0 | 1 | 2;
};

const TONE_WEIGHT: Record<Tone, number> = { danger: 3, warning: 2, info: 1, success: 0, neutral: 0 };

function kbList(kbs: string[]) {
  return kbs.join(", ");
}

/** Подсказка со списком операций: первые 12 строк и «… ещё N» — подсказка не должна быть выше экрана. */
const OPS_IN_TITLE = 12;
function opsTitle(head: string, ops: string[]) {
  const rest = ops.length - OPS_IN_TITLE;
  return [head, ...ops.slice(0, OPS_IN_TITLE), ...(rest > 0 ? [`… ещё ${rest}`] : [])].join("\n");
}

/** Этап проекта, отметки ТК / набора и этапы КБ — всё, что раньше стояло на карточке строками, теперь в подсказке чипа. */
function statusTitle(tile: KitsetTile) {
  const lines = [`Этап проекта: ${tile.status || "—"}`];
  if (tile.blocker) lines.push(`Блокер: ${tile.blocker}`);
  const marks = [
    tile.tc_ready === undefined ? "" : tile.tc_ready ? "ТК готова" : "ТК не готова",
    tile.kit_picked === undefined ? "" : tile.kit_picked ? "КБ набрана" : "КБ не набрана",
  ].filter(Boolean);
  if (marks.length) lines.push(marks.join(" · "));
  const byStage = new Map<string, string[]>();
  for (const kb of tile.kb_stages) byStage.set(kb.status, [...(byStage.get(kb.status) ?? []), kb.index]);
  if (byStage.size > 1) for (const [stage, kbs] of byStage) lines.push(`${stage}: ${kbList(kbs)}`);
  return lines.join("\n");
}

export function boardCard(input: BoardCardInput): BoardCardModel {
  const { tile, project } = input;
  const customer = tile?.customer || input.customer || "";
  const chips = deadlineChips(input.plan, input.taskDeadline, input.now);
  // В шапке — самый горящий срок; при равном тоне — дедлайн проекта. Оба срока — в подсказке.
  const deadline = chips.reduce<DeadlineChip | null>((best, chip) => !best || TONE_WEIGHT[chip.tone] > TONE_WEIGHT[best.tone] ? chip : best, null);
  const deadlineTitle = chips.length ? chips.map((chip) => chip.title).join("\n") : "Дедлайна нет в производственном плане";

  const headTitle = [
    customer && `Заказчик: ${customer}`,
    tile && `сборок ${tile.assemblies} · позиций ${tile.lines}`,
    tile?.updated_at && `обновлено ${formatDateTime(tile.updated_at)}${tile.updated_by ? `, ${tile.updated_by}` : ""}`,
  ].filter(Boolean).join("\n");

  // Один чип: блокер (СТОП, ОЖИД КОМПЛ, ОЖИД ИНФ…) важнее этапа; этап — Новый … В работе … Отгружено.
  const status = tile
    ? { word: tile.blocker || tile.status, title: statusTitle(tile) }
    : { word: "", title: "Проекта нет в «Проектах» — этап неизвестен" };

  const actions: BoardCardLine[] = [];
  if (input.toLaunch > 0) actions.push({ text: `к запуску ${input.toLaunch}`, tone: "info", title: "КБ с выданной техкартой: задания нет или запущен не весь тираж" });
  if (input.total > 0) {
    actions.push(input.free
      ? { text: `без исполнителя ${input.free} из ${input.total}`, tone: "warning", title: "Операции заданий без монтажника" }
      : { text: `распределено ${input.total}`, tone: "success", title: "У всех операций заданий есть монтажник" });
  }
  if (input.handed > 0) actions.push({ text: `в ${input.handedSite}: ${input.handed}`, tone: "warning", title: `Заданий отдано на площадку ${input.handedSite}` });

  const problems: BoardCardLine[] = [];
  const push = (text: string, count: number, tone: Tone, title: string) => { if (count > 0) problems.push({ text, count, tone, title }); };
  push("план истёк", input.flags.late.length, "danger", opsTitle("Не начато, план истёк:", input.flags.late));
  push("позже срока", input.flags.over.length, "danger", opsTitle("План позже срока задания:", input.flags.over));
  const index = tile ? input.statuses.indexOf(tile.status) : -1;
  const shipped = input.statuses.indexOf("Отгружено");
  // «Отменён» стоит после «Отгружено»: у закрытого проекта факты про ТК и набор не нужны.
  if (tile && !(shipped >= 0 && index > shipped)) {
    const kbs = tile.kb_stages;
    const withTc = kbs.filter((kb) => kb.tc);
    const defects = withTc.filter((kb) => kb.review?.value === "с погрешностями").map((kb) => kb.index);
    const unreviewed = withTc.filter((kb) => !(kb.review?.value && kb.review.value !== "не проверена")).map((kb) => kb.index);
    const noTc = tile.tc_ready ? [] : kbs.filter((kb) => !kb.tc).map((kb) => kb.index);
    const notPicked = tile.kit_picked ? [] : kbs.filter((kb) => !kb.kit).map((kb) => kb.index);
    push("ТК с погрешностями", defects.length, "danger", `ТК с погрешностями: ${kbList(defects)}`);
    push("ТК не выдана", noTc.length, "warning", `ТК не выдана (${tcProgress(tile).tc} из ${kbs.length} КБ с ТК): ${kbList(noTc)}`);
    push("ТК не проверена", unreviewed.length, "warning", `ТК не проверена: ${kbList(unreviewed)}`);
    push("КБ не набрана", notPicked.length, "warning", `КБ не набрана: ${kbList(notPicked)}`);
  }
  push("ждёт ОТК", input.flags.wait.length, "warning", opsTitle("Ждут ОТК:", input.flags.wait));
  problems.sort((a, b) => TONE_WEIGHT[b.tone] - TONE_WEIGHT[a.tone]);

  const burning = Boolean(tile?.blocker) || (deadline !== null && deadline.tone !== "neutral") || problems.some((line) => line.tone === "danger");
  const rank = burning ? 0 : problems.length ? 1 : 2;
  return { project, customer, headTitle, deadline, deadlineTitle, status, actions, problems, rank };
}

/** Счётчик проблемы: «ТК не проверена: 3 КБ», «КБ не набрана: 2», «план истёк: 1». */
function problemText(line: BoardCardLine) {
  return line.text.startsWith("ТК ") ? `${line.count} КБ` : String(line.count);
}

export function BoardProjectCard({ model, open }: { model: BoardCardModel; open: () => void }) {
  return <div className="mb-card" role="button" tabIndex={0} data-rank={model.rank} onClick={open}
    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
    <div className="mb-card-head" title={model.headTitle || undefined}>
      <strong className="mb-card-no">{model.project}</strong>
      <span className="mb-card-due" data-tone={model.deadline?.tone ?? "neutral"} title={model.deadlineTitle}>
        {model.deadline?.text ?? "ДЛ —"}
      </span>
    </div>
    <div className="mb-card-customer" title={model.customer || undefined}>{model.customer || "—"}</div>
    <div className="mb-card-status"><StatusChip status={model.status.word} title={model.status.title} /></div>
    <div className="mb-card-action">
      {model.actions.map((line) => <span key={line.text} data-tone={line.tone} title={line.title}>{line.text}</span>)}
    </div>
    <div className="mb-card-problems">
      {model.problems.map((line) => <span key={line.text} data-tone={line.tone} title={line.title}>
        {line.text}: <b>{problemText(line)}</b>
      </span>)}
    </div>
  </div>;
}
