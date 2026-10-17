import type { EtkCardVersion } from "./api";
import { formatDayMonth } from "./format";

/* Откуда карточка операции ЭТК (владелец 17.10: «если карта берётся без изменений — она должна идти из базы данных, а не
   копией»): backend etk_live отдаёт у операции `card_source` и дату `card_changed_at`. */
export type EtkCardSource = "template" | "edited" | "snapshot";

export type EtkCardSourceFields = { card_source?: string; card_changed_at?: string };

/** Серая строка под названием операции, на экране и в печати: «по шаблону от 17.10», «правлена в ТК 17.10»,
 *  «копия на момент сборки» (шаблона в базе нет). Своя операция ручной сборки и старый ответ — без `card_source`, пусто. */
export function etkCardSourceNote(operation: EtkCardSourceFields): string {
  const day = operation.card_changed_at ? formatDayMonth(operation.card_changed_at) : "";
  switch (operation.card_source) {
    case "template": return day ? `по шаблону от ${day}` : "по шаблону";
    case "edited": return day ? `правлена в ТК ${day}` : "правлена в ТК";
    case "snapshot": return "копия на момент сборки";
    default: return "";
  }
}

const SOURCE_ORDER = ["template", "edited", "snapshot"];

/** Одна строка на все операции ЭТК задания («Мои задания»): «по шаблону от 15.10» (разные даты — через запятую),
 *  «правлена в ТК 16.10», «копия на момент сборки», через « · ». Нет операций с источником — пусто. */
export function etkCardsNote(cards: EtkCardVersion[]): string {
  return SOURCE_ORDER.map((source) => {
    const rows = cards.filter((item) => item.source === source);
    if (!rows.length) return "";
    const days = [...new Set(rows.map((item) => formatDayMonth(item.changed_at)).filter(Boolean))].join(", ");
    switch (source) {
      case "template": return days ? `по шаблону от ${days}` : "по шаблону";
      case "edited": return days ? `правлена в ТК ${days}` : "правлена в ТК";
      default: return "копия на момент сборки";
    }
  }).filter(Boolean).join(" · ");
}

/** Изменилась ли карточка операции ЭТК после первого «Начать»: записанная на начале версия той же операции ЭТК
 *  расходится с нынешней. «шаблон изменён после начала» / «правлена в ТК после начала»; не начинали, на начале ЭТК не
 *  было или всё как было — пусто. */
export function etkCardsChangedNote(cards?: { live: EtkCardVersion[]; started: EtkCardVersion[] | null }): string {
  if (!cards?.started) return "";
  const started = new Map(cards.started.map((item) => [item.position, item]));
  const changed = cards.live.filter((item) => {
    const was = started.get(item.position);
    return was && (was.source !== item.source || was.changed_at !== item.changed_at);
  });
  if (!changed.length) return "";
  return changed.some((item) => item.source === "template") ? "шаблон изменён после начала" : "правлена в ТК после начала";
}

/** Подтверждение «Сохранить также в шаблон»: правка шаблона видна во всех ЭТК с этой операцией. */
export const ETK_TO_TEMPLATE_CONFIRM =
  "Сохранить правку и в шаблон операции? Изменение сразу увидят все ЭТК с этой операцией, кроме операций, правленных в своей ТК.";
