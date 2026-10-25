// @vitest-environment node
import { expect, it } from "vitest";
import { detectSolderConn_, initState, makeGen, type Comp, type GenData } from "./tcGenLogic";

// Владелец 25.10: QS8-S / AS150 / AS150U / XT150 — своя карта пайки SDR_QS8 (раньше QS8 шёл на SDR_XT90), Weipu SA —
// SDR_SA и INS_housing_SA (раньше SA8xx шёл на SDR_GX12 / INS_housing_GX). GX12, LP-12 и остальные — без изменений.

it("solder card by connector article: AMASS QS8 / AS150 / XT150 → SDR_QS8, Weipu SA → SDR_SA, the rest as before", () => {
  for (const art of ["QS8-S-M", "QS8-S-F", "QS8 S", "AS150-M", "AS150U-F", "AS 150", "AMASS AS150U-M.G.B", "XT150-M", "XT-150"]) {
    expect(detectSolderConn_(art), art).toBe("SDR_QS8");
  }
  for (const art of ["SA812/S4", "SA810/P4", "SA1210/P9", "SA812/S4B", "Weipu SA810/P4"]) expect(detectSolderConn_(art), art).toBe("SDR_SA");
  expect(detectSolderConn_("XT90-M")).toBe("SDR_XT90");
  expect(detectSolderConn_("XT60PW")).toBe("SDR_XT60");
  expect(detectSolderConn_("XT30U-M")).toBe("SDR_XT30");
  expect(detectSolderConn_("GX12-4")).toBe("SDR_GX12");
  expect(detectSolderConn_("LP-12-4 розетка")).toBe("SDR_GX12");
  expect(detectSolderConn_("GX16-8")).toBe("SDR_GX16");
  expect(detectSolderConn_("BASS150X")).toBe(null);
  expect(detectSolderConn_("SA")).toBe(null);
});

const WIRE: Comp = { id: "w", type: "Провод", name: "Силиконовый 8AWG", art: "Силиконовый 8AWG", qty: 0.6 };
const dataOf = (components: Comp[]): GenData => ({
  components, assemblyInfo: { "Индекс": "КБ7", "Наименование": "Жгут батарейного питания", "Кол-во": "2" },
  templates: [], ops: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: [], terRecords: [],
});

it("the generator puts SDR_QS8 on a QS8-S harness and no housing step; SA gets SDR_SA and the INS_housing_SA step by default", () => {
  const qs8 = dataOf([WIRE, { id: "c", type: "Соед", name: "QS8-S-M", art: "QS8-S-M", qty: 2 }]);
  const qOps = makeGen(qs8, initState(qs8)).getActiveOps();
  const qSolder = qOps.filter((o) => o.type === "solderConn");
  expect(qSolder.length).toBeGreaterThan(0);
  expect(qSolder.every((o) => o.tmplHint === "SDR_QS8")).toBe(true);
  expect(qOps.some((o) => o.tmplHint === "SDR_XT90")).toBe(false);
  expect(qOps.some((o) => o.type === "insHousing")).toBe(false);

  const sa = dataOf([{ ...WIRE, name: "МГТФ 0,35", art: "МГТФ 0,35", qty: 1 }, { id: "c", type: "Соед", name: "SA812/S4", art: "SA812/S4", qty: 1 }]);
  const saOps = makeGen(sa, initState(sa)).getActiveOps();
  expect(saOps.filter((o) => o.type === "solderConn").every((o) => o.tmplHint === "SDR_SA")).toBe(true);
  expect(saOps.find((o) => o.type === "insHousing")).toMatchObject({ tmplHint: "INS_housing_SA", cat: "Монтаж корпуса" });

  const gx = dataOf([{ ...WIRE, name: "МГТФ 0,35", art: "МГТФ 0,35", qty: 1 }, { id: "c", type: "Соед", name: "GX12-4", art: "GX12-4", qty: 1 }]);
  const gxOps = makeGen(gx, initState(gx)).getActiveOps();
  expect(gxOps.filter((o) => o.type === "solderConn").every((o) => o.tmplHint === "SDR_GX12")).toBe(true);
  expect(gxOps.find((o) => o.type === "insHousing")).toMatchObject({ tmplHint: "INS_housing_GX" });
});
