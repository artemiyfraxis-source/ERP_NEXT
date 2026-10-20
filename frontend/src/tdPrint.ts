// Печать комплекта ТД (ЕСТД), СТО типовых дефектов и сопроводительного листа. Сигнатуры — контракт: их зовёт блок
// «Комплект ТД» в ЭТК. Вёрстка — tdPrintSheets.ts, stoPrint.ts, routeSheetPrint.ts.
import { openPrintHtml } from "./print";
import { buildRouteSheetHtml } from "./routeSheetPrint";
import { buildStoHtml } from "./stoPrint";
import { buildTdKitHtml } from "./tdPrintSheets";
import type { RouteSheetOptions, Sto, TdPrintModel } from "./tdTypes";

export function openTdKitPrint(model: TdPrintModel): void {
  openPrintHtml(buildTdKitHtml(model));
}

export function openStoPrint(sto: Sto): void {
  openPrintHtml(buildStoHtml(sto));
}

export function openRouteSheetPrint(model: TdPrintModel, options: RouteSheetOptions): void {
  openPrintHtml(buildRouteSheetHtml(model, options));
}
