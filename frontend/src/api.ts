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

export interface FulfillmentRequest {
  id: string;
  number: string;
  version: number;
  created_at: string;
  requester: string;
  purpose: string;
  deadline: string | null;
  status: string;
  handout_location: string | null;
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
  // Сетевой график: блок схемы, этапы-входы, финальная ли операция (её выход идёт в ОТК и на склад ГП) и сколько
  // штук она сдаёт по заданию (тираж × штук её полуфабриката на изделие; в цепочке — тираж).
  stage_key?: string | null;
  inputs?: ProductionOperationInput[];
  final?: boolean;
  required_quantity?: string;
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
export type CableKind = "coax" | "wire" | "other";

export interface BasesResult {
  bases: ReferenceBase[];
  /** Records per stored tab and per cable list — the counts on the tab chips. */
  tabs?: Partial<Record<TechOperationTab, number>>;
  cables?: Record<CableKind, number>;
}

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
}

export interface CablesResult {
  columns: string[];
  counts: Record<CableKind, number>;
  rows: CableRow[];
}

export interface TechOperationRecord {
  id: string;
  tab: TechOperationTab;
  display_text: string;
  source_sheet: string;
  fields: Record<string, string>;
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
  }>;
}

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
export function failureText(reason: unknown, fallback: string) {
  if (reason instanceof TypeError) return NO_CONNECTION;
  return reason instanceof Error ? reason.message : fallback;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  // FormData (a file upload) sets its own multipart boundary — the header is left to the browser.
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const csrf = cookie("__Host-erp_csrf") || cookie("erp_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, { ...init, headers, credentials: "include" });
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

export const identityApi = {
  me: () => api<Principal>("/api/auth/me"),
  login: (login: string, password: string) =>
    api<Principal>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    }),
  loginStaff: () => api<{ staff: LoginStaff[] }>("/api/auth/login-staff"),
  /** An empty PIN asks for «узнать PIN» (first sign-in); the answer is then a PinReveal, not a session. */
  pinLogin: (who: string, pin: string) =>
    api<Principal | PinReveal>("/api/auth/pin-login", { method: "POST", body: JSON.stringify({ who, pin }) }),
  adminMode: (on: boolean) =>
    api<Principal>("/api/auth/admin-mode", { method: "POST", body: JSON.stringify({ on }) }),
  logout: () => api<void>("/api/auth/logout", { method: "POST" }),
  profile: () => api<OwnProfile>("/api/auth/profile"),
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
  list: (q = "") =>
    api<{ requests: RequestRow[] }>(q ? `/api/requests?q=${encodeURIComponent(q)}` : "/api/requests"),
  /** RQ-MINE: server-side filter by the authenticated initiator, paginated (replaces the client-side
   *  name match over the latest 200 rows). */
  mine: (limit: number, offset: number, q = "") =>
    api<MineRequestsResult>(
      `/api/requests/mine?limit=${limit}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
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
    }),
  /** Fallback when the server has no Google key: the ЗКП range pasted as a grid. */
  importParse: (grid: Array<Array<string | null>>) =>
    api<CalcImportPreview>("/api/requests/import/parse", {
      method: "POST",
      body: JSON.stringify({ grid }),
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
  lines: (OrderLineRef & { price_with_vat: string | null })[];
}

export interface InvoiceUpload {
  url: string;
  file_name: string;
  invoice_number: string;
  invoice_date: string;
  hint: string;
}

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
    api<{ lines: ProcurementLine[]; invoice_error: string }>("/api/procurement/kp", {
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
      `/api/procurement/mine${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    ),
  // «Режим просмотра» (ID-6): every buyer's lines in work — the read-only «что едет».
  inWork: (after?: string) =>
    api<{ lines: ProcurementLine[]; next_cursor: string | null }>(
      `/api/procurement/in-work${after ? `?after=${encodeURIComponent(after)}` : ""}`,
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
    api<{ lines: ProcurementLine[] }>(`/api/procurement/orders/${encodeURIComponent(orderNumber)}/apply-document`, {
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
  setExpectedArrival: (lineId: string, version: number, expectedArrival: string) =>
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/expected-arrival`, {
      method: "PATCH",
      body: JSON.stringify({ version, expected_arrival: expectedArrival }),
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
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/invoice`, {
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
    api<{ line: ProcurementLine }>(`/api/procurement/${lineId}/invoice/replace`, {
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

export const receivingApi = {
  list: (after?: string) =>
    api<{ lines: ReceivingLine[]; next_cursor: string | null }>(
      `/api/receiving${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    ),
  /** WH-6: «Принято» без места на складе — печатный бланк через тот же механизм, что FF-8 (print.ts). */
  putawaySheet: () => api<{ sheet: PrintSheet }>("/api/receiving/putaway-sheet"),
  inspect: (lineId: string, payload: Record<string, unknown>) =>
    api<{ line: ReceivingLine; lines?: ReceivingLine[] }>(`/api/receiving/${lineId}/inspect`, {
      method: "POST",
      body: JSON.stringify(payload),
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
  issue: (requestId: string, version: number) =>
    api<{ request: FulfillmentRequest }>(`/api/issues/${requestId}/issue`, {
      method: "POST",
      body: JSON.stringify({ version }),
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
};

export const productionApi = {
  list: () => api<{ tasks: ProductionTask[]; next_cursor: string | null }>("/api/production/tasks"),
  mine: () => api<{ tasks: ProductionTask[] }>("/api/production/my"),
  operators: () => api<{ operators: ProductionOperator[]; viewer_site?: string }>("/api/production/operators"),
  calendar: () => api<{ holidays: string[]; absences: ProductionAbsence[]; shift: ProductionShift }>("/api/production/calendar"),
  saveCalendar: (holidays: string[], absences: ProductionAbsence[], shift: ProductionShift) => api<void>("/api/production/calendar", {
    method: "PUT",
    body: JSON.stringify({ holidays, shift, absences: absences.map(({ operator_id, from_date, to_date, kind }) => ({ operator_id, from_date, to_date, kind })) }),
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
  schedule: (taskId: string, version: number, replan: boolean, maxCrew: number) =>
    api<{ variants: ProductionScheduleVariant[] }>(`/api/production/tasks/${taskId}/schedule`, {
      method: "POST", body: JSON.stringify({ version, replan, max_crew: maxCrew }),
    }),
  applySchedule: (taskId: string, version: number, replan: boolean, maxCrew: number, transferBatch: number) =>
    api<{ task: ProductionTask; variant: ProductionScheduleVariant }>(`/api/production/tasks/${taskId}/schedule/apply`, {
      method: "POST", body: JSON.stringify({ version, replan, max_crew: maxCrew, transfer_batch: transferBatch }),
    }),
  execution: () => api<{ session: ProductionWorkSession | null; boxes: ProductionBox[] }>("/api/production/my/execution"),
  inputs: (taskId: string, operationId: string) =>
    api<{ boxes: ProductionBox[]; materials: ProductionMaterialSource[] }>(`/api/production/tasks/${taskId}/operations/${operationId}/inputs`),
  boxes: (taskId?: string) => api<{ boxes: ProductionBox[] }>(`/api/production/boxes${taskId ? `?task_id=${taskId}` : ""}`),
  startWork: (taskId: string, operationId: string, inputs: string[], materials: Array<{ fulfillment_line_id: string; quantity: string }> = []) =>
    api<{ session: ProductionWorkSession }>(`/api/production/tasks/${taskId}/operations/${operationId}/work/start`, {
      method: "POST", body: JSON.stringify({ inputs, materials }),
    }),
  workAction: (sessionId: string, action: "pause" | "resume" | "cancel") =>
    api<{ session: ProductionWorkSession }>(`/api/production/work/${sessionId}/${action}`, { method: "POST" }),
  submitWork: (sessionId: string, goodQuantity: string, defectQuantity: string, defectCharacter = "", defectDescription = "") =>
    api<{ session: ProductionWorkSession; box: ProductionBox; output_id: string; defect: ProductionDefect | null }>(`/api/production/work/${sessionId}/submit`, {
      method: "POST", body: JSON.stringify({ good_quantity: goodQuantity, defect_quantity: defectQuantity, defect_character: defectCharacter, defect_description: defectDescription }),
    }),
  boxAction: (boxId: string, action: "confirm", version: number) =>
    api<{ box: ProductionBox }>(`/api/production/boxes/${boxId}/${action}`, {
      method: "POST", body: JSON.stringify({ version }),
    }),
  boxOutputs: (boxId: string) => api<{ outputs: ProductionOutput[] }>(`/api/production/boxes/${boxId}/outputs`),
  correctBox: (boxId: string, version: number, input: CorrectBoxInput) =>
    api<{ box: ProductionBox; output: ProductionOutput; defect: ProductionDefect | null }>(`/api/production/boxes/${boxId}/correct`, {
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
  addEvent: (payload: {
    project: string;
    kind: "note" | "block" | "unblock" | "deadline" | "stage" | "tcReview" | "mark";
    blocker?: string;
    text?: string;
    deadline?: string | null;
    item_index?: string;
    value?: string;
    field?: string;
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
  print: (shipmentId: string, document: "pl" | "dn") => api<{ sheets: PrintSheet[] }>(`/api/shipping/shipments/${shipmentId}/print?document=${document}`),
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
};

export const referencesApi = {
  bases: () => api<BasesResult>("/api/references/bases"),
  cables: () => api<CablesResult>("/api/references/cables"),
  moveCable: (recordId: string, kind: CableKind) =>
    api<CableRow>(`/api/references/cables/${recordId}/kind`, { method: "PUT", body: JSON.stringify({ kind }) }),
  saveCableDiameters: (payload: { cable: string; dia_id: string | null; old: Record<string, string>; value: Record<string, string> }) =>
    api<CableDiameters>("/api/references/cables/diameters", { method: "PUT", body: JSON.stringify(payload) }),
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
  editRecord: (id: string, edits: Array<{ field: string; old: string; value: string }>) =>
    api<TechOperationRecord>(`/api/references/tech-operations/${id}`, { method: "PATCH", body: JSON.stringify({ edits }) }),
  createRecord: (tab: TechOperationTab, fields: Record<string, string>) =>
    api<TechOperationRecord>("/api/references/tech-operations", { method: "POST", body: JSON.stringify({ tab, fields }) }),
  resolveTerminal: (payload: { article: string; wires: string[]; folded?: boolean; gost_awg?: Record<string, number> }) =>
    api<TerminalResolution>("/api/references/technology/resolve-terminal", { method: "POST", body: JSON.stringify(payload) }),
};

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
  importGrapher: (filename: string, contentBase64: string, components: Array<{ id: string; article: string; name: string }> = []) =>
    api<GrapherImportResult>("/api/techcards/grapher/import", {
      method: "POST", body: JSON.stringify({ filename, content_base64: contentBase64, components }),
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
  autoBuild: (cardId: string, version: number, config: Record<string, unknown>) =>
    api<ProjectTechcard>(`/api/techcards/projects/${cardId}/build`, {
      method: "POST",
      body: JSON.stringify({ version, config }),
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
  connectorFamilies: (article = "") => api<{ items: ConnectorFamily[]; match: ConnectorFamily["housing"] | null }>(
    `/api/techcards/connector-families${article ? `?article=${encodeURIComponent(article)}` : ""}`),
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
};

/** «Проекты» — карточки наборов (SUPPLYMAP erptechmap, TcKitset.gs). */
export interface KitsetKbStage {
  index: string;
  status: string;
  manual: boolean;
  tc: boolean;
  kit: boolean;
  review: { value: string; text: string; who: string } | null;
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
}

/** Смена статуса проекта в один клик (POST …/status): этап на все КБ, «авто», закрытие, блокер. */
export type ProjectStatusAction = { action: "status"; value: string; text?: string }
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
}

export interface KitsetSupplyLine {
  art: string;
  name: string;
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
  issue: (project: string, deadline: string) =>
    api<{ created: Array<{ no: string; index: string; shortfall: string[] }>; skipped: string[]; message: string }>(
      `${kitsetPath(project)}/issue`, { method: "POST", body: JSON.stringify({ deadline }) },
    ),
  remove: (project: string) => api<{ res: KitsetDeleteResult }>(kitsetPath(project), { method: "DELETE" }),
  mark: (project: string, payload: { kb: string; kind: "stage" | "tcReview"; value: string; text: string }) =>
    api<{ msg: string }>(`${kitsetPath(project)}/marks`, { method: "POST", body: JSON.stringify(payload) }),
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
