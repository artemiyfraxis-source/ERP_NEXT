import { api, type KitsetSupplyLine } from "./api";

/* «Снабжение» проекта: позиции спецификации против склада и закупки, и «Заказать недостающее». */

export interface SupplyLine extends KitsetSupplyLine {
  /** Свободно на складе (ДОСТУПНО + собственные резервы проекта). */
  free: string;
  /** Без ГРН: закупка отметила «Есть на складе, без ГРН» — на полке, но ни в одной партии и ни в одном резерве
   *  (за вычетом выданного комплектацией «есть, не заведено»). Старый сервер поле не шлёт. */
  off_book?: string;
  /** В закупке: живые заявки и строки закупки проекта, ещё не принятые. */
  ordered: string;
  /** Уже в комплектации проекта: в резерве или выдано. */
  kitted: string;
  /** Не хватает = Нужно − Свободно − Без ГРН − В закупке − В комплектации, не меньше нуля. */
  short: string;
}

export interface OrderMissingResult {
  created: Array<{ art: string; name: string; quantity: string; unit: string; legacy_id: string }>;
  skipped: Array<{ art: string; kind: string; reason: string }>;
  message: string;
}

const path = (project: string) => `/api/techcards/kitsets/${encodeURIComponent(project)}`;

export const supplyApi = {
  lines: (project: string) => api<{ project: string; lines: SupplyLine[] }>(`${path(project)}/supply`),
  /** Пустой `articles` — все позиции, где не хватает. */
  orderMissing: (project: string, body: { articles: string[]; deadline?: string }) =>
    api<OrderMissingResult>(`${path(project)}/order-missing`, { method: "POST", body: JSON.stringify(body) }),
};
