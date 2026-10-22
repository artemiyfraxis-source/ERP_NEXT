// Комплект ТД по ЕСТД (выпуск комплекта, ГОСТ РВ): общий контракт API, печати и экрана. Меняется только вместе
// с backend/app/modules/techcards/td_issues_api.py.
import type { ProjectTechcard } from "./api";

export type TdIssueStatus = "draft" | "signing" | "active" | "annulled";

export interface TdSignatory { name: string; position: string }

/** Подписанты основной надписи (ГОСТ 3.1103, графы 12–14) и «Согласовано» ВП на ТЛ. */
export interface TdSignatories {
  developer: TdSignatory;
  checker: TdSignatory;
  normcontrol: TdSignatory;
  approver: TdSignatory;
  agreed_vp: TdSignatory;
}

/** Обозначения документов комплекта по ГОСТ 3.1201, например «ЮТНВ.60185.00001» (до 22.10.2026 — «ФРАК…»). ТЛ и ЛР несут обозначение ВТД. */
export interface TdDesignations { vtd: string; mk: string; ke: string; ok: string; ok_tk: string; vm: string; vo: string }

export interface TdIssue {
  id: string;
  version: number;
  project_card_id: string;
  /** 0 — первый выпуск; дальше — номер изменения по извещению. */
  change_no: number;
  status: TdIssueStatus;
  /** «проект» / «на подписи» / «действует» / «аннулирован». */
  status_label: string;
  /** Ревизия ЭТК, замороженная в выпуске. */
  etk_revision: number;
  current_etk_revision: number;
  /** Есть более новая ревизия ЭТК — нужно оформить извещение (новый выпуск). */
  has_changes: boolean;
  sequence_no: number;
  org_code: string;
  /** Вид ТП по методу выполнения (3.1201): «85» электромонтаж, «88» сборка, «80» пайка. */
  method_code: string;
  /** '' (без литеры), П, О, О1…О5, А, Б, И — ГОСТ Р 3.102-2024. */
  litera: string;
  designations: TdDesignations;
  signatories: TdSignatories;
  notice_no: string;
  reason: string;
  /** Цех и участок выпуска (МК «Цех», «Уч.»; ТЛ; сопроводительный лист). Пусто — нормоконтроль блокирует подпись. */
  workshop: string;
  site: string;
  created_at: string;
  created_by: string;
  approved_at: string | null;
  annulled_at: string | null;
}

export interface TdIssueInput {
  litera: string;
  method_code: string;
  signatories: TdSignatories;
  notice_no: string;
  reason: string;
  workshop: string;
  /** «ЦАР» / «ЖУК». */
  site: string;
}

/** Автор выпуска, открытого самим ERP при выдаче ЭТК или её новой ревизии. */
export const TD_SYSTEM_ACTOR = "система";

/** Профессия и разряд исполнителя операции, замороженные в снимке выпуска (`card.td_ops[operation_key]`). */
export interface TdOpGrade {
  profession: string;
  /** "1"…"6"; "" — не задан (нормоконтроль предупреждает). */
  grade: string;
  /** «БД.ОП» — разряд из БД.ОП; «по умолчанию» — по ЕТКС для семейства операции. */
  source: string;
}

/** td_ops снимка по ключу операции; нет (снимок старый) — пусто. */
export function tdOpGrade(card: ProjectTechcard, key: string): TdOpGrade {
  const all = (card as unknown as { td_ops?: Record<string, TdOpGrade> }).td_ops;
  return all?.[key] ?? { profession: "", grade: "", source: "" };
}

export interface TdFinding {
  code: string;
  severity: "blocking" | "warning";
  operation_key: string | null;
  text: string;
}

export interface TdStoRef { section: string; title: string }

export interface TdChange {
  change_no: number;
  notice_no: string;
  reason: string;
  designation: string;
  approved_at: string | null;
  developer: string;
}

/** GET /techcards/td-issues/{id}/print — всё для печати из замороженного снимка. */
export interface TdPrintModel {
  issue: TdIssue;
  /** Карта ЭТК в том виде, как её отдавал GET /techcards/projects/{id} в момент выпуска, и `td_ops` — профессия и
   *  разряд операций (tdOpGrade). */
  card: ProjectTechcard;
  /** operation_key → раздел СТО типовых дефектов (null — раздел не найден). */
  sto_sections: Record<string, TdStoRef | null>;
  changes: TdChange[];
  sto: { designation: string; revision: string };
}

export interface StoRow { code: string; defect: string; criterion: string; class: string; method: string; action: string }
export interface StoSection { code: string; title: string; scope: string; rows: StoRow[]; subsections?: StoSection[] }
export interface StoClass { class: string; meaning: string; rv: string }

/** GET /techcards/sto — backend/data/sto-frak-001-2026.json как есть. */
export interface Sto {
  designation: string;
  title: string;
  revision: string;
  date: string;
  intro: { scope: string[]; references: string[]; classes: StoClass[]; classes_note: string };
  sections: StoSection[];
  actions: string[];
  changes: { change: string; notice: string; sections: string; date: string; signature: string }[];
}

export interface RouteSheetOptions { serial: string; batch: string; qty: string }
