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

/** Подтверждение «Сохранить также в шаблон»: правка шаблона видна во всех ЭТК с этой операцией. */
export const ETK_TO_TEMPLATE_CONFIRM =
  "Сохранить правку и в шаблон операции? Изменение сразу увидят все ЭТК с этой операцией, кроме операций, правленных в своей ТК.";
