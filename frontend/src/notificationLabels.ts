/* Виды уведомлений по-русски: код вида из очереди (`digest`) на экран не попадает (skill erp-screens §1).
   Коды — те, что ставят модули через `notification_requested(kind=…)`; новый вид без записи здесь
   показывается как «Событие», а не сырым кодом. */

export type NotificationTone = "ok" | "warn" | "danger" | "info" | "neutral";

const KINDS: Record<string, { label: string; tone: NotificationTone }> = {
  absent: { label: "Нет на складе", tone: "warn" },
  arrival: { label: "Приход под проект", tone: "ok" },
  digest: { label: "Сводка", tone: "info" },
  disposal: { label: "Утилизация", tone: "danger" },
  error: { label: "Ошибка", tone: "danger" },
  nonconformity: { label: "Несоответствие", tone: "warn" },
  quarantine: { label: "Карантин", tone: "warn" },
  production: { label: "Цех", tone: "info" },
  ready: { label: "Готово к выдаче", tone: "ok" },
  ship: { label: "Отгрузка", tone: "info" },
  security: { label: "Безопасность", tone: "danger" },
  leave: { label: "Отпуск и отгул", tone: "info" },
  document: { label: "Документ к ознакомлению", tone: "info" },
  status: { label: "Статус заявки", tone: "neutral" },
  info: { label: "Сообщение", tone: "neutral" },
};

const UNKNOWN = { label: "Событие", tone: "neutral" as NotificationTone };

export function notificationKind(type: string | null | undefined): { label: string; tone: NotificationTone } {
  return KINDS[String(type ?? "").trim().toLowerCase()] ?? UNKNOWN;
}
