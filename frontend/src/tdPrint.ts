// Печать комплекта ТД (ЕСТД), СТО типовых дефектов и сопроводительного листа. Заглушка контракта: тела функций
// пишет задача печати, сигнатуры не меняются — их зовёт блок «Комплект ТД» в ЭТК.
import type { RouteSheetOptions, Sto, TdPrintModel } from "./tdTypes";

export function openTdKitPrint(model: TdPrintModel): void {
  void model;
  throw new Error("Печать комплекта ТД ещё не подключена");
}

export function openStoPrint(sto: Sto): void {
  void sto;
  throw new Error("Печать СТО ещё не подключена");
}

export function openRouteSheetPrint(model: TdPrintModel, options: RouteSheetOptions): void {
  void model;
  void options;
  throw new Error("Сопроводительный лист ещё не подключён");
}
