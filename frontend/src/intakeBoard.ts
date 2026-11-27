import type { ProjectAddressee } from "./api";
import { isPickup, needsTrack } from "./carriers";
import { shipmentDispatchStage } from "./dispatchStage";
import { formatDayMonth } from "./format";
import { isoDay } from "./Gantt";
import { dispatchBoard, kbKey, num, packable, permittedPack, recentShipment, round2 } from "./intakeStages";
import type { DispatchShipment, IntakeRow } from "./productionIntakeApi";
import { readyForTrack } from "./ShipmentCard";
import { shipmentGroupKey, shipmentSequence } from "./ShipmentGroupHeader";
import { shipmentSearch } from "./shipmentSearch";
import { shipmentTitle } from "./shipmentTitle";

/* Доска «Приёмка с производства» (владелец 27.11, вариант «В»): карточка — проект или отправка на своей стадии.
   Здесь — чистая логика: из КБ в работе и отправок получаются карточки колонок, каждая со строкой «кто это», одним
   следующим действием и строкой «почему нельзя дальше» (что именно не внесено и кто вносит). Без количеств и без «ОТГ». */

export type ColumnId = "production" | "accepted" | "packed" | "to_ship" | "shipped";
export const COLUMNS: ReadonlyArray<readonly [ColumnId, string]> = [
  ["production", "Производство"], ["accepted", "Принято"], ["packed", "Упаковано"], ["to_ship", "На отгрузку"], ["shipped", "Отгружено"],
];
export type ProductionGroup = "production" | "waiting";
export const GROUPS: ReadonlyArray<readonly [ProductionGroup, string]> = [["production", "В производстве"], ["waiting", "В ожидании"]];
/** Сколько карточек колонка показывает сразу и сколько добавляет «ещё N». */
export const PAGE_SIZE = 20;
const HOT_DAYS = 3;

export type ActionId = "accept" | "pack" | "permit" | "make-pl" | "make-dn" | "fill" | "enter-upd" | "enter-upd-number" | "enter-upd-file"
  | "enter-track" | "ship";
export interface CardAction { id: ActionId; label: string; disabled?: boolean; note?: string }
export interface Blocker { text: string; who: string }
export interface BoardRights { canManage: boolean; canWarehouse: boolean; canDispatch: boolean }

export interface BoardCard {
  id: string;
  column: ColumnId;
  group?: ProductionGroup;
  project: string;
  customer: string;
  /** КБ карточки: для проекта — строки приёмки; для отправки — пусто (строки в `shipment.lines`). */
  kbs: IntakeRow[];
  shipment?: DispatchShipment;
  /** «КБ3 Жгут для …» / «КБ1 …, КБ2 …, КБ5 … и ещё 2». */
  kbLine: string;
  /** «СДЭК · срок 02.12», «самовывоз · срок —», «СДЭК · отгружено 27.11». */
  deliveryLine: string;
  /** Код PL — с «Упаковано». */
  code: string;
  /** «отгрузка 1 из 2», только когда у проекта несколько живых отправок. */
  sequence: string;
  /** Что цех сдал и ещё что важно на карточке («сдано цехом 12»). */
  note: string;
  blocker: Blocker | null;
  /** «✓ всё заполнено» / «✓ закрыта»: у карточки нечего вносить. */
  okText: string;
  action: CardAction | null;
  /** Второе, не главное действие меню «⋯». */
  extra: CardAction[];
  deadline: string | null;
  /** Данных не хватает — чип «Только с пробелами». */
  gaps: boolean;
  search: string;
  sortDay: string;
}

const norm = (text: string) => text.trim().toUpperCase();
const SHORT = 24;
const short = (name: string, limit = SHORT) => name.length > limit ? `${name.slice(0, limit - 1).trimEnd()}…` : name;

/** Строка КБ: одна — с полным коротким именем, до трёх — «КБ1 Имя, КБ2 Имя», остальные — «и ещё N». */
export function kbSummary(items: Array<{ item_index: string; name: string }>): string {
  const unique = [...new Map(items.map((item) => [item.item_index, item])).values()];
  const shown = unique.slice(0, 3).map((item) => `${item.item_index} ${short(item.name, unique.length === 1 ? 44 : 20)}`.trim());
  return shown.join(", ") + (unique.length > 3 ? ` и ещё ${unique.length - 3}` : "");
}

function deliveryText(source: { terms?: string | null; carrier?: string | null } | undefined): string {
  if (!source) return "доставка не выбрана";
  if (isPickup(source.carrier ?? "") || source.terms === "САМОВЫВОЗ") return "самовывоз";
  return source.carrier?.trim() || (source.terms === "ДОСТАВКА ТК" ? "ТК не выбрана" : "доставка не выбрана");
}
const dueText = (deadline: string | null | undefined) => `срок ${deadline ? formatDayMonth(deadline) : "—"}`;

/** Слова сервера → кто вносит. Трек вписывает склад после УПД и ТК, «без УПД» разрешает уполномоченный по ПИН. */
const TRACK_GAP = "трек-номер";
const APPROVAL_GAP = "разрешение на отгрузку без УПД";
export function shipmentGaps(shipment: DispatchShipment): string[] {
  return [...new Set([...(shipment.missing_data ?? []), ...(shipment.missing_ship ?? [])])];
}
function whoFills(gaps: string[]): string {
  const others = gaps.filter((gap) => gap !== TRACK_GAP && gap !== APPROVAL_GAP);
  if (others.length) return "вносит склад или менеджер";
  if (gaps.includes(APPROVAL_GAP)) return "разрешает уполномоченный по ПИН";
  return "вписывает склад после УПД и ТК";
}

export function updGapAction(shipment: DispatchShipment): CardAction | null {
  const gaps = new Set(shipment.missing_ship ?? []);
  if (gaps.has("тип УПД")) return { id: "enter-upd", label: "Внести УПД…" };
  if (gaps.has("№ УПД")) return { id: "enter-upd-number", label: "Вписать № УПД…" };
  if (gaps.has("файл УПД")) return { id: "enter-upd-file", label: "Загрузить УПД…" };
  return null;
}

/** Отправка до отгрузки: PL → DN → УПД → трек; блокер называет, чего нет и кто вносит. */
export function shipmentBlocker(shipment: DispatchShipment, closing = false): Blocker | null {
  const gaps = shipmentGaps(shipment);
  const docs: string[] = [];
  if (!shipment.pl_number) docs.push("PL");
  else if (!shipment.dn_number && !gaps.some((gap) => /DN/i.test(gap))) docs.push("DN");
  const list = [...docs, ...gaps];
  if (!list.length) return closing ? { text: "не закрыта", who: "закроется сама, когда всё внесено" } : null;
  const who = docs.length ? "формирует склад или менеджер" + (gaps.length ? "; данные вносит склад или менеджер" : "") : whoFills(gaps);
  return { text: `${closing ? "не закрыта — " : ""}нет: ${list.join(", ")}`, who: closing ? "закроется сама: " + who : who };
}

export interface BuildInput {
  rows: IntakeRow[];
  shipments: DispatchShipment[];
  addressees: Array<Pick<ProjectAddressee, "project" | "terms" | "carrier">>;
  rights: BoardRights;
  now?: Date;
}

/** Все карточки доски. Порядок внутри колонки: по сроку (просроченные выше), «Отгружено» — по дате отгрузки, новые сверху. */
export function buildBoard({ rows, shipments, addressees, rights, now = new Date() }: BuildInput): BoardCard[] {
  const board = dispatchBoard(rows, shipments, now);
  const cardOf = new Map(addressees.map((card) => [norm(card.project), card]));
  const cards: BoardCard[] = [];
  const live = shipments.filter((shipment) => ["Новая", "Собрана", "Упакована"].includes(shipment.status));
  const sequences = shipmentSequence(live.flatMap((shipment) => shipment.lines.map((line) => ({ project: line.project, shipment }))));
  const nextDeadline = (list: IntakeRow[]) => list.map((kb) => kb.deadline).filter((day): day is string => Boolean(day)).sort()[0] ?? null;
  const byProject = new Map<string, IntakeRow[]>();
  for (const row of board.rows) byProject.set(row.project, [...(byProject.get(row.project) ?? []), row]);
  const projectSearch = (kbs: IntakeRow[]) => kbs.flatMap((kb) => [kb.project, kb.customer, kb.item_index, kb.name, kb.grn ?? "", ...(kb.acts ?? []).map((act) => act.grn),
    ...kb.tasks.map((task) => task.number)]).join(" ");

  // Производство и «Принято»: одна карточка на проект и стадию.
  for (const [project, kbs] of byProject) {
    const customer = kbs[0].customer;
    const piecesOf = (kb: IntakeRow) => board.pieces.get(kbKey(kb))!;
    const delivery = deliveryText(cardOf.get(norm(project)));
    const base = { project, customer, code: "", sequence: "", gaps: false, sortDay: "" };
    for (const [group] of [["production"], ["waiting"]] as const) {
      const here = kbs.filter((kb) => group === "production" ? piecesOf(kb).production > 0 || piecesOf(kb).ready > 0 : piecesOf(kb).waiting > 0);
      if (!here.length) continue;
      const handed = round2(here.reduce((sum, kb) => sum + Math.max(num(kb.delivered) - num(kb.accepted), 0), 0));
      const acceptable = here.filter((kb) => piecesOf(kb).ready > 0 || (group === "production" && num(kb.remaining) > 0));
      cards.push({ ...base, id: `p:${project}:${group}`, column: "production", group, kbs: here, kbLine: kbSummary(here),
        deliveryLine: `${delivery} · ${dueText(nextDeadline(here))}`, note: group === "waiting" ? "ждёт запуска в цех" : handed > 0 ? `сдано цехом ${handed}` : "цех пока ничего не сдал",
        blocker: null, okText: "", deadline: nextDeadline(here), search: projectSearch(here),
        action: group === "production" && rights.canWarehouse && acceptable.length ? { id: "accept", label: "Принять на склад…" } : null, extra: [] });
    }
    const stock = kbs.filter((kb) => packable(kb) > 0);
    if (stock.length) {
      const packNow = stock.filter((kb) => permittedPack(kb) > 0 && kb.good_id);
      const blocked = stock.filter((kb) => !(permittedPack(kb) > 0 && kb.good_id) && !(kb as IntakeRow & { pack_auto_allowed?: boolean }).pack_auto_allowed);
      const permit: CardAction = { id: "permit", label: "Разрешить упаковку…" };
      cards.push({ ...base, id: `p:${project}:accepted`, column: "accepted", kbs: stock, kbLine: kbSummary(stock), deliveryLine: `${delivery} · ${dueText(nextDeadline(stock))}`,
        note: "", okText: "", deadline: nextDeadline(stock), search: projectSearch(stock),
        blocker: blocked.length ? { text: `${blocked.map((kb) => kb.item_index).slice(0, 4).join(", ")}: нет разрешения упаковки`, who: "даёт менеджер (раздел «Отгрузка»)" } : null,
        action: rights.canDispatch && packNow.length ? { id: "pack", label: `Упаковать ${packNow.map((kb) => kb.item_index).slice(0, 3).join(", ")}${packNow.length > 3 ? " и др." : ""}…` }
          : rights.canManage && blocked.length ? permit : null,
        extra: rights.canManage && blocked.length && packNow.length ? [permit] : [] });
    }
  }

  // Отправки: стадия — по статусу и готовности; «Отгружено» — за 30 дней.
  for (const shipment of shipments) {
    const stage = shipmentDispatchStage(shipment, "Принято складом");
    if (!stage || stage === "waiting" || stage === "production" || stage === "ready") continue;
    if (stage === "shipped" && !recentShipment(shipment, now)) continue;
    const projects = [...new Set(shipment.lines.map((line) => line.project))];
    const first = projects[0] ?? "";
    const kbs = shipment.lines.filter((line) => num(line.picked) > 0 || shipment.lines.every((other) => !(num(other.picked) > 0)));
    const column: ColumnId = stage === "accepted" ? "accepted" : stage;
    const closed = shipment.status === "Закрыта";
    const gaps = shipmentGaps(shipment);
    const pickupLike = isPickup(shipment.carrier) || shipment.terms === "САМОВЫВОЗ";
    const trackWord = shipment.track_number.trim() ? ` · трек ${shipment.track_number.trim()}` : "";
    const delivery = deliveryText(shipment);
    let blocker: Blocker | null = null; let action: CardAction | null = null; const extra: CardAction[] = []; let okText = "";
    if (column === "accepted") {
      blocker = { text: "нет: PL — не сформирован", who: "формирует склад или менеджер" };
      if (rights.canDispatch) action = { id: "make-pl", label: "Сформировать PL" };
    } else if (column === "packed") {
      blocker = shipmentBlocker(shipment);
      const upd = updGapAction(shipment);
      if (rights.canDispatch) {
        if (!shipment.pl_number) action = { id: "make-pl", label: "Сформировать PL" };
        else if (!shipment.dn_number && !gaps.some((gap) => /DN/i.test(gap))) action = { id: "make-dn", label: "Сформировать DN" };
        else if (upd) action = upd;
        else if (needsTrack(shipment) && !shipment.track_number.trim() && readyForTrack(shipment)) action = { id: "enter-track", label: "Вписать трек…" };
        else action = { id: "fill", label: "Заполнить данные…" };
      }
    } else if (column === "to_ship") {
      okText = "всё заполнено — можно отгружать";
      if (rights.canWarehouse) action = { id: "ship", label: "Отгрузить" };
      else if (rights.canManage) action = { id: "ship", label: "Отгрузить", disabled: true, note: "отгружает склад" };
    } else {
      if (closed) okText = "закрыта";
      else {
        blocker = shipmentBlocker(shipment, true);
        const upd = updGapAction(shipment);
        if (rights.canDispatch) {
          if (upd) action = upd;
          else if (needsTrack(shipment) && !shipment.track_number.trim() && !pickupLike) action = { id: "enter-track", label: "Вписать трек…" };
        }
      }
    }
    const sequence = sequences.get(shipmentGroupKey({ project: first, shipment })) ?? "";
    const live = ["Новая", "Собрана", "Упакована"].includes(shipment.status);
    const [position, total] = sequence.split(" из ");
    cards.push({
      id: `s:${shipment.id}`, column, project: projects.join(" + "), customer: shipment.customer, kbs: [], shipment,
      kbLine: kbSummary(kbs), deliveryLine: stage === "shipped" ? `${delivery} · отгружено ${shipment.shipped_at ? formatDayMonth(shipment.shipped_at) : "—"}`
        : `${delivery}${trackWord} · ${dueText(shipment.deadline)}`,
      code: column === "accepted" ? "" : shipment.pl_number, sequence: live && Number(total) > 1 ? `отгрузка ${position} из ${total}` : "",
      note: "", blocker, okText, action, extra, deadline: shipment.deadline, gaps: Boolean(blocker),
      search: [shipmentSearch(shipment).join(" "), shipment.customer, shipmentTitle(shipment)].join(" "),
      sortDay: shipment.shipped_at ?? "",
    });
  }

  const order = (a: BoardCard, b: BoardCard) => {
    if (a.column === "shipped") return b.sortDay.localeCompare(a.sortDay);
    if (a.deadline !== b.deadline) return !a.deadline ? 1 : !b.deadline ? -1 : a.deadline.localeCompare(b.deadline);
    return a.project.localeCompare(b.project, "ru", { numeric: true });
  };
  // Стабильно: колонка за колонкой.
  return cards.sort((a, b) => a.column === b.column ? order(a, b) : COLUMNS.findIndex(([id]) => id === a.column) - COLUMNS.findIndex(([id]) => id === b.column));
}

export interface BoardFilter { query: string; mine: boolean; gaps: boolean; hot: boolean; customer: string }
export const NO_FILTER: BoardFilter = { query: "", mine: false, gaps: false, hot: false, customer: "" };

export function isHot(card: BoardCard, now = new Date()): boolean {
  if (!card.deadline || card.column === "shipped") return false;
  const limit = new Date(now); limit.setDate(limit.getDate() + HOT_DAYS);
  return card.deadline.slice(0, 10) <= isoDay(limit);
}

export function matchesFilter(card: BoardCard, filter: BoardFilter, now = new Date()): boolean {
  if (filter.mine && !(card.action && !card.action.disabled)) return false;
  if (filter.gaps && !card.gaps && !card.blocker) return false;
  if (filter.hot && !isHot(card, now)) return false;
  if (filter.customer && card.customer !== filter.customer) return false;
  const words = filter.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = card.search.toLowerCase();
  return words.every((word) => text.includes(word));
}

/** КБ-строки карточки, которые принимает окно приёмки: сданное цехом — по умолчанию, иначе остаток досрочно. */
export function acceptPlan(card: BoardCard, piecesReady: (kb: IntakeRow) => number): { rows: IntakeRow[]; quantities: Record<string, number>; blank: boolean } {
  const rows = card.kbs.filter((kb) => piecesReady(kb) > 0 || num(kb.remaining) > 0);
  const anyReady = rows.some((kb) => piecesReady(kb) > 0);
  const quantities = Object.fromEntries(rows.map((kb) => [`${kb.project}\u0000${kb.item_index}`,
    piecesReady(kb) > 0 ? piecesReady(kb) : round2(num(kb.remaining))]));
  return { rows, quantities, blank: !anyReady };
}
