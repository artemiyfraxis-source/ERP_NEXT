/* Системный журнал по-русски: тип события (`warehouse.written_off`) → «Списание со склада», детали без
   UUID и [object Object]. Код события остаётся мелкой серой строкой под названием (skill erp-screens §1). */

export const AUDIT_MODULES: Array<[prefix: string, label: string]> = [
  ["request", "Заявки"],
  ["procurement", "Закупка"],
  ["receiving", "Приёмка"],
  ["warehouse", "Склад"],
  ["fulfillment", "Комплектация и выдача"],
  ["production", "Производство"],
  ["shipping", "Отгрузка"],
  ["techcards", "Техкарты"],
  ["references", "Справочники"],
  ["identity", "Сотрудники и вход"],
];

const EVENT_NAMES: Record<string, string> = {
  "request.created": "Заявка создана",
  "request.deleted": "Заявка удалена",
  "request.sent_to_procurement": "Заявка передана в закупку",
  "request.resent_to_procurement": "Заявка повторно передана в закупку",
  "requests.reorder_requested": "Запрошена дозакупка",

  "procurement.created": "Позиция в листе закупки",
  "procurement.line_deleted": "Позиция закупки удалена",
  "procurement.note_saved": "Комментарий закупщика",
  "procurement.expected_arrival_set": "Ожидаемая дата изменена",
  "procurement.actual_arrival": "Фактическая дата прихода",
  "receiving.expected_arrival_copied": "Ожидаемая дата передана в приёмку",
  "procurement.taken_in_work": "Взято в работу",
  "procurement.autofilled": "Заполнено автоматически",
  "procurement.facts_saved": "Данные поставки сохранены",
  "procurement.kp_saved": "КП сохранено",
  "procurement.money_saved": "Суммы сохранены",
  "procurement.supplier_request_sent": "Запрос поставщику отправлен",
  "procurement.requested": "Запрошено у поставщика",
  "procurement.approval_requested": "Запрошено согласование",
  "procurement.approved": "Согласовано",
  "procurement.invoice_recorded": "Счёт внесён",
  "procurement.invoice_file_uploaded": "Файл счёта загружен",
  "procurement.invoice_replaced": "Счёт заменён",
  "procurement.paid": "Оплачено",
  "procurement.rate_reconciled": "Курс сверен",
  "procurement.receipt_stage": "Стадия поставки",
  "procurement.replacement_set": "Замена позиции",
  "procurement.split": "Позиция разделена",
  "procurement.cancelled": "Закупка отменена",
  "procurement.traceability_saved": "Прослеживаемость сохранена",
  "procurement.backorder_requested": "Запрошен недопоставленный остаток",
  "procurement.backorder_created": "Создан недопоставленный остаток",
  "procurement.backorder_confirmed": "Недопоставка подтверждена",
  "procurement.backorder_cancelled": "Недопоставка отменена",

  "receiving.expected": "Ожидается поставка",
  "receiving.unpacked": "Поставка распакована",
  "receiving.document_attached": "Внесён УПД",
  "receiving.inspected": "Входной контроль",
  "receiving.auto_quarantined": "В карантин автоматически",
  "receiving.file_attached": "Документ приложен",
  "receiving.placed": "Размещено на складе",
  "receiving.sent_to_warehouse": "Передано на склад",
  "receiving.returned_to_supplier": "Возврат поставщику",
  "receiving.vk_archived": "Акт ВК в архив",
  "receiving.vk_revision_opened": "Новая ревизия ВК",

  "warehouse.received": "Приход на склад",
  "warehouse.manually_received": "Внесено на склад вручную",
  "warehouse.written_off": "Списание со склада",
  "warehouse.writeoff_returned": "Возврат на склад",
  "warehouse.moved": "Перемещение",
  "warehouse.transferred": "Передача на другой склад",
  "warehouse.location_moved": "Место перенесено",
  "warehouse.location_corrected": "Место исправлено",
  "warehouse.row_edited": "Строка склада исправлена",
  "warehouse.identity_edited": "Наименование исправлено",
  "warehouse.item_types_saved": "Типы позиций сохранены",
  "warehouse.rack_layout_saved": "Стеллажи сохранены",
  "warehouse.returned_to_supplier": "Возврат поставщику со склада",
  "warehouse.supplier_return_requested": "Запрошен возврат поставщику",
  "warehouse.legacy_imported": "Перенос склада из Google",
  "warehouse.legacy_synced": "Догрузка склада из Google",
  "warehouse.legacy_writeoff_history": "История списаний из Google",

  "fulfillment.created": "Заявка на комплектацию",
  "fulfillment.lot_bound": "ГРН привязан",
  "fulfillment.lot_arrived": "Приход встал в комплект",
  "fulfillment.lot_unbound": "ГРН отвязан",
  "fulfillment.completed": "Укомплектовано",
  "fulfillment.ready": "Отправлено на выдачу",
  "fulfillment.issued": "КБ выдана",
  "fulfillment.returned": "Возврат из цеха",
  "fulfillment.return_completed": "Возврат принят",
  "fulfillment.closed": "Заявка закрыта",
  "fulfillment.cancelled": "Заявка отменена",
  "fulfillment.deleted": "Заявка удалена",

  "production.task_deleted": "Задание удалено",
  "production.launched": "Задание запущено",
  "production.auto_scheduled": "Автоплан",
  "production.replanned": "План пересчитан",
  "production.assigned": "Операция назначена",
  "production.share_assigned": "Доля назначена",
  "production.share_removed": "Доля снята",
  "production.work_started": "Работа начата",
  "production.work_paused": "Работа на паузе",
  "production.work_resumed": "Работа продолжена",
  "production.work_cancelled": "Работа отменена",
  "production.output_recorded": "Выработка сдана",
  "production.box_confirmed": "Коробка подтверждена",
  "production.box_corrected": "Коробка исправлена",
  "production.box_returned": "Коробка возвращена оператору",
  "production.box_handed_to_finished_goods": "Коробка сдана на склад ГП",
  "production.handed_over": "Передано дальше",
  "production.qc_accepted": "Принято ОТК",
  "production.qc_rejection_decided": "Решение по браку ОТК",
  "production.defect_decided": "Решение по браку",
  "production.instrument_registered": "Средство измерения добавлено",
  "production.calendar_replaced": "Календарь цеха сохранён",
  "production.skills_replaced": "Компетенции сохранены",
  "production.stage_changed": "Смена этапа проекта",
  "production.plan_event_added": "Запись в журнале проекта",
  "production.plan_events_deleted": "Журнал проекта удалён",
  "production.plan_projects_replaced": "Проекты плана обновлены",
  "production.cancelled": "Задание отменено",
  "production.restored": "Задание восстановлено",

  "shipping.created": "Отправка создана",
  "shipping.good_registered": "ГП заведена",
  "shipping.good_queued": "ГП ждёт приёмки",
  "shipping.good_accepted": "ГП принята от цеха",
  "shipping.good_dropped": "ГП убрана",
  "shipping.good_reordered": "ГП переупорядочена",
  "shipping.picked": "Отобрано в отправку",
  "shipping.unpicked": "Убрано из отправки",
  "shipping.packed": "Упаковано",
  "shipping.boxes_laid_out": "Разложено по коробам",
  "shipping.documents_updated": "Отгрузочные документы",
  "shipping.upd_attached": "УПД приложен",
  "shipping.addressee_set": "Адрес отгрузки",
  "shipping.addressee_remembered": "Адрес запомнен",
  "shipping.head_edited": "Отправка исправлена",
  "shipping.head_followed_project": "Отправка взяла данные проекта",
  "shipping.requested_changed": "Заказано изменено",
  "shipping.shipped": "Отгружено",
  "shipping.shipped_before_erp": "Отгружено до ERP",
  "shipping.closed": "Отправка закрыта",
  "shipping.cancelled": "Отправка отменена",

  "techcards.project_created": "Открыт генератор ТК",
  "techcards.project_auto_built": "ТК собрана генератором",
  "techcards.project_rebuilt": "ТК пересобрана",
  "techcards.scheme_corrected": "Схема ТК исправлена",
  "techcards.project_imported": "ТК перенесена",
  "techcards.project_issued": "ЭТК выдана",
  "techcards.project_draft_discarded": "Черновик ТК удалён",
  "techcards.operation_edited": "Операция ТК исправлена",
  "techcards.operation_record_edited": "Строка БД.ОП исправлена",
  "techcards.harness_component_created": "Шаблон компонента создан",
  "techcards.harness_component_published": "Опубликована версия шаблона",
  "techcards.operation_library_replaced": "Библиотека операций обновлена",
  "techcards.template_sheet_saved": "Шаблон-лист сохранён",
  "techcards.template_sheet_deleted": "Шаблон-лист удалён",
  "techcards.erp_templates_seeded": "Шаблоны ERP загружены",
  "techcards.kitset_loaded": "Проект загружен из расчёта",
  "techcards.kitset_deleted": "Проект удалён",
  "techcards.kitset_document_added": "КД приложена",
  "techcards.kitset_document_removed": "КД убрана",
  "techcards.kitset_harness_saved": "Чертёж жгута сохранён",
  "techcards.kitset_harness_imported": "Чертёж жгута загружен из TECHMAP-GRAPHER",
  "techcards.kitset_harness_unlinked": "Чертёж жгута отвязан",
  "techcards.kitset_harness_restored": "Чертёж жгута возвращён",
  "techcards.kitset_harness_deleted": "Отвязанный чертёж жгута удалён",

  "references.tech_snapshot_replaced": "Снимок базы данных обновлён",
  "references.tech_record_created": "Запись базы данных добавлена",
  "references.tech_record_edited": "Запись базы данных исправлена",
  "references.maps_replaced": "Карты соответствий сохранены",
  "references.supplier_learned": "Поставщик запомнен",

  "identity.login_succeeded": "Вход",
  "identity.login_failed": "Неудачный вход",
  "identity.login_rate_limited": "Вход заблокирован на время",
  "identity.logout": "Выход",
  "identity.own_password_changed": "Пароль изменён",
  "identity.pin_revealed": "PIN показан",
  "identity.user_created": "Сотрудник заведён",
  "identity.user_updated": "Сотрудник исправлен",
  "identity.user_deactivated": "Сотрудник отключён",
  "identity.user_deleted": "Сотрудник удалён",
  "identity.departments_saved": "Отделы сохранены",
  "identity.department_renamed": "Отдел переименован",
  "identity.department_head_set": "Руководитель отдела назначен",
  "identity.role_access_saved": "Доступы ролей сохранены",
  "identity.level_access_saved": "Доступы уровней сохранены",
  "identity.access_levels_migrated": "Роли переведены в уровни доступа",
  "identity.dormant_access_archived": "Исключения прежней роли с полным доступом убраны в архив",
  "identity.level_view_defaults_seeded": "Уровням открыт просмотр склада, закупки и приёмки",
  "identity.user_access_saved": "Исключения доступа сохранены",
  "identity.admin_mode_on": "Режим администратора включён",
  "identity.admin_mode_off": "Режим администратора выключен",
  "identity.view_as_on": "Просмотр от лица сотрудника начат",
  "identity.view_as_off": "Просмотр от лица сотрудника закончен",
  "identity.login_lock_released": "Блокировка входа снята",
  // Темы очереди фоновых заданий (их показывают «Ошибки фоновых заданий» и «Фоновые процессы»).
  "procurement.autofill_requested": "Автозаполнение строки закупки",
  "notification.requested": "Уведомление",
  "arbiter.trigger": "Задача модулю по проекту",
  "operations.outbox_retried": "Фоновое задание запущено повторно",
};

const DETAIL_LABELS: Record<string, string> = {
  expected_arrival: "ожид. дата",
  previous_expected_arrival: "была ожид. дата",
  actual_arrival: "факт. дата",
  number: "№", project: "проект", item_index: "КБ", quantity: "кол-во", grn: "ГРН", from: "откуда", to: "куда",
  storage_location: "место", reason: "причина", revision: "ревизия", operations: "операций", warnings: "предупреждений",
  from_status: "было", to_status: "стало", legacy_id: "ID", package: "пакет", kind: "вид", inherited: "из проекта",
  good: "годных", defect: "брак", accepted: "принято", rejected: "отклонено", disposition: "решение",
  act_reference: "акт", reserved: "резерв", shortfall: "недобор", lines: "строк", handout_location: "место выдачи",
  finished_good: "ГП", ordered: "заказано", verdict: "вердикт", inventory_number: "инв. №", operator: "монтажник",
  skills: "компетенций", allowed: "допущено", holidays: "праздников", absences: "отсутствий", shift_start: "смена с",
  shift_finish: "до", effective_seconds: "секунд в смене", transfer_batch: "передаточная партия", max_crew: "людей",
  first_ready: "первая готовность", finish: "окончание", idle_seconds: "простой, с", fields: "поля", pl: "PL", dn: "DN",
  moves: "перемещений", materials: "материалов", inputs: "входов", projects: "проектов", replan: "перепланировать",
  events: "записей",
  level: "уровень", access_level: "уровень доступа", shop_worker: "монтажник", site_master: "мастер площадки",
  login: "логин", role: "прежняя роль", changes: "изменения", exceptions: "исключения", levels: "уровни", people: "люди",
  note: "примечание", viewed: "сотрудник",
  source: "сайт", tls: "сертификат", topic: "задание", attempts: "попыток", error: "ошибка", released: "снято",
};

/* Уровни доступа по ключу: `level` и `access_level` в деталях событий хранят ключ, а не подпись. */
const LEVEL_NAMES: Record<string, string> = {
  superadmin: "Суперадминистратор", admin: "Администратор", commercial: "Коммерческое руководство",
  tech_lead: "Технический руководитель", foreman: "Бригадир", operator: "Оператор",
};
const LEVEL_KEYS = new Set(["level", "access_level"]);

/* Имена полей, которые события пишут списком («fields», «inherited»). */
const FIELD_LABELS: Record<string, string> = {
  carrier: "перевозчик", contact: "контакт", phone: "телефон", receiver: "получатель", shipping_point: "адрес отгрузки",
  terms: "условия", upd_type: "тип УПД", deadline: "срок", note: "примечание", address: "адрес", customer: "заказчик",
  supplier: "поставщик", site_name: "наименование (сайт)", manufacturer: "изготовитель", country: "страна",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HIDDEN_KEY = /(^id$|_id$|_ids$|commit$)/;

export function auditModule(eventType: string): string {
  const head = eventType.split(".")[0];
  return AUDIT_MODULES.find(([key]) => key === head || key === head.replace(/s$/, ""))?.[1] ?? "";
}

/** «warehouse.written_off» → «Списание со склада»; неизвестный тип — «Склад: событие», а код виден строкой ниже. */
export function auditEventName(eventType: string): string {
  return EVENT_NAMES[eventType] ?? `${auditModule(eventType) || "Событие"}: ${eventType.split(".").slice(1).join(" ").replace(/_/g, " ")}`;
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "string") return UUID.test(value) ? "" : FIELD_LABELS[value] ?? trimNumber(value);
  if (typeof value === "number") return trimNumber(String(value));
  if (Array.isArray(value)) {
    if (value.every((item) => typeof item !== "object" || item === null)) return value.map(shown).filter(Boolean).join(", ");
    return String(value.length);
  }
  if (typeof value === "object") return String(Object.keys(value as object).length);
  return String(value);
}

/** «39.000000» → «39», «0.900» → «0,9». */
function trimNumber(value: string): string {
  return /^-?\d+\.\d+$/.test(value) ? value.replace(/\.?0+$/, "").replace(".", ",") : value;
}

/** Детали события одной строкой: русские подписи, без UUID и служебных id. Строки с UUID-ключом
    (количество по каждой строке) сводятся в «строк: N». */
export function auditDetails(details: Record<string, unknown>): string {
  const entries = Object.entries(details);
  const keyed = entries.filter(([key]) => UUID.test(key)).length;
  const parts = entries
    .filter(([key]) => !HIDDEN_KEY.test(key) && !UUID.test(key))
    .map(([key, value]) => [
      DETAIL_LABELS[key] ?? key.replace(/_/g, " "),
      LEVEL_KEYS.has(key) && typeof value === "string" ? LEVEL_NAMES[value] ?? shown(value)
        : key === "topic" && typeof value === "string" ? auditEventName(value) : shown(value),
    ] as const)
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}: ${value}`);
  if (keyed) parts.push(`строк: ${keyed}`);
  return parts.join(" · ");
}
