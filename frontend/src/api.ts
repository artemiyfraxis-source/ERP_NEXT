export type AccessMode = "view" | "edit";
export const AUTH_REQUIRED_EVENT = "erp:auth-required";

/** Access levels (owner 04.10.2026), strongest first; names as in backend identity LEVEL_LABELS. */
export const ACCESS_LEVEL_LABELS: Readonly<Record<string, string>> = {
  superadmin: "Суперадминистратор",
  admin: "Администратор",
  commercial: "Коммерческое руководство",
  tech_lead: "Технический руководитель",
  foreman: "Бригадир",
  operator: "Оператор",
};

/** The level's Russian name; an unknown key is shown as is, an empty one as a dash. */
export function accessLevelLabel(level: string | null | undefined): string {
  const key = String(level ?? "").trim();
  return ACCESS_LEVEL_LABELS[key] ?? (key || "—");
}

export interface Principal {
  user: {
    id: string;
    email: string;
    name: string;
    department: string;
  };
  /** The access level key, one element (backend keeps the legacy name for one release); `access_level` is the same key. */
  roles: string[];
  /** superadmin | admin | commercial | tech_lead | foreman | operator — the effective level, the admin mode included. */
  access_level?: string;
  /** Section → mode. superadmin/admin carry every section on edit; the screens never read anything else. */
  permissions: Record<string, AccessMode>;
  /** portalState_ roleLabel: the level's name, «· режим» while the admin mode is on. */
  role_label?: string;
  /** «Режим админа» (portalToggleAdmin): the account may switch it / it is on for this session. */
  can_admin_mode?: boolean;
  admin_mode?: boolean;
  /** «Смотреть как сотрудник»: `user` and the rights above are the viewed employee's; who really looks and until when.
   *  The server refuses every change while it is on, except «Вернуться к себе» and «Выйти». */
  viewing?: { viewer: { id: string; name: string }; until: string | null } | null;
}

/** The login list (portalStaffLite_): active employees, no e-mails. */
export interface LoginStaff {
  id: string;
  name: string;
  department: string;
}

/** portalRevealPin_: the new PIN of a newcomer, shown once. */
export interface PinReveal {
  reveal: true;
  name: string;
  pin: string;
}

export interface RequestSchema {
  required: string[];
  purposes: string[];
  delivery_points: string[];
  units: string[];
  categories: string[];
  /** `purposeProjects_`: open registry projects for the ЦЕЛЬ dropdown (empty = registry unavailable). */
  projects?: string[];
  /** ТИП combobox options (REQUEST_FORM_FIELDS «ТИП»). */
  item_types?: string[];
}

export interface RequestRow {
  id: string;
  legacy_id: string;
  designation: string;
  status: string;
  /** RQ-MINE: the pipeline stage — Procurement's own status once handed off, else Requests' status. */
  stage: string;
  created_at: string;
  initiator: string;
  department: string;
  purpose: string;
  project_reference: string | null;
  delivery_point: string;
  name: string;
  item_type: string;
  category: string;
  quantity: string;
  unit: string;
  priority: string;
  planned_date: string;
  procurement_deadline?: string;
  quality_document?: string;
  design_article?: string | null;
  link?: string | null;
  price?: string | null;
  price_with_vat?: string | null;
  line_sum?: string | null;
  comment?: string | null;
  /** «Мои заявки» only: the purchase-sheet columns the initiator sees (via Procurement's read port). */
  buyer?: string | null;
  supplier?: string | null;
  order_number?: string | null;
  expected_arrival?: string | null;
  procurement_sum?: string | null;
}

export interface MineRequestsResult {
  requests: RequestRow[];
  total: number;
  limit: number;
  offset: number;
}

/** RQ-5 `/api/requests/import/parse`: one ЗКП row as read from a calculation grid. */
export interface CalcImportItem {
  item_type: string;
  name: string;
  quantity: string | null;
  price: string | null;
  price_with_vat: string | null;
  link: string | null;
  country: string | null;
  unit: string | null;
  quality_document: string;
  planned_date: string | null;
}

export interface CalcImportPreview {
  project: string;
  items: CalcImportItem[];
  /** `/import/calc` only: the soft unknown-project warning of `importRequests_`. */
  warnings?: string[];
}

export interface SupplierOrder {
  order_number: string;
  order_date: string;
  supplier: string;
  task_type: string;
  delivery_point: string;
  buyer: string;
  letter: string;
  line_ids: string[];
  /** Документ запроса в хранилище ERP (SUPPLYMAP: Google Doc из шаблона в папке год/месяц/поставщик). */
  document?: SupplierOrderDocument | null;
}

export interface SupplierOrderDocument {
  url: string;
  file_name: string;
  folder: string;
}

export interface SupplierRequestsResult {
  orders: SupplierOrder[];
  lines: ProcurementLine[];
  /** Строки, которые в запрос не попали, и почему (поставщик не указан, строка изменилась…). */
  skipped: SkippedRequestLine[];
  other_buyer: number;
}

export interface SkippedRequestLine {
  line_id: string;
  reason: string;
}

export interface ProcurementLine {
  id: string;
  /** ЦЕЛЬ заявки, из которой строка (списки листа; в ответах на правки её нет). */
  purpose?: string | null;
  legacy_id: string;
  revision: string;
  status: string;
  version: number;
  request_date: string;
  initiator: string;
  department: string;
  task_type?: string;
  designation: string;
  project_reference: string | null;
  priority: string;
  delivery_point: string;
  planned_date: string;
  link: string | null;
  item_type: string;
  design_article: string | null;
  category: string;
  calculated_name: string;
  calculated_quantity: string;
  calculated_unit: string;
  /** ЦЕНА / ЦЕНА С НДС / СУММА (Расчет): цены расчёта, с которыми пришла заявка. */
  calculated_price?: string | null;
  calculated_price_with_vat?: string | null;
  calculated_sum?: string | null;
  buyer: string | null;
  supplier: string | null;
  site_name: string | null;
  actual_quantity: string | null;
  actual_unit: string | null;
  quality_document: string;
  supplier_request_reference: string | null;
  order_number: string | null;
  order_date: string | null;
  /** «ЗАКАЗ»: ссылка на документ запроса поставщику. */
  order_url?: string | null;
  /** ОЖИД. ДАТА: закупщик меняет её от «Жду счет» до «Карантин» (setExpectedArrival). */
  expected_arrival: string | null;
  /** ФАКТ. ДАТА: последняя дата входного контроля среди поставок строки (ставит приёмка). */
  actual_arrival?: string | null;
  /** КОММЕНТАРИЙ: свободная заметка закупщика, от «В работе»; не на «Новый»/«Отменено» (setNote). */
  note?: string | null;
  /** DATASHEET: ссылка или текст, правится как комментарий (setDatasheet). */
  datasheet?: string | null;
  /** Файлы РАЗРЕШЕНИЕ и ДОК. КАЧЕСТВА (только в списках: «Мои позиции», «Весь лист», «По поставщикам»). */
  permit_files?: ProcurementFile[];
  quality_files?: ProcurementFile[];
  invoice_url: string | null;
  paid_at: string | null;
  currency: string | null;
  actual_price: string | null;
  actual_vat_rate: string | null;
  actual_price_with_vat: string | null;
  actual_sum: string | null;
  source_currency: string | null;
  source_price: string | null;
  source_price_with_vat: string | null;
  source_sum: string | null;
  exchange_rate: string | null;
  exchange_rate_date: string | null;
  exchange_rate_manual: boolean;
  invoice_item_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  pay_missing: string[];
  is_backorder: boolean;
  replacement: boolean | null;
  replacement_decision: "Разрешено" | "Запрещено" | null;
  replacement_open: boolean;
  permission_number: string | null;
  permission_date: string | null;
  manufacturer?: string | null;
  country?: string | null;
  quality_document_type?: string | null;
  quality_document_number?: string | null;
  quality_document_date?: string | null;
  standard?: string | null;
}

export interface ProcurementTraceabilityInput {
  manufacturer: string;
  country: string;
  quality_document_type: string;
  quality_document_number: string;
  quality_document_date: string | null;
  standard: string;
}

export type MoneyDriver = "net" | "gross" | "vat" | "qty" | "sum";

export interface ProcurementMoneyInput {
  driver?: MoneyDriver;
  currency: string;
  price?: string | null;
  vat_rate?: string | null;
  price_with_vat?: string | null;
  sum?: string | null;
  invoice_item_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  manual_exchange_rate?: string | null;
}

export interface ManualRatePrompt {
  code: "manual_rate_required";
  currency: string;
  date: string;
  message: string;
}

export interface WarehouseRackLayout {
  racks: number;
  shelves: number;
  perRack: Record<string, number>;
}

export interface ReceivingLine {
  id: string;
  legacy_id: string;
  /** ГРН prefix derived from НАЗНАЧЕНИЕ / ЦЕЛЬ / ССЫЛКА; null — the storekeeper picks CSM or PPM. */
  grn_prefix?: string | null;
  status: string;
  version: number;
  warehouse: string;
  article: string;
  series: string;
  name: string;
  item_type: string;
  purpose: string;
  supplier: string;
  ordered_quantity: string;
  unit: string;
  quality_document_required: boolean;
  shipping_document_url: string | null;
  quality_document_url: string | null;
  document_quantity: string | null;
  fact_quantity: string | null;
  expiry_date: string | null;
  unit_mass_g?: string | null;
  mass_fields?: UnitMassFields;
  disposition: string | null;
  verdict: string | null;
  nonconformities: string[];
  /** «УПД не приложен — придёт по ЭДО»: ВК прошёл без УПД, не блокирует; null после «Внести УПД». */
  shipping_document_note?: string | null;
  accepted_quantity: string | null;
  backorder_quantity: string;
  grn: string | null;
  package: string;
  storage_location: string | null;
  inspector: string | null;
  warehouse_event_emitted: boolean;
  inspected_on: string | null;
  revision_of_id?: string | null;
  nonconformity_description?: string;
  supplier_returns?: number;
  order_number?: string;
  expected_arrival?: string | null;
  document_number?: string;
  document_date?: string | null;
  document_name?: string;
  inspection_note?: string;
  manufacture_date?: string | null;
  manufacturer?: string;
  country?: string;
  quality_document_type?: string;
  quality_document_number?: string;
  quality_document_date?: string | null;
  standard?: string;
  split_of_id?: string | null;
  /** RC-9: выпущенные акты строки — печатаются бланком. */
  acts?: { kind: string; title: string; number: string; date: string | null }[];
}

/** RC-6: принятая позиция в форме возврата поставщику и что по её ГРН лежит на складе. */
export interface SupplierReturnCandidate extends ReceivingLine {
  stock_warehouse: string | null;
  stock_balance: string | null;
}

export interface SupplierReturnPayload {
  items: { id: string; version: number; quantity?: string }[];
  reason: string;
  mode: "wait" | "close";
  claim?: string;
  comment?: string;
}

/** One place a lot was written off to: a project number or a surname, with what is still out there. */
export interface WarehouseDestination {
  name: string;
  written_off: string;
  returned: string;
  outstanding: string;
  unit: string;
  last_date: string;
}

export interface WarehouseLot {
  id: string;
  version: number;
  warehouse: string;
  grn: string;
  package: string;
  article: string;
  series: string;
  name: string;
  item_type: string;
  purpose: string;
  unit: string;
  storage_location: string;
  inbound: string;
  written_off: string;
  reserved: string;
  balance: string;
  available: string;
  source_receiving_ids: string[];
  /** Who holds the reserve: kit requests, techcards, procurement covers. Only lots with a reserve carry holders. */
  reserve_holders?: Array<{ kind: string; label: string; quantity: string; unit: string }>;
  reserve_note?: string;
  // Lot passport (SK-6); older payloads may lack it.
  unit_mass_g?: string | null;
  expiry_date?: string | null;
  manufacturer?: string;
  description?: string;
  storage_conditions?: string;
  datasheet?: string;
  certificate?: string;
}

/** МАССА 1 ШТ as typed: the weight of 1, 10 or 100 pieces, grams; any one is enough. */
export interface UnitMassFields {
  mass_1: string;
  mass_10: string;
  mass_100: string;
}

export interface WarehouseLotPassport extends UnitMassFields {
  expiry_date: string | null;
  manufacturer: string;
  description: string;
  storage_conditions: string;
  datasheet: string;
  certificate: string;
}

/** A quick-entry row («Внести позиции»); empty article → the name, empty unit/purpose → «шт»/«Склад». */
export type WarehouseManualItem = {
  warehouse: string;
  article: string;
  series: string;
  name: string;
  item_type: string;
  purpose: string;
  unit: string;
  quantity: string;
  storage_location: string;
} & Partial<WarehouseLotPassport>;

/** Quick-entry answer (SK-10, stock05Add_): bad rows are skipped; `i` is the 0-based index of the sent row. */
export interface WarehouseAddResult {
  added: number;
  lots: WarehouseLot[];
  assigned: Array<{ i: number; id: string; grn: string; article: string; quantity: string; unit: string; storage_location: string; warehouse: string }>;
  errors: Array<{ i: number; error: string }>;
}

/** Editable fields of a stock row (SK-7); ГРН, пакет and склад are the row key and are not here. */
export type WarehouseRowEdit = Partial<Record<
  | "article" | "series" | "name" | "item_type" | "unit" | "purpose" | "storage_location"
  | "unit_mass_g" | "expiry_date" | "manufacturer" | "description" | "storage_conditions"
  | "datasheet" | "certificate" | "inbound" | "written_off" | "reserved",
  string
>>;

/** SK-14: the item type dictionary, rows per type as written on the stock, and the dropdown choices. */
export interface WarehouseItemTypes {
  types: string[];
  counts: Record<string, number>;
  choices: string[];
}

/** SK-14: reference fields of one stock row, as text — the export row and the import edit share them. */
export type WarehouseReferenceField =
  | "name" | "item_type" | "unit" | "storage_location" | "purpose" | "unit_mass_g" | "expiry_date"
  | "manufacturer" | "description" | "storage_conditions" | "article" | "series";

export type WarehouseExportRow = { warehouse: string; grn: string; package: string; balance: string }
  & Record<WarehouseReferenceField, string>;

export type WarehouseReferenceEdit = { warehouse?: string; grn: string; package?: string }
  & Partial<Record<WarehouseReferenceField, string>>;

/** Bulk edit answer (stock05BulkEdit_): the first 500 changes, 200 missing keys, rows skipped with a reason. */
export interface WarehouseBulkResult {
  applied: boolean;
  rows: number;
  count: number;
  changes: Array<{ warehouse: string; grn: string; package: string; name: string; column: string; was: string; now: string }>;
  missing: string[];
  missing_count: number;
  errors: Array<{ warehouse: string; grn: string; package: string; error: string }>;
}

/** SK-16 «Журнал склада» (portalStock05Log): the last 400 matches, newest first. */
export interface WarehouseJournalRow {
  occurred_at: string;
  kind: string;
  lot: string;
  name: string;
  event: string;
  who: string;
  warehouse: string;
}

export interface WarehouseJournal {
  rows: WarehouseJournalRow[];
  total: number;
  truncated: number;
}

export interface WarehouseShelfPlaces {
  warehouse: string;
  shelf: string;
  occupied: string[];
  next_number: number | null;
}

export interface WarehouseArticleInfo {
  series: string;
  name: string;
  item_type: string;
  source: string;
}

export interface FulfillmentLot {
  id: string;
  warehouse: string;
  grn: string;
  package: string;
  article: string;
  name: string;
  unit: string;
  storage_location: string;
  available: string;
}

/** «Возможно есть»: a warehouse lot that may be a КД position; `kind` exact — the same article, fuzzy — a lookalike;
 * `by` — matched by the position's name (against the lot's article) or, as the fallback, by the КД article. */
export interface FulfillmentCandidate extends FulfillmentLot {
  lot_id: string;
  kind: "exact" | "fuzzy";
  match: string;
  by?: "name" | "article";
}

export interface FulfillmentPositionCandidates {
  article: string;
  name: string;
  unit: string;
  candidates: FulfillmentCandidate[];
  more: number;
}

export interface FulfillmentLine {
  id: string;
  requirement_id: string;
  article: string;
  name: string;
  unit: string;
  requirement: string;
  reserved: string;
  issued: string;
  returned_good: string;
  defective_returned: string;
  defective_missing: string;
  consumed: string;
  lot: null | {
    id: string;
    warehouse: string;
    grn: string;
    package: string;
    storage_location: string;
  };
}

export interface PrintSheet {
  title: string;
  subtitle: string;
  columns: string[];
  rows: string[][];
  note: string;
  sign: string;
}

/** Лист PL/DN отгрузки по образцу владельца: шапка «подпись | значение | реквизиты», позиции, пустые строки. */
export interface ShippingForm {
  kind: "pl" | "dn";
  title: string;
  head: { label: string; value: string; side: string }[];
  columns: string[];
  rows: string[][];
  blank_rows: number;
  sign_label: string;
  sign_name: string;
}

export interface FulfillmentRequest {
  id: string;
  number: string;
  version: number;
  created_at: string;
  requester: string;
  purpose: string;
  deadline: string | null;
  status: string;
  /** «main» — комплект КБ (одна живая заявка на КБ проекта); «extra» — «Довыдача» сверх выданного комплекта. */
  kind?: "main" | "extra";
  handout_location: string | null;
  /** «Укомплектован» only: the place «Отправить на выдачу» offers — the last one used at the kit's site, else «Зона выдачи». */
  handout_place?: string;
  return_location: string | null;
  fully_reserved: boolean;
  /** НЕДОБОР (issueShortText_): what a lot does not close yet. */
  shortfall?: string;
  /** issueProjectOf_: the part of НАЗНАЧЕНИЕ before « · »; "" for a manual request. */
  project?: string;
  /** FF-4 stage dates by the first hit: В РАБОТУ / НАБРАНА / ВЫДАНА / ВОЗВРАЩЕНА / ЗАКРЫТА. */
  at_work?: string | null;
  at_picked?: string | null;
  at_issued?: string | null;
  at_returned?: string | null;
  at_closed?: string | null;
  lines: FulfillmentLine[];
}

/** Rack layout for the handout address (SUPPLYMAP issueCardResp_ `places.config`). */
export interface FulfillmentPlaces {
  racks: number;
  shelves: number;
  per_rack: Record<string, number>;
  slots: string;
}

export interface FulfillmentPlace {
  rack: string;
  shelf: string;
  num: string;
  slot: string;
}

/** Issue.gs issueCalcMatch_: one ЗКП position of the calculation matched against the warehouse. */
export interface FulfillmentCalcLine {
  name: string;
  qty: string;
  unit: string;
  found: boolean;
  short: string;
  picks: Array<{
    lot_id: string;
    warehouse: string;
    grn: string;
    package: string;
    article: string;
    name: string;
    unit: string;
    storage_location: string;
    qty: string;
    /** The part taken from the project's own «Со склада» reserve (absent when none). */
    reserved?: string;
  }>;
}

export interface FulfillmentDraftLine {
  article: string;
  name: string;
  unit: string;
  requirement: string;
  /** The picked warehouse lot (IssueForm FIFO pick); reserved when the request is created. */
  lot_id?: string;
}

export interface ProductionOperator {
  id: string;
  name: string;
  site: string;
  department?: string;
}

export interface ProductionOperation {
  id: string;
  position: number;
  code: string;
  stage: string;
  description: string;
  seconds_per_unit: string;
  setup_seconds: string;
  norm_seconds: number;
  operator_ids: string[];
  planned_start: string | null;
  planned_finish: string | null;
  assigned_by: string | null;
  // Доли по людям: штуки (null — вся операция бригадой), своё окно, факт «взял»/«закончил».
  shares?: ProductionShare[];
  my_share?: ProductionShare | null;
  fact?: { good: string; defect: string; processed: string };
  state?: "не начата" | "в работе" | "выполнена";
  wip?: { ok: string; closed: string };
  first?: boolean;
  mates?: string[];
  /** Готовность входов для монтажника («Мои задания») и сколько штук ему осталось сдать. */
  readiness?: ProductionReadiness;
  remaining?: string;
  /** «Факт / норма» сданного исполнителем по журналу времени: факт без пауз, норма на сданные штуки. */
  time_fact?: { actual_seconds: number; norm_seconds: number; percent: number | null; finishes: number } | null;
  // Сетевой график: блок схемы, этапы-входы, финальная ли операция (её выход идёт в ОТК и на склад ГП) и сколько
  // штук она сдаёт по заданию (тираж × штук её полуфабриката на изделие; в цепочке — тираж).
  stage_key?: string | null;
  inputs?: ProductionOperationInput[];
  final?: boolean;
  required_quantity?: string;
}

export interface ProductionReadyBox {
  id: string;
  number: string | null;
  /** Код с коробки «180К.11·КБ1·13.10·Б07·3»; null — коробка до 13.10.2026. */
  code?: string | null;
  /** Доступно в коробке, шт. */
  quantity: string;
  operation_position: number;
  stage: string;
}

export interface ProductionReadiness {
  state: "ready" | "assembly" | "waiting_master" | "waiting_stage" | "done";
  text: string;
  /** Подтверждённые мастером коробки этого ПЗ, которые можно взять на вход. */
  boxes: ProductionReadyBox[];
}

export interface ProductionOperationInput {
  operation_id: string;
  /** Номер этапа-входа. */
  position: number | null;
  /** Штук полуфабриката входа на одно изделие этой операции. */
  per_unit: string;
  /** Передаточная партия ребра; null — партия задания. */
  transfer_batch: string | null;
  /** Название этапа-входа, если сервер его прислал; иначе его берут из операций задания. */
  stage?: string;
}

export interface ProductionShare {
  operator_id: string;
  quantity: number | null;
  planned_start: string | null;
  planned_finish: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ProductionLaunchable {
  techcard_id: string;
  project: string;
  index: string;
  item: string;
  name: string;
  customer: string;
  run: string;
  deadline: string | null;
  launched: number;
  operations: number;
}

export interface ProductionAssignPlan {
  start: string | null;
  finish: string | null;
  warnings: string[];
}

export interface ProductionTask {
  id: string;
  number: string;
  version: number;
  created_at: string;
  project: string;
  item: string;
  name: string;
  quantity: number;
  fulfillment_request_id: string | null;
  deadline: string | null;
  status: string;
  launched_by: string;
  handed_to?: string;
  planned_start: string | null;
  planned_finish: string | null;
  assigned: number;
  operations: ProductionOperation[];
  /** План задания (Цель 2): техкарта и ревизия норм, режим передачи, партия, «первые k раньше», команда. */
  techcard_id?: string | null;
  techcard_revision?: number | null;
  flow_mode?: ProductionFlowMode;
  transfer_batch?: number | null;
  early_quantity?: number | null;
  team?: string[];
  /** «Сдано N шт» площадки без отменённых; null — не считалось в этом ответе. */
  delivered?: number | null;
}

export type ProductionFlowMode = "sequential" | "parallel" | "mixed";

export interface ProductionDelivery {
  id: string;
  site: string;
  quantity: number;
  delivered_at: string;
  delivered_by: string;
  cancelled_at: string | null;
  cancelled_by: string | null;
}

export interface ProductionFlowStep {
  operation_id: string;
  position: number;
  stage: string;
  code: string;
  people: string;
  operator_ids: string[];
  start: string;
  first_out: string;
  finish: string;
  wait_seconds: number;
}

export interface ProductionFlow {
  task: ProductionTask;
  modes: { value: ProductionFlowMode; name: string }[];
  thresholds: { notice: string; limit: string };
  plan: {
    anchor: string;
    transfer_batch: number;
    first_ready: string;
    first_quantity: number;
    finish: string;
    baseline_finish: string;
    baseline_early_ready: string | null;
    delay_percent: string | null;
    level: "" | "ok" | "notice" | "limit";
    work_seconds: number;
    warnings: string[];
    steps: ProductionFlowStep[];
  };
  deliveries: ProductionDelivery[];
  delivered: number;
  norms: { techcard_id: string | null; revision: number | null; current_revision: number | null; outdated: boolean };
}

export interface ProductionPlanEvent {
  id: string;
  project: string;
  occurred_at: string;
  who: string;
  kind: string;
  blocker: string;
  text: string;
  deadline: string | null;
  item_index: string;
  value: string;
  /** Колонка листа «ЗАГРУЗ» у записи «Отметка плана». */
  field?: string;
  from_value?: string;
  reason?: string;
}

/** Ячейка листа «ЗАГРУЗ»: показанное значение, вычисленное ERP и признак ручной отметки поверх него. */
export interface ProductionPlanSheetCell {
  value: string;
  auto: string;
  manual: boolean;
}

export type ProductionPlanSheetField =
  | "comment" | "status" | "ready" | "in_plan" | "customer_supplied" | "kit_checked" | "kit_present"
  | "zkp" | "kit_arrival" | "tc_ready" | "test_sb" | "kit_picked";

export interface ProductionPlanRow {
  project: string;
  customer: string;
  opened_on: string | null;
  deadline: string | null;
  days_left: number | null;
  stage: string;
  /** Registry status is terminal (closed/cancelled): the row is dimmed and out of «В работе». */
  terminal: boolean;
  stage_manual: boolean;
  kits: Array<{
    item_index: string;
    status: string;
    manual: boolean;
    tc: boolean;
    kit: boolean;
    review: null | { value: string; text: string; who: string; occurred_at: string | null };
  }>;
  supply: null | {
    total: number;
    stock: number;
    transit: number;
    buying: number;
    request: number;
    customer_supplied: number;
    customer_supplied_stock: number;
    eta: string;
  };
  plan: null | {
    tasks: number;
    assigned: number;
    total: number;
    start: string | null;
    finish: string | null;
  };
  blocker: null | { type: string; text: string; since: string; who: string };
  last: null | { occurred_at: string; who: string; text: string };
  events: ProductionPlanEvent[];
  /** Группа строки листа: на паузе сверху, в работе, готово/отгрузка внизу, закрытые проекты отдельно. */
  group?: "hold" | "active" | "done" | "closed";
  sheet?: Partial<Record<ProductionPlanSheetField, ProductionPlanSheetCell>>;
  /** Календарь проекта: плановые дни работ, кол-во задания в первый день, отметки дней. Даты ISO. */
  calendar?: { work: string[]; starts: Record<string, number>; notes: Record<string, string> };
}

export interface ProductionPlanResult {
  blockers: string[];
  stages: string[];
  review_values: string[];
  shop_statuses?: string[];
  supplied_values?: string[];
  arrival_values?: string[];
  today?: string;
  calendar_from?: string;
  calendar_to?: string;
  rows: ProductionPlanRow[];
}

export interface ProductionAbsence {
  id?: string;
  operator_id: string;
  from_date: string;
  to_date: string;
  kind: string;
  /** Отсутствие по зарегистрированному заявлению: календарь его показывает, но не правит и не сохраняет. */
  leave_request_id?: string | null;
  /** ФИО из справочника сотрудников (у заявления — любой сотрудник, не только монтажник). */
  operator_name?: string | null;
}

export interface ProductionShift {
  weekdays: number[];
  start: string;
  finish: string;
  breaks: [string, string][];
  effective_hours: string;
}

export interface ProductionSkill {
  operator_id: string;
  operation_code: string;
  allowed: boolean;
  stars: number;
  coefficient: string;
}

export interface ProductionScheduleVariant {
  transfer_batch: number;
  first_ready: string;
  start: string;
  finish: string;
  idle_seconds: number;
  warnings: string[];
  operations: Array<{
    operation_id: string;
    position: number;
    operator_ids: string[];
    start: string;
    finish: string;
    automatic: boolean;
  }>;
}

export interface ProductionBox {
  id: string;
  number: string | null;
  /** Код для подписи маркером «180К.11·КБ1·13.10·Б07·3»; null — коробка до 13.10.2026. */
  code: string | null;
  version: number;
  task_id: string;
  operation_id: string;
  item: string;
  operation_position: number;
  operator_id: string;
  quantity: string;
  consumed: string;
  reserved: string;
  rejected: string;
  available: string;
  status: string;
  created_at: string;
  closed_at: string | null;
  closed_by: string | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
  return_reason: string;
  qc_inspected?: boolean;
}

export interface ProductionWorkSession {
  id: string;
  task_id: string;
  operation_id: string;
  operator_id: string;
  started_at: string;
  status: string;
  pause_started_at: string | null;
  pause_seconds: number;
  /** Сколько штук можно сдать из взятых входных коробок; нет входов — null. */
  input_limit?: string | null;
}

export interface ProductionMaterialSource {
  fulfillment_line_id: string;
  article: string;
  name: string;
  unit: string;
  grn: string;
  package: string;
  issued: string;
  consumed: string;
  reserved: string;
  available: string;
}

export interface FinishedGoodsReadyBox extends ProductionBox {
  project: string;
  task_item: string;
}

export interface ProductionDefect {
  id: string;
  number: string;
  version: number;
  created_at: string;
  output_id: string;
  source_box_id: string;
  task_id: string;
  operation_id: string;
  operator_id: string;
  quantity: string;
  character: string;
  description: string;
  status: string;
  replaced_by_output_id: string | null;
}

export interface ProductionOutput {
  id: string;
  good_quantity: string;
  defect_quantity: string;
  finished_at: string;
  correction_of_id: string | null;
}

export interface WorkCall {
  onBehalfOf?: string;
  key?: string;
}

function behalfQuery(onBehalfOf: string | undefined): string {
  return onBehalfOf ? `?on_behalf_of=${encodeURIComponent(onBehalfOf)}` : "";
}

function replayHeaders(key: string | undefined): Record<string, string> | undefined {
  return key ? { "Idempotency-Key": key } : undefined;
}

export interface CorrectBoxInput {
  output_id: string;
  good_quantity: string;
  defect_quantity: string;
  defect_character: string;
  defect_description: string;
}

export interface ProductionInstrument {
  id: string;
  version: number;
  inventory_number: string;
  name: string;
  instrument_type: string;
  verification_number: string;
  verified_at: string;
  verification_due: string;
  status: string;
}

export interface ProductionQcRejection {
  id: string;
  box_id: string;
  box_number: string | null;
  box_code?: string | null;
  item: string;
  inspected_at: string;
  rejected: number;
  defect_number: string;
  defect_character: string;
  defect_description: string;
  status: string;
  decision: null | {
    id: string;
    disposition: string;
    decided_by: string;
    decided_at: string;
    act_reference: string;
  };
}

export interface ProductionSerial {
  id: string;
  serial_number: string;
  position: number;
  status: string;
  current_box_id: string | null;
  finished_goods_grn: string | null;
}

export interface GenealogyResult {
  nodes: Array<{ key: string; kind: string; label: string; details: string }>;
  edges: Array<{ source: string; target: string; quantity: string; relation: string }>;
}

export interface FinishedGood {
  id: string;
  version: number;
  project: string;
  item_index: string;
  customer: string;
  article: string;
  name: string;
  ordered: string;
  unit: string;
  accepted: string;
  shipped: string;
  remaining: string;
  /** Уехало с завода: отправки «Отгружена»/«Закрыта» плюс отгруженное до ERP. */
  sent: string;
  /** Отгружено ещё в Google (лист цеха «ЗАГРУЗ»), входит в принятое, отобранное и уехавшее. */
  before_erp: string;
  received_at: string | null;
  grn: string | null;
  note: string;
}

/** Состояние штук КБ в регистре готовой продукции: В производстве → Принято складом → Упаковано → Отгружено. */
export type GoodsStateKey = "production" | "accepted" | "packed" | "shipped";

/** Задание цеха КБ и сколько из его тиража сдано (площадка — «Сдано N шт», цех — годные финальной операции). */
export interface FinishedGoodsTaskShare {
  number: string;
  status: string;
  /** Площадка (ЖУК), пусто — свой цех. */
  site: string;
  quantity: number;
  delivered: string;
}

/** «Из производства»: КБ с живыми заданиями, которые склад принял не целиком. */
export interface FromProductionRow {
  project: string;
  item_index: string;
  name: string;
  unit: string;
  needed: string;
  /** Сдано цехом по живым заданиям. */
  delivered: string;
  accepted: string;
  /** Сдано, но склад ещё не принял — можно принять сейчас. */
  remaining: string;
  in_production: string;
  good_id: string | null;
  good_version: number | null;
  tasks: FinishedGoodsTaskShare[];
}

/** Ход журнала готовой продукции (новые сверху). */
export interface FinishedGoodsMove {
  id: string;
  item_index: string;
  kind: string;
  kind_label: string;
  quantity: string;
  from: GoodsStateKey;
  to: GoodsStateKey;
  from_label: string;
  to_label: string;
  shipment_id: string | null;
  shipment_number: string | null;
  actor: string;
  occurred_at: string;
  reason: string;
}

/** Сквозной статус КБ проекта: штуки в каждом состоянии, отправки КБ. */
export interface ProjectGoodsKb {
  item_index: string;
  name: string;
  unit: string;
  good_id: string | null;
  needed: string;
  delivered: string | null;
  waiting: string;
  pieces: Record<GoodsStateKey, string>;
  totals: { accepted: string; packed: string; shipped: string };
  has_tasks: boolean;
  journal_ok: boolean;
  shipments: Array<{ id: string; number: string; status: string; picked: string }>;
}

export interface ProjectGoodsStatus {
  project: string;
  states: Array<{ key: GoodsStateKey; label: string }>;
  kbs: ProjectGoodsKb[];
  moves: FinishedGoodsMove[];
}

export interface ShipmentLine {
  id: string;
  good_id: string;
  project: string;
  item_index: string;
  article: string;
  name: string;
  requested: string;
  picked: string;
  shortage: string;
  unit: string;
  grn: string | null;
}

export interface Shipment {
  id: string;
  number: string;
  version: number;
  created_at: string;
  status: string;
  customer: string;
  receiver: string;
  shipping_point: string;
  contact: string;
  phone: string;
  terms: "САМОВЫВОЗ" | "ДОСТАВКА ТК" | null;
  carrier: string;
  deadline: string | null;
  upd_type: "ЭДО" | "БУМАГА" | null;
  upd_number: string;
  upd_date: string | null;
  upd_url: string;
  pl_number: string;
  dn_number: string;
  track_number: string;
  packed_at: string | null;
  shipped_at: string | null;
  manager: string;
  note: string;
  tk_note: string;
  places: number;
  weight: string;
  boxes: ShipmentBox[];
  lines: ShipmentLine[];
  /** Поля адресата, которые идут за карточкой проекта, пока отправка не закрыта. */
  inherited: string[];
}

export interface ShipmentBox {
  place: number;
  kind: string;
  dims: string;
  net: string;
  gross: string;
  seal: string;
  items: Array<{ item_index: string; quantity: string }>;
}

/** Карточка отгрузки проекта (`ОТГРУЗКА.ПРОЕКТЫ`): адресат, доставка, тип УПД и реквизиты DN. */
export interface ProjectAddressee {
  project: string;
  version: number;
  receiver: string;
  shipping_point: string;
  contact: string;
  phone: string;
  terms: "САМОВЫВОЗ" | "ДОСТАВКА ТК" | null;
  carrier: string;
  upd_type: "ЭДО" | "БУМАГА" | null;
  updated_by: string;
  updated_at: string | null;
  po: string;
  contract: string;
  spec: string;
  invoice: string;
  pays: string;
  customer_address: string;
  hours: string;
}

export interface ReferencePair {
  key: string;
  value: string;
}

export interface ReferenceMaps {
  suppliers: ReferencePair[];
  countries: ReferencePair[];
  gost_awg: ReferencePair[];
  /** Which maps this user may change: supplier/country — «Лист закупки» on edit, ГОСТ→AWG — «Справочник» on edit. */
  can_edit?: { suppliers: boolean; gost_awg: boolean };
}

/** A tile of «Справочник» (SUPPLYMAP portalRefList): a base with its record count. */
export interface ReferenceBase {
  id: string;
  label: string;
  editable: boolean;
  icon: string;
  sub: string;
  count: number | null;
}

export type TechOperationTab = "ob" | "op" | "ter" | "coax" | "prov" | "mat" | "kab_coax" | "kab_awg";

/** «Кабели и провода»: the list a БД.ПРОВ row is in (backend `references/cables.py`). */
export type CableKind = "coax" | "wire" | "multi" | "other";

export interface BasesResult {
  bases: ReferenceBase[];
  /** Records per stored tab and per cable list — the counts on the tab chips. */
  tabs?: Partial<Record<TechOperationTab, number>>;
  cables?: Record<CableKind, number>;
  /** Rows of «Сечения AWG ↔ мм²». */
  gauges?: number;
}

/** «Сечения AWG ↔ мм²»: one conductor size (backend `references/cable_structure.py`). */
export interface WireGauge {
  id: string;
  /** «28», «1/0»; empty — a ГОСТ-only size. */
  awg: string;
  section_mm2: number;
  conductor_d_mm: number | null;
  strands: string;
  label: string;
  /** Hook-up wires on this size. */
  wires: number;
}

/** «Монтажные провода»: the structure of a БД.ПРОВ wire; the conductor is a size row. */
export interface HookupWire {
  series: string;
  gauge_id: string;
  insulation: string;
  outer_d_mm: number | null;
  voltage_v: number | null;
  temp_min_c: number | null;
  temp_max_c: number | null;
  /** «медь луженая»; "" — not known yet. Filled by the hook-up wire file import. */
  conductor_material?: string;
  insulation_thickness_mm?: number | null;
}

export interface CoaxCard {
  id: string;
  name: string;
  family: string;
  layers: Array<{ layer: string; label: string; material: string; diameter_mm: number | null }>;
  connectors: Array<{ family: string; max_ghz: number | null; max_note: string; span_to_ghz: number | null }>;
  /** VSWR — an acceptance norm proposed for the VNA check, not a datasheet value. `peak` — the «Peak» marker. */
  /** `vswr_ref` — what a reference assembly's datasheet guarantees (to settle a disputed reject). */
  markers: Array<{ freq_mhz: number | null; peak: boolean; vswr_max: number | null; vswr_ref: number | null; note: string }>;
  /** As the datasheet publishes it, plus the derived dB/m; `interpolated` + `basis` — not published at that frequency. */
  attenuation: Array<{
    freq_mhz: number; kind: "nominal" | "typical" | "max"; value: number; unit: string; ref_length_m: number;
    temp_c: number | null; loss_db_m: number; derived: boolean; interpolated: boolean; source: string; basis: string;
  }>;
  span_from_mhz: number | null;
  span_to_ghz: number | null;
  span_note: string;
  /** Cut-off of the first higher mode (TE11) — physics; not the rated limit `max_operating_ghz`. */
  fc_ghz: number | null;
  fc_note: string;
  max_operating_ghz: number | null;
  max_operating_note: string;
  /** Datasheet wording behind a number («50 ±2», «>165 (EZ)»), and `mechanical` notes. */
  param_notes: Record<string, string>;
  notes: string;
  sources: string[];
  confidence: string;
  verified: boolean;
  changes: string[];
  params: Partial<Record<CoaxParam, number>>;
}

export type CoaxParam = "impedance_ohm" | "velocity_pct" | "capacitance_pf_m" | "shielding_db" | "bend_static_mm"
  | "bend_dynamic_mm" | "temp_min_c" | "temp_max_c" | "voltage_max_v" | "max_power_w" | "max_power_at_mhz" | "weight_g_m";

/** The D1·D2·D3 row the ETK generator uses for a coax cable (СПР.КАБ · коаксиал). */
export interface CableDiameters {
  id: string;
  cable: string;
  D1: string;
  D2: string;
  D3: string;
}

export interface CableRow {
  /** БД.ПРОВ record id, or `dia:<id>` for a cable that is only in the D-table. */
  id: string;
  kind: CableKind;
  auto_kind: CableKind;
  reason: string;
  manual: boolean;
  cable: string;
  record: TechOperationRecord | null;
  dia: CableDiameters | null;
  /** No own D row: the row of the same family («RG58» for «RG-58 A/U»), to copy from. */
  dia_hint: CableDiameters | null;
  /** Coax: the card by the exact name (`card_exact`) or of the family («RG-58» for «RG-58 C/U»). */
  card?: CoaxCard | null;
  card_exact?: boolean;
  /** Монтажный wire: its structure, or — not structured yet — why, and what the БД.ПРОВ row suggests. */
  hookup?: HookupWire | null;
  hookup_problem?: string;
  hookup_suggestion?: Omit<HookupWire, "gauge_id"> & { gauge_id: string };
}

export interface CablesResult {
  columns: string[];
  counts: Record<CableKind, number>;
  rows: CableRow[];
  gauges?: WireGauge[];
}

/** «Оболочки»: БД.МАТ rows that cover a harness, grouped and parsed by the server (`coverings.py`). */
export type CoveringGroup = "tut" | "ttk" | "braid" | "corrugated" | "other";
export interface CoveringRow {
  id: string;
  name: string;
  group: CoveringGroup;
  /** «вручную» — the override cell; otherwise how the classifier decided. */
  group_source: string;
  auto_group: CoveringGroup | null;
  datasheet: string;
  unit: string;
  before_mm: number | null;
  after_mm: number | null;
  ratio: string;
  wall_mm: number | null;
  adhesive: string;
  nominal_mm: number | null;
  min_mm: number | null;
  max_mm: number | null;
  density: string;
  inner_mm: number | null;
  outer_mm: number | null;
  split: boolean | null;
  material: string;
  material_short: string;
  temp_min_c: number | null;
  temp_max_c: number | null;
  color: string;
  maker: string;
  series: string;
  series_family: string;
  notes: string[];
}
export interface CoveringsResult {
  counts: Record<CoveringGroup, number>;
  titles: Record<CoveringGroup, string>;
  rows: CoveringRow[];
}

export interface HookupPayload {
  series: string;
  gauge_id: string;
  insulation: string;
  outer_d_mm: string;
  voltage_v: string;
  temp_min_c: string;
  temp_max_c: string;
}

export interface TechOperationRecord {
  id: string;
  tab: TechOperationTab;
  display_text: string;
  source_sheet: string;
  fields: Record<string, string>;
}

/** GET /api/references/wire-dia — «Ø изоляции по маркам» из базы монтажных проводов: строка на размер «Сечения AWG ↔
 *  мм²», поля по порядку «По старой таблице» · AWG · ГОСТ · Ø жилы · Ø наружный по каждой из 12 серий («—» — нет).
 *  «По старой таблице» — серии, чей Ø в строке взят из старой СПР.КАБ (в базе его ещё нет). Читает генератор ЭТК. */
export interface WireDiaResult {
  columns: string[];
  series: string[];
  records: Array<{ id: string; display_text: string; source_sheet: string; fields: Record<string, string> }>;
}

export interface TechOperationResult {
  metadata: null | {
    schema_version: number;
    source_updated_at: string | null;
    imported_at: string;
    record_count: number;
  };
  columns?: string[];
  records: TechOperationRecord[];
}

/** ЭТК `document.route`: схема полуфабрикатов (блок = один полуфабрикат из 1..n операций ЭТК, стрелка = «берёт per_unit
 *  штук с блока-источника»). `ops` — 1-based позиции в `document.operations`. */
export interface EtkRouteBlock {
  key: string;
  name: string;
  ops: number[];
  final: boolean;
  components: unknown[];
  outputs: number;
  /** Входных полуфабрикатов на одну штуку блока (Σ per_unit входящих стрелок). */
  inputs: number;
  minutes: number;
  unit_minutes: number;
  rolled_minutes: number;
  rolled_ops: number;
}

export interface EtkRouteEdge {
  from: string;
  to: string;
  per_unit: number;
}

export interface EtkRoute {
  source: "derived" | "manual";
  final: string;
  blocks: EtkRouteBlock[];
  edges: EtkRouteEdge[];
}

export interface ProjectTechcard {
  id: string;
  version: number;
  project: string;
  item_index: string;
  product_name: string;
  source_calculation: string;
  deadline: string | null;
  status: "Черновик" | "Выдана";
  revision: number;
  document: Record<string, unknown> | null;
  manual_operation_keys: string[];
  created_at: string;
  updated_at: string;
  issued_at: string | null;
}

export interface TechcardRevision {
  id: string;
  revision: number;
  reason: string;
  actor_name: string;
  occurred_at: string;
  document: Record<string, unknown>;
}

export interface OperationCardSummary {
  id: string;
  code: string;
  side: string;
  title: string;
  name: string;
  category: string;
  steps: number;
  tooling: number;
  /** Catalog meta as SUPPLYMAP openTcRecords shows it; optional for older API answers. */
  controls?: number;
  first_step?: string;
  step_text?: string;
  /** БД.ОП time norms by code; null — no row in БД.ОП («нет норм»), norms_known=false — references not loaded. */
  norms?: { operation_seconds: string; setup_seconds: string } | null;
  norms_known?: boolean;
  changed_at?: string | null;
  changed_by?: string;
}

export interface OperationCardStepValue { number?: string; kind: string; text: string; tolerance: string }
export interface OperationCardToolValue {
  section: string;
  number?: string;
  name: string;
  inventory_number: string;
  program: string;
  parameters: string;
  norm: string;
  unit: string;
}

/** Шаблон-лист БД.ТК в каталоге (SUPPLYMAP tcTemplateCatalogForPortal). */
export interface TemplateSheet {
  id: string;
  title: string;
  category: string;
  description: string;
  size: string;
  updatedAt: string;
  savedBy: string;
}

/** Черновик шаблона-листа (SUPPLYMAP лист «Черновик — …»). */
export interface TemplateSheetDraft {
  id: string;
  draftSheet: string;
  existingTemplateId: string;
  title: string;
  category: string;
  description: string;
  createdAt: string;
  createdBy: string;
  updatedAt: string;
  rows?: string[][];
}

export interface TemplateSheetSaveResult {
  action: "created" | "updated";
  id: string;
  title: string;
  sizeLabel: string;
}

export interface OperationCardDetail extends OperationCardSummary {
  document: {
    safety: string;
    defects: string;
    revision?: string;
    steps: Array<{ number: string; kind: string; text: string; tolerance: string }>;
    tooling: Array<{
      section: string; number: string; name: string; inventory_number: string; program: string;
      parameters?: string; norm?: string; unit?: string;
    }>;
  };
  raw_rows: string[][];
}

/** SUPPLYMAP tcCardEditForm_ → portalTcRecordSave payload. */
export interface OperationSketch {
  operation_key: string;
  file_name: string;
  size: number;
  uploaded_by: string;
  uploaded_at: string;
}

export interface ConnectorFamily {
  family: string;
  title: string;
  sample: string;
  housing: import("./connectorArt").ConnectorHousing & { conf?: string; note?: string };
}

/** «База данных → Разъёмы» по сериям (backend techcards/connector_series.py). */
export interface ConnectorSeriesHousing {
  article: string;
  n: number;
  housing: ConnectorFamily["housing"];
  headers: Array<{ title: string; article: string }>;
}

export interface ConnectorTerminal {
  article: string; maker: string; product: string; series: string; kind: string;
  awg_from: string; awg_to: string; mm2_from: string; mm2_to: string; strip: string; applicator: string; crimp_height: string;
  pull_force: string;
}

export interface ConnectorSeries {
  id: string;
  title: string;
  maker: string;
  variants: Array<{ title: string; family: string; housings: ConnectorSeriesHousing[] }>;
  range_checked: boolean;
  range_src: string;
  datasheet: string;
  note: string;
  terminals: ConnectorTerminal[];
  wire: { awg_thin?: number; awg_thick?: number; mm2_min?: number; mm2_max?: number } | null;
  housing_count: number;
  counts: number[];
  pitch: number | null;
  color: string | null;
  checked: boolean;
  sample: ConnectorSeriesHousing | null;
  /** Раздел галереи: «Провод — плата», «Автомобильные», … — у каждой серии ровно один. */
  category: string;
  /** Артикулы БД.КОАКС для ВЧ-серий. */
  catalog: Array<{ article: string; kind: string; maker: string; wire: string }>;
  /** Документы производителя, по которым заполнен каталог серии (первый — основной). */
  sources?: Array<{ title: string; url: string }>;
  /** Контакты из каталога производителя; in_base — есть ли артикул (REEL или BAG) во вкладке «Терминалы». */
  contacts?: Array<{ article: string; wire: string; in_base: boolean }>;
}

export interface ConnectorSeriesMatch {
  series: string | null;
  article: string | null;
  housing: ConnectorFamily["housing"];
}

export interface OperationRecordEdit {
  name: string;
  safety: string;
  defects: string;
  revision?: string;
  steps: OperationCardStepValue[];
  tooling: OperationCardToolValue[];
}

export interface GrapherImportResult {
  project: { designation: string; name: string };
  harnesses: Array<{
    designation: string;
    quantity: number;
    pairs: number;
    warnings: string[];
    connectors: Array<{ designation: string; part_number: string; terminals: string[] }>;
    groups: Array<{
      material: string;
      length_mm: string | number | null;
      quantity: number;
      cores: number;
      jacket_mm: string | number | null;
      correction_from_mm: string | number;
      correction_to_mm: string | number;
      cut_mm: string | number | null;
      ends: string[];
      source_ids: string[];
      component_id: string;
      mixed_corrections?: boolean;
    }>;
  } & GrapherHarnessDetails>;
  format?: { snapshot_format: number | null; source_commit: string };
  catalog?: {
    pinned: Array<{ source_kind: string; record_key: string; version: string; name: string; value: string; unit: string }>;
    components: Array<{ snapshot_id: string; code: string; name: string; version: number | null; articles: string[] }>;
    placements: Array<{ harness_id: string; article_key: string; entity_type: string; snapshot_id: string; component_code: string }>;
  };
  import_warnings?: string[];
}

/** Операция генератора, которую подразумевает провод чертежа (type — тип операции генератора; shared — общая для
 *  нескольких проводов: сросток, пара, кабель, оболочка — брать один раз; block — строка маршрута чертежа). */
export interface GrapherImpliedOp {
  type: string; label: string; side: "" | "A" | "B"; shared: string; block: string; data: Record<string, unknown>;
}

export interface GrapherWireEnd {
  type: "contact" | "junction" | "screen" | "free"; type_label: string; title: string;
  connector_id: string; connector: string; part_number: string; contact_id: string; contact_number: number | null;
  contact_name: string; contact_type: string; circuit: string; terminal_article: string; terminal: string;
  terminal_l_plus_mm: number | null; terminal_l_minus_mm: number | null;
  terminal_details: { manufacturer: string; series: string; description: string; article: string } | null;
  junction_id: string; screen_id: string; screen_side: string; correction_mm: number; strip_profile: string;
}

type GrapherBlock = { id: string; title: string; index: string } | null;

/** Полное чтение жгута TECHMAP-GRAPHER (модель жгута ERP, backend/app/modules/techcards/harness). Необязательные —
 *  старый backend их не присылает. */
export interface GrapherHarnessDetails {
  id?: string;
  format?: { schema_version: number; writer_contract: number | null };
  wires?: Array<{
    id: string; circuit: string; color: string; material: string; material_article: string; section: string;
    component_id: string; cable_id: string; length_mm: number | null; correction_from_mm: number; correction_to_mm: number;
    rounding_step_mm: number; unrounded_mm: number | null; cut_mm: number | null; label: string;
    from: GrapherWireEnd; to: GrapherWireEnd; block: GrapherBlock; covering_ids: string[];
    ops: GrapherImpliedOp[]; route_ops: GrapherImpliedOp[];
  }>;
  junctions?: Array<{ id: string; label: string; wire_ids: string[]; ending_wire_ids: string[]; circuits: string[] }>;
  screens?: Array<{ id: string; label: string; wire_ids: string[]; terminal_side: string; drain_wire_ids: string[] }>;
  diff_pairs?: Array<{ id: string; wire_ids: string[]; circuits: string[]; step: number | null; amplitude: number | null; variant: number }>;
  twisted_groups?: Array<{ id: string; kind: string; label: string; wire_ids: string[] }>;
  cables?: Array<{
    id: string; label: string; material: string; material_article: string; component_id: string;
    member_wire_ids: string[]; cores: number; length_mm: number | null; correction_from_mm: number; correction_to_mm: number;
    rounding_step_mm: number; unrounded_mm: number | null; cut_mm: number | null;
    sheath_strip: { from_mm: number | null; to_mm: number | null; cut_from_mm: number | null; cut_to_mm: number | null } | null;
    block: GrapherBlock;
  }>;
  strip_profiles?: Array<{
    wire_id: string; end: "from" | "to"; circuit: string; profile: string; source_key: string;
    layers: Array<{ name: string; index: number; length_mm: number; step_mm: number; diameter_mm: number | null }>;
  }>;
  coverings?: Array<{
    id: string; name: string; kind: string; kind_label: string; material: string; material_article: string;
    component_id: string; color: string; length_mode: string; length_mm: number | null;
    spans: Array<{ segment_id: string; from: number; to: number; from_connector: string; to_connector: string }>;
    segment_ids: string[]; wire_ids: string[]; cable_ids: string[]; block: GrapherBlock; ops: GrapherImpliedOp[];
  }>;
  route_status?: string;
  route_status_label?: string;
  route_rows?: Array<{
    id: string; kind: string; kind_label: string; role: string; index: string; title: string;
    quantity: number | null; reserve: number | null; operation_time_min: number | null; comment: string; prepared: boolean;
    depends_on: string[]; sources: Array<{ kind: string; id: string; label: string }>;
    operations: Array<{ id: string; code: string; name: string; mode: string; note: string }>;
    terminal_requirements: Array<{ wire_id: string; end: "from" | "to"; terminal_article: string; strip_length_mm: number | null }>;
  }>;
  connector_details?: Array<{
    id: string; designation: string; part_number: string; library: string; template_code: string; template_name: string;
    template_article: string; template_version: number | null;
    contacts: Array<{
      id: string; number: number | null; name: string; contact_type: string; circuit: string; terminal_article: string;
      terminal: string; terminal_l_plus_mm: number | null; terminal_l_minus_mm: number | null; wire: string;
      wire_section: string; color: string; status: string;
    }>;
  }>;
  sheet_match?: Array<{
    kind: string; name: string; article: string; unit: string; quantity: number | null; item_ids: string[];
    sheet_id: string; sheet_quantity: number | null; matched_by: string;
    status: "ok" | "quantity" | "not_in_sheet" | "not_in_drawing";
  }>;
  sheet_warnings?: string[];
  import_warnings?: string[];
}

/** Строка «Данных из листа» для сверки с чертежом. */
export type GrapherSheetRow = { id: string; type: string; name: string; article: string; kd: string; qty: number | string | null };

export interface NotificationItem {
  id: string;
  created_at: string;
  type: string;
  reference: string;
  title: string;
  body: string;
  read: boolean;
}

export interface AuditItem {
  id: string;
  aggregate_id: string;
  event_type: string;
  actor: string;
  department: string;
  occurred_at: string;
  details: Record<string, unknown>;
}

export type BackgroundStatus = "ok" | "warn" | "red";

/** Группа фоновых работ воркера (аудит U08): последний успех, время прогона, сбои подряд. */
export interface BackgroundGroup {
  key: string;
  label: string;
  status: BackgroundStatus;
  success_at: string | null;
  failure_at: string | null;
  error: string | null;
  duration_ms: number;
  failures: number;
  max_silence_seconds: number;
}

/** Тема очереди с ожидающими сообщениями: сколько и сколько ждёт самое старое. */
export interface BackgroundTopic {
  topic: string;
  count: number;
  oldest_at: string;
  age_seconds: number;
  status: BackgroundStatus;
}

export interface OutboxHealth {
  total: number;
  published: number;
  pending: number;
  dead: number;
  failures: Array<{
    id: string;
    topic: string;
    attempts: number;
    error: string;
    dead: boolean;
  }>;
  ok?: boolean;
  groups?: BackgroundGroup[];
  topics?: BackgroundTopic[];
  oldest_pending_seconds?: number | null;
  /** «Повторить» остановленное задание — только администратору. */
  can_retry?: boolean;
  /** Сайты поставщиков, которые читаются без проверки сертификата (ERP_TLS_UNVERIFIED_HOSTS). */
  tls_exceptions?: string[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
  }
}

function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    return (detail as { message: string }).message;
  }
  return `HTTP ${status}`;
}

export function manualRatePrompt(error: unknown): ManualRatePrompt | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail as Partial<ManualRatePrompt> | undefined;
  return detail?.code === "manual_rate_required" ? (detail as ManualRatePrompt) : null;
}

function cookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

/** «Failed to fetch» (TypeError): ERP не ответила — перезапуск при обновлении или сеть. */
export const NO_CONNECTION = "Нет связи с ERP: сервер перезапускается или недоступен. Обнови страницу через минуту.";
/** Срок ответа на чтение; дольше — чтение обрывается понятным текстом, кнопки снова доступны. */
export const READ_DEADLINE_MS = 20_000;
/** Срок ответа на запись. Его истечение — не отказ: запись могла пройти (см. UnknownResultError). */
export const WRITE_DEADLINE_MS = 120_000;
export const READ_TIMEOUT_TEXT = "ERP не ответила за 20 с — проверьте связь и нажмите «Повторить».";
export const UNKNOWN_RESULT_TEXT = "Результат неизвестен: ответ ERP не дошёл. Проверьте, записалось ли, прежде чем повторять.";

/** Чтение не уложилось в срок (READ_DEADLINE_MS): ничего не записано, повтор безопасен. */
export class ApiTimeoutError extends Error {
  constructor(message = READ_TIMEOUT_TEXT) {
    super(message);
    this.name = "ApiTimeoutError";
  }
}

/** Запись ушла, а ответ не пришёл (сеть оборвалась или истёк срок): она могла выполниться. Не повторять вслепую —
 *  сверить состояние или повторить с тем же ключом идемпотентности; резерв и оплату сами не повторяем. */
export class UnknownResultError extends Error {
  constructor(message = UNKNOWN_RESULT_TEXT) {
    super(message);
    this.name = "UnknownResultError";
  }
}

/** Чтение отменено, потому что его заменило более новое (`latestRead`): ни ошибку, ни данные показывать не нужно. */
export function isAborted(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

/** Отмена устаревших чтений: каждый вызов отменяет предыдущий сигнал и выдаёт новый (поиск, перезагрузка списка). */
export function latestRead(): () => AbortSignal {
  let current: AbortController | null = null;
  return () => {
    current?.abort();
    current = new AbortController();
    return current.signal;
  };
}

export function failureText(reason: unknown, fallback: string) {
  if (reason instanceof TypeError) return NO_CONNECTION;
  return reason instanceof Error ? reason.message : fallback;
}

/** `timeoutMs`: свой срок ответа (null — без срока); `safe`: POST, который ничего не меняет или безопасен к повтору
 *  (предпросмотр, вход, выход) — его срок и обрыв сети считаются как у чтения. */
export type ApiInit = RequestInit & { timeoutMs?: number | null; safe?: boolean };

const READ_METHODS = ["GET", "HEAD", "OPTIONS", "TRACE"];

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const { timeoutMs, safe, signal: outer, ...request } = init;
  const headers = new Headers(request.headers);
  // FormData (a file upload) sets its own multipart boundary — the header is left to the browser.
  if (request.body && !(request.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const method = (request.method ?? "GET").toUpperCase();
  if (!READ_METHODS.includes(method)) {
    const csrf = cookie("__Host-erp_csrf") || cookie("erp_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const reading = safe ?? READ_METHODS.includes(method);
  // A file upload has no deadline: a large scan over a slow link is not a hung request.
  const deadline = timeoutMs !== undefined ? timeoutMs
    : reading ? READ_DEADLINE_MS
      : request.body instanceof FormData ? null : WRITE_DEADLINE_MS;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let onAbort: (() => void) | undefined;
  // Settles only on the deadline or the caller's cancel; it races the request, so a fetch that ignores the signal ends too.
  const stop = new Promise<never>((_, reject) => {
    if (deadline) {
      timer = setTimeout(() => {
        controller.abort();
        reject(reading ? new ApiTimeoutError() : new UnknownResultError());
      }, deadline);
    }
    if (outer) {
      onAbort = () => {
        controller.abort();
        reject(new DOMException("Запрос заменён более новым", "AbortError"));
      };
      if (outer.aborted) onAbort();
      else outer.addEventListener("abort", onAbort, { once: true });
    }
  });
  async function send(): Promise<T> {
    const response = await fetch(path, { ...request, headers, credentials: "include", signal: controller.signal });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/pin-login") {
        window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
      }
      throw new ApiError(errorMessage(payload.detail, response.status), response.status, payload.detail);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  try {
    return await Promise.race([send(), stop]);
  } catch (reason) {
    // A write whose connection broke may still have been applied on the server: not a failure, an unknown result.
    if (!reading && reason instanceof TypeError) throw new UnknownResultError();
    throw reason;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    if (outer && onAbort) outer.removeEventListener("abort", onAbort);
  }
}

export const identityApi = {
  me: () => api<Principal>("/api/auth/me"),
  // Sign-in, sign-out and the admin switch carry the target state: repeating them is harmless (`safe`).
  login: (login: string, password: string) =>
    api<Principal>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
      safe: true,
    }),
  loginStaff: () => api<{ staff: LoginStaff[] }>("/api/auth/login-staff"),
  /** An empty PIN asks for «узнать PIN» (first sign-in); the answer is then a PinReveal, not a session. */
  pinLogin: (who: string, pin: string) =>
    api<Principal | PinReveal>("/api/auth/pin-login", { method: "POST", body: JSON.stringify({ who, pin }), safe: true }),
  adminMode: (on: boolean) =>
    api<Principal>("/api/auth/admin-mode", { method: "POST", body: JSON.stringify({ on }), safe: true }),
  logout: () => api<void>("/api/auth/logout", { method: "POST", safe: true }),
  /** «Смотреть как сотрудник» (administrator only): the answer is the viewed employee's principal. */
  viewAs: (userId: string) =>
    api<Principal>("/api/auth/view-as", { method: "POST", body: JSON.stringify({ user_id: userId }) }),
  /** «Вернуться к себе»: the own principal again. */
  stopViewAs: () => api<Principal>("/api/auth/view-as", { method: "DELETE", safe: true }),
  profile:() => api<OwnProfile>("/api/auth/profile"),
  changePassword: (current: string, next: string) =>
    api<void>("/api/auth/password", { method: "POST", body: JSON.stringify({ current, new: next }) }),
};

/** The signed-in employee as the header shows them (SUPPLYMAP portalState_: role, department, site). */
export interface OwnProfile {
  name: string;
  login: string;
  email: string;
  department: string;
  /** Legacy position key; the level is `access_level`. */
  role: string;
  role_label: string;
  access_level?: string;
  access_level_label?: string;
  site: string;
  site_label: string;
  /** «Личная карточка»: кадровые поля, blank until the administrator fills them. */
  personnel_number?: string;
  job_title?: string;
  qualification?: string;
  job_since?: string | null;
  manager_id?: string | null;
  manager?: PersonRef | null;
  department_head?: PersonRef | null;
}

export interface PersonRef {
  id: string;
  name: string;
}

/** Кадровые поля «Личной карточки»: «» / null — не заполнено. `job_since` — ISO date (2026-10-04). */
export interface PersonnelFields {
  /** Табельный номер, unique (FRX-0017). */
  personnel_number: string;
  /** Должность. */
  job_title: string;
  /** Категория / квалификация. */
  qualification: string;
  /** В должности с. */
  job_since: string | null;
  /** Непосредственный руководитель (id сотрудника). */
  manager_id: string | null;
}

/** ID-8 «Сотрудники»: the directory as the administrator sees it. No password ever comes back. */
export interface Employee extends PersonnelFields {
  id: string;
  login: string;
  email: string;
  name: string;
  department: string;
  /** Уровень доступа (ключ) и его подпись; `role` — прежняя роль, один выпуск. */
  access_level: string;
  access_level_label?: string;
  role?: string;
  site: string;
  active: boolean;
  /** «Монтажник (работает в цеху)» и «Мастер площадки»: флаги человека, не уровня. */
  shop_worker: boolean;
  site_master: boolean;
  /** portalStaffGet `pinSet`: a PIN is assigned (the PIN itself never comes back). */
  pin_set?: boolean;
}

/** Уровень доступа; `full` — суперадминистратор и администратор: всегда полный доступ, матрица их не меняет. */
export interface AccessLevel {
  key: string;
  label: string;
  full: boolean;
}

export interface StaffDirectory {
  users: Employee[];
  departments: string[];
  /** Отдел → id руководителя (только отделы с руководителем). */
  department_heads: Record<string, string>;
  levels: AccessLevel[];
  sites: Array<{ code: string; city: string }>;
}

export interface EmployeeCreate extends PersonnelFields {
  login: string;
  email: string;
  name: string;
  department: string;
  access_level: string;
  site: string;
  password: string;
  /** 6–8 digits; empty — no PIN yet. */
  pin: string;
  shop_worker: boolean;
  site_master: boolean;
}

/** A personnel field left out stays as stored; «» / null clears it. A flag left out stays as stored too (it still
 *  follows the old role rule); true / false sets it. */
export interface EmployeeUpdate extends Partial<PersonnelFields> {
  name: string;
  department: string;
  access_level: string;
  site: string;
  active: boolean;
  password?: string | null;
  pin?: string | null;
  shop_worker?: boolean;
  site_master?: boolean;
}

export const staffApi = {
  directory: () => api<StaffDirectory>("/api/auth/staff"),
  create: (payload: EmployeeCreate) =>
    api<{ user: Employee; pin: string }>("/api/auth/staff", { method: "POST", body: JSON.stringify(payload) }),
  update: (id: string, payload: EmployeeUpdate) =>
    api<{ user: Employee }>(`/api/auth/staff/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** Erases an employee without history; one with history is switched off and hidden (`deleted: false`). */
  remove: (id: string) =>
    api<{ user: Employee; deleted: boolean }>(`/api/auth/staff/${encodeURIComponent(id)}`, { method: "DELETE" }),
  saveDepartments: (departments: string[]) =>
    api<{ departments: string[] }>("/api/auth/departments", { method: "PUT", body: JSON.stringify({ departments }) }),
  renameDepartment: (source: string, target: string) =>
    api<{ departments: string[]; users: number }>("/api/auth/departments/rename", {
      method: "POST",
      body: JSON.stringify({ source, target }),
    }),
  /** Руководитель отдела; `headId` null — без руководителя. */
  setDepartmentHead: (department: string, headId: string | null) =>
    api<{ departments: string[]; department_heads: Record<string, string> }>("/api/auth/departments/head", {
      method: "PUT",
      body: JSON.stringify({ department, head_id: headId }),
    }),
};

/** NAV-ACCESS «Доступы»: a stored cell. In the level matrix an absent / "" cell is closed; in a person's
 *  exceptions it is «как у уровня» and `none` closes the section. */
export type AccessSetting = "none" | "view" | "edit";
export type AccessRow = Partial<Record<string, AccessSetting>>;

export interface AccessUser {
  id: string;
  login: string;
  name: string;
  /** Уровень доступа человека (ключ); `role` — прежняя роль. */
  access_level: string;
  role?: string;
  active: boolean;
  /** Личные исключения поверх уровня. */
  access: AccessRow;
}

export interface AccessOverview {
  sections: Array<{ key: string; label: string }>;
  levels: AccessLevel[];
  /** What a level has with nothing stored: superadmin / admin — everything on edit, any other level — closed. */
  defaults: Record<string, Partial<Record<string, AccessMode>>>;
  /** Рекомендованные строки уровней (план доступов). */
  seed?: Record<string, AccessRow>;
  /** Stored rows of the levels the matrix drives (not superadmin / admin). */
  matrix: Record<string, AccessRow>;
  users: AccessUser[];
}

export const accessApi = {
  overview: () => api<AccessOverview>("/api/auth/access"),
  saveLevels: (matrix: Record<string, AccessRow>) =>
    api<{ matrix: Record<string, AccessRow> }>("/api/auth/access/levels", { method: "PUT", body: JSON.stringify({ matrix }) }),
  saveUser: (id: string, access: AccessRow) =>
    api<{ user_id: string; access: AccessRow }>(`/api/auth/access/users/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ access }),
    }),
};

export const requestsApi = {
  schema: () => api<RequestSchema>("/api/requests/schema"),
  list: (q = "", signal?: AbortSignal) =>
    api<{ requests: RequestRow[] }>(q ? `/api/requests?q=${encodeURIComponent(q)}` : "/api/requests", { signal }),
  /** RQ-MINE: server-side filter by the authenticated initiator, paginated (replaces the client-side
   *  name match over the latest 200 rows). */
  mine: (limit: number, offset: number, q = "", signal?: AbortSignal) =>
    api<MineRequestsResult>(
      `/api/requests/mine?limit=${limit}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
      { signal },
    ),
  create: (request: Record<string, unknown>, idempotencyKey: string) =>
    api<{ requests: RequestRow[] }>("/api/requests", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ requests: [request] }),
    }),
  /** RQ-IMPORT-UI «Вставить из Excel»: one create call, every pasted position at once. */
  createMany: (requests: Record<string, unknown>[], idempotencyKey: string) =>
    api<{ requests: RequestRow[] }>("/api/requests", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ requests }),
    }),
  handoff: (requestIds: string[], retransfer = false) =>
    api<{ accepted: unknown[] }>("/api/requests/handoff", {
      method: "POST",
      body: JSON.stringify({ request_ids: requestIds, retransfer }),
    }),
  /** «Из расчёта» as SUPPLYMAP `portalExtractFromCalc`: the server reads the linked calculation sheet. */
  importFromLink: (url: string) =>
    api<CalcImportPreview>("/api/requests/import/calc", {
      method: "POST",
      body: JSON.stringify({ url }),
      // A preview writes nothing; reading a Google sheet may take longer than a plain read.
      safe: true,
      timeoutMs: 60_000,
    }),
  /** Fallback when the server has no Google key: the ЗКП range pasted as a grid. */
  importParse: (grid: Array<Array<string | null>>) =>
    api<CalcImportPreview>("/api/requests/import/parse", {
      method: "POST",
      body: JSON.stringify({ grid }),
      safe: true,
    }),
  importCreate: (
    payload: {
      project: string;
      purpose: string;
      delivery_point: string;
      procurement_deadline: string;
      items: Array<Record<string, unknown>>;
    },
    idempotencyKey: string,
  ) =>
    api<{ requests: RequestRow[]; warnings: string[] }>("/api/requests/import", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),
};

export type KpRowInput = {
  line_id: string;
  version: number;
  actual_quantity: string | null;
  actual_unit: string | null;
  currency: string | null;
  driver?: MoneyDriver;
  price: string | null;
  vat_rate: string | null;
  price_with_vat: string | null;
  sum: string | null;
  invoice_item_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  expected_arrival: string | null;
};

export type KpMatch = { line_id: string; name: string; qty: string; price: string; vat: string };

export type OrderLineRef = { line_id: string; version: number };

/** «Вставить счёт» / «Вставить КП»: what the server read from the file, matched to the order's lines. */
export type OrderDocumentKind = "invoice" | "kp";
export type MatchConfidence = "article" | "name" | "weak" | null;

export interface RecognizedOrderLine {
  line_id: string;
  legacy_id: string;
  version: number;
  price_with_vat: string | null;
  price: string | null;
  vat_rate: string | null;
  quantity: string | null;
  unit: string | null;
  sum: string | null;
  matched_name: string | null;
  confidence: MatchConfidence;
  /** Кол-во или ед. в документе расходятся со строкой. */
  warnings?: string[];
}

export interface OrderDocumentRecognition {
  file: { url: string; name: string };
  kind: OrderDocumentKind;
  invoice_number: string | null;
  invoice_date: string | null;
  currency: string | null;
  /** false — цены не читаются автоматически (скан без текста, валюта); `hint` говорит, что делать. */
  auto?: boolean;
  hint?: string;
  lines: RecognizedOrderLine[];
  unmatched_items: { name: string; quantity: string | null; price_with_vat: string | null }[];
  unmatched_lines: string[];
  totals: { sum: string | null; vat: string | null; sum_with_vat: string | null } | null;
}

export interface ApplyOrderDocumentInput {
  kind: OrderDocumentKind;
  file_url: string;
  invoice_number?: string | null;
  invoice_date?: string | null;
  expected_arrival?: string | null;
  /** `invoice_item_name` — «Наименование в счёте»; пусто — сервер берёт название с сайта поставщика. */
  lines: (OrderLineRef & { price_with_vat: string | null; invoice_item_name?: string | null })[];
}

/** Вид файла строки закупки: разрешение (на замену) или документ качества. */
export type ProcurementFileKind = "permit" | "quality";

export interface ProcurementFile {
  id: string;
  kind: ProcurementFileKind;
  file_name: string;
  url: string;
  uploaded_by: string;
  uploaded_at: string;
}

export interface InvoiceUpload {
  url: string;
  file_name: string;
  invoice_number: string;
  invoice_date: string;
  hint: string;
}

// «Мои позиции» / «Режим просмотра»: большие страницы — тысячи строк за 1–5 запросов, а не за 50.
const PROCUREMENT_QUEUE_PAGE = 500;

export const procurementApi = {
  kpOrder: (reference: string) =>
    api<{ order_reference: string; lines: ProcurementLine[] }>(
      `/api/procurement/kp?reference=${encodeURIComponent(reference)}`,
    ),
  parseKp: (reference: string, fileName: string, contentBase64: string) =>
    api<{ matches: KpMatch[]; not_found: string[]; items: Omit<KpMatch, "line_id">[] }>(
      "/api/procurement/kp/parse",
      {
        method: "POST",
        body: JSON.stringify({ order_reference: reference, file_name: fileName, content_base64: contentBase64 }),
      },
    ),
  saveKp: (reference: string, rows: KpRowInput[], invoiceUrl: string, manualExchangeRate?: string) =>
    api<{ lines: ProcurementLine[]; invoice_error: string; warnings?: string[] }>("/api/procurement/kp", {
      method: "POST",
      body: JSON.stringify({
        order_reference: reference,
        rows,
        invoice_url: invoiceUrl || null,
        manual_exchange_rate: manualExchangeRate || null,
      }),
    }),
  available: (after?: string) =>
    api<{ lines: ProcurementLine[]; next_cursor: string | null }>(
      `/api/procurement/available${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    ),
  mine: (after?: string) =>
    api<{ lines: ProcurementLine[]; next_cursor: string | null }>(
      `/api/procurement/mine?limit=${PROCUREMENT_QUEUE_PAGE}${after ? `&after=${encodeURIComponent(after)}` : ""}`,
    ),
  // «Режим просмотра» (ID-6): every buyer's lines in work — the read-only «что едет».
  inWork: (after?: string) =>
    api<{ lines: ProcurementLine[]; next_cursor: string | null }>(
      `/api/procurement/in-work?limit=${PROCUREMENT_QUEUE_PAGE}${after ? `&after=${encodeURIComponent(after)}` : ""}`,
    ),
  // Весь лист закупки: все статусы и закупщики, поиск и отбор на сервере, страницы по курсору.
  sheet: (filters: { q?: string; status?: string; buyer?: string }, after?: string) => {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.status) params.set("status", filters.status);
    if (filters.buyer) params.set("buyer", filters.buyer);
    if (after) params.set("after", after);
    const query = params.toString();
    return api<{ lines: ProcurementLine[]; next_cursor: string | null; buyers: string[] }>(
      `/api/procurement/lines${query ? `?${query}` : ""}`,
    );
  },
  // PR-11 portalPurchaseBySupplier: открытые строки всех закупщиков и порядок этапов.
  bySupplier: () => api<{ lines: ProcurementLine[]; stages: string[] }>("/api/procurement/by-supplier"),
  meta: () => api<{ units: string[]; statuses: string[] }>("/api/procurement/meta"),
  // portalSavePurchaseFacts: отмеченные строки одним вызовом.
  saveFactsBatch: (rows: Record<string, unknown>[]) =>
    api<{
      lines: ProcurementLine[];
      saved: number;
      ready: number;
      skipped: string[];
      failed: { legacy_id: string; error: string }[];
    }>("/api/procurement/facts", { method: "POST", body: JSON.stringify({ rows }) }),
  /** Файл счёта или КП по заказу → цены строк, № и дата, распознанные сервером (ничего в строки не пишет). */
  recognizeOrderDocument: async (orderNumber: string, file: File, kind: OrderDocumentKind) => {
    // Файл — base64 в JSON, как у загрузки счёта строки (сервер без multipart).
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
      reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
      reader.readAsDataURL(file);
    });
    return api<OrderDocumentRecognition>(`/api/procurement/orders/${encodeURIComponent(orderNumber)}/recognize`, {
      method: "POST",
      body: JSON.stringify({ kind, file_name: file.name, content_type: file.type || "application/octet-stream", content_base64: content }),
    });
  },
  /** Проверенные цены документа — все строки заказа или ни одной (409 — набор устарел, 422 — сервер называет строку).
   *  КП: цены, строки остаются «Жду счет»; счёт: цены и счёт, строки уходят «В оплату». */
  applyOrderDocument: (orderNumber: string, input: ApplyOrderDocumentInput) =>
    api<{ lines: ProcurementLine[]; warnings?: string[] }>(`/api/procurement/orders/${encodeURIComponent(orderNumber)}/apply-document`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  /** «Оплачено» по заказу: все строки «В оплату» → «В пути»; 409 `manual_rate_required` — ЦБ молчит, нужен курс. */
  orderPaid: (orderNumber: string, lines: OrderLineRef[], manualExchangeRate?: string) =>
    api<{ lines: ProcurementLine[] }>(`/api/procurement/orders/${encodeURIComponent(orderNumber)}/paid`, {
      method: "POST",
      body: JSON.stringify(manualExchangeRate ? { lines, manual_exchange_rate: manualExchangeRate } : { lines }),
    }),
  take: (lineIds: string[]) =>
    api<{ lines: ProcurementLine[]; skipped?: string[] }>("/api/procurement/take", {
      method: "POST",
      body: JSON.stringify({ line_ids: lineIds }),
    }),
  save: (lineId: string, facts: Record<string, unknown>) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/facts`, {
      method: "PATCH",
      body: JSON.stringify(facts),
    }),
  /** Без списка — все свои строки «Готов к запросу»; со списком — только отмеченные. */
  buildSupplierRequests: (lineIds?: string[]) =>
    api<SupplierRequestsResult>("/api/procurement/supplier-requests", lineIds
      ? { method: "POST", body: JSON.stringify({ line_ids: lineIds }) }
      : { method: "POST" }),
  setReplacement: (
    lineId: string,
    version: number,
    replacement: boolean,
    decision: "Разрешено" | "Запрещено" | null,
  ) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/replacement`, {
      method: "PATCH",
      body: JSON.stringify({ version, replacement, decision }),
    }),
  setTraceability: (lineId: string, version: number, trace: ProcurementTraceabilityInput) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/traceability`, {
      method: "PATCH",
      body: JSON.stringify({ version, ...trace }),
    }),
  setNote: (lineId: string, version: number, note: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/note`, {
      method: "PATCH",
      body: JSON.stringify({ version, note }),
    }),
  setDatasheet: (lineId: string, version: number, datasheet: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/datasheet`, {
      method: "PATCH",
      body: JSON.stringify({ version, datasheet }),
    }),
  /** Файл разрешения или документа качества к строке; документ качества ставит «Док. качества: Да». */
  uploadLineFile: (lineId: string, kind: ProcurementFileKind, file: { name: string; type: string; base64: string }) =>
    api<{ file: ProcurementFile; line: ProcurementLine }>(`/api/procurement/${lineId}/files`, {
      method: "POST",
      body: JSON.stringify({ kind, file_name: file.name, content_type: file.type, content_base64: file.base64 }),
    }),
  setExpectedArrival: (lineId: string, version: number, expectedArrival: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/expected-arrival`, {
      method: "PATCH",
      body: JSON.stringify({ version, expected_arrival: expectedArrival }),
    }),
  /** ФАКТ. ДАТА руками (необязательно): пустая строка стирает дату. */
  setActualArrival: (lineId: string, version: number, actualArrival: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/actual-arrival`, {
      method: "PATCH",
      body: JSON.stringify({ version, actual_arrival: actualArrival || null }),
    }),
  replacementPermissionSheet: (lineId: string) =>
    api<{ sheet: PrintSheet }>(`/api/procurement/${lineId}/replacement-permission`),
  recordInvoice: (
    lineId: string,
    version: number,
    invoiceUrl: string,
    expectedArrival: string,
    invoiceNumber = "",
    invoiceDate = "",
  ) =>
    api<{ line: ProcurementLine; warnings?: string[] }>(`/api/procurement/${lineId}/invoice`, {
      method: "POST",
      body: JSON.stringify({
        version,
        invoice_url: invoiceUrl,
        expected_arrival: expectedArrival,
        invoice_number: invoiceNumber.trim() || null,
        invoice_date: invoiceDate || null,
      }),
    }),
  /** Invoices.gs `uploadInvoice`: the file goes to ERP storage; № and date come back recognised for the form. */
  uploadInvoiceFile: (lineId: string, file: { name: string; type: string; base64: string }) =>
    api<InvoiceUpload>("/api/procurement/invoice-files", {
      method: "POST",
      body: JSON.stringify({ line_id: lineId, file_name: file.name, content_type: file.type, content_base64: file.base64 }),
    }),
  replaceInvoice: (
    lineId: string,
    version: number,
    invoiceUrl: string,
    invoiceNumber: string,
    invoiceDate: string,
  ) =>
    api<{ line: ProcurementLine; warnings?: string[] }>(`/api/procurement/${lineId}/invoice/replace`, {
      method: "POST",
      body: JSON.stringify({
        version,
        invoice_url: invoiceUrl,
        invoice_number: invoiceNumber.trim() || null,
        invoice_date: invoiceDate || null,
      }),
    }),
  saveMoney: (lineId: string, version: number, money: ProcurementMoneyInput) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/money`, {
      method: "PATCH",
      body: JSON.stringify({ version, ...money }),
    }),
  markPaid: (lineId: string, version: number, manualExchangeRate?: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/paid`, {
      method: "POST",
      body: JSON.stringify(
        manualExchangeRate ? { version, manual_exchange_rate: manualExchangeRate } : { version },
      ),
    }),
  reconcileRate: (lineId: string, version: number) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/exchange-rate/reconcile`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  confirmBackorder: (lineId: string, version: number, expectedArrival: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/backorder/confirm`, {
      method: "POST",
      body: JSON.stringify({ version, expected_arrival: expectedArrival }),
    }),
  // Ручного «На согласование» нет (владелец, 04.10): сервер отвечает 422. Выход остаётся для строк,
  // поставленных туда раньше.
  resumeFromApproval: (lineId: string, version: number) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/approval/resume`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  cancelLine: (lineId: string, version: number) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  cancelBackorder: (lineId: string, version: number) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/backorder/cancel`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  split: (lineId: string, version: number, copies: number) =>
    api<{ line: ProcurementLine; copies: ProcurementLine[] }>(`/api/procurement/${lineId}/split`, {
      method: "POST",
      body: JSON.stringify({ version, copies }),
    }),
};

export interface ReceivingQuery {
  after?: string;
  preset?: "open" | "to_stock" | "done" | "archive" | "all";
  status?: string[];
  limit?: number;
}

export type DeliveryDeviation = "none" | "brak" | "peresort" | "no_quality_doc" | "expired";

export interface DeliveryLinePayload {
  id: string;
  version: number;
  document_quantity?: string;
  fact_quantity?: string;
  storage_location?: string;
  deviation?: DeliveryDeviation;
  deviation_quantity?: string;
  expiry_date?: string;
}

export interface DeliveryPayload {
  lines: DeliveryLinePayload[];
  shipping_document_url: string | null;
  /** УПД необязателен: часто приходит по ЭДО позже («Внести УПД»). */
  document_number?: string;
  document_date?: string | null;
  warehouse?: string;
}

export const receivingApi = {
  /** `preset` (open / to_stock / done / archive / all) and the repeatable `status` filter on the server. */
  list: (options: string | ReceivingQuery = {}) => {
    const { after, preset, status = [], limit } = typeof options === "string" ? { after: options } as ReceivingQuery : options;
    const query = new URLSearchParams();
    if (after) query.set("after", after);
    if (preset) query.set("preset", preset);
    for (const value of status) query.append("status", value);
    if (limit) query.set("limit", String(limit));
    const text = query.toString();
    return api<{ lines: ReceivingLine[]; next_cursor: string | null }>(`/api/receiving${text ? `?${text}` : ""}`);
  },
  /** «Провести поставку»: один УПД, позиции одного поставщика на один склад; количества — только при расхождении. */
  acceptDelivery: (payload: DeliveryPayload) =>
    api<{ delivery_id: string; lines: ReceivingLine[] }>("/api/receiving/deliveries", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** WH-6: «Принято» без места на складе — печатный бланк через тот же механизм, что FF-8 (print.ts). */
  putawaySheet: () => api<{ sheet: PrintSheet }>("/api/receiving/putaway-sheet"),
  inspect: (lineId: string, payload: Record<string, unknown>) =>
    api<{ line: ReceivingLine; lines?: ReceivingLine[] }>(`/api/receiving/${lineId}/inspect`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** «Внести УПД»: один документ на несколько позиций заказа, всё или ничего. */
  attachDocuments: (
    lines: { id: string; version: number }[],
    payload: { shipping_document_url: string | null; document_number: string; document_date: string | null },
  ) =>
    api<{ lines: ReceivingLine[] }>("/api/receiving/documents", {
      method: "POST",
      body: JSON.stringify({ lines, ...payload }),
    }),
  place: (lineId: string, version: number, storageLocation: string) =>
    api<{ line: ReceivingLine }>(`/api/receiving/${lineId}/place`, {
      method: "POST",
      body: JSON.stringify({ version, storage_location: storageLocation }),
    }),
  /** RC-8 «Разукомплектовка (пакеты)»: принятая строка делится на пакеты с общим ГРН до раскладки. */
  unpack: (lineId: string, version: number, quantities: string[]) =>
    api<{ lines: ReceivingLine[] }>(`/api/receiving/${lineId}/unpack`, {
      method: "POST",
      body: JSON.stringify({ version, quantities }),
    }),
  /** УПД/М-15 or the quality document into the ERP storage; the returned link goes into the ВК form. */
  uploadFile: (lineId: string, kind: "upd" | "quality", file: { name: string; type: string; base64: string }) =>
    api<{ url: string; file_name: string; size: number }>("/api/receiving/files", {
      method: "POST",
      body: JSON.stringify({ line_id: lineId, kind, file_name: file.name, content_type: file.type, content_base64: file.base64 }),
    }),
  actSheet: (lineId: string, kind: string) =>
    api<{ sheet: PrintSheet }>(`/api/receiving/${lineId}/acts/${kind}`),
  openVkRevision: (lineId: string, version: number) =>
    api<{ archived: ReceivingLine; line: ReceivingLine }>(`/api/receiving/${lineId}/vk-revision`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  supplierReturnCandidates: () =>
    api<{ lines: SupplierReturnCandidate[]; reasons: string[]; modes: Record<string, string> }>(
      "/api/receiving/supplier-return",
    ),
  returnToSupplier: (payload: SupplierReturnPayload) =>
    api<{ lines: ReceivingLine[]; writeoffs: number; notes: string[]; message: string }>(
      "/api/receiving/supplier-return",
      { method: "POST", body: JSON.stringify(payload) },
    ),
};

export type WarehouseTotal = { balance: string; reserved: string; available: string; lots: number; units: string[] };
export type WarehouseTotals = {
  group: string;
  groups: Array<WarehouseTotal & { group: string; articles: number }>;
  articles: Array<WarehouseTotal & { group: string; article: string }>;
};

export const warehouseApi = {
  list: ({ after, query = "", includeZero = false, warehouse = "" }: {
    after?: string;
    query?: string;
    includeZero?: boolean;
    warehouse?: string;
  } = {}) => {
    const params = new URLSearchParams();
    if (after) params.set("after", after);
    if (query.trim()) params.set("q", query.trim());
    if (includeZero) params.set("zero", "true");
    if (warehouse) params.set("warehouse", warehouse);
    const suffix = params.size ? `?${params}` : "";
    return api<{ lots: WarehouseLot[]; total: number; truncated: number; next_cursor: string | null }>(`/api/stock${suffix}`);
  },
  /** Sums per group and per article of a group over ALL lots of the filter (not just the loaded pages). */
  totals: ({ group = "", query = "", includeZero = false, warehouse = "" }: {
    group?: string;
    query?: string;
    includeZero?: boolean;
    warehouse?: string;
  } = {}) => {
    const params = new URLSearchParams();
    if (group) params.set("group", group);
    if (query.trim()) params.set("q", query.trim());
    if (includeZero) params.set("zero", "true");
    if (warehouse) params.set("warehouse", warehouse);
    const suffix = params.size ? `?${params}` : "";
    return api<WarehouseTotals>(`/api/stock/totals${suffix}`);
  },
  shelfPlaces: (warehouse: string, shelf: string) => api<WarehouseShelfPlaces>(
    `/api/stock/places?warehouse=${encodeURIComponent(warehouse)}&shelf=${encodeURIComponent(shelf)}`,
  ),
  articleInfo: (article: string) => api<{ info: WarehouseArticleInfo | null }>(
    `/api/stock/article-info?q=${encodeURIComponent(article)}`,
  ),
  add: (items: WarehouseManualItem[]) => api<WarehouseAddResult>("/api/stock/batches", {
    method: "POST",
    body: JSON.stringify({ items }),
  }),
  writeOff: (lotId: string, version: number, quantity: string, purpose: string, reason: string, date?: string) =>
    api<{ lot: WarehouseLot }>(`/api/stock/batches/${lotId}/writeoff`, {
      method: "POST",
      body: JSON.stringify({ version, quantity, purpose, reason, ...(date ? { date } : {}) }),
    }),
  /** Where the lot was written off to, with what is still there («Откуда» of a return), newest first. */
  issuedTo: (lotId: string) => api<{ destinations: WarehouseDestination[] }>(`/api/stock/batches/${lotId}/issued-to`),
  returnWriteOff: (lotId: string, version: number, quantity: string, source: string, reason: string) =>
    api<{ lot: WarehouseLot }>(`/api/stock/batches/${lotId}/return`, {
      method: "POST",
      body: JSON.stringify({ version, quantity, source, reason }),
    }),
  editIdentity: (lotId: string, version: number, article: string, name: string, series: string) =>
    api<{ lot: WarehouseLot }>(`/api/stock/batches/${lotId}/identity`, {
      method: "PUT",
      body: JSON.stringify({ version, article, name, series }),
    }),
  /** SK-7: the whole stock row by hand. Only the fields sent change; "" clears a text field. */
  editRow: (lotId: string, version: number, changes: WarehouseRowEdit) =>
    api<{ lot: WarehouseLot; changes: Array<{ field: string; column: string; from: string; to: string }>; message: string }>(
      `/api/stock/batches/${lotId}/row`,
      { method: "PUT", body: JSON.stringify({ version, ...changes }) },
    ),
  correctLocation: (lotId: string, version: number, storageLocation: string) =>
    api<{ lot: WarehouseLot }>(`/api/stock/batches/${lotId}/location`, {
      method: "PUT",
      body: JSON.stringify({ version, storage_location: storageLocation }),
    }),
  move: (lotId: string, version: number, storageLocation: string, warehouse = "") =>
    api<{ lot: WarehouseLot }>(`/api/stock/batches/${lotId}/move`, {
      method: "POST",
      body: JSON.stringify({ version, storage_location: storageLocation, warehouse }),
    }),
  moveLocation: (warehouse: string, sourceLocation: string, targetLocation: string) =>
    api<{ lots: WarehouseLot[] }>("/api/stock/locations/move", {
      method: "POST",
      body: JSON.stringify({ warehouse, source_location: sourceLocation, target_location: targetLocation }),
    }),
  itemTypes: () => api<WarehouseItemTypes>("/api/stock/item-types"),
  saveItemTypes: (types: string[]) => api<WarehouseItemTypes & { message: string }>("/api/stock/item-types", {
    method: "PUT",
    body: JSON.stringify({ types }),
  }),
  renameItemType: (source: string, target: string) =>
    api<WarehouseBulkResult & { message: string }>("/api/stock/item-types/rename", {
      method: "POST",
      body: JSON.stringify({ source, target }),
    }),
  exportRows: () => api<{ rows: WarehouseExportRow[] }>("/api/stock/export"),
  /** The administrator only; the search runs on the server. */
  /** «Раскладка» (portalStock05PlacesSave): стеллажи, полок по умолчанию, стеллажи со своим числом полок. */
  rackLayout: () => api<{ layout: WarehouseRackLayout }>("/api/stock/rack-layout"),
  saveRackLayout: (layout: WarehouseRackLayout) =>
    api<{ layout: WarehouseRackLayout; message: string }>("/api/stock/rack-layout", { method: "PUT", body: JSON.stringify(layout) }),
  journal: (query = "") => api<WarehouseJournal>(`/api/stock/journal${query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ""}`),
  /** apply=false is the preview: the same diff, nothing written. The administrator only. */
  importTable: (edits: WarehouseReferenceEdit[], apply: boolean) => api<WarehouseBulkResult>("/api/stock/import", {
    method: "POST",
    body: JSON.stringify({ edits, apply }),
  }),
};

export const fulfillmentApi = {
  // portalIssueList reads the whole sheet; the API pages by 100, so the screens walk every page (real projects give
  // hundreds of requests — the first page alone lost the rest).
  list: async () => {
    type Page = { requests: FulfillmentRequest[]; next_cursor: string | null; places?: FulfillmentPlaces };
    const first = await api<Page>("/api/issues");
    const requests = [...first.requests];
    for (let cursor = first.next_cursor; cursor;) {
      const page: Page = await api<Page>(`/api/issues?after=${encodeURIComponent(cursor)}`);
      requests.push(...page.requests);
      cursor = page.next_cursor;
    }
    return { ...first, requests, next_cursor: null };
  },
  create: (
    payload: { purpose: string; deadline: string; lines: FulfillmentDraftLine[]; short_note?: string[] },
    idempotencyKey: string,
  ) => api<{ request: FulfillmentRequest }>("/api/issues", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  }),
  calcPreview: (url: string, warehouse: string) =>
    api<{ project: string; lines: FulfillmentCalcLine[] }>("/api/issues/calc-preview", {
      method: "POST",
      body: JSON.stringify({ url, warehouse }),
    }),
  complete: (requestId: string, version: number) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/complete`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  close: (requestId: string, version: number) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/close`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  remove: (requestId: string) =>
    api<{ message: string }>(`/api/issues/${requestId}`, { method: "DELETE" }),
  lots: () => api<{ lots: FulfillmentLot[] }>("/api/issues/lots"),
  /** «Возможно есть»: the lots that may be each position of the project, one request for the whole card. */
  candidates: (project: string) =>
    api<{ positions: FulfillmentPositionCandidates[] }>(`/api/issues/projects/${encodeURIComponent(project)}/candidates`),
  /** The same «Возможно есть» for positions the caller names (the project's «Снабжение», before any kit request). */
  candidatesFor: (project: string, positions: Array<{ article: string; name: string; unit?: string }>) =>
    api<{ positions: FulfillmentPositionCandidates[] }>(`/api/issues/projects/${encodeURIComponent(project)}/candidates`, {
      method: "POST", body: JSON.stringify({ positions }),
    }),
  printSheet: (requestId: string) => api<{ sheet: PrintSheet }>(`/api/issues/${requestId}/print`),
  returnSheet: (requestId: string) => api<{ sheet: PrintSheet }>(`/api/issues/${requestId}/return-sheet`),
  bind: (requestId: string, lineId: string, version: number, lotId: string) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/lines/${lineId}/bind`, {
      method: "POST",
      body: JSON.stringify({ version, lot_id: lotId }),
    }),
  unbind: (requestId: string, lineId: string, version: number) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/lines/${lineId}/bind`, {
      method: "DELETE",
      body: JSON.stringify({ version }),
    }),
  /** Either a rack address (the server glues the code, placeCode_) or a free-text place. */
  ready: (requestId: string, version: number, place: FulfillmentPlace | string) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/ready`, {
      method: "POST",
      body: JSON.stringify(typeof place === "string" ? { version, handout_location: place } : { version, place }),
    }),
  /** «Выдать комплект» / «Выдать выбранные»: one transaction for every kit given, all or nothing. A «Укомплектован»
      kit is sent to the handout point on the way (the place the server offers). */
  issueBatch: (requests: Array<{ id: string; version: number }>) =>
    api<{ requests: FulfillmentRequest[]; message: string }>("/api/issues/issue-batch", {
      method: "POST",
      body: JSON.stringify({ requests }),
    }),
  returned: (requestId: string, version: number, returnLocation: string) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/returned`, {
      method: "POST",
      body: JSON.stringify({ version, return_location: returnLocation }),
    }),
  completeReturn: (
    requestId: string,
    version: number,
    lines: Array<{
      line_id: string;
      returned_good: string;
      defective_returned: string;
      defective_missing: string;
      storage_location: string | null;
    }>,
  ) => api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/complete-return`, {
    method: "POST",
    body: JSON.stringify({ version, lines }),
  }),
  cancel: (requestId: string, version: number) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  /** «Довыдать»: заявка «Довыдача» на позиции выданного комплекта — с причиной, ждёт ГРН как новая. */
  extra: (
    requestId: string,
    payload: { reason: string; deadline?: string | null; lines: Array<{ article: string; name: string; unit: string; quantity: string }> },
    idempotencyKey: string,
  ) => api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/extra`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  }),
  /** «Вернуть в комплектацию»: «Готов к выдаче» → «Укомплектован», резервы остаются на партиях. */
  returnToKitting: (requestId: string, version: number) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/return-to-kitting`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
};

export interface AutoplanProposal {
  task_id: string;
  task_version: number;
  task_number: string;
  operation_id: string;
  position: number;
  stage: string;
  code: string;
  operator_id: string;
  operator_name: string;
  start: string;
  finish: string;
  machine: string;
  setup_label: string;
  changeover_seconds: number;
  setup_seconds: number;
  batched: boolean;
  /** Операция будущего задания: КБ с выданной техкартой без задания, задание создаст «Применить». */
  new?: boolean;
}

/** «Весь цех» — вся работа на всех монтажников; «По проекту» — работа проекта на выбранных людей. */
export type AutoplanMode = "shop" | "project";

export interface AutoplanRequest {
  mode: AutoplanMode;
  project?: string;
  people?: string[];
  /** «Весь цех»: исключённые задания (id) и КБ без задания (id техкарты). */
  exclude?: string[];
  /** «До даты» вручную; без неё — план до горизонта работы. */
  date_to?: string | null;
}

export interface AutoplanContext {
  period: { start: string; last_day: string; auto: boolean };
  max_days: number;
  tasks: Array<{ id: string; number: string; project: string; item: string; name: string; deadline: string | null }>;
  new: Array<{ techcard_id: string; project: string; index: string; name: string; run: string; deadline: string | null; operations: number; skipped: string }>;
  projects: Array<{ project: string; deadline: string | null; tasks: number; new: number }>;
  fitters: Array<{ id: string; name: string; site: string; free_seconds: number }>;
}

export interface AutoplanPreview {
  period: { start: string; last_day: string };
  scope: { tasks: number; new: number; fitters: number };
  new_tasks: Array<{ task_id: string; techcard_id: string; project: string; index: string; name: string; quantity: number; deadline: string | null }>;
  proposals: AutoplanProposal[];
  batches: Array<{ operator_id: string; operator_name: string; batches: Array<{ label: string; machine: string; operations: number; tasks: string[]; start: string; finish: string; changeover_seconds: number }> }>;
  unplaced: Array<{ operation_id: string | null; task_id: string | null; techcard_id?: string; task_number: string; position: number | null; reason: string }>;
  warnings: string[];
  stats: { changeovers: number; changeovers_naive: number; saved_seconds: number };
  fingerprint: string;
}

export const productionApi = {
  autoplanContext: (payload: AutoplanRequest) => api<AutoplanContext>("/api/production/autoplan/context", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  autoplanPreview: (payload: AutoplanRequest) => api<AutoplanPreview>("/api/production/autoplan/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  autoplanApply: (payload: AutoplanRequest & { items: Array<{ task_id: string; operation_id: string; operator_id: string }>; fingerprint: string }) =>
    api<{ applied: number; tasks: Array<{ id: string; number: string; version: number }>; created: Array<{ id: string; number: string }> }>("/api/production/autoplan/apply", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  list: () => api<{ tasks: ProductionTask[]; next_cursor: string | null }>("/api/production/tasks"),
  /** can_act — смотрящий может работать за выбранного сотрудника (сервер проверяет то же при каждом действии). */
  mine: (operatorId = "") => api<{ tasks: ProductionTask[]; can_act?: boolean }>(`/api/production/my${operatorId ? `?operator_id=${operatorId}` : ""}`),
  operators: () => api<{ operators: ProductionOperator[]; viewer_site?: string }>("/api/production/operators"),
  calendar: () => api<{ holidays: string[]; absences: ProductionAbsence[]; shift: ProductionShift }>("/api/production/calendar"),
  saveCalendar: (holidays: string[], absences: ProductionAbsence[], shift: ProductionShift) => api<void>("/api/production/calendar", {
    method: "PUT",
    body: JSON.stringify({ holidays, shift, absences: absences.filter((item) => !item.leave_request_id).map(({ operator_id, from_date, to_date, kind }) => ({ operator_id, from_date, to_date, kind })) }),
  }),
  skills: () => api<{ skills: ProductionSkill[]; operation_codes?: Array<{ code: string; name: string }> }>("/api/production/skills"),
  launchable: () => api<{ items: ProductionLaunchable[] }>("/api/production/launchable"),
  saveSkills: (operatorId: string, skills: Omit<ProductionSkill, "operator_id">[]) => api<void>(`/api/production/skills/${operatorId}`, {
    method: "PUT", body: JSON.stringify({ skills }),
  }),
  launch: (idempotencyKey: string, payload: {
    techcard_id: string;
    quantity: number;
    deadline: string | null;
    fulfillment_request_id: string | null;
  }) => api<{ task: ProductionTask }>("/api/production/tasks", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  }),
  assign: (taskId: string, operationId: string, version: number, operatorIds: string[], requestedStart: string | null, dry = false) =>
    api<{ task: ProductionTask; warnings: string[]; plan: ProductionAssignPlan; dry: boolean }>(`/api/production/tasks/${taskId}/operations/${operationId}/assign`, {
      method: "POST",
      body: JSON.stringify({ version, operator_ids: operatorIds, requested_start: requestedStart, dry }),
    }),
  // Экран мастера: доля операции монтажнику (штуки, с какого момента) или снять её.
  share: (taskId: string, operationId: string, version: number, operatorId: string, quantity: number | null, requestedStart: string | null, remove = false) =>
    api<{ task: ProductionTask; warnings: string[]; plan: { start: string | null; finish: string | null; warnings: string[] } }>(`/api/production/tasks/${taskId}/operations/${operationId}/share`, {
      method: "POST",
      body: JSON.stringify(remove
        ? { version, operator_id: operatorId, remove: true }
        : { version, operator_id: operatorId, quantity, requested_start: requestedStart }),
    }),
  cancel: (taskId: string, version: number, cancelled: boolean) =>
    api<{ task: ProductionTask }>(`/api/production/tasks/${taskId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ version, cancelled }),
      }),
  handOver: (taskId: string, version: number, site: string) =>
    api<{ task: ProductionTask }>(`/api/production/tasks/${taskId}/handover`, {
      method: "POST",
      body: JSON.stringify({ version, site }),
    }),
  /** «План задания»: расчёт по этапам в датах смены, сдачи площадки, свежесть норм ЭТК. */
  flow: (taskId: string) => api<ProductionFlow>(`/api/production/tasks/${taskId}/flow`),
  updateFlow: (taskId: string, version: number, settings: { flow_mode: ProductionFlowMode; transfer_batch: number | null; early_quantity: number | null; team: string[] }) =>
    api<{ task: ProductionTask }>(`/api/production/tasks/${taskId}/flow`, {
      method: "PUT", body: JSON.stringify({ version, ...settings }),
    }),
  refreshNorms: (taskId: string, version: number) =>
    api<{ task: ProductionTask }>(`/api/production/tasks/${taskId}/norms/refresh`, {
      method: "POST", body: JSON.stringify({ version }),
    }),
  /** «Сдано N шт»; quantity null — весь остаток. */
  deliver: (taskId: string, version: number, quantity: number | null) =>
    api<{ task: ProductionTask; delivery: ProductionDelivery }>(`/api/production/tasks/${taskId}/deliveries`, {
      method: "POST", body: JSON.stringify({ version, quantity }),
    }),
  cancelDelivery: (taskId: string, deliveryId: string, version: number) =>
    api<{ task: ProductionTask }>(`/api/production/tasks/${taskId}/deliveries/${deliveryId}/cancel`, {
      method: "POST", body: JSON.stringify({ version }),
    }),
  /** «Выполнено» (done) / «Вернуть в работу». */
  setDone: (taskId: string, version: number, done: boolean) =>
    api<{ task: ProductionTask }>(`/api/production/tasks/${taskId}/status`, {
      method: "POST", body: JSON.stringify({ version, done }),
    }),
  schedule:(taskId: string, version: number, replan: boolean, maxCrew: number) =>
    api<{ variants: ProductionScheduleVariant[] }>(`/api/production/tasks/${taskId}/schedule`, {
      method: "POST", body: JSON.stringify({ version, replan, max_crew: maxCrew }),
    }),
  applySchedule: (taskId: string, version: number, replan: boolean, maxCrew: number, transferBatch: number) =>
    api<{ task: ProductionTask; variant: ProductionScheduleVariant }>(`/api/production/tasks/${taskId}/schedule/apply`, {
      method: "POST", body: JSON.stringify({ version, replan, max_crew: maxCrew, transfer_batch: transferBatch }),
    }),
  execution: (operatorId = "") => api<{ session: ProductionWorkSession | null; boxes: ProductionBox[] }>(`/api/production/my/execution${operatorId ? `?operator_id=${operatorId}` : ""}`),
  /** ЭТК операции для монтажника: выданная карта ПЗ и операция, на которой её открыть (не требует раздела техкарт). */
  operationEtkSketches: (taskId: string, operationId: string) =>
    api<{ items: OperationSketch[] }>(`/api/production/my/tasks/${taskId}/operations/${operationId}/etk/sketches`),
  operationEtkSketchUrl: (taskId: string, operationId: string, operationKey: string, at = "") =>
    `/api/production/my/tasks/${taskId}/operations/${operationId}/etk/sketches/${encodeURIComponent(operationKey)}${at ? `?v=${encodeURIComponent(at)}` : ""}`,
  /** Сохранённый чертёж жгута позиции задания — только чтение, без раздела «Проекты» (нет чертежа — `design: null`). */
  taskHarness: (taskId: string) => api<HarnessDrawing>(`/api/production/my/tasks/${taskId}/harness`),
  operationEtk: (taskId: string, operationId: string) =>
    api<{ card: ProjectTechcard; focus: { position: number; code: string } }>(`/api/production/my/tasks/${taskId}/operations/${operationId}/etk`),
  inputs: (taskId: string, operationId: string, operatorId = "") =>
    api<{ boxes: ProductionBox[]; materials: ProductionMaterialSource[] }>(`/api/production/tasks/${taskId}/operations/${operationId}/inputs${operatorId ? `?operator_id=${operatorId}` : ""}`),
  boxes: (taskId?: string) => api<{ boxes: ProductionBox[] }>(`/api/production/boxes${taskId ? `?task_id=${taskId}` : ""}`),
  // «Задания»: work.onBehalfOf — работа за выбранного сотрудника (право проверяет сервер), work.key — ключ повтора:
  // тот же ключ второй раз не делает действие дважды (двойной клик, повтор после обрыва связи).
  startWork: (taskId: string, operationId: string, inputs: string[], materials: Array<{ fulfillment_line_id: string; quantity: string }> = [], work: WorkCall = {}) =>
    api<{ session: ProductionWorkSession }>(`/api/production/tasks/${taskId}/operations/${operationId}/work/start${behalfQuery(work.onBehalfOf)}`, {
      method: "POST", headers: replayHeaders(work.key), body: JSON.stringify({ inputs, materials }),
    }),
  workAction: (sessionId: string, action: "pause" | "resume" | "cancel", work: WorkCall = {}) =>
    api<{ session: ProductionWorkSession }>(`/api/production/work/${sessionId}/${action}${behalfQuery(work.onBehalfOf)}`, { method: "POST", headers: replayHeaders(work.key) }),
  submitWork: (sessionId: string, goodQuantity: string, defectQuantity: string, defectCharacter = "", defectDescription = "", work: WorkCall = {}) =>
    api<{ session: ProductionWorkSession; box: ProductionBox; output_id: string; defect: ProductionDefect | null }>(`/api/production/work/${sessionId}/submit${behalfQuery(work.onBehalfOf)}`, {
      method: "POST", headers: replayHeaders(work.key), body: JSON.stringify({ good_quantity: goodQuantity, defect_quantity: defectQuantity, defect_character: defectCharacter, defect_description: defectDescription }),
    }),
  boxOutputs: (boxId: string) => api<{ outputs: ProductionOutput[] }>(`/api/production/boxes/${boxId}/outputs`),
  correctBox: (boxId: string, version: number, input: CorrectBoxInput, onBehalfOf = "") =>
    api<{ box: ProductionBox; output: ProductionOutput; defect: ProductionDefect | null }>(`/api/production/boxes/${boxId}/correct${behalfQuery(onBehalfOf)}`, {
      method: "POST", body: JSON.stringify({ version, ...input }),
    }),
  returnBox: (boxId: string, version: number, reason: string) =>
    api<{ box: ProductionBox }>(`/api/production/boxes/${boxId}/return`, {
      method: "POST", body: JSON.stringify({ version, reason }),
    }),
  finishedGoodsReady: () => api<{ boxes: FinishedGoodsReadyBox[] }>("/api/production/finished-goods-ready"),
  handoff: (boxId: string, version: number, finishedGoodId: string) =>
    api<{ box: ProductionBox; finished_good: { id: string; version: number; grn: string; accepted: string; remaining: string }; receipt_id: string }>(`/api/production/boxes/${boxId}/handoff`, {
      method: "POST", body: JSON.stringify({ version, finished_good_id: finishedGoodId }),
    }),
  defects: () => api<{ defects: ProductionDefect[] }>("/api/production/defects"),
  decideDefect: (defectId: string, version: number, disposition: "Доработка" | "Списание" | "Использовать как есть", actReference: string) =>
    api<{ defect: ProductionDefect; decision_id: string }>(`/api/production/defects/${defectId}/decide`, {
      method: "POST", body: JSON.stringify({ version, disposition, act_reference: actReference }),
    }),
  instruments: () => api<{ instruments: ProductionInstrument[] }>("/api/production/instruments"),
  qcRejections: () => api<{ rejections: ProductionQcRejection[] }>("/api/production/qc/rejections"),
  decideQcRejection: (inspectionId: string, disposition: "Доработка" | "Списание" | "Использовать как есть", actReference: string) =>
    api(`/api/production/qc/rejections/${inspectionId}/decide`, {
      method: "POST", body: JSON.stringify({ disposition, act_reference: actReference }),
    }),
  registerInstrument: (payload: Omit<ProductionInstrument, "id" | "version" | "status">) =>
    api<{ instrument: ProductionInstrument }>("/api/production/instruments", {
      method: "POST", body: JSON.stringify(payload),
    }),
  inspectBox: (boxId: string, payload: { version: number; accepted: number; rejected: number; defect_character: string; defect_description: string; instrument_ids: string[] }) =>
    api<{ box: ProductionBox; inspection_id: string; defect_number: string | null; serials: Array<{ serial_number: string; status: string }> }>(`/api/production/boxes/${boxId}/qc`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  serials: (taskId: string) => api<{ serials: ProductionSerial[] }>(`/api/production/tasks/${taskId}/serials`),
  traceability: (query: string) => api<GenealogyResult>(`/api/production/traceability?q=${encodeURIComponent(query)}`),
};

export const productionPlanApi = {
  load: () => api<ProductionPlanResult>("/api/production/plan"),
  stagePreview: (project: string, value: string, kb = "") =>
    api<StagePreview>(`/api/production/plan/stage-preview?project=${encodeURIComponent(project)}&value=${encodeURIComponent(value)}&kb=${encodeURIComponent(kb)}`),
  addEvent: (payload: {
    project: string;
    kind: "note" | "block" | "unblock" | "deadline" | "stage" | "tcReview" | "mark" | "status";
    blocker?: string;
    text?: string;
    deadline?: string | null;
    item_index?: string;
    value?: string;
    field?: string;
    reason?: string;
    preview_token?: string;
  }) => api<{ event: ProductionPlanEvent }>("/api/production/plan/events", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
};

export const shippingApi = {
  goods: () => api<{ goods: FinishedGood[] }>("/api/shipping/goods"),
  shipments: () => api<{ shipments: Shipment[] }>("/api/shipping/shipments"),
  registerGood: (payload: Record<string, unknown>) => api<{ good: FinishedGood }>("/api/shipping/goods", {
    method: "POST", body: JSON.stringify(payload),
  }),
  accept: (goodId: string, version: number, quantity: string, receivedAt?: string) => api<{ good: FinishedGood }>(`/api/shipping/goods/${goodId}/accept`, {
    method: "POST", body: JSON.stringify(receivedAt ? { version, quantity, received_at: receivedAt } : { version, quantity }),
  }),
  create: (payload: Record<string, unknown>) => api<{ shipment: Shipment }>("/api/shipping/shipments", {
    method: "POST", body: JSON.stringify(payload),
  }),
  action: (shipmentId: string, action: "pick" | "pack" | "ship" | "close" | "cancel", version: number) =>
    api<{ shipment: Shipment; closed?: boolean; why?: string }>(`/api/shipping/shipments/${shipmentId}/${action}`, {
      method: "POST", body: JSON.stringify({ version }),
    }),
  unpick: (shipmentId: string, version: number, lineId: string) => api<{ shipment: Shipment }>(`/api/shipping/shipments/${shipmentId}/unpick`, {
    method: "POST", body: JSON.stringify({ version, line_id: lineId }),
  }),
  documents: (shipmentId: string, payload: Record<string, unknown>) => api<{ shipment: Shipment }>(`/api/shipping/shipments/${shipmentId}/documents`, {
    method: "PUT", body: JSON.stringify(payload),
  }),
  /** `shipSetHead_`: шапка отправки; приходят только правленые поля, пустое поле адресата — из карточки проекта. */
  head: (shipmentId: string, payload: Record<string, unknown>) => api<{ shipment: Shipment }>(`/api/shipping/shipments/${shipmentId}/head`, {
    method: "PUT", body: JSON.stringify(payload),
  }),
  /** SH-4 `portalShipItemQty`: «Заказано» после создания, не ниже набранного. */
  setRequested: (shipmentId: string, version: number, lineId: string, quantity: string) => api<{ shipment: Shipment }>(`/api/shipping/shipments/${shipmentId}/lines/${lineId}`, {
    method: "PUT", body: JSON.stringify({ version, quantity }),
  }),
  /** `portalShipDocs`: короба и примечания для ТК; первый раз — ещё и «Упакована» с номерами PL/DN. */
  boxes: (shipmentId: string, payload: Record<string, unknown>) => api<{ shipment: Shipment }>(`/api/shipping/shipments/${shipmentId}/boxes`, {
    method: "PUT", body: JSON.stringify(payload),
  }),
  print: (shipmentId: string, document: "pl" | "dn") => api<{ forms: ShippingForm[] }>(`/api/shipping/shipments/${shipmentId}/print?document=${document}`),
  addressees: () => api<{ addressees: ProjectAddressee[] }>("/api/shipping/project-addressees"),
  /** `portalShipUpdFile`: файл УПД к отправке; ссылка на него встаёт в отправку. */
  uploadUpd: (shipmentId: string, version: number, file: { name: string; type: string; base64: string }) => api<{ shipment: Shipment }>("/api/shipping/upd-files", {
    method: "POST", body: JSON.stringify({ shipment_id: shipmentId, version, file_name: file.name, content_type: file.type, content_base64: file.base64 }),
  }),
  saveAddressee: (payload: Record<string, unknown>) => api<{ addressee: ProjectAddressee }>("/api/shipping/project-addressee", {
    method: "PUT", body: JSON.stringify(payload),
  }),
  /** `portalShipPack`: «Упаковать» отмеченное в карточке проекта. */
  projectPack: (project: string, lines: Array<{ good_id: string; quantity: string }>) => api<{ shipment: Shipment }>("/api/shipping/project-pack", {
    method: "POST", body: JSON.stringify({ project, lines }),
  }),
  /** «Отгрузить» / «Упаковать, отгрузить позже» карточки проекта: карточка, отправка, УПД и отгрузка — одна транзакция.
   * Тот же ключ с тем же телом возвращает уже сделанное, с другим — 409. */
  projectShip: (payload: {
    project: string;
    addressee_version: number;
    lines: Array<{ good_id: string; version: number; quantity: string }>;
    addressee?: Record<string, string | null>;
    documents?: { upd_number: string; upd_date: string | null; deadline: string | null };
    upd_file?: { file_name: string; content_type: string; content_base64: string };
    ship: boolean;
  }, idempotencyKey: string) => api<{ shipment: Shipment; shipped: boolean; closed: boolean; why: string; replayed: boolean }>("/api/shipping/project-ship", {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload),
  }),
  /** «Из производства»: что цех сдал, а склад ещё не принял, и что ещё в производстве. */
  fromProduction: () => api<{ rows: FromProductionRow[] }>("/api/shipping/finished-goods/from-production"),
  /** Приёмка пакетом: все отмеченные КБ или ни одной; не больше сданного цехом. Тот же ключ с тем же телом — уже сделанное. */
  acceptFromProduction: (lines: Array<{ project: string; item_index: string; quantity: string }>, receivedAt: string | null, idempotencyKey: string) =>
    api<{ goods: FinishedGood[]; replayed: boolean }>("/api/shipping/finished-goods/accept", {
      method: "POST", headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(receivedAt ? { lines, received_at: receivedAt } : { lines }),
    }),
  /** Сквозной статус проекта по КБ и журнал ходов. */
  projectGoods: (project: string) => api<ProjectGoodsStatus>(`/api/shipping/finished-goods/project?project=${encodeURIComponent(project)}`),
  /** Откат «Отгружено» — только администратор, с причиной; штуки снова «Упаковано». */
  unship: (shipmentId: string, version: number, reason: string) => api<{ shipment: Shipment }>(`/api/shipping/shipments/${shipmentId}/unship`, {
    method: "POST", body: JSON.stringify({ version, reason }),
  }),
};

export const referencesApi = {
  bases: () => api<BasesResult>("/api/references/bases"),
  cables: () => api<CablesResult>("/api/references/cables"),
  coverings: () => api<CoveringsResult>("/api/references/coverings"),
  setCoveringGroup: (recordId: string, group: string) =>
    api<TechOperationRecord>(`/api/references/coverings/${recordId}/group`, { method: "PUT", body: JSON.stringify({ group }) }),
  moveCable: (recordId: string, kind: CableKind) =>
    api<CableRow>(`/api/references/cables/${recordId}/kind`, { method: "PUT", body: JSON.stringify({ kind }) }),
  saveCableDiameters: (payload: { cable: string; dia_id: string | null; old: Record<string, string>; value: Record<string, string> }) =>
    api<CableDiameters>("/api/references/cables/diameters", { method: "PUT", body: JSON.stringify(payload) }),
  saveHookup: (recordId: string, payload: HookupPayload & { old: HookupPayload | null }) =>
    api<{ hookup: HookupWire; record: TechOperationRecord }>(`/api/references/cables/${recordId}/hookup`, { method: "PUT", body: JSON.stringify(payload) }),
  saveGauge: (gaugeId: string | null, payload: { awg: string; section_mm2: string; conductor_d_mm: string; strands: string }) =>
    api<WireGauge>(gaugeId ? `/api/references/wire-gauges/${gaugeId}` : "/api/references/wire-gauges", { method: gaugeId ? "PUT" : "POST", body: JSON.stringify(payload) }),
  deleteGauge: (gaugeId: string) => api<void>(`/api/references/wire-gauges/${gaugeId}`, { method: "DELETE" }),
  maps: () => api<ReferenceMaps>("/api/references/maps"),
  saveMaps: (maps: ReferenceMaps) => api<ReferenceMaps>("/api/references/maps", {
    method: "PUT",
    body: JSON.stringify(maps),
  }),
  techOperations: (tab: TechOperationTab, query = "", limit?: number) => {
    const params = new URLSearchParams({ tab });
    if (query.trim()) params.set("q", query.trim());
    if (limit) params.set("limit", String(limit));
    return api<TechOperationResult>(`/api/references/tech-operations?${params}`);
  },
  wireDia: () => api<WireDiaResult>("/api/references/wire-dia"),
  /** СПР.КАБ · коаксиал для генератора ЭТК: строки D1·D2·D3 и по строке на каждое другое имя кабеля (полное название,
   *  артикул поставщика, другое написание марки) с D1–D3 этого кабеля. */
  coaxDia: () => api<TechOperationResult>("/api/references/coax-dia"),
  editRecord: (id: string, edits: Array<{ field: string; old: string; value: string }>) =>
    api<TechOperationRecord>(`/api/references/tech-operations/${id}`, { method: "PATCH", body: JSON.stringify({ edits }) }),
  createRecord: (tab: TechOperationTab, fields: Record<string, string>) =>
    api<TechOperationRecord>("/api/references/tech-operations", { method: "POST", body: JSON.stringify({ tab, fields }) }),
  equipmentNumber: (type: string) =>
    api<{ inventory: string; row: string }>(`/api/references/tech-operations/equipment-number?${new URLSearchParams({ type })}`),
  duplicateRecord: (id: string) =>
    api<TechOperationRecord>(`/api/references/tech-operations/${id}/duplicate`, { method: "POST" }),
  crimps: (id: string) => api<CrimpOverview>(`/api/references/tech-operations/${id}/crimps`),
  crimpCodes: () => api<CrimpCodes>("/api/references/tech-operations/crimp-codes"),
  crimpTerminal: (toolId: string, terminalId: string, add: boolean) =>
    api<CrimpTerminal>(`/api/references/tech-operations/${toolId}/crimps/terminals/${terminalId}`, { method: add ? "POST" : "DELETE" }),
  crimpTarget: (toolId: string, target: CrimpTarget, add: boolean) =>
    api<{ targets: CrimpTarget[] }>(`/api/references/tech-operations/${toolId}/crimps/targets${add ? "" : "/remove"}`, {
      method: "POST", body: JSON.stringify(target),
    }),
  resolveTerminal: (payload: { article: string; wires: string[]; folded?: boolean; gost_awg?: Record<string, number> }) =>
    api<TerminalResolution>("/api/references/technology/resolve-terminal", { method: "POST", body: JSON.stringify(payload) }),
};

export interface CrimpTerminal {
  id: string; display_text: string; article: string; series: string; maker: string; kind: string;
  applicator: string; doubtful: boolean;
}
export interface CrimpTarget { kind: string; key: string; label: string }
export interface CrimpOverview { code: string; terminals: CrimpTerminal[]; targets: CrimpTarget[] }
export interface CrimpCodes { owners: Record<string, string>; missing: Array<{ code: string; count: number }> }

export interface TerminalResolution {
  applicator: string;
  crimp_height: string;
  pull_force: string;
  by_section: Array<{ section: string; crimp_height: string; pull_force: string; applicator: string; range_only: boolean; found: boolean }>;
}

export const techcardsApi = {
  list: () => api<{ items: ProjectTechcard[] }>("/api/techcards/projects"),
  /** Одна карточка ТК по id — генератор и вкладка «ТК КБn» не тянут весь список со всеми ЭТК. */
  get: (cardId: string) => api<ProjectTechcard>(`/api/techcards/projects/${encodeURIComponent(cardId)}`),
  templates: () => api<{
    metadata: null | { source_commit: string; imported_at: string; template_count: number };
    items: OperationCardSummary[];
  }>("/api/techcards/templates"),
  template: (templateId: string) =>
    api<OperationCardDetail>(`/api/techcards/templates/${encodeURIComponent(templateId)}`),
  saveTemplate: (templateId: string, record: OperationRecordEdit) =>
    api<OperationCardDetail>(`/api/techcards/templates/${encodeURIComponent(templateId)}`, {
      method: "PUT",
      body: JSON.stringify(record),
    }),
  templateSheets: () => api<{ list: TemplateSheet[]; drafts: TemplateSheetDraft[] }>("/api/techcards/template-sheets"),
  openTemplateDraft: (templateId: string, title: string) =>
    api<TemplateSheetDraft>("/api/techcards/template-sheets/drafts", {
      method: "POST", body: JSON.stringify({ template_id: templateId, title }),
    }),
  templateDraft: (draftId: string) => api<TemplateSheetDraft>(`/api/techcards/template-sheets/drafts/${draftId}`),
  editTemplateDraft: (draftId: string, rows: string[][]) =>
    api<TemplateSheetDraft>(`/api/techcards/template-sheets/drafts/${draftId}`, {
      method: "PUT", body: JSON.stringify({ rows }),
    }),
  saveTemplateDraft: (draftId: string, form: { title: string; category: string; description: string }) =>
    api<TemplateSheetSaveResult>(`/api/techcards/template-sheets/drafts/${draftId}/save`, {
      method: "POST", body: JSON.stringify(form),
    }),
  dropTemplateDraft: (draftId: string) =>
    api<{ dropped: boolean }>(`/api/techcards/template-sheets/drafts/${draftId}`, { method: "DELETE" }),
  deleteTemplateSheet: (templateId: string) =>
    api<{ deleted: boolean; id: string; title: string }>(`/api/techcards/template-sheets/${encodeURIComponent(templateId)}`, { method: "DELETE" }),
  editOperation: (cardId: string, version: number, operationKey: string, operation: Record<string, unknown>) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/operations/${encodeURIComponent(operationKey)}`, {
      method: "PUT",
      body: JSON.stringify({ version, operation }),
    }),
  /** «Сохранить ПФ»: операции ЭТК по полуфабрикатам (блоки схемы, порядок операций в блоке — порядок работы). */
  saveRoute: (cardId: string, version: number, route: Record<string, unknown>) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/route`, {
      method: "PUT",
      body: JSON.stringify({ version, route }),
    }),
  importGrapher: (filename: string, contentBase64: string, components: Array<{ id: string; article: string; name: string }> = [],
    sheet: GrapherSheetRow[] = []) =>
    api<GrapherImportResult>("/api/techcards/grapher/import", {
      method: "POST", body: JSON.stringify({ filename, content_base64: contentBase64, components, sheet }),
    }),
  create: (payload: {
    project: string;
    item_index: string;
    product_name: string;
    source_calculation: string;
    deadline: string | null;
  }) => api<ProjectTechcard>("/api/techcards/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  rebuild: (cardId: string, version: number, document: Record<string, unknown>) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/etk`, {
      method: "PUT",
      body: JSON.stringify({ version, document }),
    }),
  /** generatorOrigin — типовая сборка, из которой «Поправить вручную» сделало ручную (`generator_origin`, та же форма). */
  autoBuild: (cardId: string, version: number, config: Record<string, unknown>, draftId?: string, generatorOrigin?: Record<string, unknown>) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/build`, {
      method: "POST",
      body: JSON.stringify({ version, config, ...(draftId ? { draft_id: draftId } : {}), ...(generatorOrigin ? { generator_origin: generatorOrigin } : {}) }),
    }),
  issue: (cardId: string, version: number) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/issue`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  revisions: (cardId: string) =>
    api<{ items: TechcardRevision[] }>(`/api/techcards/projects/${cardId}/revisions`),
  /** Свои картинки эскиза к операциям ЭТК (одна на операцию), байты — в базе ERP. */
  sketches: (cardId: string) => api<{ items: OperationSketch[] }>(`/api/techcards/projects/${cardId}/sketches`),
  sketchUrl: (cardId: string, operationKey: string, at = "") =>
    `/api/techcards/projects/${cardId}/sketches/${encodeURIComponent(operationKey)}${at ? `?v=${encodeURIComponent(at)}` : ""}`,
  putSketch: (cardId: string, operationKey: string, file: { file_name: string; content_type: string; content_base64: string }) =>
    api<OperationSketch>(`/api/techcards/projects/${cardId}/sketches/${encodeURIComponent(operationKey)}`, {
      method: "PUT", body: JSON.stringify(file),
    }),
  deleteSketch: (cardId: string, operationKey: string) =>
    api<{ deleted: number }>(`/api/techcards/projects/${cardId}/sketches/${encodeURIComponent(operationKey)}`, { method: "DELETE" }),
  /** База разъёмов: семейства корпусов с образцом артикула и раскладкой контактов. */
  connectorFamilies: (article = "") => api<{ items: ConnectorFamily[]; match: ConnectorFamily["housing"] | null; series_count?: number }>(
    `/api/techcards/connector-families${article ? `?article=${encodeURIComponent(article)}` : ""}`),
  connectorSeries: (article = "") => api<{ series: ConnectorSeries[]; match: ConnectorSeriesMatch | null }>(
    `/api/techcards/connector-series${article ? `?article=${encodeURIComponent(article)}` : ""}`),
  discardDraft: (cardId: string, version: number) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/etk`, {
      method: "DELETE",
      body: JSON.stringify({ version }),
    }),
};

export const operationsApi = {
  notifications: () => api<{ notifications: NotificationItem[]; unread: number }>("/api/notifications"),
  unread: () => api<{ unread: number }>("/api/notifications/unread"),
  markRead: (ids: string[] = []) => api<{ unread: number }>("/api/notifications/read", {
    method: "POST",
    body: JSON.stringify({ ids }),
  }),
  /** Удалить одно своё уведомление; в ответе — сколько непрочитанных осталось (для колокольчика). */
  deleteNotification: (id: string) =>
    api<{ unread: number }>(`/api/notifications/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** «Удалить все» / «Удалить прочитанные» (scope) или выбранные строки (ids); чужие не затрагиваются. */
  deleteNotifications: (selector: { scope: "all" | "read" } | { ids: string[] }) => {
    const params = new URLSearchParams();
    if ("scope" in selector) params.set("scope", selector.scope);
    else selector.ids.forEach((id) => params.append("id", id));
    return api<{ deleted: number; unread: number }>(`/api/notifications?${params}`, { method: "DELETE" });
  },
  audit: (eventType = "") => {
    const params = new URLSearchParams();
    if (eventType.trim()) params.set("event_type", eventType.trim());
    return api<{ events: AuditItem[] }>(`/api/operations/audit${params.size ? `?${params}` : ""}`);
  },
  outbox: () => api<OutboxHealth>("/api/operations/outbox"),
  /** Вернуть остановленное задание в очередь как есть (журналируется; повторный вызов ничего не меняет). */
  retryOutbox: (id: string) =>
    api<{ id: string; requeued: boolean }>(`/api/operations/outbox/${encodeURIComponent(id)}/retry`, { method: "POST" }),
};

/** Задача модулю по проекту: её ставит арбитр ERP по событию модуля (ARCH-TRACK-3). */
export type ProjectTaskStatus = "open" | "done" | "cancelled";
export type ProjectTaskItem = {
  id: string;
  /** Номер проекта в каноническом виде («708К.4»). */
  project: string;
  /** КБ («КБ1»); пусто — задача на весь проект. */
  item_index: string;
  /** Раздел, которому задача: ключ доступа (`production`, `shipping`, …) и его название («Производство»). */
  module: string;
  module_label: string;
  /** Вид задачи: `plan_item`, `launch_task`, `ship_goods`. */
  kind: string;
  /** Текст по-русски: «Поставить в план: 708К.4 КБ1». */
  title: string;
  status: ProjectTaskStatus;
  /** «Открыта» / «Закрыта» / «Отменена». */
  status_label: string;
  created_at: string;
  closed_at: string | null;
  /** Кто закрыл: сотрудник или «Арбитр ERP». */
  closed_by: string;
  /** Тип события журнала, из которого задача родилась. */
  source_type: string;
  /** Может ли текущий сотрудник закрыть её руками. */
  can_close: boolean;
};

export const projectTasksApi = {
  list: (filters: { project?: string; module?: string; status?: ProjectTaskStatus | "" } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value?.trim()) params.set(key, value.trim());
    return api<{ tasks: ProjectTaskItem[] }>(`/api/project-tasks${params.size ? `?${params}` : ""}`);
  },
  close: (id: string) =>
    api<{ task: ProjectTaskItem }>(`/api/project-tasks/${encodeURIComponent(id)}/close`, { method: "POST" }),
};

/**
 * «Личная карточка» сотрудника (концепт владельца, 04.10): сводка по человеку из всех модулей, только чтение.
 * Нет данных — `null` или пустой список, не выдуманный ноль; модуль не подключён на сервере — раздел пустой
 * (`count`/`hours`/`sessions` = null).
 */
export type EmployeeCardSite = "ЦАР" | "ЖУК" | "АДМ";
export type EmployeeCardHeader = {
  id: string;
  /** ФИО. */
  name: string;
  department: string | null;
  /** Роли с русскими названиями: `{ key: "master", label: "Мастер" }`. */
  roles: Array<{ key: string; label: string }>;
  /** Площадка и её город («ЦАР» → «Москва»); null — человек видит все площадки. */
  site: EmployeeCardSite | null;
  site_label: string | null;
  active: boolean;
};
export type EmployeeCardSubordinate = { id: string; name: string; site: string | null; department: string | null };
/** Проект человека: «Исполнитель» (операции задания) и/или «Инициатор заявок»; срок — из снимка плана. */
export type EmployeeCardProject = {
  /** Номер проекта в каноническом виде («708К.4»). */
  project: string;
  roles: Array<"Исполнитель" | "Инициатор заявок">;
  /** ISO-дата; null — в снимке плана срока нет. */
  deadline: string | null;
};
export type EmployeeCardSkill = {
  /** Код операции из матрицы компетенций. */
  operation: string;
  /** Название операции из выданных техкарт или запущенных операций; null — названия нет. */
  name?: string | null;
  allowed: boolean;
  stars: number;
  /** Десятичная строка: "1.25". */
  coefficient: string;
};
export type EmployeeCardAbsence = { kind: string; from_date: string; to_date: string };
export type EmployeeCardRequest = { id: string; number: string; name: string; status: string };
export type EmployeeCardTask = {
  id: string;
  project: string;
  item_index: string | null;
  module: string;
  module_label: string;
  kind: string;
  title: string;
  created_at: string;
};
/** Кадровые данные: приходят, когда справочник сотрудников их хранит; пока поля нет в ответе — раздел их не показывает. */
export type EmployeeCardPersonnel = {
  tab_number: string | null;
  position: string | null;
  category: string | null;
  /** ISO-дата «в должности с». */
  since: string | null;
  manager_name: string | null;
};
export type EmployeeCard = {
  /** Дата, на которую собрана карточка (Москва). */
  as_of: string;
  header: EmployeeCardHeader;
  personnel?: EmployeeCardPersonnel | null;
  /** Для мастера — монтажники его площадки, иначе пусто. */
  subordinates: EmployeeCardSubordinate[];
  projects: EmployeeCardProject[];
  skills: EmployeeCardSkill[];
  /** Текущие и будущие отсутствия по возрастанию даты; `next_vacation` — ближайший «Отпуск». */
  absences: { upcoming: EmployeeCardAbsence[]; next_vacation: EmployeeCardAbsence | null };
  /** Отработано по закрытым рабочим сессиям за текущий месяц (Москва); hours null — сессий нет. */
  hours_this_month: { month: string; label: string; hours: number | null; sessions: number | null };
  /** Свои открытые заявки: всего и первые 10, новые сверху. */
  requests: { count: number | null; items: EmployeeCardRequest[] };
  /** Открытые задачи арбитра в разделах, которые человек правит: всего и первые 10, старые сверху. */
  tasks: { count: number | null; items: EmployeeCardTask[] };
};

export const employeeCardApi = {
  /** Своя карточка — открыта любому вошедшему. */
  me: () => api<EmployeeCard>("/api/employee-card/me"),
  /** Чужая — только с доступом к разделу «Сотрудники» (403 без него, 404 — нет такого сотрудника). */
  byId: (id: string) => api<EmployeeCard>(`/api/employee-card/${encodeURIComponent(id)}`),
  /** «Рабочее время»: месяц ГГГГ-ММ (не задан — текущий) по дням и баланс года; чужой — по доступу к «Сотрудникам». */
  worktime: (month?: string, id?: string) =>
    api<WorkTimeView>(`/api/employee-card/${id ? encodeURIComponent(id) : "me"}/worktime${month ? `?month=${encodeURIComponent(month)}` : ""}`),
};

export type WorkTimeDay = {
  date: string;
  /** «пн» … «вс». */
  weekday: string;
  workday: boolean;
  /** Вид отсутствия («отпуск», «Отгул» …) или null. */
  absence: string | null;
  day_off: boolean;
  future: boolean;
  norm_hours: number;
  worked_hours: number | null;
  overtime_hours: number | null;
};
export type WorkTimeBalance = {
  year: number;
  from: string;
  to: string;
  overtime_hours: number;
  day_off_days: number;
  day_off_hours: number;
  balance_hours: number;
  state: "debt" | "overtime" | "even";
  label: string;
};
export type WorkTimeView = {
  employee_id: string;
  month: string;
  /** «октябрь 2026». */
  label: string;
  as_of: string;
  source: string;
  note: string;
  available: boolean;
  /** «Нет данных: …» — когда available = false. */
  no_data_reason: string | null;
  shift_hours: number | null;
  days: WorkTimeDay[];
  totals: { norm_hours: number; worked_hours: number; overtime_hours: number } | null;
  balance: WorkTimeBalance | null;
};

/**
 * Заявления на отпуск и отгул («Моя карточка» → «Отпуска и заявления»): Сотрудник → Руководитель → Кадры, каждый шаг —
 * подпись PIN или паролем. `version` — версия строки: шаг с чужой версией сервер отклоняет (409).
 */
export type LeaveKind = "vacation" | "day_off";
export type LeaveStatus = "draft" | "signed" | "approved" | "registered" | "rejected" | "withdrawn";
export type LeaveSignatureView = {
  step: string;
  step_label: string;
  decision: string;
  decision_label: string;
  signer_name: string;
  signed_at: string;
  method_label: string;
  content_version: number;
  content_sha256: string;
  comment: string;
};
export type LeaveRequestView = {
  id: string;
  employee_id: string;
  employee_name: string;
  kind: LeaveKind;
  kind_label: string;
  from_date: string;
  to_date: string;
  days: number;
  comment: string;
  status: LeaveStatus;
  status_label: string;
  /** Кто решает сейчас: ФИО руководителя (и замещающего) или «Кадры»; null — решений не ждёт. */
  waiting_for: string | null;
  content_version: number;
  content_sha256: string;
  version: number;
  created_at: string;
  updated_at: string;
  signatures: LeaveSignatureView[];
  actions: { edit: boolean; sign: boolean; decide: boolean; withdraw: boolean };
};
export type LeaveBalance = { year: number; norm: number; registered: number; pending: number; left: number };
export type LeaveOverview = {
  today: string;
  balance: LeaveBalance;
  own: LeaveRequestView[];
  /** Ждут решения этого человека (руководитель, замещающий, «Кадры»). */
  inbox: LeaveRequestView[];
  /** Журнал кадров (все, кроме черновиков); null — без доступа «Кадры». */
  journal: LeaveRequestView[] | null;
  hr: boolean;
};
export type LeaveDraft = { kind: LeaveKind; from_date: string; to_date: string; comment: string };

export const leaveApi = {
  overview: () => api<LeaveOverview>("/api/leave-requests"),
  waiting: () => api<{ count: number }>("/api/leave-requests/waiting"),
  create: (draft: LeaveDraft) =>
    api<LeaveRequestView>("/api/leave-requests", { method: "POST", body: JSON.stringify(draft) }),
  update: (id: string, version: number, draft: LeaveDraft) =>
    api<LeaveRequestView>(`/api/leave-requests/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ ...draft, version }) }),
  sign: (id: string, version: number, secret: string) =>
    api<LeaveRequestView>(`/api/leave-requests/${encodeURIComponent(id)}/sign`, { method: "POST", body: JSON.stringify({ version, secret }) }),
  decide: (id: string, version: number, approve: boolean, comment: string, secret: string) =>
    api<LeaveRequestView>(`/api/leave-requests/${encodeURIComponent(id)}/decision`, {
      method: "POST", body: JSON.stringify({ version, approve, comment, secret }),
    }),
  withdraw: (id: string, version: number, secret: string, comment: string) =>
    api<LeaveRequestView>(`/api/leave-requests/${encodeURIComponent(id)}/withdraw`, {
      method: "POST", body: JSON.stringify({ version, secret, comment }),
    }),
};

/** «Проекты» — карточки наборов (SUPPLYMAP erptechmap, TcKitset.gs). */
export interface KitsetKbStage {
  index: string;
  status: string;
  manual: boolean;
  tc: boolean;
  kit: boolean;
  review: { value: string; text: string; who: string } | null;
  /** Этапы, которые ручная смена перескочила (в порядке лестницы) — «пропущен» в карточке. */
  skipped?: string[];
}

/** Предпросмотр ручной смены этапа (GET …/status/preview и /api/production/plan/stage-preview). */
export interface StagePreviewKb {
  kb: string;
  label: string;
  from: string;
  to: string;
  skipped: string[];
  change: boolean;
  note: string;
  warnings: string[];
  /** Что смена закроет в этой КБ и что оставит (с причиной). */
  close?: StageCloseItem[];
  keep?: StageKeepItem[];
}

/** Закрываемое ручной сменой вперёд: задание цеха, заявка на комплектацию, резерв техкарты. */
export interface StageCloseItem { kind: "task" | "kit_request" | "reserve" | string; id: string; label: string; qty: string; unit: string }
export interface StageKeepItem { kind: string; label: string; reason: string }
export interface StageCascadeTotals { tasks: number; kit_requests: number; reserves: number; close: number; keep: number }

export interface StagePreview {
  project: string;
  value: string;
  kb: string;
  direction: "forward" | "back" | "auto" | "none";
  reason_required: boolean;
  error: string;
  message: string;
  terminal: string;
  blocker: string;
  kbs: StagePreviewKb[];
  warnings: string[];
  close?: StageCloseItem[];
  keep?: StageKeepItem[];
  totals?: StageCascadeTotals;
  /** «Назад ничего не открывается» — для хода назад и «авто». */
  cascade_note?: string;
  /** Уходит с записью: изменилось закрываемое с момента предпросмотра — сервер ответит 409. */
  preview_token?: string;
}

export interface KitsetTile {
  project: string;
  customer: string;
  calc_source: string;
  updated_at: string;
  updated_by: string;
  assemblies: number;
  lines: number;
  kbs: string[];
  status: string;
  kb_stages: KitsetKbStage[];
  blocker: string;
}

export interface KitsetLot {
  lot_id: string | null;
  warehouse: string;
  grn: string;
  package: string;
  /** Версия партии из списка ГРН: уходит вместе с выбором, устаревшая даёт 409. */
  version?: number;
}

/** Что ГРН строки держит под КБ прямо сейчас; null — ничего не зарезервировано. */
export interface KitsetReserve {
  quantity: string;
  unit: string;
  lot_id: string;
  warehouse: string;
  grn: string;
  package: string;
}

export interface KitsetComponent {
  art: string;
  type: string;
  name: string;
  link: string;
  per: string;
  scrap: string;
  batch: string;
  /** шт / м — by type, as the calc import (ZPK_UNIT_BY_TYPE). */
  unit: string;
  lot: KitsetLot | null;
  reserve?: KitsetReserve | null;
}

/** Строка реестра «КД» (SUPPLYMAP TcKd.gs): файл хранится в базе ERP (`stored`), его отдаёт `documentUrl`.
 *  Строка из времён Google Диска без байтов — только `url` на Диск, архив для чтения. */
export interface KitsetDocument {
  id: string;
  index: string;
  name: string;
  size: number | null;
  stored: boolean;
  url: string | null;
  uploaded_at: string;
  who: string;
  /** «harness» — чертёж жгута, правится в ERP (`kitsetsApi.harness`); «harness_unlinked» — чертёж, отвязанный от позиции
   *  (хранится, «Вернуть» — `kitsetsApi.restoreHarness`); «file» — вложенный файл. */
  kind?: "harness" | "harness_unlinked" | "file";
}

/** Чертёж жгута позиции (документ TECHMAP-GRAPHER, schemaVersion 1) — строка КД с текущим чертежом и история версий.
 *  `revision` — номер версии, с которым сохранять: чужое сохранение после него даёт 409. Чертежа нет — всё null.
 *  `version` — прежнее имя того же номера (текстом), сервер отдаёт его один выпуск для открытых вкладок. */
export interface HarnessDrawing {
  document: KitsetDocument | null;
  design: Record<string, unknown> | null;
  revision: number | null;
  version?: string | null;
  warnings: string[];
}

/** Строка «Истории версий»: чертёж на версию `revision`; автосохранения одного сотрудника в пределах 10 минут —
 *  одна строка (`first_revision`…`revision`, `started_at`…`saved_at`). Как появилась — сохранение, загрузка из
 *  TECHMAP-GRAPHER, восстановление (`restored_from` — из какой), начальная (чертёж до истории); `closed` — закреплена
 *  кнопкой «Сохранить версию». */
export interface HarnessRevision {
  revision: number;
  first_revision: number;
  started_at: string;
  closed: boolean;
  saved_at: string;
  saved_by: string;
  size: number;
  kind: "saved" | "imported" | "restored" | "initial";
  restored_from: number | null;
}

export interface HarnessRevisions {
  document_id: string;
  current: number;
  total: number;
  revisions: HarnessRevision[];
}

/** Документы чертежа жгута, построенные сервером из самого чертежа (techcards/harness/documents.py): ничего не хранится. */
export type HarnessDocumentKind = "connections" | "cut" | "bom";

/** Строка «Таблицы соединений» — один провод; пустое значение — «—». */
export interface HarnessConnectionRow {
  wire_id: string; index: string; mark: string; section: string; marking: string;
  from: string; to: string; color: string; color_hex: string; length_mm: number | null; route: string;
}

export interface HarnessStripStep { index: number; diameter_mm: number | null; length_mm: number; step_mm: number }
export interface HarnessCutEnd { label: string; profile: string | null; steps: HarnessStripStep[] }

/** Заготовка «Карты резки»: провод вне кабеля, кабель или оболочка; длины — мм, метраж — м на все жгуты. */
export interface HarnessCutItem {
  id: string; kind: "wire" | "cable" | "covering"; title: string; material: string | null; material_key: string | null;
  length_mm: number | null; correction_a_mm: number; correction_b_mm: number; rounding_step_mm: number;
  cut_mm: number | null; unrounded_mm: number | null; pieces: number; total_m: number | null;
  status: "ready" | "incomplete"; warnings: ("material-missing" | "length-missing")[];
}

/** Лист резки и разделки одной заготовки (провода или кабеля). */
export interface HarnessCutSheet {
  object_id: string; member_wire_ids: string[]; kind: "wire" | "cable"; title: string; material: string | null;
  a: HarnessCutEnd; b: HarnessCutEnd;
  sheath: { a_mm: number | null; b_mm: number | null; total_mm: number | null; complete: boolean } | null;
  conductors: { id: string; a: HarnessCutEnd; b: HarnessCutEnd }[];
  warnings: string[];
}

/** Строка «Спецификации»; `key` — тот же ключ, по которому на чертеже стоят позиции, порядок и правки текста. */
export interface HarnessBomRow {
  key: string; position: number; index: string; designation: string; name: string; amount: number | null;
  unit: string; note: string; object_ids: string[];
}

export interface HarnessDocuments {
  connections: { kind: "connections"; rows: HarnessConnectionRow[] };
  cut: { kind: "cut"; quantity: number; items: HarnessCutItem[]; sheets: HarnessCutSheet[]; status: "ready" | "incomplete" };
  bom: { kind: "bom"; quantity: number; rows: HarnessBomRow[] };
}

/** «Ручная сборка» из чертежа: build_config (операции с `origin` = `harness:<объект>:<код>`, блоки по маршруту КД) —
 *  экран сливает её со схемой мастера через `rederive`. Ничего не сохраняется. */
export interface HarnessDerived {
  build_config: Record<string, unknown>;
  warnings: string[];
}

export interface KitsetAssembly {
  index: string;
  name: string;
  run: string;
  item: string;
  stage: KitsetKbStage | null;
  techcard: { card_id: string; status: string; issued: boolean; stages: number; revision: number } | null;
  kd: KitsetDocument[];
  components: KitsetComponent[];
}

/** КБ проекта перед отправкой на комплектацию: живая заявка — уже отправлена, второй раз не уйдёт. */
export interface KitsetIssueAssembly {
  index: string;
  name: string;
  has_items: boolean;
  request: { id: string; no: string; status: string } | null;
}

export interface KitsetCard {
  project: string;
  customer: string;
  calc_source: string;
  updated_at: string;
  updated_by: string;
  version: number;
  status: string;
  blocker: string;
  /** Активный блокер журнала проекта: вид, причина, с какого момента, кто поставил. */
  blocker_info?: { type: string; text: string; since: string; who: string } | null;
  deadline?: string | null;
  journal?: KitsetJournalEvent[];
  stages: string[];
  review_values: string[];
  terminals?: string[];
  blockers?: string[];
  assemblies: KitsetAssembly[];
}

export interface KitsetJournalEvent {
  id: string;
  occurred_at: string;
  who: string;
  kind: string;
  blocker: string;
  text: string;
  item_index: string;
  value: string;
  /** Для смены этапа: что было и почему (назад — причина обязательна). */
  from_value?: string;
  reason?: string;
}

/** Смена статуса проекта в один клик (POST …/status): этап на все КБ, «авто», закрытие, блокер. */
export type ProjectStatusAction = { action: "status"; value: string; text?: string; reason?: string; preview_token?: string }
  | { action: "block"; value: string; text: string }
  | { action: "unblock"; text?: string };

export interface KitsetLoadResult {
  project: string;
  assemblies: number;
  lines: number;
  erp_error: string;
  ship_error: string;
  ship_to_error: string;
  deadline_error: string;
  /** «Сразу создать заявки на закупку»: null — галочка не стояла или заявки не созданы (см. requests_error). */
  requests?: { created: number; existing: number; skipped: string[] } | null;
  requests_error?: string;
  /** Не пусто — резерв ГРН пересчитался не полностью. */
  reserve_error?: string;
}

export interface KitsetSupplyLine {
  art: string;
  name: string;
  /** Where the name comes from: ref — справочник ERP, spec — спецификация, stock — склад, site — сайт, "" — not found. */
  name_source?: "ref" | "spec" | "stock" | "site" | "";
  /** Tooltip of the name: its source, or why the article is unknown. */
  name_hint?: string;
  /** «Стадия» in one word: short · kitting · procurement · received · outside · stock · none. */
  state?: string;
  state_label?: string;
  state_tone?: "ok" | "warn" | "danger" | "info" | "neutral";
  /** The numbers behind the state. */
  state_hint?: string;
  kbs: string[];
  need: string;
  unit: string;
  grn: string;
  stage: string;
  received: boolean;
  rows: Array<{ where: string; status: string; source_id: string }>;
}

export interface KitsetStockLot {
  lot_id: string;
  warehouse: string;
  grn: string;
  package: string;
  article: string;
  name: string;
  unit: string;
  storage_location: string;
  available: string;
  /** The part of `available` that is the project's own «Со склада» reserve (absent when none). */
  reserved?: string;
  /** Версия партии: уходит с выбором в `lot.version`, устаревшая даёт 409. */
  version?: number;
}

export interface KitsetLineEdit {
  index: string;
  art: string;
  per: string;
  scrap: string;
  name?: string;
  link?: string;
  lot?: KitsetLot | null;
}

export interface KitsetDeleteResult {
  project: string;
  deleted: number;
  erp: Record<string, number>;
  cards: { cards: number; revisions: number };
  gp: { deleted: number; kept: number };
  /** Записи журнала проекта в производственном плане, снесённые вместе с проектом. */
  journal?: { deleted: number };
  /** Не пусто — резерв ГРН вернулся на склад не полностью. */
  reserve_error?: string;
  /** Хвосты без движения: журнал, запросы на комплектацию, задания, заявки на закупку; `message` — итог по-русски. */
  tails?: {
    fulfillment: { deleted: number; kept: number };
    production: { deleted: number; kept: number };
    requests: { deleted: number; kept: number };
    journal?: { deleted: number; kept: number };
    freed: number;
    kept: { section: string; label: string; reason: string }[];
    message: string;
  } | null;
  tails_error?: string;
}

const kitsetPath = (project: string) => `/api/techcards/kitsets/${encodeURIComponent(project)}`;
const harnessPath = (project: string, index: string) => `${kitsetPath(project)}/harness/${encodeURIComponent(index)}`;

/** Запись трека проекта: что создано в одном из модулей. `at` — ISO-время; null — события ещё нет (такие идут в конце). */
export type ProjectTrackEntry = {
  at: string | null;
  /** Раздел ERP по-русски: «Заявки», «Закупка», «Приёмка», «Комплектация», «Производство», «Отгрузка», «Задачи», …
   * У «Задачи» kind — текст задачи, ref — кому она («Производство»), status — «Открыта»/«Закрыта». */
  module: string;
  /** Что за запись: «Запрос на комплектацию», «Задание запущено», … */
  kind: string;
  /** Человеческий номер: SM-…, ПЗ-…, ГРН; у карточки проекта — номер проекта. */
  ref: string;
  status: string;
  /** КБ записи («КБ1»); пусто, если запись относится ко всему проекту. */
  item_index: string;
};

export type ProjectTrack = { project: string; entries: ProjectTrackEntry[] };

/** `tcLoadBomFromKit`: состав сборки для генератора и строки листа КБn расчёта (`tcCalcOps_`). */
export type KitsetBom = {
  project: string; index: string; name: string; run: string; part_qty: string; calc_source: string;
  components: Array<{ id: string; type: string; name: string; art: string; kd: string; qty: number | string }>;
  calc_ops: Array<{ code: string; comp: string; n: string; l: string; req: string }>;
};

export const kitsetsApi = {
  list: (sort = "upd") => api<{ items: KitsetTile[]; statuses: string[]; stages?: string[]; terminals?: string[]; blockers?: string[] }>(`/api/techcards/kitsets?sort=${encodeURIComponent(sort)}`),
  get: (project: string) => api<KitsetCard>(kitsetPath(project)),
  load: (payload: {
    url: string;
    number?: string;
    deadline?: string | null;
    ship?: { shipping_point: string; contact: string; phone: string } | null;
    requests?: { purpose: string; delivery_point: string; procurement_deadline: string } | null;
  }) => api<{ res: KitsetLoadResult }>("/api/techcards/kitsets/load", { method: "POST", body: JSON.stringify(payload) }),
  saveLines: (project: string, edits: KitsetLineEdit[]) =>
    api<{ res: { updated: number } }>(`${kitsetPath(project)}/lines`, { method: "PUT", body: JSON.stringify({ edits }) }),
  supply: (project: string) => api<{ lines: KitsetSupplyLine[] }>(`${kitsetPath(project)}/supply`),
  /** Трек проекта: записи всех модулей по времени. */
  track: (project: string) => api<ProjectTrack>(`${kitsetPath(project)}/track`),
  bom: (project: string, index: string) =>
    api<KitsetBom>(
      `${kitsetPath(project)}/bom/${encodeURIComponent(index)}`,
    ),
  lots: (query: string, project: string) =>
    api<{ lots: KitsetStockLot[] }>(`/api/techcards/kitsets/lots?q=${encodeURIComponent(query)}&project=${encodeURIComponent(project)}`),
  /** Перед «Отправить на комплектацию»: по каждой КБ — её живая заявка (не «Отменена») или null. */
  issueState: (project: string) =>
    api<{ project: string; assemblies: KitsetIssueAssembly[] }>(`${kitsetPath(project)}/issue`),
  issue: (project: string, deadline: string) =>
    api<{ created: Array<{ no: string; index: string; shortfall: string[] }>; skipped: string[]; already?: Array<{ index: string; no: string; status: string }>; message: string; reserve_error?: string }>(
      `${kitsetPath(project)}/issue`, { method: "POST", body: JSON.stringify({ deadline }) },
    ),
  remove: (project: string) => api<{ res: KitsetDeleteResult }>(kitsetPath(project), { method: "DELETE" }),
  mark: (project: string, payload: { kb: string; kind: "stage" | "tcReview"; value: string; text: string; reason?: string; preview_token?: string }) =>
    api<{ msg: string }>(`${kitsetPath(project)}/marks`, { method: "POST", body: JSON.stringify(payload) }),
  stagePreview: (project: string, value: string, kb = "") =>
    api<StagePreview>(`${kitsetPath(project)}/status/preview?value=${encodeURIComponent(value)}&kb=${encodeURIComponent(kb)}`),
  setStatus: (project: string, payload: ProjectStatusAction) =>
    api<{ events: number; card: KitsetCard }>(`${kitsetPath(project)}/status`, { method: "POST", body: JSON.stringify(payload) }),
  journal: (project: string, payload: { kind: "note" | "deadline"; text: string; deadline?: string | null }) =>
    api<{ card: KitsetCard }>(`${kitsetPath(project)}/journal`, { method: "POST", body: JSON.stringify(payload) }),
  addDocument: (project: string, payload: { index: string; file_name: string; content_type: string; content_b64: string }) =>
    api<{ res: KitsetDocument }>(`${kitsetPath(project)}/documents`, { method: "POST", body: JSON.stringify(payload) }),
  documentUrl: (project: string, id: string) => `${kitsetPath(project)}/documents/${encodeURIComponent(id)}`,
  removeDocument: (project: string, id: string) =>
    api<{ res: { deleted: number } }>(`${kitsetPath(project)}/documents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  openTechcard: (project: string, index: string) =>
    api<{ card_id: string }>(`${kitsetPath(project)}/techcards/${encodeURIComponent(index)}`, { method: "POST", body: JSON.stringify({}) }),
};

/** Шаблон библиотеки компонентов жгута (последняя версия): разъём по ключу справочника и номеру версии. */
export interface HarnessComponentSummary {
  templateId: string;
  version: number;
  code: string;
  name: string;
  connectorRef: string;
  articleBindings: { sourceId: string; entityType: string; articleKey: string }[];
  createdUtc: string;
  updatedUtc: string;
  hasImage: boolean;
}

/** Версия шаблона целиком: модель TECHMAP-GRAPHER (v3–v5) и рисунки; соединитель на чертеже закрепляет
 *  `templateId` + `version` + `versionSha256`. */
export interface HarnessComponent extends HarnessComponentSummary {
  versionSha256: string;
  assets: { assetId: string; fileName: string; mediaType: "image/png"; sha256: string; sizeBytes: number }[];
  content: Record<string, unknown>;
}

/** Тело записи шаблона: модель, код и название; картинка разъёма — по желанию. */
export interface HarnessComponentInput {
  code: string;
  name: string;
  model: Record<string, unknown>;
  png_base64?: string;
}

/** Библиотечные соединители чертежа и закреплённые ими версии шаблонов; `unresolved` — соединители, чьей версии нет
 *  ни в библиотеке, ни в копии старого чертежа (рисуются по своим контактам). */
export interface HarnessPlacementGraph {
  placements: Record<string, unknown>[];
  snapshots: Record<string, unknown>[];
  unresolved: string[];
}

/** «Маршрут» позиции как его читает холст (techcards/harness/route_application.py `view`): `stale` — чертёж с тех пор
 *  изменился; `missing_refs` — части, которых в чертеже уже нет; `complete` — одна карточка «Готовый жгут» со всеми
 *  частями чертежа. Маршрута нет — `route: null`; чертежа нет — `drawing: null`. */
export interface HarnessRouteRead {
  route: {
    revision: number; document: Record<string, unknown>; drawing_document_id: string; drawing_revision: number;
    updated_at: string; updated_by: string;
  } | null;
  drawing: { document_id: string; revision: number } | null;
  stale: boolean;
  missing_refs: Array<{ kind: string; id: string }>;
  complete: boolean;
}

/** «Заполнить техкарту» (techcards/harness/route_to_assembly.py): карточки маршрута — черновиком «Ручной сборки».
 *  `route` — схема полуфабрикатов, как её собрал сборщик ЭТК (блоки с операциями, стрелки с количеством на штуку);
 *  `draft` — сохранённый черновик (при просмотре — null). */
export interface HarnessRouteDraft {
  dry_run: boolean;
  revision: number;
  build_config: { ops?: Array<{ label?: string; qty?: string; tOp?: string; seq?: number }> } & Record<string, unknown>;
  operations: number;
  route: {
    source: string; final: string;
    blocks: Array<{ key: string; name: string; ops: number[]; final: boolean; minutes: number }>;
    edges: Array<{ from: string; to: string; per_unit: number }>;
  } | null;
  warnings: string[];
  draft: { id: string; project: string; item_index: string; source: string; author: string; status: string; note: string; created_at: string } | null;
}

const componentsPath = "/api/techcards/harness-components";
const componentPath = (templateId: string, revision?: number) =>
  `${componentsPath}/${encodeURIComponent(templateId)}${revision === undefined ? "" : `/versions/${revision}`}`;

/** Чертёж жгута позиции — единственный сетевой слой редактора жгута: чертёж и его история, документы, «Ручная сборка»,
 *  обмен с TECHMAP-GRAPHER, справочники ERP для пикеров и библиотека компонентов. */
export const harnessApi = {
  /** Чертёж позиции; `revision` — номер версии, с которым сохранять (чертежа нет — всё null). */
  drawing: (project: string, index: string) => api<HarnessDrawing>(harnessPath(project, index)),
  /** Сохранить чертёж новой версией: `revision` — из последнего чтения (null — чертежа ещё нет; иначе 409, если его
   *  сохранили после). Чертёж, который нельзя хранить, — 422 со списком причин (`ApiError.detail.errors`).
   *  `checkpoint` — записать версию сразу закрытой строкой истории (автосохранения в неё не сольются). */
  save: (project: string, index: string, design: Record<string, unknown>, revision: number | null, options: { checkpoint?: boolean } = {}) =>
    api<HarnessDrawing & { created: boolean }>(harnessPath(project, index), { method: "PUT",
      body: JSON.stringify({ design, revision, ...(options.checkpoint ? { checkpoint: true } : {}) }) }),
  /** «Сохранить версию»: текущая версия остаётся в истории отдельной строкой; `revision` — версия на экране (иначе 409). */
  checkpoint: (project: string, index: string, revision: number | null) =>
    api<{ revision: number | null; row: HarnessRevision | null }>(`${harnessPath(project, index)}/checkpoint`, { method: "POST", body: JSON.stringify({ revision }) }),
  /** «История версий» чертежа, новые сверху. */
  revisions: (project: string, index: string) => api<HarnessRevisions>(`${harnessPath(project, index)}/revisions`),
  /** Восстановить версию: она становится чертежом новой версией; `current` — версия на экране (иначе 409). */
  restoreRevision: (project: string, index: string, revision: number, current: number | null) =>
    api<HarnessDrawing>(`${harnessPath(project, index)}/revisions/${revision}/restore`, { method: "POST", body: JSON.stringify({ revision: current }) }),
  /** «Ручная сборка» из чертежа: переданного (несохранённого в редакторе) или сохранённого. */
  derive: (project: string, index: string, design?: Record<string, unknown>) =>
    api<HarnessDerived>(`${harnessPath(project, index)}/derive`, { method: "POST", body: JSON.stringify(design ? { design } : {}) }),
  /** Документ чертежа: по чертежу из редактора (`design`, ещё не сохранённому) или по сохранённому. */
  documents: <K extends HarnessDocumentKind>(project: string, index: string, kind: K, quantity = 1, design?: Record<string, unknown>) => {
    const path = `${harnessPath(project, index)}/documents?kind=${kind}&quantity=${quantity}`;
    return design
      ? api<HarnessDocuments[K]>(path, { method: "POST", body: JSON.stringify({ design }) })
      : api<HarnessDocuments[K]>(path);
  },
  /** Документ сохранённого чертежа файлом для Excel. */
  documentsExcelUrl: (project: string, index: string, kind: HarnessDocumentKind, quantity = 1) =>
    `${harnessPath(project, index)}/documents?kind=${kind}&quantity=${quantity}&format=xlsx`,
  /** «Отвязать» чертёж от позиции: строка КД и чертёж остаются, карточка показывает его с «Вернуть». */
  unlink: (project: string, index: string, revision: number | null = null) =>
    api<{ document: KitsetDocument }>(`${harnessPath(project, index)}/unlink`, { method: "POST", body: JSON.stringify({ revision }) }),
  /** «Вернуть» отвязанный чертёж позиции (если у неё нет другого). */
  relink: (project: string, index: string, documentId: string) =>
    api<HarnessDrawing>(`${harnessPath(project, index)}/restore`, { method: "POST", body: JSON.stringify({ document_id: documentId }) }),
  /** «Удалить» отвязанный чертёж позиции насовсем, с историей версий; привязанный сервер не удалит (409). */
  removeUnlinked: (project: string, index: string, documentId: string) =>
    api<{ res: { deleted: number; revisions: number } }>(`${harnessPath(project, index)}/unlinked/${encodeURIComponent(documentId)}`, { method: "DELETE" }),
  /** «Маршрут» позиции (Маршрут v3): карточки, связи, версия и версия чертежа, на которой он построен. */
  route: (project: string, index: string) => api<HarnessRouteRead>(`${harnessPath(project, index)}/route`),
  /** Сохранить маршрут новой версией: `revision` — из последнего чтения (null — маршрута ещё нет; иначе 409, если его
   *  сохранили после); `drawingRevision` — версия чертежа, на которой маршрут построен. Нельзя хранить — 422 со списком. */
  saveRoute: (project: string, index: string, document: Record<string, unknown>, revision: number | null, drawingRevision: number) =>
    api<HarnessRouteRead>(`${harnessPath(project, index)}/route`, { method: "PUT", body: JSON.stringify({ document, revision, drawing_revision: drawingRevision }) }),
  /** «Построить по чертежу»: карточки и связи заново по сохранённому чертежу (ручные правки карточек пропадают). */
  generateRoute: (project: string, index: string, revision: number | null) =>
    api<HarnessRouteRead>(`${harnessPath(project, index)}/route/generate`, { method: "POST", body: JSON.stringify({ revision }) }),
  /** «Заполнить техкарту»: карточки маршрута — черновиком «Ручной сборки». `dryRun` — только показать результат;
   *  без него — сохранить черновик по просмотренной версии маршрута `revision` (изменился после просмотра — 409). */
  routeDraft: (project: string, index: string, revision: number | null, dryRun: boolean) =>
    api<HarnessRouteDraft>(`${harnessPath(project, index)}/route/draft${dryRun ? "?dry_run=true" : ""}`, { method: "POST", body: JSON.stringify({ revision }) }),
  /** `.techmap-project.zip` чертежа — открыть в TECHMAP-GRAPHER. */
  exportUrl: (project: string, index: string) => `${harnessPath(project, index)}/export`,
  /** Чертёж из `.techmap-project.zip` TECHMAP-GRAPHER — новой версией (та же проверка версии и содержимого). */
  import: (project: string, index: string, payload: { file_name: string; content_b64: string; revision: number | null }) =>
    api<HarnessDrawing & { created: boolean }>(`${harnessPath(project, index)}/import`, { method: "POST", body: JSON.stringify(payload) }),
  /** Справочники ERP для пикеров редактора (БД.ТЕР, БД.ПРОВ, СПР.КАБ, БД.КОАКС, БД.МАТ, разъёмы) — снимками. */
  catalog: () => api<unknown>("/api/techcards/harness-catalog"),
  /** Библиотека компонентов: последняя версия каждого шаблона. */
  components: () => api<HarnessComponentSummary[]>(componentsPath),
  /** Версия шаблона (без номера — последняя). */
  component: (templateId: string, revision?: number) => api<HarnessComponent>(componentPath(templateId, revision)),
  /** Новый шаблон библиотеки (версия 1): модель TECHMAP-GRAPHER, код и название; `png_base64` — картинка разъёма.
   *  403 — нет права «Проекты: правка»; 422 — русская причина, по которой модель не принята. */
  createComponent: (input: HarnessComponentInput) =>
    api<HarnessComponent>(componentsPath, { method: "POST", body: JSON.stringify(input) }),
  /** Новая версия шаблона (N+1); `base_revision` — версия, с которой правили (устаревшая — 409 «Шаблон уже изменил …»). */
  publishComponentVersion: (templateId: string, baseRevision: number, input: HarnessComponentInput) =>
    api<HarnessComponent>(`${componentPath(templateId)}/versions`, { method: "POST", body: JSON.stringify({ base_revision: baseRevision, ...input }) }),
  /** Рисунок версии шаблона (PNG). */
  componentAssetUrl: (templateId: string, revision: number, assetId: string) =>
    `${componentPath(templateId, revision)}/assets/${encodeURIComponent(assetId)}`,
  /** Библиотечные соединители сохранённого чертежа и закреплённые версии. */
  placements: (project: string, index: string) => api<HarnessPlacementGraph>(`${harnessPath(project, index)}/components`),
};

/** «Чертёж → схема»: черновики «Ручной сборки» по позиции проекта; финал и урок — пара для корпуса образцов. */
export type AssemblyDraftSource = "claude-code" | "server-agent" | "manual";

export interface AssemblyDraft {
  id: string;
  project: string;
  item_index: string;
  drawing_document_id: string | null;
  drawing_url: string | null;
  source: AssemblyDraftSource | string;
  author: string;
  status: "draft" | "final" | string;
  final_revision: number | null;
  lesson: string;
  note: string;
  created_at: string;
  updated_at: string;
  /** Есть только у одного черновика (GET /drafts/{id}), в списке их нет. */
  draft_config?: Record<string, unknown>;
  final_config?: Record<string, unknown> | null;
}

export const assemblyApi = {
  /** Типовая сборка генератора как «Ручная сборка»: блоки с операциями; ничего не сохраняется. */
  fromGenerator: (config: Record<string, unknown>) =>
    api<Record<string, unknown>>("/api/techcards/assembly/from-generator", { method: "POST", body: JSON.stringify({ config }) }),
  drafts: (project: string, itemIndex: string) =>
    api<{ items: AssemblyDraft[] }>(`/api/techcards/assembly/drafts?project=${encodeURIComponent(project)}&item_index=${encodeURIComponent(itemIndex)}`),
  draft: (id: string) => api<AssemblyDraft>(`/api/techcards/assembly/drafts/${encodeURIComponent(id)}`),
  setLesson: (id: string, lesson: string) =>
    api<AssemblyDraft>(`/api/techcards/assembly/drafts/${encodeURIComponent(id)}/lesson`, { method: "PUT", body: JSON.stringify({ lesson }) }),
  /** Чертёж позиции — в КД её сборки: PDF, JPG или PNG до 20 МБ. */
  uploadDrawing: (project: string, itemIndex: string, fileName: string, contentBase64: string) =>
    api<{ id: string; name: string; url: string | null }>("/api/techcards/assembly/drawings", {
      method: "POST", body: JSON.stringify({ project, item_index: itemIndex, file_name: fileName, content_base64: contentBase64 }),
    }),
  /** Несколько PDF в заданном порядке — один файл КД позиции; сервер читает все части и сохраняет всё или ничего. */
  uploadMergedDrawing: (project: string, itemIndex: string, fileName: string, files: { file_name: string; content_base64: string }[]) =>
    api<{ id: string; name: string; url: string | null; pages: { file_name: string; pages: number }[] }>("/api/techcards/assembly/drawings/merge", {
      method: "POST", body: JSON.stringify({ project, item_index: itemIndex, file_name: fileName, files }),
    }),
};

/* Черновик генератора ТК на сервере (владелец 11.10): недособранная форма генератора — своя у каждого человека за
   проектом и КБ; сохраняется поверх версии (409 — изменён в другом месте). */
export interface GeneratorDraftRecord {
  project: string;
  item_index: string;
  payload: Record<string, unknown>;
  build_fingerprint: string;
  version: number;
  created_at: string;
  updated_at: string;
}
export interface GeneratorDraftConflict { message: string; code: "generator_draft_conflict"; version: number; updated_at: string | null }

const generatorDraftUrl = (project: string, itemIndex: string) =>
  `/api/techcards/generator-draft?project=${encodeURIComponent(project)}&item_index=${encodeURIComponent(itemIndex)}`;

export const generatorDraftApi = {
  get: (project: string, itemIndex: string) => api<{ draft: GeneratorDraftRecord | null }>(generatorDraftUrl(project, itemIndex)),
  /** Повтор безвреден (`safe`): запись идёт поверх версии. keepalive — при закрытии страницы: запрос доходит и после
   *  её выгрузки. */
  save: (project: string, itemIndex: string, body: { payload: unknown; build_fingerprint: string; base_version: number }, keepalive = false) =>
    api<GeneratorDraftRecord>(generatorDraftUrl(project, itemIndex), { method: "PUT", body: JSON.stringify(body), keepalive, safe: true }),
  remove: (project: string, itemIndex: string) =>
    api<{ deleted: boolean }>(generatorDraftUrl(project, itemIndex), { method: "DELETE", safe: true }),
};

/* Токены API (генератор техкарт, черновики сборки): выпускает и отзывает только полный доступ. */
export type ApiTokenStatus = "active" | "expired" | "revoked";
export interface ApiTokenRow {
  id: string;
  name: string;
  scopes: string[];
  scope_labels: string[];
  status: ApiTokenStatus;
  created_at: string | null;
  created_by: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  revoked_by: string;
}
export interface ApiTokenIssued extends ApiTokenRow {
  /** Секрет `erp_…` приходит один раз, в ответе на выпуск. */
  token: string;
}

export const apiTokensApi = {
  list: () => api<{ tokens: ApiTokenRow[]; scopes: Record<string, string> }>("/api/auth/api-tokens"),
  issue: (payload: { name: string; scopes: string[]; days: number }) =>
    api<ApiTokenIssued>("/api/auth/api-tokens", { method: "POST", body: JSON.stringify(payload) }),
  revoke: (id: string) =>
    api<ApiTokenRow>(`/api/auth/api-tokens/${encodeURIComponent(id)}/revoke`, { method: "POST", body: JSON.stringify({}) }),
};

/** «Документы с ознакомлением» (Личная карточка, фаза 3): PDF приходит и уходит base64 внутри JSON. */
export type DocumentKind = "job_description" | "regulation" | "other";
export type DocumentScope = "all" | "department" | "job_title";
export type DocumentVersionView = {
  id: string;
  version_no: number;
  file_name: string;
  size: number;
  sha256: string;
  uploaded_by_name: string;
  uploaded_at: string;
};
export type DocumentBase = {
  id: string;
  title: string;
  kind: DocumentKind;
  kind_label: string;
  scope: DocumentScope;
  scope_label: string;
  scope_value: string;
  /** «Все сотрудники» / «Отдел: Монтаж» / «Должность: Мастер». */
  audience: string;
  archived: boolean;
  created_by_name: string;
  created_at: string;
  current: DocumentVersionView | null;
};
export type OwnDocument = DocumentBase & {
  signed: { signed_at: string; method_label: string } | null;
  /** Номер ранее подписанной редакции, когда текущая ещё не подписана. */
  signed_earlier_version: number | null;
};
export type JournalDocument = DocumentBase & {
  versions: DocumentVersionView[];
  readers: number;
  signed: number;
};
export type DocumentsOverview = { own: OwnDocument[]; journal: JournalDocument[] | null; hr: boolean };
export type SheetRow = {
  user_id: string;
  name: string;
  department: string | null;
  job_title: string | null;
  in_scope: boolean;
  signed_at: string | null;
  method_label: string | null;
};
export type DocumentSheet = DocumentBase & {
  versions: DocumentVersionView[];
  version: DocumentVersionView;
  is_current: boolean;
  readers: number;
  signed: number;
  rows: SheetRow[];
};
export type DocumentDraft = { title: string; kind: DocumentKind; scope: DocumentScope; scope_value: string };

export const documentsApi = {
  overview: () => api<DocumentsOverview>("/api/documents"),
  pending: () => api<{ count: number }>("/api/documents/pending"),
  fileUrl: (versionId: string) => `/api/documents/versions/${encodeURIComponent(versionId)}/file`,
  create: (draft: DocumentDraft, file_name: string, content_base64: string) =>
    api<DocumentSheet>("/api/documents", { method: "POST", body: JSON.stringify({ ...draft, file_name, content_base64 }) }),
  sheet: (id: string, versionNo?: number) =>
    api<DocumentSheet>(`/api/documents/${encodeURIComponent(id)}/sheet${versionNo ? `?version_no=${versionNo}` : ""}`),
  addVersion: (id: string, file_name: string, content_base64: string) =>
    api<DocumentSheet>(`/api/documents/${encodeURIComponent(id)}/versions`, { method: "POST", body: JSON.stringify({ file_name, content_base64 }) }),
  archive: (id: string, archived: boolean) =>
    api<DocumentSheet>(`/api/documents/${encodeURIComponent(id)}/archive`, { method: "POST", body: JSON.stringify({ archived }) }),
  removeVersion: (versionId: string) =>
    api<{ deleted_document: boolean; document: DocumentSheet | null }>(`/api/documents/versions/${encodeURIComponent(versionId)}`, { method: "DELETE" }),
  acknowledge: (versionId: string, secret: string) =>
    api<DocumentsOverview>(`/api/documents/versions/${encodeURIComponent(versionId)}/acknowledge`, { method: "POST", body: JSON.stringify({ secret }) }),
};
